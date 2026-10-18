# -*- coding: utf-8 -*-
"""隔离验证 dsh-our-free-model standalone 的内置供应商接线。"""
from __future__ import annotations

import json
import os
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer
from unittest import mock

from base import BaseTest


class TestOurFreeModelProvider(BaseTest):
    def _write_standalone_settings(self, key, port=None):
        home = self.tmp / "our-free-model"
        home.mkdir()
        settings = {"forwardKey": key}
        if port is not None:
            settings["standalonePort"] = port
        (home / "settings.json").write_text(json.dumps(settings), encoding="utf-8")
        self.old_home = os.environ.get("OFM_HOME")
        os.environ["OFM_HOME"] = str(home)

    def tearDown(self):
        if hasattr(self, "old_home"):
            if self.old_home is None:
                os.environ.pop("OFM_HOME", None)
            else:
                os.environ["OFM_HOME"] = self.old_home
        super().tearDown()

    def test_first_start_is_idempotent_and_does_not_persist_key(self):
        from app.core import modelhub

        self._write_standalone_settings("ofm-test-key-123")
        with mock.patch.object(modelhub, "_probe_our_free_model", return_value=(True, "")), \
                mock.patch.object(modelhub, "refresh_models", return_value=(0, "")), \
                mock.patch.object(modelhub.threading, "Thread"):
            first = modelhub.ensure_builtin_free_provider()
            second = modelhub.ensure_builtin_free_provider()

        self.assertTrue(first["created"])
        self.assertFalse(second["created"])
        providers = modelhub.providers()
        self.assertEqual(len([p for p in providers
                              if p.get("source") == modelhub.OUR_FREE_MODEL_SOURCE]), 1)
        provider = next(p for p in providers
                        if p.get("source") == modelhub.OUR_FREE_MODEL_SOURCE)
        self.assertEqual(provider["api_key"], "ofm-test-key-123")
        stored = json.loads((self.data_dir / "models.json").read_text(encoding="utf-8"))
        stored_provider = next(p for p in stored["providers"]
                               if p.get("source") == modelhub.OUR_FREE_MODEL_SOURCE)
        self.assertNotIn("api_key", stored_provider)
        self.assertNotIn("ofm-test-key-123", json.dumps(stored))

    def test_user_disable_is_preserved(self):
        from app.core import modelhub

        self._write_standalone_settings("ofm-test-key-456")
        with mock.patch.object(modelhub, "_probe_our_free_model", return_value=(True, "")) as probe, \
                mock.patch.object(modelhub, "refresh_models", return_value=(0, "")), \
                mock.patch.object(modelhub.threading, "Thread"):
            modelhub.ensure_builtin_free_provider()
            provider = next(p for p in modelhub.providers()
                            if p.get("source") == modelhub.OUR_FREE_MODEL_SOURCE)
            modelhub.providers_op([provider["id"]], "disable")
            modelhub.ensure_builtin_free_provider()

        provider = next(p for p in modelhub.providers()
                        if p.get("source") == modelhub.OUR_FREE_MODEL_SOURCE)
        self.assertFalse(provider["enabled"])
        self.assertEqual(provider["builtin_status"], "disabled")
        self.assertEqual(probe.call_count, 1)

    def test_missing_standalone_key_creates_clear_not_started_state(self):
        from app.core import modelhub

        with mock.patch.object(modelhub, "_probe_our_free_model") as probe:
            result = modelhub.ensure_builtin_free_provider()
        probe.assert_not_called()
        self.assertFalse(result["ready"])
        provider = next(p for p in modelhub.provider_view()
                        if p.get("source") == modelhub.OUR_FREE_MODEL_SOURCE)
        self.assertEqual(provider["builtin_status"], "not_started")
        self.assertIn("未启动", provider["builtin_status_message"])
        self.assertFalse(provider["enabled"])

    def test_port_is_loaded_from_standalone_settings(self):
        from app.core import modelhub

        self._write_standalone_settings("ofm-port-key", port=19001)
        with mock.patch.object(modelhub, "_probe_our_free_model", return_value=(False, "offline")):
            modelhub.ensure_builtin_free_provider()
        provider = next(p for p in modelhub.providers()
                        if p.get("source") == modelhub.OUR_FREE_MODEL_SOURCE)
        self.assertEqual(provider["base_url"], "http://127.0.0.1:19001/v1")

    def test_local_standalone_fixture_is_probed_and_models_are_refreshed(self):
        from app.core import modelhub

        seen = []

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *_args):
                pass

            def do_GET(self):
                seen.append((self.path, self.headers.get("Authorization")))
                if self.path == "/health":
                    payload = {"ok": True}
                elif self.path == "/v1/models":
                    payload = {"data": [{"id": "free-test-model"}]}
                else:
                    self.send_response(404)
                    self.end_headers()
                    return
                raw = json.dumps(payload).encode("utf-8")
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(raw)))
                self.end_headers()
                self.wfile.write(raw)

        server = HTTPServer(("127.0.0.1", 0), Handler)
        worker = threading.Thread(target=server.serve_forever, daemon=True)
        worker.start()
        try:
            self._write_standalone_settings("ofm-fixture-key", port=server.server_port)
            with mock.patch.object(modelhub, "refresh_models", return_value=(0, "")):
                result = modelhub.ensure_builtin_free_provider()
            self.assertTrue(result["ready"])
            provider = next(p for p in modelhub.providers()
                            if p.get("source") == modelhub.OUR_FREE_MODEL_SOURCE)
            with mock.patch.object(modelhub, "probe_wire_caps", return_value=({}, "")):
                count, error = modelhub.refresh_models(provider["id"])
            self.assertEqual((count, error), (1, ""))
            self.assertTrue(any(path == "/health" for path, _ in seen))
            self.assertIn(("/v1/models", "Bearer ofm-fixture-key"), seen)
            refreshed = next(p for p in modelhub.providers() if p["id"] == provider["id"])
            self.assertEqual(refreshed["models"][0]["name"], "free-test-model")
        finally:
            server.shutdown()
            worker.join(timeout=2)
            server.server_close()

    def test_external_builtin_endpoint_never_receives_discovered_key(self):
        from app.core import modelhub

        self._write_standalone_settings("ofm-secret")
        (self.data_dir / "models.json").write_text(json.dumps({
            "providers": [{"id": "prov-1", "name": "bad", "protocol": "openai",
                           "base_url": "https://attacker.example/v1",
                           "source": modelhub.OUR_FREE_MODEL_SOURCE}],
            "bindings": {},
        }), encoding="utf-8")
        provider = modelhub.providers()[0]
        self.assertEqual(provider.get("api_key"), "")

    def test_user_delete_is_not_recreated_on_next_start(self):
        from app.core import modelhub

        modelhub.ensure_builtin_free_provider()
        provider = next(p for p in modelhub.providers()
                        if p.get("source") == modelhub.OUR_FREE_MODEL_SOURCE)
        modelhub.providers_op([provider["id"]], "delete")
        result = modelhub.ensure_builtin_free_provider()
        self.assertFalse(result["created"])
        self.assertFalse([p for p in modelhub.providers()
                          if p.get("source") == modelhub.OUR_FREE_MODEL_SOURCE])

    def test_builtin_credentials_cannot_be_managed_as_regular_keys(self):
        from app.core import modelhub

        modelhub.ensure_builtin_free_provider()
        provider = next(p for p in modelhub.providers()
                        if p.get("source") == modelhub.OUR_FREE_MODEL_SOURCE)
        self.assertIn("standalone", modelhub.key_op(provider["id"], "add", key="other"))
        self.assertIn("不能复制", modelhub.providers_op([provider["id"]], "duplicate")[1])

    def test_builtin_process_status_does_not_expose_local_paths(self):
        from app.core import modelhub

        modelhub.ensure_builtin_free_provider()
        provider = next(p for p in modelhub.provider_view()
                        if p.get("source") == modelhub.OUR_FREE_MODEL_SOURCE)
        process = provider.get("builtin_process") or {}
        self.assertNotIn("root", process)
        self.assertNotIn("data_dir", process)
        self.assertNotIn("pid", process)
