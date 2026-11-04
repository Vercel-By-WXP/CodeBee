# -*- coding: utf-8 -*-
"""吸引力门（P0-B）单测：类型承诺兑现是可判的。

守四件事：

1. **判据认稿不认人**：《一头猎物，三笔账》前三章跑出来必须是「第 1、2 章
   无获得、第 3 章给出兑现段号」——这是平台驳回来的那份稿子，判据与它对齐
   才说明这一维真的被量到了。
2. **只判发生过的事**：「拟以腌肉换木料」「再决定家庭能拿到什么」是计划，
   不是获得；否定句里的动作用不能算。这一条决定闸门会不会天天误拦。
3. **认不出题材就不判**：把「年代打猎文」的承诺套到鉴宝文上，只会制造假拦截。
4. **模型只是兜底**：默认零网络、零额度；开启后模型也不能凭空造一条能顶开
   闸门的证据——它给的每一条引文都要在原文里核对得到。
"""
from __future__ import annotations

import socket
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from core.publish import publish_auth
from core import store
from core.quality import appeal


FIXTURE = Path(__file__).with_name("fixtures") / "appeal-yitou-liewu-ch1-5.md"

# 改造后的开场（按《签约驳回分析与新作调整方向》§3 写）：第 1 章见血见猎、
# 第 2 章第一笔现金进家、第 3 章完成「进城卖—换—家兑现」闭环。
IMPROVED = """# 第1章 雪地里的第一枪

血珠落在雪上，像撒了一把红豆。沈砚抬手一枪放倒那头带伤的野猪，獠牙还挂着冰碴。

三个人把猎物抬上爬犁，赵铁柱喘着气说这回总算没有白跑。

# 第2章 第一锅肉

回到柳河屯，沈砚把卖肉拿到的七十六元揣进怀里，先去卫生室把母亲的药买了回来。

晚上锅里炖着肉，孟兰英把煤票递给沈砚，说这个冬天不用再赊了。

# 第3章 一张新车票

第二天进城，沈砚把剩下的皮子卖了，拿到现金两百二十元。

他在供销社买了棉鞋和棉衣，把妹妹的报名费交上，回家时把车票拍在桌上。
"""

# 三章都没有获得：只有意向、计划和被否掉的交易。
EMPTY = """# 第1章 签字

沈砚在任务单上签了字，跟着林场队伍走到界桩前，看了半天脚印，最后决定今天不进山。

# 第2章 空手

回到屯里，沈砚说没有货。沈葵把三十一元写在账本上，又划掉一元。

孟兰英说拟以腌肉一块换木料一根，尺寸与交割待明日确认。

# 第3章 核界

县里来人核了边界，换发一张新附图。沈砚把旧附件交回登记，回家时天已经黑了。
"""


def _profile(**kw):
    base = dict(appeal.profiles()["hunt_1980s"])
    base.update(kw)
    return base


