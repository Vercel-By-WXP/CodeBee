/* i18n 字典重复键检查（后者覆盖前者，是静默 bug 源）。
 * 用法：node tests/_chk_i18n_dups.mjs */
import { readFileSync } from "node:fs";
import { join, dirname } from "node:path";
import { fileURLToPath } from "node:url";

const ROOT = join(dirname(fileURLToPath(import.meta.url)), "..");
const src = readFileSync(join(ROOT, "app/ui/i18n.js"), "utf8");
const m = src.match(/const EN = \{([\s\S]*?)\n  \};/);
if (!m) { console.error("未找到 EN 字典"); process.exit(1); }
const seen = new Map();
const dups = [];
const re = /^\s{4}"((?:[^"\\]|\\.)*)":/gm;
let hit;
while ((hit = re.exec(m[1])) !== null) {
  const k = hit[1];
  if (seen.has(k)) dups.push(k);
  else seen.set(k, true);
}
console.log("键总数:", seen.size, "· 重复键:", dups.length ? dups.join(" | ") : "无");
process.exit(dups.length ? 2 : 0);
