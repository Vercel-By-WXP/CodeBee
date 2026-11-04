# -*- coding: utf-8 -*-
"""改稿单（P1-A）单测：拦住之后要说得出「哪一段差什么」。

守五件事：

1. **锚点可核验**：每条都带段号/句号与原句摘录，作者能直接翻到原文那一段。
2. **不猜**：没读到正文时只说章级判据，绝不写「全章没有一处提到东西」——
   那是要通读过全文才能下的结论。
3. **两类实测误判不能回来**（真稿上踩过）：
   * 「天还没黑……塞不进棉鞋」的「还没」管的是天黑，离「棉鞋」二十多字，
     不能算未来语气；
   * 「今天买了纱布」是**过去时**，只是「纱布」不在词表里，不能算未来语气。
4. **有「一句话能改」的锚点时不摆对话铺陈**：否则作者会以为那些也是待改点。
5. **没被拦就整块不出现**：不注入空段，也不给已经过关的章提建议。
"""
from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from core.quality import appeal, revise


def _profile(**over):
    data = {"id": "test", "name": "测试题材", "chapters": 3, "scan_chapters": 6,
            "min_words": 100,
            "promises": ({"id": "loop", "text": "每几章一个小闭环",
                          "kind": "gain", "gain": "any"},)}
    data.update(over)
    return data


def _chapter(text, number=1, title=""):
    return {"chapter": number, "title": title, "text": text}


class ClassifyTest(unittest.TestCase):
    """单句分类：每一类都要能被原句直接证伪。"""

    def _marks(self, sentence):
        hit = revise._classify(sentence)
        return hit[0] if hit else None

    def test_negated_action_is_negated(self):
        # 「没有把猎物抬上爬犁」——动作写了但被否掉，改半句就能兑现。
        self.assertEqual(self._marks("他没有把猎物抬上爬犁，只记下了时间。"), "negated")

    def test_plan_is_pending(self):
        # 「拟」是强标记，吊到句尾也算：这整句就是在说「还没做」。
        self.assertEqual(
            self._marks("沈葵在本子上添了一行：拟以腌肉一块换木料一根，尺寸与交割待明日确认。"),
            "pending")

    def test_far_away_marker_is_not_pending(self):
        # 真稿第 2 章 P1：这里「还没」管的是「天黑」，与棉鞋无关。
        self.assertEqual(
            self._marks("回到柳河屯时，天还没黑，石头的脚踝已经肿得塞不进棉鞋。"), "bare")

    def test_past_tense_purchase_is_not_pending(self):
        # 真稿第 2 章 P12：「今天买了纱布」是过去时。原先因为句中有「买了」
        # 就判成未来语气，等于让作者去改一件已经发生的事。
        self.assertEqual(
            self._marks("她先把三十一元写在最上面，又划掉一元：“今天买了纱布。"), "bare")

    def test_realized_sentence_is_not_an_anchor(self):
        self.assertIsNone(self._marks("沈砚把卖肉拿到的七十六元揣进怀里。"))

    def test_sentence_without_thing_is_ignored(self):
        self.assertIsNone(self._marks("他把时间、方位和自己看不准的地方记在背面。"))


class AnchorsTest(unittest.TestCase):
    def test_cheap_anchors_win_over_mentions(self):
        text = ("沈砚没有抬头：“一头？”\n\n"
                "野猪的脚印一直延伸到旧线。\n\n"
                "沈葵在本子上添了一行：拟以腌肉一块换木料一根，待明日确认。\n")
        rows, total = revise.anchors_of(text)
        self.assertGreaterEqual(total, 3)
        # 只列能一句话改的那条，不把两句对话铺陈摆出来。
        self.assertEqual([r["paragraph"] for r in rows], [3])
        self.assertEqual(rows[0]["mark"], "pending")

    def test_only_two_bare_anchors_when_nothing_cheaper(self):
        text = "\n\n".join("野猪的痕迹在第%d段。" % i for i in range(1, 7))
        rows, total = revise.anchors_of(text)
        self.assertEqual(total, 6)
        self.assertEqual(len(rows), 2)

    def test_anchor_carries_paragraph_sentence_and_quote(self):
        rows, _ = revise.anchors_of("开头一句。\n\n沈砚没有把猎物抬上爬犁。\n")
        self.assertEqual((rows[0]["paragraph"], rows[0]["sentence"]), (2, 1))
        self.assertIn("没有把猎物抬上爬犁", rows[0]["text"])


