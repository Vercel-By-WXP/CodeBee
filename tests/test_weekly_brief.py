# -*- coding: utf-8 -*-
"""zentao.weekly_brief（工作汇报禅道素材注入）单元测试：mock _call/list_bugs，
不起假服务器。覆盖：正常聚合/异常静默空串/未配置空串/条目封顶。"""
from __future__ import annotations

from unittest import mock

from base import BaseTest


class TestWeeklyBrief(BaseTest):
    def runTest(self):
        from app.core import zentao

        profile = {"product": 1, "assigned_to": "coder"}
        fake_tasks = {"tasks": [
            {"id": 7, "name": "报表导出", "status": "doing"},
            {"id": 8, "name": "登录改造", "status": "wait"},
            {"id": 9, "name": "已完结不该出现", "status": "done"},
        ]}
        fake_bugs = [{"id": 101, "title": "白屏", "severity": "1"},
                     {"id": 102, "title": "超时", "severity": "2"}]

        with mock.patch.object(zentao, "_profiles", return_value=[profile]), \
             mock.patch.object(zentao, "_call", return_value=fake_tasks), \
             mock.patch.object(zentao, "list_bugs", return_value=fake_bugs):
            brief = zentao.weekly_brief()
        self.assertIn("[任务#7] 报表导出（doing）", brief)
        self.assertIn("[任务#8] 登录改造（wait）", brief)
        self.assertNotIn("已完结不该出现", brief)      # done 状态过滤
        self.assertIn("[Bug#101] 白屏", brief)
        self.assertIn("[Bug#102] 超时", brief)

        # 接口炸了 → 静默空串，绝不抛
        with mock.patch.object(zentao, "_profiles", return_value=[profile]), \
             mock.patch.object(zentao, "_call", side_effect=RuntimeError("boom")), \
             mock.patch.object(zentao, "list_bugs", side_effect=RuntimeError("boom")):
            self.assertEqual(zentao.weekly_brief(), "")

        # 未配置档案 → 空串
        with mock.patch.object(zentao, "_profiles", return_value=[]):
            self.assertEqual(zentao.weekly_brief(), "")

        # 条目封顶：双源各取 limit，总量不超 limit*2
        many_tasks = {"tasks": [{"id": i, "name": "t%d" % i, "status": "doing"}
                                for i in range(30)]}
        many_bugs = [{"id": i, "title": "b%d" % i, "severity": "3"} for i in range(30)]
        with mock.patch.object(zentao, "_profiles", return_value=[profile]), \
             mock.patch.object(zentao, "_call", return_value=many_tasks), \
             mock.patch.object(zentao, "list_bugs", return_value=many_bugs):
            brief = zentao.weekly_brief(limit=5)
        lines = brief.splitlines()
        self.assertLessEqual(len(lines), 10)
        self.assertEqual(len(lines), 10)
