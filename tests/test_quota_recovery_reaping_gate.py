# -*- coding: utf-8 -*-
"""11-09 九步第 ⑧ 步第五道闸：重启收尸自洽性必须真被写入口消费（只读守测）。

15:4x 实案（离线实证，全 mock、零触网）
----------------------------------------
13:20 为堵「retry_ready 假绿」加了 judge_restart_reaping，但它**只装在
⑥ --check**：⑥ 在 reap!=ok 时明说「别开跑」，而 ⑧ --retry-one 是唯一真烧
额度的写入口，四道闸只用了 judge_retry_readiness（无 active run + 4 task
全终态）。用真实夹具派生「收尸只做一半」对照：

    A 收尸干净     ⑥ 可 retry 一本   ⑧ exit 0 / POST 1
    B 收尸只做一半 ⑥ 别开跑         ⑧ exit 0 / POST 1   ← 假绿，输出连「收尸」都不提

修后 B 变 exit 2 / POST 0。本守测锁住这个结论不被回退。

真样本与派生
------------
- `quota_recovery_restart_reaping.json`：13:03 生产 8765 只读导出的
  **26 run / 47 task / 26 详情**，含 5 条 11-06 历史老账与 4 条 09:48 锚点。
- 写路径复用 14:22 同族守测的 `_state(reaped=True)` / `_details()` / `_run_cli`
  （真实 20 条 run 身份字段）。所有「重启后」状态都是派生，**单列标注、
  不计真被判过样本数**（决策 18）。

dev / prod
----------
纯函数 + mock 掉的 HTTP，零网络、零写盘、零额度；dev
`py -3.13 -m pytest tests/test_quota_recovery_reaping_gate.py -q`，
prod 由 `scripts/run_protected_suite.py` 的 unittest discover 收集。
"""
from __future__ import annotations

import ast
import copy
import importlib.util
import json
import sys
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "quota_recovery.py"
FIXTURE = ROOT / "tests" / "fixtures" / "quota_recovery_restart_reaping.json"
HANDOFF = ROOT / "迭代计划.md"

_spec = importlib.util.spec_from_file_location("quota_recovery_reaping_gate", SCRIPT)
qr = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(qr)

sys.path.insert(0, str(ROOT / "tests"))
from test_quota_recovery_retry_one import (  # noqa: E402
    _state, _details, _run_cli, AFTER, EXPECTED_RECOMMENDED,
)

NEW = "r-new-0001"
FUTURE = "2026-11-09 14:20:01"       # 派生批次时刻，不是真实落盘值
HISTORY = "2026-11-06 09:47:13"      # 夹具里真实存在的历史老账时刻
LATER = "2026-11-09 14:25:01"        # 派生「更晚收尸」，不计真样本
ANCHOR_PREFIX = "r-20261106-0948"


def _fixture():
    return json.loads(FIXTURE.read_text(encoding="utf-8"))


def _anchors(state):
    return [r["id"] for r in state["runs"]
            if str(r["id"]).startswith(ANCHOR_PREFIX)]


def _write_state():
    """⑧ 真写路径状态：4 条锚点摘要层带收尸专属 error（派生，非落盘值）。"""
    state = _state(reaped=True)
    for r in state["runs"]:
        if str(r["id"]).startswith(ANCHOR_PREFIX):
            r["error"] = "服务重启中断，可重试"
    return state


class GateAPI:
    """只认 /api/state 与 /api/runs/<id>；POST 记账，绝不发真实请求。"""

    def __init__(self, state, details, after=None, post_result=None):
        self.state = state
        self.details = details
        self.after = after
        self.post_result = post_result or {"ok": True, "run_id": NEW}
        self.gets = []
        self.posts = []
        self.state_calls = 0

    def get(self, _base, path):
        self.gets.append(path)
        if path == "/api/state":
            self.state_calls += 1
            if self.state_calls > 1 and self.after is not None:
                return self.after
            return self.state
        if path.startswith("/api/runs/"):
            val = self.details.get(path.rsplit("/", 1)[-1], {})
            if val is None:
                # 详情读不到：返回无 run 键的空响应。cmd_retry_one 的
                # `d.get("run") or d` 会得到 {}，交给 unknown 分支，不编造。
                return {}
            return {"run": val}
        raise AssertionError("unexpected GET " + path)

    def post(self, _base, path, body):
        self.posts.append((path, body))
        return self.post_result


def _after():
    return _state(reaped=True, extra_runs=[
        {"id": NEW, "task_id": EXPECTED_RECOMMENDED, "status": "queued"}])


def _run_write(state, details, dry_run=False):
    argv = ["--retry-one", "--confirm-quota-restored"]
    if dry_run:
        argv.append("--dry-run")
    api = GateAPI(state, details, after=_after())
    rc, text = _run_cli(argv, api, now=AFTER)
    return rc, text, api


