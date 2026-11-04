# -*- coding: utf-8 -*-
"""P0-E 守测：等待人工接管（退避 / 落账节流 / 原因分类 / 前端字段 / 清零）。

背景实测（2026-11-04 台账）：番茄 `connect` 失败 485 次、七猫 26 次，全部
同一句「平台要求安全验证：验证码」，跨 16 天；失败后 2–40 分钟就再试一次，
每次失败都写一条台账（connect 占全部台账 72%）。三条守测纪律：

1. **同因失败不能刷满台账**——首条 + 每 20 次一条，是这里最硬的一条。
2. **内部异常不能说成登录问题**——历史上 9 条 `not enough values to unpack`
   被写成「保存的平台登录态不可用」，会把人骗去重新扫码。
3. **恢复后必须清零**——`_set` 是 update 语义，残留的 attempts 会让下一次
   偶发失败直接从「已尝试 485 次」起跳。
"""
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock


APP = Path(__file__).resolve().parents[1] / "app"
if str(APP) not in sys.path:
    sys.path.insert(0, str(APP))

from core.publish import human_wait  # noqa: E402


class AuthPage:
    """最小 Page 替身：只提供登录探测需要的两个方法。"""

    def __init__(self, url, text=""):
        self.current_url = url
        self.text = text
        self.navigations = []

    def navigate(self, url, timeout=30):
        self.navigations.append(url)
        self.current_url = url

    def url(self):
        return self.current_url

    def call(self, function, timeout=8):
        return self.text


class ClassifyTests(unittest.TestCase):
    def test_captcha_wins_over_generic_login_marks(self):
        # 「平台等待登录或安全验证……验证码」同时含登录与验证码字样，
        # 必须先判验证码：两者的处置完全不同（过码 vs 重新扫码）。
        self.assertEqual(
            human_wait.classify("平台等待登录或安全验证，验证完成后自动重试："
                                "平台要求安全验证：验证码"),
            human_wait.CAUSE_CAPTCHA)

    def test_unpack_exception_is_internal_not_login(self):
        # 这条文案本身就是「登录态不可用 + 程序异常」的混合体，
        # 先认内部异常才不会骗人去重新扫码。
        self.assertEqual(
            human_wait.classify("保存的平台登录态不可用，自动发布未等待人工登录："
                                "not enough values to unpack (expected 2, got 0)"),
            human_wait.CAUSE_INTERNAL)

    def test_plain_login_redirect_is_login(self):
        self.assertEqual(
            human_wait.classify("https://zuozhe.qimao.com/front/register-login/login"),
            human_wait.CAUSE_LOGIN)

    def test_empty_error_defaults_to_login(self):
        # 空文案不猜验证码：验证码会驱动一个「过一次」按钮，猜错代价更大。
        self.assertEqual(human_wait.classify(""), human_wait.CAUSE_LOGIN)

    def test_each_cause_has_advice(self):
        for cause in (human_wait.CAUSE_CAPTCHA, human_wait.CAUSE_LOGIN,
                      human_wait.CAUSE_INTERNAL):
            self.assertTrue(human_wait.advice_for(cause).strip())


class BackoffTests(unittest.TestCase):
    def test_ladder_grows_then_caps(self):
        self.assertEqual(human_wait.backoff_seconds(1), 30.0)
        self.assertEqual(human_wait.backoff_seconds(2), 60.0)
        self.assertEqual(human_wait.backoff_seconds(3), 120.0)
        self.assertEqual(human_wait.backoff_seconds(6), 900.0)
        # 封顶：再多的失败也不会退化成「永不重试」，人过完码仍要能自动恢复
        self.assertEqual(human_wait.backoff_seconds(999), 900.0)

    def test_zero_attempts_means_no_delay(self):
        self.assertEqual(human_wait.backoff_seconds(0), 0.0)
        self.assertEqual(human_wait.backoff_seconds(None), 0.0)

    def test_should_probe_respects_next_at(self):
        state = {"status": "waiting_login", "human_wait_next_at": 1000.0}
        self.assertFalse(human_wait.should_probe(state, now=999.0))
        self.assertTrue(human_wait.should_probe(state, now=1000.0))

    def test_should_probe_allows_other_states(self):
        # 只有「在等人」才受退避管；connected/none 不该被这里拦住
        self.assertTrue(human_wait.should_probe({"status": "connected"},
                                                now=0.0))
        self.assertTrue(human_wait.should_probe({}, now=0.0))


