/* 一次性核验：蜂巢卡「当前使用模型 + 思考过程」行（Edge headless + CDP，临时端口 18890）。
 * 启动收尸（recover_orphaned_runs）会吞掉种盘的 running run，故分两层：
 *   A. 种盘终态任务：步骤列表 st-model 徽章 + 终态格 who 带模型（真实磁盘数据）。
 *   B. 页内 stub fetch 拦 /log 接口 + 直接 renderHive 造运行格：真实 hiveTick
 *      轮询代码端到端跑——断言 hc-who「工具 · 模型」、hc-think 取最近【思考】、
 *      hc-tail 跳过二进制残渣行取【消息】。 */
import { spawn, execSync } from "node:child_process";
import { mkdtempSync, rmSync, mkdirSync, writeFileSync } from "node:fs";
import { tmpdir } from "node:os";
import { join, dirname } from "node:path";
import { fileURLToPath } from "node:url";

const PORT = 18890;
const SERVICE = "http://127.0.0.1:" + PORT;
const CDP_PORT = 9366;
const EDGE = "C:\\Program Files (x86)\\Microsoft\\Edge\\Application\\msedge.exe";
const ROOT = join(dirname(fileURLToPath(import.meta.url)), "..");
const sleep = (ms) => new Promise((r) => setTimeout(r, ms));

const results = [];
const check = (name, cond, detail = "") => {
  results.push({ name, ok: !!cond });
  console.log((cond ? "  ✓ " : "  ✗ ") + name + (cond ? "" : "　— " + String(detail).slice(0, 300)));
};

