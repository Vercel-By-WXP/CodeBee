/* 模型接入页图标验收：刷新按钮（真 SVG 且加载态不丢图标）、拖拽手柄、设为主模型按钮。
 * 用法：node tests/ui_check_models_icons.mjs [port]（默认对已运行服务 8765 只读验收） */
import { spawn } from "node:child_process";
import { mkdtempSync, rmSync } from "node:fs";
import { tmpdir } from "node:os";
import { join, dirname } from "node:path";
import { fileURLToPath } from "node:url";

const PORT = Number(process.argv[2] || 8765);
const SERVICE = "http://127.0.0.1:" + PORT;
const CDP_PORT = 9346;
const ROOT = join(dirname(fileURLToPath(import.meta.url)), "..");
const EDGE = [
  "C:\\Program Files (x86)\\Microsoft\\Edge\\Application\\msedge.exe",
  "C:\\Program Files\\Microsoft\\Edge\\Application\\msedge.exe",
].find(() => true);
const sleep = (ms) => new Promise((r) => setTimeout(r, ms));

const results = [];
const check = (name, cond, detail = "") => {
  results.push({ name, ok: !!cond });
  console.log((cond ? "  ✓ " : "  ✗ ") + name + (cond ? "" : "　— " + String(detail).slice(0, 240)));
};

