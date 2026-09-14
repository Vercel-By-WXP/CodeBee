#!/usr/bin/env node
'use strict';
/*
 * Tutti 启动垫片：在本机找 Python 3.8+，以子进程方式运行包内 app/main.py。
 * npm 只负责分发与版本管理，不携带解释器——所以运行时才探测 python，
 * 找不到就报错并给出安装指引（不写 postinstall，避免在别人机器上制造环境事故）。
 *
 * 安全边界：
 *  - 子进程可执行文件全部是本文件内的字面量（py / python / python3），
 *    参数数组全由字面量常量拼成，不启用 shell；
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
  return 'Tutti 需要本机安装 Python 3.8+（npm 包不含解释器）。\n'
    + '  · 安装：' + url + '\n'
    + '  · Windows 安装时勾选 “Add python.exe to PATH”（自带 py 启动器）\n'
    + '装好后重新运行：tutti';
}

const py = resolvePython();
if (!py) {
  console.error('[Tutti] 未找到可用的 Python。\n' + guide());
  process.exit(1);
}
if (py.tooOld) {
  console.error('[Tutti] 找到 ' + py.name + '（' + py.ver + '），但版本过低。\n' + guide());
  process.exit(1);
}

const userArgs = [];
for (const a of process.argv.slice(2)) {
  if (typeof a === 'string' && SAFE_ARG.test(a)) {
    userArgs.push(a);
  } else {
    console.error('[Tutti] 参数含非法字符，已拒绝：' + String(a).slice(0, 80));
    process.exit(2);
  }
}

// CLI 转发契约：tutti 之后的用户参数（已过白名单）原样作为 main.py 的 argv。
let child;
if (py.name === 'py') {
  child = spawn('py', ['-3', MAIN, ...userArgs], { stdio: 'inherit' });
} else if (py.name === 'python') {
  child = spawn('python', [MAIN, ...userArgs], { stdio: 'inherit' });
} else {
  child = spawn('python3', [MAIN, ...userArgs], { stdio: 'inherit' });
}

for (const sig of ['SIGINT', 'SIGTERM']) {
  process.on(sig, () => { try { child.kill(sig); } catch (e) { /* 已退出 */ } });
}
child.on('exit', (code, signal) => {
  process.exit(signal ? 1 : (code == null ? 0 : code));
});
child.on('error', (e) => {
  console.error('[Tutti] 无法启动 Python：' + e.message);
  process.exit(1);
});
