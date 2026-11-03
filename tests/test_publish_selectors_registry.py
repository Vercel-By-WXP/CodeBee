# -*- coding: utf-8 -*-
"""P0-a 第一刀守测：发布链共用层定位型选择器外置（先红后绿）。

口径（照迭代计划 P0-a 登记，复核 2026-11-01）：
  ast.walk 扫 app/core/publish/*.py 字符串常量 → 含 querySelector(All) →
  正则取参数 → 首字符是引号 = LIT（硬编码调用点）。

二分判据（refined）：逗号选择器是 OR 语义——每个分支都依赖类/属性/伪类
（平台改 CSS/DOM 全灭）= locating（真靶点，必须外置）；有任一纯标签分支
（文本匹配仍能兜住）= traversal（改版不失效）。

本守测锁三件事：
1. 【先红后绿】共用层（flow/flow_helpers/browser）的 locating LIT 必须为 0
   ——字面量只许住在 selectors.py 注册表；
2. 【等价锁】注册表值 == 迁移前字面量逐字节一致（写死在 EXPECTED）；
3. 【棘轮】全发布包 LIT 总数不得回涨（基线 113）。
"""
from __future__ import annotations

import ast
import re
from pathlib import Path

try:
    import pytest  # noqa: F401 — 守测环境在用；纯标准库发版闸门环境无 pytest，
except ImportError:  # 本文件用例为 pytest 函数风格，unittest 收集数为 0，缺库不炸发现
    pytest = None  # type: ignore[assignment]

PUB = Path(__file__).resolve().parents[1] / "app" / "core" / "publish"
SHARED = ("flow.py", "flow_helpers.py", "browser.py")
PLATFORM = ("flow_fanqie.py", "fanqie.py", "flow_qimao.py", "qimao.py")
BASELINE_TOTAL_LIT = 113

QS_RE = re.compile(r"querySelector(?:All)?\s*\((['\"])(.*?)\1", re.S)


def _kind(sel):
    # %s 占位 = 已外置（注册表内插），不计入任何一类
    if "%s" in sel:
        return "externalized"
    def alt(a):
        a = a.strip()
        if not a:
            return "tag"
        return "styled" if re.search(r"[.\[:#]", a) else "tag"
    return "locating" if all(alt(x) == "styled" for x in sel.split(",")) \
        else "traversal"


def _lit_rows():
    rows = []
    for py in sorted(PUB.glob("*.py")):
        tree = ast.parse(py.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if not isinstance(node, ast.Constant) or not isinstance(node.value, str):
                continue
            if "querySelector" not in node.value:
                continue
            for m in QS_RE.finditer(node.value):
                if m.group(2).strip():
                    rows.append((py.name, node.lineno, m.group(2), _kind(m.group(2))))
    return rows


def test_locating_literals_live_only_in_registry():
    """发布包（共用层+平台层）定位型字面量必须为 0（外置进 selectors.py）。
    第一刀只扫共用层；第二/三刀扩到番茄/七猫平台层。迁移前此测报红。"""
    offenders = [(f, ln, sel) for f, ln, sel, k in _lit_rows()
                 if k == "locating" and (f in SHARED or f in PLATFORM)]
    assert not offenders, (
        "发布包仍有 %d 处定位型硬编码（应迁入 publish/selectors.py）：%s"
        % (len(offenders),
           "；".join("%s:%d %s" % (f, ln, sel[:40]) for f, ln, sel in offenders[:8])))


def test_registry_values_match_pre_migration_literals():
    """等价锁：注册表值与迁移前字面量逐字节一致（改动=平台行为变化，必须显式）。"""
    from app.core.publish import selectors
    for key, want in selectors.EXPECTED_PRE_MIGRATION.items():
        got = selectors.DEFAULTS[key]
        assert got == want, "注册表 %s 被改动：%r != 迁移前 %r" % (key, got, want)


def test_total_lit_ratchet():
    """棘轮（P0-a 验收①终态）：全发布包 querySelector 硬编码调用点 ≤30。
    基线 113 → 三刀外置后 16；新选择器一律进 selectors.py，不许回涨字面量。"""
    n = len(_lit_rows())
    assert n <= 30, (
        "发布包 querySelector 硬编码调用点回涨：%d > 终态 30" % n)


def test_traversal_literals_do_not_grow():
    """遍历型（改版不失效）暂留原地，但只许减不许增。"""
    n = len([1 for f, ln, sel, k in _lit_rows() if k == "traversal"])
    assert n <= 45, "遍历型 LIT 回涨：%d > 45" % n
