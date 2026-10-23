"""全通道死亡时的兜底路径：任务必须成功，不能停摆。

2026-10-23 实案：维云余额 ¥0.001、智谱七天额度上限，三条真实通道全死，
每个任务都判「评审未完成 / 起草失败」→ 用户什么都拿不到。用户的诉求是
「我要成功，不是失败」。

这些用例锁死三条兜底路径：
1. 评审全灭 + 有稿件  → 本地确定性评审给出可解释分数，任务 done；
2. 起草全灭 + 无稿件  → 本地模板生成执行计划起点，任务 done；
3. 修订全灭          → 本地确定性修订（去重复段/拆长段），继续走完流程。

所有兜底产物都必须带来源标记（review_fallback / draft_source），
不冒充模型创作——成功可以，但要诚实。
"""

import json
import os
import subprocess
import sys
import tempfile
import unittest
from unittest import mock

APP_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if APP_ROOT not in sys.path:
    sys.path.insert(0, APP_ROOT)

from app.core import local_fallback  # noqa: E402
from app.core import pipeline  # noqa: E402


GOOD_TEXT = (
    "清晨的雾还没散，老陈把最后一箱工具搬上三轮车。这是他修表修了四十年的"
    "老街，今天要拆了。他蹲下来，摸了摸那道被鞋底磨光的门槛，没说话。\n\n"
    "第二天，推土机来的时候，老陈在巷口支了个小摊，给拆房子的工人免费修"
    "手表。他说：表停了可以修，街没了就没了。\n"
)
BAD_TEXT = (
    "主角走进房间，看到桌上有一封信。他拆开信，里面写着：明天下午三点，"
    "老地方见。他把信折好塞进口袋，转身出了门。\n\n"
    "主角走进房间，看到桌上有一封信。他拆开信，里面写着：明天下午三点，"
    "老地方见。他把信折好塞进口袋，转身出了门。\n\n"
    "主角走进房间，看到桌上有一封信。他拆开信，里面写着：明天下午三点，"
    "老地方见。他把信折好塞进口袋，转身出了门。\n\n"
    "主角走进房间，看到桌上有一封信。他拆开信，里面写着：明天下午三点，"
    "老地方见。他把信折好塞进口袋，转身出了门。\n"
)


class LocalReviewTests(unittest.TestCase):
    def test_clean_manuscript_scores_above_threshold(self):
        review = local_fallback.local_review(
            GOOD_TEXT, ["情节", "人物", "文笔", "节奏", "吸引力"], "novel")
        self.assertTrue(review["scores"])
        self.assertEqual(review["review_fallback"],
                         local_fallback.HEURISTIC)
        self.assertNotIn("review_degraded", review)
        mean = sum(review["scores"].values()) / len(review["scores"])
        self.assertGreaterEqual(mean, 7.0, review["scores"])

    def test_repeated_manuscript_scores_below_threshold(self):
        review = local_fallback.local_review(
            BAD_TEXT, ["情节", "人物", "文笔", "节奏", "吸引力"], "novel")
        mean = sum(review["scores"].values()) / len(review["scores"])
        self.assertLess(mean, 7.0, review["scores"])
        self.assertTrue(review["heuristic_issues"])
        self.assertIn("重复", "".join(review["heuristic_issues"]))

    def test_empty_manuscript_is_not_faked(self):
        review = local_fallback.local_review("", ["质量"], "doc")
        self.assertEqual(review["scores"]["质量"], 1.0)
        self.assertTrue(review["heuristic_issues"])

    def test_tiny_manuscript_scores_low_on_every_dimension(self):
        review = local_fallback.local_review(
            "就一句话。", ["情节", "文笔"], "doc")
        for dim, score in review["scores"].items():
            self.assertLess(score, 5.0, (dim, score))

    def test_unknown_dimensions_get_neutral_score_and_say_so(self):
        review = local_fallback.local_review(GOOD_TEXT, ["氛围"], "novel")
        self.assertEqual(review["scores"]["氛围"], 7.0)
        self.assertIn("中性分", review["summary"])


class LocalReviseTests(unittest.TestCase):
    def test_duplicate_paragraphs_are_dropped(self):
        new_text, changes = local_fallback.local_revise(BAD_TEXT)
        self.assertTrue(changes)
        self.assertIn("重复", " ".join(changes))
        self.assertLess(len(new_text), len(BAD_TEXT))

    def test_clean_text_is_left_alone(self):
        new_text, changes = local_fallback.local_revise(GOOD_TEXT)
        self.assertEqual(changes, [])
        self.assertEqual(new_text, GOOD_TEXT)


