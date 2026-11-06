# -*- coding: utf-8 -*-
"""`--check-inherit` 只读判据守测：复现 retry 会继承的章号，别靠人眼挑本子。

夹具 `tests/fixtures/retry_inherit_real_runs.json` 是 2026-11-06 17:1x 从生产
`/api/state` + `/api/runs/<id>` 只读取的 **20 条真实 run**（4 个 11-09 待续跑
task 下的全部 run），与 `tests/test_retry_inherit_regression.py` 共用同一份。

守八件事：

1. 判据与**真调 `store.retry_task`** 逐章一致（不是判据自己算自己）：4 个 task
   各装一次隔离账本、真跑一次 retry，比对 `run["inherit"]["done_chapters"]`。
2. 真实样本全被判过：20/20 run、4/4 task、历史章号 30 章、派生收尸 4 条，
   无 `unknown` 沉默（决策 18：报真被判过的样本数）。
3. 11-09 挑本子的结论可证：`t-20261104-170153-4815` 一章不丢，另外 3 本各
   倒退 4 章（[54..57] / [65..68] / [3..6]），推荐先 retry 前者。
4. **部署包未重启**按旧代码只会继承 steps：`-4815` 多丢 [3..8] 6 章。
5. 判不了的三种情况一律 `unknown`、不上绿标：`publishable=False` 完结 verdict
   （ledger 会剔除未达标章，published_chapters 不在 API）、无非降级大纲
   （retry 根本不落 inherit）、该 task 下没有 run 详情。合成样本单列标注、
   不计真样本。
6. CLI `--check-inherit` 零 `api_post`，stdout 真的打出样本数、倒退章清单、
   legacy 对比与推荐本子（判据有消费点，不是死代码）。
7. `--check` 里提示 `--check-inherit`（入口存在）。
8. 源码锚点锁 `store.retry_task` 的 inherit 并集修复没被回滚——判据复刻的
   是修复后的语义，部署包若仍是旧代码，`--check-inherit` 的结论会失真。

重启收尸（`running → failed + interrupted=restart`）是 11-09 才会出现的状态，
生产此刻不存在，由 `_reap_after_restart()` 派生，单列标注、不计真样本。
"""
from __future__ import annotations

import copy
import importlib.util
import io
import json
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path
from unittest import mock

from app.core import store

ROOT = Path(__file__).resolve().parents[1]
FIXTURE = ROOT / "tests" / "fixtures" / "retry_inherit_real_runs.json"
_spec = importlib.util.spec_from_file_location(
    "quota_recovery_inherit", ROOT / "scripts" / "quota_recovery.py")
qr = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(qr)

# 与 test_retry_inherit_regression.py 的 EXPECTED_DONE 逐字一致（真调 retry 的落盘值）。
EXPECTED_DONE = {
    "t-20261022-104907-1000": [52, 53],
    "t-20261029-102103-5910": [59, 60, 61, 62, 63, 64],
    "t-20261104-170153-4815": [1, 2, 3, 4, 5, 6, 7, 8],
    "t-20261105-152926-2324": [1, 2],
}
# 相对「历史出现过的章号并集」的倒退章（17:4x 生产只读取证）。
EXPECTED_LOST = {
    "t-20261022-104907-1000": [54, 55, 56, 57],
    "t-20261029-102103-5910": [65, 66, 67, 68],
    "t-20261104-170153-4815": [],
    "t-20261105-152926-2324": [3, 4, 5, 6],
}
RECOMMENDED = "t-20261104-170153-4815"


def _fixture():
    return json.loads(FIXTURE.read_text(encoding="utf-8"))


def _inputs(rows=None):
    rows = rows if rows is not None else _fixture()["runs"]
    state_runs = [{"id": r["id"], "task_id": r["task_id"]} for r in rows]
    details = {r["id"]: r for r in rows}
    return state_runs, details


def _reap_after_restart(runs):
    """派生：store.py:900/941 重启收尸。生产此刻仍是 running/paused，不计真样本。"""
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


