# -*- coding: utf-8 -*-
"""配额耗尽场景回归：codex 失败事件解析、链降级判定、路由绑定校准、实现步跨 CLI 换将。

2026-09-16 实测：配额烧干的 codex 连跑 5 次全失败收场——错误串只有 stderr 的
"Reading prompt from stdin..."（quota 判定失效）、turn.failed 被解析器丢弃、
绑定链为空仍按静态能力基线压过健康备用 CLI、实现步失败直接判死不换将。
本文件逐条封堵。所有 CLI 均为运行时构造的假子进程，无真实调用。
"""
from __future__ import annotations

import json
import sys

from base import BaseTest


class TestCodexFailMsg(BaseTest):
    def runTest(self):
        from app.core import runner as R
        # turn.failed 终态优先
        out = R._codex_fail_msg("\n".join([
            json.dumps({"type": "error", "message": "Reconnecting... 2/5 (rate limit exceeded: x)"}),
            json.dumps({"type": "error", "message": "Quota exceeded. Check your plan and billing details."}),
            json.dumps({"type": "turn.failed", "error": {"message": "Quota exceeded. Check your plan and billing details."}}),
        ]))
        self.assertIn("Quota exceeded", out)
        # 无终态时取最后一条非重试 error（Reconnecting 是 CLI 内部重试噪音）
        out2 = R._codex_fail_msg(json.dumps(
            {"type": "error", "message": "rate limit exceeded: 已达到 5 小时的使用上限"}))
        self.assertIn("rate limit exceeded", out2)
        # 纯重试噪音/空输入 → 空
        self.assertEqual(R._codex_fail_msg(json.dumps(
            {"type": "error", "message": "Reconnecting... 1/5 (x)"})), "")
        self.assertEqual(R._codex_fail_msg(""), "")


class TestCodexTurnFailedExit0(BaseTest):
    """退出码 0 但 turn.failed（配额耗尽的真实形态）→ 必须判失败。"""

    def runTest(self):
        import app.core.runner as R
        stdout = "\n".join([
            json.dumps({"type": "thread.started", "thread_id": "th-1"}),
            json.dumps({"type": "error", "message": "Quota exceeded. Check your plan and billing details."}),
            json.dumps({"type": "turn.failed", "error": {"message": "Quota exceeded. Check your plan and billing details."}}),
        ])
        orig = R.run_process
        R.run_process = lambda **kw: {"ok": True, "exit_code": 0, "stdout": stdout,
                                      "stderr": "", "duration": 0, "cancelled": False,
                                      "timed_out": False}
        try:
            agent = {"id": "codex-cli", "kind": "codex", "mode": "real", "command": "codex"}
            out = R.run_agent(agent, "hi", readonly=True, timeout=60)
        finally:
            R.run_process = orig
        self.assertFalse(out["ok"])
        self.assertIn("Quota exceeded", out["error"])


class TestQuotaVisibleInErrorEnablesFallback(BaseTest):
    """错误串必须带上 stdout：stderr 被 codex 的 stdin 提示占用时，
    quota 关键词只存在于 stdout——旧代码 tail=stderr 导致判定失效不降级。"""

    def runTest(self):
        import app.core.runner as R
        py = sys.executable or "python"
        script = ("import os,sys;"
                  "sys.stderr.write('Reading prompt from stdin...\\n');"
                  "sys.stdout.write('Quota exceeded. Check your plan and billing details.\\n');"
                  "sys.exit(1)"
                  " if os.environ.get('TUTTI_FAKE_FAIL')=='1' else"
                  " print('CHAIN2-OK '+os.environ.get('TUTTI_FAKE_MODEL',''))")
        agent = {
            "id": "fake-cli", "kind": "generic", "mode": "real", "command": py,
            "argv_template": ["-c", script],
            "call_chain": [
                {"model": "m1", "env": {"TUTTI_FAKE_FAIL": "1"}},
                {"model": "m2", "env": {"TUTTI_FAKE_FAIL": "0", "TUTTI_FAKE_MODEL": "two"}},
            ],
        }
        out = R.run_agent(agent, "hi", readonly=True, timeout=60)
        self.assertTrue(out["ok"], out.get("error"))
        self.assertEqual(out["model"], "m2")  # quota 可见 → 降级到第二条


