/* 探针：重试/续写/新建任务后的跳转落点——左栏任务树不许被带进设置导航。
 * 覆盖全部 6 个入口的 DOM 级真实点击：
 *   A1 任务级详情 →「↻ 重试任务」按钮        （主视图 → 左栏不动）
 *   A2 侧栏右键菜单 →「↻ 重试任务」          （主视图 → 左栏不动）
 *   A3 run 级详情 →「↻ 重试任务」按钮        （主视图 → 左栏不动）
 *   A4 详情页「✎ 编辑重试」→ 表单提交创建     （主视图 → 左栏不动）
 *   A5 详情页「继续连载」按钮                （主视图 → 左栏不动）
 *   B  设置目录页调用 openRunInRuns          （设置模式 → 保持设置导航，run 详情铺开）
 * retry/continue/建任务的写接口用页面内 api 分发 stub（写操作不打真服务），
 * 读接口透传真实服务。独立端口 18841 / CDP 9363，临时数据目录，跑完即清。 */
import { spawn } from "node:child_process";
import { mkdtempSync, rmSync, mkdirSync, writeFileSync } from "node:fs";
import { tmpdir } from "node:os";
import { join, dirname } from "node:path";
import { fileURLToPath } from "node:url";

const PORT = 18841;
const SERVICE = "http://127.0.0.1:" + PORT;
const CDP_PORT = 9363;
const EDGE = ["C:\\Program Files (x86)\\Microsoft\\Edge\\Application\\msedge.exe",
  "C:\\Program Files\\Microsoft\\Edge\\Application\\msedge.exe"].find((p) => true);
const ROOT = join(dirname(fileURLToPath(import.meta.url)), "..");
const sleep = (ms) => new Promise((r) => setTimeout(r, ms));

const results = [];
const check = (name, cond, detail = "") => {
  results.push({ name, ok: !!cond });
  console.log((cond ? "  ✓ " : "  ✗ ") + name + (cond ? "" : "　— " + String(detail).slice(0, 300)));
};

