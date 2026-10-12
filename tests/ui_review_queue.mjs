/* Real-browser regression against isolated local review API; no external channels. */
import assert from "node:assert/strict";
import { spawn } from "node:child_process";
import { mkdtempSync, writeFileSync } from "node:fs";
import { tmpdir } from "node:os";
import { join, dirname } from "node:path";
import { fileURLToPath } from "node:url";
import { createServer } from "node:net";
import { bootEdge } from "./_ui_boot.mjs";

const ROOT = join(dirname(fileURLToPath(import.meta.url)), "..");
const outputDir = mkdtempSync(join(tmpdir(), "codebee-review-ui-"));
const server = createServer();
await new Promise((resolve) => server.listen(0, "127.0.0.1", resolve));
const port = server.address().port;
await new Promise((resolve) => server.close(resolve));
const url = "http://127.0.0.1:" + port;
const service = spawn("python", ["-X", "utf8", join(ROOT, "app", "main.py"),
  "--port", String(port), "--host", "127.0.0.1", "--no-browser"], {
  cwd: ROOT, stdio: "ignore", env: { ...process.env, TUTTI_DATA: join(outputDir, "data") },
});
const sleep = (ms) => new Promise((resolve) => setTimeout(resolve, ms));
let browser;
let checks = 0;
const check = (name, value) => { assert.ok(value, name); checks++; console.log("PASS " + name); };

try {
  let ready = false;
  for (let i = 0; i < 60 && !ready; i++) {
    try { ready = (await fetch(url + "/api/state")).ok; } catch {}
    if (!ready) await sleep(100);
  }
  check("isolated service starts", ready);
  browser = await bootEdge({ width: 1440, height: 950, awaitPromise: true });
  console.log(browser.envInfo.line);
  const { send, evalJs } = browser;
  const waitFor = async (expression) => {
    for (let i = 0; i < 100; i++) {
      if (await evalJs(expression)) return;
      await sleep(100);
    }
    throw new Error("Timed out: " + expression);
  };
  await send("Page.enable");
  await send("Page.navigate", { url });
  await waitFor("typeof switchTab === 'function' && S.state !== null");
  await evalJs('welcomeClose()');
  await evalJs('switchTab("agents")');
  await waitFor("REVIEW_QUEUE.loaded");
  check("queue loads through settings navigation", await evalJs('!$("sub-agents").classList.contains("hidden") && $("review-queue-list").textContent.includes("暂无匹配")'));

  const create = async (summary) => {
    await evalJs('openReviewRequest()');
    await evalJs(`$("review-run-id").value = "run-browser"; $("review-summary").value = ${JSON.stringify(summary)};
      $("review-paths").value = "ui/app.js\\ncore/main.py"; $("review-channel").value = "#frontend";
      $("review-save").click();`);
    await waitFor('!REVIEW_QUEUE.saving && $("modal").classList.contains("hidden")');
    return await evalJs(`REVIEW_QUEUE.rows.find((row) => row.summary === ${JSON.stringify(summary)}).id`);
  };
  const transition = async (id, action, reason = "") => {
    await evalJs(`document.querySelector('[data-review-id="${id}"][data-review-action="${action}"]').click();`);
    check("opens " + action + " form", await evalJs('!$("modal").classList.contains("hidden")'));
    await evalJs(`$("review-reviewer").value = "alice"; $("review-reason").value = ${JSON.stringify(reason)}; $("review-save").click();`);
    await waitFor('!REVIEW_QUEUE.saving && $("modal").classList.contains("hidden")');
  };

  const unsafeSummary = '<img src=x onerror="window.__reviewXss=true"> Review output';
  const first = await create(unsafeSummary);
  check("creation records normalized paths and channel", await evalJs('REVIEW_QUEUE.rows[0].paths.length === 2 && REVIEW_QUEUE.rows[0].channel === "#frontend"'));
  check("summary renders as text, without HTML execution", await evalJs('!window.__reviewXss && !$("review-queue-list").querySelector("img") && $("review-queue-list").textContent.includes("<img")'));
  check("requested queue cannot approve before assignment", await evalJs('!$("review-queue-list").querySelector("[data-review-action=approve]")'));
  await transition(first, "assign");
  check("assignment persisted reviewer", await evalJs('REVIEW_QUEUE.rows[0].status === "assigned" && REVIEW_QUEUE.rows[0].reviewer === "alice"'));
  await transition(first, "approve", "Checked output");
  check("approved request leaves pending queue", await evalJs('!$("review-queue-list").querySelector("[data-review-action]")'));
  await evalJs('$("review-queue-filter").value = "approved"; $("review-queue-filter").dispatchEvent(new Event("change"));');
  check("approved filter shows saved feedback", await evalJs('$("review-queue-list").textContent.includes("Checked output")'));

  await evalJs('$("review-queue-filter").value = "active"; renderReviewQueue();');
  const second = await create("Second review");
  await transition(second, "assign");
  await transition(second, "changes_requested", "Please fix tests");
  check("change feedback remains actionable", await evalJs('$("review-queue-list").textContent.includes("Please fix tests") && !$("review-queue-list").querySelector("[data-review-action=approve]")'));
  await transition(second, "dismiss", "Resolved elsewhere");
  check("closed request leaves pending queue", await evalJs('$("review-queue-list").textContent.includes("暂无匹配")'));
  const third = await create("Expired review");
  await transition(third, "expire", "Review window elapsed");

  const invalidStatus = await evalJs(`fetch("/api/reviews/${first}/transition", {
    method: "POST", headers: authHeaders(), body: JSON.stringify({ action: "assign", reviewer: "bob" })
  }).then((response) => response.status)`);
  check("API rejects transition from terminal status", invalidStatus === 400);
  const persisted = await evalJs('api("/api/reviews")');
  check("all final states persisted through real API", ["approved", "dismissed", "expired"].every((status) => persisted.reviews.some((row) => row.status === status)));
  await evalJs('setLangBtn("en"); $("review-queue-filter").value = "all"; renderReviewQueue();');
  check("queue and dynamic status localize to English", await evalJs('$("review-queue-title").textContent === "Human review queue" && $("review-queue-list").textContent.includes("Approved")'));
  await send("Emulation.setDeviceMetricsOverride", { width: 390, height: 844, deviceScaleFactor: 1, mobile: false });
  check("queue cards remain within mobile panel", await evalJs('Array.from(document.querySelectorAll(".review-request")).every((card) => card.scrollWidth <= card.clientWidth + 1)'));
  await send("Emulation.clearDeviceMetricsOverride");
  await evalJs('$("review-queue-title").scrollIntoView({block:"start"})');
  const shot = await send("Page.captureScreenshot", { format: "png" });
  writeFileSync(join(outputDir, "review-queue.png"), Buffer.from(shot.result.data, "base64"));
  console.log("SCREENSHOT " + join(outputDir, "review-queue.png"));
  console.log(`Review queue browser regression: ${checks} passed`);
} finally {
  if (browser) await browser.close();
  service.kill();
}
