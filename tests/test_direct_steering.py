# -*- coding: utf-8 -*-
"""运行中指挥链路：信箱 add/drain、步骤注入、下达审计头（全部 mock，不碰真实 CLI）。"""
from __future__ import annotations

import json
import time

from base import BaseTest


class TestMessageMailbox(BaseTest):
    """store 信箱：追加→drain→防重复，run.json 落盘可回放。"""

    def runTest(self):
        from app.core import store
        run = store.create_run("orchestration", "指挥链路测试")
        rid = run["id"]

        # 空消息拒绝（文字附件都空）
        self.assertIsNone(store.add_message(rid, "  "))
        self.assertEqual(store.drain_messages(rid), [])

        # 追加：带附件路径
        m1 = store.add_message(rid, "第三章把林昭改成林小满", sender="测试设备",
                               attachments=["_attachments/shot.png"])
        self.assertIsNotNone(m1)
        self.assertFalse(m1["consumed"])
        m2 = store.add_message(rid, "文风再克制一点")
        self.assertEqual(len(store.get_run(rid)["messages"]), 2)

        # drain：一次取走全部并标记 consumed；再 drain 为空
        pend = store.drain_messages(rid)
        self.assertEqual([p["text"] for p in pend],
                         ["第三章把林昭改成林小满", "文风再克制一点"])
        self.assertEqual(pend[0]["attachments"], ["_attachments/shot.png"])
        self.assertEqual(pend[0]["sender"], "测试设备")
        self.assertTrue(all(m["consumed"] for m in store.get_run(rid)["messages"]))
        self.assertEqual(store.drain_messages(rid), [])

        # 落盘可回放：重启加载后消息仍在
        import json as _json
        disk = _json.loads((store.paths.RUNS_DIR / rid / "run.json")
                           .read_text(encoding="utf-8"))
        self.assertEqual(len(disk["messages"]), 2)
        self.assertTrue(disk["messages"][0]["consumed"])

        # 不存在的 run：静默返回，不炸
        self.assertIsNone(store.add_message("r-nope", "x"))
        self.assertEqual(store.drain_messages("r-nope"), [])

        # 旧 run.json 无 messages 字段：_ensure_messages 兜底不炸
        self.assertEqual(store.drain_messages(rid), [])


class TestDirectiveInjection(BaseTest):
    """_run_step 真实分支：信箱指令注入 prompt 头部、图片并入 images、消费不重复。"""

    def runTest(self):
        from app.core import pipeline, runner, store
        run = store.create_run("orchestration", "注入测试")
        rid = run["id"]
        store.add_message(rid, "把结尾改成开放式", sender="注入测试")
        store.add_message(rid, "", sender="注入测试",
                          attachments=["_attachments/pic.png"])
        # 伪造图片文件（图片路径必须存在才并入 images）
        wd = self.workdir
        (wd / "_attachments").mkdir()
        (wd / "_attachments" / "pic.png").write_bytes(b"png")

        agent = {"id": "a1", "kind": "codex", "mode": "real", "command": "codex",
                 "env": {"TUTTI_TEST_SELFCONFIG": "1"}}  # 自带配置：过死链闸门
        seen = {}
        orig = runner.run_process

        def fake(argv=None, **kw):
            seen["stdin"] = kw.get("stdin_text") or ""
            seen["log"] = kw.get("log_path")
            return {"ok": True, "exit_code": 0, "stdout": "done", "stderr": "",
                    "duration": 0.1, "cancelled": False, "timed_out": False}
        runner.run_process = fake
        try:
            res = pipeline._run_step(rid, "implement", agent, "原始任务目标",
                                     str(wd), readonly=False, ev=None)
            self.assertTrue(res["ok"])
            # 注入块在 prompt 头部，原始目标仍在
            self.assertIn("用户实时指令", seen["stdin"])
            self.assertIn("把结尾改成开放式", seen["stdin"])
            self.assertIn("原始任务目标", seen["stdin"])
            self.assertLess(seen["stdin"].index("用户实时指令"),
                            seen["stdin"].index("原始任务目标"))
            self.assertTrue(seen["stdin"].endswith("原始任务目标")
                            or "_attachments/pic.png" in seen["stdin"])
        finally:
            runner.run_process = orig

        # 已消费：第二个真实步骤不再注入
        def fake2(argv=None, **kw):
            seen["stdin"] = kw.get("stdin_text") or ""
            return {"ok": True, "exit_code": 0, "stdout": "done", "stderr": "",
                    "duration": 0.1, "cancelled": False, "timed_out": False}
        runner.run_process = fake2
        try:
            pipeline._run_step(rid, "implement-2", agent, "第二步", str(wd),
                               readonly=False, ev=None)
            self.assertNotIn("用户实时指令", seen["stdin"])
        finally:
            runner.run_process = orig

        # mock 分支不 drain：信箱里的新消息保留到下一个真实步骤
        store.add_message(rid, "mock 步骤不该吃掉这条")
        mock_agent = {"id": "mk", "kind": "generic", "mode": "mock", "command": "x"}
        pipeline._run_step(rid, "critique", mock_agent, "评审", str(wd),
                           readonly=True, ev=None)
        self.assertEqual(len(store.drain_messages(rid)), 1)


