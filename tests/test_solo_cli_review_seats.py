# -*- coding: utf-8 -*-
"""单通道（只装了一条 CLI）评审兜底。

用户环境只有一条 CLI 时，评审契约要求的两席无法由「不同厂商」满足：排除作者
后候选为空，旧行为会把每个任务判成 review_degraded、稿件永不可发布。兜底方案
是给该 CLI 派生「独立复核席」——不同人格、互不可见、串行执行、绑定不变。

本文件锁四件事：
1. 单 CLI 任务能走到 publishable，且独立性档位如实标成 solo；
2. 两条 CLI 时装配是恒等变换（不因兜底降低既有门槛）；
3. 作者永不自评（pick_critics 不再把作者塞进评审组）；
4. 复核席不改绑定、不改账号口径（死错一起停用，避免下一章再撞一次）。
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
import unittest
from unittest import mock

from app.core import modelhub, pipeline, review_roster, router


SOLO_PROBE = r"""
import json, os, sys
from unittest import mock
from app.core import pipeline, store

workdir = sys.argv[1]
task = {"type": "doc", "title": "single cli deliverable",
        "goal": "写一份可直接交付的说明", "workdir": workdir,
        "manuscript": "manuscript.md", "rubric": ["质量"],
        "threshold": 7.0, "rounds": 2}
agent = {"id": "claude-cli", "mode": "real", "kind": "claude",
         "label": "Only CLI", "model": "only-model"}
seen = []

def fake_step(run_id, role, bound, prompt, wd, readonly, ev, **kwargs):
    if role == "draft":
        with open(os.path.join(workdir, "manuscript.md"), "w", encoding="utf-8") as f:
            f.write("初稿正文，可供评审。\n")
        return {"ok": True, "text": "draft", "raw": {"exit_code": 0}}
    if role.startswith("critique"):
        seen.append({"agent": bound.get("id"), "prompt": prompt,
                     "resume": kwargs.get("resume")})
        return {"ok": True,
                "text": json.dumps({"scores": {"质量": 8},
                                    "issues": [], "summary": "reviewed"},
                                   ensure_ascii=False),
                "raw": {"exit_code": 0}}
    if role.startswith("revise"):
        return {"ok": True, "text": "revised", "raw": {"exit_code": 0}}
    raise AssertionError("unexpected pipeline step: " + role)

t = store.create_task(task)
run = store.create_run("orchestration", "single cli deliverable", task_id=t["id"])
store.update_run(run["id"], status="running")
with mock.patch.object(pipeline, "_run_step", side_effect=fake_step), \
     mock.patch.object(pipeline.modelhub, "bind_agent", side_effect=lambda a, d="default": a), \
     mock.patch.object(pipeline.router, "pick", return_value=(agent, "only CLI")), \
     mock.patch.object(pipeline.router, "pick_critics", return_value=([], "only CLI")), \
     mock.patch.object(pipeline.router, "pick_reviewer", return_value=(None, "no alternate CLI")), \
     mock.patch.object(pipeline.router, "pick_switch_candidate", return_value=(None, "no alternate CLI")), \
     mock.patch.object(pipeline.planner, "make_novel_plan", return_value={"steps": []}), \
     mock.patch.object(pipeline.planner, "make_review_outline",
                       return_value={"items": [],
                                     "workflow": {"reviewers": 2}}), \
     mock.patch("app.core.bench.benched_ids", return_value=set()), \
     mock.patch.object(pipeline.time, "sleep", lambda *_a, **_k: None):
    pipeline._run_content_review(
        {"id": run["id"]}, task, [agent], None, {}, "expert")
