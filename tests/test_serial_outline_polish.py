# -*- coding: utf-8 -*-
"""连载打磨选章与全书文本回归（2026-10-03 不周山守村人评审死锁案）。

锁定五个行为：
1. aider 形态超长提示词 → 落盘文件走 ``--read`` 只读入会话。实案：旧路径
   指挥模型「先读文件再执行」被判定为提示注入直接拒绝，全局评审每轮只有
   1 名有效评审，门禁「≥2 名」结构性不可能通过，重写多少次都拦截；
2. 续写批次前作章文件被归档（chapter-NN.md 不在场）→ 全书评审文本回退
   「已成稿/」归档找回前文，且本批全文保底在场、不被预算截掉；
3. 合并成书带归档前文，续写批次的成品不再静默丢掉此前全部章节；
4. 全局 major 点名的章优先进入打磨面，修订提示词附点名原话并放开
   「不得改主线」约束（允许按大纲推翻脱纲情节）；模板占位符全部替换；
5. 跨轮评审 issues 去重，major 计数不因重评轮次虚高。
"""
from __future__ import annotations

import json
import os

from base import BaseTest


def _ok(text=""):
    return {"ok": True, "text": text, "json": None, "cost_usd": 0.0,
            "tokens": 0, "error": "", "raw": {"exit_code": 0}, "sid": ""}


RUBRIC = {"情节": 9.0, "人物": 9.0, "文笔": 9.0, "节奏": 9.0, "吸引力": 9.0}
SIGN = {"开篇吸引力": 9.0, "情节推进": 9.0, "文风统一": 9.0,
        "情感细腻度": 9.0, "节奏控制": 9.0}
WEAK = {d: 5.0 for d in RUBRIC}


def _j(scores, issues=None):
    return json.dumps({"scores": scores, "issues": issues or [],
                       "summary": "总评"}, ensure_ascii=False)


def _outline(n):
    beats = ["主角修好水车并立下规矩", "旱情缓解，村人开始跟风",
             "上游来人收水钱，主角据理力争"]
    return {"book_title": "打磨选章回归", "source": "test",
            "chapters": [{"title": "第%d章" % (k + 1), "beats": beats[k % len(beats)],
                          "highlight": "小冲突"} for k in range(n)]}


class PolishRunBase(BaseTest):
    """真实 impl（fake _run_step 代写章文件）驱动 _run_serial_review，
    捕获打磨提示词与全局评审提示词做断言。"""

    def _make(self, chapters=3, start=1):
        from app.core import store
        serial = {"chapters": chapters, "words_per_chapter": 300}
        if start > 1:
            serial["start_chapter"] = start
        task = store.create_task({
            "type": "serial_novel", "title": "打磨选章回归", "goal": "写三章",
            "workdir": str(self.workdir), "serial": serial,
            "implementer": "impl-a", "critics": ["c1", "c2"],
        })
        run = store.create_run("orchestration", task["title"], task_id=task["id"])
        store.update_run(run["id"], status="running")
        run = dict(store.get_run(run["id"]))
        run["inherit"] = {"outline": _outline(chapters)}
        critics = [{"id": cid, "mode": "real", "kind": "generic", "command": "x",
                    "label": cid} for cid in ("c1", "c2")]
        impl = {"id": "impl-a", "mode": "real", "kind": "generic", "command": "x",
                "label": "Impl"}
        return task, run, critics + [impl], critics, impl

    def _fake_step(self, cap):
        from app.core import pipeline
        wd = str(self.workdir)
        # _chapter_state 验收要求去空白字数 ≥ wpc*0.6（300*0.6=180），mock 稿
        # 只有 ~170 字不够——fake 代写用足够长的正文
        body = "这是一段足够长的测试正文，主角在修水车。" * 30

        def fake(run_id, role, agent, prompt, workdir, readonly, ev, *a, **kw):
            cap.setdefault("roles", []).append(role)
            if role.startswith("draft-c") and "-v" not in role:
                i = int(role.split("draft-c")[1])
                pipeline._write_chapter(wd, i, body)
                return _ok("已写")
            if role.startswith("polish-c"):
                i = int(role.split("polish-c")[1])
                cap.setdefault("polish_prompts", {})[i] = prompt
                pipeline._write_chapter(wd, i, body)
                return _ok("已修订")
            if role.startswith("critique-c"):
                return _ok(_j(RUBRIC))
            if role == "signing-eval":
                return _ok(_j(SIGN))
            if role == "global-critique":
                cap["global_calls"] = cap.get("global_calls", 0) + 1
                cap.setdefault("global_prompts", []).append(prompt)
                # 按轮次返回：同一轮的两位评审必须口径一致（并发各调一次）
                ncritics = cap.get("ncritics") or 2
                rnd = (cap["global_calls"] - 1) // ncritics
                if rnd == 0 and cap.get("first_global_weak"):
                    return _ok(_j(WEAK, [{"dim": "情节", "severity": "major",
                                          "note": cap.get("named_note", ""),
                                          "quote": ""}]))
                return _ok(_j(RUBRIC))
            return _ok()
        return fake

    def _review(self, task, run, agents, critics, impl, cap):
        from app.core import pipeline
        cap["ncritics"] = len(critics)
        orig = pipeline._run_step
        pipeline._run_step = self._fake_step(cap)
        try:
            pipeline._run_serial_review(
                run, task, agents, None, {}, "manual", critics, impl, {}, None, "default")
        finally:
            pipeline._run_step = orig


