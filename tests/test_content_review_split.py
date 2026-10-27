# -*- coding: utf-8 -*-
"""内容评审流水线 P2-1 第三刀的回归：四段串联仍能跑完并产出报告。

背景：`_run_content_review` 原为 1074 行单函数（与 `_run_serial_review` 同构），
2026-10-27 按阶段拆成 `_content_prepare` / `_content_draft` / `_content_rounds`
/ `_content_finalize`，状态用 `SimpleNamespace` 快照串接。拆分过程中踩到两个
必须锁死的坑：

1. 段函数只解包自己用到的名字，若直接 `SimpleNamespace(**locals())` 就会把上游
   其余字段截断 —— `_content_rounds` 取 `st.is_selection` 当场 AttributeError。
   所以状态推进必须是「上游打底、本段覆盖」的合并（`_content_state`）。
2. 原来的 `_reseat_reviewers` 闭包捕获 prepare 的局部 `impl`，而起草换将会在
   draft 段重绑 `impl` 后再装评审席。闭包会拿旧作者装配 —— 拆成模块级函数把
   impl 显式传参，才和原语义一致（换将后要按新作者装席）。
"""
import inspect
import json
import os
import subprocess
import sys
import tempfile
import unittest
from types import SimpleNamespace
from unittest import mock

from app.core import pipeline

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

PROBE = r"""
import json, os, sys
from unittest import mock

from app.core import pipeline, store

workdir = sys.argv[1]
task = {"type": "novel", "title": "拆分回归评审", "goal": "写一个关于老街的短篇",
        "workdir": workdir, "manuscript": "manuscript.md",
        "rubric": ["情节", "文笔", "节奏"], "threshold": 9.0, "rounds": 2}
agent = {"id": "mock-author", "mode": "mock", "kind": "builtin", "label": "Mock"}
t = store.create_task(task)
run = store.create_run("orchestration", task["title"], task_id=t["id"])
store.update_run(run["id"], status="running")
err = ""
with mock.patch.object(pipeline.router, "pick", return_value=(agent, "mock")), \
     mock.patch.object(pipeline.router, "pick_critics",
                       return_value=([agent], "mock")), \
     mock.patch.object(pipeline.planner, "make_novel_plan",
                       return_value={"steps": []}), \
     mock.patch.object(pipeline.time, "sleep", lambda *_a, **_k: None):
    try:
        pipeline._run_content_review({"id": run["id"]}, task, [agent], None, {}, "auto")
    except Exception as exc:
        err = "%s: %s" % (type(exc).__name__, exc)
r = store.get_run(run["id"]) or {}
v = r.get("verdict") or {}
report = ""
rp = store.run_dir(run["id"]) / "report.md"
if rp.exists():
    report = rp.read_text(encoding="utf-8")
print("@@RESULT@@" + json.dumps({
    "error": err,
    "status": r.get("status"),
    "steps": [s.get("role") for s in (r.get("steps") or [])],
    "rounds_used": v.get("rounds_used"),
    "history_len": len(v.get("history") or []),
    "threshold": v.get("threshold"),
    "type": v.get("type"),
    "means": (v.get("history") or [{}])[-1].get("means") if v.get("history") else {},
    "publishable": v.get("publishable"),
    "report_chars": len(report),
    "report_has_table": "## 各轮维度均分" in report,
    "report_has_r1": "| 1 |" in report,
    "report_has_r2": "| 2 |" in report,
}, ensure_ascii=True))
"""


class ContentPipelineShapeTests(unittest.TestCase):
    """拆分形状：编排薄、四段各就各位、合并式状态推进没被改回截断式。"""

    STAGES = ("_content_prepare", "_content_draft", "_content_rounds",
              "_content_finalize")

    def test_orchestrator_is_thin(self):
        src = pipeline._run_content_review.__doc__ or ""
        for name in self.STAGES:
            self.assertIn(name, src, "编排入口 docstring 没提 %s" % name)
        lines = inspect.getsource(pipeline._run_content_review).splitlines()
        self.assertLess(len(lines), 30,
                        "_run_content_review 又变厚了（%d 行）：新逻辑请落进阶段函数"
                        % len(lines))

    def test_phase_functions_exist_with_docstring(self):
        for name in self.STAGES:
            fn = getattr(pipeline, name, None)
            self.assertTrue(callable(fn), "缺少阶段函数 %s" % name)
            self.assertTrue((fn.__doc__ or "").strip(), "%s 缺 docstring" % name)

    def test_stage_functions_stay_under_budget(self):
        """防回涨：任一段再膨胀回千行级就该继续拆。"""
        budgets = {"_content_prepare": 160, "_content_draft": 280,
                   "_content_rounds": 80, "_content_finalize": 450}
        for name, budget in budgets.items():
            n = len(inspect.getsource(getattr(pipeline, name)).splitlines())
            self.assertLess(n, budget, "%s 涨到 %d 行（预算 %d），该继续拆"
                            % (name, n, budget))

    def test_draft_and_rounds_merge_upstream_state(self):
        """防截断回归：这两段必须走合并式推进，不能直接 locals() 重造快照。"""
        for name in ("_content_draft", "_content_rounds"):
            src = inspect.getsource(getattr(pipeline, name))
            self.assertIn("_content_state(", src,
                          "%s 没用合并式状态推进，上游字段会被截断" % name)


