/* Workspace modes: real menu clicks/keyboard, preference races, shared tasks,
 * drafts, request payloads, reload, language and mobile. Never runs an AI CLI. */
import assert from 'node:assert/strict';
import { spawn } from 'node:child_process';
import { mkdtempSync, mkdirSync, writeFileSync, rmSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import { fileURLToPath } from 'node:url';
import { bootEdge } from './_ui_boot.mjs';

const root = fileURLToPath(new URL('../', import.meta.url));
const data = mkdtempSync(join(tmpdir(), 'codebee-workspace-modes-'));
const output = join(root, 'output', 'workspace-modes');
const port = Number(process.env.TUTTI_TEST_PORT || 18974);
const origin = `http://127.0.0.1:${port}`;
const sleep = ms => new Promise(resolve => setTimeout(resolve, ms));
let browser, server, checks = 0;
const check = (name, value) => { assert.ok(value, name); checks++; console.log('  ✓ ' + name); };
mkdirSync(output, { recursive: true });
mkdirSync(join(data, 'tasks'), { recursive: true });
const workdir = join(data, 'ModeProject');
mkdirSync(workdir);
for (const [id, type] of [['mode-code', 'code'], ['mode-doc', 'doc']]) {
  writeFileSync(join(data, 'tasks', id + '.json'), JSON.stringify({
    id, title: '示例任务 ' + type, type, goal: 'UI fixture', workdir,
    status: 'done', archived: false, mode: 'expert', created_at: '2099-01-01 00:00:00',
  }));
}
try {
  server = spawn('python', ['app/main.py', '--port', String(port)], {
    cwd: root, windowsHide: true, stdio: 'ignore', env: { ...process.env, TUTTI_DATA: data },
  });
  for (let i = 0; i < 60; i++) {
    try { if ((await fetch(origin + '/api/state')).ok) break; } catch {}
    await sleep(250);
  }
  browser = await bootEdge({ width: 1440, height: 950, reducedMotion: true, awaitPromise: true });
  console.log(browser.envInfo.line);
  const { send, evalJs: ev } = browser;
  const errors = [];
  browser.ws.addEventListener('message', event => {
    const message = JSON.parse(event.data);
    if (message.method === 'Runtime.exceptionThrown') errors.push(message.params.exceptionDetails.text);
  });
  await send('Runtime.enable');
  await send('Page.enable');
  await send('Page.addScriptToEvaluateOnNewDocument', { source: 'localStorage.setItem("orch.welcomed","1");' });
  const waitFor = async expression => {
    for (let i = 0; i < 80; i++) { if (await ev(expression)) return true; await sleep(100); }
    return false;
  };
  const ready = () => waitFor('S.flows?.length > 0 && S.lastPrefs !== null && S.state?.tasks?.length === 2');
  const resize = (width, height = 950) => send('Emulation.setDeviceMetricsOverride', { width, height, deviceScaleFactor: 1, mobile: width <= 900 });
  const click = async selector => {
    const r = await ev(`(() => { const e=document.querySelector(${JSON.stringify(selector)}); e.scrollIntoView({block:'nearest'}); const r=e.getBoundingClientRect(); const hit=document.elementFromPoint(r.x+r.width/2,r.y+r.height/2); return {x:r.x+r.width/2,y:r.y+r.height/2,hit:hit===e||e.contains(hit),target:hit?.id||hit?.className,w:r.width,h:r.height} })()`);
    assert.ok(r.hit, 'click target reachable: ' + selector + ' ' + JSON.stringify(r));
    await send('Input.dispatchMouseEvent', { type: 'mousePressed', x: r.x, y: r.y, button: 'left', clickCount: 1 });
    await send('Input.dispatchMouseEvent', { type: 'mouseReleased', x: r.x, y: r.y, button: 'left', clickCount: 1 });
  };
  const key = async (key, code) => {
    const keyCode = { Enter: 13, Escape: 27, ArrowDown: 40, ArrowUp: 38, Home: 36, End: 35, Tab: 9 }[key];
    await send('Input.dispatchKeyEvent', { type: 'keyDown', key, code, windowsVirtualKeyCode: keyCode, nativeVirtualKeyCode: keyCode, ...(key === 'Enter' ? { text: '\r', unmodifiedText: '\r' } : {}) });
    await send('Input.dispatchKeyEvent', { type: 'keyUp', key, code, windowsVirtualKeyCode: keyCode, nativeVirtualKeyCode: keyCode });
  };
  const capture = async name => {
    const shot = await send('Page.captureScreenshot', { format: 'png' });
    writeFileSync(join(output, name + '.png'), Buffer.from(shot.result.data, 'base64'));
  };
  await resize(1440);
  await send('Page.navigate', { url: origin });
  check('加载共用任务与流程', await ready());
  check('首次工作模式默认直接执行', await ev('workspaceChoice.mode === "work" && $("f-type").value === "direct"'));
  check('工作入口文档/调研/翻译可见', await ev('$("cmp-quick").textContent.includes("文档") && $("cmp-quick").textContent.includes("调研") && !$("cmp-quick").textContent.includes("缺陷复盘")'));
  await capture('work-initial');
  await click('#btn-brand');
  check('菜单展开并聚焦选中模式', await ev('$("btn-brand").getAttribute("aria-expanded") === "true" && document.activeElement.dataset.workspaceMode === "work"'));
  await capture('mode-menu');
  await ev(`$('f-goal').value='保留这段目标和所有上下文，模式切换不自动提交任务'; $('f-workdir').value=${JSON.stringify(workdir)}; $('f-context').value='保留背景'; $('f-mode').value='expert'; S.atts=[{id:'fixture-attachment',name:'sample.txt'}];`);
  await key('ArrowDown', 'ArrowDown');
  await key('Enter', 'Enter');
  check('键盘选择开发，默认代码流程', await waitFor('workspaceChoice.mode === "develop" && $("f-type").value === "code"'));
  check('开发首页提示与常用入口联动', await ev('$("cmp-greet").textContent.includes("构建") && $("cmp-quick").textContent.includes("缺陷复盘")'));
  check('模式菜单关闭并归还焦点', await ev('$("brand-menu").classList.contains("hidden") && document.activeElement.id === "btn-brand"'));
  check('草稿/目录/附件/编排档位保留', await ev(`$('f-goal').value.includes('保留这段目标') && $('f-workdir').value === ${JSON.stringify(workdir)} && $('f-context').value === '保留背景' && $('f-mode').value === 'expert' && S.atts[0].id === 'fixture-attachment'`));
  check('模式切换未筛掉任一已有任务', await ev('document.querySelectorAll("#side-tasks .stask").length === 2'));
  // Capture the existing submission boundary, rejecting locally before any run starts.
  await ev(`window.modeOriginalApi=api; window.modePayloads=[]; api=async function(url,opts){if(url==='/api/tasks'&&opts?.method==='POST'){modePayloads.push(JSON.parse(opts.body));throw new Error('验收：未提交任务');}return modeOriginalApi(url,opts)};`);
  await ev('createTask()');
  check('开发提交沿用代码引擎与原编排档位', await ev('modePayloads.at(-1)?.type === "code" && modePayloads.at(-1)?.mode === "expert" && modePayloads.at(-1)?.attachments[0] === "fixture-attachment"'));
  await capture('develop');
  await click('#btn-brand'); await click('[data-workspace-mode="chat"]');
  check('聊天模式默认直连并显示聊天提示', await waitFor('workspaceChoice.mode === "chat" && $("f-type").value === "direct" && $("cmp-greet").textContent.includes("聊")'));
  check('聊天模式只展示直接对话入口', await ev('$("cmp-quick").textContent.includes("直接执行") && $("cmp-quick").querySelectorAll(".cmp-chip").length === 1'));
  await click('#btn-brand'); await click('[data-workspace-mode="work"]');
  await ev('S.typeSuggestionDone=true; createTask()');
  check('工作提交沿用直连类型，未引入新后端模式', await ev('modePayloads.at(-1)?.type === "direct" && !Object.hasOwn(modePayloads.at(-1),"workspace_mode")'));
  await ev('pickType("research")');
  await click('#btn-brand'); await click('[data-workspace-mode="develop"]');
  await click('#btn-brand'); await click('[data-workspace-mode="work"]');
  check('分别记忆工作与开发的任务类型', await ev('$("f-type").value === "research"'));
  await capture('work');
  await ev('S.lastPrefs={type:"code",mode:"manual"}; renderTypeOptions()');
  check('迟到的全局偏好不覆盖已选择场景', await ev('workspaceChoice.mode === "work" && $("f-type").value === "research" && $("f-mode").value === "expert"'));
  await click('#btn-brand'); await key('Escape', 'Escape');
  check('Escape关闭菜单且焦点可见', await ev('$("brand-menu").classList.contains("hidden") && document.activeElement.id === "btn-brand"'));
  await send('Page.reload'); check('刷新后恢复模式与任务类型', await ready());
  check('研究类型记忆在刷新后保留', await ev('workspaceChoice.mode === "work" && $("f-type").value === "research"'));
  await ev('pickLang("en")');
  check('模式菜单与首页支持英文', await ev('$("brand-mode").textContent === "Work" && $("cmp-greet").textContent === "Turn ideas into results"'));
  await resize(390, 844);
  await click('#btn-menu'); await click('#btn-brand'); await click('[data-workspace-mode="develop"]');
  check('手机抽屉可切换开发模式', await ev('workspaceChoice.mode === "develop" && $("brand-mode").textContent === "Develop"'));
  await click('#btn-menu'); await ev('mobileNewTask()');
  check('手机模式首页无横向溢出', await ev('document.documentElement.scrollWidth <= innerWidth'));
  await capture('mobile-develop');
  // Explicit choice outranks legacy prefs, but migration without a choice retains them.
  await ev('localStorage.removeItem(WORKSPACE_MODE_KEY)'); await send('Page.reload'); await ready();
  await ev('S.lastPrefs={type:"code"}; S.typeTouched=false; renderTypeOptions()');
  check('旧用户代码偏好迁移到开发模式', await ev('workspaceChoice.mode === "develop" && $("f-type").value === "code"'));
  await ev('localStorage.setItem(WORKSPACE_MODE_KEY,"broken-json")'); await send('Page.reload'); await ready();
  check('损坏偏好安全回落工作模式', await ev('workspaceChoice.mode === "work" && $("f-type").value === "direct"'));
  await ev('pickWorkspaceMode("work"); S.lastPrefs={type:"code"}; S.typeTouched=false; renderTypeOptions()');
  check('明确选择当前工作模式也可阻止迟到偏好覆盖', await ev('workspaceChoice.mode === "work" && $("f-type").value === "direct"'));
  await ev('S.creatingTask=true; const before=$("f-type").value, beforeMode=workspaceChoice.mode; pickWorkspaceMode("develop"); window.modeCreatingSafe=$("f-type").value===before && workspaceChoice.mode===beforeMode; S.creatingTask=false;');
  check('任务提交中切模式保持场景与表单一致', await ev('modeCreatingSafe'));
  check('页面无未捕获异常', errors.length === 0);
  console.log(`工作/开发/聊天模式：${checks} 项通过`);
} finally {
  if (browser) await browser.close();
  if (server) {
    const stopped = new Promise(resolve => server.once('exit', resolve));
    server.kill(); await stopped;
  }
  rmSync(data, { recursive: true, force: true });
}
