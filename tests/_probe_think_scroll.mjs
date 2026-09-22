/* 思考面板滚动行为验证（stub timeline）：贴底跟随 / 上滑保持 / 回底恢复跟随 */
import { spawn } from "node:child_process";
import { mkdtempSync, rmSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";

const BASE = process.argv[2] || "http://127.0.0.1:19931";
const RUN = process.argv[3] || "r-stub";
const CDP_PORT = 9361;
const EDGE = "C:\\Program Files (x86)\\Microsoft\\Edge\\Application\\msedge.exe";
const sleep = (ms) => new Promise((r) => setTimeout(r, ms));

const profile = mkdtempSync(join(tmpdir(), "cb-scroll-"));
const proc = spawn(EDGE, ["--headless=new", "--disable-gpu", "--no-first-run",
  `--user-data-dir=${profile}`, `--remote-debugging-port=${CDP_PORT}`,
  "--window-size=1600,1000", "about:blank"], { stdio: "ignore" });
try {
  let target = null;
  for (let i = 0; i < 30 && !target; i++) {
    await sleep(500);
    try {
      const list = await (await fetch(`http://127.0.0.1:${CDP_PORT}/json/list`)).json();
      target = list.find((t) => t.type === "page");
    } catch (e) {}
  }
  const ws = new WebSocket(target.webSocketDebuggerUrl);
  await new Promise((res, rej) => { ws.onopen = res; ws.onerror = rej; });
  let seq = 0;
  const pending = new Map();
  ws.onmessage = (ev) => {
    const m = JSON.parse(ev.data);
    if (m.id && pending.has(m.id)) pending.get(m.id)(m);
  };
  const send = (method, params = {}) => new Promise((res) => {
    const id = ++seq;
    pending.set(id, res);
    ws.send(JSON.stringify({ id, method, params }));
  });
  const evalJs = async (expression) => {
    const r = await send("Runtime.evaluate",
      { expression, returnByValue: true, awaitPromise: true, userGesture: true });
    console.log("RAW:", JSON.stringify(r || null).slice(0, 260));
    if (r && r.exceptionDetails) console.log("EVAL-ERR:", r.exceptionDetails.text,
      String((r.exceptionDetails.exception || {}).description || "").slice(0, 200));
    return r && r.result && r.result.result ? r.result.result.value : undefined;
  };

  await send("Page.enable");
  await send("Runtime.enable");
  await send("Page.navigate", { url: BASE + "/" });
  await sleep(3500);
  // stub api()：/timeline 运行中载荷（思维链 3000 字），其余走原通道
  await evalJs(`
    window.THINK = Array.from({length: 60}, (_, i) =>
      "第" + i + "段思考：模型在推理本步骤应当如何处理输入，分析约束并检查边界条件。").join("\\n\\n");
    const orig = window.api;
    window.api = async (url, opts) => {
      if (String(url).includes("/timeline")) {
        return { run_id: ${JSON.stringify(RUN)}, status: "running", engine: "direct", items: [
          { kind: "agent", at: "11:58:00", who: "内置智能体", role: "chat",
            status: "done", text: Array.from({length: 40}, (_, i) =>
              "上一轮回答第 " + i + " 行：这里是历史回复的正文内容，用来把时间线撑到超出可视区。").join("
"),
            run: ${JSON.stringify(RUN)}, log: "" },
          { kind: "user", at: "12:00:00", text: "帮我写个教程" },
          { kind: "agent", at: "12:00:10", who: "内置智能体", role: "chat",
            status: "running", text: "", thinking: window.THINK,
            stream: "", activity: [], run: ${JSON.stringify(RUN)}, log: "" }
        ], result: null };
      }
      return orig(url, opts);
    };
    "stubbed"`);
  await evalJs(`switchTab("runs"); openRun(${JSON.stringify(RUN)});`);
  await sleep(2500);
  await evalJs(`S.rdTab = "chat";
                (document.querySelector('#rd-tabs .rd-tab[data-tab="chat"]')||{click(){}}).click();
                drawChatFlow(S.lastRun, null, true);`);
  await sleep(1200);

  const render = () => evalJs(`(async () => {
    const d = await api("/api/runs/" + encodeURIComponent(${JSON.stringify(RUN)}) + "/timeline");
    drawChatFlow(S.lastRun, d, true);
    const b = document.querySelector('[data-think-live="1"] .ct-body');
    return b ? { top: Math.round(b.scrollTop), sh: b.scrollHeight, ch: b.clientHeight } : null;
  })()`);

  const f1 = await render();
  console.log("frame1(first render):", JSON.stringify(f1),
    "=> PINNED-BOTTOM:", !!(f1 && f1.sh - f1.top - f1.ch < 4));

  await evalJs(`(document.querySelector('[data-think-live="1"] .ct-body')).scrollTop = 120;`);
  const f2 = await render();
  console.log("frame2(user scrolled up to 120):", JSON.stringify(f2),
    "=> POSITION-KEPT:", !!(f2 && Math.abs(f2.top - 120) < 40));

  await evalJs(`(() => { const b = document.querySelector('[data-think-live="1"] .ct-body');
    b.scrollTop = b.scrollHeight; })();`);
  const f3 = await render();
  console.log("frame3(scroll back to bottom):", JSON.stringify(f3),
    "=> PINNED-BOTTOM:", !!(f3 && f3.sh - f3.top - f3.ch < 4));

  // 帧4：模拟「用户发送消息」——时间线滚到顶部读历史后 chatForceBottom 置位，
  // 下一次重绘必须强制把【外层时间线】贴底（不管先前位置）；思考面板内滚条不受影响
  await evalJs(`(() => { const fl = document.getElementById("rd-chat-flow");
    fl.scrollTop = 0; chatForceBottom = true; })();`);
  const f4 = await render();
  console.log("flow-diag:", await evalJs(`(() => { const fl = document.getElementById("rd-chat-flow");
    return JSON.stringify({ top: Math.round(fl.scrollTop), sh: fl.scrollHeight, ch: fl.clientHeight }); })()`));
  console.log("frame4(force bottom after send):", JSON.stringify(f4),
    "=> outer timeline follows via chatForceBottom (see flow-diag)");

  ws.close();
} finally {
  proc.kill();
  await sleep(800);
  try { rmSync(profile, { recursive: true, force: true }); } catch (e) {}
}
process.exit(0);