class AppealRulesTest(unittest.TestCase):
    def setUp(self):
        self.text = FIXTURE.read_text(encoding="utf-8") if FIXTURE.is_file() else ""
        if not self.text:
            self.skipTest("缺少回归样本 tests/fixtures/appeal-yitou-liewu-ch1-5.md")

    def _by_chapter(self, result):
        return {item["chapter"]: item for item in result["chapters"]}

    def test_real_manuscript_chapter_one_and_two_have_no_gain(self):
        result = appeal.analyze(self.text)
        chapters = self._by_chapter(result)
        self.assertEqual(chapters[1]["gains"], [])
        self.assertEqual(chapters[2]["gains"], [])
        joined = " ".join(result["blockers"])
        self.assertIn("第 1 章没有兑现任何「获得」", joined)
        self.assertIn("第 2 章没有兑现任何「获得」", joined)

    def test_real_manuscript_chapter_three_reports_position(self):
        result = appeal.analyze(self.text)
        third = self._by_chapter(result)[3]
        self.assertTrue(third["gains"])
        first = third["gains"][0]
        self.assertEqual(first["label"], "猎获")
        self.assertGreaterEqual(first["paragraph"], 1)
        self.assertGreaterEqual(first["sentence"], 1)
        self.assertTrue(first["text"].strip())
        self.assertFalse([b for b in result["blockers"] if "第 3 章" in b])

    def test_real_manuscript_chapter_four_is_short_and_empty_but_only_warns(self):
        # 原稿第 4 章 1406 字、零获得（投稿前内部审核早写过「1407 字是硬伤」）。
        # 门禁外单章不拦，但必须**看得见**——旧版只读前三章，这两条永远不会出现。
        result = appeal.analyze(self.text)
        rows = self._by_chapter(result)
        self.assertEqual(rows[4]["gains"], [])
        self.assertTrue(any("第 4 章仅 1406 字" in w for w in result["warnings"]))
        self.assertTrue(any("第 4 章没有兑现「获得」" in w for w in result["warnings"]))
        self.assertFalse([b for b in result["blockers"] if "第 4 章" in b])

    def test_real_manuscript_chapter_five_debt_cleared_counts(self):
        result = appeal.analyze(self.text)
        rows = self._by_chapter(result)
        self.assertEqual({gain["type"] for gain in rows[5]["gains"]}, {"home"})
        self.assertTrue(any("欠项清了" in gain["text"] for gain in rows[5]["gains"]))

    def test_improved_opening_passes(self):
        result = appeal.analyze(IMPROVED, profile=_profile(min_words=100))
        self.assertEqual(result["blockers"], [])
        for chapter in result["chapters"]:
            self.assertTrue(chapter["gains"], "第 %d 章应有获得" % chapter["chapter"])

    def test_every_chapter_missing_writes_one_blocker_each(self):
        result = appeal.analyze(EMPTY, profile=_profile(min_words=100))
        self.assertEqual(len(result["blockers"]), 3)
        for number in (1, 2, 3):
            self.assertTrue(any("第 %d 章" % number in b for b in result["blockers"]))

    def test_planned_trade_is_not_a_gain(self):
        # 「拟」「待明日确认」= 还没发生。这条误判会让闸门在第 2 章天天误拦，
        # 而它恰恰是《一头猎物》里真实存在的写法。
        sentence = "孟兰英说拟以腌肉一块换木料一根，尺寸与交割待明日确认。"
        self.assertEqual(appeal.gains_of(sentence), [])

    def test_negated_action_is_not_a_gain(self):
        for sentence in ("沈砚说今天没有货。",
                         "正式接收点未确认前，不分肉、不换物、不收现钱。"):
            self.assertEqual(appeal.gains_of(sentence), [], sentence)

    def test_only_first_three_chapters_are_gated_per_chapter(self):
        # 第 4 章写得再好也救不了开头：过签评估看的就是前三章。反过来，
        # 第 4 章起**不**逐章判（连载里的过渡章是正常写法），只参与连续空窗。
        text = EMPTY + "\n# 第4章 补一个\n\n沈砚把猎物抬上爬犁，货拿到了。\n"
        result = appeal.analyze(text, profile=_profile(min_words=100))
        self.assertEqual(result["chapter_count"], 4)
        rows = {row["chapter"]: row for row in result["chapters"]}
        self.assertTrue(rows[1]["gated"] and rows[3]["gated"])
        self.assertFalse(rows[4]["gated"])
        self.assertEqual(rows[4]["blockers"], [])
        for number in (1, 2, 3):
            self.assertTrue(any("第 %d 章" % number in b for b in result["blockers"]))

    def test_consecutive_empty_chapters_beyond_gate_are_blocked(self):
        # 「每几章一个小闭环」= 不许连着两章没有获得。只判单章会放过这一种：
        # 第 3 章给了东西、第 4 章过渡、第 5 章又空 —— 读者连着两章没拿到。
        text = IMPROVED + ("\n# 第4章 冰面\n\n窄溪边的薄冰塌下去，右侧滑橇卡在岸沿，"
                          "三个人先退回实地。\n"
                          "\n# 第5章 结算\n\n何满仓把单子推过来，说月底再核。\n")
        result = appeal.analyze(text, profile=_profile(min_words=100))
        joined = " ".join(result["blockers"])
        self.assertIn("第 4、5 章连续两章没有兑现任何「获得」", joined)
        self.assertEqual(len(result["blockers"]), 1)

    def test_single_empty_chapter_beyond_gate_only_warns(self):
        text = IMPROVED + ("\n# 第4章 冰面\n\n窄溪边的薄冰塌下去，右侧滑橇卡在岸沿，"
                          "三个人先退回实地。\n"
                          "\n# 第5章 结算\n\n沈砚把卖肉的钱拿到手，先还了药店的欠项。\n")
        result = appeal.analyze(text, profile=_profile(min_words=100))
        self.assertEqual(result["blockers"], [])
        self.assertTrue(any("第 4 章没有兑现「获得」（单章空窗" in w
                            for w in result["warnings"]))

    def test_debt_cleared_is_a_home_gain(self):
        # 假阴性比假阳性危险：第 5 章原话是「欠项清了」，若只认「买了/换了」
        # 这类加法动作，真稿唯一落地的那次获得就会被读成「零获得」。
        gains = appeal.gains_of("欠项清了，接下来领药买煤得重新付。家里现钱还是三十元。")
        self.assertEqual([g["type"] for g in gains], ["home"])

    def test_unpaid_debt_is_not_a_gain(self):
        # 「欠条摊在桌上」是债务存在，不是债务消失 —— 不能放宽成见到「欠」就算。
        gains = appeal.gains_of("林春禾先把药店和煤店的欠条摊在桌上，等月底再核。")
        self.assertEqual(gains, [])

    def test_short_chapter_only_warns(self):
        result = appeal.analyze(IMPROVED, profile=_profile(min_words=5000))
        self.assertEqual(result["blockers"], [])
        self.assertTrue(any("低于 5000 字" in w for w in result["warnings"]))

    def test_promise_table_marks_unjudged_promises(self):
        result = appeal.analyze(self.text)
        rows = {row["id"]: row for row in result["promises"]}
        self.assertFalse(rows["enter_fast"]["judged"])
        self.assertTrue(rows["gain_traceable"]["judged"])
        self.assertEqual(rows["gain_traceable"]["covered"][0]["chapter"], 3)