class LocalDraftTests(unittest.TestCase):
    def test_template_draft_lands_and_is_marked(self):
        with tempfile.TemporaryDirectory() as tmp:
            ok, body, note = local_fallback.write_local_draft(
                {"title": "写部署说明", "goal": "介绍部署步骤", "type": "doc"},
                tmp, "manuscript.md")
            self.assertTrue(ok)
            self.assertIn("本地模板", note)
            text = open(os.path.join(tmp, "manuscript.md"),
                        encoding="utf-8").read()
            self.assertIn("介绍部署步骤", text)
            self.assertIn("待补充", text)


DEAD_ALL_PROBE = r"""
import json, os, sys
from unittest import mock
from app.core import pipeline, store

workdir = sys.argv[1]
task = {"type": "doc", "title": "全通道死亡", "goal": "写一份部署说明",
        "workdir": workdir, "manuscript": "manuscript.md",
        "rubric": ["质量"], "threshold": 7.0,
        "rounds": int(os.environ.get("DEAD_PROBE_ROUNDS", "1"))}
author = {"id": "claude-cli", "mode": "real", "kind": "claude", "label": "Claude"}
calls = []

def fake_step(run_id, role, bound, prompt, wd, readonly, ev, **kwargs):
    calls.append(role)
    return {"ok": False, "text": "",
            "error": "APIConnectionError: 预扣费额度失败, 用户剩余额度: "
                     "¥0.001014, 需要预扣费额度: ¥0.020000",
            "error_code": "QUOTA", "raw": {"exit_code": 1}}

t = store.create_task(task)
run = store.create_run("orchestration", "全通道死亡", task_id=t["id"])
store.update_run(run["id"], status="running")
with mock.patch.object(pipeline, "_run_step", side_effect=fake_step), \
     mock.patch.object(pipeline.modelhub, "bind_agent",
                       side_effect=lambda a, d="default": a), \
     mock.patch.object(pipeline.router, "pick", return_value=(author, "only")), \
     mock.patch.object(pipeline.router, "pick_critics", return_value=([author], "only")), \
     mock.patch.object(pipeline.router, "pick_reviewer",
                       return_value=(None, "no alternate")), \
     mock.patch.object(pipeline.router, "pick_switch_candidate",
                       return_value=(None, "no alternate")), \
     mock.patch.object(pipeline.planner, "make_novel_plan", return_value={"steps": []}), \
     mock.patch.object(pipeline.planner, "make_review_outline",
                       return_value={"items": [], "workflow": {"reviewers": 2}}), \
     mock.patch("app.core.bench.benched_ids", return_value=set()), \
     mock.patch.object(pipeline.time, "sleep", lambda *_a, **_k: None):
    pipeline._run_content_review({"id": run["id"]}, task, [author], None, {}, "expert")
r = store.get_run(run["id"])
v = r.get("verdict") or {}
print("@@RESULT@@" + json.dumps({
    "status": r.get("status"),
    "run_id": run["id"],
    "publishable": v.get("publishable"),
    "review_degraded": v.get("review_degraded"),
    "draft_source": v.get("draft_source"),
    "overall": v.get("overall"),
    "review_fallbacks": [
        fallback
        for history in (v.get("history") or [])
        for fallback in (history.get("review_fallbacks") or [])
    ],
    "steps": [
        {"role": step.get("role"), "status": step.get("status")}
        for step in (r.get("steps") or [])
    ],
    "report": (store.run_dir(run["id"]) / "report.md").read_text(encoding="utf-8"),
    "calls": calls,
}, ensure_ascii=True))
"""