r = store.get_run(run["id"])
v = r.get("verdict") or {}
print("@@RESULT@@" + json.dumps({
    "status": r.get("status"),
    "publishable": v.get("publishable"),
    "review_degraded": v.get("review_degraded"),
    "review_independence": v.get("review_independence"),
    "overall": v.get("overall"),
    "scores": v.get("scores"),
    "reviewers": (v.get("route") or {}).get("critics"),
    "seen": seen,
    "report": (store.run_dir(run["id"]) / "report.md").read_text(encoding="utf-8"),
}, ensure_ascii=True))
"""


SOLO_FALLBACK_PROBE = r"""
import json, os, sys
from unittest import mock
from app.core import pipeline, store

workdir = sys.argv[1]
task = {"type": "doc", "title": "fallback seat recovery",
        "goal": "写一份可直接交付的说明", "workdir": workdir,
        "manuscript": "manuscript.md", "rubric": ["质量"],
        "threshold": 7.0, "rounds": 1}
agent = {"id": "claude-cli", "mode": "real", "kind": "claude",
         "label": "Only CLI", "model": "only-model",
         "review_models": ["only-model", "alternate-model", "backfill-model"]}
seen = []

def fake_step(run_id, role, bound, prompt, wd, readonly, ev, **kwargs):
    if role == "draft":
        with open(os.path.join(workdir, "manuscript.md"), "w", encoding="utf-8") as f:
            f.write("初稿正文，可供评审。包含充分内容与清晰结构。\n")
        return {"ok": True, "text": "draft", "raw": {"exit_code": 0}}
    if role.startswith("critique"):
        seen.append({"agent": bound.get("id"), "prompt": prompt})
        # 第一评审席触发本地机测，其余真实复核席返回完整模型评审。
        if bound.get("id") == "claude-cli#recheck":
            return {"ok": False, "text": "", "error": "temporary reviewer failure",
                    "error_code": "UNKNOWN", "raw": {"exit_code": 1}}
        return {"ok": True,
                "text": json.dumps({"scores": {"质量": 8},
                                    "issues": [], "summary": "reviewed"},
                                   ensure_ascii=False),
                "raw": {"exit_code": 0}}
    if role.startswith("revise"):
        return {"ok": True, "text": "revised", "raw": {"exit_code": 0}}
    raise AssertionError("unexpected pipeline step: " + role)

t = store.create_task(task)
run = store.create_run("orchestration", "fallback seat recovery", task_id=t["id"])
store.update_run(run["id"], status="running")
with mock.patch.object(pipeline, "_run_step", side_effect=fake_step), \
     mock.patch.object(pipeline.modelhub, "bind_agent", side_effect=lambda a, d="default": a), \
     mock.patch.object(pipeline.router, "pick", return_value=(agent, "only CLI")), \
     mock.patch.object(pipeline.router, "pick_critics", return_value=([], "only CLI")), \
     mock.patch.object(pipeline.router, "pick_reviewer", return_value=(None, "no alternate CLI")), \
     mock.patch.object(pipeline.router, "pick_switch_candidate", return_value=(None, "no alternate CLI")), \
     mock.patch.object(pipeline.planner, "make_novel_plan", return_value={"steps": []}), \
     mock.patch.object(pipeline.planner, "make_review_outline",
                       return_value={"items": [], "workflow": {"reviewers": 2}}), \
     mock.patch("app.core.bench.benched_ids", return_value=set()), \
     mock.patch.object(pipeline.time, "sleep", lambda *_a, **_k: None):
    pipeline._run_content_review(
        {"id": run["id"]}, task, [agent], None, {}, "expert")
r = store.get_run(run["id"])
v = r.get("verdict") or {}
print("@@RESULT@@" + json.dumps({
    "status": r.get("status"),
    "publishable": v.get("publishable"),
    "review_degraded": v.get("review_degraded"),
    "review_independence": v.get("review_independence"),
    "overall": v.get("overall"),
    "seen": seen,
}, ensure_ascii=True))
"""


PROBE_DROP = r"""
import json, os, sys
from unittest import mock
from app.core import pipeline, store

workdir = sys.argv[1]
task = {"type": "doc", "title": "reviewer drop", "goal": "写一份说明",
        "workdir": workdir, "manuscript": "manuscript.md",
        "rubric": ["质量"], "threshold": 7.0, "rounds": 1}
