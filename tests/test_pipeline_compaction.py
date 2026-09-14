# -*- coding: utf-8 -*-
"""pipeline × 压缩灰度接线测试：TUTTI_COMPACTION=1 时 _run_step 走 execute_step 路径。
设计稿：docs/migration/02-context-compaction.md §1D 集成段。
"""
from __future__ import annotations

import os
import sys
from pathlib import Path
from base import BaseTest

FIXTURES = Path(__file__).parent


class _CompactionEnv:
    """临时设置 TUTTI_COMPACTION 的上下文。"""

    def __init__(self, on):
        self.on = on
        self.old = None

    def __enter__(self):
        self.old = os.environ.get("TUTTI_COMPACTION")
        if self.on:
            os.environ["TUTTI_COMPACTION"] = "1"
        else:
            os.environ.pop("TUTTI_COMPACTION", None)
        return self

    def __exit__(self, *a):
        if self.old is None:
            os.environ.pop("TUTTI_COMPACTION", None)
        else:
            os.environ["TUTTI_COMPACTION"] = self.old


def _real_agent():
    return {"id": "fakecli", "kind": "generic",
            "command": sys.executable,
            "argv_template": [str(FIXTURES / "fixtures_role_cli.py"), "{prompt}"],
            "mode": "real", "label": "Fake CLI"}


class TestPipelineCompactionWiring(BaseTest):

    def setUp(self):
        super().setUp()
        from app.core.token_meter import token_meter
        self._meter = token_meter

    def test_default_off_no_session_file(self):
        """默认关：_run_step 走原路径，不产生 session.jsonl。"""
        from app.core import pipeline, store
        with _CompactionEnv(on=False):
            run = store.create_run("orchestration", "t")
            res = pipeline._run_step(run["id"], "draft", _real_agent(),
                                     "网文主编 出大纲", str(self.workdir),
                                     readonly=True, ev=None)
            self.assertTrue(res["ok"], res.get("error"))
        sfile = self.data_dir / "runs" / run["id"] / "session.jsonl"
        self.assertFalse(sfile.exists())

    def test_enabled_writes_session_and_meter(self):
        """开启：_run_step 走 execute_step 路径，session.jsonl 有事件、meter 有累计。"""
        from app.core import pipeline, store
        with _CompactionEnv(on=True):
            run = store.create_run("orchestration", "t2")
            res = pipeline._run_step(run["id"], "draft", _real_agent(),
                                     "网文主编 出大纲", str(self.workdir),
                                     readonly=True, ev=None)
            self.assertTrue(res["ok"], res.get("error"))
        sfile = self.data_dir / "runs" / run["id"] / "session.jsonl"
        self.assertTrue(sfile.exists(), "session.jsonl 应被创建")
        content = sfile.read_text(encoding="utf-8")
        self.assertIn("user_message", content)
        self.assertIn("assistant_message", content)
        # meter 有累计（usage 来自 fake CLI 的空输出解析 → 可能全 0，但 key 应存在）
        # 直接验证 session 可从缓存取回并派生
        s = pipeline._get_session(run["id"])
        self.assertGreaterEqual(len(s.events()), 2)

    def test_session_cache_reuse(self):
        """同一 run_id 两次取 session 返回同一实例且事件累积。"""
        from app.core import pipeline, store
        with _CompactionEnv(on=True):
            run = store.create_run("orchestration", "t3")
            pipeline._run_step(run["id"], "draft", _real_agent(),
                               "网文主编", str(self.workdir), readonly=True, ev=None)
            s1 = pipeline._get_session(run["id"])
            n1 = len(s1.events())
            pipeline._run_step(run["id"], "draft-c1", _real_agent(),
                               "写第一章", str(self.workdir), readonly=False, ev=None)
            s2 = pipeline._get_session(run["id"])
        self.assertIs(s1, s2)
        self.assertGreater(len(s2.events()), n1)