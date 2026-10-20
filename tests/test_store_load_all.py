# -*- coding: utf-8 -*-
"""store.load_all 启动加载守护测试。

load_all 是冷启动路径（2026-10-20 起读盘阶段走线程池并发），这里的职责是
钉住行为语义与串行版逐条一致：中断收尸、退避保留、脏状态回填、坏文件跳过。
隔离方式：临时目录 + TUTTI_DATA，在子进程里加载（store 的 _TASKS/_RUNS 是
模块级缓存，进程内换数据目录会互相污染）。
"""
import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]

# 子进程里跑 load_all 并吐结果摘要：任务/运行的最终状态 + 落盘后的任务文件。
PROBE = r"""
import json, sys
from app.core import store, paths
store.load_all()
tasks = {t: {"status": v.get("status"), "error": v.get("error")}
         for t, v in store._TASKS.items()}
runs = {r: {"status": v.get("status"),
            "has_ended_at": bool(v.get("ended_at")),
            "error": v.get("error")}
        for r, v in store._RUNS.items()}
on_disk = {}
for p in paths.TASKS_DIR.glob("*.json"):
    try:
        d = json.loads(p.read_text(encoding="utf-8"))
        on_disk[d.get("id")] = d.get("status")
    except Exception:
        on_disk[p.stem] = "<unreadable>"
print("@@RESULT@@" + json.dumps(
    {"tasks": tasks, "runs": runs, "on_disk": on_disk}, ensure_ascii=False))
"""


def _run_load_all(task_fixtures, run_fixtures):
    """在隔离子进程里种盘并执行 load_all，返回摘要 dict。"""
    with tempfile.TemporaryDirectory() as tmp:
        tmp = Path(tmp)
        tasks_dir = tmp / "tasks"
        runs_dir = tmp / "runs"
        tasks_dir.mkdir()
        runs_dir.mkdir()
        for name, payload in task_fixtures.items():
            (tasks_dir / name).write_text(
                json.dumps(payload, ensure_ascii=False), encoding="utf-8")
        for run_id, payload in run_fixtures.items():
            d = runs_dir / run_id
            d.mkdir()
            (d / "run.json").write_text(
                json.dumps(payload, ensure_ascii=False), encoding="utf-8")
        env = dict(os.environ)
        env["TUTTI_DATA"] = str(tmp)
        proc = subprocess.run(
            [sys.executable, "-c", PROBE],
            cwd=str(REPO), env=env, timeout=120,
            capture_output=True, text=True)
        if proc.returncode != 0:
            raise AssertionError("probe failed: %s" % proc.stderr[-2000:])
        for line in proc.stdout.splitlines():
            if line.startswith("@@RESULT@@"):
                return json.loads(line[len("@@RESULT@@"):])
        raise AssertionError("no result marker in probe output: %s"
                             % proc.stdout[-2000:])


class StoreLoadAllTests(unittest.TestCase):
    def test_interrupted_and_deferred_runs(self):
        now = "2026-10-20 08:00:00"
        result = _run_load_all(
            task_fixtures={
                "t1.json": {"id": "t1", "status": "running"},
                "t2.json": {"id": "t2", "status": "running"},
                "t3.json": {"id": "t3", "status": "queued"},
            },
            run_fixtures={
                "r-1": {"id": "r-1", "task_id": "t1", "status": "running"},
                "r-2": {"id": "r-2", "task_id": "t2", "status": "queued",
                        "resume_enqueue_at": now},
                "r-3": {"id": "r-3", "task_id": "t3", "status": "queued"},
            })
        # running 无人在跑 → 收尸为 failed 且补 ended_at
        self.assertEqual(result["runs"]["r-1"]["status"], "failed")
        self.assertTrue(result["runs"]["r-1"]["has_ended_at"])
        self.assertIn("服务重启中断", result["runs"]["r-1"]["error"])
        # 带 resume_enqueue_at 的 queued 是有意的退避，保留给 Timer 重建
        self.assertEqual(result["runs"]["r-2"]["status"], "queued")
        # 无退避的 queued 是残骸 → 收尸
        self.assertEqual(result["runs"]["r-3"]["status"], "failed")

    def test_stale_task_status_reconciled(self):
        result = _run_load_all(
            task_fixtures={
                # 运行已 done 而任务仍停在 running：回填任务终态
                "t1.json": {"id": "t1", "status": "running"},
                # 卡在排队却从未有运行：允许重试
                "t2.json": {"id": "t2", "status": "queued"},
            },
            run_fixtures={
                "r-1": {"id": "r-1", "task_id": "t1", "status": "done"},
            })
        self.assertEqual(result["tasks"]["t1"]["status"], "done")
        self.assertEqual(result["on_disk"]["t1"], "done")
        self.assertEqual(result["tasks"]["t2"]["status"], "failed")
        self.assertIn("没有运行记录", result["tasks"]["t2"]["error"])
        self.assertEqual(result["on_disk"]["t2"], "failed")

    def test_corrupt_files_skipped_others_loaded(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp = Path(tmp)
            tasks_dir = tmp / "tasks"
            runs_dir = tmp / "runs"
            tasks_dir.mkdir()
            runs_dir.mkdir()
            (tasks_dir / "good.json").write_text(
                json.dumps({"id": "good", "status": "done"}), encoding="utf-8")
            (tasks_dir / "bad.json").write_text("{broken", encoding="utf-8")
            good = runs_dir / "r-good"
            good.mkdir()
            (good / "run.json").write_text(
                json.dumps({"id": "r-good", "task_id": "good",
                            "status": "done"}), encoding="utf-8")
            bad = runs_dir / "r-bad"
            bad.mkdir()
            (bad / "run.json").write_text("not json at all", encoding="utf-8")
            no_id = runs_dir / "r-noid"
            no_id.mkdir()
            (no_id / "run.json").write_text(
                json.dumps({"status": "done"}), encoding="utf-8")
            env = dict(os.environ)
            env["TUTTI_DATA"] = str(tmp)
            proc = subprocess.run(
                [sys.executable, "-c", PROBE],
                cwd=str(REPO), env=env, timeout=120,
                capture_output=True, text=True)
            self.assertEqual(proc.returncode, 0, proc.stderr[-2000:])
            result = None
            for line in proc.stdout.splitlines():
                if line.startswith("@@RESULT@@"):
                    result = json.loads(line[len("@@RESULT@@"):])
            self.assertIsNotNone(result)
            self.assertIn("good", result["tasks"])
            self.assertNotIn("bad", result["tasks"])
            self.assertIn("r-good", result["runs"])
            self.assertNotIn("r-bad", result["runs"])
            self.assertNotIn("r-noid", result["runs"])


if __name__ == "__main__":
    unittest.main()
