# -*- coding: utf-8 -*-
"""runner / store / mocks 单元测试（全部用 mock，不碰真实 CLI）。"""
from __future__ import annotations

import json

from base import BaseTest


class TestExtractJson(BaseTest):
    def runTest(self):
        from app.core import runner
        self.assertEqual(runner.extract_json('{"a":1}'), {"a": 1})
        self.assertEqual(runner.extract_json('前言 ```json\n{"a": 2}\n``` 后记'),
                         {"a": 2})
        self.assertEqual(runner.extract_json('评语 {"scores": {"x": 1.5}} 结尾'),
                         {"scores": {"x": 1.5}})
        self.assertIsNone(runner.extract_json("没有任何 JSON"))
        self.assertIsNone(runner.extract_json('{"popped'))  # 坏 JSON 不炸


class TestParsers(BaseTest):
    def runTest(self):
        from app.core import runner
        codex_out = "\n".join([
            '{"type":"thread.started","thread_id":"t1"}',
            '{"type":"turn.started"}',
            '{"type":"item.completed","item":{"id":"i0","type":"agent_message","text":"你好"}}',
            '{"type":"turn.completed","usage":{"input_tokens":100,"output_tokens":5}}',
        ])
        text, tokens = runner._parse_codex_jsonl(codex_out)
        self.assertEqual(text, "你好")
        self.assertEqual(tokens, 105)

        claude_out = json.dumps({
            "type": "result", "is_error": False, "result": "OK",
            "total_cost_usd": 0.01,
            "usage": {"input_tokens": 10, "output_tokens": 2},
        })
        p = runner._parse_claude_json(claude_out)
        self.assertEqual(p["text"], "OK")
        self.assertEqual(p["tokens"], 12)
        self.assertIsNone(runner._parse_claude_json("not json"))


class TestResolveCommand(BaseTest):
    def runTest(self):
        from app.core import runner
        parts = runner.resolve_command("cmd")
        self.assertEqual(len(parts), 1)  # 直接可执行文件原样返回
        self.assertEqual(runner.resolve_command(r"C:\x\tool.cmd")[0:2], ["cmd", "/c"])
        self.assertEqual(runner.resolve_command(r"C:\x\tool.exe"), [r"C:\x\tool.exe"])


class TestStoreValidation(BaseTest):
    def runTest(self):
        from app.core import store
        wd = str(self.workdir)
        with self.assertRaises(ValueError):
            store.create_task({"type": "bad", "title": "t", "goal": "g", "workdir": wd})
        with self.assertRaises(ValueError):
            store.create_task({"type": "code", "title": "", "goal": "", "workdir": wd})
        # 标题可省略：自动取目标首行
        t0 = store.create_task({"type": "code", "title": "", "goal": "标题自动派生\n第二行",
                                "workdir": wd})
        self.assertEqual(t0["title"], "标题自动派生")
        with self.assertRaises(ValueError):
            store.create_task({"type": "code", "title": "t", "goal": "", "workdir": wd})
        with self.assertRaises(ValueError):
            store.create_task({"type": "code", "title": "t", "goal": "g", "workdir": "relative/path"})
        with self.assertRaises(ValueError):
            store.create_task({"type": "code", "title": "t", "goal": "g",
                               "workdir": str(self.tmp / "nope")})
        # 稿件名消毒：带路径分隔符会被拍平
        t = store.create_task({"type": "novel", "title": "t", "goal": "g", "workdir": wd,
                               "manuscript": r"..\..\evil.md", "rounds": 2, "threshold": 7.0})
        self.assertNotIn("..", t["manuscript"])
        self.assertNotIn("/", t["manuscript"])
        self.assertNotIn("\\", t["manuscript"])


class TestStepLogTraversal(BaseTest):
    def runTest(self):
        from app.core import store
        run = store.create_run("mgmt", "t")
        secret = self.tmp / "secret.txt"
        secret.write_text("TOPSECRET", encoding="utf-8")
        # 目录穿越尝试必须拿不到内容
        self.assertEqual(store.read_step_log(run["id"], "../secret.txt"), "")


class TestDeleteRun(BaseTest):
    def runTest(self):
        from app.core import paths, store
        run = store.create_run("mgmt", "t")
        (paths.RUNS_DIR / run["id"] / "steps").mkdir(parents=True, exist_ok=True)
        (paths.RUNS_DIR / run["id"] / "steps" / "01-x.log").write_text("log", encoding="utf-8")
        store.update_run(run["id"], status="done")  # 初始为 queued，先置为已结束
        # 正常删除：内存与磁盘同时清掉
        ok, err = store.delete_run(run["id"])
        self.assertTrue(ok, err)
        self.assertIsNone(store.get_run(run["id"]))
        self.assertFalse((paths.RUNS_DIR / run["id"]).exists())
        # 重复删除 / 不存在
        ok, err = store.delete_run(run["id"])
        self.assertFalse(ok)
        self.assertIn("不存在", err)
        # 非法 ID（防目录穿越）
        for bad in ("../evil", "a/b", "a\\b", "..", ""):
            ok, err = store.delete_run(bad)
            self.assertFalse(ok, bad)
            self.assertIn("非法", err)
        # 运行中/排队中不允许删
        r2 = store.create_run("mgmt", "t2")
        for st in ("queued", "running"):
            store.update_run(r2["id"], status=st)
            ok, err = store.delete_run(r2["id"])
            self.assertFalse(ok)
            self.assertIn("取消", err)
        self.assertIsNotNone(store.get_run(r2["id"]))
        # 已取消/失败/完成可删
        store.update_run(r2["id"], status="cancelled")
        ok, err = store.delete_run(r2["id"])
        self.assertTrue(ok, err)
        self.assertIsNone(store.get_run(r2["id"]))


