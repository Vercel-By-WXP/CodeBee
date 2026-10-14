/* Pending-message tray: real clicks, payloads, draft preservation and mobile layout.
 * Runs against an isolated fixture server; never launches an AI task. */
import assert from 'node:assert/strict';
import { spawn } from 'node:child_process';
import { mkdtempSync, mkdirSync, writeFileSync, rmSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import { fileURLToPath } from 'node:url';
import { bootEdge } from './_ui_boot.mjs';

const root = fileURLToPath(new URL('../', import.meta.url));
const data = mkdtempSync(join(tmpdir(), 'codebee-chat-delivery-'));
const port = Number(process.env.TUTTI_TEST_PORT || 18983);
const origin = `http://127.0.0.1:${port}`;
const output = join(root, 'output', 'playwright', 'chat-delivery');
const sleep = ms => new Promise(resolve => setTimeout(resolve, ms));
let server, browser, checks = 0;
const check = (name, value) => { assert.ok(value, name); checks++; console.log('  OK ' + name); };
mkdirSync(output, { recursive: true });
mkdirSync(join(data, 'tasks'), { recursive: true });
mkdirSync(join(data, 'runs', 'r-chat-fixture'), { recursive: true });
writeFileSync(join(data, 'tasks', 't-chat-fixture.json'), JSON.stringify({
  id: 't-chat-fixture', title: '消息引导验收', type: 'direct', engine: 'direct',
  goal: '验证待发送队列', workdir: data, status: 'done', created_at: '2026-10-14 10:00:00',
}));
writeFileSync(join(data, 'runs', 'r-chat-fixture', 'run.json'), JSON.stringify({
  id: 'r-chat-fixture', task_id: 't-chat-fixture', kind: 'orchestration',
  title: '消息引导验收', status: 'done', steps: [], messages: [], created_at: '2026-10-14 10:00:00',
}));
try {
  try { await fetch(origin + '/api/state'); throw new Error('Fixture port is already in use'); }
  catch (error) { if (error.message !== 'fetch failed') throw error; }
  server = spawn('python', ['-X', 'utf8', 'app/main.py', '--port', String(port), '--no-browser'], {
    cwd: root, windowsHide: true, stdio: 'ignore',
    env: { ...process.env, TUTTI_DATA: data, TUTTI_CLASSROOM_SYNC: '0' },
  });
  for (let i = 0; i < 60; i++) {
    try { if ((await fetch(origin + '/api/state')).ok) break; } catch {}
    await sleep(200);
  }
  browser = await bootEdge({ width: 1440, height: 950, reducedMotion: true, awaitPromise: true });
  const { send, evalJs: ev } = browser;
  const waitFor = async expression => {
    for (let i = 0; i < 80; i++) { if (await ev(expression)) return true; await sleep(100); }
    return false;
  };
  const click = async selector => {
    const point = await ev(`(() => { const e=document.querySelector(${JSON.stringify(selector)}); if(!e)return null; e.scrollIntoView({block:'nearest'}); const r=e.getBoundingClientRect(); const hit=document.elementFromPoint(r.x+r.width/2,r.y+r.height/2); return {x:r.x+r.width/2,y:r.y+r.height/2,hit:hit===e||e.contains(hit)}; })()`);
    assert.ok(point?.hit, 'Reachable click target: ' + selector);
    await send('Input.dispatchMouseEvent', { type: 'mousePressed', x: point.x, y: point.y, button: 'left', clickCount: 1 });
    await send('Input.dispatchMouseEvent', { type: 'mouseReleased', x: point.x, y: point.y, button: 'left', clickCount: 1 });
  };
  await send('Page.enable');
  await send('Page.addScriptToEvaluateOnNewDocument', { source: 'localStorage.setItem("orch.welcomed","1");' });
  await send('Page.navigate', { url: origin });
  check('App is ready', await waitFor('typeof S!=="undefined" && S.state?.tasks?.length===1'));
  await ev('jumpToRun("r-chat-fixture")');
  check('Conversation is visible', await waitFor('chatRunId==="r-chat-fixture" && !$("rd-chat").classList.contains("hidden")'));
  await sleep(500);
  await ev(`
    stopChatLive();
    window.fixtureRun={...S.lastRun,status:'running'}; S.lastRun=fixtureRun;
    window.fixtureItems=[{kind:'user',id:null,text:'验证待发送队列',consumed:true},
      {kind:'user',id:'000001',run:'r-chat-fixture',text:'立即发送，发送过去还是在排队啊',consumed:false,attachments:['_attachments/image.png']},
      {kind:'user',id:'000002',run:'r-chat-fixture',text:'<img src=x onerror=alert(1)> 保留这条消息',consumed:false}];
    window.fixtureDraw=()=>{chatLiveSig='';drawChatFlow(fixtureRun,{items:fixtureItems},true)};
    renderChat=async ()=>fixtureDraw();
    window.fixtureCalls=[]; window.fixtureApi=api;
    api=async (url,options)=>{
      if(url.endsWith('/timeline')) return {items:fixtureItems};
      if(url.endsWith('/chat')){fixtureCalls.push(JSON.parse(options.body)); return {ok:true,run_id:'r-next',delivery:'now'}};
      if(url.endsWith('/messages/retract')){fixtureCalls.push(JSON.parse(options.body)); fixtureItems=fixtureItems.filter(i=>i.id!==JSON.parse(options.body).id); return {ok:true}};
      return fixtureApi(url,options);
    };
    jumpToRun=async id=>{window.fixtureJump=id};
    fixtureDraw(); $('rd-chat-input').value='保留正在写的草稿'; syncChatSendControls();
  `);
  check('Pending messages appear only above the input', await ev('$("rd-chat-queue").querySelectorAll(".chat-queue-row").length===2 && !$("rd-chat-flow").textContent.includes("立即发送，发送过去") && $("rd-chat-queue").getBoundingClientRect().bottom <= $("rd-chat-input").getBoundingClientRect().top'));
  check('Message text is escaped', await ev('!$("rd-chat-queue").querySelector("img") && $("rd-chat-queue").textContent.includes("<img src=x")'));
  await click('[data-chat-steer="1"]');
  check('Guide submits an existing message ID', await waitFor('fixtureCalls[0]?.message_id==="000001" && fixtureCalls[0]?.delivery==="now" && fixtureJump==="r-next"'));
  check('Guide preserves the unsent draft and avoids duplicate text', await ev('$("rd-chat-input").value==="保留正在写的草稿" && !Object.hasOwn(fixtureCalls[0],"text") && !chatSending'));
  await click('[data-chat-retract="2"]');
  check('Remove targets only the selected queued message', await waitFor('fixtureCalls[1]?.id==="000002" && fixtureItems.length===2'));
  await ev('fixtureItems[1].delivery="now"; fixtureDraw()');
  check('Immediate guidance is distinct from queued delivery', await ev('$("rd-chat-queue").textContent.includes("正在引导") && !$("rd-chat-queue").querySelector("[data-chat-steer]")'));
  const shot = await send('Page.captureScreenshot', { format: 'png' });
  writeFileSync(join(output, 'desktop.png'), Buffer.from(shot.result.data, 'base64'));
  await send('Emulation.setDeviceMetricsOverride', { width: 390, height: 844, deviceScaleFactor: 1, mobile: true });
  await ev('fixtureItems[1].delivery="queue"; fixtureDraw()');
  await sleep(250);
  check('Mobile actions fit and have touch targets', await ev('(() => {const row=$("rd-chat-queue").querySelector(".chat-queue-row").getBoundingClientRect();return row.right<=innerWidth && [...$("rd-chat-queue").querySelectorAll("button")].every(e=>e.getBoundingClientRect().height>=40)})()'));
  const mobile = await send('Page.captureScreenshot', { format: 'png' });
  writeFileSync(join(output, 'mobile.png'), Buffer.from(mobile.result.data, 'base64'));
  await ev('setLang("en"); fixtureDraw()');
  check('Queue actions have English labels', await ev('$("rd-chat-queue").textContent.includes("Guide") && $("rd-chat-queue").querySelector("[data-chat-retract]").getAttribute("aria-label")==="Remove queued message"'));
  await ev('fixtureItems[1].consumed=true; fixtureDraw()');
  check('Delivered message moves into the conversation', await ev('$("rd-chat-queue").classList.contains("hidden") && $("rd-chat-flow").textContent.includes("立即发送，发送过去")'));
  console.log(`${checks} checks passed. Screenshots: ${output}`);
} finally {
  if (browser) await browser.close();
  if (server) { server.kill(); await sleep(500); }
  rmSync(data, { recursive: true, force: true });
}
