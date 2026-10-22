'use strict';

const { EventEmitter } = require('node:events');
const { spawn, execFile } = require('node:child_process');
const { randomBytes } = require('node:crypto');
const fs = require('node:fs');
const http = require('node:http');
const https = require('node:https');
const path = require('node:path');

const REGISTRY_TARBALL = version => `https://registry.npmjs.org/codebee/-/codebee-${version}.tgz`;
const REGISTRY_HOSTS = new Set(['registry.npmjs.org']);

function downloadTo(url, dest, timeout, { allowedHosts = REGISTRY_HOSTS } = {}) {
  return new Promise((resolve, reject) => {
    // The registry is https-only; a plain http client would throw
    // ERR_INVALID_PROTOCOL. Every redirect hop is re-validated so a
    // compromised registry response cannot bounce us off-host.
    const get = (target, redirects) => {
      let parsed;
      try { parsed = new URL(target); } catch { reject(new Error('引擎下载地址无效')); return; }
      if (parsed.protocol !== 'https:' || !allowedHosts.has(parsed.hostname)) {
        reject(new Error('引擎下载地址不受信任'));
        return;
      }
      const request = https.get(parsed, { timeout }, response => {
        const status = Number(response.statusCode || 0);
        if (status >= 300 && status < 400 && response.headers.location && redirects > 0) {
          response.resume();
          let redirected;
          try { redirected = new URL(response.headers.location, parsed).toString(); } catch {
            reject(new Error('引擎下载重定向无效'));
            return;
          }
          return get(redirected, redirects - 1);
        }
        if (status !== 200) {
          response.resume();
          return reject(new Error(`引擎下载失败 HTTP ${status}`));
        }
        const file = fs.createWriteStream(dest);
        response.pipe(file);
        file.on('finish', () => file.close(resolve));
        file.on('error', reject);
      });
      request.on('timeout', () => request.destroy(new Error('引擎下载超时')));
      request.on('error', reject);
    };
    get(url, 4);
  });
}

function requestJSON(origin, endpoint, secret, body) {
  return new Promise((resolve, reject) => {
    const data = body === undefined ? undefined : JSON.stringify(body);
    const request = http.request(origin + endpoint, {
      method: data === undefined ? 'GET' : 'POST',
      headers: { 'X-CodeBee-Desktop': secret, ...(data === undefined ? {} : {
        'Content-Type': 'application/json', 'Content-Length': Buffer.byteLength(data),
      }) },
      timeout: 4000,
    }, response => {
      let buffer = '';
      response.setEncoding('utf8');
      response.on('data', chunk => {
        buffer += chunk;
        if (buffer.length > 1048576) request.destroy(new Error('Desktop status too large'));
      });
      response.on('error', reject);
      response.on('end', () => {
        try {
          const result = JSON.parse(buffer);
          if (response.statusCode !== 200) {
            const error = new Error(result.error || `HTTP ${response.statusCode}`);
            error.status = response.statusCode;
            reject(error);
          } else resolve(result);
        } catch (error) { reject(error); }
      });
    });
    request.on('timeout', () => request.destroy(new Error('Desktop request timed out')));
    request.on('error', reject);
    request.end(data);
  });
}

class Backend extends EventEmitter {
  constructor({ python, script, dataDir, env, port = 0 }) {
    super();
    Object.assign(this, { python, script, dataDir, env, port });
    this.secret = randomBytes(32).toString('hex');
    this.child = null;
    this.origin = '';
    // Attach mode: the data directory was owned by an already-running engine;
    // the sidecar handed us its URL and exits by design (we never own it).
    this.attached = false;
    this.attachToken = '';
    this.stopping = false;
  }

