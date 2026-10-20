"""Provisioned browser auth state is isolated and safe to import."""

import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from app.core.publish import manager


class _Page:
    _target_id = "target-1"

    def __init__(self, url="about:blank"):
        self.current_url = url
        self.cookies = []
        self.scripts = []
        self.evaluated = []
        self.removed = []

    def set_cookies(self, cookies):
        self.cookies.extend(cookies)

    def add_init_script(self, source):
        self.scripts.append(source)
        return "init-%d" % len(self.scripts)

    def remove_init_script(self, identifier):
        self.removed.append(identifier)

    def url(self):
        return self.current_url

    def evaluate(self, source):
        self.evaluated.append(source)


class PublishAuthStateTests(unittest.TestCase):
    def setUp(self):
        self._saved_state = manager._state
        manager._state = {"platforms": {}}
        with manager._AUTH_STATE_CACHE_LOCK:
            manager._AUTH_STATE_CACHE.clear()

    def tearDown(self):
        with manager._AUTH_STATE_CACHE_LOCK:
            manager._AUTH_STATE_CACHE.clear()
        manager._state = self._saved_state

    def test_cookie_export_filters_unrelated_hosts(self):
        state = {"cookies": [
            {"name": "session", "value": "ok", "domain": ".qimao.com",
             "sameSite": "lax", "expirationDate": 123.0},
            {"name": "leak", "value": "bad", "domain": ".example.com"},
        ]}
        cookies = manager._auth_cookie_items("qimao", state)
        self.assertEqual([c["name"] for c in cookies], ["session"])
        self.assertEqual(cookies[0]["sameSite"], "Lax")
        self.assertEqual(cookies[0]["expires"], 123.0)

    def test_hostless_cookie_defaults_to_platform_host(self):
        cookies = manager._auth_cookie_items("qimao", {
            "cookies": [{"name": "session", "value": "ok"}]})
        self.assertEqual(cookies[0]["domain"], "zuozhe.qimao.com")

    def test_storage_export_filters_unrelated_origins(self):
        state = {"origins": [
            {"origin": "https://zuozhe.qimao.com", "localStorage": [
                {"name": "sid", "value": "ok"}]},
            {"origin": "https://evil.example", "localStorage": [
                {"name": "secret", "value": "no"}]},
        ]}
        origins = manager._auth_storage_origins("qimao", state)
        self.assertEqual(origins, {
            "https://zuozhe.qimao.com": {"sid": "ok"}})

    def test_seed_auth_state_imports_cookie_and_init_storage(self):
        with tempfile.TemporaryDirectory() as td:
            path = Path(td) / "qimao.json"
            path.write_text(json.dumps({
                "cookies": [{"name": "session", "value": "opaque",
                             "domain": ".qimao.com", "path": "/"}],
                "origins": [{"origin": "https://zuozhe.qimao.com",
                              "localStorage": [{"name": "sid", "value": "1"}]}],
            }), encoding="utf-8")
            page = _Page()
            with mock.patch.dict(os.environ, {
                    manager.AUTH_STATE_FILE_ENV: str(path)}, clear=False):
                self.assertTrue(manager._seed_auth_state("qimao", "qimao", page))
            self.assertEqual([c["name"] for c in page.cookies], ["session"])
            self.assertEqual(len(page.scripts), 1)
            self.assertIn("localStorage.setItem", page.scripts[0])
            self.assertNotIn("localStorage.clear", page.scripts[0])

    def test_seed_auth_state_applies_storage_to_existing_first_party_page(self):
        with tempfile.TemporaryDirectory() as td:
            path = Path(td) / "qimao.json"
            path.write_text(json.dumps({
                "origins": [{"origin": "https://zuozhe.qimao.com",
                              "localStorage": [{"name": "sid", "value": "1"}]}],
            }), encoding="utf-8")
            page = _Page("https://zuozhe.qimao.com/front/book-manage")
            with mock.patch.dict(os.environ, {
                    manager.AUTH_STATE_FILE_ENV: str(path)}, clear=False):
                self.assertTrue(manager._seed_auth_state("qimao", "qimao", page))
            self.assertEqual(len(page.evaluated), 1)
            self.assertIn("localStorage.setItem", page.evaluated[0])

    def test_rotating_state_removes_old_init_script(self):
        with tempfile.TemporaryDirectory() as td:
            path = Path(td) / "qimao.json"
            with mock.patch.dict(os.environ, {
                    manager.AUTH_STATE_FILE_ENV: str(path)}, clear=False):
                page = _Page()
                for value in ("one", "two"):
                    path.write_text(json.dumps({"origins": [{
                        "origin": "https://zuozhe.qimao.com",
                        "localStorage": [{"name": "sid", "value": value}],
                    }]}), encoding="utf-8")
                    self.assertTrue(manager._seed_auth_state("qimao", "qimao", page))
                self.assertEqual(page.removed, ["init-1"])

    def test_storage_rotation_removes_only_previous_imported_keys(self):
        with tempfile.TemporaryDirectory() as td:
            path = Path(td) / "qimao.json"
            with mock.patch.dict(os.environ, {
                    manager.AUTH_STATE_FILE_ENV: str(path)}, clear=False):
                page = _Page("https://zuozhe.qimao.com")
                path.write_text(json.dumps({"origins": [{
                    "origin": "https://zuozhe.qimao.com",
                    "localStorage": [{"name": "sid", "value": "one"},
                                      {"name": "refresh", "value": "r1"}],
                }]}), encoding="utf-8")
                self.assertTrue(manager._seed_auth_state("qimao", "qimao", page))
                path.write_text(json.dumps({"origins": [{
                    "origin": "https://zuozhe.qimao.com",
                    "localStorage": [{"name": "sid", "value": "two"}],
                }]}), encoding="utf-8")
                self.assertTrue(manager._seed_auth_state("qimao", "qimao", page))
                self.assertIn("localStorage.removeItem", page.evaluated[-1])
                self.assertIn("refresh", page.evaluated[-1])
                self.assertNotIn("localStorage.clear", page.evaluated[-1])

    def test_rotation_after_service_cache_reset_uses_persisted_script_id(self):
        with tempfile.TemporaryDirectory() as td:
            path = Path(td) / "qimao.json"
            with mock.patch.dict(os.environ, {
                    manager.AUTH_STATE_FILE_ENV: str(path)}, clear=False), \
                    mock.patch.object(manager, "_set"), \
                    mock.patch.object(manager, "_st", return_value={}):
                page = _Page()
                path.write_text(json.dumps({"origins": [{
                    "origin": "https://zuozhe.qimao.com",
                    "localStorage": [{"name": "sid", "value": "one"}],
                }]}), encoding="utf-8")
                self.assertTrue(manager._seed_auth_state("qimao", "qimao", page))
                with manager._AUTH_STATE_CACHE_LOCK:
                    manager._AUTH_STATE_CACHE.clear()
                persisted = {"auth_script_id": "init-1",
                             "auth_script_target_id": "target-1"}
                with mock.patch.object(manager, "_st", return_value=persisted):
                    path.write_text(json.dumps({"origins": [{
                        "origin": "https://zuozhe.qimao.com",
                        "localStorage": [{"name": "sid", "value": "two"}],
                    }]}), encoding="utf-8")
                    self.assertTrue(manager._seed_auth_state("qimao", "qimao", page))
                self.assertEqual(page.removed, ["init-1"])

    def test_cached_state_backfills_legacy_fingerprint(self):
        with tempfile.TemporaryDirectory() as td:
            path = Path(td) / "qimao.json"
            with mock.patch.dict(os.environ, {
                    manager.AUTH_STATE_FILE_ENV: str(path)}, clear=False):
                page = _Page()
                path.write_text(json.dumps({"origins": [{
                    "origin": "https://zuozhe.qimao.com",
                    "localStorage": [{"name": "sid", "value": "one"}],
                }]}), encoding="utf-8")
                self.assertTrue(manager._seed_auth_state("qimao", "qimao", page))
                manager._state["platforms"]["qimao"].pop(
                    "auth_state_fingerprint", None)
                self.assertTrue(manager._seed_auth_state("qimao", "qimao", page))
                self.assertIn("auth_state_fingerprint",
                              manager._state["platforms"]["qimao"])

    def test_rotating_state_across_tabs_removes_previous_target_script(self):
        with tempfile.TemporaryDirectory() as td:
            path = Path(td) / "qimao.json"
            page_a = _Page("https://zuozhe.qimao.com")
            page_a._target_id = "target-a"
            page_b = _Page("https://zuozhe.qimao.com")
            page_b._target_id = "target-b"
            pages = {"target-a": page_a, "target-b": page_b}
            browser = mock.Mock()
            browser.page_for_target.side_effect = lambda target: pages.get(target)
            with mock.patch.dict(os.environ, {
                    manager.AUTH_STATE_FILE_ENV: str(path)}, clear=False), \
                    mock.patch.object(manager, "_browsers", {"qimao": browser}):
                path.write_text(json.dumps({"origins": [{
                    "origin": "https://zuozhe.qimao.com",
                    "localStorage": [{"name": "sid", "value": "one"}],
                }]}), encoding="utf-8")
                self.assertTrue(manager._seed_auth_state("qimao", "qimao", page_a))
                path.write_text(json.dumps({"origins": [{
                    "origin": "https://zuozhe.qimao.com",
                    "localStorage": [{"name": "sid", "value": "two"}],
                }]}), encoding="utf-8")
                self.assertTrue(manager._seed_auth_state("qimao", "qimao", page_b))
            self.assertEqual(page_a.removed, ["init-1"])
            self.assertEqual(len(page_b.scripts), 1)
            self.assertIn('"sid":"two"', page_b.scripts[0])

    def test_missing_state_is_a_noop(self):
        with tempfile.TemporaryDirectory() as td, mock.patch.dict(
                os.environ, {manager.AUTH_STATE_DIR_ENV: td}, clear=False):
            page = _Page()
            self.assertFalse(manager._seed_auth_state("fanqie", "fanqie", page))
            self.assertEqual(page.cookies, [])
            self.assertEqual(page.scripts, [])


if __name__ == "__main__":
    unittest.main()
