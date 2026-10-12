'use strict';

const fs = require('node:fs');
const path = require('node:path');
const { Arch } = require('builder-util');

module.exports = async function beforePack(context) {
  const desktopRoot = path.resolve(__dirname, '..');
  const platform = context.electronPlatformName;
  const arch = Arch[context.arch];
  const manifestFile = path.join(desktopRoot, 'runtime', 'manifest.json');
  if (!fs.existsSync(manifestFile)) throw new Error('Run npm run prepare:runtime before packaging.');
  const runtime = JSON.parse(fs.readFileSync(manifestFile, 'utf8'));
  const pinned = require('../python-runtime.json');
  const expected = pinned.assets[`${platform}-${arch}`];
  if (!expected || runtime.platform !== platform || runtime.arch !== arch || runtime.sha256 !== expected.sha256) {
    throw new Error(`Prepare the native Python runtime for ${platform}-${arch} before packaging.`);
  }
  const rootPackage = require('../../package.json');
  const desktopPackage = require('../package.json');
  if (desktopPackage.version !== rootPackage.version) throw new Error('Desktop/engine version mismatch. Sync desktop/package.json first.');
};