author = {"id": "author-cli", "mode": "real", "kind": "claude", "label": "Author"}
dead = {"id": "dead-cli", "mode": "real", "kind": "codex", "label": "Dead CLI"}
live = {"id": "live-cli", "mode": "real", "kind": "opencode", "label": "Live CLI"}
calls = []

def fake_step(run_id, role, bound, prompt, wd, readonly, ev, **kwargs):
    calls.append(bound.get("id"))
    if role == "draft":
        with open(os.path.join(workdir, "manuscript.md"), "w", encoding="utf-8") as f:
            f.write("正文。\n")
        return {"ok": True, "text": "draft", "raw": {"exit_code": 0}}
    if bound.get("id") == "dead-cli":
        return {"ok": False, "text": "", "error":
                "API Error: Request rejected (429) · [1310][已达到 7 天使用上限，"
                "2026-10-26 14:09 后可继续使用]",
                "error_code": "QUOTA", "raw": {"exit_code": 1}}
    return {"ok": True,
            "text": json.dumps({"scores": {"质量": 8}, "issues": [],
                                "summary": "ok"}, ensure_ascii=False),
            "raw": {"exit_code": 0}}

t = store.create_task(task)
run = store.create_run("orchestration", "reviewer drop", task_id=t["id"])
store.update_run(run["id"], status="running")
with mock.patch.object(pipeline, "_run_step", side_effect=fake_step), \
     mock.patch.object(pipeline.modelhub, "bind_agent", side_effect=lambda a, d="default": a), \
     mock.patch.object(pipeline.router, "pick", return_value=(author, "author")), \
     mock.patch.object(pipeline.router, "pick_critics",
                       return_value=([dead, live], "two critics")), \
     mock.patch.object(pipeline.router, "pick_reviewer", return_value=(None, "no alt")), \
     mock.patch.object(pipeline.router, "pick_switch_candidate",
                       return_value=(None, "no alt")), \
     mock.patch.object(pipeline.planner, "make_novel_plan", return_value={"steps": []}), \
     mock.patch.object(pipeline.planner, "make_review_outline",
                       return_value={"items": [], "workflow": {"reviewers": 2}}), \
     mock.patch("app.core.bench.benched_ids", return_value=set()), \
     mock.patch.object(pipeline.time, "sleep", lambda *_a, **_k: None):
    pipeline._run_content_review(
        {"id": run["id"]}, task, [author, dead, live], None, {}, "expert")