async function main() {
  const tmp = mkdtempSync(join(tmpdir(), "tutti-retrynav-"));
  const dataDir = join(tmp, "data");
  mkdirSync(join(dataDir, "tasks"), { recursive: true });
  const mk = (taskId, runId, title, extra) => {
    writeFileSync(join(dataDir, "tasks", taskId + ".json"), JSON.stringify({
      id: taskId, type: "code", engine: "code", title, goal: "造数：" + title,
      workdir: join(tmp, "wd-" + taskId), git_rev: "", git_state: "",
      status: "failed", created_at: "2026-09-15 10:00:00", attachments: [],
      mode: "auto", difficulty: "auto", implementer: "", verify_command: "",
      ...(extra || {}),
    }), "utf-8");
    mkdirSync(join(dataDir, "runs", runId), { recursive: true });
    writeFileSync(join(dataDir, "runs", runId, "run.json"), JSON.stringify({
      id: runId, kind: "orchestration", title, task_id: taskId,
      status: "failed", steps: [], messages: [], created_at: "2026-09-15 10:00:01",
      started_at: "2026-09-15 10:00:01", ended_at: "2026-09-15 10:00:30",
      cost_usd: 0, tokens: 0, error: "工作区有未提交改动",
      verdict: null, summary: "", git: null,
    }), "utf-8");
  };
  mk("tedit1", "redit1", "失败态任务");
  mk("tser1", "rser1", "连载任务", { serial: { chapters: 4, words_per_chapter: 2500, variants: 1 } });

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

    // 页面内装 api 分发 stub：写接口（retry/continue/建任务）返回假 run_id，其余透传真服务
    await evalJs(`(() => { const orig = api; window.__origApi = orig;
      api = async (path, opts) => {
        const post = opts && opts.method === "POST";
        if (post && /\\/retry$/.test(path)) return { run_id: "redit1" };
        if (/continue-info$/.test(path)) return { can: true, last_chapter: 2, default_chapters: 2 };
        if (post && /\\/continue$/.test(path)) return { run_id: "redit1" };
        if (post && path === "/api/tasks") return { run_id: "redit1" };
        return orig(path, opts);
      }; return "stub-on"; })()`);
    const navState = `JSON.stringify({
      settingsMode: document.body.classList.contains("settings-mode"),
      detailVisible: !document.getElementById("run-detail").classList.contains("hidden"),
      treeVisible: (document.getElementById("sidebar") || {}).offsetParent !== null })`;

    const resetMain = `(() => { document.getElementById("run-detail").classList.add("hidden");
      document.body.classList.remove("settings-mode"); })()`;

    // A1 任务级详情 →「↻ 重试任务」按钮
    await evalJs(`sideOpenTask("tedit1")`);
    await sleep(1200);
    const pre1 = JSON.parse(await evalJs(`JSON.stringify({ sm: document.body.classList.contains("settings-mode"),
      btn: !document.getElementById("btn-retry").classList.contains("hidden") })`));
    check("A1 前置：任务级详情按钮可见且主视图", !pre1.sm && pre1.btn, JSON.stringify(pre1));
    await evalJs(`document.getElementById("btn-retry").click()`);
    await sleep(1200);
    const a1 = JSON.parse(await evalJs(navState));
    check("A1 详情页按钮重试：左栏任务树不动", !a1.settingsMode, JSON.stringify(a1));
    check("A1 详情页按钮重试：主区打开运行详情", a1.detailVisible, JSON.stringify(a1));
    await evalJs(resetMain);

    // A2 侧栏右键菜单 →「↻ 重试任务」
    await evalJs(`(async () => {
      const det = document.querySelector('#side-tasks .stask[data-task="tedit1"]');
      det.dispatchEvent(new MouseEvent("contextmenu",
        { bubbles: true, cancelable: true, clientX: 200, clientY: 200 }));
      await new Promise(r => setTimeout(r, 300));
      const item = [...document.querySelectorAll("#ctx-menu .ctx-item")]
        .find((x) => x.textContent.includes("继续任务"));
      if (item) item.click();
      return item ? "clicked" : "no-item"; })()`);
    await sleep(1200);
    const a2 = JSON.parse(await evalJs(navState));
    check("A2 右键菜单重试：左栏任务树不动", !a2.settingsMode, JSON.stringify(a2));
    check("A2 右键菜单重试：主区打开运行详情", a2.detailVisible, JSON.stringify(a2));
    await evalJs(resetMain);

    // A3 run 级详情 →「↻ 重试任务」按钮
    await evalJs(`sideOpenRun("redit1")`);
    await sleep(1200);
    const pre3 = JSON.parse(await evalJs(`JSON.stringify({ sm: document.body.classList.contains("settings-mode"),
      btn: !document.getElementById("btn-retry").classList.contains("hidden") })`));
    check("A3 前置：run 级详情重试按钮可见", !pre3.sm && pre3.btn, JSON.stringify(pre3));
    await evalJs(`document.getElementById("btn-retry").click()`);
    await sleep(1200);
    const a3 = JSON.parse(await evalJs(navState));
    check("A3 run 级按钮重试：左栏任务树不动", !a3.settingsMode, JSON.stringify(a3));
    check("A3 run 级按钮重试：主区打开运行详情", a3.detailVisible, JSON.stringify(a3));
    await evalJs(resetMain);

    // A4 「✎ 编辑重试」→ 回表单 → 点「创建并运行」
    await evalJs(`sideOpenTask("tedit1")`);
    await sleep(1200);
    await evalJs(`document.getElementById("btn-editretry").click()`);
    await sleep(1000);
    const a4p = JSON.parse(await evalJs(`JSON.stringify({
      sm: document.body.classList.contains("settings-mode"),
      goal: document.getElementById("f-goal").value })`));
    check("A4 编辑重试：回新建表单且预填（主视图）",
      !a4p.sm && a4p.goal === "造数：失败态任务", JSON.stringify(a4p));
    await evalJs(`window.__origApi; document.getElementById("f-goal").value = "探针目标";`);
    await evalJs(`document.getElementById("btn-create").click()`);
    await sleep(1200);
    const a4 = JSON.parse(await evalJs(navState));
    check("A4 表单提交创建：左栏任务树不动", !a4.settingsMode, JSON.stringify(a4));
    check("A4 表单提交创建：主区打开运行详情", a4.detailVisible, JSON.stringify(a4));
    await evalJs(resetMain);

    // A5 「继续连载」按钮（stub 掉 uiPrompt 直接给章数）
    await evalJs(`window.uiPrompt = async () => "2"; sideOpenTask("tser1")`);
    await sleep(1200);
    const pre5 = JSON.parse(await evalJs(`JSON.stringify({ sm: document.body.classList.contains("settings-mode"),
      btn: !document.getElementById("btn-continue").classList.contains("hidden") })`));
    check("A5 前置：连载任务继续按钮可见", !pre5.sm && pre5.btn, JSON.stringify(pre5));
    await evalJs(`document.getElementById("btn-continue").click()`);
    await sleep(1200);
    const a5 = JSON.parse(await evalJs(navState));
    check("A5 继续连载：左栏任务树不动", !a5.settingsMode, JSON.stringify(a5));
    check("A5 继续连载：主区打开运行详情", a5.detailVisible, JSON.stringify(a5));

    // B 设置目录页「完整运行」：保持设置导航（有意保留的唯一入口）
    await evalJs(`switchTab("agents")`);
    await sleep(600);
    await evalJs(`openRunInRuns("redit1")`);
    await sleep(1200);
    const b1 = JSON.parse(await evalJs(navState));
    check("B 目录页完整运行：保持设置导航", b1.settingsMode, JSON.stringify(b1));
    check("B 目录页完整运行：主区打开运行详情", b1.detailVisible, JSON.stringify(b1));
    // 收尾恢复真实 api，避免页面对真实服务的后续请求打到 stub
    await evalJs(`api = window.__origApi; window.__origApi = null; "restored"`);
  } finally {
    try { if (ws) ws.close(); } catch (e) { /* ignore */ }
    try { if (edge) edge.kill(); } catch (e) { /* ignore */ }
    try { if (svc) svc.kill(); } catch (e) { /* ignore */ }
    await sleep(800);
    try { rmSync(tmp, { recursive: true, force: true }); } catch (e) { /* ignore */ }
  }
  const bad = results.filter((r) => !r.ok).length;
  console.log(bad ? `\n${bad} 项未过` : "\n全部通过");
  process.exit(bad ? 1 : 0);
}

main().catch((e) => { console.error("FATAL", e); process.exit(1); });
