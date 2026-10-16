/* 一次性探针：复现 ui_project_overview「recent runs filtered」红——
 * 对账 ov-project-select 选项值（路径键 vs 哈希 id）与按路径选择的行为 */
import { spawn } from 'node:child_process';
import { mkdtempSync, mkdirSync, writeFileSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import { fileURLToPath } from 'node:url';
import { bootEdge } from './_ui_boot.mjs';

const root = fileURLToPath(new URL('../', import.meta.url));
const data = mkdtempSync(join(tmpdir(), 'codebee-ov-probe-'));
const port = 18795;
const origin = `http://127.0.0.1:${port}`;
const sleep = ms => new Promise(resolve => setTimeout(resolve, ms));
const paths = [join(data, 'Atlas'), join(data, 'Empty'), join(data, 'Beacon')];
paths.forEach(path => mkdirSync(path));
mkdirSync(join(data, 'tasks'));
mkdirSync(join(data, 'runs'), { recursive: true });
for (const [id, workdir] of [['atlas', paths[0]], ['empty', paths[1]], ['beacon', paths[2]]]) {
  writeFileSync(join(data, 'tasks', id + '.json'), JSON.stringify({
    id, workdir, git_state: 'isolated', title: id + ' 布局验收示例', type: 'code', goal: 'UI fixture',
    status: 'done', archived: false, created_at: '2099-01-01 00:00:00',
  }));
}
for (const [n, task_id] of [[1, 'atlas'], [2, 'beacon'], [3, 'atlas']]) {
  const id = `r-20990101-00000${n}-0001`;
  mkdirSync(join(data, 'runs', id), { recursive: true });
  writeFileSync(join(data, 'runs', id, 'run.json'), JSON.stringify({
    id, title: task_id + ' 示例', kind: 'direct', engine: 'direct', task_id,
    steps: [], messages: [], created_at: `2099-01-01 00:00:0${n}`,
    started_at: '2099-01-01 00:00:00', ended_at: '2099-01-01 00:01:00',
    status: 'done', summary: '摘要' + n,
  }));
}

let browser, server;
try {
  server = spawn('python', ['-X', 'utf8', 'app/main.py', '--port', String(port), '--no-browser'], {
    cwd: root, stdio: 'ignore', windowsHide: true, env: { ...process.env, TUTTI_DATA: data },
  });
  let ready = false;
  for (let i = 0; i < 80 && !ready; i++) { try { ready = (await fetch(origin + '/api/state')).ok; } catch {} if (!ready) await sleep(250); }
  console.log('service up:', ready);
  browser = await bootEdge({ width: 1440, height: 1000, reducedMotion: false });
  const { send, evalJs: ev } = browser;
  await send('Runtime.enable');
  await send('Page.addScriptToEvaluateOnNewDocument', { source: 'localStorage.setItem("orch.welcomed","1")' });
  await send('Page.navigate', { url: origin });
  for (let i = 0, loaded = false; i < 80 && !loaded; i++) { loaded = await ev('typeof S !== "undefined" && S.state?.runs?.length === 3'); if (!loaded) await sleep(150); }
  await ev(`welcomeClose(); exitSettings()`);
  await sleep(300);

  console.log('api/projects:', await ev(`fetch('/api/projects').then(r => r.json()).then(d => JSON.stringify(d.projects.map(p => ({ id: p.id, workdir: p.workdir }))))`));

  await ev(`switchTab('overview')`); await sleep(350);
  console.log('options:', await ev(`JSON.stringify([...$('ov-project-select').options].map(o => ({ value: o.value.slice(0, 40), label: o.textContent.slice(0, 60) })))`));

  // 复刻测试 choose()：按小写路径设值
  console.log('choose-by-path:', await ev(`(() => {
    const want = ${JSON.stringify(paths[0].replace(/\\\\/g, '/').toLowerCase())};
    const select = $('ov-project-select');
    select.value = want;
    select.dispatchEvent(new Event('change'));
    return JSON.stringify({ want, actual: select.value, rows: $('ov-recent-body').querySelectorAll('[data-project-run]').length, hasBeacon: $('ov-recent-body').textContent.includes('beacon') });
  })()`));

  // 修复后行为：按 label 含原始路径选
  console.log('choose-by-label:', await ev(`(() => {
    const want = ${JSON.stringify(paths[0])};
    const select = $('ov-project-select');
    const opt = [...select.options].find(o => o.value && o.textContent.includes(want));
    if (!opt) return 'no-option';
    select.value = opt.value;
    select.dispatchEvent(new Event('change'));
    return JSON.stringify({ picked: opt.value.slice(0, 40), rows: $('ov-recent-body').querySelectorAll('[data-project-run]').length, hasBeacon: $('ov-recent-body').textContent.includes('beacon') });
  })()`));
  await browser.close();
} finally {
  try { if (browser) await browser.close(); } catch {}
  if (server) { try { spawn('taskkill', ['/F', '/T', '/PID', String(server.pid)], { stdio: 'ignore' }); } catch {} }
}
process.exit(0);
