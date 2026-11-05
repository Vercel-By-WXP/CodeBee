# -*- coding: utf-8 -*-
"""阻塞型死错的本场连败熔断（2026-11-05「换将失效」实案）。

实案：一本 1103 步的书当天 92 条 UPSTREAM_SERVER（全是 codex 上游 503），
其中 25 条摊在 26 个不同步骤上——8 个章节的评审席每一步都把同一个 CLI 重新
选上，时间线排出 13 张失败卡。

根因不是「没有熔断机制」，是那次「谁都没数」：UPSTREAM_SERVER 属
BLOCKED_CODES（刻意不算产品失败、不停账号），而唯一可能生效的供应商级熔断
这批失败根本没建档——`errors-202611.jsonl` 里 11-05 的 137 条失败中有 80 条
provider 为空（`health.report_failure` 首行 `if not provider: return`），另有
28 条命中已停用供应商配置被 `_inactive_config` 丢弃。于是同一 CLI 每一步都被
重新选中，没人记得它已经死了 25 次。

守测契约：
· 同一 CLI 同一错误码**连续** 3 次才熔断（前两次不熔，单次抖动不该踢人）；
· 换码重新计数——「连续失败」指同一件事反复失败，混码累计会把抖动叠成死错；
· 该 CLI 真实成功一次即清零，并摘掉「连败熔断」那条（账号死错仍归
  `record_success` 的统一恢复语义，不许一次成功把「欠费」也当没事）；
· 熔断写进 `_BENCHED`，所以 `benched_ids()` 直接吃到——换将/补位各处不用改；
· 不写账号台账：不落盘、不跨 run、不在通道体检页冒充账号级故障；
· TIMEOUT/NETWORK/QUOTA/FORBIDDEN/VENDOR_ERROR 都不归这一层；
· 空 run/agent 不记；`reset(run_id)` 一并清空。
"""
import inspect
import tempfile
import unittest
from pathlib import Path

from app.core import bench
from app.core import pipeline
from app.core.error_codes import ErrorCode

RUN = "run-blocked"

_SUM_UPSTREAM = ErrorCode.UPSTREAM_SERVER.value
_SUM_CONFIG = ErrorCode.UPSTREAM_CONFIG.value


class BlockedFuseTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        bench.init(self.tmp.name, start_probe=False)
        self.addCleanup(bench.init, self.tmp.name, start_probe=False)
        self.addCleanup(self.tmp.cleanup)
        self.addCleanup(bench.reset, RUN)
        self.addCleanup(bench.reset, "run-other")

    def _fail(self, n, code=_SUM_UPSTREAM, agent="codex", run=RUN):
        """连打 n 次同因失败，返回最后一次的返回值。"""
        out = False
        for _ in range(n):
            out = bench.note_blocked_failure(run, agent, code)
        return out

    def test_fuse_only_after_consecutive_threshold(self):
        self.assertFalse(self._fail(1))
        self.assertEqual(bench.benched_ids(RUN), set())
        self.assertFalse(self._fail(1))   # 第 2 次仍不熔
        self.assertEqual(bench.benched_ids(RUN), set())
        self.assertTrue(self._fail(1))    # 第 3 次熔断
        self.assertIn("codex", bench.benched_ids(RUN))
        self.assertTrue(bench.is_benched(RUN, "codex"))

    def test_different_code_restarts_the_count(self):
        # 两败一换码：不是「同一件事反复失败」，重新从 1 数起
        self._fail(2)
        self.assertFalse(bench.note_blocked_failure(RUN, "codex", _SUM_CONFIG))
        self.assertEqual(bench.benched_ids(RUN), set())
        self.assertFalse(bench.note_blocked_failure(RUN, "codex", _SUM_CONFIG))
        self.assertTrue(bench.note_blocked_failure(RUN, "codex", _SUM_CONFIG))

    def test_success_clears_streak_and_fuse(self):
        self._fail(2)
        self.assertTrue(bench.note_blocked_ok(RUN, "codex"))
        # 复位后重新数：单次失败不再熔断
        self.assertFalse(bench.note_blocked_failure(RUN, "codex", _SUM_UPSTREAM))
        self.assertEqual(bench.benched_ids(RUN), set())
        # 熔断之后来了一次真实成功：把连败熔断那条摘掉
        self._fail(1)
        self.assertTrue(self._fail(1))
        self.assertIn("codex", bench.benched_ids(RUN))
        bench.note_blocked_ok(RUN, "codex")
        self.assertNotIn("codex", bench.benched_ids(RUN))

    def test_success_never_unbenches_account_dead_errors(self):
        # 欠费那类是账号级死错，一次成功不等于账号恢复（恢复要走探针/显式 reset）
        self.assertTrue(bench.bench(RUN, "qwen", ErrorCode.QUOTA.value))
        bench.note_blocked_ok(RUN, "qwen")
        self.assertIn("qwen", bench.benched_ids(RUN))

    def test_only_upstream_family_is_counted(self):
        # TIMEOUT 是慢不是死；NETWORK 有自己的 upstream 级熔断；其余归账号级
        for code in (ErrorCode.TIMEOUT, ErrorCode.NETWORK, ErrorCode.QUOTA,
                     ErrorCode.FORBIDDEN, ErrorCode.VENDOR_ERROR,
                     ErrorCode.MODEL_UNAVAILABLE):
            with self.subTest(code=code):
                self._fail(4, code=code.value, agent="cli-%s" % code.value)
                self.assertNotIn("cli-%s" % code.value, bench.benched_ids(RUN))
        self.assertEqual(bench.benched_ids(RUN), set())

    def test_fuse_is_run_local_and_never_touches_account_health(self):
        self.assertTrue(self._fail(3))
        # 不落账号台账：通道体检页不该出现一条「codex 账号故障」
        self.assertFalse(bench.is_globally_benched("codex"))
        self.assertEqual(bench.snapshot()["accounts"], [])
        self.assertFalse((Path(self.tmp.name) / "account_health.json").exists())
        # 别的 run 不受影响
        self.assertNotIn("codex", bench.benched_ids("run-other"))
        bench.reset(RUN)
        self.assertEqual(bench.benched_ids(RUN), set())
        self.assertFalse(bench.note_blocked_failure(RUN, "codex", _SUM_UPSTREAM))

    def test_reason_is_readable_for_step_notes(self):
        self._fail(3)
        why = bench.reason(RUN, "codex")
        self.assertIn("上游服务端异常", why)
        self.assertIn(_SUM_UPSTREAM, why)
        self.assertIn("codex", bench.benched_agents(RUN))

    def test_empty_arguments_are_ignored(self):
        self.assertFalse(bench.note_blocked_failure("", "codex", _SUM_UPSTREAM))
        self.assertFalse(bench.note_blocked_failure(RUN, "", _SUM_UPSTREAM))
        self.assertFalse(bench.note_blocked_failure(None, None, None))
        self.assertFalse(bench.note_blocked_ok(RUN, "codex"))
        self.assertEqual(bench.benched_ids(RUN), set())


