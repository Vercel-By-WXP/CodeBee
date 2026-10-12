'use strict';

const { app, BrowserWindow, Menu, Tray, nativeImage, dialog, shell, Notification } = require('electron');
const fs = require('node:fs');
const os = require('node:os');
const path = require('node:path');
const assert = require('node:assert/strict');
const { spawn } = require('node:child_process');
const { Backend } = require('./lib/backend.cjs');
const { RELEASES_URL, dataDirectory, runtimePaths, isInternal, externalURL, cliEnvironment } = require('./lib/platform.cjs');

const smoke = process.argv.includes('--smoke-test');
const smokeSecond = process.argv.includes('--smoke-second');
let smokeRoot;
if (smoke) {
  smokeRoot = fs.mkdtempSync(path.join(os.tmpdir(), 'codebee-desktop-smoke-'));
  app.setPath('userData', path.join(smokeRoot, 'electron'));
} else if (smokeSecond && process.env.CODEBEE_SMOKE_USER_DATA) {
  app.setPath('userData', process.env.CODEBEE_SMOKE_USER_DATA);
}

app.setAppUserModelId('com.codebee.desktop');
const ownsInstance = app.requestSingleInstanceLock();
if (!ownsInstance) {
  app.quit();
} else {
  app.on('second-instance', showWindow);
  app.whenReady().then(launch).catch(failStartup);
}

let window;
let tray;
let backend;
let paths;
let quitting = false;
let starting = false;
let quitRequested = false;
let exitPending = false;
let pollTimer;
let notificationPrimed = false;
const seen = new Map();

function showWindow() {
  if (!window || window.isDestroyed()) return;
  if (window.isMinimized()) window.restore();
  window.show();
  window.focus();
  if (process.platform === 'darwin') app.dock.show();
}

function secureWindow(target) {
  target.webContents.setWindowOpenHandler(({ url }) => {
    if (isInternal(url, backend?.origin)) {
      const preview = new BrowserWindow({ width: 1100, height: 780, parent: window,
        webPreferences: { nodeIntegration: false, contextIsolation: true, sandbox: true } });
      secureWindow(preview);
      void preview.loadURL(url);
    } else if (externalURL(url)) {
      void shell.openExternal(url).catch(() => {});
    }
    return { action: 'deny' };
  });
  const restrictNavigation = (event, url) => {
    if (isInternal(url, backend?.origin)) return;
    event.preventDefault();
    if (externalURL(url)) void shell.openExternal(url).catch(() => {});
  };
  target.webContents.on('will-navigate', restrictNavigation);
  target.webContents.on('will-redirect', restrictNavigation);
  target.webContents.session.setPermissionRequestHandler((contents, permission, callback, details) => {
    callback(contents === window?.webContents && permission === 'clipboard-sanitized-write' &&
      isInternal(details.requestingUrl, backend?.origin));
  });
  target.webContents.session.setPermissionCheckHandler((contents, permission, origin) => {
    return contents === window?.webContents && permission === 'clipboard-sanitized-write' &&
      isInternal(origin, backend?.origin);
  });
  target.webContents.on('will-attach-webview', event => event.preventDefault());
}

async function launch() {
  paths = runtimePaths(app.isPackaged, process.resourcesPath, __dirname);
  window = new BrowserWindow({ title: 'CodeBee', width: 1380, height: 900,
    minWidth: 860, minHeight: 620, backgroundColor: '#f4f7fb', icon: paths.icon,
    webPreferences: { nodeIntegration: false, contextIsolation: true, sandbox: true } });
  secureWindow(window);
  window.on('close', event => {
    if (!quitting) { event.preventDefault(); window.hide(); }
  });
  window.on('closed', () => { window = null; });
  window.webContents.on('render-process-gone', () => {
    if (!quitting) void startWindow();
  });
  const trayIcon = process.platform === 'darwin' ?
    nativeImage.createFromPath(path.join(__dirname, 'assets', 'trayTemplate.png')) :
    nativeImage.createFromPath(paths.icon).resize({ width: 20, height: 20 });
  if (process.platform === 'darwin') trayIcon.setTemplateImage(true);
  tray = new Tray(trayIcon);
  tray.setToolTip('CodeBee · 关闭窗口后任务继续运行');
  tray.on('click', showWindow);
  tray.setContextMenu(Menu.buildFromTemplate([
    { label: '打开 CodeBee', click: showWindow },
    { label: '下载桌面新版', click: () => void shell.openExternal(RELEASES_URL) },
    { type: 'separator' }, { label: '退出 CodeBee', click: () => void requestQuit() },
  ]));
  const template = [
    ...(process.platform === 'darwin' ? [{ label: 'CodeBee', submenu: [
      { role: 'about' }, { type: 'separator' },
      { label: '下载桌面新版', click: () => void shell.openExternal(RELEASES_URL) },
      { type: 'separator' }, { role: 'hide' }, { role: 'hideOthers' }, { role: 'unhide' },
      { type: 'separator' }, { label: '退出 CodeBee', accelerator: 'Command+Q', click: () => void requestQuit() },
    ] }] : [{ label: 'CodeBee', submenu: [
      { label: '打开 CodeBee', click: showWindow },
      { label: '退出 CodeBee', accelerator: 'Alt+F4', click: () => void requestQuit() },
    ] }]),
    { label: '编辑', submenu: [{ role: 'undo' }, { role: 'redo' }, { type: 'separator' },
      { role: 'cut' }, { role: 'copy' }, { role: 'paste' }, { role: 'selectAll' }] },
    { label: '视图', submenu: [{ label: '刷新界面', accelerator: 'CmdOrCtrl+R',
      click: () => window?.webContents.reload() }, { role: 'resetZoom' }, { role: 'zoomIn' },
      { role: 'zoomOut' }, { role: 'togglefullscreen' }] },
    { label: '帮助', submenu: [
      { label: '下载桌面新版', click: () => void shell.openExternal(RELEASES_URL) },
      { label: '打开桌面日志', click: () => backend?.logPath && void shell.openPath(path.dirname(backend.logPath)) },
      { label: '打开数据目录', click: () => backend && void shell.openPath(backend.dataDir) },
    ] },
  ];
  Menu.setApplicationMenu(Menu.buildFromTemplate(template));
  app.setAboutPanelOptions({ applicationName: 'CodeBee', applicationVersion: app.getVersion(),
    version: '桌面预览版', copyright: 'CodeBee · All agents, one hive.' });
  await startBackend();
  if (smoke) await runSmoke();
}

