'use strict';

const path = require('node:path');
const fs = require('node:fs');
const os = require('node:os');
const { execFile } = require('node:child_process');

const RELEASES_URL = 'https://github.com/Vercel-By-WXP/CodeBee/actions/workflows/desktop-preview.yml';

function dataDirectory(platform = process.platform, env = process.env, home = os.homedir()) {
  if (env.TUTTI_DATA) return path.resolve(env.TUTTI_DATA);
  const base = platform === 'win32' ? (env.APPDATA || path.join(home, 'AppData', 'Roaming')) : home;
  const legacy = path.join(base, platform === 'win32' ? 'Tutti' : '.tutti');
  return fs.existsSync(legacy) ? legacy : path.join(base, platform === 'win32' ? 'CodeBee' : '.codebee');
}

function runtimePaths(packaged, resources, desktopRoot, platform = process.platform) {
  const root = packaged ? path.join(resources, 'codebee') : path.dirname(desktopRoot);
  const runtime = packaged ? path.join(resources, 'python') : path.join(desktopRoot, 'runtime', 'python');
  return {
    root,
    script: path.join(root, 'app', 'desktop_host.py'),
    python: path.join(runtime, platform === 'win32' ? 'python.exe' : 'bin/python3'),
    icon: path.join(root, 'app', 'ui', 'icons', 'icon-512.png'),
  };
}

function isInternal(url, origin) {
  try { return Boolean(origin) && new URL(url).origin === origin; }
  catch { return false; }
}

function externalURL(url) {
  try {
    const parsed = new URL(url);
    return ['https:', 'http:'].includes(parsed.protocol) && !parsed.username && !parsed.password;
  } catch { return false; }
}

async function cliEnvironment(platform = process.platform, env = process.env, home = os.homedir()) {
  const result = { ...env };
  // The bundled interpreter must not import from a user's Python/Conda env.
  delete result.PYTHONHOME;
  delete result.PYTHONPATH;
  delete result.ELECTRON_RUN_AS_NODE;
  if (platform === 'darwin') {
    const loginPath = await new Promise(resolve => {
      execFile('/bin/zsh', ['-l', '-c', '/usr/bin/printenv PATH'],
        { timeout: 4000, maxBuffer: 65536, env: result }, (error, stdout) => {
          resolve(error ? '' : stdout.trim().split(/\r?\n/).at(-1));
        });
    });
    result.PATH = [...new Set([
      ...(loginPath || result.PATH || '').split(':'), '/opt/homebrew/bin', '/usr/local/bin',
      path.join(home, '.local', 'bin'), path.join(home, '.npm-global', 'bin'),
      path.join(home, '.cargo', 'bin'), '/usr/bin', '/bin',
    ].filter(Boolean))].join(':');
  }
  return result;
}

module.exports = { RELEASES_URL, dataDirectory, runtimePaths, isInternal, externalURL, cliEnvironment };
