# -*- coding: utf-8 -*-
"""三段式压缩测试。
设计稿：docs/migration/02-context-compaction.md §1B。
"""
from __future__ import annotations

from base import BaseTest


def _fake_llm(summary="这是摘要"):
    calls = []

    def caller(messages):
        calls.append(messages)
        return summary

    caller.calls = calls
    return caller


class TestPruneText(BaseTest):

    def test_short_text_untouched(self):
        from app.core.compaction import prune_text
        self.assertEqual(prune_text("short"), "short")

    def test_long_text_pruned(self):
        from app.core.compaction import prune_text, PRUNE_HEAD, PRUNE_TAIL
        text = "A" * PRUNE_HEAD + "X" * 100000 + "B" * PRUNE_TAIL
        out = prune_text(text)
        self.assertLess(len(out), len(text))
        self.assertTrue(out.startswith("A"))
        self.assertTrue(out.endswith("B"))
        self.assertIn("已剪枝", out)

    def test_none_safe(self):
        from app.core.compaction import prune_text
        self.assertEqual(prune_text(None), "")


class TestSelectRange(BaseTest):

    def _session_with_turns(self, n_turns, content="x" * 100):
        from app.core.session_log import Session
        s = Session("r1")
        s.append("system_message", {"content": "sys"})
        for i in range(n_turns):
            s.append("user_message", {"content": f"u{i} " + content})
            s.append("assistant_message", {"content": f"a{i} " + content})
        return s

    def test_no_user_message_returns_zero(self):
        from app.core.session_log import Session
        from app.core.compaction import select_range
        s = Session("r1")
        s.append("system_message", {"content": "sys"})
        self.assertEqual(select_range(s), (0, 0))

    def test_small_session_no_region(self):
        """内容都在尾预算内 → 无可压缩区域。"""
        from app.core.compaction import select_range, RETAIN_TAIL_TOKENS
        s = self._session_with_turns(2)
        start, end = select_range(s, retain_tail_tokens=RETAIN_TAIL_TOKENS)
        # 每条 ~25 token，远小于 8000 → 无区域
        self.assertEqual((start, end), (0, 0))

    def test_region_skips_system_head(self):
        from app.core.session_log import Session
        from app.core.compaction import select_range
        s = Session("r1")
        s.append("system_message", {"content": "sys"})
        s.append("user_message", {"content": "q"})
        s.append("assistant_message", {"content": "a"})
        # 尾预算 0 → 全部 user 起的区域可压
        start, end = select_range(s, retain_tail_tokens=0)
        self.assertEqual(start, 2)  # 第一条 user 的 seq
        self.assertEqual(end, 3)

    def test_tail_retained(self):
        from app.core.compaction import select_range
        s = self._session_with_turns(6, content="x" * 400)  # 每条 ~100 token
        start, end = select_range(s, retain_tail_tokens=250)
        self.assertGreater(start, 0)
        self.assertLess(end, s.events()[-1].seq)

    def test_never_cuts_tool_pair(self):
        from app.core.session_log import Session
        from app.core.compaction import select_range
        s = Session("r1")
        s.append("system_message", {"content": "sys"})
        s.append("user_message", {"content": "q1"})
        s.append("tool_call", {"content": "call"})
        s.append("tool_result", {"content": "result"})
        s.append("assistant_message", {"content": "a1"})
        start, end = select_range(s, retain_tail_tokens=0)
        self.assertEqual(start, 2)
        # 末端回退到非 tool 事件
        by_seq = {e.seq: e for e in s.events()}
        self.assertNotIn(by_seq[end].type, ("tool_call", "tool_result"))

    def test_region_event_cap(self):
        from app.core.compaction import select_range
        s = self._session_with_turns(30, content="x" * 400)
        start, end = select_range(s, retain_tail_tokens=0, max_region_events=10)
        count = sum(1 for e in s.events() if start <= e.seq <= end)
        self.assertLessEqual(count, 10)


