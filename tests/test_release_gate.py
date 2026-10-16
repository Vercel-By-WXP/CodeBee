# -*- coding: utf-8 -*-
from __future__ import annotations

import json
from pathlib import Path

from base import BaseTest


class TestReleaseGate(BaseTest):
    def test_production_guard_rejects_port_8765_without_connecting(self):
        from scripts.production_guard.sitecustomize import _guard

        with self.assertRaisesRegex(RuntimeError, "8765"):
            _guard("socket.connect", (None, ("127.0.0.1", 8765)))

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

    def test_package_member_validation_rejects_session_artifacts(self):
        from scripts.release_gate import GateError, validate_package_members

        for member in (
            "package/app/output/_probe_fq_submit.py",
            "package/app/outputs/state.json",
            "package/app/.playwright-cli/page.yml",
            "package/app/.ui-shots/page.png",
            "package/app/.codebee/session.json",
            "package/app/.mimocode/session.json",
            "package/app/.workbuddy/session.json",
            "package/app/.zcode/session.json",
            "package/app/.tmp-backup/main.py",
            "package/app/tmp_probe.py",
            "package/app/debug.log",
            "package/app/session.patch",
            "package/app/session.diff",
            "package/app/browser-workspace.png",
            "package/app/current-browser.png",
            "package\\app\\output\\state.json",
        ):
            with self.subTest(member=member), self.assertRaises(GateError):
                validate_package_members(["package/app/main.py", member])

    def test_package_member_validation_preserves_runtime_assets(self):
        from scripts.release_gate import validate_package_members

        validate_package_members([
            "package/app/core/tool_outputs.py", "package/app/core/browser.py",
            "package/app/pet_bee.png", "package/app/ui/icons/icon-192.png",
            "package/app/ui/assets/workbench-reference.png",
        ])

    def test_pack_json_accepts_npm_array(self):
        from scripts.release_gate import _pack_json

        self.assertEqual(_pack_json('[{"filename":"codebee-0.1.65.tgz"}]')["filename"],
                         "codebee-0.1.65.tgz")
