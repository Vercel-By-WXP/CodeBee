# -*- coding: utf-8 -*-
"""工具动作验证（oc-ferry 探测协议借鉴）+ 对话式定时任务（AstrBot future_task 借鉴）回归。"""
from __future__ import annotations

import json
import tempfile
import time
import unittest
from pathlib import Path
from unittest import mock

from app.core import action_policy, builtin_agent, modelhub, router
from app.core.builtin_agent_tools import (
    SCHEDULE_TASK_SPEC, _tool_schedule_task,
)


def _openai_tool_payload(token):
    return {"choices": [{"message": {
        "role": "assistant", "content": None,
        "tool_calls": [{"id": "c1", "type": "function",
                        "function": {"name": "report_token",
                                     "arguments": json.dumps({"token": token})}}]}}]}


def _openai_text_payload(text="今天是晴天。"):
    return {"choices": [{"message": {"role": "assistant", "content": text}}]}


def _anthropic_tool_payload(token):
    return {"content": [{"type": "tool_use", "id": "t1", "name": "report_token",
                         "input": {"token": token}}]}


def _anthropic_text_payload(text="好的。"):
    return {"content": [{"type": "text", "text": text}]}


class ToolVerdictParseTests(unittest.TestCase):
    def test_openai_matching_token_is_tool(self):
        v, why = modelhub._tool_verdict_from_payload("openai", _openai_tool_payload("AB12"), "AB12")
        self.assertEqual((v, why), ("tool", ""))

    def test_openai_wrong_token_is_mismatch(self):
        v, why = modelhub._tool_verdict_from_payload("openai", _openai_tool_payload("XXXX"), "AB12")
        self.assertEqual(v, "mismatch")
        self.assertIn("令牌不符", why)

    def test_openai_text_only_is_chat_only(self):
        v, why = modelhub._tool_verdict_from_payload("openai", _openai_text_payload(), "AB12")
        self.assertEqual((v, why), ("chat_only", "只回文本未调用工具"))

    def test_anthropic_matching_and_text(self):
        v, _ = modelhub._tool_verdict_from_payload("anthropic", _anthropic_tool_payload("ZZ9"), "ZZ9")
        self.assertEqual(v, "tool")
        v, _ = modelhub._tool_verdict_from_payload("anthropic", _anthropic_text_payload(), "ZZ9")
        self.assertEqual(v, "chat_only")

    def test_empty_content_is_unknown(self):
        v, _ = modelhub._tool_verdict_from_payload("openai", {"choices": [{"message": {
            "role": "assistant", "content": None}}]}, "AB12")
        self.assertEqual(v, "")


def _prov_fixture(tmp: Path, **over):
    prov = {"id": "prov-t", "name": "T", "protocol": "openai", "enabled": True,
            "base_url": "https://gw.example/v1", "api_key": "k1",
            "models": [{"name": "m1", "enabled": True, "hidden": False}]}
    prov.update(over)
    data = {"providers": [prov], "bindings": {}}
    path = tmp / "models.json"
    path.write_text(json.dumps(data), encoding="utf-8")
    return path, prov


