# -*- coding: utf-8 -*-
"""CSS token 治理守测（P3，2026-11-01；DESIGN.md 设计宪法的自动化半环）。

复核口径（迭代计划 P3）：25 个 CSS、282 唯一自定义属性、95+ 跨文件重复、
`npx @google/design.md lint` 只校验 DESIGN.md 自身不扫 CSS——拦 CSS 回归
得自写。本守测三绿一债：

1. 【绿】var() 全有源：每个 var(--x) 要么在任一 CSS 定义、要么由 JS/HTML
   内联注入、要么带 fallback——悬空 var 是真实的断链 bug；
2. 【绿】--wb-* 家族按皮肤完备：DESIGN.md §2 记录的 7 个家族 token 必须在
   wb-themes.css 的每个皮肤分段里都有定义（61.3c「任何皮肤必须给全 --wb-*」
   的自动化形态），DESIGN.md 宪法本身也必须持续记载这 7 个名字；
3. 【债·xfail】跨文件同名定义 = 0：当前 111 个（部分是覆盖层架构的有意
   重定义，部分是无序重复，需逐个甄别收敛）——strict=False 的 xfail 让债务
   常驻可见而不破套件；清零之日自动转绿。
4. 【绿·冷冻线】重复数不得无界增长（≤160，2026-11-01 基线 111 + 并行在途
   手机壳余量）；谁把重复数顶破冷冻线谁负责收敛。
"""
from __future__ import annotations

import re
from collections import defaultdict
from pathlib import Path

import pytest

UI = Path(__file__).resolve().parents[1] / "app" / "ui"

WB_FAMILY = ("--wb-panel", "--wb-panel2", "--wb-border", "--wb-border-strong",
             "--wb-text", "--wb-accent", "--wb-accent2")
SKIN_IDS = ("wbbuddy", "wb-light", "wb-dark", "wb-breeze", "wb-qq",
            "wb-peace", "wb-peer", "wb-ripple", "wb-buddy", "wb-codebee")
DUP_FREEZE = 115          # 冷冻线（死皮肤两文件删除后基线 104 + 余量），破线=必须收敛
THEMES = "wb-themes.css"


def _css_texts():
    return {p.name: p.read_text(encoding="utf-8", errors="replace")
            for p in sorted(UI.glob("*.css"))}


def _inline_sources():
    """JS/HTML 里以 `--x:` 形式注入的自定义属性（内联样式/JS 赋值）。"""
    out = set()
    for p in list(UI.glob("*.js")) + list(UI.glob("*.html")):
        out |= set(re.findall(r"(--[a-zA-Z][\w-]*)\s*:",
                              p.read_text(encoding="utf-8", errors="replace")))
    return out


def _defined_names(texts):
    defs = set()
    for t in texts.values():
        defs |= set(re.findall(r"(--[a-zA-Z][\w-]*)\s*:", t))
    return defs


def test_var_use_always_resolves():
    """每个 var(--x) 必须有源：CSS 定义 / JS-HTML 内联注入 / fallback。"""
    texts = _css_texts()
    defs = _defined_names(texts) | _inline_sources()
    dangling = []
    for name, t in texts.items():
        for m in re.finditer(
                r"var\(\s*(--[a-zA-Z][\w-]*)\s*(?:,[^)]*)?\)", t):
            v = m.group(1)
            has_fallback = "," in m.group(0)
            if v not in defs and not has_fallback:
                dangling.append((name, v))
    assert not dangling, (
        "悬空 var()（无定义无 fallback）：%s"
        % "；".join("%s:%s" % (f, v) for f, v in dangling[:8]))


def test_wb_family_complete_per_skin():
    """61.3c：每个皮肤的分段都必须给全 --wb-* 家族（DESIGN.md §2 真源）。"""
    t = (UI / THEMES).read_text(encoding="utf-8", errors="replace")
    headers = [(m.start(), m.group(0)) for m in re.finditer(
        r"html\[[^\]]*data-skin=[\"']?([\w-]+)[^\]]*\][^{]*\{", t)]
    missing = []
    for sid in SKIN_IDS:
        idxs = [i for i, (_, h) in enumerate(headers)
                if ('data-skin="%s"' % sid) in h]
        blob = "\n".join(
            t[headers[i][0]:(headers[i + 1][0] if i + 1 < len(headers) else len(t))]
            for i in idxs)
        for tok in WB_FAMILY:
            if not re.search(re.escape(tok) + r"\s*:", blob):
                missing.append("%s/%s" % (sid, tok))
    assert not missing, "皮肤 token 缺失（61.3c 破约）：%s" % "；".join(missing[:10])


