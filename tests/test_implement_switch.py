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


class TestForbiddenSwitchUsesDifferentUpstream(BaseTest):
    """明确的 HTTP 403 上游拒绝应继续尝试异上游 CLI，不能换壳重撞同一网关。"""

    def runTest(self):
        from unittest.mock import patch
        from app.core import pipeline, router, store

        py = sys.executable or "python"
        denied_script = ("import sys;"
                         "sys.stderr.write('Reading prompt from stdin...\\n');"
                         "sys.stdout.write('403 {\"error\":{\"type\":\"forbidden\","
                         "\"message\":\"Request not allowed\"}}\\n');"
                         "sys.exit(1)")
        ok_script = ("import sys;"
                     "sys.stdin.read();"
                     "print('{\"pass\": true, \"scores\": {\"accuracy\": 9}, \"issues\": []}')")
        agents = [
            {"id": "forbidden-primary", "kind": "generic", "mode": "real",
             "command": py, "argv_template": ["-c", denied_script]},
            {"id": "same-upstream", "kind": "generic", "mode": "real",
             "command": py, "argv_template": ["-c", denied_script]},
            {"id": "different-upstream", "kind": "generic", "mode": "real",
             "command": py, "argv_template": ["-c", ok_script]},
        ]
        upstreams = {"forbidden-primary": {"gateway.example"},
                     "same-upstream": {"gateway.example"},
                     "different-upstream": {"backup.example"}}
        pipeline._agents = lambda: agents
        orig_plan = pipeline.planner.make_code_plan
        pipeline.planner.make_code_plan = (
            lambda task, agent, wd, ev, resume=None, log_path=None:
            {"source": "test", "steps": [{"title": "s1", "detail": "d1"}]})
        try:
            original_pick = router.pick
            original_switch_pick = router.pick_switch_candidate
            switch_choices = []

            def _pick_first_implement(pool, role, ttype, stats=None, exclude=()):
                if role == "implement" and not exclude:
                    return pool[0], "primary test candidate"
                return original_pick(pool, role, ttype, stats, exclude=exclude)

            def _trace_switch_pick(*args, **kwargs):
                choice = original_switch_pick(*args, **kwargs)
                switch_choices.append(choice[0].get("id") if choice[0] else None)
                return choice

            task = store.create_task({
                "type": "code", "title": "403 异上游换将回归", "goal": "做点事",
                "workdir": str(self.workdir),
            })
            run = store.create_run("orchestration", task["title"], task_id=task["id"])
            with patch.object(router, "pick", side_effect=_pick_first_implement), \
                    patch.object(router, "pick_switch_candidate", side_effect=_trace_switch_pick), \
                    patch.object(router, "agent_upstreams",
                                 side_effect=lambda aid, **_kw: upstreams.get(aid, set())):
                pipeline.execute_run(run["id"])
        finally:
            pipeline.planner.make_code_plan = orig_plan

        run = store.get_run(run["id"])
        self.assertEqual(run["status"], "done", run.get("error"))
        self.assertEqual(switch_choices, ["different-upstream"])
        impl_agents = [s["agent"] for s in run["steps"]
                       if s["role"].startswith("implement")]
        self.assertEqual(impl_agents,
                         ["forbidden-primary", "different-upstream"])
        self.assertNotIn("same-upstream", [s["agent"] for s in run["steps"]])


class TestEffectiveUpstreamDetection(BaseTest):
    """换将判重应覆盖自动推荐链和 Pi 自己 settings 中的默认 provider。"""

    def test_permission_denial_is_not_misclassified_as_bad_api_key(self):
        from app.core import runner

        denied = ('退出码 1；stderr/stdout: 403 '
                  '{"error":{"type":"forbidden","message":"Request not allowed"}}')
        self.assertTrue(runner._permission_error(denied))
        self.assertFalse(runner._auth_error(denied))
        denied_with_auth_wording = "HTTP 403: invalid API key for this model"
        self.assertTrue(runner._permission_error(denied_with_auth_wording))
        self.assertFalse(runner._auth_error(denied_with_auth_wording))
        self.assertTrue(runner._permission_error("HTTP 403 unknown response"))
        self.assertFalse(runner._permission_error("HTTP 401: invalid API key"))

    def test_auto_recommended_provider_is_counted(self):
        from unittest.mock import patch
        from app.core import modelhub, router

        modelhub._FILE = self.data_dir / "models.json"
        modelhub._save({"providers": [], "bindings": {}})
        recommended = {"call_chain": [{
            "provider_id": "auto", "provider": {"base_url": "https://shared.example/v1"},
        }]}
        with patch.object(modelhub, "recommend_binding", return_value=recommended):
            self.assertEqual(
                router.agent_upstreams("claude-code", task_type="code", role="implement"),
                {"shared.example"})

    def test_pi_local_default_provider_is_counted(self):
        import json
        from unittest.mock import patch
        from app.core import modelhub, router

        modelhub._FILE = self.data_dir / "models.json"
        modelhub._save({"providers": [], "bindings": {}})
        pi_settings = self.tmp / "pi-settings.json"
        pi_settings.write_text(json.dumps({
            "defaultProvider": "shared",
            "providers": {
                "shared": {"baseUrl": "https://shared.example/api", "apiKey": "fake"},
                "unused": {"baseUrl": "https://unused.example/api", "apiKey": "fake"},
            },
        }), encoding="utf-8")
        with patch.object(router, "_PI_SETTINGS_PATH", str(pi_settings)), \
                patch.object(modelhub, "recommend_binding", return_value=None):
            self.assertEqual(router.agent_upstreams("pi"), {"shared.example"})

    def test_claude_code_local_endpoint_is_counted(self):
        import json
        from unittest.mock import patch
        from app.core import modelhub, router

        modelhub._FILE = self.data_dir / "models.json"
        modelhub._save({"providers": [], "bindings": {}})
        claude_settings = self.tmp / "claude-settings.json"
        claude_settings.write_text(json.dumps({
            "env": {"ANTHROPIC_BASE_URL": "https://shared.example/v1"},
        }), encoding="utf-8")
        probes = (("claude-code", str(claude_settings),
                   r'"ANTHROPIC_BASE_URL"\s*:\s*"([^"]+)"'),)
        with patch.object(router, "_LOCAL_ENDPOINT_PROBES", probes), \
                patch.object(modelhub, "recommend_binding", return_value=None):
            self.assertEqual(router.agent_upstreams("claude-code"), {"shared.example"})

    def test_rejected_upstream_is_excluded_from_reviewer(self):
        from unittest.mock import patch
        from app.core import router

        impl = {"id": "impl", "kind": "codex", "mode": "real"}
        agents = [
            impl,
            {"id": "rejected", "kind": "claude", "mode": "real"},
            {"id": "available", "kind": "qwen", "mode": "real"},
        ]
        upstreams = {"rejected": {"denied.example"},
                     "available": {"backup.example"}}
        with patch.object(router, "score", side_effect=lambda agent, *_args, **_kw:
                          (90 if agent["id"] == "rejected" else 50, "test")), \
                patch.object(router, "agent_upstreams",
                             side_effect=lambda aid, **_kw: upstreams.get(aid, set())):
            reviewer, note = router.pick_reviewer(
                agents, impl, "code", {}, dead_upstreams=[{"denied.example"}])
        self.assertEqual(reviewer["id"], "available")
        self.assertTrue(note.startswith("跨厂商评审"))


