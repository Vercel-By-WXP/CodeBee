'use strict';
// Contract harness for desktop/lib/backend.cjs downloadTo (engine hot update).
// Spawned by tests/test_desktop_hotupdate_download.py, never by unittest itself.
// NODE_TLS_REJECT_UNAUTHORIZED=0 is set by the Python wrapper so the bundled
// self-signed localhost fixture can terminate TLS.

const assert = require('node:assert/strict');
const fs = require('node:fs');
const https = require('node:https');
const os = require('node:os');
const path = require('node:path');

const [backendPath, fixturesDir] = process.argv.slice(2);
const { downloadTo, REGISTRY_HOSTS } = require(backendPath);
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

  console.log('OK desktop_hotupdate_harness');
})().catch(error => {
  console.error((error && error.stack) || String(error));
  process.exit(1);
});
