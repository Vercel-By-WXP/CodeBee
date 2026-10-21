import base64
import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from app.core import (assets, attachments, automation, backup, bench, catalog,
                      chaptersafe, cleanup, experts, goal_service, health,
                      hook_diagnostics, hooks, knowledge, knowledge_pipeline,
                      market, market_remote, paths, phone_relay, plugins, prefs,
                      presence, project_permissions, story_tracking,
                      task_templates, usage, wxdigest)


class DurableFileIntegrationTests(unittest.TestCase):
    def test_metadata_registries_use_shared_atomic_json_writer(self):
        old_data_dir = paths.DATA_DIR
        old_asset_file = project_permissions._FILE
        old_presence_file = presence._FILE
        old_template_file = task_templates._FILE
        old_automation_file = automation._FILE
        old_tasks = automation._TASKS
        try:
            with tempfile.TemporaryDirectory() as tmp:
                root = Path(tmp)
                paths.DATA_DIR = root
                project_permissions._FILE = root / "project-permissions.json"
                presence._FILE = root / "agent_presence.json"
                task_templates._FILE = root / "task-templates.json"
                automation._FILE = root / "automation.json"
                automation._TASKS = {"task-1": {"id": "task-1"}}
                with mock.patch.object(assets.atomic, "write_json",
                                       wraps=assets.atomic.write_json) as save_json, \
                     mock.patch.object(automation.store, "bump_state"):
                    assets._save({"assets": {}})
                    project_permissions._save({"project-1": {"local": "owner"}})
                    presence._save({"agent-1": {"status": "idle"}})
                    task_templates.save({"name": "custom", "goal": "goal"})
                    automation._save_locked()

                self.assertEqual(save_json.call_count, 5)
                self.assertTrue(all(call.kwargs["mode"] == 0o600
                                    for call in save_json.call_args_list))
                self.assertTrue((root / "assets.json").is_file())
                self.assertTrue((root / "project-permissions.json").is_file())
                self.assertTrue((root / "agent_presence.json").is_file())
                self.assertTrue((root / "task-templates.json").is_file())
                self.assertTrue((root / "automation.json").is_file())
        finally:
            paths.DATA_DIR = old_data_dir
            project_permissions._FILE = old_asset_file
            presence._FILE = old_presence_file
            task_templates._FILE = old_template_file
            automation._FILE = old_automation_file
            automation._TASKS = old_tasks

    def test_health_preferences_hooks_and_goals_use_atomic_json_writer(self):
        old_health_file = health._FILE
        old_health_providers = health._PROVIDERS
        old_hooks_file = hooks._FILE
        old_goal_file = goal_service._FILE
        old_goals = goal_service._GOALS
        old_data_dir = paths.DATA_DIR
        try:
            with tempfile.TemporaryDirectory() as tmp:
                root = Path(tmp)
                paths.DATA_DIR = root
                health.init(root, start_probe=False)
                hooks._FILE = root / "hooks.json"
                goal_service.init(root)
                health._PROVIDERS = {"cli": {"name": "cli"}}
                goal_service._GOALS = {}
                with mock.patch.object(health.atomic, "write_json",
                                       wraps=health.atomic.write_json) as save_json:
                    health._persist()
                    prefs.record({"type": "code"})
                    hooks.save_list([{"event": "run_end", "cmd": "echo ok"}])
                    goal_service._persist()
                    hook_diagnostics.record(
                        root, {"id": "hook-1", "event": "run_end"},
                        {"run_id": "run-1", "task_id": "task-1"},
                        status="succeeded")
                self.assertEqual(save_json.call_count, 5)
                self.assertTrue(all(call.kwargs["mode"] == 0o600
                                    for call in save_json.call_args_list))
                for name in ("provider_health.json", "last-prefs.json",
                             "hooks.json", "goals.json", "hook-diagnostics.json"):
                    self.assertTrue((root / name).is_file(), name)
        finally:
            health._FILE = old_health_file
            health._PROVIDERS = old_health_providers
            hooks._FILE = old_hooks_file
            goal_service._FILE = old_goal_file
            goal_service._GOALS = old_goals
            paths.DATA_DIR = old_data_dir

    def test_pending_attachment_uses_atomic_payload_and_metadata_writers(self):
        old_data_dir = paths.DATA_DIR
        try:
            with tempfile.TemporaryDirectory() as tmp:
                paths.DATA_DIR = Path(tmp)
                body = b"complete attachment bytes"
                with mock.patch.object(attachments.atomic, "write_bytes",
                                       wraps=attachments.atomic.write_bytes) as write_bytes, \
                     mock.patch.object(attachments.atomic, "write_json",
                                       wraps=attachments.atomic.write_json) as write_json:
                    item = attachments.save_pending("note.txt", base64.b64encode(body).decode())
                self.assertEqual((Path(tmp) / "pending" / item["id"]).read_bytes(), body)
                metadata = json.loads((Path(tmp) / "pending" /
                                       (item["id"] + ".json")).read_text(encoding="utf-8"))
                self.assertEqual(metadata["size"], len(body))
                self.assertEqual(write_bytes.call_count, 2)
                self.assertEqual(write_json.call_count, 1)
        finally:
            paths.DATA_DIR = old_data_dir

    def test_cross_device_attachment_fallback_uses_atomic_copy(self):
        old_data_dir = paths.DATA_DIR
        try:
            with tempfile.TemporaryDirectory() as tmp:
                root = Path(tmp)
                paths.DATA_DIR = root / "data"
                workdir = root / "work"
                workdir.mkdir()
                body = b"copied completely"
                item = attachments.save_pending("note.txt", base64.b64encode(body).decode())
                pending_file = paths.DATA_DIR / "pending" / item["id"]
                original_replace = Path.replace

                def cross_device_replace(path, target):
                    if path == pending_file:
                        raise OSError("simulated cross-device rename")
                    return original_replace(path, target)

                with mock.patch.object(Path, "replace", cross_device_replace), \
                     mock.patch.object(attachments.atomic, "write_bytes",
                                       wraps=attachments.atomic.write_bytes) as write_bytes:
                    committed = attachments.commit_to_workdir(workdir, [item["id"]])
                target = workdir / committed[0]["path"]
                self.assertEqual(target.read_bytes(), body)
                self.assertFalse(pending_file.exists())
                self.assertFalse((pending_file.parent / (item["id"] + ".json")).exists())
                self.assertEqual(write_bytes.call_count, 1)
        finally:
            paths.DATA_DIR = old_data_dir

    def test_failed_cross_device_copy_keeps_pending_attachment_and_metadata(self):
        old_data_dir = paths.DATA_DIR
        try:
            with tempfile.TemporaryDirectory() as tmp:
                root = Path(tmp)
                paths.DATA_DIR = root / "data"
                workdir = root / "work"
                workdir.mkdir()
                item = attachments.save_pending(
                    "note.txt", base64.b64encode(b"still pending").decode())
                pending = paths.DATA_DIR / "pending"
                payload_path = pending / item["id"]
                metadata_path = pending / (item["id"] + ".json")

                with mock.patch.object(Path, "replace",
                                       side_effect=OSError("simulated cross-device rename")), \
                     mock.patch.object(attachments.atomic, "write_bytes",
                                       side_effect=OSError("simulated copy failure")):
                    committed = attachments.commit_to_workdir(workdir, [item["id"]])
                self.assertEqual(committed, [])
                self.assertEqual(payload_path.read_bytes(), b"still pending")
                self.assertTrue(metadata_path.is_file())
        finally:
            paths.DATA_DIR = old_data_dir

    def test_pending_metadata_failure_removes_untracked_payload(self):
        old_data_dir = paths.DATA_DIR
        try:
            with tempfile.TemporaryDirectory() as tmp:
                paths.DATA_DIR = Path(tmp)
                with mock.patch.object(attachments.atomic, "write_json",
                                       side_effect=OSError("simulated metadata failure")):
                    with self.assertRaises(OSError):
                        attachments.save_pending(
                            "note.txt", base64.b64encode(b"orphan prevention").decode())
                pending = Path(tmp) / "pending"
                self.assertEqual(list(pending.iterdir()), [])
        finally:
            paths.DATA_DIR = old_data_dir

    def test_story_chapter_files_use_atomic_byte_writes(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            with mock.patch.object(story_tracking, "_render"), \
                 mock.patch.object(story_tracking.atomic, "write_bytes",
                                   wraps=story_tracking.atomic.write_bytes) as write_bytes:
                story_tracking.init(root, "task-1", "测试故事")
                story_tracking.commit_chapter(root, 1, "第一章大纲", "正文第一行\n正文第二行")
            chapter = root / ".codebee" / "chapters" / "0001"
            self.assertEqual((chapter / "outline.md").read_bytes(),
                             "第一章大纲\n".encode("utf-8"))
            self.assertEqual((chapter / "prose.md").read_bytes(),
                             "正文第一行\n正文第二行\n".encode("utf-8"))
            self.assertGreaterEqual(write_bytes.call_count, 2)

    def test_knowledge_state_uses_atomic_json_writer(self):
        old_file = knowledge._FILE
        try:
            with tempfile.TemporaryDirectory() as tmp:
                knowledge._FILE = Path(tmp) / "knowledge.json"
                with mock.patch.object(knowledge.atomic, "write_json",
                                       wraps=knowledge.atomic.write_json) as write_json:
                    knowledge._save({"entries": [{"id": "entry-1"}]})
                self.assertEqual(json.loads(knowledge._FILE.read_text(encoding="utf-8")),
                                 {"entries": [{"id": "entry-1"}]})
                self.assertEqual(write_json.call_count, 1)
        finally:
            knowledge._FILE = old_file

    def test_bench_account_health_uses_atomic_json_writer(self):
        old_file = bench._FILE
        old_accounts = bench._ACCOUNTS
        try:
            with tempfile.TemporaryDirectory() as tmp:
                bench._FILE = Path(tmp) / "account_health.json"
                bench._ACCOUNTS = {"cli": {"agent_id": "cli", "status": "down"}}
                with mock.patch.object(bench.atomic, "write_json",
                                       wraps=bench.atomic.write_json) as write_json:
                    bench._persist()
                self.assertEqual(write_json.call_count, 1)
                self.assertEqual(write_json.call_args.kwargs["mode"], 0o600)
                self.assertEqual(
                    json.loads(bench._FILE.read_text(encoding="utf-8")),
                    {"accounts": bench._ACCOUNTS})
        finally:
            bench._FILE = old_file
            bench._ACCOUNTS = old_accounts

    def test_cleanup_state_and_log_truncation_use_atomic_helpers(self):
        old_data_dir = paths.DATA_DIR
        old_keep = cleanup.LOG_KEEP_BYTES
        try:
            with tempfile.TemporaryDirectory() as tmp:
                paths.DATA_DIR = Path(tmp)
                with mock.patch.object(cleanup.atomic, "write_json",
                                       wraps=cleanup.atomic.write_json) as write_json:
                    cleanup._save_state({"last_run": "2026-10-21"})
                self.assertEqual(write_json.call_count, 1)
                self.assertEqual(write_json.call_args.kwargs["mode"], 0o600)
                self.assertEqual(
                    json.loads(cleanup._state_path().read_text(encoding="utf-8")),
                    {"last_run": "2026-10-21"})

                log = Path(tmp) / "service.log"
                log.write_bytes(b"x" * 100)
                cleanup.LOG_KEEP_BYTES = 10
                with mock.patch.object(cleanup.atomic, "write_bytes",
                                       wraps=cleanup.atomic.write_bytes) as write_bytes:
                    cleanup._delete_path(log, "truncate")
                self.assertEqual(write_bytes.call_count, 1)
                self.assertEqual(log.read_bytes(), b"x" * 10)
        finally:
            paths.DATA_DIR = old_data_dir
            cleanup.LOG_KEEP_BYTES = old_keep

    def test_knowledge_pipeline_report_uses_atomic_json_writer(self):
        old_file = knowledge_pipeline._REPORT_FILE
        try:
            with tempfile.TemporaryDirectory() as tmp:
                knowledge_pipeline._REPORT_FILE = Path(tmp) / "knowledge_pipeline.json"
                with mock.patch.object(knowledge_pipeline.retrieval, "upsert",
                                       return_value={"id": "chunk"}), \
                     mock.patch.object(knowledge_pipeline.atomic, "write_json",
                                       wraps=knowledge_pipeline.atomic.write_json) as write_json:
                    report = knowledge_pipeline.ingest("source.md", "hello world")
                self.assertEqual(write_json.call_count, 1)
                self.assertEqual(write_json.call_args.kwargs["mode"], 0o600)
                stored = json.loads(
                    knowledge_pipeline._REPORT_FILE.read_text(encoding="utf-8"))
                self.assertEqual(stored[report["ingest_id"]]["source"], "source.md")
        finally:
            knowledge_pipeline._REPORT_FILE = old_file

    def test_backup_path_remap_uses_atomic_json_writer(self):
        old_data_dir = paths.DATA_DIR
        try:
            with tempfile.TemporaryDirectory() as tmp:
                paths.DATA_DIR = Path(tmp)
                target = Path(tmp) / "run.json"
                target.write_text(json.dumps({"path": "E:/old/project/file.txt"}),
                                  encoding="utf-8")
                with mock.patch.object(backup.atomic, "write_json",
                                       wraps=backup.atomic.write_json) as write_json:
                    files, hits = backup._remap_all_json([("E:/old", "F:/new")])
                self.assertEqual((files, hits), (1, 1))
                self.assertEqual(write_json.call_count, 1)
                self.assertEqual(json.loads(target.read_text(encoding="utf-8")),
                                 {"path": "F:/new/project/file.txt"})
        finally:
            paths.DATA_DIR = old_data_dir

    def test_experts_registry_uses_atomic_json_writer(self):
        old_data_dir = paths.DATA_DIR
        try:
            with tempfile.TemporaryDirectory() as tmp:
                paths.DATA_DIR = Path(tmp)
                expert = {"id": "shared_demo", "name": "Demo", "version": "1.0.0",
                          "flow_id": "demo-flow", "prompt": "do work",
                          "skills": [], "knowledge_scope": [],
                          "deliverables": []}
                with mock.patch.object(experts.flows, "get_flow",
                                       return_value={"id": "demo-flow"}), \
                     mock.patch.object(experts.atomic, "write_json",
                                       wraps=experts.atomic.write_json) as write_json:
                    experts.import_expert(expert)
                self.assertEqual(write_json.call_count, 1)
                self.assertEqual(write_json.call_args.kwargs["mode"], 0o600)
                stored = json.loads((Path(tmp) / "experts.json").read_text(encoding="utf-8"))
                self.assertIn("shared_demo", stored)
        finally:
            paths.DATA_DIR = old_data_dir

    def test_market_and_plugin_registries_use_atomic_json_writer(self):
        old_data_dir = paths.DATA_DIR
        try:
            with tempfile.TemporaryDirectory() as tmp:
                paths.DATA_DIR = Path(tmp)
                with mock.patch.object(market.atomic, "write_json",
                                       wraps=market.atomic.write_json) as market_json:
                    market._save_registry({"installed": {"pack-1": {"version": "1"}}})
                with mock.patch.object(plugins.atomic, "write_json",
                                       wraps=plugins.atomic.write_json) as plugin_json:
                    plugins._save_registry({"installed": {"plugin-1": {"version": "1"}}})
                self.assertEqual(market_json.call_count, 1)
                self.assertEqual(market_json.call_args.kwargs["mode"], 0o600)
                self.assertEqual(plugin_json.call_count, 1)
                self.assertEqual(plugin_json.call_args.kwargs["mode"], 0o600)
                self.assertTrue((Path(tmp) / "market.json").is_file())
                self.assertTrue((Path(tmp) / "plugins.json").is_file())
        finally:
            paths.DATA_DIR = old_data_dir

    def test_budget_alert_state_uses_atomic_json_writer(self):
        old_data_dir = paths.DATA_DIR
        old_push = usage._ALERT_PUSH
        try:
            with tempfile.TemporaryDirectory() as tmp:
                paths.DATA_DIR = Path(tmp)
                usage.set_alert_push(lambda message: True)
                with mock.patch.object(usage, "cost_snapshot", return_value=(80.0, 80.0)), \
                     mock.patch("app.core.settings_schema.get", return_value=100.0), \
                     mock.patch("app.core.settings_schema.register_default_namespaces"), \
                     mock.patch.object(usage.atomic, "write_json",
                                       wraps=usage.atomic.write_json) as write_json:
                    usage._budget_alert_check("2026-10-21")
                self.assertEqual(write_json.call_count, 1)
                self.assertEqual(write_json.call_args.kwargs["mode"], 0o600)
                stored = json.loads((Path(tmp) / "budget_alerts.json").read_text(
                    encoding="utf-8"))
                self.assertEqual(stored["day"], "2026-10-21")
                self.assertEqual(stored["done"], ["d80", "m80"])
        finally:
            paths.DATA_DIR = old_data_dir
            usage.set_alert_push(old_push)

    def test_catalog_defaults_use_atomic_json_writer(self):
        old_file = paths.CATALOG_FILE
        old_cache = catalog._CACHE["entries"]
        try:
            with tempfile.TemporaryDirectory() as tmp:
                paths.CATALOG_FILE = Path(tmp) / "catalog.json"
                with mock.patch.object(catalog.atomic, "write_json",
                                       wraps=catalog.atomic.write_json) as write_json:
                    catalog.reset_to_default()
                self.assertEqual(write_json.call_count, 1)
                self.assertEqual(write_json.call_args.kwargs["mode"], 0o600)
                self.assertEqual(json.loads(paths.CATALOG_FILE.read_text(encoding="utf-8")),
                                 catalog.DEFAULT_CATALOG)
        finally:
            paths.CATALOG_FILE = old_file
            catalog._CACHE["entries"] = old_cache

    def test_wxdigest_state_uses_atomic_json_writer(self):
        old_file = wxdigest._FILE
        old_state = wxdigest._STATE
        try:
            with tempfile.TemporaryDirectory() as tmp:
                wxdigest._FILE = Path(tmp) / "wxdigest.json"
                wxdigest._STATE = {"config": {"groups": [{"username": "g"}]},
                                   "cursors": {"g": {"last_ts": 1}}, "unseen": 2,
                                   "groups_cache": ["g"], "last_scan": "2026-10-21"}
                with mock.patch.object(wxdigest.atomic, "write_json",
                                       wraps=wxdigest.atomic.write_json) as write_json:
                    wxdigest._save_locked()
                self.assertEqual(write_json.call_count, 1)
                self.assertEqual(write_json.call_args.kwargs["mode"], 0o600)
                stored = json.loads(wxdigest._FILE.read_text(encoding="utf-8"))
                self.assertEqual(stored["cursors"], {"g": {"last_ts": 1}})
                self.assertEqual(stored["unseen"], 2)
        finally:
            wxdigest._FILE = old_file
            wxdigest._STATE = old_state

    def test_phone_relay_credentials_use_atomic_json_writer(self):
        old_data_dir = paths.DATA_DIR
        try:
            with tempfile.TemporaryDirectory() as tmp:
                paths.DATA_DIR = Path(tmp)
                config = {"secret": "a" * 64, "enabled": False, "configured": True}
                with mock.patch.object(phone_relay.atomic, "write_json",
                                       wraps=phone_relay.atomic.write_json) as write_json:
                    phone_relay._save(config)
                self.assertEqual(write_json.call_count, 1)
                self.assertEqual(write_json.call_args.kwargs["mode"], 0o600)
                self.assertEqual(
                    json.loads((Path(tmp) / "phone-relay.json").read_text(encoding="utf-8")),
                    config)
        finally:
            paths.DATA_DIR = old_data_dir

    def test_chapter_writer_uses_atomic_text_writer(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            with mock.patch.object(chaptersafe.atomic, "write_text",
                                   wraps=chaptersafe.atomic.write_text) as write_text:
                target = chaptersafe.atomic_write_chapter(root, 1, "正文第一行")
            self.assertEqual(write_text.call_count, 1)
            self.assertEqual(target.read_text(encoding="utf-8"), "正文第一行")

    def test_market_remote_cache_uses_atomic_json_writer(self):
        old_data_dir = paths.DATA_DIR
        old_sources = market_remote.SOURCES
        try:
            with tempfile.TemporaryDirectory() as tmp:
                paths.DATA_DIR = Path(tmp)
                source = {"id": "fake", "name": "Fake", "kind": "generic",
                          "urls": ["https://example.invalid/catalog.json"]}
                market_remote.SOURCES = [source]
                payload = json.dumps({"name": "Fake", "description": "d",
                                      "plugins": [{"id": "p1"}]}).encode("utf-8")
                with mock.patch.object(market_remote, "_fetch", return_value=payload), \
                     mock.patch.object(market_remote.atomic, "write_json",
                                       wraps=market_remote.atomic.write_json) as write_json:
                    market_remote.refresh("fake")
                self.assertEqual(write_json.call_count, 1)
                self.assertEqual(write_json.call_args.kwargs["mode"], 0o600)
                cached = json.loads((Path(tmp) / "market_remote" / "fake.json").read_text(
                    encoding="utf-8"))
                self.assertEqual(cached["catalog"]["name"], "Fake")
                self.assertEqual(cached["url"], source["urls"][0])
        finally:
            paths.DATA_DIR = old_data_dir
            market_remote.SOURCES = old_sources

    def test_wxdigest_reader_group_file_uses_atomic_json_writer(self):
        old_dir = wxdigest._DIR
        old_script = wxdigest.READER_SCRIPT
        try:
            with tempfile.TemporaryDirectory() as tmp:
                root = Path(tmp)
                wxdigest._DIR = root / "wxdigest"
                wxdigest._DIR.mkdir()
                script = root / "wx_reader.py"
                script.write_text("", encoding="utf-8")
                wxdigest.READER_SCRIPT = script
                result = root / "wxdigest" / "reader_result.json"

                def fake_run(cmd, **kwargs):
                    result.write_text(json.dumps({"ok": True, "count": 0}),
                                      encoding="utf-8")

                with mock.patch("subprocess.run", side_effect=fake_run), \
                     mock.patch.object(wxdigest.atomic, "write_json",
                                       wraps=wxdigest.atomic.write_json) as write_json:
                    count = wxdigest._run_reader({
                        "reader_python": sys.executable,
                        "watch_dir": str(root / "inbox"),
                        "groups": [{"username": "wxid_1", "name": "群一"}],
                    })
                self.assertEqual(count, 0)
                self.assertEqual(write_json.call_count, 1)
                self.assertEqual(write_json.call_args.kwargs["mode"], 0o600)
                self.assertEqual(write_json.call_args.args[1],
                                 [{"username": "wxid_1", "name": "群一"}])
        finally:
            wxdigest._DIR = old_dir
            wxdigest.READER_SCRIPT = old_script


if __name__ == "__main__":
    unittest.main()
