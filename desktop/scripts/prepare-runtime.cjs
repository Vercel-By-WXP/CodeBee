'use strict';

const fs = require('node:fs');
const path = require('node:path');
const crypto = require('node:crypto');
const { spawnSync } = require('node:child_process');
const { Readable } = require('node:stream');
const { pipeline } = require('node:stream/promises');
const manifest = require('../python-runtime.json');

const desktopRoot = path.resolve(__dirname, '..');
const cacheRoot = path.join(desktopRoot, '.cache');
const runtimeRoot = path.join(desktopRoot, 'runtime');

async function sha256(file) {
  const hash = crypto.createHash('sha256');
  for await (const chunk of fs.createReadStream(file)) hash.update(chunk);
  return hash.digest('hex');
}

function checkInterpreter(executable, platform, arch) {
  const result = spawnSync(executable, ['-E', '-s', '-c',
    'import sys, ssl, sqlite3, platform, ctypes; print(sys.version.split()[0]); print(sys.platform); print(platform.machine())'],
  { encoding: 'utf8', timeout: 30000, windowsHide: true });
  if (result.error || result.status !== 0) throw new Error(`Python self-check failed: ${result.error || result.stderr}`);
  const [version, actualPlatform, machine] = result.stdout.trim().split(/\r?\n/);
  if (version !== manifest.version || actualPlatform !== platform ||
      !((arch === 'x64' && /^(AMD64|x86_64)$/i.test(machine)) || (arch === 'arm64' && /^(arm64|aarch64)$/i.test(machine)))) {
    throw new Error(`Wrong Python runtime: ${result.stdout}`);
  }
  console.log(`Python ${version} ${actualPlatform}/${machine}: ssl + sqlite3 + ctypes OK`);
}

async function prepare(platform = process.platform, arch = process.arch) {
  const asset = manifest.assets[`${platform}-${arch}`];
  if (!asset) throw new Error(`Unsupported desktop platform: ${platform}-${arch}`);
  fs.mkdirSync(cacheRoot, { recursive: true });
  const marker = path.join(runtimeRoot, 'manifest.json');
  if (fs.existsSync(marker)) {
    const existing = JSON.parse(fs.readFileSync(marker, 'utf8'));
    if (existing.platform === platform && existing.arch === arch && existing.sha256 === asset.sha256 &&
        fs.existsSync(path.join(runtimeRoot, asset.executable))) {
      if (platform === process.platform && arch === process.arch) checkInterpreter(path.join(runtimeRoot, asset.executable), platform, arch);
      return;
    }
  }
  const archive = path.join(cacheRoot, asset.file);
  if (!fs.existsSync(archive) || await sha256(archive) !== asset.sha256) {
    const url = `https://github.com/astral-sh/python-build-standalone/releases/download/${manifest.release}/${asset.file}`;
    console.log(`Downloading Python for ${platform}-${arch}: ${url}`);
    const partial = archive + '.partial';
    try {
      const response = await fetch(url, { signal: AbortSignal.timeout(300000) });
      if (!response.ok || !response.body) throw new Error(`Runtime download failed: HTTP ${response.status}`);
      await pipeline(Readable.fromWeb(response.body), fs.createWriteStream(partial));
      if (await sha256(partial) !== asset.sha256) throw new Error('Python runtime SHA256 mismatch');
      fs.renameSync(partial, archive);
    } finally {
      if (fs.existsSync(partial)) fs.unlinkSync(partial);
    }
  }
  console.log('SHA256 verified. Extracting Python runtime…');
  const listing = spawnSync('tar', ['-tzf', archive], { encoding: 'utf8', maxBuffer: 4194304, windowsHide: true });
  if (listing.status !== 0) throw new Error(`Cannot inspect runtime archive: ${listing.error || listing.stderr}`);
  for (const entry of listing.stdout.trim().split(/\r?\n/)) {
    if (!(entry === 'python' || entry.startsWith('python/')) || entry.split(/[\\/]/).includes('..')) {
      throw new Error(`Unsafe runtime archive entry: ${entry}`);
    }
  }
  const staging = fs.mkdtempSync(path.join(cacheRoot, 'extract-'));
  try {
    const extracted = spawnSync('tar', ['-xzf', archive, '-C', staging], { windowsHide: true, encoding: 'utf8' });
    if (extracted.status !== 0) throw new Error(`Runtime extraction failed: ${extracted.error || extracted.stderr}`);
    if (platform === process.platform && arch === process.arch) checkInterpreter(path.join(staging, asset.executable), platform, arch);
    fs.writeFileSync(path.join(staging, 'manifest.json'), JSON.stringify({
      platform, arch, version: manifest.version, release: manifest.release, sha256: asset.sha256,
    }, null, 2));
    // Both targets are fixed children of this desktop checkout, never user paths.
    fs.rmSync(runtimeRoot, { recursive: true, force: true });
    fs.renameSync(staging, runtimeRoot);
  } finally { fs.rmSync(staging, { recursive: true, force: true }); }
}

if (require.main === module) {
  prepare(process.argv[2], process.argv[3]).catch(error => { console.error(error.message); process.exitCode = 1; });
}
module.exports = { prepare, sha256, checkInterpreter };
