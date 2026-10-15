# -*- coding: utf-8 -*-
"""跨章矛盾 major「反复写→反复不达标」死循环回归（2026-10-13 头七/神瞳实案）。

实案链：章分与全局分全过线（global_pass=True，综合 8.2 vs 阈值 7.0），卡死的
是跨章事实矛盾类 major（时间线互斥/金额放大十倍/套餐年份三处口径不一）——
打磨循环只按 not global_pass 触发，一次都不进；retry 回炉只认 major 的
chapter 字段（=评审/签约检查点所在章），病灶章只写在 note 文案里，回炉
永远修不掉病，重跑必然再被同一条 major 点名。

锁定五个行为：
1. `_issue_hits_chapter`/`_major_named_chapters`：显式 chapter 字段 + note
   文案点名并集定位病灶章；「全书」字段不炸、越界章丢弃；
2. global_pass=True 但存在 major → 自驱打磨照常触发，重改文案点名的病灶章
   （提示词带跨章口径统一指令），重评干净后 publishable 翻正；
3. 检查点章名下的错位账（chapter=检查点章、病灶在文案里）：病灶章重写重评
   后按「点名命中」撤账，不再被陈账压死；
4. store.retry_task：major note 文案点名的章进回炉集（含「全书」级 major），
   无点名文案的全书级 major 只随原因账下发、不拉章回炉；
5. 回炉章起草提示词按点名路由：病灶章收到对应 major，未被点名的章只收
   全书级账，不收别章的病。
"""
from __future__ import annotations

import json

from base import BaseTest


def _ok(text=""):
    return {"ok": True, "text": text, "json": None, "cost_usd": 0.0,
            "tokens": 0, "error": "", "raw": {"exit_code": 0}, "sid": ""}


GOOD = json.dumps({"scores": {"情节": 9.0, "人物": 9.0, "文笔": 9.0,
                              "节奏": 9.0, "吸引力": 9.0}, "issues": []},
                  ensure_ascii=False)
# 全局分过线 + 一条点名第 2 章的跨章矛盾 major（复刻实案形态：
# 分数全达标、major 是唯一拦路虎）
GLOBAL_GOOD_MAJOR_C2 = json.dumps(
    {"scores": {"情节": 9.0, "人物": 9.0, "文笔": 9.0, "节奏": 9.0, "吸引力": 9.0},
     "issues": [{"dim": "情节", "severity": "major",
                 "note": "第2章金额前后矛盾：借款单写十五万、到账却念成一百万",
                 "quote": "入账一百五十万元"}]}, ensure_ascii=False)
# 章级评审出的 major：落账带 chapter=3（检查点/评审章），病灶写在文案里
# （复刻签约检查点错位归因形态：chapter=9、病灶在第 6 章）
CRIT_C3_GOOD_MAJOR = json.dumps(
    {"scores": {"情节": 9.0, "人物": 9.0, "文笔": 9.0, "节奏": 9.0, "吸引力": 9.0},
     "issues": [{"dim": "情节", "severity": "major",
                 "note": "第2章金额与第1章矛盾：借款单写十五万、到账念成一百万",
                 "quote": "入账一百五十万元"}]}, ensure_ascii=False)

AMOUNT_NOTE = "第2章金额前后矛盾：借款单写十五万、到账却念成一百万"
TIMELINE_NOTE = "第1章与第2章时间线互斥：三点零五分取件对不上三点四十七分"


