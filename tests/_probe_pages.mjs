/* 诊断探针：把侧栏 + 自动化/插件市场两个子页实际渲染成文本 dump，抓页面 JS 错误。
 * 自起临时服务（18831，临时数据目录），Edge headless CDP。只读检查。 */
import { spawn } from "node:child_process";
import { mkdtempSync, rmSync, mkdirSync } from "node:fs";
import { tmpdir } from "node:os";
import { join, dirname } from "node:path";
import { fileURLToPath } from "node:url";

const PORT = 18831;
const SERVICE = "http://127.0.0.1:" + PORT;
const CDP_PORT = 9346;
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

    console.log("=== 侧栏（主视图）===");
    console.log(await evalJs(`(() => {
      const vis = (el) => el && el.getBoundingClientRect().height > 0;
      const side = document.querySelector("#sidebar");
      const texts = [...side.querySelectorAll(".side-main > *, .side-label-row > *")].filter(vis)
        .map((e) => e.className + " | " + e.textContent.trim().replace(/\\s+/g, " ").slice(0, 60));
      const tree = [...document.querySelectorAll("#side-tasks .sdir > summary, #side-tasks .stask")]
        .slice(0, 5).map((e) => e.textContent.trim().replace(/\\s+/g, " ").slice(0, 50));
      return JSON.stringify({ rows: texts, treeSample: tree, searchVisible: vis(document.querySelector(".side-search")) }, null, 1);
    })()`));

    console.log("=== 点快捷入口 ===");
    console.log(await evalJs(`(async () => {
      document.querySelector("#btn-q-automation").click();
      await new Promise(r => setTimeout(r, 1200));
      const autoOpen = !document.querySelector("#sub-automation").classList.contains("hidden");
      document.querySelector("#btn-q-market").click();
      await new Promise(r => setTimeout(r, 1200));
      const marketOpen = !document.querySelector("#sub-market").classList.contains("hidden");
      return JSON.stringify({ autoOpen, marketOpen });
    })()`));

    console.log("=== 命令面板 ===");
    console.log(await evalJs(`(async () => {
      const mask = document.querySelector("#cmdk-mask");
      if (!mask) return "!! 无面板 DOM";
      document.querySelector("#btn-cmdk").click();
      await new Promise(r => setTimeout(r, 300));
      const open1 = !mask.classList.contains("hidden");
      const items0 = document.querySelectorAll("#cmdk-list .cmdk-item").length;
      const inp = document.querySelector("#cmdk-q");
      inp.value = "自"; inp.dispatchEvent(new Event("input", { bubbles: true }));
      await new Promise(r => setTimeout(r, 200));
      const labels1 = [...document.querySelectorAll("#cmdk-list .cmdk-item .l")].map(x => x.textContent);
      inp.value = "zzz不存在"; inp.dispatchEvent(new Event("input", { bubbles: true }));
      await new Promise(r => setTimeout(r, 200));
      const empty1 = !!document.querySelector("#cmdk-list .cmdk-empty");
      inp.value = ""; inp.dispatchEvent(new Event("input", { bubbles: true }));
      await new Promise(r => setTimeout(r, 200));
      inp.dispatchEvent(new KeyboardEvent("keydown", { key: "Escape", bubbles: true }));
      await new Promise(r => setTimeout(r, 200));
      const closed = mask.classList.contains("hidden");
      return JSON.stringify({ open1, items0, labels1, empty1, closed });
    })()`));

    console.log("=== 进入设置 → 自动化 ===");
    await evalJs(`document.querySelector("#btn-settings").click()`);
    await sleep(1200);
    await evalJs(`[...document.querySelectorAll(".set-item")].find((b) => b.dataset.sub === "automation")?.click()`);
    await sleep(1500);
    console.log(await evalJs(`(() => {
      const p = document.querySelector("#sub-automation");
      if (!p || p.classList.contains("hidden")) return "!! 子页隐藏";
      const title = p.querySelector("h1,h2,.panel-head,.sec-title");
      const btns = [...p.querySelectorAll("button")].map((b) => b.textContent.trim().replace(/\\s+/g, " ")).filter(Boolean).slice(0, 15);
      const body = p.textContent.trim().replace(/\\s+/g, " ").slice(0, 600);
      return JSON.stringify({ title: title?.textContent.trim(), buttons: btns, bodySample: body }, null, 1);
    })()`));

    console.log("=== 插件市场 ===");
    await evalJs(`[...document.querySelectorAll(".set-item")].find((b) => b.dataset.sub === "market")?.click()`);
    await sleep(1500);
    console.log(await evalJs(`(() => {
      const p = document.querySelector("#sub-market");
      if (!p || p.classList.contains("hidden")) return "!! 子页隐藏";
      const cards = [...p.querySelectorAll("#mk-grid > *")].map((c) => c.textContent.trim().replace(/\\s+/g, " ").slice(0, 90));
      const btns = [...p.querySelectorAll("button")].map((b) => b.textContent.trim().replace(/\\s+/g, " ")).filter(Boolean).slice(0, 12);
      return JSON.stringify({ cardCount: cards.length, cardsSample: cards.slice(0, 4), buttons: btns }, null, 1);
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
