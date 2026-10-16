// Real mobile browser + local Codebee + local Worker; no production data.
import { spawn } from "node:child_process";
import { mkdtempSync, mkdirSync, writeFileSync, rmSync, existsSync } from "node:fs";
import { tmpdir } from "node:os";
import { resolve, join } from "node:path";
import { randomBytes } from "node:crypto";
import { fileURLToPath } from "node:url";
import assert from "node:assert/strict";

const root = fileURLToPath(new URL("../../../", import.meta.url));
const relay = process.env.CODEBEE_RELAY_TEST_URL || "http://127.0.0.1:18791";
const local = "http://127.0.0.1:18893";
const temp = mkdtempSync(join(tmpdir(), "codebee-phone-browser-"));
const data = join(temp, "data");
mkdirSync(data);
writeFileSync(join(data, "phone-relay.json"), JSON.stringify({ secret: randomBytes(32).toString("hex"), enabled: true }));
const service = spawn("python", ["app/main.py", "--host", "127.0.0.1", "--port", "18893", "--no-browser", "--no-public-tunnel"], {
  cwd: root, stdio: "ignore", windowsHide: true,
  env: { ...process.env, TUTTI_DATA: data, CODEBEE_RELAY_URL: relay, TUTTI_PET_DISABLED: "1", TUTTI_CLASSROOM_SYNC: "0" },
});
const sleep = ms => new Promise(resolve => setTimeout(resolve, ms));
const until = async fn => {
  for (let i = 0; i < 200; i++) { try { const value = await fn(); if (value) return value; } catch {} await sleep(300); }
  throw new Error("Browser fixture timeout");
};
const edgePath = "C:/Program Files (x86)/Microsoft/Edge/Application/msedge.exe";
if (!existsSync(edgePath)) throw new Error("Edge is required for this Windows browser check");
let edge, ws;
try {
  await until(async () => (await (await fetch(local + "/api/connect")).json()).relay.connected);
  edge = spawn(edgePath, ["--headless=new", "--disable-gpu", "--no-first-run", "--no-default-browser-check",
    "--user-data-dir=" + join(temp, "edge"), "--remote-debugging-port=9393", "about:blank"], { stdio: "ignore", windowsHide: true });
  const target = await until(async () => (await (await fetch("http://127.0.0.1:9393/json/list")).json()).find(x => x.type === "page"));
  ws = new WebSocket(target.webSocketDebuggerUrl);
  await new Promise((resolve, reject) => { ws.onopen = resolve; ws.onerror = reject; });
  let seq = 0;
  const pending = new Map();
  ws.onmessage = event => {
    const message = JSON.parse(event.data);
    const item = pending.get(message.id);
    if (item) { pending.delete(message.id); message.error ? item.reject(new Error(message.error.message)) : item.resolve(message.result); }
  };
  const cdp = (method, params = {}) => new Promise((resolve, reject) => {
    const id = ++seq;
    pending.set(id, { resolve, reject });
    ws.send(JSON.stringify({ id, method, params }));
  });
  const evaluate = async expression => {
    const result = await cdp("Runtime.evaluate", { expression: `(async () => (${expression}))()`, returnByValue: true, awaitPromise: true });
    if (result.exceptionDetails) throw new Error(result.exceptionDetails.text);
    return result.result.value;
  };
  await cdp("Page.enable");
  await cdp("Emulation.setDeviceMetricsOverride", { width: 390, height: 844, deviceScaleFactor: 1, mobile: true });
  await cdp("Page.navigate", { url: relay + "/relay/portal" });
  await until(() => evaluate("!!document.querySelector('.brand img.mark')"));
  await until(() => evaluate("document.querySelector('.brand img.mark').naturalWidth > 0"));
  assert.equal(await evaluate("(await fetch('/relay/codebee-logo.png')).status"), 200, "unpaired visitors can load the CodeBee logo");
  assert.equal(await evaluate("document.documentElement.scrollWidth <= innerWidth"), true);
  const output = resolve(root, "output/playwright/phone-relay");
  mkdirSync(output, { recursive: true });
  writeFileSync(join(output, "portal-mobile.png"), Buffer.from((await cdp("Page.captureScreenshot", { format: "png" })).data, "base64"));
  // The worker negotiates the portal language from Accept-Language; English is
  // the neutral default when the header is missing or names another language.
  await cdp("Emulation.setUserAgentOverride", { userAgent: "Mozilla/5.0", acceptLanguage: "en-US,en;q=0.9" });
  await cdp("Page.navigate", { url: relay + "/relay/portal" });
  await until(() => evaluate("document.documentElement.lang === 'en' && /Connect to your computer/.test(document.title)"));
  await cdp("Emulation.setUserAgentOverride", { userAgent: "Mozilla/5.0", acceptLanguage: "zh-CN,zh;q=0.9,en;q=0.8" });
  await cdp("Page.navigate", { url: relay + "/relay/portal" });
  await until(() => evaluate("document.documentElement.lang === 'zh-CN' && document.title.includes('连接我的电脑')"));
  await cdp("Emulation.setUserAgentOverride", { userAgent: "Mozilla/5.0", acceptLanguage: "fr-FR,fr;q=0.9" });
  await cdp("Page.navigate", { url: relay + "/relay/portal" });
  await until(() => evaluate("document.documentElement.lang === 'en'"));
  const paired = await fetch(local + "/api/connect/relay", { method: "POST", headers: { "content-type": "application/json", origin: local }, body: '{"action":"pair"}' });
  assert.equal(paired.status, 200);
  const code = await until(async () => (await (await fetch(local + "/api/connect")).json()).relay.pair_code);
  await cdp("Page.navigate", { url: relay + "/relay/portal#pair=" + code });
  await until(() => evaluate("location.pathname === '/' && !!document.querySelector('#app')"));
  assert.equal(await evaluate("location.hash"), "", "one-time secret removed from address bar");
  try { await until(() => evaluate("typeof S !== 'undefined' && !!document.querySelector('#app')")); }
  catch (error) {
    console.error(await evaluate("({path:location.pathname,status:document.querySelector('#status')?.textContent,title:document.title,app:!!document.querySelector('#app'),ready:document.readyState})"));
    throw error;
  }
  assert.equal(await evaluate("location.pathname"), "/");
  assert.equal(await evaluate("document.cookie.includes('codebee-device')"), false, "device cookie is HttpOnly");
  assert.equal(await evaluate("(await fetch('/api/state')).status"), 200);
  assert.equal(await evaluate("(await fetch('/api/connect/relay',{method:'POST',headers:{'content-type':'application/json'},body:JSON.stringify({action:'revoke'})})).status"), 403);
  writeFileSync(join(output, "paired-workbench-mobile.png"), Buffer.from((await cdp("Page.captureScreenshot", { format: "png" })).data, "base64"));
  // Test the desktop modal against the real local service.
  await cdp("Emulation.setDeviceMetricsOverride", { width: 1280, height: 900, deviceScaleFactor: 1, mobile: false });
  await cdp("Page.navigate", { url: local });
  await until(() => evaluate("typeof openPhoneConnect === 'function'"));
  await evaluate("openPhoneConnect()");
  await until(() => evaluate("!!document.getElementById('relay-connect-status')"));
  await evaluate("phoneRelayAction('pair')");
  await until(() => evaluate("!!document.querySelector('#qr-box svg') && !!document.getElementById('relay-pair-code').textContent"));
  console.log("PASS: mobile layout, QR fragment, real pairing, HttpOnly session, remote administration denied, desktop pairing modal");
} finally {
  ws?.close();
  for (const process of [edge, service]) if (process && process.exitCode === null) {
    process.kill();
    await Promise.race([new Promise(resolve => process.once("exit", resolve)), sleep(5000)]);
  }
  // Both paths were created under this test's unique OS temporary directory.
  rmSync(temp, { recursive: true, force: true, maxRetries: 5, retryDelay: 200 });
}
