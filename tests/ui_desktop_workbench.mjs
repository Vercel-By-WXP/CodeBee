/* Desktop layout regression: isolated data, no CLI execution. Screenshots use
 * labelled sample messages to exercise long conversations and scrolling. */
import assert from 'node:assert/strict';
import { spawn } from 'node:child_process';
import { mkdtempSync, mkdirSync, writeFileSync, rmSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import { fileURLToPath } from 'node:url';
import { bootEdge } from './_ui_boot.mjs';

const root = fileURLToPath(new URL('../', import.meta.url));
const data = mkdtempSync(join(tmpdir(), 'codebee-desktop-layout-'));
const runId = 'r-20990101-000000-0001';
const output = join(root, 'output', 'desktop-workbench');
const port = Number(process.env.TUTTI_TEST_PORT || 18973);
const origin = `http://127.0.0.1:${port}`;
const sleep = ms => new Promise(resolve => setTimeout(resolve, ms));
let browser, server;
mkdirSync(output, { recursive: true });
mkdirSync(join(data, 'tasks'), { recursive: true });
mkdirSync(join(data, 'runs', runId, 'steps'), { recursive: true });
const workdir = join(data, 'CodeBee');
mkdirSync(workdir);
writeFileSync(join(data, 'tasks', 'layout-a.json'), JSON.stringify({
  id: 'layout-a', title: '桌面工作台 · 样式与布局优化', type: 'code',
  goal: '参考 Codex、ZCode 和 QCoder，优化桌面工作台的布局与阅读体验。',
  workdir, status: 'done', archived: false, mode: 'manual', engine: 'direct',
  created_at: '2099-01-01 00:00:00',
}));
writeFileSync(join(data, 'runs', runId, 'run.json'), JSON.stringify({
  id: runId, task_id: 'layout-a', title: '桌面工作台 · 样式与布局优化', kind: 'direct',
  engine: 'direct', status: 'done', steps: [], messages: [],
  created_at: '2099-01-01 00:00:00', started_at: '2099-01-01 00:00:00',
  ended_at: '2099-01-01 00:01:00', cost_usd: 0, tokens: 0, error: '',
  summary: '已完成工作台布局优化：左侧统一项目导航，中间聚焦对话，右侧展示任务进度和产物。',
}));
try {
  server = spawn('python', ['app/main.py', '--port', String(port)], {
    cwd: root, stdio: 'ignore', windowsHide: true, env: { ...process.env, TUTTI_DATA: data },
  });
  for (let i = 0; i < 60; i++) {
    try { if ((await fetch(origin + '/api/state')).ok) break; } catch {}
    await sleep(250);
  }
  browser = await bootEdge({ width: 1600, height: 1000, awaitPromise: true, reducedMotion: true });
  console.log(browser.envInfo.line);
  const errors = [];
  browser.ws.addEventListener('message', event => {
    const message = JSON.parse(event.data);
    if (message.method === 'Runtime.exceptionThrown') errors.push(message.params.exceptionDetails.text);
  });
  await browser.send('Runtime.enable');
  await browser.send('Emulation.setDeviceMetricsOverride', { width: 1600, height: 1000, deviceScaleFactor: 1, mobile: false });
  await browser.send('Page.navigate', { url: origin });
  await sleep(1800);
  const ev = browser.evalJs;
  for (let i = 0; i < 30; i++) {
    if (await ev("Boolean(S.state?.tasks?.some(t => t.id === 'layout-a'))")) break;
    await sleep(250);
  }
  assert.ok(await ev("Boolean(S.state?.tasks?.some(t => t.id === 'layout-a'))"), 'fixture loaded');
  await ev("welcomeClose()");
  const shell = await ev(`(() => {
    const rail = document.querySelector('.side-rail').getBoundingClientRect();
    const sidebar = document.getElementById('sidebar').getBoundingClientRect();
    const root = getComputedStyle(document.documentElement);
    return { rail: Math.round(rail.width), sidebar: Math.round(sidebar.width), bg: root.getPropertyValue('--cb-canvas').trim(),
      detail: getComputedStyle(document.getElementById('run-detail')).borderRadius };
  })()`);
  assert.equal(shell.rail, 46, 'Codex shell keeps a 46px navigation rail');
  assert.equal(shell.sidebar, 264, 'Codex shell keeps a 264px task sidebar');
  assert.equal(shell.bg, '#ffffff', 'light desktop canvas uses a clean white surface');
  assert.notEqual(shell.detail, '0px', 'run detail uses a rounded card edge');
  console.log('Codex shell', JSON.stringify(shell));
  const capture = async name => {
    const shot = await browser.send('Page.captureScreenshot', { format: 'png' });
    writeFileSync(join(output, name + '.png'), Buffer.from(shot.result.data, 'base64'));
  };
  await capture('home');
  await ev("sideOpenTask('layout-a')");
  await sleep(900);
  await ev("rdChatNavBack()");
  // Wait for the initial async timeline before adding the long-message fixture;
  // otherwise a delayed response can replace the fixture mid-assertion.
  for (let i = 0; i < 50; i++) {
    if (await ev(`document.getElementById('rd-chat-flow').dataset.runId === '${runId}'`)) break;
    await sleep(100);
  }
  console.log('detail', await ev("({hidden:document.getElementById('run-detail').className, kind:S.lastRun?.kind,engine:S.lastRun?.engine,main:document.querySelector('main').className})"));
  // Long content tests the real timeline's scroll chain without invoking an AI CLI.
  await ev(`document.getElementById('rd-chat-flow').innerHTML =
    '<div class="chat-meta">示例数据 · 桌面布局验收</div><div class="chat-row me"><div class="chat-bubble me">参考 Codex、ZCode 和 QCoder，优化桌面版样式和布局。</div></div>' +
    '<div class="chat-row"><div class="chat-avatar"><svg class="ico"><use href="#i-baton"></use></svg></div><div class="chat-bubble agent"><div class="chat-body chat-markdown"><p>已整理三款桌面应用的布局特点，采用简洁、清晰的工作台结构。</p><ul><li>左侧：项目和聊天统一导航，突出当前任务。</li><li>中间：对话列居中，控制阅读宽度，底部保留输入框。</li><li>右侧：任务状态、进度和成果按需查看。</li></ul><p>输入框和对话共用一条阅读轴，浅色与深色主题使用一致的间距与层级。</p></div><div class="chat-meta">CodeBee · 已完成</div></div></div>'.repeat(8);
    document.getElementById('rd-chat-flow').scrollTop=0;`);
  await capture('chat-light');
  {
    for (const width of [1920, 1600, 1440, 1366, 1024, 920]) {
      await browser.send('Emulation.setDeviceMetricsOverride', { width, height: 900, deviceScaleFactor: 1, mobile: false });
      await sleep(120);
      const bounds = await ev(`(() => {
        const rect = s => {const r=document.querySelector(s).getBoundingClientRect();return {x:r.x,y:r.y,w:r.width,h:r.height,right:r.right,bottom:r.bottom}};
        return {viewport:innerWidth, doc:document.documentElement.scrollWidth, main:rect('main'), flow:rect('#rd-chat-flow'), bar:rect('.rd-chat-bar'), rail:rect('#rd-railcol'), scroll:document.querySelector('main').scrollHeight, client:document.querySelector('main').clientHeight};
      })()`);
      console.log(width, JSON.stringify(bounds));
      assert.ok(bounds.doc <= width + 1, 'no page horizontal overflow');
      assert.ok(bounds.bar.bottom <= 901, 'composer visible');
      assert.ok(bounds.flow.h > 120, 'timeline retains reading space');
      assert.ok(bounds.flow.w <= 961, 'reading width capped on wide screens');
      assert.ok(width - bounds.rail.right <= 25, 'task rail stays docked at right edge');
      assert.ok(bounds.bar.right <= bounds.rail.x + 1, 'composer and task rail do not overlap');
      assert.ok(bounds.scroll <= bounds.client + 1, 'only timeline scrolls');
      assert.ok(await ev("document.getElementById('rd-chat-flow').scrollHeight > document.getElementById('rd-chat-flow').clientHeight"), 'long conversation scrolls inside timeline');
      assert.equal(await ev("getComputedStyle(document.getElementById('rd-chat-flow')).scrollbarWidth"), 'none', 'conversation remains scrollable without a visible inner scrollbar');
      if (width === 1366) await capture('chat-1366');
    }
    await browser.send('Emulation.setDeviceMetricsOverride', { width: 1600, height: 1000, deviceScaleFactor: 1, mobile: false });
    await capture('chat-light');
    await ev("document.documentElement.dataset.theme='dark'");
    await capture('chat-dark');
    await ev("document.body.classList.add('side-collapsed')");
    await capture('chat-collapsed');
    await ev("document.documentElement.dataset.skin='forest'");
    assert.notEqual(await ev("getComputedStyle(document.documentElement).getPropertyValue('--accent').trim()"), '#376fbd', 'alternate palette respected');
    await ev("document.body.classList.remove('side-collapsed'); document.documentElement.style.setProperty('--side-w','300px')");
    assert.equal(await ev("document.getElementById('sidebar').getBoundingClientRect().width"), 300, 'resized sidebar width respected');
    await browser.send('Emulation.setDeviceMetricsOverride', { width: 1366, height: 600, deviceScaleFactor: 1, mobile: false });
    assert.ok(await ev("document.querySelector('.rd-chat-bar').getBoundingClientRect().bottom <= innerHeight"), 'short-window composer visible');
    assert.ok(await ev("getComputedStyle(document.getElementById('rd-rail')).overflowY === 'auto'"), 'short-window task actions scroll');
    await browser.send('Emulation.setDeviceMetricsOverride', { width: 1600, height: 1000, deviceScaleFactor: 1, mobile: false });
    await ev("switchTab('agents')");
    const settingsLayout = await ev(`(() => {
      const nav = document.querySelector('.side-settings');
      const main = document.querySelector('main').getBoundingClientRect();
      const page = document.getElementById('page-settings').getBoundingClientRect();
      return {title: document.querySelector('.settings-nav-head strong')?.textContent.trim(),
        scrollbar: getComputedStyle(nav).scrollbarWidth, pageWidth: Math.round(page.width),
        centered: Math.abs((page.left + page.right) / 2 - (main.left + main.right) / 2) < 2,
        horizontalOverflow: document.documentElement.scrollWidth > innerWidth};
    })()`);
    assert.equal(settingsLayout.title, '设置', 'settings navigation has a clear title');
    assert.equal(settingsLayout.scrollbar, 'none', 'settings navigation hides the visual scrollbar');
    assert.ok(settingsLayout.pageWidth <= 821, 'settings content stays in a readable column');
    assert.ok(settingsLayout.centered, 'settings content is centered in the main canvas');
    assert.equal(settingsLayout.horizontalOverflow, false, 'settings layout has no horizontal overflow');
    await ev("document.documentElement.dataset.theme='light'; document.documentElement.dataset.skin='classic'");
    await capture('settings-agents');
    assert.deepEqual(errors, [], 'no uncaught browser exceptions');
    console.log('PASS desktop geometry and palette');
  }
} finally {
  if (browser) await browser.close();
  if (server) {
    await new Promise(resolve => { const kill=spawn('taskkill',['/F','/T','/PID',String(server.pid)],{stdio:'ignore',windowsHide:true});kill.on('exit',resolve); });
  }
  rmSync(data, { recursive: true, force: true });
}
