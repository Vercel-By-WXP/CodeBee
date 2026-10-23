# -*- coding: utf-8 -*-
"""「修复后重验」专项回归（2026-10-23）。

背景：全仓此前没有任何测试驱动过 `pipeline_code._run_code`——代码任务的
「实现 → 验证 → 修复 → 重验」闭环只被真实跑过，没有回归保护。这意味着
「修复轮被验证失败驱动」「重验通过后才判通过」「连败时收口为未通过」
这些关键语义改坏了没人知道。

本测试补上这条闭环，且**不掺假**：
- `verify_command` 由 `runner.run_process` 起**真实子进程**执行，脚本按调用
  次数计分（第 N 次才退出 0），首轮验证失败是确定性事实，不是打桩假失败；
- 实现/修复步骤用 mock 智能体（不耗额度），但修复提示词必须真的带上验证
  失败清单——锁的是「修复轮由验证失败驱动」，不是「反正最后也过了」；
- 终态判据读 verdict 三态字段（verify_ran/verify_pass/pass）与 report.md，
  与线上报告同一份数据。

全部用例跑在隔离 TUTTI_DATA + 临时工作目录的子进程里，不启动服务、
不碰 8765、不消耗任何模型额度。
"""
import json
import os
import subprocess
import sys
import tempfile
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

# 会计次验证脚本：第 success_at 次调用（含）之后才退出 0。计数器与脚本同目录，
# 同一次 probe 内的多次 _run_verify 共享，不同用例的临时目录互相隔离。
VERIFY_SCRIPT = r'''
import os, sys
here = os.path.dirname(os.path.abspath(__file__))
counter = os.path.join(here, "verify_attempts.txt")
n = 0
if os.path.exists(counter):
    try:
        n = int(open(counter, encoding="utf-8").read().strip() or "0")
    except ValueError:
        n = 0
n += 1
with open(counter, "w", encoding="utf-8") as fh:
    fh.write(str(n))
success_at = int(sys.argv[1])
if n >= success_at:
    print("verify attempt %d: ok" % n)
    sys.exit(0)
print("verify attempt %d: failing on purpose" % n)
sys.exit(1)
'''

CODE_PROBE = r'''
import json, os, sys
from unittest import mock
from app.core import pipeline, store

workdir = sys.argv[1]
verify_script = sys.argv[2]
success_at = sys.argv[3]
mode = sys.argv[4]  # flaky | stuck | noverify | deep

task = {"type": "code", "title": "修复后重验回归", "goal": "创建 hello.py 并跑通",
        "workdir": workdir, "difficulty": "hard" if mode == "deep" else "easy"}
if mode != "noverify":
    task["verify_command"] = '"%s" "%s" %s' % (sys.executable, verify_script, success_at)

agent = {"id": "mock-a", "mode": "mock", "kind": "generic", "label": "Mock CLI",
         "command": "mock-a", "model": "m1"}
reviewer = dict(agent, id="mock-b", label="Mock Reviewer")
steps = []

def fake_step(run_id, role, bound, prompt, wd, readonly=False, ev=None, **kw):
    steps.append({"role": role, "prompt": prompt})
    if role.startswith("implement") or role.startswith("fix"):
        # 模拟真实实现：实现轮与修复轮都往工作区落东西，证明轮次真的在跑
        with open(os.path.join(wd, "hello.py"), "a", encoding="utf-8") as fh:
            fh.write("# %s\n" % role)
    return {"ok": True, "text": "[fake] %s" % role, "json": None,
            "cost_usd": 0.0, "tokens": 0, "error": "", "raw": {"exit_code": 0}}

t = store.create_task(task)
run = store.create_run("orchestration", task["title"], task_id=t["id"])
store.update_run(run["id"], status="running")
patches = [
    mock.patch.object(pipeline, "_run_step", side_effect=fake_step),
    mock.patch.object(pipeline.modelhub, "bind_agent",
                      side_effect=lambda a, d="default": a),
    mock.patch.object(pipeline.router, "pick",
                      return_value=(agent, "test: only mock CLI")),
    mock.patch.object(pipeline.router, "pick_reviewer",
                      return_value=(reviewer, "test: mock reviewer")),
    mock.patch.object(pipeline.router, "pick_switch_candidate",
                      return_value=(None, "test: no alternate CLI")),
    mock.patch("app.core.bench.benched_ids", return_value=set()),
]
if mode == "deep":
    # 高难度走完整规划链：规划器打桩，验证/修复/评审全真
    patches.append(mock.patch.object(
        pipeline.planner, "make_code_plan",
        return_value={"source": "test-plan", "difficulty": "hard",
                      "steps": [{"title": "实现", "detail": task["goal"]}],
                      "workflow": {}}))
for p in patches:
    p.start()
try:
    pipeline._run_code({"id": run["id"]}, task, [agent, reviewer], None, {}, "auto")
finally:
    for p in patches:
        p.stop()

r = store.get_run(run["id"])
v = r.get("verdict") or {}
report = (store.run_dir(run["id"]) / "report.md").read_text(encoding="utf-8")
print("@@RESULT@@" + json.dumps({
    "status": r.get("status"),
    "verdict_pass": v.get("pass"),
    "verify_ran": v.get("verify_ran"),
    "verify_pass": v.get("verify_pass"),
    "review_pass": v.get("review_pass"),
    "repairs": v.get("repairs"),
    "steps": [s["role"] for s in steps],
    "run_steps": [{"role": s.get("role"), "status": s.get("status"),
                   "summary": (s.get("summary") or "")[:60]}
                  for s in (r.get("steps") or [])],
    "fix_prompts": [s["prompt"] for s in steps if s["role"].startswith("fix")],
    "summary": r.get("summary") or "",
    "report": report,
}, ensure_ascii=False))
'''