class ProbeToolUseTests(unittest.TestCase):
    def test_text_only_persists_chat_only(self):
        with tempfile.TemporaryDirectory() as tmp:
            path, prov = _prov_fixture(Path(tmp))
            with mock.patch.object(modelhub, "_FILE", path), \
                 mock.patch.object(modelhub, "_post_json_http",
                                   return_value=(200, _openai_text_payload(), "")):
                caps, note = modelhub.probe_tool_use("prov-t")
            self.assertEqual(caps.get("verdict"), "chat_only")
            self.assertEqual(note, "")
            stored = json.loads(path.read_text(encoding="utf-8"))
            self.assertEqual(stored["providers"][0]["tool_caps"]["verdict"], "chat_only")
            self.assertEqual(stored["providers"][0]["tool_caps"]["model"], "m1")

    def test_matching_tool_call_persists_tool(self):
        import re

        def _echo_token_post(url, headers, body, allow_private, timeout=20):
            content = body["messages"][0]["content"]
            m = re.search(r"逐字符为：([0-9A-F]+)", content)
            return 200, _openai_tool_payload(m.group(1) if m else "MISS"), ""

        with tempfile.TemporaryDirectory() as tmp:
            path, _ = _prov_fixture(Path(tmp))
            with mock.patch.object(modelhub, "_FILE", path), \
                 mock.patch.object(modelhub, "_post_json_http",
                                   side_effect=_echo_token_post):
                caps, _ = modelhub.probe_tool_use("prov-t")
            self.assertEqual(caps.get("verdict"), "tool")

    def test_double_mismatch_downgrades_to_chat_only(self):
        with tempfile.TemporaryDirectory() as tmp:
            path, _ = _prov_fixture(Path(tmp))
            # 恒回错误令牌：首次 mismatch + 换令牌重试仍 mismatch → 仅对话
            with mock.patch.object(modelhub, "_FILE", path), \
                 mock.patch.object(modelhub, "_post_json_http",
                                   return_value=(200, _openai_tool_payload("WRONG"), "")):
                caps, note = modelhub.probe_tool_use("prov-t")
            self.assertEqual(caps.get("verdict"), "chat_only")
            self.assertIn("两次", caps.get("note") or note or "")

    def test_transport_failure_keeps_old_verdict(self):
        with tempfile.TemporaryDirectory() as tmp:
            old = {"verdict": "tool", "checked_at": time.strftime("%Y-%m-%d %H:%M"),
                   "model": "m1"}
            path, _ = _prov_fixture(Path(tmp), tool_caps=old)
            with mock.patch.object(modelhub, "_FILE", path), \
                 mock.patch.object(modelhub, "_post_json_http",
                                   return_value=(0, None, "connection refused")):
                caps, note = modelhub.probe_tool_use("prov-t")
            self.assertEqual(caps, {})
            self.assertTrue(note)
            stored = json.loads(path.read_text(encoding="utf-8"))
            self.assertEqual(stored["providers"][0]["tool_caps"], old)

    def test_tool_verdict_ttl_and_model_match(self):
        with tempfile.TemporaryDirectory() as tmp:
            fresh = {"verdict": "tool", "checked_at": time.strftime("%Y-%m-%d %H:%M"),
                     "model": "m1"}
            stale = {"verdict": "tool",
                     "checked_at": time.strftime("%Y-%m-%d %H:%M",
                                                 time.localtime(time.time() - 9 * 86400)),
                     "model": "m1"}
            path, _ = _prov_fixture(Path(tmp), tool_caps=dict(fresh))
            with mock.patch.object(modelhub, "_FILE", path):
                self.assertEqual(modelhub.tool_verdict("prov-t", model="m1"), "tool")
                self.assertEqual(modelhub.tool_verdict("prov-t", model="m2"), "")
            path, _ = _prov_fixture(Path(tmp), tool_caps=dict(stale))
            with mock.patch.object(modelhub, "_FILE", path):
                self.assertEqual(modelhub.tool_verdict("prov-t", model="m1"), "")

    def test_chain_all_chat_only(self):
        with tempfile.TemporaryDirectory() as tmp:
            prov_a = {"id": "prov-a", "name": "A", "protocol": "openai", "enabled": True,
                      "base_url": "https://a.example/v1", "api_key": "k",
                      "models": [{"name": "ma", "enabled": True, "hidden": False}],
                      "tool_caps": {"verdict": "chat_only",
                                    "checked_at": time.strftime("%Y-%m-%d %H:%M"),
                                    "model": "ma"}}
            data = {"providers": [prov_a],
                    "bindings": {"opencode": {"chain": [{"provider_id": "prov-a",
                                                         "model": "ma"}]}}}
            path = Path(tmp) / "models.json"
            path.write_text(json.dumps(data), encoding="utf-8")
            with mock.patch.object(modelhub, "_FILE", path):
                flag, txt = modelhub.chain_tool_verdict("opencode")
            self.assertEqual(flag, "all_chat_only")
            self.assertIn("1", txt)


class RouterPenaltyTests(unittest.TestCase):
    def test_all_chat_only_deducts_ten(self):
        agent = {"id": "opencode", "kind": "opencode"}
        with mock.patch.object(modelhub, "chain_tool_verdict",
                               return_value=("unknown", "")):
            base_score, _ = router.score(agent, "implement", "code", stats={})
        with mock.patch.object(modelhub, "chain_tool_verdict",
                               return_value=("all_chat_only", "绑定链 1 个模型动作验证均为仅对话")):
            penalized, reason = router.score(agent, "implement", "code", stats={})
        self.assertAlmostEqual(penalized, base_score - 10.0, places=1)
        self.assertIn("动作验证", reason)

    def test_tool_verdict_no_penalty(self):
        agent = {"id": "opencode", "kind": "opencode"}
        with mock.patch.object(modelhub, "chain_tool_verdict",
                               return_value=("tool", "")):
            s, reason = router.score(agent, "implement", "code", stats={})
        self.assertNotIn("动作验证", reason)


class _FakeGateway:
    def __init__(self):
        self.tasks = {}
        self.seq = 0

    def create(self, payload):
        if not str(payload.get("run_at") or payload.get("time") or ""):
            if payload.get("kind") == "once":
                raise ValueError("once 需要未来时间")
        self.seq += 1
        t = dict(payload)
        t.update({"id": "auto-test-%d" % self.seq, "enabled": True,
                  "next_run": "2026-10-23 09:00"})
        self.tasks[t["id"]] = t
        return t

    def list_tasks(self):
        return [dict(v) for v in self.tasks.values()]

    def get_task(self, tid):
        t = self.tasks.get(tid)
        return dict(t) if t else None

    def set_enabled(self, tid, enabled):
        t = self.tasks.get(tid)
        if t is None:
            return None
        t["enabled"] = bool(enabled)
        return dict(t)

    def run_now(self, tid):
        t = self.tasks.get(tid)
        return (dict(t), "run-1") if t else (None, None)

    def delete(self, tid):
        return self.tasks.pop(tid, None) is not None


class ScheduleToolTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.workdir = self.tmp.name
        self.gw = _FakeGateway()

    def _sandbox(self):
        from app.core import policy
        return policy.normalize_sandbox({}, self.workdir)

    def test_no_gateway_is_unsupported(self):
        out = _tool_schedule_task(self.workdir, {"action": "list"}, schedule_gateway=None)
        self.assertIn("不支持管理定时任务", out)

    def test_create_once_and_get(self):
        out = _tool_schedule_task(self.workdir, {
            "action": "create", "name": "晨报", "prompt": "生成今日晨报",
            "kind": "once", "run_at": "2026-10-23 09:30"}, schedule_gateway=self.gw,
            sandbox=self._sandbox())
        self.assertIn("已登记", out)
        tid = [k for k in self.gw.tasks][0]
        self.assertEqual(self.gw.tasks[tid]["kind"], "once")
        out = _tool_schedule_task(self.workdir, {"action": "get", "id": tid},
                                  schedule_gateway=self.gw, sandbox=self._sandbox())
        self.assertIn("生成今日晨报", out)

    def test_create_validation_error_surfaces(self):
        out = _tool_schedule_task(self.workdir, {
            "action": "create", "name": "x", "prompt": "y",
            "kind": "once", "run_at": ""}, schedule_gateway=self.gw,
            sandbox=self._sandbox())
        self.assertIn("校验未通过", out)

    def test_create_outside_sandbox_denied(self):
        other = Path(self.workdir).parent / "elsewhere"
        out = _tool_schedule_task(self.workdir, {
            "action": "create", "name": "x", "prompt": "y", "kind": "daily",
            "time": "09:00", "workdir": str(other)}, schedule_gateway=self.gw,
            sandbox={"allowed_roots": [self.workdir]})
        self.assertIn("沙箱拒绝", out)
        self.assertEqual(self.gw.tasks, {})

    def test_enable_disable_delete_run_now(self):
        _tool_schedule_task(self.workdir, {
            "action": "create", "name": "n", "prompt": "p", "kind": "daily",
            "time": "09:00"}, schedule_gateway=self.gw, sandbox=self._sandbox())
        tid = [k for k in self.gw.tasks][0]
        out = _tool_schedule_task(self.workdir, {"action": "disable", "id": tid},
                                  schedule_gateway=self.gw, sandbox=self._sandbox())
        self.assertIn("已停用", out)
        out = _tool_schedule_task(self.workdir, {"action": "enable", "id": tid},
                                  schedule_gateway=self.gw, sandbox=self._sandbox())
        self.assertIn("已启用", out)
        out = _tool_schedule_task(self.workdir, {"action": "run_now", "id": tid},
                                  schedule_gateway=self.gw, sandbox=self._sandbox())
        self.assertIn("run-1", out)
        out = _tool_schedule_task(self.workdir, {"action": "delete", "id": tid},
                                  schedule_gateway=self.gw, sandbox=self._sandbox())
        self.assertIn("已删除", out)
        self.assertEqual(self.gw.tasks, {})

    def test_missing_id_and_bad_action(self):
        out = _tool_schedule_task(self.workdir, {"action": "delete"},
                                  schedule_gateway=self.gw, sandbox=self._sandbox())
        self.assertIn("需要任务 id", out)
        out = _tool_schedule_task(self.workdir, {"action": "purge"},
                                  schedule_gateway=self.gw, sandbox=self._sandbox())
        self.assertIn("create / list / get", out)

    def test_spec_registered_in_action_policy(self):
        spec = action_policy.spec_for("schedule_task")
        self.assertIsNotNone(spec)
        self.assertEqual(spec.zone, "yellow")
        decision = action_policy.preflight("schedule_task", args={"action": "list"},
                                           sandbox={"allowed_roots": [self.workdir]})
        self.assertEqual(decision["decision"], "autoExecute")


class ExtraSpecWiringTests(unittest.TestCase):
    def test_schedule_spec_reaches_request_body(self):
        _, _, body = builtin_agent._build_request(
            "openai", "https://gw.example/v1", "k", "m1", "sys", [], True,
            extra_specs=[SCHEDULE_TASK_SPEC])
        names = [t.get("function", {}).get("name")
                 for t in body.get("tools") or [] if isinstance(t, dict)]
        self.assertIn("schedule_task", names)


if __name__ == "__main__":
    unittest.main()
