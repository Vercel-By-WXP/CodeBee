'use strict';

const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const os = require('node:os');
const path = require('node:path');
const yaml = require('js-yaml');
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
  assert.equal(windows.icon, path.join('/resources', 'codebee', 'icon.png'));
  assert.equal(mac.icon, windows.icon);
  assert.equal(runtimePaths(false, '/resources', '/repo/desktop', 'win32').icon,
    path.join('/repo/desktop', 'assets', 'icon-512.png'));
});

test('macOS universal runtime selects the host architecture payload', t => {
  const resources = fs.mkdtempSync(path.join(os.tmpdir(), 'cb-universal-'));
  t.after(() => fs.rmSync(resources, { recursive: true, force: true }));
  fs.mkdirSync(path.join(resources, 'python', 'arm64', 'bin'), { recursive: true });
  fs.mkdirSync(path.join(resources, 'python', 'x64', 'bin'), { recursive: true });
  assert.equal(runtimePaths(true, resources, '/desktop', 'darwin', 'arm64').python,
    path.join(resources, 'python', 'arm64', 'bin/python3'));
  assert.equal(runtimePaths(true, resources, '/desktop', 'darwin', 'x64').python,
    path.join(resources, 'python', 'x64', 'bin/python3'));
});

test('Windows and macOS installers use the shared CodeBee atom icon family', () => {
  const assets = path.join(__dirname, '..', 'assets');
  const png = fs.readFileSync(path.join(assets, 'icon-512.png'));
  const webPng = fs.readFileSync(path.join(__dirname, '..', '..', 'app', 'ui', 'icons', 'icon-512.png'));
  const ico = fs.readFileSync(path.join(assets, 'icon.ico'));
  const icns = fs.readFileSync(path.join(assets, 'icon.icns'));
  const builder = yaml.load(fs.readFileSync(path.join(__dirname, '..', 'electron-builder.yml'), 'utf8'));
  const win7 = yaml.load(fs.readFileSync(path.join(__dirname, '..', 'electron-builder.win7.yml'), 'utf8'));
  const compatible = yaml.load(fs.readFileSync(path.join(__dirname, '..', 'electron-builder.win-compatible.yml'), 'utf8'));
  assert.equal(png.subarray(0, 8).toString('hex'), '89504e470d0a1a0a');
  assert.deepEqual(webPng, png);
  assert.equal(ico.subarray(0, 4).toString('hex'), '00000100');
  assert.equal(icns.subarray(0, 4).toString('ascii'), 'icns');
  assert.equal(icns.readUInt32BE(4), icns.length);
  assert.equal(builder.win.icon, 'assets/icon.ico');
  assert.equal(builder.mac.icon, 'assets/icon.icns');
  assert.deepEqual(builder.linux.target.map(target => target.target), ['AppImage', 'deb', 'rpm']);
  assert.deepEqual(builder.linux.target.map(target => target.arch), [['x64'], ['x64'], ['x64']]);
  assert.equal(builder.linux.icon, 'assets/icon-512.png');
  assert.equal(builder.linux.maintainer, 'CodeBee <codebee@users.noreply.github.com>');
  assert.equal(JSON.parse(fs.readFileSync(path.join(__dirname, '..', 'package.json'))).homepage,
    'https://github.com/Vercel-By-WXP/CodeBee');
  const runtimeManifest = JSON.parse(fs.readFileSync(path.join(__dirname, '..', 'python-runtime.json'), 'utf8'));
  assert.equal(runtimeManifest.assets['linux-x64'].executable, 'python/bin/python3');
  assert.equal(win7.extends, 'electron-builder.win-compatible.yml');
  assert.equal(compatible.electronVersion, '22.3.27');
  assert.deepEqual(compatible.win.target[0].arch, ['x64']);
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
