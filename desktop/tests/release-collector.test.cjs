'use strict';

const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs/promises');
const os = require('node:os');
const path = require('node:path');

async function collector() {
  return import('../../scripts/collect_desktop_release.mjs');
}

const names = [
  'CodeBee-0.1.116-win-x64.exe',
  'CodeBee-0.1.116-mac-arm64.dmg',
  'CodeBee-0.1.116-mac-arm64.zip',
  'CodeBee-0.1.116-mac-x64.dmg',
  'CodeBee-0.1.116-mac-x64.zip',
  'CodeBee-0.1.116-linux-x64.AppImage',
  'CodeBee-0.1.116-linux-x64.deb',
  'CodeBee-0.1.116-linux-x64.rpm',
];

async function fixture() {
  const root = await fs.mkdtemp(path.join(os.tmpdir(), 'codebee-release-'));
  const inputDir = path.join(root, 'artifacts');
  const outputDir = path.join(root, 'release');
  await fs.mkdir(inputDir, { recursive: true });
  for (const [index, name] of names.entries()) {
    await fs.mkdir(path.join(inputDir, `job-${index}`), { recursive: true });
    await fs.writeFile(path.join(inputDir, `job-${index}`, name), `asset-${index}`.repeat(3));
  }
  const rootPackagePath = path.join(root, 'package.json');
  const desktopPackagePath = path.join(root, 'desktop-package.json');
  await fs.writeFile(rootPackagePath, JSON.stringify({ version: '0.1.116' }));
  await fs.writeFile(desktopPackagePath, JSON.stringify({ version: '0.1.116' }));
  return { root, inputDir, outputDir, rootPackagePath, desktopPackagePath };
}

test('collector copies all normalized assets and writes checksum/manifest records', async () => {
  const { collectRelease } = await collector();
  const data = await fixture();
  try {
    const result = await collectRelease({ ...data, tag: 'v0.1.116', environment: { SOURCE_DATE_EPOCH: '0', GITHUB_SHA: 'abc' } });
    assert.equal(result.assets.length, 8);
    assert.match(await fs.readFile(path.join(data.outputDir, 'SHA512SUMS.txt'), 'utf8'), /CodeBee-0\.1\.116-win-x64\.exe/);
    assert.equal(JSON.parse(await fs.readFile(path.join(data.outputDir, 'desktop-builds.json'))).tag, 'v0.1.116');
    assert.equal(JSON.parse(await fs.readFile(path.join(data.outputDir, 'build-record.json'))).commit, 'abc');
  } finally { await fs.rm(data.root, { recursive: true, force: true }); }
});

test('collector rejects duplicate names, invalid names, and version/tag mismatches', async () => {
  const { collectRelease } = await collector();
  const data = await fixture();
  try {
    await fs.mkdir(path.join(data.inputDir, 'duplicate'), { recursive: true });
    await fs.copyFile(path.join(data.inputDir, 'job-0', names[0]), path.join(data.inputDir, 'duplicate', names[0]));
    await assert.rejects(() => collectRelease({ ...data, tag: 'v0.1.116' }), /重复产物/);
    await fs.rm(path.join(data.inputDir, 'duplicate'), { recursive: true });
    await fs.writeFile(path.join(data.inputDir, 'CodeBee-0.1.116-linux-x64.tar.zip'), 'bad');
    await assert.rejects(() => collectRelease({ ...data, tag: 'v0.1.116' }), /文件名不符合规范/);
    await fs.rm(path.join(data.inputDir, 'CodeBee-0.1.116-linux-x64.tar.zip'));
    await assert.rejects(() => collectRelease({ ...data, tag: 'v0.1.115' }), /tag 版本/);
  } finally { await fs.rm(data.root, { recursive: true, force: true }); }
});