class BuildTest(unittest.TestCase):
    def test_no_blocker_means_empty_brief(self):
        result = {"enabled": True, "genre": "hunt_1980s", "blockers": [],
                  "chapters": [{"chapter": 1, "words": 2000, "blockers": []}]}
        brief = revise.build(result)
        self.assertFalse(brief["enabled"])
        self.assertEqual(revise.render(brief), "")

    def test_only_blocked_chapters_get_anchors(self):
        result = {
            "enabled": True, "genre": "hunt_1980s",
            "blockers": ["第 1 章没有兑现任何「获得」"],
            "chapters": [
                {"chapter": 1, "title": "醒来", "words": 2000,
                 "blockers": ["第 1 章没有兑现任何「获得」"]},
                {"chapter": 2, "title": "空手", "words": 2100, "blockers": []},
            ],
        }
        with tempfile.TemporaryDirectory() as tmp:
            task = {"type": "serial_novel", "workdir": tmp,
                    "appeal_genre": "hunt_1980s"}
            brief = revise.build(result, task)
        self.assertEqual([c["chapter"] for c in brief["chapters"]], [1])

    def test_missing_text_is_declared_not_guessed(self):
        """没有正文时说「没读到」，不许说「全章没有一处提到东西」。"""
        result = {"enabled": True, "genre": "hunt_1980s",
                  "blockers": ["第 1 章没有兑现任何「获得」"],
                  "chapters": [{"chapter": 1, "words": 2000,
                                "blockers": ["第 1 章没有兑现任何「获得」"]}]}
        brief = revise.build(result)  # 不传 task，拿不到正文
        chapter = brief["chapters"][0]
        self.assertFalse(chapter["located"])
        text = revise.render(brief)
        self.assertIn("未读到本章正文", text)
        self.assertNotIn("全章没有一处提到", text)
        self.assertNotIn("全章提到", text)

    def test_brief_reads_chapters_from_workdir(self):
        """正文按章号从 workdir 现读：与判据读的是同一批文件。"""
        result = {"enabled": True, "genre": "hunt_1980s",
                  "blockers": ["第 1 章没有兑现任何「获得」"],
                  "chapters": [{"chapter": 1, "words": 2000,
                                "blockers": ["第 1 章没有兑现任何「获得」"]}]}
        with tempfile.TemporaryDirectory() as tmp:
            (Path(tmp) / "chapter-01.md").write_text(
                "沈砚没有抬头：“一头？”\n\n拟以腌肉一块换木料一根，待明日确认。\n",
                encoding="utf-8")
            brief = revise.build(result, {"workdir": tmp})
        chapter = brief["chapters"][0]
        self.assertTrue(chapter["located"])
        self.assertEqual(chapter["paragraphs"], 2)
        self.assertEqual(chapter["anchors"][0]["mark"], "pending")

    def test_closing_pending_is_reported(self):
        text = "猎物在旧线以北。\n\n若明天雪停，再决定要不要进山。\n"
        chapter = revise._closing(text)
        self.assertTrue(chapter["pending"])
        self.assertEqual(chapter["paragraph"], 2)

    def test_merged_manuscript_also_yields_anchors(self):
        """前几章在合稿里时，改稿单同样要拿得到原文（P2-E：两套读法会分叉）。"""
        result = {"enabled": True, "genre": "hunt_1980s",
                  "blockers": ["第 1 章没有兑现任何「获得」"],
                  "chapters": [{"chapter": 1, "words": 2000,
                                "blockers": ["第 1 章没有兑现任何「获得」"]}]}
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            # 根目录只有第 21 章起；前二十章在「已成稿」的合稿里，标题不带 `#`。
            (root / "chapter-21.md").write_text("沈砚看着任务单。\n", encoding="utf-8")
            (root / "已成稿").mkdir()
            body = ("第一章 忙完这阵\n\n猎物在旧线以北。\n\n拟以腌肉一块换木料一根，待明日确认。\n\n"
                    + "他把门闩插好，回身看见灶台上的灯还亮着。\n\n" * 40
                    + "第二章 送到医院\n\n" + "沈砚没有抬头。\n\n" * 40)
            (root / "已成稿" / "前二十章.md").write_text(body, encoding="utf-8")
            brief = revise.build(result, {"workdir": str(root)})
        chapter = brief["chapters"][0]
        self.assertTrue(chapter["located"])
        self.assertEqual(chapter["anchors"][0]["mark"], "pending")


