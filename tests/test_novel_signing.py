# -*- coding: utf-8 -*-
"""签约向连载链回归（2026-09-28 《戍边骑奴》驳回案）：

锁定五个行为：
1. 大纲产出文风锚与逐章爆点（style_anchor / highlight 进 _norm_chapters）；
2. 续写批次沿 serial.continues 链回查前批文风锚；
3. 起草/返修/签约评估三面提示词带文风锚与爆点占位符；
4. 开篇闸门：第 1 章签约评估「开篇吸引力」未达线 → 回大纲层重设计前三章
   → 修订后仍不达标 → 停批止损（开篇失败=全书判死）；
5. 开篇达线时闸门零干预，正常收尾并记录评估结果。
"""
from __future__ import annotations

import json
from unittest.mock import patch

from base import BaseTest


def _res(text="", ok=True, error=""):
    return {"ok": ok, "text": text, "json": None, "cost_usd": 0.0,
            "tokens": 0, "error": error, "raw": {}, "sid": ""}


def _critique_json(scores, summary="ok", issues=None):
    return _res(json.dumps({"scores": scores, "issues": issues or [],
                            "summary": summary}, ensure_ascii=False))


SIGNING_SCORES = {"开篇吸引力": 8.0, "情节推进": 8.0, "文风统一": 8.0,
                  "情感细腻度": 8.0, "节奏控制": 8.0}
TASK_DIM_SCORES = {"情节": 8.0, "人物": 8.0, "文笔": 8.0, "节奏": 8.0, "吸引力": 8.0}


class OutlineSigningFieldsTests(BaseTest):
    def test_norm_chapters_extracts_style_anchor_and_highlight(self):
        from app.core import planner
        out = planner._norm_chapters({
            "book_title": "测试书", "style_anchor": "第三人称限制视角，短句白描",
            "chapters": [{"title": "第一章", "beats": "开局冲突", "hook": "钩子",
                          "highlight": "主角当众反转"}],
        }, 1)
        self.assertEqual(out["style_anchor"], "第三人称限制视角，短句白描")
        self.assertEqual(out["chapters"][0]["highlight"], "主角当众反转")

    def test_norm_chapters_defaults_missing_fields(self):
        from app.core import planner
        out = planner._norm_chapters({
            "book_title": "测试书",
            "chapters": [{"title": "第一章", "beats": "开局冲突", "hook": "钩子"}],
        }, 1)
        self.assertNotIn("style_anchor", out)
        self.assertEqual(out["chapters"][0]["highlight"], "")

    def test_merge_opening_replaces_only_head_chapters(self):
        from app.core import planner
        outline = {"book_title": "测试书", "chapters": [
            {"title": "第一章", "beats": "旧节拍1", "hook": "钩1", "highlight": ""},
            {"title": "第二章", "beats": "旧节拍2", "hook": "钩2", "highlight": ""},
            {"title": "第三章", "beats": "旧节拍3", "hook": "钩3", "highlight": ""},
            {"title": "第四章", "beats": "旧节拍4", "hook": "钩4", "highlight": ""},
        ]}
        merged = planner.merge_opening(outline, {"chapters": [
            {"title": "新一章", "beats": "新节拍1", "hook": "新钩1", "highlight": "爆1"},
            {"title": "新二章", "beats": "新节拍2", "hook": "新钩2", "highlight": "爆2"},
        ]}, count=2)
        self.assertIsNotNone(merged)
        self.assertEqual(outline["chapters"][0]["beats"], "新节拍1")
        self.assertEqual(outline["chapters"][1]["highlight"], "爆2")
        self.assertEqual(outline["chapters"][2]["beats"], "旧节拍3")
        self.assertEqual(outline["chapters"][3]["beats"], "旧节拍4")

    def test_merge_opening_rejects_empty_beats_and_garbage(self):
        from app.core import planner
        outline = {"chapters": [{"title": "第一章", "beats": "旧", "hook": "", "highlight": ""}]}
        self.assertIsNone(planner.merge_opening(outline, None))
        self.assertIsNone(planner.merge_opening(outline, {"chapters": [{"title": "x"}]}))
        self.assertIsNone(planner.merge_opening(None, {"chapters": []}))

    def test_prev_style_anchor_walks_chain(self):
        from app.core import planner, store
        first = store.create_task({
            "type": "serial_novel", "title": "第一批", "goal": "写书",
            "workdir": str(self.workdir), "serial": {"chapters": 2},
        })
        run = store.create_run("orchestration", first["title"], task_id=first["id"])
        store.update_run(run["id"], outline={
            "book_title": "锚书", "style_anchor": "第一人称，短句",
            "chapters": [{"title": "第一章", "beats": "b", "hook": "h", "highlight": ""}]})
        second = store.create_task({
            "type": "serial_novel", "title": "第二批", "goal": "写书",
            "workdir": str(self.workdir), "serial": {"chapters": 2, "continues": first["id"]},
        })
        self.assertEqual(planner.prev_style_anchor(second), "第一人称，短句")
        self.assertEqual(planner.prev_style_anchor(first), "")


