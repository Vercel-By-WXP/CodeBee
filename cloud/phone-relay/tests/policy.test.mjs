import test from "node:test";
import assert from "node:assert/strict";
import { COOKIE, digest, readSession, sameOrigin, safePath, boundedBody, encode, decode } from "../src/policy.mjs";

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
