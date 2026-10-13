/* Running chat workspace integration: live CLI output, adjacent editor, file
 * save/conflict handling, and the narrow-screen stacked layout. */
import assert from 'node:assert/strict';
import { spawn } from 'node:child_process';
import { mkdtempSync, mkdirSync, writeFileSync, readFileSync, utimesSync, rmSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import { fileURLToPath } from 'node:url';
import { bootEdge } from '../../tests/_ui_boot.mjs';

const root = fileURLToPath(new URL('../../', import.meta.url));
const data = mkdtempSync(join(tmpdir(), 'codebee-chat-workspace-'));
const port = Number(process.env.TUTTI_TEST_PORT || 19173);
const origin = `http://127.0.0.1:${port}`;
const sleep = ms => new Promise(resolve => setTimeout(resolve, ms));
const workdir = join(data, 'CodeBee');
const logPath = join(data, 'runs', 'r-chat', 'steps', '01-chat.log');
const runFile = join(data, 'runs', 'r-chat', 'run.json');
let server, browser;

mkdirSync(join(data, 'tasks'), { recursive: true });
mkdirSync(join(data, 'runs', 'r-chat', 'steps'), { recursive: true });
mkdirSync(join(workdir, 'src'), { recursive: true });
writeFileSync(join(workdir, 'README.md'), 'hello workspace\n');
writeFileSync(join(workdir, 'src', 'main.py'), 'print("ok")\n');
writeFileSync(join(data, 'tasks', 't-chat.json'), JSON.stringify({
  id: 't-chat', title: 'Chat workspace', type: 'code', goal: '显示执行过程', workdir,
  status: 'running', archived: false, mode: 'manual', engine: 'direct',
  created_at: '2099-01-01 00:00:00',
}));
const fixtureRun = {
  id: 'r-chat', task_id: 't-chat', title: 'Chat workspace', kind: 'direct', engine: 'direct',
  status: 'failed', steps: [{ n: 1, role: 'implementer', agent: 'claude-code',
    agent_label: 'Claude Code', status: 'cancelled', started_at: '2099-01-01 00:00:01',
    log: 'steps/01-chat.log', thinking: '', stream: '',
    activity: ['run_command: Read workspace files'] }],
  messages: [], created_at: '2099-01-01 00:00:00', started_at: '2099-01-01 00:00:01',
};
writeFileSync(runFile, JSON.stringify(fixtureRun));
writeFileSync(logPath, '$ Read workspace files\ninitial log line\n');

try {
  server = spawn('python', ['app/main.py', '--port', String(port)], {
    cwd: root, stdio: 'ignore', windowsHide: true, env: { ...process.env, TUTTI_DATA: data },
  });
  for (let i = 0; i < 60; i++) {
    try { if ((await fetch(origin + '/api/state')).ok) break; } catch {}
    await sleep(150);
  }
  browser = await bootEdge({ width: 1600, height: 1000, awaitPromise: true, reducedMotion: true });
  console.log(browser.envInfo.line);
  await browser.send('Page.navigate', { url: origin });
  await sleep(1600);
  const ev = browser.evalJs;
  for (let i = 0; i < 40; i++) {
    if (await ev("Boolean(S.state?.tasks?.some(t=>t.id==='t-chat'))")) break;
    await sleep(100);
  }
  assert.equal(await ev("Boolean(S.state?.tasks?.some(t=>t.id==='t-chat'))"), true, 'fixture loaded');
  await ev("sideOpenTask('t-chat')");
  await sleep(800);
  await ev("S.rdTab='chat'; applyRdTabs()");
  for (let i = 0; i < 10; i++) {
    await ev("if(!chatWorkspaceState.open) chatWorkspaceToggle()");
    await sleep(250);
    if (await ev("chatWorkspaceState.open && document.querySelector('#rd-chat-workspace-files [data-chat-workspace-file]')")) break;
  }
  await sleep(700);
  for (let i = 0; i < 30; i++) {
    if ((await ev("document.querySelector('#rd-chat-flow .chat-cli-log-text')?.textContent||''")).includes('initial log line')) break;
    await sleep(150);
  }
  const initial = await ev(`(()=>{const c=document.querySelector('#rd-chat-flow .chat-cli-log-text');
    const f=document.querySelector('#rd-chat-flow').getBoundingClientRect();
    const w=document.querySelector('#rd-chat-workspace').getBoundingClientRect();
    const b=document.querySelector('#rd-chat').getBoundingClientRect();
    return {open:document.querySelector('#rd-chat').classList.contains('chat-workspace-open'),
      files:document.querySelectorAll('#rd-chat-workspace-files [data-chat-workspace-file]').length,
      log:c?.textContent||'', width:w.width, chat:b.width, right:w.right, boxRight:b.right}})()`);
  assert.equal(initial.open, true, 'workspace opens beside conversation');
  assert.equal(initial.files, 2, 'file tree lists workspace files');
  assert.match(initial.log, /initial log line/, 'CLI output is loaded into the timeline');
  assert.ok(initial.width > 400 && initial.width < initial.chat, 'editor gets a substantial share without overflowing its parent');
  assert.ok(initial.right <= initial.boxRight + 1, 'workspace stays inside chat pane');
  // Let startup recovery finish first, then switch the fixture to a live run.
  // A CLI log file can grow without any timeline field changing; this exercises
  // the live poll's log refresh independently of the timeline render signature.
  fixtureRun.status = 'running';
  fixtureRun.steps[0].status = 'running';
  writeFileSync(runFile, JSON.stringify(fixtureRun));
  await ev("S.lastRun.status='running'; S.lastRun.steps[0].status='running'; chatLiveSig=''; drawChatFlow(S.lastRun,{items:chatTimelineItems},true)");

  await ev("document.querySelector('[data-chat-workspace-file=\"README.md\"]').click()");
  await sleep(300);
  assert.match(await ev("document.getElementById('rd-chat-editor-text').value"), /hello workspace/, 'file opens in editor');
  await ev("document.getElementById('rd-chat-editor-text').value='saved from chat editor\\n'; document.getElementById('rd-chat-editor-text').dispatchEvent(new Event('input',{bubbles:true})); document.querySelector('[data-chat-editor-save]').click()");
  await sleep(400);
  assert.equal(readFileSync(join(workdir, 'README.md'), 'utf8'), 'saved from chat editor\n', 'save writes the edited contents');
  assert.equal(await ev("document.getElementById('rd-chat-editor-state').textContent"), '已保存', 'editor confirms save');

  const file = join(workdir, 'README.md');
  const changed = new Date(Date.now() + 5000);
  writeFileSync(file, 'changed externally\n');
  utimesSync(file, changed, changed);
  await ev("document.getElementById('rd-chat-editor-text').value='must not overwrite\\n'; document.getElementById('rd-chat-editor-text').dispatchEvent(new Event('input',{bubbles:true})); document.querySelector('[data-chat-editor-save]').click()");
  await sleep(400);
  assert.equal(await ev("document.getElementById('rd-chat-editor-state').textContent"), '文件已被外部修改，请重新打开后再保存', 'mtime conflict is surfaced');
  assert.equal(readFileSync(file, 'utf8'), 'changed externally\n', 'conflict does not overwrite external changes');

  writeFileSync(logPath, '$ Read workspace files\ninitial log line\nsecond live line\n');
  await ev("chatLogFetchedAt.delete(chatLogKey('r-chat','steps/01-chat.log')); drawChatFlow(S.lastRun,{items:chatTimelineItems},true)");
  await sleep(700);
  const refreshedLog = await ev("document.querySelector('#rd-chat-flow .chat-cli-log-text')?.textContent||''");
  assert.match(refreshedLog, /second live line/, 'running CLI output refreshes during timeline polling');

  await browser.send('Emulation.setDeviceMetricsOverride', { width: 820, height: 1000, deviceScaleFactor: 1, mobile: false });
  await sleep(150);
  const narrow = await ev(`(()=>{const flow=document.querySelector('#rd-chat-flow').getBoundingClientRect();
    const workspace=document.querySelector('#rd-chat-workspace').getBoundingClientRect();
    return {doc:document.documentElement.scrollWidth, viewport:innerWidth, flowY:flow.y, workspaceY:workspace.y}})()`);
  assert.ok(narrow.doc <= narrow.viewport + 1, 'narrow layout has no horizontal overflow');
  assert.ok(narrow.workspaceY > narrow.flowY, 'workspace stacks below the conversation on narrow screens');
  console.log('PASS chat workspace runtime', JSON.stringify({ initial, narrow }));
} finally {
  if (browser) await browser.close();
  if (server) await new Promise(resolve => {
    const killer = spawn('taskkill', ['/F', '/T', '/PID', String(server.pid)], { stdio: 'ignore', windowsHide: true });
    killer.on('exit', resolve);
  });
  rmSync(data, { recursive: true, force: true });
}