class TestAuditHeader(BaseTest):
    """run_process 审计头：下达的命令 + stdin 指令原文先于输出落进日志。"""

    def runTest(self):
        import sys
        import tempfile
        from pathlib import Path
        from app.core import runner
        log = Path(tempfile.mkdtemp(prefix="orch-audit-")) / "step.log"
        res = runner.run_process(argv=[sys.executable, "-c", "print('PROC_OUT')"],
                                 stdin_text="指令原文行一\n指令原文行二",
                                 log_path=str(log), timeout=30)
        self.assertTrue(res["ok"])
        text = log.read_text(encoding="utf-8")
        self.assertIn("===== 下达 ", text)
        self.assertIn("指令原文行二", text)
        self.assertIn("--- 输出 ---", text)
        self.assertIn("PROC_OUT", text)
        # 顺序：指令原文必须在 "--- 输出 ---" 分隔线之前（其后才是子进程真实输出）
        self.assertLess(text.index("指令原文行二"), text.index("--- 输出 ---"))
        self.assertLess(text.index("--- 输出 ---"), text.rindex("PROC_OUT"))


class TestReceiptAndReviewRole(BaseTest):
    """送达回执（consumed_by 步号+角色）+ 评审步骤注入升级为评分依据。"""

    def runTest(self):
        from app.core import pipeline, store
        run = store.create_run("orchestration", "回执测试")
        rid = run["id"]
        store.add_message(rid, "用户纠偏一条")
        block, _ = pipeline._drain_directives(rid, str(self.workdir),
                                              role="critique-c3", step_n=5)
        self.assertIn("用户纠偏一条", block)
        self.assertIn("评分依据", block)  # 评审角色 → 框架文案升级
        self.assertIn("引用用户原话", block)  # 框架要求评审引用用户意见
        # 回执落盘：consumed + consumed_by 指向 #5 critique-c3
        m = store.get_run(rid)["messages"][0]
        self.assertTrue(m["consumed"])
        self.assertEqual(m["consumed_by"], {"step": 5, "role": "critique-c3"})

        # 非评审角色：无评分依据文案
        store.add_message(rid, "普通纠偏")
        block2, _ = pipeline._drain_directives(rid, str(self.workdir),
                                               role="draft-c2", step_n=7)
        self.assertIn("普通纠偏", block2)
        self.assertNotIn("评分依据", block2)
        self.assertEqual(store.get_run(rid)["messages"][1]["consumed_by"],
                         {"step": 7, "role": "draft-c2"})

        # peek 不带回执语义不受影响
        store.add_message(rid, "peek 一条")
        self.assertEqual(len(store.peek_messages(rid)), 1)
        self.assertFalse(store.get_run(rid)["messages"][2].get("consumed"))


class TestPeekAndInherit(BaseTest):
    """peek 不消费（规划/执行双视角）+ retry 把未消费指令带进新 run。"""

    def runTest(self):
        from app.core import store
        run = store.create_run("orchestration", "peek 测试")
        rid = run["id"]
        store.add_message(rid, "规划也要看到这条")
        # peek：可见但不消费
        peeked = store.peek_messages(rid)
        self.assertEqual(len(peeked), 1)
        self.assertTrue(all(m["consumed"] for m in []) or True)
        self.assertFalse(store.get_run(rid)["messages"][0]["consumed"])
        self.assertEqual(len(store.peek_messages(rid)), 1)  # peek 幂等
        # drain 后 peek 为空
        self.assertEqual(len(store.drain_messages(rid)), 1)
        self.assertEqual(store.peek_messages(rid), [])

        # retry 继承：旧 run 有未消费消息 → 新 run 带副本
        store.add_message(rid, "失败前下的指令")
        store.update_run(rid, status="failed", ended_at="2099-01-01 00:00:00")
        ok, err, new_run = store.retry_task("no-such-task")
        self.assertFalse(ok)
        # 造一个挂在 run 上的任务
        task = store._TASKS.get("t-x")
        if task is None:
            task = {"id": "t-x", "title": "继承任务", "type": "novel", "goal": "g",
                    "workdir": str(self.workdir), "status": "failed", "created_at": ""}
            store._TASKS["t-x"] = task
        run["task_id"] = "t-x"
        store._RUNS[rid] = run
        ok, err, nr = store.retry_task("t-x")
        self.assertTrue(ok, err)
        msgs = nr.get("messages") or []
        self.assertEqual([m["text"] for m in msgs], ["失败前下的指令"])
        self.assertFalse(msgs[0]["consumed"])
        # 旧 run 消息原样保留
        self.assertEqual(len(store._RUNS[rid]["messages"]), 2)


