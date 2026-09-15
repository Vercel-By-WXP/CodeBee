/* 右键菜单新增项核验：打开工作目录 / 复制路径组 / 重命名任务。
 *   任务行菜单应含：打开详情、打开工作目录、复制工作目录路径、复制日志目录路径、
 *                  重命名任务、归档、删除任务；
 *   管理运行行（无 task_id）应含：复制日志目录路径、删除记录，不含重命名。
 *   API：reveal open=false 回真实路径；伪造 id 404；空标题 rename 400；
 *   页面流程：点「复制工作目录路径」剪贴板拿到 workdir；点「重命名任务」后侧栏与
 *   state 里的任务和运行标题同步更新。
 * 前置：TUTTI_DATA 用 tests/_seed_ctx_fixtures.py 造数，再起临时服务（端口/CDP
 * 可用 TUTTI_TEST_PORT / TUTTI_TEST_CDP 覆盖，默认 18798/9339——18798 常被并行
 * 测试或残留服务占用，Windows 下同端口可双绑，撞上会打到别人的服务）。 */
import { spawn } from "node:child_process";
import { execSync } from "node:child_process";
import { mkdtempSync, rmSync } from "node:fs";
import { tmpdir } from "node:os";
import { join, dirname } from "node:path";
import { fileURLToPath } from "node:url";