class TestPolishTargetsNamedChapters(PolishRunBase):
    def test_named_chapter_polished_with_deviation_permission(self):
        """全局 major 点名的章（单章分数达标）必须进入打磨面，且修订提示词
        附点名原话、放开「不得改主线」、占位符全部替换。"""
        from app.core import store
        task, run, agents, critics, impl = self._make(chapters=3)
        cap = {"first_global_weak": True,
               "named_note": "第3章整体背离章纲：章纲要求据理力争收水钱，成稿写成赶集"}
        self._review(task, run, agents, critics, impl, cap)

        pp = cap.get("polish_prompts") or {}
        self.assertIn(3, pp, "被点名的第3章必须被打磨（旧逻辑只按分数选章，永远选不中）")
        p3 = pp[3]
        self.assertIn("第3章整体背离章纲", p3, "点名原话应随修订提示词下发")
        self.assertIn("以本章大纲要点为准重写", p3, "脱纲章须放开约束允许按大纲推翻")
        self.assertNotIn("不得改动既有剧情主线的关键事实", p3)
        self.assertIn("上游来人收水钱", p3, "__BEATS__ 应替换为本章大纲要点")
        for ph in ("__BEATS__", "__HIGHLIGHT__", "__STYLE__"):
            self.assertNotIn(ph, p3, "模板占位符不得残留：%s" % ph)
        # 未被点名的章仍走原约束（不放开改主线）
        others = [p for k, p in pp.items() if k != 3]
        self.assertTrue(others)
        for p in others:
            self.assertIn("不得改动既有剧情主线的关键事实", p)

    def test_no_named_chapters_keeps_score_based_selection(self):
        """全局低分但无点名章号 → 维持分数选章原路径。"""
        task, run, agents, critics, impl = self._make(chapters=2)
        cap = {"first_global_weak": True}
        self._review(task, run, agents, critics, impl, cap)
        pp = cap.get("polish_prompts") or {}
        self.assertTrue(pp, "全局低分应触发打磨")
        for p in pp.values():
            self.assertIn("不得改动既有剧情主线的关键事实", p)


