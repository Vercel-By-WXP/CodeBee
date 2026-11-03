# -*- coding: utf-8 -*-
"""CLI 日志必须走真弹窗（2026-11-03 用户连续三轮：「点击详情很丑」「感觉没变」「还是没弹窗」）。

根因（实测定案）：
    chatEngineIsDirect(run) 只要 run 有对应任务就返回 true（app.js:14110），
    所以几乎每个任务详情的 #run-detail 都带 chat-mode。任何「非 chat-mode 才
    弹窗 / 才用浮层」的分流，实际永远走不到 —— 用户点步骤仍然看到内联抽屉。

锁死三件事，防止并行会话整文件重写 app.js 时把修复再次回滚：
1) toggleLog 无条件调用 openLogModal（在函数体里出现在任何 chat-mode 判断之前）；
2) openLogModal 走应用内 #modal（有标题「CLI 日志」、#logm-pre、复制、关闭）；
3) style.css 的弹窗宽度写在内部卡片 `#modal.log-modal .modal` 上（cls 加在外层
   遮罩 `.modal-wrap` 上，只写 `.modal.log-modal` 匹配不到卡片）。
"""
import os
import re
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
UI_DIR = os.path.join(ROOT, "app", "ui")


def _read(name):
    with open(os.path.join(UI_DIR, name), "r", encoding="utf-8") as fh:
        return fh.read()


class TestLogModal(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.js = _read("app.js")
        cls.css = _read("style.css")

    def _toggle_log_body(self):
        start = self.js.index("async function toggleLog(runId, rel)")
        # 函数体到下一个顶层 "}\n" 结束（下一个函数注释前）
        end = self.js.index("/* ---------------- 蜂巢工作台", start)
        return self.js[start:end]

    def test_toggle_log_always_opens_modal(self):
        body = self._toggle_log_body()
        self.assertIn("typeof openLogModal === \"function\"", body,
                      "toggleLog 必须先尝试弹窗；否则又会退回内联抽屉")
        # 不允许在弹窗之前先按 chat-mode 分流（chat-mode 恒真，会吞掉弹窗）
        guard = body.index("typeof openLogModal")
        mode_check = body.find('contains("chat-mode")')
        self.assertTrue(mode_check == -1 or guard < mode_check,
                        "chat-mode 判断不能排在弹窗之前：chatEngineIsDirect() 对任何"
                        "有任务归属的 run 都为真，详情页几乎恒带 chat-mode")

    def test_open_log_modal_builds_modal_dialog(self):
        start = self.js.index("function openLogModal(runId, rel)")
        seg = self.js[start:self.js.index("window.openLogModal = openLogModal", start)]
        self.assertIn('openModal(t("CLI 日志")', seg, "日志弹窗要走应用内 openModal")
        self.assertIn("logm-pre", seg, "弹窗正文容器 #logm-pre")
        self.assertIn("logModalCopy", seg, "弹窗要有复制入口")
        self.assertIn('t("关闭")', seg, "弹窗要有明确的关闭按钮")

    def test_css_width_targets_inner_modal_card(self):
        self.assertIn("#modal.log-modal .modal", self.css,
                      "log-modal 类加在 #modal（外层遮罩）上，宽度必须写给内部 .modal 卡片")
        self.assertIn(".logm-pre", self.css, "日志弹窗正文样式缺失")

    # ---- 正文 token 配对 + 整词断行（与抽屉同一套口径）------------------
    def _declared(self, selector, prop):
        """该属性在「最后一个声明它的规则体」里的值（层叠以靠后者为准）。"""
        val = None
        for m in re.finditer(re.escape(selector) + r"\s*[,{]", self.css):
            brace = self.css.index("{", m.end() - 1)
            depth = 0
            for i in range(brace, len(self.css)):
                if self.css[i] == "{":
                    depth += 1
                elif self.css[i] == "}":
                    depth -= 1
                    if depth == 0:
                        body = self.css[brace + 1:i]
                        hit = re.search(r"(?:^|[;\s])" + re.escape(prop) + r"\s*:\s*([^;]+);", body)
                        if hit:
                            val = hit.group(1).strip()
                        break
        return val

    def test_log_body_pairs_log_surface_tokens(self):
        """`--log-text` 是跟着 `--log-bg` 调的：用 `--bg` 垫底属于跨 token 错配。"""
        self.assertEqual(self._declared(".logm-pre", "color"), "var(--log-text, var(--text))")
        bg = self._declared("#modal.log-modal .logm-pre", "background")
        self.assertEqual(bg, "var(--log-bg)",
                         "日志弹窗正文必须坐 --log-bg 终端面（换到 log 面偏暗的皮肤上，"
                         "--bg + --log-text 会读不清）")

    def test_log_body_wraps_tokens_instead_of_chopping(self):
        self.assertEqual(self._declared("#modal.log-modal .logm-pre", "word-break"), "normal",
                         "word-break: break-word 是旧别名，改用 overflow-wrap: anywhere")
        self.assertEqual(self._declared("#modal.log-modal .logm-pre", "overflow-wrap"), "anywhere",
                         "长路径放不下才断，放得下要整词")

    def test_surface_selector_targets_the_wrap_not_the_card(self):
        """`log-modal` 挂在遮罩 `#modal` 上、`.modal` 是内层卡片：写成 `.modal.log-modal`
        两个类同元素，匹配不到任何东西（第一版就是这样，改完线上量到的还是旧底色）。"""
        self.assertIn("#modal.log-modal .logm-pre", self.css)
        self.assertNotIn(".modal.log-modal .logm-pre", self.css,
                         "同一个元素不可能既是遮罩又是卡片：这里必须写 #modal.log-modal")


if __name__ == "__main__":
    unittest.main()
