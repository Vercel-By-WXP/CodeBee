# -*- coding: utf-8 -*-
"""The external marketplace help text should name all configured sources."""
from __future__ import annotations

import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


class MarketSourceCopyTests(unittest.TestCase):
    def test_six_external_sources_are_named_in_localized_help(self):
        from app.core import market_remote

        text = (ROOT / "app" / "ui" / "i18n.js").read_text(encoding="utf-8")
        expected_ids = {
            "zcode", "anthropic", "anthropic-skills", "claude-skills",
            "clawhub", "cocoloop",
        }
        self.assertEqual(expected_ids, {source["id"] for source in market_remote.SOURCES})
        self.assertIn("ZCode、Anthropic", text)
        self.assertIn("ClawHub 与 CocoLoop", text)
        self.assertIn("ClawHub, and CocoLoop", text)


if __name__ == "__main__":
    unittest.main()
