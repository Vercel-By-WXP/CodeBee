/* Windows Edge/WebGL release smoke: real app shell, projected live overlays, click wiring and screenshot. */
import assert from "node:assert/strict";
import { spawn, execFileSync } from "node:child_process";
import { existsSync, mkdirSync, mkdtempSync, rmSync, writeFileSync } from "node:fs";
import { tmpdir } from "node:os";
import { join, dirname } from "node:path";
import { fileURLToPath } from "node:url";
import net from "node:net";

const ROOT = join(dirname(fileURLToPath(import.meta.url)), "..");
const EDGE_CANDIDATES = [
  "C:\\Program Files (x86)\\Microsoft\\Edge\\Application\\msedge.exe",
  "C:\\Program Files\\Microsoft\\Edge\\Application\\msedge.exe",
];
const sleep = ms => new Promise(resolve => setTimeout(resolve, ms));

async function freePort() {
  const server = net.createServer();
  await new Promise((resolve, reject) => server.once("error", reject).listen(0, "127.0.0.1", resolve));
  const port = server.address().port;
  await new Promise(resolve => server.close(resolve));
  return port;
}
async function waitFor(check, timeoutMs, label) {
  const end = Date.now() + timeoutMs;
  let lastError;
  while (Date.now() < end) {
    try { const value = await check(); if (value) return value; } catch (error) { lastError = error; }
    await sleep(300);
  }
  throw new Error("Timed out waiting for " + label + (lastError ? ": " + lastError.message : ""));
}
function killTree(proc) {
  if (!proc || !proc.pid) return;
  try { execFileSync("taskkill", ["/F", "/T", "/PID", String(proc.pid)], { stdio: "ignore", timeout: 10000 }); }
  catch { try { proc.kill(); } catch {} }
}

