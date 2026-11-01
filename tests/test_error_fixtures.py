# -*- coding: utf-8 -*-
"""P1 实案转回归（第一刀）：错误台账冻结的分类回归夹具。

夹具由 scripts/freeze_error_fixtures.py 从 data/errors 台账（真实实案）
生成：每条 = 真实 detail 原文 + 该实案归属的分类。测试断言
classify_error_text(原文) == 归属类——即分类器对这些**真实报错**的行为
被锁死，任何分类回归（正则被改、信号顺序被打乱）当场红。

验收对照（迭代计划 P1）：
- 夹具数 == 被引用的真失败样本数：逐条带 ledger 溯源（day#idx），内部
  一致性在此校验（detail 唯一、非空、类在枚举内）；
- 每条新夹具先证明它能失败：断言本身就是「能失败」的证明——分类器当前
  对该原文给出既定类，改坏即红。
"""
from __future__ import annotations

import json
import re
from pathlib import Path

import pytest

from core.error_codes import ErrorCode, classify_error_text

FIXTURES = Path(__file__).resolve().parents[1] / "tests" / "fixtures" / "error_cases.json"


def _cases():
    data = json.loads(FIXTURES.read_text(encoding="utf-8"))
    return data, data.get("cases") or []


def test_fixtures_exist_and_nonempty():
    data, cases = _cases()
    assert data.get("ledger_records", 0) > 0, "冻结脚本没读到台账——先跑 scripts/freeze_error_fixtures.py"
    assert len(cases) >= 3, (
        "实案夹具过薄（%d 条）：确认台账在 data/errors/ 且脚本可跑" % len(cases))


def test_second_cut_locks_both_directions():
    """第二刀（2026-11-01）：text-lock 与 none-lock 双向在册。

    只锁一致性夹具（3 类）是单向的：分类器「多认」不会红。text-lock 锁
    「真实文本必须归此类」，none-lock 锁「真实噪声必须保持无信号」——
    正则过度激进误吞结构化行时，none-lock 当场红。
    """
    data, cases = _cases()
    tags = {c.get("tag") for c in cases}
    assert "text-lock" in tags, "第二刀 text-lock 缺席：重跑 scripts/freeze_error_fixtures.py"
    assert "none-lock" in tags, "第二刀 none-lock 缺席：重跑 scripts/freeze_error_fixtures.py"
    assert len(cases) >= 12, (
        "实案夹具过薄（%d 条）：第二刀后应显著厚于第一刀的 3 条" % len(cases))


def test_each_fixture_proves_classification():
    """核心回归：分类器对每条真实实案原文必须给出冻结时的既定行为——
    text-lock/一致性夹具锁「归此类」，none-lock 锁「保持无信号」。"""
    _data, cases = _cases()
    assert cases
    for c in cases:
        got = classify_error_text(c.get("detail") or "")
        if c.get("expect_none"):
            assert got is None, (
                "实案 %s（%s）应无文本信号却分到 %s：正则过度激进误吞结构化行\n原文：%s"
                % (c.get("incident"), c.get("tag"),
                   got.value if got else "?", str(c.get("detail"))[:120]))
            continue
        assert got is not None, (
            "实案 %s（%s）分类掉 None：文本分类器对真实报错失去信号"
            % (c.get("incident"), c.get("tag")))
        assert str(got.value) == c.get("expect_class"), (
            "实案 %s（%s）分类漂移：%s != %s\n原文：%s"
            % (c.get("incident"), c.get("tag"), got.value,
               c.get("expect_class"), str(c.get("detail"))[:120]))


def test_fixture_internal_consistency():
    """溯源配对（验收「计数成对取」）：detail 唯一非空、类在枚举内、
    incident 带台账 day#idx 溯源、fingerprint 可复算。"""
    _data, cases = _cases()
    details = [str(c.get("detail") or "").strip() for c in cases]
    assert all(details), "夹具含空 detail"
    assert len(set(details)) == len(details), "夹具 detail 重复（去重失效）"
    valid = {e.value for e in ErrorCode}
    for c in cases:
        if not c.get("expect_none"):
            assert c.get("expect_class") in valid, (
                "夹具 %s 的 expect_class 不在枚举： %r" % (c.get("incident"),
                                                         c.get("expect_class")))
        incident = str(c.get("incident") or "")
        assert re.match(r"^\d{6}#\d+$", incident), (
            "夹具缺台账溯源（day#idx）：%r" % incident)
