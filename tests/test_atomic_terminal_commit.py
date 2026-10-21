# -*- coding: utf-8 -*-
"""原子终态提交测试。

验证 finish_step 的 terminal_run_status 参数能同时原子更新 step 和 run 终态，
避免中间崩溃导致 step done 但 run 仍 running。
"""
import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]

PROBE = r"""
import json, sys
from app.core import store, paths

# 创建任务和运行
store.create_task({"id": "t1", "title": "测试任务", "goal": "测试", "type": "code"})
run = store.create_run("code", "测试运行", task_id="t1")
run_id = run["id"]
store.update_run(run_id, status="running")

# 添加步骤
store.add_step(run_id, "implement", "test", "测试智能体")

# 用 terminal_run_status 完成步骤
store.finish_step(
    run_id, 1, status="done", summary="完成",
    terminal_run_status="done", terminal_error=None
)

# 检查结果
run = store.get_run(run_id)
step = next((s for s in run["steps"] if s["n"] == 1), None)
result = {
    "run_status": run.get("status"),
    "run_has_ended_at": bool(run.get("ended_at")),
    "step_status": step.get("status") if step else None,
    "step_has_ended_at": bool(step.get("ended_at")) if step else False,
}
print("@@RESULT@@" + json.dumps(result, ensure_ascii=False))
"""


def _run_probe():
    """在隔离子进程里执行探针，返回结果 dict。"""
    with tempfile.TemporaryDirectory() as tmp:
        tmp = Path(tmp)
        env = dict(os.environ)
        env["TUTTI_DATA"] = str(tmp)
        proc = subprocess.run(
            [sys.executable, "-c", PROBE],
            cwd=str(REPO), env=env, timeout=30,
            capture_output=True, text=True)
        if proc.returncode != 0:
            raise AssertionError("probe failed: %s" % proc.stderr[-2000:])
        for line in proc.stdout.splitlines():
            if line.startswith("@@RESULT@@"):
                return json.loads(line[len("@@RESULT@@"):])
        raise AssertionError("no result marker in probe output: %s"
                             % proc.stdout[-2000:])


class AtomicTerminalCommitTests(unittest.TestCase):
    def test_finish_step_with_terminal_status_updates_both(self):
        """finish_step 带 terminal_run_status 时同时更新 step 和 run。"""
        result = _run_probe()
        # run 应该是终态
        self.assertEqual(result["run_status"], "done")
        self.assertTrue(result["run_has_ended_at"])
        # step 也应该是终态
        self.assertEqual(result["step_status"], "done")
        self.assertTrue(result["step_has_ended_at"])

    def test_finish_step_without_terminal_status_only_updates_step(self):
        """finish_step 不带 terminal_run_status 时只更新 step。"""
        probe = r"""
import json, sys
from app.core import store, paths

store.create_task({"id": "t1", "title": "测试任务", "goal": "测试", "type": "code"})
run = store.create_run("code", "测试运行", task_id="t1")
run_id = run["id"]
store.update_run(run_id, status="running")
store.add_step(run_id, "implement", "test", "测试智能体")

# 不带 terminal_run_status
store.finish_step(run_id, 1, status="done", summary="完成")

run = store.get_run(run_id)
step = next((s for s in run["steps"] if s["n"] == 1), None)
result = {
    "run_status": run.get("status"),
    "step_status": step.get("status") if step else None,
}
print("@@RESULT@@" + json.dumps(result, ensure_ascii=False))
"""
        with tempfile.TemporaryDirectory() as tmp:
            tmp = Path(tmp)
            env = dict(os.environ)
            env["TUTTI_DATA"] = str(tmp)
            proc = subprocess.run(
                [sys.executable, "-c", probe],
                cwd=str(REPO), env=env, timeout=30,
                capture_output=True, text=True)
            self.assertEqual(proc.returncode, 0, proc.stderr[-2000:])
            result = None
            for line in proc.stdout.splitlines():
                if line.startswith("@@RESULT@@"):
                    result = json.loads(line[len("@@RESULT@@"):])
            self.assertIsNotNone(result)
            # run 应该仍是 running
            self.assertEqual(result["run_status"], "running")
            # step 应该是 done
            self.assertEqual(result["step_status"], "done")


if __name__ == "__main__":
    unittest.main()
