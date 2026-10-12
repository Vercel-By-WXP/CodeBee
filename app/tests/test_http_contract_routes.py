import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import main
from core import backend_profiles, command_auth, contracts, goal_service, review_requests, story_tracking


class HttpContractRouteTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.contract_dir = self.root / "contracts"
        self.patch_contracts = patch.object(contracts, "_DIR", self.contract_dir)
        self.patch_contracts.start()
        self.addCleanup(self.patch_contracts.stop)
        self.addCleanup(self.tmp.cleanup)

    def handler(self, path, body):
        h = object.__new__(main.Handler)
        h.path = path
        h._authed = lambda: True
        h._deny_control = lambda: None
        h._body = lambda: body
        h._client_name = lambda: "test"
        h._json = lambda code, payload: (code, payload)
        return h

    def test_unauthorized_get_and_post_are_rejected_before_dispatch(self):
        get = self.handler("/api/tasks/t1/contract", {})
        get._authed = lambda: False
        status, payload = get._route_get()
        self.assertEqual(status, 401)
        self.assertIn("令牌", payload["error"])

        post = self.handler("/api/tasks/t1/contract", {"op": "approve"})
        post._authed = lambda: False
        status, payload = post._route_post()
        self.assertEqual(status, 401)
        self.assertIn("令牌", payload["error"])

    def test_write_route_returns_423_when_another_device_holds_control(self):
        h = self.handler("/api/tasks", {"goal": "不会真正创建"})
        h._client_id = lambda: "client-a"
        h._client_name = lambda: "设备 A"
        with patch.object(main.remote, "acquire",
                          return_value=(False, {"mode": "held", "mine": False,
                                                "holder": "设备 B", "expires_in": 40})):
            h._deny_control = main.Handler._deny_control.__get__(h, main.Handler)
            status, payload = h._route_post()
        self.assertEqual(status, 423)
        self.assertEqual(payload["control"]["holder"], "设备 B")

    def test_contract_evidence_and_approval_http_shape(self):
        contracts.create("t1", {"acceptance_criteria": ["测试通过"], "approval_required": True})
        h = self.handler("/api/tasks/t1/contract", {
            "op": "evidence", "evidence": {"criterion": "测试通过", "status": "passed"}})
        status, payload = h._route_post()
        self.assertEqual(status, 200)
        self.assertEqual(payload["contract"]["evidence"][-1]["status"], "passed")

        h = self.handler("/api/tasks/t1/contract", {"op": "approve", "note": "人工复核"})
        status, payload = h._route_post()
        self.assertEqual(status, 200)
        self.assertEqual(payload["contract"]["approval"]["status"], "approved")

    def test_story_tracking_corruption_returns_client_error(self):
        workdir = self.root / "story"
        workdir.mkdir()
        codebee = workdir / ".codebee"
        codebee.mkdir()
        (codebee / "story_tracking.json").write_text("{broken", encoding="utf-8")
        h = self.handler("/api/tasks/t1/story-tracking", {"op": "init"})
        with patch.object(main.store, "get_task", return_value={
            "id": "t1", "title": "书", "goal": "目标", "workdir": str(workdir)}):
            status, payload = h._route_post()
        self.assertEqual(status, 400)
        self.assertIn("不可读", payload["error"])

    def test_retrieval_index_rejects_out_of_scope_root(self):
        allowed = self.root / "workspace"
        outside = self.root / "outside"
        allowed.mkdir(); outside.mkdir()
        h = self.handler("/api/retrieval/index", {"root": str(outside)})
        with patch.object(main.settings, "default_workdir", return_value=str(allowed)):
            status, payload = h._route_post()
        self.assertEqual(status, 400)
        self.assertIn("工作目录", payload["error"])

    def test_task_clarification_disables_execution_tools(self):
        h = self.handler("/api/tasks/clarify", {"goal": "短目标"})
        with patch("core.builtin_agent.resolve", return_value={"model": "test"}), \
             patch("core.builtin_agent.run", return_value={"text": "[]"}) as run:
            status, payload = h._api_task_clarify()

        self.assertEqual(status, 200)
        self.assertEqual(payload, {"questions": []})
        kwargs = run.call_args.kwargs
        self.assertFalse(kwargs["sandbox"]["network"])
        self.assertTrue({"run_command", "write_file", "edit_file", "append_file",
                         "fs_manage", "create_task", "read_tool_output"}.issubset(
                             set(kwargs["sandbox"]["disabled_tools"])))

    def test_auto_submit_schedule_requires_approval(self):
        task = {"id": "t1", "approval_required": True}
        h = self.handler("/api/publish/task/t1/auto-publish", {
            "platform": "fanqie", "enabled": True, "auto_submit": True, "time": "09:00"})
        with patch.object(main.store, "get_task", return_value=task), \
             patch.object(contracts, "get", return_value={"task_id": "t1", "approval_required": True}), \
             patch.object(contracts, "release_allowed", return_value=False):
            status, payload = h._route_post()
        self.assertEqual(status, 409)
        self.assertIn("审批", payload["error"])

    def test_manual_schedule_can_be_saved_without_approval(self):
        task = {"id": "t1", "approval_required": False}
        h = self.handler("/api/publish/task/t1/auto-publish", {
            "platform": "fanqie", "enabled": True, "auto_submit": False, "time": "09:00"})
        # Exercise the timer-configuration branch directly; the publish task
        # route also handles immediate create/chapter operations.
        h._route_post = main.Handler._route_post.__get__(h, main.Handler)
        with patch.object(main.store, "get_task", return_value=task), \
             patch.object(contracts, "get", return_value={"task_id": "t1", "approval_required": False}), \
             patch.object(contracts, "release_allowed", return_value=False), \
             patch.object(main.store, "set_auto_publish", return_value=True), \
             patch("core.publish.auto.norm_auto_publish", return_value=({
                 "enabled": True, "platform": "fanqie", "auto_submit": False, "time": "09:00"}, "")):
            status, payload = h._route_post()
        self.assertEqual(status, 200)
        self.assertFalse(payload["auto_publish"]["auto_submit"])

    def test_backend_profile_and_command_auth_routes(self):
        backend_profiles.init(self.root / "profile-data")
        h = self.handler("/api/backend-profiles", {
            "label": "ACP", "command": "acp-agent", "args": [], "env": {},
            "workspace_path": "",
        })
        status, payload = h._route_post()
        self.assertEqual(status, 200)
        self.assertEqual(payload["profile"]["kind"] if "kind" in payload["profile"] else "ACP", "ACP")

        command_auth.init(self.root / "auth-data")
        h = self.handler("/api/command-auth", {"mode": "restricted"})
        status, payload = h._route_post()
        self.assertEqual(status, 200)
        self.assertEqual(payload["mode"], "restricted")
        h = self.handler("/api/command-auth", {})
        status, payload = h._route_get()
        self.assertEqual(status, 200)
        self.assertEqual(payload["mode"], "restricted")

    def test_external_review_routes_have_stable_http_shape(self):
        review_file = self.root / "reviews.jsonl"
        with patch.object(review_requests, "_FILE", review_file):
            h = self.handler("/api/reviews", {
                "run_id": "run-http", "summary": "Review changed files",
                "paths": "ui/app.js", "channel": "#frontend"})
            status, payload = h._route_post()
            self.assertEqual(status, 200)
            review = payload["review"]
            self.assertEqual(review["paths"], ["ui/app.js"])

            listing = self.handler("/api/reviews", {})
            status, payload = listing._route_get()
            self.assertEqual(status, 200)
            self.assertEqual(payload["reviews"][0]["id"], review["id"])

            transition = self.handler("/api/reviews/" + review["id"] + "/transition",
                                      {"action": "assign", "reviewer": "alice"})
            status, payload = transition._route_post()
            self.assertEqual(status, 200)
            self.assertEqual(payload["review"]["status"], "assigned")

            invalid = self.handler("/api/reviews", {"summary": "missing run"})
            status, payload = invalid._route_post()
            self.assertEqual(status, 400)
            self.assertIn("run_id", payload["error"])

    def test_goal_routes_create_read_and_transition_with_revision_cas(self):
        goal_service.init(self.root / "goal-data")
        h = self.handler("/api/goals", {"title": "跨轮目标", "description": "逐步完成"})
        status, payload = h._route_post()
        self.assertEqual(status, 200)
        goal = payload["goal"]
        self.assertEqual(goal["phase"], "active")

        status, payload = self.handler("/api/goals", {})._route_get()
        self.assertEqual(status, 200)
        self.assertEqual(payload["current"]["goal_id"], goal["goal_id"])

        h = self.handler("/api/goals/" + goal["goal_id"], {
            "revision": goal["revision"], "phase": "paused"})
        status, payload = h._route_post()
        self.assertEqual(status, 200)
        self.assertEqual(payload["goal"]["phase"], "paused")

        h = self.handler("/api/goals/" + goal["goal_id"], {
            "revision": goal["revision"], "phase": "active"})
        status, payload = h._route_post()
        self.assertEqual(status, 409)

        h = self.handler("/api/goals/" + goal["goal_id"], {
            "revision": payload["current"]["revision"], "phase": "active"})
        status, payload = h._route_post()
        self.assertEqual(status, 200)

        h = self.handler("/api/goals/" + goal["goal_id"] + "/round", {
            "revision": payload["goal"]["revision"]})
        status, payload = h._route_post()
        self.assertEqual(status, 200)
        self.assertEqual(payload["goal"]["rounds_started"], 1)

    def test_goal_route_rejects_invalid_create_and_missing_revision(self):
        goal_service.init(self.root / "goal-data")
        status, payload = self.handler("/api/goals", {"title": " "})._route_post()
        self.assertEqual(status, 400)
        self.assertIn("标题", payload["error"])
        g = goal_service.create("已有")
        status, payload = self.handler("/api/goals/" + g["goal_id"], {
            "phase": "paused"})._route_post()
        self.assertEqual(status, 400)
        self.assertIn("revision", payload["error"])


class GoalUiContractTests(unittest.TestCase):

    def test_overview_has_goal_surface_and_controls(self):
        root = Path(__file__).resolve().parents[1]
        index = (root / "ui" / "index.html").read_text(encoding="utf-8")
        app = (root / "ui" / "app.js").read_text(encoding="utf-8")
        style = (root / "ui" / "style.css").read_text(encoding="utf-8")
        self.assertIn('id="btn-rail-overview"', index)
        self.assertIn("data-rail-page=\"overview\"", index)
        for element_id in ("ov-goal", "ov-goal-create", "ov-goal-title", "ov-goal-description",
                           "ov-goal-phase", "ov-goal-progress", "ov-goal-actions"):
            self.assertIn('id="%s"' % element_id, index)
        self.assertIn("/api/goals", app)
        self.assertIn("goalRender", app)
        self.assertIn('["active", "paused"].includes(data.goal.phase) ? data.goal : null', app)
        self.assertIn('uiConfirm(t("确定将这个长目标标记为完成吗？")', app)
        self.assertIn(".ov-goal-card", style)


if __name__ == "__main__":
    unittest.main()
