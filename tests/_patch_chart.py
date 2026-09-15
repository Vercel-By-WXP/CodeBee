# -*- coding: utf-8 -*-
"""图表补丁（可重复执行）：2~14 天横向构成条 + --uc-* 配色。幂等：已打则跳过。"""
import io
import sys

p = "app/ui/app.js"
src = io.open(p, encoding="utf-8").read()
changed = False

if "usageRowsHtml" not in src:
    old = '''  if (!byDay || !byDay.length) return '<p class="hint">（暂无数据）</p>';
  if (byDay.length === 1) return '<div class="usage-single">' + usageSingleDay(byDay[0]) + "</div>";'''
    new = '''  if (!byDay || !byDay.length) return '<p class="hint">（暂无数据）</p>';
  if (byDay.length === 1) return '<div class="usage-single">' + usageSingleDay(byDay[0]) + "</div>";
  if (byDay.length <= 14) return usageRowsHtml(byDay);'''
    if src.count(old) != 1:
        print("head anchor count:", src.count(old))
        sys.exit(1)
    src = src.replace(old, new)
    fn_anchor = "function usageTrendSvg(byDay) {"
    fn_code = '''/* 多日横向构成条（2~14 天）："单日构成卡"的逐日版——每天一行全宽堆叠条，
 * 日期在左、总量在右；零用量那天画灰色的细占位行。天数多时行太多，仍走竖柱。 */
function usageRowsHtml(byDay) {
  const p = (n) => String(n).padStart(2, "0");
  const now = new Date();
  const today = now.getFullYear() + "-" + p(now.getMonth() + 1) + "-" + p(now.getDate());
  const rows = byDay.map((d) => {
    const tok = d.tokens || 0;
    const tip = esc(d.day + "　总 " + fmtTok(tok) + (tok > 0 ? "（输入 " + fmtTok(d.input || 0) +
      " · 输出 " + fmtTok(d.output || 0) + " · 其他/缓存 " + fmtTok(Math.max(0, tok - (d.input || 0) - (d.output || 0))) + "）" : "") +
      "\\n调用 " + (d.calls || 0) + " 次 · " + fmtUsd(d.cost_usd));
    let track;
    if (tok > 0) {
      const seg = (cls, v) => {
        const pct = (v || 0) * 100 / tok;
        return pct > 0 ? '<div class="ur-seg us-' + cls + '" style="width:' + pct.toFixed(2) + '%" title="' + tip + '"></div>' : "";
      };
      track = seg("in", d.input) + seg("ca", Math.max(0, tok - (d.input || 0) - (d.output || 0))) + seg("out", d.output);
    } else {
      track = '<div class="ur-zero" title="' + tip + '">无用量</div>';
    }
    return '<div class="ur-row' + (tok > 0 ? "" : " zero") + (d.day === today ? " today" : "") + '">' +
      '<span class="ur-day">' + esc(String(d.day).slice(5)) + "</span>" +
      '<div class="ur-track">' + track + "</div>" +
      '<span class="ur-sum">' + (tok > 0 ? esc(fmtTok(tok)) : "—") + "</span></div>";
  }).join("");
  const legend = '<div class="ur-legend">' +
    '<span><i class="us-in"></i>输入</span><span><i class="us-ca"></i>缓存/其他</span><span><i class="us-out"></i>输出</span></div>';
  return '<div class="usage-rows">' + legend + rows + "</div>";
}

function usageTrendSvg(byDay) {'''
    if src.count(fn_anchor) != 1:
        print("fn anchor count:", src.count(fn_anchor))
        sys.exit(1)
    src = src.replace(fn_anchor, fn_code)
    changed = True

# 配色（幂等：只替换还没换过的）
pairs = [
    ('" fill="var(--accent)"><title>', '" fill="var(--uc-in)"><title>'),
    ('" fill="var(--ok)" opacity="0.55"><title>', '" fill="var(--uc-ca)" opacity="0.85"><title>'),
    ('" fill="var(--accent2)" opacity="0.9"><title>', '" fill="var(--uc-out)" opacity="0.95"><title>'),
    ('fill="var(--accent)"/><text class="uc-x" x="20" y="11">输入</text>',
     'fill="var(--uc-in)"/><text class="uc-x" x="20" y="11">输入</text>'),
    ('fill="var(--ok)" opacity="0.55"/><text class="uc-x" x="66" y="11">缓存/其他</text>',
     'fill="var(--uc-ca)" opacity="0.85"/><text class="uc-x" x="66" y="11">缓存/其他</text>'),
    ('fill="var(--accent2)" opacity="0.9"/><text class="uc-x" x="132" y="11">输出</text>',
     'fill="var(--uc-out)" opacity="0.95"/><text class="uc-x" x="132" y="11">输出</text>'),
]
for old, new in pairs:
    if old in src:
        src = src.replace(old, new)
        changed = True

io.open(p, "w", encoding="utf-8", newline="").write(src)
print("patched:", changed)
