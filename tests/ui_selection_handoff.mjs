/* Isolated browser journey; no AI jobs, real user directories, or accounts. */
import assert from 'node:assert/strict';
import { spawn } from 'node:child_process';
import { mkdtempSync, mkdirSync, writeFileSync, rmSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { join, resolve, sep } from 'node:path';
import { fileURLToPath } from 'node:url';
import { createServer } from 'node:net';
import { bootEdge } from './_ui_boot.mjs';

const root = fileURLToPath(new URL('../', import.meta.url));
const data = mkdtempSync(join(tmpdir(), 'codebee-selection-'));
const probe = createServer();
await new Promise(resolve => probe.listen(0, '127.0.0.1', resolve));
const port = probe.address().port;
await new Promise(resolve => probe.close(resolve));
const origin = `http://127.0.0.1:${port}`;
const sleep = ms => new Promise(resolve => setTimeout(resolve, ms));
let server, browser;
for (const [task, run, type] of [['t-scan', 'r-scan', 'rank_scan'], ['t-book', 'r-book', 'serial_novel']]) {
  mkdirSync(join(data, 'tasks'), { recursive: true });
  mkdirSync(join(data, 'runs', run), { recursive: true });
  writeFileSync(join(data, 'tasks', task + '.json'), JSON.stringify({
    id: task, title: task, type, engine: 'review', goal: task, workdir: data,
    status: 'done', created_at: '2026-10-15 10:00:00',
    ...(type === 'serial_novel' ? { serial: { chapters: 8, words_per_chapter: 2500 } } : {}),
  }));
  writeFileSync(join(data, 'runs', run, 'run.json'), JSON.stringify({
    id: run, task_id: task, title: task, kind: 'orchestration', status: 'done',
    steps: [], messages: [], created_at: '2026-10-15 10:00:00',
  }));
}
try {
  server = spawn('python', ['-X', 'utf8', 'app/main.py', '--port', String(port), '--no-browser'], {
    cwd: root, windowsHide: true, stdio: 'ignore',
    env: { ...process.env, TUTTI_DATA: data, TUTTI_ISOLATE_HOME: '1', TUTTI_CLASSROOM_SYNC: '0' },
  });
  let ready = false;
  for (let i = 0; i < 100; i++) {
    try { if ((await fetch(origin + '/api/state')).ok) { ready = true; break; } } catch {}
    await sleep(200);
  }
  assert.ok(ready, 'Isolated server starts');
  browser = await bootEdge({ width: 1440, height: 950, reducedMotion: true, awaitPromise: true });
  const { send, evalJs: ev } = browser;
  console.log(browser.envInfo.line);
  const waitFor = async expression => {
    for (let i = 0; i < 100; i++) { if (await ev(expression)) return true; await sleep(100); }
    return false;
  };
  await send('Page.enable');
  await send('Page.addScriptToEvaluateOnNewDocument', { source: 'localStorage.setItem("orch.welcomed","1");' });
  await send('Page.navigate', { url: origin });
  assert.ok(await waitFor('typeof S!=="undefined" && S.state?.tasks?.length===2'), 'App ready');
  await ev(`
    window.launchCalls=[]; window.selectionApi=api;
    api=async (url, opts={})=>{
      if (url.endsWith('/selection')) return {run_id:'r-scan',parent_dir:'E:/Books',candidates:[
        {id:'c1',title:'借命修城',platform:'七猫',premise:'救人会付出记忆代价'},
        {id:'c2',title:'第二本书',platform:'番茄',premise:'第二种机制'}]};
      if (url.endsWith('/selection/serial')) {
        launchCalls.push(JSON.parse(opts.body));
        await new Promise(r=>setTimeout(r,100));
        return {task_id:'t-book',run_id:'r-book'};
      }
      return selectionApi(url,opts);
    };
    sideOpenTask('t-scan');
  `);
  assert.ok(await waitFor('S.detailTaskKey==="t-scan" && !$("btn-selection-serial").classList.contains("hidden")'), 'Scan action visible');
  await ev('$("btn-selection-serial").click()');
  assert.ok(await waitFor('!!$("sel-book")'), 'Proposal chooser opens');
  assert.ok(await ev('$("sel-path").textContent==="E:/Books/借命修城"'), 'Book title folder preview');
  await ev('$("sel-book").value="c2"; $("sel-book").dispatchEvent(new Event("change"))');
  assert.ok(await ev('$("sel-path").textContent.endsWith("/第二本书") && $("sel-premise").textContent==="第二种机制"'), 'Selection updates brief and folder');
  await ev('$("sel-chapters").value="21"; $("sel-start").click()');
  assert.ok(await ev('launchCalls.length===0 && !!$("sel-error").textContent'), 'Invalid chapter count blocked');
  await ev('$("sel-chapters").value="10"; $("sel-words").value="3000"; $("sel-start").click(); $("sel-start").click()');
  const opened = await waitFor('S.detailRunId==="r-book" && chatRunId==="r-book"');
  assert.ok(opened, 'New serial opens: ' + JSON.stringify(await ev('({task:S.detailTaskKey,run:S.detailRunId,chat:chatRunId,calls:launchCalls,error:$("sel-error")?.textContent})')));
  assert.deepEqual(await ev('launchCalls'), [{run_id:'r-scan',candidate_id:'c2',chapters:10,words_per_chapter:3000}]);
  assert.ok(await ev('$("btn-selection-serial").classList.contains("hidden")'), 'Scan action does not leak into serial');
  console.log('Selection → proposal → validation → single launch → child navigation passed.');
} finally {
  if (browser) await browser.close();
  if (server) { server.kill(); await sleep(500); }
  const safeTempRoot = resolve(tmpdir()) + sep;
  if (!resolve(data).startsWith(safeTempRoot) || !data.includes('codebee-selection-')) throw new Error('Unexpected test directory');
  rmSync(data, { recursive: true, force: true });
}
