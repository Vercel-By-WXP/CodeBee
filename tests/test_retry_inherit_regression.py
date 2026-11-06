# -*- coding: utf-8 -*-
"""断点续跑「继承衰减」回归守测（2026-11-06 生产实案）。

夹具 `tests/fixtures/retry_inherit_real_runs.json` 是 2026-11-06 17:1x 从生产
`/api/state` + `/api/runs/<id>` 只读取的 **20 条真实 run**（4 个 11-09 待续跑
task 下的全部 run）；只裁掉 command/configuration/output/log/messages 与
verdict 里与章号继承无关的大字段，**未改写任何字符串**。

病灶：`store.retry_task` 的 `done` 原先只从 `prev["steps"]` 里 status=done 的
`draft-cN` 取；中途暂停 / 超时 / 重启中断的 run，steps 只覆盖到中断点，上一遍
继承来并已复用的章（`inherit.done_chapters`）不写进 steps（复用章虽也记 step，
但只记到中断点为止）。而 `pipeline.py:4606` 是
`reuse = i in done_set and os.path.exists(ch_file)` —— **章号必须进 done_set，
磁盘有稿也不够** → 下一遍把这些章重写，白烧刚恢复的额度。
生产实案 `t-20261104-170153-4815`：09:48 批次 run 的
`inherit.done_chapters=[1..8]`，steps 只有 `draft-c1/c2` done，
修复前 retry 会退成 `[1,2]`（丢 6 章）。

修法：同一 prev 的 `inherit.done_chapters` 并进 done 候选；`inherit.chapter_scores`
按章号补进 base_scores（实时分数优先），避免「章号补回但分数缺失 → 复用章被
重评」再烧一轮。无 inherit 的正常路径逐字等价；redo 剔除 / 已发布章豁免 /
ledger 逻辑一律不动。

**诚实的边界**：这修的是「下一轮继续衰减」，不是「找回更早批次已经丢掉的章」。
`retry_task` 的 `if not done` 只取最新终态 prev，另外 3 本书的 09:48 run 自身
inherit 就比历史 run 少（说明之前已衰减过），修复后不会更差但也不会自动找回。
11-09 第一本建议挑 `t-20261104-170153-4815`（inherit 完整 1..8，修复后一章不丢）；
其余 3 本按交接单第 3 条先核对磁盘成稿与历史章号，别把本守测当成「零重写」保证。

决策 18：报出真被判过的真实样本数。重启收尸（`running → failed +
interrupted=restart`）是 11-09 才会出现的状态，生产此刻不存在，由
`_reap_after_restart()` 派生，单列标注、不计真样本。合成反向样本同样单列标注。
"""
from __future__ import annotations

import copy
import json
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from app.core import store

ROOT = Path(__file__).resolve().parents[1]
FIXTURE = ROOT / "tests" / "fixtures" / "retry_inherit_real_runs.json"

# 修复后 retry 应得到的 done_chapters（来自 09:48 批次 run 的
# steps draft-c done ∪ 同 run 的 inherit.done_chapters）。
EXPECTED_DONE = {
    "t-20261022-104907-1000": [52, 53],
    "t-20261029-102103-5910": [59, 60, 61, 62, 63, 64],
    "t-20261104-170153-4815": [1, 2, 3, 4, 5, 6, 7, 8],
    "t-20261105-152926-2324": [1, 2],
}
# 修复前（只按 steps 取）会得到的 done_chapters —— 第三本退 6 章。
LEGACY_DONE = {
    "t-20261022-104907-1000": [52, 53],
    "t-20261029-102103-5910": [59, 60, 61, 62, 63, 64],
    "t-20261104-170153-4815": [1, 2],
    "t-20261105-152926-2324": [1, 2],
}
# 修复后 inherit.chapter_scores 应覆盖的章（实时分数 + 继承分数补缺）。
EXPECTED_SCORE_CHS = {
    "t-20261022-104907-1000": [52],
    "t-20261029-102103-5910": [59, 60, 61, 62, 63, 64],
    "t-20261104-170153-4815": [1, 2, 3, 4, 5, 6, 7, 8],
    "t-20261105-152926-2324": [1],
}
PAUSED_RUN_IDS = {
    "r-20261106-094827-3523", "r-20261106-094824-4277",
    "r-20261106-094822-4307", "r-20261106-094817-8634",
}


