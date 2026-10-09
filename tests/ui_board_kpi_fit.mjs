/* 大屏指标条长数值自适应：usage 值大时「2246.7万」这类长值曾把「万」挤到第二行
 * 并被 .kpi 的 overflow hidden 裁掉。守卫：render 后 k-row 不外溢、k-num 单行、
 * 整个数字都在卡片框内；窄窗降到末档也不许纵向裁字。
 * 端口 18924 / CDP 9365（防并行撞车，可用 TUTTI_TEST_PORT / TUTTI_TEST_CDP 覆盖）。 */
import { spawn } from "node:child_process";
import { writeFileSync, mkdirSync, mkdtempSync, rmSync } from "node:fs";
import { tmpdir } from "node:os";
import { join, dirname } from "node:path";
import { fileURLToPath } from "node:url";

const PORT = Number(process.env.TUTTI_TEST_PORT) || 18924;
const SERVICE = "http://127.0.0.1:" + PORT;
const CDP_PORT = Number(process.env.TUTTI_TEST_CDP || 9365);
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

/* 种上实拍同款数据并 render，回读几何：行是否外溢 / 数字几行 / 是否出卡 */
const PROBE = `(() => {
  if (typeof LAST === "undefined" || !LAST) return JSON.stringify({ ready: false });
  LAST.usage_today = { tokens: 22467000, cost_usd: 28.64, calls: 41 };
  render(LAST);
  const kpi = document.querySelector(".k-use");
  const row = kpi.querySelector(".k-row");
  const num = document.getElementById("k-tokens");
  const kr = kpi.getBoundingClientRect(), nr = num.getBoundingClientRect();
  return JSON.stringify({
    ready: true,
    text: num.textContent,
    cls: num.className,
    rowFits: row.scrollWidth <= row.clientWidth + 1,
    lineH: Math.round(nr.height),
    insideV: nr.bottom <= kr.bottom - 1,
    insideH: nr.right <= kr.right - 1,
  });
})()`;

async function main() {
  const tmp = mkdtempSync(join(tmpdir(), "tutti-uiboardfit-"));
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
    const waitFor = async (expr, ms = 15000) => {
      for (let t = 0; t < ms; t += 400) {
        if (await evalJs(expr)) return true;
        await sleep(400);
      }
      return false;
    };

    await send("Page.enable");
    await send("Page.navigate", { url: SERVICE + "/" });
    await sleep(2500);
    await evalJs(`localStorage.setItem("orch.token", "t-xxxx"); "ok"`);
    await send("Page.navigate", { url: SERVICE + "/board.html" });
    await sleep(1500);
    check("board 页加载且首轮 poll 完成",
      await waitFor(`typeof LAST !== "undefined" && !!LAST && !!document.getElementById("k-tokens")`));

    // 1) 实拍同款宽窗（1400）：2246.7万 + $28.64 · 41 次调用
    const wide = JSON.parse(await evalJs(PROBE));
    check("探针拿到数据并完成 render", wide.ready === true, JSON.stringify(wide));
    check("数值仍为 2246.7万（不是截断/换行后的残串）", wide.text === "2246.7万", String(wide.text));
    check("长数值触发降档（sm/xs）", / sm| xs/.test(" " + wide.cls), String(wide.cls));
    check("k-row 不横向外溢", wide.rowFits === true, JSON.stringify(wide));
    check("k-num 保持单行（行高≤30px）", wide.lineH <= 30, String(wide.lineH));
    check("数字整体在卡片框内（不纵向裁字）", wide.insideV === true, JSON.stringify(wide));
    check("数字右缘不出卡", wide.insideH === true, JSON.stringify(wide));

    // 2) 窄窗（1024）：降到末档也不许回到「换行被裁」的老病
    await send("Emulation.setDeviceMetricsOverride",
      { width: 1024, height: 800, deviceScaleFactor: 0, mobile: false });
    await sleep(400);
    const narrow = JSON.parse(await evalJs(PROBE));
    check("窄窗下同样不纵向裁字", narrow.ready === true && narrow.insideV === true, JSON.stringify(narrow));
    check("窄窗下数字仍单行", narrow.lineH <= 30, String(narrow.lineH));
    await send("Emulation.clearDeviceMetricsOverride");
  } finally {
    try { ws && ws.close(); } catch (e) { /* ignore */ }
    try { edge && edge.kill(); } catch (e) { /* ignore */ }
    try { svc && svc.kill(); } catch (e) { /* ignore */ }
    await sleep(800);
    for (const p of [edge, svc]) {
      if (p && p.pid) {
        try { spawn("taskkill", ["/F", "/T", "/PID", String(p.pid)], { stdio: "ignore" }); } catch (e) { /* ignore */ }
      }
    }
    if (results.some((r) => !r.ok)) {
      console.log("（失败现场保留：%s）", tmp);
    } else {
      try { rmSync(tmp, { recursive: true, force: true }); } catch (e) { /* ignore */ }
    }
  }

  const bad = results.filter((r) => !r.ok);
  console.log("\n===== 大屏 KPI 长数值自适应：%d 通过 / %d 失败 =====",
    results.length - bad.length, bad.length);
  if (bad.length) { console.log("失败项：", bad.map((b) => b.name)); process.exit(1); }
}

main().catch((e) => { console.error(e); process.exit(1); });
