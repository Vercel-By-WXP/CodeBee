# -*- coding: utf-8 -*-
"""分卷（网文卷结构）测试。

覆盖三层：
1. 纯函数（app/core/volumes.py）：卷规划表推导、用户显式卷表解析、文字识别
   的**负例**（普通文字绝不能被误判成分卷）。
2. 任务层：新建任务时显式卷表/每卷章数落进 serial，续写批次沿用同一份
   （卷边界跨批次必须稳定，否则同一卷会被续写重切）。
3. 端到端（mock 流水线）：合并成书插入卷标题、章节卡带卷号、报告含分卷行；
   以及**不分卷时的向后兼容**（旧任务 serial 无分卷字段 → 成书无卷标题）。
"""
from __future__ import annotations

from base import BaseTest


class TestVolumesPure(BaseTest):
    """卷规划表纯函数。"""

    def test_norm_per(self):
        from app.core import volumes as V
        self.assertEqual(V.norm_per(20), 20)
        self.assertEqual(V.norm_per("30"), 30)
        self.assertEqual(V.norm_per(0), 0)      # 0 = 不分卷
        self.assertEqual(V.norm_per(1), 0)      # 1 章一卷等于没分
        self.assertEqual(V.norm_per(None), 0)
        self.assertEqual(V.norm_per("abc"), 0)
        self.assertEqual(V.norm_per(9999), V.MAX_PER)   # 上限钳制

    def test_plan_uniform_per(self):
        from app.core import volumes as V
        plan = V.build_plan([], 20, upto=45)
        self.assertEqual([(e["vol"], e["first"], e["last"]) for e in plan],
                         [(1, 1, 20), (2, 21, 40), (3, 41, 60)])
        self.assertEqual(V.find(plan, 20)["vol"], 1)
        self.assertEqual(V.find(plan, 21)["vol"], 2)
        self.assertTrue(V.is_vol_end(plan, 20))
        self.assertFalse(V.is_vol_end(plan, 21))
        self.assertEqual(V.position(plan, 21), (1, 20))
        self.assertEqual([e["vol"] for e in V.plan_volumes(plan, 9, 8)], [1])
        self.assertEqual([e["vol"] for e in V.plan_volumes(plan, 21, 8)], [2])
        # 跨卷批次：37–44 同时落在卷 2（37–40）与卷 3（41–44）
        self.assertEqual([e["vol"] for e in V.plan_volumes(plan, 37, 8)], [2, 3])

    def test_plan_explicit_spec_chapters(self):
        """显式卷表（每卷章数不等）优先于每卷章数。"""
        from app.core import volumes as V
        spec = V.norm_spec([{"title": "卷一 少年初入江湖", "chapters": 20},
                            {"title": "卷二 风云再起", "chapters": 16}])
        plan = V.build_plan(spec, 20, upto=40)
        self.assertEqual([(e["vol"], e["first"], e["last"]) for e in plan],
                         [(1, 1, 20), (2, 21, 36), (3, 37, 56)])
        self.assertEqual(plan[0]["title"], "卷一 少年初入江湖")
        self.assertTrue(V.is_vol_end(plan, 36))
        self.assertFalse(V.is_vol_end(plan, 37))

    def test_plan_explicit_spec_range_and_open(self):
        from app.core import volumes as V
        spec = V.norm_spec([{"title": "A", "start": 1, "end": 10},
                            {"title": "B", "start": 11}])     # B 开放到书末
        plan = V.build_plan(spec, 0, upto=60)
        self.assertEqual(len(plan), 2)
        self.assertIsNone(plan[1]["last"])
        self.assertEqual(V.find(plan, 55)["vol"], 2)
        self.assertEqual(V.position(plan, 55), (45, 0))    # 开放卷总数未知记 0
        self.assertFalse(V.is_vol_end(plan, 55))

    def test_no_volumes_is_empty(self):
        from app.core import volumes as V
        self.assertEqual(V.build_plan([], 0), [])
        self.assertEqual(V.build_plan(None, None), [])
        self.assertEqual(V.plan_volumes([], 1, 8), [])
        self.assertEqual(V.find([], 3), None)
        self.assertEqual(V.position([], 3), (0, 0))

    def test_parse_text_explicit_user_input(self):
        """用户在目标里写明卷结构 → 按给定的来（用户补充需求的直接落地）。"""
        from app.core import volumes as V
        cases = [
            ("写一本都市小说。第一卷 少年初入江湖 第1-20章；卷二 风云再起 21-40章",
             [("少年初入江湖", 1, 20), ("风云再起", 21, 40)]),
            ("第一卷 少年初入江湖 20章\n第二卷 风云再起 16章",
             [("少年初入江湖", 1, 20), ("风云再起", 21, 36)]),
            ("目标：写三卷：卷一 入江湖 20章，卷二 风云 25章，卷三 归隐 10章",
             [("入江湖", 1, 20), ("风云", 21, 45), ("归隐", 46, 55)]),
            ("分卷：第1部 少年 第1-10章；第2部 中年 第11-25章",
             [("少年", 1, 10), ("中年", 11, 25)]),
            ("全书分两卷：卷1 崛起 30章；卷2 争霸 30章",
             [("崛起", 1, 30), ("争霸", 31, 60)]),
        ]
        for text, expect in cases:
            spec = V.parse_spec_text(text)
            plan = V.build_plan(spec, 0, upto=expect[-1][2])
            got = [(e["title"], e["first"], e["last"]) for e in plan[:len(expect)]]
            self.assertEqual(got, expect, "解析失败：%s → %s" % (text, got))

    def test_parse_text_negative_no_false_positive(self):
        """普通文字绝不能被误判成分卷（保守识别的核心保障）。"""
        from app.core import volumes as V
        for text in (
            "写个短篇，分三章即可",
            "写一部两万字连载小说，主角重生回高中",
            "帮我写一份技术方案，分三个部分",
            "目标：写一本都市小说，第一卷要吸引人",     # 只提卷一但没边界 → 不成立
            "",
        ):
            self.assertEqual(V.parse_spec_text(text), [], "误判分卷：%s" % text)

    def test_merge_titles_keeps_user_title(self):
        """用户显式给的卷名优先，不被模型改写；模型只补空缺。"""
        from app.core import volumes as V
        plan = V.build_plan(V.norm_spec([{"title": "卷一 少年初入江湖", "chapters": 20},
                                         {"chapters": 10}]), 0, upto=30)
        merged = V.merge_titles(plan, {"1": {"title": "模型乱改名"},
                                       "2": {"title": "卷二 风云", "arc": "冲突升级"}})
        self.assertEqual(merged[0]["title"], "卷一 少年初入江湖")
        self.assertEqual(merged[1]["title"], "卷二 风云")
        self.assertEqual(merged[1]["arc"], "冲突升级")


