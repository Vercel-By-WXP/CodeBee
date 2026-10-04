# -*- coding: utf-8 -*-
"""锁定章纲注入回归（2026-10-03 不周山守村人 rogue 大纲实案）。

批次大纲存在 run.json 继承链里，编排者现场生成时从不读工作目录的章纲/卷纲
文件——起草跟批次大纲、评审跟章纲，两边永久互斥，重写多少轮都拦在同一个
结构性 major 上（36 个 run 实证）。本套锁定三面注入：
1. planner.locked_chapter_outline_text 按章号精确取 章纲/*ch-<N>.md
   （UTF-8/GBK 双兼容），无文件返回空串（老书零噪音）；
2. planner.locked_serial_outline_block 汇总本批各章章纲；一份都没有时回退
   大纲/*.md；两者皆无返回空串；
3. 起草提示词逐章注入「锁定章纲（最高优先级）」块，无章纲的章不注入；
4. 修订/打磨提示词同样注入（修订是结构问题唯一能被修复的回路）；
5. 续写大纲提示词带锁定章纲段，模板占位符不得残留。
"""
from __future__ import annotations

import json

from base import BaseTest


def _ok(text=""):
    return {"ok": True, "text": text, "json": None, "cost_usd": 0.0,
            "tokens": 0, "error": "", "raw": {"exit_code": 0}, "sid": ""}


RUBRIC = {"情节": 9.0, "人物": 9.0, "文笔": 9.0, "节奏": 9.0, "吸引力": 9.0}
SIGN = {"开篇吸引力": 9.0, "情节推进": 9.0, "文风统一": 9.0,
        "情感细腻度": 9.0, "节奏控制": 9.0}


def _j(scores, issues=None):
    return json.dumps({"scores": scores, "issues": issues or [],
                       "summary": "总评"}, ensure_ascii=False)


def _outline(n):
    beats = ["照川核停工范围并在饭桌说清", "许知微夜班复核、两人几句家常",
             "试清露出一截沟口，水位小幅回落"]
    return {"book_title": "锁定章纲回归", "source": "test",
            "chapters": [{"title": "第%d章" % (k + 1), "beats": beats[k % len(beats)],
                          "highlight": "小冲突"} for k in range(n)]}


class TestLockedOutlineHelpers(BaseTest):

    def test_chapter_lookup_utf8_gbk_and_miss(self):
        from app.core import planner
        gang = self.workdir / "章纲"
        gang.mkdir()
        (gang / "vol-1-ch-21.md").write_text(
            "---\ntitle: 停工单上的范围\npov: 陈照川\n---\n## 衔接与任务\n饭桌争执。",
            encoding="utf-8")
        (gang / "vol-1-ch-22.md").write_bytes(
            "## 章内节拍\n许知微夜班接班，空床留给下一位病人。".encode("gbk"))
        t21 = planner.locked_chapter_outline_text(str(self.workdir), 21)
        self.assertIn("停工单上的范围", t21)
        self.assertIn("饭桌争执", t21)
        t22 = planner.locked_chapter_outline_text(str(self.workdir), 22)
        self.assertIn("空床留给下一位病人", t22, "GBK 章纲必须能读（CLI 落盘双形态）")
        self.assertEqual(planner.locked_chapter_outline_text(str(self.workdir), 99), "")
        self.assertEqual(planner.locked_chapter_outline_text("", 21), "")

    def test_block_range_and_datong_fallback(self):
        from app.core import planner
        gang = self.workdir / "章纲"
        gang.mkdir()
        for n in (21, 22):
            (gang / ("vol-1-ch-%d.md" % n)).write_text(
                "第%d章锁定要点：事件ABC。" % n, encoding="utf-8")
        block = planner.locked_serial_outline_block(
            {"workdir": str(self.workdir)}, 21, 28)
        self.assertIn("### 第 21 章锁定章纲", block)
        self.assertIn("### 第 22 章锁定章纲", block)
        self.assertIn("事件ABC", block)
        self.assertNotIn("第 23 章", block, "范围外章纲不得混入")

        # 无逐章章纲 → 回退 大纲/*.md
        import shutil
        empty = self.tmp / "work2"
        empty.mkdir()
        (empty / "大纲").mkdir()
        (empty / "大纲" / "volume-1.md").write_text(
            "# 第一卷：山门换锁\n试清因下游风险中止。", encoding="utf-8")
        fb = planner.locked_serial_outline_block({"workdir": str(empty)}, 21, 28)
        self.assertIn("volume-1.md", fb)
        self.assertIn("试清因下游风险中止", fb)

        # 两者皆无 → 空串（零噪音）
        self.assertEqual(
            planner.locked_serial_outline_block({"workdir": str(self.tmp / "none")}, 1, 8), "")

    def test_total_cap_scales_with_batch_size(self):
        """12 章批（章纲总量 2.4 万字 > 旧固定 16000）不得截尾——尾部章的
        章纲必须完整进注入块（2026-10-04 实案：45—48 章被生成成「待核」）。"""
        from app.core import planner
        gang = self.workdir / "章纲"
        gang.mkdir()
        filler = "章节节拍与红线占位内容，确保每份章纲有真实长度。" * 85   # ≈2040 字
        for n in range(37, 49):
            (gang / ("vol-1-ch-%d.md" % n)).write_text(
                "第%d章锁定要点：%s" % (n, filler), encoding="utf-8")
        block = planner.locked_serial_outline_block(
            {"workdir": str(self.workdir)}, 37, 48)
        self.assertIn("### 第 48 章锁定章纲", block, "尾章章纲不得被固定 cap 截掉")
        self.assertIn("第37章锁定要点", block.replace(" ", ""))
        self.assertGreater(len(block), 16000, "cap 应随批大小放大")