class TestMajorHelpers(BaseTest):
    def test_issue_hits_chapter(self):
        from app.core import pipeline as P
        # 显式字段命中
        self.assertTrue(P._issue_hits_chapter({"chapter": 6, "note": "金额矛盾"}, 6))
        # 字段是检查点章、病灶在文案 → 文案命中病灶章（实案形态）
        self.assertTrue(P._issue_hits_chapter(
            {"chapter": 9, "note": "第6章金额自相矛盾"}, 6))
        # 「全书」字段不炸，按文案命中
        self.assertTrue(P._issue_hits_chapter(
            {"chapter": "全书", "note": TIMELINE_NOTE}, 1))
        self.assertTrue(P._issue_hits_chapter(
            {"chapter": "全书", "note": TIMELINE_NOTE}, 2))
        self.assertFalse(P._issue_hits_chapter(
            {"chapter": "全书", "note": TIMELINE_NOTE}, 3))
        # 区间文案逐章命中；无字段无点名 → 不命中；非 dict 安全
        self.assertTrue(P._issue_hits_chapter(
            {"chapter": "全书", "note": "第1—9章主角未付代价"}, 5))
        self.assertFalse(P._issue_hits_chapter({"note": "全书节奏拖沓"}, 2))
        self.assertFalse(P._issue_hits_chapter(None, 1))
        self.assertFalse(P._issue_hits_chapter("x", 1))

    def test_major_named_chapters(self):
        from app.core import pipeline as P
        majors = [{"chapter": 9, "note": "第6章金额自相矛盾"},
                  {"chapter": "全书", "note": TIMELINE_NOTE},
                  {"chapter": "全书", "note": "第1—9章主角未付任何实质代价"}]
        self.assertEqual(P._major_named_chapters(majors),
                         {1, 2, 5, 6, 9} | set(range(1, 10)))
        # 越界章丢弃（续写批：点名前批归档章不进本批打磨面）
        self.assertEqual(P._major_named_chapters(
            [{"chapter": "全书", "note": "第137章口径与第140章不一致"}], lo=149, hi=160),
            set())
        self.assertEqual(P._major_named_chapters([]), set())
        self.assertEqual(P._major_named_chapters([None, "x"]), set())


class _SerialFixture(BaseTest):
    """三条集成用例共用的假件骨架（同 test_serial_polish_named 的打磨组基建）。"""

    def _make(self, chapters=3):
        from app.core import store
        task = store.create_task({
            "type": "serial_novel", "title": "major 修复回归", "goal": "写三章",
            "workdir": str(self.workdir),
            "serial": {"chapters": chapters, "words_per_chapter": 300},
            "implementer": "mock-a", "critics": ["c1", "c2"],
        })
        # Isolate major-repair routing; store normalization does not persist this runtime flag.
        task = dict(task, serial=dict(task["serial"], opening_gate=False))
        run = store.create_run("orchestration", task["title"], task_id=task["id"])
        store.update_run(run["id"], status="running")
        return task, run

    def _agents(self):
        critics = [{"id": cid, "mode": "real", "kind": "generic", "command": "x",
                    "label": cid} for cid in ("c1", "c2")]
        # impl 必须 real：mock 打磨直接写盘不经过 _run_step，假件收不到 polish-c 角色
        impl = {"id": "impl-r", "mode": "real", "kind": "generic",
                "command": "x", "label": "Impl R"}
        return critics, impl

    def _run_with(self, task, run, critics, impl, fake_step):
        from app.core import pipeline, store
        orig_step = pipeline._run_step
        orig_outline = pipeline.planner.make_serial_outline

        def fake_outline(t, author_agent=None, workdir=None, ev=None, log_path=None):
            n = int((t.get("serial") or {}).get("chapters") or 3)
            return {"book_title": "回归书", "source": "llm(fake)",
                    "chapters": [{"title": "第 %d 章" % (k + 1), "beats": "节拍%d" % (k + 1),
                                  "hook": "钩子"} for k in range(n)]}

        pipeline.planner.make_serial_outline = fake_outline
        pipeline._run_step = fake_step
        try:
            pipeline._run_serial_review(
                store.get_run(run["id"]), task, critics, None, {}, "manual",
                critics, impl, {}, None, "default")
        finally:
            pipeline._run_step = orig_step
            pipeline.planner.make_serial_outline = orig_outline
        return store.get_run(run["id"])


