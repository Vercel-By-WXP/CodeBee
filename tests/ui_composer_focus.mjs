/* Composer focus regression: the workspace path and prompt fields stay visually quiet. */
import assert from 'node:assert/strict';
import { spawn } from 'node:child_process';
import { mkdtempSync, rmSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import { fileURLToPath } from 'node:url';
import { bootEdge } from './_ui_boot.mjs';

const root = fileURLToPath(new URL('../', import.meta.url));
const data = mkdtempSync(join(tmpdir(), 'codebee-composer-focus-'));
const port = Number(process.env.TUTTI_TEST_PORT || 18987);
const origin = `http://127.0.0.1:${port}`;
const sleep = ms => new Promise(resolve => setTimeout(resolve, ms));
let browser, server;

try {
  server = spawn('python', ['app/main.py', '--port', String(port)], {
    cwd: root, stdio: 'ignore', windowsHide: true, env: { ...process.env, TUTTI_DATA: data },
  });
  for (let i = 0; i < 60; i++) {
    try { if ((await fetch(origin + '/api/state')).ok) break; } catch {}
    await sleep(250);
  }
  browser = await bootEdge({ width: 1400, height: 950, awaitPromise: true, reducedMotion: true });
  await browser.send('Page.navigate', { url: origin });
  for (let i = 0; i < 40; i++) {
    if (await browser.evalJs("Boolean(document.querySelector('#sub-tasks .cmp-wd input'))")) break;
    await sleep(150);
  }
  await browser.evalJs(`(() => {
    welcomeClose();
    document.getElementById('page-settings').classList.remove('hidden');
    document.querySelectorAll('#page-settings > .subpage').forEach(page =>
      page.classList.toggle('hidden', page.id !== 'sub-tasks'));
  })()`);

  const result = await browser.evalJs(`(() => { try {
    const box = document.querySelector('#sub-tasks .cmp-box');
    const path = document.querySelector('#sub-tasks .cmp-wd input');
    const pathChip = path.closest('.cmp-wd');
    const pathPick = pathChip.querySelector('.wd-pick-btn');
    const prompt = box.querySelector('textarea');
    const command = document.querySelector('#cmdk-q');
    const styles = el => {
      const css = getComputedStyle(el);
      return { outline: css.outlineStyle, shadow: css.boxShadow,
        borderColor: css.borderColor, backgroundColor: css.backgroundColor };
    };
    const before = styles(box);
    const pathBefore = styles(path);
    const commandBefore = styles(command);
    const pathChipBefore = styles(pathChip);
    const pathPickBefore = getComputedStyle(pathPick).opacity;
    path.focus({ focusVisible: true });
    const pathFocus = styles(path);
    const pathBox = styles(box);
    const pathChipFocus = styles(pathChip);
    const pathPickFocus = getComputedStyle(pathPick).opacity;
    prompt.focus({ focusVisible: true });
    const promptFocus = styles(prompt);
    const promptBox = styles(box);
    document.getElementById('cmdk-mask').classList.remove('hidden');
    command.focus({ focusVisible: true });
    const commandFocus = styles(command);
    return JSON.stringify({ before, pathBefore, pathFocus, pathBox, commandBefore, pathChipBefore, pathChipFocus,
      pathPickBefore, pathPickFocus, promptFocus, promptBox, commandFocus });
  } catch (error) { return JSON.stringify({ error: String(error), stack: error.stack }); }
  })()`);
  const styles = JSON.parse(result);
  assert.equal(styles.error, undefined, styles.stack || styles.error);
  for (const field of ['pathFocus', 'promptFocus', 'commandFocus']) {
    assert.equal(styles[field].outline, 'none', `${field} has no focus outline`);
    assert.equal(styles[field].shadow, 'none', `${field} has no focus glow`);
  }
  assert.deepEqual(styles.pathBox, styles.before, 'workspace path focus does not alter the composer frame');
  assert.deepEqual(styles.pathChipFocus, styles.pathChipBefore, 'workspace path focus does not alter its chip');
  assert.equal(styles.pathPickFocus, styles.pathPickBefore, 'workspace path focus does not reveal a second picker button');
  assert.deepEqual(styles.pathFocus, styles.pathBefore, 'workspace path focus does not alter the input appearance');
  assert.deepEqual(styles.commandFocus, styles.commandBefore, 'command search focus does not alter the input appearance');
  assert.deepEqual(styles.promptBox, styles.before, 'prompt focus does not alter the composer frame');
  console.log('PASS composer fields keep their normal appearance while focused', JSON.stringify(styles));
} finally {
  if (browser) await browser.close();
  if (server) {
    if (server.exitCode === null && server.signalCode === null) {
      const stopped = new Promise(resolve => {
        const timeout = setTimeout(() => resolve('timeout'), 3000);
        server.once('exit', () => { clearTimeout(timeout); resolve('exit'); });
      });
      server.kill();
      await stopped;
    }
  }
  rmSync(data, { recursive: true, force: true });
}
