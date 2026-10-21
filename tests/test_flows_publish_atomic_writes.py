import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from app.core import flows, modelhub, paths
from app.core.publish import ledger as publish_ledger


class FlowAndPublishAtomicWriteTests(unittest.TestCase):
    def test_flow_and_history_writers_use_atomic_json(self):
        old_file = flows._FILE
        old_data_dir = paths.DATA_DIR
        try:
            with tempfile.TemporaryDirectory() as tmp:
                root = Path(tmp)
                flows._FILE = root / "flows.json"
                paths.DATA_DIR = root
                history_path = root / "flows_history.json"
                with mock.patch.object(flows.atomic, "write_json",
                                       wraps=flows.atomic.write_json) as write_json:
                    flows._write({"flows": [{"id": "code"}]})
                    flows._history_write({"code": []})
                self.assertEqual(json.loads(flows._FILE.read_text(encoding="utf-8")),
                                 {"flows": [{"id": "code"}]})
                self.assertEqual(json.loads(history_path.read_text(encoding="utf-8")),
                                 {"code": []})
                self.assertEqual(write_json.call_count, 2)
        finally:
            flows._FILE = old_file
            paths.DATA_DIR = old_data_dir

    def test_orchestration_migration_uses_atomic_json(self):
        old_file = modelhub._FILE
        old_enabled_file = paths.ENABLED_FILE
        try:
            with tempfile.TemporaryDirectory() as tmp:
                root = Path(tmp)
                modelhub._FILE = root / "models.json"
                paths.ENABLED_FILE = root / "orchestration.json"
                modelhub._FILE.write_text(
                    json.dumps({"providers": [], "bindings": {}}), encoding="utf-8")
                paths.ENABLED_FILE.write_text(json.dumps({
                    "agent-a": {"enabled": True, "models": ["model-a"]},
                }), encoding="utf-8")
                with mock.patch.object(modelhub, "_backup_file"), \
                     mock.patch.object(modelhub.atomic, "write_json",
                                       wraps=modelhub.atomic.write_json) as write_json:
                    self.assertEqual(modelhub.migrate_orch_models(), 1)
                self.assertEqual(write_json.call_count, 2)
                self.assertEqual(json.loads(modelhub._FILE.read_text(encoding="utf-8"))[
                    "bindings"]["agent-a"]["models"], ["model-a"])
                self.assertEqual(json.loads(paths.ENABLED_FILE.read_text(encoding="utf-8")),
                                 {"agent-a": {"enabled": True}})
        finally:
            modelhub._FILE = old_file
            paths.ENABLED_FILE = old_enabled_file

    def test_successful_chat_cache_is_atomic_and_private(self):
        old_data_dir = paths.DATA_DIR
        response = {"choices": [{"message": {"content": "cached response"}}],
                    "usage": {"prompt_tokens": 1, "completion_tokens": 2,
                              "total_tokens": 3}}
        try:
            with tempfile.TemporaryDirectory() as tmp:
                paths.DATA_DIR = Path(tmp)
                with mock.patch.object(modelhub, "providers", return_value=[{
                        "id": "provider-a", "api_key": "secret",
                        "allow_private": False, "wire_api": ""}]), \
                     mock.patch.object(modelhub, "_ensure_wire_candidates",
                                       return_value=([("openai", "https://example.test/v1")], "")), \
                     mock.patch.object(modelhub, "_chain_keys",
                                       return_value=[{"key": "secret", "id": ""}]), \
                     mock.patch.object(modelhub, "_post_json_http",
                                       return_value=(200, response, "")) as post, \
                     mock.patch.object(modelhub, "note_key_ok"), \
                     mock.patch.object(modelhub.atomic, "write_json",
                                       wraps=modelhub.atomic.write_json) as write_json:
                    first = modelhub.chat("provider-a", "model-a", "prompt",
                                          cache_ttl=60)
                    self.assertTrue(first["ok"])
                    self.assertEqual(write_json.call_count, 1)
                    self.assertEqual(write_json.call_args.kwargs["mode"], 0o600)
                    cache_path = modelhub._chat_cache_path(
                        "provider-a", "model-a", "prompt", 2048)
                    self.assertEqual(json.loads(cache_path.read_text(encoding="utf-8")),
                                     first)
                    if os.name != "nt":
                        self.assertEqual(cache_path.stat().st_mode & 0o777, 0o600)
                    second = modelhub.chat("provider-a", "model-a", "prompt",
                                           cache_ttl=60)
                    self.assertEqual(second, first)
                    post.assert_called_once()
        finally:
            paths.DATA_DIR = old_data_dir

    def test_legacy_chat_cache_permissions_are_restricted_on_read(self):
        old_data_dir = paths.DATA_DIR
        try:
            with tempfile.TemporaryDirectory() as tmp:
                paths.DATA_DIR = Path(tmp)
                cache_path = modelhub._chat_cache_path(
                    "provider-a", "model-a", "legacy prompt", 2048)
                cache_path.parent.mkdir(parents=True)
                cached = {"ok": True, "text": "legacy response", "tokens": 1,
                          "usage": None, "error": ""}
                cache_path.write_text(json.dumps(cached), encoding="utf-8")
                cache_dir_key = os.path.normcase(os.path.abspath(
                    str(cache_path.parent)))
                modelhub._SECURED_CHAT_CACHE_DIRS.discard(cache_dir_key)
                if os.name != "nt":
                    os.chmod(str(cache_path.parent), 0o755)
                    os.chmod(str(cache_path), 0o644)

                result = modelhub.chat("provider-a", "model-a", "legacy prompt",
                                       cache_ttl=60)

                self.assertEqual(result, cached)
                if os.name != "nt":
                    self.assertEqual(cache_path.parent.stat().st_mode & 0o777, 0o700)
                    self.assertEqual(cache_path.stat().st_mode & 0o777, 0o600)
        finally:
            paths.DATA_DIR = old_data_dir

    @unittest.skipIf(os.name == "nt", "POSIX permission and symlink semantics")
    def test_chat_cache_directory_symlink_is_rejected_without_chmod_target(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            external = root / "external"
            external.mkdir()
            target = external / "keep.json"
            target.write_text("{}", encoding="utf-8")
            os.chmod(str(external), 0o755)
            os.chmod(str(target), 0o644)
            cache_dir = root / "data" / "chat_cache"
            cache_dir.parent.mkdir()
            try:
                cache_dir.symlink_to(external, target_is_directory=True)
            except OSError as exc:
                self.skipTest("symlink creation unavailable: %s" % exc)
            key = os.path.normcase(os.path.abspath(str(cache_dir)))
            modelhub._SECURED_CHAT_CACHE_DIRS.discard(key)

            self.assertFalse(modelhub._secure_chat_cache_dir(cache_dir))
            self.assertEqual(external.stat().st_mode & 0o777, 0o755)
            self.assertEqual(target.stat().st_mode & 0o777, 0o644)

    @unittest.skipIf(os.name == "nt", "POSIX permission and symlink semantics")
    def test_chat_cache_json_symlink_is_rejected_without_chmod_target(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            cache_dir = root / "chat_cache"
            cache_dir.mkdir()
            external = root / "external.json"
            external.write_text("{}", encoding="utf-8")
            os.chmod(str(cache_dir), 0o755)
            os.chmod(str(external), 0o644)
            try:
                (cache_dir / "linked.json").symlink_to(external)
            except OSError as exc:
                self.skipTest("symlink creation unavailable: %s" % exc)
            key = os.path.normcase(os.path.abspath(str(cache_dir)))
            modelhub._SECURED_CHAT_CACHE_DIRS.discard(key)

            self.assertFalse(modelhub._secure_chat_cache_dir(cache_dir))
            self.assertEqual(external.stat().st_mode & 0o777, 0o644)

    def test_publish_book_mutations_use_atomic_json(self):
        old_file = publish_ledger._BOOKS_FILE
        try:
            with tempfile.TemporaryDirectory() as tmp:
                publish_ledger._BOOKS_FILE = Path(tmp) / "books.json"
                with mock.patch.object(publish_ledger, "_chain_order",
                                       return_value=["task-1"]), \
                     mock.patch.object(publish_ledger.atomic,
                                       "write_json",
                                       wraps=publish_ledger.atomic.write_json) as write_json:
                    publish_ledger.save_book(
                        "task-1", "demo", {"book_id": "b-1", "title": "书名"})
                    publish_ledger.update_book(
                        "task-1", "demo", remote_id="r-1")
                    self.assertTrue(publish_ledger.remove_book(
                        "task-1", "demo"))
                self.assertEqual(write_json.call_count, 3)
                self.assertEqual(json.loads(
                    publish_ledger._BOOKS_FILE.read_text(encoding="utf-8")), {})
        finally:
            publish_ledger._BOOKS_FILE = old_file


if __name__ == "__main__":
    unittest.main()
