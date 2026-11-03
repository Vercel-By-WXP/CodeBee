# -*- coding: utf-8 -*-
"""重复调用守门（repeat_guard）回归：确保自循环在第 5 次强制停止，不只提醒。

背景（2026-11-03 优化点⑥）：server-restart.log 曾有 count=5 仍 stop=False 的旧
记录——只有提醒没有强制动作，自循环会一直空转。当前实现 count>=last_thr 时
should_stop=True。这里固化该行为，防止将来被改回「只提醒不停止」。
"""
from __future__ import annotations

import unittest

from app.core.repeat_guard import RepeatGuard


class RepeatGuardStopTests(unittest.TestCase):
    def test_fifth_repeat_forces_stop(self):
        """第 5 次相同输入必须 should_stop=True，且提醒文案是 STOP_TEMPLATE。"""
        g = RepeatGuard((3, 5))
        run, role, prompt, ident = "r1", "critique", "同一段提示词", "codex-cli"
        seen = {}
        for n in range(1, 6):
            seen[n] = g.check(run, role, prompt, identity=ident)
        self.assertTrue(seen[5]["should_stop"],
                        "第 5 次相同输入必须强制停止（self-judgment 循环不应空转）")
        self.assertIn("强制停止", seen[5]["reminder"])
        self.assertFalse(any(seen[n]["should_stop"] for n in (1, 2, 3, 4)),
                         "阈值 5 之前不应 stop，只给 reminder")

    def test_third_repeat_reminds_but_not_stop(self):
        """第 3 次命中应给 REMINDER_TEMPLATE 提醒，但 should_stop 仍为 False。"""
        g = RepeatGuard((3, 5))
        r = g.check("r2", "critique", "同一个模板路径", identity="aider")
        r = g.check("r2", "critique", "同一个模板路径", identity="aider")
        r = g.check("r2", "critique", "同一个模板路径", identity="aider")
        self.assertFalse(r["should_stop"])
        self.assertIn("几乎相同的输入", r["reminder"])

    def test_new_prompt_resets_chain(self):
        """新 prompt（不同指纹）应重置连续计数，不被旧的 stop 阈值误杀。"""
        g = RepeatGuard((3, 5))
        run, role, ident = "r3", "critique", "claude"
        for _ in range(4):
            g.check(run, role, "旧模板 A", identity=ident)
        r = g.check(run, role, "换了一个完全不同的新输入 B", identity=ident)
        self.assertEqual(r["count"], 1)
        self.assertFalse(r["should_stop"])

    def test_identity_switch_resets_chain(self):
        """相同 prompt 换执行者（identity）属于有效回退，不共享同一连续预算。"""
        g = RepeatGuard((3, 5))
        run, role, prompt = "r4", "critique", "同一段"
        for _ in range(3):
            g.check(run, role, prompt, identity="a")
        r = g.check(run, role, prompt, identity="b")
        self.assertEqual(r["count"], 1, "换执行者应开始新的连续链")


if __name__ == "__main__":
    unittest.main()