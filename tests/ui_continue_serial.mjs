/* 「继续连载」核验：在旧任务基础上新建任务接着写下一批章节。
 *   API：continue-info（已写到第 2 章、can=true；非连载任务 can=false + 原因）；
 *        POST continue → 新任务 start_chapter=3、标题带 ·续，mock 跑完后
 *        工作目录新增 chapter-03/04.md，成书合并含第 3 章，旧章原封不动；
 *   UI：连载任务右键菜单含「继续连载（新任务）」、单稿件任务不含；
 *        任务详情「继续连载」按钮对连载任务可见、单稿件隐藏；
 *        点菜单项 → prompt 弹续写章数 → 新任务出现并自动打开运行详情。
 * 前置：TUTTI_DATA 用 tests/_seed_continue_fixtures.py 造数，再起 18798 临时服务。 */
import { spawn, execSync } from "node:child_process";
import { mkdtempSync, rmSync } from "node:fs";
import { tmpdir } from "node:os";
import { join, dirname } from "node:path";
import { fileURLToPath } from "node:url";

const SERVICE = "http://127.0.0.1:18798";
const PORT = 18798;
const CDP_PORT = 9339;
const EDGE = [
  "C:\\Program Files (x86)\\Microsoft\\Edge\\Application\\msedge.exe",
  "C:\\Program Files\\Microsoft\\Edge\\Application\\msedge.exe",
].find((p) => true);
const ROOT = join(dirname(fileURLToPath(import.meta.url)), "..");
const TASK = "task-cont";
const PLAIN = "task-plain";

const results = [];
const check = (n, c, d = "") => {
  results.push(!!c);
  console.log((c ? "  ✓ " : "  ✗ ") + n + (c ? "" : "　— " + String(d).slice(0, 300)));
};
const sleep = (ms) => new Promise((r) => setTimeout(r, ms));
const api = async (path, opts = {}) => {
  const r = await fetch(SERVICE + path, {
    method: opts.method || "GET",
    headers: { "Content-Type": "application/json" },
    body: opts.body ? JSON.stringify(opts.body) : undefined,
  });
  let j = null;
  try { j = await r.json(); } catch (e) { /* ignore */ }
  return { code: r.status, json: j };
};

