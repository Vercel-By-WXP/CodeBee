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

    def test_disable_flows_treat_followup_silence_as_best_effort(self):
        # 禁用一成功，后端 clear_inactive 就会把健康行清掉，随后的顺手
        # 静默必然 404——它绝不能把整个禁用操作报成失败。
        self.assertNotIn(
            'if (provider) await applyHealthOp(provider, "silence");',
            self.block)
        self.assertEqual(
            self.block.count(
                'try { await applyHealthOp(provider, "silence"); } catch (e) {}'),
            2)

    def test_health_op_names_pruned_alert_branch(self):
        self.assertIn("e.status === 404", self.block)
        self.assertIn('t("该告警已消失，无需处理")', self.block)

    def test_health_op_survives_pruned_alert_and_reports_real_failures(self):
        node = shutil.which("node")
        if not node:
            self.skipTest("Node.js is required to exercise the browser helper")
        script = r"""
const assert = require('node:assert/strict');
const fs = require('node:fs');
const source = fs.readFileSync(process.argv[1], 'utf8');
const start = source.indexOf('async function applyHealthOp(provider, op) {');
const end = source.indexOf('\n/* 模型级禁用', start);
assert(start >= 0 && end > start, 'health op helpers must be present');
const load = new Function('S', 'toast', 'closeModal', 'render', 'refreshState',
  'renderHealthBanner', 't', 'api',
  source.slice(start, end) + '; return { healthOp };');
function makeEnv(failWith) {
  const calls = { toasts: [], closed: 0, rendered: 0, refreshed: 0 };
  const env = load(
    { state: {} },
    (...a) => calls.toasts.push(a),
    () => { calls.closed += 1; },
    () => { calls.rendered += 1; },
    () => { calls.refreshed += 1; return Promise.resolve(); },
    () => {},
    (k) => k,
    () => (failWith ? Promise.reject(failWith) : Promise.resolve({ health: {} })),
  );
  return { calls, env };
}
(async () => {
  const gone = new Error('未知的供应商'); gone.status = 404;
  const a = makeEnv(gone);
  await a.env.healthOp('silence', { provider: 'P' });
  assert.equal(a.calls.closed, 1, 'pruned alert must close like a success');
  assert.equal(a.calls.refreshed, 1);
  assert.deepEqual(a.calls.toasts, [['该告警已消失，无需处理']]);

  const boom = new Error('boom'); boom.status = 500;
  const b = makeEnv(boom);
  await b.env.healthOp('reset', { provider: 'P' });
  assert.equal(b.calls.closed, 0, 'real failures keep the dialog open');
  assert.deepEqual(b.calls.toasts, [['操作失败：boom', true]]);

  const c = makeEnv(null);
  await c.env.healthOp('silence', { provider: 'P' });
  assert.equal(c.calls.closed, 1);
  assert.deepEqual(c.calls.toasts, [['已静默 P 的告警（恢复后自动重新武装）']]);
})().catch((e) => { console.error(e); process.exit(1); });
"""
        subprocess.run(
            [node, "-e", script, str(APP_JS)],
            check=True, capture_output=True, text=True, timeout=10,
        )


if __name__ == "__main__":
    unittest.main()
