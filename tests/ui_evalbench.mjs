/* 模型评测（评测基准台）无头验证：Edge headless + CDP + 页内 stub fetch。
 * 1) 空态：子页渲染、3 道内置样题、裁判未就绪提示、无候选提示。
 * 2) stub /api/evalbench + /api/models：候选复选框渲染、榜单排序与徽章。
 * 3) 勾选→开始评测：POST /api/evalbench/run 被 stub 接住（不真发后端）。
 * 4) EN i18n：模型评测 → Model Bench。
 * 用法：node tests/ui_evalbench.mjs （脚本自己起临时服务，端口 18821） */
import { spawn } from "node:child_process";
import { mkdtempSync, rmSync, mkdirSync } from "node:fs";
import { tmpdir } from "node:os";
import { join, dirname } from "node:path";
import { fileURLToPath } from "node:url";

const PORT = 18821;
const SERVICE = "http://127.0.0.1:" + PORT;
const CDP_PORT = 9361;
const EDGE = "C:\\Program Files (x86)\\Microsoft\\Edge\\Application\\msedge.exe";
const ROOT = join(dirname(fileURLToPath(import.meta.url)), "..");

const results = [];
const check = (name, cond, detail = "") => {
  results.push({ name, ok: !!cond });
  console.log((cond ? "  ✓ " : "  ✗ ") + name + (cond ? "" : "　— " + String(detail).slice(0, 260)));
};
const sleep = (ms) => new Promise((r) => setTimeout(r, ms));

const FAKE_EB = {
  running: false, progress: null,
  judge: { provider_id: "prov-j", model: "judge-x", ready: true },
  samples: [
    { id: "writing", name: "章节写作", requirement: "写 300 字开篇", dims: ["情节", "人物"], verify: false },
    { id: "bugfix", name: "小 bug 修复", requirement: "修复滑动窗口", dims: ["正确性"], verify: true },
  ],
  leaderboard: [
    { rank: 1, provider_id: "prov-a", provider_name: "蜂巢网关", model: "model-a",
      overall: 8.1, samples_n: 2, scored_n: 2, failed_n: 0,
      scores: { 情节: 8.2, 人物: 8.0 }, verify_pass: 1, verify_total: 1,
      same_family: false, last_ts: "2026-09-25 21:00:00" },
    { rank: 2, provider_id: "prov-j", provider_name: "蜂巢网关", model: "model-b",
      overall: 7.4, samples_n: 2, scored_n: 2, failed_n: 1,
      scores: { 情节: 7.4 }, verify_pass: 0, verify_total: 1,
      same_family: true, last_ts: "2026-09-25 21:00:00" },
  ],
  last_runs: [],
};
const FAKE_MODELS = {
  providers: [
    { id: "prov-a", name: "蜂巢网关", enabled: true,
      models: [{ name: "model-a", enabled: true }, { name: "model-old", enabled: false },
               { name: "model-c", enabled: true, hidden: true }] },
    { id: "prov-b", name: "停用商", enabled: false,
      models: [{ name: "model-z", enabled: true }] },
  ],
};

