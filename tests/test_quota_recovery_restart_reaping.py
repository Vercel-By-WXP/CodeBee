# -*- coding: utf-8 -*-
"""11-09 九步流程第 ⑤ 步：重启收尸自洽性判据守测（只读、零写入）。

夹具 `tests/fixtures/quota_recovery_restart_reaping.json` 是 2026-11-07 13:03
从生产 8765 `GET /api/state` + `GET /api/runs/<id>` **只读导出的 26 条真实 run**
（47 task / 26 份详情）。其中 5 条带 `interrupted=restart`，是 11-06 09:47:13
那次重启的历史老账；4 条 09:48 批次此刻仍 `running/paused`，是 11-09 重启
才会收尸的对象。守测里派生的「重启后」状态**单列标注、不计真样本**。

为什么需要这条判据（11-07 复核）
--------------------------------
九步第 ④ 步 `POST /api/selfupdate/restart` 是全链唯一不可逆的生产中断动作；
第 ⑤ 步 `--check` 原只判 `retry_ready`（无 active run + 4 个 task 全终态）就
宣布「可 retry 一本」，看不出这些终态是不是**本次重启收尸**造成的。更坑的是
11-06 那次重启已经给这 4 个 task 的旧 run 打过一模一样的 restart 标记，
光看「有没有标记」或「全局最新时刻」会拿老账冒充本次（离线复核连抓两次）。

判据以**重启前那批 active run 的 id** 为无歧义锚点（id 重启前后不变），
核对 4 条锚点的 `interrupted` / `interrupted_at` / `ended_at` 齐备且同批次，
且不存在比锚点批次更晚的收尸记录。`store.py:900` 本来就收所有 active run，
所以同一批次里别的 task 被一并收尸是**合法**的，单列提醒、不判误伤。
判不了一律 `unknown`、不上绿标（决策 18）。

守八件事
--------
1. 夹具是真数据：26 run / 47 task / 26 详情，5 条 restart 与 5 条 error
   专属文案一一对应，报「真被判过样本数」。
2. `RESTART_INTERRUPT_TEXT` 与 `store.py:47 _RESTART_INTERRUPT_TEXT` 逐字对齐
   （AST 棘轮，防上游文案改了这边还拿旧前缀筛）。
3. 六场景：正常重启 `ok`；收尸只做一半 / 老账冒充 / 锚点清单错位 → `blocked`；
   详情读不到 / 没给锚点 → `unknown`；合法同批次收尸 → `ok` 不假红。
4. 今天（未重启）直接调判据也不给绿标。
5. `cmd_check` 消费点：额度见底期 `retry_ready=False` → **零额外详情 GET**；
   重启后 → 真拉详情、stdout 打印自洽性与真被判过条数。
6. 结论降级：收尸判据非 `ok` 时 `--check` 说「别开跑」，绝不给绿灯。
7. 全程零 `api_post`。
8. 源码锚点：`cmd_check` 真调 `judge_restart_reaping` 且传 `expected_ids`，
   `judge_retry_readiness` 不再采恒为 None 的 `interrupted`（防死代码/假字段）。

dev / prod
----------
纯函数 + mock 掉的 HTTP，零网络、零写盘、零额度；dev 直接
`py -3.13 -m pytest tests/test_quota_recovery_restart_reaping.py -q`，
prod 由 `scripts/run_protected_suite.py` 的 unittest discover 收集。
"""
from __future__ import annotations

import ast
import copy
import importlib.util
import io
import json
import unittest
from contextlib import redirect_stdout
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
FIXTURE = ROOT / "tests" / "fixtures" / "quota_recovery_restart_reaping.json"
SCRIPT = ROOT / "scripts" / "quota_recovery.py"
STORE = ROOT / "app" / "core" / "store.py"
_spec = importlib.util.spec_from_file_location("quota_recovery_reaping", SCRIPT)
qr = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(qr)

FUTURE_BATCH_AT = "2026-11-09 14:20:01"   # 派生用，不是真实落盘值


def _fixture():
    return json.loads(FIXTURE.read_text(encoding="utf-8"))


def _anchor_ids(state):
    """重启前那批 active run（09:48 批次），id 重启前后不变。"""
    return [r["id"] for r in state["runs"]
            if str(r["id"]).startswith("r-20261106-0948")]


def _derive(at=FUTURE_BATCH_AT):
    """把真实夹具派生成「11-09 重启后」；派生部分单列标注、不计真样本。"""
    fx = _fixture()
    state = copy.deepcopy(fx["state"])
    details = copy.deepcopy(fx["run_details"])
    anchors = _anchor_ids(state)
    for r in state["runs"]:
        if r["id"] in anchors:
            r["status"] = "failed"
            r["error"] = "服务重启中断，可重试"
            r["paused"] = False
            details[r["id"]].update({
                "status": "failed", "interrupted": "restart",
                "interrupted_at": at, "ended_at": at,
                "error": "服务重启中断，可重试"})
    for t in state["tasks"]:
        if t["id"] in qr.RESUME_TASK_IDS:
            t["status"] = "failed"
    return state, details, anchors


