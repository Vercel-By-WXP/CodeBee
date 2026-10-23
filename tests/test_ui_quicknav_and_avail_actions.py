"""快捷导航绑定守卫 + 通道体检行内操作回归。

btn-q-kanban 落地时漏绑 click（其余快捷项都有监听）→ 按钮看得见、点了没
反应（2026-10-23 实案）。这里守三条纪律：

1. .side-quick 里每颗 data-page 按钮都必须在 app.js 有 addEventListener；
2. 同一页只挂 side-quick / side-settings 其中一边（升级=搬家不是复制）；
3. 通道体检行内操作只复用现成端点（provider-op / model-op / health/op），
   停用路径必须过 uiConfirm，静默的 404 按告警已消失收场。
"""
from __future__ import annotations

import re
import shutil
import subprocess
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
INDEX = ROOT / "app" / "ui" / "index.html"
APP_JS = ROOT / "app" / "ui" / "app.js"
I18N_JS = ROOT / "app" / "ui" / "i18n.js"


def _side_quick_pages(html: str) -> list[tuple[str, str]]:
    """[(button_id, data_page)]，按出现顺序。"""
    block = html[html.index('class="side-quick"'):html.index('id="side-projects"')]
    return re.findall(
        r'<button class="qitem[^"]*"[^>]*id="([^"]+)"[^>]*data-page="([^"]+)"', block)


class QuickNavBindingTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.html = INDEX.read_text(encoding="utf-8")
        cls.app = APP_JS.read_text(encoding="utf-8")

    def test_every_side_quick_button_has_click_binding(self):
        """看板按钮死亡案的类守卫：新加快捷项忘绑 click 时直接红。"""
        missing = [
            f"{bid} (data-page={page})" for bid, page in _side_quick_pages(self.html)
            if f'$("{bid}").addEventListener("click"' not in self.app
        ]
        self.assertEqual(missing, [], "快捷导航按钮缺 click 监听: %s" % missing)

    def test_kanban_binding_survives(self):
        self.assertIn(
            '$("btn-q-kanban").addEventListener("click", () => switchTab("kanban", "main"));',
            self.app)

    def test_availability_promoted_is_bound_in_main_shell(self):
        self.assertIn('id="btn-q-availability" data-page="availability"', self.html)
        self.assertIn(
            '$("btn-q-availability").addEventListener("click", () => switchTab("availability", "main"));',
            self.app)

    def test_no_page_hangs_in_both_navs(self):
        """重复挂=两处入口（index.html 左栏导航注释里的既有纪律）。"""
        quick = {page for _, page in _side_quick_pages(self.html)}
        nav_block = self.html[self.html.index('class="side-settings"'):self.html.index('id="side-resizer"')]
        settings = set(re.findall(r'data-sub="([^"]+)"', nav_block))
        self.assertEqual(quick & settings, set())

    def test_availability_set_item_removed_from_settings_nav(self):
        self.assertNotIn('data-sub="availability"', self.html)

    def test_app_js_still_parses(self):
        node = shutil.which("node")
        if not node:
            self.skipTest("Node.js is required")
        for path in (APP_JS, I18N_JS):
            r = subprocess.run([node, "--check", str(path)],
                               capture_output=True, text=True, timeout=60)
            self.assertEqual(r.returncode, 0, f"{path.name} 语法检查失败: {r.stderr}")


