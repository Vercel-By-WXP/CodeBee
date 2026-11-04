# -*- coding: utf-8 -*-
"""无人值守放行的授权边界（P0-A2）单测。

要守的不是「多了个开关」，而是四条**不能反悔**的边界：

1. **没有授权，程序不能替人放行**：闸门拦下后自动带 force 的请求必须被拒，
   并把拒绝原因连同被按掉的判据一起回给界面。这就是那段写死「用户已启用
   全自动发布」的前端重试代码留下的坑——2026-10 台账 25 条 forced 里
   14 条是它代按的，用户从没看见拦截。
2. **有人真按了，就必须放**：显式声明 manual 是人的决定，不能因为任务没开
   自动模式就被拦死，否则「改完稿、知道风险、还是要发」这条路断掉。
3. **授权是任务级的**：另一本开了不等于这本开了，不能串味。
4. **放行仍要留证**：强制放行成功时那条被按掉的判据必须还在
   （`overridden`），否则复盘时只有一句「投了」，看不到当初判了哪几条不过。
"""
from __future__ import annotations

import unittest
from unittest import mock

from core.publish import manager, publish_auth
from core import quality_gate, store


# 一个「评审没过」的最小 verdict：serial 模式下能稳定产出多条 blockers，
# 不依赖磁盘上的真实评审记录。
REJECTED_VERDICT = {
    "publishable": False,
    "scores": {"logic": 3, "appeal": 3},
    "threshold": 4,
    "end_chapter": 3,
}


def _task(**kw):
    base = {"id": "t-auto", "type": "serial_novel", "title": "测", "workdir": ""}
    base.update(kw)
    return base


class _GuardBase(unittest.TestCase):
    def setUp(self):
        runs = [{"id": "r1", "status": "done", "verdict": dict(REJECTED_VERDICT)}]
        # publish_auth 在函数体内 `from .. import store`，patch 打在同一模块对象上
        self._patches = [
            mock.patch.object(store, "task_runs", return_value=runs),
        ]
        for p in self._patches:
            p.start()

    def tearDown(self):
        for p in reversed(self._patches):
            p.stop()

    def guard(self, task=None, **flags):
        base = {"force": False, "force_confirmed": False,
                "force_reason": "", "force_origin": ""}
        base.update(flags)
        return publish_auth._quality_release_guard(
            task if task is not None else _task(), "fanqie",
            action="publish", target_chapter=1, **base)


class UnauthorizedAutoForceTest(_GuardBase):
    def test_plain_publish_is_blocked_without_force(self):
        d = self.guard()
        self.assertFalse(d["allowed"])
        self.assertFalse(d["forced"])
        self.assertTrue(d["blockers"])

    def test_auto_origin_without_task_authorization_is_denied(self):
        d = self.guard(force=True, force_confirmed=True,
                       force_reason="用户已启用全自动发布；系统按平台校准流程自动提交",
                       force_origin="auto")
        self.assertFalse(d["allowed"])
        self.assertFalse(d["forced"])
        self.assertTrue(d.get("auto_force_denied"))
        self.assertIn("未开启全自动发布", d["blockers"][0])

    def test_undeclared_origin_counts_as_unattended(self):
        # 老前端 / 第三方脚本不带 origin：默认按无人值守处理，
        # 否则只要不写 origin 就能把闸门整个绕过去。
        d = self.guard(force=True, force_confirmed=True,
                       force_reason="用户明确要求真实发布前10章")
        self.assertFalse(d["allowed"])
        self.assertTrue(d.get("auto_force_denied"))

    def test_denied_decision_still_carries_overridden_blockers(self):
        # 界面要能告诉用户「哪几条不过」，只回一句「未授权」等于什么都没说
        d = self.guard(force=True, force_confirmed=True,
                       force_reason="自动", force_origin="auto")
        self.assertGreaterEqual(len(d["overridden"]), 2)
        self.assertEqual(d["blockers"][1:], d["overridden"])

    def test_string_flags_do_not_count_as_authorization(self):
        # JSON 里的 "false" 曾是真值陷阱：字符串永远不等于 True
        d = self.guard(task=_task(auto_publish="false"),
                       force=True, force_confirmed=True,
                       force_reason="自动", force_origin="auto")
        self.assertFalse(d["allowed"])
        self.assertTrue(d.get("auto_force_denied"))


class AuthorizedAutoForceTest(_GuardBase):
    def test_authorized_task_allows_auto_force(self):
        d = self.guard(task=_task(auto_publish=True), force=True,
                       force_confirmed=True, force_reason="自动",
                       force_origin="auto")
        self.assertTrue(d["allowed"])
        self.assertTrue(d["forced"])
        self.assertFalse(d.get("auto_force_denied"))

    def test_authorization_is_per_task(self):
        # 这本开了，不能把授权带到另一本上
        self.guard(task=_task(auto_publish=True), force=True,
                   force_confirmed=True, force_reason="自动",
                   force_origin="auto")
        other = self.guard(task=_task(id="t-other"), force=True,
                           force_confirmed=True, force_reason="自动",
                           force_origin="auto")
        self.assertFalse(other["allowed"])

    def test_manual_origin_passes_without_authorization(self):
        d = self.guard(force=True, force_confirmed=True,
                       force_reason="赶周末流量，先发再说",
                       force_origin="manual")
        self.assertTrue(d["allowed"])
        self.assertTrue(d["forced"])

    def test_origin_is_case_and_space_insensitive(self):
        d = self.guard(force=True, force_confirmed=True,
                       force_reason="我确认过", force_origin="  MANUAL ")
        self.assertTrue(d["allowed"])

    def test_force_without_confirmation_is_still_blocked(self):
        # 有授权不等于免掉二次确认与理由——那是闸门自己的硬约束
        d = self.guard(task=_task(auto_publish=True), force=True,
                       force_origin="auto")
        self.assertFalse(d["allowed"])