class TestCompactRegion(BaseTest):

    def test_success_writes_four_events(self):
        from app.core.session_log import Session
        from app.core.compaction import compact_region
        s = Session("r1")
        s.append("user_message", {"content": "q1"})
        s.append("assistant_message", {"content": "a1"})
        n0 = len(s.events())
        llm = _fake_llm("摘要内容")
        ok = compact_region(s, 1, 2, llm)
        self.assertTrue(ok)
        events = s.events()
        types = [e.type for e in events[n0:]]
        self.assertEqual(types, ["compaction_start", "compaction_summary",
                                 "user_message", "compaction_end"])
        # 摘要调用了 LLM
        self.assertEqual(len(llm.calls), 1)
        # surface 被替换
        msgs = s.derive_messages()
        joined = "\n".join(m["content"] for m in msgs)
        self.assertIn("摘要内容", joined)
        self.assertNotIn("q1", joined)

    def test_llm_failure_still_writes_end(self):
        from app.core.session_log import Session
        from app.core.compaction import compact_region
        s = Session("r1")
        s.append("user_message", {"content": "q1"})

        def bad_llm(messages):
            raise RuntimeError("llm down")

        ok = compact_region(s, 1, 1, bad_llm)
        self.assertFalse(ok)
        types = [e.type for e in s.events()]
        self.assertIn("compaction_end", types)
        # 失败时不产生 surface replace → 原内容仍在
        msgs = s.derive_messages()
        self.assertIn("q1", msgs[-1]["content"])

    def test_tool_result_pruned_before_summary(self):
        from app.core.session_log import Session
        from app.core.compaction import compact_region, PRUNE_HEAD
        s = Session("r1")
        s.append("user_message", {"content": "q"})
        s.append("tool_result", {"content": "R" * (PRUNE_HEAD * 3)})
        llm = _fake_llm("ok")
        compact_region(s, 1, 2, llm)
        sent = llm.calls[0][-1]["content"]
        self.assertIn("已剪枝", sent)
        self.assertLess(len(sent), PRUNE_HEAD * 3)


class TestMaybeCompact(BaseTest):

    def test_no_llm_caller_noop(self):
        from app.core.session_log import Session
        from app.core.compaction import maybe_compact
        s = Session("r1")
        s.append("user_message", {"content": "q"})
        self.assertFalse(maybe_compact(s, threshold=0.0))

    def test_below_threshold_noop(self):
        from app.core.session_log import Session
        from app.core.compaction import maybe_compact
        from app.core.token_meter import token_meter
        token_meter.reset("r-mc1")
        s = Session("r-mc1")
        s.append("user_message", {"content": "q"})
        token_meter.accumulate("r-mc1", {"input": 100})
        self.assertFalse(maybe_compact(s, model="", llm_caller=_fake_llm(),
                                       threshold=0.8))

    def test_above_threshold_compacts(self):
        from app.core.session_log import Session
        from app.core.compaction import maybe_compact
        from app.core.token_meter import token_meter
        rid = "r-mc2"
        token_meter.reset(rid)
        s = Session(rid)
        s.append("system_message", {"content": "sys"})
        for i in range(8):
            s.append("user_message", {"content": f"u{i} " + "x" * 500})
            s.append("assistant_message", {"content": f"a{i} " + "x" * 500})
        token_meter.accumulate(rid, {"input": 120_000}, model="")
        ok = maybe_compact(s, model="", llm_caller=_fake_llm("压"), threshold=0.8,
                           run_id=rid, retain_tail_tokens=500)
        self.assertTrue(ok)
        # surface 被替换过
        self.assertGreaterEqual(s.replace_generation(), 1)
        token_meter.reset(rid)

    def test_all_within_budget_noop(self):
        """压力超阈值但内容全在尾预算内 → 无区域，不压。"""
        from app.core.session_log import Session
        from app.core.compaction import maybe_compact, RETAIN_TAIL_TOKENS
        from app.core.token_meter import token_meter
        rid = "r-mc3"
        token_meter.reset(rid)
        s = Session(rid)
        s.append("user_message", {"content": "tiny"})
        token_meter.accumulate(rid, {"input": 120_000})
        ok = maybe_compact(s, llm_caller=_fake_llm(), threshold=0.8, run_id=rid)
        self.assertFalse(ok)
        token_meter.reset(rid)