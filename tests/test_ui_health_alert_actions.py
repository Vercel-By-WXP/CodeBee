"""Regression checks for health-dialog action targeting."""
from __future__ import annotations

import re
import shutil
import subprocess
import unittest
from pathlib import Path


APP_JS = Path(__file__).resolve().parents[1] / "app" / "ui" / "app.js"


class HealthAlertDialogRegressionTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        source = APP_JS.read_text(encoding="utf-8")
        cls.source = source
        start = source.index("function healthBannerClick()")
        end = source.index("async function refreshState()", start)
        cls.block = source[start:end]

    def test_dialog_separates_provider_and_cli_account_alerts(self):
        self.assertIn('t("供应商连接告警")', self.block)
        self.assertIn('t("CLI 账号告警")', self.block)

    def test_provider_rows_bind_actions_to_their_own_alert_index(self):
        self.assertIn(r'data-health-index=\"', self.block)
        self.assertIn("bindHealthAlertActions(alerts, accountAlerts)", self.block)
        self.assertRegex(
            self.block,
            re.compile(r"alerts\[Number\(btn\.dataset\.healthIndex\)\]"),
        )

    def test_health_actions_never_fall_back_to_first_alert(self):
        self.assertNotIn("alerts[0]", self.block)
        self.assertNotIn("health.alerts[0]", self.block)

    def test_account_rows_offer_individual_twelve_hour_dismissal(self):
        self.assertIn(r'data-account-index=\"', self.block)
        self.assertIn("dismissAccountAlert(account.agent_id)", self.block)

    def test_disabled_provider_alerts_are_filtered_after_models_load(self):
        self.assertIn("function visibleProviderAlerts(health)", self.source)
        self.assertIn("S._modelsLoaded", self.source)
        self.assertIn("provider.enabled === false", self.source)
        self.assertIn("row.enabled === false || row.hidden", self.source)
        self.assertIn("visibleProviderAlerts(S.state && S.state.health)", self.block)

    def test_provider_alert_filter_waits_for_config_and_preserves_active_alerts(self):
        node = shutil.which("node")
        if not node:
            self.skipTest("Node.js is required to exercise the browser helper")
        script = r"""
const assert = require('node:assert/strict');
const fs = require('node:fs');
const source = fs.readFileSync(process.argv[1], 'utf8');
const start = source.indexOf('function visibleProviderAlerts(health) {');
const end = source.indexOf('\nfunction dismissVisibleAccountAlerts()', start);
assert(start >= 0 && end > start, 'provider alert filter must be present');
const getFilter = new Function('S', source.slice(start, end) + '; return visibleProviderAlerts;');
const activeAlert = { provider: 'P', provider_id: 'p1', model: 'm1' };
const unknownAlert = { provider: 'synthetic', provider_id: '', model: '' };
const health = { alerts: [activeAlert, unknownAlert] };
assert.deepEqual(getFilter({ _modelsLoaded: false, providers: [] })(health), []);
assert.deepEqual(getFilter({ _modelsLoaded: true, providers: [
  { id: 'p1', enabled: false, models: [{ name: 'm1', enabled: true }] }
] })(health), [unknownAlert]);
assert.deepEqual(getFilter({ _modelsLoaded: true, providers: [
  { id: 'p1', enabled: true, models: [{ name: 'm1', enabled: false }] }
] })(health), [unknownAlert]);
assert.deepEqual(getFilter({ _modelsLoaded: true, providers: [
  { id: 'p1', enabled: true, models: [{ name: 'm1', enabled: true }] }
] })(health), [activeAlert, unknownAlert]);
"""
        subprocess.run(
            [node, "-e", script, str(APP_JS)],
            check=True, capture_output=True, text=True, timeout=10,
        )


if __name__ == "__main__":
    unittest.main()
