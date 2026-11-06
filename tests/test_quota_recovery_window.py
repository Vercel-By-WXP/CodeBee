# -*- coding: utf-8 -*-
"""恢复窗口硬判据守测：11-09 之前手滑 --resume-tasks 必须拒写。

20:3x 实案：`QUOTA_RECOVERY_AT` 原先只被打印进文案，写路径没有任何时间
判据。`--resume-tasks` 是唯一会写生产的入口，只要人手带上
`--confirm-quota-restored` 就能把 4 个调度开回去，白烧见底的池子。

真样本：`tests/fixtures/quota_recovery_error_texts.json` 里 3 条生产
`[1310][已达到 7 天使用上限，2026-11-09 14:09:00 后可继续使用]` 原文，
脚本解析结果与 `core.error_codes.quota_recovery_at()` 逐字交叉验证
（不是判据自己算自己）。合成边界样本单列标注、不计真样本数。

决策 18：打印真被判过的真实样本数。
"""
from __future__ import annotations

import importlib.util
import io
import json
import unittest
from contextlib import redirect_stdout
from datetime import datetime
from pathlib import Path
from unittest import mock

from core.error_codes import quota_recovery_at as core_recovery_at

ROOT = Path(__file__).resolve().parents[1]
ERROR_FIXTURE = ROOT / "tests" / "fixtures" / "quota_recovery_error_texts.json"
AUTOMATION_FIXTURE = ROOT / "tests" / "fixtures" / "quota_recovery_automation.json"
SCRIPT = ROOT / "scripts" / "quota_recovery.py"
_spec = importlib.util.spec_from_file_location("quota_recovery_window", SCRIPT)
qr = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(qr)

AT = datetime(2026, 11, 9, 14, 9)
BEFORE = datetime(2026, 11, 6, 20, 35)
AFTER = datetime(2026, 11, 9, 15, 0)


def _real_texts():
    f = json.loads(ERROR_FIXTURE.read_text(encoding="utf-8"))
    return [c["text"] for c in f["cases"]
            if isinstance(c, dict) and "后可继续使用" in str(c.get("text") or "")]


def _fake_get(tasks):
    def get(_base, path):
        if path == "/api/automation":
            return {"tasks": tasks}
        return {"pause_control": {"active": 4, "paused": 4}, "runs": []}
    return get


class ParseRecoveryAtTest(unittest.TestCase):
    def test_real_upstream_texts_match_core_parser(self):
        texts = _real_texts()
        self.assertEqual(len(texts), 3, "夹具里应有 3 条真实 [1310] 原话")
        judged = 0
        for text in texts:
            at = qr.parse_recovery_at(text)
            self.assertEqual(at, AT, "真实原话没解析出 14:09：%s" % text[:80])
            # 与生产解析层交叉验证，不是判据自己算自己
            self.assertEqual(core_recovery_at(text), "2026-11-09 14:09")
            self.assertEqual(at.strftime("%Y-%m-%d %H:%M"),
                             core_recovery_at(text))
            judged += 1
        print("\n真被判过的生产 [1310] 原文样本数：%d/%d" % (judged, len(texts)))

    def test_plain_constant_and_seconds_are_accepted(self):
        for text in ("2026-11-09 14:09", "2026-11-09 14:09:00",
                     "  2026-11-09 14:09  "):
            self.assertEqual(qr.parse_recovery_at(text), AT)

    def test_noise_and_invalid_text_is_unknown(self):
        # 合成边界样本，单列标注、不计真样本数
        for text in ("", "CLI 输出超过捕获上限", "429 Too Many Requests",
                     "2026-13-45 99:99", "request id 429202611091409"):
            self.assertIsNone(qr.parse_recovery_at(text), text)
        print("合成边界样本 5 条（单列标注、不计真样本数）：解析不了按 unknown")


class WindowJudgeTest(unittest.TestCase):
    def test_boundaries(self):
        one_second_before = datetime(2026, 11, 9, 14, 8, 59)
        self.assertFalse(qr.judge_quota_window(now=one_second_before)["reached"])
        self.assertTrue(qr.judge_quota_window(now=AT)["reached"],
                        "恰好等于恢复时刻必须放行")
        self.assertTrue(qr.judge_quota_window(now=AFTER)["reached"])
        self.assertIsNone(qr.judge_quota_window("不是时刻", now=AFTER)["reached"],
                          "解析不了要 fail-closed")

    def test_human_text_is_consumable(self):
        early = qr.judge_quota_window(now=BEFORE)
        self.assertIn("距额度恢复窗口", early["human"])
        self.assertIn("别恢复调度", early["human"])
        self.assertIn("2 天", early["human"])
        late = qr.judge_quota_window(now=AFTER)
        self.assertIn("已过", late["human"])
        self.assertIn("必要非充分条件", late["human"])
        unknown = qr.judge_quota_window("bad", now=AFTER)
        self.assertIn("解析不了", unknown["human"])
        self.assertIn("不写生产调度", unknown["human"])

    def test_upstream_plain_text_can_be_used_as_override(self):
        # 真实原文直接喂 --recovery-at 也要能判（上游窗口变更的消费点）
        text = _real_texts()[0]
        w = qr.judge_quota_window(text, now=BEFORE)
        self.assertEqual(w["at"], AT)
        self.assertFalse(w["reached"])


