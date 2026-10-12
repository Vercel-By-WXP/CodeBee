import test from "node:test";
import assert from "node:assert/strict";
import fs from "node:fs";
import vm from "node:vm";

const source = fs.readFileSync(new URL("../app/ui/app.js", import.meta.url), "utf8");
const start = source.indexOf("function chatBodyHTML(text) {");
const end = source.indexOf("\nasync function renderChat", start);
assert.notEqual(start, -1, "chatBodyHTML exists");
assert.notEqual(end, -1, "chatBodyHTML has a clear end boundary");
const codeBlockHTML = (text) => `<pre><code>${text}</code></pre>`;
const esc = (value) => String(value).replace(/[&<>"']/g, (ch) => ({
  "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;",
}[ch]));
const chatBodyHTML = vm.runInNewContext(
  `${source.slice(start, end)}\nchatBodyHTML;`,
  { esc, codeBlockHTML },
);

test("preserves Markdown heading, paragraph, list and quote structure", () => {
  const html = chatBodyHTML([
    "Summary paragraph.", "", "### Priorities", "",
    "1. **Search settings**", "2. Split oversized page", "", "> Keep existing tools",
  ].join("\n"));
  assert.match(html, /<p>Summary paragraph\.<\/p>/);
  assert.match(html, /<h3>Priorities<\/h3>/);
  assert.match(html, /<ol><li><strong>Search settings<\/strong><\/li><li>Split oversized page<\/li><\/ol>/);
  assert.match(html, /<blockquote><p>Keep existing tools<\/p><\/blockquote>/);
});

test("keeps fenced code blocks and renders inline code", () => {
  const html = chatBodyHTML("Use `npm test`.\n\n```js\nconst ok = true;\n```");
  assert.match(html, /Use <code>npm test<\/code>\./);
  assert.match(html, /<pre><code>const ok = true;<\/code><\/pre>/);
});

test("escapes raw HTML and only creates links for allowed schemes", () => {
  const html = chatBodyHTML(
    '<img src=x onerror="alert(1)"> [safe](https://example.com) [bad](javascript:alert(1))',
  );
  assert.match(html, /&lt;img src=x onerror=&quot;alert\(1\)&quot;&gt;/);
  assert.match(html, /<a href="https:\/\/example\.com" target="_blank" rel="noopener noreferrer">safe<\/a>/);
  assert.doesNotMatch(html, /href="javascript:/);
  assert.match(html, /\[bad\]\(javascript:alert\(1\)\)/);
});
