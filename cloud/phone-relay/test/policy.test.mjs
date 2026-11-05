import assert from "node:assert/strict";
import test from "node:test";
import { readFile } from "node:fs/promises";
import { assetTarget, rateLimitSubject, REQUEST_HEADERS, staleConnectors } from "../src/policy.mjs";

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

test("paired API requests forward the CodeBee token to the desktop", () => {
  // The relay authenticates the phone with its session cookie, but the
  // desktop still authenticates each forwarded API request with this token.
  // Dropping it makes every /api/state request look unauthenticated.
  assert.equal(REQUEST_HEADERS.has("x-codebee-token"), true);
});

test("reconnect evicts every socket that can still be closed", () => {
  // getWebSockets() 在休眠恢复后既可能给出 OPEN 的尸体（对端已死、云端没收到
  // 关闭帧），也可能给出已 CLOSED 的记录。前者必须关掉给新连接腾位，后者再
  // close 一次会抛 —— 这正是这台电脑被 409 永久锁死的那条路径。
  const open = { readyState: 1 };
  const closing = { readyState: 2 };
  const closed = { readyState: 3 };
  assert.deepEqual(staleConnectors([open, closing, closed]), [open, closing]);
  assert.deepEqual(staleConnectors([]), []);
  assert.deepEqual(staleConnectors(undefined), []);
});

test("connector reconnect is never refused with 409", async () => {
  // 「已经有一台连着，所以拒绝你」是错的语义：能通过 Bearer+sha256 双重校验的
  // 只可能是这台电脑自己。一旦回 409，本机就只能无限重连、手机侧恒显示
  // 「你的电脑当前离线」，且云端没有任何接口能把那条僵尸连接踢掉。
  const src = await readFile(new URL("../src/worker.mjs", import.meta.url), "utf8");
  // 注释里会解释这段历史，先剥掉注释行再断言，否则守测守的是文案不是行为。
  const code = src.split("\n")
    .filter(line => !/^\s*(\/\/|\/\*|\*)/.test(line))
    .join("\n");
  assert.equal(/Device already connected/.test(code), false);
  assert.match(code, /staleConnectors\(this\.ctx\.getWebSockets\("connector"\)\)/);
});
