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
        self.assertIn("非硬门槛", first)
        self.assertIn("不因未在固定字数内", first)
        self.assertIn("不强制每章反转", later)
        self.assertIn("继续阅读理由", later)


    def test_signal_summary_is_deterministic_and_actionable(self):
        text = "他推门而入。\n\n‘你终于来了。’她握紧刀柄。"
        result = signal_summary(text, chapter=1)
        self.assertGreater(result["chars"], 0)
        self.assertEqual(result["paragraphs"], 2)
        self.assertGreater(result["dialogue_chars"], 0)
        self.assertGreater(result["avg_sentence_chars"], 0)
        self.assertIsInstance(result["hints"], list)

    def test_no_dialogue_or_emotion_words_is_not_a_quality_warning(self):
        result = signal_summary("雪落在空院里。", chapter=1)
        self.assertEqual(result["hints"], [])


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


class StyleConsistencyTests(unittest.TestCase):
    """跨章/章内体例一致性检测（2026-10-15 七猫双拒实案校准）。

    神瞳 ch05 整章「」与其余章 ASCII 直引号混用、头七 ch02 弯直混用+BOM，
    平台按「风格不统一」拒——检测须确定性命中且分档 major。
    """

    def _ascii_ch(self):
        return '# 第N章 测试\n\n他说"东西不对，账要重算"，说完便坐下。\n她答"好，我等着"。\n他又补了一句"别等太久"。\n'

    def _corner_ch(self):
        return '# 第N章 测试\n\n他说「东西不对，账要重算」，说完便坐下。\n她答「好，我等着」。\n他又补了一句「别等太久」。\n'

    def test_uniform_style_passes(self):
        from core.novel_quality import style_consistency_notes
        chapters = {i: self._corner_ch() for i in range(1, 5)}
        self.assertEqual(style_consistency_notes(chapters), [])

    def test_cross_chapter_mix_is_major_and_names_stray_chapter(self):
        from core.novel_quality import style_consistency_notes
        chapters = {i: self._ascii_ch() for i in range(1, 5)}
        chapters[5] = self._corner_ch()          # 神瞳 ch05 实案形态
        notes = style_consistency_notes(chapters)
        cross = [n for n in notes if "跨章混用" in n["note"]]
        self.assertEqual(len(cross), 1)
        self.assertEqual(cross[0]["severity"], "major")
        self.assertIsNone(cross[0]["chapter"])    # 全书级账，note 点名病灶章
        self.assertIn("第 5 章", cross[0]["note"])
        # 证据句定位到病灶章的对白而非章首
        self.assertIn("「", cross[0]["quote"])
        self.assertNotIn("#", cross[0]["quote"][:2])

    def test_intra_chapter_mix_is_major(self):
        from core.novel_quality import style_consistency_notes
        mixed = self._corner_ch() + '\n他说"这句话用直引号"，前后都是直角引号。\n她又说了"第二句"作证。\n最后一句"收尾"。\n'
        chapters = {i: self._corner_ch() for i in range(1, 4)}
        chapters[2] = mixed                       # 头七 ch02 实案形态
        notes = style_consistency_notes(chapters)
        intra = [n for n in notes if "章内混用" in n["note"]]
        self.assertEqual(len(intra), 1)
        self.assertEqual(intra[0]["severity"], "major")
        self.assertEqual(intra[0]["chapter"], 2)

    def test_bom_is_flagged(self):
        from core.novel_quality import style_consistency_notes
        chapters = {i: self._corner_ch() for i in range(1, 4)}
        chapters[2] = "\ufeff" + self._corner_ch()
        notes = style_consistency_notes(chapters)
        self.assertTrue(any("BOM" in n["note"] and n["severity"] == "major"
                            for n in notes))

    def test_few_quotes_do_not_trigger_cross_chapter(self):
        from core.novel_quality import style_consistency_notes
        # 对白稀少的章节（旁白章一两句）不该被判「用错了制式」
        chapters = {i: self._ascii_ch() for i in range(1, 5)}
        chapters[3] = '# 第3章\n\n他整夜没说话，天亮时才合眼。\n'
        self.assertEqual(style_consistency_notes(chapters), [])
