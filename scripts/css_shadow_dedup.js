/* css_shadow_dedup.js —— 剪枝 CSS 中被完全遮蔽的重复声明（级联等值重构）。
 * 判定：同一纯 @media 上下文（或均在顶层）、选择器文本相同、属性相同 ⇒ 保留最后一次；
 *       任一参与方含 !important、或声明位于嵌套规则/@supports/@keyframes 内 ⇒ 不动。
 * 用法：node scripts/css_shadow_dedup.js [--write] <file.css>...
 *       默认只出报告；--write 时把结果写为 <file>.pruned.css 供审查。 */
"use strict";
const fs = require("fs");

function stripComments(css) {
  let out = "";
  for (let i = 0; i < css.length; i++) {
    const c = css[i];
    if (c === "/" && css[i + 1] === "*") {
      let j = i + 2;
      while (j < css.length && !(css[j] === "*" && css[j + 1] === "/")) j++;
      out += css.slice(i, Math.min(j + 2, css.length)).replace(/[^\n]/g, " ");
      i = j + 1;
    } else if (c === '"' || c === "'") {
      let j = i + 1;
      while (j < css.length && css[j] !== c) { if (css[j] === "\\") j++; j++; }
      out += css.slice(i, j + 1);
      i = j;
    } else out += c;
  }
  return out;
}

/* 单遍扫描：维护括号栈，收集「顶层或纯@media 下的规则」及其声明区间。 */
function scan(css) {
  const mask = stripComments(css);
  const frames = [{ kind: "root", ctx: "ROOT", pure: true }];
  const rules = [];
  let token = "";       // 当前累积的 prelude / 声明文本
  let inStr = null, paren = 0, segStart = 0;
  const normSel = (s) => s.replace(/\s+/g, " ").trim();
  const endDecl = (j, withSemi) => {
    const f = frames[frames.length - 1];
    if (f.kind === "rule" && f.safe) {
      const raw = css.slice(segStart, j);
      const t = raw.trim();
      const ci = t.indexOf(":");
      if (t && ci > 0 && !/[{}();]/.test(t.slice(0, ci))) {
        let prop = t.slice(0, ci).trim().toLowerCase();
        let val = t.slice(ci + 1).trim();
        let important = false;
        const im = /!\s*important$/i.exec(val);
        if (im) { important = true; val = val.slice(0, im.index).trim(); }
        if (/^-{1,2}[a-z]/.test(prop) && !/\s/.test(prop)) {
          f.decls.push({ prop, value: val.replace(/\s+/g, " "), important, start: segStart, end: j + (withSemi ? 1 : 0), semi: withSemi });
        }
      }
    }
    segStart = j + 1;
  };
  const popRule = (i) => {
    const f = frames.pop();
    if (f.kind === "rule" && f.safe) {
      const bodyEnd = i;
      rules.push({ selector: f.selector, ctx: f.ctx, from: f.bodyStart, to: bodyEnd, decls: f.decls });
    }
    segStart = i + 1;
  };
  for (let i = 0; i < mask.length; i++) {
    const c = mask[i];
    if (inStr) { if (c === inStr && mask[i - 1] !== "\\") inStr = null; token += c; continue; }
    if (c === '"' || c === "'") { inStr = c; token += c; continue; }
    if (c === "(") { paren++; token += c; continue; }
    if (c === ")") { paren--; token += c; continue; }
    if (paren > 0) { token += c; continue; }
    const f = frames[frames.length - 1];
    if (c === "{") {
      const prelude = normSel(token);
      if (f.kind === "rule" && !f.safe) f.safe = true; // 已有声明后又嵌套 ⇒ 规则含嵌套，整条不处理
      if (/^@/.test(prelude)) {
        const name = (prelude.match(/^@([\w-]+)/) || [, ""])[1].toLowerCase();
        const pureMedia = name === "media" && !/-\w+-|\bx\b|width\s*<|>=/.test(prelude.slice(6, 18));
        frames.push({ kind: "at", pure: pureMedia, ctx: f.ctx === "ROOT" ? (pureMedia ? prelude : "SKIP") : f.ctx + " >> " + (pureMedia ? prelude : "SKIP"), safe: !pureMedia });
      } else if (f.kind === "root" || (f.kind === "at" && f.pure)) {
        frames.push({ kind: "rule", selector: prelude, ctx: f.ctx, bodyStart: i + 1, decls: [], safe: true });
      } else {
        frames.push({ kind: "rule", selector: prelude, ctx: "?", bodyStart: i + 1, decls: [], safe: false });
      }
      token = ""; segStart = i + 1;
      continue;
    }
    if (c === "}") {
      if (f.kind === "rule") { if (f.safe) endDecl(i, false); }
      if (f.kind === "rule") { if (f.safe) popRule(i); else frames.pop(), segStart = i + 1; }
      else frames.pop();
      token = ""; segStart = i + 1;
      continue;
    }
    if (c === ";" && f.kind === "rule") { endDecl(i, true); token = ""; continue; }
    token += c;
  }
  return rules;
}

