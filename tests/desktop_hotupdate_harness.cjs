'use strict';
// Contract harness for desktop/lib/backend.cjs downloadTo (engine hot update).
// Spawned by tests/test_desktop_hotupdate_download.py, never by unittest itself.
// NODE_TLS_REJECT_UNAUTHORIZED=0 is set by the Python wrapper so the bundled
// self-signed localhost fixture can terminate TLS.

const assert = require('node:assert/strict');
const { execFileSync } = require('node:child_process');
const crypto = require('node:crypto');
const fs = require('node:fs');
const https = require('node:https');
const os = require('node:os');
const path = require('node:path');

const [backendPath, fixturesDir] = process.argv.slice(2);
const {
  downloadTo, REGISTRY_HOSTS, REGISTRY_PACKUMENT,
  fetchIntegrity, fileIntegrity, EXTRACT_SCRIPT,
} = require(backendPath);
const tls = {
  key: fs.readFileSync(path.join(fixturesDir, 'desktop_hotupdate_localhost-key.pem')),
  cert: fs.readFileSync(path.join(fixturesDir, 'desktop_hotupdate_localhost.pem')),
};
const LOCAL_HOSTS = new Set(['127.0.0.1']);

function startServer(makeRoutes) {
  const hits = [];
  const state = { port: 0 };
  return new Promise(resolve => {
    const server = https.createServer(tls, (req, res) => {
      hits.push(req.url);
      const route = makeRoutes(req.url, state.port) || null;
      if (!route) { res.writeHead(404); res.end(); return; }
      if (route.redirect) { res.writeHead(302, { Location: route.redirect }); res.end(); return; }
      res.writeHead(200, { 'Content-Type': 'application/octet-stream' });
      res.end(route.body || '');
    });
    server.listen(0, '127.0.0.1', () => {
      state.port = server.address().port;
      resolve({ server, port: state.port, hits });
    });
  });
}

async function rejectsWith(promise, needle) {
  await assert.rejects(promise, error => {
    assert.match(String((error && error.message) || error), needle);
    return true;
  });
}

