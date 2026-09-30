/* 设置页整页滚不动修复核验（2026-09-30 实案）：
 * 对话态详情开着时（renderChatNav 给 main 挂 chat-fill = overflow:hidden 锁滚动），
 * 从设置运行列表直接切别的设置子页——switchTab/exitSettings 原先不关详情，
 * chat-fill 与 S.detailRunId 双残留：顶栏标题卡「运行详情」+ 目标子页整页滚不动。
 * 修法：switchTab（目标≠runs）/exitSettings 发现详情开着先 closeRun() 解锚。
 * 真实链路：种子 direct 任务 → 设置 runs 子页 openRun（chat-fill 真落上）→
 * 点「本机智能体」→ 断言滚动解锁/标题归位；另验 switchTab("runs") 仍保留详情、
 * exitSettings 同样解锚。前置：临时数据目录自起服务（同 _probe_rd_width 骨架）。
 */
import { spawn, execSync } from "node:child_process";
import { mkdtempSync } from "node:fs";
import { tmpdir } from "node:os";
import { join, dirname } from "node:path";
import { fileURLToPath } from "node:url";

const PORT = Number(process.env.TUTTI_TEST_PORT) || 18798;
const CDP_PORT = Number(process.env.TUTTI_TEST_CDP) || 9337;
const SERVICE = "http://127.0.0.1:" + PORT;
const EDGE = "C:\\Program Files (x86)\\Microsoft\\Edge\\Application\\msedge.exe";
const ROOT = join(dirname(fileURLToPath(import.meta.url)), "..");

const results = [];
const check = (n, c, d = "") => {
  results.push(!!c);
  console.log((c ? "  ✓ " : "  ✗ ") + n + (c ? "" : "　— " + String(d).slice(0, 300)));
};
const sleep = (ms) => new Promise((r) => setTimeout(r, ms));

