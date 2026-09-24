/* 共享 Edge 无头引导（docs/ui-design.md §8 验收环境基线）。
 * 所有 UI 测试的浏览器段一律从这里起，保证可比性：
 *   - DPR 钉 1（--force-device-scale-factor=1）：不同缩放的截图不可比较；
 *   - CDP 口默认随机（固定口多代理并行会双绑互驱假红），可用 TUTTI_TEST_CDP 覆盖；
 *   - 动画断言显式传 reducedMotion:false（headless 默认 reduce 会命中全局动画禁用）。
 * 引导完成即回读 devicePixelRatio / innerWidth×innerHeight 进 envInfo，
 * 测试结果头应打印它，交付记录才有据可查。close() 按 PID 杀进程树并清 profile。 */
import { spawn } from "node:child_process";
import { mkdtempSync, rmSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";

const EDGE_CANDIDATES = [
  "C:\\Program Files (x86)\\Microsoft\\Edge\\Application\\msedge.exe",
  "C:\\Program Files\\Microsoft\\Edge\\Application\\msedge.exe",
];

const sleep = (ms) => new Promise((r) => setTimeout(r, ms));

export async function bootEdge(opts = {}) {
  const width = opts.width || 1440, height = opts.height || 950;
  const cdpPort = Number(opts.cdp || process.env.TUTTI_TEST_CDP)
    || (21000 + Math.floor(Math.random() * 20000));
  const profile = mkdtempSync(join(tmpdir(), "tutti-boot-"));
  const args = [
    "--headless=new", "--disable-gpu", "--no-first-run",
    `--user-data-dir=${profile}`, `--remote-debugging-port=${cdpPort}`,
    `--window-size=${width},${height}`,
    "--force-device-scale-factor=1",       // DPR=1：截图/度量基线
    "about:blank",
  ];
  if (opts.reducedMotion === false) args.push("--blink-settings=prefersReducedMotion=false");
  if (opts.reducedMotion === true) args.push("--force-prefers-reduced-motion");
  const proc = spawn(EDGE_CANDIDATES[0], args, { stdio: "ignore" });

  let target = null;
  for (let i = 0; i < 30 && !target; i++) {
    await sleep(500);
    try {
      const res = await fetch(`http://127.0.0.1:${cdpPort}/json/list`);
      const list = await res.json();
      target = list.find((t) => t.type === "page");
    } catch (e) { /* Edge 未就绪 */ }
  }
  if (!target) {
    try { spawn("taskkill", ["/F", "/T", "/PID", String(proc.pid)], { stdio: "ignore" }); } catch (e) {}
    throw new Error("Edge headless 未就绪（CDP " + cdpPort + "）");
  }

  const ws = new WebSocket(target.webSocketDebuggerUrl);
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
    const r = await send("Runtime.evaluate", { expression: expr, returnByValue: true, awaitPromise: !!opts.awaitPromise });
    return r.result?.result?.value;
  };

  // 环境自证：DPR/视口回读。若 DPR≠1 说明环境被外因改写，测试头应显式报出来。
  let envInfo = { dpr: null, w: 0, h: 0 };
  try {
    envInfo = await evalJs("JSON.stringify({dpr: window.devicePixelRatio, w: innerWidth, h: innerHeight})")
      .then((s) => JSON.parse(s || "{}"));
  } catch (e) { /* 回读失败不阻塞测试，但 envInfo 留空可见 */ }
  envInfo.cdp = cdpPort;
  envInfo.line = `Edge env: DPR=${envInfo.dpr} viewport=${envInfo.w}x${envInfo.h} cdp=${cdpPort}`;

  const close = async () => {
    try { ws.close(); } catch (e) { /* ignore */ }
    try { proc.kill(); } catch (e) { /* ignore */ }
    await sleep(800);
    try { spawn("taskkill", ["/F", "/T", "/PID", String(proc.pid)], { stdio: "ignore" }); } catch (e) { /* ignore */ }
    try { rmSync(profile, { recursive: true, force: true }); } catch (e) { /* ignore */ }
  };

  return { proc, ws, send, evalJs, envInfo, cdpPort, close };
}
