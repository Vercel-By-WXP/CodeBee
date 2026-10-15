import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { runInNewContext } from "node:vm";

const source = readFileSync(new URL("../app/ui/app.js", import.meta.url), "utf8");
function block(start, end) {
  const a = source.indexOf(start), b = source.indexOf(end, a + start.length);
  assert(a >= 0 && b > a, start);
  return source.slice(a, b);
}
const esc = text => String(text).replace(/[&<>"']/g, c => ({
  "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
const context = { esc, t: x => x };
runInNewContext(block("function executionConfigHtml(run)", "function renderChatContext(direct)"), context);
runInNewContext(block("function hookDiagnosticHtml(hook, diagnostics)", "function hookHarvest()"), context);
runInNewContext(block("function pluginCardHtml(p)", "function renderPlugins()"), context);

assert.equal(context.executionConfigHtml({}), "");
const config = context.executionConfigHtml({ execution_snapshot: { configuration: {
  parameters: { thinking: { value: "high", source: "task.params" } },
  selection: { source: "task.direct_agent", overrides: ["direct_model"] },
} }, steps: [{ role: "draft", configuration: { agent: "codex-cli", model: "primary",
  binding_source: "binding.explicit", result: { model: "backup<script>", provider_id: "p2" } } }] });
assert(config.includes("任务修改"));
assert(config.includes("已被覆盖：direct_model"));
assert(config.includes("backup&lt;script&gt;"));
assert(!config.includes("<script>"));
assert(!config.includes("primary"));

const hook = context.hookDiagnosticHtml({ id: "h1", event: "task_start" }, {
  recent: [{ hook_id: "h1", event: "task_start", status: "succeeded", duration_ms: 9, exit_code: 0 }],
  last_errors: [{ hook_id: "h1", event: "task_start", error: "<img src=x>" }],
});
assert(hook.includes("成功"));
assert(hook.includes("exit 0"));
assert(hook.includes("最近错误：&lt;img src=x&gt;"));
assert(!hook.includes("<img"));
assert.equal(context.hookDiagnosticHtml({ id: "other" }, {}), "");

const card = context.pluginCardHtml({ id: "sample", installed: true, enabled: false,
  state: "disabled", version: "1.0.0", compatibility: { compatible: false, reason: "Need <2.0>" } });
assert(card.includes("pluginUpdate('sample')"));
assert(card.includes("Need &lt;2.0&gt;"));
assert(!card.includes('type="checkbox" checked'));

context.S = { capabilities: { plugins: [{ id: "blocked", installed: true, enabled: true,
  available: true, state: "enabled", compatibility: { compatible: false, reason: "Need <2>" } }] } };
runInNewContext(block("function cmpCapability(id)", "function cmpCapabilityRows()"), context);
assert.equal(context.cmpCapability("blocked").enabled, false);
context.S.capabilities.plugins[0].compatibility.compatible = true;
assert.equal(context.cmpCapability("blocked").enabled, true);
context.S.capabilities.plugins[0].state = "blocked";
assert.equal(context.cmpCapability("blocked").enabled, false);
console.log("borrow follow-up UI runtime: passed");