class AvailabilityInlineActionTests(unittest.TestCase):
    """行内操作只复用现成端点；危险路径过确认框；404 按消失收场。"""

    @classmethod
    def setUpClass(cls):
        cls.app = APP_JS.read_text(encoding="utf-8")
        start = cls.app.index("async function availProviderOp(")
        end = cls.app.index("function availAutoStart()", start)
        cls.block = cls.app[start:end]

    def test_provider_op_uses_existing_endpoint(self):
        self.assertIn('api("/api/models/provider-op"', self.block)
        self.assertIn("op === \"disable\"", self.block)

    def test_disable_paths_require_ui_confirm(self):
        self.assertEqual(self.block.count("uiConfirm("), 2,
                         "停用厂商/停用模型都必须过 uiConfirm")
        self.assertIn('danger: true', self.block)

    def test_disable_followed_by_best_effort_silence(self):
        # 停用成功后 clear_inactive 多半已清掉健康行：顺手静默必须吞异常
        self.assertIn('try { await applyHealthOp(pname || pid, "silence"); } catch (e) {}',
                      self.block)

    def test_silence_treats_404_as_already_gone(self):
        start = self.block.index("async function availSilence(")
        silence = self.block[start:self.block.index("/* 自动刷新", start)]
        self.assertIn('e.status === 404', silence)
        self.assertIn('t("该告警已消失，无需处理")', silence)

    def test_model_op_uses_existing_endpoint(self):
        self.assertIn('api("/api/models/model-op"', self.block)
        self.assertIn('op: enable ? "enable" : "disable"', self.block)

    def test_model_chips_pass_provider_id(self):
        self.assertIn("models.map((m) => availModelChip(m, p.id))", self.app)
        self.assertNotIn("models.map(availModelChip)", self.app)

    def test_silence_button_passes_provider_name(self):
        # health 台账按 provider name 对账（availability.py by_name），不是 id
        self.assertIn('onclick="availSilence(\\\'', self.app)


class AvailabilityAutoRefreshAndReportTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = APP_JS.read_text(encoding="utf-8")
        start = cls.app.index("function availAutoStart()")
        cls.block = cls.app[start:cls.app.index("function availCopyReport()", start)]
        cls.copy_block = cls.app[cls.app.index("function availCopyReport()"):
                                 cls.app.index("function homeHealthRefresh()")]

    def test_auto_refresh_only_silent_reloads(self):
        self.assertIn("loadAvailability({ silent: true })", self.block)
        self.assertNotIn("loadAvailability()", self.block)
        self.assertIn("S.tab !== \"availability\"", self.block)
        self.assertIn("document.hidden", self.block)

    def test_auto_timer_started_on_switchtab_stopped_on_leave(self):
        self.assertIn("loadAvailability({ silent: true }); availAutoStart(); }", self.app)
        self.assertIn("else availAutoStop();", self.app)
        self.assertIn("availAutoStop();   // 通道体检页的自动刷新同契约", self.app)

    def test_copy_report_uses_copytext_helper(self):
        self.assertIn("copyText(text, () => toast(t(\"诊断报告已复制\")));", self.copy_block)
        self.assertNotIn("navigator.clipboard", self.copy_block)

    def test_report_is_local_only(self):
        # 报告拼自 AVAIL.data 缓存，不再打任何接口
        self.assertNotIn("api(", self.block)

    def test_new_ui_strings_have_en_translations(self):
        i18n = I18N_JS.read_text(encoding="utf-8")
        for key in ("复制报告", "复制诊断报告", "诊断报告已复制", "自动刷新",
                    "静默告警", "停用通道", "停用模型", "{0} 个模型熔断",
                    "点击启用该模型", "还没有体检数据，先点「重新体检」"):
            self.assertIn(f'"{key}":', i18n, f"缺英文翻译: {key}")


class AvailabilityPageMarkupTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.html = INDEX.read_text(encoding="utf-8")

    def test_hero_has_copy_report_button(self):
        self.assertIn('id="avail-copy"', self.html)
        self.assertIn("onclick=\"availCopyReport()\"", self.html)

    def test_toolbar_has_auto_refresh_toggle(self):
        self.assertIn('id="avail-auto"', self.html)
        self.assertIn("onchange=\"availSetAuto(this.checked)\"", self.html)

    def test_kanban_qitem_present(self):
        self.assertIn('id="btn-q-kanban" data-page="kanban"', self.html)


if __name__ == "__main__":
    unittest.main()