class TestForbiddenModelChain(BaseTest):
    """模型链遇到 403 后跳过同一 host，仍可尝试不同 host。"""

    def runTest(self):
        from app.core import runner

        py = sys.executable or "python"
        script = ("import os,sys;"
                  "mode=os.environ.get('TUTTI_FORBIDDEN_TEST_MODE');"
                  "sys.exit((print('403 {\"error\":{\"type\":\"forbidden\","
                  "\"message\":\"Request not allowed\"}}'),1)[1]) "
                  "if mode != 'backup' else print('backup-ok')")
        agent = {
            "id": "forbidden-chain", "kind": "generic", "mode": "real",
            "command": py, "argv_template": ["-c", script],
            "call_chain": [
                {"model": "first", "provider": {"base_url": "https://same.example/v1"},
                 "env": {"TUTTI_FORBIDDEN_TEST_MODE": "first"}},
                {"model": "same-host", "provider": {"base_url": "https://same.example/api"},
                 "env": {"TUTTI_FORBIDDEN_TEST_MODE": "same"}},
                {"model": "backup", "provider": {"base_url": "https://other.example/v1"},
                 "env": {"TUTTI_FORBIDDEN_TEST_MODE": "backup"}},
            ],
        }
        result = runner.run_agent(agent, "hi", readonly=True, timeout=60)
        self.assertTrue(result["ok"], result.get("error"))
        self.assertEqual(result["model"], "backup")
        self.assertEqual([attempt["model"] for attempt in result["attempts"]],
                         ["first", "backup"])

    def test_forbidden_upstream_survives_later_model_failure(self):
        from app.core import pipeline, runner

        py = sys.executable or "python"
        script = ("import os,sys;"
                  "mode=os.environ.get('TUTTI_FORBIDDEN_TEST_MODE');"
                  "print('403 {\"error\":{\"type\":\"forbidden\","
                  "\"message\":\"Request not allowed\"}}' if mode == 'forbidden' "
                  "else 'HTTP 503 upstream error');"
                  "sys.exit(1)")
        agent = {
            "id": "forbidden-then-failure", "kind": "generic", "mode": "real",
            "command": py, "argv_template": ["-c", script],
            "call_chain": [
                {"model": "denied", "provider": {"base_url": "https://denied.example/v1"},
                 "env": {"TUTTI_FORBIDDEN_TEST_MODE": "forbidden"}},
                {"model": "broken-backup", "provider": {"base_url": "https://backup.example/v1"},
                 "env": {"TUTTI_FORBIDDEN_TEST_MODE": "backup"}},
            ],
        }
        result = runner.run_agent(agent, "hi", readonly=True, timeout=60)
        self.assertFalse(result["ok"])
        self.assertTrue(runner._transient_error(result.get("error")))
        self.assertEqual(result.get("forbidden_upstreams"), ["denied.example"])
        self.assertEqual(
            pipeline._forbidden_upstream_sets(result, {"fallback.example"}),
            [{"denied.example"}])


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
        router.agent_upstreams = lambda aid, **_kwargs: ups_map.get(aid, set())
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
            # 403 上游拒绝是硬阻断：候选池只剩同 host 时不要捡回重试。
            aid5, _ = router.pick_switch_candidate(
                [agents[0]], "implement", "code", {},
                blocked_upstreams=[{"bm.cn"}])
            self.assertIsNone(aid5)
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

        forbidden = self.workdir / "forbidden.log"
        forbidden.write_text("pi version 1.0.0\n"
                             "403 {\"error\":{\"type\":\"forbidden\","
                             "\"message\":\"Request not allowed\"}}\n",
                             encoding="utf-8")
        out4 = {"error": "退出码 1；stderr/stdout: pi version 1.0.0"}
        R._augment_error_from_log(out4, str(forbidden))
        self.assertTrue(R._permission_error(out4["error"]))


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