class TestVolumesTask(BaseTest):
    """任务层：卷配置落盘 + 续写沿用。"""

    def test_create_task_volume_chapters(self):
        from app.core import store
        t = store.create_task({"type": "serial_novel", "goal": "写连载",
                               "workdir": str(self.workdir),
                               "serial": {"chapters": 8, "words_per_chapter": 2000,
                                          "volume_chapters": 20}})
        self.assertEqual(t["serial"]["volume_chapters"], 20)
        # 每卷章数非法/关闭时不留字段（保持旧 serial 形状，向后兼容）
        t2 = store.create_task({"type": "serial_novel", "goal": "写连载",
                                "workdir": str(self.workdir),
                                "serial": {"chapters": 8, "words_per_chapter": 2000,
                                           "volume_chapters": 0}})
        self.assertNotIn("volume_chapters", t2["serial"])

    def test_create_task_explicit_volumes_from_goal(self):
        """新建任务时目标里写明分卷 → 按给定的卷结构落进 serial。"""
        from app.core import store
        t = store.create_task({
            "type": "serial_novel", "workdir": str(self.workdir),
            "goal": "写一部都市连载。第一卷 少年初入江湖 第1-20章；第二卷 风云再起 第21-40章",
            "serial": {"chapters": 8, "words_per_chapter": 2000}})
        vols = t["serial"].get("volumes")
        self.assertTrue(vols, t["serial"])
        self.assertEqual([v["title"] for v in vols], ["少年初入江湖", "风云再起"])
        self.assertEqual((vols[0]["start"], vols[0]["end"]), (1, 20))

    def test_create_task_explicit_volumes_field(self):
        """表单直接给卷表（serial.volumes）优先于文字解析。"""
        from app.core import store
        t = store.create_task({
            "type": "serial_novel", "workdir": str(self.workdir),
            "goal": "写连载",   # 目标里没有任何分卷信息
            "serial": {"chapters": 8, "words_per_chapter": 2000,
                       "volumes": [{"title": "卷一 起", "chapters": 12},
                                   {"title": "卷二 承", "chapters": 12}]}})
        vols = t["serial"]["volumes"]
        self.assertEqual([v["title"] for v in vols], ["卷一 起", "卷二 承"])
        self.assertEqual(vols[0]["chapters"], 12)

    def test_continue_task_carries_volume_config(self):
        """续写批次必须沿用同一份卷配置，否则同一卷会被重切。"""
        from app.core import store
        t = store.create_task({
            "type": "serial_novel", "workdir": str(self.workdir), "title": "分卷书",
            "goal": "写一部连载。第一卷 少年初入江湖 第1-2章；第二卷 风云再起 第3-4章",
            "serial": {"chapters": 2, "words_per_chapter": 800,
                       "volume_chapters": 2}})
        # 造出「已写 2 章」的事实，让 continue 可用
        (self.workdir / "chapter-01.md").write_text("正文一", encoding="utf-8")
        (self.workdir / "chapter-02.md").write_text("正文二", encoding="utf-8")
        ok, err, new = store.continue_task(t["id"], 2)
        self.assertTrue(ok, err)
        self.assertEqual(new["serial"]["volume_chapters"], 2)
        self.assertEqual([v["title"] for v in new["serial"]["volumes"]],
                         ["少年初入江湖", "风云再起"])
        self.assertEqual(new["serial"]["start_chapter"], 3)
        self.assertEqual(new["serial"]["continues"], t["id"])


