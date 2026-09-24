# -*- coding: utf-8 -*-
"""修订钉住与执行留痕测试（借鉴 WorkDSH ADR-0010 / ResolvedExecutionBinding /
取消结算分层的落地守卫）。

覆盖五件：
  1. flows.flow_digest：语义字段稳定指纹（展示位不制造漂移噪音）；
  2. create_task 钉 flow_snapshot + flows.flow_drift 漂移检测（新任务无漂移、
     改语义字段告漂移、改展示位不告、流程删除不告）；
  3. 步骤绑定速记：_binding_brief 三形态（call_chain / env 注入 / 空） +
     store.add_step 落 provider 列（默认空串，老数据兼容）；
  4. 停稳未知：_kill_tree 返回确认结果，run_process 杀树未确认时带
     quiescence_unknown 且不改变 timed_out 语义；确认清空时无该键。
  5. 参数修订 CAS：update_task_params 带 expected_rev 的过期写入被拒、
     匹配写入 rev+1、不带 expected_rev 旧调用零影响、坏类型拒绝。
"""
from __future__ import annotations

import sys
import unittest
from unittest import mock

from base import BaseTest


class TestFlowDigest(BaseTest):
    def test_digest_stable_and_semantic_sensitive(self):
        from app.core import flows
        base = {"id": "novel", "engine": "review", "threshold": 7.0,
                "rubric": ["情节", "人物"], "manuscript": "manuscript.md"}
        d1 = flows.flow_digest(base)
        self.assertEqual(len(d1), 16)
        # 字典序无关：同内容不同插入序同指纹
        self.assertEqual(d1, flows.flow_digest(dict(reversed(list(base.items())))))
        # 展示位（icon/note/name/goal_hint）不参与：改措辞零漂移
        display = dict(base, icon="✨", note="换了说明", name="新名字", goal_hint="新提示")
        self.assertEqual(d1, flows.flow_digest(display))
        # 语义字段一变即变：阈值/维度/提示词/serial
        self.assertNotEqual(d1, flows.flow_digest(dict(base, threshold=8.5)))
        self.assertNotEqual(d1, flows.flow_digest(dict(base, rubric=["节奏"])))
        self.assertNotEqual(d1, flows.flow_digest(dict(base, draft_prompt="换个写法")))
        self.assertNotEqual(d1, flows.flow_digest(dict(base, serial={"chapters": 4})))
        # 坏输入：非 dict 给空串（调用方按「无指纹」处理）
        self.assertEqual(flows.flow_digest(None), "")

    def test_create_task_pins_flow_snapshot(self):
        from app.core import flows, store
        task = store.create_task({"type": "novel", "goal": "写个故事",
                                  "workdir": str(self.workdir)})
        snap = task.get("flow_snapshot") or {}
        self.assertEqual(snap.get("id"), "novel")
        self.assertEqual(snap.get("engine"), "review")
        self.assertFalse(snap.get("edited"))
        self.assertEqual(len(snap.get("digest") or ""), 16)
        self.assertEqual(snap.get("digest"),
                         flows.flow_digest(flows.get_flow("novel")))

    def test_flow_drift_scenarios(self):
        from app.core import flows, store
        # 场景 0：老任务无快照 → None（不做任何推断）
        legacy = {"type": "novel"}
        self.assertIsNone(flows.flow_drift(legacy))
        task = store.create_task({"type": "novel", "goal": "写个故事",
                                  "workdir": str(self.workdir)})
        # 场景 1：刚创建无漂移
        self.assertIsNone(flows.flow_drift(task))
        # 场景 2：改语义字段（阈值）→ 告漂移，pinned/current 分明
        flows.upsert_flow({"id": "novel", "threshold": 9.0})
        drift = flows.flow_drift(task)
        self.assertIsNotNone(drift)
        self.assertEqual(drift["flow"], "novel")
        self.assertEqual(drift["pinned"], task["flow_snapshot"]["digest"])
        self.assertNotEqual(drift["pinned"], drift["current"])
        # 任务固化参数不受流程修改影响（钉住的阈值还在任务上）
        self.assertEqual(task["threshold"], 7.0)
        # 场景 3：改展示位（note/name）不制造漂移噪音
        flows.reset_flow("novel")
        fresh = store.create_task({"type": "doc", "goal": "写个文档",
                                   "workdir": str(self.workdir)})
        flows.upsert_flow({"id": "doc", "note": "完全换了一套说明文字",
                           "name": "文档V2"})
        self.assertIsNone(flows.flow_drift(fresh))
        # 场景 4：自定义流程被删除 → 不告漂移（任务自有固化参数可跑）
        flows.upsert_flow({"id": "customx", "name": "自定义", "engine": "review"})
        ct = store.create_task({"type": "customx", "goal": "干点活",
                                "workdir": str(self.workdir)})
        flows.delete_flow("customx")
        self.assertIsNone(flows.flow_drift(ct))


