/* 定位 i18n EN 字典重复键的行号（配合 _chk_i18n_dups.mjs 用） */
import { readFileSync } from "node:fs";
import { join, dirname } from "node:path";
import { fileURLToPath } from "node:url";

const ROOT = join(dirname(fileURLToPath(import.meta.url)), "..");
const src = readFileSync(join(ROOT, "app/ui/i18n.js"), "utf8");
const m = src.match(/const EN = \{([\s\S]*?)\n  \};/);
const seen = new Map(), dups = [];
const re = /^\s{4}"((?:[^"\\]|\\.)*)":/gm;
let hit;
while ((hit = re.exec(m[1])) !== null) {
  const k = hit[1];
  if (seen.has(k)) dups.push(k); else seen.set(k, true);
}
const lineOf = (absIdx) => src.slice(0, absIdx).split("\n").length;
for (const k of dups) {
  const lines = [];
  const esc = k.replace(/[.*+?^${}()|[\]\\]/g, "\\$&");
  const re2 = new RegExp('^\\s{4}"' + esc + '":', "gm");
  let h;
  while ((h = re2.exec(m[1])) !== null) lines.push(lineOf(m.index + h.index));
  console.log("DUP:", JSON.stringify(k), "-> lines", lines.join(","));
}
console.log("total dups:", dups.length);
