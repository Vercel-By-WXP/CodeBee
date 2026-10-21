import json
import os
import tempfile
import threading
import time
import unittest
from pathlib import Path
from unittest import mock

from app.core import atomic


class AtomicPersistenceTests(unittest.TestCase):
    def test_create_publishes_complete_payload_and_cleans_source_temp(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            target = root / "story-bible.md"
            atomic.create_text(target, "完整设定", mode=0o644)
            self.assertEqual(target.read_text(encoding="utf-8"), "完整设定")
            self.assertEqual(
                list(root.glob(".story-bible.md.*.tmp")),
                [],
                "exclusive create must not leave its temporary hard-link source behind",
            )
            with self.assertRaises(FileExistsError):
                atomic.create_text(target, "竞争写入")
            self.assertEqual(target.read_text(encoding="utf-8"), "完整设定")

    def test_concurrent_exclusive_creators_have_one_complete_winner(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            target = root / "story-bible.md"
            barrier = threading.Barrier(8)
            outcomes = []

            def create(index):
                try:
                    barrier.wait()
                    atomic.create_text(target, "设定-%d" % index)
                    outcomes.append(("winner", index))
                except FileExistsError:
                    outcomes.append(("loser", index))

            threads = [threading.Thread(target=create, args=(i,)) for i in range(8)]
            for thread in threads:
                thread.start()
            for thread in threads:
                thread.join()
            self.assertEqual(sum(kind == "winner" for kind, _ in outcomes), 1)
            self.assertRegex(target.read_text(encoding="utf-8"), r"^设定-[0-7]$")
            self.assertEqual(list(root.glob(".story-bible.md.*.tmp")), [])

    def test_replace_never_exposes_partial_json_to_readers(self):
        with tempfile.TemporaryDirectory() as tmp:
            target = Path(tmp) / "state.json"
            atomic.write_json(target, {"version": 0, "payload": "old"})
            errors = []

            def writer():
                try:
                    for version in range(1, 80):
                        atomic.write_json(target, {
                            "version": version,
                            "payload": "x" * 2000,
                        })
                except Exception as exc:  # pragma: no cover - diagnostic path
                    errors.append(exc)

            thread = threading.Thread(target=writer)
            thread.start()
            while thread.is_alive():
                try:
                    value = json.loads(target.read_text(encoding="utf-8"))
                    self.assertIsInstance(value["version"], int)
                    self.assertEqual(len(value["payload"]), 2000 if value["version"] else 3)
                except PermissionError:
                    # Windows briefly denies a new open while ReplaceFile is
                    # publishing over a reader-held handle.  Retry the read;
                    # the invariant under test is that every successful read
                    # is a complete JSON document.
                    if os.name != "nt":
                        self.fail("reader could not open the durable document")
                    time.sleep(0.001)
                except (OSError, json.JSONDecodeError, KeyError, TypeError):
                    self.fail("reader observed a partial document")
            thread.join()
            self.assertEqual(errors, [])

    @unittest.skipIf(os.name == "nt", "POSIX mode bits are not portable on Windows")
    def test_sensitive_mode_is_applied_before_publish(self):
        with tempfile.TemporaryDirectory() as tmp:
            target = Path(tmp) / "secret.json"
            atomic.write_json(target, {"token": "secret"}, mode=0o600)
            self.assertEqual(target.stat().st_mode & 0o777, 0o600)

    def test_windows_mode_application_does_not_make_files_read_only(self):
        with mock.patch.object(atomic.os, "name", "nt"), \
             mock.patch.object(atomic.os, "chmod") as chmod:
            atomic._apply_mode("unused-on-windows", 0o600)
        chmod.assert_not_called()


if __name__ == "__main__":
    unittest.main()
