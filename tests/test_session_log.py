# -*- coding: utf-8 -*-
"""surface 会话日志测试。
设计稿：docs/migration/02-context-compaction.md §1A。
"""
from __future__ import annotations

import json
from pathlib import Path
from base import BaseTest


def _session_path(self) -> Path:
    """BaseTest 管理的临时目录内文件（setUp 创建、tearDown 清理）。"""
    return self.tmp / "session.jsonl"


class TestSessionAppend(BaseTest):

    def test_append_seq_monotonic(self):
        from app.core.session_log import Session
        s = Session("r1")
        e1 = s.append("user_message", {"content": "a"})
        e2 = s.append("assistant_message", {"content": "b"})
        e3 = s.append("user_message", {"content": "c"})
        self.assertEqual((e1.seq, e2.seq, e3.seq), (1, 2, 3))

    def test_derive_basic_roles(self):
        from app.core.session_log import Session
        s = Session("r1")
        s.append("system_message", {"content": "你是主编"})
        s.append("user_message", {"content": "写一章"})
        s.append("assistant_message", {"content": "好的"})
        msgs = s.derive_messages()
        self.assertEqual([m["role"] for m in msgs], ["system", "user", "assistant"])

    def test_non_surface_types_excluded(self):
        from app.core.session_log import Session
        s = Session("r1")
        s.append("user_message", {"content": "q"})
        s.append("tool_result", {"content": "raw output"}, surface_op="shadow")
        s.append("assistant_message", {"content": "a"})
        msgs = s.derive_messages()
        self.assertEqual(len(msgs), 2)
        self.assertEqual(msgs[1]["content"], "a")

    def test_adjacent_same_role_merged(self):
        from app.core.session_log import Session
        s = Session("r1")
        s.append("system_message", {"content": "part1"})
        s.append("system_message", {"content": "part2"})
        s.append("user_message", {"content": "go"})
        msgs = s.derive_messages()
        self.assertEqual(len(msgs), 2)
        self.assertIn("part1", msgs[0]["content"])
        self.assertIn("part2", msgs[0]["content"])


class TestSurfaceReplace(BaseTest):

    def test_replace_folds_range(self):
        from app.core.session_log import Session
        s = Session("r1")
        s.append("user_message", {"content": "turn1"})
        s.append("assistant_message", {"content": "reply1"})
        s.append("user_message", {"content": "turn2"})
        gen0 = s.replace_generation()
        s.append("user_message", {
            "content": "[摘要] 前两轮已压缩"},
            surface_op={"op": "replace", "start_seq": 1, "end_seq": 2})
        self.assertEqual(s.replace_generation(), gen0 + 1)
        msgs = s.derive_messages()
        joined = "\n".join(m["content"] for m in msgs)
        # 摘要替换了 seq1-2；turn2 仍在。摘要（user）与 turn2（user）相邻合并为一条
        self.assertEqual(len(msgs), 1)
        self.assertIn("[摘要]", joined)
        self.assertIn("turn2", joined)
        self.assertNotIn("reply1", joined)
        self.assertNotIn("turn1", joined)

    def test_replace_generation_counts(self):
        from app.core.session_log import Session
        s = Session("r1")
        self.assertEqual(s.replace_generation(), 0)
        s.append("user_message", {"content": "x"},
                 surface_op={"op": "replace", "start_seq": 1, "end_seq": 2})
        s.append("user_message", {"content": "y"},
                 surface_op={"op": "replace", "start_seq": 1, "end_seq": 3})
        self.assertEqual(s.replace_generation(), 2)

    def test_shadow_does_not_bump_generation(self):
        from app.core.session_log import Session
        s = Session("r1")
        s.append("tool_result", {"content": "x"}, surface_op="shadow")
        self.assertEqual(s.replace_generation(), 0)


class TestSessionPersist(BaseTest):

    def test_persist_and_reload(self):
        from app.core.session_log import Session
        path = _session_path(self)
        s = Session("r1", store_path=path)
        s.append("user_message", {"content": "中文消息"})
        # 摘要（assistant 角色）替换 seq1：surface 只剩摘要
        s.append("assistant_message", {"content": "回复"},
                 surface_op={"op": "replace", "start_seq": 1, "end_seq": 1})
        s2 = Session("r1", store_path=path)
        self.assertEqual(len(s2.events()), 2)
        self.assertEqual(s2.replace_generation(), 1)
        msgs = s2.derive_messages()
        self.assertEqual(len(msgs), 1)
        self.assertEqual(msgs[0]["content"], "回复")
        self.assertNotIn("中文消息", msgs[0]["content"])

    def test_corrupt_line_skipped(self):
        from app.core.session_log import Session
        path = _session_path(self)
        path.write_text(
            json.dumps({"seq": 1, "type": "user_message", "data": {"content": "ok"}}, ensure_ascii=False) + "\n"
            + "THIS IS NOT JSON\n"
            + json.dumps({"seq": 2, "type": "assistant_message", "data": {"content": "fine"}}, ensure_ascii=False) + "\n",
            encoding="utf-8")
        s = Session("r1", store_path=path)
        self.assertEqual(len(s.events()), 2)
        # 续写 seq 从最大值继续
        e = s.append("user_message", {"content": "next"})
        self.assertEqual(e.seq, 3)

    def test_no_store_path_memory_only(self):
        from app.core.session_log import Session
        s = Session("r1")
        s.append("user_message", {"content": "x"})
        self.assertEqual(len(s.events()), 1)


class TestThreadSafety(BaseTest):

    def test_concurrent_append(self):
        import threading
        from app.core.session_log import Session
        s = Session("r1")

        def worker():
            for _ in range(20):
                s.append("user_message", {"content": "x"})

        threads = [threading.Thread(target=worker) for _ in range(5)]
        for t in threads:
            t.start()
        for t in threads:
            t.join(timeout=5)
        seqs = [e.seq for e in s.events()]
        self.assertEqual(len(seqs), 100)
        self.assertEqual(len(set(seqs)), 100)  # 无重复
        self.assertEqual(max(seqs), 100)