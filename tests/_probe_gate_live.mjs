/* 只读验证真实 8765 上新弹窗的实际渲染：打开页面→用真实任务 id 调 pbGateChoice→
 * dump 弹窗 DOM→关闭。零写接口调用（pbGateChoice 只读 S.state 画框）。 */
import { spawn } from "node:child_process";
import { mkdtempSync, rmSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";

const CDP_PORT = 9374;
const EDGE_CANDIDATES = [
  "C:\\Program Files (x86)\\Microsoft\\Edge\\Application\\msedge.exe",
  "C:\\Program Files\\Microsoft\\Edge\\Application\\msedge.exe",
];
const sleep = (ms) => new Promise((r) => setTimeout(r, ms));

async function main() {
  const profile = mkdtempSync(join(tmpdir(), "tutti-live-"));
  const edge = spawn(EDGE_CANDIDATES.find((p) => true), [
    "--headless=new", "--disable-gpu", "--no-first-run",
    `--user-data-dir=${profile}`, `--remote-debugging-port=${CDP_PORT}`,
    "--window-size=1400,950", "http://127.0.0.1:8765/",
  ], { stdio: "ignore" });
  try {
    let target = null;
    for (let i = 0; i < 30 && !target; i++) {
      await sleep(500);
      try {
        const res = await fetch(`http://127.0.0.1:${CDP_PORT}/json/list`);
        target = (await res.json()).find((t) => t.type === "page");
      } catch (e) { /* Edge 未就绪 */ }
    }
    console.log("stage1 CDP:", !!target);
    if (!target) return;
    const ws = new WebSocket(target.webSocketDebuggerUrl);
    await new Promise((res, rej) => { ws.onopen = res; ws.onerror = rej; });
    let seq = 0; const pending = new Map();
    ws.onmessage = (ev) => { const m = JSON.parse(ev.data); if (m.id && pending.has(m.id)) pending.get(m.id)(m); };
    const send = (method, params = {}) => new Promise((res) => {
      const id = ++seq; pending.set(id, res); ws.send(JSON.stringify({ id, method, params }));
    });
    await send("Runtime.enable");
    await sleep(4000);   // 等 /api/state 首拉
    console.log("stage2 page loaded");

    const expr = `(async () => {
      const tid = "t-20260929-114752-9405";
      const tk = ((S.state || {}).tasks || []).find((x) => x.id === tid);
      const out = { taskFound: !!tk, serial: !!(tk && tk.serial), status: tk && tk.status,
                    jsNew: String(pbGateChoice).includes("busyHint") };
      const p = pbGateChoice({ message: "质量门禁拦截：测试" }, "发章前的质量门禁未通过", tid);
      await new Promise((r2) => setTimeout(r2, 80));
      const ask = document.getElementById("ask");
      const extra = ask.querySelector(".ask-extra");
      out.btnText = extra ? extra.textContent : null;
      out.disabled = extra ? extra.disabled : null;
      out.title = extra ? extra.title : "";
      out.hint = [...ask.querySelectorAll("#ask-body .ask-msg")].map((m) => m.textContent)
        .find((m) => m.includes("任务正在运行/排队中")) || "";
      document.getElementById("ask-no").click();
      await p.catch(() => {});
      return out;
    })()`;
    const raced = await Promise.race([
      send("Runtime.evaluate", { expression: expr, awaitPromise: true, returnByValue: true }),
      sleep(15000).then(() => null),
    ]);
    console.log("stage3 evaluate done");
    const val = raced && raced.result ? raced.result.result : { __err: "timeout/exception" };
    console.log(JSON.stringify(val, null, 2));
  } finally {
    try { ws && ws.close(); } catch (e) {}
    try { spawn("taskkill", ["/F", "/T", "/PID", String(edge.pid)], { stdio: "ignore" }); } catch (e) {}
    await sleep(800);
    try { rmSync(profile, { recursive: true, force: true }); } catch (e) {}
  }
  process.exit(0);   // WebSocket 会吊住事件循环，必须显式退出
}
let ws = null;
main().catch((e) => { console.error(e); process.exit(1); });
