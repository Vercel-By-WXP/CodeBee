import assert from 'node:assert/strict';
import { spawn } from 'node:child_process';
import { mkdtempSync, mkdirSync, writeFileSync, rmSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import { fileURLToPath } from 'node:url';
import { bootEdge } from './_ui_boot.mjs';

const root = fileURLToPath(new URL('../', import.meta.url));
const data = mkdtempSync(join(tmpdir(), 'codebee-mobile-workbench-'));
const port = Number(process.env.TUTTI_TEST_PORT || 18992);
const origin = `http://127.0.0.1:${port}`;
const sleep = ms => new Promise(resolve => setTimeout(resolve, ms));
let server, browser, checks = 0;
const check = (name, value) => { assert.ok(value, name); checks++; console.log('  ✓ ' + name); };
mkdirSync(join(data, 'tasks'), { recursive: true });
mkdirSync(join(data, 'runs', 'r-mobile-fixture'), { recursive: true });
const task = { id: 't-mobile-fixture', title: '手机任务详情布局', type: 'direct', engine: 'direct', goal: 'UI fixture', workdir: data, status: 'done', created_at: '2026-10-14 10:00:00' };
const run = { id: 'r-mobile-fixture', task_id: task.id, title: task.title, kind: 'orchestration', status: 'done', steps: [], messages: [{ role: 'assistant', text: '对话内容应在输入框之上独立滚动。' }], created_at: task.created_at, started_at: task.created_at, ended_at: task.created_at };
writeFileSync(join(data, 'tasks', task.id + '.json'), JSON.stringify(task));
writeFileSync(join(data, 'runs', run.id, 'run.json'), JSON.stringify(run));
try {
  server = spawn('python', ['-X', 'utf8', 'app/main.py', '--port', String(port), '--no-browser'], {
    cwd: root, windowsHide: true, stdio: 'ignore', env: { ...process.env, TUTTI_DATA: data, TUTTI_CLASSROOM_SYNC: '0' },
  });
  let up = false;
  for (let i = 0; i < 70 && !up; i++) { try { up = (await fetch(origin + '/api/state')).ok; } catch {} if (!up) await sleep(200); }
  assert.ok(up, 'fixture server starts');
  browser = await bootEdge({ width: 1440, height: 950, reducedMotion: true, awaitPromise: true });
  const { send, evalJs: ev } = browser;
  const waitFor = async expression => { for (let i = 0; i < 80; i++) { if (await ev(expression)) return true; await sleep(100); } return false; };
  await send('Page.enable');
  await send('Emulation.setDeviceMetricsOverride', { width: 390, height: 844, deviceScaleFactor: 1, mobile: true });
  await send('Page.addScriptToEvaluateOnNewDocument', { source: 'localStorage.setItem("orch.welcomed","1");' });
  await send('Page.navigate', { url: origin });
  check('应用及测试任务已加载', await waitFor('S.state?.tasks?.some(t=>t.id==="t-mobile-fixture")'));
  await ev('jumpToRun("r-mobile-fixture")');
  check('手机任务对话详情可打开', await waitFor('S.lastRun?.id==="r-mobile-fixture" && !$("rd-chat").classList.contains("hidden")'));
  await sleep(400);
  check('手机专用控件逻辑已加载', await ev('window.mobileWorkbenchReady===true'));
  await sleep(300);
  check('返回/更多有44px触控区，状态清晰显示且整行不出屏', await ev(`(() => {const back=$('btn-back').getBoundingClientRect(),more=$('rd-more-toggle').getBoundingClientRect(),status=$('rd-status').getBoundingClientRect(),title=$('rd-title').getBoundingClientRect();return back.height>=44&&more.height>=44&&status.height>=18&&title.width>80&&[back,more,status,title].every(r=>r.x>=0&&r.right<=innerWidth+1)})()`));
  const compact = JSON.parse(await ev('JSON.stringify({hidden:$("mobile-chat-options").classList.contains("hidden"),parent:$("rd-mode").closest(".cmp-sel")?.parentElement.id,send:$("rd-chat-send").getBoundingClientRect().height})'));
  check('次要设置折叠，仅主输入和发送操作常驻', compact.hidden && compact.parent === 'mobile-chat-options' && compact.send >= 44, JSON.stringify(compact));
  check('手机输入框短提示避免长文占屏，保持16px', await ev('$("rd-chat-input").placeholder==="继续说…" && getComputedStyle($("rd-chat-input")).fontSize==="16px"'));
  const geometry = JSON.parse(await ev(`JSON.stringify((()=>{const main=document.querySelector('main'),input=$('rd-chat-input'),send=$('rd-chat-send'); if(!main||!input||!send)return {missing:true}; const a=main.getBoundingClientRect(),b=input.getBoundingClientRect(),c=send.getBoundingClientRect(); return {ok:b.y>=a.y&&c.bottom<=innerHeight+1&&c.right<=innerWidth+1&&main.scrollWidth<=main.clientWidth+1,a,b,c,sw:main.scrollWidth,cw:main.clientWidth};})())`));
  check('主输入和发送操作在真实视口内且无横向溢出', geometry.ok, JSON.stringify(geometry));
  await ev('$("mobile-chat-options-toggle").click()');
  check('高级选项点开后能操作并有清晰展开状态', await ev('!$("mobile-chat-options").classList.contains("hidden") && $("mobile-chat-options-toggle").getAttribute("aria-expanded")==="true" && $("rd-thinking").getBoundingClientRect().width>0'));
  await ev('$("rd-chat-input").focus(); $("rd-chat-input").dispatchEvent(new FocusEvent("focusin", {bubbles:true}))');
  const focusState = await ev('JSON.stringify({hidden:$("mobile-chat-options").classList.contains("hidden"),active:document.activeElement?.id,expanded:$("mobile-chat-options-toggle").getAttribute("aria-expanded")})');
  check('输入时高级选项自动收起让位对话', JSON.parse(focusState).hidden && JSON.parse(focusState).active === 'rd-chat-input', focusState);
  await ev('$("rd-more-toggle").click()');
  const info = JSON.parse(await ev(`JSON.stringify((()=>{const p=$('rd-meta-popover'),r=p.getBoundingClientRect(),a=p.querySelector('.mobile-task-actions');return {hidden:p.classList.contains('hidden'),x:r.x,right:r.right,bottom:r.bottom,actions:!!a,count:a?.children.length||0}})())`));
  check('更多任务信息与必要操作可按需显示且卡片不超屏', !info.hidden&&info.x>=0&&info.right<=390&&info.bottom<=845&&info.count>0, JSON.stringify(info));
  await ev('$("rd-more-toggle").click()');
  await send('Page.captureScreenshot', { format: 'png' }).then(result => {
    writeFileSync(join(root, 'output', 'playwright', 'mobile-workbench-390.png'), Buffer.from(result.result.data, 'base64'));
  });
  console.log(`\n手机详情工作台：${checks} 项通过。`);
} finally {
  if (browser) await browser.close();
  if (server) { server.kill(); await sleep(500); }
  rmSync(data, { recursive: true, force: true });
}