async function main() {
  try {
    execSync(`netstat -ano | findstr ":${PORT} " | findstr "LISTENING"`, { stdio: "pipe" });
    console.error("端口 " + PORT + " 已被占用，先清理残留服务再跑");
    process.exit(2);
  } catch (e) { /* 空闲 */ }

  // CDP 口残留按「占用者且确系 msedge」定向清，绝不按映像名连坐
  try {
    const out = execSync(`netstat -ano | findstr ":${CDP_PORT} " | findstr "LISTENING"`, { stdio: "pipe" }).toString();
    const pids = [...new Set(out.split(/\r?\n/).map((l) => l.trim().split(/\s+/).pop()).filter(Boolean))];
    for (const pid of pids) {
      try {
        const img = execSync(`tasklist /FI "PID eq ${pid}" /FO CSV /NH`, { stdio: "pipe" }).toString();
        if (/msedge\.exe/i.test(img)) execSync(`taskkill /F /PID ${pid} /T`, { stdio: "pipe" });
      } catch (e) { /* 进程可能已退出 */ }
    }
    await sleep(1000);
  } catch (e) { /* 口上本就无人 */ }

  const dataDir = mkdtempSync(join(tmpdir(), "tutti-setscroll-"));
  const seed = spawn("python", ["-X", "utf8", join(ROOT, "tests", "_seed_direct_fixture.py")],
    { env: { ...process.env, TUTTI_DATA: dataDir }, cwd: ROOT, stdio: ["ignore", "pipe", "pipe"] });
  let seedOut = "";
  seed.stdout.on("data", (d) => { seedOut += String(d); });
  await new Promise((res) => seed.on("exit", res));
  const runId = (seedOut.match(/RUN_ID=(\S+)/) || [])[1] || "";
  check("种子任务/运行就绪", !!runId, seedOut.slice(0, 120));

  const svc = spawn("python", ["-X", "utf8", join(ROOT, "app", "main.py"), "--port", String(PORT),
    "--no-browser", "--host", "127.0.0.1"], {
    env: { ...process.env, TUTTI_DATA: dataDir, PYTHONPATH: ROOT }, cwd: ROOT, stdio: "ignore",
  });
  let up = false;
  for (let i = 0; i < 40 && !up; i++) {
    await sleep(500);
    try { up = (await fetch(SERVICE + "/api/state")).ok; } catch (e) { /* wait */ }
  }
  check("临时服务启动", up);

  const profile = mkdtempSync(join(tmpdir(), "tutti-setscroll-edge-"));
  const proc = spawn(EDGE, ["--headless=new", "--disable-gpu", "--no-first-run",
    "--disable-sync", "--disable-extensions", `--user-data-dir=${profile}`,
    `--remote-debugging-port=${CDP_PORT}`, "--window-size=1720,950", "about:blank"], { stdio: "ignore" });
  try {
    let target = null;
    for (let i = 0; i < 30 && !target; i++) {
      await sleep(500);
      try {
        const list = await fetch(`http://127.0.0.1:${CDP_PORT}/json/list`).then((r) => r.json());
        target = list.find((t) => t.type === "page" && t.url === "about:blank");
      } catch (e) { /* 未就绪 */ }
    }
    check("Edge headless + CDP 就绪", !!target);
    const ws = new WebSocket(target.webSocketDebuggerUrl);
    await new Promise((res, rej) => { ws.onopen = res; ws.onerror = rej; });
    let seq = 0;
    const pending = new Map();
    const send = (method, params = {}) => new Promise((res, rej) => {
      const id = ++seq;
      const timer = setTimeout(() => { pending.delete(id); rej(new Error("CDP 超时：" + method)); }, 25000);
      pending.set(id, (m) => { clearTimeout(timer); res(m); });
      ws.send(JSON.stringify({ id, method, params }));
    });
    ws.onmessage = (e) => {
      const m = JSON.parse(e.data);
      if (m.id && pending.has(m.id)) pending.get(m.id)(m);
      else if (m.method === "Page.javascriptDialogOpening")
        send("Page.handleJavaScriptDialog", { accept: true });
    };
    const evalJs = async (x) => {
      const r = await send("Runtime.evaluate", { expression: x, returnByValue: true, awaitPromise: true });
      const ex = r.result?.exceptionDetails;
      if (ex) throw new Error("页面抛错：" + (ex.exception?.description || ex.text || "").slice(0, 240));
      return r.result?.result?.value;
    };
    await send("Page.enable");
    await send("Page.navigate", { url: SERVICE + "/" });
    await sleep(4000);

    const mainState = () => evalJs(`(() => {
      const m = document.querySelector("main"); const cs = getComputedStyle(m);
      return JSON.stringify({ fill: m.classList.contains("chat-fill"), oy: cs.overflowY,
        title: document.getElementById("page-title").textContent,
        mode: document.body.classList.contains("settings-mode"),
        detailOpen: !document.getElementById("run-detail").classList.contains("hidden"),
        detailCtx: !!(S.detailRunId || S.detailTaskKey) });
    })()`);
    const st = (s) => JSON.parse(s);
    // 子页内容注入高塔，保证内容必然超出视口（目录/编排页在临时目录可能条目很少）
    const tower = () => evalJs(`(() => {
      const sub = [...document.querySelectorAll("#page-settings > .subpage")].find((el) => !el.classList.contains("hidden"));
      if (!sub) return "no-sub";
      let t = document.getElementById("probe-tower");
      if (!t) { t = document.createElement("div"); t.id = "probe-tower"; t.style.height = "3000px"; sub.appendChild(t); }
      return "ok";
    })()`);

    // ===== 复现用户路径：设置 → 运行记录 → 开对话态详情 → 切「本机智能体」 =====
    await evalJs(`switchTab("runs", "settings"); "ok"`);
    await sleep(600);
    await evalJs(`openRun(${JSON.stringify(runId)}); "ok"`);
    await sleep(1500);
    let s = st(await mainState());
    check("前置：详情开着且 main.chat-fill 已落上（复现锁滚动态）",
      s.detailOpen && s.fill && s.oy === "hidden" && s.title === "运行详情",
      JSON.stringify(s));

    // 点设置导航「本机智能体」（真点击，走 switchTab 全链路）
    await evalJs(`document.querySelector('.set-item[data-sub="agents"]').click(); "ok"`);
    await sleep(800);
    s = st(await mainState());
    check("切子页后详情已关（上下文不残留）", !s.detailOpen && !s.detailCtx, JSON.stringify(s));
    check("chat-fill 已摘、overflow-y 恢复 auto", !s.fill && s.oy === "auto", JSON.stringify(s));
    check("顶栏标题归位「本机智能体」", s.title === "本机智能体", s.title);

    await tower();
    const scrolled = await evalJs(`(() => {
      const m = document.querySelector("main");
      m.scrollTop = 400;
      return JSON.stringify({ want: 400, got: m.scrollTop,
        sh: m.scrollHeight, ch: m.clientHeight });
    })()`);
    const sc = JSON.parse(scrolled);
    check("设置子页整页可滚动（scrollTop 钉得住）",
      sc.got > 0 && sc.sh > sc.ch + 500, JSON.stringify(sc));

    // ===== 反向保护：切 runs 子页时详情必须保留（详情是 runs 子页的住户） =====
    await evalJs(`S.detailRunId = "probe-keep"; switchTab("runs"); JSON.stringify({ kept: S.detailRunId })`);
    await sleep(400);
    const kept = await evalJs(`S.detailRunId`);
    check("switchTab(runs) 不误关详情（守护只对非 runs 生效）", kept === "probe-keep", "detailRunId=" + kept);
    await evalJs(`S.detailRunId = null; "ok"`);

    // ===== exitSettings 同款守护：开着详情回表单也要解锚 =====
    await evalJs(`S.detailRunId = "probe-exit";
      document.querySelector("main").classList.add("chat-fill");
      exitSettings(); "ok"`);
    await sleep(600);
    s = st(await mainState());
    check("exitSettings 关详情+摘 chat-fill+回表单",
      !s.detailCtx && !s.fill && s.oy === "auto" && !s.mode, JSON.stringify(s));
  } finally {
    try { proc.kill(); } catch (e) { /* ignore */ }
    try { execSync(`taskkill /F /PID ${proc.pid} /T`, { stdio: "pipe" }); } catch (e) { /* ignore */ }
    try { execSync(`taskkill /F /PID ${svc.pid} /T`, { stdio: "pipe" }); } catch (e) { /* ignore */ }
  }
  await sleep(800);
  try {
    const line = execSync(`netstat -ano | findstr ":${PORT} " | findstr "LISTENING"`, { stdio: "pipe" }).toString();
    console.error("⚠ 端口未释放：" + line.trim());
  } catch (e) { console.log("  ✓ 端口已释放"); }

  const bad = results.filter((r) => !r).length;
  console.log("\n===== 设置页滚动修复探针：" + (results.length - bad) + " 通过 / " + bad + " 失败 =====");
  process.exit(bad ? 1 : 0);
}

main().catch((e) => { console.error("FATAL", e); process.exit(1); });
