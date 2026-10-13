'use strict';

const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const os = require('node:os');
const path = require('node:path');
const { dataDirectory, runtimePaths, isInternal, externalURL, cliEnvironment } = require('../lib/platform.cjs');
const { machOBinaries } = require('../scripts/after-pack.cjs');

test('reuse existing CodeBee and legacy Tutti data, never the app bundle', t => {
  const temp = fs.mkdtempSync(path.join(os.tmpdir(), 'cb-platform-'));
  t.after(() => fs.rmSync(temp, { recursive: true, force: true }));
  assert.equal(dataDirectory('darwin', {}, temp), path.join(temp, '.codebee'));
  fs.mkdirSync(path.join(temp, '.tutti'));
  assert.equal(dataDirectory('darwin', {}, temp), path.join(temp, '.tutti'));
  assert.equal(dataDirectory('win32', { APPDATA: temp }, temp), path.join(temp, 'CodeBee'));
  fs.mkdirSync(path.join(temp, 'Tutti'));
  assert.equal(dataDirectory('win32', { APPDATA: temp }, temp), path.join(temp, 'Tutti'));
  assert.equal(dataDirectory('darwin', { TUTTI_DATA: temp }, temp), temp);
});

test('packaged runtime and engine stay outside asar for both OSes', () => {
  const windows = runtimePaths(true, '/resources', '/desktop', 'win32');
  const mac = runtimePaths(true, '/resources', '/desktop', 'darwin');
  assert.equal(windows.python, path.join('/resources', 'python', 'python.exe'));
  assert.equal(mac.python, path.join('/resources', 'python', 'bin/python3'));
  assert.equal(mac.script, path.join('/resources', 'codebee', 'app', 'desktop_host.py'));
});

test('restrict navigation by exact origin and reject executable external protocols', () => {
  assert.equal(isInternal('http://127.0.0.1:9876/preview', 'http://127.0.0.1:9876'), true);
  for (const url of ['http://127.0.0.1:9877/', 'http://127.0.0.1.evil:9876/', 'file:///tmp/x', 'javascript:alert(1)']) {
    assert.equal(isInternal(url, 'http://127.0.0.1:9876'), false);
  }
  assert.equal(externalURL('https://github.com/Vercel-By-WXP/CodeBee/actions/workflows/desktop-preview.yml'), true);
  for (const url of ['file:///tmp/x', 'javascript:alert(1)', 'ms-settings:test', 'https://u:p@example.com', 'broken']) {
    assert.equal(externalURL(url), false);
  }
});

test('do not leak an unrelated Python or Electron env into the sidecar', async () => {
  const result = await cliEnvironment('win32', { PATH: 'cli', PYTHONHOME: 'wrong',
    PYTHONPATH: 'wrong', ELECTRON_RUN_AS_NODE: '1' });
  assert.deepEqual(result, { PATH: 'cli' });
});

test('macOS signing enumeration includes executable + extension libs, excludes symlinks', t => {
  const root = fs.mkdtempSync(path.join(os.tmpdir(), 'cb-macho-'));
  t.after(() => fs.rmSync(root, { recursive: true, force: true }));
  fs.mkdirSync(path.join(root, 'lib'));
  fs.writeFileSync(path.join(root, 'python'), Buffer.from('cffaedfe00000000', 'hex'));
  fs.writeFileSync(path.join(root, 'lib', '_ssl.so'), Buffer.from('feedfacf00000000', 'hex'));
  fs.writeFileSync(path.join(root, 'readme'), 'documentation');
  assert.deepEqual(machOBinaries(root, 'Contents/Resources/python').sort(), [
    path.join('Contents/Resources/python', 'lib', '_ssl.so'),
    path.join('Contents/Resources/python', 'python'),
  ].sort());
});
