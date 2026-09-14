# -*- coding: utf-8 -*-
"""GoalService（2A 目标状态外置 + revision CAS）测试。
设计稿：docs/migration/03-state-externalization.md §2A。
"""
from __future__ import annotations

import threading
from base import BaseTest


class GoalServiceBase(BaseTest):

    def setUp(self):
        super().setUp()
        from app.core import goal_service
        goal_service.init(data_dir=str(self.data_dir))

    def tearDown(self):
        from app.core import goal_service
        with goal_service._LOCK:
            goal_service._GOALS.clear()
        super().tearDown()


class TestGoalCreate(GoalServiceBase):

    def test_create_returns_active(self):
        from app.core import goal_service
        g = goal_service.create("写完连载", "两万字", rounds_max=3)
        self.assertEqual(g["phase"], "active")
        self.assertEqual(g["revision"], 1)
        self.assertEqual(g["rounds_started"], 0)
        self.assertEqual(g["rounds_max"], 3)

    def test_second_create_rejected(self):
        from app.core import goal_service, goal_service as gs
        goal_service.create("第一个")
        try:
            goal_service.create("第二个")
            self.fail("应拒绝第二个 goal")
        except goal_service.GoalExistsError:
            pass

    def test_create_after_complete_allowed(self):
        from app.core import goal_service
        g1 = goal_service.create("第一个")
        goal_service.update(g1["goal_id"], g1["revision"], phase="complete")
        g2 = goal_service.create("第二个")
        self.assertEqual(g2["phase"], "active")

    def test_current_returns_latest_open(self):
        from app.core import goal_service
        self.assertIsNone(goal_service.current())
        g = goal_service.create("当前目标")
        self.assertEqual(goal_service.current()["goal_id"], g["goal_id"])


class TestGoalCAS(GoalServiceBase):

    def test_update_with_correct_revision(self):
        from app.core import goal_service
        g = goal_service.create("t")
        g2 = goal_service.update(g["goal_id"], g["revision"], description="改了")
        self.assertEqual(g2["description"], "改了")
        self.assertEqual(g2["revision"], 2)

    def test_update_with_stale_revision_raises(self):
        from app.core import goal_service
        g = goal_service.create("t")
        goal_service.update(g["goal_id"], g["revision"], description="第一次")
        try:
            goal_service.update(g["goal_id"], g["revision"], description="陈旧")
            self.fail("陈旧 revision 应被拒绝")
        except goal_service.StaleRevisionError:
            pass

    def test_terminal_phase_immutable(self):
        from app.core import goal_service
        g = goal_service.create("t")
        g2 = goal_service.update(g["goal_id"], g["revision"], phase="complete")
        try:
            goal_service.update(g["goal_id"], g2["revision"], phase="active")
            self.fail("终态不可改回")
        except ValueError:
            pass

    def test_invalid_phase_rejected(self):
        from app.core import goal_service
        g = goal_service.create("t")
        try:
            goal_service.update(g["goal_id"], g["revision"], phase="bogus")
            self.fail("非法 phase 应被拒绝")
        except ValueError:
            pass

    def test_unknown_field_rejected(self):
        from app.core import goal_service
        g = goal_service.create("t")
        try:
            goal_service.update(g["goal_id"], g["revision"], hacker="x")
            self.fail("未知字段应被拒绝")
        except ValueError:
            pass

    def test_increment_rounds(self):
        from app.core import goal_service
        g = goal_service.create("t")
        g2 = goal_service.increment_rounds(g["goal_id"], g["revision"])
        self.assertEqual(g2["rounds_started"], 1)
        self.assertEqual(g2["revision"], 2)

    def test_concurrent_cas_one_winner(self):
        """并发 increment：CAS 保证只有一个成功。"""
        from app.core import goal_service
        g = goal_service.create("t")
        results = []

        def worker():
            try:
                goal_service.increment_rounds(g["goal_id"], g["revision"])
                results.append("ok")
            except goal_service.StaleRevisionError:
                results.append("stale")

        threads = [threading.Thread(target=worker) for _ in range(4)]
        for t in threads:
            t.start()
        for t in threads:
            t.join(timeout=5)
        self.assertEqual(results.count("ok"), 1)
        self.assertEqual(results.count("stale"), 3)


class TestGoalPersistence(GoalServiceBase):

    def test_reload_from_disk(self):
        from app.core import goal_service
        g = goal_service.create("持久化", "描述", rounds_max=7)
        goal_service.increment_rounds(g["goal_id"], g["revision"])
        # 模拟重启：重新 init
        goal_service.init(data_dir=str(self.data_dir))
        cur = goal_service.current()
        self.assertEqual(cur["title"], "持久化")
        self.assertEqual(cur["rounds_started"], 1)
        self.assertEqual(cur["rounds_max"], 7)

    def test_list_goals(self):
        from app.core import goal_service
        g1 = goal_service.create("a")
        goal_service.update(g1["goal_id"], g1["revision"], phase="complete")
        goal_service.create("b")
        goals = goal_service.list_goals()
        self.assertEqual(len(goals), 2)