async function main() {
  const tmp = mkdtempSync(join(tmpdir(), "tutti-ebench-"));
  const dataDir = join(tmp, "data");
  mkdirSync(dataDir, { recursive: true });
  let svc = null, edge = null, ws = null;
  try {
    svc = spawn("python", ["-X", "utf8", join(ROOT, "app", "main.py"), "--port", String(PORT),
      "--no-browser", "--host", "127.0.0.1"], {
      env: { ...process.env, TUTTI_DATA: dataDir, PYTHONPATH: ROOT },
      cwd: ROOT, stdio: "ignore",
    });
    let up = false;
    for (let i = 0; i < 40 && !up; i++) {
      await sleep(500);
      try { up = (await fetch(SERVICE + "/api/state")).ok; } catch (e) { /* wait */ }
    }
    check("临时服务启动（端口 " + PORT + "）", up);

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
    check("Edge headless 就绪", !!target);
    ws = new WebSocket(target.webSocketDebuggerUrl);
    await new Promise((res, rej) => { ws.onopen = res; ws.onerror = rej; });
    let seq = 0; const pending = new Map();
    ws.onmessage = (ev) => { const m = JSON.parse(ev.data); if (m.id && pending.has(m.id)) pending.get(m.id)(m); };
    const send = (method, params = {}) => new Promise((res) => { const id = ++seq; pending.set(id, res); ws.send(JSON.stringify({ id, method, params })); });
    const evalJs = async (expr) => {
      const r = await send("Runtime.evaluate", { expression: expr, returnByValue: true, awaitPromise: true });
      if (r.result && r.result.exceptionDetails) throw new Error("页面报错: " + JSON.stringify(r.result.exceptionDetails).slice(0, 300));
      return r.result && r.result.result ? r.result.result.value : undefined;
    };

    await send("Page.enable");
    await send("Runtime.enable");
    await send("Page.navigate", { url: SERVICE });
    await sleep(2500);
    await send("Page.addScriptToEvaluateOnNewDocument", {
      source: `window.confirm=()=>true; window.alert=()=>{}; window.prompt=()=>'';
        setInterval(()=>{ try{ localStorage.setItem('orch.deviceCtl','1'); }catch(e){} }, 1000);`,
    });
    await sleep(600);

    // ── 1. 空态（真服务：无供应商、无编排者、无结果）──
    await evalJs(`(function(){ if(typeof switchTab==="function") switchTab("settings","settings");
      const b=document.querySelector('[data-sub="evalbench"]'); if(b) b.click(); return true; })()`);
    await sleep(1200);
    check("评测子页出现", await evalJs(`!document.getElementById("sub-evalbench").classList.contains("hidden")`));
    check("3 道内置样题渲染（修 bug 题带客观验证徽章）", await evalJs(
      `document.querySelectorAll("#eb-samples .item").length===3 &&
       document.querySelector("#eb-samples .tag.ok")!==null`));
    check("裁判未就绪有提示", await evalJs(
      `document.getElementById("eb-judge").textContent.includes("未就绪")`));
    check("无候选给出引导文案", await evalJs(
      `document.getElementById("eb-cands").textContent.includes("模型接入")`));

    // ── 2. stub fetch：候选 + 榜单渲染 ──
    await evalJs(`(function(){
      const realFetch = window.fetch;
      window.fetch = function(url, opts){
        const u = String(url);
        if (u.indexOf("/api/evalbench") >= 0) {
          if (opts && opts.method === "POST") {
            window.__ebPosted = window.__ebPosted || [];
            try { window.__ebPosted.push({ url: u, body: JSON.parse(opts.body) }); } catch(e) {}
            return Promise.resolve(new Response(JSON.stringify({ok:true,run_id:"bench-stub",total:1}),
              { status: 200, headers: {"Content-Type":"application/json"} }));
          }
          return Promise.resolve(new Response(JSON.stringify(${JSON.stringify(FAKE_EB)}),
            { status: 200, headers: {"Content-Type":"application/json"} }));
        }
        if (u.indexOf("/api/models") >= 0) {
          return Promise.resolve(new Response(JSON.stringify(${JSON.stringify(FAKE_MODELS)}),
            { status: 200, headers: {"Content-Type":"application/json"} }));
        }
        return realFetch.apply(window, arguments);
      };
      return true; })()`);
    await evalJs(`(function(){ document.getElementById("eb-samples").dataset.done="";
      S.models = null;                      // 清掉启动期真实拉到的空目录，让 stub 生效
      if (typeof loadEvalBench==="function") return loadEvalBench(); return true; })()`);
    await sleep(900);
    check("候选复选框只列启用模型（停用商/停用模型/隐藏模型剔除）", await evalJs(
      `document.querySelectorAll(".eb-cand").length===1 &&
       document.querySelector(".eb-cand").dataset.model==="model-a"`));
    check("榜单 2 行且按分排序", await evalJs(
      `document.querySelectorAll("#eb-board .item").length===2 &&
       document.querySelector("#eb-board .item .name").textContent.includes("#1") &&
       document.querySelector("#eb-board .item .name").textContent.includes("model-a")`));
    check("同族评审与失败徽章如实标注", await evalJs(
      `document.querySelector("#eb-board").innerHTML.includes("同族评审") &&
       document.querySelector("#eb-board").innerHTML.includes("失败 1")`));
    check("裁判就绪显示裁判模型", await evalJs(
      `document.getElementById("eb-judge").textContent.indexOf("judge-x")>=0`));

    // ── 3. 勾选 → 开始评测（stub 接住 POST）──
    // 注意：renderEvalBench 会把榜单上已有的模型预勾选（产品行为），
    // 「未勾选」用例必须先全部取消勾选。
    check("未勾选时开始评测给提示", await evalJs(`(async function(){
      document.querySelectorAll(".eb-cand").forEach(function(c){ c.checked=false; });
      if(typeof evalbenchRun!=="function") return false;
      await evalbenchRun();
      var m=document.getElementById("eb-msg");
      return m.className.indexOf("err")>=0 && m.textContent.indexOf("勾选")>=0; })()`));
    await evalJs(`(async function(){
      document.querySelector(".eb-cand").checked=true;
      await evalbenchRun(); return true; })()`);
    await sleep(400);
    const posted = await evalJs(`(window.__ebPosted||[]).map(function(p){
      return {url:p.url, n:(p.body.candidates||[]).length}; })`);
    check("开始评测的 POST 带 1 个候选", posted.length === 1 && posted[0].n === 1,
      JSON.stringify(posted));

    // ── 4. EN i18n ──
    await evalJs(`(function(){ try{ localStorage.setItem("orch.lang","en"); }catch(e){}
      if(typeof applyI18n==="function") applyI18n(); return true; })()`);
    await sleep(400);
    check("EN 下导航项翻译", await evalJs(
      `var b=document.querySelector('[data-sub="evalbench"]'); b && b.textContent.trim()==="Model Bench"`));

    const fails = results.filter((r) => !r.ok).length;
    console.log(fails === 0 ? "\n全部通过 (" + results.length + ")" : "\n失败 " + fails + "/" + results.length);
    process.exitCode = fails === 0 ? 0 : 1;
  } catch (e) {
    console.error("探针异常:", e.message);
    process.exitCode = 1;
  } finally {
    try { if (ws) ws.close(); } catch (e) {}
    try { if (edge) edge.kill(); } catch (e) {}
    try { if (svc) svc.kill(); } catch (e) {}
    await sleep(800);
    try { rmSync(tmp, { recursive: true, force: true }); } catch (e) {}
  }
}

main();
