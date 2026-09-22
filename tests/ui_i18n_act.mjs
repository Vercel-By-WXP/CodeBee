// Edge headless + CDP 验证 i18n 补漏（2026-09-22）：
//   1. 直连对话「思考过程」活动行：后端烤死的「请求工具: a、b」在 EN 模式渲染成
//      「Requesting tools: a, b」，中文模式原样（chatActLine 老数据兼容）。
//   2. 本轮补的字典词条抽查（钩子/超时/标点）。
//   3. face 徽章/自动推荐/welcome 徽章的 data-i18n 生效。
// 用法：node tests/ui_i18n_act.mjs <cdpPort> <appUrl> [token]
const wsUrl = process.argv[2] || "ws://127.0.0.1:9222/devtools/page/";
const appUrl = process.argv[3] || "http://127.0.0.1:18801/";
const TOKEN = process.argv[4] || "";

let msgId = 0;
const pending = new Map();

function call(ws, method, params) {
  const id = ++msgId;
  return new Promise((resolve, reject) => {
    pending.set(id, { resolve, reject });
    ws.send(JSON.stringify({ id, method, params }));
  });
}

const PASS = [], FAIL = [];
function check(name, cond, detail = "") {
  (cond ? PASS : FAIL).push(name);
  console.log((cond ? "  ✓ " : "  ✗ ") + name + (cond ? "" : "　— " + String(detail).slice(0, 300)));
}

(async () => {
  const targetsRes = await fetch("http://127.0.0.1:9222/json");
  const targets = await targetsRes.json();
  const target = targets.find((t) => t.type === "page") || targets[0];
  const ws = new WebSocket(target.webSocketDebuggerUrl);
  await new Promise((res, rej) => { ws.onopen = res; ws.onerror = rej; });
  ws.onmessage = (ev) => {
    const m = JSON.parse(ev.data);
    if (m.id && pending.has(m.id)) {
      const { resolve, reject } = pending.get(m.id);
      pending.delete(m.id);
      if (m.error) reject(new Error(m.error.message));
      else resolve(m.result);
    }
  };

  async function evalJs(expr) {
    const r = await call(ws, "Runtime.evaluate", { expression: expr, returnByValue: true, awaitPromise: true });
    if (r.exceptionDetails) throw new Error("EVAL EXC: " + r.exceptionDetails.text);
    return r.result.value;
  }

  await call(ws, "Page.enable");
  await call(ws, "Page.navigate", { url: appUrl + (TOKEN ? "?token=" + TOKEN : "") });
  await new Promise((r) => setTimeout(r, 1800));

  // —— EN 模式 ——
  await evalJs(`localStorage.setItem("orch.lang", "en"); location.reload();`);
  await new Promise((r) => setTimeout(r, 1800));

  const en = await evalJs(`(() => {
    const line = chatActLine("请求工具: list_files、read_file");
    const pass = chatActLine("read_file E:\\\\x.py");
    const html = chatThinkHTML({ status: "running", thinking: "let me look", activity: ["请求工具: list_files、read_file", "read_file E:\\\\x.py"] });
    return {
      line, pass, html,
      tHooks: t("钩子"), tTimeout: t("超时（秒）"), tRunning: t("运行中…"),
      tSent: t("（已发 "), tComma: t("，"), tBook: t("《"), tUnt: t("未命名"),
      tReq: t("请求工具"), tNewTask: t("新建任务"), tBuiltin: t("内置"),
      modeFace: document.getElementById("f-mode-face")?.textContent || "",
      directBtn: document.getElementById("f-direct-btn")?.textContent || "",
    };
  })()`);
  console.log("EN:", JSON.stringify(en));
  check("EN 活动行翻译", en.line === "Requesting tools: list_files, read_file", en.line);
  check("EN 无前缀行原样放行", en.pass === "read_file E:\\x.py", en.pass);
  check("EN 思考块含译文", en.html.includes("Requesting tools") && !en.html.includes("请求工具"), en.html.slice(0, 200));
  check("EN 思考块保留无前缀行", en.html.includes("read_file E:\\x.py"), en.html.slice(0, 200));
  check("词条:钩子", en.tHooks === "Hooks", en.tHooks);
  check("词条:超时（秒）", en.tTimeout === "Timeout (s)", en.tTimeout);
  check("词条:运行中…", en.tRunning === "Running…", en.tRunning);
  check("词条:（已发 ", en.tSent === " (sent ", en.tSent);
  check("词条:，", en.tComma === ", ", en.tComma);
  check("词条:《", en.tBook === "“", en.tBook);
  check("词条:未命名", en.tUnt === "Untitled", en.tUnt);
  check("词条:请求工具", en.tReq === "Requesting tools", en.tReq);
  check("词条:新建任务", en.tNewTask === "New task", en.tNewTask);
  check("词条:内置", en.tBuiltin === "Built-in", en.tBuiltin);
  check("face 徽章 EN", en.modeFace === "Auto" || en.modeFace === "", en.modeFace);
  check("自动推荐按钮 EN", en.directBtn === "Auto-recommended" || en.directBtn === "", en.directBtn);

  // —— 中文模式回落 ——
  await evalJs(`localStorage.setItem("orch.lang", "zh"); location.reload();`);
  await new Promise((r) => setTimeout(r, 1800));
  const zh = await evalJs(`(() => ({
    line: chatActLine("请求工具: list_files、read_file"),
    face: document.getElementById("f-mode-face")?.textContent || "",
    badge: document.getElementById("welcome-badge")?.textContent || "",
  }))()`);
  console.log("ZH:", JSON.stringify(zh));
  check("ZH 活动行原样", zh.line === "请求工具: list_files、read_file", zh.line);
  check("ZH face 徽章回落", zh.face === "自动", zh.face);
  check("welcome 徽章 data-i18n", zh.badge === "首次启动", zh.badge);

  console.log(`\n结果: ${PASS.length} 过 / ${FAIL.length} 挂`);
  ws.close();
  process.exit(FAIL.length ? 1 : 0);
})().catch((e) => { console.error("FAIL:", e); process.exit(1); });
