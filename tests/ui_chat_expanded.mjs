/* Expanded conversation regression: isolated data, no CLI execution. */
import assert from 'node:assert/strict';
import { spawn } from 'node:child_process';
import { mkdtempSync, mkdirSync, writeFileSync, rmSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import { fileURLToPath } from 'node:url';
import { bootEdge } from './_ui_boot.mjs';

const root = fileURLToPath(new URL('../', import.meta.url));
const data = mkdtempSync(join(tmpdir(), 'codebee-chat-expanded-'));
const output = join(root, 'output', 'playwright', 'chat-expanded');
const port = Number(process.env.TUTTI_TEST_PORT || 18983);
const origin = `http://127.0.0.1:${port}`;
const runId = 'r-20990101-000000-0001';
const sleep = ms => new Promise(resolve => setTimeout(resolve, ms));
let browser, server;
mkdirSync(output, { recursive: true });
mkdirSync(join(data, 'tasks'), { recursive: true });
mkdirSync(join(data, 'runs', runId, 'steps'), { recursive: true });
writeFileSync(join(data, 'tasks', 'expanded-a.json'), JSON.stringify({
  id: 'expanded-a', title: 'Expanded layout fixture', goal: 'Expanded conversation layout',
  type: 'code', workdir: data, status: 'done', archived: false, mode: 'manual', engine: 'direct',
  created_at: '2099-01-01 00:00:00',
}));
writeFileSync(join(data, 'runs', runId, 'run.json'), JSON.stringify({
  id: runId, task_id: 'expanded-a', title: 'Expanded layout fixture', kind: 'direct',
  engine: 'direct', status: 'done', steps: [], messages: [],
  created_at: '2099-01-01 00:00:00', started_at: '2099-01-01 00:00:00',
  ended_at: '2099-01-01 00:01:00', cost_usd: 0, tokens: 0, error: '',
}));

try {
  server = spawn('python', ['app/main.py', '--port', String(port)], {
    cwd: root, stdio: 'ignore', windowsHide: true, env: { ...process.env, TUTTI_DATA: data },
  });
  for (let i = 0; i < 60; i++) {
    try { if ((await fetch(origin + '/api/state')).ok) break; } catch {}
    await sleep(250);
  }
  browser = await bootEdge({ width: 1920, height: 1080, awaitPromise: true, reducedMotion: true });
  console.log(browser.envInfo.line);
  const ev = browser.evalJs;
  await browser.send('Page.navigate', { url: origin });
  for (let i = 0; i < 60; i++) {
    if (await ev("typeof S !== 'undefined' && Boolean(S.state?.tasks?.some(t => t.id === 'expanded-a'))")) break;
    await sleep(100);
  }
  await ev("welcomeClose(); sideOpenTask('expanded-a')");
  for (let i = 0; i < 60; i++) {
    if (await ev(`document.getElementById('rd-chat-flow').dataset.runId === '${runId}'`)) break;
    await sleep(100);
  }
  assert.equal(await ev(`document.getElementById('rd-chat-flow').dataset.runId === '${runId}'`), true, 'fixture loaded');
  await ev(`document.getElementById('rd-chat-flow').innerHTML =
    '<div class="chat-row"><div class="chat-bubble agent"><p>Sample conversation output for layout verification.</p></div></div>'.repeat(80);
    renderChatRunStatus({...S.lastRun, status:'running'}, true);`);

  const geometry = () => ev(`(() => {
    const rect = s => { const r = document.querySelector(s).getBoundingClientRect();
      return { x:r.x, y:r.y, right:r.right, bottom:r.bottom, w:r.width, h:r.height }; };
    const detail = document.getElementById('run-detail');
    const flow = document.getElementById('rd-chat-flow');
    const bg = getComputedStyle(detail).backgroundColor;
    return { detail:rect('#run-detail'), flow:rect('#rd-chat-flow'), bar:rect('.rd-chat-bar'),
      status:rect('#rd-chat-run-status'), rail:rect('#rd-railcol'), workspace:rect('#rd-chat-workspace'),
      opaque:bg !== 'rgba(0, 0, 0, 0)' && !bg.endsWith(', 0)'),
      scrollable:flow.scrollHeight > flow.clientHeight,
      overflow:detail.scrollWidth > detail.clientWidth + 1,
      covered:document.elementFromPoint(20,20)?.closest('#run-detail') !== null };
  })()`);

  for (const [width, height, theme = 'light'] of [[1920,1080], [1366,768], [1024,600], [390,844], [1366,768,'dark']]) {
    await browser.send('Emulation.setDeviceMetricsOverride', { width, height, deviceScaleFactor:1, mobile:width < 901 });
    await ev(`document.documentElement.dataset.theme = '${theme}'`);
    for (const workspace of [false, true]) {
      await ev(`document.getElementById('rd-chat').classList.toggle('chat-workspace-open', ${workspace});
        document.getElementById('rd-chat-workspace').classList.toggle('hidden', ${!workspace});
        renderChatRunStatus({...S.lastRun, status:'running'}, true);`);
      const before = await geometry();
      await ev("document.querySelector('[data-chat-run-expand]').click()");
      await sleep(100);
      // The fixture is done, so render the running controls without starting a CLI.
      await ev("renderChatRunStatus({...S.lastRun, status:'running'}, true)");
      const bounds = await geometry();
      console.log(width, height, workspace, JSON.stringify(bounds));
      assert.ok(bounds.detail.x >= 0 && bounds.detail.y >= 0, 'expanded detail stays inside viewport');
      assert.ok(bounds.detail.right <= width && bounds.detail.bottom <= height, 'expanded detail fits viewport');
      assert.ok(bounds.opaque && bounds.covered, 'expanded surface covers underlying sidebar');
      assert.ok(!bounds.overflow, 'no horizontal overflow inside expanded surface');
      assert.ok(bounds.flow.h > 100 && bounds.scrollable, 'timeline retains reading space and scrolls');
      assert.ok(bounds.bar.x >= bounds.detail.x && bounds.bar.right <= bounds.detail.right, 'composer fits expanded surface');
      assert.ok(bounds.bar.bottom <= bounds.detail.bottom, 'composer stays visible');
      assert.ok(bounds.flow.bottom <= bounds.status.y + 1, 'history does not overlap controls');
      if (workspace) assert.ok(bounds.workspace.bottom <= bounds.detail.bottom, 'workspace stays inside expanded surface');
      if (width >= 901) {
        assert.ok(bounds.bar.right <= bounds.rail.x, 'composer does not overlap task rail');
        if (workspace) assert.ok(bounds.bar.right <= bounds.workspace.x || bounds.bar.bottom <= bounds.workspace.y, 'composer does not overlap workspace');
      }
      const shot = await browser.send('Page.captureScreenshot', { format:'png' });
      writeFileSync(join(output, `expanded-${width}-${workspace ? 'workspace' : 'chat'}-${theme}.png`), Buffer.from(shot.result.data, 'base64'));
      await ev("document.querySelector('[data-chat-run-expand]').click(); renderChatRunStatus({...S.lastRun, status:'running'}, true)");
      const after = await geometry();
      assert.deepEqual(after.detail, before.detail, 'collapse restores detail geometry');
      assert.deepEqual(after.bar, before.bar, 'collapse restores composer geometry');
    }
  }
  console.log('PASS expanded conversation geometry and collapse');
} finally {
  if (browser) await browser.close();
  if (server) await new Promise(resolve => {
    const kill = spawn('taskkill', ['/F','/T','/PID',String(server.pid)], { stdio:'ignore', windowsHide:true });
    kill.on('exit', resolve);
  });
  rmSync(data, { recursive:true, force:true });
}
