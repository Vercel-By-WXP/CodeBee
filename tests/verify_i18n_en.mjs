// i18n 英文模式全量扫描：
//   起 Edge headless(9222) → 设 localStorage.orch.lang=en → 逐页遍历（任务/运行/自动化/
//   用量/经验库/各设置子页/各弹框），把每个可见文本节点里残留的中文抓出来报告。
//   模板字面量里保留的中文是已知限制，这里要确认「包过 t() 的词条」在 en 下真的命中。
// 依赖：先起服务 18801（TUTTI_DATA 临时目录），裸跑：
//   node tests/verify_i18n_en.mjs [wsPrefix] [appUrl] [token]
const wsPrefix = process.argv[2] || "ws://127.0.0.1:9222/devtools/page/";
const appUrl = process.argv[3] || "http://127.0.0.1:18801/";
const TOKEN = process.argv[4] || "";

const CDP = process.env.CDP || "http://127.0.0.1:9222";
let msgId = 0;
const pending = new Map();
let ws;

function send(method, params) {
  const id = ++msgId;
  return new Promise((resolve, reject) => {
    pending.set(id, { resolve, reject });
    ws.send(JSON.stringify({ id, method, params }));
  });
}
const sleep = (ms) => new Promise((r) => setTimeout(r, ms));

async function evalJs(expr) {
  const r = await send("Runtime.evaluate", { expression: expr, returnByValue: true, awaitPromise: true });
  if (r.exceptionDetails) throw new Error("JS exception: " + JSON.stringify(r.exceptionDetails.exception?.description || r.exceptionDetails.text));
  return r.result.value;
}

// 豁免名单：刻意不做 i18n 的文本（语言名母语写死）+ 测试种子任务名（用户数据不翻译）
// + 后端已格式化的动态 note（note_args 通道见 modelhub.sources，前端拿到的不是模板）
const I18N_IGNORE = [
  /^中文$/, /^中文 \/ English$/, /^样式核验-/,
  /^跳过 \d+ 条（缺地址或格式不识别）/, /^跳过 \d+ 个（缺合法 baseURL）/,
  /^跳过 \d+ 个（缺合法 baseURL）：/, /^\d+ 个未带出密钥——请在「模型接入」页手填：/,
];

// 收集当前页残留中文：遍历文本节点 + title/placeholder/aria-label 属性（隐藏元素不计）
async function collectChinese(where) {
  return await evalJs(`(() => {
    const out = [];
    const w = ${JSON.stringify(where)};
    const IGNORE = ${JSON.stringify(I18N_IGNORE.map((r) => r.source))};
    const inIgnore = (s) => IGNORE.some((src) => { try { return new RegExp(src, 'u').test(s); } catch (e) { return false; } });
    const visible = (el) => !!(el && el.closest('.hidden') === null);
    const cjk = /[\\u4e00-\\u9fff]/;
    const walk = document.createTreeWalker(document.body, NodeFilter.SHOW_TEXT);
    let n, seen = new Set();
    while (n = walk.nextNode()) {
      const t = (n.textContent || '').trim();
      if (t && cjk.test(t) && !inIgnore(t) && visible(n.parentElement)) {
        let p = n.parentElement, path = [];
        for (let i = 0; p && i < 4; i++) {
          path.push(p.tagName.toLowerCase() + (p.id ? '#' + p.id : '') + (p.className && typeof p.className === 'string' ? '.' + p.className.trim().split(/\\s+/).slice(0,2).join('.') : ''));
          p = p.parentElement;
        }
        const key = t.slice(0, 60);
        if (!seen.has(key)) { seen.add(key); out.push({ text: key, at: path.join('<') }); }
      }
    }
    const attrs = ['title','placeholder','aria-label'];
    document.querySelectorAll('*').forEach(e => {
      if (!visible(e)) return;
      for (const a of attrs) {
        const v = e.getAttribute && e.getAttribute(a);
        if (v && cjk.test(v) && !inIgnore(v)) {
          const key = a + '=' + v.slice(0, 60);
          if (!seen.has(key)) { seen.add(key); out.push({ text: v.slice(0,60), at: '[attr ' + a + '] ' + e.tagName.toLowerCase() + (e.id ? '#'+e.id : '') }); }
        }
      }
    });
    return out;
  })()`);
}

