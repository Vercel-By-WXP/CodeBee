'use strict';

const test = require('node:test');
const assert = require('node:assert/strict');
const {
  compareVersions,
  versionTuple,
  assetForPlatform,
  downloadInstaller,
} = require('../lib/installer.cjs');

test('desktop updater compares release versions and ignores build suffixes', () => {
  assert.deepEqual(versionTuple('v1.2.3'), [1, 2, 3]);
  assert.equal(compareVersions('1.2.4', '1.2.3'), 1);
  assert.equal(compareVersions('1.2.3-beta.1', '1.2.3'), 0);
  assert.equal(compareVersions('0.9.9', '1.0.0'), -1);
});

test('desktop updater selects an installer for each supported desktop architecture', () => {
  const release = { assets: [
    { name: 'CodeBee-1.2.3-win-x64.exe' },
    { name: 'CodeBee-1.2.3-mac-arm64.dmg' },
    { name: 'CodeBee-1.2.3-mac-arm64.zip' },
    { name: 'CodeBee-1.2.3-mac-x64.dmg' },
    { name: 'CodeBee-1.2.3-mac-universal.dmg' },
  ] };
  assert.equal(assetForPlatform(release, 'win32', 'x64'), 'CodeBee-1.2.3-win-x64.exe');
  assert.equal(assetForPlatform(release, 'darwin', 'arm64'), 'CodeBee-1.2.3-mac-arm64.zip');
  assert.equal(assetForPlatform(release, 'darwin', 'x64'), 'CodeBee-1.2.3-mac-universal.dmg');
  assert.equal(assetForPlatform({ assets: [
    { name: 'CodeBee-1.2.3-linux-x64.deb' },
    { name: 'CodeBee-1.2.3-linux-x64.AppImage' },
    { name: 'CodeBee-1.2.3-linux-x64.rpm' },
  ] }, 'linux', 'x64', { APPIMAGE: '/opt/CodeBee.AppImage' }), 'CodeBee-1.2.3-linux-x64.AppImage');
  assert.equal(assetForPlatform({ assets: [
    { name: 'CodeBee-1.2.3-linux-x64.deb' },
    { name: 'CodeBee-1.2.3-linux-x64.rpm' },
  ] }, 'linux', 'x64', {}, 'fedora'), 'CodeBee-1.2.3-linux-x64.rpm');
  assert.equal(assetForPlatform({ assets: [
    { name: 'CodeBee-1.2.3-linux-x64.deb' },
    { name: 'CodeBee-1.2.3-linux-x64.rpm' },
  ] }, 'linux', 'x64', {}, 'ubuntu'), 'CodeBee-1.2.3-linux-x64.deb');
  assert.equal(assetForPlatform(release, 'linux', 'arm64'), '');
});

test('desktop updater requires a release-provided SHA-256 digest before downloading', async () => {
  await assert.rejects(
    () => downloadInstaller({ assetName: 'CodeBee-1.2.3-win-x64.exe',
      downloadUrl: 'https://github.com/example/app.exe' }),
    /缺少 SHA-256/,
  );
});

test('desktop updater rejects installer URLs outside GitHub release assets', async () => {
  await assert.rejects(
    () => downloadInstaller({ assetName: 'CodeBee-1.2.3-win-x64.exe',
      digest: '0'.repeat(64), downloadUrl: 'https://example.com/app.exe' }),
    /来源不受信任/,
  );
});
