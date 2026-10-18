# -*- coding: utf-8 -*-
"""隔离验证 Our Free Model standalone 的 CodeBee 生命周期托管。"""
from __future__ import annotations

import json
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest import mock


class _FakeProcess:
    def __init__(self, pid=4242, poll_value=None, wait_value=None):
        self.pid = pid
        self._poll = poll_value
        self.wait_value = wait_value
        self.terminated = 0
        self.killed = 0

    def poll(self):
        return self._poll

    def terminate(self):
        self.terminated += 1
        self._poll = 0

    def kill(self):
        self.killed += 1
        self._poll = -9

    def wait(self, timeout=None):
        if isinstance(self.wait_value, BaseException):
            raise self.wait_value
        return self.wait_value


class TestOurFreeModelService(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix="ofm-manager-")
        self.tmp_path = Path(self.tmp.name)
        self.root = self.tmp_path / "dsh-our-free-model"
        (self.root / "packages" / "standalone").mkdir(parents=True)
        (self.root / "packages" / "standalone" / "cli.mjs").write_text("", encoding="utf-8")
        (self.root / "package.json").write_text(json.dumps({"engines": {"node": ">=22"}}),
                                                    encoding="utf-8")
        self.data_dir = self.tmp_path / "ofm-data"

    def tearDown(self):
        self.tmp.cleanup()

    def _manager(self, **kwargs):
        from app.core.our_free_model_service import OurFreeModelServiceManager

        kwargs.setdefault("roots", [self.root])
        return OurFreeModelServiceManager(
            data_dir=self.data_dir, node="node", ready_timeout=0, **kwargs,
        )

    def test_missing_installation_does_not_block_codebee(self):
        manager = self._manager(roots=[])
        result = manager.start()
        self.assertEqual(result["state"], "unavailable")
        self.assertFalse(result["managed"])

    def test_node_version_is_checked_before_spawn(self):
        manager = self._manager()
        with mock.patch.object(manager, "_node_version", return_value=(22, 18, 0)):
            result = manager.start()
        self.assertEqual(result["state"], "unavailable")
        self.assertIn("22.19", result["error"])

    def test_existing_service_is_reused_and_not_stopped(self):
        manager = self._manager()
        with mock.patch.object(manager, "_probe_existing", return_value=(True, 19001)), \
                mock.patch("app.core.our_free_model_service.subprocess.Popen") as popen:
            result = manager.start()
            manager.stop()
        self.assertEqual(result["state"], "already_running")
        self.assertFalse(result["managed"])
        popen.assert_not_called()

    def test_health_on_shared_port_without_matching_lock_is_not_reused(self):
        manager = self._manager()
        with mock.patch.object(manager, "_probe_health", return_value=True) as probe:
            self.assertEqual(manager._probe_existing(), (False, None))
        probe.assert_not_called()

    def test_lock_probe_treats_windows_kill_system_error_as_dead(self):
        manager = self._manager()
        self.data_dir.mkdir(parents=True, exist_ok=True)
        (self.data_dir / "service.lock").write_text(
            json.dumps({"product": "our-free-model-standalone", "pid": 4242}),
            encoding="utf-8")
        with mock.patch("app.core.our_free_model_service.os.kill",
                        side_effect=SystemError("kill returned a result with an error set")):
            self.assertIsNone(manager._live_lock_pid(self.data_dir))

    def test_spawn_uses_standalone_root_data_dir_and_environment(self):
        manager = self._manager()
        proc = _FakeProcess()
        with mock.patch.object(manager, "_probe_existing", return_value=(False, None)), \
                mock.patch.object(manager, "_node_version", return_value=(24, 1, 0)), \
                mock.patch.object(manager, "_wait_ready", return_value=19002), \
                mock.patch("app.core.our_free_model_service.subprocess.Popen", return_value=proc) as popen:
            result = manager.start()
            manager.stop()
        self.assertEqual(result["state"], "started")
        self.assertTrue(result["managed"])
        self.assertEqual(result["pid"], 4242)
        argv = popen.call_args.args[0]
        self.assertEqual(argv[0], "node")
        self.assertEqual(argv[1], str(self.root / "packages" / "standalone" / "cli.mjs"))
        self.assertIn("--data-dir", argv)
        self.assertEqual(popen.call_args.kwargs["cwd"], str(self.root))
        self.assertEqual(popen.call_args.kwargs["env"]["OFM_HOME"], str(self.data_dir))

    def test_start_does_not_spawn_twice(self):
        manager = self._manager()
        proc = _FakeProcess()
        with mock.patch.object(manager, "_probe_existing", return_value=(False, None)), \
                mock.patch.object(manager, "_node_version", return_value=(24, 1, 0)), \
                mock.patch.object(manager, "_wait_ready", return_value=19002), \
                mock.patch("app.core.our_free_model_service.subprocess.Popen", return_value=proc) as popen:
            manager.start()
            again = manager.start()
            manager.stop()
        self.assertEqual(again["pid"], 4242)
        popen.assert_called_once()

    def test_stop_terminates_only_manager_owned_process_and_is_idempotent(self):
        manager = self._manager()
        proc = _FakeProcess()
        with mock.patch.object(manager, "_probe_existing", return_value=(False, None)), \
                mock.patch.object(manager, "_node_version", return_value=(24, 1, 0)), \
                mock.patch.object(manager, "_wait_ready", return_value=19002), \
                mock.patch("app.core.our_free_model_service.subprocess.Popen", return_value=proc):
            manager.start()
            first = manager.stop()
            second = manager.stop()
        self.assertEqual(first["state"], "stopped")
        self.assertEqual(second["state"], "stopped")
        self.assertEqual(proc.terminated, 1)
        self.assertEqual(proc.killed, 0)

    def test_stop_kills_when_graceful_termination_times_out(self):
        manager = self._manager()
        proc = _FakeProcess(wait_value=subprocess.TimeoutExpired("node", 5))
        with mock.patch.object(manager, "_probe_existing", return_value=(False, None)), \
                mock.patch.object(manager, "_node_version", return_value=(24, 1, 0)), \
                mock.patch.object(manager, "_wait_ready", return_value=19002), \
                mock.patch("app.core.our_free_model_service.subprocess.Popen", return_value=proc), \
                mock.patch("app.core.our_free_model_service.subprocess.run"):
            manager.start()
            manager.stop()
        self.assertEqual(proc.terminated, 1)
        self.assertEqual(proc.killed, 1)


if __name__ == "__main__":
    unittest.main()