  async start() {
    if (!fs.existsSync(this.python)) throw new Error('未找到随应用携带的 Python，请重新安装 CodeBee。');
    if (!fs.existsSync(this.script)) throw new Error('CodeBee 引擎文件缺失，请重新安装。');
    fs.mkdirSync(this.dataDir, { recursive: true });
    const logs = path.join(this.dataDir, 'desktop-logs');
    fs.mkdirSync(logs, { recursive: true });
    this.logPath = path.join(logs, 'backend.log');
    if (fs.existsSync(this.logPath) && fs.statSync(this.logPath).size > 2097152) {
      fs.copyFileSync(this.logPath, this.logPath + '.previous');
      fs.truncateSync(this.logPath, 0);
    }
    const log = fs.createWriteStream(this.logPath, { flags: 'a' });
    log.on('error', error => this.emit('log-error', error));
    return new Promise((resolve, reject) => {
      const child = spawn(this.python, ['-E', '-s', '-X', 'utf8', '-u', this.script, '--port', String(this.port)], {
        cwd: this.dataDir, windowsHide: true, stdio: ['pipe', 'pipe', 'pipe'],
        env: { ...this.env, TUTTI_DATA: this.dataDir, CODEBEE_DESKTOP: '1',
          CODEBEE_DESKTOP_SECRET: this.secret },
      });
      this.child = child;
      let ready = false;
      let settled = false;
      let buffer = '';
      let tail = '';
      const timer = setTimeout(() => {
        settled = true;
        this.stopping = true;
        void this.kill();
        reject(new Error('启动超过 90 秒，请查看桌面日志后重试。'));
      }, 90000);
      child.stderr.on('data', chunk => {
        log.write(chunk);
        tail = (tail + chunk.toString('utf8')).slice(-3000);
      });
      child.stdout.setEncoding('utf8');
      child.stdout.on('data', chunk => {
        log.write(chunk);
        buffer += chunk;
        if (buffer.length > 1048576) buffer = buffer.slice(-65536);
        let newline;
        while ((newline = buffer.indexOf('\n')) !== -1) {
          const line = buffer.slice(0, newline).trim();
          buffer = buffer.slice(newline + 1);
          if (line.startsWith('CODEBEE_DESKTOP_READY ')) {
            try {
              const message = JSON.parse(line.slice('CODEBEE_DESKTOP_READY '.length));
              if (message.pid !== child.pid || !Number.isInteger(message.port) || message.port < 1 || message.port > 65535) {
                throw new Error('Invalid sidecar identity');
              }
              this.origin = `http://127.0.0.1:${message.port}`;
              // A TCP listener alone is insufficient: verify the private token and PID.
              void this.status().then(status => {
                if (status.pid !== child.pid) throw new Error('Sidecar identity mismatch');
                if (settled) return;
                settled = true;
                ready = true;
                clearTimeout(timer);
                resolve(this.origin);
              }).catch(error => {
                if (settled) return;
                settled = true;
                clearTimeout(timer);
                this.stopping = true;
                void this.kill();
                reject(error);
              });
            } catch (error) { tail = error.message; }
          } else if (line.startsWith('CODEBEE_DESKTOP_ATTACH ')) {
            try {
              const message = JSON.parse(line.slice('CODEBEE_DESKTOP_ATTACH '.length));
              if (!Number.isInteger(message.port) || message.port < 1 || message.port > 65535) {
                throw new Error('Invalid attach payload');
              }
              this.attached = true;
              this.attachToken = typeof message.token === 'string' ? message.token : '';
              this.origin = `http://127.0.0.1:${message.port}`;
              if (settled) return;
              settled = true;
              ready = true;
              clearTimeout(timer);
              this.stopping = true; // Sidecar exits 0 after handing over; never "unexpected".
              resolve(this.origin);
            } catch (error) { tail = error.message; }
          } else if (line.startsWith('CODEBEE_DESKTOP_APPLY ')) {
            try {
              const message = JSON.parse(line.slice('CODEBEE_DESKTOP_APPLY '.length));
              const version = String(message.version || '');
              if (!/^\d+\.\d+\.\d+$/.test(version)) throw new Error('Invalid apply payload');
              this.emit('apply-update', { version });
            } catch { /* Malformed sidecar output is not an action. */ }
          } else if (line.startsWith('CODEBEE_DESKTOP_PICK ')) {
            try { this.emit('pick', JSON.parse(line.slice('CODEBEE_DESKTOP_PICK '.length))); }
            catch { /* Malformed sidecar output is not a native action. */ }
          } else {
            tail = (tail + '\n' + line).slice(-3000);
            this.emit('progress', line);
          }
        }
      });
      child.stdin.on('error', () => {}); // Closing stdin during shutdown is expected.
      child.on('error', error => {
        clearTimeout(timer);
        log.end();
        if (!settled) { settled = true; reject(error); }
      });
      child.on('exit', (code, signal) => {
        clearTimeout(timer);
        log.end();
        if (!settled) {
          settled = true;
          reject(new Error(tail.trim() || `CodeBee 引擎启动失败 (${code ?? signal})`));
        } else if (ready && !this.stopping) {
          this.emit('unexpected-exit', new Error(`CodeBee 引擎已退出 (${code ?? signal})。\n${tail}`));
        }
      });
    });
  }

  status() { return requestJSON(this.origin, '/api/desktop/status', this.secret); }