class TestArchivedPreface(PolishRunBase):
    ARCH = "前文归档正文水车 KeyValue 前情提要" * 40
    TOC = "这是阅读目录索引不要并入正文"

    def _seed_archive(self, big=False):
        adir = self.workdir / "已成稿"
        adir.mkdir()
        body = self.ARCH * (30 if big else 1)
        (adir / "不周山守村人-前作.md").write_text(body, encoding="utf-8")
        (adir / "前作阅读目录.md").write_text(self.TOC, encoding="utf-8")
        return body

    def test_unit_helpers(self):
        from app.core import pipeline
        wd = str(self.workdir)
        body = self._seed_archive()
        self.assertEqual(pipeline._archived_preface(wd, 1), ("", 0))
        got, upto = pipeline._archived_preface(wd, 3)
        self.assertIn("前文归档正文", got)
        self.assertEqual(upto, 2, "1–2 缺失 → 归档覆盖到第 2 章")
        self.assertNotIn(self.TOC, got, "目录索引文件不得并入前文")
        # 前文章节文件仍在场 → 不走归档回退
        (self.workdir / "chapter-01.md").write_text("第一章正文", encoding="utf-8")
        self.assertEqual(pipeline._archived_preface(wd, 3), ("", 0))
        (self.workdir / "chapter-01.md").unlink()

    def test_mixed_archived_prefix(self):
        """混合态回归（2026-10-04 实案）：1–2 已归档、第 3 章在场——旧版见
        任一章在场就整体放弃，归档的 1–2 被静默丢出成书；同名 .md/.txt
        并存只取一份。"""
        from app.core import pipeline
        wd = str(self.workdir)
        body = self._seed_archive()
        (self.workdir / "已成稿" / "不周山守村人-前作.txt").write_text(
            body, encoding="utf-8")   # 同题名双格式：只允许计一次
        (self.workdir / "chapter-03.md").write_text("第三章正文", encoding="utf-8")
        got, upto = pipeline._archived_preface(wd, 4)
        self.assertIn("前文归档正文", got)
        self.assertEqual(upto, 2, "连续缺失前缀 1–2 计入归档")
        self.assertNotIn("第三章正文", got, "在场的第 3 章不并入归档段")
        self.assertEqual(got.count("前文归档正文"), 40, "同题名 .md/.txt 只取一份")

    def test_full_manuscript_budget_keeps_batch(self):
        from app.core import pipeline
        wd = str(self.workdir)
        self._seed_archive(big=True)
        (self.workdir / "chapter-03.md").write_text("第三" * 800, encoding="utf-8")
        (self.workdir / "chapter-04.md").write_text("第四" * 800, encoding="utf-8")
        # cap=5000 < 前文+本批 → 必走截断路径；预算优先保本批全文
        got = pipeline._full_manuscript(wd, 3, 4, cap=5000)
        self.assertLessEqual(len(got), 5000)
        self.assertIn("第三" * 800, got, "本批正文必须完整在场")
        self.assertIn("第四" * 800, got, "本批正文必须完整在场")
        self.assertIn("前文较长，仅保留紧邻本批的尾部", got)
        self.assertIn("前文归档正文", got, "归档前文尾部应保留")

    def test_review_and_merge_use_archive(self):
        """start=3 续写批：全局评审提示词含归档前文与本批全文；成书带归档前文。"""
        from app.core import store
        task, run, agents, critics, impl = self._make(chapters=2, start=3)
        body = self._seed_archive()
        cap = {"first_global_weak": False}
        self._review(task, run, agents, critics, impl, cap)

        self.assertTrue(cap.get("global_prompts"))
        gp = "\n".join(cap["global_prompts"])
        self.assertIn("前文归档正文", gp, "全局评审必须看到归档前文")
        self.assertNotIn(self.TOC, gp)
        run = store.get_run(run["id"])
        self.assertEqual(run["status"], "done", run.get("error"))
        ms = (self.workdir / "manuscript.md")
        self.assertTrue(ms.is_file())
        txt = ms.read_text(encoding="utf-8")
        self.assertIn("## 前文（第 1–2 章 · 归档稿）", txt)
        self.assertIn("前文归档正文", txt)
        self.assertNotIn(self.TOC, txt, "成书不得混入目录索引文件")
        self.assertIn("这是一段足够长的测试正文", txt, "本批章节必须在成书里")