class SigningPromptContractTests(BaseTest):
    def test_chapter_prompt_carries_style_and_highlight(self):
        from app.core import pipeline
        tpl = pipeline.SERIAL_CHAPTER_PROMPT
        self.assertIn("__STYLE__", tpl)
        self.assertIn("__HIGHLIGHT__", tpl)
        self.assertIn("特写镜头", tpl)

    def test_revise_prompt_carries_beats_style_highlight(self):
        from app.core import pipeline
        tpl = pipeline.SERIAL_REVISE_PROMPT
        for ph in ("__BEATS__", "__HIGHLIGHT__", "__STYLE__", "__CRITIQUE__"):
            self.assertIn(ph, tpl)
        self.assertIn("特写镜头", tpl)

    def test_signing_eval_prompt_covers_rejection_talk_tracks(self):
        from app.core import pipeline
        tpl = pipeline.SERIAL_SIGNING_EVAL_PROMPT
        self.assertIn("__DIMKEYS__", tpl)
        self.assertIn("__MANUSCRIPT__", tpl)
        self.assertIn("__NOTE__", tpl)
        for dim in ("开篇吸引力", "情节推进", "文风统一", "情感细腻度", "节奏控制"):
            self.assertIn(dim, tpl)

    def test_style_anchor_block_prefers_outline_then_fallback(self):
        from app.core import novel_quality
        block = novel_quality.style_anchor_block("短句白描")
        self.assertIn("短句白描", block)
        self.assertEqual(novel_quality.style_anchor_block(""), "")


def _serial_task(workdir, chapters=1, threshold=5.0):
    from app.core import store
    return store.create_task({
        "type": "serial_novel", "title": "签约闸门回归", "goal": "写一本书",
        "workdir": str(workdir), "threshold": threshold,
        "serial": {"chapters": chapters, "words_per_chapter": 300},
    })


def _agents():
    impl = {"id": "author-a", "label": "Author A", "kind": "codex",
            "mode": "real", "command": "a"}
    critic = {"id": "c1", "label": "Critic", "kind": "generic",
              "mode": "real", "command": "x"}
    return [impl, critic], [critic], impl