class TestMajorTriggersPolishWhenGlobalPasses(_SerialFixture):
    def test_global_pass_with_major_still_polishes_named_chapter(self):
        """global_pass=True + major → 打磨必须进（旧代码 while(not global_pass)
        一次都不进，major 永远没人修）；病灶章=文案点名的第 2 章。"""
        from app.core import pipeline
        task, run = self._make()
        critics, impl = self._agents()
        polish_prompts = {}
        n_global = {"n": 0}

        def fake_step(run_id, role, agent, prompt, workdir, readonly, ev, *a, **kw):
            if role.startswith("draft-c"):
                pipeline._write_chapter(workdir, int(role.split("c")[-1]), "山" * 400)
                return _ok()
            if role.startswith("critique-c"):
                return _ok(GOOD)
            if role == "global-critique":
                n_global["n"] += 1
                # 第一轮全局（2 名评审）：分数过线 + 点名第 2 章 major → 打磨；
                # 之后重评全干净 → 账消 publishable 翻正
                if n_global["n"] <= len(critics):
                    return _ok(GLOBAL_GOOD_MAJOR_C2)
                return _ok(GOOD)
            if role.startswith("polish-c"):
                polish_prompts[role] = prompt
                pipeline._write_chapter(workdir, int(role.split("c")[-1]), "改" * 400)
                return _ok()
            return _ok()

        run = self._run_with(task, run, critics, impl, fake_step)

        # 只打磨文案点名的第 2 章；未被点名的 1/3 章不进
        self.assertEqual(sorted(polish_prompts), ["polish-c2"],
                         "global 过线但 major 在场时打磨必须触发并只改病灶章")
        p2 = polish_prompts["polish-c2"]
        self.assertIn(AMOUNT_NOTE[:30], p2)
        self.assertIn("跨章口径统一", p2)
        self.assertIn("可推翻现稿自创的情节线", p2)
        self.assertIn("节拍2", p2)

        self.assertEqual(run["status"], "done", run.get("error"))
        v = run["verdict"]
        self.assertTrue(v["global_pass"], v.get("global_scores"))
        self.assertTrue(v["publishable"],
                        "病灶章已重写、重评已干净，major 账必须消：%s"
                        % v.get("major_issues"))

    def test_misattributed_account_cleared_by_mention(self):
        """chapter=3 名下、病灶在第 1/2 章的错位账：病灶章重写重评后按点名
        撤账——旧代码只按字段撤，修好了第 2 章也摘不掉第 3 章名下的账。"""
        from app.core import pipeline
        task, run = self._make()
        critics, impl = self._agents()
        polish_prompts = {}

        def fake_step(run_id, role, agent, prompt, workdir, readonly, ev, *a, **kw):
            if role.startswith("draft-c"):
                pipeline._write_chapter(workdir, int(role.split("c")[-1]), "山" * 400)
                return _ok()
            if role.startswith("critique-c"):
                i = int(role.split("c")[-1])
                if "打磨后" in prompt:
                    return _ok(GOOD)
                # 第 3 章首轮评审：分数全过线但带回一条点名 1/2 章的 major
                # （复刻签约检查点形态：账记在第 3 章名下、病灶在文案里）
                return _ok(CRIT_C3_GOOD_MAJOR if i == 3 else GOOD)
            if role == "global-critique":
                return _ok(GOOD)     # 全局评审全程干净（global_pass=True）
            if role.startswith("polish-c"):
                polish_prompts[role] = prompt
                pipeline._write_chapter(workdir, int(role.split("c")[-1]), "改" * 400)
                return _ok()
            return _ok()

        run = self._run_with(task, run, critics, impl, fake_step)

        # major 点名 {1,2}（文案）∪ {3}（字段）→ 三章都进打磨面（cap=4）
        self.assertEqual(sorted(polish_prompts),
                         ["polish-c1", "polish-c2", "polish-c3"])
        # 第 2 章的打磨面收到错位账的原话（靠文案命中，不是靠字段）
        self.assertIn("借款单写十五万", polish_prompts["polish-c2"])
        self.assertIn("跨章口径统一", polish_prompts["polish-c2"])

        self.assertEqual(run["status"], "done", run.get("error"))
        v = run["verdict"]
        self.assertTrue(v["publishable"],
                        "病灶章已重写重评，错位账必须按点名撤下：major_issues=%s"
                        % v.get("major_issues"))


