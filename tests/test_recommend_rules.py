# -*- coding: utf-8 -*-
"""任务类型推荐规则完整性（app.js recommendTaskType）：direct 之外所有预置
流程都必须有推荐规则——缺失时用户在「直接执行」档输入该类需求（如「做答辩
PPT」「优化简历」「编标书」「写操作手册」「扫榜看热门题材」）不会弹类型切换
建议，直接走快档、旁路掉对应流程的评审门禁或专用数据链路（2026-10-07 巡检
补 doc/presentation/resume/bid_doc 四类型；2026-10-08 巡检补 rank_scan/
defect_retro 两型；2026-10-08 第二轮巡检对齐 goal_hint 引导词：presentation
「演示场合」/serial_novel「女频/男频/签约平台」/article「头条/知乎」/
research「调研」——同 video_script 补词第 3 例缝隙）。

跑法：python -m unittest discover -s tests -p "test_recommend_rules.py" -v
"""
from __future__ import annotations

import re
from pathlib import Path

from base import BaseTest

_APP = Path(__file__).resolve().parent.parent / "app" / "ui" / "app.js"

# direct 自身（推荐逻辑只在 direct 档触发，无需自荐）之外，其余预置流程都应有
# 推荐规则（code/review/direct 引擎一律算；2026-10-08 轮补齐 rank_scan/
# defect_retro 两型——此前快档三件一并豁免，两型的专用数据链路因此永远旁路）。
_RULELESS = {"direct"}


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
        """每个可推荐的预置流程都有推荐规则（direct 自身除外）。"""
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
            # 2026-10-08 轮补两型：
            ("复盘上个版本的缺陷，输出改进动作", "defect_retro"),
            ("复盘这些bug，给出改进动作", "defect_retro"),   # bug 词不被 code 截胡
            ("扫榜看看热门题材", "rank_scan"),
            ("分析一下起点排行榜", "rank_scan"),
            # 防误荐回归：纯 debug（无复盘/漏测语义）仍归 code
            ("分析这个bug的原因", "code"),
            # 2026-10-08 第二轮：goal_hint 引导词对齐（照提示输入即命中）
            ("演示场合：新品发布会，听众是渠道客户", "presentation"),
            ("都市女频，20万字，目标可签约平台", "serial_novel"),
            ("写篇头条文章聊聊AI编程", "article"),
            ("调研一下主流AI编排工具的优劣", "research"),
            # 防误荐回归：bug 词先于 research 的「调研」；「演示」不带「场合」不荐
            ("帮我调研这个bug的成因", "code"),
            ("演示一下这个函数怎么用", ""),
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