class NoteFailureTests(unittest.TestCase):
    def test_first_failure_records_and_starts_clock(self):
        fields, log_now = human_wait.note_failure(
            {}, error="平台要求安全验证：验证码", now=1_800_000_000.0)
        self.assertTrue(log_now)
        self.assertTrue(fields["needs_human"])
        self.assertEqual(fields["human_wait_attempts"], 1)
        self.assertEqual(fields["human_wait_cause"], human_wait.CAUSE_CAPTCHA)
        self.assertTrue(fields["human_wait_since"].startswith("202"))
        self.assertEqual(fields["human_wait_next_at"], 1_800_000_000.0 + 30.0)
        self.assertEqual(fields["human_wait_logged"], 1)

    def test_499_followups_add_only_24_rows(self):
        """核心验收：500 次同因失败，台账只能是 25 行（首条 + 每 20 次）。"""
        state, rows, now = {}, 0, 1_800_000_000.0
        for _ in range(500):
            fields, log_now = human_wait.note_failure(
                state, error="平台要求安全验证：验证码", now=now)
            state.update(fields)
            rows += int(log_now)
            now += human_wait.backoff_seconds(fields["human_wait_attempts"])
        self.assertEqual(rows, 25)
        self.assertEqual(state["human_wait_attempts"], 500)

    def test_since_is_kept_across_failures(self):
        state = {}
        fields, _ = human_wait.note_failure(state, error="验证码", now=1000.0)
        state.update(fields)
        first = state["human_wait_since"]
        fields, _ = human_wait.note_failure(state, error="验证码", now=2000.0)
        self.assertEqual(fields["human_wait_since"], first)

    def test_summary_and_ledger_text_carry_the_count(self):
        state = {}
        for _ in range(3):
            fields, _ = human_wait.note_failure(state, error="验证码",
                                                now=1_800_000_000.0)
            state.update(fields)
        self.assertIn("已尝试 3 次", human_wait.summary(state))
        self.assertIn("连续 3 次", human_wait.ledger_error_text(state, "验证码"))

    def test_summary_is_empty_when_not_waiting(self):
        self.assertEqual(human_wait.summary({}), "")
        self.assertEqual(human_wait.summary({"human_wait_attempts": 0}), "")


class SuccessResetTests(unittest.TestCase):
    def test_success_fields_clear_every_counter(self):
        state = {}
        fields, _ = human_wait.note_failure(state, error="验证码", now=1.0)
        state.update(fields)
        state.update(human_wait.note_success())
        self.assertEqual(state["human_wait_attempts"], 0)
        self.assertFalse(state["needs_human"])
        self.assertEqual(state["human_wait_since"], "")
        self.assertEqual(human_wait.summary(state), "")


