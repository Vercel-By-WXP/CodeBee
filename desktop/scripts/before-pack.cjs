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
  const electronVersion = context.electronVersion || context.packager?.config?.electronVersion || '';
  const electronMajor = Number(String(electronVersion).split('.')[0]);
  const profile = platform === 'win32' && electronMajor > 0 && electronMajor < 23 ? 'win7' : 'modern';
  const pinned = require(profile === 'win7' ? '../python-runtime-win7.json' : '../python-runtime.json');
  if (runtime.profile !== profile) throw new Error(`Prepare the ${profile} Python runtime before packaging.`);
  const expected = pinned.assets[`${platform}-${arch}`];
  if (!expected || runtime.platform !== platform || runtime.arch !== arch || runtime.sha256 !== expected.sha256) {
    throw new Error(`Prepare the native Python runtime for ${platform}-${arch} before packaging.`);
  }
  const rootPackage = require('../../package.json');
  const desktopPackage = require('../package.json');
  if (desktopPackage.version !== rootPackage.version) throw new Error('Desktop/engine version mismatch. Sync desktop/package.json first.');
};
