/* 运行详情步骤行·窄主列折行回归（1010 实案：st-model/st-prov 徽章把定宽列总和顶到
 * ~783px，浏览器缩放/窄窗/检查器挤压主列时 #rd-pane-steps 长出横向滚动条，
 * CLI 日志按钮被右缘切半）。修法=#rd-steps 容器查询，≤820px 折行（与 ≤900 移动端同款）。
 * 断言：
 *  1) 宽主列(1440)容器查询不接管：.step 仍 nowrap、面板无横滚
 *  2) 窄主列(1100/900/720)容器查询接管：.step 折行、面板/主区/html 三层都无横向溢出
 *  3) 注入最坏情况行（运行中行带模型+上游徽章+日志按钮、超长摘要、run-sep 错误条）
 * 自含临时服务（随机端口），mock 任务零配额。用法：node tests/ui_step_row_narrow.mjs */
import { spawn } from "node:child_process";
import { mkdtempSync, rmSync, mkdirSync } from "node:fs";
import { tmpdir } from "node:os";
import { join, dirname } from "node:path";
import { fileURLToPath } from "node:url";
import { randomInt } from "node:crypto";

const PORT = randomInt(20000, 28000);
const SERVICE = "http://127.0.0.1:" + PORT;
const CDP_PORT = randomInt(30000, 38000);
const ROOT = join(dirname(fileURLToPath(import.meta.url)), "..");
const EDGE = "C:\\Program Files (x86)\\Microsoft\\Edge\\Application\\msedge.exe";
const sleep = (ms) => new Promise((r) => setTimeout(r, ms));
setTimeout(() => { console.error("WATCHDOG 300s 强制退出"); process.exit(2); }, 300000).unref();
process.on("uncaughtException", (e) => console.log("[uncaught]", String(e).slice(0, 100)));
const rfetch = async (url, n = 3) => {
  for (let i = 0; i < n; i++) {
    try { return await fetch(url); } catch (e) { if (i === n - 1) throw e; await sleep(400); }
  }
};

const PASS = [], FAIL = [];
function check(name, cond, detail = "") {
  (cond ? PASS : FAIL).push(name);
  console.log((cond ? "  ✓ " : "  ✗ ") + name + (cond ? "" : "　— " + String(detail).slice(0, 220)));
}

const INJECT = `(() => {
  const box = document.getElementById('rd-steps');
  if (!box) return 'no rd-steps';
  box.insertAdjacentHTML('beforeend',
    '<div class="run-sep"><span class="rs-i">第 2/2 次运行</span><span class="rs-t">10-10 17:06</span>' +
    '<span class="chip running">运行中</span>' +
    '<span class="rs-err">原定 Codex CLI 绑定链全部失效，已补位 Claude Code 继续执行本轮全部步骤不会中断</span></div>' +
    '<div class="step" data-n="2"><span class="n">02</span><span class="role">draft-c2</span>' +
    '<span class="who">Claude Code</span><span class="st-model">glm-5.3-flash</span>' +
    '<span class="st-prov">prov-36@open.bigmodel.cn,prov-3aaaaaaaaaaaaaaaaaaaa</span>' +
    '<span class="sum"></span><span class="dur"></span><span class="chip running">运行中</span>' +
    '<button class="step-log-btn" type="button">CLI 日志</button></div>' +
    '<div class="step" data-n="1"><span class="n">01</span><span class="role">global-critique</span>' +
    '<span class="who">OpenCode CLI</span><span class="st-model">glm-5.3-flash</span>' +
    '<span class="st-prov">prov-36@open.bigmodel.cn,prov-3aaaaaaaaaaaaaaaaaaaa</span>' +
    '<span class="sum">◆ 断点续跑 — 继承上一遍大纲（共 1 章）— 章纲已生成 1 章，评审通过后进入起草阶段等待中</span>' +
    '<span class="dur">1175.2s</span><span class="chip done">完成</span>' +
    '<button class="step-log-btn" type="button">CLI 日志</button></div>');
  return 'ok';
})()`;

