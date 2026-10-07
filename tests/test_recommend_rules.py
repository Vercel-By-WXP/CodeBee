# -*- coding: utf-8 -*-
"""任务类型推荐规则完整性（app.js recommendTaskType）：所有适合推荐的预置
review 引擎流程都必须有推荐规则——缺失时用户在「直接执行」档输入该类需求
（如「做答辩 PPT」「优化简历」「编标书」「写操作手册」）不会弹类型切换建议，
直接走无评审门禁的快档（2026-10-07 巡检发现 doc/presentation/resume/bid_doc
四类型无规则）。

跑法：python -m unittest discover -s tests -p "test_recommend_rules.py" -v
"""
from __future__ import annotations

import re
from pathlib import Path

from base import BaseTest

_APP = Path(__file__).resolve().parent.parent / "app" / "ui" / "app.js"

# direct 快档三件（推荐逻辑本身只在 direct 档触发，无需自荐）之外，
# 其余预置流程都应有推荐规则（code 引擎与 review 引擎一律算）。
_RULELESS = {"direct", "rank_scan", "defect_retro"}


def _rules():
    """提取 recommendTaskType 的规则表：[(flow_id, 编译后的正则), ...]（按序）。"""
    src = _APP.read_text(encoding="utf-8")
    m = re.search(r"function recommendTaskType.*?const rules = \[(.*?)\n  \];",
                  src, re.S)
    assert m, "app.js 中找不到 recommendTaskType 的 rules 表"
    out = []
    for fid, body, flags in re.findall(
            r'\["([a-z_]+)",\s*/(.*?)/([a-z]*)\]', m.group(1)):
        flag = re.IGNORECASE if "i" in flags else 0
        out.append((fid, re.compile(body, flag)))
    return out


class RecommendRulesTests(BaseTest):

    def test_rules_cover_all_recommended_flows(self):
        """每个可推荐的预置流程都有推荐规则（direct 快档三件除外）。"""
        from app.core.flows import BUILTIN_FLOWS

        ids = {fid for fid, _ in _rules()}
        for f in BUILTIN_FLOWS:
            if f["id"] in _RULELESS:
                continue
            self.assertIn(f["id"], ids,
                          "流程 %s 无推荐规则：该类需求不会触发类型切换建议" % f["id"])

    def test_first_hit_wins(self):
        """规则按序首个命中生效：样例 goal 命中预期类型（含本班补的四类型）。"""
        rules = _rules()

        def hit(goal):
            for fid, pat in rules:
                if pat.search(goal):
                    return fid
            return ""

        cases = [
            ("帮我做一份毕业答辩PPT", "presentation"),
            ("优化我的简历，投后端岗", "resume"),
            ("按招标文件编标书，突出业绩", "bid_doc"),
            ("写一份用户操作手册", "doc"),
            ("写个周报，本周做了三件事", "weekly_report"),
            ("把这篇合同翻译成英文", "translation"),
            ("写个短视频脚本，抖音带货", "video_script"),
            ("修复登录接口的报错", "code"),
            ("长篇网文连载，都市女频", "serial_novel"),
        ]
        for goal, want in cases:
            self.assertEqual(hit(goal), want,
                             "goal=%r 应推荐 %s" % (goal, want))

    def test_novel_before_article(self):
        """小说词先于文章词命中（规则顺序回归：「小说」同时满足两者）。"""
        rules = _rules()

        def hit(goal):
            for fid, pat in rules:
                if pat.search(goal):
                    return fid
            return ""

        self.assertEqual(hit("写一篇悬疑小说"), "novel")


if __name__ == "__main__":
    import unittest
    unittest.main()