class AllChannelsDeadTests(unittest.TestCase):
    """起草、评审、修订三条路全灭——任务仍然要走到 done。"""

    def test_existing_manuscript_completes_with_local_review_and_revise(self):
        """预置稿件由本地机测评审/修订收尾，不应因低于发布阈值而失败。"""
        with tempfile.TemporaryDirectory() as tmp:
            workdir = os.path.join(tmp, "workspace")
            os.makedirs(workdir)
            with open(os.path.join(workdir, "manuscript.md"), "w",
                      encoding="utf-8") as fh:
                fh.write(BAD_TEXT)
            env = dict(os.environ)
            env["TUTTI_DATA"] = os.path.join(tmp, "data")
            env["PYTHONIOENCODING"] = "utf-8"
            env["DEAD_PROBE_ROUNDS"] = "2"
            proc = subprocess.run(
                [sys.executable, "-c", DEAD_ALL_PROBE, workdir],
                cwd=APP_ROOT, env=env, timeout=180,
                capture_output=True, text=True)
            if proc.returncode != 0:
                raise AssertionError("probe failed: %s" % proc.stderr[-2000:])
            line = [x for x in proc.stdout.splitlines()
                    if x.startswith("@@RESULT@@")][-1]
            result = json.loads(line[len("@@RESULT@@"):])

        self.assertEqual(result["status"], "done", result)
        self.assertFalse(result["publishable"], result)
        self.assertEqual(result["draft_source"], "provided-manuscript", result)
        self.assertFalse(result["review_degraded"], result)
        self.assertIn("local-heuristic", result["review_fallbacks"])
        self.assertIn("revise-r1", result["calls"], result)
        self.assertIn("本地兜底完成", result["report"])
        self.assertIn("本地确定性算法", result["report"])
        self.assertIn("发布标准未通过", result["report"])

    def test_run_completes_with_local_draft_and_local_review(self):
        with tempfile.TemporaryDirectory() as tmp:
            workdir = os.path.join(tmp, "workspace")
            os.makedirs(workdir)
            env = dict(os.environ)
            env["TUTTI_DATA"] = os.path.join(tmp, "data")
            env["PYTHONIOENCODING"] = "utf-8"
            proc = subprocess.run(
                [sys.executable, "-c", DEAD_ALL_PROBE, workdir],
                cwd=APP_ROOT, env=env, timeout=180,
                capture_output=True, text=True)
            if proc.returncode != 0:
                raise AssertionError("probe failed: %s" % proc.stderr[-2000:])
            line = [x for x in proc.stdout.splitlines()
                    if x.startswith("@@RESULT@@")][-1]
            result = json.loads(line[len("@@RESULT@@"):])
            # 交付物必须在临时目录清理前取到（大小 + 前缀）
            ms_path = os.path.join(workdir, "manuscript.md")
            ms_size = os.path.getsize(ms_path) if os.path.isfile(ms_path) else 0

        # 不停摆：本地模板起草 + 本地确定性评审，流程走到终态。
        self.assertEqual(result["status"], "done", result)
        self.assertFalse(result["review_degraded"], result)
        self.assertEqual(result["draft_source"],
                         local_fallback.TEMPLATE, result)
        self.assertIsNotNone(result["overall"], result)
        # 回归锁：本地模板稿 + 机测评审绝不能冒充「可发布」。占位稿不是成品，
        # 纯机测评审也不构成真实共识——publishable 必须为 False（2026-10-23
        # 曾因中性分 7.0 误判 publishable=True，给用户一个假「成功」）。
        self.assertFalse(result["publishable"], result)
        # 起草失败一次就走本地模板；两席评审各试一次即被账号死错拦停，
        # 不再重放、不再换将——没有 39 连撞。
        self.assertEqual(result["calls"],
                         ["draft", "critique-r1", "critique-r1"], result)
        # 交付物落在工作目录，且是模板稿（带"待补充"标记），不是空文件
        self.assertGreater(ms_size, 100, result)

    def test_short_circuit_skips_model_calls_when_nothing_is_live(self):
        """无可用通道时评审步不再调模型：一秒出本地评审，不烧超时。"""
        with tempfile.TemporaryDirectory() as tmp:
            workdir = os.path.join(tmp, "workspace")
            os.makedirs(workdir)
            with open(os.path.join(workdir, "manuscript.md"), "w",
                      encoding="utf-8") as fh:
                fh.write(GOOD_TEXT)
            agent = {"id": "dead-cli", "mode": "real", "kind": "codex",
                     "label": "Dead"}
            with mock.patch("app.core.bench.benched_ids",
                            return_value={"dead-cli"}), \
                 mock.patch.object(pipeline, "_run_step",
                                   side_effect=AssertionError(
                                       "无可用通道时不得再调模型")):
                cj, res, actual, note = pipeline._critique_step_with_cli_fallback(
                    "r-dead", "critique-r1", agent, "prompt", workdir, None,
                    [agent], agent, "default", "doc", {}, "auto", False,
                    dims=["质量"], ms_name="manuscript.md")
        self.assertTrue(cj["scores"])
        self.assertEqual(cj["review_fallback"], local_fallback.HEURISTIC)
        self.assertIn("确定性算法", note)
        self.assertIs(actual, agent)


if __name__ == "__main__":
    unittest.main()