const PORT = Number(process.env.TUTTI_TEST_PORT) || 18798;
const CDP_PORT = Number(process.env.TUTTI_TEST_CDP) || 9339;
const SERVICE = "http://127.0.0.1:" + PORT;
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

    /* ---- G) 「查看文件」：左侧栏整页切到文件浏览页，递归列出全部文件 ---- */
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
    // API 先行核验：递归返回根/一层/两层嵌套文件，跳过 node_modules
    const scan = await api("/api/dir/scan?path=" + encodeURIComponent(workdir));
    const names = (scan.json && scan.json.files || []).map((f) => f.name);
    check("scan 递归列出全部文件（根+docs+docs/deep）",
      scan.code === 200 && ["manuscript.md", "docs/note.md", "docs/deep/data.csv"]
        .every((n) => names.includes(n)), JSON.stringify(names));
    check("scan 跳过 node_modules 等噪音目录", !names.some((n) => n.includes("node_modules")),
      JSON.stringify(names));
    // 嵌套文件可打开（Windows 下曾把子目录文件误判越界 403）
    const nested = await fetch(SERVICE + "/api/dir/file?dir=" + encodeURIComponent(workdir.replace(/\\/g, "/"))
      + "&name=" + encodeURIComponent("docs/deep/data.csv"));
    check("嵌套文件 /api/dir/file 可打开（不误判越界）", nested.status === 200,
      "status=" + nested.status);
    await nested.arrayBuffer();   // 消费响应体
    // 右键「查看文件」→ 左栏整页切到文件浏览页（不再内联塞任务树）
    await evalJs(`(async () => {
      const det = document.querySelector('#side-tasks .sdir:not([data-dir="__orphan__"])');
      det.dispatchEvent(new MouseEvent("contextmenu",
        { bubbles: true, cancelable: true, clientX: 200, clientY: 200 }));
      await new Promise(r => setTimeout(r, 200));
      [...document.querySelectorAll("#ctx-menu .ctx-item")]
        .find(x => x.textContent.trim() === "查看文件").click();
    })()`);
    let page = null;
    for (let i = 0; i < 20 && !page; i++) {
      await sleep(300);
      page = await evalJs(`(() => {
        if (!document.body.classList.contains("files-mode")) return null;
        const rows = [...document.querySelectorAll("#sf-body .sf-file")];
        if (!rows.length) return null;
        return JSON.stringify({
          sideMainShown: getComputedStyle(document.querySelector(".side-main")).display !== "none",
          filesShown: getComputedStyle(document.querySelector(".side-files")).display !== "none",
          title: (document.getElementById("sf-dir") || {}).textContent || "",
          count: (document.getElementById("sf-count") || {}).textContent || "",
          files: rows.map(a => a.querySelector("span").textContent + "|" + (a.dataset.name || "")),
          dirCount: document.querySelectorAll("#sf-body details.sf-dir").length,
          dirsOpen: document.querySelectorAll("#sf-body details.sf-dir[open]").length,
        });
      })()`);
    }
    const fp = page ? JSON.parse(page) : {};
    check("「查看文件」后左栏整页切到文件页（任务树收起）",
      !!page && fp.sideMainShown === false && fp.filesShown === true, JSON.stringify(fp));
    check("文件页标题为文件夹名", fp.title === "sandbox-workdir", fp.title);
    check("文件页计数 = 3 个文件", fp.count.includes("3"), fp.count);
    check("树里列出全部 3 个文件（含两层嵌套）",
      fp.files && fp.files.length === 3 &&
      fp.files.some((x) => x.endsWith("|docs/note.md")) &&
      fp.files.some((x) => x.endsWith("|docs/deep/data.csv")), JSON.stringify(fp.files || []));
    check("子目录渲染为 2 个可折叠节点且默认展开",
      fp.dirCount === 2 && fp.dirsOpen === 2, "dirs=" + fp.dirCount + " open=" + fp.dirsOpen);
    check("未弹出 modal（左栏页面而非弹框）",
      await evalJs(`document.getElementById("modal").classList.contains("hidden")`) === true);
    // 点文件行（manuscript.md）→ 中央弹窗预览内容，不再开新标签页
    await evalJs(`(() => {
      const row = [...document.querySelectorAll("#sf-body .sf-file")]
        .find(a => a.dataset.name === "manuscript.md");
      if (!row) return false;
      row.click();
      return true;
    })()`);
    let pop = null;
    for (let i = 0; i < 20 && !pop; i++) {
      await sleep(300);
      pop = await evalJs(`(() => {
        const el = document.getElementById("file-pop");
        if (!el || el.classList.contains("hidden")) return null;
        const code = el.querySelector(".fp-code");
        return JSON.stringify({ text: code ? code.innerText : "" });
      })()`);
    }
    const pp = pop ? JSON.parse(pop) : {};
    check("点文件行中央弹窗预览内容（不开新标签页）",
      pp.text && pp.text.includes("造数稿件"), JSON.stringify(pp).slice(0, 200));
    await evalJs(`window.filePopClose()`);
    await sleep(200);
    // 点「返回任务列表」→ 恢复任务树
    await evalJs(`document.getElementById("btn-files-back").click()`);
    await sleep(300);
    const backOk = await evalJs(`(() => ({
      modeOff: !document.body.classList.contains("files-mode"),
      treeShown: getComputedStyle(document.querySelector(".side-main")).display !== "none",
    }))()`);
    check("「返回任务列表」恢复任务树", backOk.modeOff === true && backOk.treeShown === true,
      JSON.stringify(backOk));

    /* ---- H) 旧后端兼容：scan 只回一层 files+subdirs 时，文件夹懒加载可点 ---- */
    await evalJs(`(() => {
      window.__realFetch = window.fetch;
      window.fetch = (u, o) => String(u).includes("/api/dir/scan")
        ? Promise.resolve(new Response(JSON.stringify({
            path: "x", truncated: false,
            files: [{ name: "manuscript.md", size: 9, mtime: 2 }],
            subdirs: ["docs"],
          }), { status: 200, headers: { "Content-Type": "application/json" } }))
        : window.__realFetch(u, o);
      return true;
    })()`);
    // 重新右键进文件页
    await evalJs(`(async () => {
      const det = document.querySelector('#side-tasks .sdir:not([data-dir="__orphan__"])');
      det.dispatchEvent(new MouseEvent("contextmenu",
        { bubbles: true, cancelable: true, clientX: 200, clientY: 200 }));
      await new Promise(r => setTimeout(r, 200));
      [...document.querySelectorAll("#ctx-menu .ctx-item")]
        .find(x => x.textContent.trim() === "查看文件").click();
    })()`);
    let legacy = null;
    for (let i = 0; i < 20 && !legacy; i++) {
      await sleep(300);
      legacy = await evalJs(`(() => {
        const lz = document.querySelector("#sf-body details.sf-lazy");
        if (!lz) return null;
        return JSON.stringify({
          lazyName: (lz.querySelector("summary span") || {}).textContent || "",
        });
      })()`);
    }
    const lg = legacy ? JSON.parse(legacy) : {};
    check("旧格式 subdirs 渲染成可点文件夹节点", lg.lazyName === "docs", JSON.stringify(lg));
    // 展开懒加载文件夹 → 内层文件由 stub 接口填充
    await evalJs(`(() => {
      const d = document.querySelector("#sf-body details.sf-lazy");
      d.open = true;
      return true;
    })()`);
    let kid = null;
    for (let i = 0; i < 20 && !kid; i++) {
      await sleep(300);
      kid = await evalJs(`(() => {
        const f = document.querySelector("#sf-body details.sf-lazy .sf-kids .sf-file");
        if (!f) return null;
        return JSON.stringify({ name: f.dataset.name || "" });
      })()`);
    }
    const kd = kid ? JSON.parse(kid) : {};
    check("点开懒加载文件夹展示内层文件", kd.name === "manuscript.md", JSON.stringify(kd));
    // 点懒加载文件夹里的文件：取内容必须用「该子目录」当 dir（曾用根目录拼名 → 404）
    await evalJs(`(() => {
      window.__fpReq = "";
      window.fetch = (u, o) => {
        if (String(u).includes("/api/dir/file")) {
          window.__fpReq = String(u);
          return Promise.resolve(new Response("# lazy 文件正文", {
            status: 200, headers: { "Content-Type": "text/plain; charset=utf-8" } }));
        }
        return window.__realFetch(u, o);
      };
      document.querySelector("#sf-body details.sf-lazy .sf-kids .sf-file").click();
      return true;
    })()`);
    let req = "";
    for (let i = 0; i < 20 && !req; i++) {
      await sleep(200);
      req = await evalJs(`window.__fpReq || ""`);
    }
    check("懒加载子目录文件按子目录取内容（不再 404）",
      /dir=[^&]+%2Fdocs&name=manuscript\.md$/.test(req), req);
    await sleep(600);
    const lazyBody = await evalJs(
      `(document.querySelector("#file-pop .fp-body")||{}).innerText||""`);
    check("弹窗展示懒加载子目录文件内容", lazyBody.includes("lazy 文件正文"),
      lazyBody.slice(0, 120));
    await evalJs(`window.filePopClose()`);
    // 收尾：还原 fetch，退出文件页
    await evalJs(`(() => {
      window.fetch = window.__realFetch;
      document.getElementById("btn-files-back").click();
      return true;
    })()`);
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
