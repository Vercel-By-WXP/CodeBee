/* New-task fixed composer: validate actual geometry, including normal motion.
 * --staged checks the CSS being committed without disturbing shared edits. */
import assert from 'node:assert/strict';
import { spawn, execFileSync } from 'node:child_process';
import { mkdtempSync, mkdirSync, writeFileSync, rmSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import { fileURLToPath } from 'node:url';
import { bootEdge } from './_ui_boot.mjs';

const root = fileURLToPath(new URL('../', import.meta.url));
const data = mkdtempSync(join(tmpdir(), 'codebee-new-composer-'));
const port = Number(process.env.TUTTI_TEST_PORT || 18991);
const origin = `http://127.0.0.1:${port}`;
const stagedCss = process.argv.includes('--staged')
  ? execFileSync('git', ['show', ':app/ui/desktop-workbench.css'], { cwd: root, encoding: 'utf8' }) : null;
const sleep = ms => new Promise(resolve => setTimeout(resolve, ms));
let browser, server;
try {
  server = spawn('python', ['app/main.py', '--port', String(port), '--no-browser'], {
    cwd: root, stdio: 'ignore', windowsHide: true, env: { ...process.env, TUTTI_DATA: data },
  });
  let ready = false;
  for (let i = 0; i < 60 && !ready; i++) {
    try { ready = (await fetch(origin + '/api/state')).ok; } catch {}
    if (!ready) await sleep(250);
  }
  assert.ok(ready, 'isolated test server ready');
  browser = await bootEdge({ width: 1920, height: 1000, awaitPromise: true, reducedMotion: false });
  await browser.send('Page.navigate', { url: origin });
  for (let i = 0; i < 40; i++) {
    if (await browser.evalJs("typeof welcomeClose === 'function'")) break;
    await sleep(150);
  }
  await browser.evalJs('welcomeClose()');
  if (stagedCss !== null) {
    await browser.evalJs(`(() => {
      document.querySelector('link[href*="desktop-workbench.css"]').disabled = true;
      const style = document.createElement('style');
      style.textContent = ${JSON.stringify(stagedCss)};
      document.head.append(style);
    })()`);
  }
  let count = 0;
  for (const [width, height] of [[1920, 1000], [1366, 768], [1043, 367], [1043, 300], [920, 600]]) {
    await browser.send('Emulation.setDeviceMetricsOverride', { width, height, deviceScaleFactor: 1, mobile: false });
    for (const [collapsed, inspector, sidebar] of [[false, false, 288], [true, false, 288], [false, false, 360], [false, true, 288], [true, true, 288]]) {
      await browser.evalJs(`document.body.classList.toggle('side-collapsed', ${collapsed});
        document.body.classList.toggle('inspector-open', ${inspector});
        document.documentElement.style.setProperty('--side-w', '${sidebar}px')`);
      await sleep(100);
      const bounds = await browser.evalJs(`(() => {
        const rect = selector => { const r=document.querySelector(selector).getBoundingClientRect();
          return {left:r.left,right:r.right,top:r.top,bottom:r.bottom,width:r.width}; };
        const hint=document.querySelector('#cmp-workspace-hint');
        const hintStyle=getComputedStyle(hint);
        return {main:rect('main'), form:rect('#sub-tasks .form'), input:rect('#f-goal'),
          hint:rect('#cmp-workspace-hint'), hintVisible:hintStyle.display !== 'none',
          hintAlign:hintStyle.textAlign, viewport:innerHeight};
      })()`);
      assert.ok(bounds, 'geometry evaluated');
      const label = JSON.stringify({width, height, collapsed, inspector, sidebar, bounds});
      assert.ok(Math.abs((bounds.form.left + bounds.form.right) / 2 - (bounds.main.left + bounds.main.right) / 2) < 2, 'composer centered: ' + label);
      assert.ok(bounds.form.left >= bounds.main.left + 23 && bounds.form.right <= bounds.main.right - 23, 'composer stays inside main canvas: ' + label);
      assert.ok(bounds.form.bottom <= bounds.viewport - 23 && bounds.form.top >= bounds.main.top, 'composer visible vertically: ' + label);
      assert.ok(bounds.input.width > 0, 'prompt available: ' + label);
      if (bounds.hintVisible) {
        assert.ok(Math.abs((bounds.hint.left + bounds.hint.right) / 2 - (bounds.form.left + bounds.form.right) / 2) < 2, 'subtitle centered on composer: ' + label);
        assert.equal(bounds.hintAlign, 'center', 'subtitle text centered: ' + label);
      }
      count++;
    }
  }
  await browser.send('Emulation.setDeviceMetricsOverride', { width: 1920, height: 1000, deviceScaleFactor: 1, mobile: false });
  await browser.evalJs("document.body.classList.remove('side-collapsed','inspector-open'); document.documentElement.style.removeProperty('--side-w')");
  const output = join(root, 'output', 'composer-geometry');
  mkdirSync(output, { recursive: true });
  const shot = await browser.send('Page.captureScreenshot', { format: 'png' });
  writeFileSync(join(output, 'new-task-centered.png'), Buffer.from(shot.result.data, 'base64'));
  console.log(`PASS ${count} new-task geometry cases (${stagedCss === null ? 'workspace' : 'staged'} CSS, normal motion)`);
} finally {
  if (browser) await browser.close();
  if (server && server.exitCode === null) {
    const stopped = new Promise(resolve => server.once('exit', resolve));
    server.kill();
    await stopped;
  }
  rmSync(data, { recursive: true, force: true });
}
