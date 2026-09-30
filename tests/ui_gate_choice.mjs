/* 质量门禁三选一弹框（pbGateChoice）运行时验证：Edge headless + CDP，零依赖。
 * 覆盖三种任务态：serial+排队中 →「重新评审」置灰+自动续跑提示（不再无声藏按钮）；
 * serial+空闲失败 → 按钮可点、返回 "review"；非 serial → 无第三个按钮。
 * 临时数据目录 + 独立端口，不碰真实 data/ 与 8765。 */
import { spawn } from "node:child_process";
import { mkdtempSync, rmSync } from "node:fs";
import { tmpdir } from "node:os";
import { join, dirname } from "node:path";
import { fileURLToPath } from "node:url";

const SERVICE_PORT = 18815;
const CDP_PORT = 9371;
const SERVICE = `http://127.0.0.1:${SERVICE_PORT}`;
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

async function main() {
  const dataDir = mkdtempSync(join(tmpdir(), "tutti-gate-"));
  const srv = spawn("python", ["app/main.py", "--port", String(SERVICE_PORT)], {
    cwd: ROOT, stdio: "ignore",
    env: { ...process.env, TUTTI_DATA: dataDir },
  });
  const edgePath = EDGE_CANDIDATES.find((p) => true);
  const profile = mkdtempSync(join(tmpdir(), "tutti-cdp-"));
  const edge = spawn(edgePath, [
    "--headless=new", "--disable-gpu", "--no-first-run",
    `--user-data-dir=${profile}`, `--remote-debugging-port=${CDP_PORT}`,
    "--window-size=1400,950", "about:blank",
  ], { stdio: "ignore" });

  try {
    let up = false;
    for (let i = 0; i < 60 && !up; i++) {
      await sleep(500);
      try { const r = await fetch(`${SERVICE}/api/state`); up = r.ok; } catch (e) { /* retry */ }
    }
    check("临时服务就绪(18815)", up);

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
    await send("Page.navigate", { url: SERVICE });
    await sleep(2500);

    const evalJson = async (expr) => {
      const r = await send("Runtime.evaluate",
        { expression: expr, awaitPromise: true, returnByValue: true });
      if (r.result && r.result.exceptionDetails) return { __err: r.result.exceptionDetails.text };
      return r.result ? r.result.result.value : undefined;
    };

    const R = await evalJson(`(async () => {
      const out = {};
      const GATE_MSG = "质量门禁拦截：最近一次质量评审未达标；有效全局评审不足 2 名；存在未达标章节";
      // 三种任务态：排队中（带自动续跑时刻）/ 空闲失败 / 非连载
      S.state = { tasks: [
        { id: "t-busy", title: "连载·排队中", serial: { chapters: 10 }, status: "queued" },
        { id: "t-idle", title: "连载·失败", serial: { chapters: 10 }, status: "failed" },
        { id: "t-code", title: "代码任务", serial: null, status: "failed" },
      ], task_latest: { "t-busy": { resume_enqueue_at: "2026-09-30 14:44:08" } } };

      const openGate = async (taskId) => {
        const p = pbGateChoice({ message: GATE_MSG }, "发章前的质量门禁未通过", taskId);
        await new Promise((r) => setTimeout(r, 60));
        const ask = document.getElementById("ask");
        const extra = ask.querySelector(".ask-extra");
        const msgs = [...ask.querySelectorAll("#ask-body .ask-msg")].map((m) => m.textContent);
        return { p, ask, extra, msgs };
      };
      const closeGate = async () => {
        document.getElementById("ask-no").click();
        await new Promise((r) => setTimeout(r, 30));
      };

      // 1) serial + queued：按钮必须显出来（置灰）+ 写明出路，不能无声消失
      const busy = await openGate("t-busy");
      out.busyVisible = !busy.ask.classList.contains("hidden");
      out.busyHasBtn = !!busy.extra;
      out.busyBtnText = busy.extra ? busy.extra.textContent : "";
      out.busyDisabled = busy.extra ? busy.extra.disabled : null;
      out.busyTitle = busy.extra ? busy.extra.title : "";
      out.busyHint = busy.msgs.find((m) => m.includes("任务正在运行/排队中")) || "";
      out.busyHasTime = out.busyHint.includes("14:44");
      out.busyBtnCount = busy.ask.querySelectorAll(".ask-extra").length;
      // 置灰按钮点击不关闭（disabled 不派发 click）
      if (busy.extra) busy.extra.click();
      await new Promise((r) => setTimeout(r, 30));
      out.busyStillOpen = !busy.ask.classList.contains("hidden");
      await closeGate();

      // 2) serial + failed（空闲）：按钮可点 → Promise 解析 "review"
      const idle = await openGate("t-idle");
      out.idleHasBtn = !!idle.extra;
      out.idleDisabled = idle.extra ? idle.extra.disabled : null;
      out.idleHintAbsent = !idle.msgs.some((m) => m.includes("任务正在运行/排队中"));
      out.idleExplain = idle.msgs.some((m) => m.includes("只重写未达标章节"));
      if (idle.extra) idle.extra.click();
      out.idlePick = await idle.p;
      await new Promise((r) => setTimeout(r, 20));

      // 3) 非 serial：不给第三个按钮（历史行为保持）
      const code = await openGate("t-code");
      out.codeBtnCount = code.ask.querySelectorAll(".ask-extra").length;
      await closeGate();

      // 4) running 态同 queued：置灰 + 无时刻提示（task_latest 无 resume_enqueue_at）
      S.state.tasks[0].status = "running";
      delete S.state.task_latest["t-busy"];
      const run = await openGate("t-busy");
      out.runDisabled = run.extra ? run.extra.disabled : null;
      out.runHintNoTime = (run.msgs.find((m) => m.includes("任务正在运行/排队中")) || "").includes("预计") === false;
      await closeGate();
      return out;
    })()`);

    if (!R || R.__err) { check("浏览器端评估执行", false, JSON.stringify(R)); }
    else {
      check("排队中：弹框打开", R.busyVisible);
      check("排队中：「重新评审」按钮仍渲染（不再无声藏掉）", R.busyHasBtn, R.busyBtnCount);
      check("排队中：按钮置灰 disabled", R.busyDisabled === true, R.busyDisabled);
      check("排队中：置灰按钮带原因 title", (R.busyTitle || "").includes("等这轮跑完"), R.busyTitle);
      check("排队中：提示写明出路（等跑完再来发布）", (R.busyHint || "").includes("等这轮跑完再来点发布"), R.busyHint);
      check("排队中：提示带自动续跑时刻 14:44", R.busyHasTime, R.busyHint);
      check("排队中：只有一个 extra 按钮", R.busyBtnCount === 1, R.busyBtnCount);
      check("排队中：置灰按钮点击不生效（弹框不关）", R.busyStillOpen);
      check("空闲失败：按钮可点", R.idleHasBtn && R.idleDisabled === false, R.idleDisabled);
      check("空闲失败：不出现运行中提示", R.idleHintAbsent);
      check("空闲失败：保留「只重写未达标章」语义说明", R.idleExplain);
      check("空闲失败：点按返回 review", R.idlePick === "review", R.idlePick);
      check("非 serial：不给第三个按钮", R.codeBtnCount === 0, R.codeBtnCount);
      check("running（无续跑时刻）：置灰且提示不带「预计」", R.runDisabled === true && R.runHintNoTime);
    }
    check("无未捕获 JS 异常", consoleErrors.length === 0, consoleErrors.join(" | ").slice(0, 200));
  } finally {
    try { spawn("taskkill", ["/F", "/T", "/PID", String(edge.pid)], { stdio: "ignore" }); } catch (e) {}
    try { spawn("taskkill", ["/F", "/T", "/PID", String(srv.pid)], { stdio: "ignore" }); } catch (e) {}
    await sleep(600);
    try { rmSync(profile, { recursive: true, force: true }); } catch (e) {}
    try { rmSync(dataDir, { recursive: true, force: true }); } catch (e) {}
  }
  const bad = results.filter((r) => !r.ok).length;
  console.log(bad ? `\nFAILED ${bad}/${results.length}` : `\nOK ${results.length}/${results.length}`);
  process.exit(bad ? 1 : 0);
}

main().catch((e) => { console.error(e); process.exit(1); });
