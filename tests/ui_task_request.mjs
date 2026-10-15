/* Task-creation replies must leave the parent conversation and open the child.
 * Isolated fixture data; the API reply is stubbed and no AI job is launched. */
import assert from 'node:assert/strict';
import { spawn } from 'node:child_process';
import { mkdtempSync, mkdirSync, writeFileSync, rmSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import { fileURLToPath } from 'node:url';
import { createServer } from 'node:net';
import { bootEdge } from './_ui_boot.mjs';

const root = fileURLToPath(new URL('../', import.meta.url));
const data = mkdtempSync(join(tmpdir(), 'codebee-task-request-'));
const probe = createServer();
await new Promise(resolve => probe.listen(0, '127.0.0.1', resolve));
const port = probe.address().port;
await new Promise(resolve => probe.close(resolve));
const origin = `http://127.0.0.1:${port}`;
const sleep = ms => new Promise(resolve => setTimeout(resolve, ms));
let server, browser;
for (const [task, run, title, type] of [
  ['t-source', 'r-source', '原对话', 'direct'],
  ['t-child', 'r-child', '独立连载任务', 'serial_novel'],
]) {
  mkdirSync(join(data, 'tasks'), { recursive: true });
  mkdirSync(join(data, 'runs', run), { recursive: true });
  writeFileSync(join(data, 'tasks', task + '.json'), JSON.stringify({
    id: task, title, type, engine: type === 'direct' ? 'direct' : 'review',
    goal: title, workdir: data, status: 'done', created_at: '2026-10-15 10:00:00',
    ...(type === 'serial_novel' ? { serial: { chapters: 10, words_per_chapter: 2500 } } : {}),
  }));
  writeFileSync(join(data, 'runs', run, 'run.json'), JSON.stringify({
    id: run, task_id: task, kind: 'orchestration', title, status: 'done',
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
  assert.ok(await waitFor('typeof S!=="undefined" && S.state?.tasks?.length===2'), 'App is ready');
  await ev('sideOpenTask("t-source")');
  assert.ok(await waitFor('chatRunId==="r-source" && S.detailTaskKey==="t-source"'), 'Source task conversation opens');
  await ev(`
    window.taskRequestCalls=[]; window.taskRequestApi=api;
    api=async (url, options)=>{
      if(url.endsWith('/chat')) {
        taskRequestCalls.push(JSON.parse(options.body));
        return {ok:true, task_id:'t-child', run_id:'r-child', created_task:true, delivery:'sent'};
      }
      return taskRequestApi(url, options);
    };
    $('rd-chat-input').value='请帮我新建一个 CodeBee 任务，写连载小说前十章，要过七猫和番茄';
    syncChatSendControls();
  `);
  await ev('chatSend()');
  assert.ok(await waitFor('chatRunId==="r-child" && S.lastRun?.task_id==="t-child"'), 'New task reply navigates to the child');
  assert.ok(await ev('taskRequestCalls.length===1 && taskRequestCalls[0].text.includes("前十章")'), 'Original user request is submitted once');
  assert.ok(await ev('$("rd-chat-input").value==="" && !chatSending'), 'Submitted draft clears and sending state resets');
  assert.ok(await ev('document.body.textContent.includes("已新建 CodeBee 任务，开始执行")'), 'Creation confirmation appears');
  console.log('Task creation UI checks passed; no AI job was launched.');
} finally {
  if (browser) await browser.close();
  if (server) { server.kill(); await sleep(500); }
  // mkdtempSync created this exact directory for this test run.
  rmSync(data, { recursive: true, force: true });
}
