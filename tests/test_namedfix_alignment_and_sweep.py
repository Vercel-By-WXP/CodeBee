# -*- coding: utf-8 -*-
"""点名修两防线回归（2026-10-22 神瞳 0049 实案）。

实案：任务意图契约写「修订第49章，其余41-48/50一字不动」，步骤模板却写
「修订 chapter-41.md」——CLI 按契约改写 chapter-49.md 并用 Bash 直写
.codebee/chapters/0049/prose.md，管线账面停在 ch41，ch49 新稿无人回登记，
下一轮 run 开场漂移闸连环拒跑。

防线A（模板对齐）：修订/起草提示词写明「对象以本步骤 __FILE__ 为准」，
整批分工表述不再诱导 CLI 改写账面外章稿。
防线B（步内对账）：_sweep_chapter_registrations 在动稿步骤后按磁盘回登记
（reconcile_prose 口径），账面外章号写进 story_tracking_warning 留痕。
"""
import json
import tempfile
import unittest
from pathlib import Path

from app.core import pipeline
from app.core import pipeline_content
from app.core import story_tracking


class TemplateAlignmentTests(unittest.TestCase):
    """防线A：模板必须把修订/写作对象钉在本步骤文件上。"""

    def test_revise_prompt_pins_step_file(self):
        p = pipeline_content.SERIAL_REVISE_PROMPT
        self.assertIn("以本步骤为准", p)
        self.assertIn("唯一允许改写", p)
        self.assertIn("manuscript.md", p)

    def test_revise_prompt_renders_file_and_goal_together(self):
        # 冲突现场还原：账面章 41 + 目标点名修 49，两个信息必须同场且条款在场
        rendered = pipeline._render_template_values(
            pipeline_content.SERIAL_REVISE_PROMPT, {
                "__I__": "41",
                "__FILE__": "chapter-41.md",
                "__GOAL__": "修订并复评第49章（单章点名修），其余41-48、50章一字不动",
                "__VOLUME__": "", "__CONTINUITY__": "", "__BEATS__": "",
                "__LOCKED_CH__": "", "__REVIEW_RUBRIC__": "",
                "__HIGHLIGHT__": "", "__STYLE__": "", "__CRITIQUE__": "",
                "__WORDS__": "4000",
            })
        self.assertIn("chapter-41.md", rendered)
        self.assertIn("修订并复评第49章", rendered)
        self.assertIn("以本步骤为准", rendered)

    def test_draft_prompt_has_scope_redline(self):
        p = pipeline_content.SERIAL_CHAPTER_PROMPT
        self.assertIn("范围红线", p)
        self.assertIn("以本步骤为准", p)


class SweepChapterRegistrationsTests(unittest.TestCase):
    """防线B：步骤后对账回登记 + 账面外改动留痕。"""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.wd = Path(self._tmp.name).resolve()
        story_tracking.init(self.wd, "task-1", "测试故事")
        story_tracking.commit_chapter(self.wd, 1, "第一章大纲",
                                      "第一章正文甲\n第一章正文乙")
        story_tracking.commit_chapter(self.wd, 2, "第二章大纲",
                                      "第二章正文甲\n第二章正文乙")
        self.warnings = []
        self._orig_update = pipeline.store.update_run

        def _capture(run_id, **kw):
            if "story_tracking_warning" in kw:
                self.warnings.append(str(kw["story_tracking_warning"]))
            return self._orig_update(run_id, **kw)

        pipeline.store.update_run = _capture

    def tearDown(self):
        pipeline.store.update_run = self._orig_update
        self._tmp.cleanup()

    def _prose(self, n):
        return (self.wd / ".codebee" / "chapters" / ("%04d" % n) / "prose.md")

    def test_out_of_step_edit_is_healed_and_warned(self):
        # 实案还原：CLI 账面在 ch1，却按契约直写了 ch2 的章稿副本
        self._prose(2).write_text("第二章正文甲\n被顺手改写的新稿\n", encoding="utf-8")
        self.assertFalse(story_tracking.check(self.wd).get("ok"))
        healed = pipeline._sweep_chapter_registrations(
            "run-x", str(self.wd), 1, "章节修订")
        self.assertEqual(healed, [2])
        self.assertTrue(story_tracking.check(self.wd).get("ok"))
        self.assertEqual(len(self.warnings), 1)
        self.assertIn("章节修订", self.warnings[0])
        self.assertIn("第0002章", self.warnings[0])
        self.assertIn("第0001章", self.warnings[0])   # 账面章也在文案里可对照

    def test_nominal_only_drift_heals_without_warning(self):
        # 账面章自身被 CLI 同步副本属已知行为：静默回登记，不留痕
        self._prose(1).write_text("第一章正文甲\n修订后的新稿\n", encoding="utf-8")
        healed = pipeline._sweep_chapter_registrations(
            "run-x", str(self.wd), 1, "章节验收")
        self.assertEqual(healed, [1])
        self.assertTrue(story_tracking.check(self.wd).get("ok"))
        self.assertEqual(self.warnings, [])

    def test_clean_workdir_is_noop(self):
        healed = pipeline._sweep_chapter_registrations(
            "run-x", str(self.wd), 1, "章节修订")
        self.assertEqual(healed, [])
        self.assertEqual(self.warnings, [])
        # 幂等：连扫两次结果一致
        healed2 = pipeline._sweep_chapter_registrations(
            "run-x", str(self.wd), 1, "章节修订")
        self.assertEqual(healed2, [])

    def test_unregistered_chapter_file_is_ignored(self):
        # 未登记章（还没走到验收）不进对账范围，交给 commit_chapter 正常登记
        (self.wd / "chapter-03.md").write_text("第三章全新草稿\n", encoding="utf-8")
        healed = pipeline._sweep_chapter_registrations(
            "run-x", str(self.wd), 3, "章节起草")
        self.assertEqual(healed, [])
        self.assertEqual(self.warnings, [])

    def test_unregistered_chapter_state_entry_skipped(self):
        # 状态里登记了但文件还没落盘（起草中）——对账不误报、不炸
        state = json.loads(
            (self.wd / ".codebee" / "story_tracking.json").read_text("utf-8"))
        state["chapters"].append({"chapter": 9, "outline": "待起草",
                                  "prose_digest": "deadbeef"})
        (self.wd / ".codebee" / "story_tracking.json").write_text(
            json.dumps(state, ensure_ascii=False), encoding="utf-8")
        healed = pipeline._sweep_chapter_registrations(
            "run-x", str(self.wd), 1, "章节修订")
        self.assertEqual(healed, [])
        self.assertTrue(story_tracking.check(self.wd).get("ok") in (True, False))
        # 第9章缺文件仍由 check 的缺文件闸如实拦截，对账不掩盖
        result = story_tracking.check(self.wd)
        self.assertTrue(any("缺失" in e for e in result.get("errors") or []))


if __name__ == "__main__":
    unittest.main()