class BlockForTest(unittest.TestCase):
    """提示词入口：有拦截才注入，没拦截一个字都不出现。"""

    def _task(self, tmp, body):
        root = Path(tmp)
        (root / "chapter-01.md").write_text(body, encoding="utf-8")
        return {"id": "t1", "type": "serial_novel", "workdir": str(root),
                "appeal_genre": "hunt_1980s"}

    def test_block_present_when_chapter_has_no_gain(self):
        with tempfile.TemporaryDirectory() as tmp:
            task = self._task(tmp, "沈砚看着任务单，没有说话。\n" * 30)
            text = revise.block_for(task)
        self.assertIn("改稿单", text)
        self.assertIn("第 1 章", text)

    def test_block_absent_when_gates_pass(self):
        with tempfile.TemporaryDirectory() as tmp:
            task = self._task(tmp, "沈砚一枪放倒那头野猪，把猎物抬上爬犁。\n" * 30)
            self.assertEqual(revise.block_for(task), "")

    def test_block_absent_for_non_novel(self):
        with tempfile.TemporaryDirectory() as tmp:
            task = self._task(tmp, "沈砚看着任务单，没有说话。\n" * 30)
            task["type"] = "code"
            self.assertEqual(revise.block_for(task), "")

    def test_block_absent_without_workdir(self):
        self.assertEqual(revise.block_for({"type": "serial_novel"}), "")

    def test_block_is_capped(self):
        with tempfile.TemporaryDirectory() as tmp:
            body = "\n\n".join("沈砚看着第%d段的任务单，没有说话，也没有动。" % i
                               for i in range(1, 80))
            task = self._task(tmp, body)
            text = revise.block_for(task, max_chars=200)
        self.assertLessEqual(len(text), 260)
        self.assertIn("已截断", text)


class RenderTest(unittest.TestCase):
    def test_headline_lists_blocked_chapters(self):
        brief = {"chapters": [{"chapter": 1}, {"chapter": 2}]}
        self.assertIn("第 1、2 章", revise.headline(brief))

    def test_render_mentions_total_mentions(self):
        brief = {"chapters": [{"chapter": 1, "title": "", "words": 2000,
                               "paragraphs": 3, "located": True,
                               "anchors": [{"paragraph": 1, "sentence": 1,
                                            "mark": "bare",
                                            "mark_text": "只提到东西，没有兑现动作",
                                            "text": "野猪在北边。", "action": "",
                                            "gain": "hunt", "gain_label": "猎获"}],
                               "anchor_total": 17,
                               "closing": {}, "advice": ["补一次兑现。"]}]}
        text = revise.render(brief)
        self.assertIn("17 处", text)
        self.assertIn("P1 S1", text)
        self.assertIn("补一次兑现", text)


if __name__ == "__main__":
    unittest.main()
