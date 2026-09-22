/* 检查重复键的两处值是否一致：一致=无害冗余，不一致=覆盖真问题 */
import { readFileSync } from "node:fs";
import { join, dirname } from "node:path";
import { fileURLToPath } from "node:url";

const ROOT = join(dirname(fileURLToPath(import.meta.url)), "..");
const src = readFileSync(join(ROOT, "app/ui/i18n.js"), "utf8");
const m = src.match(/const EN = \{([\s\S]*?)\n  \};/);
const entries = [];
const re = /^\s{4}"((?:[^"\\]|\\.)*)":\s*"((?:[^"\\]|\\.)*)",?\s*$/gm;
let hit;
while ((hit = re.exec(m[1])) !== null) {
  const line = src.slice(0, m.index + hit.index).split("\n").length;
  entries.push({ key: hit[1], val: hit[2], line });
}
const byKey = new Map();
for (const e of entries) {
  if (!byKey.has(e.key)) byKey.set(e.key, []);
  byKey.get(e.key).push(e);
}
let same = 0, diff = 0;
for (const [k, list] of byKey) {
  if (list.length < 2) continue;
  const uniq = new Set(list.map((e) => e.val));
  if (uniq.size === 1) { same++; continue; }
  diff++;
  console.log("DIFF-VALUE:", JSON.stringify(k));
  for (const e of list) console.log("   line", e.line, "=>", JSON.stringify(e.val));
}
console.log("dup keys total:", [...byKey.values()].filter((l) => l.length > 1).length,
  "| same-value:", same, "| diff-value:", diff);