(async () => {
  const dataDir = fs.mkdtempSync(path.join(os.tmpdir(), 'codebee-hotupdate-test-'));
  const dest = path.join(dataDir, 'engine.tgz');

  // The shipped registry allowlist covers the real tarball host.
  assert.equal(REGISTRY_HOSTS.has(new URL('https://registry.npmjs.org/codebee/-/codebee-9.9.9.tgz').hostname), true);

  // Plain http is refused before any connection is attempted (a live check
  // here would instead fail with ECONNREFUSED on the closed port 9).
  await rejectsWith(downloadTo('http://127.0.0.1:9/x.tgz', dest, 1000, { allowedHosts: LOCAL_HOSTS }), /不受信任/);

  // Off-allowlist hosts are refused without DNS or connection.
  await rejectsWith(downloadTo('https://evil.example.com/x.tgz', dest, 1000), /不受信任/);

  // 200 download writes the payload.
  {
    const { server, port } = await startServer(url =>
      url === '/codebee-1.0.0.tgz' ? { body: 'engine-payload-1' } : null);
    try {
      await downloadTo(`https://127.0.0.1:${port}/codebee-1.0.0.tgz`, dest, 5000, { allowedHosts: LOCAL_HOSTS });
      assert.equal(fs.readFileSync(dest, 'utf8'), 'engine-payload-1');
    } finally { server.close(); }
  }

  // Same-host redirect is followed and the final payload lands.
  {
    const { server, port, hits } = await startServer((url, p) => {
      if (url === '/codebee-1.0.0.tgz') return { redirect: `https://127.0.0.1:${p}/final.tgz` };
      if (url === '/final.tgz') return { body: 'redirected-payload' };
      return null;
    });
    try {
      await downloadTo(`https://127.0.0.1:${port}/codebee-1.0.0.tgz`, dest, 5000, { allowedHosts: LOCAL_HOSTS });
      assert.equal(fs.readFileSync(dest, 'utf8'), 'redirected-payload');
      assert.deepEqual(hits, ['/codebee-1.0.0.tgz', '/final.tgz']);
    } finally { server.close(); }
  }

  // A redirect bounced off the allowlist mid-flight is refused.
  {
    const { server, port } = await startServer(() => ({ redirect: 'https://evil.example.com/x.tgz' }));
    try {
      await rejectsWith(downloadTo(`https://127.0.0.1:${port}/x.tgz`, dest, 5000, { allowedHosts: LOCAL_HOSTS }), /不受信任/);
    } finally { server.close(); }
  }

  // HTTP-level failures surface with the status code.
  {
    const { server, port } = await startServer(() => null);
    try {
      await rejectsWith(downloadTo(`https://127.0.0.1:${port}/missing.tgz`, dest, 5000, { allowedHosts: LOCAL_HOSTS }),
        /引擎下载失败 HTTP 404/);
    } finally { server.close(); }
  }

  // ---- 发布摘要（dist.integrity）与文件摘要 ---------------------------------
  const payload = path.join(dataDir, 'digest-payload.bin');
  fs.writeFileSync(payload, 'codebee-engine-bytes');
  const expected = 'sha512-' + crypto.createHash('sha512')
    .update(fs.readFileSync(payload)).digest('base64');
  assert.equal(await fileIntegrity(payload, 'sha512'), expected);

  {
    const { server, port } = await startServer(url => (url === '/codebee/1.2.3'
      ? { body: JSON.stringify({ dist: { integrity: expected } }) } : null));
    try {
      const integrity = await fetchIntegrity(`https://127.0.0.1:${port}/codebee/1.2.3`,
        5000, { allowedHosts: LOCAL_HOSTS });
      assert.equal(integrity, expected);
    } finally { server.close(); }
  }
  // 没有摘要 = 无法证明包没被换过，必须拒绝更新而不是放行。
  {
    const { server, port } = await startServer(() => ({ body: JSON.stringify({ dist: {} }) }));
    try {
      await rejectsWith(fetchIntegrity(`https://127.0.0.1:${port}/codebee/1.2.3`,
        5000, { allowedHosts: LOCAL_HOSTS }), /缺少 sha512 完整性摘要/);
    } finally { server.close(); }
  }
  // 摘要本身也可能来自被投毒的响应：非 sha512 形态一律拒绝。
  {
    const { server, port } = await startServer(() => ({
      body: JSON.stringify({ dist: { integrity: 'md5-abc' } }),
    }));
    try {
      await rejectsWith(fetchIntegrity(`https://127.0.0.1:${port}/codebee/1.2.3`,
        5000, { allowedHosts: LOCAL_HOSTS }), /缺少 sha512 完整性摘要/);
    } finally { server.close(); }
  }
  // 非白名单主机不得读取元数据（与 tarball 下载同一信任边界）。
  await rejectsWith(fetchIntegrity('https://evil.example.com/codebee/1.2.3', 1000), /不受信任/);
  assert.equal(REGISTRY_PACKUMENT('9.9.9'), 'https://registry.npmjs.org/codebee/9.9.9');

  // ---- 解包器契约：只允许 package/ 下的普通文件 -----------------------------
  const python = process.env.CODEBEE_TEST_PYTHON || '';
  if (!python) {
    console.log('SKIP desktop_hotupdate_extract (CODEBEE_TEST_PYTHON 未设置)');
  } else {
    const makeTar = (tgz, items) => {
      const program = [
        'import io, json, sys, tarfile',
        'spec = json.loads(sys.argv[2])',
        'with tarfile.open(sys.argv[1], "w:gz") as archive:',
        '    for item in spec:',
        '        info = tarfile.TarInfo(item["name"])',
        '        if item.get("dir"):',
        '            info.type = tarfile.DIRTYPE',
        '            info.mode = 0o755',
        '            archive.addfile(info)',
        '            continue',
        '        if item.get("link"):',
        '            info.type = tarfile.SYMTYPE',
        '            info.linkname = item["link"]',
        '            archive.addfile(info)',
        '            continue',
        '        data = (item.get("data") or "").encode("utf8")',
        '        info.size = len(data)',
        '        archive.addfile(info, io.BytesIO(data))',
      ].join('\n');
      execFileSync(python, ['-c', program, tgz, JSON.stringify(items)], { stdio: 'pipe' });
    };
    const extract = (tgz, out) => {
      try {
        execFileSync(python, ['-E', '-s', '-X', 'utf8', '-c', EXTRACT_SCRIPT, tgz, out], { stdio: 'pipe' });
        return 0;
      } catch (error) { return error.status || 1; }
    };

    // 正常包：内容落地。
    {
      const root = fs.mkdtempSync(path.join(os.tmpdir(), 'codebee-extract-ok-'));
      const tgz = path.join(root, 'ok.tgz');
      makeTar(tgz, [
        { name: 'package', dir: true },
        { name: 'package/package.json', data: '{"version":"9.9.9"}' },
        { name: 'package/app/main.py', data: 'print(1)' },
      ]);
      const out = path.join(root, 'out');
      assert.equal(extract(tgz, out), 0);
      assert.equal(fs.readFileSync(path.join(out, 'package', 'app', 'main.py'), 'utf8'), 'print(1)');
    }

    // 越界成员：必须在解包阶段失败，且 staging 外不出现任何文件。
    {
      const root = fs.mkdtempSync(path.join(os.tmpdir(), 'codebee-extract-escape-'));
      const tgz = path.join(root, 'escape.tgz');
      const out = path.join(root, 'staging');
      makeTar(tgz, [
        { name: 'package/package.json', data: '{}' },
        { name: 'package/../../escaped.txt', data: 'pwned' },
      ]);
      assert.notEqual(extract(tgz, out), 0);
      assert.equal(fs.existsSync(path.join(root, 'escaped.txt')), false);
      assert.equal(fs.existsSync(path.join(root, '..', 'escaped.txt')), false);
    }

    // 符号链接成员：不允许（链接可以把后续写入引到 staging 外）。
    {
      const root = fs.mkdtempSync(path.join(os.tmpdir(), 'codebee-extract-link-'));
      const tgz = path.join(root, 'link.tgz');
      makeTar(tgz, [
        { name: 'package/package.json', data: '{}' },
        { name: 'package/passwd', link: 'C:/Windows/win.ini' },
      ]);
      assert.notEqual(extract(tgz, path.join(root, 'out')), 0);
    }

    // 反斜杠成员名：Windows 上等价于路径分隔符，一律拒绝。
    {
      const root = fs.mkdtempSync(path.join(os.tmpdir(), 'codebee-extract-slash-'));
      const tgz = path.join(root, 'slash.tgz');
      makeTar(tgz, [{ name: 'package\\evil.txt', data: 'pwned' }]);
      assert.notEqual(extract(tgz, path.join(root, 'out')), 0);
    }

    // 包外成员（不以 package/ 开头）同样拒绝。
    {
      const root = fs.mkdtempSync(path.join(os.tmpdir(), 'codebee-extract-outside-'));
      const tgz = path.join(root, 'outside.tgz');
      makeTar(tgz, [{ name: 'other/file.txt', data: 'x' }]);
      assert.notEqual(extract(tgz, path.join(root, 'out')), 0);
    }
  }

  console.log('OK desktop_hotupdate_harness');
})().catch(error => {
  console.error((error && error.stack) || String(error));
  process.exit(1);
});
