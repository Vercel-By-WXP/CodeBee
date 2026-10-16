import test from "node:test";
import assert from "node:assert/strict";
import { existsSync, readdirSync, statSync } from "node:fs";
import { join } from "node:path";
import { COOKIE, digest, readSession, sameOrigin, safePath, assetTarget, boundedBody, encode, decode, preferredLang } from "../src/policy.mjs";

test("device credentials yield stable, separate identities", async () => {
  const a = await digest("a".repeat(64)), b = await digest("b".repeat(64));
  assert.equal(a.length, 64);
  assert.notEqual(a.slice(0, 32), b.slice(0, 32));
});

test("only complete secure session cookies are parsed", () => {
  const request = value => new Request("https://app.codebeeagent.com/", { headers: { cookie: value } });
  assert.equal(readSession(request(`${COOKIE}=invalid`)), null);
  assert.equal(readSession(request(`other=${"a".repeat(32)}.${"b".repeat(64)}`)), null);
  assert.deepEqual(readSession(request(`${COOKIE}=${"a".repeat(32)}.${"b".repeat(64)}`)), ["a".repeat(32), "b".repeat(64)]);
});

test("writes require the same origin", () => {
  assert.equal(sameOrigin(new Request("https://app.codebeeagent.com/", { headers: { origin: "https://evil.example" } })), false);
  assert.equal(sameOrigin(new Request("https://app.codebeeagent.com/", { headers: { origin: "https://app.codebeeagent.com" } })), true);
});

test("proxy cannot select arbitrary hosts or local pairing administration", () => {
  for (const path of ["https://localhost/", "//localhost/", "/%2fother", "/\\other", "/api/connect", "/%61pi/connect/relay", "/relay/pair"]) assert.equal(safePath(path), false, path);
  assert.equal(safePath("/api/state?client=phone"), true);
});

test("body cap applies even without content-length", async () => {
  const request = new Request("https://app.codebeeagent.com/", { method: "POST", body: "12345" });
  await assert.rejects(boundedBody(request, 4), RangeError);
});

test("binary bodies survive transport encoding", () => {
  const value = Uint8Array.from([0, 255, 128, 13, 10, 42]);
  assert.deepEqual(decode(encode(value)), value);
});

test("portal language follows Accept-Language with English as neutral default", () => {
  assert.equal(preferredLang("zh-CN,zh;q=0.9,en;q=0.8"), "zh");
  assert.equal(preferredLang("zh-Hans-CN"), "zh");
  assert.equal(preferredLang("en-US,en;q=0.9"), "en");
  assert.equal(preferredLang("zh;q=0.3,en;q=0.9"), "en");
  // Unrecognised or absent headers get English, never a blank guess.
  assert.equal(preferredLang("fr-FR,fr;q=0.9"), "en");
  assert.equal(preferredLang(""), "en");
  assert.equal(preferredLang(undefined), "en");
});

test("workbench asset targets mirror desktop paths, APIs stay on the relay", () => {
  assert.equal(assetTarget("/app.js"), "/ui/app.js");
  assert.equal(assetTarget("/icons/icon-192.png"), "/ui/icons/icon-192.png");
  assert.equal(assetTarget("/manifest.json"), "/ui/manifest.json");
  // API, documents, and odd shapes never take the asset shortcut.
  for (const path of ["/", "/api/state", "/api/events", "/relay/portal", "/index.html",
                      "/a//b.js", "/x y.js", "/a/b/", "/a.b/c", "/big"]) {
    assert.equal(assetTarget(new URL("https://x" + path).pathname), null, path);
  }
  // The desktop route regex allows dots inside segment names but not trailing.
  assert.equal(assetTarget("/preview/r-1/tok/app.js"), "/ui/preview/r-1/tok/app.js");
});

test("synced asset mirror covers the desktop workbench bundle", () => {
  const ui = join(import.meta.dirname, "..", "..", "..", "app", "ui");
  const mirror = join(import.meta.dirname, "..", "public", "ui");
  const files = readdirSync(ui).filter(f => /\.(?:js|css|html|json|txt)$/i.test(f) && f !== "index.html");
  assert.ok(files.includes("app.js") && files.includes("style.css"), "desktop bundle detected");
  for (const name of files) {
    const copied = join(mirror, name);
    assert.ok(existsSync(copied), `missing from mirror: ${name}`);
    assert.equal(statSync(copied).size, statSync(join(ui, name)).size, `size drift: ${name}`);
  }
  // The biggest payload really is served from the edge, not the relay.
  assert.ok(statSync(join(mirror, "app.js")).size > 500 * 1024);
});