class InheritJudgeTests(unittest.TestCase):
    """纯函数判据：真实夹具、真被判过样本数、倒退章清单、unknown 边界。"""

    def test_real_runs_are_all_judged(self):
        """决策 18：报真被判过的样本数；判据沉默不算判得准。"""
        state_runs, details = _inputs()
        plan = qr.plan_check_inherit(state_runs, details)
        self.assertEqual(plan["judged_run_count"], 20)
        self.assertEqual(plan["judged_task_count"], 4)
        self.assertEqual(plan["historical_chapter_count"], 30)
        self.assertEqual(plan["derived_reaped_count"], 4)
        self.assertEqual(plan["unknown_task_count"], 0)
        self.assertEqual(plan["recommended_first"], RECOMMENDED)
        print("\n真被判过的真实 run 样本数 %d/%d；待续跑 task %d/%d；"
              "历史出现过的章号合计 %d 章" % (
                  plan["judged_run_count"], len(details),
                  plan["judged_task_count"], len(qr.RESUME_TASK_IDS),
                  plan["historical_chapter_count"]))
        print("派生重启收尸 run %d 条（不计真样本）；判不了的 task %d 个" % (
            plan["derived_reaped_count"], plan["unknown_task_count"]))

    def test_three_books_regress_and_one_is_safe(self):
        """11-09 挑本子的结论必须可证：列出每本倒退章，别靠人眼。"""
        state_runs, details = _inputs()
        plan = qr.plan_check_inherit(state_runs, details)
        by_id = {r["task_id"]: r for r in plan["results"]}
        self.assertEqual(set(by_id), set(EXPECTED_DONE))
        judged = 0
        for tid, lost in EXPECTED_LOST.items():
            r = by_id[tid]
            self.assertEqual(r["done_chapters"], EXPECTED_DONE[tid], tid)
            self.assertEqual(r["lost_chapters"], lost, tid)
            self.assertEqual(r["verdict"], "ok" if not lost else "regress", tid)
            judged += 1
        print("\n真被判过倒退章的 task %d/%d：3 本各倒退 4 章、%s 一章不丢"
              % (judged, len(EXPECTED_DONE), RECOMMENDED))
        self.assertEqual(judged, 4)

    def test_legacy_without_restart_loses_six_more(self):
        """部署包已同步但未重启：运行进程仍加载旧模块，只按 steps 取。"""
        state_runs, details = _inputs()
        plan = qr.plan_check_inherit(state_runs, details)
        by_id = {r["task_id"]: r for r in plan["results"]}
        self.assertEqual(by_id[RECOMMENDED]["legacy_done_chapters"], [1, 2])
        self.assertEqual(by_id[RECOMMENDED]["extra_lost_without_restart"],
                         [3, 4, 5, 6, 7, 8])
        for tid in EXPECTED_DONE:
            if tid == RECOMMENDED:
                continue
            self.assertEqual(by_id[tid]["extra_lost_without_restart"], [],
                             "%s 的 09:48 run 自身 inherit 已衰减，修复不再改善" % tid)
        print("\n未重启按旧代码，%s 会多丢 %s（共 6 章）" % (
            RECOMMENDED, by_id[RECOMMENDED]["extra_lost_without_restart"]))

    def test_unknown_on_publishable_false_verdict(self):
        """合成：ledger 会剔除未达标章，API 没有 published_chapters → unknown。"""
        rows = copy.deepcopy(_fixture()["runs"])
        mine = sorted((r for r in rows if r["task_id"] == RECOMMENDED),
                      key=lambda r: (r.get("created_at_ns") or 0, r["id"]),
                      reverse=True)
        mine[0]["verdict"] = {"publishable": False}
        state_runs, details = _inputs(rows)
        plan = qr.plan_check_inherit(state_runs, details)
        r = next(x for x in plan["results"] if x["task_id"] == RECOMMENDED)
        self.assertTrue(r["redo_risk"])
        self.assertEqual(r["verdict"], "unknown")
        self.assertIn("publishable=False", " ".join(r["reasons"]))
        self.assertEqual(plan["unknown_task_count"], 1)
        print("\n合成样本（publishable=False，不计真样本）：判不了 %d 个 task，不上绿标"
              % plan["unknown_task_count"])

    def test_unknown_without_outline_or_runs(self):
        """合成：无非降级大纲 / 该 task 没有详情，一律 unknown 不猜。"""
        rows = copy.deepcopy(_fixture()["runs"])
        for r in rows:
            if r["task_id"] == RECOMMENDED:
                r["outline"] = {"degraded": True, "chapters": []}
        state_runs, details = _inputs(rows)
        r = next(x for x in qr.plan_check_inherit(state_runs, details)["results"]
                 if x["task_id"] == RECOMMENDED)
        self.assertFalse(r["outline_available"])
        self.assertEqual(r["verdict"], "unknown")
        self.assertIn("大纲", " ".join(r["reasons"]))

        empty = qr.plan_check_inherit([{"id": "r-x", "task_id": RECOMMENDED}], {})
        self.assertEqual(empty["judged_run_count"], 0)
        self.assertEqual(empty["unknown_task_count"], 4)
        self.assertEqual(empty["recommended_first"], "")
        print("合成样本（无大纲 / 无详情，不计真样本）：两种都判不了，推荐为空")

    def test_cli_is_readonly_and_prints_the_verdict(self):
        """判据得有消费点：CLI 零 POST，stdout 真的打出样本数与倒退章清单。"""
        state_runs, details = _inputs()

        def fake_get(base, path):
            if path == "/api/state":
                return {"runs": state_runs, "tasks": [], "pause_control": {}}
            if path.startswith("/api/runs/"):
                rid = path.rsplit("/", 1)[-1]
                self.assertIn(rid, details)
                return {"run": details[rid]}
            raise AssertionError("意外的 GET：" + path)

        buf = io.StringIO()
        with mock.patch.object(qr, "api_get", side_effect=fake_get), \
             mock.patch.object(qr, "api_post") as post, \
             redirect_stdout(buf):
            rc = qr.main(["--check-inherit"])
        self.assertEqual(rc, 0)
        post.assert_not_called()
        text = buf.getvalue()
        self.assertIn("真被判过的真实 run 样本数 20/20", text)
        self.assertIn("派生重启收尸 run 4 条", text)
        self.assertIn("模拟重启收尸后", text)
        self.assertIn("一章不丢", text)
        self.assertIn("会倒退 4 章", text)
        self.assertIn("[54,55,56,57]", text)
        self.assertIn("多丢 [3,4,5,6,7,8]", text)
        self.assertIn("先只 retry %s" % RECOMMENDED, text)
        self.assertIn("POST /api/selfupdate/restart", text)
        print("\n--check-inherit 零 POST，stdout 含 20/20 样本数、倒退章清单与推荐本子")

    def test_check_points_to_inherit_judge(self):
        """`--check` 是入口：挑本子前先跑 --check-inherit。"""
        auto = {"tasks": [{"id": tid, "enabled": False, "name": "x", "next_run": ""}
                          for tid in qr.TASK_IDS]}
        state = {"runs": [], "tasks": [], "pause_control": {"active": 0, "paused": 0}}

        def fake_get(base, path):
            return auto if path == "/api/automation" else state

        buf = io.StringIO()
        with mock.patch.object(qr, "api_get", side_effect=fake_get), \
             mock.patch.object(qr, "api_post") as post, \
             redirect_stdout(buf):
            qr.main(["--check"])
        post.assert_not_called()
        self.assertIn("--check-inherit", buf.getvalue())

    def test_store_fix_anchor_not_rolled_back(self):
        """判据复刻的是修复后语义；部署包若回滚，结论会失真。"""
        src = (ROOT / "app" / "core" / "store.py").read_text(encoding="utf-8")
        self.assertIn("got = sorted(set(got) | inh_done)", src)
        self.assertIn('prev_inh = prev.get("inherit") or {}', src)