async function startWindow() {
  if (backend?.origin && window && !window.isDestroyed()) await window.loadURL(backend.origin);
}

async function startBackend() {
  if (starting || quitting) return;
  starting = true;
  try {
    if (window && !window.isDestroyed()) await window.loadFile(path.join(__dirname, 'startup.html'));
    const env = await cliEnvironment();
    if (smoke) env.TUTTI_ISOLATE_HOME = '1';
    backend = new Backend({ ...paths, env, port: smoke ? 0 : 8765,
      dataDir: smoke ? path.join(smokeRoot, 'data') : dataDirectory() });
    backend.on('pick', message => void pickNative(message));
    backend.on('unexpected-exit', error => {
      clearInterval(pollTimer);
      if (!quitting) void failStartup(error);
    });
    backend.on('log-error', error => console.error('Desktop log:', error.message));
    await backend.start();
    window.webContents.session.webRequest.onBeforeSendHeaders(
      { urls: [`${backend.origin}/*`] }, (details, callback) => {
        details.requestHeaders['X-CodeBee-Desktop'] = backend.secret;
        callback({ cancel: false, requestHeaders: details.requestHeaders });
      });
    await startWindow();
    notificationPrimed = false;
    seen.clear();
    clearInterval(pollTimer);
    if (!smoke) pollTimer = setInterval(() => void pollNotifications(), 5000);
  } finally {
    starting = false;
    if (quitRequested && !quitting) {
      quitRequested = false;
      void requestQuit();
    }
  }
}

async function pickNative(message) {
  if (!message || typeof message.id !== 'string' || !/^[a-f0-9]{32}$/.test(message.id) ||
      !['file', 'directory'].includes(message.mode)) return;
  const owner = backend;
  if (smoke) { owner.replyPick(message.id, owner.dataDir); return; }
  try {
    showWindow();
    const result = await dialog.showOpenDialog(window, {
      title: typeof message.title === 'string' ? message.title.slice(0, 120) : '选择文件',
      ...(typeof message.initial === 'string' && fs.existsSync(message.initial) ? { defaultPath: message.initial } : {}),
      properties: [message.mode === 'directory' ? 'openDirectory' : 'openFile'],
      ...(message.mode === 'file' ? { filters: [{ name: '备份包', extensions: ['zip'] },
        { name: '全部文件', extensions: ['*'] }] } : {}),
    });
    owner.replyPick(message.id, result.canceled ? '' : (result.filePaths[0] || ''));
  } catch { owner.replyPick(message.id, ''); }
}

async function pollNotifications() {
  if (quitting || starting) return;
  try {
    const status = await backend.status();
    for (const event of status.events) {
      const previous = seen.get(event.id);
      if (notificationPrimed && previous && previous !== event.status &&
          ['done', 'failed', 'timeout'].includes(event.status) && Notification.isSupported()) {
        const notification = new Notification({ title: event.status === 'done' ? 'CodeBee · 任务完成' : 'CodeBee · 任务需要处理',
          body: event.title || '点击打开 CodeBee 查看结果', icon: paths.icon });
        notification.on('click', showWindow);
        notification.show();
      }
      seen.set(event.id, event.status);
    }
    notificationPrimed = true;
    if (seen.size > 500) {
      const live = new Set(status.events.map(event => event.id));
      for (const id of seen.keys()) if (!live.has(id)) seen.delete(id);
    }
  } catch { /* Transient misses must not create repeated notifications. */ }
}

