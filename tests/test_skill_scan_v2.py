# -*- coding: utf-8 -*-
"""skill_scan 新增模式与三档风险（SkillSpector 补齐）单测。

跑法：python -m unittest discover -s tests -p "test_skill_scan_v2.py" -v
"""
from __future__ import annotations

from base import BaseTest


class NewPatternsTests(BaseTest):
    def _scan(self):
        from app.core import skill_scan
        return skill_scan

    def test_base64_decode_flagged(self):
        sk = self._scan()
        fs = sk.scan_text("import base64\ndata = base64.b64decode(payload)")
        cats = {f["category"] for f in fs}
        self.assertIn("数据外发", cats)

    def test_child_process_flagged(self):
        sk = self._scan()
        fs = sk.scan_text("const cp = require('child_process')\ncp.exec(cmd)")
        cats = {f["category"] for f in fs}
        self.assertIn("代码执行", cats)

    def test_ecosystem_config_paths_flagged(self):
        """AI 助手配置目录触碰（.claude/.zcode/.kimi-code/.codebee）。"""
        sk = self._scan()
        for path in ("cat ~/.claude/settings.json", "read ~/.zcode/config.json",
                     "ls ~/.kimi-code/", "grep ~/.codebee/"):
            fs = sk.scan_text(path)
            self.assertTrue(any(f["category"] == "敏感文件" for f in fs), path)

    def test_normal_content_clean(self):
        """正常内容不误报（新增模式不引入噪声）。"""
        sk = self._scan()
        fs = sk.scan_text("这是一个写作辅助 skill。\n请按用户要求写一篇文章。")
        self.assertEqual(fs, [])


class RiskTiersTests(BaseTest):
    def test_medium_risk_two_medium_cats(self):
        """两个中危类同时命中 = 中风险（如 网络请求+环境读取）。"""
        from app.core import skill_scan
        findings = [
            {"category": "网络请求", "detail": "", "line": 1},
            {"category": "环境读取", "detail": "", "line": 2},
        ]
        self.assertEqual(skill_scan.risk_label(findings), "⚠ 中风险")

    def test_single_medium_is_attention(self):
        from app.core import skill_scan
        findings = [{"category": "网络请求", "detail": "", "line": 1}]
        self.assertEqual(skill_scan.risk_label(findings), "△ 注意")

    def test_high_still_wins_over_medium(self):
        from app.core import skill_scan
        findings = [
            {"category": "代码执行", "detail": "", "line": 1},
            {"category": "网络请求", "detail": "", "line": 2},
            {"category": "环境读取", "detail": "", "line": 3},
        ]
        self.assertEqual(skill_scan.risk_label(findings), "⚠ 高风险")

    def test_empty_is_clean(self):
        from app.core import skill_scan
        self.assertEqual(skill_scan.risk_label([]), "")

    def test_base64_counts_as_high(self):
        """base64 解码归入数据外发（高危类）。"""
        from app.core import skill_scan
        findings = [{"category": "数据外发", "detail": "", "line": 1}]
        self.assertEqual(skill_scan.risk_label(findings), "⚠ 高风险")


if __name__ == "__main__":
    import unittest
    unittest.main()
