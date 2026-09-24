# -*- coding: utf-8 -*-
"""i18n 字典重复键守卫（发版前巡检清单例行化）单测：
EN 字典键=中文原文，JS 对象字面量后值静默覆盖前值——「执行」曾同时
存在 Run/Implement 两译（2026-09-24 实案）。本守卫强制零重复键：
同值重复也不许（保持字典唯一权威形态，新重复当场红）。

跑法：python -m unittest discover -s tests -p "test_i18n_dups.py" -v
"""
from __future__ import annotations

import io
import os
import re
from collections import Counter

from base import BaseTest

_I18N = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                     "app", "ui", "i18n.js")


def _en_block():
    src = io.open(_I18N, encoding="utf-8").read()
    m = re.search(r"const\s+EN\s*=\s*\{", src)
    if not m:
        return ""
    start, depth, end = m.end(), 1, m.end()
    while end < len(src) and depth:
        if src[end] == "{":
            depth += 1
        elif src[end] == "}":
            depth -= 1
        end += 1
    return src[start:end]


def _keys_of(block):
    # 位置锚定：只在行首/`{`/`,` 之后认键——值字符串里的引号片段不算键
    pat = re.compile(r'(?:^|[,{]\s*)"((?:[^"\\]|\\.)+)"\s*:', re.M)
    return pat.findall(block)


class I18nDupGuardTests(BaseTest):

    def test_no_duplicate_keys(self):
        """EN 字典零重复键（后值静默覆盖=隐形文案事故）。"""
        keys = _keys_of(_en_block())
        self.assertGreater(len(keys), 1000, "字典形态异常：键数骤降说明解析失效")
        dups = {k: v for k, v in Counter(keys).items() if v > 1}
        self.assertEqual(dups, {}, "重复键（后值覆盖前值）: %r" % list(dups)[:8])

    def test_known_keys_present(self):
        """修复后的关键键在位：执行=Run（唯一）、实现=Implement（唯一）。"""
        keys = _keys_of(_en_block())
        cnt = Counter(keys)
        self.assertEqual(cnt.get("执行"), 1)
        self.assertEqual(cnt.get("实现"), 1)


if __name__ == "__main__":
    import unittest
    unittest.main()
