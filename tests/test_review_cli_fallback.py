# -*- coding: utf-8 -*-
"""评审步骤跨 CLI 补位：可降级的失败换人重评，解析失败仍原地重试。"""
import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from app.core import pipeline

REPO = Path(__file__).resolve().parents[1]

PROBE = r"""
import json, sys
from unittest import mock
from app.core import store
from app.core import pipeline

workdir = sys.argv[1]
task = {"type": "doc", "title": "review fallback", "goal": "写一篇说明",
        "workdir": workdir, "manuscript": "manuscript.md",
        "rubric": ["质量"], "threshold": 7.0, "rounds": 1}
agents = [
    {"id": "author", "mode": "mock", "label": "MockAuthor"},
    {"id": "critic-a", "mode": "real", "kind": "codex", "label": "CriticA"},
    {"id": "critic-b", "mode": "real", "kind": "opencode", "label": "CriticB"},
]
calls = []

def fake_step(run_id, role, agent, prompt, workdir, readonly, ev, **kwargs):
    calls.append(agent.get("id"))
    if agent.get("id") == "critic-a":
        return {"ok": False, "text": "", "error": "HTTP 403 forbidden",
                "error_code": "FORBIDDEN", "raw": {"exit_code": 1}}
    body = {"scores": {"质量": 8}, "issues": [], "summary": "ok"}
    return {"ok": True, "text": json.dumps(body, ensure_ascii=False),
            "error": "", "error_code": "", "raw": {"exit_code": 0}}

t = store.create_task(task)
run = store.create_run("orchestration", "review fallback", task_id=t["id"])
store.update_run(run["id"], status="running")
with mock.patch.object(pipeline, "_run_step", side_effect=fake_step), \
     mock.patch.object(pipeline.modelhub, "bind_agent",
                       side_effect=lambda a, d="default": a), \
     mock.patch.object(pipeline.router, "pick",
                       return_value=(agents[0], "mock author")), \
     mock.patch.object(pipeline.router, "pick_critics",
                       return_value=([agents[1], agents[2]], "test critics")), \
     mock.patch.object(pipeline.router, "pick_reviewer",
                       return_value=(agents[2], "model-family isolated")), \
     mock.patch.object(pipeline.time, "sleep", lambda *_a, **_k: None):
    pipeline._run_content_review({"id": run["id"]}, task, agents, None, {}, "auto")
r = store.get_run(run["id"])
plan = r.get("route_plan") or {}
review = plan.get("review") or {}
route = (r.get("verdict") or {}).get("route") or {}
print("@@RESULT@@" + json.dumps({
    "status": r.get("status"),
    "calls": calls,
    "review_participants": review.get("participants"),
    "review_selected": review.get("selected"),
    "critic_reason": route.get("critics", ""),
    "error": (r.get("error") or "")[:100],
}, ensure_ascii=True))
"""


def _agents():
    return [
        {"id": "author", "mode": "mock", "label": "MockAuthor"},
        {"id": "critic-a", "mode": "real", "kind": "codex", "label": "CriticA"},
        {"id": "critic-b", "mode": "real", "kind": "opencode", "label": "CriticB"},
    ]


def _failure(code="FORBIDDEN", error="HTTP 403 forbidden"):
    return {"ok": False, "text": "", "error": error, "error_code": code,
            "raw": {"exit_code": 1}}


def _success(text=None):
    body = {"scores": {"质量": 8}, "issues": [], "summary": "ok"}
    return {"ok": True, "text": text or json.dumps(body), "error": "",
            "error_code": "", "raw": {"exit_code": 0}}


