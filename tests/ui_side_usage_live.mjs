/* 侧栏用量条实时化 UI 验证：自起临时服务（预置台账种子）→ Edge headless + CDP
 * 打开主页 → 断言用量条渲染 → 追加台账记录后 force 刷新 → 断言数字变化 +
 * su-live 脉冲 + 在飞/节流守卫 + 忙/闲动态节奏。SSE event: usage 的服务端
 * 事件链路由 _probe_sse_usage.py 钉住，这里验证前端消费侧。
 * 结束按 PID 清理浏览器与服务进程、验证端口归还。 */
import { spawn } from "node:child_process";
import { writeFileSync, appendFileSync, mkdirSync, mkdtempSync, rmSync, readFileSync } from "node:fs";
import { tmpdir } from "node:os";
import { join, dirname } from "node:path";
import { fileURLToPath } from "node:url";

const PORT = 18797;
const SERVICE = "http://127.0.0.1:" + PORT;
const CDP_PORT = 9338;
const ROOT = join(dirname(fileURLToPath(import.meta.url)), "..");
const EDGE_CANDIDATES = [
  "C:\\Program Files (x86)\\Microsoft\\Edge\\Application\\msedge.exe",
  "C:\\Program Files\\Microsoft\\Edge\\Application\\msedge.exe",
];

const results = [];
function check(name, cond, detail = "") {
  results.push({ name, ok: !!cond });
  console.log((cond ? "  ✓ " : "  ✗ ") + name + (cond ? "" : "　— " + String(detail).slice(0, 200)));
}
const sleep = (ms) => new Promise((r) => setTimeout(r, ms));

function isoDay(offset = 0) {
  return new Date(Date.now() + offset * 86400000).toISOString().slice(0, 10);
}

function seedUsage(dataDir) {
  const ym = isoDay().slice(0, 7).replace("-", "");
  const rec = (over) => JSON.stringify(Object.assign({
    ts: isoDay() + " 12:00:00", day: isoDay(), source: "pipeline", run_id: "r-ui",
    task_id: "t-ui", task_type: "code", role: "implement", agent: "codex-cli",
    agent_label: "Codex CLI", tool: "codex", model: "gpt-ui", provider: "",
    ok: true, duration_s: 5, cost_usd: 0.02,
    input: 1000, output: 400, cached: 200, reasoning: 0, total: 1600,
  }, over));
  mkdirSync(join(dataDir, "usage"), { recursive: true });
  writeFileSync(join(dataDir, "usage", "usage-" + ym + ".jsonl"), rec() + "\n", "utf8");
  return join(dataDir, "usage", "usage-" + ym + ".jsonl");
}

