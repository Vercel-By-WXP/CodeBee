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
    const body = getComputedStyle(document.body);
    const hero = getComputedStyle(document.querySelector('#sub-tasks .hero'));
    const task = getComputedStyle(document.querySelector('.stask'));
    const newTask = getComputedStyle(document.getElementById('btn-new-task'));
    const dirBody = getComputedStyle(document.querySelector('.sdir > .dirbody'));
    const selectedTaskEl = document.querySelector('.stask');
    selectedTaskEl.classList.add('active');
    const selectedTask = getComputedStyle(selectedTaskEl);
    const selectedTaskStyle = { background: selectedTask.backgroundColor, shadow: selectedTask.boxShadow };
    selectedTaskEl.classList.remove('active');
    const hasFocusRule = rules => Array.from(rules || []).some(rule =>
      rule.selectorText?.includes('.rail-btn:focus-visible') || hasFocusRule(rule.cssRules));
    const railFocusRule = Array.from(document.styleSheets).some(sheet => {
      try { return hasFocusRule(sheet.cssRules); } catch { return false; }
    });
    const iconPill = getComputedStyle(document.getElementById('btn-board'));
    const textPillEl = document.getElementById('btn-ops');
    textPillEl.classList.remove('hidden');
    textPillEl.querySelector('#op-pill-text').textContent = '操作状态';
    const textPill = getComputedStyle(textPillEl);
    return { rail: Math.round(rail.width), sidebar: Math.round(sidebar.width), bg: root.getPropertyValue('--cb-canvas').trim(),
      detail: getComputedStyle(document.getElementById('run-detail')).borderRadius,
      composerRadius: getComputedStyle(document.querySelector('.rd-chat-bar')).borderRadius,
      topbarHeight: Math.round(document.querySelector('.topbar').getBoundingClientRect().height),
      bodyFont: body.fontFamily, bodySize: parseFloat(body.fontSize), heroSize: parseFloat(hero.fontSize), taskSize: parseFloat(task.fontSize),
      taskRowMinHeight: parseFloat(task.minHeight), newTaskBorder: newTask.borderTopWidth, iconPillWidth: parseFloat(iconPill.width),
      textPillWidth: parseFloat(textPill.width), railFocusRule,
      dirLineWidth: parseFloat(dirBody.borderLeftWidth), selectedTaskStyle };
  })()`);
  assert.equal(shell.rail, 52, 'Codex shell keeps a 52px navigation rail');
  assert.equal(shell.sidebar, 288, 'Codex shell keeps a 288px task sidebar');
  assert.equal(shell.bg, '#ffffff', 'light desktop canvas uses a clean white surface');
  assert.notEqual(shell.detail, '0px', 'run detail uses a rounded card edge');
  assert.match(shell.composerRadius, /(?:18px|0px 0px 18px 18px)/, 'chat composer keeps the compact Codex card radius while joining the run status bar');
  assert.equal(shell.topbarHeight, 56, 'desktop header stays compact at 56px');
  assert.match(shell.bodyFont, /Segoe UI Variable/, 'desktop UI uses the Codex Segoe UI Variable font stack');
  assert.equal(shell.bodySize, 14, 'desktop body text uses the Codex 14px baseline');
  assert.ok(shell.heroSize >= 26 && shell.heroSize <= 32, 'new-task title stays in the Codex 26-32px range');
  assert.ok(shell.taskSize >= 12.5 && shell.taskSize <= 14, 'sidebar task text stays in the Codex compact range');
  assert.equal(shell.taskRowMinHeight, 29, 'sidebar task rows stay compact at 29px');
  assert.ok(shell.dirLineWidth > 0, 'project tree keeps its vertical hierarchy line');
  assert.notEqual(shell.selectedTaskStyle.background, 'rgba(0, 0, 0, 0)', 'selected Goal row has a visible theme surface');
  assert.match(shell.selectedTaskStyle.shadow, /2px 0px 0px.*inset|inset.*2px 0px 0px/, 'selected Goal row has a 2px leading accent');
  assert.equal(shell.newTaskBorder, '0px', 'new-chat entry is a quiet list action');
  assert.equal(shell.iconPillWidth, 32, 'icon-only topbar controls stay square');
  assert.ok(shell.textPillWidth > shell.iconPillWidth, 'text-bearing topbar pills keep content sizing');
  assert.equal(shell.railFocusRule, true, 'desktop navigation keeps a visible keyboard focus ring');
  console.log('Codex shell', JSON.stringify(shell));
  // The landing composer is viewport-anchored.  Check the actual empty-state
  // form at short and narrow desktop sizes so the lower controls never fall
  // below the visible window edge.
  const landingBounds = [];
  for (const [width, height] of [[1600, 1000], [1366, 600], [1024, 520], [920, 520]]) {
    await browser.send('Emulation.setDeviceMetricsOverride', { width, height, deviceScaleFactor: 1, mobile: false });
    await sleep(80);
    landingBounds.push(await ev(`(() => {
      const form = document.querySelector('#sub-tasks .form');
      const box = form?.getBoundingClientRect();
      return { width: innerWidth, height: innerHeight,
        top: box?.top ?? 0, bottom: box?.bottom ?? 0,
        formHeight: box?.height ?? 0,
        docHeight: document.documentElement.scrollHeight };
    })()`));
  }
  console.log('landing composer', JSON.stringify(landingBounds));
  for (const bounds of landingBounds) {
    assert.ok(bounds.formHeight > 0, 'landing composer remains rendered');
    assert.ok(bounds.bottom <= bounds.height - 1, 'landing composer stays inside the viewport');
    assert.ok(bounds.bottom >= bounds.height - 40, 'landing composer keeps a small bottom safety gap');
    assert.ok(bounds.docHeight <= bounds.height + 1, 'landing page does not grow a hidden vertical overflow');
  }
  await browser.send('Emulation.setDeviceMetricsOverride', { width: 1600, height: 1000, deviceScaleFactor: 1, mobile: false });
  const capture = async name => {
    const shot = await browser.send('Page.captureScreenshot', { format: 'png' });
    writeFileSync(join(output, name + '.png'), Buffer.from(shot.result.data, 'base64'));
  };
  await capture('home');
  await ev("sideOpenTask('layout-a')");
  await sleep(900);
  await ev(`S.detailSide = {task:{id:'layout-a',title:'桌面工作台',workdir:'C:/Projects/MultiAgentOrchestration'},git:{branch:'main'},changes:{add_total:139,del_total:2,files:[]}}; renderChangeSummary()`);
  const summary = await ev(`(() => ({hidden:document.getElementById('change-summary').classList.contains('hidden'),
    project:document.getElementById('change-summary-project').textContent,
    add:document.getElementById('change-summary-add').textContent,
    del:document.getElementById('change-summary-del').textContent}))()`);
  assert.deepEqual(summary, { hidden: false, project: 'MultiAgentOrchestration', add: '+139', del: '-2' }, 'Git receipt follows the active task side snapshot');
  await ev("document.getElementById('rd-git').classList.remove('hidden'); document.getElementById('change-summary-open').click()");
  assert.equal(await ev("S.rdTab"), 'git', 'change receipt opens the task Git section');
  await ev("document.getElementById('btn-browser-split').click()");
  const split = await ev(`(() => ({open:document.body.classList.contains('browser-split-open'),
    pressed:document.getElementById('btn-browser-split').getAttribute('aria-pressed'),
    pane:Math.round(document.getElementById('sub-browser').getBoundingClientRect().width),
    detail:document.getElementById('rd-chat-flow').getBoundingClientRect().right,
    viewport:innerWidth}))()`);
  assert.equal(split.open, true, 'browser split opens without navigating away from the conversation');
  assert.equal(split.pressed, 'true', 'browser split button exposes its pressed state');
  assert.ok(split.pane > 400, 'browser workspace is visible beside the task conversation');
  assert.ok(split.detail <= split.viewport - split.pane + 1, 'conversation keeps its own column beside the browser');
  await ev("document.getElementById('btn-browser-split').click()");
  assert.equal(await ev("document.body.classList.contains('browser-split-open')"), false, 'browser split closes cleanly');
  await ev("S.detailSide = {task:{id:'layout-a',title:'桌面工作台',workdir:'C:/Projects/MultiAgentOrchestration'},changes:{add_total:139,del_total:2}}; renderChangeSummary(); document.getElementById('change-summary-more').click()");
  assert.equal(await ev("document.getElementById('change-summary').classList.contains('hidden')"), true, 'change receipt hides when Git context is unavailable');
  assert.equal(await ev("S.rdTab"), 'git', 'missing Git context does not change the current section');
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
  // Active-run context and the composer should share one reading axis. Inject
  // a long goal to exercise the real desktop status treatment without
  // starting a CLI process in this layout-only fixture.
  await ev(`(() => {
    const status = document.getElementById('rd-chat-run-status');
    status.classList.remove('hidden');
    status.querySelector('[data-chat-run-goal]').textContent = '在 MultiAgentOrchestration 中完成运行详情页样式优化并验证桌面与窄窗布局';
    status.querySelector('[data-chat-run-elapsed]').textContent = '28 分 24 秒';
    status.querySelector('[data-chat-run-pause]').textContent = '暂停';
  })()`);
  const activeComposer = await ev(`(() => {
    const status = document.getElementById('rd-chat-run-status');
    const bar = document.querySelector('.rd-chat-bar');
    const goal = status.querySelector('[data-chat-run-goal]');
    const sr = status.getBoundingClientRect();
    const br = bar.getBoundingClientRect();
    return {
      statusX: Math.round(sr.x), statusW: Math.round(sr.width),
      barX: Math.round(br.x), barW: Math.round(br.width),
      goalContained: goal.getBoundingClientRect().right <= sr.right,
      goalOverflow: getComputedStyle(goal).overflow,
      goalEllipsis: getComputedStyle(goal).textOverflow,
      statusBottom: getComputedStyle(status).borderBottomWidth,
      barTopLeft: getComputedStyle(bar).borderTopLeftRadius,
      nextClass: status.nextElementSibling?.className || '',
      joins: bar.matches('#run-detail.chat-mode .rd-chat-run-status + .rd-chat-bar'),
    };
  })()`);
  console.log('active composer', JSON.stringify(activeComposer));
  assert.equal(activeComposer.statusX, activeComposer.barX, 'active-run status aligns with the composer');
  assert.equal(activeComposer.statusW, activeComposer.barW, 'active-run status and composer share a width');
  assert.equal(activeComposer.goalContained, true, 'long active goal stays inside the status strip');
  assert.equal(activeComposer.goalOverflow, 'hidden', 'long active goal clips inside its flexible text slot');
  assert.equal(activeComposer.goalEllipsis, 'ellipsis', 'long active goal uses an ellipsis');
  assert.equal(activeComposer.statusBottom, '0px', 'status strip joins the composer without a doubled divider');
  assert.match(activeComposer.barTopLeft, /0px/, 'composer top corners join the active status strip');
  {
    for (const width of [1920, 1600, 1440, 1366, 1024, 920]) {
      await browser.send('Emulation.setDeviceMetricsOverride', { width, height: 900, deviceScaleFactor: 1, mobile: false });
      await sleep(120);
      const bounds = await ev(`(() => {
        const rect = s => {const r=document.querySelector(s).getBoundingClientRect();return {x:r.x,y:r.y,w:r.width,h:r.height,right:r.right,bottom:r.bottom}};
        return {viewport:innerWidth, doc:document.documentElement.scrollWidth, main:rect('main'), flow:rect('#rd-chat-flow'), status:rect('#rd-chat-run-status'), bar:rect('.rd-chat-bar'), rail:rect('#rd-railcol'), scroll:document.querySelector('main').scrollHeight, client:document.querySelector('main').clientHeight};
      })()`);
      console.log(width, JSON.stringify(bounds));
      assert.ok(bounds.doc <= width + 1, 'no page horizontal overflow');
      assert.ok(bounds.bar.bottom <= 901, 'composer visible');
      assert.ok(bounds.flow.h > 120, 'timeline retains reading space');
      assert.ok(bounds.flow.w <= 781, 'reading width capped on wide screens');
      assert.ok(bounds.bar.w <= 741, 'composer width follows the 740px reading measure');
      assert.equal(bounds.status.w, bounds.bar.w, 'active status and composer share a width at every desktop size');
      assert.equal(bounds.status.x, bounds.bar.x, 'active status and composer share an edge at every desktop size');
      assert.ok(width - bounds.rail.right <= 25, 'task rail stays docked at right edge');
      assert.ok(bounds.bar.right <= bounds.rail.x + 1, 'composer and task rail do not overlap');
      if (width < 1600) assert.equal(Math.round(bounds.rail.y), Math.round(bounds.flow.y), 'docked task rail aligns with the run content');
      assert.ok(bounds.scroll <= bounds.client + 1, 'only timeline scrolls');
      assert.ok(await ev("document.getElementById('rd-chat-flow').scrollHeight > document.getElementById('rd-chat-flow').clientHeight"), 'long conversation scrolls inside timeline');
      assert.equal(await ev("getComputedStyle(document.getElementById('rd-chat-flow')).scrollbarWidth"), 'none', 'conversation remains scrollable without a visible inner scrollbar');
      if (width === 1366) await capture('chat-1366');
    }
    await browser.send('Emulation.setDeviceMetricsOverride', { width: 1600, height: 1000, deviceScaleFactor: 1, mobile: false });
    await ev("document.getElementById('rd-chat-run-status').classList.add('hidden')");
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
    await browser.send('Emulation.setDeviceMetricsOverride', { width: 390, height: 844, deviceScaleFactor: 1, mobile: true });
    await ev("document.documentElement.dataset.theme='light'; document.documentElement.dataset.skin='classic'");
    const railHeightBeforeInfo = await ev("Math.round(document.getElementById('rd-rail').getBoundingClientRect().height)");
    await ev("document.getElementById('rd-more-toggle').click()");
    await sleep(100);
    const infoLayout = await ev(`(() => {
      const rail = document.getElementById('rd-rail').getBoundingClientRect();
      const pop = document.getElementById('rd-meta-popover');
      const box = pop.getBoundingClientRect();
      return {railHeight: Math.round(rail.height), popPosition: getComputedStyle(pop).position,
        railOverflow: getComputedStyle(document.getElementById('rd-rail')).overflow,
        railcolOverflow: getComputedStyle(document.getElementById('rd-railcol')).overflow,
        popWidth: Math.round(box.width), popScrollWidth: pop.scrollWidth, popClientWidth: pop.clientWidth,
        background: getComputedStyle(pop).backgroundColor,
        scrollbar: getComputedStyle(pop).scrollbarWidth, docWidth: document.documentElement.scrollWidth};
    })()`);
    console.log('mobile task info', JSON.stringify(infoLayout));
    assert.equal(infoLayout.popPosition, 'absolute', 'mobile task information floats over rail content');
    assert.equal(infoLayout.railHeight, railHeightBeforeInfo, 'opening task information does not increase rail height');
    assert.ok(infoLayout.popWidth <= 350, 'task information fits a narrow viewport');
    assert.ok(infoLayout.popScrollWidth <= infoLayout.popClientWidth + 1, 'task information has no horizontal overflow');
    assert.ok(!infoLayout.background.startsWith('rgba('), 'task information uses an opaque canvas background');
    assert.equal(infoLayout.scrollbar, 'none', 'task information hides its visual scrollbar');
    assert.ok(infoLayout.docWidth <= 391, 'mobile details have no page horizontal overflow');
    await capture('mobile-task-info');
    await ev("document.getElementById('rd-more-close').click()");
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
