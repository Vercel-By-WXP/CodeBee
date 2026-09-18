/* 工作目录「选择…」弹框 + 表单对齐核验（覆盖网页弹框回落通道）。
 * 主通道是系统原生「选择文件夹」对话框（ui_workdir_pick.mjs 覆盖）；本页把
 * /api/pick_folder stub 成 fallback=true，专验无 tkinter 机器的回落路径：
 *   A) 创建表单 grid-2 两列的 select 顶部对齐（label 等高修复）；
 *   B) /api/browse：缺省=主目录、指定目录=子目录列表、不存在=404、盘符列表可用；
 *   C) 页面流程：点「选择…」弹框打开 → 跳到目标目录 → 点子目录行「选这个」→
 *      输入框写回该子目录绝对路径；「使用当前目录」写回当前路径。
 * 前置：TUTTI_DATA 临时目录起 18798 服务；探针在 sandbox-workdir 下造 a/b 两级子目录。 */
import { spawn } from "node:child_process";
import { execSync } from "node:child_process";
import { mkdtempSync, rmSync, mkdirSync } from "node:fs";
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

const results = [];
const check = (n, c, d = "") => {
  results.push(!!c);
  console.log((c ? "  ✓ " : "  ✗ ") + n + (c ? "" : "　— " + String(d).slice(0, 300)));
};
const sleep = (ms) => new Promise((r) => setTimeout(r, ms));
const api = async (p) => (await fetch(SERVICE + p)).json();

