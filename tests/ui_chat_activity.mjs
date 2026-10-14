/* Conversation reference regression: real log API, isolated data, no CLI execution. */
import assert from 'node:assert/strict';
import { spawn } from 'node:child_process';
import { mkdtempSync, mkdirSync, writeFileSync, rmSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import { fileURLToPath } from 'node:url';
import { bootEdge } from './_ui_boot.mjs';

const root = fileURLToPath(new URL('../', import.meta.url));
const data = mkdtempSync(join(tmpdir(), 'codebee-chat-activity-'));
const output = join(root, 'output', 'playwright', 'chat-activity');
const port = Number(process.env.TUTTI_TEST_PORT || 18984);
const origin = `http://127.0.0.1:${port}`;
const runId = 'r-20990101-000000-0001';
const log = 'steps/01.log';
const sleep = ms => new Promise(resolve => setTimeout(resolve, ms));
let browser, server;
mkdirSync(output, { recursive: true });
mkdirSync(join(data, 'tasks'), { recursive: true });
mkdirSync(join(data, 'runs', runId, 'steps'), { recursive: true });
writeFileSync(join(data, 'tasks', 'activity-a.json'), JSON.stringify({
  id: 'activity-a', title: '对话执行过程', goal: '支持可展开的执行过程，并默认定位最新消息',
  type: 'code', workdir: data, status: 'done', archived: false, mode: 'manual', engine: 'direct',
  attachments: ['_attachments/a.png', '_attachments/b.png', '_attachments/c.png', '_attachments/d.png'],
  created_at: '2099-01-01 00:00:00',
}));
const finalText = '已完成对话布局改造。工具操作可以展开查看，进入对话会显示最新消息。';
writeFileSync(join(data, 'runs', runId, 'run.json'), JSON.stringify({
  id: runId, task_id: 'activity-a', title: '对话执行过程', kind: 'direct', engine: 'direct', status: 'done',
  steps: [{ n: 1, role: 'chat', agent: 'codex-cli', agent_label: 'Codex', status: 'done', log,
    output: finalText, duration_s: 256, started_at: '00:00:00', ended_at: '00:04:16' }],
  messages: Array.from({ length: 24 }, (_, i) => ({ id: String(i), consumed: true,
    sender: '用户', text: `历史消息 ${i}：这是用于验证默认跳到最新位置的对话内容。`,
    attachments: i === 23 ? ['_attachments/e.png'] : [], created_at: '00:00:00' })),
  created_at: '2099-01-01 00:00:00', started_at: '2099-01-01 00:00:00',
  ended_at: '2099-01-01 00:04:16', cost_usd: 0, tokens: 0, error: '',
}));
const events = [{ type: 'item.completed', item: { id: 'm1', type: 'agent_message', text: '我先查看项目约定，确认需要补充的对话交互。' } }];
for (let i = 0; i < 40; i++) events.push({ type: 'item.completed', item: {
  id: 'c' + i, type: 'command_execution', command: i === 0 ? '<img src=x onerror="window.__injected=1">' : `Get-Content app/ui/app.js | Select-Object -Skip ${i * 20} -First 20`,
  aggregated_output: i === 0 ? '<script>window.__injected=1</script>' : `检索结果 ${i}`,
  exit_code: i === 3 ? 1 : 0,
} });
events.push({ type: 'item.completed', item: { id: 'm2', type: 'agent_message', text: '已确认进入对话时的滚动问题，接下来修复并验证。' } });
events.push({ type: 'item.completed', item: { id: 'mcp', type: 'mcp_tool_call', server: 'Context7', tool: 'query-docs', arguments: { query: '工具文档' }, result: { content: '文档内容' } } });
events.push({ type: 'item.completed', item: { id: 'final', type: 'agent_message', text: finalText } });
writeFileSync(join(data, 'runs', runId, log), events.map(e => JSON.stringify(e)).join('\n'));

try {
  server = spawn('python', ['-X', 'utf8', 'app/main.py', '--port', String(port), '--no-browser', '--host', '127.0.0.1'], {
    cwd: root, stdio: 'ignore', windowsHide: true, env: { ...process.env, TUTTI_DATA: data, TUTTI_CLASSROOM_SYNC: '0' },
  });
  for (let i = 0; i < 60; i++) {
    try { if ((await fetch(origin + '/api/state')).ok) break; } catch {}
    await sleep(250);
  }
  const api = await fetch(`${origin}/api/runs/${runId}/log?step=${encodeURIComponent(log)}&tail=40000&pretty=1&activities=1`).then(r => r.json());
  assert.equal(api.activities.length, 41, 'real log API returns all tool calls');
  assert.equal(api.activities[3].status, 'failed', 'command failure preserved');
  assert.match(api.log, /【命令】/, 'existing pretty log remains available');
  browser = await bootEdge({ width: 1366, height: 900, awaitPromise: true, reducedMotion: true });
  const ev = browser.evalJs;
  const waitFor = async expression => {
    for (let i = 0; i < 80; i++) { if (await ev(expression)) return; await sleep(100); }
    assert.equal(await ev(expression), true, expression);
  };
  await browser.send('Page.navigate', { url: origin });
  await waitFor("typeof S !== 'undefined' && Boolean(S.state?.tasks?.some(t => t.id === 'activity-a'))");
  await ev("welcomeClose(); sideOpenTask('activity-a')");
  await waitFor("document.querySelectorAll('.chat-tool-row').length === 41");
  const pos = () => ev(`(() => { const f=$('rd-chat-flow'); return { dist:f.scrollHeight-f.scrollTop-f.clientHeight, height:f.clientHeight, scroll:f.scrollHeight }; })()`);
  const top = () => ev("$('rd-chat-flow').style.scrollBehavior='auto'; $('rd-chat-flow').scrollTop=0; chatForceBottom=false");
  assert.ok((await pos()).dist <= 40, 'first entry loads latest message');
  assert.ok((await pos()).scroll > (await pos()).height * 2, 'fixture has long history');
  assert.equal(await ev("document.querySelectorAll('.chat-tool-group').length"), 2, 'commentary splits consecutive tool groups');
  assert.equal(await ev("document.querySelectorAll('.chat-execution-message').length"), 2, 'intermediate commentary preserved without duplicating final answer');
  assert.equal(await ev("Boolean(window.__injected || document.querySelector('.chat-tool-row img,.chat-tool-row script'))"), false, 'log text is escaped');
  await top();
  await ev("rdChatNavGo('steps'); rdChatNavBack()");
  assert.ok((await pos()).dist <= 40, 'returning to chat defaults to latest without an existing flag');
  await top();
  await ev("closeRun(); sideOpenTask('activity-a')");
  await waitFor(`$('rd-chat-flow').dataset.runId === '${runId}' && !$('run-detail').classList.contains('hidden') && S.rdTab === 'chat'`);
  await sleep(150);
  assert.ok((await pos()).dist <= 40, 'reopening same conversation loads latest');
  await top();
  await ev(`document.querySelector('.chat-tool-row summary').click(); document.querySelector('.chat-tool-list').scrollTop=70`);
  await sleep(30);
  await ev(`chatLiveSig=''; drawChatFlow(S.lastRun,{items:chatTimelineItems},false)`);
  assert.equal(await ev("document.querySelector('.chat-tool-row').open"), true, 'tool detail disclosure survives refresh');
  assert.equal(await ev("document.querySelector('.chat-tool-list').scrollTop"), 70, 'tool list scroll survives refresh');
  assert.ok((await pos()).dist > 40, 'refresh preserves reading history');
  await ev("document.querySelector('.chat-tool-group summary').click()");
  await sleep(30);
  await ev("chatLiveSig=''; drawChatFlow(S.lastRun,{items:chatTimelineItems},false)");
  assert.equal(await ev("document.querySelector('.chat-tool-group').open"), false, 'collapsed tool group survives refresh');

  // Visual checks use display state only; no run or cancellation is started.
  await ev(`S.detailSide={run:S.lastRun,task:S.state.tasks.find(t=>t.id==='activity-a'),git:{branch:'main'},
    changes:{count:2,add_total:131,del_total:2,files:[{path:'app/ui/app.js',status:'M',add:130,del:2},{path:'app/ui/index.html',status:'M',add:1,del:0}]}};
    chatLiveSig=''; drawChatFlow(S.lastRun,{items:chatTimelineItems},false); renderChatContext(true); chatJumpLatest();`);
  assert.equal(await ev("$('rd-context').textContent.includes('+131') && $('rd-context').textContent.includes('main')"), true,
    'task rail shows actual Git summary and branch');
  assert.equal(await ev("document.querySelectorAll('.rd-context-step').length"), 1, 'task rail shows run progress');
  await ev("S.state.tasks.find(t=>t.id==='activity-a').engine='code'; S.lastRun.steps[0].agent='codex-cli'; renderChatContext(true)");
  assert.equal(await ev("document.querySelector('.rd-context-agent')?.textContent.includes('1 完成')"), true,
    'orchestration rail shows completed subagent count');
  const agentsShot = await browser.send('Page.captureScreenshot', { format:'png' });
  writeFileSync(join(output, 'chat-subagents-1366-light.png'), Buffer.from(agentsShot.result.data, 'base64'));
  await ev("S.state.tasks.find(t=>t.id==='activity-a').engine='direct'; renderChatContext(true)");
  assert.equal(await ev("document.querySelectorAll('.rd-context-source').length"), 5, 'task rail includes initial and conversation sources');
  await ev("chatTimelineItems.push({kind:'user',run:'r-earlier',attachments:['_attachments/e.png']}); renderChatContext(true)");
  assert.equal(await ev("document.querySelectorAll('.rd-context-source').length"), 6,
    'same-named attachments from different runs remain separate sources');
  await ev("chatTimelineItems.pop(); renderChatContext(true)");
  assert.equal(await ev("document.querySelectorAll('.rd-context-source:not(.hidden)').length"), 3, 'source list starts compact');
  await ev("document.querySelector('[data-context-action=\"all-sources\"]').click()");
  assert.equal(await ev("document.querySelectorAll('.rd-context-source:not(.hidden)').length"), 5, 'all sources can expand');
  await ev(`S.detailSide={...S.detailSide,run:{id:'r-newer',status:'running',started_at:'2099-01-02 00:00:00'},
    progress:{done:0,total:1},steps:[{n:9,status:'running',summary:'新一轮步骤',log:'steps/09.log'}]}; renderChatContext(true)`);
  assert.equal(await ev(`document.querySelector('.rd-context-step').dataset.stepN === '1' &&
    document.querySelector('[data-context-action="terminal"]').dataset.logRun === '${runId}' &&
    !$('rd-context').textContent.includes('新一轮步骤')`), true, 'historical run ignores latest task progress and terminal');
  await ev(`S.detailSide={run:S.lastRun,task:S.state.tasks.find(t=>t.id==='activity-a'),git:{branch:'main'},
    changes:{count:2,add_total:131,del_total:2,files:[{path:'app/ui/app.js',status:'M',add:130,del:2},{path:'app/ui/index.html',status:'M',add:1,del:0}]}};
    renderChatContext(true)`);
  assert.equal(await ev("document.querySelector('.chat-change-card').open"), false, 'file receipt starts as compact pill');
  await ev("document.querySelector('.chat-change-head').click()");
  assert.equal(await ev("document.querySelectorAll('.chat-change-file').length"), 2, 'file receipt keeps actionable file list');
  await ev("document.querySelector('.chat-change-head').click()");

  for (const [width, height, theme] of [[1366,900,'light'], [1366,900,'dark'], [390,844,'light']]) {
    await browser.send('Emulation.setDeviceMetricsOverride', { width, height, deviceScaleFactor:1, mobile:width < 901 });
    await ev(`document.documentElement.dataset.theme='${theme}'; renderChatRunStatus({...S.lastRun,status:'running'},true);
      syncChatSendControls({...S.lastRun,status:'running'}); chatJumpLatest()`);
    assert.equal(await ev("!$('rd-chat-stop').classList.contains('hidden') && $('rd-chat-send').classList.contains('hidden')"), true, 'running empty composer offers stop');
    const bounds = await ev(`(() => { const r=document.querySelector('.rd-chat-bar').getBoundingClientRect(); const s=$('rd-chat-run-status').getBoundingClientRect();
      return { right:r.right,bottom:r.bottom,top:r.top,statusBottom:s.bottom,overflow:$('rd-chat').scrollWidth>$('rd-chat').clientWidth+1 }; })()`);
    assert.ok(bounds.right <= width && bounds.bottom <= height && !bounds.overflow, `composer fits ${width} ${theme}: ${JSON.stringify(bounds)}`);
    assert.ok(bounds.statusBottom <= bounds.top + 12, 'goal strip sits immediately above composer');
    assert.equal(await ev("getComputedStyle(document.querySelector('.rd-chat-bar')).borderTopLeftRadius"), '34px', 'composer top corners are rounded');
    assert.equal(await ev("getComputedStyle(document.querySelector('.rd-chat-bar')).borderTopRightRadius"), '34px', 'composer top corners are rounded');
    if (width < 901) assert.equal(await ev("getComputedStyle(document.querySelector('[data-chat-run-cancel]')).display"), 'flex', 'mobile status icons are centered');
    if (width < 901) {
      assert.equal(await ev("getComputedStyle($('rd-context')).display"), 'none', 'mobile task context starts closed');
      await ev("$('rd-context-toggle').click()");
      const panel = await ev("(() => { const r=$('rd-context').getBoundingClientRect(); return {display:getComputedStyle($('rd-context')).display,right:r.right,bottom:r.bottom} })()");
      assert.ok(panel.display !== 'none' && panel.right <= width && panel.bottom <= height, 'mobile context panel fits viewport');
    }
    const shot = await browser.send('Page.captureScreenshot', { format:'png' });
    writeFileSync(join(output, `chat-${width}-${theme}.png`), Buffer.from(shot.result.data, 'base64'));
  }
  await browser.send('Emulation.setDeviceMetricsOverride', { width:1024, height:600, deviceScaleFactor:1, mobile:false });
  const railBounds = await ev(`(() => { const rail=$('rd-rail').getBoundingClientRect(); const context=$('rd-context');
    return {top:rail.top,bottom:rail.bottom,height:rail.height,max:getComputedStyle($('rd-rail')).maxHeight,
      viewport:innerHeight,media:matchMedia('(min-width: 901px)').matches,
      contextScroll:context.scrollHeight, contextHeight:context.clientHeight}; })()`);
  assert.ok(railBounds.bottom <= 600 && railBounds.contextHeight > 0,
    `short desktop rail fits viewport: ${JSON.stringify(railBounds)}`);
  await ev(`$('rd-chat-input').value='继续'; syncChatSendControls({...S.lastRun,status:'running'})`);
  assert.equal(await ev("$('rd-chat-stop').classList.contains('hidden') && !$('rd-chat-send').classList.contains('hidden')"), true, 'typing restores send and delivery options');
  await top();
  await ev(`closeRun(); openRun('${runId}')`);
  await waitFor(`S.detailRunId === '${runId}' && S.runDetailSig !== '' && $('rd-chat-flow').dataset.runId === '${runId}' && S.rdTab === 'chat'`);
  await sleep(200);
  assert.ok((await pos()).dist <= 40, 'reopening the same run loads latest');
  console.log('PASS conversation activity, disclosure/scroll, latest entry and reference layout');
} finally {
  if (browser) await browser.close();
  if (server) await new Promise(resolve => {
    const kill = spawn('taskkill', ['/F','/T','/PID',String(server.pid)], { stdio:'ignore', windowsHide:true });
    kill.on('exit', resolve);
  });
  assert.ok(data.startsWith(tmpdir()), 'cleanup stays in task temporary directory');
  rmSync(data, { recursive:true, force:true });
}
