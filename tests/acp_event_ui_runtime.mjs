import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { join } from "node:path";

const source = readFileSync(join(process.cwd(), "app", "ui", "app.js"), "utf8");
const start = source.indexOf("function chatAcpEventsHTML");
const end = source.indexOf("\nfunction chatLogKey", start);
assert(start >= 0 && end > start, "chatAcpEventsHTML must remain a standalone helper");

const t = (value) => ({
  "ACP 活动": "ACP activity",
  "上下文压缩": "Context compaction",
  "压缩摘要": "Compaction summary",
  "通知": "Notice",
  "条": "events",
}[value] || value);
const esc = (value) => String(value ?? "")
  .replaceAll("&", "&amp;")
  .replaceAll("<", "&lt;")
  .replaceAll(">", "&gt;")
  .replaceAll('"', "&quot;")
  .replaceAll("'", "&#39;");
const chatAcpEventsHTML = Function("t", "esc", `${source.slice(start, end)}; return chatAcpEventsHTML;`)(t, esc);

const html = chatAcpEventsHTML({ acp_events: [
  { sessionUpdate: "compaction_update", compactionId: "cmp-1", status: "in_progress",
    summary: [{ type: "text", text: "Retained context\n<safe>" }] },
  { sessionUpdate: "compaction_summary_chunk", compactionId: "cmp-1",
    content: "A streamed summary" },
  { sessionUpdate: "compaction_summary_chunk", compactionId: "cmp-1",
    content: "A second chunk" },
  { sessionUpdate: "compaction_update", compactionId: "cmp-1", status: "completed",
    summary: [{ type: "text", text: "Retained context\n<safe>" }] },
  { sessionUpdate: "notice", severity: "warning", title: "Fallback <provider>",
    description: "Continuing & safely." },
  { sessionUpdate: "plan", entries: [{ status: "in_progress", content: "Keep plan" }] },
] });

assert.match(html, /Context compaction/);
assert.match(html, /3 events/);
assert.match(html, /cmp-1/);
assert.match(html, /completed/);
assert.match(html, /Retained context/);
assert.match(html, /Compaction summary/);
assert.match(html, /A streamed summary\nA second chunk/);
assert.match(html, /Notice/);
assert.match(html, /Fallback &lt;provider&gt;/);
assert.match(html, /Continuing &amp; safely\./);
assert.match(html, /Keep plan/);
assert.doesNotMatch(html, /Fallback <provider>/);
assert.doesNotMatch(html, /<safe>/);
console.log("acp event UI runtime: ok");
