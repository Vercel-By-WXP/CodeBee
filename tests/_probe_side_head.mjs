/* 诊断探针：侧栏「任务」区头（标签 + 展开/归档/提示音三颗工具钮）布局与状态检查。
 * 自起临时服务（18833，临时数据目录），Edge headless CDP。只读检查（点击仅前端状态）。 */
import { spawn } from "node:child_process";
import { mkdtempSync, rmSync, mkdirSync } from "node:fs";
import { tmpdir } from "node:os";
import { join, dirname } from "node:path";
import { fileURLToPath } from "node:url";

const PORT = 18833;
const SERVICE = "http://127.0.0.1:" + PORT;
const CDP_PORT = 9348;
const EDGE = "C:\\Program Files (x86)\\Microsoft\\Edge\\Application\\msedge.exe";
const ROOT = join(dirname(fileURLToPath(import.meta.url)), "..");
const sleep = (ms) => new Promise((r) => setTimeout(r, ms));

async function main() {
  const tmp = mkdtempSync(join(tmpdir(), "tutti-probe-"));
  const dataDir = join(tmp, "data");
  mkdirSync(dataDir, { recursive: true });
  let svc = null, edge = null, ws = null;
  try {
    svc = spawn("python", ["-X", "utf8", join(ROOT, "app", "main.py"), "--port", String(PORT),
      "--no-browser", "--host", "127.0.0.1"], {
      env: { ...process.env, TUTTI_DATA: dataDir, PYTHONPATH: ROOT },
      cwd: ROOT, stdio: "ignore",
    });
    for (let i = 0; i < 40; i++) { await sleep(500); try { if ((await fetch(SERVICE + "/api/state")).ok) break; } catch (e) {} }
    edge = spawn(EDGE, ["--headless=new", "--disable-gpu", "--no-first-run",
      `--user-data-dir=${join(tmp, "p")}`, `--remote-debugging-port=${CDP_PORT}`,
      "--window-size=1400,950", "about:blank"], { stdio: "ignore" });
    let target = null;
    for (let i = 0; i < 30 && !target; i++) {
      await sleep(500);
      try { target = (await (await fetch(`http://127.0.0.1:${CDP_PORT}/json/list`)).json()).find((t) => t.type === "page"); } catch (e) {}
    }
    ws = new WebSocket(target.webSocketDebuggerUrl);
    await new Promise((res, rej) => { ws.onopen = res; ws.onerror = rej; });
    let seq = 0; const pending = new Map();
    ws.onmessage = (ev) => { const m = JSON.parse(ev.data); if (m.id && pending.has(m.id)) pending.get(m.id)(m); };
    const send = (method, params = {}) => new Promise((res) => { const id = ++seq; pending.set(id, res); ws.send(JSON.stringify({ id, method, params })); });
    const evalJs = async (expr) => {
      const r = await send("Runtime.evaluate", { expression: expr, returnByValue: true, awaitPromise: true });
      if (r.result?.exceptionDetails) return "!!EXC: " + JSON.stringify(r.result.exceptionDetails).slice(0, 400);
      return r.result?.result?.value;
    };
    await send("Runtime.enable");
    await send("Log.enable");
    const errors = [];
    ws.onmessage = (() => { const prev = ws.onmessage; return (ev) => { prev(ev); const m = JSON.parse(ev.data);
      if (m.method === "Runtime.exceptionThrown") errors.push(m.params.exceptionDetails?.exception?.description || JSON.stringify(m.params).slice(0, 200));
      if (m.method === "Log.entryAdded" && m.params.entry.level === "error") errors.push(m.params.entry.text?.slice(0, 200)); }; })();
    await send("Page.enable");
    await send("Page.navigate", { url: SERVICE + "/" });
    await sleep(3500);

    console.log("=== 区头布局几何 ===");
    console.log(await evalJs(`(() => {
      const row = document.querySelector(".side-label-row");
      const label = row.querySelector(".side-label");
      const btns = [...row.querySelectorAll("button")];
      const rr = row.getBoundingClientRect();
      const info = btns.map((b) => {
        const r = b.getBoundingClientRect();
        return { id: b.id, w: +r.width.toFixed(1), h: +r.height.toFixed(1), x: +r.x.toFixed(1) };
      });
      const gaps = info.slice(1).map((b, i) => +(b.x - (info[i].x + info[i].w)).toFixed(1));
      return JSON.stringify({
        label: { text: label.textContent.trim(), padBottom: getComputedStyle(label).paddingBottom, marginRight: getComputedStyle(label).marginRight },
        buttons: info, gapsBetween: gaps,
        clusterRight: +(info[info.length - 1].x + info[info.length - 1].w).toFixed(1), rowRight: +rr.right.toFixed(1),
      }, null, 1);
    })()`));

    console.log("=== 归档开关状态 ===");
    console.log(await evalJs(`(async () => {
      const b = document.getElementById("btn-side-arch");
      const before = { title: b.title, on: b.classList.contains("on") };
      b.click(); await new Promise((r) => setTimeout(r, 400));
      const afterOn = { title: b.title, on: b.classList.contains("on") };
      b.click(); await new Promise((r) => setTimeout(r, 400));
      const afterOff = { title: b.title, on: b.classList.contains("on") };
      return JSON.stringify({ before, afterOn, afterOff }, null, 1);
    })()`));

    console.log("=== 展开钮翻转（注入假文件夹验证逻辑）===");
    console.log(await evalJs(`(() => {
      const b = document.getElementById("btn-side-expand");
      const box = document.getElementById("side-tasks");
      box.innerHTML = '<details class="sdir"><summary>fake</summary></details>';
      if (typeof syncSideExpandBtn !== "function") return "!! syncSideExpandBtn 不是全局函数";
      syncSideExpandBtn();
      const closed = { title: b.title, up: b.classList.contains("up") };
      box.querySelector("details.sdir").open = true;
      syncSideExpandBtn();
      const opened = { title: b.title, up: b.classList.contains("up") };
      box.innerHTML = "";
      syncSideExpandBtn();
      const empty = { title: b.title, up: b.classList.contains("up") };
      return JSON.stringify({ closed, opened, emptyAfterClear: empty }, null, 1);
    })()`));

    console.log("=== 页面 JS 错误 ===");
    console.log(errors.length ? errors.join("\n") : "（无）");
  } finally {
    try { ws && ws.close(); } catch (e) {}
    try { edge && edge.kill(); } catch (e) {}
    try { svc && svc.kill(); } catch (e) {}
    try { rmSync(tmp, { recursive: true, force: true }); } catch (e) {}
  }
}
main().catch((e) => { console.error(e); process.exit(2); });
