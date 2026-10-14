"""JSONL checkpoint reads preserve line windows and isolate damaged files."""
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from core import checkpoints


class CheckpointStreamingTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.patch = patch.object(checkpoints, "_DIR", self.root)
        self.patch.start()
        self.addCleanup(self.patch.stop)

    def test_unicode_separators_inside_json_strings_are_not_record_boundaries(self):
        row = {"run_id": "run", "text": "中文😀\u2028段落\u2029末尾\u0085继续"}
        (self.root / "checkpoints-202610.jsonl").write_text(
            json.dumps(row, ensure_ascii=False) + "\n", encoding="utf-8")
        self.assertEqual(checkpoints._read("run"), [row])

    def test_invalid_utf8_month_is_skipped_without_losing_other_months(self):
        row = {"run_id": "run", "step": 1}
        (self.root / "checkpoints-202609.jsonl").write_text(
            json.dumps(row) + "\n", encoding="utf-8")
        (self.root / "checkpoints-202610.jsonl").write_bytes(
            json.dumps(row).encode("utf-8") + b"\n\xff")
        self.assertEqual(checkpoints._read("run"), [row])

    def test_line_window_counts_invalid_and_other_run_rows_before_filtering(self):
        first = json.dumps({"run_id": "run", "step": 0})
        last = {"run_id": "run", "step": 2}
        padding = "\nnot json\n[]\n" * 16666
        other = json.dumps({"run_id": "other", "step": 1})
        (self.root / "checkpoints-202610.jsonl").write_text(
            first + "\n" + padding + other + "\n" + json.dumps(last),
            encoding="utf-8")
        self.assertEqual(checkpoints._read("run"), [last])

    def test_crlf_blank_lines_and_final_row_without_newline(self):
        rows = [{"run_id": "run", "step": 1}, {"run_id": "run", "step": 2}]
        content = json.dumps(rows[0]) + "\r\n\r\nnot json\r\n" + json.dumps(rows[1])
        (self.root / "checkpoints-202610.jsonl").write_bytes(content.encode("utf-8"))
        self.assertEqual(checkpoints._read("run"), rows)

    def test_oversized_file_is_still_skipped(self):
        with (self.root / "checkpoints-202610.jsonl").open("wb") as handle:
            handle.seek(4 * 1024 * 1024)
            handle.write(b"\n")
        self.assertEqual(checkpoints._read(), [])


if __name__ == "__main__":
    unittest.main()
