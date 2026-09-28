import re
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
INDEX = (ROOT / "ui" / "index.html").read_text(encoding="utf-8")
APP = (ROOT / "ui" / "app.js").read_text(encoding="utf-8")
STYLE = (ROOT / "ui" / "style.css").read_text(encoding="utf-8")


class BrowserWorkspaceContractTests(unittest.TestCase):
    def test_navigation_and_page_contract_exists(self):
        self.assertRegex(INDEX, r'id="btn-q-browser"[^>]+data-page="browser"')
        self.assertRegex(INDEX, r'id="btn-rail-browser"[^>]+data-rail-page="browser"')
        self.assertIn('id="sub-browser"', INDEX)
        self.assertIn('id="browser-nav-form"', INDEX)
        self.assertIn('id="browser-frame"', INDEX)
        self.assertIn('id="browser-frame-error"', INDEX)
        self.assertIn('id="browser-open-preview"', INDEX)
        for element_id in (
            "browser-context",
            "browser-task-select",
            "browser-task-card",
            "browser-verify-card",
            "browser-publish-card",
        ):
            self.assertIn('id="%s"' % element_id, INDEX)

    def test_browser_workspace_has_navigation_controls(self):
        for element_id in (
            "browser-back",
            "browser-forward",
            "browser-reload",
            "browser-home",
            "browser-new-tab",
            "browser-open-external",
        ):
            self.assertIn('id="%s"' % element_id, INDEX)

    def test_browser_url_normalization_rejects_unsafe_schemes(self):
        self.assertIn("function normalizeBrowserUrl", APP)
        self.assertRegex(APP, r"normalizeBrowserUrl[\s\S]{0,1400}javascript")
        self.assertRegex(APP, r"normalizeBrowserUrl[\s\S]{0,1400}https://")

    def test_browser_state_is_bounded_and_persisted(self):
        self.assertIn("BROWSER_STORAGE_KEY", APP)
        self.assertIn("browserSaveState", APP)
        self.assertRegex(APP, r"slice\(0,\s*20\)")

    def test_browser_iframe_uses_sandbox_and_external_fallback(self):
        iframe = re.search(r'<iframe id="browser-frame"[^>]*>', INDEX)
        self.assertIsNotNone(iframe)
        self.assertIn("sandbox=", iframe.group(0))
        self.assertIn("window.open", APP)
        self.assertIn("browser-open-external", APP)

    def test_browser_auth_only_attaches_token_to_same_origin_urls(self):
        self.assertIn("function browserUrlAuth", APP)
        self.assertRegex(APP, r"frame\.src\s*=\s*browserUrlAuth\(")
        self.assertRegex(APP, r"window\.open\(browserUrlAuth\(")
        self.assertNotIn("frame.src = urlAuth(", APP)
        self.assertRegex(APP, r'browser-open-external[\s\S]{0,320}window\.open\(browserUrlAuth\(')

    def test_browser_styles_cover_responsive_workspace(self):
        for selector in (".browser-shell", ".browser-toolbar", ".browser-stage", ".browser-tabs"):
            self.assertIn(selector, STYLE)
        self.assertIn("@media (max-width: 640px)", STYLE)

    def test_browser_rail_selection_is_synchronized(self):
        self.assertIn('data-rail-page="browser"', INDEX)
        self.assertRegex(APP, r'rail-btn\[data-rail-page\][\s\S]{0,260}dataset\.railPage')

    def test_browser_uses_full_workspace_width_and_google_embed_mode(self):
        self.assertIn('#page-settings > #sub-browser', STYLE)
        self.assertIn('max-width: none', STYLE)
        self.assertIn('const BROWSER_SEARCH = "https://www.google.com/search?igu=1&q="', APP)
        self.assertIn('searchParams.set("igu", "1")', APP)
        self.assertIn('Google 可在本机系统浏览器访问', APP)

    def test_browser_context_is_task_aware_and_reuses_publish_state(self):
        self.assertIn("function browserCurrentTask", APP)
        self.assertIn("function browserRenderTaskContext", APP)
        self.assertIn("/api/publish", APP)
        self.assertIn("/api/publish/task/" , APP)
        self.assertIn("发布由 CodeBee 受控浏览器执行", INDEX)
        self.assertIn("browser-page-active", STYLE)

    def test_browser_home_is_a_task_workbench(self):
        for action in ("preview", "verify", "publish", "retry"):
            self.assertIn('data-browser-home-action="%s"' % action, INDEX)
        self.assertIn('id="browser-home-task-title"', INDEX)
        self.assertIn('id="browser-home-run"', INDEX)
        self.assertIn(".browser-home-workflows", STYLE)
        self.assertIn("grid-template-columns: repeat(2", STYLE)


if __name__ == "__main__":
    unittest.main()