class ResumeWindowGuardTest(unittest.TestCase):
    """唯一会写生产的入口：窗口未到必须拒，且零 IO。"""

    def test_before_window_refuses_with_zero_io(self):
        buf = io.StringIO()
        with mock.patch.object(qr, "_now", return_value=BEFORE), \
             mock.patch.object(qr, "api_get") as get, \
             mock.patch.object(qr, "api_post") as post, \
             redirect_stdout(buf):
            rc = qr.main(["--resume-tasks", "--confirm-quota-restored"])
        self.assertEqual(rc, 2, buf.getvalue())
        self.assertIn("拒绝写", buf.getvalue())
        self.assertIn("距额度恢复窗口", buf.getvalue())
        get.assert_not_called()
        post.assert_not_called()

    def test_unknown_window_also_refuses_with_zero_io(self):
        buf = io.StringIO()
        with mock.patch.object(qr, "api_get") as get, \
             mock.patch.object(qr, "api_post") as post, \
             redirect_stdout(buf):
            rc = qr.main(["--resume-tasks", "--confirm-quota-restored",
                          "--recovery-at", "不是时刻"])
        self.assertEqual(rc, 2, buf.getvalue())
        self.assertIn("解析不了", buf.getvalue())
        get.assert_not_called()
        post.assert_not_called()

    def test_dry_run_before_window_can_preview_with_zero_post(self):
        tasks = json.loads(AUTOMATION_FIXTURE.read_text(encoding="utf-8"))["tasks"]
        buf = io.StringIO()
        with mock.patch.object(qr, "_now", return_value=BEFORE), \
             mock.patch.object(qr, "api_get", side_effect=_fake_get(tasks)), \
             mock.patch.object(qr, "api_post") as post, \
             redirect_stdout(buf):
            rc = qr.main(["--resume-tasks", "--dry-run",
                          "--confirm-quota-restored"])
        self.assertEqual(rc, 0, buf.getvalue())
        self.assertIn("提示（dry-run", buf.getvalue())
        self.assertIn("dry-run 结束，生产未改动", buf.getvalue())
        post.assert_not_called()

    def test_after_window_still_requires_human_confirmation(self):
        buf = io.StringIO()
        with mock.patch.object(qr, "_now", return_value=AFTER), \
             mock.patch.object(qr, "api_get") as get, \
             mock.patch.object(qr, "api_post") as post, \
             redirect_stdout(buf):
            rc = qr.main(["--resume-tasks"])
        self.assertEqual(rc, 2)
        self.assertIn("必须显式带 --confirm-quota-restored", buf.getvalue())
        get.assert_not_called()
        post.assert_not_called()

    def test_check_prints_window_judgement(self):
        tasks = json.loads(AUTOMATION_FIXTURE.read_text(encoding="utf-8"))["tasks"]
        buf = io.StringIO()
        with mock.patch.object(qr, "_now", return_value=BEFORE), \
             mock.patch.object(qr, "api_get", side_effect=_fake_get(tasks)), \
             mock.patch.object(qr, "api_post") as post, \
             redirect_stdout(buf):
            rc = qr.main(["--check"])
        self.assertEqual(rc, 0, buf.getvalue())
        self.assertIn("恢复窗口判据：", buf.getvalue())
        self.assertIn("别恢复调度", buf.getvalue())
        post.assert_not_called()

    def test_recovery_at_is_routed_to_the_write_guard(self):
        with mock.patch.object(qr, "cmd_resume_tasks", return_value=0) as cmd:
            rc = qr.main(["--resume-tasks", "--confirm-quota-restored",
                          "--recovery-at", "2026-11-11 08:00"])
        self.assertEqual(rc, 0)
        self.assertEqual(cmd.call_args[0][3], "2026-11-11 08:00")


class WindowSourceAnchorTest(unittest.TestCase):
    def test_guard_and_route_are_not_rolled_back(self):
        src = SCRIPT.read_text(encoding="utf-8")
        self.assertIn('if not dry_run and window["reached"] is not True:', src)
        self.assertIn("if delta > 0:", src)
        self.assertIn('"--recovery-at"', src)
        self.assertIn("args.recovery_at)", src)


if __name__ == "__main__":
    unittest.main()