class TestRouterBindingCalibration(BaseTest):
    """路由按绑定链可用性校准：链有备胎（≥2 条）+8，单条=单点 +0，
    解析为空（回落 CLI 本机默认）-25。2026-09-22 起单条链不再白拿 +8
    ——智谱余额清零实案里「绑定链可用」的全是单点，一家备胎都没有。"""

    def runTest(self):
        from app.core import modelhub, router
        pa = "prov-router-test"
        modelhub._save({"providers": [{
            "id": pa, "name": "t", "enabled": True, "api_key": "sk-x",
            "protocol": "openai", "base_url": "http://127.0.0.1:9/v1",
        }], "bindings": {
            "bind-ok": {"provider_id": pa, "model": "m1",
                        "chain": [{"provider_id": pa, "model": "m1"},
                                  {"provider_id": pa, "model": "m2"}],
                        "models": ["m1", "m2"]},
            "bind-single": {"provider_id": pa, "model": "m1",
                            "chain": [{"provider_id": pa, "model": "m1"}],
                            "models": ["m1"]},
        }})
        ok_agent = {"id": "bind-ok", "kind": "generic", "mode": "real"}
        single_agent = {"id": "bind-single", "kind": "generic", "mode": "real"}
        none_agent = {"id": "bind-none", "kind": "generic", "mode": "real"}
        s_ok, r_ok = router.score(ok_agent, "implement", "code")
        s_single, r_single = router.score(single_agent, "implement", "code")
        s_none, r_none = router.score(none_agent, "implement", "code")
        self.assertAlmostEqual(s_ok - s_none, 33.0)  # +8 与 -25 的差
        self.assertAlmostEqual(s_single - s_none, 25.0)  # 0 与 -25 的差
        self.assertIn("绑定链可用", r_ok)
        self.assertIn("绑定单点", r_single)
        self.assertIn("绑定链为空", r_none)


class TestImplementSwitchWalkOnQuota(BaseTest):
    """配额类失败是确定性秒死：换将后仍撞配额 → 继续走查候选名单，
    不能像 2026-09-22 智谱余额清零实案那样第二棒死了就让异上游候选全程坐冷板凳。"""

    def runTest(self):
        from app.core import pipeline, store
        py = sys.executable or "python"
        quota_fail = ("import sys;"
                      "sys.stderr.write('Reading prompt from stdin...\\n');"
                      "sys.stdout.write('Quota exceeded. Check your plan and billing details.\\n');"
                      "sys.exit(1)")
        ok_script = ("import sys;"
                     "sys.stdin.read();"
                     "print('{\"pass\": true, \"scores\": {\"accuracy\": 9}, \"issues\": []}')")
        agents = [
            {"id": "fake-w1", "kind": "generic", "mode": "real", "label": "欠费一",
             "env": {"TUTTI_TEST_SELFCONFIG": "1"},
             "command": py, "argv_template": ["-c", quota_fail]},
            {"id": "fake-w2", "kind": "generic", "mode": "real", "label": "欠费二",
             "env": {"TUTTI_TEST_SELFCONFIG": "1"},
             "command": py, "argv_template": ["-c", quota_fail]},
            {"id": "fake-w3", "kind": "generic", "mode": "real", "label": "健康",
             "env": {"TUTTI_TEST_SELFCONFIG": "1"},
             "command": py, "argv_template": ["-c", ok_script]},
        ]
        pipeline._agents = lambda: agents
        orig_plan = pipeline.planner.make_code_plan
        pipeline.planner.make_code_plan = (
            lambda task, agent, wd, ev, resume=None, log_path=None:
            {"source": "test", "steps": [{"title": "s1", "detail": "d1"}]})
        try:
            task = store.create_task({
                "type": "code", "title": "配额走查回归", "goal": "做点事",
                "workdir": str(self.workdir),
            })
            run = store.create_run("orchestration", task["title"], task_id=task["id"])
            pipeline.execute_run(run["id"])
        finally:
            pipeline.planner.make_code_plan = orig_plan
        run = store.get_run(run["id"])
        self.assertEqual(run["status"], "done", run.get("error"))
        impl_agents = [s["agent"] for s in run["steps"] if s["role"].startswith("implement")]
        self.assertEqual(impl_agents, ["fake-w1", "fake-w2", "fake-w3"])
        self.assertIn("fake-w2", run.get("route_note") or "")