class CodeRepairReverifyTests(unittest.TestCase):
    """跑真实 _run_code 闭环：verify 是真子进程，实现/修复是 mock 智能体。"""

    def _run_probe(self, tmp, mode, success_at):
        workspace = os.path.join(tmp, "workspace")
        os.makedirs(workspace, exist_ok=True)
        script = os.path.join(tmp, "verify_counter.py")
        with open(script, "w", encoding="utf-8") as fh:
            fh.write(VERIFY_SCRIPT)
        env = dict(os.environ)
        env["TUTTI_DATA"] = os.path.join(tmp, "data")
        env["PYTHONIOENCODING"] = "utf-8"
        proc = subprocess.run(
            [sys.executable, "-c", CODE_PROBE, workspace, script,
             str(success_at), mode],
            cwd=ROOT, env=env, timeout=180, capture_output=True, text=True,
            encoding="utf-8", errors="replace")
        if proc.returncode != 0:
            raise AssertionError("probe failed: %s" % proc.stderr[-3000:])
        line = [row for row in proc.stdout.splitlines()
                if row.startswith("@@RESULT@@")][-1]
        return json.loads(line[len("@@RESULT@@"):]), workspace

    def _verify_steps(self, result):
        return [s for s in result["run_steps"] if s["role"] == "verify"]

    # ---- 场景一：首轮验证失败，修复一轮后重验转绿 → 通过 ----

    def test_flaky_verify_passes_after_one_repair_round(self):
        with tempfile.TemporaryDirectory() as tmp:
            result, _ = self._run_probe(tmp, "flaky", success_at=2)

        self.assertTrue(result["verdict_pass"], result)
        self.assertTrue(result["verify_ran"], result)
        self.assertTrue(result["verify_pass"], result)
        # 修复轮真的跑了，且有两轮验证记录：先失败、后通过
        self.assertIn("implement", result["steps"], result)
        self.assertIn("fix-r1", result["steps"], result)
        verify_steps = self._verify_steps(result)
        self.assertEqual([s["status"] for s in verify_steps], ["failed", "done"], result)
        # 轮次记账：初始轮未通过 → 修复轮通过
        kinds = [(r["kind"], r["verify_pass"]) for r in result["repairs"]]
        self.assertEqual(kinds, [("initial", False), ("repair", True)], result)
        # 修复提示词必须带上确定性验证失败清单——修复是被验证失败驱动的
        self.assertEqual(len(result["fix_prompts"]), 1, result)
        self.assertIn("验证命令未通过", result["fix_prompts"][0], result)
        self.assertIn("verify_counter.py", result["fix_prompts"][0], result)
        # 报告与摘要的三态口径
        self.assertIn("✅ 通过", result["report"], result)
        self.assertNotIn("❌ 未通过", result["report"], result)
        self.assertIn("→ 通过", result["report"], result)
        self.assertIn("1 轮修复", result["report"], result)
        self.assertIn("验证通过", result["summary"], result)

    # ---- 场景二：验证连败 → 修满一轮后收口为未通过，不许蒙混 ----

    def test_persistent_verify_failure_terminates_as_not_passed(self):
        with tempfile.TemporaryDirectory() as tmp:
            result, _ = self._run_probe(tmp, "stuck", success_at=99)

        self.assertFalse(result["verdict_pass"], result)
        self.assertTrue(result["verify_ran"], result)
        self.assertFalse(result["verify_pass"], result)
        # 快速路径最多 1 轮修复：验证跑满 2 次，不会无限重试
        self.assertEqual([s["status"] for s in self._verify_steps(result)],
                         ["failed", "failed"], result)
        self.assertIn("fix-r1", result["steps"], result)
        self.assertNotIn("fix-r2", result["steps"], result)
        self.assertEqual([(r["kind"], r["verify_pass"]) for r in result["repairs"]],
                         [("initial", False), ("repair", False)], result)
        # 终态可以收口为 done（跑完了），但结论必须是未通过，且不许出现通过字样
        self.assertEqual(result["status"], "done", result)
        self.assertIn("❌ 未通过", result["report"], result)
        self.assertIn("→ 未通过", result["report"], result)
        self.assertNotIn("✅ 通过", result["report"], result)
        self.assertNotIn("→ 通过", result["report"], result)
        self.assertIn("验证未通过", result["summary"], result)

    # ---- 场景三：没配验证命令 → 验证列必须写「未执行」，不是「通过」 ----

    def test_missing_verify_command_is_reported_as_not_run(self):
        with tempfile.TemporaryDirectory() as tmp:
            result, _ = self._run_probe(tmp, "noverify", success_at=1)

        # 无验证命令时保留一次模型评审（mock 通过）——整体可以过，
        # 但验证列必须如实写「未执行」（r-20260917-195700-0323 回归）。
        self.assertTrue(result["verdict_pass"], result)
        self.assertFalse(result["verify_ran"], result)
        self.assertIn("review", result["steps"], result)
        self.assertIn("→ 未执行", result["report"], result)
        self.assertIn("验证未执行", result["report"], result)
        self.assertNotIn("→ 通过", result["report"], result)
        self.assertIn("验证未执行", result["summary"], result)

    # ---- 场景四：高难度两轮修复后才转绿（gate 连败后恢复评审参与） ----

    def test_two_repair_rounds_then_pass_with_review_back_in(self):
        with tempfile.TemporaryDirectory() as tmp:
            result, _ = self._run_probe(tmp, "deep", success_at=3)

        self.assertTrue(result["verdict_pass"], result)
        self.assertTrue(result["verify_ran"], result)
        self.assertTrue(result["verify_pass"], result)
        self.assertIn("fix-r1", result["steps"], result)
        self.assertIn("fix-r2", result["steps"], result)
        self.assertEqual([s["status"] for s in self._verify_steps(result)],
                         ["failed", "failed", "done"], result)
        self.assertEqual([(r["kind"], r["verify_pass"]) for r in result["repairs"]],
                         [("initial", False), ("repair", False), ("repair", True)],
                         result)
        # 第二轮修复前 gate 已连败 2 次：模型评审必须回来参与诊断
        self.assertIn("review", result["steps"], result)
        self.assertTrue(result["review_pass"], result)
        self.assertIn("✅ 通过", result["report"], result)
        self.assertIn("2 轮修复", result["report"], result)


if __name__ == "__main__":
    unittest.main()