class TestRetryRedoByMention(BaseTest):
    """retry 回炉集：major note 文案点名的章必须回炉（旧代码只认 chapter
    字段，「全书」级转 int 失败直接跳过——头七年份矛盾点名 1/2/4 章永远
    不回炉，重跑必然再被同一条 major 点名）。"""

    def _prev_run(self, majors, chapters=3, global_scores=None):
        from app.core import store
        task = store.create_task({
            "type": "serial_novel", "title": "回炉回归", "goal": "写三章",
            "workdir": str(self.workdir),
            "serial": {"chapters": chapters, "words_per_chapter": 300},
            "implementer": "mock-a", "critics": ["c1", "c2"],
        })
        run = store.create_run("orchestration", task["title"], task_id=task["id"])
        store.update_run(run["id"], status="done",
                         outline={"book_title": "回归书", "source": "llm(fake)",
                                  "chapters": [{"title": "第 %d 章" % (k + 1),
                                                "beats": "节拍", "hook": "钩子"}
                                               for k in range(chapters)]},
                         steps=[{"n": k + 1, "role": "draft-c%d" % (k + 1),
                                 "status": "done"} for k in range(chapters)],
                         verdict={"type": "serial_novel", "publishable": False,
                                 "threshold": 7.0,
                                 "global_scores": global_scores or {},
                                  "start_chapter": 1, "end_chapter": chapters,
                                  "chapter_scores": [
                                      {"chapter": k + 1, "title": "第 %d 章" % (k + 1),
                                       "means": {"情节": 8.2}, "passed": True,
                                       "rounds": 1, "words": 300}
                                      for k in range(chapters)],
                                  "major_issues": majors})
        return task

    def test_mentioned_chapters_enter_redo(self):
        from app.core import store
        task = self._prev_run([
            {"chapter": 3, "dim": "情节", "severity": "major", "note": AMOUNT_NOTE},
            {"chapter": "全书", "dim": "情节", "severity": "major", "note": TIMELINE_NOTE},
        ])
        ok, err, new_run = store.retry_task(task["id"])
        self.assertTrue(ok, err)
        inh = new_run.get("inherit") or {}
        # 金额账：字段 3 + 文案点名 2；时间线账：文案点名 1、2 → redo={1,2,3}
        self.assertEqual(inh.get("done_chapters"), [])
        self.assertEqual(len(inh.get("redo_issues") or []), 2)
        # 过线章分数不随 redo 陪葬的通道仍在（全回炉时为空表）
        self.assertEqual(inh.get("chapter_scores"), [])

    def test_unmentioned_book_major_stays_issue_only(self):
        from app.core import store
        task = self._prev_run([
            {"chapter": 3, "dim": "节奏", "severity": "major", "note": "第2章节奏拖沓"},
            {"chapter": "全书", "dim": "节奏", "severity": "major", "note": "全书前期铺陈偏慢"},
        ])
        ok, err, new_run = store.retry_task(task["id"])
        self.assertTrue(ok, err)
        inh = new_run.get("inherit") or {}
        # redo={2,3}；无点名文案的全书级 major 只进原因账，不拉第 1 章回炉
        self.assertEqual(inh.get("done_chapters"), [1])
        self.assertEqual([c["chapter"] for c in inh.get("chapter_scores") or []], [1])
        self.assertEqual(len(inh.get("redo_issues") or []), 2)

    def test_global_score_shortfall_redoes_weakest_chapters(self):
        """全章均过线但全局维度失败时，不能沿用原稿后原样重评。"""
        from app.core import store
        task = self._prev_run([], global_scores={"情节": 6.0})
        prev = store.task_runs(task["id"])[-1]
        # 全章章节均达标；全局情节分低于 7.0。按该维章分最低者回炉。
        scores = prev["verdict"]["chapter_scores"]
        scores[0]["means"]["情节"] = 8.3
        scores[1]["means"]["情节"] = 8.0
        scores[2]["means"]["情节"] = 7.5
        store.update_run(prev["id"], verdict=prev["verdict"])

        ok, err, new_run = store.retry_task(task["id"])
        self.assertTrue(ok, err)
        inherit = new_run.get("inherit") or {}
        self.assertEqual(inherit["done_chapters"], [1])
        self.assertEqual([c["chapter"] for c in inherit["chapter_scores"]], [1])
        self.assertEqual(
            [issue["chapter"] for issue in inherit["redo_issues"]], [3, 2])
        self.assertIn("全局评审「情节」6.0", inherit["redo_issues"][0]["note"])

    def test_unmappable_global_score_does_not_guess_chapters(self):
        """全局分与章分维度不匹配时不虚构修复目标。"""
        from app.core import store
        task = self._prev_run([], global_scores={"结构总评": 6.0})
        prev = store.task_runs(task["id"])[-1]
        store.update_run(prev["id"], verdict=prev["verdict"])

        ok, err, new_run = store.retry_task(task["id"])
        self.assertTrue(ok, err)
        inherit = new_run.get("inherit") or {}
        self.assertEqual(inherit["done_chapters"], [1, 2, 3])
        self.assertEqual(inherit["redo_issues"], [])

    def test_unlocated_global_major_routes_only_with_dimension_shortfall(self):
        """全书级 major 有低分维度证据时路由最弱章，否则不盲改。"""
        from app.core import store
        task = self._prev_run([
            {"chapter": "全书", "dim": "情节", "severity": "major",
             "note": "全书节奏失衡，冲突推进不足"}],
            global_scores={"情节": 6.0})
        prev = store.task_runs(task["id"])[-1]
        scores = prev["verdict"]["chapter_scores"]
        scores[0]["means"]["情节"] = 8.3
        scores[1]["means"]["情节"] = 8.0
        scores[2]["means"]["情节"] = 7.5
        store.update_run(prev["id"], verdict=prev["verdict"])

        ok, err, new_run = store.retry_task(task["id"])
        self.assertTrue(ok, err)
        inherit = new_run.get("inherit") or {}
        self.assertEqual(inherit["done_chapters"], [1])
        self.assertEqual([c["chapter"] for c in inherit["chapter_scores"]], [1])
        # 全局低分已消耗两章回炉上限；全书级原 major 仍完整下发给回炉章。
        self.assertTrue(any(x.get("chapter") == "全书"
                            and "全书节奏失衡" in x.get("note", "")
                            for x in inherit["redo_issues"]))


