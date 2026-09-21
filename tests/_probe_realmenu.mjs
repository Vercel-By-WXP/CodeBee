/* 一次性诊断：连真机 8765（带令牌），真实点击复现「厂商菜单选不了模型」。
 * 跑法：node tests/_probe_realmenu.mjs  （只读浏览+前端菜单点击，不写配置） */
import { spawn } from "node:child_process";
import { mkdtempSync, rmSync } from "node:fs";
import { tmpdir } from "node:os";
import { join, dirname } from "node:path";
import { fileURLToPath } from "node:url";

const SERVICE = process.argv[2] || "http://127.0.0.1:8765";
const TOKEN = process.argv[3] || "";
const CDP_PORT = 9760 + (process.pid % 200);
const EDGE = "C:\\Program Files (x86)\\Microsoft\\Edge\\Application\\msedge.exe";
const ROOT = join(dirname(fileURLToPath(import.meta.url)), "..");
const sleep = (ms) => new Promise((r) => setTimeout(r, ms));

async function main() {
  const profile = mkdtempSync(join(tmpdir(), "tutti-realmenu-"));
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
    ws.onmessage = (ev) => { const m = JSON.parse(ev.data); if (m.id && pending.has(m.id)) pending.get(m.id)(m); };
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
    await send("Page.enable");
    await send("Page.navigate", { url: SERVICE + "/?token=" + TOKEN });
    await sleep(5000);

    console.log("app.js 版本标记:", await evalJs(`[
      typeof window.toggleModelMenu === "function" ? "toggleModelMenu✓" : "toggleModelMenu✗",
      typeof window.cmpDirectBtnSync === "function" ? "cmpDirectBtnSync✓" : "cmpDirectBtnSync✗",
      document.getElementById("f-direct-wrap") ? "f-direct-wrap✓" : "f-direct-wrap✗",
      document.getElementById("welcome") && !document.getElementById("welcome").classList.contains("hidden") ? "welcome开" : "welcome关"
    ].join("  ")`));
    await evalJs(`(function () { if (window.welcomeClose) welcomeClose(); return 1; })()`);

    // 开菜单（真实点击 pill）
    const bpt = JSON.parse(await evalJs(`(() => {
      const b = document.getElementById("f-direct-btn");
      if (!b) return "{}";
      const r = b.getBoundingClientRect();
      return JSON.stringify({ x: Math.round(r.x + r.width / 2), y: Math.round(r.y + r.height / 2),
        hidden: b.closest(".cmp-sel")?.classList.contains("hidden") });
    })()`));
    console.log("模型 pill 位置:", JSON.stringify(bpt));
    if (bpt.hidden) { console.log("pill 隐藏（非 direct 类型？）"); return; }
    await realClick(bpt.x, bpt.y);
    await sleep(500);
    const lvl1 = JSON.parse(await evalJs(`(() => {
      const m = document.getElementById("cmp-model-menu");
      const first = m?.querySelector("[data-p]:not([data-m])");
      const r = first ? first.getBoundingClientRect() : null;
      const hit = r ? document.elementFromPoint(r.x + r.width / 2, r.y + r.height / 2) : null;
      return JSON.stringify({ open: m && !m.classList.contains("hidden"),
        items: m ? m.querySelectorAll("[data-p]").length : 0,
        firstProv: first ? (first.dataset.p) : "",
        hitTag: hit ? hit.tagName : "NONE",
        hitSame: hit ? !!first.contains(hit) : false,
        x: r ? Math.round(r.x + r.width / 2) : 0, y: r ? Math.round(r.y + r.height / 2) : 0 });
    })()`));
    console.log("厂商级菜单:", JSON.stringify(lvl1));
    if (!lvl1.open || !lvl1.firstProv) { console.log("菜单未开/无厂商项"); return; }

    // 真实点击第一个厂商
    await realClick(lvl1.x, lvl1.y);
    await sleep(500);
    const lvl2 = JSON.parse(await evalJs(`(() => {
      const m = document.getElementById("cmp-model-menu");
      return JSON.stringify({ back: !!m.querySelector("[data-back]"),
        models: Array.from(m.querySelectorAll("[data-m]")).slice(0, 4).map((b) => b.dataset.m),
        pv: document.getElementById("f-direct-provider").value,
        btn: document.getElementById("f-direct-btn").textContent });
    })()`));
    console.log("点厂商后（模型级）:", JSON.stringify(lvl2));
    console.log(lvl2.back && lvl2.models.length ? "\n结论：真机实例上钻取正常——用户页面是旧脚本，需要强刷" : "\n结论：真机实例上复现「点厂商无反应」——代码在真机环境有 bug");
  } finally {
    try { proc.kill(); } catch (e) {}
    try { rmSync(profile, { recursive: true, force: true }); } catch (e) {}
  }
}
main().catch((e) => { console.error(e); process.exit(1); });
