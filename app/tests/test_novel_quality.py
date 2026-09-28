import unittest

from core.novel_quality import (
    OPENING_DIM,
    SIGNING_RUBRIC,
    opening_below,
    opening_requirements,
    rubric_for,
    signal_summary,
    style_anchor_block,
    style_deviation_notes,
)


class NovelQualityTests(unittest.TestCase):
    def test_signing_rubric_covers_rejection_dimensions(self):
        joined = " ".join(SIGNING_RUBRIC)
        for keyword in ("开篇吸引力", "情节推进", "文风统一", "情感细腻度", "节奏控制"):
            self.assertIn(keyword, joined)


    def test_serial_novel_uses_signing_rubric_when_not_customized(self):
        self.assertEqual(rubric_for({"type": "serial_novel"}), list(SIGNING_RUBRIC))


    def test_custom_rubric_is_preserved(self):
        self.assertEqual(rubric_for({"type": "serial_novel", "rubric": ["自定义", "结构"]}), [
            "自定义",
            "结构",
        ])


    def test_opening_requirements_only_apply_to_first_chapter(self):
        first = opening_requirements(1)
        later = opening_requirements(2)
        self.assertIn("前 100 字", first)
        self.assertIn("前 600 字", first)
        self.assertNotIn("前 100 字", later)
        self.assertIn("章末钩子", later)


    def test_signal_summary_is_deterministic_and_actionable(self):
        text = "他推门而入。\n\n‘你终于来了。’她握紧刀柄。"
        result = signal_summary(text, chapter=1)
        self.assertGreater(result["chars"], 0)
        self.assertEqual(result["paragraphs"], 2)
        self.assertGreater(result["dialogue_chars"], 0)
        self.assertGreater(result["avg_sentence_chars"], 0)
        self.assertIsInstance(result["hints"], list)


class StyleAnchorTests(unittest.TestCase):
    def test_anchor_block_renders_and_requires(self):
        block = style_anchor_block("第一人称短句白描，禁网络梗")
        self.assertIn("全书文风锚", block)
        self.assertIn("第一人称短句白描", block)
        self.assertEqual(style_anchor_block(""), "")
        self.assertEqual(style_anchor_block(None), "")


class StyleFingerprintTests(unittest.TestCase):
    @staticmethod
    def _summary(avg, dialog_per_k, emotion_per_k, chars=2000):
        return {
            "chars": chars,
            "avg_sentence_chars": avg,
            "dialogue_chars": round(dialog_per_k * chars / 1000.0),
            "emotion_action_hits": round(emotion_per_k * chars / 1000.0),
        }

    def test_no_notes_without_stable_baseline(self):
        cur = self._summary(18, 200, 5)
        self.assertEqual(style_deviation_notes([], cur), [])
        self.assertEqual(style_deviation_notes([cur], cur), [])

    def test_stable_style_produces_no_notes(self):
        base = self._summary(18, 200, 5)
        cur = self._summary(19, 190, 5)
        self.assertEqual(style_deviation_notes([base, base, base], cur), [])

    def test_sentence_length_drift_is_flagged(self):
        base = self._summary(18, 200, 5)
        long_winded = self._summary(40, 200, 5)
        notes = style_deviation_notes([base, base, base], long_winded)
        self.assertTrue(any("句式明显变长" in n for n in notes))
        choppy = self._summary(6, 200, 5)
        notes = style_deviation_notes([base, base, base], choppy)
        self.assertTrue(any("句式明显变碎" in n for n in notes))

    def test_dialogue_and_emotion_collapse_are_flagged(self):
        base = self._summary(18, 300, 6)
        flat = self._summary(18, 60, 1)
        notes = style_deviation_notes([base, base, base], flat)
        self.assertTrue(any("对白占比骤降" in n for n in notes))
        self.assertTrue(any("情绪动作密度骤降" in n for n in notes))


class OpeningGateTests(unittest.TestCase):
    def test_opening_dim_is_part_of_signing_rubric(self):
        self.assertIn(OPENING_DIM, SIGNING_RUBRIC)

    def test_opening_below_line(self):
        self.assertTrue(opening_below({OPENING_DIM: 4.0}, 5.0))
        self.assertFalse(opening_below({OPENING_DIM: 5.0}, 5.0))
        self.assertFalse(opening_below({}, 5.0))
        self.assertFalse(opening_below({OPENING_DIM: "abc"}, 5.0))
        self.assertFalse(opening_below({OPENING_DIM: 4.0}, None))