def test_design_md_documents_wb_family():
    """宪法自检：DESIGN.md §2 必须持续记载 --wb-* 家族（token 真源宣告）。"""
    md = (UI.parent.parent / "DESIGN.md").read_text(encoding="utf-8",
                                                    errors="replace")
    lost = [tok for tok in WB_FAMILY if tok not in md]
    assert not lost, "DESIGN.md 丢了家族 token 记载：%s" % "；".join(lost)


def test_warm_skin_is_the_first_run_default_and_preserves_user_choice():
    """新安装默认蜜巢；有效的显式主题偏好仍优先。"""
    index = (UI / "index.html").read_text(encoding="utf-8", errors="replace")
    app = (UI / "app.js").read_text(encoding="utf-8", errors="replace")
    board_html = (UI / "board.html").read_text(encoding="utf-8", errors="replace")
    board_js = (UI / "board.js").read_text(encoding="utf-8", errors="replace")

    assert 'localStorage.getItem("orch.skin") || "wb-codebee"' in index
    assert 'const s = localStorage.getItem(SKIN_KEY) || "";' in app
    assert 'return SKIN_IDS.has(s) ? s : "wb-codebee";' in app
    assert 'localStorage.getItem("orch.boardSkin") || "wb-codebee"' in board_html
    assert 'localStorage.getItem("orch.boardSkin") || "wb-codebee"' in board_js
    assert 'return SKIN_IDS.has(v) ? v : "wb-codebee";' in board_js


def test_shared_workbench_style_layer_uses_theme_tokens():
    """壳层样式不绑定单一皮肤，背景/描边必须跟随当前主题 token。"""
    css = (UI / "style.css").read_text(encoding="utf-8", errors="replace")
    start = css.index("/* ================= 工作台共享观感层")
    end = css.index("/* ================= 四页观感提升", start)
    shell = css[start:end]

    assert 'html[data-layout="workbench"] #sidebar' in shell
    assert "background: var(--sidebar) !important;" in shell
    assert "border-right: 1px solid var(--border) !important;" in shell
    assert 'data-skin="wbbuddy"' not in shell
    assert "#f7f8fa" not in shell


def _dup_vars():
    """跨文件重复定义的 var 名单（在 ≥2 个 CSS 文件里都有 `--x:` 定义）。"""
    texts = _css_texts()
    where = defaultdict(set)
    for name, t in texts.items():
        for m in re.finditer(r"(--[a-zA-Z][\w-]*)\s*:", t):
            where[m.group(1)].add(name)
    return {k: sorted(v) for k, v in where.items() if len(v) > 1}


def test_cross_file_duplicate_defs_frozen():
    """冷冻线：跨文件重复 var 数不得增长（基线 111 个 var，2026-11-01；
    覆盖层架构允许有意重定义，但**数量**必须冻结——新增 token 走单一真源）。"""
    dups = _dup_vars()
    assert len(dups) <= DUP_FREEZE, (
        "跨文件重复 var 破冷冻线：%d > %d（新增 token 请进单一真源文件，"
        "或收敛既有重复）" % (len(dups), DUP_FREEZE))


@pytest.mark.xfail(reason="P3 收敛目标：跨文件同名定义应归零（死皮肤两文件删除后"
                          "余 104，甄别结论=几乎全为分层皮肤架构的有意重定义："
                          "61.3c wb-themes 映射层 / board 大屏自辖域 / settings "
                          "覆盖链 / hive 基线+desktop 覆盖——继续归零收益低）",
                   strict=False)
def test_cross_file_duplicate_defs_zero():
    """终态：跨文件同名定义为 0。当前未达标（债务标记，自动转绿之日可删）。"""
    dups = _dup_vars()
    assert not dups, "跨文件重复定义 %d 个：%s" % (
        len(dups), "；".join(sorted(dups)[:8]))
