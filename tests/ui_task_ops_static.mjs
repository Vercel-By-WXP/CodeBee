import assert from "node:assert/strict";
import fs from "node:fs";

const html = fs.readFileSync("app/ui/index.html", "utf8");
const js = fs.readFileSync("app/ui/app.js", "utf8");

for (const id of ["f-template", "btn-template-start", "review-policy-dimensions", "hitl-list", "cli-discovery-list", "analytics-kpis", "set-browser-notify", "set-notify-sound"]) {
  assert.match(html, new RegExp(`id=["']${id}["']`), `missing UI anchor: ${id}`);
}
for (const endpoint of ["/api/review-policy", "/api/interventions", "/api/analytics", "/api/task-templates", "/api/cli-discovery", "/api/scaffold-adapter"]) {
  assert.match(js, new RegExp(endpoint.replaceAll("/", "\\/")), `missing API wiring: ${endpoint}`);
}
for (const symbol of ["saveReviewPolicy", "loadInterventions", "loadCliDiscovery", "generateAdapterScaffold", "loadAnalytics", "requestBrowserNotification"]) {
  assert.match(js, new RegExp(`function ${symbol}`), `missing UI handler: ${symbol}`);
}
console.log("ui_task_ops_static: ok");
