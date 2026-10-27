# -*- coding: utf-8 -*-
"""连载流水线 P2-1 拆分后的回归：三段串联仍能跑完并产出成稿。

背景：`_run_serial_review` 原为 2366 行单函数，2026-10-27 按阶段拆成
`_serial_prepare` / `_serial_chapters` / `_serial_finalize` 三段，状态用
`SimpleNamespace` 显式传递。全量套件里没有任何用例真正跑过连载主循环，
拆完必须有一条真的跑通「大纲 → 逐章 → 全局评审 → 合并」的用例兜底，
否则下次再动这里只有「import 没报错」这一层保护。
"""
import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from app.core import pipeline

REPO = Path(__file__).resolve().parents[1]

PROBE = r"""
import json, os, sys
from unittest import mock

from app.core import pipeline, store

workdir = sys.argv[1]
task = {
    "type": "serial_novel",
    "title": "拆分回归连载",
    "goal": "写一个关于旧书店的悬疑故事，主角靠一本账本追查失踪者",
    "workdir": workdir,
    "manuscript": "manuscript.md",
    "rubric": ["情节", "人物", "文笔", "节奏"],
    "threshold": 7.0,
    "rounds": 1,
    "serial": {"chapters": 2, "words_per_chapter": 600, "start_chapter": 1},
}
agents = [
    {"id": "author-mock", "mode": "mock", "label": "MockAuthor"},
    {"id": "critic-a", "mode": "mock", "label": "MockCriticA"},
    {"id": "critic-b", "mode": "mock", "label": "MockCriticB"},
]
calls = []

PROSE = ("雨停在旧书店的玻璃上，像一层没擦干净的旧漆。"
         "林昭把账本翻到第九页，指腹压住那行被水洇开的数字，"
         "听见阁楼传来第三次同样的响动。他没有回头，"
         "只把台灯拧亮了一格，让光落在账本边缘的折痕上。"
         "折痕是新的，说明有人在他之前来过，而且翻到了同一页。"
         "他合上账本，把钥匙从锁孔里退出来，钥匙齿上沾着一点铁锈的红。"
         "楼下有人敲门，敲了三下，停两拍，再敲一下——这是他和失踪者约好的暗号。"
         "他站在原地数自己的呼吸，数到第七下才走过去。") * 3


def fake_step(run_id, role, agent, prompt, workdir, readonly, ev, **kw):
    calls.append(role)
    low = (role or "").lower()
    if "critique" in low or "global" in low or "review" in low:
        body = {"scores": {d: 8.2 for d in task["rubric"]},
                "issues": [], "summary": "ok"}
        return {"ok": True, "text": json.dumps(body, ensure_ascii=False),
                "json": None, "error": "", "error_code": "",
                "raw": {"exit_code": 0}}
    return {"ok": True, "text": PROSE, "json": None, "error": "",
            "error_code": "", "raw": {"exit_code": 0}}


t = store.create_task(task)
run = store.create_run("orchestration", task["title"], task_id=t["id"])
store.update_run(run["id"], status="running")
critics = [agents[1], agents[2]]

with mock.patch.object(pipeline, "_run_step", side_effect=fake_step), \
     mock.patch.object(pipeline, "_run_builtin_step", side_effect=fake_step), \
     mock.patch.object(pipeline.modelhub, "bind_agent",
                       side_effect=lambda a, d="default": a), \
     mock.patch.object(pipeline.router, "pick",
                       return_value=(agents[0], "mock author")), \
     mock.patch.object(pipeline.router, "pick_critics",
                       return_value=(critics, "mock critics")), \
     mock.patch.object(pipeline.router, "pick_reviewer",
                       return_value=(agents[2], "mock reviewer")), \
     mock.patch.object(pipeline.router, "pick_switch_candidate",
                       return_value=None), \
     mock.patch.object(pipeline.time, "sleep", lambda *_a, **_k: None):
    err = ""
    try:
        pipeline._run_serial_review({"id": run["id"]}, task, agents, None, {},
                                    "auto", critics, agents[0], {}, None, "default")
    except Exception as exc:      # 异常本身就是要断言的失败信号
        err = "%s: %s" % (type(exc).__name__, exc)

r = store.get_run(run["id"]) or {}
ms = os.path.join(workdir, task["manuscript"])
chapters = sorted(x for x in os.listdir(workdir)
                  if x.startswith("chapter-") and x.endswith(".md"))
report = ""
rp = store.run_dir(run["id"]) / "report.md"
if rp.exists():
    report = rp.read_text(encoding="utf-8")
print("@@RESULT@@" + json.dumps({
    "status": r.get("status"),
    "error": (r.get("error") or err)[:400],
    "chapters": chapters,
    "manuscript_chars": (os.path.getsize(ms) if os.path.exists(ms) else 0),
    "manuscript_head": (open(ms, encoding="utf-8").read()[:200]
                        if os.path.exists(ms) else ""),
    "report_chars": len(report),
    "report_has_chapter_1": "第1章" in report or "第 1 章" in report,
    "report_has_global": "全局评审" in report,
    "verdict_keys": sorted((r.get("verdict") or {}).keys()),
    "verdict": {k: (r.get("verdict") or {}).get(k)
                for k in ("chapters_used", "total_words", "publishable",
                          "quality_status", "review_status")},
    "calls": len(calls),
}, ensure_ascii=True))
"""


