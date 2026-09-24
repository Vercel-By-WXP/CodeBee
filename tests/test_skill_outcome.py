# -*- coding: utf-8 -*-
"""教训 outcome 加权 + **精确归因**单测：注入登记 → 按单条归因回写胜负 →
收缩后 karma 参与排序。

归因纪律（2026-09-24 改造）：旧实现给整批 top-8 统一记 +/-1，一次失败把不相干的
教训一起打成负分沉底。现要求 penalize/reward 显式点名，未点名不落账。
"""
from __future__ import annotations

import re

from base import BaseTest


class OutcomeKarmaTests(BaseTest):
    @staticmethod
    def _karma_of(lesson, key):
        """won/lost 按需写入，未落账时字段缺省 —— 断言按 0 计。"""
        return int(lesson.get(key) or 0)

    def _skills(self):
        import app.core.skills as sk
        sk._FILE = self.data_dir / "skills.json"
        sk._INJECTED.clear()
        return sk

    def test_block_for_registers_and_outcome_writes(self):
        sk = self._skills()
        a = sk.upsert_lesson("code", "好教训", "能防住问题的具体做法" * 3)
        sk.block_for({"type": "code", "goal": "做事"}, run_id="run-x")
        self.assertIn("run-x", sk._INJECTED)
        # 点名 + 过审 → won+1；幂等：登记已消费，第二次调用不重复记
        sk.note_outcome("run-x", True, reward=[a["id"]])
        les = next(l for l in sk.list_lessons("code") if l["id"] == a["id"])
        self.assertEqual(les.get("won"), 1)
        self.assertNotIn("run-x", sk._INJECTED)
        sk.note_outcome("run-x", True, reward=[a["id"]])
        les = next(l for l in sk.list_lessons("code") if l["id"] == a["id"])
        self.assertEqual(les.get("won"), 1)

    def test_failed_run_marks_lost(self):
        sk = self._skills()
        b = sk.upsert_lesson("code", "没用教训", "防不住问题的空泛说法" * 3)
        sk.block_for({"type": "code", "goal": "做事"}, run_id="run-y")
        sk.note_outcome("run-y", False, penalize=[b["id"]])
        les = next(l for l in sk.list_lessons("code") if l["id"] == b["id"])
        self.assertEqual(les.get("lost"), 1)

    def test_unnamed_lesson_is_not_buried_with_the_batch(self):
        """核心回归：整批注入不连坐。失败 run 只惩罚被点名的那条。"""
        sk = self._skills()
        guilty = sk.upsert_lesson("code", "伏笔回收教训", "伏笔必须在本卷内回收" * 3)
        innocent = sk.upsert_lesson("code", "对白格式教训", "对白独立成段" * 3)
        sk.block_for({"type": "code", "goal": "写一卷"}, run_id="run-z")
        sk.note_outcome("run-z", False, penalize=[guilty["id"]])
        items = {l["id"]: l for l in sk.list_lessons("code")}
        self.assertEqual(items[guilty["id"]].get("lost"), 1)
        self.assertEqual(self._karma_of(items[innocent["id"]], "lost"), 0)
        self.assertEqual(self._karma_of(items[innocent["id"]], "won"), 0)

    def test_no_attribution_settles_nothing(self):
        """归因缺失 = 本次无信号，宁可不落账（旧行为是给全批虚增 won）。"""
        sk = self._skills()
        a = sk.upsert_lesson("code", "无信号教训", "具体做法" * 3)
        sk.block_for({"type": "code", "goal": "做事"}, run_id="run-n")
        sk.note_outcome("run-n", True)
        sk.note_outcome("run-n", False, penalize=[], reward=[])
        les = next(l for l in sk.list_lessons("code") if l["id"] == a["id"])
        self.assertEqual(self._karma_of(les, "won"), 0)
        self.assertEqual(self._karma_of(les, "lost"), 0)
        self.assertNotIn("run-n", sk._INJECTED)   # 登记仍被消费，不泄漏

    def test_run_outcome_is_three_state(self):
        """结局判定表。字段组合取自实跑 110 个 run 中真实出现过的 verdict 形状——
        旧实现 bool(verdict.get("pass")) 会把其中 75 个「无结论」run 全判成质量失败。"""
        sk = self._skills()
        cases = [
            ({"status": "done", "verdict": {"pass": True}}, True),
            ({"status": "done", "verdict": {"pass": False, "verify_pass": True,
                                            "review_pass": False}}, False),
            ({"status": "done", "verdict": {"publishable": True}}, True),
            ({"status": "done", "verdict": {"publishable": False, "global_pass": False}}, False),
            ({"status": "done", "verdict": {"verify_pass": True, "review_pass": True}}, True),
            ({"status": "done", "verdict": {}}, None),                    # done 但没结论
            ({"status": "done"}, None),
            ({"status": "failed", "verdict": {"pass": False}}, None),     # 链路失败≠质量失败
            ({"status": "cancelled", "verdict": {"pass": False}}, None),  # 取消不进任何惩罚
            ({"status": "timeout", "verdict": None}, None),
            ({"status": "running"}, None),
        ]
        for run, want in cases:
            self.assertIs(sk._run_outcome(run), want, run)

    def test_unknown_outcome_consumes_without_blaming(self):
        sk = self._skills()
        a = sk.upsert_lesson("code", "无结局条目", "具体做法" * 3)
        sk.block_for({"type": "code", "goal": "做事"}, run_id="run-u")
        sk.note_outcome("run-u", None, penalize=[a["id"]])
        les = next(l for l in sk.list_lessons("code") if l["id"] == a["id"])
        self.assertEqual(self._karma_of(les, "lost"), 0)
        self.assertNotIn("run-u", sk._INJECTED)   # 登记照样消费

    def test_only_registered_ids_are_settled(self):
        """编造或本 run 未注入的编号不认账；另一侧结局的写入被忽略。"""
        sk = self._skills()
        a = sk.upsert_lesson("code", "甲乙丙在册项", "具体做法" * 3)
        # 标题刻意不与前一条共享 bigram：近似题（包含度≥0.8）会被合并成同一条
        off = sk.upsert_lesson("code", "丁戊己旁支项", "另一条具体做法" * 3)
        sk.lesson_op(off["id"], "disable")     # 停用 → 不会被注入，也就无归因资格
        sk.block_for({"type": "code", "goal": "做事"}, run_id="run-s")
        sk.note_outcome("run-s", False, penalize=[a["id"], off["id"], "sk-notreal"])
        # 登记已消费：再按过审清算一次不应有任何写入
        sk.note_outcome("run-s", True, reward=[a["id"]])
        items = {l["id"]: l for l in sk.list_lessons("code")}
        self.assertEqual(self._karma_of(items[a["id"]], "lost"), 1)
        self.assertEqual(self._karma_of(items[a["id"]], "won"), 0)
        self.assertEqual(self._karma_of(items[off["id"]], "lost"), 0)

    def test_attribute_splits_by_category(self):
        """A 路归因：与问题同类别的挨罚，与达标维度同类别且不冲突的记功。"""
        sk = self._skills()
        plot = sk.upsert_lesson("novel", "伏笔断线", "伏笔要登记回收", category="情节逻辑")
        style = sk.upsert_lesson("novel", "措辞重复", "减少口癖", category="文笔风格")
        loose = sk.upsert_lesson("novel", "无类别条目", "归不了类的条目")
        by_id = {x["id"]: x for x in sk.list_lessons("novel")}
        reg = [plot["id"], style["id"], loose["id"]]
        pen, rew = sk._attribute(reg, by_id,
                                 issues=[{"dim": "情节逻辑", "note": "伏笔没回收"}],
                                 strengths=[{"dim": "文笔"}])
        self.assertEqual(pen, [plot["id"]])
        self.assertEqual(rew, [style["id"]])       # 无类别那条两侧都不动

    def test_multi_dim_issue_matches_every_segment(self):
        sk = self._skills()
        self.assertEqual(sk._issue_cats({"dim": "钩子、爽点"}), {"节奏爽点"})
        self.assertEqual(sk._issue_cats({"dim": "报告", "note": "字数不达标"}), {"流程规范"})

    def test_learn_from_run_consumes_outcome(self):
        """run 收尾真实路径：learn_from_run 一定消费注入登记。mock 评审无维度
        → 无可归因信号 → 不得给整批虚增胜负（这正是本次改造要修的偏差）。"""
        from app.core import store, pipeline
        sk = self._skills()
        c = sk.upsert_lesson("code", "链路教训", "走真实收尾链验证" * 3)
        task = store.create_task({"type": "code", "title": "outcome", "goal": "g",
                                  "workdir": str(self.workdir), "mode": "auto",
                                  "verify_command": "exit 0"})
        run = store.create_run("orchestration", task["title"], task_id=task["id"])
        sk.block_for({"type": "code", "goal": "g"}, run_id=run["id"])
        pipeline._agents = self.mock_agents
        pipeline.execute_run(run["id"])
        r = store.get_run(run["id"])
        self.assertEqual("done", r["status"])
        self.assertTrue((r.get("verdict") or {}).get("pass"))
        # 收尾沉淀是 daemon 线程（learn_async），断言前同步补跑一次消除时序依赖：
        # 无论线程是否已抢先把登记消费掉，结果都必须一致。
        sk.learn_from_run(run["id"], use_orchestrator=False)
        self.assertNotIn(run["id"], sk._INJECTED)  # 登记已消费
        les = next(l for l in sk.list_lessons("code") if l["id"] == c["id"])
        self.assertEqual(self._karma_of(les, "won"), 0)

    def test_injected_ids_are_visible_for_citation(self):
        """B 路前提：注入块里带教训编号，复盘官才有点名的抓手。"""
        sk = self._skills()
        a = sk.upsert_lesson("code", "带编号教训", "具体做法" * 3)
        block, _ = sk.block_for({"type": "code", "goal": "做事"})
        self.assertIn("[%s]" % a["id"], block)

    def test_learn_prompt_placeholders_are_all_wired(self):
        """新增占位符必须同时在 learn_from_run 里替换，否则原样进提示词。"""
        sk = self._skills()
        self.assertEqual(set(re.findall(r"__[A-Z_]+__", sk.LEARN_PROMPT)),
                         {"__CATEGORIES__", "__TYPE__", "__GOAL__", "__INJECTED__",
                          "__VERDICT__", "__ISSUES__"})

    def test_karma_is_exposure_aware(self):
        """可信度分母是注入次数：注入 1175 次只失守 3 次的，必须排在注入 4 次
        失守 1 次的之前；从未注入的落中性值，既不拔高也不打压。"""
        sk = self._skills()
        self.assertGreater(sk._karma({"hits": 1175, "lost": 3}),
                           sk._karma({"hits": 4, "lost": 1}))
        self.assertAlmostEqual(sk._karma({"hits": 0, "lost": 0}), 0.5, places=6)
        self.assertGreater(sk._karma({"hits": 2, "lost": 0}), 0.5)     # 正面证据要高于无证据
        self.assertLess(sk._karma({"hits": 5, "lost": 2}),
                        sk._karma({"hits": 500, "lost": 2}))
        self.assertEqual(sk._karma({"hits": 2, "lost": 9}),
                         sk._karma({"hits": 2, "lost": 2}))            # 倒挂台账不炸

    def test_ranking_prefers_positive_karma(self):
        """同相关性下：可信度与归因成功次数共同决定 top-k 归属。"""
        sk = self._skills()
        good = sk.upsert_lesson("code", "共享标题甲", "具体做法甲" * 3)
        bad = sk.upsert_lesson("code", "共享标题乙", "具体做法乙" * 3)
        mid = sk.upsert_lesson("code", "共享标题丙", "具体做法丙" * 3)
        with sk._LOCK:
            data = sk._load()
            for it in data["lessons"]:
                if it["id"] == good["id"]:
                    it["hits"], it["won"] = 200, 5
                elif it["id"] == bad["id"]:
                    it["hits"], it["lost"] = 200, 4
                elif it["id"] == mid["id"]:
                    it["hits"] = 200
            sk._save(data)
        top = sk.relevance_top(
            sk.list_lessons("code", only_enabled=True),
            {"type": "code", "goal": "共享标题"}, 2)   # limit<总数才触发排序
        ids = [x["id"] for x in top]
        self.assertEqual(ids[0], good["id"])           # 同可信度下 won 提权
        self.assertNotIn(bad["id"], ids)               # 4 笔真实归因失守被挤出

    def test_registration_is_union_across_steps(self):
        """同一 run 多步注入必须并集登记：覆盖式登记会让先注入那批永远拿不到归因。"""
        sk = self._skills()
        a = sk.upsert_lesson("code", "评审步教训", "具体做法" * 3)
        sk._INJECTED["run-m"] = ["sk-earlier"]        # 模拟大纲步先登记过一条
        sk.block_for({"type": "code", "goal": "做事"}, run_id="run-m")
        self.assertEqual(sk._INJECTED["run-m"], sorted(["sk-earlier", a["id"]]))

    def test_run_id_reaches_planner(self):
        """归因登记要穿过 _planner_call 抵达 planner.make_serial_outline；
        旧测试替身不接收新 kwarg 时，只退回报错点名那一个，且不吞掉无关 TypeError。"""
        import inspect
        from app.core import pipeline, planner
        self.assertIn("run_id", inspect.signature(planner.make_serial_outline).parameters)
        got = {}

        def double(task, author_agent=None, deadline=None, run_id=None):
            got.update(deadline=deadline, run_id=run_id)
            return "ok"
        self.assertEqual("ok", pipeline._planner_call(double, {}, deadline=7, run_id="r-1"))
        self.assertEqual({"deadline": 7, "run_id": "r-1"}, got)

        def legacy(task):                              # 两个新 kwarg 都不接收
            return "legacy"
        self.assertEqual("legacy", pipeline._planner_call(legacy, {}, deadline=7, run_id="r-1"))

        def unrelated(task):                           # 替身内部真错，不能被兜底吞掉
            raise TypeError("boom about 'zzz'")
        self.assertRaises(TypeError, pipeline._planner_call, unrelated, {}, deadline=7)

    def test_injected_map_bounded(self):
        sk = self._skills()
        for i in range(205):
            sk.block_for({"type": "code", "goal": "x%d" % i}, run_id="r%d" % i)
        self.assertLess(len(sk._INJECTED), 205)       # 上限触发整体作废


if __name__ == "__main__":
    import unittest
    unittest.main()