class TestBindingBrief(BaseTest):
    def test_call_chain_form(self):
        from app.core import pipeline
        agent = {"id": "codex-cli", "call_chain": [
            {"provider_id": "bigmodel",
             "provider": {"base_url": "https://api.example.com/v4"}},
            {"provider_id": "backup", "provider": {"base_url": "http://127.0.0.1:8787/"}},
        ]}
        brief = pipeline._binding_brief(agent)
        # scheme/路径被归一剥离，默认端口不出现，非默认端口保留
        self.assertIn("bigmodel@api.example.com", brief)
        self.assertIn("backup@127.0.0.1:8787", brief)
        self.assertNotIn(":443", brief)
        self.assertNotIn(":80/", brief)
        self.assertNotIn("https://", brief)

    def test_env_injection_form(self):
        from app.core import pipeline
        secret = "".join(("sk", "-test-", "fixture-", "zzz"))
        agent = {"id": "claude-code", "env": {
            "ANTHROPIC_BASE_URL": "https://gw.example.cn/api",
            "ANTHROPIC_AUTH_TOKEN": secret,
        }}
        brief = pipeline._binding_brief(agent)
        self.assertIn("ANTHROPIC_BASE_URL=", brief)
        self.assertIn("gw.example.cn", brief)
        # 只记 host 不记密钥：非 URL 的 env 值绝不进速记
        self.assertNotIn(secret, brief)

    def test_empty_and_fallback(self):
        from app.core import pipeline
        self.assertEqual(pipeline._binding_brief({}), "")
        # 坏形状条目不炸，能落到「?」占位
        self.assertEqual(pipeline._binding_brief({"call_chain": [{}]}), "?")

    def test_add_step_records_provider(self):
        from app.core import store
        run = store.create_run("orchestration", "t", task_id=None)
        step, _ = store.add_step(run["id"], "implement", "codex-cli", "Codex",
                                 model="glm-5.1", provider="bigmodel@api.example.com")
        self.assertEqual(step["provider"], "bigmodel@api.example.com")
        # 不传 provider 的老调用路径：字段在、值为空串（读取方 .get 兼容老数据）
        step2, _ = store.add_step(run["id"], "review", "mock-b", "Mock B")
        self.assertEqual(step2["provider"], "")


