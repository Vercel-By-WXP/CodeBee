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
      const det = ${rowSel};
      det.dispatchEvent(new MouseEvent("contextmenu",
        { bubbles: true, cancelable: true, clientX: 200, clientY: 200 }));
      await new Promise(r => setTimeout(r, 200));
      const item = [...document.querySelectorAll("#ctx-menu .ctx-item")]
        .find(x => x.textContent.trim() === "重命名任务");
      item.click();
    })()`);
    // 重命名走应用内 #ask 输入弹框（uiPrompt，非 window.prompt）：等弹框出现，
    // 填入新标题后点「确定」提交
    await sleep(800);
    const askShown = await evalJs(`(() => {
      const dlg = document.getElementById("ask");
      if (!dlg || dlg.classList.contains("hidden")) return false;
      document.getElementById("ask-input").value = ${JSON.stringify(NEW_TITLE)};
      document.getElementById("ask-yes").click();
      return true;
    })()`);
    check("重命名弹出应用内输入弹框", askShown === true);
    // renameTask 内部 poll() 完成后，SSE 推送按 2s 桶轮询版本号，等重绘
    await sleep(500);
    // SSE 推送按 2s 桶轮询版本号：改名后轮询 DOM 等重绘，最坏等 8s
    let sideTitle = "";
    for (let i = 0; i < 16; i++) {
      await sleep(500);
      sideTitle = await evalJs(
        `(document.querySelector('#side-tasks .stask[data-task="${TASK}"] summary .t')||{}).textContent?.trim()||""`);
      if (sideTitle === NEW_TITLE) break;
    }
    if (sideTitle !== NEW_TITLE) {
      // 失败时 dump 页面内部状态，定位是 state 没到还是 sig/重绘问题
      const dump = await evalJs(`JSON.stringify({
        sseLive: !!S.sseLive,
        esState: S.es ? S.es.readyState : "no-es",
        stateRunTitles: (S.state.runs || []).map(r => [r.id.slice(-4), r.title]),
        sideSig: (S.sideSig || "").slice(0, 80),
        domTitles: [...document.querySelectorAll("#side-tasks .stask")].map(d => d.dataset.task + ":" + d.querySelector(".t").textContent),
        conn: (document.getElementById("conn") || {}).textContent,
      })`);
      console.log("  [dump]", dump);
    }
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

    /* ---- F) 文件夹行右键菜单：新建任务/打开工作目录/复制路径/移除 ---- */
    const fm = await menuOf(`document.querySelector('#side-tasks .sdir:not([data-dir="__orphan__"])')`);
    const needDir = ["查看文件", "新建任务到该目录", "打开工作目录", "复制工作目录路径", "移除该文件夹"];
    check("文件夹行右键菜单弹出且含五项", !fm.err && !fm.hidden && needDir.every((x) => fm.labels.includes(x)),
      JSON.stringify(fm));
    // 「其他」兜底文件夹（无主运行聚合，非真实目录）不弹菜单
    const om = await menuOf(`document.querySelector('#side-tasks .sdir[data-dir="__orphan__"]')`);
    check("「其他」兜底文件夹不弹菜单", !!om.err || om.hidden === true, JSON.stringify(om));
    // 点「新建任务到该目录」→ 表单工作目录被预填
    await evalJs(`(async () => {
      const det = document.querySelector('#side-tasks .sdir:not([data-dir="__orphan__"])');
      det.dispatchEvent(new MouseEvent("contextmenu",
        { bubbles: true, cancelable: true, clientX: 200, clientY: 200 }));
      await new Promise(r => setTimeout(r, 200));
      [...document.querySelectorAll("#ctx-menu .ctx-item")]
        .find(x => x.textContent.trim() === "新建任务到该目录").click();
    })()`);
    await sleep(600);
    const wdForm = await evalJs(`document.getElementById("f-workdir").value`);
    check("「新建任务到该目录」预填表单工作目录", wdForm === workdir,
      "form=" + wdForm + " want=" + workdir);
    // 点「移除该文件夹」→ 确认弹框 → 任务被归档，侧栏文件夹消失；文件还在
    await evalJs(`(async () => {
      const det = document.querySelector('#side-tasks .sdir:not([data-dir="__orphan__"])');
      det.dispatchEvent(new MouseEvent("contextmenu",
        { bubbles: true, cancelable: true, clientX: 200, clientY: 200 }));
      await new Promise(r => setTimeout(r, 200));
      [...document.querySelectorAll("#ctx-menu .ctx-item")]
        .find(x => x.textContent.trim() === "移除该文件夹").click();
    })()`);
    await sleep(800);
    const confirmShown = await evalJs(`(() => {
      const dlg = document.getElementById("ask");
      if (!dlg || dlg.classList.contains("hidden")) return false;
      document.getElementById("ask-yes").click();
      return true;
    })()`);
    check("「移除」弹确认框", confirmShown === true);
    // SSE 按 2s 桶轮询版本号：轮询等侧栏重绘，该 workdir 的 .sdir 应消失
    let dirGone = false;
    for (let i = 0; i < 16; i++) {
      await sleep(500);
      dirGone = await evalJs(
        `!document.querySelector('#side-tasks .sdir[data-dir=' + JSON.stringify(${JSON.stringify(workdir)}) + ']')`);
      if (dirGone) break;
    }
    check("移除后侧栏文件夹消失", dirGone === true);
    // 移除=归档：检查任务进了 archived_tasks、文件未删
    const st2 = await api("/api/state");
    const t2 = (st2.json.archived_tasks || []).find((x) => x.id === TASK);
    const man = workdir && (await import("node:fs")).existsSync(workdir + "/manuscript.md");
    check("移除=归档：任务进 archived_tasks 且文件未删",
      !!t2 && man, JSON.stringify({ inArchived: !!t2, man }));

    /* ---- G) 「查看文件」：侧栏文件夹内联展开（附件式 chips，可折叠 toggle） ---- */
    // 取消归档找回：页面持有控制权，写接口必须由页面自己发（无头 API 会被 423 挡）。
    const restore = await evalJs(`fetch("/api/tasks/${TASK}/archive", {
      method: "POST",
      headers: Object.assign({ "Content-Type": "application/json" }, authHeaders()),
      body: JSON.stringify({ archived: false }) }).then(r => r.status)`);
    check("取消归档还原（为查看文件测试准备）", restore === 200);
    // SSE 按 2s 桶轮询：等文件夹重新出现在侧栏
    let folderBack = false;
    for (let i = 0; i < 16; i++) {
      await sleep(500);
      const sdir = await evalJs(
        `!!document.querySelector('#side-tasks .sdir:not([data-dir="__orphan__"])')`);
      if (sdir) { folderBack = true; break; }
    }
    check("取消归档后文件夹回到侧栏", folderBack === true);
    // 右键「查看文件」→ 文件夹行内联展开文件 chips
    const fb = await menuOf(`document.querySelector('#side-tasks .sdir:not([data-dir="__orphan__"])')`);
    if (fb && !fb.err && !fb.hidden && fb.labels.includes("查看文件")) {
      await evalJs(`(async () => {
        const det = document.querySelector('#side-tasks .sdir:not([data-dir="__orphan__"])');
        det.dispatchEvent(new MouseEvent("contextmenu",
          { bubbles: true, cancelable: true, clientX: 200, clientY: 200 }));
        await new Promise(r => setTimeout(r, 200));
        [...document.querySelectorAll("#ctx-menu .ctx-item")]
          .find(x => x.textContent.trim() === "查看文件").click();
      })()`);
      await sleep(800);
      const fbInline = await evalJs(`(() => {
        const box = document.querySelector("#side-tasks .sdir:not([data-dir='__orphan__']) .sdir-files");
        const chips = box ? [...box.querySelectorAll(".sdir-chip")] : [];
        const files = chips.map(c => c.querySelector("span").textContent.trim()
          + " " + c.querySelector("i").textContent.trim());
        return JSON.stringify({ exists: !!box, count: chips.length, files });
      })()`);
      check("「查看文件」在文件夹行内联展开（无弹框）",
        JSON.parse(fbInline).exists === true, "inline=" + fbInline);
      check("内联 chips 含 manuscript 文件", fbInline.includes("manuscript"),
        "chips=" + fbInline);
      const modalStillHidden = await evalJs(
        `document.getElementById("modal").classList.contains("hidden")`);
      check("未弹出 modal（保持侧栏内联）", modalStillHidden === true);
      // 再次点「查看文件」→ 收起（toggle 关闭）
      await evalJs(`(async () => {
        const det = document.querySelector('#side-tasks .sdir:not([data-dir="__orphan__"])');
        det.dispatchEvent(new MouseEvent("contextmenu",
          { bubbles: true, cancelable: true, clientX: 200, clientY: 200 }));
        await new Promise(r => setTimeout(r, 200));
        [...document.querySelectorAll("#ctx-menu .ctx-item")]
          .find(x => x.textContent.trim() === "查看文件").click();
      })()`);
      await sleep(400);
      const fbGone = await evalJs(
        `!document.querySelector('#side-tasks .sdir:not([data-dir="__orphan__"]) .sdir-files')`);
      check("再次点击「查看文件」收起内联区", fbGone === true);
    } else {
      check("侧栏文件夹可弹出右键菜单", false, JSON.stringify(fb));
    }
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
