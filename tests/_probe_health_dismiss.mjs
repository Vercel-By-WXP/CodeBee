// 探针：顶栏健康告警横幅——账号类告警 × 隐藏（localStorage TTL）+ 页名不折行。
// 隔离服务（TUTTI_DATA 临时目录 + 独立端口）+ Edge 无头；跑完按 PID 清场。
// 裸跑：node tests/_probe_health_dismiss.mjs
import assert from 'node:assert/strict';
import { spawn } from 'node:child_process';
import { mkdtempSync, writeFileSync, rmSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import { fileURLToPath } from 'node:url';
import { bootEdge } from './_ui_boot.mjs';

const root = fileURLToPath(new URL('../', import.meta.url));
const port = Number(process.env.TUTTI_TEST_PORT || 18991);
const origin = `http://127.0.0.1:${port}`;
const sleep = (ms) => new Promise((r) => setTimeout(r, ms));

const data = mkdtempSync(join(tmpdir(), 'tutti-healthprobe-'));
// 预置两个「账号失效」的 CLI 账号，让横幅进 alerting 态
const now = Math.floor(Date.now() / 1000);
const acct = (id) => ({
  agent_id: id, status: 'down', code: 'ACCOUNT_DEAD', summary: '账号失效',
  failed_at: now, last_error: '账号失效', last_probe_at: 0,
  next_probe_at: now + 3600, probe_failures: 1, recovered_at: 0,
});
writeFileSync(join(data, 'account_health.json'),
  JSON.stringify({ accounts: { 'cline-cli': acct('cline-cli'), pi: acct('pi') } }, null, 2));

let server = null;
let passed = 0;
const ok = (name) => { passed++; console.log(`  ✓ ${name}`); };

// 看门狗：任何一步卡死（如无超时 fetch 撞上服务冷启动）强制定场并清服务进程。
// unref：不挡正常退场——只有事件循环仍被卡住时才会到点触发。
setTimeout(() => {
  console.error('WATCHDOG: 150s 超时，强制收场');
  if (server && server.pid) {
    try { spawn('taskkill', ['/F', '/T', '/PID', String(server.pid)], { stdio: 'ignore', windowsHide: true }); } catch (e) { /* ignore */ }
  }
  process.exit(3);
}, 150000).unref();

try {
  server = spawn('python', ['-X', 'utf8', 'app/main.py', '--port', String(port), '--no-browser', '--host', '127.0.0.1'], {
    cwd: root, stdio: 'ignore', windowsHide: true,
    env: { ...process.env, TUTTI_DATA: data, TUTTI_CLASSROOM_SYNC: '0' },
  });
  let up = false;
  for (let i = 0; i < 40 && !up; i++) {
    await sleep(500);
    try { up = (await fetch(origin + '/api/state', { signal: AbortSignal.timeout(2000) })).ok; } catch (e) { /* 起服务中 */ }
  }
  assert.ok(up, '隔离服务未就绪');
  ok(`隔离服务就绪 ${origin}`);

  const br = await bootEdge({ width: 1440, height: 900 });
  try {
    await br.send('Page.enable');
    await br.send('Page.navigate', { url: origin + '/' });
    await sleep(4000);

    // 1) 横幅进 alerting 态，文案含两个账号，× 按钮在位（Node 侧轮询，bootEdge 默认不 awaitPromise）
    let alerting = false;
    for (let i = 0; i < 30 && !alerting; i++) {
      alerting = await br.evalJs(`document.getElementById('health-banner').classList.contains('alerting')`);
      if (!alerting) await sleep(500);
    }
    assert.ok(alerting, '横幅未进告警态');
    ok('横幅 alerting 态出现');
    const txt = await br.evalJs(`document.getElementById('health-banner').textContent`);
    assert.ok(txt.includes('cline-cli') && txt.includes('pi'), '横幅文案缺账号名: ' + txt);
    ok('横幅文案含 cline-cli / pi');
    assert.equal(await br.evalJs(`!!document.querySelector('#health-banner .health-x')`), true, '× 按钮不在');
    ok('× 隐藏按钮在位');

    // 2) 页名不折行（alerting 挤压下单行 + 省略号由样式保证）
    const titleBox = await br.evalJs(`(function(){ const el = document.getElementById('page-title');
      return JSON.stringify({ h: el.getBoundingClientRect().height, nowrap: getComputedStyle(el).whiteSpace }); })()`);
    const tb = JSON.parse(titleBox);
    assert.ok(tb.h <= 30, `页名高度 ${tb.h}px 疑似折行`);
    assert.match(tb.nowrap, /nowrap/, '页名缺 white-space:nowrap');
    ok(`页名单行不折行（h=${tb.h}px, white-space=${tb.nowrap}）`);

    // 3) 点 × → 横幅消失 + localStorage TTL 落盘
    await br.evalJs(`document.querySelector('#health-banner .health-x').click()`);
    await sleep(600);
    assert.equal(await br.evalJs(`document.getElementById('health-banner').classList.contains('hidden')`),
      true, '点 × 后横幅未隐藏');
    const stored = JSON.parse(await br.evalJs(`localStorage.getItem('health.acct.dismissUntil') || '{}'`));
    assert.ok(stored['cline-cli'] > Date.now() && stored['pi'] > Date.now(), 'TTL 未落盘: ' + JSON.stringify(stored));
    ok('点 × 横幅消失，cline-cli/pi 的 12h TTL 已落盘');

    // 4) 刷新后仍隐藏（TTL 生效）
    await br.send('Page.navigate', { url: origin + '/' });
    await sleep(4000);
    assert.equal(await br.evalJs(`document.getElementById('health-banner').classList.contains('hidden')`),
      true, '刷新后横幅复现（TTL 未生效）');
    ok('刷新后横幅仍隐藏（TTL 生效）');

    // 5) 弹框路径：清 TTL + 整页刷新（横幅随 /api/state 重画）→ 横幅回来 → 弹框「隐藏账号告警」按钮同样生效
    await br.evalJs(`localStorage.removeItem('health.acct.dismissUntil')`);
    await br.send('Page.navigate', { url: origin + '/' });
    let back = false;
    for (let i = 0; i < 30 && !back; i++) {
      back = await br.evalJs(`document.getElementById('health-banner').classList.contains('alerting')`);
      if (!back) await sleep(500);
    }
    assert.ok(back, '清 TTL 后横幅未回归');
    await br.evalJs(`document.getElementById('health-banner').click()`);
    await sleep(600);
    const hasBtn = await br.evalJs(`[...document.querySelectorAll('#modal-foot button')].some(b => b.textContent.includes('隐藏账号告警'))`);
    assert.ok(hasBtn, '弹框缺「隐藏账号告警」按钮');
    await br.evalJs(`[...document.querySelectorAll('#modal-foot button')].find(b => b.textContent.includes('隐藏账号告警')).click()`);
    await sleep(600);
    assert.equal(await br.evalJs(`document.getElementById('health-banner').classList.contains('hidden')`),
      true, '弹框按钮隐藏失败');
    ok('弹框「隐藏账号告警」按钮全链路生效');
  } finally {
    await br.close();
  }
  console.log(`\n全部通过：${passed} 项`);
} finally {
  if (server && server.pid) {
    await new Promise((res) => {
      const kill = spawn('taskkill', ['/F', '/T', '/PID', String(server.pid)], { stdio: 'ignore', windowsHide: true });
      kill.on('close', res);
    });
  }
  await sleep(500);
  try { rmSync(data, { recursive: true, force: true }); } catch (e) { /* ignore */ }
  // 端口归还自证
  let freed = false;
  for (let i = 0; i < 6 && !freed; i++) {
    const r = spawn('netstat', ['-ano'], { windowsHide: true });
    // 简化：直接探连接
    try { await fetch(origin + '/api/state', { signal: AbortSignal.timeout(800) }); await sleep(500); }
    catch (e) { freed = true; }
  }
  console.log(freed ? `端口 ${port} 已归还` : `警告：端口 ${port} 未能确认释放，请按 PID 复核`);
}
