import unittest
from unittest.mock import patch

import main


class ProjectMobileRouteTests(unittest.TestCase):
    def handler(self, path, body=None):
        h = object.__new__(main.Handler)
        h.path = path
        h.headers = {}
        h._authed = lambda: True
        h._body = lambda: body or {}
        h._client_id = lambda: "local"
        h._client_name = lambda: "本机"
        h._json = lambda code, payload: (code, payload)
        return h

    def test_project_mobile_and_cloud_get_contracts(self):
        with patch.object(main.project_space, "snapshot", return_value=[{"id": "p"}]), \
             patch.object(main.store, "list_runs", return_value=[]), \
             patch.object(main.experts, "list_experts", return_value=[{"id": "research"}]), \
             patch.object(main.cloud_readiness, "snapshot", return_value={"status": "hold"}):
            status, payload = self.handler("/api/projects")._route_get()
            self.assertEqual((status, payload), (200, {"projects": [{"id": "p"}]}))
            status, payload = self.handler("/api/mobile/experts")._route_get()
            self.assertEqual(payload["experts"][0]["id"], "research")
            status, payload = self.handler("/api/mobile/status")._route_get()
            self.assertIn("runs", payload)
            status, payload = self.handler("/api/cloud/readiness")._route_get()
            self.assertEqual(payload["status"], "hold")

    def test_share_import_preview_and_mobile_launch(self):
        with patch("core.sharing.import_package", return_value={"kind": "expert", "applied": False}) as imp:
            status, payload = self.handler("/api/sharing/import", {"code": "abc"})._route_post()
            self.assertEqual(status, 200)
            self.assertFalse(payload["applied"])
            imp.assert_called_once_with("abc", apply=False)
        expert = {"id": "research", "flow_id": "research"}
        with patch.object(main.experts, "get_expert", return_value=expert), \
             patch.object(main.Handler, "_create_and_start", return_value=(200, {"run_id": "r"})) as start:
            status, payload = self.handler("/api/mobile/experts/research/launch",
                                           {"goal": "分析市场"})._route_post()
            self.assertEqual(status, 200)
            self.assertEqual(payload["expert"], "research")
            start.assert_called_once()
            self.assertEqual(start.call_args.args[0]["expert_id"], "research")

    def test_permission_route_rejects_non_owner(self):
        with patch.object(main.project_space, "get", return_value={"id": "p"}), \
             patch.object(main.project_permissions, "can", return_value=False):
            status, payload = self.handler("/api/projects/p/permissions",
                                           {"member": "alice", "role": "viewer"})._route_post()
            self.assertEqual(status, 403)

    def test_openapi_exposes_all_five_layers(self):
        from core import openapi
        paths = openapi.document()["paths"]
        for path in ("/api/experts", "/api/projects", "/api/mobile/status",
                     "/api/sharing/import", "/api/projects/{project_id}/permissions",
                     "/api/cloud/readiness"):
            self.assertIn(path, paths)


if __name__ == "__main__":
    unittest.main()
