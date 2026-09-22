/* 网页成品预览（借鉴对话式编程产品的预览窗）端到端回归：
 * 1. 服务端 /api/runs/<id>/preview 挑入口与页签；/preview/<run>/<令牌>/<文件>
 *    真把工作目录挂出来（<base> 注入 + 令牌补丁 + 穿越拒绝 + 令牌校验）。
 * 2. UI：详情页出现「预览」页签并自动落位；运行视图 iframe 里的 style.css /
 *    script.js 这些**相对子资源**真的解析到位（脚本改写标题、样式生效）——
 *    这是整条链路最有价值也最容易翻车的一环；文件页签切源码视图；「刷新」
 *    重挂 iframe；无网页成品的任务「预览」页签整体隐藏。
 * 造数：任务 A（code，workdir 有 index.html/style.css/script.js）+
 *       任务 B（连载，workdir 只有章节 md，无网页成品）。
 * 用法：node tests/ui_preview.mjs */
import { spawn } from "node:child_process";
import { mkdtempSync, rmSync, writeFileSync, mkdirSync, readFileSync } from "node:fs";
import { tmpdir } from "node:os";
import { join, dirname } from "node:path";
import { fileURLToPath } from "node:url";

// 端口可用 TUTTI_TEST_PORT / TUTTI_TEST_CDP 覆盖：并行跑测试时固定口会被
// 残留服务/Edge 双绑（同 CDP 口互相驱动对方页面，症状像产品 bug）
const SERVICE_PORT = Number(process.env.TUTTI_TEST_PORT) || 18891;
const CDP_PORT = Number(process.env.TUTTI_TEST_CDP) || 9377;
const SERVICE = `http://127.0.0.1:${SERVICE_PORT}`;
const RUN_A = "r-20990101-000000-00pa";
const RUN_B = "r-20990101-000000-00pb";
const RUN_C = "r-20990101-000000-00pc";
const EDGE_CANDIDATES = [
  "C:\\Program Files (x86)\\Microsoft\\Edge\\Application\\msedge.exe",
  "C:\\Program Files\\Microsoft\\Edge\\Application\\msedge.exe",
];
const ROOT = join(dirname(fileURLToPath(import.meta.url)), "..");

const results = [];
function check(name, cond, detail = "") {
  results.push({ name, ok: !!cond });
  console.log((cond ? "  ✓ " : "  ✗ ") + name + (cond ? "" : "　— " + String(detail).slice(0, 300)));
}
const sleep = (ms) => new Promise((r) => setTimeout(r, ms));

const INDEX_HTML = "<!DOCTYPE html><html><head><meta charset=\"utf-8\">" +
  "<link rel=\"stylesheet\" href=\"style.css\"></head><body><h1 id=\"pv-title\">待办事项</h1>" +
  "<script src=\"script.js\"></script></body></html>";
const STYLE_CSS = "#pv-title { color: rgb(255, 140, 0); font-size: 40px; }";
const SCRIPT_JS = "document.getElementById('pv-title').textContent = '由预览脚本改写';";