async function requestQuit() {
  if (exitPending || quitting) return;
  exitPending = true;
  try {
    if (starting) {
      quitRequested = true;
      return;
    }
    let force = false;
    if (backend?.origin && backend.child?.exitCode === null) {
      const status = await backend.status();
      const active = status.jobs.alive + status.jobs.chat_alive + status.jobs.queued;
      if (active) {
        const choice = await dialog.showMessageBox(window, { type: 'question',
          message: `还有 ${active} 个任务在运行`, detail: '关闭窗口可以继续后台运行。退出应用会停止当前任务。',
          buttons: ['继续后台运行', '停止任务并退出'], defaultId: 0, cancelId: 0 });
        if (choice.response === 0) { window.hide(); return; }
        force = true;
      }
      await backend.stop(force);
    }
    finishQuit();
  } catch (error) {
    const choice = await dialog.showMessageBox(window, { type: 'warning', message: 'CodeBee 暂时无法退出',
      detail: error.message, buttons: ['继续运行', '强制退出'], defaultId: 0, cancelId: 0 });
    if (choice.response === 1) { await backend?.kill(); finishQuit(); }
  } finally { exitPending = false; }
}

function finishQuit() {
  quitting = true;
  clearInterval(pollTimer);
  tray?.destroy();
  app.quit();
}

async function failStartup(error) {
  if (smoke) {
    console.error('CODEBEE_SMOKE_FAIL', error.stack || error.message);
    await backend?.kill();
    quitting = true;
    app.exit(1);
    return;
  }
  const choice = await dialog.showMessageBox(window || undefined, { type: 'error',
    message: 'CodeBee 启动或运行遇到问题', detail: error.message +
      (backend?.logPath ? `\n\n日志：${backend.logPath}` : ''),
    buttons: ['重试', '打开日志', '退出'], defaultId: 0, cancelId: 2 });
  if (choice.response === 1 && backend?.logPath) {
    await shell.openPath(path.dirname(backend.logPath));
    return failStartup(error);
  }
  if (choice.response === 0) {
    await backend?.kill();
    try { await startBackend(); } catch (next) { await failStartup(next); }
  } else {
    await backend?.kill();
    finishQuit();
  }
}

async function runSmoke() {
  const security = await window.webContents.executeJavaScript(`({
    title: document.title, node: typeof window.require, process: typeof window.process,
    content: Boolean(document.querySelector('body').children.length)
  })`);
  assert.equal(security.node, 'undefined');
  assert.equal(security.process, 'undefined');
  assert.equal(security.content, true);
  assert.match(security.title, /CodeBee/);
  const unauthenticated = await fetch(backend.origin + '/api/desktop/status');
  assert.equal(unauthenticated.status, 403);
  const status = await backend.status();
  assert.equal(status.pid, backend.child.pid);
  const picker = await fetch(backend.origin + '/api/pick_folder', { method: 'POST',
    headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({}) });
  const picked = await picker.json();
  assert.equal(picked.path, backend.dataDir);
  const update = await (await fetch(backend.origin + '/api/selfupdate')).json();
  assert.equal(update.mode, 'desktop');
  const second = spawn(app.getPath('exe'), app.isPackaged ? ['--smoke-second'] : [__dirname, '--smoke-second'], {
    env: { ...process.env, CODEBEE_SMOKE_USER_DATA: app.getPath('userData') }, windowsHide: true,
  });
  const secondCode = await new Promise((resolve, reject) => {
    const timeout = setTimeout(() => { second.kill(); reject(new Error('Second instance did not exit')); }, 10000);
    second.once('error', error => { clearTimeout(timeout); reject(error); });
    second.once('exit', code => { clearTimeout(timeout); resolve(code); });
  });
  assert.equal(secondCode, 0);
  assert.equal((await backend.status()).pid, status.pid);
  window.close();
  assert.equal(window.isVisible(), false);
  assert.equal((await backend.status()).pid, status.pid);
  showWindow();
  const artifacts = path.join(__dirname, 'dist', 'smoke');
  // Packaged app.asar is read-only; CI provides a writable artifact destination.
  const output = process.env.CODEBEE_SMOKE_OUTPUT || (app.isPackaged ? path.join(smokeRoot, 'artifacts') : artifacts);
  fs.mkdirSync(output, { recursive: true });
  fs.writeFileSync(path.join(output, 'desktop.png'), (await window.webContents.capturePage()).toPNG());
  await backend.stop();
  assert.equal(backend.child.exitCode, 0);
  console.log('CODEBEE_SMOKE_PASS', JSON.stringify({ ...security, picker: true, singleInstance: true,
    background: true, shutdown: true, output }));
  fs.writeFileSync(path.join(output, 'result.json'), JSON.stringify({ ok: true, security, platform: process.platform,
    arch: process.arch, python: paths.python }, null, 2));
  finishQuit();
}

app.on('activate', showWindow);
app.on('window-all-closed', () => {});
app.on('before-quit', event => {
  if (!quitting && ownsInstance) { event.preventDefault(); void requestQuit(); }
});
app.on('will-quit', () => { clearInterval(pollTimer); backend?.child?.stdin.end(); });