class RealFixtureTest(unittest.TestCase):
    """报「真被判过样本数」：夹具是真取证，不是合成稿。"""

    def test_01_fixture_is_real_production_data(self):
        fx = _fixture()
        self.assertEqual(len(fx["state"]["runs"]), 26)
        self.assertEqual(len(fx["state"]["tasks"]), 47)
        self.assertEqual(len(fx["run_details"]), 26)
        print("真被判过的真实 run 样本数 %d / 详情 %d"
              % (len(fx["state"]["runs"]), len(fx["run_details"])))

    def test_02_four_anchors_and_five_history_records_exist(self):
        fx = _fixture()
        anchors = [r["id"] for r in fx["state"]["runs"]
                   if str(r["id"]).startswith(ANCHOR_PREFIX)]
        history = [rid for rid, d in fx["run_details"].items()
                   if d.get("interrupted") == "restart"]
        self.assertEqual(len(anchors), 4)
        self.assertEqual(len(history), 5)

    def test_03_prefix_constant_matches_real_anchor_ids(self):
        self.assertEqual(qr.RESTART_BATCH_PREFIX, ANCHOR_PREFIX)
        fx = _fixture()
        ids = qr.restart_anchor_ids(fx["state"])
        self.assertEqual(len(ids), 4)
        self.assertTrue(all(x.startswith(ANCHOR_PREFIX) for x in ids))

    def test_04_shared_helpers_equal_sixth_step_sieve(self):
        """⑥ 原来用 09:48 前缀手筛；抽出的共用函数必须等价、不漂移。"""
        state = _fixture()["state"]
        manual = [str(r.get("id") or "") for r in state["runs"]
                  if str(r.get("id") or "").startswith(ANCHOR_PREFIX)]
        self.assertEqual(qr.restart_anchor_ids(state), manual)


class ReapingGateWritePathTest(unittest.TestCase):
    """⑧ 真写：收尸 ok 才准 POST；blocked / unknown 一律零 POST。"""

    def _base(self):
        return _write_state(), copy.deepcopy(_details())

    def test_05_clean_reaping_posts_once(self):
        state, details = self._base()
        rc, text, api = _run_write(state, details)
        self.assertEqual(rc, 0, text)
        self.assertEqual(len(api.posts), 1, "只准 POST 一次")
        self.assertIn("重启收尸自洽性：ok", text)
        self.assertIn("⑨ 完整命令", text)

    def test_06_half_reaped_is_rejected_with_zero_post(self):
        """核心反例：⑥ 判「别开跑」，⑧ 必须同样拒写（修复前 exit 0 / POST 1）。"""
        state, details = self._base()
        a = _anchors(state)[0]
        for k in ("interrupted", "interrupted_at", "ended_at"):
            details[a].pop(k, None)
        rc, text, api = _run_write(state, details)
        self.assertEqual(rc, 2, text)
        self.assertEqual(len(api.posts), 0, "POST 必须零次")
        self.assertIn("重启收尸自洽性：blocked", text)
        self.assertIn("别开跑", text)
        self.assertIn("收尸只做了一半", text)
        self.assertNotIn("复核通过：", text)

    def test_07_history_ledger_impersonation_is_rejected(self):
        """老账冒充：锚点用历史时刻 + 存在更晚收尸记录 → blocked → 零 POST。"""
        state, details = self._base()
        anchors = _anchors(state)
        for rid in anchors:
            details[rid]["interrupted_at"] = HISTORY
            details[rid]["ended_at"] = HISTORY
        other = next(r["id"] for r in state["runs"]
                     if r["id"] not in anchors)
        details[other].update({"interrupted": "restart",
                              "interrupted_at": LATER, "ended_at": LATER})
        rc, text, api = _run_write(state, details)
        self.assertEqual(rc, 2, text)
        self.assertEqual(len(api.posts), 0)
        self.assertIn("重启收尸自洽性：blocked", text)
        self.assertIn("更晚的收尸记录", text)

    def test_08_missing_detail_is_unknown_and_rejected(self):
        """判不了 ≠ 不通过，更不能放行：unknown 也 fail-closed 零 POST。"""
        state, details = self._base()
        details[_anchors(state)[0]] = None
        rc, text, api = _run_write(state, details)
        self.assertEqual(rc, 2, text)
        self.assertEqual(len(api.posts), 0)
        self.assertIn("重启收尸自洽性：unknown", text)
        self.assertIn("判不了", text)

    def test_09_anchor_list_mismatch_is_rejected(self):
        """锚点被改到前缀外 → 只剩 3 条、与 4 个待续跑 task 对不上。"""
        state, details = self._base()
        for r in state["runs"]:
            if str(r["id"]).startswith(ANCHOR_PREFIX):
                r["id"] = "r-moved-0001"
                break
        rc, text, api = _run_write(state, details)
        self.assertEqual(rc, 2, text)
        self.assertEqual(len(api.posts), 0)
        self.assertIn("重启收尸自洽性", text)
        self.assertIn("对不上", text)

    def test_10_dry_run_bad_reaping_still_exits_zero_with_zero_post(self):
        """⑦ 预演不能因为收尸未就绪变红，但必须明确提醒真写会被拒。"""
        state, details = self._base()
        for k in ("interrupted", "interrupted_at", "ended_at"):
            details[_anchors(state)[0]].pop(k, None)
        rc, text, api = _run_write(state, details, dry_run=True)
        self.assertEqual(rc, 0, text)
        self.assertEqual(len(api.posts), 0)
        self.assertIn("真写时第五道闸会拒写", text)
        self.assertIn("dry-run 结束，生产未改动", text)

    def test_11_dry_run_clean_reaping_still_zero_post(self):
        state, details = self._base()
        rc, text, api = _run_write(state, details, dry_run=True)
        self.assertEqual(rc, 0, text)
        self.assertEqual(len(api.posts), 0)
        self.assertIn("重启收尸自洽性：ok", text)

    def test_12_all_scenarios_never_post_more_than_once(self):
        """四本齐跑抢见底池：任何场景 POST 都不得超过 1 次。"""
        for broken in (False, True):
            state, details = self._base()
            if broken:
                details[_anchors(state)[0]] = {}
            rc, text, api = _run_write(state, details)
            self.assertLessEqual(len(api.posts), 1, text)


