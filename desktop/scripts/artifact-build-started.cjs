'use strict';

const path = require('node:path');
const { createRequire } = require('node:module');
const {
  patchNsisInstallSectionFile,
  restoreNsisInstallSectionFileSync,
} = require('./nsis-stage.cjs');

const requireFromHook = createRequire(__filename);
let patchedInProcess = false;
let restoreState = null;

function isNsisTarget(event) {
  const name = event?.targetPresentableName || event?.target?.name || event?.target?.targetName || '';
  return name === 'nsis' || name === 'nsis-web';
}

function resolveInstallSectionPath() {
  const builderRoot = path.dirname(requireFromHook.resolve('app-builder-lib/package.json'));
  return path.join(builderRoot, 'templates', 'nsis', 'installSection.nsh');
}

async function artifactBuildStarted(event) {
  if (!isNsisTarget(event) || patchedInProcess) return;
  const filePath = resolveInstallSectionPath();
  const result = await patchNsisInstallSectionFile(filePath);
  patchedInProcess = true;
  if (result.changed) {
    restoreState = { filePath, originalSource: result.originalSource };
    process.once('exit', () => {
      restoreNsisInstallSectionFileSync(restoreState);
      restoreState = null;
    });
  }
}

module.exports = artifactBuildStarted;
module.exports.isNsisTarget = isNsisTarget;
module.exports.resolveInstallSectionPath = resolveInstallSectionPath;