  replyPick(id, selectedPath) {
    if (this.child?.stdin.writable) this.child.stdin.write(JSON.stringify({ id, path: selectedPath }) + '\n');
  }

  /* Desktop hot update: pull the engine tarball from the npm registry and
   * swap resources/codebee/app, then the caller restarts this sidecar.
   * Must be called with the sidecar still up (it verifies the version);
   * the swap itself happens after this.stop() below (file locks on Windows). */
  async hotUpdate(version, onProgress) {
    const note = text => { try { onProgress(String(text)); } catch { /* noop */ } };
    const staging = path.join(this.dataDir, 'update-staging');
    fs.rmSync(staging, { recursive: true, force: true });
    fs.mkdirSync(staging, { recursive: true });
    const tgz = path.join(staging, `codebee-${version}.tgz`);
    note(`下载引擎 v${version}…`);
    await downloadTo(REGISTRY_TARBALL(version), tgz, 600000);
    if (fs.statSync(tgz).size < 100000) throw new Error('引擎包异常（过小），已取消更新');
    note('校验并解压引擎…');
    await new Promise((resolve, reject) => {
      // The bundled interpreter doubles as the extraction tool: tarfile is
      // stdlib on every supported runtime (3.8 embeddable included).
      const extract = spawn(this.python, ['-E', '-s', '-X', 'utf8', '-c',
        'import sys, tarfile; t = tarfile.open(sys.argv[1]); members = [m for m in t.getmembers() if m.name == "package" or m.name.startswith("package/")]; [t.extract(m, sys.argv[2]) for m in members]; t.close()',
        tgz, staging], { cwd: this.dataDir, windowsHide: true, stdio: ['ignore', 'ignore', 'pipe'] });
      let err = '';
      extract.stderr.on('data', chunk => { err += chunk.toString('utf8'); });
      extract.on('exit', code => code === 0
        ? resolve()
        : reject(new Error('引擎包解压失败: ' + err.slice(-400))));
    });
    const pkg = path.join(staging, 'package');
    for (const marker of [path.join(pkg, 'app', 'main.py'), path.join(pkg, 'package.json')]) {
      if (!fs.existsSync(marker)) throw new Error('引擎包内容异常（缺 ' + path.basename(marker) + '）');
    }
    const bumped = JSON.parse(fs.readFileSync(path.join(pkg, 'package.json'), 'utf8')).version;
    if (bumped !== version) throw new Error(`引擎包版本不符（${bumped} ≠ ${version}）`);
    note('停止旧引擎并替换文件…');
    await this.stop();   // drains; rejects when tasks are still running
    const appDir = path.dirname(this.script);
    const rootDir = path.dirname(appDir);
    const backup = path.join(rootDir, 'app.bak-' + Date.now());
    fs.rmSync(backup, { recursive: true, force: true });
    fs.renameSync(appDir, backup);
    try {
      fs.renameSync(path.join(pkg, 'app'), appDir);
      for (const name of ['package.json', 'CHANGELOG.md']) {
        const from = path.join(pkg, name);
        if (fs.existsSync(from)) fs.copyFileSync(from, path.join(rootDir, name));
      }
    } catch (error) {
      fs.rmSync(appDir, { recursive: true, force: true });
      if (!fs.existsSync(appDir)) fs.renameSync(backup, appDir);
      await this.start();
      throw error;
    }
    fs.rmSync(staging, { recursive: true, force: true });
    note('启动新引擎…');
  }

  async stop(force = false) {
    if (!this.child || this.child.exitCode !== null || this.child.signalCode !== null) return;
    const exited = new Promise(resolve => this.child.once('exit', resolve));
    await requestJSON(this.origin, '/api/desktop/shutdown', this.secret, { force });
    this.stopping = true;
    this.child.stdin.end();
    let timer;
    try {
      await Promise.race([exited, new Promise((_, reject) => {
        timer = setTimeout(() => reject(new Error('引擎退出超时')), 12000);
      })]);
    } finally { clearTimeout(timer); }
  }

  async kill() {
    const child = this.child;
    if (!child || child.exitCode !== null || child.signalCode !== null) return;
    this.stopping = true;
    child.stdin.end();
    if (process.platform === 'win32') {
      await new Promise(resolve => execFile('taskkill', ['/PID', String(child.pid), '/T', '/F'],
        { windowsHide: true, timeout: 5000 }, () => resolve()));
    } else child.kill('SIGTERM');
  }
}

module.exports = { Backend, downloadTo, requestJSON, REGISTRY_HOSTS };