class AppealModelFallbackTest(unittest.TestCase):
    def test_model_unavailable_degrades_to_rules(self):
        def boom(chapters, profile):
            raise RuntimeError("model offline")

        with_model = appeal.analyze(EMPTY, profile=_profile(min_words=100),
                                   use_model=True, model=boom)
        plain = appeal.analyze(EMPTY, profile=_profile(min_words=100))
        self.assertEqual(with_model["blockers"], plain["blockers"])
        self.assertFalse(with_model["model_used"])
        self.assertTrue(with_model["model_note"].startswith("error:"))

    def test_model_cannot_invent_evidence(self):
        # 模型说第 2 章有现金，但引文不在原文里 —— 不许采纳，闸门照拦。
        def liar(chapters, profile):
            return {"chapters": [{"chapter": 2, "gains": [
                {"type": "cash", "paragraph": 1, "quote": "拿到现金五百元"}]}]}

        result = appeal.analyze(EMPTY, profile=_profile(min_words=100),
                                use_model=True, model=liar)
        self.assertTrue(any("第 2 章" in b for b in result["blockers"]))
        self.assertIn("丢弃 1 条", result["model_note"])

    def test_model_quote_that_exists_is_accepted(self):
        # 真引文（在原文里能核对到）才作为补充证据；这一条决定兜底有没有用。
        def honest(chapters, profile):
            return {"chapters": [{"chapter": 1, "gains": [
                {"type": "hunt", "paragraph": 1, "quote": "看了半天脚印"}]}]}

        result = appeal.analyze(EMPTY, profile=_profile(min_words=100),
                                use_model=True, model=honest)
        self.assertFalse([b for b in result["blockers"] if "第 1 章" in b])
        self.assertTrue(result["model_used"])

    def test_rules_never_open_a_socket(self):
        def no_network(*args, **kwargs):
            raise AssertionError("吸引力门不允许联网")

        with mock.patch.object(socket, "socket", no_network), \
                mock.patch.object(socket, "create_connection", no_network):
            result = appeal.analyze(IMPROVED, profile=_profile(min_words=100))
        self.assertEqual(result["blockers"], [])
        self.assertFalse(result["model_used"])


