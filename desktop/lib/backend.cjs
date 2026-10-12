'use strict';

const { EventEmitter } = require('node:events');
const { spawn, execFile } = require('node:child_process');
const { randomBytes } = require('node:crypto');
const fs = require('node:fs');
const http = require('node:http');
const path = require('node:path');

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
  constructor({ python, script, dataDir, env, port = 8765 }) {
    super();
    Object.assign(this, { python, script, dataDir, env, port });
    this.secret = randomBytes(32).toString('hex');
    this.child = null;
    this.origin = '';
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

module.exports = { Backend, requestJSON };