class TestMocksDeterministic(BaseTest):
    def runTest(self):
        from app.core import mocks
        task = {"title": "T", "goal": "G"}
        dims = ["情节", "人物"]
        c1a = mocks.critique("mock-a", 1, dims, 7.0)
        c1b = mocks.critique("mock-a", 1, dims, 7.0)
        self.assertEqual(c1a, c1b)  # 同参数结果必须一致（可重放）
        r1 = mocks.critique("mock-a", 1, dims, 7.0)
        r2 = mocks.critique("mock-a", 2, dims, 7.0)
        self.assertFalse(r1["scores"]["情节"] >= 7.0)   # 第 1 轮低于阈值
        self.assertTrue(r2["scores"]["情节"] >= 7.0)    # 第 2 轮达标


class TestTaskArchiveDelete(BaseTest):
    def runTest(self):
        from app.core import store
        wd = str(self.workdir)
        t1 = store.create_task({"type": "code", "goal": "任务一", "workdir": wd})
        t2 = store.create_task({"type": "novel", "goal": "任务二", "workdir": wd})
        r1 = store.create_run("orchestration", t1["title"], task_id=t1["id"])
        r2 = store.create_run("orchestration", t2["title"], task_id=t2["id"])
        store.update_run(r1["id"], status="done")   # 新建运行默认 queued，不可删
        store.update_run(r2["id"], status="done")

        # 归档：默认列表隐藏、归档列表可见、可恢复
        ok, err = store.archive_task(t1["id"], True)
        self.assertTrue(ok, err)
        self.assertEqual([t["id"] for t in store.list_tasks(archived=False)], [t2["id"]])
        self.assertEqual([t["id"] for t in store.list_tasks(archived=True)], [t1["id"]])
        ok, _ = store.archive_task(t1["id"], False)
        self.assertTrue(ok)
        self.assertEqual(len(store.list_tasks(archived=False)), 2)

        # 删除：任务 + 关联运行（内存与磁盘目录）一并清除
        ok, err = store.delete_task(t2["id"])
        self.assertTrue(ok, err)
        self.assertIsNone(store.get_task(t2["id"]))
        self.assertIsNone(store.get_run(r2["id"]))
        self.assertFalse((self._paths.RUNS_DIR / r2["id"]).exists())
        self.assertEqual([r["id"] for r in store.list_runs(10)], [r1["id"]])

        # 防护：运行中的任务不能删除/归档；非法 ID 拒绝
        store.update_run(r1["id"], status="running")
        ok, err = store.delete_task(t1["id"])
        self.assertFalse(ok)
        self.assertIn("取消", err)
        ok, _ = store.archive_task(t1["id"], True)
        self.assertFalse(ok)
        ok, _ = store.delete_task("../escape")
        self.assertFalse(ok)


class TestTaskRetry(BaseTest):
    def runTest(self):
        from app.core import store
        wd = str(self.workdir)
        t = store.create_task({"type": "code", "goal": "重试我", "workdir": wd})
        r1 = store.create_run("orchestration", t["title"], task_id=t["id"])
        store.update_run(r1["id"], status="running")
        ok, err, _ = store.retry_task(t["id"])
        self.assertFalse(ok)                      # 运行中不能重试
        store.update_run(r1["id"], status="failed")
        # 终态回填：run 失败时任务状态同步为 failed（归档不再误报"运行中"）
        self.assertEqual(store.get_task(t["id"])["status"], "failed")
        ok, err, r2 = store.retry_task(t["id"])
        self.assertTrue(ok, err)
        self.assertEqual(r2["task_id"], t["id"])
        self.assertEqual(store.get_task(t["id"])["status"], "queued")
        ok, _ = store.archive_task(t["id"], True)
        self.assertFalse(ok)                      # 重试产生的活跃运行挡住归档
        store.update_run(r2["id"], status="done")
        self.assertEqual(store.get_task(t["id"])["status"], "done")
        # load_all 回填历史遗留：run 终态而任务停在 queued
        t3 = store.create_task({"type": "code", "goal": "遗留", "workdir": wd})
        r3 = store.create_run("orchestration", t3["title"], task_id=t3["id"])
        store.update_run(r3["id"], status="failed")
        store._TASKS[t3["id"]]["status"] = "queued"
        store.load_all()
        self.assertEqual(store.get_task(t3["id"])["status"], "failed")
        # 非法 ID
        ok, _, _ = store.retry_task("../escape")
        self.assertFalse(ok)
        # 重启恢复：磁盘上停留在 queued/running 的运行判为中断（failed），任务状态随之回填
        t4 = store.create_task({"type": "code", "goal": "断点", "workdir": wd})
        r4 = store.create_run("orchestration", t4["title"], task_id=t4["id"])
        store._RUNS[r4["id"]]["status"] = "running"
        store._save_json(self._paths.RUNS_DIR / r4["id"] / "run.json", store._RUNS[r4["id"]])
        store._RUNS.clear()
        store._TASKS.clear()
        store.load_all()
        self.assertEqual(store.get_run(r4["id"])["status"], "failed")
        self.assertEqual(store.get_task(t4["id"])["status"], "failed")
        # 卡在 queued 却从未有运行（创建中断）→ 标记失败，可重试
        t5 = store.create_task({"type": "code", "goal": "创建中断", "workdir": wd})
        store._TASKS[t5["id"]]["status"] = "queued"
        store.load_all()
        self.assertEqual(store.get_task(t5["id"])["status"], "failed")
        ok, err, r5 = store.retry_task(t5["id"])
        self.assertTrue(ok, err)


if __name__ == "__main__":
    import unittest as _u
    _u.main()