class TestVolumesPipeline(BaseTest):
    """端到端（mock）：卷标题进成书、章节卡带卷号、不分卷保持原样。"""

    def _run_serial(self, serial, goal="写一部短篇连载"):
        from app.core import pipeline, store
        pipeline._agents = self.mock_agents
        task = store.create_task({"type": "serial_novel", "title": "分卷连载",
                                  "mode": "auto", "goal": goal,
                                  "workdir": str(self.workdir),
                                  "serial": serial, "threshold": 7.0})
        run = store.create_run("orchestration", task["title"], task_id=task["id"])
        pipeline.execute_run(run["id"])
        return task, store.get_run(run["id"])

    def test_uniform_volume_chapters_e2e(self):
        """每卷 2 章 + 共 4 章 → 成书两卷，章节卡带卷号。"""
        task, run = self._run_serial({"chapters": 4, "words_per_chapter": 1200,
                                      "volume_chapters": 2})
        self.assertEqual(run["status"], "done", run.get("error"))
        ms = (self.workdir / "manuscript.md").read_text(encoding="utf-8")
        self.assertIn("## 第 1 卷", ms)
        self.assertIn("## 第 2 卷", ms)
        # 卷标题插在卷首章之前
        self.assertLess(ms.index("## 第 1 卷"), ms.index("## 第 2 卷"))
        vols = run["verdict"]["volumes"]
        self.assertEqual([(v["vol"], v["first"], v["last"]) for v in vols],
                         [(1, 1, 2), (2, 3, 4)])
        # 章节卡带卷号（UI 按卷分组的真源）
        self.assertEqual([c.get("vol") for c in run["chapter_scores"]], [1, 1, 2, 2])
        report = (self._paths.RUNS_DIR / run["id"] / "report.md").read_text(encoding="utf-8")
        self.assertIn("- 分卷：", report)

    def test_explicit_volumes_e2e(self):
        """目标里写明卷结构 → 成书卷标题按用户给的卷名。"""
        goal = ("写一部短篇连载。第一卷 少年初入江湖 第1-2章；第二卷 风云再起 第3-4章")
        task, run = self._run_serial({"chapters": 4, "words_per_chapter": 1200},
                                     goal=goal)
        self.assertEqual(run["status"], "done", run.get("error"))
        self.assertEqual([v["title"] for v in task["serial"]["volumes"]],
                         ["少年初入江湖", "风云再起"])
        ms = (self.workdir / "manuscript.md").read_text(encoding="utf-8")
        self.assertIn("## 第 1 卷 《少年初入江湖》", ms)
        self.assertIn("## 第 2 卷 《风云再起》", ms)

    def test_no_volumes_backward_compatible(self):
        """不分卷（旧任务形状）→ 成书无卷标题，行为与分卷前一致。"""
        task, run = self._run_serial({"chapters": 3, "words_per_chapter": 1200})
        self.assertEqual(run["status"], "done", run.get("error"))
        self.assertNotIn("volume_chapters", task["serial"])
        ms = (self.workdir / "manuscript.md").read_text(encoding="utf-8")
        self.assertNotIn("## 第 1 卷", ms)
        self.assertNotIn("volumes", run["verdict"])
        self.assertTrue(all("vol" not in c for c in run["chapter_scores"]))

    def test_continue_keeps_volume_boundaries(self):
        """跨批次卷边界稳定：续写批次落在第 2 卷，成书补齐第 1 卷标题。

        这是分卷设计的核心不变量——卷边界只由全书章号决定，所以第二批
        （第 3–4 章）必须进第 2 卷，且合并成书时第 1 卷的卷标题也要在
        （第一批写的 1–2 章同样被本批合进全书）。
        """
        from app.core import pipeline, store
        pipeline._agents = self.mock_agents
        t1 = store.create_task({
            "type": "serial_novel", "title": "分卷续写", "mode": "auto",
            "goal": "写一部短篇连载。第一卷 少年初入江湖 第1-2章；第二卷 风云再起 第3-4章",
            "workdir": str(self.workdir),
            "serial": {"chapters": 2, "words_per_chapter": 1200}, "threshold": 7.0})
        r1 = store.create_run("orchestration", t1["title"], task_id=t1["id"])
        pipeline.execute_run(r1["id"])
        self.assertEqual(store.get_run(r1["id"])["status"], "done")

        ok, err, t2 = store.continue_task(t1["id"], 2)
        self.assertTrue(ok, err)
        r2 = store.create_run("orchestration", t2["title"], task_id=t2["id"])
        pipeline.execute_run(r2["id"])
        run2 = store.get_run(r2["id"])
        self.assertEqual(run2["status"], "done", run2.get("error"))
        # 第二批的章节全部落在第 2 卷（卷边界没有被重切）
        self.assertEqual([c.get("vol") for c in run2["chapter_scores"]], [2, 2])
        # 合并成书覆盖全书：第 1 卷（第一批）与第 2 卷（本批）标题都在
        ms = (self.workdir / "manuscript.md").read_text(encoding="utf-8")
        self.assertIn("## 第 1 卷", ms)
        self.assertIn("## 第 2 卷", ms)
        self.assertLess(ms.index("## 第 1 卷"), ms.index("## 第 2 卷"))
        # 卷名跨批次沿用：用户给的卷名不会在第二批被改名
        self.assertEqual([v["title"] for v in run2["verdict"]["volumes"][:2]],
                         ["少年初入江湖", "风云再起"])