async function main() {
  const edgePath = EDGE_CANDIDATES.find(existsSync);
  assert.ok(edgePath, "Microsoft Edge exists on the Windows runner");
  const temp = mkdtempSync(join(tmpdir(), "codebee-hive3d-smoke-"));
  const dataDir = join(temp, "data");
  const profile = join(temp, "edge-profile");
  const servicePort = await freePort();
  const cdpPort = await freePort();
  const serviceUrl = "http://127.0.0.1:" + servicePort;
  let service, edge, ws;
  const consoleErrors = [];
  const serviceLog = [];
  let serviceExit = null;
  let serviceSpawnError = null;
  const rememberServiceOutput = chunk => {
    for (const line of String(chunk).split(/\\r?\\n/)) if (line.trim()) {
      serviceLog.push(line);
      if (serviceLog.length > 120) serviceLog.shift();
    }
  };
  try {
    service = spawn("python", ["-X", "utf8", join(ROOT, "app", "main.py"), "--port", String(servicePort), "--no-browser", "--host", "127.0.0.1"], {
      cwd: ROOT, stdio: ["ignore", "pipe", "pipe"], env: { ...process.env, TUTTI_DATA: dataDir, PYTHONPATH: ROOT },
    });
    service.stdout.on("data", rememberServiceOutput);
    service.stderr.on("data", rememberServiceOutput);
    service.on("error", error => { serviceSpawnError = error.message; });
    service.on("exit", (code, signal) => { serviceExit = { code, signal }; });
    await waitFor(async () => {
      if (serviceSpawnError) throw new Error("unable to spawn Python: " + serviceSpawnError);
      if (serviceExit) throw new Error("CodeBee exited early: " + JSON.stringify(serviceExit) + "\\n" + serviceLog.slice(-30).join("\\n"));
      return (await fetch(serviceUrl + "/api/state")).ok;
    }, 90000, "CodeBee API (startup log follows) " + serviceLog.slice(-12).join(" | "));
    edge = spawn(edgePath, [
      "--headless=new", "--no-first-run", "--disable-extensions", "--enable-webgl",
      "--use-gl=angle", "--use-angle=swiftshader", "--enable-unsafe-swiftshader",
      "--force-device-scale-factor=1", "--window-size=1500,1000",
      "--user-data-dir=" + profile, "--remote-debugging-port=" + cdpPort, "about:blank",
    ], { stdio: "ignore" });
    const page = await waitFor(async () => {
      const targets = await (await fetch("http://127.0.0.1:" + cdpPort + "/json/list")).json();
      return targets.find(target => target.type === "page");
    }, 30000, "Edge CDP page");
    ws = new WebSocket(page.webSocketDebuggerUrl);
    await new Promise((resolve, reject) => { ws.onopen = resolve; ws.onerror = reject; });
    let seq = 0;
    const pending = new Map();
    ws.onmessage = event => {
      const message = JSON.parse(event.data);
      if (message.id && pending.has(message.id)) {
        const resolve = pending.get(message.id); pending.delete(message.id); resolve(message);
      }
      if (message.method === "Runtime.exceptionThrown") consoleErrors.push(message.params.exceptionDetails && message.params.exceptionDetails.text || "runtime exception");
      if (message.method === "Runtime.consoleAPICalled" && message.params.type === "error")
        consoleErrors.push((message.params.args || []).map(arg => arg.value || arg.description || "").join(" "));
    };
    const send = (method, params = {}) => new Promise((resolve, reject) => {
      const id = ++seq;
      pending.set(id, message => message.error ? reject(new Error(JSON.stringify(message.error))) : resolve(message));
      ws.send(JSON.stringify({ id, method, params }));
    });
    const evaluate = async expression => {
      const response = await send("Runtime.evaluate", { expression, returnByValue: true, awaitPromise: true });
      if (response.result && response.result.exceptionDetails) throw new Error(response.result.exceptionDetails.text || "browser evaluation failed");
      return response.result && response.result.result ? response.result.result.value : undefined;
    };
    await send("Runtime.enable");
    await send("Page.enable");
    await send("Emulation.setDeviceMetricsOverride", { width: 1500, height: 1000, deviceScaleFactor: 1, mobile: false });
    await send("Page.navigate", { url: serviceUrl + "/" });
    await waitFor(async () => (await evaluate("document.readyState")) === "complete", 20000, "CodeBee page load");
    await sleep(800);

    const setupExpr = [
      "(() => {",
      " const viewport=document.getElementById('rd-hive-viewport');",
      " const canvas=document.getElementById('rd-hive-gl');",
      " const overlay=document.getElementById('rd-hive-overlay');",
      " if(!viewport||!canvas||!overlay||!window.Hive3D) throw new Error('Hive3D host or engine missing');",
      " document.body.style.cssText='margin:0;padding:24px;background:#eaf3fa;font-family:Arial,sans-serif;overflow:hidden';",
      " document.body.appendChild(viewport);",
      " viewport.classList.add('hive-mode-3d');viewport.classList.remove('hive-mode-2d');",
      " viewport.style.cssText+=';display:block;position:relative;width:1200px;height:650px;aspect-ratio:auto;margin:0 auto;overflow:hidden';",
      " const activated=[];window.__hive3dSmokeClicks=activated;",
      " const scene=window.Hive3D.create({canvas,overlay,onCellActivate:(runId,rel,cell)=>activated.push({runId,rel,status:cell.status}),cellRefresh:()=>({tail:'latest output',elapsed:'3s'})});",
      " if(!scene) throw new Error('WebGL renderer unavailable');",
      " const allCells=Array.from({length:14},(_,i)=>({rel:'steps/smoke-'+i+'.log',role:'测试步骤 '+(i+1),status:i===0?'running':i<7?'done':'queued',displayTail:'最新输出 '+i,displayElapsed:'3s',displayAgent:'smoke agent'}));",
      " const names=['规划','起草','评审','执行','打磨','合成'];let cursor=0;",
      " const lanes=names.map((name,index)=>{const count=index<2?3:2;const cells=allCells.slice(cursor,cursor+count);cursor+=count;return{name,count:cells.length,settled:cells.filter(c=>!['running','queued'].includes(c.status)).length,active:cells.some(c=>c.status==='running'),cells};});",
      " scene.sync({runId:'release-smoke',lanes});scene.setActive(true);",
      " const gl=canvas.getContext('webgl')||canvas.getContext('experimental-webgl');",
      " const monitor=viewport.querySelector('.hg-monitor'),badge=viewport.querySelector('.hg-badge');",
      " return JSON.stringify({info:scene.info(),canvas:[canvas.width,canvas.height],glError:gl?gl.getError():-1,glVersion:gl?gl.getParameter(gl.VERSION):'',monitors:viewport.querySelectorAll('.hg-monitor').length,visibleMonitors:Array.from(viewport.querySelectorAll('.hg-monitor')).filter(el=>getComputedStyle(el).visibility!=='hidden'&&el.getBoundingClientRect().width>5).length,badges:viewport.querySelectorAll('.hg-badge').length,visibleBadges:Array.from(viewport.querySelectorAll('.hg-badge')).filter(el=>getComputedStyle(el).visibility!=='hidden'&&el.getBoundingClientRect().width>5).length,flowLinks:viewport.querySelectorAll('.hg-flow-link').length,monitorRect:monitor?(()=>{const r=monitor.getBoundingClientRect();return[r.x,r.y,r.width,r.height];})():null,badgeRect:badge?(()=>{const r=badge.getBoundingClientRect();return[r.x,r.y,r.width,r.height];})():null});",
      "})()"
    ].join("\n");
    const state = JSON.parse(await evaluate(setupExpr));
    assert.equal(state.info.renderer, "webgl", "the browser initialized the WebGL renderer");
    assert.ok(state.info.objects > 1000, "the room and workstation geometry was built");
    assert.equal(state.info.cells, 14, "fourteen task cells were mapped");
    assert.equal(state.info.lanes, 6, "six workflow lanes were mapped");
    assert.ok(state.canvas[0] >= 1000 && state.canvas[1] >= 500, "canvas has a real drawing buffer");
    assert.equal(state.glError, 0, "WebGL reports NO_ERROR after rendering");
    assert.equal(state.monitors, 14, "fourteen clickable monitor overlays exist");
    assert.ok(state.visibleMonitors >= 8, "monitor overlays are projected into visible screen coordinates");
    assert.equal(state.badges, 6, "six workflow cards exist");
    assert.ok(state.visibleBadges >= 5, "workflow cards are projected onto the wall");
    assert.equal(state.flowLinks, 5, "five workflow connectors exist");
    assert.ok(state.monitorRect && state.monitorRect[2] > 5 && state.monitorRect[3] > 5, "monitor overlay has on-screen bounds");
    assert.ok(state.badgeRect && state.badgeRect[2] > 5 && state.badgeRect[3] > 5, "workflow card has on-screen bounds");

    const shot = await send("Page.captureScreenshot", { format: "png", captureBeyondViewport: false });
    const artifactDir = join(ROOT, "tests", ".ui-shots");
    mkdirSync(artifactDir, { recursive: true });
    writeFileSync(join(artifactDir, "hive3d-webgl.png"), Buffer.from(shot.result.data, "base64"));

    await evaluate("document.querySelector('#rd-hive-viewport .hg-monitor:not(:disabled)').click()");
    await sleep(100);
    assert.ok(await evaluate("window.__hive3dSmokeClicks.length") > 0, "monitor click reaches the live-log callback");
    assert.deepEqual(consoleErrors, [], "browser console is free of errors during scene initialization");
    console.log("Hive3D browser smoke passed " + JSON.stringify({
      glVersion: state.glVersion, objects: state.info.objects, monitors: state.visibleMonitors + "/14",
      stages: state.visibleBadges + "/6", connectors: state.flowLinks, canvas: state.canvas
    }));
  } finally {
    try { if (ws) ws.close(); } catch {}
    killTree(edge);
    killTree(service);
    try { rmSync(temp, { recursive: true, force: true }); } catch {}
  }
}

main().catch(error => { console.error("Hive3D browser smoke failed:", error); process.exitCode = 1; });
