/* 「最近任务」面板移除 + 侧栏已归档找回 UI 验证（Edge headless + CDP，沿 ui_check_about 模板）。
 * 自起临时服务（TUTTI_DATA=临时目录、端口 18799，可 SERVICE/CDP_PORT 覆盖）→ 造数后把
 * task-3d 预置为已归档 → 断言：主区不再有「最近任务」面板与 #task-list；侧栏头部有
 * 「显示已归档」开关（默认不选中）；默认侧栏不含已归档任务；点开关后已归档任务灰显回
 * 原文件夹（带「已归档」徽章，会话内生效、不写 localStorage）；再点还原。顺带收集页面
 * JS 错误（旧 chk-archived 接线若残留会在 init 抛错）。 */
import { spawn, spawnSync } from "node:child_process";
import { mkdtempSync, rmSync, readFileSync, writeFileSync, accessSync } from "node:fs";
import { tmpdir } from "node:os";
import { join, dirname } from "node:path";
import { fileURLToPath } from "node:url";
import { get as httpGet } from "node:http";

/* Node 24 的 undici 对 Edge DevTools 端点 /json/list 的响应会断言失败，改用原生 http.get */
const cdpList = (port) => new Promise((res, rej) => {
  httpGet(`http://127.0.0.1:${port}/json/list`, (r) => {
    let b = "";
    r.on("data", (c) => { b += c; });
    r.on("end", () => { try { res(JSON.parse(b)); } catch (e) { rej(e); } });
  }).on("error", rej);
});

const PORT = Number(process.env.PORT) || 18799;
const SERVICE = process.env.SERVICE || ("http://127.0.0.1:" + PORT);
const CDP_PORT = Number(process.env.CDP_PORT) || 9338;
const EDGE = ["C:\\Program Files (x86)\\Microsoft\\Edge\\Application\\msedge.exe",
  "C:\\Program Files\\Microsoft\\Edge\\Application\\msedge.exe"]
  .find((p) => { try { accessSync(p); return true; } catch (e) { return false; } });
const ROOT = join(dirname(fileURLToPath(import.meta.url)), "..");

const results = [];
const check = (n, c, d = "") => {
  results.push(!!c);
  console.log((c ? "  ✓ " : "  ✗ ") + n + (c ? "" : "　— " + String(d).slice(0, 300)));
};
const sleep = (ms) => new Promise((r) => setTimeout(r, ms));

