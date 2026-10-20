import assert from "node:assert/strict";
import test from "node:test";
import { assetTarget, rateLimitSubject } from "../src/policy.mjs";

const DEVICE = "a".repeat(32);
const PHONE_A = "b".repeat(64);
const PHONE_B = "c".repeat(64);

function request(ip = "203.0.113.8") {
  return new Request("https://app.codebeeagent.com/api/mobile/status", {
    headers: { "cf-connecting-ip": ip },
  });
}

test("request limits are isolated per paired phone session", () => {
  const req = request();
  assert.equal(rateLimitSubject(req, "request", [DEVICE, PHONE_A]),
    rateLimitSubject(req, "request", [DEVICE, PHONE_A]));
  assert.notEqual(rateLimitSubject(req, "request", [DEVICE, PHONE_A]),
    rateLimitSubject(req, "request", [DEVICE, PHONE_B]));
});

test("unpaired and non-request actions remain limited by IP", () => {
  assert.equal(rateLimitSubject(request(), "request"), "ip:203.0.113.8");
  assert.equal(rateLimitSubject(request(), "request", [DEVICE, "invalid"]), "ip:203.0.113.8");
  assert.equal(rateLimitSubject(request(), "pair", [DEVICE, PHONE_A]), "ip:203.0.113.8");
});

test("manifest is mapped to the mirrored workbench asset", () => {
  assert.equal(assetTarget("/manifest.json"), "/ui/manifest.json");
  assert.equal(assetTarget("/api/wxdigest"), null);
});