class SerialPipelineShapeTests(unittest.TestCase):
    """P2-1 的验收：编排函数本身必须薄，阶段函数必须各自存在。"""

    def test_orchestrator_is_thin(self):
        src = pipeline._run_serial_review.__doc__ or ""
        self.assertIn("_serial_prepare", src)
        self.assertIn("_serial_chapters", src)
        self.assertIn("_serial_finalize", src)
        import inspect
        lines = inspect.getsource(pipeline._run_serial_review).splitlines()
        self.assertLess(len(lines), 40,
                        "_run_serial_review 又变厚了（%d 行）：新逻辑请落进阶段函数"
                        % len(lines))

    def test_phase_functions_exist(self):
        for name in ("_serial_prepare", "_serial_chapters", "_serial_finalize"):
            fn = getattr(pipeline, name, None)
            self.assertTrue(callable(fn), "缺少阶段函数 %s" % name)
            self.assertTrue((fn.__doc__ or "").strip(), "%s 缺 docstring" % name)

    def test_prepare_returns_state_namespace(self):
        # prepare 失败（故事追踪不可用）返回 None，正常返回带 run_id 的命名空间；
        # 这里只校验「返回类型契约」不依赖具体 run。
        import inspect
        src = inspect.getsource(pipeline._serial_prepare)
        self.assertIn("return SimpleNamespace(**locals())", src)


class SerialChapterStageTests(unittest.TestCase):
    """P2-1 第二步：`_serial_chapters`（1335 行）再切成单章流水。

    整章循环体按「上下文 / 评审执行器 / 起草 / 评审 / 检查点」切开，跨段状态
    只保留 prefix_words 这一个整数（返回后回写 st），其余都是就地追加的容器。
    """

    STAGES = ("_serial_event_block", "_serial_one_chapter",
              "_serial_chapter_context", "_serial_critique_factory",
              "_serial_reused_chapter", "_serial_draft_chapter",
              "_serial_review_chapter", "_serial_signing_checkpoint")

    def test_stage_functions_exist_with_docstring(self):
        for name in self.STAGES:
            fn = getattr(pipeline, name, None)
            self.assertTrue(callable(fn), "缺少阶段函数 %s" % name)
            self.assertTrue((fn.__doc__ or "").strip(), "%s 缺 docstring" % name)

    def test_chapter_loop_stays_thin(self):
        import inspect
        src = pipeline._serial_chapters.__doc__ or ""
        self.assertIn("_serial_one_chapter", src)
        lines = inspect.getsource(pipeline._serial_chapters).splitlines()
        self.assertLess(len(lines), 20,
                        "_serial_chapters 又长出逻辑了（%d 行）：单章逻辑请落进 "
                        "_serial_one_chapter 及其阶段函数" % len(lines))
        one = inspect.getsource(pipeline._serial_one_chapter)
        self.assertLess(len(one.splitlines()), 40,
                        "_serial_one_chapter 只该做串联（%d 行）"
                        % len(one.splitlines()))
        for callee in ("_serial_chapter_context", "_serial_critique_factory",
                       "_serial_draft_chapter", "_serial_review_chapter",
                       "_serial_signing_checkpoint"):
            self.assertIn(callee, one, "单章流水漏了 %s" % callee)

    def test_stage_functions_stay_under_budget(self):
        """防回涨：任一段再膨胀回千行级就该继续拆。"""
        import inspect
        budgets = {"_serial_draft_chapter": 620, "_serial_review_chapter": 480,
                   "_serial_signing_checkpoint": 220, "_serial_chapter_context": 140,
                   "_serial_critique_factory": 300}
        for name, budget in budgets.items():
            n = len(inspect.getsource(getattr(pipeline, name)).splitlines())
            self.assertLess(n, budget, "%s 涨到 %d 行（预算 %d），该继续拆"
                            % (name, n, budget))

    def test_moved_helper_replaced(self):
        """原闭包 build_event_block 已升为模块级 _serial_event_block。"""
        import inspect
        src = inspect.getsource(pipeline._serial_chapters)
        self.assertNotIn("build_event_block", src)
        self.assertTrue(hasattr(pipeline, "_serial_event_block"))

    def test_event_block_embeds_volume_context(self):
        out = pipeline._serial_event_block(
            {"beats": "甲：进山", "hook": "乙：信来了", "highlight": "丙：摔杯"},
            "本卷上下文：第 2 卷，卷末章")
        self.assertIn("剧情要点：甲：进山", out)
        self.assertIn("章末钩子：乙：信来了", out)
        self.assertIn("丙：摔杯", out)
        self.assertIn("本卷上下文：第 2 卷，卷末章", out)
        # 大纲缺项要走兜底文案，不能出现 None
        out2 = pipeline._serial_event_block({}, "")
        self.assertIn("按大纲推进", out2)
        self.assertNotIn("None", out2)

    def test_reused_chapter_records_score_without_review(self):
        from types import SimpleNamespace
        from unittest import mock

        st = SimpleNamespace(
            chapter_scores=[], inh_scores={3: {"words": 500, "means": {"情节": 8.0}}},
            prefix_words=100, run_id="run-x", vol_plan=None)
        with mock.patch.object(pipeline.store, "update_run") as upd:
            out = pipeline._serial_reused_chapter(st, 3)
        self.assertEqual(600, out, "累计字数必须把复用章字数加回去")
        self.assertEqual(1, len(st.chapter_scores))
        cs = st.chapter_scores[0]
        self.assertTrue(cs["reused"])
        self.assertEqual(3, cs["chapter"])
        self.assertEqual({"情节": 8.0}, cs["means"])
        upd.assert_called_once()

    def test_signing_checkpoint_passthrough_when_not_due(self):
        from types import SimpleNamespace
        from unittest import mock

        st = SimpleNamespace(
            _signing_eval=lambda *a, **k: ({}, [], ""), chapter_scores=[],
            crit_prompt_for=lambda *a, **k: "", critics=[], difficulty="default",
            dims=["情节"], end=9, ev=None, impl={"mode": "real"}, issues_all=[],
            outline={"chapters": []}, rubric_block="", run_id="run-y",
            signing_review_meta={}, start=1, story_tracking=None, style_tpl="",
            task={"goal": "g"}, threshold=8.0, threshold_ch=7.0,
            vol_block_for=lambda i: "", workdir="/tmp", wpc=1000)
        with mock.patch.object(pipeline, "_signing_checkpoint_due",
                               return_value=False), \
                mock.patch.object(pipeline.store, "get_run", return_value={}):
            self.assertEqual(1234, pipeline._serial_signing_checkpoint(st, 1, {}, 1234))

    def test_critique_factory_returns_shared_buckets(self):
        from types import SimpleNamespace

        st = SimpleNamespace(
            _bench_noted=[], actual_critics=[], agents=[], crit_prompt_for=None,
            critics=[], difficulty="default", dims=["情节"], ev=None,
            impl={"mode": "mock"}, issues_all=[], mode="auto",
            refresh_actual_route=None, remember_agent=None,
            review_degradation_reasons=[], run_id="run-z", stats={},
            task={"goal": "g"}, threshold_ch=7.0, workdir="/tmp")
        run_critique, ev_check_lines, ledger_lines = \
            pipeline._serial_critique_factory(st, 5)
        self.assertTrue(callable(run_critique))
        self.assertEqual([[]], ev_check_lines)
        self.assertEqual([], ledger_lines)
        # 关键：返回的两个桶必须就是闭包内就地填充的那两个对象，
        # 否则调用方读到的永远是空列表。
        cells = [c.cell_contents for c in (run_critique.__closure__ or ())
                 if getattr(c, "cell_contents", None) is not None]
        self.assertTrue(any(c is ev_check_lines for c in cells),
                        "ev_check_lines 不是闭包捕获的同一个对象")
        self.assertTrue(any(c is ledger_lines for c in cells),
                        "ledger_lines 不是闭包捕获的同一个对象")