class TestOpeningGate(BaseTest):
    OUTLINE = {"book_title": "闸门书", "source": "llm(fake)",
               "style_anchor": "第三人称限制视角，短句白描",
               "chapters": [{"title": "第一章", "beats": "开局冲突", "hook": "钩子",
                             "highlight": "主角当众反转"}]}

    def _run(self, task, run, agents, critics, impl, fake_step):
        from app.core import pipeline
        orig_step = pipeline._run_step
        orig_outline = pipeline.planner.make_serial_outline
        pipeline._run_step = fake_step
        pipeline.planner.make_serial_outline = lambda *a, **k: dict(self.OUTLINE)
        try:
            return pipeline._run_serial_review(
                run, task, agents, None, {}, "manual", critics, impl, {}, None, "default")
        finally:
            pipeline._run_step = orig_step
            pipeline.planner.make_serial_outline = orig_outline

    def test_below_line_rebuilds_then_aborts(self):
        """开篇吸引力两轮未达线：重设计被调用 → 停批，错误点名开篇闸门。"""
        from app.core import pipeline, store
        task = _serial_task(str(self.workdir), chapters=1)
        run = store.create_run("orchestration", task["title"], task_id=task["id"])
        store.update_run(run["id"], status="running")
        agents, critics, impl = _agents()
        rebuild_calls = []

        def fake_redesign(t, outline=None, critique="", **kw):
            rebuild_calls.append(critique)
            outline["chapters"][0]["beats"] = "重设计后的节拍"
            return outline

        def fake_step(run_id, role, agent, prompt, workdir, readonly, ev, *a, **kw):
            if role == "draft-c1":
                pipeline._write_chapter(workdir, 1, "山" * 300)
                return _res("第 1 章完成")
            if role == "signing-eval":
                low = dict(SIGNING_SCORES, 开篇吸引力=4.0)
                return _critique_json(low, summary="开篇平淡，切入点缺吸引力")
            if role.startswith("critique-c"):
                return _critique_json(TASK_DIM_SCORES)
            return _res()

        with patch.object(pipeline.planner, "redesign_opening", side_effect=fake_redesign), \
                patch.object(pipeline.time, "sleep", return_value=None):
            self._run(task, run, agents, critics, impl, fake_step)

        saved = store.get_run(run["id"])
        self.assertEqual(saved["status"], "failed", saved.get("error"))
        self.assertIn("开篇闸门", saved.get("error") or "")
        self.assertIn("开篇吸引力", saved.get("error") or "")
        self.assertEqual(len(rebuild_calls), 1)   # 重设计一次，终判前不再重复
        evals = saved.get("signing_evals") or {}
        self.assertIn("opening_gate", evals)
        self.assertTrue(evals["opening_gate"]["opening_below_line"])
        self.assertIn("opening_gate_final", evals)

    def test_on_line_gate_is_silent_and_run_completes(self):
        """开篇达线：闸门零干预，run 正常收尾并记录评估结果。"""
        from app.core import pipeline, store
        task = _serial_task(str(self.workdir), chapters=1)
        run = store.create_run("orchestration", task["title"], task_id=task["id"])
        store.update_run(run["id"], status="running")
        agents, critics, impl = _agents()
        rebuild_calls = []

        def fake_step(run_id, role, agent, prompt, workdir, readonly, ev, *a, **kw):
            if role == "draft-c1":
                pipeline._write_chapter(workdir, 1, "山" * 300)
                return _res("第 1 章完成")
            if role == "signing-eval":
                return _critique_json(SIGNING_SCORES, summary="开篇抓人，可送签")
            if role.startswith("critique-c"):
                return _critique_json(TASK_DIM_SCORES)
            if role == "global-critique":
                return _critique_json(TASK_DIM_SCORES, summary="达到可签约水平")
            return _res()

        with patch.object(pipeline.planner, "redesign_opening",
                          side_effect=lambda *a, **k: rebuild_calls.append(1)):
            self._run(task, run, agents, critics, impl, fake_step)

        saved = store.get_run(run["id"])
        self.assertEqual(saved["status"], "done", saved.get("error"))
        self.assertEqual(rebuild_calls, [])
        evals = saved.get("signing_evals") or {}
        self.assertFalse(evals.get("opening_gate", {}).get("opening_below_line", True))

    def test_gate_disabled_by_serial_flag(self):
        """serial.opening_gate=false：不做签约评估，老链路行为不变。"""
        from app.core import pipeline, store
        task = _serial_task(str(self.workdir), chapters=1)
        task = dict(task)
        task["serial"] = dict(task["serial"], opening_gate=False)
        run = store.create_run("orchestration", task["title"], task_id=task["id"])
        store.update_run(run["id"], status="running")
        agents, critics, impl = _agents()
        signing_calls = []

        def fake_step(run_id, role, agent, prompt, workdir, readonly, ev, *a, **kw):
            if role == "draft-c1":
                pipeline._write_chapter(workdir, 1, "山" * 300)
                return _res("第 1 章完成")
            if role == "signing-eval":
                signing_calls.append(role)
                return _critique_json(dict(SIGNING_SCORES, 开篇吸引力=2.0))
            if role.startswith("critique-c"):
                return _critique_json(TASK_DIM_SCORES)
            if role == "global-critique":
                return _critique_json(TASK_DIM_SCORES, summary="达到可签约水平")
            return _res()

        self._run(task, run, agents, critics, impl, fake_step)
        saved = store.get_run(run["id"])
        self.assertEqual(saved["status"], "done", saved.get("error"))
        self.assertEqual(signing_calls, [])
