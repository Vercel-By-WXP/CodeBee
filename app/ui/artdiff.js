/* 成品两版对比的纯 diff 计算（OpenCreator「版本化对比」借鉴）。
 * 独立文件无 DOM 依赖：app.js 用 window.ArtifactDiff 渲染，tests 用
 * module.exports 直测。算法：公共前后缀修剪 + 中段 LCS（上限保护，
 * 超限诚实降级为整块替换计数，不装作对齐了）。 */
(function (root, factory) {
  var api = factory();
  if (typeof window !== "undefined") window.ArtifactDiff = api;
  if (typeof module !== "undefined" && module.exports) module.exports = api;
})(typeof self !== "undefined" ? self : this, function () {
  "use strict";

  var MID_CAP = 400;          // 中段行数单边上限
  var MID_PRODUCT_CAP = 160000; // LCS DP 格子上限（400×400）

  /* LCS 回溯产出统一 diff 行：[{t:"="|"-"/"+", s:行文本}]（只对中段）。 */
  function lcsRows(A, B) {
    var n = A.length, m = B.length;
    var dp = [];
    for (var k = 0; k <= n; k++) dp.push(new Uint32Array(m + 1));
    for (var i = n - 1; i >= 0; i--)
      for (var j = m - 1; j >= 0; j--)
        dp[i][j] = A[i] === B[j] ? dp[i + 1][j + 1] + 1
          : (dp[i + 1][j] >= dp[i][j + 1] ? dp[i + 1][j] : dp[i][j + 1]);
    var rows = [];
    i = 0; j = 0;
    while (i < n && j < m) {
      if (A[i] === B[j]) { rows.push({ t: "=", s: A[i] }); i++; j++; }
      else if (dp[i + 1][j] >= dp[i][j + 1]) { rows.push({ t: "-", s: A[i] }); i++; }
      else { rows.push({ t: "+", s: B[j] }); j++; }
    }
    while (i < n) { rows.push({ t: "-", s: A[i] }); i++; }
    while (j < m) { rows.push({ t: "+", s: B[j] }); j++; }
    return rows;
  }

  /* 对外主口：旧版 a、新版 b → {
   *   prefix/suffix: 相同行数（渲染端折叠展示），
   *   identical: 布尔，两版一致，
   *   rows: 中段统一 diff 行（超限时为 null），
   *   oversized: {a, b} 超限时中段行数（诚实整块计数） } */
  function diff(a, b) {
    a = String(a == null ? "" : a);
    b = String(b == null ? "" : b);
    var A = a.split("\n"), B = b.split("\n");
    var p = 0;
    while (p < A.length && p < B.length && A[p] === B[p]) p++;
    var s = 0;
    while (s < A.length - p && s < B.length - p &&
           A[A.length - 1 - s] === B[B.length - 1 - s]) s++;
    var midA = A.slice(p, A.length - s), midB = B.slice(p, B.length - s);
    var out = { prefix: p, suffix: s, identical: !midA.length && !midB.length,
                rows: null, oversized: null };
    if (out.identical) return out;
    if (midA.length > MID_CAP || midB.length > MID_CAP ||
        midA.length * midB.length > MID_PRODUCT_CAP) {
      out.oversized = { a: midA.length, b: midB.length };
      return out;
    }
    out.rows = lcsRows(midA, midB);
    return out;
  }

  /* rows → 摘要计数（+x/-y，渲染端徽章用）。 */
  function counts(d) {
    var add = 0, del = 0;
    if (d.oversized) return { add: d.oversized.b, del: d.oversized.a };
    (d.rows || []).forEach(function (r) {
      if (r.t === "+") add++; else if (r.t === "-") del++;
    });
    return { add: add, del: del };
  }

  return { diff: diff, counts: counts, lcsRows: lcsRows };
});
