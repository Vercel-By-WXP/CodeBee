/* 诊断探针 v2：侧栏点不动/右侧冻结案（坐进运行详情页复现）。
 * 阶段1 启动后挂 render 计时包装 → 阶段2 点进运行中任务详情坐 60s 量
 * render 次数/单次耗时/长任务/点击延迟/网络请求数 → 阶段3 切概览再静置 20s
 * 看有没有泄漏轮询（请求数不回落=泄漏）。只读操作，不写。 */
import { spawn } from "node:child_process";
import { mkdtempSync, rmSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";

const SERVICE = process.env.TUTTI_PROBE_SERVICE || "http://127.0.0.1:8765";
const CDP_PORT = Number(process.env.TUTTI_TEST_CDP) || 9377;
const EDGE = "C:\\Program Files (x86)\\Microsoft\\Edge\\Application\\msedge.exe";

const sleep = (ms) => new Promise((r) => setTimeout(r, ms));

async function main() {
  const profile = mkdtempSync(join(tmpdir(), "tutti-freeze-"));
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
        const list = await (await fetch(`http://127.0.0.1:${CDP_PORT}/json/list`)).json();
        target = list.find((t) => t.type === "page");
      } catch (e) { /* Edge 未就绪 */ }
    }
    if (!target) { console.log("FATAL: Edge 未启动"); return; }
    const ws = new WebSocket(target.webSocketDebuggerUrl);
    await new Promise((res, rej) => { ws.onopen = res; ws.onerror = rej; });
    let seq = 0; const pending = new Map();
    ws.onmessage = (ev) => {
      const m = JSON.parse(ev.data);
      if (m.id && pending.has(m.id)) pending.get(m.id)(m);
    };
    const send = (method, params = {}) => new Promise((res) => {
      const id = ++seq; pending.set(id, res);
      ws.send(JSON.stringify({ id, method, params }));
    });
    const evalJs = async (expr) =>
      (await send("Runtime.evaluate", { expression: expr, returnByValue: true, awaitPromise: true })).result?.result?.value;

    await send("Runtime.enable");
    await send("Page.enable");
    await send("Page.addScriptToEvaluateOnNewDocument", { source: `
      window.__LT = []; window.__JSERR = []; window.__FETCH = 0;
      try {
        new PerformanceObserver((l) => {
          for (const e of l.getEntries()) window.__LT.push({ t: e.startTime, d: e.duration });
        }).observe({ entryTypes: ["longtask"] });
      } catch (e) {}
      window.addEventListener("error", (e) => window.__JSERR.push(String(e.message).slice(0, 200)));
      const of = window.fetch;
      window.fetch = function (...a) { window.__FETCH++; return of.apply(this, a); };
    ` });
    await send("Page.navigate", { url: SERVICE + "/" });
    await sleep(4500);

    // 启动后挂 render 包装（顶层函数声明已就位，此时替换才会拦到内部调用）
    const hooked = await evalJs(`(() => {
      if (typeof window.render !== "function") return false;
      const o = window.render;
      window.__R = [];
      window.render = function (...a) {
        const t0 = performance.now();
        try { return o.apply(this, a); }
        finally { window.__R.push(Math.round(performance.now() - t0)); }
      };
      return true;
    })()`);
    console.log("render 包装挂载:", hooked);

    const ltSum = `(() => { const lt = window.__LT || [];
      return { ltN: lt.length, ltMs: Math.round(lt.reduce((s, e) => s + e.d, 0)),
               ltMax: Math.round(Math.max(0, ...lt.map((e) => e.d))),
               ltOver1s: lt.filter((e) => e.d > 1000).length }; })()`;

    // 阶段2：点进运行中任务的详情（走真实激活路径）
    const opened = await evalJs(`(async () => {
      const row = document.querySelector('.stask[data-status="running"]') ||
                  document.querySelector(".stask");
      if (!row) return "no-row";
      const t0 = performance.now();
      row.click();
      await new Promise((r) => setTimeout(r, 1500));
      return { row: row.dataset.key, ms: Math.round(performance.now() - t0),
               title: (document.getElementById("rd-title") || {}).textContent };
    })()`);
    console.log("== 打开运行中任务详情 ==", JSON.stringify(opened));

    const mark0 = await evalJs("window.__FETCH");
    await sleep(60000);   // 坐 60s 收 SSE 驱动的渲染
    const phase2 = await evalJs(`(() => ({
      renders: (window.__R || []).length,
      renderMs: (window.__R || []),
      fetchIn60s: window.__FETCH - ${mark0},
      jserr: window.__JSERR.slice(0, 5),
      heapMB: Math.round(performance.memory.usedJSHeapSize / 1048576),
    }))()`);
    const lt2 = await evalJs(ltSum);
    console.log("== 详情页静坐 60s ==", JSON.stringify(phase2));
    console.log("== 长任务累计 ==", JSON.stringify(lt2));

    // 详情页上连点 6 次侧栏快捷导航量延迟
    const clickMs = [];
    for (const id of ["btn-q-overview", "btn-q-runs", "btn-q-automation",
                      "btn-q-overview", "btn-q-runs", "btn-q-automation"]) {
      const r = await evalJs(`(async () => {
        const el = document.getElementById("${id}");
        if (!el) return -1;
        const t0 = performance.now();
        el.click();
        await new Promise((res) => requestAnimationFrame(() => requestAnimationFrame(res)));
        return Math.round(performance.now() - t0);
      })()`);
      clickMs.push(r);
      await sleep(250);
    }
    console.log("== 详情页在开时侧栏连点延迟 ms ==", JSON.stringify(clickMs));

    // 阶段3：切到概览静置 20s——泄漏的轮询会让请求数维持高位
    await evalJs(`(async () => { document.getElementById("btn-q-overview").click(); return 1; })()`);
    const mark1 = await evalJs("window.__FETCH");
    await sleep(20000);
    const phase3 = await evalJs(`(() => ({
      fetchIn20sIdle: window.__FETCH - ${mark1},
      rendersTotal: (window.__R || []).length,
    }))()`);
    const lt3 = await evalJs(ltSum);
    console.log("== 回概览静置 20s ==", JSON.stringify(phase3));
    console.log("== 长任务最终 ==", JSON.stringify(lt3));
  } finally {
    try { proc.kill(); } catch (e) {}
    await sleep(800);
    try { proc.kill("SIGKILL"); } catch (e) {}
    try { rmSync(profile, { recursive: true, force: true }); } catch (e) {}
  }
}
main().then(() => process.exit(0)).catch((e) => { console.error("FATAL", e); process.exit(1); });