async function main() {
  try {
    execSync(`netstat -ano | findstr ":${PORT} " | findstr "LISTENING"`, { stdio: "pipe" });
    console.error("端口 " + PORT + " 已被占用，先清理残留服务再跑");
    process.exit(2);
  } catch (e) { /* 空闲 */ }

  const dataDir = mkdtempSync(join(tmpdir(), "tutti-pick-"));
  const sandbox = join(dataDir, "sandbox-workdir");
  mkdirSync(join(sandbox, "a", "b"), { recursive: true });  // 两级子目录
  await new Promise((res) => {
    const p = spawn("python", ["-X", "utf8", join(ROOT, "tests", "_seed_ctx_fixtures.py")],
      { env: { ...process.env, TUTTI_DATA: dataDir }, cwd: ROOT, stdio: "ignore" });
    p.on("exit", res);
  });
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

  const profile = mkdtempSync(join(tmpdir(), "tutti-pick-edge-"));
  const proc = spawn(EDGE, ["--headless=new", "--disable-gpu", "--no-first-run",
    "--disable-sync", "--disable-extensions", `--user-data-dir=${profile}`,
    `--remote-debugging-port=${CDP_PORT}`, "--window-size=1400,950", "about:blank"], { stdio: "ignore" });
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
      if (ex) throw new Error("页面抛错：" + (ex.exception?.description || ex.text || "").slice(0, 240));
      return r.result?.result?.value;
    };
    await send("Page.enable");
    await send("Page.navigate", { url: SERVICE + "/" });
    await sleep(3500);

    /* 本页只验网页弹框回落通道：原生 pick_folder 一律回 fallback=true */
    await evalJs(`(() => {
      const orig = window.fetch.bind(window);
      window.fetch = (url, opts) => {
        if (String(url).includes("/api/pick_folder")) {
          return Promise.resolve(new Response(JSON.stringify({ path: "", fallback: true }),
            { status: 200, headers: { "Content-Type": "application/json" } }));
        }
        return orig(url, opts);
      };
      return 1;
    })()`);

    /* ---- A) Composer 布局契约：目录=框内首行上下文，目标居中，类型/发送在底部工具条 ---- */
    const align = await evalJs(`(() => {
      const typeBtn = document.getElementById("f-type-btn");
      const wd = document.querySelector(".cmp-wd");
      const goal = document.getElementById("f-goal");
      const send = document.getElementById("btn-create");
      const tb = typeBtn && typeBtn.getBoundingClientRect();
      const wb = wd && wd.getBoundingClientRect();
      const gb = goal && goal.getBoundingClientRect();
      return JSON.stringify({
        hasType: !!typeBtn, hasWd: !!wd, hasGoal: !!goal, hasSend: !!send,
        typeTop: tb ? Math.round(tb.top) : -1, wdTop: wb ? Math.round(wb.top) : -1,
        wdInBoxFirst: !!(wb && gb && wd.closest(".cmp-box") && wb.top < gb.top),
        typeBelowGoal: !!(tb && gb && tb.top > gb.bottom - 2),
        sendInBox: !!(send && send.closest(".cmp-box")),
      });
    })()`);
    const al = JSON.parse(align);
    check("Composer 四件套齐全", al.hasType && al.hasWd && al.hasGoal && al.hasSend, align);
    check("工作目录行在输入框内首行", al.wdInBoxFirst, align);
    check("类型选择在目标框下方工具条", al.typeBelowGoal, align);
    check("发送按钮在 composer 框内", al.sendInBox, align);

    /* ---- B) /api/browse ---- */
    const home = await api("/api/browse");
    check("缺省 path 返回主目录", home.path && home.path !== "此电脑" && Array.isArray(home.dirs),
      JSON.stringify(home).slice(0, 160));
    const sb = await api("/api/browse?path=" + encodeURIComponent(sandbox));
    check("指定目录返回子目录列表", sb.path === sandbox && sb.dirs.includes("a"), JSON.stringify(sb));
    check("返回上级（非根目录）", !!sb.parent, JSON.stringify(sb));
    const nf = await api("/api/browse?path=" + encodeURIComponent(join(dataDir, "no-such-dir")));
    check("不存在目录 404", nf.error && /不存在/.test(nf.error), JSON.stringify(nf));
    const dv = await api("/api/browse?path=__drives__");
    check("盘符列表可用", dv.path === "此电脑" && dv.dirs.some((d) => /^[A-Z]:\\?$/.test(d)),
      JSON.stringify(dv));

    /* ---- C1) 点「选择…」按钮 → 弹框打开（从「此电脑」开始）---- */
    await evalJs(`(() => {
      const btn = document.querySelector("#f-workdir").closest(".input-row").querySelector("button");
      btn.click(); return 1;
    })()`);
    await sleep(800);
    const opened = await evalJs(`JSON.stringify({
      title: document.getElementById("modal-title").textContent,
      rows: document.querySelectorAll("#pk-body .pk-row").length,
      path: (document.querySelector("#pk-body .pk-path") || {}).textContent,
    })`);
    const op = JSON.parse(opened);
    // 「此电脑」页本身就是盘符列表，不再显示「此电脑」按钮；盘符行 data-p 以 :\ 结尾
    check("弹框打开且列出盘符", op.title === "选择文件夹" && op.rows > 0 && op.path === "此电脑", opened);

    /* ---- C2) 跳到 sandbox → 点 a 行「选这个」→ 输入框写回 ---- */
    await evalJs(`(async () => { await pickerBrowse(${JSON.stringify(sandbox)}); return 1; })()`);
    await sleep(500);
    const rows = await evalJs(`JSON.stringify(
      [...document.querySelectorAll("#pk-body .pk-row")].map(r => r.dataset.p))`);
    check("sandbox 下列出 a/b 子目录行", JSON.parse(rows).length >= 1, rows);
    await evalJs(`(async () => {
      const row = [...document.querySelectorAll("#pk-body .pk-row")].find(r => r.dataset.p.endsWith("\\\\a"));
      row.querySelector("button").click(); return 1;
    })()`);
    await sleep(300);
    const v1 = await evalJs(`document.getElementById("f-workdir").value`);
    check("「选这个」写回子目录绝对路径", v1 === join(sandbox, "a"), "v1=" + v1);
    check("选择后弹框关闭", await evalJs(`document.getElementById("modal").classList.contains("hidden")`));

    /* ---- C3) 再开 → 进入 a → 底部「使用当前目录」 ---- */
    await evalJs(`(() => {
      document.querySelector("#f-workdir").closest(".input-row").querySelector("button").click();
      return 1;
    })()`);
    await sleep(500);
    await evalJs(`(async () => { await pickerBrowse(${JSON.stringify(join(sandbox, "a"))}); return 1; })()`);
    await sleep(800);
    await evalJs(`(() => {
      const btn = [...document.querySelectorAll("#modal-foot button")].find(b => b.textContent.includes("使用当前目录"));
      btn.click(); return 1;
    })()`);
    await sleep(300);
    const v2 = await evalJs(`document.getElementById("f-workdir").value`);
    check("「使用当前目录」写回当前路径", v2 === join(sandbox, "a"), "v2=" + v2);

    /* ---- C4) 设置页同款按钮存在 ---- */
    const setBtn = await evalJs(`(() => {
      const row = document.getElementById("set-workdir").closest(".input-row");
      return !!row && [...row.querySelectorAll("button")].some(b => b.textContent.includes("选择"));
    })()`);
    check("设置页默认路径也有「选择…」按钮", setBtn);
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