class TestRedoIssueRouting(_SerialFixture):
    def test_redraft_prompt_routes_issues_by_mention(self):
        """回炉章起草提示词：病灶章收到点名自己的 major；未被点名的章只收
        全书级账——旧代码按 chapter 字段路由，第 6 章的病会下发给第 9 章。"""
        from app.core import pipeline, store
        task, run = self._make()
        critics, impl = self._agents()
        # 手工构造「重写未达标章」继承：第 3 章复用，1/2 章回炉
        store.update_run(run["id"], inherit={
            "outline": {"book_title": "回归书", "source": "llm(fake)",
                        "chapters": [{"title": "第 %d 章" % (k + 1), "beats": "节拍%d" % (k + 1),
                                      "hook": "钩子"} for k in range(3)]},
            "done_chapters": [3],
            "chapter_scores": [{"chapter": 3, "title": "第 3 章",
                                "means": {"情节": 8.2}, "passed": True,
                                "rounds": 1, "words": 300}],
            "redo_issues": [
                {"chapter": 3, "dim": "情节", "severity": "major", "note": AMOUNT_NOTE},
                {"chapter": "全书", "dim": "情节", "severity": "major", "note": TIMELINE_NOTE},
            ],
        })
        # 复用章的成稿必须在场（坏稿防线按文件存在+字数判复用）
        pipeline._write_chapter(self.workdir, 3, "复" * 300)
        draft_prompts = {}

        def fake_step(run_id, role, agent, prompt, workdir, readonly, ev, *a, **kw):
            if role.startswith("draft-c"):
                draft_prompts[role] = prompt
                pipeline._write_chapter(workdir, int(role.split("c")[-1]), "山" * 400)
                return _ok()
            if role.startswith("critique-c") or role == "global-critique":
                return _ok(GOOD)
            return _ok()

        run = self._run_with(task, run, critics, impl, fake_step)

        # 只有 1/2 章回炉起草；第 3 章复用不重写
        self.assertEqual(sorted(draft_prompts), ["draft-c1", "draft-c2"])
        # 第 1 章：收时间线账（文案点名），不收金额账（点名的是第 2 章）
        self.assertIn("时间线互斥", draft_prompts["draft-c1"])
        self.assertIn("上一轮评审未达标原因", draft_prompts["draft-c1"])
        self.assertNotIn("借款单写十五万", draft_prompts["draft-c1"])
        # 第 2 章：两笔账都点名了它 → 全收
        self.assertIn("借款单写十五万", draft_prompts["draft-c2"])
        self.assertIn("时间线互斥", draft_prompts["draft-c2"])
        # 第 3 章（字段命中）不重写，账随 1/2 章的修订消解
        self.assertEqual(run["status"], "done", run.get("error"))
