/* 推送通知卡 + 流程分享码 无头验证：Edge headless + CDP。
 * 1) 编排设置子页「推送通知」卡渲染：7 输入 + 保存/测试按钮。
 * 2) saveNotifyPush 落盘：填 webhook → 保存 → /api/settings 回读一致。
 * 3) /api/notify/test 未配置通道 → 400 且页面提示错误（不碰外网）。
 * 4) 流程分享码：管理弹框有「导入分享码」；flowExport 弹出 textarea 且码以
 *    CBFLOW1. 开头；坏码导入 API 返回 400。
 * 5) i18n：lang=en 下推送卡标题变英文。
 * 用法：node tests/ui_notify_push.mjs （脚本自己起临时服务，端口 18819） */
import { spawn } from "node:child_process";
import { mkdtempSync, rmSync, mkdirSync } from "node:fs";
import { tmpdir } from "node:os";
import { join, dirname } from "node:path";
import { fileURLToPath } from "node:url";

const PORT = 18819;
const SERVICE = "http://127.0.0.1:" + PORT;
const CDP_PORT = 9359;
const EDGE = "C:\\Program Files (x86)\\Microsoft\\Edge\\Application\\msedge.exe";
const ROOT = join(dirname(fileURLToPath(import.meta.url)), "..");

const results = [];
const check = (name, cond, detail = "") => {
  results.push({ name, ok: !!cond });
  console.log((cond ? "  ✓ " : "  ✗ ") + name + (cond ? "" : "　— " + String(detail).slice(0, 260)));
};
const sleep = (ms) => new Promise((r) => setTimeout(r, ms));

async function main() {
  const tmp = mkdtempSync(join(tmpdir(), "tutti-push-"));
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

    // ── 进编排设置子页 ──
    await evalJs(`(function(){
      if (typeof switchTab==="function") switchTab("settings", "settings");
      const btn=document.querySelector('[data-sub="orch"]'); if(btn) btn.click();
      return true;
    })()`);
    await sleep(900);

    // ── 1. 推送通知卡渲染 ──
    check("推送通知卡标题存在", await evalJs(`!!document.querySelector('#sub-orch .sec-title[data-i18n="推送通知"]')`));
    check("7 个推送输入齐全", await evalJs(
      `["set-notify-webhook","set-notify-bark-server","set-notify-bark-key","set-notify-ntfy",
        "set-notify-serverchan","set-notify-tg-token","set-notify-tg-chat"]
        .every(id=>!!document.getElementById(id))`));
    check("保存/测试按钮在", await evalJs(
      `typeof saveNotifyPush==="function" && typeof testNotifyPush==="function"`));

    // ── 2. 保存落盘 + 回读 ──
    await evalJs(`(function(){
      document.getElementById("set-notify-webhook").value="https://oapi.dingtalk.com/robot?access_token=t";
      document.getElementById("set-notify-bark-key").value="bk-ui-test";
      return saveNotifyPush();
    })()`);
    await sleep(500);
    const back = await (await fetch(SERVICE + "/api/settings")).json();
    check("webhook 已落盘回读", back.notify_webhook === "https://oapi.dingtalk.com/robot?access_token=t", back.notify_webhook);
    check("bark key 已落盘回读", back.notify_bark_key === "bk-ui-test", back.notify_bark_key);
    check("保存后提示 ok", await evalJs(
      `document.getElementById("notify-test-msg").className.includes("ok")`));

    // ── 3. 测试推送：已配 webhook → 后端真发钉钉？不行，会出外网。改为先清空再测 400 分支 ──
    await evalJs(`(function(){
      ["set-notify-webhook","set-notify-bark-key"].forEach(id=>document.getElementById(id).value="");
      return saveNotifyPush();
    })()`);
    await sleep(400);
    await evalJs(`testNotifyPush()`);
    await sleep(600);
    check("未配置通道时测试给出错误提示", await evalJs(
      `document.getElementById("notify-test-msg").className.includes("err") &&
       document.getElementById("notify-test-msg").textContent.includes("未配置")`),
      await evalJs(`document.getElementById("notify-test-msg").textContent`));

    // ── 4. 流程分享码 ──
    await evalJs(`(function(){ if(typeof openFlowsManager==="function") openFlowsManager(); return true; })()`);
    await sleep(500);
    check("任务类型管理弹框有「导入分享码」", await evalJs(
      `!!document.querySelector('#modal-body') &&
       document.querySelector('#modal-body').innerHTML.includes("导入分享码")`));
    await evalJs(`closeModal()`);
    await evalJs(`(async function(){ await flowExport("code"); return true; })()`);
    await sleep(400);
    check("导出弹框出现分享码 textarea", await evalJs(
      `var ta=document.getElementById("fl-share-code"); !!ta && ta.value.startsWith("CBFLOW1.")`,
      await evalJs(`var ta=document.getElementById("fl-share-code"); ta?ta.value.slice(0,20):"(无弹框)"`)));
    await evalJs(`closeModal()`);
    // 写接口需设备控制权：必须走页面内 api()（带鉴权头），裸 fetch 会 423
    const bad = await evalJs(
      `(async function(){ try { await api("/api/flows/import", { method: "POST",
          body: JSON.stringify({ code: "not-a-code" }) }); return "no-error"; }
        catch (e) { return String(e.message || e); } })()`);
    check("坏分享码被拒绝并报人话错误", typeof bad === "string" && bad.includes("分享码"), bad);

    // ── 5. i18n EN ──
    await evalJs(`(function(){ try{ localStorage.setItem("orch.lang","en"); }catch(e){}
      if(typeof applyI18n==="function") applyI18n(); return true; })()`);
    await sleep(400);
    check("EN 下推送卡标题翻译", await evalJs(
      `var el=document.querySelector('#sub-orch .sec-title[data-i18n="推送通知"]');
       el && el.textContent.trim()==="Push Notifications"`));

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