class AppealTaskScopeTest(unittest.TestCase):
    """认不出题材 / 没有正文 / 不是长篇 —— 一个字都不许拦。"""

    def _task(self, root, **kw):
        base = {"id": "t-appeal", "type": "serial_novel", "workdir": str(root)}
        base.update(kw)
        return base

    def test_text_without_known_genre_is_not_judged(self):
        with tempfile.TemporaryDirectory() as tmp:
            Path(tmp, "chapter-01.md").write_text(
                "# 第1章 碎镜开瞳\n\n江野把电动车支在台阶下，打量面前这只纸箱。\n",
                encoding="utf-8")
            result = appeal.check_task(self._task(tmp))
        self.assertFalse(result["enabled"])
        self.assertEqual(result["reason"], "unknown_genre")
        self.assertEqual(result["blockers"] if "blockers" in result else [], [])

    def test_missing_chapters_is_not_judged(self):
        with tempfile.TemporaryDirectory() as tmp:
            result = appeal.check_task(self._task(tmp))
        self.assertFalse(result["enabled"])
        self.assertEqual(result["reason"], "no_text")

    def test_short_story_is_not_judged(self):
        with tempfile.TemporaryDirectory() as tmp:
            result = appeal.check_task(self._task(tmp, type="short_story"))
        self.assertFalse(result["enabled"])
        self.assertEqual(result["reason"], "not_serial")

    def test_review_engine_alone_does_not_opt_in(self):
        # 文档/文章/调研报告也走 review 引擎。放宽到 engine 判断，等于把打猎文
        # 的承诺套到任何 workdir 里碰巧有 chapter-NN.md 的任务上。
        with tempfile.TemporaryDirectory() as tmp:
            for number, body in enumerate(EMPTY.split("# 第")[1:], 1):
                Path(tmp, "chapter-%02d.md" % number).write_text(
                    "# 第" + body, encoding="utf-8")
            result = appeal.check_task(self._task(tmp, type="doc", engine="review"))
        self.assertFalse(result["enabled"])
        self.assertEqual(result["reason"], "not_serial")

    def test_hunting_text_is_detected_and_judged(self):
        with tempfile.TemporaryDirectory() as tmp:
            for number, body in enumerate(EMPTY.split("# 第")[1:], 1):
                Path(tmp, "chapter-%02d.md" % number).write_text(
                    "# 第" + body, encoding="utf-8")
            result = appeal.check_task(self._task(tmp))
        self.assertTrue(result["enabled"])
        self.assertEqual(result["genre"], "hunt_1980s")
        self.assertEqual(len(result["blockers"]), 3)


class AppealGateIntegrationTest(unittest.TestCase):
    """闸门只看一件事：证据有没有进 blockers、放行有没有留证。"""

    VERDICT = {"publishable": True, "scores": {"logic": 8, "prose": 8}}

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.root = Path(self._tmp.name)
        for number, body in enumerate(EMPTY.split("# 第")[1:], 1):
            (self.root / ("chapter-%02d.md" % number)).write_text(
                "# 第" + body, encoding="utf-8")
        self.task = {"id": "t-appeal-gate", "type": "novel",
                     "title": "一头猎物", "workdir": str(self.root)}
        runs = [{"id": "r1", "status": "done", "verdict": dict(self.VERDICT)}]
        self._patch = mock.patch.object(store, "task_runs", return_value=runs)
        self._patch.start()

    def tearDown(self):
        self._patch.stop()
        self._tmp.cleanup()

    def guard(self, **flags):
        base = {"force": False, "force_confirmed": False, "force_reason": "",
                "force_origin": ""}
        base.update(flags)
        return publish_auth._quality_release_guard(
            self.task, "fanqie", action="publish", target_chapter=1, **base)

    def test_appeal_blockers_land_in_release_decision(self):
        decision = self.guard()
        self.assertFalse(decision["allowed"])
        self.assertTrue(any("没有兑现任何「获得」" in b
                            for b in decision["blockers"]))
        self.assertTrue(decision["appeal"]["enabled"])
        self.assertEqual(decision["appeal"]["genre"], "hunt_1980s")

    def test_manual_force_still_releases_and_keeps_evidence(self):
        decision = self.guard(force=True, force_confirmed=True,
                              force_reason="人工复核后放行", force_origin="manual")
        self.assertTrue(decision["allowed"])
        self.assertTrue(decision["forced"])
        self.assertTrue(any("没有兑现任何「获得」" in b
                            for b in decision["overridden"]))

    def test_draft_is_not_blocked_by_appeal(self):
        decision = publish_auth._quality_release_guard(
            self.task, "fanqie", action="draft",
            force=False, force_confirmed=False, force_reason="", force_origin="")
        self.assertTrue(decision["allowed"])
        self.assertFalse(decision["appeal"]["enabled"])

    def test_judge_failure_degrades_and_tells_the_author(self):
        with mock.patch.object(appeal, "check_task", side_effect=RuntimeError("坏了")):
            decision = self.guard()
        self.assertTrue(decision["allowed"])
        self.assertTrue(any("吸引力门执行失败" in w
                            for w in decision["warnings"]))


if __name__ == "__main__":
    unittest.main()
