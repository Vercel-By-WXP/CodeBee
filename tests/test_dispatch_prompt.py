import unittest

from app.core.dispatch_prompt import (
    compile_prompt,
    prompt_strategy,
)
from app.core.planner import _compile_planning_prompt


class DispatchPromptTests(unittest.TestCase):
    def test_low_risk_task_uses_deterministic_strategy(self):
        strategy = prompt_strategy({
            "type": "code",
            "mode": "auto",
            "difficulty": "easy",
            "goal": "修复一个断言",
            "verify_command": "python -m unittest tests/test_one.py",
        })

        self.assertEqual(strategy["mode"], "deterministic")
        self.assertEqual(strategy["max_extra_model_calls"], 0)

    def test_legacy_preflight_flag_never_triggers_hidden_model_call(self):
        strategy = prompt_strategy({
            "type": "code",
            "mode": "expert",
            "difficulty": "hard",
            "goal": "迁移数据库并保持公共 API 兼容",
            "prompt_preflight": True,
        })

        self.assertEqual(strategy["mode"], "deterministic")
        self.assertEqual(strategy["max_extra_model_calls"], 0)
        self.assertIn("不重复改写", strategy["reason"])

    def test_compile_prompt_preserves_original_step_and_adds_phase_contract(self):
        prompt = "请读取文件并完成当前步骤。"
        task = {
            "type": "serial_novel",
            "mode": "auto",
            "difficulty": "hard",
            "goal": "打磨第一卷",
            "intent_contract": {
                "must": ["先完成诊断"],
                "must_not": ["不要开始正文"],
                "deliverables": ["输出讨论稿"],
            },
            "attachments": [{"name": "交接总纲.md"}],
        }

        compiled = compile_prompt(task, "outline", prompt, readonly=True)

        self.assertIn("## CodeBee 下发契约（v1）", compiled)
        self.assertIn("当前阶段：规划/讨论", compiled)
        self.assertIn("不要开始正文", compiled)
        self.assertIn("附件不可读或缺失时", compiled)
        self.assertIn("--- 原始步骤提示词（保持原意） ---", compiled)
        self.assertIn(prompt, compiled)
        self.assertIn("--- 原始步骤提示词结束", compiled)
        self.assertGreater(compiled.index("--- 原始步骤提示词结束"), compiled.index(prompt))

    def test_compile_prompt_is_idempotent(self):
        task = {"type": "code", "goal": "修复 bug"}
        first = compile_prompt(task, "implement", "执行。", readonly=False)
        second = compile_prompt(task, "implement", first, readonly=False)

        self.assertEqual(first, second)

    def test_contract_envelope_is_bounded_before_original_prompt(self):
        task = {
            "type": "research",
            "intent_contract": {
                "must": ["甲" * 1000 for _ in range(20)],
                "must_not": ["乙" * 1000 for _ in range(20)],
                "deliverables": ["丙" * 1000 for _ in range(20)],
            },
        }

        compiled = compile_prompt(task, "research", "原始提示词", readonly=True)
        envelope = compiled.split("--- 原始步骤提示词（保持原意） ---", 1)[0]

        self.assertLess(len(envelope), 7000)
        self.assertIn("原始提示词", compiled)

    def test_intent_context_is_not_repeated(self):
        task = {
            "type": "code",
            "intent_contract": {"must": ["先完成诊断"]},
            "context": "## 用户意图契约（优先级高于默认做法，必须逐项核对）\n- 先完成诊断",
        }
        compiled = compile_prompt(task, "implement", task["context"], readonly=False)

        self.assertEqual(compiled.count("先完成诊断"), 1)
        self.assertIn("不重复改写其条目", compiled)

    def test_specialized_roles_get_their_quality_phase(self):
        task = {"type": "serial_novel"}
        for role in ("signing-eval", "race-select", "bestof-select", "verify", "score"):
            with self.subTest(role=role):
                self.assertIn("当前阶段：评审/验收", compile_prompt(task, role, "执行"))

    def test_planner_side_calls_use_the_same_contract(self):
        task = {
            "type": "code",
            "goal": "修复构建失败",
            "intent_contract": {"must": ["先定位真实错误"]},
        }
        compiled = _compile_planning_prompt(task, "plan", "只输出计划 JSON")

        self.assertIn("## CodeBee 下发契约（v1）", compiled)
        self.assertIn("当前阶段：规划/讨论", compiled)
        self.assertIn("先定位真实错误", compiled)
        self.assertIn("原始步骤提示词结束", compiled)


if __name__ == "__main__":
    unittest.main()
