# -*- coding: utf-8 -*-
"""连载 branches（多线剧情推演）UI 入口回归——本轮 borrow 落地件。

后端 branches 全链早已支持（store.create_task 钳位 1-3、branching.plan_branches
写前推演、continue_task 随链沿用），但前端任务表单与流程编辑器都没有输入口，
且任务级 payload.serial 整体替换流程默认（字段级不合并）——UI 建的任务永远
读不到流程里配好的 branches，多线推演只能靠手拼 API（22 时班 C/G 专项实录
缺口）。本文件锁定补齐后的契约：index.html 输入框、app.js 两处 payload 接线
（与 variants 同口径 1-3 钳位）、i18n EN 键，以及 create_task 的 branches 钳位。
"""
from __future__ import annotations

import io
import os
import re

from base import BaseTest

_UI = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                   "app", "ui")

_BRANCH_PH = "1 = 关闭（2-3 写前剧情分支推演择优）"


def _read(name):
    with io.open(os.path.join(_UI, name), encoding="utf-8") as f:
        return f.read()


class BranchesUiContractTests(BaseTest):
    """前端接线契约：输入框在位、payload 钳位口径与后端一致（1-3）。"""

    def test_task_form_has_branches_input(self):
        """任务表单连载区块有 f-branches 输入框（1-3 + i18n 属性齐全）。"""
        html = _read("index.html")
        m = re.search(r'<input id="f-branches"[^>]*>', html)
        self.assertIsNotNone(m, "f-branches 输入框缺失")
        tag = m.group(0)
        self.assertIn('type="number"', tag)
        self.assertIn('min="1"', tag)
        self.assertIn('max="3"', tag)
        self.assertIn('data-i18n="多线剧情推演数"', html)
        self.assertIn('data-i18n-ph="%s"' % _BRANCH_PH, html)

    def test_task_payload_wires_branches_clamp(self):
        """任务表单与流程编辑器 payload 都带 branches（1-3 钳位同 variants）。"""
        src = _read("app.js")
        self.assertIn(
            'branches: Math.max(1, Math.min(3, '
            'parseInt($("f-branches").value, 10) || 1)),', src)
        self.assertIn('id="fl-branches"', src)
        self.assertIn(
            'branches: Math.max(1, Math.min(3, '
            'parseInt($("fl-branches").value, 10) || 1)),', src)
        # 切换流程清空 + 任务详情回填两路径都认得新字段
        self.assertIn('"f-branches"', src)
        self.assertIn(
            '$("f-branches").value = tk.serial && tk.serial.branches', src)

    def test_i18n_en_keys_for_branches(self):
        """两个新文案键都有非空 EN 译（英文界面不再静默回落中文）。"""
        en = _read("i18n.js")
        for key in ("多线剧情推演数", _BRANCH_PH):
            self.assertRegex(en, r'"%s"\s*:\s*"[^"]+"' % re.escape(key))


class CreateTaskBranchesTests(BaseTest):
    """后端旧行为锁定：create_task 的 branches 钳位（1-3，1=不启用不落键）。"""

    def _create(self, branches):
        from app.core import store
        serial = {"chapters": 3, "words_per_chapter": 1000}
        if branches is not None:
            serial["branches"] = branches
        return store.create_task({
            "type": "serial_novel",
            "goal": "branches 钳位回归",
            "workdir": str(self.workdir),
            "serial": serial,
        })

    def test_branches_pass_through(self):
        """branches=2 原样落任务（UI 新通路打开后端既有能力）。"""
        got = self._create(2)
        self.assertEqual(got["serial"].get("branches"), 2)

    def test_branches_clamp_out_of_range(self):
        """branches=9 越界钳到 3（与 variants 同口径）。"""
        got = self._create(9)
        self.assertEqual(got["serial"].get("branches"), 3)

    def test_branches_off_omits_key(self):
        """branches=1/缺省（不启用）不落键——与 store.create_task 口径一致。"""
        self.assertNotIn("branches", self._create(1)["serial"])
        self.assertNotIn("branches", self._create(None)["serial"])


if __name__ == "__main__":
    import unittest
    unittest.main()
