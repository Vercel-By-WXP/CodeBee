"""Regression checks for mobile-friendly startup request scheduling."""
from pathlib import Path
import unittest


APP_JS = Path(__file__).resolve().parents[1] / "app" / "ui" / "app.js"
MOBILE_JS = Path(__file__).resolve().parents[1] / "app" / "ui" / "mobile-workbench.js"


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
        self.assertIn("if (!S.state || document.hidden || _startupEnhancementTimer || _startupEnhancementRunning) return;", source)
        self.assertNotIn("loadTaskTemplates();", startup_tail)

    def test_hidden_tab_does_not_drop_pending_startup_enhancements(self):
        source = self.source
        start = source.index("function scheduleStartupEnhancements()")
        end = source.index("function scheduleSSEReconnect()", start)
        scheduler = source[start:end]
        self.assertIn("_startupEnhancementTasks", scheduler)
        self.assertIn("_startupEnhancementRunning", scheduler)
        self.assertIn("if (document.hidden) return;", scheduler)
        visibility = source[source.index('document.addEventListener("visibilitychange"'):
                            source.index("/* ---------------------------------------------------------- 多端控制权")]
        self.assertIn("scheduleStartupEnhancements();", visibility)

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


if __name__ == "__main__":
    unittest.main()
