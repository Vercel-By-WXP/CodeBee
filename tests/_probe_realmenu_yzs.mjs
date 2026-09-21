/* 定向诊断：真机 8765 上真实点击「云知声」厂商项，抓取控制台异常与菜单状态 */
import { spawn } from "node:child_process";
import { mkdtempSync, rmSync } from "node:fs";
import { tmpdir } from "node:os";
import { join, dirname } from "node:path";
import { fileURLToPath } from "node:url";

const SERVICE = process.argv[2] || "http://127.0.0.1:8765";
const TOKEN = process.argv[3] || "";
const CDP_PORT = 9780 + (process.pid % 150);
const EDGE = "C:\\Program Files (x86)\\Microsoft\\Edge\\Application\\msedge.exe";
const ROOT = join(dirname(fileURLToPath(import.meta.url)), "..");
const sleep = (ms) => new Promise((r) => setTimeout(r, ms));

async function main() {
  const profile = mkdtempSync(join(tmpdir(), "tutti-yzs-"));
  const proc = spawn(EDGE, ["--headless=new", "--disable-gpu", "--no-first-run", "--disable-sync",
    `--user-data-dir=${profile}`, `--remote-debugging-port=${CDP_PORT}`, "--window-size=1400,950", "about:blank"],
    { stdio: "ignore" });
  try {
    let wsUrl = null;
    for (let i = 0; i < 30 && !wsUrl; i++) {
      await sleep(400);
      try {
        const list = await (await fetch(`http://127.0.0.1:${CDP_PORT}/json`)).json();
        wsUrl = list.find((t) => t.type === "page")?.webSocketDebuggerUrl;
      } catch (e) {}
    }
    const ws = new WebSocket(wsUrl);
    await new Promise((res, rej) => { ws.onopen = res; ws.onerror = rej; });
    let seq = 0; const pending = new Map();
    const events = [];
    ws.onmessage = (ev) => {
      const m = JSON.parse(ev.data);
      if (m.id && pending.has(m.id)) { pending.get(m.id)(m); return; }
      if (m.method === "Runtime.exceptionThrown")
        events.push("EXC: " + (m.params.exceptionDetails?.exception?.description || m.params.exceptionDetails?.text).slice(0, 200));
      if (m.method === "Runtime.consoleAPICalled" && m.params.type === "error")
        events.push("ERR: " + (m.params.args || []).map((a) => a.value || a.description || "").join(" ").slice(0, 200));
    };
    const send = (method, params = {}) => new Promise((res) => { const id = ++seq; pending.set(id, res); ws.send(JSON.stringify({ id, method, params })); });
    const evalJs = async (expr) => {
      const r = await send("Runtime.evaluate", { expression: expr, returnByValue: true, awaitPromise: true });
      if (r.result?.exceptionDetails) throw new Error(r.result.exceptionDetails.exception?.description || "eval failed");
      return r.result?.result?.value;
    };
    const realClick = async (x, y) => {
      await send("Input.dispatchMouseEvent", { type: "mousePressed", x, y, button: "left", clickCount: 1 });
      await send("Input.dispatchMouseEvent", { type: "mouseReleased", x, y, button: "left", clickCount: 1 });
    };
    await send("Runtime.enable");
    await send("Page.enable");
    await send("Page.navigate", { url: SERVICE + "/?token=" + TOKEN });
    await sleep(5000);
    await evalJs(`(function () { if (window.welcomeClose) welcomeClose(); return 1; })()`);
    await evalJs(`(function () { if (typeof switchTab === "function") switchTab("tasks"); return 1; })()`);
    await sleep(800);
    await evalJs(`(function () { if (window.welcomeClose) welcomeClose(); return 1; })()`);

    const bpt = JSON.parse(await evalJs(`(() => {
      const b = document.getElementById("f-direct-btn");
      if (!b || b.closest(".cmp-sel").classList.contains("hidden")) return "{}";
      const r = b.getBoundingClientRect();
      return JSON.stringify({ x: Math.round(r.x + r.width / 2), y: Math.round(r.y + r.height / 2) });
    })()`));
    if (!bpt.x) { console.log("pill 不可见（当前类型非直连？）"); return; }
    await realClick(bpt.x, bpt.y);
    await sleep(400);

    // 找「云知声」项并真实点击
    const yzs = JSON.parse(await evalJs(`(() => {
      const items = Array.from(document.querySelectorAll("#cmp-model-menu [data-p]"));
      const b = items.find((x) => (x.textContent || "").indexOf("云知声") >= 0);
      if (!b) return "{}";
      const r = b.getBoundingClientRect();
      return JSON.stringify({ x: Math.round(r.x + r.width / 2), y: Math.round(r.y + r.height / 2),
        p: b.dataset.p, text: (b.textContent || "").trim() });
    })()`));
    console.log("云知声项:", JSON.stringify(yzs));
    if (!yzs.x) { console.log("菜单里没有云知声"); return; }
    await realClick(yzs.x, yzs.y);
    await sleep(600);
    const after = JSON.parse(await evalJs(`(() => {
      const m = document.getElementById("cmp-model-menu");
      return JSON.stringify({ back: !!m.querySelector("[data-back]"),
        modelItems: Array.from(m.querySelectorAll("[data-m]")).map((b) => b.dataset.m).slice(0, 6),
        pv: document.getElementById("f-direct-provider").value,
        btn: document.getElementById("f-direct-btn").textContent });
    })()`));
    console.log("点云知声后:", JSON.stringify(after, null, 1));
    console.log("浏览器异常/错误:", events.length ? "\n  " + events.slice(0, 5).join("\n  ") : "无");
  } finally {
    try { proc.kill(); } catch (e) {}
    try { rmSync(profile, { recursive: true, force: true }); } catch (e) {}
  }
}
main().catch((e) => { console.error(e); process.exit(1); });
