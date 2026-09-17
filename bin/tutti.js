#!/usr/bin/env node
'use strict';
/*
 * CodeBee 启动垫片：在本机找 Python 3.8+，以子进程方式运行包内 app/main.py。
 * npm 只负责分发与版本管理，不携带解释器——所以运行时才探测 python，
 * 找不到就报错并给出安装指引（不写 postinstall，避免在别人机器上制造环境事故）。
 *
 * 安全边界：
 *  - 子进程可执行文件全部是本文件内的字面量（py / python / python3）——包括
 *    TUTTI_PYTHON 覆盖时：它只允许在这三个字面量里选一个，绝不拼接任意路径；
 *  - 参数数组全由字面量常量拼成，不启用 shell；
 *  - 探测只传 `--version`（不向解释器传递任何代码）；Windows 商店的 python.exe
 *    假占位会非零退出，被 catch 后自然跳过；
 *  - 用户参数逐个过白名单校验（仅字母数字与 _ - . : = / @ % 空格），
 *    含 shell 元字符/控制字符的参数直接拒绝。
 */
const { execFileSync, spawn } = require('child_process');
const path = require('path');

const MIN_MAJOR = 3;
const MIN_MINOR = 8;
const MAIN = path.join(__dirname, '..', 'app', 'main.py');
const SAFE_ARG = /^[A-Za-z0-9_\-.:=/@% ]+$/;

// 解析 `--version` 输出：形如 "Python 3.12.4"，返回 {ver, major, minor} 或 null。
// 纯字符串切分，不用正则匹配执行路径。
function parseVersion(text) {
  const at = String(text).indexOf('Python ');
  if (at === -1) return null;
  const parts = String(text).slice(at + 7).trim().split(' ')[0].split('.');
  const major = Number(parts[0]);
  const minor = Number(parts[1]);
  if (!Number.isFinite(major) || !Number.isFinite(minor)) return null;
  return { ver: major + '.' + minor, major: major, minor: minor };
}

function probePy() {
  try {
    return parseVersion(execFileSync('py', ['-3', '--version'],
      { encoding: 'utf8', stdio: ['ignore', 'pipe', 'ignore'] }));
  } catch (e) { return null; }
}
function probePython() {
  try {
    return parseVersion(execFileSync('python', ['--version'],
      { encoding: 'utf8', stdio: ['ignore', 'pipe', 'ignore'] }));
  } catch (e) { return null; }
}
function probePython3() {
  try {
    return parseVersion(execFileSync('python3', ['--version'],
      { encoding: 'utf8', stdio: ['ignore', 'pipe', 'ignore'] }));
  } catch (e) { return null; }
}

// 探测顺序：win32 优先 py 启动器（跨版本选真 3.x），其余按 python3 → python。
function resolvePython() {
  const order = process.platform === 'win32'
    ? [['py', probePy], ['python', probePython], ['python3', probePython3]]
    : [['python3', probePython3], ['python', probePython]];
  for (const [name, probe] of order) {
    const hit = probe();
    if (!hit) continue;
    if (hit.major > MIN_MAJOR || (hit.major === MIN_MAJOR && hit.minor >= MIN_MINOR)) {
      return { name: name };
    }
    return { tooOld: true, name: name, ver: hit.ver };
  }
  return null;
}

function guide() {
  const url = { win32: 'https://www.python.org/downloads/windows/',
    darwin: 'https://www.python.org/downloads/macos/',
    linux: 'https://docs.python.org/3/using/unix.html' }[process.platform]
    || 'https://www.python.org/downloads/';
  return 'CodeBee 需要本机安装 Python 3.8+（npm 包不含解释器）。\n'
    + '  · 安装：' + url + '\n'
    + '  · Windows 安装时勾选 “Add python.exe to PATH”（自带 py 启动器）\n'
    + '装好后重新运行：codebee';
}

const py = resolvePython();
if (!py) {
  console.error('[CodeBee] 未找到可用的 Python。\n' + guide());
  process.exit(1);
}
if (py.tooOld) {
  console.error('[CodeBee] 找到 ' + py.name + '（' + py.ver + '），但版本过低。\n' + guide());
  process.exit(1);
}

const userArgs = [];
for (const a of process.argv.slice(2)) {
  if (typeof a === 'string' && SAFE_ARG.test(a)) {
    userArgs.push(a);
  } else {
    console.error('[CodeBee] 参数含非法字符，已拒绝：' + String(a).slice(0, 80));
    process.exit(2);
  }
}

// CLI 转发契约：codebee 之后的用户参数（已过白名单）原样作为 main.py 的 argv。
// 控制台日志即见：GBK 控制台下强制 UTF-8 输出（否则中文 print 触发 UnicodeEncodeError 静默卡死）。
const childEnv = Object.assign({}, process.env, { PYTHONIOENCODING: 'utf-8', PYTHONUTF8: '1', PYTHONUNBUFFERED: '1' });
// TUTTI_PYTHON 覆盖解释器：只允许在三个字面量里选（py/python/python3，.exe
// 后缀可选、大小写不限）——环境变量永远只做「选择题」，不做「填空题」；
// 机器上有多个解释器且探测选错时，用它切换。
let exe = py.name;
const ovRaw = String(process.env.TUTTI_PYTHON || '').trim().toLowerCase();
const ov = ovRaw.endsWith('.exe') ? ovRaw.slice(0, -4) : ovRaw;
if (ov === 'python') {
  exe = 'python';
} else if (ov === 'python3') {
  exe = 'python3';
} else if (ov === 'py') {
  exe = 'py';
}

let child;
if (exe === 'py') {
  child = spawn('py', ['-3', MAIN, ...userArgs], { stdio: 'inherit', env: childEnv });
} else if (exe === 'python') {
  child = spawn('python', [MAIN, ...userArgs], { stdio: 'inherit', env: childEnv });
} else {
  child = spawn('python3', [MAIN, ...userArgs], { stdio: 'inherit', env: childEnv });
}
console.log('[CodeBee] 正在启动（Python: ' + exe + '）… 浏览器将自动打开 http://127.0.0.1:8765；'
  + '本窗口保持开着就是服务，Ctrl+C 退出。');

for (const sig of ['SIGINT', 'SIGTERM']) {
  process.on(sig, () => { try { child.kill(sig); } catch (e) { /* 已退出 */ } });
}
const t0 = Date.now();
child.on('exit', (code, signal) => {
  if (!signal && code != null && code !== 0 && Date.now() - t0 < 4000) {
    // 秒退：多半是 py 启动器指到了坏解释器（如损坏的 Anaconda）。给排障出口。
    console.error('[CodeBee] 启动失败：Python 进程 ' + (code ? '退出码 ' + code : '异常终止') + '。'
      + '\n  · 运行 python --version 确认可用且为 3.8+'
      + '\n  · 若装有多个 Python，可设环境变量 TUTTI_PYTHON 切换（cmd：set TUTTI_PYTHON=python）');
  }
  process.exit(signal ? 1 : (code == null ? 0 : code));
});
child.on('error', (e) => {
  console.error('[CodeBee] 无法启动 Python：' + e.message);
  process.exit(1);
});