def _automation(enabled=True):
    return {"tasks": [
        {"id": tid, "enabled": enabled, "name": "任务", "next_run": "2026-11-10 09:00"}
        for tid in qr.TASK_IDS
    ] + [{"id": "auto-20260921-135622-8720", "enabled": False, "next_run": ""}]}


def _fake_get(state, details, calls):
    def fake(base, path):
        calls.append(path)
        if path == "/api/automation":
            return _automation()
        if path == "/api/state":
            return state
        if path.startswith("/api/runs/"):
            rid = path.rsplit("/", 1)[-1]
            return {"run": details.get(rid, {})}
        raise AssertionError("意外 GET：" + path)
    return fake


class RestartReapingTest(unittest.TestCase):

    def test_01_fixture_is_real_production_data(self):
        """报「真被判过样本数」：26 run / 47 task / 26 详情 / 5 条老账。"""
        fx = _fixture()
        self.assertEqual(len(fx["state"]["runs"]), 26)
        self.assertEqual(len(fx["state"]["tasks"]), 47)
        self.assertEqual(len(fx["run_details"]), 26)
        marked = [rid for rid, d in fx["run_details"].items()
                  if d.get("interrupted") == "restart"]
        by_error = [r["id"] for r in fx["state"]["runs"]
                    if qr._run_reaped_by_restart(r)]
        self.assertEqual(len(marked), 5)
        self.assertEqual(set(marked), set(by_error),
                         "摘要层的收尸文案必须与详情层的 restart 标记一一对应")
        # 4 条 09:48 锚点此刻仍是 running/paused，重启后才收尸（派生不计真样本）
        anchors = _anchor_ids(fx["state"])
        self.assertEqual(len(anchors), 4)
        self.assertTrue(all(
            r["status"] == "running" and r["paused"]
            for r in fx["state"]["runs"] if r["id"] in anchors))
        print("真被判过的真实 run 样本数：26；历史收尸老账 5；重启前 active 锚点 4")

    def test_02_error_text_matches_store_constant(self):
        """AST 棘轮：上游 store.py 改了文案，这边必须跟着改。"""
        tree = ast.parse(STORE.read_text(encoding="utf-8"))
        values = {}
        for node in tree.body:
            if isinstance(node, ast.Assign) and len(node.targets) == 1:
                tgt = node.targets[0]
                if isinstance(tgt, ast.Name) and tgt.id == "_RESTART_INTERRUPT_TEXT":
                    values[tgt.id] = ast.literal_eval(node.value)
        self.assertEqual(values.get("_RESTART_INTERRUPT_TEXT"),
                         qr.RESTART_INTERRUPT_TEXT)
        # 负样本：普通失败 / 额度失败不许被当成收尸
        self.assertFalse(qr._run_reaped_by_restart({"error": "第 4 章起草失败"}))
        self.assertFalse(qr._run_reaped_by_restart({"error": ""}))
        self.assertFalse(qr._run_reaped_by_restart({}))

    def test_03_normal_restart_is_ok(self):
        state, details, anchors = _derive()
        r = qr.judge_restart_reaping(state, details, expected_ids=anchors)
        self.assertEqual(r["verdict"], "ok")
        self.assertEqual(r["judged_count"], 4)
        self.assertEqual(r["batch_size"], 4)
        self.assertEqual(r["batch_at"], FUTURE_BATCH_AT)
        self.assertTrue(qr.judge_retry_readiness(state)["retry_ready"])
        self.assertTrue(any("历史重启老账" in n for n in r["notes"]))

    def test_04_partial_reaping_is_blocked(self):
        state, details, anchors = _derive()
        details[anchors[0]].pop("interrupted")
        r = qr.judge_restart_reaping(state, details, expected_ids=anchors)
        self.assertEqual(r["verdict"], "blocked")
        self.assertIn("收尸只做了一半", r["blockers"][0])

    def test_05_old_ledger_cannot_impersonate_this_restart(self):
        """锚点批次比历史老账还旧 = 重启没生效，别判绿灯。"""
        state, details, anchors = _derive("2026-11-06 09:47:12")
        r = qr.judge_restart_reaping(state, details, expected_ids=anchors)
        self.assertEqual(r["verdict"], "blocked")
        self.assertTrue(any("更晚" in b or "老账" in b for b in r["blockers"]))

    def test_06_unreadable_detail_is_unknown_not_green(self):
        state, details, anchors = _derive()
        details.pop(anchors[0])
        r = qr.judge_restart_reaping(state, details, expected_ids=anchors)
        self.assertEqual(r["verdict"], "unknown")
        self.assertIn("不上绿标", r["blockers"][0])

    def test_07_missing_anchor_is_unknown(self):
        state, details, anchors = _derive()
        r = qr.judge_restart_reaping(state, details)
        self.assertEqual(r["verdict"], "unknown")
        self.assertIn("分不出本次收尸与历史老账", r["blockers"][0])

    def test_08_anchor_mismatch_is_blocked(self):
        state, details, anchors = _derive()
        r = qr.judge_restart_reaping(state, details, expected_ids=anchors[:3])
        self.assertEqual(r["verdict"], "blocked")
        self.assertIn("对不上", r["blockers"][0])

    def test_09_today_before_restart_never_green(self):
        fx = _fixture()
        r = qr.judge_restart_reaping(fx["state"], fx["run_details"],
                                     expected_ids=_anchor_ids(fx["state"]))
        self.assertEqual(r["verdict"], "blocked")

    def test_10_same_batch_other_task_is_legal_not_false_red(self):
        state, details, anchors = _derive()
        details["r-other-legal"] = {
            "status": "failed", "interrupted": "restart",
            "interrupted_at": FUTURE_BATCH_AT, "ended_at": FUTURE_BATCH_AT}
        r = qr.judge_restart_reaping(state, details, expected_ids=anchors)
        self.assertEqual(r["verdict"], "ok")
        self.assertTrue(any("属合法" in n for n in r["notes"]))

    def test_11_check_before_restart_has_zero_extra_get(self):
        """额度见底期 --check 不拉任何 /api/runs/ 详情（零额外 IO）。"""
        fx = _fixture()
        calls = []
        with mock.patch.object(qr, "api_get", _fake_get(fx["state"], fx["run_details"], calls)), \
             mock.patch.object(qr, "api_post", side_effect=AssertionError("不许写")), \
             redirect_stdout(io.StringIO()) as out:
            qr.cmd_check("http://x")
        self.assertEqual([p for p in calls if p.startswith("/api/runs/")], [])
        self.assertIn("不可 retry", out.getvalue())
        self.assertNotIn("重启收尸自洽性", out.getvalue())

    def test_12_check_after_restart_consumes_the_verdict(self):
        """重启后 --check 真拉详情、打印自洽性与真被判过条数、给绿灯。"""
        state, details, _ = _derive()
        calls = []
        with mock.patch.object(qr, "api_get", _fake_get(state, details, calls)), \
             mock.patch.object(qr, "api_post", side_effect=AssertionError("不许写")), \
             redirect_stdout(io.StringIO()) as out:
            qr.cmd_check("http://x")
        text = out.getvalue()
        self.assertTrue(any(p.startswith("/api/runs/") for p in calls))
        self.assertIn("重启收尸自洽性：ok", text)
        self.assertIn("真被判过 4 条锚点", text)
        self.assertIn("收尸自洽性 ok", text)
        self.assertIn("可 POST /api/tasks/<id>/retry", text)

    def test_13_bad_reaping_downgrades_conclusion(self):
        """收尸判据非 ok 时 --check 说「别开跑」，绝不假绿。"""
        state, details, anchors = _derive()
        details[anchors[0]].pop("interrupted")
        calls = []
        with mock.patch.object(qr, "api_get", _fake_get(state, details, calls)), \
             mock.patch.object(qr, "api_post", side_effect=AssertionError("不许写")), \
             redirect_stdout(io.StringIO()) as out:
            qr.cmd_check("http://x")
        text = out.getvalue()
        self.assertIn("重启收尸自洽性：blocked", text)
        self.assertIn("别开跑", text)
        self.assertNotIn("可 POST /api/tasks/<id>/retry", text)

    def test_14_source_anchors_no_dead_code(self):
        """锁接线：判据真被 --check 消费，且不采恒为 None 的假字段。"""
        src = SCRIPT.read_text(encoding="utf-8")
        self.assertIn("judge_restart_reaping(state, details, expected_ids=expected_ids)", src)
        self.assertIn("elif reap[\"verdict\"] != \"ok\":", src)
        self.assertNotIn('"interrupted": r.get("interrupted")', src,
                         "/api/state 摘要不带 interrupted，别再采恒 None 的字段")
        tree = ast.parse(src)
        fn = next(n for n in tree.body
                  if isinstance(n, ast.FunctionDef) and n.name == "judge_restart_reaping")
        self.assertEqual([a.arg for a in fn.args.args],
                         ["state", "details", "expected_ids"])


if __name__ == "__main__":
    unittest.main()
