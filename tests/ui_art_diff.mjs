/* artdiff.js 纯函数直测（node tests/ui_art_diff.mjs）。
 * 覆盖：一致性/前后缀修剪/LCS 对齐/插入删除计数/超限降级/空输入。 */
const { createRequire } = await import("node:module");
const require = createRequire(import.meta.url);
const AD = require("../app/ui/artdiff.js");

let pass = 0, fail = 0;
function check(name, cond, extra) {
  if (cond) { pass++; console.log("[PASS] " + name); }
  else { fail++; console.log("[FAIL] " + name + (extra ? " :: " + extra : "")); }
}

// 1) 一致
const d0 = AD.diff("a\nb\nc", "a\nb\nc");
check("identical", d0.identical === true && d0.prefix === 3 && d0.suffix === 0);

// 2) 纯新增（后缀修剪，前缀保留）
const d1 = AD.diff("x\ny", "x\nNEW\ny");
check("insert only", !d1.identical && d1.prefix === 1 && d1.suffix === 1 &&
  JSON.stringify(d1.rows) === JSON.stringify([{ t: "+", s: "NEW" }]));
check("counts insert", JSON.stringify(AD.counts(d1)) === '{"add":1,"del":0}');

// 3) 修改中段
const d2 = AD.diff("第一章\n旧正文\n结尾", "第一章\n新正文\n结尾");
check("modify mid", d2.prefix === 1 && d2.suffix === 1);
const adds = d2.rows.filter(r => r.t === "+").map(r => r.s);
const dels = d2.rows.filter(r => r.t === "-").map(r => r.s);
check("modify rows", adds.join() === "新正文" && dels.join() === "旧正文");

// 4) 纯删除
const d3 = AD.diff("a\nGONE\nb", "a\nb");
check("delete only", AD.counts(d3).del === 1 && AD.counts(d3).add === 0);

// 5) 多行交错（LCS 正确性：保序对齐）
const d4 = AD.diff("1\n2\n3\n4", "2\n3\n5");
const seq = d4.rows.map(r => r.t + r.s).join("|");
check("lcs interleave", seq === "-1|=2|=3|-4|+5", seq);

// 6) 超限降级（>400 行中段）
const big1 = Array.from({ length: 500 }, (_, i) => "old" + i).join("\n");
const big2 = Array.from({ length: 520 }, (_, i) => "new" + i).join("\n");
const d5 = AD.diff(big1, big2);
check("oversized degrade", d5.rows === null && d5.oversized.a === 500 && d5.oversized.b === 520);
check("counts oversized", AD.counts(d5).del === 500 && AD.counts(d5).add === 520);

// 7) 空输入
const d6 = AD.diff("", "a\nb");
check("empty old", d6.prefix === 0 && AD.counts(d6).add === 2);
const d7 = AD.diff("", "");
check("both empty identical", d7.identical === true);

// 8) 相同行多副本不串（LCS 稳定）
const d8 = AD.diff("x\nx\nx", "x\nx");
check("dup lines", AD.counts(d8).del === 1 && AD.counts(d8).add === 0);

console.log(fail ? `\n${fail} FAILED` : `\nAll ${pass} checks passed`);
process.exit(fail ? 1 : 0);