class BlockedWarningTests(unittest.TestCase):
    """熔断必须留下人话：换将成功的那本，用户否则什么都看不到。"""

    def _fake_store(self, warnings=None):
        from unittest import mock
        fake = mock.Mock()
        fake.get_run.return_value = {"warnings": list(warnings or [])}
        return fake

    def test_warning_names_the_cli_and_the_reason(self):
        from unittest import mock
        fake = self._fake_store()
        with mock.patch.object(pipeline, "store", fake):
            pipeline._note_blocked_warning(
                RUN, {"label": "Codex CLI", "id": "codex-cli"},
                "codex-cli", _SUM_UPSTREAM)
        kwargs = fake.update_run.call_args[1]
        text = kwargs["warnings"][0]
        self.assertIn("Codex CLI", text, "要说清是哪条通道被绕开")
        self.assertIn(_SUM_UPSTREAM, text, "要带错误码，便于对台账")
        self.assertIn("已绕开", text, "要说清后果：后续步骤不再选它")

    def test_threshold_in_copy_comes_from_the_fuse(self):
        # 文案里的次数必须跟判据同源：改了阈值文案不能还对不上
        from unittest import mock
        fake = self._fake_store()
        with mock.patch.object(pipeline, "store", fake):
            pipeline._note_blocked_warning(RUN, {"label": "X"}, "x", _SUM_UPSTREAM)
        text = fake.update_run.call_args[1]["warnings"][0]
        self.assertIn("连续 %d 次" % bench.blocked_threshold(), text)

    def test_same_warning_is_not_appended_twice(self):
        from unittest import mock
        from unittest.mock import patch
        fake = self._fake_store()
        with patch.object(pipeline, "store", fake):
            pipeline._note_blocked_warning(RUN, {"label": "X"}, "x", _SUM_UPSTREAM)
            first = fake.update_run.call_args[1]["warnings"][0]
        fake2 = self._fake_store([first])
        with patch.object(pipeline, "store", fake2):
            pipeline._note_blocked_warning(RUN, {"label": "X"}, "x", _SUM_UPSTREAM)
        self.assertFalse(fake2.update_run.called, "同一句话每 run 只留一次")

    def test_store_failure_never_breaks_the_step(self):
        from unittest import mock
        fake = mock.Mock()
        fake.get_run.side_effect = RuntimeError("store 挂了")
        with mock.patch.object(pipeline, "store", fake):
            pipeline._note_blocked_warning(RUN, {"label": "X"}, "x", _SUM_UPSTREAM)


class PipelineWiringTests(unittest.TestCase):
    """跨文件前提：这一步的记账必须挂在步收尾上，否则熔断永远数不到。"""

    def test_finish_step_result_books_blocked_failures(self):
        src = inspect.getsource(pipeline._finish_step_result)
        self.assertIn("note_blocked_failure", src)
        self.assertIn("note_blocked_ok", src)

    def test_fuse_uses_the_return_value_to_warn(self):
        # 只要返回值被丢，熔断就只剩内存态：看不见、事后也解释不了
        src = inspect.getsource(pipeline._finish_step_result)
        self.assertIn("if _bench.note_blocked_failure", src,
                      "必须吃返回值，否则不会写 run.warnings")
        self.assertIn("_note_blocked_warning(", src)

    def test_wiring_is_guarded_by_real_agent(self):
        # mock 步骤不记账：mock 的失败不代表任何通道不可用
        src = inspect.getsource(pipeline._finish_step_result)
        for name in ("note_blocked_failure", "note_blocked_ok"):
            idx = src.index(name)
            window = src[max(0, idx - 900):idx]
            self.assertIn('agent.get("mode") == "real"', window,
                          "%s 必须在 real 分支里记账" % name)

    def test_benched_ids_is_what_switches_the_hand(self):
        # 换将各点只吃 benched_ids：熔断写进去就必须能被它读出来
        with tempfile.TemporaryDirectory() as tmp:
            bench.init(tmp, start_probe=False)
            self.addCleanup(bench.init, tmp, start_probe=False)
            for _ in range(3):
                bench.note_blocked_failure(RUN, "codex#recheck", _SUM_UPSTREAM)
            self.assertIn("codex#recheck", bench.benched_ids(RUN))
            bench.reset(RUN)


if __name__ == "__main__":
    unittest.main()