const MEASURE = `(() => {
  const pane = document.querySelector('#rd-pane-steps') ||
    document.querySelector('#run-detail .rd-pane[data-pane="steps"]');
  const box = document.getElementById('rd-steps');
  const html = document.documentElement;
  const main = document.querySelector('#run-detail > .rd-main');
  const step = box ? box.querySelector('.step') : null;
  return JSON.stringify({
    paneOx: pane ? pane.scrollWidth - pane.clientWidth : -1,
    boxOx: box ? box.scrollWidth - box.clientWidth : -1,
    mainOx: main ? main.scrollWidth - main.clientWidth : -1,
    htmlOx: html.scrollWidth - html.clientWidth,
    wrap: step ? getComputedStyle(step).flexWrap : '?',
    steps: box ? box.querySelectorAll('.step').length : 0,
    cw: box ? box.clientWidth : 0,
  });
})()`;

async function main() {
  const tmp = mkdtempSync(join(tmpdir(), "tutti-stepnarrow-"));
  mkdirSync(join(tmp, "work"), { recursive: true });
  let edge = null, svc = null;
  try {
    svc = spawn("python", ["-X", "utf8", join(ROOT, "app", "main.py"), "--port", String(PORT),
      "--no-browser", "--host", "127.0.0.1"], {
      env: { ...process.env, TUTTI_DATA: join(tmp, "data"), PYTHONPATH: ROOT },
      cwd: ROOT, stdio: "ignore",
    });
    let up = false;
    for (let i = 0; i < 40 && !up; i++) {
      await sleep(500);
      try { up = (await rfetch(SERVICE + "/api/state")).ok; } catch (e) { /* wait */ }
    }
    check("临时服务启动（端口 " + PORT + "）", up);
    if (!up) throw new Error("service down");

    const state = await (await rfetch(SERVICE + "/api/state")).json();
    for (const a of state.agents.filter((a) => a.mode === "real")) {
      await fetch(SERVICE + "/api/orchestration", { method: "POST",
        body: JSON.stringify({ agent_id: a.id, enabled: false }) });
    }
    const sub = await (await fetch(SERVICE + "/api/tasks", { method: "POST",
      body: JSON.stringify({ type: "doc", goal: "步骤行窄主列折行回归：目标文本足够长以撑出摘要列省略号形态", workdir: join(tmp, "work"), mode: "auto" })
    })).json();
    let run = null;
    for (let i = 0; i < 120; i++) {
      await sleep(500);
      run = (await (await fetch(SERVICE + "/api/runs/" + sub.run_id)).json()).run;
      if (["done", "failed", "cancelled"].includes(run.status)) break;
    }
    check("mock 任务完成", run.status === "done" && (run.steps || []).length > 0,
      run.status + " steps=" + (run.steps || []).length);

    edge = spawn(EDGE, ["--headless=new", "--disable-gpu", "--no-first-run",
      `--user-data-dir=${join(tmp, "p")}`, `--remote-debugging-port=${CDP_PORT}`,
      "--window-size=1440,1000", "--force-device-scale-factor=1", "about:blank"], { stdio: "ignore" });
    let target = null;
    for (let i = 0; i < 30 && !target; i++) {
      await sleep(500);
      try {
        const list = await (await rfetch(`http://127.0.0.1:${CDP_PORT}/json/list`)).json();
        target = list.find((t) => t.type === "page");
      } catch (e) { /* wait */ }
    }
    const ws = new WebSocket(target.webSocketDebuggerUrl);
    await new Promise((res, rej) => { ws.onopen = res; ws.onerror = rej; });
    let seq = 0; const pending = new Map();
    ws.onmessage = (ev) => { const m = JSON.parse(ev.data); if (m.id && pending.has(m.id)) pending.get(m.id)(m); };
    const send = (method, params = {}) => new Promise((res) => {
      const id = ++seq; pending.set(id, res); ws.send(JSON.stringify({ id, method, params }));
    });
    const jsRaw = async (expr) => (await send("Runtime.evaluate",
      { expression: expr, returnByValue: true, awaitPromise: true })).result?.result?.value;
    const js = async (expr) => Promise.race([
      jsRaw(expr),
      new Promise((res) => setTimeout(() => res("__TIMEOUT__"), 20000)),
    ]);
    await send("Runtime.enable"); await send("Page.enable");
    await send("Page.navigate", { url: SERVICE + "/" });
    await sleep(4500);
    const envInfo = await js(`JSON.stringify({ dpr: devicePixelRatio, w: innerWidth, h: innerHeight })`);
    console.log("envInfo:", envInfo, "｜served app.js:", await js(
      `fetch("app.js").then(r=>r.text()).then(t=>t.includes("rdChatNavGo")?"新":"旧")`));

    await js(`sideOpenRun(${JSON.stringify(sub.run_id)}); "ok"`);
    await sleep(1200);
    // openRun 异步重渲染会把页签拽回默认：轮询强切到步骤直到面板真现身
    let ready = false;
    for (let i = 0; i < 20 && !ready; i++) {
      await js(`(typeof rdChatNavGo==="function"?rdChatNavGo("steps"):(S.rdTab="steps",applyRdTabs&&applyRdTabs())); "ok"`);
      await sleep(600);
      ready = await js(`(() => {
        const pane = document.querySelector('#rd-pane-steps');
        return !!pane && !pane.classList.contains('hidden') &&
          pane.getBoundingClientRect().width > 0 &&
          document.querySelectorAll('#rd-steps .step').length > 0;
      })()`);
    }
    check("步骤面板就绪", ready);
    check("注入最坏情况行", (await js(INJECT)) === "ok");

    // 期望按容器实际宽度断言（wrap ↔ 容器宽 <820px），不按视口——doc 任务与连载
    // chat-mode 任务的详情栏宽不同，同一视口下容器宽可差 200px+
    for (const W of [1650, 1440, 1100, 900, 720]) {
      await send("Emulation.setDeviceMetricsOverride", { width: W, height: 1000, deviceScaleFactor: 1, mobile: false });
      await js(`(typeof rdChatNavGo==="function"?rdChatNavGo("steps"):(S.rdTab="steps",applyRdTabs&&applyRdTabs())); "ok"`);
      await sleep(700);
      const m = JSON.parse(await js(MEASURE));
      const noOx = (v) => v >= -1 && v <= 1;
      const wantWrap = m.cw > 0 && m.cw < 820;
      check(`@${W}（容器宽 ${m.cw}px）三层无横向溢出`,
        noOx(m.paneOx) && noOx(m.boxOx) && noOx(m.mainOx) && noOx(m.htmlOx),
        JSON.stringify(m));
      check(`@${W} 容器查询${wantWrap ? "接管（wrap）" : "不接管（nowrap）"}`,
        m.wrap === (wantWrap ? "wrap" : "nowrap"), "flexWrap=" + m.wrap);
      if (W === 1650) check("@1650 容器宽确超阈值（nowrap 档有效）", m.cw >= 820, "cw=" + m.cw);
    }
  } finally {
    for (const p of [edge, svc]) {
      if (p && p.pid) { try { spawn("taskkill", ["/F", "/T", "/PID", String(p.pid)], { stdio: "ignore" }); } catch (e) {} }
    }
    await sleep(800);
    try { rmSync(tmp, { recursive: true, force: true }); } catch (e) {}
  }
  console.log(`\\n结果: ${PASS.length} 过 / ${FAIL.length} 挂`);
  if (FAIL.length) { console.log("挂项:", FAIL.join("；")); process.exit(1); }
}
main().catch((e) => { console.error("TEST FAIL:", e); process.exit(1); });