r = store.get_run(run["id"])
v = r.get("verdict") or {}
print("@@RESULT@@" + json.dumps({
    "status": r.get("status"),
    "publishable": v.get("publishable"),
    "review_degraded": v.get("review_degraded"),
    "review_independence": v.get("review_independence"),
    "overall": v.get("overall"),
    "critics": (v.get("route") or {}).get("critics"),
    "calls": calls,
}, ensure_ascii=True))
"""


class SoloSeatTests(unittest.TestCase):
    def test_single_cli_derives_two_review_seats(self):
        author = {"id": "claude-cli", "mode": "real", "label": "Claude"}
        seats, note, independence = review_roster.ensure_review_seats(
            [], [author], author, 2)
        self.assertEqual([s["id"] for s in seats],
                         ["claude-cli#recheck", "claude-cli#recheck2"])
        self.assertEqual(independence, review_roster.SOLO_MODE)
        self.assertIn("独立复核席", note)
        # 两席人格必须不同，否则第二遍只会复读第一遍。
        self.assertNotEqual(review_roster.prompt_suffix(seats[0]),
                            review_roster.prompt_suffix(seats[1]))
        self.assertTrue(all(review_roster.is_solo_seat(s) for s in seats))

    def test_existing_isolated_reviewers_are_never_replaced(self):
        author = {"id": "claude-cli", "mode": "real"}
        critic_a = {"id": "codex-cli", "mode": "real"}
        critic_b = {"id": "aider", "mode": "real"}
        seats, note, independence = review_roster.ensure_review_seats(
            [critic_a, critic_b], [author, critic_a, critic_b], author, 2)
        self.assertEqual([s["id"] for s in seats], ["codex-cli", "aider"])
        self.assertEqual((note, independence), ("", ""))

    def test_spare_real_cli_is_preferred_over_author_self_review(self):
        author = {"id": "claude-cli", "mode": "real", "label": "Claude"}
        spare = {"id": "codex-cli", "mode": "real", "label": "Codex"}
        seats, _note, independence = review_roster.ensure_review_seats(
            [], [author, spare], author, 2)
        self.assertEqual([s["id"] for s in seats], ["codex-cli",
                                                    "claude-cli#recheck"])
        self.assertEqual(independence, review_roster.SOLO_MODE)

    def test_mock_only_environment_gets_no_fake_seats(self):
        impl = {"id": "mock-a", "mode": "mock"}
        seats, note, independence = review_roster.ensure_review_seats(
            [], [impl], impl, 2)
        self.assertEqual(seats, [])
        self.assertEqual((note, independence), ("", ""))

    def test_same_cli_different_bound_models_are_preferred(self):
        author = {"id": "claude-cli", "mode": "real", "kind": "claude",
                  "provider_id": "vendor-a", "model": "model-a",
                  "review_models": ["model-a", "model-b"]}
        seats, _note, independence = review_roster.ensure_review_seats(
            [], [author], author, 2)
        self.assertEqual(len(seats), 2)
        self.assertEqual(next(s for s in seats if s.get("_review_model"))[
            "_review_model"], "model-b")
        self.assertEqual(independence, review_roster.CROSS_MODEL_SAME_CLI)

    def test_independence_levels_are_separate_from_seat_count(self):
        cross_vendor = [
            {"id": "a", "mode": "real", "kind": "claude", "model": "m1"},
            {"id": "b", "mode": "real", "kind": "codex", "model": "m1"},
        ]
        same_model = [
            {"id": "a", "mode": "real", "kind": "claude", "model": "m1"},
            review_roster.make_solo_seat(
                {"id": "a", "mode": "real", "kind": "claude", "model": "m1"}, 1),
        ]
        self.assertEqual(review_roster.independence_level(cross_vendor),
                         review_roster.CROSS_VENDOR)
        self.assertEqual(review_roster.independence_level(same_model),
                         review_roster.SAME_MODEL_MULTI_VIEW)
        self.assertEqual(review_roster.independence_level([]), review_roster.NO_REVIEW)

    def test_duplicate_seat_identity_does_not_create_extra_reviewer(self):
        seat = {"id": "a", "mode": "real", "kind": "claude", "model": "m1"}
        self.assertEqual(review_roster.completed_seat_count([seat, dict(seat)]), 1)
        self.assertEqual(review_roster.independence_level([seat, dict(seat)]),
                         review_roster.NO_REVIEW)

    def test_solo_seat_keeps_binding_of_source_cli(self):
        author = {"id": "claude-cli", "mode": "real", "kind": "claude",
                  "label": "Claude", "model": "m1"}
        seat = review_roster.make_solo_seat(author, 1)
        self.assertEqual(seat["_binding_source"], "claude-cli")
        seen = []

        def fake_binding(rid):
            seen.append(rid)
            return {"chain": [{"model": "only-model", "provider_id": "p1"}]}

        with mock.patch.object(modelhub, "_binding_for", side_effect=fake_binding), \
             mock.patch.object(modelhub, "resolve_binding", return_value=None), \
             mock.patch("app.core.manager.sync_runtime_config",
                        return_value=True), \
             mock.patch.object(modelhub, "recommend_binding",
                               side_effect=AssertionError("不得重新推荐绑定")):
            modelhub.bind_agent(seat, "default")
        # 影子 id 不能拿去查绑定，否则查不到就落到「智能推荐」换供应商。
        self.assertEqual(seen, ["claude-cli"])

    def test_model_review_seat_selects_only_requested_model_from_source_chain(self):
        source = {"id": "claude-cli", "mode": "real", "kind": "claude"}
        seat = review_roster.make_model_seat(source, "model-b")
        resolved = {"model": "model-a", "env": {"A": "1"},
                    "call_chain": [
                        {"model": "model-a", "provider_id": "vendor-a", "env": {"A": "1"}},
                        {"model": "model-b", "provider_id": "vendor-a", "env": {"B": "2"}},
                    ]}
        with mock.patch.object(modelhub, "_binding_for", return_value={"chain": []}), \
             mock.patch.object(modelhub, "resolve_binding", return_value=resolved), \
             mock.patch("app.core.manager.sync_runtime_config", return_value=True), \
             mock.patch.object(modelhub, "recommend_binding",
                               side_effect=AssertionError("不得重新推荐绑定")):
            bound = modelhub.bind_agent(seat, "default")
        self.assertEqual(bound["model"], "model-b")
        self.assertEqual([e["model"] for e in bound["call_chain"]], ["model-b"])
        self.assertEqual(bound["env"], {"B": "2"})

    def test_account_scope_covers_seat_and_source(self):
        author = {"id": "claude-cli", "mode": "real"}
        seat = review_roster.make_solo_seat(author, 2)
        self.assertEqual(set(pipeline._account_ids(seat)),
                         {"claude-cli", "claude-cli#recheck2"})
        self.assertEqual(set(pipeline._account_ids(author)), {"claude-cli"})

    def test_same_source_seats_share_one_execution_lock(self):
        author = {"id": "claude-cli", "mode": "real"}
        other = {"id": "codex-cli", "mode": "real"}
        seat_a = review_roster.make_solo_seat(author, 1)
        seat_b = review_roster.make_solo_seat(author, 2)
        self.assertIs(review_roster.execution_lock(seat_a),
                      review_roster.execution_lock(seat_b))
        self.assertIsNot(review_roster.execution_lock(seat_a),
                         review_roster.execution_lock(other))

    def test_single_real_cli_is_never_its_own_reviewer(self):
        author = {"id": "claude-cli", "mode": "real", "kind": "claude",
                  "label": "Only CLI"}
        critics, note = router.pick_critics([author], "story", stats={}, impl=author)
        self.assertEqual(critics, [])
        self.assertIn("不启用作者自评", note)


class SoloReviewPipelineTests(unittest.TestCase):
    def test_single_cli_expert_task_becomes_publishable(self):
        with tempfile.TemporaryDirectory() as tmp:
            workdir = os.path.join(tmp, "workspace")
            os.makedirs(workdir)
            env = dict(os.environ)
            env["TUTTI_DATA"] = os.path.join(tmp, "data")
            env["PYTHONIOENCODING"] = "utf-8"
            proc = subprocess.run(
                [sys.executable, "-c", SOLO_PROBE, workdir],
                cwd=os.path.dirname(os.path.dirname(__file__)), env=env,
                timeout=180, capture_output=True, text=True)
            if proc.returncode != 0:
                raise AssertionError("probe failed: %s" % proc.stderr[-2000:])
            line = [x for x in proc.stdout.splitlines()
                    if x.startswith("@@RESULT@@")][-1]
            result = json.loads(line[len("@@RESULT@@"):])

        self.assertEqual(result["status"], "done", result)
        self.assertTrue(result["publishable"], result)
        self.assertFalse(result["review_degraded"], result)
        self.assertEqual(result["review_independence"],
                         review_roster.SOLO_MODE, result)
        self.assertEqual(result["overall"], 8.0, result)
        # 两席都来自同一条 CLI，但步骤 id 与人格分开、且都不复用会话
        self.assertEqual(len(result["seen"]), 2, result["seen"])
        self.assertEqual({row["agent"] for row in result["seen"]},
                         {"claude-cli#recheck", "claude-cli#recheck2"})
        prompts = [row["prompt"] for row in result["seen"]]
        self.assertNotEqual(prompts[0], prompts[1])
        self.assertTrue(all("复核人须知" in text for text in prompts))
        self.assertTrue(all(row["resume"] is None for row in result["seen"]))
        self.assertIn("评审独立性", result["report"])
        self.assertIn("claude-cli", result["report"])

    def test_local_heuristic_score_does_not_block_real_solo_reviewer_backfill(self):
        """机测评审不是模型席，不能阻止单 CLI 追加另一人格的真实复核。"""
        with tempfile.TemporaryDirectory() as tmp:
            workdir = os.path.join(tmp, "workspace")
            os.makedirs(workdir)
            env = dict(os.environ)
            env["TUTTI_DATA"] = os.path.join(tmp, "data")
            env["PYTHONIOENCODING"] = "utf-8"
            proc = subprocess.run(
                [sys.executable, "-c", SOLO_FALLBACK_PROBE, workdir],
                cwd=os.path.dirname(os.path.dirname(__file__)), env=env,
                timeout=180, capture_output=True, text=True)
            if proc.returncode != 0:
                raise AssertionError("probe failed: %s" % proc.stderr[-2000:])
            line = [x for x in proc.stdout.splitlines()
                    if x.startswith("@@RESULT@@")][-1]
            result = json.loads(line[len("@@RESULT@@"):])

        self.assertEqual(result["status"], "done", result)
        self.assertTrue(result["publishable"], result)
        self.assertFalse(result["review_degraded"], result)
        self.assertEqual(result["review_independence"],
                         review_roster.CROSS_MODEL_SAME_CLI, result)
        reviewers = [row["agent"] for row in result["seen"]]
        self.assertTrue(any("backfill-model" in reviewer
                            for reviewer in reviewers), result["seen"])
        self.assertTrue(any("alternate-model" in reviewer
                            for reviewer in reviewers), result["seen"])
        # 减员补位应先使用源 CLI 配置的其他模型，而不是直接复制同模型人格。
        self.assertFalse(any("#recheck3" in reviewer for reviewer in reviewers),
                         result["seen"])


class ReviewerDropTests(unittest.TestCase):
    def test_dead_reviewer_is_replaced_by_an_independent_recheck(self):
        """一席死在账号级错误上，另一席仍健康时必须补位而非判未评估。

        2026-10-23 实案：评审组 [claude-code(七天上限), aider]，claude 死后
        只剩了1 席；稿件本身没问题，却被判「评审未完成」。
        """
        with tempfile.TemporaryDirectory() as tmp:
            workdir = os.path.join(tmp, "workspace")
            os.makedirs(workdir)
            env = dict(os.environ)
            env["TUTTI_DATA"] = os.path.join(tmp, "data")
            env["PYTHONIOENCODING"] = "utf-8"
            proc = subprocess.run(
                [sys.executable, "-c", PROBE_DROP, workdir],
                cwd=os.path.dirname(os.path.dirname(__file__)), env=env,
                timeout=180, capture_output=True, text=True)
            if proc.returncode != 0:
                raise AssertionError("probe failed: %s" % proc.stderr[-2000:])
            line = [x for x in proc.stdout.splitlines()
                    if x.startswith("@@RESULT@@")][-1]
            result = json.loads(line[len("@@RESULT@@"):])

        # 一席死于账号级错误时，不把本地机测当真实席；同源 CLI 追加不同人格
        # 的复核席后，两个有效模型意见可形成共识并独立通过发布门禁。
        self.assertEqual(result["status"], "done", result)
        self.assertTrue(result["publishable"], result)
        self.assertEqual(result["overall"], 8.0, result)
        self.assertIn("dead-cli", result["calls"], result)
        self.assertIn("live-cli", result["calls"], result)
        self.assertIn("author-cli#recheck", result["calls"], result)


if __name__ == "__main__":
    unittest.main()
