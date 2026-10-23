"""Regression checks for mobile-friendly startup request scheduling."""
from pathlib import Path
import unittest


APP_JS = Path(__file__).resolve().parents[1] / "app" / "ui" / "app.js"
MOBILE_JS = Path(__file__).resolve().parents[1] / "app" / "ui" / "mobile-workbench.js"
PHONE_UI = Path(__file__).resolve().parents[1] / "cloud" / "phone-relay" / "public" / "ui"


class StartupConnectivityTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.source = APP_JS.read_text(encoding="utf-8")

    def test_startup_data_is_scheduled_after_state_and_not_eagerly_fired(self):
        source = self.source
        start = source.index("document.addEventListener(\"DOMContentLoaded\"")
        end = source.index("/* ===== 命令面板", start)
        startup = source[start:end]
        startup_tail = startup[startup.rindex("  poll();"):]
        self.assertIn("poll();", startup)
        for eager_call in ("loadFlows();", "loadExperts();", "refreshSessionAgents();",
                           "loadOrchestrator();", "loadSettings();", "suStartupCheck();"):
            self.assertNotIn(eager_call, startup_tail)
        self.assertIn("scheduleStartupEnhancements();", source)
        self.assertIn("if (!S.state || document.hidden || _startupEnhancementTimer || _startupEnhancementActive >= 2) return;", source)
        self.assertNotIn("loadTaskTemplates();", startup_tail)

    def test_hidden_tab_does_not_drop_pending_startup_enhancements(self):
        source = self.source
        start = source.index("function scheduleStartupEnhancements()")
        end = source.index("function scheduleSSEReconnect()", start)
        scheduler = source[start:end]
        self.assertIn("_startupEnhancementTasks", scheduler)
        self.assertIn("_startupEnhancementActive", scheduler)
        self.assertIn("if (document.hidden) return;", scheduler)
        visibility = source[source.index('document.addEventListener("visibilitychange"'):
                            source.index("/* ---------------------------------------------------------- 多端控制权")]
        self.assertIn("scheduleStartupEnhancements();", visibility)

    def test_startup_enhancements_use_bounded_parallelism_after_first_state(self):
        source = self.source
        start = source.index("function scheduleStartupEnhancements()")
        end = source.index("function scheduleSSEReconnect()", start)
        scheduler = source[start:end]
        self.assertIn("_startupEnhancementActive >= 2", scheduler)
        self.assertIn("Promise.resolve().then(task)", scheduler)
        self.assertIn("if (!S.state || document.hidden", scheduler)
        self.assertNotIn("setTimeout(runNext, 250)", scheduler)

    def test_type_estimates_are_loaded_on_demand_not_during_first_render(self):
        source = self.source
        start = source.index("function renderTypeMenu()")
        end = source.index("window.manageFlowsFromMenu", start)
        self.assertNotIn("fetchFlowEstimates();", source[start:end])
        toggle_start = source.index("function toggleTypeMenu(force)")
        toggle_end = source.index("function pickType", toggle_start)
        self.assertIn("fetchFlowEstimates()", source[toggle_start:toggle_end])

    def test_mobile_control_requests_wait_for_the_first_state_snapshot(self):
        app = self.source
        mobile = MOBILE_JS.read_text(encoding="utf-8")
        self.assertIn('window.dispatchEvent(new Event("orch:state-ready"))', app)
        self.assertIn('window.addEventListener("orch:state-ready", setup, { once: true })', mobile)
        self.assertNotIn("window.setTimeout(setup, 500)", mobile)

    def test_sse_open_does_not_mark_mobile_connection_live(self):
        source = self.source
        start = source.index("function startSSE()")
        end = source.index("/* 页签可见性", start)
        sse = source[start:end]
        onopen = sse[sse.index("es.onopen") : sse.index("// 命名事件")]
        self.assertIn("S.sseLive = false", onopen)
        self.assertNotIn("S.sseLive = true", onopen)
        self.assertIn("S.sseLive = true", sse[sse.index("es.onmessage") :])

    def test_state_poll_continues_until_first_state_frame_and_recovers_silent_sse(self):
        source = self.source
        start = source.index("async function poll()")
        end = source.index("function schedulePolling()", start)
        poll = source[start:end]
        self.assertIn("if (!S.sseLive || !S.state) await refreshState();", poll)
        self.assertIn("_lastStateSseAt", poll)
        self.assertIn("await refreshState();", poll)

    def test_state_poll_shows_relay_error_details_and_allows_slow_mobile_links(self):
        source = self.source
        refresh = source[source.index("async function refreshState()"):
                        source.index("let _lookupRefresh", source.index("async function refreshState()"))]
        self.assertIn('timeout: 30000', refresh)
        start = source.index("async function poll()")
        end = source.index("function schedulePolling()", start)
        poll = source[start:end]
        self.assertIn('if (!S.state || !S.sseLive || !S.es)', poll)
        self.assertIn('t("连接失败：") + String((e && e.message) || e).slice(0, 100)', poll)

    def test_mobile_state_poll_uses_conditional_requests_and_keeps_cached_snapshot(self):
        source = self.source
        refresh = source[source.index("async function refreshState()"):
                        source.index("let _lookupRefresh", source.index("async function refreshState()"))]
        api = source[source.index("async function api(path, opts)"):
                    source.index("/* 质量门禁", source.index("async function api(path, opts)"))]
        self.assertIn('"If-None-Match"', refresh)
        self.assertIn("_stateEtag", refresh)
        self.assertIn("onResponse", refresh)
        self.assertIn("res.status === 304", api)
        self.assertIn("if (state) applyState(state)", refresh)

    def test_sse_degradation_does_not_hide_a_successful_state_snapshot(self):
        source = self.source
        start = source.index("function startSSE()")
        end = source.index("/* 页签可见性", start)
        sse = source[start:end]
        error = sse[sse.index("es.onerror") :]
        self.assertIn('if (S.state)', error)
        self.assertIn('conn.textContent = t("已连接")', error)
        self.assertIn('conn.className = "conn ok"', error)
        self.assertIn('conn.title = t("实时连接受限，已用轮询兜底")', error)
        # Before the first state snapshot, only /api/state knows whether the
        # phone relay is unauthorized/offline. SSE errors must not overwrite
        # the more useful polling error with a generic badge.
        self.assertNotIn('conn.textContent = t("连接失败")', error)

    def test_only_explicit_desktop_token_401_opens_token_gate(self):
        source = self.source
        start = source.index("async function api(path, opts)")
        end = source.index("/* 质量门禁", start)
        api = source[start:end]
        self.assertLess(api.index("const rawText = await res.text();"), api.index("if (res.status === 401)"))
        self.assertIn('if (res.status === 401)', api)
        self.assertIn('const authError = (data && data.error)', api)
        self.assertIn('const isDesktopTokenError = /需要访问令牌|令牌(?:不正确|不匹配)|invalid or revoked token/i.test(authError)', api)
        self.assertIn('if (isDesktopTokenError)', api)
        self.assertNotIn('const isRelayAuthError', api)
        self.assertIn('showTokenGate(t("令牌不正确或已更换，请重新输入"))', api)
        self.assertIn('const failure = new Error(authError)', api)

    def test_phone_edge_assets_match_the_current_mobile_workbench_sources(self):
        for name in ("app.js", "mobile-workbench.js", "index.html"):
            with self.subTest(asset=name):
                self.assertEqual((APP_JS.parent / name).read_bytes(), (PHONE_UI / name).read_bytes())


if __name__ == "__main__":
    unittest.main()