(async () => {
  let targets = [];
  for (let i = 0; i < 30 && !targets.length; i++) {
    await sleep(500);
    try { targets = (await (await fetch(CDP + "/json")).json()).filter(t => t.type === "page"); } catch (e) { /* not up */ }
  }
  if (!targets.length) throw new Error("Edge 9222 未就绪");
  ws = new WebSocket(targets[0].webSocketDebuggerUrl);
  await new Promise((res, rej) => { ws.onopen = res; ws.onerror = rej; });
  ws.onmessage = (ev) => {
    const m = JSON.parse(ev.data);
    if (m.id && pending.has(m.id)) { const { resolve, reject } = pending.get(m.id); pending.delete(m.id);
      if (m.error) reject(new Error(m.error.message)); else resolve(m.result); }
  };

  await send("Page.enable");
  await send("Runtime.enable");

  async function goto(u) { await send("Page.navigate", { url: u }); await sleep(1500); }
  async function click(sel) {
    await evalJs(`(() => { const e = document.querySelector(${JSON.stringify(sel)}); if (!e) return 'missing'; e.click(); return 'ok'; })()`);
  }

  await goto(appUrl + (TOKEN ? "?token=" + encodeURIComponent(TOKEN) : ""));
  await sleep(1500);
  // 设英文并 reload，确保走完整首屏（pre-paint 读 orch.lang）；带 cache-bust 强制拉新 js
  await evalJs(`localStorage.setItem('orch.lang','en'); 'set'`);
  await send("Network.enable");
  await send("Network.setCacheDisabled", { cacheDisabled: true });
  await goto(appUrl + (TOKEN ? "?token=" + encodeURIComponent(TOKEN) : "") + "&cb=" + Date.now());
  await sleep(2000);
  const lang = await evalJs(`document.documentElement.dataset.lang`);
  console.log("data-lang =", lang, lang === "en" ? "OK" : "（切换失败）");

  const all = [];
  const page = (name) => ({ page: name, items: null });
  async function sweep(label, actions) {
    for (const a of (actions || [])) { await a(); await sleep(700); }
    const items = await collectChinese(label);
    all.push({ page: label, items });
    console.log("[" + label + "] 残留中文 " + items.length + " 处");
    for (const it of items) console.log("    · " + it.text + "   @ " + it.at);
  }

  // 主区任务页
  await sweep("tasks");
  // 顶栏各子页（switchTab 是全局导航）
  const subtabs = ["runs", "automation", "usage", "agents", "models", "bindings", "skills", "knowledge", "market", "orch", "appearance", "about"];
  for (const st of subtabs) {
    await sweep(st, [() => evalJs(`(typeof switchTab === 'function' ? (switchTab(${JSON.stringify(st)}), 'ok') : 'no-nav:' + typeof switchTab)`)]);
  }
  // 打开「＋ 新任务」弹框（页面上下文可能因轮询刷新而换文档，包 try）
  async function trySweep(label, actions) {
    try { await sweep(label, actions); } catch (e) { console.log("[" + label + "] 跳过：" + (e.message || e)); }
  }
  await trySweep("composer", [() => evalJs(`switchTab("tasks"); "ok"`), () => click("#btn-new-task")]);
  // 添加供应商弹框
  await trySweep("add-provider-dialog", [() => evalJs(`openAddProviderDialog(); "ok"`)]);
  // 导入供应商弹框
  await trySweep("import-dialog", [() => evalJs(`openImportDialog(); "ok"`)]);
  // 流程管理弹框
  await trySweep("flows-manager", [() => evalJs(`closeModal&&closeModal(); openFlowsManager(); "ok"`)]);
  // 自动化新建弹框
  await trySweep("auto-form", [() => evalJs(`closeModal&&closeModal(); autoForm(); "ok"`)]);
  // 命令面板
  await trySweep("cmdk", [() => evalJs(`(document.getElementById('cmdk-mask')||{classList:{add(){}}}).classList.remove('hidden'); document.dispatchEvent(new KeyboardEvent('keydown',{key:'k',ctrlKey:true})); "ok"`)]);

  const total = all.reduce((s, x) => s + x.items.length, 0);
  console.log("\n=== 英文模式残留中文合计：" + total + " 处 ===");
  ws.close();
  process.exit(total > 0 ? 2 : 0);
})().catch((e) => { console.error("FAIL:", e && e.message ? e.message : e); process.exit(1); });