async function main() {
  // 0) 静态断言：SSE usage 事件监听器已接线
  const appSrc = readFileSync(join(ROOT, "app", "ui", "app.js"), "utf8");
  check("app.js 已接线 event: usage 监听", appSrc.includes('addEventListener("usage"'));

  const tmp = mkdtempSync(join(tmpdir(), "tutti-susage-"));
  const dataDir = join(tmp, "data");
  const usageFile = seedUsage(dataDir);

  const svc = spawn("python", ["-X", "utf8", join(ROOT, "app", "main.py"),
    "--port", String(PORT), "--no-browser", "--host", "127.0.0.1"], {
    cwd: ROOT, stdio: "ignore",
    env: Object.assign({}, process.env, { TUTTI_DATA: dataDir, PYTHONPATH: ROOT }),
  });
  let edge = null, ws = null;
  try {
    let up = false;
    for (let i = 0; i < 40 && !up; i++) {
      await sleep(500);
      try { up = (await fetch(SERVICE + "/api/state")).status === 200; } catch (e) { /* wait */ }
    }
    check("临时服务启动", up);
    if (!up) throw new Error("service not up");

    edge = spawn(EDGE_CANDIDATES.find(() => true), [
      "--headless=new", "--disable-gpu", "--no-first-run",
      `--user-data-dir=${join(tmp, "profile")}`, `--remote-debugging-port=${CDP_PORT}`,
      "--window-size=1400,950", "about:blank",
    ], { stdio: "ignore" });

    let target = null;
    for (let i = 0; i < 30 && !target; i++) {
      await sleep(500);
      try {
        const list = await (await fetch(`http://127.0.0.1:${CDP_PORT}/json/list`)).json();
        target = list.find((t) => t.type === "page");
      } catch (e) { /* Edge 未就绪 */ }
    }
    check("Edge headless 启动并开放 CDP", !!target);
    if (!target) throw new Error("no CDP target");

    ws = new WebSocket(target.webSocketDebuggerUrl);
    await new Promise((res, rej) => { ws.onopen = res; ws.onerror = rej; });
    let seq = 0;
    const pending = new Map();
    ws.onmessage = (ev) => {
      const msg = JSON.parse(ev.data);
      if (msg.id && pending.has(msg.id)) pending.get(msg.id)(msg);
    };
    const send = (method, params = {}) => new Promise((res) => {
      const id = ++seq;
      pending.set(id, res);
      ws.send(JSON.stringify({ id, method, params }));
    });
    const evalJs = async (expr, awaitP = false) => {
      const r = await send("Runtime.evaluate",
        { expression: expr, returnByValue: true, awaitPromise: awaitP });
      return r.result?.result?.value;
    };

    await send("Page.enable");
    await send("Page.navigate", { url: SERVICE + "/" });
    await sleep(3500);  // 首屏 sideUsageRefresh 异步拉台账

    // 1) 初始渲染：种子记录 1600 tokens · 1 次
    const tok0 = await evalJs(`document.getElementById("su-tok").textContent`);
    check("初始用量条渲染种子数据", /1,?600/.test(tok0) && /1\s*次/.test(tok0), tok0);

    // 2) 台账追加一条（模拟运行中新的记账落盘）→ force 刷新（usage 事件同路径）
    appendFileSync(usageFile, JSON.stringify({
      ts: isoDay() + " 12:30:00", day: isoDay(), source: "pipeline", run_id: "r-ui2",
      task_id: "t-ui", task_type: "code", role: "review", agent: "claude-code",
      agent_label: "Claude Code", tool: "claude", model: "claude-ui", provider: "",
      ok: true, duration_s: 3, cost_usd: 0.01,
      input: 200, output: 200, cached: 0, reasoning: 0, total: 400,
    }) + "\n", "utf8");

    // fetch 计数器：守卫与节流的观察面
    await evalJs(`(function(){
      window.__usageFetches = 0;
      const of = window.fetch;
      window.fetch = function(u, o) {
        if (String(u).indexOf("/api/usage?") >= 0) window.__usageFetches++;
        return of.apply(this, arguments);
      };
      return "ok";
    })()`);

    // 并发双发：在飞保护必须折叠成一次真实请求
    await evalJs(`Promise.all([sideUsageLoad(true), sideUsageLoad(true)])`, true);
    await sleep(600);
    const fetches1 = await evalJs(`window.__usageFetches`);
    check("并发双发只打一次 /api/usage（在飞保护）", fetches1 === 1, "fetches=" + fetches1);

    // 紧接第三次（3s 节流窗口内）也必须跳过
    await evalJs(`sideUsageLoad(true)`, true);
    const fetches2 = await evalJs(`window.__usageFetches`);
    check("3s 节流窗口内的重复 force 被跳过", fetches2 === 1, "fetches=" + fetches2);

    // 3) 数字更新 + 脉冲
    const tok1 = await evalJs(`document.getElementById("su-tok").textContent`);
    check("追加后 tokens 累计=2000", /2,?000/.test(tok1), tok1);
    check("追加后次数累计=2 次", /2\s*次/.test(tok1), tok1);
    const live1 = await evalJs(`document.getElementById("side-usage").classList.contains("su-live")`);
    check("数字变化触发 su-live 脉冲", live1 === true);
    await sleep(1400);
    const live2 = await evalJs(`document.getElementById("side-usage").classList.contains("su-live")`);
    check("脉冲 ~1.1s 后自动移除", live2 === false);

    // 4) 动态节奏：闲（无在跑 run）时 10s 前的快照不触发拉取；忙（有 running）时触发
    await evalJs(`(function(){
      S.sideUsageAt = Date.now() - 10000;   // 10s 前：闲 60s 档内 / 忙 3s 档外
      S.state = S.state || {}; S.state.runs = [];
      window.__usageFetches = 0;
      return "ok";
    })()`);
    await evalJs(`sideUsageLoad()`, true);
    const fetchesIdle = await evalJs(`window.__usageFetches`);
    check("空闲（无 running）10s 内不拉台账", fetchesIdle === 0, "fetches=" + fetchesIdle);
    await evalJs(`(function(){ S.state.runs = [{ status: "running" }]; return "ok"; })()`);
    await evalJs(`sideUsageLoad()`, true);
    const fetchesBusy = await evalJs(`window.__usageFetches`);
    check("有 running 时同快照立即拉台账（3s 档）", fetchesBusy === 1, "fetches=" + fetchesBusy);

  } finally {
    if (ws) { try { ws.close(); } catch (e) { /* ignore */ } }
    if (edge) {
      spawn("taskkill", ["/PID", String(edge.pid), "/T", "/F"], { stdio: "ignore" });
    }
    if (svc) {
      spawn("taskkill", ["/PID", String(svc.pid), "/T", "/F"], { stdio: "ignore" });
    }
    await sleep(1500);
    // 端口归还自证（按 PID 杀树，不按映像名连坐）
    let freed = false;
    const net = spawn("netstat", ["-ano"], { stdio: "ignore" });
    try { net.unref(); } catch (e) { /* ignore */ }
    try {
      await fetch(SERVICE + "/api/state", { signal: AbortSignal.timeout(1500) });
    } catch (e) {
      freed = true;  // 连不上 = 已归还
    }
    check("临时服务已退出、端口已归还", freed);
    if (results.every((r) => r.ok)) {
      rmSync(tmp, { recursive: true, force: true });
    } else {
      console.log("（失败现场保留：%s）", tmp);
    }
  }

  const fails = results.filter((r) => !r.ok);
  console.log("\n===== 侧栏用量条实时化 UI 探针：%d 通过 / %d 失败 =====",
    results.length - fails.length, fails.length);
  if (fails.length) {
    console.log("失败项：", fails.map((r) => r.name));
    process.exit(1);
  }
}

main().catch((e) => { console.error(e); process.exit(1); });