/* 遮蔽检测：ctx+selectorPart+prop 相同 ⇒ 早者被晚者遮蔽。 */
function findShadowed(rules) {
  const last = new Map();
  const order = [];
  rules.forEach((r, ri) => {
    const parts = r.selector.split(",").map((s) => s.trim()).filter(Boolean);
    r.decls.forEach((d, di) => {
      for (const p of parts) {
        const key = r.ctx + "|" + p + "|" + d.prop;
        if (!last.has(key)) { order.push(key); last.set(key, []); }
        last.get(key).push({ ri, di, d, multi: parts.length > 1 });
      }
    });
  });
  const doomed = [];
  let skippedImportant = 0, skippedMulti = 0;
  for (const key of order) {
    const list = last.get(key);
    const keeper = list[list.length - 1];
    for (let k = 0; k < list.length - 1; k++) {
      const it = list[k];
      if (it.d.important || keeper.d.important) { skippedImportant++; continue; }
      if (it.multi || keeper.multi) { skippedMulti++; continue; } // 逗号复合选择器保守跳过
      doomed.push(it);
    }
  }
  return { doomed, skippedImportant, skippedMulti, last };
}

function rewrite(css, rules, doomed) {
  const lines = css.split("\n");
  const lineStarts = [];
  let off = 0;
  for (const l of lines) { lineStarts.push(off); off += l.length + 1; }
  const lineOf = (pos) => { let lo = 0, hi = lineStarts.length - 1; while (lo < hi) { const m = (lo + hi + 1) >> 1; if (lineStarts[m] <= pos) lo = m; else hi = m - 1; } return lo; };
  const cuts = new Map(); // lineIdx -> [{from,to}] 行内列区间
  for (const it of doomed) {
    const d = it.d;
    const li = lineOf(d.start);
    if (!cuts.has(li)) cuts.set(li, []);
    cuts.get(li).push([d.start - lineStarts[li], d.end - lineStarts[li]]);
  }
  const emptied = new Set();
  const byRule = new Map();
  for (const it of doomed) byRule.set(it.ri, (byRule.get(it.ri) || 0) + 1);
  for (const [ri, n] of byRule) if (rules[ri].decls.length === n) emptied.add(ri);
  // 空规则的 { 行与 } 行删除：{ 行 = 选择器独占行时；否则保留（同行可能是 `sel { decl }`）
  const dropLines = new Set();
  for (const ri of emptied) {
    const r = rules[ri];
    const openLine = lineOf(r.from - 1);
    const closeLine = lineOf(r.to);
    const openText = lines[openLine];
    const closeText = lines[closeLine];
    const openAlone = /\{\s*$/.test(openText.replace(/[^\s]*\{\s*$/, (m) => m)) && /\{\s*$/.test(openText);
    const closeAlone = /^\s*\}\s*$/.test(closeText);
    if (openLine === closeLine) continue; // 单行规则：交给列剪枝后判空
    if (closeAlone) dropLines.add(closeLine);
    if (openAlone && r.selector && r.from > lineStarts[openLine]) {
      const rest = openText.slice(0, openText.lastIndexOf("{")).trim();
      const selNorm = r.selector.replace(/\s+/g, " ");
      if (rest.replace(/\s+/g, " ") === selNorm) dropLines.add(openLine);
    }
  }
  const out = [];
  for (let li = 0; li < lines.length; li++) {
    if (dropLines.has(li)) continue;
    let text = lines[li];
    if (cuts.has(li)) {
      const base = lineStarts[li];
      void base;
      const segs = cuts.get(li).sort((a, b) => b[0] - a[0]);
      for (const [from, to] of segs) {
        let f = from, t = to;
        while (t < text.length && text[t] !== ";" && text[t] !== "\n") t++; // 含结尾分号
        if (text[t] === ";") t++;
        while (t < text.length && (text[t] === " " || text[t] === "\t")) t++;
        while (f > 0 && text[f - 1] === " ") f--;
        if (f > 0 && text[f - 1] === "\n" || f === 0) { /* 行首 */ }
        text = text.slice(0, f) + text.slice(t);
      }
    }
    const ruleBodyLine = /^\s*[{}]/.test(text);
    void ruleBodyLine;
    if (!text.trim() && cuts.has(li)) continue;               // 剪枝后整行皆空 ⇒ 丢行
    if (/\{\s*\}\s*$/.test(text) && cuts.has(li)) continue;    // `sel{}` 残留 ⇒ 丢行
    if (/^\s*[\w.#:%[>~+*,-].*\{\s*$/.test(text) && cuts.has(li + 1000000)) void 0;
    out.push(text.replace(/\s+$/, ""));
  }
  // 再清一轮：删除剪枝后只剩 `}` 或与 { 相邻成空体的行
  const cleaned = [];
  for (let k = 0; k < out.length; k++) {
    const cur = out[k], next = out[k + 1];
    if (/^\s*\{\s*$/.test(cur) && next && /^\s*\}\s*$/.test(next)) { k++; continue; }
    cleaned.push(cur);
  }
  return cleaned.join("\n");
}

const args = process.argv.slice(2);
const write = args.includes("--write");
const list = args.includes("--list");
for (const file of args.filter((a) => !a.startsWith("--"))) {
  const css = fs.readFileSync(file, "utf8");
  const rules = scan(css);
  const { doomed, skippedImportant, skippedMulti, last: lastCache } = findShadowed(rules);
  const before = css.split("\n").length;
  const lineOf = (pos) => css.slice(0, pos).split("\n").length;
  if (list) for (const it of [...doomed].sort((a, b) => a.d.start - b.d.start)) {
    const r = rules[it.ri];
    const k = r.ctx + "|" + r.selector + "|" + it.d.prop;
    const grp = lastCache.get(k) || [];
    const keeper = grp[grp.length - 1];
    const kl = keeper ? lineOf(keeper.d.start) : "?";
    console.log(`L${lineOf(it.d.start)}<-L${kl}  [${r.ctx}]  ${r.selector.slice(0, 60)}  ${it.d.prop}: ${it.d.value.slice(0, 40)}`);
  }
  console.log(`\n== ${file} ==`);
  console.log(`topRules=${rules.length}  shadowedDecls=${doomed.length}  skip(!important)=${skippedImportant}  skip(comma-selector)=${skippedMulti}`);
  const freq = new Map();
  for (const it of doomed) {
    const r = rules[it.ri];
    const k = `${r.ctx === "ROOT" ? "" : "@"} {${r.selector.slice(0, 44)}} ${it.d.prop}`;
    freq.set(k, (freq.get(k) || 0) + 1);
  }
  [...freq.entries()].sort((a, b) => b[1] - a[1]).slice(0, 20).forEach(([k, n]) => console.log(`  x${n}  ${k}`));
  const byProp = new Map();
  for (const it of doomed) byProp.set(it.d.prop, (byProp.get(it.d.prop) || 0) + 1);
  console.log("  props:", [...byProp.entries()].sort((a, b) => b[1] - a[1]).slice(0, 12).map(([p, n]) => `${p}=${n}`).join(" "));
  if (write) {
    const outCss = rewrite(css, rules, doomed);
    const target = file.replace(/\.css$/, "") + ".pruned.css";
    fs.writeFileSync(target, outCss, "utf8");
    console.log(`  ${before} lines -> ${outCss.split("\n").length} lines  => ${target}`);
  }
}
