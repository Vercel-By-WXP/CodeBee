# -*- coding: utf-8 -*-
"""空间管家规则库的数据守测：坏数据不会报错，只会静默失效。

规则库是**数据不是代码**：字段拼错、风险档写错、宏没被代码认识，程序照常启动，
只是「50+ 条规则」里真正生效的没几条，或者更糟——把高危目录放进了可清理清单。
本测不碰实现，只锁住三份真数据本身，以及几条必须成立的不变量。
"""
from __future__ import annotations

import json
import re
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
RULES_DIR = ROOT / "app" / "core" / "cspace" / "rules"
FILES = ("system.json", "dev_browser.json", "apps.json")

# 与 rules.expand_macros 认识的宏对齐。上游若新增宏，这里先红，逼着代码补展开，
# 而不是让规则静默跳过（2026-11-05 实测 system.json 有 14 条 %WINDIR%）。
KNOWN_MACROS = {
    "%WINDIR%", "%SYSTEMROOT%", "%LOCALAPPDATA%", "%APPDATA%",
    "%USERPROFILE%", "%PROGRAMDATA%", "%TEMP%", "%TMP%",
    "%SYSTEMDRIVE%", "%HOME%",
}
RISKS = {"safe", "caution", "danger"}


def _all_rules():
    out = []
    for name in FILES:
        data = json.loads((RULES_DIR / name).read_text(encoding="utf-8"))
        for row in data:
            row = dict(row)
            row["_file"] = name
            out.append(row)
    return out


class RulesDataTest(unittest.TestCase):
    def test_three_files_parse_and_have_real_volume(self):
        self.assertTrue(RULES_DIR.is_dir(), "规则库目录缺失")
        rows = _all_rules()
        self.assertGreaterEqual(len(rows), 100, "规则库条数异常，可能被清空或截断")
        for name in FILES:
            data = json.loads((RULES_DIR / name).read_text(encoding="utf-8"))
            self.assertIsInstance(data, list)
            self.assertGreater(len(data), 10, "%s 条数异常" % name)

    def test_required_fields_and_types(self):
        for r in _all_rules():
            for key in ("id", "name", "category", "paths", "risk"):
                self.assertIn(key, r, "%s 缺 %s" % (r.get("id"), key))
            self.assertIsInstance(r["paths"], list)
            self.assertTrue(r["paths"], "%s 没有路径" % r["id"])
            for p in r["paths"]:
                self.assertIsInstance(p, str)
            self.assertIn(r["risk"], RISKS, "%s 风险档不合法" % r["id"])
            self.assertIsInstance(bool(r.get("cleanable", False)), bool)
            self.assertIsInstance(bool(r.get("migratable", False)), bool)
            if r.get("processes") is not None:
                self.assertIsInstance(r["processes"], list)

    def test_danger_rules_are_never_cleanable(self):
        """最要紧的一条：高危目录绝不许进清理清单。"""
        bad = [r["id"] for r in _all_rules()
               if r["risk"] == "danger" and r.get("cleanable")]
        self.assertEqual([], bad, "高危规则被标成可清理：%s" % bad)

    def test_cleanable_rules_declare_a_clean_target(self):
        bad = [r["id"] for r in _all_rules()
               if r.get("cleanable") and not r.get("cleanTarget")]
        self.assertEqual([], bad, "可清规则没写 cleanTarget：%s" % bad)

    def test_windir_macros_are_present(self):
        """防止有人为了「让守测过」把 %WINDIR% 规则整批删掉。"""
        system = json.loads((RULES_DIR / "system.json").read_text(encoding="utf-8"))
        hits = sum(1 for r in system
                   if any("%WINDIR%" in p for p in r.get("paths", [])))
        self.assertGreaterEqual(hits, 10, "系统规则里的 %WINDIR% 条目异常减少")

    def test_every_macro_is_known(self):
        unknown = set()
        for r in _all_rules():
            for p in r["paths"]:
                unknown.update(set(re.findall(r"%[A-Z]+%", p)) - KNOWN_MACROS)
        self.assertEqual(set(), unknown, "出现代码不认识的路径宏：%s" % sorted(unknown))

    def test_attribution_file_exists(self):
        notice = ROOT / "THIRD_PARTY_NOTICES.md"
        self.assertTrue(notice.is_file(), "缺少第三方来源说明")
        text = notice.read_text(encoding="utf-8")
        self.assertIn("github.com/lz-kwok", text)
        self.assertIn("MIT", text)


if __name__ == "__main__":
    unittest.main()