async function main() {
  const dataDir = mkdtempSync(join(tmpdir(), "tutti-pvui-"));
  const wdA = join(dataDir, "app");
  const wdB = join(dataDir, "book");
  mkdirSync(wdA, { recursive: true });
  mkdirSync(wdB, { recursive: true });
  mkdirSync(join(dataDir, "tasks"), { recursive: true });
  mkdirSync(join(dataDir, "runs", RUN_A), { recursive: true });
  mkdirSync(join(dataDir, "runs", RUN_B), { recursive: true });
  mkdirSync(join(dataDir, "runs", RUN_C), { recursive: true });
  writeFileSync(join(wdA, "index.html"), INDEX_HTML, "utf-8");
  writeFileSync(join(wdA, "style.css"), STYLE_CSS, "utf-8");
  writeFileSync(join(wdA, "script.js"), SCRIPT_JS, "utf-8");
  writeFileSync(join(wdB, "chapter-1.md"), "# 第一章\n\n正文。\n", "utf-8");

  const mkTask = (id, title, engine, created) => ({
    id, title, type: engine === "direct" ? "direct" : "novel",
    engine, serial: engine !== "direct",
    goal: "造数：" + title, workdir: engine === "direct" ? wdA : wdB,
    status: "done", archived: false, created_at: created, mode: "manual",
  });
  const mkRun = (id, task_id, title, started) => ({
    id, kind: "orchestration", title, task_id, status: "done", messages: [],
    steps: [{ n: 1, role: "draft", agent: "mock-a", agent_label: "写手", note: "",
      status: "done", started_at: "00:00:01", ended_at: "00:00:10", duration_s: 9,
      exit_code: 0, summary: "完成", log: "steps/01.log", cost_usd: 0, tokens: 0 }],
    created_at: started, started_at: started, ended_at: started,
    cost_usd: 0, tokens: 0, error: "", summary: "",
  });
  writeFileSync(join(dataDir, "tasks", "task-a.json"),
    JSON.stringify(mkTask("task-a", "待办事项网页", "direct", "2000-01-01 00:00:00"), null, 2));
  writeFileSync(join(dataDir, "tasks", "task-b.json"),
    JSON.stringify(mkTask("task-b", "甜宠小说", undefined, "2000-01-01 00:00:00"), null, 2));
  // 任务 C：非直连的 code 任务 + 网页成品 —— 验证「跑完自动落在预览」的选卡
  const wdC = join(dataDir, "webapp");
  mkdirSync(wdC, { recursive: true });
  writeFileSync(join(wdC, "index.html"), INDEX_HTML, "utf-8");
  writeFileSync(join(wdC, "style.css"), STYLE_CSS, "utf-8");
  writeFileSync(join(dataDir, "tasks", "task-c.json"), JSON.stringify({
    id: "task-c", title: "落地页", type: "code", serial: false,
    goal: "造数：落地页", workdir: wdC, status: "done", archived: false,
    created_at: "2000-01-01 00:00:00", mode: "manual",
  }, null, 2));
  writeFileSync(join(dataDir, "runs", RUN_C, "run.json"),
    JSON.stringify(mkRun(RUN_C, "task-c", "落地页", "2000-01-01 00:00:00"), null, 2));
  writeFileSync(join(dataDir, "runs", RUN_A, "run.json"),
    JSON.stringify(mkRun(RUN_A, "task-a", "待办事项网页", "2000-01-01 00:00:00"), null, 2));
  writeFileSync(join(dataDir, "runs", RUN_B, "run.json"),
    JSON.stringify(mkRun(RUN_B, "task-b", "甜宠小说", "2000-01-01 00:00:00"), null, 2));

  const srv = spawn("python", ["app/main.py", "--port", String(SERVICE_PORT)], {
    cwd: ROOT, stdio: "ignore",
    env: { ...process.env, TUTTI_DATA: dataDir },
  });
  const edgePath = EDGE_CANDIDATES.find((p) => true);
  const profile = mkdtempSync(join(tmpdir(), "tutti-cdp-"));
  const edge = spawn(edgePath, [
    "--headless=new", "--disable-gpu", "--no-first-run", "--force-device-scale-factor=1",
    `--user-data-dir=${profile}`, `--remote-debugging-port=${CDP_PORT}`,
    "--window-size=1400,950", "about:blank",
  ], { stdio: "ignore" });

  try {
    let up = false;
    for (let i = 0; i < 60 && !up; i++) {
      await sleep(500);
      try { const r = await fetch(`${SERVICE}/api/state`); up = r.ok; } catch (e) { /* retry */ }
    }
    check("临时服务就绪(18891)", up);
    const token = JSON.parse(readFileSync(join(dataDir, "remote.json"), "utf-8")).token || "";
    check("服务令牌已生成", !!token);

    // ---------- 服务端：发现 + 挂载 + 安全闸 ----------
    const meta = await (await fetch(`${SERVICE}/api/runs/${RUN_A}/preview`)).json();
    check("preview 元数据：入口 index.html + 3 个页签",
      meta.ok === true && meta.entry === "index.html" && (meta.files || []).length === 3,
      JSON.stringify(meta).slice(0, 200));
    check("preview 元数据：base 带路径令牌", String(meta.base || "").startsWith(`/preview/${RUN_A}/${token}/`),
      meta.base || "");
    const noMeta = await (await fetch(`${SERVICE}/api/runs/${RUN_B}/preview`)).json();
    check("无网页成品：ok=false（no_html）", noMeta.ok === false && noMeta.reason === "no_html",
      JSON.stringify(noMeta));

    const page = await fetch(`${SERVICE}${meta.base}index.html`);
    const pageText = await page.text();
    check("挂载入口页 200 + text/html",
      page.status === 200 && (page.headers.get("content-type") || "").includes("text/html"),
      `${page.status} ${page.headers.get("content-type")}`);
    check("入口页注入 <base href>（相对子资源由此解析）",
      pageText.includes(`<base href="/preview/${RUN_A}/${token}/">`), pageText.slice(0, 160));
    check("入口页注入令牌补丁（fetch/XHR 加 X-CodeBee-Token）",
      pageText.includes("X-CodeBee-Token"));
    const cssRes = await fetch(`${SERVICE}${meta.base}style.css`);
    check("相对子资源 style.css 走路径令牌 200",
      cssRes.status === 200 && (cssRes.headers.get("content-type") || "").includes("text/css"),
      `${cssRes.status} ${cssRes.headers.get("content-type")}`);

    // 本机（loopback）与 /api/* 同一豁免：不带/带错令牌都放行；远程错令牌
    // 的拒绝在 tests/test_preview.py 里按 request_authed 的远程形态断言
    const anyTok = await fetch(`${SERVICE}/preview/${RUN_A}/wrongtoken/index.html`);
    check("本机豁免：错令牌也放行（与 /api/* 同一把尺子）", anyTok.status === 200, String(anyTok.status));
    const trav = await fetch(`${SERVICE}/preview/${RUN_A}/${token}/%2e%2e%2f%2e%2e%2fremote.json`);
    check("穿越（编码 ..）→ 拒绝", trav.status === 404, String(trav.status));
    const trav2 = await fetch(`${SERVICE}/preview/${RUN_A}/${token}/..%5cremote.json`);
    check("穿越（编码 反斜杠..）→ 拒绝", trav2.status === 404, String(trav2.status));
    const pyFile = await fetch(`${SERVICE}/preview/${RUN_A}/${token}/evil.py`);
    check("白名单外扩展名 → 拒绝", pyFile.status === 404, String(pyFile.status));

    // ---------- 浏览器：页签、自动落位、iframe 真跑起来 ----------
    let target = null;
    for (let i = 0; i < 30 && !target; i++) {
      await sleep(500);
      try {
        const res = await fetch(`http://127.0.0.1:${CDP_PORT}/json/list`);
        target = (await res.json()).find((t) => t.type === "page");
      } catch (e) { /* Edge 未就绪 */ }
    }
    check("Edge headless CDP", !!target);
    const ws = new WebSocket(target.webSocketDebuggerUrl);
    await new Promise((res, rej) => { ws.onopen = res; ws.onerror = rej; });
    let seq = 0;
    const pending = new Map();
    const consoleErrors = [];
    ws.onmessage = (ev) => {
      const msg = JSON.parse(ev.data);
      if (msg.id && pending.has(msg.id)) pending.get(msg.id)(msg);
      if (msg.method === "Runtime.exceptionThrown")
        consoleErrors.push(msg.params.exceptionDetails.text);
      if (msg.method === "Runtime.consoleAPICalled" && msg.params.type === "error")
        consoleErrors.push(String(msg.params.args.map((a) => a.value).join(" ")));
    };
    const send = (method, params = {}) => new Promise((res) => {
      const id = ++seq; pending.set(id, res);
      ws.send(JSON.stringify({ id, method, params }));
    });
    await send("Runtime.enable");
    await send("Page.enable");
    await send("Emulation.setDeviceMetricsOverride",
      { width: 1400, height: 950, deviceScaleFactor: 1, mobile: false });
    await send("Page.navigate", { url: SERVICE });
    await sleep(2500);

    const evalJson = async (expr) => {
      const r = await send("Runtime.evaluate",
        { expression: expr, awaitPromise: true, returnByValue: true });
      if (r.result && r.result.exceptionDetails) return { __err: r.result.exceptionDetails.text };
      return r.result ? r.result.result.value : undefined;
    };

    // A（直连任务，有网页成品）：对话仍是第一视线（chat-mode），但「预览」
    // 以胶囊在场——点开后面板可见、iframe 真跑起来
    const detA = await evalJson(`(async () => {
      sideOpenTask("task-a");
      await new Promise((r) => setTimeout(r, 1500));
      const tab = document.getElementById("rd-tab-preview");
      return {
        active: ((document.querySelector("#rd-tabs .rd-tab.active") || {}).dataset || {}).tab || "",
        tabHidden: tab ? tab.classList.contains("hidden") : true,
        chatNavPills: Array.from(document.querySelectorAll("#rd-chat-nav .cn-pill")).map((b) => b.textContent.trim()),
      };
    })()`);
    check("详情 A：直连任务默认落对话，预览胶囊在场",
      detA.active === "chat" && detA.tabHidden === false &&
      detA.chatNavPills.some((p) => p.startsWith("预览")), JSON.stringify(detA));

    const opened = await evalJson(`(async () => {
      const pill = Array.from(document.querySelectorAll("#rd-chat-nav .cn-pill"))
        .find((b) => b.textContent.indexOf("预览") === 0);
      if (!pill) return { err: "no preview pill", pills:
        Array.from(document.querySelectorAll("#rd-chat-nav .cn-pill")).map((b) => b.textContent.trim()) };
      pill.click();
      await new Promise((r) => setTimeout(r, 600));
      const pane = document.getElementById("rd-pane-preview");
      const rect = pane.getBoundingClientRect();
      const frame = document.getElementById("rd-pv-frame");
      return {
        active: ((document.querySelector("#rd-tabs .rd-tab.active") || {}).dataset || {}).tab || "",
        paneW: Math.round(rect.width), paneH: Math.round(rect.height),
        frameSrc: frame ? frame.getAttribute("src") : "",
        tabs: Array.from(document.querySelectorAll("#rd-pv-tabs .pv-tab")).map((b) => b.dataset.pv),
      };
    })()`);
    check("详情 A：点胶囊切到预览且面板有真实几何尺寸（非坍缩）",
      opened.active === "preview" && opened.paneW > 300 && opened.paneH > 200,
      `active=${opened.active} w=${opened.paneW} h=${opened.paneH}`);
    check("详情 A：iframe 挂上带令牌的预览地址",
      String(opened.frameSrc).includes(`/preview/${RUN_A}/`) && String(opened.frameSrc).includes("index.html"),
      opened.frameSrc);
    check("详情 A：子页签 = 运行视图 + 3 个文件",
      opened.tabs.length === 4 && opened.tabs[0] === "run", JSON.stringify(opened.tabs));

    // iframe 内部：相对 style.css / script.js 都解析到位（脚本改写 + 样式生效）
    await sleep(1200);
    const inner = await evalJson(`(() => {
      const f = document.getElementById("rd-pv-frame");
      if (!f || !f.contentDocument) return { err: "no frame doc" };
      const h = f.contentDocument.getElementById("pv-title");
      return {
        title: h ? h.textContent : "",
        color: h ? f.contentDocument.defaultView.getComputedStyle(h).color : "",
        base: (f.contentDocument.querySelector("base") || {}).href || "",
      };
    })()`);
    check("iframe 内：入口页脚本（相对 script.js）真的执行了",
      inner.title === "由预览脚本改写", JSON.stringify(inner));
    check("iframe 内：相对 style.css 生效（标题染上橙色）",
      /rgb\(255,\s*140,\s*0\)/.test(inner.color || ""), inner.color);
    check("iframe 内：<base> 指向带令牌的挂载路径",
      String(inner.base || "").includes(`/preview/${RUN_A}/${token}/`), inner.base);

    // 文件页签 → 源码视图；「刷新」重挂 iframe（src 带时间戳）
    const tabAct = await evalJson(`(async () => {
      const before = document.getElementById("rd-pv-frame").getAttribute("src");
      document.querySelector('#rd-pv-tabs .pv-tab[data-pv="script.js"]').click();
      await new Promise((r) => setTimeout(r, 900));
      const code = document.getElementById("rd-pv-code");
      const codeTxt = code ? code.textContent : "";
      document.querySelector('#rd-pv-tabs .pv-tab[data-pv="run"]').click();
      await new Promise((r) => setTimeout(r, 400));
      document.getElementById("rd-pv-reload").click();
      await new Promise((r) => setTimeout(r, 400));
      const after = document.getElementById("rd-pv-frame").getAttribute("src");
      return { codeTxt: codeTxt.slice(0, 60), hasFrame: !!document.getElementById("rd-pv-frame"),
        srcChanged: before !== after, openHref: document.getElementById("rd-pv-open").getAttribute("href") };
    })()`);
    check("文件页签：script.js 源码视图有内容", (tabAct.codeTxt || "").includes("pv-title"),
      tabAct.codeTxt);
    check("「刷新」重挂 iframe（src 变化）", tabAct.srcChanged === true && tabAct.hasFrame,
      JSON.stringify(tabAct));
    check("「打开新窗口」指向预览挂载地址",
      String(tabAct.openHref || "").startsWith(`/preview/${RUN_A}/`), tabAct.openHref);

    // B（无网页成品）：预览页签整体隐藏，且 iframe 不留上一家的残影
    const detB = await evalJson(`(async () => {
      sideOpenTask("task-b");
      await new Promise((r) => setTimeout(r, 1500));
      const pane = document.getElementById("rd-pane-preview");
      return {
        active: ((document.querySelector("#rd-tabs .rd-tab.active") || {}).dataset || {}).tab || "",
        tabHidden: document.getElementById("rd-tab-preview").classList.contains("hidden"),
        paneHidden: pane ? pane.classList.contains("hidden") : true,
        frame: !!document.getElementById("rd-pv-frame"),
        badge: (document.querySelector('#rd-tabs .rd-tab[data-tab="preview"] .rd-badge') || {}).textContent || "",
      };
    })()`);
    check("详情 B：预览页签与面板都收起", detB.tabHidden && detB.paneHidden, JSON.stringify(detB));
    check("详情 B：没有残留 iframe", detB.frame === false, JSON.stringify(detB));

    // C（非直连 code 任务 + 网页成品）：跑完自动落在「预览」——借鉴对话式
    // 编程产品「跑完先看东西跑起来」
    const detC = await evalJson(`(async () => {
      sideOpenTask("task-c");
      await new Promise((r) => setTimeout(r, 1500));
      const pane = document.getElementById("rd-pane-preview");
      const rect = pane.getBoundingClientRect();
      return {
        active: ((document.querySelector("#rd-tabs .rd-tab.active") || {}).dataset || {}).tab || "",
        tabHidden: document.getElementById("rd-tab-preview").classList.contains("hidden"),
        w: Math.round(rect.width), h: Math.round(rect.height),
        frameSrc: (document.getElementById("rd-pv-frame") || {}).src || "",
      };
    })()`);
    check("详情 C：终态 code 任务自动落在预览（面板可见）",
      detC.active === "preview" && detC.tabHidden === false && detC.w > 300 && detC.h > 200,
      JSON.stringify(detC));

    check("无控制台错误", consoleErrors.length === 0, consoleErrors.join(" | "));
    if (results.some((r) => !r.ok))
      console.log("  [console] " + (consoleErrors.join(" | ") || "(无记录)"));
  } finally {
    // 杀进程树（/T）：child.kill() 只杀直接子进程，Edge 的渲染器/服务子进程
    // 会滞留成孤儿——本机内存被测试残留吃满的根源（用户明确要求测试后清场）
    const killTree = (pid) => {
      if (!pid) return;
      try { spawn("taskkill", ["/PID", String(pid), "/T", "/F"], { stdio: "ignore" }); }
      catch (e) { /* ignore */ }
    };
    killTree(edge.pid);
    killTree(srv.pid);
    await sleep(600);
    try { rmSync(dataDir, { recursive: true, force: true }); } catch (e) { /* win 锁 */ }
    try { rmSync(profile, { recursive: true, force: true }); } catch (e) { /* win 锁 */ }
    // 清场自证：端口必须归还，残留即报告失败（下次并行跑会双绑互驱）
    for (const port of [SERVICE_PORT, CDP_PORT]) {
      let free = true;
      try {
        const r = await fetch(`http://127.0.0.1:${port}/json/version`, { signal: AbortSignal.timeout(1200) });
        free = !r.ok;
      } catch (e) { free = true; }   // 连不上 = 已无人监听 = 已归还
      check(`清场：端口 ${port} 已归还`, free);
    }
  }
  const bad = results.filter((r) => !r.ok).length;
  console.log(bad ? `\nFAIL ${bad}/${results.length}` : `\nPASS ${results.length}/${results.length}`);
  process.exit(bad ? 1 : 0);
}

let send = async () => {};
main().catch((e) => { console.error(e); process.exit(1); });
