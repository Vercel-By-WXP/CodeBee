/* 右键菜单新增项核验：打开工作目录 / 复制路径组 / 重命名任务。
 *   任务行菜单应含：打开详情、打开工作目录、复制工作目录路径、复制日志目录路径、
 *                  重命名任务、归档、删除任务；
 *   管理运行行（无 task_id）应含：复制日志目录路径、删除记录，不含重命名。
 *   API：reveal open=false 回真实路径；伪造 id 404；空标题 rename 400；
 *   页面流程：点「复制工作目录路径」剪贴板拿到 workdir；点「重命名任务」后侧栏与
 *   state 里的任务和运行标题同步更新。
 * 前置：TUTTI_DATA 用 tests/_seed_ctx_fixtures.py 造数，再起 18798 临时服务。 */
import { spawn } from "node:child_process";
import { execSync } from "node:child_process";
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
const TASK = "task-ctx";
const NEW_TITLE = "右键菜单核验-改名成功";

const results = [];
const check = (n, c, d = "") => {
  results.push(!!c);
  console.log((c ? "  ✓ " : "  ✗ ") + n + (c ? "" : "　— " + String(d).slice(0, 300)));
};
const sleep = (ms) => new Promise((r) => setTimeout(r, ms));
/* 无头请求统一 "local" 身份（127.0.0.1 无头默认就是 local），控制权才不会自己跟自己抢 */
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
  /* 端口 18798 在 Windows 上可被双绑：先确认没有别人在听 */
  try {
    const occupied = execSync(
      `netstat -ano | findstr ":${PORT} " | findstr "LISTENING"`, { stdio: "pipe" }).toString();
    console.error("端口 " + PORT + " 已被占用（可能是别处残留服务），先处理再跑：\n" + occupied);
    process.exit(2);
  } catch (e) { /* findstr 无匹配 = 空闲 */ }

  const dataDir = mkdtempSync(join(tmpdir(), "tutti-ctx-"));
  await new Promise((res) => {
    const p = spawn("python", ["-X", "utf8", join(ROOT, "tests", "_seed_ctx_fixtures.py")],
      { env: { ...process.env, TUTTI_DATA: dataDir }, cwd: ROOT, stdio: "ignore" });
    p.on("exit", res);
  });

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

  const profile = mkdtempSync(join(tmpdir(), "tutti-ctx-edge-"));
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
        // 必须按 url 过滤：本机 Edge 账号会多开一个 sync-confirmation-dialog page，
        // 盲取第一个 page 会连到它上面，CDP 消息有去无回
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
        send("Page.handleJavaScriptDialog", { accept: true });  // 意外弹框自动吞掉，别卡住流程
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

    const state0 = await api("/api/state");
    const task0 = (state0.json.tasks || []).find((t) => t.id === TASK);
    check("造数任务在 state 里且带工作目录", !!task0 && !!task0.workdir, JSON.stringify(task0 || {}));
    const workdir = task0 ? task0.workdir : "";
    const runId = ((state0.json.runs || []).find((r) => r.task_id === TASK) || {}).id || "";

    /* ---- A) 任务行右键菜单项 ---- */
    const menuOf = async (selExpr) => JSON.parse(await evalJs(`(async () => {
      const det = ${selExpr};
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
    const rowSel = `document.querySelector('#side-tasks .stask[data-task="${TASK}"]')`;
    const tm = await menuOf(rowSel);
    check("任务行右键菜单弹出", !tm.err && !tm.hidden, JSON.stringify(tm));
    const needTask = ["打开详情", "打开工作目录", "复制工作目录路径", "复制日志目录路径", "重命名任务", "归档", "删除任务"];
    check("任务行菜单含全部新项+原有项",
      needTask.every((x) => tm.labels.includes(x)), JSON.stringify(tm.labels));

    /* ---- B) reveal / rename API 校验（open=false 只回路径，不真开资源管理器）---- */
    const rv1 = await api(`/api/tasks/${TASK}/reveal`, { method: "POST", body: { open: false } });
    check("任务 reveal 返回工作目录", rv1.code === 200 && rv1.json.path === workdir, JSON.stringify(rv1));
    const rv2 = await api(`/api/runs/${runId}/reveal`, { method: "POST", body: { open: false } });
    check("运行 reveal 返回记录目录", rv2.code === 200 && String(rv2.json?.path || "").endsWith(runId), JSON.stringify(rv2));
    const rv3 = await api("/api/tasks/bogus-id/reveal", { method: "POST", body: { open: false } });
    check("伪造任务 id reveal 404", rv3.code === 404, JSON.stringify(rv3));
    const rn0 = await api(`/api/tasks/${TASK}/rename`, { method: "POST", body: { title: "  " } });
    check("空标题 rename 400", rn0.code === 400, JSON.stringify(rn0));
    await api("/api/control", { method: "POST", body: { action: "release" } });  // 让位给页面流程

    /* ---- C) 页面点「复制工作目录路径」→ 剪贴板 ---- */
    await evalJs(`(() => {
      navigator.clipboard.writeText = (t) => { window.__copied = t; return Promise.resolve(); };
      return true;
    })()`);
    await evalJs(`(async () => {
      const det = ${rowSel};
      det.dispatchEvent(new MouseEvent("contextmenu",
        { bubbles: true, cancelable: true, clientX: 200, clientY: 200 }));
      await new Promise(r => setTimeout(r, 200));
      const item = [...document.querySelectorAll("#ctx-menu .ctx-item")]
        .find(x => x.textContent.trim() === "复制工作目录路径");
      item.click();
    })()`);
    await sleep(1200);
    const copied = await evalJs(`window.__copied || ""`);
    check("点「复制工作目录路径」剪贴板拿到 workdir", copied === workdir,
      "copied=" + copied + " want=" + workdir);

    /* ---- D) 页面点「重命名任务」→ 侧栏/state/运行标题同步 ---- */
    await evalJs(`(async () => {
      window.prompt = () => ${JSON.stringify(NEW_TITLE)};
      const det = ${rowSel};
      det.dispatchEvent(new MouseEvent("contextmenu",
        { bubbles: true, cancelable: true, clientX: 200, clientY: 200 }));
      await new Promise(r => setTimeout(r, 200));
      const item = [...document.querySelectorAll("#ctx-menu .ctx-item")]
        .find(x => x.textContent.trim() === "重命名任务");
      item.click();
    })()`);
    // SSE 推送按 2s 桶轮询版本号，改名后的重绘最坏要等两个周期
    await sleep(5000);
    const sideTitle = await evalJs(
      `document.querySelector('#side-tasks .stask[data-task="${TASK}"] summary .t').textContent.trim()`);
    check("侧栏任务组标题已更新", sideTitle === NEW_TITLE, sideTitle);
    const st1 = await api("/api/state");
    const t1 = (st1.json.tasks || []).find((t) => t.id === TASK);
    const runTitles = (st1.json.runs || []).filter((r) => r.task_id === TASK).map((r) => r.title);
    check("state 任务标题已更新", t1 && t1.title === NEW_TITLE, t1 && t1.title);
    check("该任务全部运行标题同步更新", runTitles.length >= 2 && runTitles.every((x) => x === NEW_TITLE),
      JSON.stringify(runTitles));

    /* ---- E) 管理运行行（无 task_id）菜单 ---- */
    const mm = await menuOf(`[...document.querySelectorAll('#side-tasks .stask')]
      .find(d => !d.dataset.task)`);
    check("管理运行行菜单含 复制日志目录路径/删除记录、不含重命名",
      !mm.err && mm.labels.includes("复制日志目录路径") && mm.labels.includes("删除记录")
      && !mm.labels.includes("重命名任务") && !mm.labels.includes("打开工作目录"),
      JSON.stringify(mm));
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
