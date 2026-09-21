/* 请求反馈回归：用户操作立即反馈、防重复点击，后台请求静默，结束后恢复。 */
import { spawn } from "node:child_process";
import { mkdtempSync, rmSync } from "node:fs";
import { tmpdir } from "node:os";
import { join, dirname } from "node:path";
import { fileURLToPath } from "node:url";

const ROOT = join(dirname(fileURLToPath(import.meta.url)), "..");
const PORT = 18823;
const CDP_PORT = 9397;
const SERVICE = `http://127.0.0.1:${PORT}`;
const EDGE = [
  "C:\\Program Files (x86)\\Microsoft\\Edge\\Application\\msedge.exe",
  "C:\\Program Files\\Microsoft\\Edge\\Application\\msedge.exe",
].find((p) => { try { return !!p; } catch (_) { return false; } });
const sleep = (ms) => new Promise((resolve) => setTimeout(resolve, ms));
const assert = (ok, msg) => { if (!ok) throw new Error(msg); };

async function main() {
  const tmp = mkdtempSync(join(tmpdir(), "codebee-request-feedback-"));
  const svc = spawn("python", ["-X", "utf8", join(ROOT, "app", "main.py"),
    "--port", String(PORT), "--host", "127.0.0.1", "--no-browser"],
  { cwd: ROOT, stdio: "ignore", env: { ...process.env, TUTTI_DATA: join(tmp, "data"), PYTHONPATH: ROOT } });
  let edge = null;
  let ws = null;
  try {
    for (let i = 0; i < 40; i++) {
      try { if ((await fetch(SERVICE + "/api/state")).ok) break; } catch (_) { /* wait */ }
      await sleep(250);
    }
    edge = spawn(EDGE, ["--headless=new", "--disable-gpu", "--no-first-run",
      `--user-data-dir=${join(tmp, "edge")}`, `--remote-debugging-port=${CDP_PORT}`, "about:blank"],
    { stdio: "ignore" });
    let target;
    for (let i = 0; i < 40 && !target; i++) {
      await sleep(250);
      try {
        const tabs = await (await fetch(`http://127.0.0.1:${CDP_PORT}/json/list`)).json();
        target = tabs.find((tab) => tab.type === "page");
      } catch (_) { /* wait */ }
    }
    assert(target, "Edge CDP 未启动");
    ws = new WebSocket(target.webSocketDebuggerUrl);
    await new Promise((resolve, reject) => { ws.onopen = resolve; ws.onerror = reject; });
    let seq = 0;
    const pending = new Map();
    ws.onmessage = (event) => {
      const msg = JSON.parse(event.data);
      if (msg.id && pending.has(msg.id)) { pending.get(msg.id)(msg); pending.delete(msg.id); }
    };
    const send = (method, params = {}) => new Promise((resolve) => {
      const id = ++seq; pending.set(id, resolve); ws.send(JSON.stringify({ id, method, params }));
    });
    const js = async (expression) => {
      const out = await send("Runtime.evaluate", { expression, awaitPromise: true, returnByValue: true });
      if (out.result?.exceptionDetails) throw new Error(out.result.exceptionDetails.text);
      return out.result?.result?.value;
    };
    await send("Page.enable");
    await send("Page.navigate", { url: SERVICE + "/" });
    await sleep(2500);

    await js(`(() => {
      window.__calls = 0;
      window.__resolveRequest = null;
      window.fetch = () => { window.__calls++; return new Promise((resolve) => {
        window.__resolveRequest = () => resolve({ status: 200, ok: true, json: async () => ({ ok: true }) });
      }); };
      const btn = document.createElement("button"); btn.id = "feedback-test"; btn.textContent = "执行";
      btn.addEventListener("click", () => api("/test", { method: "POST", body: "{}" }));
      document.body.appendChild(btn); btn.click(); return true;
    })()`);
    const busy = await js(`({
      button: document.getElementById("feedback-test").classList.contains("request-busy"),
      aria: document.getElementById("feedback-test").getAttribute("aria-busy"),
      progress: document.getElementById("request-progress").classList.contains("active"), calls: window.__calls,
      operationVisible: !document.getElementById("btn-ops").classList.contains("hidden"),
      operationRunning: document.querySelector("#op-list .op-row.running") !== null
    })`);
    assert(busy.button && busy.aria === "true" && busy.progress, "点击后没有立即显示请求反馈");
    assert(busy.operationVisible && busy.operationRunning, "用户写操作没有进入操作状态中心");
    await js(`document.getElementById("feedback-test").click(); true`);
    assert(await js(`window.__calls`) === 1, "忙碌期间重复点击仍发送了请求");
    await js(`window.__resolveRequest(); new Promise((resolve) => setTimeout(resolve, 30))`);
    const restored = await js(`({
      button: document.getElementById("feedback-test").classList.contains("request-busy"),
      aria: document.getElementById("feedback-test").hasAttribute("aria-busy"),
      progress: document.getElementById("request-progress").classList.contains("active"),
      operationDone: document.querySelector("#op-list .op-row.done") !== null
    })`);
    assert(!restored.button && !restored.aria && !restored.progress, "请求完成后忙碌态没有恢复");
    assert(restored.operationDone, "请求完成后操作状态没有变为已完成");

    await js(`(() => {
      window.fetch = () => new Promise((resolve) => {
        window.__resolveGet = () => resolve({ status: 200, ok: true, json: async () => ({ ok: true }) });
      });
      const btn = document.getElementById("feedback-test");
      btn.replaceWith(btn.cloneNode(true));
      const next = document.getElementById("feedback-test");
      next.addEventListener("click", () => api("/test-get", { busy: true })); next.click(); return true;
    })()`);
    assert(await js(`document.getElementById("feedback-test").classList.contains("request-busy")`),
      "按钮触发的只读请求没有立即反馈");
    await js(`window.__resolveGet(); new Promise((resolve) => setTimeout(resolve, 30))`);

    await js(`(() => {
      window.fetch = () => new Promise((_, reject) => {
        window.__rejectRequest = () => reject(new Error("network down"));
      });
      const btn = document.getElementById("feedback-test");
      btn.replaceWith(btn.cloneNode(true));
      const next = document.getElementById("feedback-test");
      next.addEventListener("click", () => api("/test-error", { method: "POST" }).catch(() => {}));
      next.click(); return true;
    })()`);
    assert(await js(`document.getElementById("feedback-test").classList.contains("request-busy")`),
      "异常请求没有进入忙碌态");
    await js(`window.__rejectRequest(); new Promise((resolve) => setTimeout(resolve, 30))`);
    assert(!await js(`document.getElementById("feedback-test").classList.contains("request-busy")`),
      "请求异常后忙碌态没有恢复");
    assert(await js(`document.querySelector("#op-list .op-row.failed") !== null`),
      "失败请求没有保留失败状态");

    const beforeQuiet = await js(`document.querySelectorAll("#op-list .op-row").length`);
    await js(`window.fetch = () => Promise.reject(new Error("clarify"));
      api("/clarify", { method: "POST", operation: false, body: "{}" }).catch(() => {});
      new Promise((resolve) => setTimeout(resolve, 50))`);
    assert(await js(`document.querySelectorAll("#op-list .op-row").length`) === beforeQuiet,
      "operation:false 请求不应进入操作状态中心");

    await js(`(() => {
      window.__backgroundResolves = [];
      window.fetch = () => new Promise((resolve) => window.__backgroundResolves.push(() =>
        resolve({ status: 200, ok: true, json: async () => ({ ok: true }) })));
      api("/background-get");
      api("/background-post", { method: "POST", busy: false });
      return true;
    })()`);
    const quiet = await js(`({
      button: document.getElementById("feedback-test").classList.contains("request-busy"),
      progress: document.getElementById("request-progress").classList.contains("active"),
      operationCount: document.querySelectorAll("#op-list .op-row").length
    })`);
    assert(!quiet.button && !quiet.progress && quiet.operationCount === beforeQuiet,
      "后台 GET/POST 误触发了操作反馈: " + JSON.stringify({ beforeQuiet, quiet }));
    await js(`window.__backgroundResolves.forEach((resolve) => resolve());
      new Promise((resolve) => setTimeout(resolve, 30))`);
    console.log("请求即时反馈、防连点、后台静默与异常恢复：通过");
  } finally {
    try { ws?.close(); } catch (_) { /* ignore */ }
    for (const p of [edge, svc]) try { p?.kill(); } catch (_) { /* ignore */ }
    await sleep(500);
    for (const p of [edge, svc]) if (p?.pid) {
      try { spawn("taskkill", ["/F", "/T", "/PID", String(p.pid)], { stdio: "ignore" }); } catch (_) { /* ignore */ }
    }
    try { rmSync(tmp, { recursive: true, force: true }); } catch (_) { /* ignore */ }
  }
}

main().catch((error) => { console.error(error); process.exitCode = 1; });