class CritiqueCliFallbackUnitTests(unittest.TestCase):
    def _call(self, agent, run_step, mode="auto", pick_reviewer=None):
        agents = _agents()
        guards = []

        def step(run_id, role, bound, prompt, workdir, readonly, ev, **kwargs):
            guards.append(kwargs.get("swap_guard"))
            return run_step(bound)

        with mock.patch.object(pipeline, "_run_step", side_effect=step), \
             mock.patch.object(pipeline.modelhub, "bind_agent",
                               side_effect=lambda a, d="default": a), \
             mock.patch.object(pipeline.router, "pick_reviewer",
                               return_value=pick_reviewer
                               or (agents[2], "model-family isolated")), \
             mock.patch.object(pipeline.router, "pick_switch_candidate",
                               side_effect=AssertionError("should not relax")), \
             mock.patch("app.core.bench.benched_ids", return_value=set()), \
             mock.patch.object(pipeline.time, "sleep", lambda *_a, **_k: None):
            return pipeline._critique_step_with_cli_fallback(
                "r-test", "critique-r1", agent, "prompt", "workdir", None,
                agents, agents[0], "default", "novel", {}, mode, False), guards

    def test_fallback_eligible_failure_swaps_to_isolated_reviewer(self):
        seen = []

        def run_step(bound):
            seen.append(bound["id"])
            return _failure() if bound["id"] == "critic-a" else _success()

        (cj, res, actual, note), guards = self._call(_agents()[1], run_step)

        self.assertEqual(cj["scores"], {"质量": 8})
        self.assertEqual(actual["id"], "critic-b")
        self.assertEqual(seen, ["critic-a", "critic-b"])
        self.assertIn("评审自动换 CLI", note)
        self.assertIn("critic-a", note)
        self.assertIn("critic-b", note)
        self.assertIn("critic-a", guards[1]["exclude"])
        self.assertIn("author", guards[1]["exclude"])

    def test_auth_failure_swaps_cli(self):
        """cline-cli Unauthorized 案（2026-10-22）：凭据死错必须换人，不原地撞死号。"""
        seen = []

        def run_step(bound):
            seen.append(bound["id"])
            if bound["id"] == "critic-a":
                return _failure(
                    code="AUTH",
                    error="退出码 1；stderr/stdout: error: Unauthorized: Please make "
                          "sure you're using the latest version of Cline and "
                          "re-authenticate your Cline account.")
            return _success()

        (cj, res, actual, note), _ = self._call(_agents()[1], run_step)

        self.assertEqual(cj["scores"], {"质量": 8})
        self.assertEqual(actual["id"], "critic-b")
        self.assertEqual(seen, ["critic-a", "critic-b"])
        self.assertIn("评审自动换 CLI", note)

    def test_parse_failure_retries_same_cli_three_times(self):
        seen = []

        def run_step(bound):
            seen.append(bound["id"])
            return _success(text="not-json")

        (cj, res, actual, note), _ = self._call(_agents()[1], run_step)

        self.assertIsNone(cj)
        self.assertEqual(actual["id"], "critic-a")
        self.assertEqual(seen, ["critic-a", "critic-a", "critic-a"])
        self.assertEqual(note, "")

    def test_manual_mode_never_swaps(self):
        seen = []

        def run_step(bound):
            seen.append(bound["id"])
            return _failure()

        (cj, res, actual, note), _ = self._call(
            _agents()[1], run_step, mode="manual")

        self.assertIsNone(cj)
        self.assertEqual(actual["id"], "critic-a")
        self.assertEqual(seen, ["critic-a", "critic-a", "critic-a"])

    def test_cancel_stops_without_swapping(self):
        seen = []

        def run_step(bound):
            seen.append(bound["id"])
            out = _failure()
            out["raw"]["cancelled"] = True
            return out

        (cj, res, actual, note), _ = self._call(_agents()[1], run_step)

        self.assertIsNone(cj)
        self.assertEqual(seen, ["critic-a"])


class ContentReviewCliFallbackIntegrationTests(unittest.TestCase):
    def test_auto_review_swaps_cli_and_records_actual_route(self):
        with tempfile.TemporaryDirectory() as tmp:
            ws = os.path.join(tmp, "ws")
            os.makedirs(ws)
            env = dict(os.environ)
            env["TUTTI_DATA"] = tmp
            env["PYTHONIOENCODING"] = "utf-8"
            proc = subprocess.run(
                [sys.executable, "-c", PROBE, ws],
                cwd=str(REPO), env=env, timeout=180,
                capture_output=True, text=True)
            if proc.returncode != 0:
                raise AssertionError(
                    "probe failed: %s" % proc.stderr[-2000:])
            line = [l for l in proc.stdout.splitlines()
                    if l.startswith("@@RESULT@@")][-1]
            result = json.loads(line[len("@@RESULT@@"):])

        self.assertEqual(result["status"], "done", result)
        self.assertEqual(result["calls"], ["critic-a", "critic-b"], result)
        self.assertEqual(result["review_participants"], ["critic-b"], result)
        self.assertEqual(result["review_selected"], "critic-b", result)
        self.assertIn("评审自动换 CLI", result["critic_reason"], result)


if __name__ == "__main__":
    unittest.main()
