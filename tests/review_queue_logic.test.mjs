import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { test } from "node:test";
import vm from "node:vm";

const source = readFileSync(new URL("../app/ui/app.js", import.meta.url), "utf8");
const code = source.slice(source.indexOf("const REVIEW_QUEUE ="), source.indexOf("/* catalog 里 model"));

function harness(api) {
  const elements = new Map();
  const element = (id) => {
    if (!elements.has(id)) elements.set(id, {
      value: "", textContent: "", innerHTML: "", disabled: false, isConnected: true,
      attrs: {}, setAttribute(key, value) { this.attrs[key] = value; },
      contains() { return true; }, focus() {},
    });
    return elements.get(id);
  };
  element("review-queue-filter").value = "active";
  const context = vm.createContext({ $, S: { state: { runs: [{ id: 'run-1', goal: '<b>goal</b>' }] } },
    api, Date, t: (key, ...args) => args.reduce((text, value, index) => text.replaceAll("{" + index + "}", value), key),
    getLang: () => "en", toast() {}, closeModal() { context.closed = true; },
    openModal(title, body, footer) { context.modal = { title, body, footer }; },
    esc: (value) => String(value ?? "").replace(/[&<>"']/g, (char) =>
      ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" })[char]),
  });
  function $(id) { return element(id); }
  vm.runInContext(code, context);
  return { context, element, run: (text) => vm.runInContext(text, context) };
}

test("all statuses expose only permitted actions", () => {
  const h = harness();
  assert.equal(h.run('reviewActions("requested").join(",")'), "assign,dismiss,expire");
  assert.equal(h.run('reviewActions("assigned").join(",")'), "approve,changes_requested,dismiss,expire");
  assert.equal(h.run('reviewActions("changes_requested").join(",")'), "dismiss,expire");
  for (const status of ["approved", "dismissed", "expired", "unknown"]) {
    assert.equal(h.run(`reviewActions("${status}").length`), 0);
  }
});

test("queue filters, sorts newest first, and escapes all user text", () => {
  const h = harness();
  h.run(`REVIEW_QUEUE.loaded = true; REVIEW_QUEUE.rows = [
    {id:'old', run_id:'run-old', summary:'<script>alert(1)</script>', status:'requested', updated_at:1},
    {id:'new', run_id:'<img>', summary:'latest', status:'assigned', updated_at:2, paths:['<file>'], reviewer:'<alice>', channel:'<channel>', reason:'<reason>'},
    {id:'closed', run_id:'run-closed', summary:'closed', status:'approved', updated_at:3}]; renderReviewQueue();`);
  const html = h.element("review-queue-list").innerHTML;
  assert.ok(html.indexOf("latest") < html.indexOf("&lt;script&gt;"));
  for (const text of ["script", "img", "file", "alice", "channel", "reason"]) assert.ok(html.includes("&lt;" + text + "&gt;"));
  assert.ok(!html.includes("<script>"));
  assert.ok(!html.includes('data-review-id="closed"'));
  assert.equal(h.element("review-queue-count").textContent, "显示 2 / 3 条");
  h.element("review-queue-filter").value = "approved";
  h.run("renderReviewQueue()");
  assert.ok(h.element("review-queue-list").innerHTML.includes("closed"));
  assert.ok(!h.element("review-queue-list").innerHTML.includes("data-review-action"));
});

test("overlapping refreshes cannot overwrite newer results", async () => {
  const pending = [];
  const h = harness(() => new Promise((resolve) => pending.push(resolve)));
  const first = h.run("loadReviewQueue()"), second = h.run("loadReviewQueue()");
  pending[1]({ reviews: [{ id: "new", status: "requested" }] });
  await second;
  pending[0]({ reviews: [{ id: "old", status: "requested" }] });
  await first;
  assert.equal(h.run("REVIEW_QUEUE.rows[0].id"), "new");
  assert.equal(h.element("review-queue-list").attrs["aria-busy"], "false");
});

test("failed refresh preserves queue and exposes retryable error", async () => {
  const h = harness(async () => { throw new Error("offline"); });
  h.run('REVIEW_QUEUE.loaded = true; REVIEW_QUEUE.rows = [{id:"existing",status:"requested"}]; renderReviewQueue();');
  await h.run("loadReviewQueue()");
  assert.equal(h.run("REVIEW_QUEUE.rows[0].id"), "existing");
  assert.ok(h.element("review-queue-message").textContent.includes("offline"));
  assert.equal(h.element("review-queue-list").attrs["aria-busy"], "false");
});

test("creation trims fields and normalizes multiline paths", async () => {
  const calls = [];
  const h = harness(async (path, options) => { calls.push({ path, options }); return { reviews: [] }; });
  h.element("review-run-id").value = "  run-1  ";
  h.element("review-paths").value = " ui/app.js\r\n\n core/main.py \n";
  h.element("review-summary").value = " review diff ";
  h.element("review-channel").value = " #frontend ";
  await h.run("saveReviewRequest()");
  assert.deepEqual(JSON.parse(calls[0].options.body), { run_id: "run-1", paths: ["ui/app.js", "core/main.py"], summary: "review diff", channel: "#frontend" });
  assert.equal(calls[0].path, "/api/reviews");
  assert.equal(calls[1].path, "/api/reviews");
  assert.equal(h.context.closed, true);
});

test("blank fields and oversized path lists never submit", async () => {
  let calls = 0;
  const h = harness(async () => { calls++; });
  h.element("review-run-id").value = "  ";
  await h.run("saveReviewRequest()");
  h.element("review-run-id").value = "run-1";
  for (const paths of [Array(201).fill("file").join("\n"), "x".repeat(501)]) {
    h.element("review-paths").value = paths;
    await h.run("saveReviewRequest()");
  }
  h.element("review-reviewer").value = " ";
  await h.run('saveReviewTransition("r", "assign")');
  h.element("review-reviewer").value = "alice";
  h.element("review-reason").value = " ";
  await h.run('saveReviewTransition("r", "changes_requested")');
  assert.equal(calls, 0);
});

test("a pending save blocks duplicate submission and recovers after error", async () => {
  let reject, calls = 0;
  const h = harness(() => { calls++; return new Promise((_, fail) => { reject = fail; }); });
  const first = h.run('reviewQueueSave("/api/reviews", {})');
  assert.equal(h.element("review-save").disabled, true);
  await h.run('reviewQueueSave("/api/reviews", {})');
  assert.equal(calls, 1);
  reject(new Error("conflict"));
  await first;
  assert.equal(h.run("REVIEW_QUEUE.saving"), false);
  assert.equal(h.element("review-save").disabled, false);
  assert.ok(h.element("review-form-message").textContent.includes("conflict"));
  assert.ok(!h.context.closed);
});

test("transitions encode ID, preserve reviewer and submit feedback", async () => {
  const calls = [];
  const h = harness(async (path, options) => { calls.push({ path, options }); return { reviews: [] }; });
  h.element("review-reviewer").value = " alice ";
  h.element("review-reason").value = " fix tests ";
  await h.run('saveReviewTransition("review/a?b", "changes_requested")');
  assert.equal(calls[0].path, "/api/reviews/review%2Fa%3Fb/transition");
  assert.deepEqual(JSON.parse(calls[0].options.body), { action: "changes_requested", reviewer: "alice", reason: "fix tests" });
});

test("invalid transition cannot open a mutation form", () => {
  const h = harness();
  h.run('REVIEW_QUEUE.rows = [{id:"done",status:"approved"}]; openReviewTransition("done", "assign")');
  assert.equal(h.context.modal, undefined);
  h.run('REVIEW_QUEUE.rows = [{id:"pending",status:"requested",reviewer:"<alice>"}]; openReviewTransition("pending", "assign")');
  assert.ok(h.context.modal.body.includes("&lt;alice&gt;"));
  assert.ok(h.context.modal.body.includes(" required"));
});
