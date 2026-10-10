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
      " if(typeof window.welcomeClose==='function') window.welcomeClose();",
      " const welcome=document.getElementById('welcome');if(welcome)welcome.classList.add('hidden');",
      " document.body.style.cssText='margin:0;padding:0;background:#eaf3fa;font-family:Arial,sans-serif;overflow:hidden';",
      " const stage=document.createElement('div');stage.id='hive3d-smoke-stage';stage.style.cssText='position:fixed;inset:0;z-index:2147483000;display:flex;align-items:center;justify-content:center;padding:24px;box-sizing:border-box;background:linear-gradient(180deg,#eef7ff,#dbeaf6)';document.body.appendChild(stage);",
      " stage.appendChild(viewport);",
      " viewport.classList.add('hive-mode-3d');viewport.classList.remove('hive-mode-2d');",
      " viewport.style.cssText+=';display:block;position:relative;flex:0 0 auto;width:min(1200px,calc(100vw - 48px));height:auto;max-height:calc(100vh - 48px);aspect-ratio:2848/1600;margin:0;overflow:hidden';",
      " const activated=[];window.__hive3dSmokeClicks=activated;",
      " window.renderHive({id:'release-smoke',steps:[{role:'规划',status:'running',log:'steps/smoke-0.log',agent_label:'smoke agent',summary:'latest output'}]});",
      " const scene=window.__hive3d;if(!scene) throw new Error('WebGL renderer unavailable');",
      " scene.onCellActivate=(runId,rel,cell)=>activated.push({runId,rel,status:cell.status});scene.cellRefresh=()=>({tail:'latest output',elapsed:'3s'});",
      " const allCells=Array.from({length:14},(_,i)=>({rel:'steps/smoke-'+i+'.log',role:'测试步骤 '+(i+1),status:i===0?'running':i<7?'done':'queued',displayTail:'最新输出 '+i,displayElapsed:'3s',displayAgent:'smoke agent'}));",
      " const names=['规划','起草','评审','执行','打磨','合成'];let cursor=0;",
      " const lanes=names.map((name,index)=>{const count=index<2?3:2;const cells=allCells.slice(cursor,cursor+count);cursor+=count;return{name,count:cells.length,settled:cells.filter(c=>!['running','queued'].includes(c.status)).length,active:cells.some(c=>c.status==='running'),cells};});",
      " scene.sync({runId:'release-smoke',lanes});scene.setActive(true);",
      " const monitor=viewport.querySelector('.hg-monitor'),badge=viewport.querySelector('.hg-badge');",
      " const monitorCenters=Array.from(viewport.querySelectorAll('.hg-monitor')).map(el=>{const r=el.getBoundingClientRect();return[r.x+r.width/2,r.y+r.height/2]});",
      " return JSON.stringify({mode:scene.displayMode,referenceDisplay:getComputedStyle(viewport.querySelector('.hive-reference')).display,canvasHidden:canvas.hidden,info:scene.info(),deskRows:scene.info().deskRows,monitorCenters,monitors:viewport.querySelectorAll('.hg-monitor').length,visibleMonitors:Array.from(viewport.querySelectorAll('.hg-monitor')).filter(el=>getComputedStyle(el).visibility!=='hidden'&&el.getBoundingClientRect().width>5).length,badges:viewport.querySelectorAll('.hg-badge').length,visibleBadges:Array.from(viewport.querySelectorAll('.hg-badge')).filter(el=>getComputedStyle(el).visibility!=='hidden'&&el.getBoundingClientRect().width>5).length,flowLinks:viewport.querySelectorAll('.hg-flow-link').length,viewportRect:(()=>{const r=viewport.getBoundingClientRect();return[r.x,r.y,r.width,r.height];})(),monitorRect:monitor?(()=>{const r=monitor.getBoundingClientRect();return[r.x,r.y,r.width,r.height];})():null,badgeRect:badge?(()=>{const r=badge.getBoundingClientRect();return[r.x,r.y,r.width,r.height];})():null});",
      "})()"
    ].join("\n");
    const state = JSON.parse(await evaluate(setupExpr));
    assert.equal(state.info.renderer, "webgl", "the browser initialized the WebGL renderer");
    assert.ok(state.info.objects > 1000, "the room and workstation geometry was built");
    assert.deepEqual(state.deskRows, [8, 6], "the modeled workstation layout matches the reference (8 rear + 6 front)");
    assert.equal(state.monitorCenters.length, 14, "one DOM monitor overlay exists per modeled workstation");
    const rearAverageY = state.monitorCenters.slice(0, 8).reduce((sum, point) => sum + point[1], 0) / 8;
    const frontAverageY = state.monitorCenters.slice(8).reduce((sum, point) => sum + point[1], 0) / 6;
    assert.ok(rearAverageY < frontAverageY, "rear monitor overlays project above front-row overlays");
    assert.equal(state.info.cells, 14, "fourteen task cells were mapped");
    assert.equal(state.info.lanes, 6, "six workflow lanes were mapped");
    assert.equal(state.mode, "reference", "the supplied high-fidelity scene is the default view");
    assert.notEqual(state.referenceDisplay, "none", "reference artwork is visible in the default view");
    assert.equal(state.canvasHidden, true, "free 3D canvas stays hidden in reference mode");
    assert.ok(state.viewportRect && state.viewportRect[0] >= 0 && state.viewportRect[1] >= 0 && state.viewportRect[2] >= 1000 && state.viewportRect[3] >= 500, "the 3D viewport is placed inside the captured browser window");
    assert.equal(await evaluate("document.getElementById('welcome')?.classList.contains('hidden') ?? true"), true, "welcome modal does not obscure the scene screenshot");
    assert.equal(state.monitors, 14, "fourteen clickable monitor overlays exist");
    assert.ok(state.visibleMonitors >= 8, "monitor overlays are projected into visible screen coordinates");
    assert.equal(state.badges, 6, "six workflow cards exist");
    assert.ok(state.visibleBadges >= 5, "workflow cards are projected onto the wall");
    assert.equal(state.flowLinks, 5, "five workflow connectors exist");
    assert.ok(state.monitorRect && state.monitorRect[2] > 5 && state.monitorRect[3] > 5, "monitor overlay has on-screen bounds");
    assert.ok(state.badgeRect && state.badgeRect[2] > 5 && state.badgeRect[3] > 5, "workflow card has on-screen bounds");

    // Switch to the real WebGL scene before capturing the CI artifact. Capturing the reference plate
    // here used to hide the exact procedural geometry that this smoke test is meant to validate.
    const live3dState = JSON.parse(await evaluate([
      "(() => {",
      " document.querySelector('[data-hive-view=\"live3d\"]').click();",
      " const viewport=document.getElementById('rd-hive-viewport'),canvas=document.getElementById('rd-hive-gl'),scene=window.__hive3d;",
      " const gl=canvas.getContext('webgl')||canvas.getContext('experimental-webgl');",
      " const pixels=new Uint8Array(canvas.width*canvas.height*4);if(gl)gl.readPixels(0,0,canvas.width,canvas.height,gl.RGBA,gl.UNSIGNED_BYTE,pixels);",
      " const clear=gl?Array.from(gl.getParameter(gl.COLOR_CLEAR_VALUE)).map(v=>Math.round(v*255)):[0,0,0,0];let nonBackgroundPixels=0;const sampledColors=new Set();for(let p=0;p<pixels.length;p+=4){if(Math.abs(pixels[p]-clear[0])+Math.abs(pixels[p+1]-clear[1])+Math.abs(pixels[p+2]-clear[2])>12)nonBackgroundPixels++;if(p%(4*97)===0)sampledColors.add(pixels[p]+','+pixels[p+1]+','+pixels[p+2]);}",
      " const monitor=viewport.querySelector('.hg-monitor'),badge=viewport.querySelector('.hg-badge');",
      " return JSON.stringify({mode:scene.displayMode,referenceDisplay:getComputedStyle(viewport.querySelector('.hive-reference')).display,canvasHidden:canvas.hidden,artboardTransform:scene.artboard.style.transform,canvas:[canvas.width,canvas.height],glError:gl?gl.getError():-1,glVersion:gl?gl.getParameter(gl.VERSION):'',nonBackgroundPixels,sampledColors:sampledColors.size,monitors:viewport.querySelectorAll('.hg-monitor').length,visibleMonitors:Array.from(viewport.querySelectorAll('.hg-monitor')).filter(el=>getComputedStyle(el).visibility!=='hidden'&&el.getBoundingClientRect().width>5).length,badges:viewport.querySelectorAll('.hg-badge').length,visibleBadges:Array.from(viewport.querySelectorAll('.hg-badge')).filter(el=>getComputedStyle(el).visibility!=='hidden'&&el.getBoundingClientRect().width>5).length,flowLinks:viewport.querySelectorAll('.hg-flow-link').length,monitorWidthStyle:monitor?monitor.style.width:'',viewportRect:(()=>{const r=viewport.getBoundingClientRect();return[r.x,r.y,r.width,r.height];})(),monitorRect:monitor?(()=>{const r=monitor.getBoundingClientRect();return[r.x,r.y,r.width,r.height];})():null,badgeRect:badge?(()=>{const r=badge.getBoundingClientRect();return[r.x,r.y,r.width,r.height];})():null});",
      "})()"
    ].join("\n")));
    assert.equal(live3dState.mode, "live3d", "free 3D mode is activated through the UI control");
    assert.equal(live3dState.referenceDisplay, "none", "reference image is hidden in free 3D mode");
    assert.equal(live3dState.canvasHidden, false, "WebGL canvas is visible in free 3D mode");
    assert.equal(live3dState.artboardTransform, "none", "free 3D overlays are transformed only by the projected camera");
    assert.ok(live3dState.canvas[0] >= 1000 && live3dState.canvas[1] >= 500, "free 3D has a full-size drawing buffer");
    assert.equal(live3dState.glError, 0, "free 3D WebGL reports NO_ERROR after rendering and readback");
    assert.ok(live3dState.nonBackgroundPixels > 10000, "free 3D GPU readback contains scene pixels");
    assert.ok(live3dState.sampledColors > 20, "free 3D GPU readback contains varied lit materials");
    assert.equal(live3dState.monitors, 14, "free 3D keeps fourteen clickable monitors");
    assert.ok(live3dState.visibleMonitors >= 8, "free 3D monitor overlays remain in visible projected coordinates");
    assert.equal(live3dState.badges, 6, "free 3D keeps six workflow cards");
    assert.ok(live3dState.visibleBadges >= 5, "free 3D workflow cards remain visible on the wall");
    assert.equal(live3dState.flowLinks, 5, "free 3D keeps five workflow connectors");
    assert.ok(live3dState.monitorWidthStyle.endsWith("px"), "free 3D sizes overlays from real projected geometry");
    assert.ok(live3dState.viewportRect && live3dState.viewportRect[2] >= 1000 && live3dState.viewportRect[3] >= 500, "free 3D viewport remains inside the browser window");

    // Save the actual procedural WebGL view, not the static reference artwork. This makes CI's
    // screenshot artifact useful for visual regression reviews of geometry, lighting and framing.
    const shot = await send("Page.captureScreenshot", { format: "png", captureBeyondViewport: false, clip: { x: live3dState.viewportRect[0], y: live3dState.viewportRect[1], width: live3dState.viewportRect[2], height: live3dState.viewportRect[3], scale: 1 } });
    const artifactDir = join(ROOT, "tests", ".ui-shots");
    mkdirSync(artifactDir, { recursive: true });
    writeFileSync(join(artifactDir, "hive3d-webgl.png"), Buffer.from(shot.result.data, "base64"));

    await evaluate("document.querySelector('#rd-hive-viewport .hg-monitor:not(:disabled)').click()");
    await sleep(100);
    assert.ok(await evaluate("window.__hive3dSmokeClicks.length") > 0, "monitor click reaches the live-log callback");
    assert.deepEqual(consoleErrors, [], "browser console is free of errors during scene initialization");
    console.log("Hive3D browser smoke passed " + JSON.stringify({
      defaultView: state.mode, glVersion: live3dState.glVersion, objects: live3dState.nonBackgroundPixels,
      monitors: live3dState.visibleMonitors + "/14", stages: live3dState.visibleBadges + "/6",
      connectors: live3dState.flowLinks, canvas: live3dState.canvas, gpuPixels: live3dState.nonBackgroundPixels,
      sampledColors: live3dState.sampledColors, viewport: live3dState.viewportRect
    }));
  } finally {
    try { if (ws) ws.close(); } catch {}
    killTree(edge);
    killTree(service);
    try { rmSync(temp, { recursive: true, force: true }); } catch {}
  }
}

main().catch(error => { console.error("Hive3D browser smoke failed:", error); process.exitCode = 1; });
