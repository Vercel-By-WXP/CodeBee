# -*- coding: utf-8 -*-
"""真实运行时能力解析回归：不是只测目录展示，而是检查最终注入块和证据。"""
from __future__ import annotations

import threading
from unittest import mock

from base import BaseTest


class CapabilityRuntimeTests(BaseTest):
    def test_retry_excerpt_is_not_expanded_by_final_injector(self):
        from app.core import capability_runtime, pipeline

        block = "stable capability evidence\n" * 600
        reduced, _ = pipeline._serial_shrunk_block(block, "", "")
        prompt = "Write this chapter.\n\n" + reduced
        with mock.patch.object(capability_runtime, "prepare", return_value={"block": block}):
            actual, meta = capability_runtime.inject_prompt(prompt, task={"type": "novel"})
            unrelated, unrelated_meta = capability_runtime.inject_prompt(
                "（经验库已因上下文容量限制精简）", task={"type": "novel"})
        self.assertEqual(actual, prompt)
        self.assertLess(len(actual), len(block))
        self.assertFalse(meta["injected"])
        self.assertTrue(meta["capabilities_present"])
        self.assertTrue(unrelated_meta["injected"])
        self.assertIn(block, unrelated)

    def test_direct_cli_prompt_receives_capabilities_and_run_evidence(self):
        """The external CLI path must receive the same block as builtin direct."""
        from app.core import knowledge, pipeline, skills, store

        skills.upsert_lesson("direct", "直连证据规则", "回答前先核对用户给出的事实。")
        knowledge.upsert_entry("direct", "直连知识事实", "该任务的已确认事实。", status="approved")
        task = store.create_task({
            "type": "direct", "title": "CLI 能力注入", "goal": "核对已确认事实",
            "workdir": str(self.workdir), "direct_agent": "mock-a",
        })
        run = store.create_run("orchestration", task["title"], task_id=task["id"])
        store.update_run(run["id"], status="running")
        seen = {}

        def fake_step(_run_id, _role, _agent, prompt, *_args, **_kwargs):
            seen["prompt"] = prompt
            return {"ok": True, "text": "完成", "sid": "", "error": "", "raw": {}}

        pipeline._agents = self.mock_agents
        with mock.patch.object(pipeline, "_run_step", side_effect=fake_step):
            pipeline.execute_run(run["id"])

        self.assertIn("直连证据规则", seen["prompt"])
        self.assertIn("直连知识事实", seen["prompt"])
        saved = store.get_run(run["id"])
        self.assertEqual(saved["status"], "done", saved.get("error"))
        self.assertTrue(saved["capabilities"]["skills"])
        self.assertTrue(saved["capabilities"]["knowledge"])

    def test_serial_capabilities_include_platform_rules(self):
        from app.core import capability_runtime

        block = capability_runtime.block_for({
            "type": "serial_novel", "title": "七猫番茄连载签约", "goal": "按七猫和番茄规则检查开篇",
        }, "serial-capabilities")
        self.assertIn("七猫签约标准与写作规范", block)
        self.assertIn("番茄小说写作与流量守则", block)

    def test_codebee_orchestrator_planning_prompt_receives_capabilities(self):
        """Planner API calls must consume the same run snapshot as writers."""
        from app.core import knowledge, planner, skills

        skills.upsert_lesson("code", "计划证据规则", "先依据仓库事实拆解任务。")
        knowledge.upsert_entry("code", "计划知识事实", "该计划约束已确认。", status="approved")
        task = {"id": "planner-run", "type": "code", "goal": "按计划知识事实修改代码",
                "context": "计划证据规则", "workdir": str(self.workdir)}
        seen = {}

        def fake_chat(_provider, _model, prompt, **_kwargs):
            seen["prompt"] = prompt
            return {"ok": True, "text": "```json {\"difficulty\":\"easy\",\"subtasks\":[{\"title\":\"实现\",\"detail\":\"完成\"}]} ```",
                    "usage": {}, "raw": {}}

        with mock.patch.object(planner, "_orchestrator", return_value=(
                {"id": "prov", "name": "测试"}, "model")), \
             mock.patch.object(planner.modelhub, "chat", side_effect=fake_chat):
            result = planner._orch_code_plan(task, {"id": "prov", "name": "测试"},
                                             "model", run_id="planner-run")
        self.assertTrue(result["steps"])
        self.assertIn("计划证据规则", seen["prompt"])
        self.assertIn("计划知识事实", seen["prompt"])

    def test_mcp_evidence_is_updated_without_changing_prompt_snapshot(self):
        from app.core import capability_runtime

        task = {"type": "direct", "title": "MCP 证据", "goal": "检查工具"}
        before = capability_runtime.prepare(task, "mcp-run")
        capability_runtime.note_mcp("mcp-run", discovered=3,
                                    called=["mcp__browser__open", "mcp__browser__open"])
        after = capability_runtime.evidence(task, "mcp-run")
        self.assertEqual(before["block"], capability_runtime.prepare(task, "mcp-run")["block"])
        self.assertEqual(after["mcp"], {
            "discovered": 3, "called": ["mcp__browser__open"],
        })

    def test_prepare_contains_lessons_knowledge_plugin_and_auditable_ids(self):
        from app.core import capability_runtime, knowledge, plugins, skills

        skills.upsert_lesson("novel", "七猫签约开篇证据", "前三章必须用具体冲突验证卖点。")
        knowledge.upsert_entry("novel", "平台规则事实", "平台规则以当前官方后台为准。", status="approved")
        with mock.patch.object(plugins, "view", return_value={"plugins": [{
                "id": "demo-plugin", "name": "demo-plugin", "display_name": "签约检查插件",
                "enabled": True, "available": True, "default_prompt": ["逐条核对签约证据。"],
                "skill_pack_ids": [],
            }]}):
            task = {"id": "t1", "type": "novel", "title": "七猫签约开篇", "goal": "检查七猫签约证据"}
            block = capability_runtime.block_for(task, "run-1")
            evidence = capability_runtime.evidence(task, "run-1")
        self.assertIn("经验库", block)
        self.assertIn("知识库", block)
        self.assertIn("插件默认提示", block)
        self.assertIn("demo-plugin", evidence["plugins"])
        self.assertTrue(evidence["skills"])
        self.assertTrue(evidence["knowledge"])

    def test_snapshot_is_stable_for_followup_turns(self):
        from app.core import capability_runtime, skills

        skills.upsert_lesson("direct", "对话续轮", "先读取上一轮产出再继续。")
        task = {"id": "t2", "type": "direct", "goal": "继续处理文件"}
        first = capability_runtime.prepare(task, "run-2")
        skills.upsert_lesson("direct", "后来新增", "不应改变本轮已解析的能力块。")
        second = capability_runtime.prepare(task, "run-2")
        self.assertEqual(first["block"], second["block"])

    def test_unauthorized_plugin_is_not_prompt_input_and_defaults_are_bounded(self):
        from app.core import capability_runtime, plugins

        huge = ["x" * 900 for _ in range(8)]
        rows = [{
            "id": "blocked", "name": "blocked", "display_name": "Blocked",
            "enabled": True, "available": True,
            "authorization": {"allowed": False, "missing": ["network"]},
            "default_prompt": ["DO NOT INJECT"], "skill_pack_ids": [],
        }, {
            "id": "large", "name": "large", "display_name": "Large",
            "enabled": True, "available": True,
            "authorization": {"allowed": True},
            "default_prompt": huge, "skill_pack_ids": [],
        }, {
            "id": "large-2", "name": "large-2", "display_name": "Large 2",
            "enabled": True, "available": True,
            "authorization": {"allowed": True},
            "default_prompt": huge, "skill_pack_ids": [],
        }]
        task = {"type": "direct", "title": "large", "goal": "large large-2"}
        with mock.patch.object(plugins, "view", return_value={"plugins": rows}):
            block = capability_runtime._plugin_block(task, [])
        self.assertNotIn("DO NOT INJECT", block)
        self.assertLessEqual(len(block), capability_runtime._PLUGIN_PROMPT_BUDGET)

    def test_external_skill_budget_includes_headers(self):
        from app.core import capability_runtime, plugins

        rows = [{
            "id": "codex-skill:%d" % i, "name": "skill-%d" % i,
            "display_name": "skill-%d" % i, "description": "large",
            "source_type": "codex", "enabled": True, "available": True,
            "prompt": "x" * 8000,
        } for i in range(4)]
        with mock.patch.object(plugins, "capabilities_view", return_value={"skills": rows}):
            block, ids = capability_runtime._external_skill_block({
                "type": "direct", "goal": "skill-0 skill-1 skill-2 skill-3"})
        self.assertTrue(ids)
        self.assertLessEqual(len(block), capability_runtime._EXTERNAL_SKILL_BUDGET)

    def test_public_inject_prompt_is_idempotent_for_external_cli(self):
        from app.core import capability_runtime, knowledge, skills

        skills.upsert_lesson("direct", "桥接经验", "外部 CLI 也必须读取这条经验。")
        knowledge.upsert_entry("direct", "桥接知识", "外部 CLI 的确认事实。", status="approved")
        task = {"type": "direct", "title": "桥接", "goal": "读取桥接经验"}
        first, meta = capability_runtime.inject_prompt("请处理任务", task, "bridge-run")
        second, meta2 = capability_runtime.inject_prompt(first, task, "bridge-run")
        self.assertTrue(meta["injected"])
        self.assertFalse(meta2["injected"])
        self.assertEqual(first, second)
        self.assertIn("桥接经验", first)
        self.assertIn("桥接知识", first)

    def test_prompt_evidence_records_final_cli_capability_input(self):
        from app.core import capability_runtime, skills, store

        skills.upsert_lesson("direct", "注入审计规则", "执行前核对任务输入和能力证据。")
        task = store.create_task({"type": "direct", "title": "注入审计",
                                  "goal": "验证 CLI 收到能力", "workdir": str(self.workdir)})
        run = store.create_run("orchestration", task["title"], task_id=task["id"])
        prompt, meta = capability_runtime.inject_prompt("请执行", task, run["id"])
        self.assertTrue(meta["capabilities_present"])
        self.assertIn("本次运行可用能力", prompt)
        capability_runtime.note_prompt(run["id"], agent="claude-code",
                                       capabilities_present=meta["capabilities_present"])
        evidence = capability_runtime.evidence(task, run["id"])
        self.assertEqual(evidence["runtime"], {
            "prompt_calls": 1, "capability_prompts": 1, "agents": ["claude-code"]})
        self.assertEqual(store.get_run(run["id"])["capabilities"]["runtime"], evidence["runtime"])

    def test_evidence_prefers_persisted_historical_snapshot_after_restart(self):
        from app.core import capability_runtime, store

        historical = {
            "scope": "direct", "skills": ["old-skill"], "knowledge": True,
            "plugins": ["old-plugin"], "external_skills": [],
            "mcp": {"discovered": 2, "called": ["mcp__old__read"]},
        }
        with mock.patch.object(store, "get_run", return_value={"capabilities": historical}), \
                mock.patch.object(capability_runtime, "prepare",
                                  side_effect=AssertionError("must not rebuild history")):
            self.assertEqual(capability_runtime.evidence({}, "restarted-run"), historical)