def _fixture():
    return json.loads(FIXTURE.read_text(encoding="utf-8"))


def _draft_done(steps):
    return sorted({int(s["role"].split("c")[-1])
                   for s in (steps or [])
                   if s.get("status") == "done"
                   and (s.get("role") or "").startswith("draft-c")
                   and str(s.get("role")).split("c")[-1].isdigit()})


def _reap_after_restart(runs):
    """派生：`store.py:900/941` 重启收尸，running/paused → failed + interrupted=restart。

    生产此刻（11-06）这 4 条仍是 running/paused，收尸后的状态**不是落盘原值**，
    单列返回 ids 供守测标注，不计真样本。
    """
    out = copy.deepcopy(runs)
    reaped = []
    for r in out:
        if r.get("status") in ("queued", "running"):
            reaped.append(r["id"])
            r["status"] = "failed"
            r["interrupted"] = "restart"
            r["paused"] = False
            r["pause_phase"] = ""
    return out, reaped


class RetryInheritRegressionTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.root = Path(self._tmp.name)
        self.tasks_dir = self.root / "tasks"
        self.runs_dir = self.root / "runs"
        self.tasks_dir.mkdir(parents=True)
        self.runs_dir.mkdir(parents=True)
        self._prev = (store._TASKS, store._RUNS, store.paths.TASKS_DIR,
                      store.paths.RUNS_DIR)
        store._TASKS = {}
        store._RUNS = {}
        store.paths.TASKS_DIR = self.tasks_dir
        store.paths.RUNS_DIR = self.runs_dir
        self._bump = mock.patch.object(store, "bump_state")
        self._bump.start()

    def tearDown(self):
        self._bump.stop()
        store._TASKS, store._RUNS, store.paths.TASKS_DIR, store.paths.RUNS_DIR = self._prev
        self._tmp.cleanup()

    def _install(self, task_id, runs):
        f = _fixture()
        task = next(t for t in f["tasks"] if t["id"] == task_id)
        store._TASKS[task_id] = copy.deepcopy(task)
        store._RUNS = {r["id"]: copy.deepcopy(r) for r in runs
                       if r.get("task_id") == task_id}
        for rid in store._RUNS:
            (self.runs_dir / rid).mkdir(parents=True, exist_ok=True)

    def _retry(self, task_id):
        ok, err, run = store.retry_task(task_id)
        self.assertTrue(ok, "retry_task 被拒：%s" % err)
        self.assertIsInstance(run.get("inherit"), dict,
                              "连载 retry 必须落 inherit（否则断点续跑退化成从零重写）")
        return run["inherit"]

    def test_real_runs_do_not_regress_inherited_chapters(self):
        """4 个真实 task 各真跑一次 retry_task；报出真被判过样本数。"""
        f = _fixture()
        runs, reaped = _reap_after_restart(f["runs"])
        self.assertEqual(set(reaped), PAUSED_RUN_IDS)
        judged_tasks = 0
        judged_chapters = 0
        for task_id, expected in EXPECTED_DONE.items():
            self._install(task_id, runs)
            inh = self._retry(task_id)
            self.assertEqual(inh.get("done_chapters"), expected,
                             "%s 的 done_chapters 倒退" % task_id)
            score_chs = sorted({c.get("chapter")
                                for c in (inh.get("chapter_scores") or [])
                                if isinstance(c, dict)})
            self.assertEqual(score_chs, EXPECTED_SCORE_CHS[task_id],
                             "%s 的继承分数没补回" % task_id)
            self.assertEqual(inh.get("redo_issues"), [],
                             "%s 误进「重写未达标章」语义" % task_id)
            judged_tasks += 1
            judged_chapters += len(expected)
        print("\n真被判过的真实 run 样本数 %d/%d；真被判过的待续跑 task %d/%d"
              % (len(f["runs"]), len(f["runs"]), judged_tasks, len(EXPECTED_DONE)))
        print("真被判过的 done_chapters 章号合计 %d 章；派生收尸样本 %d 条（不计真样本）"
              % (judged_chapters, len(reaped)))
        self.assertEqual(judged_tasks, 4)
        self.assertEqual(len(f["runs"]), 20)

    def test_legacy_rule_would_regress_six_chapters(self):
        """复现修复前的取法，证明衰减真实存在（不是凭推断写守测）。"""
        f = _fixture()
        runs, _ = _reap_after_restart(f["runs"])
        for task_id, legacy in LEGACY_DONE.items():
            prev = max((r for r in runs if r.get("task_id") == task_id),
                       key=lambda r: (r.get("created_at_ns") or 0, r["id"]))
            self.assertEqual(_draft_done(prev.get("steps")), legacy)
        lost = set(EXPECTED_DONE["t-20261104-170153-4815"]) - set(
            LEGACY_DONE["t-20261104-170153-4815"])
        self.assertEqual(sorted(lost), [3, 4, 5, 6, 7, 8])
        print("\n修复前 t-20261104-170153-4815 会丢的章：%s（共 %d 章）"
              % (sorted(lost), len(lost)))

    def test_source_locks_inherit_merge(self):
        """变异护栏：抽掉合并逻辑，主守测必须变红。"""
        src = (ROOT / "app" / "core" / "store.py").read_text(encoding="utf-8")
        body = src[src.index("def retry_task("):src.index("def continue_info(")]
        self.assertIn('prev_inh = prev.get("inherit") or {}', body)
        self.assertIn("inh_done", body)
        self.assertIn("got = sorted(set(got) | inh_done)", body)
        self.assertIn("done_chapters", body)
        self.assertIn("chapter_scores", body)
        # 既有语义不许被顺手改掉
        self.assertIn("done = [n for n in done if n not in redo]", body)
        self.assertIn("redo.difference_update(published)", body)
        self.assertIn('("queued", "running")', body)

    def test_no_inherit_path_is_unchanged(self):
        """合成反向样本（单列标注、不计真样本）：无 inherit 的 run 行为逐字等价。"""
        f = _fixture()
        runs, _ = _reap_after_restart(f["runs"])
        synthetic = [{
            "id": "r-synth-no-inherit", "task_id": "t-synth", "status": "failed",
            "created_at_ns": 1, "interrupted": None,
            "outline": {"degraded": False, "source": "planner",
                        "chapters": [{"title": "c1"}]},
            "inherit": {}, "messages": [], "verdict": {},
            "steps": [
                {"n": 1, "role": "draft-c1", "status": "done"},
                {"n": 2, "role": "draft-c2", "status": "failed"},
                {"n": 3, "role": "critique-r1", "status": "done"},
            ],
            "chapter_scores": [{"chapter": 1, "passed": True}],
        }]
        f["tasks"].append({"id": "t-synth", "title": "合成", "status": "failed",
                           "serial": {"chapters": 8}, "type": "serial_novel",
                           "workdir": "", "created_at_ns": 1})
        store._TASKS["t-synth"] = f["tasks"][-1]
        store._RUNS = {r["id"]: copy.deepcopy(r) for r in synthetic}
        for rid in store._RUNS:
            (self.runs_dir / rid).mkdir(parents=True, exist_ok=True)
        inh = self._retry("t-synth")
        self.assertEqual(inh["done_chapters"], [1])
        self.assertEqual([c["chapter"] for c in inh["chapter_scores"]], [1])
        self.assertEqual(inh["redo_issues"], [])

    def test_reap_is_derived_not_real(self):
        """4 条 paused run 是生产原值；收尸状态只由守测派生，别混进真样本。"""
        f = _fixture()
        paused = [r for r in f["runs"] if r["status"] in ("running", "queued")]
        self.assertEqual(len(paused), 4)
        self.assertEqual({r["id"] for r in paused}, PAUSED_RUN_IDS)
        self.assertTrue(all(r["paused"] is True and r["pause_phase"] == "paused"
                            and r["interrupted"] is None for r in paused))
        runs, reaped = _reap_after_restart(f["runs"])
        self.assertEqual(len(reaped), 4)
        self.assertTrue(all(r["status"] == "failed" and r["interrupted"] == "restart"
                            for r in runs if r["id"] in set(reaped)))
        self.assertTrue(all(r["status"] in ("running", "queued") for r in f["runs"]
                            if r["id"] in PAUSED_RUN_IDS))
        print("\n派生收尸样本 4 条已单列标注；夹具真样本仍为 %d 条" % len(f["runs"]))


if __name__ == "__main__":
    unittest.main()
