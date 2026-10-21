import sys
import unittest
from pathlib import Path

# 路由层测试的既有导入法：服务以 app/ 为工作目录启动，main 直接顶格导入
APP_DIR = str(Path(__file__).resolve().parents[1] / "app")
if APP_DIR not in sys.path:
    sys.path.insert(0, APP_DIR)
import main


class StateRunProgressTests(unittest.TestCase):
    """状态侧栏的 0-100 进度由 _state_run_summary 折算（2026-10-21 恒 0% 案：
    侧栏读 r.progress，投影从不发该字段，进度条永久停在 0）。"""

    def test_progress_folds_done_steps_into_percent(self):
        run = {"id": "r1", "task_id": "t1", "status": "running",
               "steps": [{"n": 1, "status": "done"},
                         {"n": 2, "status": "done"},
                         {"n": 3, "status": "running"}]}
        summary = main._state_run_summary(run)
        self.assertEqual(summary["step_count"], 3)
        self.assertEqual(summary["progress"], 67)

    def test_progress_full_when_all_steps_done(self):
        run = {"id": "r2", "status": "done",
               "steps": [{"n": 1, "status": "done"}, {"n": 2, "status": "done"}]}
        self.assertEqual(main._state_run_summary(run)["progress"], 100)

    def test_progress_zero_without_steps(self):
        summary = main._state_run_summary({"id": "r3", "status": "running",
                                           "steps": []})
        self.assertEqual(summary["progress"], 0)
        self.assertEqual(summary["step_count"], 0)


if __name__ == "__main__":
    unittest.main()