class SerialPipelineEndToEndTests(unittest.TestCase):
    def test_serial_run_produces_manuscript(self):
        with tempfile.TemporaryDirectory() as tmp:
            ws = os.path.join(tmp, "ws")
            os.makedirs(ws)
            env = dict(os.environ)
            env["TUTTI_DATA"] = tmp
            env["PYTHONIOENCODING"] = "utf-8"
            proc = subprocess.run(
                [sys.executable, "-c", PROBE, ws],
                cwd=str(REPO), env=env, timeout=300,
                capture_output=True, text=True)
            out = proc.stdout or ""
            self.assertIn("@@RESULT@@", out,
                          "探针未产出结果：stdout=%s stderr=%s"
                          % (out[-1500:], (proc.stderr or "")[-1500:]))
            data = json.loads(out.split("@@RESULT@@", 1)[1].strip().splitlines()[0])
            self.assertEqual("", data["error"], data["error"])
            self.assertEqual("done", data["status"])
            self.assertEqual(["chapter-01.md", "chapter-02.md"], data["chapters"])
            self.assertGreater(data["manuscript_chars"], 400)
            self.assertIn("拆分回归连载", data["manuscript_head"])
            self.assertGreater(data["report_chars"], 100)
            self.assertTrue(data["report_has_chapter_1"])
            self.assertIn("publishable", data["verdict_keys"])
            self.assertIn("chapters_used", data["verdict_keys"])
            self.assertEqual(2, data["verdict"]["chapters_used"])
            self.assertGreater(data["verdict"]["total_words"], 300)
            # 全局评审段只有 finalize 会写：证明第三段真的跑到了报告落盘
            self.assertTrue(data["report_has_global"], "报告缺全局评审段")


if __name__ == "__main__":
    unittest.main()
