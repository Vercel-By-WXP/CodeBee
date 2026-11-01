# -*- coding: utf-8 -*-
"""ReasoningBank / eject 的 UI 接线守测（源级断言，同 native-dialog-ban 口径）。

锚点：
1. borrow-ui.js 挂记忆中心搜索框（#borrow-bank-box → /api/reasoning-bank）；
2. borrow-ui.js 挂详情页「导出带走」按钮（#btn-eject → eject/download 下载）；
3. 两个入口都走 wrap overlay 纪律（不直接改 app.js 渲染函数本体）；
4. i18n 词条在册（EN 词典不缺新按钮文案）。
"""
from __future__ import annotations

import unittest
from pathlib import Path

UI = Path(__file__).resolve().parent.parent / "app" / "ui"


class BankEjectUiTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.borrow = (UI / "borrow-ui.js").read_text(encoding="utf-8")
        cls.i18n = (UI / "i18n.js").read_text(encoding="utf-8")
        cls.index = (UI / "index.html").read_text(encoding="utf-8")

    def test_bank_search_box_wired(self):
        self.assertIn('id="borrow-bank-box"', self.borrow)
        self.assertIn("/api/reasoning-bank", self.borrow)
        self.assertIn("wrapBankSearch", self.borrow)
        self.assertIn("window.renderMemoryCenter = wrapped", self.borrow)

    def test_eject_button_wired(self):
        self.assertIn('"btn-eject"', self.borrow)
        self.assertIn("/eject/download", self.borrow)
        self.assertIn("wrapRenderDetail", self.borrow)
        # 只包 renderRunDetail/renderTaskDetail，不碰 app.js 本体
        self.assertIn('"renderRunDetail"', self.borrow)
        self.assertIn('"renderTaskDetail"', self.borrow)

    def test_i18n_keys_registered(self):
        for key in ("导出带走", "搜历史模式（跨任务经验）", "搬记忆入库",
                    "成功模式", "教训"):
            self.assertIn('"%s"' % key, self.i18n)

    def test_memory_host_and_detail_anchor_exist(self):
        # 宿主必须在静态 DOM 里真实存在（vault/disk 盒的教训：绑到不存在的
        # 宿主 = 静默死功能）
        self.assertIn('id="sub-memory"', self.index)
        self.assertIn('id="btn-delete"', self.index)

    def test_no_native_dialogs_in_new_code(self):
        # 借 UI 段落内不引入原生对话框（全仓禁令另有守测，此处双保险）
        seg = self.borrow[self.borrow.index("borrow-bank-box"):]
        for bad in ("alert(", "confirm(", "prompt("):
            self.assertNotIn("window." + bad, seg)
            self.assertNotIn("(\"" + bad[:-1] + "\")", seg)


if __name__ == "__main__":
    unittest.main()