class ForcedSourceDeclarationTest(unittest.TestCase):
    def test_declared_origin_beats_reason_text(self):
        auto_text = "用户已启用全自动发布；系统按平台校准流程自动提交"
        self.assertEqual(manager._forced_source(True, auto_text, "manual"),
                         "manual")
        self.assertEqual(manager._forced_source(True, "赶更新", "auto"), "auto")

    def test_falls_back_to_text_when_not_declared(self):
        auto_text = "用户已启用全自动发布；系统按平台校准流程自动提交"
        self.assertEqual(manager._forced_source(True, auto_text, ""), "auto")
        self.assertEqual(manager._forced_source(True, "赶更新", ""), "manual")
        self.assertEqual(manager._forced_source(True, "", ""), "unknown")

    def test_unknown_origin_value_is_ignored(self):
        self.assertEqual(manager._forced_source(True, "赶更新", "robot"),
                         "manual")
        self.assertEqual(manager._forced_source(False, "赶更新", "manual"), "")

    def test_kwargs_carry_normalized_origin(self):
        got = manager.automatic_release_kwargs(
            True, force=True, force_confirmed=True,
            force_reason="自动", force_origin=" AUTO ")
        self.assertEqual(got["force_origin"], "auto")
        self.assertTrue(got["force"])

    def test_kwargs_default_origin_is_empty(self):
        got = manager.automatic_release_kwargs(True)
        self.assertEqual(got["force_origin"], "")
        self.assertFalse(got["force"])


class OverriddenEvidenceTest(unittest.TestCase):
    """放行成功时被判据不能随 blockers 一起消失（P0-A 的真实缺陷）。"""

    def _verdict(self):
        return dict(REJECTED_VERDICT, serial=True, start_chapter=1,
                    end_chapter=3)

    def test_forced_decision_keeps_overridden(self):
        d = quality_gate.evaluate_release(
            self._verdict(), action="publish", target_chapter=1,
            force=True, force_confirmed=True, force_reason="先发")
        self.assertTrue(d["allowed"])
        self.assertTrue(d["forced"])
        self.assertEqual(d["blockers"], [])
        self.assertGreaterEqual(len(d["overridden"]), 2)

    def test_plain_block_has_empty_overridden(self):
        d = quality_gate.evaluate_release(
            self._verdict(), action="publish", target_chapter=1)
        self.assertFalse(d["allowed"])
        self.assertEqual(d["overridden"], [])

    def test_draft_shortcut_has_no_overridden(self):
        d = quality_gate.evaluate_release(
            self._verdict(), action="draft", target_chapter=1)
        self.assertTrue(d["allowed"])
        self.assertFalse(d.get("overridden"))


class StoreFlagTest(unittest.TestCase):
    """auto_publish 是任务级开关，默认关，写进去要能读回来。"""

    def _write(self, task, patch):
        from core import store
        with mock.patch.object(store, "_TASKS", {task["id"]: task}), \
             mock.patch.object(store, "_save_json"), \
             mock.patch.object(store, "bump_state"):
            return store.update_task_params(task["id"], patch)

    def test_flag_defaults_off(self):
        task = {"id": "t-auto", "status": "done"}
        self.assertFalse(task.get("auto_publish"))

    def test_flag_can_be_turned_on_and_off(self):
        task = {"id": "t-auto", "status": "done"}
        self.assertTrue(self._write(task, {"auto_publish": True})[0])
        self.assertIs(task["auto_publish"], True)
        self.assertTrue(self._write(task, {"auto_publish": False})[0])
        self.assertIs(task["auto_publish"], False)

    def test_truthy_string_does_not_enable_it(self):
        # 老前端可能把 "false"/"1" 原样发上来：只认 JSON 真布尔
        task = {"id": "t-auto", "status": "done"}
        self._write(task, {"auto_publish": "true"})
        self.assertIsNone(task.get("auto_publish"))
        self.assertFalse(publish_auth._auto_release_authorized(task))

    def test_dirty_value_does_not_turn_an_open_grant_off(self):
        # 已开启时来一个脏值必须忽略，不能顺手关掉授权——否则闸门会
        # 突然开始问人，看起来像「随机抽风」，实际是一次脏写入
        task = {"id": "t-auto", "status": "done", "auto_publish": True}
        self._write(task, {"auto_publish": "false"})
        self.assertIs(task["auto_publish"], True)
        self._write(task, {"auto_publish": 0})
        self.assertIs(task["auto_publish"], True)

    def test_running_task_cannot_be_granted(self):
        task = {"id": "t-auto", "status": "running"}
        ok, err = self._write(task, {"auto_publish": True})
        self.assertFalse(ok)
        self.assertIn("运行中", err)
        self.assertIsNone(task.get("auto_publish"))

    def test_authorization_reads_the_persisted_flag(self):
        self.assertTrue(publish_auth._auto_release_authorized(
            {"auto_publish": True}))
        self.assertFalse(publish_auth._auto_release_authorized(
            {"auto_publish": False}))
        self.assertFalse(publish_auth._auto_release_authorized({}))
        self.assertFalse(publish_auth._auto_release_authorized(None))


if __name__ == "__main__":
    unittest.main()
