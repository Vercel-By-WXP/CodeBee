'use strict';

const fs = require('node:fs');
const crypto = require('node:crypto');
const os = require('node:os');
const path = require('node:path');
const { spawn } = require('node:child_process');
const http = require('node:http');
const https = require('node:https');

const REPOSITORY = 'Vercel-By-WXP/CodeBee';
const RELEASE_API = `https://api.github.com/repos/${REPOSITORY}/releases`;
const RELEASES_URL = `https://github.com/${REPOSITORY}/releases`;
const MAX_METADATA_BYTES = 2 * 1024 * 1024;
const MAX_INSTALLER_BYTES = 2 * 1024 * 1024 * 1024;
const API_HOSTS = new Set(['api.github.com']);
const ASSET_HOSTS = new Set(['github.com', 'objects.githubusercontent.com', 'release-assets.githubusercontent.com']);

function versionTuple(version) {
  const match = String(version || '').replace(/^v/i, '').match(/^(\d+)\.(\d+)\.(\d+)(?:[-+].*)?$/);
  return match ? match.slice(1, 4).map(Number) : [0, 0, 0];
}

function compareVersions(left, right) {
  const a = versionTuple(left);
  const b = versionTuple(right);
  for (let i = 0; i < 3; i += 1) {
    if (a[i] !== b[i]) return a[i] - b[i];
  }
  return 0;
}

function request(url, { maxBytes, timeout = 30000, headers = {}, allowedHosts = API_HOSTS } = {}, redirects = 4) {
  return new Promise((resolve, reject) => {
    let parsed;
    try { parsed = new URL(url); } catch { reject(new Error('更新地址无效')); return; }
    if (parsed.protocol !== 'https:' || !allowedHosts.has(parsed.hostname)) {
      reject(new Error('更新地址来源不受信任'));
      return;
    }
    const client = parsed.protocol === 'https:' ? https : http;
    const req = client.get(parsed, {
      timeout,
      headers: { 'User-Agent': 'CodeBee-Desktop-Updater/1.0', ...headers },
    }, response => {
      const status = Number(response.statusCode || 0);
      if (status >= 300 && status < 400 && response.headers.location && redirects > 0) {
        response.resume();
        request(new URL(response.headers.location, parsed).toString(),
          { maxBytes, timeout, headers, allowedHosts }, redirects - 1).then(resolve, reject);
        return;
      }
      if (status !== 200) {
        response.resume();
        reject(new Error(`更新下载失败 HTTP ${status}`));
        return;
      }
      const chunks = [];
      let size = 0;
      response.on('data', chunk => {
        size += chunk.length;
        if (size > maxBytes) {
          req.destroy(new Error('更新文件超过大小限制'));
          return;
        }
        chunks.push(chunk);
      });
      response.on('end', () => resolve(Buffer.concat(chunks)));
      response.on('error', reject);
    });
    req.setTimeout(timeout, () => req.destroy(new Error('更新下载超时')));
    req.on('error', reject);
  });
}