class TestIssueHelpers(BaseTest):
    def test_chapters_named_in(self):
        from app.core import pipeline
        mk = lambda note: [{"note": note}]  # noqa: E731
        self.assertEqual(pipeline._chapters_named_in(mk("第26—28章脱轨"), 21, 28),
                         {26, 27, 28})
        self.assertEqual(pipeline._chapters_named_in(mk("第 22 章"), 21, 28), {22})
        self.assertEqual(pipeline._chapters_named_in(mk("第21至23章"), 21, 28),
                         {21, 22, 23})
        self.assertEqual(pipeline._chapters_named_in(mk("第1—8章黄金三章"), 21, 28),
                         set(), "范围外章号丢弃")
        self.assertEqual(pipeline._chapters_named_in(mk("黄金三章切入速度"), 1, 28),
                         set())

    def test_dedup_issues(self):
        from app.core import pipeline
        a = {"chapter": "全书", "dim": "情节", "note": "第21—28章整体脱纲：" + "x" * 80}
        b = {"chapter": "全书", "dim": "情节", "note": "第21—28章整体脱纲：" + "x" * 80 + "（重复）"}
        c = {"chapter": "全书", "dim": "人物", "note": "第21—28章整体脱纲：" + "x" * 80}
        d = {"chapter": 22, "dim": "情节", "note": "第21—28章整体脱纲：" + "x" * 80}
        out = pipeline._dedup_issues([a, b, c, d])
        self.assertEqual(len(out), 3, "同章同维度同要点只保留首条")
        self.assertIs(out[0], a)
        self.assertNotIn(b, out)
        out2 = pipeline._dedup_issues([{"chapter": "全书", "dim": "情节",
                                        "note": "同 要点\n换行"} ,
                                       {"chapter": "全书", "dim": "情节",
                                        "note": "同要点换行"}])
        self.assertEqual(len(out2), 1, "空白差异不影响去重")


class TestAiderLongPrompt(BaseTest):
    def test_overlong_prompt_uses_read_flag(self):
        """aider 超长提示词 → 文件走 --read 只读入会话，而非指挥模型自读
        （自读指令会被模型判提示注入拒绝，全局评审恒 1 名有效）。"""
        from app.core import runner as R
        agent = {"id": "aider", "kind": "aider", "mode": "real", "command": "aider"}
        long_prompt = "评审以下书稿：" + "很久很久以前。" * 4000
        argv, stdin_text, _, tmp = R._build_call(
            agent, "aider", "", True, None, long_prompt, workdir=str(self.workdir))
        try:
            self.assertIsNone(stdin_text)
            self.assertEqual(len(tmp), 1)
            self.assertNotIn(long_prompt, argv)
            self.assertIn("--read", argv)
            self.assertEqual(argv[argv.index("--read") + 1], tmp[0])
            joined = "\n".join(str(a) for a in argv)
            self.assertIn("只读文件加入会话", joined)
            self.assertNotIn("已写入文件", joined, "不得再走「指挥模型读文件」的死路")
            with open(tmp[0], encoding="utf-8") as f:
                self.assertEqual(f.read(), long_prompt)
        finally:
            for p in tmp:
                try:
                    os.remove(p)
                except OSError:
                    pass

    def test_short_prompt_direct_message(self):
        from app.core import runner as R
        agent = {"id": "aider", "kind": "aider", "mode": "real", "command": "aider"}
        argv, stdin_text, _, tmp = R._build_call(
            agent, "aider", "", True, None, "短提示词", workdir=str(self.workdir))
        self.assertEqual(tmp, [])
        self.assertNotIn("--read", argv)
        self.assertIn("短提示词", argv)


if __name__ == "__main__":
    import unittest
    unittest.main()