class TestSwitchStopsOnNonQuota(BaseTest):
    """非配额死因（超时/崩溃）仍只换一次将：防着在坏候选上再烧一整个超时。"""

    def runTest(self):
        from app.core import pipeline, store
        py = sys.executable or "python"
        hard_fail = ("import sys;"
                     "sys.stderr.write('Reading prompt from stdin...\\n');"
                     "sys.stdout.write('segmentation fault core dumped\\n');"
                     "sys.exit(1)")
        ok_script = ("import sys;"
                     "sys.stdin.read();"
                     "print('{\"pass\": true, \"scores\": {\"accuracy\": 9}, \"issues\": []}')")
        agents = [
            {"id": "fake-n1", "kind": "generic", "mode": "real", "label": "崩一",
             "env": {"TUTTI_TEST_SELFCONFIG": "1"},
             "command": py, "argv_template": ["-c", hard_fail]},
            {"id": "fake-n2", "kind": "generic", "mode": "real", "label": "崩二",
             "env": {"TUTTI_TEST_SELFCONFIG": "1"},
             "command": py, "argv_template": ["-c", hard_fail]},
            {"id": "fake-n3", "kind": "generic", "mode": "real", "label": "健康",
             "env": {"TUTTI_TEST_SELFCONFIG": "1"},
             "command": py, "argv_template": ["-c", ok_script]},
        ]
        pipeline._agents = lambda: agents
        orig_plan = pipeline.planner.make_code_plan
        pipeline.planner.make_code_plan = (
            lambda task, agent, wd, ev, resume=None, log_path=None:
            {"source": "test", "steps": [{"title": "s1", "detail": "d1"}]})
        try:
            task = store.create_task({
                "type": "code", "title": "非配额只换一次回归", "goal": "做点事",
                "workdir": str(self.workdir),
            })
            run = store.create_run("orchestration", task["title"], task_id=task["id"])
            pipeline.execute_run(run["id"])
        finally:
            pipeline.planner.make_code_plan = orig_plan
        run = store.get_run(run["id"])
        self.assertEqual(run["status"], "failed")
        impl_agents = [s["agent"] for s in run["steps"] if s["role"].startswith("implement")]
        self.assertEqual(impl_agents, ["fake-n1", "fake-n2"])  # 不烧第三个候选
        self.assertIn("换将后仍失败", run.get("error") or "")


class TestPickSwitchUpstreamDeferral(BaseTest):
    """同上游让位：配额死亡的背景下，最高分候选与死者同上游且有异上游备选
    → 让位；异上游耗尽后同上游候选捡回；上游未知不参与剔除。"""

    def runTest(self):
        from app.core import router
        agents = [
            {"id": "u-big-1", "kind": "generic", "mode": "real", "_s": 60},
            {"id": "u-big-2", "kind": "generic", "mode": "real", "_s": 59},
            {"id": "u-alt", "kind": "generic", "mode": "real", "_s": 58},
        ]
        ups_map = {"u-big-1": {"bm.cn"}, "u-big-2": {"bm.cn"},
                   "u-alt": {"alt.io"}, "u-unk": set()}
        orig_score = router.score
        orig_ups = router.agent_upstreams
        router.score = lambda a, role, ttype, stats=None: (a["_s"], "s%s" % a["_s"])
        router.agent_upstreams = lambda aid: ups_map.get(aid, set())
        try:
            aid, reason = router.pick_switch_candidate(
                agents, "implement", "code", {},
                exclude={"x-done"}, dead_upstreams=[{"bm.cn"}])
            self.assertEqual(aid["id"], "u-alt", reason)
            self.assertIn("延后让位", reason)
            # 异上游耗尽：u-alt 已试过 → 同上游候选捡回（聊胜于无）
            aid2, _ = router.pick_switch_candidate(
                agents, "implement", "code", {},
                exclude={"x-done", "u-alt"}, dead_upstreams=[{"bm.cn"}])
            self.assertEqual(aid2["id"], "u-big-1")
            # 无配额死亡记录：不剔除，纯分数
            aid3, _ = router.pick_switch_candidate(
                agents, "implement", "code", {}, exclude={"x-done"})
            self.assertEqual(aid3["id"], "u-big-1")
            # 上游未知：不参与剔除
            aid4, _ = router.pick_switch_candidate(
                [{"id": "u-unk", "kind": "generic", "mode": "real", "_s": 70}],
                "implement", "code", {}, exclude=set(), dead_upstreams=[{"bm.cn"}])
            self.assertEqual(aid4["id"], "u-unk")
        finally:
            router.score = orig_score
            router.agent_upstreams = orig_ups