async function main() {
  const dataDir = mkdtempSync(join(tmpdir(), "tutti-arch-"));

  // 1) 造数 + 预置一个已归档任务（直接改任务 JSON，不碰真实 data/）
  const seeded = spawnSync("python", [join("tests", "_seed_tree_fixtures.py")], {
    cwd: ROOT, env: { ...process.env, TUTTI_DATA: dataDir }, encoding: "utf-8",
  });
  check("造数脚本执行成功", seeded.status === 0, seeded.stderr || seeded.stdout);
  const taskFile = join(dataDir, "tasks", "task-3d.json");
  const task3d = JSON.parse(readFileSync(taskFile, "utf-8"));
  task3d.archived = true;
  writeFileSync(taskFile, JSON.stringify(task3d, null, 2), "utf-8");

  // 2) 临时服务（TUTTI_DATA 隔离）
  const svc = spawn("python", ["app/main.py", "--port", String(PORT),
    "--no-browser", "--no-public-tunnel"],
    { cwd: ROOT, env: { ...process.env, TUTTI_DATA: dataDir }, stdio: "ignore" });
  let up = false;
  for (let i = 0; i < 40 && !up; i++) {
    await sleep(500);
    try {
      const r = await fetch(SERVICE + "/api/state");
      up = r.ok;
      await r.arrayBuffer();   // 必须消费响应体，否则 socket 结束时 undici 断言崩溃
    } catch (e) { /* 未就绪 */ }
  }
  check("临时服务就绪（" + SERVICE + "，TUTTI_DATA 隔离）", up);

  // 3) Edge headless + CDP
  const profile = mkdtempSync(join(tmpdir(), "tutti-arch-edge-"));
  const proc = spawn(EDGE, [
    "--headless=new", "--disable-gpu", "--no-first-run",
    `--user-data-dir=${profile}`, `--remote-debugging-port=${CDP_PORT}`,
    "--window-size=1400,950", "about:blank",
  ], { stdio: "ignore" });

  try {
    let target = null;
    for (let i = 0; i < 30 && !target; i++) {
      await sleep(500);
      try {
        const list = await cdpList(CDP_PORT);
        target = list.find((t) => t.type === "page");
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
    };
    const evalJs = async (x) => {
      const r = await send("Runtime.evaluate", { expression: x, returnByValue: true, awaitPromise: true });
      const ex = r.result?.exceptionDetails;
      if (ex) throw new Error("页面表达式抛错：" + (ex.exception?.description || ex.text || "").slice(0, 240));
      return r.result?.result?.value;
    };
    await send("Page.enable");
    // 页面脚本跑起来前注入错误收集：init 里若还碰已删除的 chk-archived/task-list 会在这里现形
    await send("Page.addScriptToEvaluateOnNewDocument", {
      source: "window.__errs=[];window.addEventListener('error',function(e){window.__errs.push(String(e.message))});",
    });
    await send("Page.navigate", { url: SERVICE + "/" });
    await sleep(3500);
    for (let i = 0; i < 20; i++) {
      const n = await evalJs(`document.querySelectorAll("#side-tasks .stask").length`);
      if (n > 0) break;
      await sleep(500);
    }

    /* ---- A) 主区不再有「最近任务」面板 ---- */
    const gone = await evalJs(`(() => ({
      taskList: !!document.getElementById("task-list"),
      chkArch: !!document.getElementById("chk-archived"),
      textHit: document.body.innerText.includes("最近任务"),
      renderTaskList: typeof renderTaskList,
    }))()`);
    check("#task-list 面板已移除", !gone.taskList, JSON.stringify(gone));
    check("页面上不再出现「最近任务」文案", !gone.textHit, JSON.stringify(gone));
    check("renderTaskList 已随面板删除", gone.renderTaskList === "undefined", JSON.stringify(gone));

    /* ---- B) 侧栏头部开关存在；默认侧栏不含已归档任务（proj-gamma 整文件夹消失） ---- */
    const init = await evalJs(`(() => {
      const ds = [...document.querySelectorAll("#side-tasks details.sdir")];
      return {
        toggle: !!document.getElementById("btn-side-arch"),
        dirs: ds.map((d) => (d.querySelector("summary .t") || {}).textContent?.trim()),
        titles: [...document.querySelectorAll("#side-tasks .stask .t")].map((x) => x.textContent.trim()),
        archRows: document.querySelectorAll("#side-tasks .stask.archived").length,
        btnOn: document.getElementById("btn-side-arch").classList.contains("on"),
      };
    })()`);
    check("侧栏头部有「显示已归档」开关", init.toggle, JSON.stringify(init));
    check("默认图标不选中（.on 关，符合「默认隐藏已归档」）", !init.btnOn, JSON.stringify(init));
    check("默认侧栏不显示已归档任务（只剩 3 行）", init.titles.length === 3 && init.archRows === 0,
      JSON.stringify(init.titles));
    check("已归档任务所在文件夹 proj-gamma 默认隐藏", !init.dirs.includes("proj-gamma"),
      JSON.stringify(init.dirs));

    /* ---- C) 点开关：已归档任务灰显回原文件夹，带「已归档」徽章（会话内生效，不再持久化） ---- */
    await evalJs(`document.getElementById("btn-side-arch").click()`);
    await sleep(600);
    const on = await evalJs(`(() => {
      const arch = document.querySelector("#side-tasks .stask.archived");
      return {
        ls: localStorage.getItem("orch.showArchived"),
        btnOn: document.getElementById("btn-side-arch").classList.contains("on"),
        dirs: [...document.querySelectorAll("#side-tasks details.sdir")].map((d) => (d.querySelector("summary .t") || {}).textContent?.trim()),
        archTitle: arch ? arch.querySelector(".t").textContent.trim() : "",
        archBadge: arch ? (arch.querySelector(".sbadge.archb") || {}).textContent?.trim() : "",
        inGamma: arch ? !!arch.closest("details.sdir")?.dataset.dir?.endsWith("proj-gamma") : false,
      };
    })()`);
    check("开关打开后不再写 localStorage（会话级开关）", on.ls === null, JSON.stringify(on));
    check("开关按钮高亮（.on）", on.btnOn, JSON.stringify(on));
    check("已归档任务回到 proj-gamma 文件夹", on.inGamma && on.dirs.includes("proj-gamma"), JSON.stringify(on.dirs));
    check("已归档行带灰色「已归档」徽章", on.archTitle === "样式核验-三天前（20 步长任务）" && on.archBadge === "已归档",
      JSON.stringify({ archTitle: on.archTitle, archBadge: on.archBadge }));

    /* ---- D) 再点还原：已归档任务再次隐藏 ---- */
    await evalJs(`document.getElementById("btn-side-arch").click()`);
    await sleep(600);
    const off = await evalJs(`(() => ({
      ls: localStorage.getItem("orch.showArchived"),
      archRows: document.querySelectorAll("#side-tasks .stask.archived").length,
    }))()`);
    check("再点还原：已归档行消失、localStorage 保持为空", off.archRows === 0 && off.ls === null, JSON.stringify(off));

    /* ---- E) 页面无 JS 错误（含 init 期） ---- */
    const errs = await evalJs(`window.__errs`);
    check("页面无 JS 运行错误", (errs || []).length === 0, JSON.stringify(errs));

    ws.close();
  } finally {
    try { proc.kill(); } catch (e) { /* ignore */ }
    try { svc.kill(); } catch (e) { /* ignore */ }
    await sleep(300);
    try { rmSync(profile, { recursive: true, force: true }); } catch (e) { /* ignore */ }
    try { rmSync(dataDir, { recursive: true, force: true }); } catch (e) { /* ignore */ }
  }

  const pass = results.filter(Boolean).length;
  console.log(`\n${pass}/${results.length} 项通过`);
  process.exit(pass === results.length ? 0 : 1);
}

main().catch((e) => { console.error("验证脚本异常：", e); process.exit(2); });
