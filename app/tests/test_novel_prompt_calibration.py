from pathlib import Path
import unittest


ROOT = Path(__file__).resolve().parents[1]


class NovelPromptCalibrationTests(unittest.TestCase):
    def test_outline_prompt_uses_observation_points_not_universal_word_gates(self):
        source = (ROOT / "core" / "planner.py").read_text(encoding="utf-8")
        prompt = source.split("SERIAL_OUTLINE_PROMPT = \"\"\"", 1)[1].split("\"\"\"", 1)[0]

        self.assertIn("不为数字机械塞冲突", prompt)
        self.assertNotIn("每章必须给出 highlight", prompt)
        self.assertNotIn("每章都写清", prompt)
        self.assertNotIn("黄金三章", prompt)


    def test_signing_eval_does_not_claim_to_know_platform_real_scale(self):
        source = (ROOT / "core" / "pipeline.py").read_text(encoding="utf-8")
        prompt = source.split("SERIAL_SIGNING_EVAL_PROMPT = \"\"\"", 1)[1].split("\"\"\"", 1)[0]

        self.assertNotIn("平台真实尺度", prompt)
        self.assertIn("统一评分 rubric", prompt)
        self.assertIn("没有当前平台规则或明确编辑反馈时", prompt)


    def test_opening_rebuild_is_not_limited_to_stock_crisis_beats(self):
        source = (ROOT / "core" / "planner.py").read_text(encoding="utf-8")
        prompt = source.split("SERIAL_OPENING_REBUILD_PROMPT = \"\"\"", 1)[1].split("\"\"\"", 1)[0]

        self.assertIn("不预设危机、金手指、背叛或打脸", prompt)
        self.assertIn("前 100/200/600 字只是阅读观察点，不是硬指标", prompt)
        self.assertIn("不强制悬念", prompt)


if __name__ == "__main__":
    unittest.main()