class TestAugmentErrorFromLog(BaseTest):
    """捕获输出只剩启动横幅时（kimi 撞 429 实案），从流式审计日志补错误行，
    让路由层看得见配额死因。"""

    def runTest(self):
        from app.core import runner as R
        log = self.workdir / "step.log"
        log.write_text("kimi version 2.0.2\n"
                       "error: failed to run prompt: provider.rate_limit: 429 "
                       "余额不足或无可用资源包\n", encoding="utf-8")
        out = {"error": "退出码 1；stderr/stdout: kimi version 2.0.2"}
        R._augment_error_from_log(out, str(log))
        self.assertIn("429", out["error"])
        self.assertIn("日志错误行", out["error"])
        self.assertTrue(R._quota_error(out["error"]))
        # 日志无强信号 / 文件不存在：原错误不动
        quiet = self.workdir / "quiet.log"
        quiet.write_text("kimi version 2.0.2\nall good\n", encoding="utf-8")
        out2 = {"error": "退出码 1；tail"}
        R._augment_error_from_log(out2, str(quiet))
        self.assertEqual(out2["error"], "退出码 1；tail")
        out3 = {"error": "退出码 1；tail"}
        R._augment_error_from_log(out3, str(self.workdir / "nope.log"))
        self.assertEqual(out3["error"], "退出码 1；tail")


class TestImplementSwitchOnFailure(BaseTest):
    """实现步失败 → 自动换另一条真实 CLI 重试（mock/手动模式不换）。"""

    def runTest(self):
        from app.core import pipeline, store
        py = sys.executable or "python"
        fail_script = ("import sys;"
                       "sys.stderr.write('Reading prompt from stdin...\\n');"
                       "sys.stdout.write('Quota exceeded. Check your plan and billing details.\\n');"
                       "sys.exit(1)")
        ok_script = ("import sys;"
                     "sys.stdin.read();"
                     "print('{\"pass\": true, \"scores\": {\"accuracy\": 9}, \"issues\": []}')")
        a1 = {"id": "fake-a1", "kind": "generic", "mode": "real", "label": "坏CLI",
              "env": {"TUTTI_TEST_SELFCONFIG": "1"},  # 自带配置：过死链闸门
              "command": py, "argv_template": ["-c", fail_script]}
        a2 = {"id": "fake-a2", "kind": "generic", "mode": "real", "label": "好CLI",
              "env": {"TUTTI_TEST_SELFCONFIG": "1"},  # 自带配置：过死链闸门
              "command": py, "argv_template": ["-c", ok_script]}
        pipeline._agents = lambda: [a1, a2]
        # 规划离线化：不调 CLI，静态计划
        orig_plan = pipeline.planner.make_code_plan
        pipeline.planner.make_code_plan = (
            lambda task, agent, wd, ev, resume=None, log_path=None:
            {"source": "test", "steps": [{"title": "s1", "detail": "d1"}]})
        try:
            task = store.create_task({
                "type": "code", "title": "换将回归", "goal": "做点事",
                "workdir": str(self.workdir),
            })
            run = store.create_run("orchestration", task["title"], task_id=task["id"])
            pipeline.execute_run(run["id"])
        finally:
            pipeline.planner.make_code_plan = orig_plan

        run = store.get_run(run["id"])
        self.assertEqual(run["status"], "done", run.get("error"))
        impl_agents = [s["agent"] for s in run["steps"] if s["role"].startswith("implement")]
        self.assertEqual(impl_agents, ["fake-a1", "fake-a2"])  # 失败后换将成功


if __name__ == "__main__":
    import unittest as _u
    _u.main()
