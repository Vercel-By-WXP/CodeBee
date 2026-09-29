/* 侧栏拖宽验证：右缘 #side-resizer 手柄拖动调宽（--side-w grid 列）+ localStorage 记忆 + 双击复原。
 * 覆盖：手柄贴右缘几何 / 默认 216 / CDP 真实鼠标拖 +80 跟手且落盘 / 刷新记忆 /
 * 左拖触底 clamp / 双击复原清键 / 设置视图同样生效 / 窄屏抽屉模式手柄隐藏。
 * ⚠️ Edge 启动器进程会秒退，taskkill /T 扑空后子进程残活还占着 CDP 口——
 * 固定口下一轮会连上残尸页面（带着上轮 Emulation 覆写）量出鬼数字。
 * 故 CDP/服务口都随机 + finally 按 netstat 找占口真 PID 补杀 + 归还自证。 */
import { spawn, spawnSync } from "node:child_process";
import { writeFileSync, mkdirSync, mkdtempSync, rmSync } from "node:fs";
import { tmpdir } from "node:os";
import { join, dirname } from "node:path";
import { fileURLToPath } from "node:url";

const PORT = Number(process.env.TUTTI_TEST_PORT) || (19000 + Math.floor(Math.random() * 900));
const SERVICE = "http://127.0.0.1:" + PORT;
const CDP_PORT = Number(process.env.TUTTI_TEST_CDP) || (21000 + Math.floor(Math.random() * 20000));
const ROOT = join(dirname(fileURLToPath(import.meta.url)), "..");
const EDGE_CANDIDATES = [
  "C:\\Program Files (x86)\\Microsoft\\Edge\\Application\\msedge.exe",
  "C:\\Program Files\\Microsoft\\Edge\\Application\\msedge.exe",
];

const results = [];
function check(name, cond, detail = "") {
  results.push({ name, ok: !!cond });
  console.log((cond ? "  ✓ " : "  ✗ ") + name + (cond ? "" : "　— " + String(detail).slice(0, 220)));
}
const sleep = (ms) => new Promise((r) => setTimeout(r, ms));

/* 杀掉占着指定端口的真 PID（Edge 启动器秒退后 /T 扑空的兜底），连试 3 轮并回报是否归还 */
async function killPortHolders(port) {
  for (let i = 0; i < 3; i++) {
    const pids = new Set();
    try {
      const out = spawnSync("netstat", ["-ano"], { encoding: "utf8" }).stdout || "";
      for (const line of out.split("\n")) {
        if (line.includes(":" + port + " ") && /LISTENING/i.test(line)) {
          const pid = line.trim().split(/\s+/).pop();
          if (pid && /^\d+$/.test(pid) && pid !== "0") pids.add(pid);
        }
      }
    } catch (e) { /* netstat 失败下轮再试 */ }
    if (!pids.size) return true;
    for (const pid of pids) {
      try { spawnSync("taskkill", ["/F", "/T", "/PID", pid], { stdio: "ignore" }); } catch (e) { /* ignore */ }
    }
    await sleep(600);
  }
  return false;
}

