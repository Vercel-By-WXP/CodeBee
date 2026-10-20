"""Regression checks for health-dialog action targeting."""
from __future__ import annotations

import re
import unittest
from pathlib import Path


APP_JS = Path(__file__).resolve().parents[1] / "app" / "ui" / "app.js"


class HealthAlertDialogRegressionTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        source = APP_JS.read_text(encoding="utf-8")
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


if __name__ == "__main__":
    unittest.main()