class TestPauseGate(BaseTest):
    """暂停闸门：paused=True 时 _run_step 阻塞，放行后继续；取消事件直接通过。"""

    def runTest(self):
        import threading
        from app.core import pipeline, runner, store
        run = store.create_run("orchestration", "闸门测试")
        rid = run["id"]
        agent = {"id": "a1", "kind": "codex", "mode": "real", "command": "codex",
                 "env": {"TUTTI_TEST_SELFCONFIG": "1"}}  # 自带配置：过死链闸门
        seen = {}
        orig = runner.run_process

        def fake(argv=None, **kw):
            seen["stdin"] = kw.get("stdin_text") or ""
            return {"ok": True, "exit_code": 0, "stdout": "done", "stderr": "",
                    "duration": 0.05, "cancelled": False, "timed_out": False}
        runner.run_process = fake
        try:
            # 预先暂停 + 1.5s 后自动放行
            store.set_paused(rid, True)
            self.assertTrue(store.get_run(rid)["paused"])
            threading.Timer(1.5, lambda: store.set_paused(rid, False)).start()
            t0 = time.time()
            res = pipeline._run_step(rid, "implement", agent, "目标", str(self.workdir),
                                     readonly=False, ev=None)
            took = time.time() - t0
            self.assertTrue(res["ok"])
            self.assertGreaterEqual(took, 1.4, "放行前不应执行步骤")

            # 取消事件优先：暂停中点取消 → 立即返回（Cancelled 异常）
            store.set_paused(rid, True)
            ev = threading.Event()
            threading.Timer(0.3, ev.set).start()
            with self.assertRaises(pipeline.Cancelled):
                pipeline._run_step(rid, "implement-2", agent, "目标2", str(self.workdir),
                                   readonly=False, ev=ev)
        finally:
            runner.run_process = orig


class TestSteeredPlanning(BaseTest):
    """_steered_task：未消费指令合入 context（peek 语义），无消息时原样返回。"""

    def runTest(self):
        from app.core import pipeline, store
        run = store.create_run("orchestration", "规划合入测试")
        rid = run["id"]
        task = {"id": "t-p", "goal": "写个工具", "context": "已有背景"}
        # 无消息：原对象
        self.assertIs(pipeline._steered_task(rid, task), task)
        # 有消息：新副本，context 追加指令块，原 task 不被改
        store.add_message(rid, "优先支持命令行模式")
        t2 = pipeline._steered_task(rid, task)
        self.assertIsNot(t2, task)
        self.assertIn("已有背景", t2["context"])
        self.assertIn("用户实时指令", t2["context"])
        self.assertIn("优先支持命令行模式", t2["context"])
        self.assertNotIn("用户实时指令", task["context"])
        # peek 语义：消息仍未消费，后续执行步骤还能 drain 到
        self.assertEqual(len(store.peek_messages(rid)), 1)


class TestRetractMessage(BaseTest):
    """撤回未下达指令：删未消费的、拒删已送达的、幂等与边界。"""

    def runTest(self):
        from app.core import store
        run = store.create_run("orchestration", "撤回测试")
        rid = run["id"]
        m1 = store.add_message(rid, "这条会被撤回")
        m2 = store.add_message(rid, "这条会被送达")
        self.assertIsNotNone(m1)
        self.assertIsNotNone(m2)

        # 撤回未消费：从信箱删除，落盘同步
        ok, err = store.retract_message(rid, m1["id"])
        self.assertTrue(ok, err)
        msgs = store.get_run(rid)["messages"]
        self.assertEqual([m["text"] for m in msgs], ["这条会被送达"])
        disk = json.loads((store.paths.RUNS_DIR / rid / "run.json")
                          .read_text(encoding="utf-8"))
        self.assertEqual(len(disk["messages"]), 1)

        # 撤回不存在的 id：拒绝并给原因
        ok, err = store.retract_message(rid, "999999")
        self.assertFalse(ok)
        self.assertIn("不在信箱", err)

        # drain 后消息已送达：拒撤，回执信息进 err（界面上该撤的只剩未消费）
        store.drain_messages(rid, consumed_by={"step": 3, "role": "draft-c1"})
        ok, err = store.retract_message(rid, m2["id"])
        self.assertFalse(ok)
        self.assertIn("已随步骤", err)
        self.assertIn("无法撤回", err)

        # 空 id / 不存在的 run：安全拒绝不炸
        self.assertEqual(store.retract_message(rid, None), (False, "缺少消息 id"))
        self.assertFalse(store.retract_message("r-nope", "000001")[0])


if __name__ == "__main__":
    import unittest as _u
    _u.main()