async function main() {
  try {
    const occupied = execSync(
      `netstat -ano | findstr ":${PORT} " | findstr "LISTENING"`, { stdio: "pipe" }).toString();
    console.error("端口 " + PORT + " 已被占用（可能是别处残留服务），先处理再跑：\n" + occupied);
    process.exit(2);
  } catch (e) { /* findstr 无匹配 = 空闲 */ }

  const dataDir = mkdtempSync(join(tmpdir(), "tutti-cont-"));
  await new Promise((res) => {
    const p = spawn("python", ["-X", "utf8", join(ROOT, "tests", "_seed_continue_fixtures.py")],
      { env: { ...process.env, TUTTI_DATA: dataDir }, cwd: ROOT, stdio: "ignore" });
    p.on("exit", res);
  });
  const workdir = join(dataDir, "serial-book");
  const ch1Before = (await import("node:fs")).readFileSync(join(workdir, "chapter-01.md"), "utf-8");

  const svc = spawn("python", ["-X", "utf8", join(ROOT, "app", "main.py"), "--port", String(PORT),
    "--no-browser", "--host", "127.0.0.1"], {
    env: { ...process.env, TUTTI_DATA: dataDir, PYTHONPATH: ROOT },
    cwd: ROOT, stdio: "ignore",
  });
  let up = false;
  for (let i = 0; i < 40 && !up; i++) {
    await sleep(500);
    try { up = (await fetch(SERVICE + "/api/state")).ok; } catch (e) { /* wait */ }
  }
  check("临时服务启动（端口 " + PORT + "）", up);

  /* ---- A) continue-info API ---- */
  const info = await api(`/api/tasks/${TASK}/continue-info`);
  check("连载任务 continue-info 可续（已写到第 2 章）",
    info.code === 200 && info.json.can === true && info.json.last_chapter === 2
    && info.json.default_chapters === 2, JSON.stringify(info.json));
  const infoPlain = await api(`/api/tasks/${PLAIN}/continue-info`);
  check("单稿件任务不可续并给原因",
    infoPlain.code === 200 && infoPlain.json.can === false
    && (infoPlain.json.reason || "").includes("连载"), JSON.stringify(infoPlain.json));
  const infoBogus = await api("/api/tasks/bogus/continue-info");
  check("未知任务 continue-info 404", infoBogus.code === 404);

  /* ---- B) POST continue：新任务章节衔接 + mock 跑完合并成书 ---- */
  const cont = await api(`/api/tasks/${TASK}/continue`, { method: "POST", body: { chapters: 2 } });
  check("POST continue 建任务+运行", cont.code === 200 && !!cont.json.task_id && !!cont.json.run_id,
    JSON.stringify(cont.json));
  const st1 = await api("/api/state");
  const cont1 = (st1.json.tasks || []).find((t) => t.id === cont.json.task_id);
  check("新任务 start_chapter=3 且指向上批",
    cont1 && cont1.serial && cont1.serial.start_chapter === 3
    && cont1.serial.continues === TASK && cont1.title.includes("·续"), JSON.stringify(cont1 || {}));
  let run1 = null;
  for (let i = 0; i < 240; i++) {   // mock 全流程数秒即完；兜底 120s（冷启动首次 detect 较慢）
    await sleep(500);
    run1 = (await api("/api/runs/" + cont.json.run_id)).json.run;
    if (run1 && (run1.status === "done" || run1.status === "failed")) break;
  }
  check("续写运行跑完且成功", run1 && run1.status === "done",
    JSON.stringify({ status: run1 && run1.status, error: run1 && run1.error }));
  const fs = await import("node:fs");
  check("新章 chapter-03/04.md 落盘",
    fs.existsSync(join(workdir, "chapter-03.md")) && fs.existsSync(join(workdir, "chapter-04.md")));
  check("旧章原封不动",
    fs.readFileSync(join(workdir, "chapter-01.md"), "utf-8") === ch1Before);
  const ms = fs.readFileSync(join(workdir, "manuscript.md"), "utf-8");
  check("成书合并含旧章与新章（1-4 章完整一本）",
    ms.includes("第 1 章") && ms.includes("第 3 章"), ms.slice(0, 120));
  const guard = await api(`/api/tasks/${cont.json.task_id}/continue-info`);
  check("续写后再次查询进度推进到第 4 章",
    guard.json.can === true && guard.json.last_chapter === 4, JSON.stringify(guard.json));

  /* ---- C) UI：右键菜单与详情按钮 ---- */
  await api("/api/control", { method: "POST", body: { action: "release" } });  // 让位给页面
  const profile = mkdtempSync(join(tmpdir(), "tutti-cont-edge-"));
  const proc = spawn(EDGE, [
    "--headless=new", "--disable-gpu", "--no-first-run", "--disable-sync", "--disable-extensions",
    `--user-data-dir=${profile}`, `--remote-debugging-port=${CDP_PORT}`,
    "--window-size=1400,950", "about:blank",
  ], { stdio: "ignore" });

  try {
    let target = null;
    for (let i = 0; i < 30 && !target; i++) {
      await sleep(500);
      try {
        const list = await fetch(`http://127.0.0.1:${CDP_PORT}/json/list`).then((r) => r.json());
        target = list.find((t) => t.type === "page" && t.url === "about:blank");
      } catch (e) { /* 未就绪 */ }
    }
    check("Edge headless 启动并开放 CDP", !!target);
    const ws = new WebSocket(target.webSocketDebuggerUrl);
    await new Promise((res, rej) => { ws.onopen = res; ws.onerror = rej; });
    let seq = 0;
    const pending = new Map();
    const send = (method, params = {}) => new Promise((res, rej) => {
      const id = ++seq;
      const timer = setTimeout(() => { pending.delete(id); rej(new Error("CDP 超时：" + method)); }, 20000);
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
      if (ex) throw new Error("页面表达式抛错：" + (ex.exception?.description || ex.text || "").slice(0, 240));
      return r.result?.result?.value;
    };
    await send("Page.enable");
    await send("Page.navigate", { url: SERVICE + "/" });
    await sleep(3500);

    const menuOf = async (taskSel) => JSON.parse(await evalJs(`(async () => {
      const det = ${taskSel};
      if (!det) return JSON.stringify({ err: "no row" });
      det.dispatchEvent(new MouseEvent("contextmenu",
        { bubbles: true, cancelable: true, clientX: 200, clientY: 200 }));
      await new Promise(r => setTimeout(r, 200));
      const menu = document.getElementById("ctx-menu");
      const labels = [...menu.querySelectorAll(".ctx-item")].map(x => x.textContent.trim());
      const r = JSON.stringify({ labels, hidden: menu.classList.contains("hidden") });
      document.body.click();
      return r;
    })()`));
    const contMenu = await menuOf(`document.querySelector('#side-tasks .stask[data-task="${TASK}"]')`);
    check("连载任务菜单含「继续连载（新任务）」+「基于此任务新建」",
      !contMenu.err && contMenu.labels.includes("继续连载（新任务）")
      && contMenu.labels.includes("基于此任务新建"), JSON.stringify(contMenu.labels));
    const plainMenu = await menuOf(`document.querySelector('#side-tasks .stask[data-task="${PLAIN}"]')`);
    check("单稿件任务菜单含「基于此任务新建」、不含「继续连载」",
      !plainMenu.err && plainMenu.labels.includes("基于此任务新建")
      && !plainMenu.labels.includes("继续连载（新任务）"), JSON.stringify(plainMenu.labels));

    /* 基于此任务新建（通用入口）：单稿件任务预填表单，不带连载衔接 */
    await evalJs(`(async () => {
      const det = document.querySelector('#side-tasks .stask[data-task="${PLAIN}"]');
      det.dispatchEvent(new MouseEvent("contextmenu",
        { bubbles: true, cancelable: true, clientX: 200, clientY: 200 }));
      await new Promise(r => setTimeout(r, 200));
      const item = [...document.querySelectorAll("#ctx-menu .ctx-item")]
        .find(x => x.textContent.trim() === "基于此任务新建");
      item.click();
    })()`);
    await sleep(800);
    const pf = JSON.parse(await evalJs(`JSON.stringify({
      goal: document.getElementById("f-goal").value,
      workdir: document.getElementById("f-workdir").value,
      type: document.getElementById("f-type").value,
      title: document.getElementById("f-title").value,
      manuscript: document.getElementById("f-manuscript").value,
      chapters: document.getElementById("f-chapters").value,
      formVisible: !document.getElementById("page-settings").classList.contains("hidden")
    })`));
    check("点「基于此任务新建」切回表单且 goal/workdir/类型/标题/稿件名预填",
      pf.formVisible && pf.goal === "造数：负例" && pf.workdir === workdir
      && pf.type === "novel" && pf.title === "单稿件核验"
      && pf.manuscript === "manuscript.md", JSON.stringify(pf));
    check("非连载任务预填不带连载章节参数", pf.chapters === "", JSON.stringify(pf));

    /* 选择器不含演示智能体（有真实智能体时；全新机器一个真实都没有则允许 mock 兜底显示） */
    const stA = (await api("/api/state")).json;
    const hasReal = (stA.agents || []).some((a) => a.mode !== "mock");
    const selOpts = JSON.parse(await evalJs(`JSON.stringify({
      impl: [...document.getElementById("f-impl").options].map(o => o.text),
      critics: [...document.querySelectorAll("#f-critics label")].map(l => l.textContent.trim())
    })`));
    const hasMockTxt = (xs) => xs.some((x) => x.includes("mock") || x.includes("演示"));
    if (hasReal) {
      check("实现者下拉只列真实智能体（无演示智能体）",
        selOpts.impl.length > 0 && !hasMockTxt(selOpts.impl), JSON.stringify(selOpts.impl));
      check("评审组复选只列真实智能体（无演示智能体）",
        selOpts.critics.length > 0 && !hasMockTxt(selOpts.critics), JSON.stringify(selOpts.critics));
    } else {
      check("无真实智能体时选择器回退显示 mock（全新用户可试用）",
        selOpts.impl.length > 0 && hasMockTxt(selOpts.impl), JSON.stringify(selOpts.impl));
    }

    /* 任务详情按钮：连载任务=继续连载+基于此新建 / 单稿件=仅基于此新建 */
    await evalJs(`sideOpenTask("${TASK}")`);
    await sleep(800);
    const btnState = await evalJs(`(() => {
      const g = (id) => { const b = document.getElementById(id);
        return b ? !b.classList.contains("hidden") : false; };
      return JSON.stringify({ cont: g("btn-continue"), nf: g("btn-newfrom") });
    })()`);
    const bs = JSON.parse(btnState || "{}");
    check("连载任务详情显示「继续连载」和「基于此任务新建」按钮", bs.cont === true && bs.nf === true, btnState);
    await evalJs(`sideOpenTask("${PLAIN}")`);
    await sleep(800);
    const btnState2 = await evalJs(`(() => {
      const g = (id) => { const b = document.getElementById(id);
        return b ? { vis: !b.classList.contains("hidden") } : { vis: false }; };
      return JSON.stringify({ cont: g("btn-continue").vis, nf: g("btn-newfrom").vis });
    })()`);
    const bs2 = JSON.parse(btnState2 || "{}");
    check("单稿件任务详情隐藏「继续连载」、显示「基于此任务新建」",
      bs2.cont === false && bs2.nf === true, btnState2);

    /* 页面点菜单项 → 应用内输入弹框（#ask，非 window.prompt）填章数 → 确定 */
    const before = ((await api("/api/state")).json.tasks || []).length;
    await evalJs(`(async () => {
      const det = document.querySelector('#side-tasks .stask[data-task="${TASK}"]');
      det.dispatchEvent(new MouseEvent("contextmenu",
        { bubbles: true, cancelable: true, clientX: 200, clientY: 200 }));
      await new Promise(r => setTimeout(r, 200));
      const item = [...document.querySelectorAll("#ctx-menu .ctx-item")]
        .find(x => x.textContent.trim() === "继续连载（新任务）");
      item.click();
    })()`);
    await sleep(1200);
    const asked = await evalJs(`(() => {
      const dlg = document.getElementById("ask");
      if (!dlg || dlg.classList.contains("hidden")) return "";
      const title = document.getElementById("ask-title").textContent;
      const inp = document.getElementById("ask-input");
      inp.value = "1";
      document.getElementById("ask-yes").click();
      return title;
    })()`);
    check("弹框提示已写至第 4 章（衔接提示）", String(asked).includes("第 4 章"), asked);
    let grew = false, openedRun = false;
    for (let i = 0; i < 20; i++) {
      await sleep(500);
      const n = ((await api("/api/state")).json.tasks || []).length;
      if (n > before) { grew = true; break; }
    }
    openedRun = await evalJs(`!!S.detailRunId && document.getElementById("rd-title").textContent.includes("·续")`);
    check("点菜单项新建了续写任务", grew);
    check("页面自动打开续写任务的运行详情", openedRun);
  } finally {
    try { proc.kill(); } catch (e) { /* ignore */ }
    try { spawn("taskkill", ["/F", "/T", "/PID", String(proc.pid)], { stdio: "ignore" }); } catch (e) { /* ignore */ }
    try { svc.kill(); } catch (e) { /* ignore */ }
    try { spawn("taskkill", ["/F", "/T", "/PID", String(svc.pid)], { stdio: "ignore" }); } catch (e) { /* ignore */ }
    try { rmSync(dataDir, { recursive: true, force: true }); } catch (e) { /* ignore */ }
    try { rmSync(profile, { recursive: true, force: true }); } catch (e) { /* ignore */ }
  }

  const bad = results.filter((x) => !x).length;
  console.log(bad ? `\n${bad} 项未通过` : "\n全部通过");
  process.exit(bad ? 1 : 0);
}

main().catch((e) => { console.error("探针异常：", e); process.exit(1); });