class StoreCrossCheckTests(unittest.TestCase):
    """交叉验证：判据输出必须与真调 store.retry_task 的落盘值逐章一致。"""

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

    def test_projection_matches_real_retry_task(self):
        f = _fixture()
        runs, reaped = _reap_after_restart(f["runs"])
        state_runs, details = _inputs(f["runs"])  # 判据自己派生收尸，传生产原值
        plan = qr.plan_check_inherit(state_runs, details)
        tasks = {t["id"]: t for t in f["tasks"]}
        checked = 0
        for res in plan["results"]:
            tid = res["task_id"]
            store._TASKS[tid] = copy.deepcopy(tasks[tid])
            store._RUNS = {r["id"]: copy.deepcopy(r) for r in runs
                           if r.get("task_id") == tid}
            for rid in store._RUNS:
                (self.runs_dir / rid).mkdir(parents=True, exist_ok=True)
            ok, err, new_run = store.retry_task(tid)
            self.assertTrue(ok, "%s 被拒：%s" % (tid, err))
            real = (new_run.get("inherit") or {}).get("done_chapters")
            self.assertEqual(real, res["done_chapters"],
                             "%s：判据与真调 retry_task 分叉" % tid)
            self.assertEqual(real, EXPECTED_DONE[tid])
            checked += 1
        print("\n交叉验证真调 store.retry_task：%d/%d 个 task 的 done_chapters 逐章一致；"
              "派生收尸 %d 条（不计真样本）" % (checked, len(EXPECTED_DONE), len(reaped)))
        self.assertEqual(checked, 4)


if __name__ == "__main__":
    unittest.main()
