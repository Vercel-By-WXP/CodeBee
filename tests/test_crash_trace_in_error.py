# -*- coding: utf-8 -*-
"""崩溃现场随错误行下发 + 同因签名按崩溃位置区分。

真实事故（2026-10-10 Mac 实案）：连载任务连烧两轮自动续跑，都死于
AttributeError("'NoneType' object has no attribute 'get'")——错误行只有
一行 repr，看不出崩在哪个文件哪一行；traceback 全文虽写进 run 目录的
error.log，但 UI 上看不见，远程排查无从下手。修复：execute_run 全局兜底
把 traceback 尾帧（文件:行号:函数名）追加进 error 字段。

连带收益：_err_signature 剥数字但保留文本，同位置崩溃签名稳定（止损照常
生效），不同位置/不同函数的崩溃签名不同（不会被误判同因），代码升级后
行号漂移自动视为新因（允许再试一轮，次数上限仍兜底）。
"""
from __future__ import annotations

from base import BaseTest


class TestCrashTraceInError(BaseTest):

    def test_execute_run_error_carries_crash_site(self):
        """全局兜底：error 行带「崩溃于 文件:行号 函数名」，error.log 照常落全文。"""
        from app.core import pipeline, store

        task = store.create_task({"type": "direct", "engine": "direct",
                                  "goal": "崩溃现场测试",
                                  "workdir": str(self.workdir)})
        rid = store.create_run("orchestration", task["title"],
                               task_id=task["id"])["id"]
        store.update_run(rid, status="queued")

        def boom(*a, **kw):
            raise AttributeError("'NoneType' object has no attribute 'get'")

        backup = pipeline._run_direct
        pipeline._run_direct = boom
        try:
            pipeline.execute_run(rid)
        finally:
            pipeline._run_direct = backup

        run = store.get_run(rid) or {}
        self.assertEqual(run.get("status"), "failed")
        err = run.get("error") or ""
        self.assertIn("AttributeError", err)
        self.assertIn("崩溃于", err)
        self.assertIn("test_crash_trace_in_error.py", err)
        self.assertIn("boom", err)
        # traceback 全文仍落 run 目录 error.log（事后可查全链）
        err_log = store.run_dir(rid) / "error.log"
        self.assertTrue(err_log.exists())
        self.assertIn("Traceback", err_log.read_text(encoding="utf-8"))

    def test_err_signature_separates_crash_sites(self):
        """同因签名：同位置崩溃签名稳定；不同崩溃位置不算同因。"""
        from app.core import jobs
        same_a = "AttributeError(\"'NoneType' object has no attribute 'get'\")" \
                 "（崩溃于 pipeline.py:4869 _run_serial_review）"
        same_b = "AttributeError(\"'NoneType' object has no attribute 'get'\")" \
                 "（崩溃于 pipeline.py:4870 _run_serial_review）"
        other_site = "AttributeError(\"'NoneType' object has no attribute 'get'\")" \
                     "（崩溃于 task_compile.py:110 compile_task）"
        # 同函数（行号剥成 N）：签名一致 → 止损照常拦住
        self.assertEqual(jobs._err_signature(same_a), jobs._err_signature(same_b))
        # 不同函数：签名不同 → 不误判同因
        self.assertNotEqual(jobs._err_signature(same_a), jobs._err_signature(other_site))