class ContentStateMergeTests(unittest.TestCase):
    def test_merge_keeps_upstream_and_lets_local_win(self):
        up = SimpleNamespace(a=1, b=2, c="上游")
        st = pipeline._content_state(up, {"b": 20, "d": 4, "st": up})
        self.assertEqual(1, st.a, "上游字段被截断了")
        self.assertEqual("上游", st.c)
        self.assertEqual(20, st.b, "本段重绑的值必须覆盖上游")
        self.assertEqual(4, st.d, "本段新字段必须带出去")
        self.assertFalse(hasattr(st, "st"), "快照里不该留 st 自引用")

    def test_state_does_not_mutate_upstream(self):
        up = SimpleNamespace(a=1)
        pipeline._content_state(up, {"a": 9})
        self.assertEqual(1, up.a, "快照必须新建，不能改上游对象")


class ContentReseatTests(unittest.TestCase):
    """换将后要按新作者装席位：impl 走参数，不能是闭包捕获的旧值。"""

    def test_reseat_uses_passed_impl(self):
        seen = {}

        def fake_assemble(seats, agents, impl, **kw):
            seen["impl"] = impl
            seen["seats"] = list(seats)
            return list(seats), "solo"

        new_author = {"id": "new-author", "mode": "real"}
        with mock.patch.object(pipeline, "_assemble_review_seats",
                               side_effect=fake_assemble), \
                mock.patch.object(pipeline, "_mark_reader_seat",
                                  side_effect=lambda s: list(s)):
            seats, independence = pipeline._content_reseat_reviewers(
                [{"id": "c1"}, {"id": "c2"}, {"id": "c3"}],
                agents=[], impl=new_author,
                workflow={"reviewers": 2}, mode="auto", route={}, run_id="r-x")
        self.assertEqual("new-author", seen["impl"]["id"],
                         "装席用的是旧作者：换将后评审组会按落选者规则装配")
        self.assertEqual(2, len(seen["seats"]), "没按 workflow 截断席位")
        self.assertEqual("solo", independence, "独立性等级没回传")

    def test_manual_mode_never_adds_seats(self):
        with mock.patch.object(pipeline, "_assemble_review_seats",
                               return_value=([], "")), \
                mock.patch.object(pipeline, "_mark_reader_seat",
                                  side_effect=lambda s: list(s)):
            seats, _ = pipeline._content_reseat_reviewers(
                [], agents=[], impl={"id": "a"}, workflow={"reviewers": 3},
                mode="manual", route={}, run_id="r-y")
        self.assertEqual([], seats, "手动模式不该替用户加人")


class ContentPipelineEndToEndTests(unittest.TestCase):
    def test_mock_run_completes_two_review_rounds(self):
        with tempfile.TemporaryDirectory() as tmp:
            ws = os.path.join(tmp, "ws")
            os.makedirs(ws)
            env = dict(os.environ)
            env["TUTTI_DATA"] = tmp
            env["PYTHONIOENCODING"] = "utf-8"
            proc = subprocess.run(
                [sys.executable, "-c", PROBE, ws],
                cwd=REPO, env=env, timeout=300,
                capture_output=True, text=True)
            out = proc.stdout or ""
            self.assertIn("@@RESULT@@", out,
                          "探针未产出结果：stdout=%s stderr=%s"
                          % (out[-1500:], (proc.stderr or "")[-1500:]))
            data = json.loads(out.split("@@RESULT@@", 1)[1].strip().splitlines()[0])

        self.assertEqual("", data["error"], data["error"])
        self.assertEqual(["draft", "critique-r1", "revise-r1", "critique-r2"],
                         data["steps"], "四段串联的步骤序列变了")
        self.assertEqual(2, data["rounds_used"])
        self.assertEqual(2, data["history_len"])
        self.assertEqual(9.0, data["threshold"], "prepare 的阈值没带到终局")
        self.assertEqual("novel", data["type"])
        self.assertFalse(data["publishable"])
        self.assertTrue(data["report_has_table"])
        self.assertTrue(data["report_has_r1"] and data["report_has_r2"],
                        "报告只写了部分轮次")
        self.assertGreater(data["report_chars"], 300)


if __name__ == "__main__":
    unittest.main()