class TestQuiescenceUnknown(BaseTest):
    def _sleep_argv(self):
        return [sys.executable, "-c", "import time; time.sleep(20)"]

    def _fake_kill(self, confirmed):
        """mock 只改返回值，真杀照做——测试不留活进程（ResourceWarning 实训）。"""
        from app.core import runner
        real = runner._kill_tree

        def _wrapped(pid):
            real(pid)
            return confirmed
        return _wrapped

    def test_unconfirmed_kill_marks_quiescence(self):
        from app.core import runner
        with mock.patch.object(runner, "_kill_tree",
                               side_effect=self._fake_kill(False)):
            res = runner.run_process(self._sleep_argv(), timeout=0.6)
        self.assertTrue(res["timed_out"])
        self.assertFalse(res["ok"])
        # 杀树未确认：诊断标记在场，且不改变超时语义
        self.assertTrue(res.get("quiescence_unknown"))
        self.assertIn("未确认清空", res["stderr"])

    def test_confirmed_kill_has_no_marker(self):
        from app.core import runner
        with mock.patch.object(runner, "_kill_tree",
                               side_effect=self._fake_kill(True)):
            res = runner.run_process(self._sleep_argv(), timeout=0.6)
        self.assertTrue(res["timed_out"])
        # 确认清空：不落标记（键缺席 = 已确认或未杀）
        self.assertNotIn("quiescence_unknown", res)
        self.assertNotIn("未确认清空", res["stderr"])

    def test_normal_exit_never_marked(self):
        from app.core import runner
        res = runner.run_process([sys.executable, "-c", "print('ok')"], timeout=15)
        self.assertTrue(res["ok"])
        self.assertNotIn("quiescence_unknown", res)

    def test_step_summary_surfaces_marker(self):
        from app.core import pipeline, store
        run = store.create_run("orchestration", "t")
        step, _ = store.add_step(run["id"], "implement", "codex-cli", "Codex")
        res = {"ok": False, "text": "", "error": "超时 600s",
               "raw": {"exit_code": None, "timed_out": True,
                       "quiescence_unknown": True},
               "cost_usd": 0.0, "tokens": 0}
        pipeline._finish_step_result(run["id"], step, res, "implement",
                                     {"id": "codex-cli"}, 0.0)
        saved = store.get_run(run["id"])["steps"][0]
        self.assertEqual(saved["status"], "timeout")
        self.assertIn("进程树未确认清空", saved["summary"])


class TestParamsRevisionCas(BaseTest):
    def _make_task(self):
        from app.core import store
        return store.create_task({"type": "direct", "goal": "干活",
                                  "workdir": str(self.workdir)})

    def test_rev_starts_at_one_and_bumps_on_write(self):
        from app.core import store
        task = self._make_task()
        self.assertEqual(task["rev"], 1)
        ok, err = store.update_task_params(task["id"], {"mode": "fast"})
        self.assertTrue(ok, err)
        self.assertEqual(store.get_task(task["id"])["rev"], 2)
        self.assertEqual(store.get_task(task["id"])["mode"], "fast")

    def test_stale_expected_rev_rejected_without_overwrite(self):
        from app.core import store
        task = self._make_task()
        # 窗口 A、B 都看到 rev=1；A 先写成功（rev→2），B 带旧 rev 再写必须被拒
        ok, err = store.update_task_params(task["id"],
                                           {"mode": "fast", "expected_rev": 1})
        self.assertTrue(ok, err)
        ok, err = store.update_task_params(task["id"],
                                           {"mode": "expert", "expected_rev": 1})
        self.assertFalse(ok)
        self.assertIn("已被其他窗口修改", err)
        # 过期写入没有生效：mode 仍是 A 写的 fast，rev 不动
        cur = store.get_task(task["id"])
        self.assertEqual(cur["mode"], "fast")
        self.assertEqual(cur["rev"], 2)
        # 带正确 rev 的窗口 B 重试成功
        ok, err = store.update_task_params(task["id"],
                                           {"mode": "expert", "expected_rev": 2})
        self.assertTrue(ok, err)
        self.assertEqual(store.get_task(task["id"])["rev"], 3)

    def test_legacy_call_without_expected_rev_unchanged(self):
        from app.core import store
        task = self._make_task()
        # 旧调用（test_builtin_agent 的直连 CLI 切换路径等）不带 expected_rev：
        # 行为不变，照写照递增
        ok, err = store.update_task_params(task["id"], {"thinking": "high"})
        self.assertTrue(ok, err)
        self.assertEqual(store.get_task(task["id"])["thinking"], "high")

    def test_bad_expected_rev_rejected(self):
        from app.core import store
        task = self._make_task()
        ok, err = store.update_task_params(task["id"],
                                           {"mode": "fast", "expected_rev": "abc"})
        self.assertFalse(ok)
        self.assertIn("expected_rev", err)
        self.assertEqual(store.get_task(task["id"])["rev"], 1)

    def test_no_change_no_rev_bump(self):
        from app.core import store
        task = self._make_task()
        ok, err = store.update_task_params(task["id"], {"mode": task["mode"]})
        self.assertTrue(ok, err)
        self.assertEqual(store.get_task(task["id"])["rev"], 1)


if __name__ == "__main__":
    unittest.main()