class ConnectIntegrationTests(unittest.TestCase):
    """`_connect_unattended` 真的接上了：落账节流、状态字段、异常分类。"""

    def _run(self, times, page_text="请完成安全验证后继续",
             url="https://fanqienovel.com/main/writer/login", state=None):
        from core.publish import manager
        page = AuthPage(url, page_text)
        records = []
        with tempfile.TemporaryDirectory() as temp, \
                mock.patch.object(manager, "STATUS_FILE",
                                  Path(temp) / "state.json"), \
                mock.patch.object(manager, "_state", state or {
                    "platforms": {"fanqie": {"status": "waiting_login"}}}), \
                mock.patch.object(manager, "_ACTION_LOCKS", {}), \
                mock.patch.object(manager, "_open_page",
                                  return_value=(object(), page)), \
                mock.patch.object(manager, "_unattended_attach_gate",
                                  return_value=(True, "")), \
                mock.patch.object(manager.ledger, "record",
                                  side_effect=lambda *a, **k: records.append(k)):
            results = [manager.connect("fanqie", unattended=True)
                       for _ in range(times)]
            snapshot = dict(manager._st("fanqie"))
        return results, records, snapshot

    def test_repeated_captcha_failures_write_one_ledger_row(self):
        results, records, state = self._run(5)
        self.assertTrue(all(not ok for ok, _ in results))
        self.assertEqual(len(records), 1,
                         "同因连续失败只该记首条，中间交给 state 计数")
        self.assertEqual(state["status"], "waiting_login")
        self.assertTrue(state["needs_human"])
        self.assertEqual(state["human_wait_attempts"], 5)
        self.assertEqual(state["human_wait_cause"], human_wait.CAUSE_CAPTCHA)
        # 首条落账时 attempts 还是 1，所以是「等待人工」而不是「连续 N 次」；
        # N 次的分母在 state 里，前端读它显示「已尝试 5 次」。
        self.assertIn("等待人工（验证码）", records[0]["error"])
        self.assertIn("需人工过一次验证码", records[0]["error"])

    def test_failure_message_states_what_a_human_must_do(self):
        results, _, _ = self._run(1)
        self.assertIn("需人工过一次验证码", results[0][1])

    def test_internal_exception_is_not_reported_as_login_problem(self):
        from core.publish import manager
        with tempfile.TemporaryDirectory() as temp, \
                mock.patch.object(manager, "STATUS_FILE",
                                  Path(temp) / "state.json"), \
                mock.patch.object(manager, "_state", {
                    "platforms": {"fanqie": {"status": "waiting_login"}}}), \
                mock.patch.object(manager, "_ACTION_LOCKS", {}), \
                mock.patch.object(manager, "_open_page",
                                  return_value=(object(), AuthPage("x"))), \
                mock.patch.object(manager, "_unattended_attach_gate",
                                  return_value=(True, "")), \
                mock.patch.object(
                    manager, "_probe_login_pages",
                    side_effect=ValueError(
                        "not enough values to unpack (expected 2, got 0)")), \
                mock.patch.object(manager.ledger, "record"):
            ok, error = manager.connect("fanqie", unattended=True)
            state = dict(manager._st("fanqie"))
        self.assertFalse(ok)
        self.assertIn("内部异常", error)
        self.assertEqual(state["status"], "error",
                         "内部异常不能留在 waiting_login 让看门狗一直重试")
        self.assertEqual(state["human_wait_cause"], human_wait.CAUSE_INTERNAL)

    def test_recovery_clears_wait_counters(self):
        from core.publish import manager
        page = AuthPage("https://fanqienovel.com/main/writer/home", "作者后台")
        seeded = {"platforms": {"fanqie": {
            "status": "waiting_login", "needs_human": True,
            "human_wait_attempts": 485, "human_wait_cause": "captcha",
            "human_wait_since": "2026-10-20 09:57:36",
            "human_wait_next_at": 9_999_999_999.0}}}
        with tempfile.TemporaryDirectory() as temp, \
                mock.patch.object(manager, "STATUS_FILE",
                                  Path(temp) / "state.json"), \
                mock.patch.object(manager, "_state", seeded), \
                mock.patch.object(manager, "_ACTION_LOCKS", {}), \
                mock.patch.object(manager, "_open_page",
                                  return_value=(object(), page)), \
                mock.patch.object(manager, "_unattended_attach_gate",
                                  return_value=(True, "")), \
                mock.patch.object(manager.ledger, "record"):
            ok, _ = manager.connect("fanqie", unattended=True)
            state = dict(manager._st("fanqie"))
        self.assertTrue(ok)
        self.assertEqual(state["status"], "connected")
        self.assertEqual(state["human_wait_attempts"], 0,
                         "残留计数会让下次偶发失败从 485 起跳")
        self.assertFalse(state["needs_human"])