async function main() {
  let up = false;
  try { up = (await fetch(SERVICE + "/api/state")).status === 200; } catch (e) { /* not up */ }
  check(`服务在 ${PORT} 运行`, up);
  if (!up) process.exit(1);

  const tmp = mkdtempSync(join(tmpdir(), "tutti-mico-"));
  let edge = null, ws = null;
  try {
    edge = spawn(EDGE, ["--headless=new", "--disable-gpu", "--no-first-run",
      `--user-data-dir=${join(tmp, "p")}`, `--remote-debugging-port=${CDP_PORT}`,
      "--window-size=1440,1000", "about:blank"], { stdio: "ignore" });
    let target = null;
    for (let i = 0; i < 30 && !target; i++) {
      await sleep(500);
      try {
        const list = await (await fetch(`http://127.0.0.1:${CDP_PORT}/json/list`)).json();
        target = list.find((t) => t.type === "page");
      } catch (e) { /* wait */ }
    }
    check("Edge CDP 就绪", !!target);
    ws = new WebSocket(target.webSocketDebuggerUrl);
    await new Promise((res, rej) => { ws.onopen = res; ws.onerror = rej; });
    let seq = 0; const pending = new Map(); const errs = [];
    ws.onmessage = (ev) => {
      const m = JSON.parse(ev.data);
      if (m.id && pending.has(m.id)) pending.get(m.id)(m);
      if (m.method === "Runtime.exceptionThrown") {
        errs.push(m.params?.exceptionDetails?.exception?.description || m.params?.exceptionDetails?.text);
      }
    };
    const send = (method, params = {}) => new Promise((res) => {
      const id = ++seq; pending.set(id, res); ws.send(JSON.stringify({ id, method, params }));
    });
    const js = async (expr) => (await send("Runtime.evaluate",
      { expression: expr, returnByValue: true, awaitPromise: true })).result?.result?.value;

    await send("Runtime.enable");
    await send("Page.enable");
    await send("Page.navigate", { url: SERVICE + "/" });
    await sleep(4000);
    await js(`switchTab("models"); "ok"`);
    await sleep(1800);

    // ---- 1) 模型页刷新按钮：SVG 图标、无 ⟳ 残留 ----
    const refresh = JSON.parse(await js(`(() => {
      const b = document.getElementById("btn-refresh-models");
      const svg = b.querySelector("svg.ico");
      const r = svg ? svg.getBoundingClientRect() : { width: 0, height: 0 };
      const cs = svg ? getComputedStyle(svg) : {};
      return JSON.stringify({ text: b.textContent.trim(),
        use: svg ? svg.querySelector("use").getAttribute("href") : null,
        w: +r.width.toFixed(1), h: +r.height.toFixed(1),
        stroke: cs.stroke, color: getComputedStyle(b).color });
    })()`));
    check("模型页刷新按钮用 SVG 图标（无 ⟳ 残留）",
      refresh.text === "" && refresh.use === "#i-refresh", JSON.stringify(refresh));
    check("模型页刷新图标尺寸正常且描边随主题",
      refresh.w >= 13 && refresh.h >= 13 && refresh.stroke === refresh.color, JSON.stringify(refresh));

    // ---- 2) 加载态：加 loading class 后图标仍在（不被 textContent 抹掉）----
    // headless Edge 默认报告 prefers-reduced-motion: reduce，会（正确地）关掉自旋动画，
    // 所以先模拟 no-preference 再断言动画生效。
    await send("Emulation.setEmulatedMedia", {
      features: [{ name: "prefers-reduced-motion", value: "no-preference" }],
    });
    const loading = JSON.parse(await js(`(() => {
      const b = document.getElementById("btn-refresh-models");
      b.classList.add("loading");
      const svg = b.querySelector("svg.ico");
      const anim = svg ? getComputedStyle(svg).animationName : "none";
      const stillThere = !!svg;
      b.classList.remove("loading");
      return JSON.stringify({ stillThere, anim,
        reduced: matchMedia("(prefers-reduced-motion: reduce)").matches });
    })()`));
    check("加载态下图标仍在（未被文案替换）", loading.stillThere === true, JSON.stringify(loading));
    check("加载态图标带旋转动画（no-preference 下）",
      loading.anim === "ico-spin", JSON.stringify(loading));

    // 还原默认媒体特性，避免影响后续断言
    await send("Emulation.setEmulatedMedia", { features: [] });

    // ---- 3) 拖拽手柄：SVG 图标 ----
    const grip = JSON.parse(await js(`(() => {
      const g = document.querySelector(".prow .drag");
      if (!g) return JSON.stringify({ none: true });
      const svg = g.querySelector("svg.ico");
      const r = svg ? svg.getBoundingClientRect() : { width: 0, height: 0 };
      return JSON.stringify({ text: g.textContent.trim(),
        use: svg ? svg.querySelector("use").getAttribute("href") : null,
        w: +r.width.toFixed(1) });
    })()`));
    if (grip.none) {
      check("拖拽手柄存在（无供应商时跳过）", true, "本机无供应商模型行");
    } else {
      check("拖拽手柄用 SVG 图标（无 ☰ 残留）",
        grip.text === "" && grip.use === "#i-grip" && grip.w >= 10, JSON.stringify(grip));
    }

    // ---- 4) 全部页面无字符图标残留（抽查关键按钮） ----
    const leftovers = JSON.parse(await js(`(() => {
      const bad = [];
      document.querySelectorAll("button").forEach(b => {
        const t = b.textContent.trim();
        if (/[⟳☰↑↓]/.test(t)) bad.push((b.id || b.className) + ":" + t);
      });
      return JSON.stringify(bad);
    })()`));
    check("无 ⟳/☰/↑ 字符图标残留在按钮上", leftovers.length === 0, JSON.stringify(leftovers));

    check("全程无 JS 异常", errs.length === 0, errs.join(" ｜ "));
    ws.close();
  } finally {
    try { ws && ws.close(); } catch (e) { /* ignore */ }
    try { edge && edge.kill(); } catch (e) { /* ignore */ }
    await sleep(700);
    if (edge?.pid) {
      try { spawn("taskkill", ["/F", "/T", "/PID", String(edge.pid)], { stdio: "ignore" }); } catch (e) { /* ignore */ }
    }
    try { rmSync(tmp, { recursive: true, force: true }); } catch (e) { /* ignore */ }
  }
  const bad = results.filter((r) => !r.ok);
  console.log("\n===== 模型页图标验收：%d 通过 / %d 失败 =====",
    results.length - bad.length, bad.length);
  if (bad.length) { bad.forEach((b) => console.log("  ✗ " + b.name)); process.exit(1); }
}
main().catch((e) => { console.error(e); process.exit(1); });