async function main() {
  const tmp = mkdtempSync(join(tmpdir(), "tutti-uiresize-"));
  const dataDir = join(tmp, "data");
  mkdirSync(dataDir, { recursive: true });
  writeFileSync(join(dataDir, "settings.json"), JSON.stringify({ cleanup_enabled: false }), "utf8");

  const svc = spawn("python", ["-X", "utf8", join(ROOT, "app", "main.py"),
    "--port", String(PORT), "--no-browser", "--host", "127.0.0.1"], {
    cwd: ROOT, stdio: "ignore",
    env: Object.assign({}, process.env, { TUTTI_DATA: dataDir, PYTHONPATH: ROOT }),
  });
  let edge = null, ws = null;
  try {
    let up = false;
    for (let i = 0; i < 40 && !up; i++) {
      await sleep(500);
      try { up = (await fetch(SERVICE + "/api/state")).status === 200; } catch (e) { /* wait */ }
    }
    check("临时服务启动", up);
    if (!up) throw new Error("service not up");

    edge = spawn(EDGE_CANDIDATES.find(() => true), [
      "--headless=new", "--disable-gpu", "--no-first-run",
      `--user-data-dir=${join(tmp, "profile")}`, `--remote-debugging-port=${CDP_PORT}`,
      "--window-size=1400,950", "about:blank",
    ], { stdio: "ignore" });
    let target = null;
    for (let i = 0; i < 30 && !target; i++) {
      await sleep(500);
      try {
        const list = await (await fetch(`http://127.0.0.1:${CDP_PORT}/json/list`)).json();
        target = list.find((t) => t.type === "page");
      } catch (e) { /* Edge 未就绪 */ }
    }
    check("Edge headless 启动并开放 CDP", !!target);
    if (!target) throw new Error("no CDP target");

    ws = new WebSocket(target.webSocketDebuggerUrl);
    await new Promise((res, rej) => { ws.onopen = res; ws.onerror = rej; });
    let seq = 0;
    const pending = new Map();
    ws.onmessage = (ev) => {
      const msg = JSON.parse(ev.data);
      if (msg.id && pending.has(msg.id)) pending.get(msg.id)(msg);
    };
    const send = (method, params = {}) => new Promise((res) => {
      const id = ++seq;
      pending.set(id, res);
      ws.send(JSON.stringify({ id, method, params }));
    });
    const evalJs = async (expr) => {
      const r = await send("Runtime.evaluate", { expression: expr, returnByValue: true });
      return r.result?.result?.value;
    };
    // 真实鼠标路径：CDP Input 走浏览器输入管线，pointerdown/move/up 由内核派生
    const drag = async (x1, y, x2) => {
      await send("Input.dispatchMouseEvent", { type: "mousePressed", x: x1, y, button: "left", buttons: 1, clickCount: 1 });
      const steps = 6;
      for (let i = 1; i <= steps; i++) {
        await send("Input.dispatchMouseEvent", { type: "mouseMoved", x: x1 + (x2 - x1) * i / steps, y, buttons: 1 });
        await sleep(30);
      }
      await send("Input.dispatchMouseEvent", { type: "mouseReleased", x: x2, y, button: "left", buttons: 0, clickCount: 1 });
      await sleep(200);
    };
    const geom = async () => JSON.parse(await evalJs(`(() => {
      const h = document.getElementById("side-resizer");
      const s = document.getElementById("sidebar");
      if (!h || !s) return "{}";
      const r = h.getBoundingClientRect(), b = s.getBoundingClientRect();
      return JSON.stringify({ sw: Math.round(b.width), hx: Math.round((r.left + r.right) / 2),
        hy: Math.round((r.top + r.bottom) / 2), rightGap: Math.round(b.right - r.right),
        hw: Math.round(r.width), cover: r.height >= b.height - 2 });
    })()`));

    await send("Page.enable");
    await send("Page.navigate", { url: SERVICE + "/" });
    await sleep(2500);
    check("主界面加载", await evalJs(`typeof S !== "undefined" && !!document.getElementById("side-resizer")`));
    // 全新 profile 首启会自动弹欢迎引导（help 弹层 .help-toc 盖住左栏，拦走真实鼠标
    // 命中——elementFromPoint 点到的是弹层目录项 .hitem）——先记账再重载让它永不出现
    await evalJs(`localStorage.setItem("orch.welcomed", "1"); location.reload(); "ok"`);
    await sleep(2500);

    // 1) 手柄几何：贴右缘（≤2px）、热区 7px、纵向覆盖整栏
    let g = await geom();
    check("手柄存在且贴右缘", g.rightGap >= 0 && g.rightGap <= 2 && g.hw === 7 && g.cover, JSON.stringify(g));
    check("默认宽 216", g.sw === 216, String(g.sw));

    // 2) 拖 +80 → 跟手变宽 + localStorage 落盘
    await drag(g.hx, g.hy, g.hx + 80);
    g = await geom();
    check("拖 +80 宽度跟手（≈296）", Math.abs(g.sw - 296) <= 2, JSON.stringify(g));
    const saved = await evalJs(`localStorage.getItem("orch.sideW")`);
    check("松手落 localStorage", saved && Math.abs(Number(saved) - 296) <= 2, String(saved));

    // 3) 刷新 → 记忆宽度在启动时回放
    await send("Page.navigate", { url: SERVICE + "/" });
    await sleep(2500);
    g = await geom();
    check("刷新后宽度记忆生效", Math.abs(g.sw - 296) <= 2, JSON.stringify(g));

    // 4) 左拖 -300 → 触底 clamp 190
    await drag(g.hx, g.hy, g.hx - 300);
    g = await geom();
    check("左拖触底 clamp 190", g.sw === 190, String(g.sw));

    // 5) 双击复原：宽度回 216 + 记忆键清除
    await evalJs(`document.getElementById("side-resizer").dispatchEvent(new MouseEvent("dblclick", {bubbles: true})); "ok"`);
    await sleep(200);
    g = await geom();
    const keyGone = await evalJs(`localStorage.getItem("orch.sideW") === null`);
    check("双击复原 216 且清键", g.sw === 216 && keyGone, JSON.stringify(g));

    // 6) 设置视图共用同一手柄：进设置页拖 +50 照样生效
    await evalJs(`document.getElementById("btn-settings").click(); "ok"`);
    await sleep(600);
    const inSettings = await evalJs(`document.body.classList.contains("settings-mode")`);
    check("进入设置视图", inSettings === true);
    g = await geom();
    check("设置视图手柄仍在右缘", g.rightGap >= 0 && g.rightGap <= 2, JSON.stringify(g));
    await drag(g.hx, g.hy, g.hx + 50);
    g = await geom();
    check("设置视图拖宽生效（≈266）", Math.abs(g.sw - 266) <= 2, JSON.stringify(g));
    await evalJs(`document.getElementById("side-resizer").dispatchEvent(new MouseEvent("dblclick", {bubbles: true})); "ok"`);

    // 7) 窄屏抽屉模式：手柄隐藏（宽度走固定抽屉，不参与拖拽）
    await send("Emulation.setDeviceMetricsOverride", { width: 800, height: 950, deviceScaleFactor: 1, mobile: false });
    await sleep(400);
    const hiddenNarrow = await evalJs(
      `getComputedStyle(document.getElementById("side-resizer")).display === "none"`);
    check("窄屏（<900）手柄隐藏", hiddenNarrow === true);
    await send("Emulation.clearDeviceMetricsOverride");
  } finally {
    try { ws && ws.close(); } catch (e) { /* ignore */ }
    try { edge && edge.kill(); } catch (e) { /* ignore */ }
    try { svc.kill(); } catch (e) { /* ignore */ }
    await sleep(800);
    for (const p of [edge, svc]) {
      if (p && p.pid) {
        try { spawn("taskkill", ["/F", "/T", "/PID", String(p.pid)], { stdio: "ignore" }); } catch (e) { /* ignore */ }
      }
    }
    // 残尸兜底：按端口找真 PID 补杀（Edge 子进程会脱离启动器进程树）
    const edgeFreed = await killPortHolders(CDP_PORT);
    const svcFreed = await killPortHolders(PORT);
    console.log("  · 端口归还自证：CDP %s / 服务 %s", edgeFreed ? "✓" : "✗ 未释放!", svcFreed ? "✓" : "✗ 未释放!");
    if (results.some((r) => !r.ok)) {
      console.log("（失败现场保留：%s）", tmp);
    } else {
      try { rmSync(tmp, { recursive: true, force: true }); } catch (e) { /* ignore */ }
    }
  }

  const bad = results.filter((r) => !r.ok);
  console.log("\n===== 侧栏拖宽 UI：%d 通过 / %d 失败 =====",
    results.length - bad.length, bad.length);
  if (bad.length) { console.log("失败项：", bad.map((b) => b.name)); process.exit(1); }
}

main().catch((e) => { console.error(e); process.exit(1); });
