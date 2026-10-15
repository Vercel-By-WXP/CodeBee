/* Real snapshot fixtures; no CLI calls. Project filtering, attention, results,
 * safe text rendering, navigation, draft retention and responsive geometry. */
import assert from 'node:assert/strict';
import { spawn } from 'node:child_process';
import { mkdtempSync, mkdirSync, writeFileSync, rmSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import { fileURLToPath } from 'node:url';
import { bootEdge } from './_ui_boot.mjs';

const root = fileURLToPath(new URL('../', import.meta.url));
const data = mkdtempSync(join(tmpdir(), 'codebee-project-overview-'));
const port = Number(process.env.TUTTI_TEST_PORT || 18992);
const origin = `http://127.0.0.1:${port}`;
const output = join(root, 'output', 'project-overview');
const sleep = ms => new Promise(resolve => setTimeout(resolve, ms));
const paths = [join(data, 'Atlas'), join(data, 'Empty'), join(data, 'Beacon')];
paths.forEach(path => mkdirSync(path));
mkdirSync(join(data, 'tasks'));
mkdirSync(output, { recursive: true });
for (const [id, workdir, git_state] of [['atlas', paths[0], 'isolated'], ['empty', paths[1], ''], ['beacon', paths[2], '']]) {
  writeFileSync(join(data, 'tasks', id + '.json'), JSON.stringify({
    id, workdir, git_state, title: id + ' 布局验收示例', type: 'code', goal: 'UI fixture',
    status: 'done', archived: false, created_at: '2099-01-01 00:00:00',
  }));
}
const fixtures = [
  { n: 1, task_id: 'atlas', status: 'done', summary: '真实摘要：已完成导航与布局验收 <img src=x onerror=alert(1)>', verdict: { pass: true } },
  { n: 2, task_id: 'beacon', status: 'done', summary: 'Beacon 专属结果', verdict: { pass: false } },
  { n: 3, task_id: 'atlas', status: 'failed', error: '布局验收失败示例' },
];
for (const fixture of fixtures) {
  const id = `r-20990101-00000${fixture.n}-0001`;
  mkdirSync(join(data, 'runs', id), { recursive: true });
  writeFileSync(join(data, 'runs', id, 'run.json'), JSON.stringify({
    id, title: fixture.task_id + ' 布局验收示例 ' + '长标题'.repeat(24), kind: 'direct', engine: 'direct',
    steps: [], messages: [], created_at: `2099-01-01 00:00:0${fixture.n}`,
    started_at: '2099-01-01 00:00:00', ended_at: '2099-01-01 00:01:00', ...fixture,
  }));
}
let browser, server;
let checks = 0;
const check = (name, value) => { assert.ok(value, name); console.log('PASS ' + name); checks++; };
try {
  server = spawn('python', ['-X', 'utf8', 'app/main.py', '--port', String(port), '--no-browser'], {
    cwd: root, stdio: 'ignore', windowsHide: true, env: { ...process.env, TUTTI_DATA: data },
  });
  let ready = false;
  for (let i = 0; i < 80 && !ready; i++) {
    try { ready = (await fetch(origin + '/api/state')).ok; } catch {}
    if (!ready) await sleep(250);
  }
  check('isolated server', ready);
  browser = await bootEdge({ width: 1440, height: 1000, awaitPromise: true, reducedMotion: false });
  console.log(browser.envInfo.line);
  const { send, evalJs: ev } = browser;
  const errors = [];
  await send('Runtime.enable');
  browser.ws.addEventListener('message', event => {
    const message = JSON.parse(event.data);
    if (message.method === 'Runtime.exceptionThrown') errors.push(message.params.exceptionDetails.text);
  });
  await send('Page.addScriptToEvaluateOnNewDocument', { source: 'localStorage.setItem("orch.welcomed","1")' });
  await send('Page.navigate', { url: origin });
  let loaded = false;
  for (let i = 0; i < 80 && !loaded; i++) {
    loaded = await ev('typeof S !== "undefined" && S.state?.runs?.length === 3 && S.flows?.length > 0');
    if (!loaded) await sleep(150);
  }
  check('server snapshot loaded', loaded);
  const resize = (width, height) => send('Emulation.setDeviceMetricsOverride', { width, height, deviceScaleFactor: 1, mobile: width <= 900 });
  const shot = async name => {
    const image = await send('Page.captureScreenshot', { format: 'png' });
    writeFileSync(join(output, name + '.png'), Buffer.from(image.result.data, 'base64'));
  };
  const choose = path => ev(`(() => { const select=$('ov-project-select'); select.value=${JSON.stringify(path.replace(/\\/g, '/').toLowerCase())}; select.dispatchEvent(new Event('change')); return select.value; })()`);
  await ev(`welcomeClose(); exitSettings(); $('f-goal').value='保留的草稿'; pickWorkspaceMode('develop')`);
  for (const [width, height] of [[1920, 1000], [1440, 900], [1043, 300]]) {
    await resize(width, height); await sleep(250);
    if (height > 500) check('visible shortcuts ' + width, await ev("$('cmp-quick').getBoundingClientRect().height > 0"));
    await shot('home-' + width);
    if (height === 300) {
      await ev("$('f-advanced').open = true");
      check('advanced options scroll inside tiny viewport', await ev("(() => { const e=document.querySelector('#f-advanced .adv-body'), r=e.getBoundingClientRect(); return r.top >= 56 && r.bottom <= innerHeight && e.scrollHeight > e.clientHeight; })()"));
      await ev("$('f-advanced').open = false");
    }
  }
  await resize(1920, 1000);
  await ev("switchTab('overview')"); await sleep(350);
  check('all project options', await ev("$('ov-project-select').options.length === 4"));
  await choose(paths[0]);
  check('recent runs filtered', await ev("$('ov-recent-body').querySelectorAll('[data-project-run]').length === 2 && !$('ov-recent-body').textContent.includes('beacon')"));
  check('actual safe summary', await ev("$('ov-project-results').textContent.includes('真实摘要') && !$('ov-project-results').querySelector('img')"));
  check('attention groups deduplicated', await ev("document.querySelector('#ov-project-status button b').textContent === '1'"));
  await ev("document.querySelector('#ov-project-status button').click()" );
  check('attention opens latest failure and gate', await ev("$('ov-recent-body').textContent.includes('待裁决') && $('ov-recent-body').querySelectorAll('[data-project-run]').length === 1"));
  await ev("document.querySelector('#ov-project-status button').focus(); ovMaybeRefresh()");
  check('attention filter and focus survive unchanged snapshot', await ev("document.querySelector('#ov-project-status button').getAttribute('aria-pressed') === 'true' && document.activeElement === document.querySelector('#ov-project-status button')"));
  await choose(paths[2]);
  check('unqualified run labelled', await ev("$('ov-project-results').textContent.includes('未达标') && document.querySelector('#ov-project-results .project-badge.warn') !== null"));
  await choose(paths[1]);
  check('empty project states', await ev("$('ov-recent-body').textContent.includes('暂无运行记录') && $('ov-project-results').textContent.includes('运行完成后')"));
  await choose(paths[0]);
  await ev("setLangBtn('en')");
  check('language refresh', await ev("$('ov-project-results').textContent.includes('Result summaries') && $('ov-recent-body').textContent.includes('Started')"));
  await ev("setLangBtn('zh')");
  await ev(`S.state.runs.find(run => run.task_id === 'atlas' && run.status === 'done').summary = '布局验收示例：已统一导航、输入区和项目状态，保留明暗主题与手机布局。'; ovMaybeRefresh()`);
  check('changed snapshot refreshes real summary', await ev("$('ov-project-results').textContent.includes('已统一导航')"));
  check('project changes leave draft intact', await ev("$('f-goal').value === '保留的草稿'"));
  for (const [width, height, theme, skin] of [[1920,1000,'light','classic'],[1440,900,'dark','classic'],[1440,900,'light','ocean'],[390,844,'light','classic']]) {
    await resize(width, height);
    await ev(`document.documentElement.dataset.theme='${theme}'; document.documentElement.dataset.skin='${skin}'; document.querySelector('.project-overview').scrollIntoView({block:'start'})`);
    await sleep(200);
    check('no page overflow ' + [width,theme,skin].join('/'), await ev('document.documentElement.scrollWidth <= innerWidth'));
    check('project fits ' + width, await ev("document.querySelector('.project-overview').getBoundingClientRect().right <= innerWidth"));
    await shot('project-' + [width,theme,skin].join('-'));
  }
  await resize(1440, 900);
  await ev("document.querySelector('#ov-project-results [data-project-run]').click()");
  await sleep(300);
  check('result navigates to correct run', await ev("S.tab === 'runs' && S.detailRunId === 'r-20990101-000001-0001'"));
  await ev('exitSettings()');
  check('new-task draft retained', await ev("$('f-goal').value === '保留的草稿'"));
  check('no runtime exceptions', errors.length === 0);
  console.log(`PASS ${checks} project overview checks`);
} finally {
  if (browser) await browser.close();
  if (server && server.exitCode === null) {
    const stopped = new Promise(resolve => server.once('exit', resolve)); server.kill(); await stopped;
  }
  rmSync(data, { recursive: true, force: true });
}
