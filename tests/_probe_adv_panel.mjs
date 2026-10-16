/* 一次性探针：起临时服务 → Edge headless 打开首页 → 点开「更多选项」→
 * 截图 + 对账 adv-body 与 cmp-box 的层叠上下文（谁把谁盖住） */
import { spawn } from 'node:child_process';
import { mkdtempSync, writeFileSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import { fileURLToPath } from 'node:url';
import { bootEdge } from './_ui_boot.mjs';

const root = fileURLToPath(new URL('../', import.meta.url));
const data = mkdtempSync(join(tmpdir(), 'codebee-adv-probe-'));
const port = 18799;
const origin = `http://127.0.0.1:${port}`;
const SHOT = join(root, 'tests', '_adv_panel.png');
const sleep = ms => new Promise(resolve => setTimeout(resolve, ms));

let browser, server;
try {
  server = spawn('python', ['-X', 'utf8', 'app/main.py', '--port', String(port), '--no-browser'], {
    cwd: root, stdio: 'ignore', windowsHide: true, env: { ...process.env, TUTTI_DATA: data },
  });
  let ready = false;
  for (let i = 0; i < 80 && !ready; i++) {
    try { ready = (await fetch(origin + '/api/state')).ok; } catch {}
    if (!ready) await sleep(250);
  }
  console.log('service up:', ready);

  browser = await bootEdge({ width: 1600, height: 1000, reducedMotion: false });
  const { send, evalJs: ev } = browser;
  await send('Runtime.enable');
  await send('Page.addScriptToEvaluateOnNewDocument', { source: 'localStorage.setItem("orch.welcomed","1")' });
  await send('Page.navigate', { url: origin });
  let loaded = false;
  for (let i = 0; i < 80 && !loaded; i++) { loaded = await ev('typeof S !== "undefined"'); if (!loaded) await sleep(250); }
  console.log('page loaded:', loaded);
  await sleep(1500);
  console.log('welcome:', await ev(`(() => { if (typeof welcomeClose === "function") { welcomeClose(); return "closed"; } return "no-fn"; })()`));
  await sleep(600);

  console.log('open:', await ev(`(() => {
    const d = document.getElementById("f-advanced");
    if (!d) return "no-details";
    d.open = true;
    return "opened=" + d.open;
  })()`));
  await sleep(800);

  const shot = await send('Page.captureScreenshot', { format: 'png' });
  writeFileSync(SHOT, Buffer.from(shot.result.data, 'base64'));
  console.log('shot saved:', SHOT);

  console.log(await ev(`(() => {
    const pick = (el) => {
      const chain = [];
      let n = el;
      while (n && n !== document.documentElement) {
        const cs = getComputedStyle(n);
        const flags = [];
        if (cs.position !== "static") flags.push("pos=" + cs.position + "/z" + cs.zIndex);
        if (cs.transform !== "none") flags.push("transform");
        if (cs.filter !== "none") flags.push("filter");
        if (cs.backdropFilter && cs.backdropFilter !== "none") flags.push("backdrop");
        if (cs.willChange && cs.willChange !== "auto") flags.push("will-change=" + cs.willChange);
        if (cs.contain && cs.contain !== "none") flags.push("contain=" + cs.contain);
        if (parseFloat(cs.opacity) < 1) flags.push("opacity=" + cs.opacity);
        if (cs.animationName !== "none") flags.push("anim=" + cs.animationName);
        if (flags.length) chain.push(n.tagName + (n.id ? "#" + n.id : "") + (n.className && typeof n.className === "string" ? "." + n.className.split(" ").join(".") : "") + " [" + flags.join(",") + "]");
        n = n.parentElement;
      }
      return chain;
    };
    const adv = document.querySelector("#f-advanced .adv-body");
    const box = document.querySelector("#sub-tasks .cmp-box");
    const out = {
      adv: adv ? { rect: adv.getBoundingClientRect().toJSON(), chain: pick(adv) } : "no-adv-body",
      box: box ? { rect: box.getBoundingClientRect().toJSON(), chain: pick(box) } : "no-cmp-box",
    };
    return JSON.stringify(out, null, 1);
  })()`));

  console.log('hit@box-top:', await ev(`(() => {
    const box = document.querySelector("#sub-tasks .cmp-box");
    if (!box) return "no-box";
    const r = box.getBoundingClientRect();
    const el = document.elementFromPoint(r.left + r.width / 2, r.top + 4);
    return el ? el.tagName + "#" + (el.id || "") + "." + (typeof el.className === "string" ? el.className : "") : "none";
  })()`));

  await browser.close();
} finally {
  try { if (browser) await browser.close(); } catch {}
  if (server) { try { spawn('taskkill', ['/F', '/T', '/PID', String(server.pid)], { stdio: 'ignore' }); } catch {} }
}
process.exit(0);