class TestLockedChapterBlockShape(BaseTest):

    def test_block_shape_and_zero_noise(self):
        from app.core import pipeline, planner
        self.assertEqual(pipeline.serial_locked_chapter_block(str(self.workdir), 21), "")
        gang = self.workdir / "章纲"
        gang.mkdir()
        (gang / "vol-1-ch-21.md").write_text("章纲正文XYZ。", encoding="utf-8")
        block = pipeline.serial_locked_chapter_block(str(self.workdir), 21)
        self.assertTrue(block.startswith("## 锁定章纲"))
        self.assertIn("最高优先级", block)
        self.assertIn("章纲正文XYZ。", block)
        # planner 侧与 pipeline 侧读取同一份文件
        self.assertIn("章纲正文XYZ。",
                      planner.locked_chapter_outline_text(str(self.workdir), 21))

    def test_templates_carry_placeholders(self):
        from app.core import pipeline, planner
        self.assertIn("__LOCKED_CH__", pipeline.SERIAL_CHAPTER_PROMPT)
        self.assertIn("__LOCKED_CH__", pipeline.SERIAL_REVISE_PROMPT)
        self.assertIn("__LOCKED_OUTLINE__", planner.SERIAL_CONTINUE_OUTLINE_PROMPT)


class TestDraftAndReviseInjectLockedOutline(BaseTest):
    """真实 impl + fake _run_step：章纲在 workdir 时，起草与修订提示词必须
    带锁定章纲块；占位符不得残留。"""

    def _make(self, chapters=2, start=21):
        from app.core import store
        serial = {"chapters": chapters, "words_per_chapter": 300}
        if start > 1:
            serial["start_chapter"] = start
        task = store.create_task({
            "type": "serial_novel", "title": "锁定章纲回归", "goal": "写两章",
            "workdir": str(self.workdir), "serial": serial,
            "implementer": "impl-a", "critics": ["c1", "c2"]})
        run = store.create_run("orchestration", task["title"], task_id=task["id"])
        store.update_run(run["id"], status="running")
        run = dict(store.get_run(run["id"]))
        run["inherit"] = {"outline": _outline(chapters)}
        critics = [{"id": cid, "mode": "real", "kind": "generic", "command": "x",
                    "label": cid} for cid in ("c1", "c2")]
        impl = {"id": "impl-a", "mode": "real", "kind": "generic", "command": "x",
                "label": "Impl"}
        return task, run, critics + [impl], critics, impl

    def test_draft_and_revise_prompts_carry_locked_outline(self):
        from app.core import pipeline
        gang = self.workdir / "章纲"
        gang.mkdir()
        (gang / "vol-1-ch-21.md").write_text(
            "锁定要点：饭桌维修款争执后 18:45 送县医院。", encoding="utf-8")
        (gang / "vol-1-ch-22.md").write_text(
            "锁定要点：许知微视角夜班，空床实景。", encoding="utf-8")
        # 旧稿在场（重写未达标章场景）：不得被「校验沿用」
        (self.workdir / "chapter-21.md").write_text("旧稿。", encoding="utf-8")

        body = "这是一段足够长的测试正文，主角在核对停工范围。" * 30
        cap = {"draft": {}, "revise": {}}

        def fake(run_id, role, agent, prompt, workdir, readonly, ev, *a, **kw):
            if role.startswith("draft-c"):
                i = int(role.split("draft-c")[1])
                cap["draft"][i] = prompt
                pipeline._write_chapter(str(self.workdir), i, body)
                return _ok("已写")
            if role.startswith(("polish-c", "revise-c")):
                i = int("".join(ch for ch in role if ch.isdigit()))
                cap["revise"][i] = prompt
                pipeline._write_chapter(str(self.workdir), i, body)
                return _ok("已修订")
            if role.startswith("critique-c"):
                return _ok(_j(RUBRIC))
            if role == "signing-eval":
                return _ok(_j(SIGN))
            if role == "global-critique":
                return _ok(_j(RUBRIC))
            return _ok()

        task, run, agents, critics, impl = self._make(chapters=2)
        orig = pipeline._run_step
        pipeline._run_step = fake
        try:
            pipeline._run_serial_review(
                run, task, agents, None, {}, "manual", critics, impl, {}, None, "default")
        finally:
            pipeline._run_step = orig

        self.assertIn(21, cap["draft"], "第21章必须真实起草（fake 代写捕获提示词）")
        d21 = cap["draft"][21]
        self.assertIn("## 锁定章纲", d21, "起草提示词必须注入锁定章纲块")
        self.assertIn("饭桌维修款争执", d21, "锁定章纲正文必须随提示词下发")
        self.assertIn("18:45", d21)
        self.assertIn("未通过评审", d21, "旧稿在场时必须明示未过审、要求覆盖重写")
        self.assertIn("覆盖重写", d21)
        d22 = cap["draft"].get(22) or ""
        self.assertNotIn("未通过评审", d22, "无旧稿的章不得误带覆盖提示")
        for prompt in list(cap["draft"].values()) + list(cap["revise"].values()):
            self.assertNotIn("__LOCKED_CH__", prompt, "占位符不得残留")
            self.assertNotIn("__BEATS__", prompt)
        for prompt in cap["draft"].values():
            self.assertIn("锁定章纲", prompt, "本用例两章都有章纲，起草提示词都应注入")
