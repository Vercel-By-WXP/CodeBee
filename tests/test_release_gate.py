# -*- coding: utf-8 -*-
from __future__ import annotations

import json
from pathlib import Path

from base import BaseTest


class TestReleaseGate(BaseTest):
    def test_npm_publish_has_physical_pre_gate(self):
        package = json.loads((Path(__file__).resolve().parents[1] /
                              "package.json").read_text(encoding="utf-8"))
        self.assertEqual(package["scripts"]["prepublishOnly"],
                         "python scripts/release_gate.py")

    def test_package_member_validation_rejects_runtime_data(self):
        from scripts.release_gate import GateError, validate_package_members

        with self.assertRaises(GateError):
            validate_package_members(["package/app/main.py", "package/data/runs/x.json"])

    def test_package_member_validation_allows_declared_runtime(self):
        from scripts.release_gate import validate_package_members

        validate_package_members(["package/app/main.py", "package/bin/tutti.js",
                                  "package/README.md"])

    def test_pack_json_accepts_npm_array(self):
        from scripts.release_gate import _pack_json

        self.assertEqual(_pack_json('[{"filename":"codebee-0.1.65.tgz"}]')["filename"],
                         "codebee-0.1.65.tgz")
