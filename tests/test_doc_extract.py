# -*- coding: utf-8 -*-
"""Tests for local PDF/legacy document text extraction."""
import base64
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from app.core import attachments, doc_extract, paths


def _pdf_bytes(text="Hello PDF"):
    content = ("BT /F1 18 Tf 40 100 Td (%s) Tj ET" % text).encode("ascii")
    objects = [
        b"<< /Type /Catalog /Pages 2 0 R >>",
        b"<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
        b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 300 200] "
        b"/Contents 4 0 R /Resources << /Font << /F1 5 0 R >> >> >>",
        b"<< /Length %d >>\nstream\n" % len(content) + content + b"\nendstream",
        b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>",
    ]
    header = b"%PDF-1.4\n"
    body = b""
    offsets = []
    for index, obj in enumerate(objects, 1):
        offsets.append(len(header) + len(body))
        body += b"%d 0 obj\n" % index + obj + b"\nendobj\n"
    xref_at = len(header) + len(body)
    xref = b"xref\n0 %d\n0000000000 65535 f \n" % (len(objects) + 1)
    xref += b"".join(b"%010d 00000 n \n" % off for off in offsets)
    trailer = (b"trailer\n<< /Size %d /Root 1 0 R >>\nstartxref\n%d\n%%%%EOF\n"
               % (len(objects) + 1, xref_at))
    return header + body + xref + trailer


RTF = (b"{\\rtf1\\ansi\\ansicpg936 {\\fonttbl{\\f0 Arial;}}" 
       b"Hello\\par \\u20013?\\u25991? and \\'d6\\'d0\\'ce\\'c4}")


class DocExtractTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)

    def tearDown(self):
        self.tmp.cleanup()

    def test_rtf_engine_extracts_structure_and_unicode(self):
        path = self.root / "note.rtf"
        path.write_bytes(RTF)
        result = doc_extract.extract(path)
        self.assertEqual(result["engine"], "rtf-parser")
        text = result["text"]
        self.assertIn("Hello", text)
        self.assertIn("中文", text)
        self.assertNotIn("Arial", text)
        self.assertIn("\n", text)

    def test_pdf_uses_a_local_engine_when_present(self):
        path = self.root / "sample.pdf"
        path.write_bytes(_pdf_bytes())
        result = doc_extract.extract(path)
        if not result["text"]:
            self.skipTest("no local PDF engine installed")
        self.assertIn("Hello PDF", result["text"])
        self.assertIn(result["engine"], doc_extract.PDF_ENGINES)

    def test_unsupported_and_missing_files_fail_soft(self):
        bogus = self.root / "file.xyz"
        bogus.write_text("hello", encoding="utf-8")
        self.assertEqual(doc_extract.extract(bogus), {"text": "", "engine": ""})
        self.assertEqual(doc_extract.extract(self.root / "absent.pdf"),
                         {"text": "", "engine": ""})

    def test_cap_marks_truncation(self):
        path = self.root / "long.rtf"
        path.write_bytes(b"{\\rtf1 " + b"A" * 500 + b"}")
        result = doc_extract.extract(path, max_chars=20)
        self.assertTrue(result["text"].startswith("A" * 20))
        self.assertIn("已截断", result["text"])
        self.assertLess(len(result["text"]), 60)

    def test_available_lists_pdf_engines_without_running_them(self):
        info = doc_extract.available()
        ids = {row["id"] for row in info["engines"]}
        self.assertTrue(set(doc_extract.PDF_ENGINES) <= ids)
        self.assertTrue(any(row["id"] == "rtf-parser" and row["available"]
                            for row in info["engines"]))
        for row in info["engines"]:
            self.assertIsInstance(row["available"], bool)


class AttachmentSidecarTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.old_data = paths.DATA_DIR
        paths.DATA_DIR = self.root / "data"
        self.workdir = self.root / "work"
        self.workdir.mkdir()

    def tearDown(self):
        paths.DATA_DIR = self.old_data
        self.tmp.cleanup()

    def test_pdf_commit_writes_text_sidecar(self):
        item = attachments.save_pending(
            "报告.pdf", base64.b64encode(_pdf_bytes()).decode())
        with mock.patch.object(doc_extract, "extract_text", return_value="PDF 正文") as extract:
            committed = attachments.commit_to_workdir(self.workdir, [item["id"]])
        self.assertEqual(len(committed), 1)
        row = committed[0]
        self.assertEqual(row["text_path"], "_attachments/报告.pdf.txt")
        self.assertEqual((self.workdir / row["text_path"]).read_text(encoding="utf-8"),
                         "PDF 正文")
        extract.assert_called_once()

    def test_failed_extraction_keeps_attachment_without_sidecar(self):
        item = attachments.save_pending(
            "scan.pdf", base64.b64encode(_pdf_bytes()).decode())
        with mock.patch.object(doc_extract, "extract_text", return_value=None):
            committed = attachments.commit_to_workdir(self.workdir, [item["id"]])
        self.assertEqual(len(committed), 1)
        self.assertNotIn("text_path", committed[0])
        self.assertTrue((self.workdir / committed[0]["path"]).is_file())


if __name__ == "__main__":
    unittest.main()
