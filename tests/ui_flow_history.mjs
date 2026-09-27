/* 流程版本历史 无头验证：管理弹框「历史」按钮 → 版本清单（当前/来源/战绩）→ 恢复确认。
 * stub /api/flows/versions 与 /api/flows/restore，不真写数据。
 * 用法：node tests/ui_flow_history.mjs （自起临时服务，端口 18823） */
import { spawn } from "node:child_process";
import { mkdtempSync, rmSync, mkdirSync } from "node:fs";
import { tmpdir } from "node:os";
import { join, dirname } from "node:path";
import { fileURLToPath } from "node:url";

const PORT = parseInt(process.env.TUTTI_TEST_PORT || "18823", 10);
const CDP_PORT = parseInt(process.env.TUTTI_TEST_CDP || "9363", 10);
const SERVICE = "http://127.0.0.1:" + PORT;
const EDGE = "C:\\Program Files (x86)\\Microsoft\\Edge\\Application\\msedge.exe";
const ROOT = join(dirname(fileURLToPath(import.meta.url)), "..");

const results = [];
const check = (name, cond, detail = "") => {
  results.push({ name, ok: !!cond });
  console.log((cond ? "  ✓ " : "  ✗ ") + name + (cond ? "" : "　— " + String(detail).slice(0, 240)));
};
const sleep = (ms) => new Promise((r) => setTimeout(r, ms));

const FAKE_VERSIONS = {
  versions: [
    { current: true, ts: "", source: "current", name: "播客脚本",
      digest: "aaaaaaaaaaaaaaaa", stats: { runs: 4, pass_rate: 0.5, avg_overall: 7.3 } },
    { current: false, ts: "2026-09-26 10:00:00", source: "manual", name: "播客脚本",
      digest: "bbbbbbbbbbbbbbbb", stats: { runs: 2, pass_rate: 1, avg_overall: 8.0 } },
    { current: false, ts: "2026-09-25 09:00:00", source: "import", name: "播客脚本",
      digest: "cccccccccccccccc", stats: null },
  ],
};

async function main() {
  const tmp = mkdtempSync(join(tmpdir(), "tutti-fhist-"));
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

    // stub：versions/restore 走假数据；POST restore 记录调用
    await evalJs(`(function(){
      const realFetch = window.fetch;
      window.fetch = function(url, opts){
        const u = String(url);
        if (u.indexOf("/api/flows/versions") >= 0)
          return Promise.resolve(new Response(JSON.stringify(${JSON.stringify(FAKE_VERSIONS)}),
            { status: 200, headers: {"Content-Type":"application/json"} }));
        if (u.indexOf("/api/flows/restore") >= 0) {
          window.__restored = (window.__restored||0)+1;
          return Promise.resolve(new Response(JSON.stringify({ok:true}),
            { status: 200, headers: {"Content-Type":"application/json"} }));
        }
        return realFetch.apply(window, arguments);
      };
      return true; })()`);

    await evalJs(`(function(){ if (typeof openFlowsManager==="function") openFlowsManager(); return true; })()`);
    await sleep(500);
    check("管理弹框每行有「历史」按钮", await evalJs(
      `document.querySelector("#modal-body").innerHTML.includes("flowHistory")`));
    await evalJs(`(async function(){ await flowHistory("code"); return true; })()`);
    await sleep(500);
    check("历史弹框列出 3 版", await evalJs(
      `document.querySelectorAll("#modal-body .item").length===3`));
    check("当前版/手动/导入来源标签齐全", await evalJs(`(function(){
      var h=document.querySelector("#modal-body").innerHTML;
      return h.indexOf("当前")>=0 && h.indexOf("手动修改")>=0 && h.indexOf("分享码导入")>=0; })()`));
    check("战绩 tag 渲染（任务数/通过率/均分）", await evalJs(`(function(){
      var h=document.querySelector("#modal-body").innerHTML;
      return h.indexOf("任务 4")>=0 && h.indexOf("通过 50%")>=0 && h.indexOf("均分 7.3")>=0; })()`));
    check("无战绩版本不显示战绩", await evalJs(`(function(){
      var items=document.querySelectorAll("#modal-body .item");
      return items[2].innerHTML.indexOf("均分")<0; })()`));
    // uiConfirm 是自绘确认弹框（非 window.confirm），headless 下 Promise 永远等人——探针直接替身
    await evalJs(`(async function(){ window.uiConfirm = async () => true;
      await flowRestore("code", "2026-09-25 09:00:00"); return true; })()`);
    await sleep(500);
    check("恢复请求已发出", await evalJs(`window.__restored===1`));

    const fails = results.filter((r) => !r.ok).length;
    console.log(fails === 0 ? "\n全部通过 (" + results.length + ")" : "\n失败 " + fails + "/" + results.length);
    process.exitCode = fails === 0 ? 0 : 1;
  } catch (e) {
    console.error("探针异常:", e.message);
    process.exitCode = 1;
  } finally {
    // child.kill() 杀不死服务/浏览器的子进程树（2026-09-18 实锤）：一律 taskkill /T 杀树
    const treeKill = (p) => {
      if (!p || !p.pid) return;
      try { spawn("taskkill", ["/F", "/T", "/PID", String(p.pid)], { stdio: "ignore" }); } catch (e) {}
    };
    try { if (ws) ws.close(); } catch (e) {}
    treeKill(edge);
    treeKill(svc);
    await sleep(800);
    try { rmSync(tmp, { recursive: true, force: true }); } catch (e) {}
  }
}

main();
