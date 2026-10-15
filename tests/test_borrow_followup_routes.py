from unittest.mock import patch

from base import BaseTest


class BorrowFollowupRoutesTests(BaseTest):
    def handler(self, path, body=None):
        import main
        from app.core import store
        p = patch.object(main, "store", store)
        p.start()
        self.addCleanup(p.stop)
        h = main.Handler.__new__(main.Handler)
        h.path, h.headers = path, {}
        h._authed = lambda: True
        h._deny_control = lambda: None
        h._body = lambda: body or {}
        h._json = lambda code, obj: (code, obj)
        return h

    def test_config_route_uses_persisted_run_and_requires_auth(self):
        from app.core import store
        task = store.create_task({"type": "direct", "goal": "Talk", "thinking": "high",
                                  "workdir": str(self.workdir)})
        run = store.create_run("orchestration", "Talk", task_id=task["id"])
        h = self.handler("/api/runs/%s/config" % run["id"])
        status, body = h._route_get()
        self.assertEqual(status, 200)
        self.assertEqual(body["parameters"]["thinking"]["value"], "high")
        h._authed = lambda: False
        self.assertEqual(h._route_get()[0], 401)
        h._authed = lambda: True
        h.path = "/api/runs/missing/config"
        self.assertEqual(h._route_get()[0], 404)

    def test_diagnostic_route_and_hook_settings_include_same_records(self):
        from core import hooks
        expected = {"recent": [{"hook_id": "h1", "status": "timeout"}], "last_errors": []}
        with patch.object(hooks, "diagnostics", return_value=expected), \
             patch.object(hooks, "load_list", return_value=[]):
            h = self.handler("/api/hooks/diagnostics")
            self.assertEqual(h._route_get(), (200, expected))
            h.path = "/api/hooks"
            self.assertEqual(h._route_get()[1]["diagnostics"], expected)
            h._authed = lambda: False
            self.assertEqual(h._route_get()[0], 401)

    def test_plugin_update_route_and_control_gate(self):
        from core import plugins
        h = self.handler("/api/plugins/example/update")
        with patch.object(plugins, "upgrade", return_value=({"ok": True, "enabled": False}, None)) as upgrade:
            self.assertEqual(h._route_post(), (200, {"ok": True, "enabled": False}))
            upgrade.assert_called_once_with("example")
        with patch.object(plugins, "upgrade", return_value=(None, "incompatible")):
            self.assertEqual(h._route_post(), (400, {"error": "incompatible"}))
        h._deny_control = lambda: h._json(423, {"error": "held"})
        with patch.object(plugins, "upgrade") as upgrade:
            self.assertEqual(h._route_post()[0], 423)
            upgrade.assert_not_called()

    def test_openapi_exposes_new_endpoints(self):
        from app.core import openapi
        paths = openapi.document()["paths"]
        self.assertIn("get", paths["/api/runs/{run_id}/config"])
        self.assertIn("get", paths["/api/hooks/diagnostics"])
        self.assertIn("post", paths["/api/plugins/{plugin_id}/update"])
