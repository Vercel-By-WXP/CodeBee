"""安装假「完成」双保险回归（2026-10-22 minimax-code 实案）。

1. run_mgmt_command：命令退出码 0 但复检未检出 → 必须报失败，不许假「完成」；
   无 detect 配置的条目不适用本闸；复检在装照常成功。
2. 前端 vcmp：版本占位符「-」→「-」不得显示「已是最新版本，无需升级」。
"""
import re
import unittest
from pathlib import Path
from unittest import mock

from app.core import manager

ROOT = Path(__file__).resolve().parents[1]


def _ok_res():
    return {"ok": True, "exit_code": 0, "stdout": "", "stderr": "",
            "cancelled": False, "timed_out": False, "stalled": False,
            "deadline_exceeded": False, "abort_marker": None,
            "output_truncated": False, "log_truncated": False,
            "log_dropped_bytes": 0}


def _entry():
    return {"id": "codebee-fake-cli", "name": "Fake CLI",
            "detect": {"cli": "codebee-fake-never-installed"},
            "install": "echo hi"}


class MgmtInstallDetectGateTests(unittest.TestCase):
    def _run(self, entry, detected):
        """跑一次 run_mgmt_command（install/exit 0），detected=复检结论。"""
        with mock.patch.object(manager, "run_install_cmd",
                               return_value=_ok_res()), \
             mock.patch.object(manager, "detect_all"), \
             mock.patch.object(manager, "_probe_version"), \
             mock.patch.object(manager, "refresh_update_async") as m_refresh, \
             mock.patch.object(manager, "detect_entry",
                               return_value={"installed": bool(detected),
                                             "detail": r"C:\x\cli.exe" if detected else ""}):
            res = manager.run_mgmt_command(entry, "install")
        return res, m_refresh

    def test_exit_zero_but_not_detected_is_a_failure(self):
        res, m_refresh = self._run(_entry(), detected=False)
        self.assertFalse(res["ok"], res)
        self.assertIn("退出码 0", res["error"])
        self.assertIn("codebee-fake-cli", res["error"])
        m_refresh.assert_not_called()

    def test_exit_zero_and_detected_still_succeeds(self):
        res, _ = self._run(_entry(), detected=True)
        self.assertTrue(res["ok"], res)

    def test_entry_without_detect_clause_skips_the_gate(self):
        entry = {"id": "codebee-fake-cli", "install": "echo hi"}  # 无 detect
        res, _ = self._run(entry, detected=False)
        self.assertTrue(res["ok"], res)


class VcmpPlaceholderGuardTests(unittest.TestCase):
    def test_already_latest_note_requires_a_digit_in_version(self):
        src = (ROOT / "app" / "ui" / "app.js").read_text(encoding="utf-8")
        m = re.search(r"const vcmp =.*?已是最新版本，无需升级", src, re.S)
        self.assertIsNotNone(m, "vcmp 定义找不到")
        self.assertIn(r"/\d/.test(st.versionBefore", m.group(0))


if __name__ == "__main__":
    unittest.main()