class SharedWiringTest(unittest.TestCase):
    """⑥ 与 ⑧ 必须同判据、同锚点、同数据范围；判据不能变死代码。"""

    def test_13_sixth_and_eighth_step_use_same_verdict(self):
        """同一 state/details 上，⑥ 与 ⑧ 调用同一判据得到同一结论。"""
        state = _write_state()
        details = copy.deepcopy(_details())
        expected = qr.restart_anchor_ids(state)
        a = qr.judge_restart_reaping(state, details, expected_ids=expected)
        collected = qr.collect_reap_details(None, state, details)
        b = qr.judge_restart_reaping(state, collected, expected_ids=expected)
        self.assertEqual(a["verdict"], b["verdict"])
        self.assertEqual(a["batch_at"], b["batch_at"])
        self.assertEqual(a["verdict"], "ok")

    def test_14_collect_reap_details_reuses_existing_no_duplicate_get(self):
        """已有详情零重复 GET；别的 task 收尸候选才按需补一次。"""
        state = _write_state()
        details = copy.deepcopy(_details())
        calls = []
        with mock.patch.object(
                qr, "api_get", side_effect=lambda b, p: calls.append(p) or {"run": {}}):
            out = qr.collect_reap_details(None, state, details)
        self.assertEqual(calls, [], "4 条锚点详情已在手，不该重复拉")
        self.assertTrue(set(details).issubset(set(out)))

    def test_15_collect_reap_details_adds_other_task_reaped_run(self):
        """摘要层带收尸 error 但详情缺失 → 补拉，保证 ⑥/⑧ 范围一致。"""
        state = _write_state()
        details = copy.deepcopy(_details())
        rid = "r-other-task-reaped"
        state["runs"].append({"id": rid, "task_id": "t-other", "status": "failed",
                             "error": "服务重启中断，可重试"})
        calls = []
        with mock.patch.object(
                qr, "api_get", side_effect=lambda b, p: calls.append(p) or {"run": {}}):
            out = qr.collect_reap_details(None, state, details)
        self.assertEqual(calls, ["/api/runs/" + rid])
        self.assertIn(rid, out)

    def test_16_source_anchors_eighth_step_consumes_reaping(self):
        src = SCRIPT.read_text(encoding="utf-8")
        tree = ast.parse(src)
        fn = next(n for n in tree.body
                  if isinstance(n, ast.FunctionDef) and n.name == "cmd_retry_one")
        body = ast.dump(fn)
        self.assertIn("judge_restart_reaping", body)
        self.assertIn("collect_reap_details", body)
        self.assertIn("restart_anchor_ids", body)
        self.assertIn('if reap["verdict"] != "ok" and not dry_run:', src)
        self.assertIn("return 2", src)

    def test_17_two_consumer_calls_in_script(self):
        """整个脚本只有 ⑥/⑧ 两处消费；少一处=写入闸没判，多一处=分叉。"""
        src = SCRIPT.read_text(encoding="utf-8")
        tree = ast.parse(src)
        calls = [n for n in ast.walk(tree)
                 if isinstance(n, ast.Call) and isinstance(n.func, ast.Name)
                 and n.func.id == "judge_restart_reaping"]
        self.assertEqual(len(calls), 2)

    def test_18_handoff_step_eight_has_reaping_acceptance(self):
        text = HANDOFF.read_bytes().decode("utf-8")
        self.assertIn("重启收尸自洽性", text)
        self.assertIn("第五道闸", text)


if __name__ == "__main__":
    unittest.main()
