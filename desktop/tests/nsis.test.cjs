'use strict';

const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const fsp = fs.promises;
const os = require('node:os');
const path = require('node:path');
const {
  PATCH_MARKER,
  patchNsisInstallSectionFile,
  patchNsisInstallSectionSource,
  restoreNsisInstallSectionFileSync,
} = require('../scripts/nsis-stage.cjs');
const hook = require('../scripts/artifact-build-started.cjs');

test('NSIS stage patch is anchored and idempotent', () => {
  const source = fs.readFileSync(require.resolve('app-builder-lib/templates/nsis/installSection.nsh'), 'utf8');
  const patched = patchNsisInstallSectionSource(source);
  assert.notEqual(patched, source);
  assert.match(patched, new RegExp(PATCH_MARKER.replace(/[.*+?^${}()|[\]\\]/g, '\\$&')));
  assert.match(patched, /customInstallCleanupStarted/);
  assert.match(patched, /customInstallExtractCompleted/);
  assert.equal(patchNsisInstallSectionSource(patched), patched);
});

test('NSIS stage patch restores the original bytes exactly', async () => {
  const sourcePath = require.resolve('app-builder-lib/templates/nsis/installSection.nsh');
  const original = await fsp.readFile(sourcePath);
  const tempRoot = await fsp.mkdtemp(path.join(os.tmpdir(), 'codebee-nsis-'));
  const filePath = path.join(tempRoot, 'installSection.nsh');
  try {
    await fsp.writeFile(filePath, original);
    const result = await patchNsisInstallSectionFile(filePath);
    assert.equal(result.changed, true);
    assert.notDeepEqual(await fsp.readFile(filePath), original);
    restoreNsisInstallSectionFileSync({ filePath, originalSource: result.originalSource });
    assert.deepEqual(await fsp.readFile(filePath), original);
  } finally {
    await fsp.rm(tempRoot, { recursive: true, force: true });
  }
});

test('artifact hook handles only NSIS targets', () => {
  assert.equal(hook.isNsisTarget({ targetPresentableName: 'nsis' }), true);
  assert.equal(hook.isNsisTarget({ targetPresentableName: 'nsis-web' }), true);
  assert.equal(hook.isNsisTarget({ targetPresentableName: 'zip' }), false);
  assert.match(fs.readFileSync(path.join(__dirname, '..', 'electron-builder.yml'), 'utf8'), /artifactBuildStarted:\s+scripts\/artifact-build-started\.cjs/);
});

test('artifact hook patches the real template once and restores it on exit', async () => {
  const template = hook.resolveInstallSectionPath();
  const original = await fsp.readFile(template);
  const listeners = new Set(process.listeners('exit'));
  try {
    await hook({ targetPresentableName: 'nsis' });
    assert.notDeepEqual(await fsp.readFile(template), original);
    await hook({ targetPresentableName: 'nsis-web' });
    for (const listener of process.listeners('exit')) {
      if (!listeners.has(listener)) {
        process.removeListener('exit', listener);
        listener(0);
      }
    }
    assert.deepEqual(await fsp.readFile(template), original);
  } finally {
    for (const listener of process.listeners('exit')) {
      if (!listeners.has(listener)) process.removeListener('exit', listener);
    }
    await fsp.writeFile(template, original);
  }
});