async function main() {
  const tmp = mkdtempSync(join(tmpdir(), "tutti-hivemodel-"));
  const dataDir = join(tmp, "data");
  const taskId = "thive1", runId = "rhive1";
  mkdirSync(join(dataDir, "tasks"), { recursive: true });
  mkdirSync(join(dataDir, "runs", runId, "steps"), { recursive: true });
  writeFileSync(join(dataDir, "tasks", taskId + ".json"), JSON.stringify({
    id: taskId, type: "code", engine: "code", title: "模型思考行验证", goal: "造数：终态步骤",
    workdir: join(tmp, "wd"), git_rev: "", git_state: "",
    status: "done", created_at: "2026-09-23 10:00:00", attachments: [],
    mode: "auto", difficulty: "auto", implementer: "", verify_command: "",
  }), "utf-8");
  const steps = [
    { n: 1, role: "plan", agent: "a1", agent_label: "Codex CLI", note: "", model: "glm-5.3",
      status: "done", summary: "计划已出", started_at: "10:00:02", ended_at: "10:00:30",
      duration_s: 28.0, log: "" },
    { n: 2, role: "draft", agent: "a2", agent_label: "Claude Code", note: "", model: "claude-sonnet-4-5",
      status: "done", summary: "草稿落盘", started_at: "10:00:31", ended_at: "10:05:00",
      duration_s: 269.0, log: "" },
  ];
  writeFileSync(join(dataDir, "runs", runId, "run.json"), JSON.stringify({
    id: runId, kind: "orchestration", title: "模型思考行验证", task_id: taskId,
    status: "done", steps, messages: [], created_at: "2026-09-23 10:00:01",
    started_at: "2026-09-23 10:00:01", ended_at: "2026-09-23 10:05:00",
    cost_usd: 0, tokens: 0, error: "", verdict: null, summary: "", git: null,
  }), "utf-8");

  let svc = null, edge = null, ws = null;
  try {
    svc = spawn("python", ["-X", "utf8", join(ROOT, "app", "main.py"), "--port", String(PORT),
      "--no-browser", "--host", "127.0.0.1"], {
      env: { ...process.env, TUTTI_DATA: dataDir, PYTHONPATH: ROOT }, cwd: ROOT, stdio: "ignore" });
    let up = false;
    for (let i = 0; i < 40 && !up; i++) {
      await sleep(500);
      try { up = (await fetch(SERVICE + "/api/state")).ok; } catch (e) { /* wait */ }
    }
    check("临时服务启动", up);
    edge = spawn(EDGE, ["--headless=new", "--disable-gpu", "--no-first-run",
      `--user-data-dir=${join(tmp, "p")}`, `--remote-debugging-port=${CDP_PORT}`,
      "--window-size=1440,1000", "about:blank"], { stdio: "ignore" });
    let target = null;
    for (let i = 0; i < 30 && !target; i++) {
      await sleep(500);
      try {
        const list = await (await fetch(`http://127.0.0.1:${CDP_PORT}/json/list`)).json();
        target = list.find((t) => t.type === "page");
      } catch (e) { /* wait */ }
    }
    check("Edge headless 就绪", !!target);
    ws = new WebSocket(target.webSocketDebuggerUrl);
    await new Promise((res, rej) => { ws.onopen = res; ws.onerror = rej; });
    let seq = 0; const pending = new Map();
    ws.onmessage = (ev) => { const m = JSON.parse(ev.data); if (m.id && pending.has(m.id)) pending.get(m.id)(m); };
    const send = (method, params = {}) => new Promise((res) => { const id = ++seq; pending.set(id, res); ws.send(JSON.stringify({ id, method, params })); });
    const evalJs = async (expr) => {
      const r = await send("Runtime.evaluate", { expression: expr, returnByValue: true, awaitPromise: true });
      if (r.result?.exceptionDetails) throw new Error(JSON.stringify(r.result.exceptionDetails).slice(0, 300));
      return r.result?.result?.value;
    };
    await send("Page.enable");
    await send("Page.navigate", { url: SERVICE + "/" });
    await sleep(3500);

    /* ---- A. 种盘终态：st-model 徽章 + 终态格 who 带模型 ---- */
    await evalJs(`sideOpenTask("${taskId}")`);
    await sleep(1500);
    const hzA = JSON.parse(await evalJs(`JSON.stringify({
      whoDone: (document.querySelector("#rd-hive-cells .hive-cell.st-done .hc-who") || {}).textContent || "",
      badges: [...document.querySelectorAll("#rd-steps .step .st-model")].map((b) => b.textContent) })`));
    check("终态格 who 带模型（Codex CLI · glm-5.3）",
      hzA.whoDone.includes("Codex CLI") && hzA.whoDone.includes("glm-5.3"), JSON.stringify(hzA));
    check("步骤列表 st-model 徽章两个且含模型名",
      hzA.badges.length === 2 && hzA.badges.includes("glm-5.3") && hzA.badges.includes("claude-sonnet-4-5"),
      JSON.stringify(hzA));

    /* ---- B. 页内 stub：拦截 /log 接口，renderHive 造运行格，真实 hiveTick 端到端 ---- */
    const stubRes = await evalJs(`(function(){
      const LOG = [
        "— 会话启动（model=claude-sonnet-4-5）—",
        "0s\\\\u0000t\\\\u0000\\\\u0000\\\\u0000\\uFFFDM\\\\u0000...t\\\\u0000",
        "【思考】先拆解需求：贪吃蛇核心是网格与方向队列，DOM 用 CSS Grid 摆 20×20 格。",
        "【消息】好的，我来实现贪吃蛇，先建 HTML 骨架再写移动循环。"
      ].join("\\n");
      const realFetch = window.fetch;
      window.fetch = function(url, opts) {
        if (String(url).indexOf("/api/runs/${runId}/log") !== -1) {
          return Promise.resolve(new Response(JSON.stringify(
            { log: LOG, step_status: "running", run_status: "running" }),
            { status: 200, headers: { "Content-Type": "application/json" } }));
        }
        return realFetch.apply(this, arguments);
      };
      const now = new Date();
      const hhmmss = [now.getHours(), now.getMinutes(), now.getSeconds()]
        .map((v) => String(v).padStart(2, "0")).join(":");
      window.renderHive({ id: "${runId}", status: "running", steps: [
        { n: 1, role: "plan", agent: "a1", agent_label: "Codex CLI", model: "glm-5.3",
          status: "done", summary: "计划已出", duration_s: 28, log: "" },
        { n: 2, role: "draft", agent: "a2", agent_label: "Claude Code", model: "claude-sonnet-4-5",
          status: "running", summary: "", started_at: hhmmss, log: "steps/02-draft-a2.log" }
      ]});
      return "stubbed";
    })()`);
    check("stub 注入 + renderHive 调用成功", stubRes === "stubbed", String(stubRes));
    await sleep(1500);
    const hz1 = JSON.parse(await evalJs(`JSON.stringify({
      run: !!document.querySelector("#rd-hive-cells .hive-cell.st-running"),
      who: (document.querySelector("#rd-hive-cells .hive-cell.st-running .hc-who") || {}).textContent || "" })`));
    check("运行格存在且 hc-who 显示「Claude Code · claude-sonnet-4-5」",
      hz1.run && hz1.who.includes("Claude Code") && hz1.who.includes("claude-sonnet-4-5"), JSON.stringify(hz1));

    // 等 hiveTick（2s 周期）拉到 stub 日志：思考行出现、尾巴取【消息】不取残渣
    await sleep(4500);
    const hz2 = JSON.parse(await evalJs(`JSON.stringify({
      think: (document.querySelector("#rd-hive-cells .hive-cell.st-running .hc-think") || {}).textContent || "",
      tail: (document.querySelector("#rd-hive-cells .hive-cell.st-running .hc-tail") || {}).textContent || "" })`));
    check("hc-think 取到最近【思考】片段",
      hz2.think.includes("【思考】") && hz2.think.includes("方向队列"), JSON.stringify(hz2));
    check("hc-tail 显示【消息】行", hz2.tail.includes("【消息】"), JSON.stringify(hz2));
    check("hc-tail 不含二进制残渣", !hz2.tail.includes("\\u0000") && !hz2.tail.includes("\uFFFD"), JSON.stringify(hz2));
    const doneThink = await evalJs(`String(document.querySelectorAll("#rd-hive-cells .hive-cell.st-done .hc-think").length)`);
    check("终态格无思考行", doneThink === "0", doneThink);

    // 收场：清掉定时器与假运行格
    await evalJs(`window.renderHive({ id: "${runId}", status: "failed", steps: [] }); "cleaned"`);
  } finally {
    try { if (ws) ws.close(); } catch (e) { /* ignore */ }
    for (const proc of [edge, svc]) {
      if (proc && proc.pid) {
        try { execSync(`taskkill /F /T /PID ${proc.pid}`, { stdio: "ignore", timeout: 8000 }); }
        catch (e) { /* 已退出 */ }
      }
    }
    await sleep(800);
    // 端口归还自证：只认 LISTENING（TIME_WAIT 是内核正常回收，不算残留）
    let leftover = "";
    try {
      leftover = execSync(`netstat -ano | findstr "LISTENING" | findstr ":${PORT} :${CDP_PORT}"`,
        { encoding: "utf-8", timeout: 8000 }).trim();
    } catch (e) { /* 无残留 */ }
    check("端口归还（无 LISTENING 残留）", !leftover, leftover);
    try { rmSync(tmp, { recursive: true, force: true }); } catch (e) { /* ignore */ }
  }
  const bad = results.filter((r) => !r.ok).length;
  console.log(bad ? `\n${bad} 项未过` : "\n全部通过");
  process.exit(bad ? 1 : 0);
}

main().catch((e) => { console.error("FATAL", e); process.exit(1); });