class ViewTests(unittest.TestCase):
    def test_view_exposes_wait_fields_for_the_console(self):
        from core.publish import manager
        with tempfile.TemporaryDirectory() as temp, \
                mock.patch.object(manager, "STATUS_FILE",
                                  Path(temp) / "state.json"), \
                mock.patch.object(manager, "_state", {"platforms": {"fanqie": {
                    "status": "waiting_login", "needs_human": True,
                    "human_wait_attempts": 485,
                    "human_wait_cause": "captcha",
                    "human_wait_since": "2026-10-20 09:57:36",
                    "error": "平台等待登录或安全验证"}}}):
            view = manager.view()
        entry = view["platforms"]["fanqie"]
        self.assertTrue(entry["needs_human"])
        self.assertEqual(entry["wait_attempts"], 485)
        self.assertEqual(entry["wait_cause"], "captcha")
        self.assertIn("已尝试 485 次", entry["wait_summary"])
        self.assertTrue(entry["wait_advice"].strip())

    def test_view_hides_wait_fields_when_idle(self):
        from core.publish import manager
        with tempfile.TemporaryDirectory() as temp, \
                mock.patch.object(manager, "STATUS_FILE",
                                  Path(temp) / "state.json"), \
                mock.patch.object(manager, "_state", {
                    "platforms": {"fanqie": {"status": "connected"}}}):
            view = manager.view()
        entry = view["platforms"]["fanqie"]
        self.assertFalse(entry["needs_human"])
        self.assertEqual(entry["wait_summary"], "")


class WatchdogTests(unittest.TestCase):
    def test_watchdog_skips_slots_still_in_backoff(self):
        """看门狗不再是「每 30 秒必探一次」——退避未到点就不取锁、不开浏览器。"""
        from core.publish import manager
        opened = []
        future = 9_999_999_999.0
        with tempfile.TemporaryDirectory() as temp, \
                mock.patch.object(manager, "STATUS_FILE",
                                  Path(temp) / "state.json"), \
                mock.patch.object(manager, "_state", {"platforms": {"fanqie": {
                    "status": "waiting_login", "needs_human": True,
                    "human_wait_attempts": 40, "human_wait_cause": "captcha",
                    "human_wait_next_at": future}}}), \
                mock.patch.object(manager, "_ACTION_LOCKS", {}), \
                mock.patch.object(manager, "_browsers", {}), \
                mock.patch.object(manager, "_open_page",
                                  side_effect=lambda *a, **k: opened.append(a)), \
                mock.patch.object(manager.ledger, "record"):
            recovered = manager._auth_watch_once()
        self.assertEqual(recovered, 0)
        self.assertEqual(opened, [])

    def test_watchdog_never_retries_internal_errors(self):
        from core.publish import manager
        with tempfile.TemporaryDirectory() as temp, \
                mock.patch.object(manager, "STATUS_FILE",
                                  Path(temp) / "state.json"), \
                mock.patch.object(manager, "_state", {"platforms": {"fanqie": {
                    "status": "error", "needs_human": True,
                    "human_wait_cause": human_wait.CAUSE_INTERNAL,
                    "human_wait_next_at": 0.0,
                    "error": "发布链内部异常，不是登录问题"}}}), \
                mock.patch.object(manager, "_ACTION_LOCKS", {}), \
                mock.patch.object(manager, "_browsers", {}), \
                mock.patch.object(manager, "_open_page") as opened, \
                mock.patch.object(manager.ledger, "record"):
            manager._auth_watch_once()
        opened.assert_not_called()


if __name__ == "__main__":
    unittest.main()