function linuxDistro() {
  try {
    const release = fs.readFileSync('/etc/os-release', 'utf8');
    return release.match(/^ID=(.*)$/m)?.[1]?.replace(/^['"]|['"]$/g, '').toLowerCase() || '';
  } catch { return ''; }
}

function assetForPlatform(release, platform = process.platform, arch = process.arch, env = process.env, distro = linuxDistro()) {
  const assets = Array.isArray(release?.assets) ? release.assets : [];
  const names = assets.map(asset => String(asset?.name || ''));
  if (platform === 'win32' && arch === 'x64') {
    return names.find(name => /^CodeBee-\d+\.\d+\.\d+-win-x64\.exe$/i.test(name)) || '';
  }
  if (platform === 'darwin' && ['arm64', 'x64'].includes(arch)) {
    return names.find(name => /^CodeBee-\d+\.\d+\.\d+-mac-universal\.zip$/i.test(name)) ||
      names.find(name => new RegExp(`^CodeBee-\\d+\\.\\d+\\.\\d+-mac-${arch}\\.zip$`, 'i').test(name)) ||
      names.find(name => /^CodeBee-\d+\.\d+\.\d+-mac-universal\.dmg$/i.test(name)) ||
      names.find(name => new RegExp(`^CodeBee-\\d+\\.\\d+\\.\\d+-mac-${arch}\\.dmg$`, 'i').test(name)) || '';
  }
  if (platform === 'linux' && arch === 'x64') {
    const installedAppImage = Boolean(env.APPIMAGE);
    const rpmDistro = /^(fedora|rhel|centos|rocky|almalinux|opensuse|sles|suse)$/i.test(distro);
    const formats = installedAppImage ? ['AppImage'] : rpmDistro ? ['rpm', 'deb'] : ['deb', 'rpm'];
    for (const format of formats) {
      const asset = names.find(name => new RegExp(`^CodeBee-\\d+\\.\\d+\\.\\d+-linux-x64\\.${format}$`, 'i').test(name));
      if (asset) return asset;
    }
    return '';
  }
  return '';
}

function releaseVersion(release, assetName) {
  const fromAsset = String(assetName || '').match(/CodeBee-(\d+\.\d+\.\d+)-/i);
  return fromAsset ? fromAsset[1] : String(release?.tag_name || '').replace(/^v/i, '');
}

async function latestRelease({ platform = process.platform, arch = process.arch, currentVersion = '' } = {}) {
  const raw = await request(`${RELEASE_API}?per_page=20`, {
    maxBytes: MAX_METADATA_BYTES,
    headers: { Accept: 'application/vnd.github+json' },
    allowedHosts: API_HOSTS,
  });
  let releases;
  try { releases = JSON.parse(raw.toString('utf8')); } catch { throw new Error('更新信息格式无效'); }
  if (!Array.isArray(releases)) throw new Error('更新信息格式无效');
  const candidates = releases.filter(release => !release?.draft)
    .map(release => {
      const assetName = assetForPlatform(release, platform, arch);
      const asset = (release.assets || []).find(item => item?.name === assetName);
      if (!assetName || !asset?.browser_download_url) return null;
      const digest = String(asset.digest || '').match(/^sha256:([a-f0-9]{64})$/i)?.[1]?.toLowerCase() || '';
      if (!digest) return null;
      const version = releaseVersion(release, assetName);
      return {
        version,
        tag: String(release.tag_name || ''),
        prerelease: Boolean(release.prerelease),
        releaseUrl: String(release.html_url || RELEASES_URL),
        assetName,
        downloadUrl: String(asset.browser_download_url),
        size: Number(asset.size || 0),
        digest,
      };
    }).filter(Boolean)
    .filter(release => versionTuple(release.version).some(value => value > 0))
    .sort((a, b) => compareVersions(b.version, a.version));
  const latest = candidates[0] || null;
  if (!latest) return null;
  return { ...latest, hasUpdate: compareVersions(latest.version, currentVersion) > 0 };
}

function assertTrustedAssetUrl(url) {
  const parsed = new URL(url);
  if (parsed.protocol !== 'https:' ||
      !ASSET_HOSTS.has(parsed.hostname)) {
    throw new Error('更新文件来源不受信任');
  }
}

async function downloadInstaller(release, { directory = os.tmpdir(), onProgress } = {}) {
  if (!release?.downloadUrl || !release.assetName || !/^[a-f0-9]{64}$/i.test(String(release.digest || ''))) {
    throw new Error('安装包缺少 SHA-256 校验摘要');
  }
  assertTrustedAssetUrl(release.downloadUrl);
  const workDir = fs.mkdtempSync(path.join(directory, 'codebee-installer-'));
  const finalPath = path.join(workDir, path.basename(release.assetName));
  const tempPath = `${finalPath}.download`;
  const expectedDigest = String(release.digest).toLowerCase();
  const download = (url, redirects = 4) => new Promise((resolve, reject) => {
    const parsed = new URL(url);
    assertTrustedAssetUrl(parsed.toString());
    const client = parsed.protocol === 'https:' ? https : http;
    const req = client.get(parsed, {
      timeout: 15 * 60 * 1000,
      headers: { 'User-Agent': 'CodeBee-Desktop-Updater/1.0', Accept: 'application/octet-stream' },
    }, response => {
      const status = Number(response.statusCode || 0);
      if (status >= 300 && status < 400 && response.headers.location && redirects > 0) {
        response.resume();
        const redirected = new URL(response.headers.location, parsed).toString();
        download(redirected, redirects - 1).then(resolve, reject);
        return;
      }
      if (status !== 200) {
        response.resume();
        reject(new Error(`安装包下载失败 HTTP ${status}`));
        return;
      }
      const total = Number(response.headers['content-length'] || release.size || 0);
      let received = 0;
      const hash = crypto.createHash('sha256');
      const file = fs.createWriteStream(tempPath, { flags: 'wx' });
      const fail = error => {
        file.destroy();
        fs.rmSync(workDir, { recursive: true, force: true });
        reject(error);
      };
      response.on('data', chunk => {
        received += chunk.length;
        hash.update(chunk);
        if (received > MAX_INSTALLER_BYTES) {
          req.destroy(new Error('安装包超过大小限制'));
          return;
        }
        if (typeof onProgress === 'function') onProgress({ received, total });
      });
      response.on('error', fail);
      file.on('error', fail);
      file.on('finish', () => file.close(() => {
        try {
          const actualDigest = hash.digest('hex').toLowerCase();
          if (actualDigest !== expectedDigest) {
            fail(new Error('安装包 SHA-256 校验失败'));
            return;
          }
          fs.renameSync(tempPath, finalPath);
          resolve(finalPath);
        } catch (error) { fail(error); }
      }));
      response.pipe(file);
    });
    req.setTimeout(15 * 60 * 1000, () => req.destroy(new Error('安装包下载超时')));
    req.on('error', error => { fs.rmSync(workDir, { recursive: true, force: true }); reject(error); });
  });
  await download(release.downloadUrl);
  return finalPath;
}

function launchInstaller(installerPath, parentPid = process.pid, platform = process.platform) {
  if (!fs.existsSync(installerPath)) throw new Error('安装包文件不存在');
  if (platform === 'win32') {
    const command = process.env.ComSpec || 'cmd.exe';
    const scriptPath = path.join(os.tmpdir(), `codebee-install-${Date.now()}.cmd`);
    const quoted = installerPath.replace(/"/g, '""');
    const quotedWorkDir = path.dirname(installerPath).replace(/"/g, '""');
    const script = [
      '@echo off',
      ':wait',
      `tasklist /FI "PID eq ${Number(parentPid)}" | findstr /R /C:" ${Number(parentPid)} " >nul`,
      'if not errorlevel 1 (timeout /t 1 /nobreak >nul & goto wait)',
      `start "" /wait "${quoted}"`,
      `del /q "${quoted}" >nul 2>nul`,
      `rmdir /s /q "${quotedWorkDir}" >nul 2>nul`,
      'del "%~f0"',
      '',
    ].join('\r\n');
    fs.writeFileSync(scriptPath, script, 'utf8');
    const child = spawn(command, ['/d', '/c', scriptPath], {
      detached: true, stdio: 'ignore', windowsHide: true,
    });
    child.unref();
    return true;
  }
  if (platform === 'darwin') {
    if (path.extname(installerPath).toLowerCase() === '.zip' && process.execPath.includes('.app/')) {
      const appBundle = path.resolve(path.dirname(process.execPath), '..', '..');
      const workDir = path.dirname(installerPath);
      const scriptPath = path.join(os.tmpdir(), `codebee-install-${Date.now()}.sh`);
      const quote = value => `'${String(value).replace(/'/g, "'\\''")}'`;
      const script = [
        '#!/bin/sh',
        'set -eu',
        `parent=${Number(parentPid)}`,
        `zip=${quote(installerPath)}`,
        `work=${quote(workDir)}`,
        `target=${quote(appBundle)}`,
        'while kill -0 "$parent" 2>/dev/null; do sleep 1; done',
        'stage="${work}/expanded"',
        'rm -rf "$stage"',
        'mkdir -p "$stage"',
        '/usr/bin/unzip -q "$zip" -d "$stage"',
        'source="$stage/CodeBee.app"',
        'if [ ! -d "$source" ]; then /usr/bin/open "$zip"; exit 1; fi',
        'replacement="${target}.codebee-new"',
        'rm -rf "$replacement"',
        'if ! /usr/bin/ditto "$source" "$replacement"; then /usr/bin/open "$zip"; exit 1; fi',
        'if ! rm -rf "$target" || ! mv "$replacement" "$target"; then /usr/bin/open "$zip"; exit 1; fi',
        '/usr/bin/open "$target"',
        'rm -rf "$work"',
        'rm -f "$0"',
        '',
      ].join('\n');
      fs.writeFileSync(scriptPath, script, { encoding: 'utf8', mode: 0o700 });
      const child = spawn('/bin/sh', [scriptPath], { detached: true, stdio: 'ignore' });
      child.unref();
      return true;
    }
    const child = spawn('open', [installerPath], { detached: true, stdio: 'ignore' });
    child.unref();
    return true;
  }
  if (platform === 'linux') {
    const extension = path.extname(installerPath).toLowerCase();
    const quote = value => `'${String(value).replace(/'/g, "'\\''")}'`;
    try { fs.chmodSync(installerPath, 0o755); } catch (error) {
      throw new Error(`无法准备 Linux 安装包：${error.message}`);
    }
    const target = process.env.APPIMAGE && extension === '.appimage' ? path.resolve(process.env.APPIMAGE) : '';
    if (target) {
      try { fs.accessSync(path.dirname(target), fs.constants.W_OK); } catch {
        throw new Error('当前 AppImage 安装目录不可写，请从发布页下载新版并手动替换当前文件');
      }
    }
    const script = [
      '#!/bin/sh',
      'set -eu',
      `parent=${Number(parentPid)}`,
      `installer=${quote(installerPath)}`,
      'while kill -0 "$parent" 2>/dev/null; do sleep 1; done',
      ...(target ? [
        `target=${quote(target)}`,
        'replacement="${target}.codebee-new"',
        'cp "$installer" "$replacement"',
        'chmod 755 "$replacement"',
        'mv -f "$replacement" "$target"',
        'rm -rf ' + quote(path.dirname(installerPath)),
        'exec "$target"',
      ] : [
        `installer=${quote(installerPath)}`,
        extension === '.appimage' ? '"$installer"' : 'xdg-open "$installer"',
      ]),
      '',
    ].join('\n');
    const scriptPath = path.join(os.tmpdir(), `codebee-install-${Date.now()}-${process.pid}.sh`);
    fs.writeFileSync(scriptPath, script, { encoding: 'utf8', mode: 0o700 });
    const child = spawn('/bin/sh', [scriptPath], { detached: true, stdio: 'ignore' });
    child.unref();
    return true;
  }
  throw new Error('当前平台没有桌面安装器');
}

module.exports = {
  RELEASE_API,
  RELEASES_URL,
  compareVersions,
  versionTuple,
  assetForPlatform,
  linuxDistro,
  latestRelease,
  downloadInstaller,
  launchInstaller,
};
