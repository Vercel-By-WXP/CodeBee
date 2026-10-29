# -*- coding: utf-8 -*-
"""欢迎页（形态选择器）的单测。

覆盖三条决策：什么时候给选择页、切换怎么落盘、清单里要带哪些字段。
不启动服务、不调用 CLI；数据目录指向临时目录，不碰真实 state。
"""
from __future__ import annotations

import os
import tempfile
import unittest
from pathlib import Path

from core import paths, profile


class ChooserTestBase(unittest.TestCase):
    def setUp(self):
        self._saved_env = os.environ.pop(profile.ENV_VAR, None)
        self._saved_dir = paths.DATA_DIR
        self._tmp = tempfile.TemporaryDirectory()
        # state_file() 每次读 paths.DATA_DIR，替换即可隔离 profile_state.json
        paths.DATA_DIR = Path(self._tmp.name)
        profile.reset()

    def tearDown(self):
        profile.reset()
        paths.DATA_DIR = self._saved_dir
        self._tmp.cleanup()
        os.environ.pop(profile.ENV_VAR, None)
        if self._saved_env is not None:
            os.environ[profile.ENV_VAR] = self._saved_env


class NeedsChooserTest(ChooserTestBase):
    def test_no_source_means_already_decided(self):
        """没走过 select()（库调用、矩阵测试）不该弹选择页。"""
        self.assertEqual(profile.source(), "")
        self.assertFalse(profile.needs_chooser())

    def test_first_boot_needs_chooser(self):
        applied, why = profile.select("", allow_prompt=False)
        self.assertEqual(applied, profile.DEFAULT)
        self.assertEqual(why, "default")
        self.assertTrue(profile.needs_chooser())

    def test_explicit_cli_skips_chooser(self):
        applied, why = profile.select("novel", allow_prompt=False)
        self.assertEqual((applied, why), ("novel", "cli"))
        self.assertFalse(profile.needs_chooser())

    def test_env_skips_chooser(self):
        os.environ[profile.ENV_VAR] = "dossier"
        profile.reset()
        applied, why = profile.select("", allow_prompt=False)
        self.assertEqual((applied, why), ("dossier", "env"))
        self.assertFalse(profile.needs_chooser())

    def test_prompt_timeout_still_needs_chooser(self):
        """超时是「没人回答」，不是「选了默认」——不能写进 state。"""
        orig = profile._ask
        profile._ask = lambda text, timeout_s: None
        try:
            applied, why = profile.select("", allow_prompt=True, quiet=True)
        finally:
            profile._ask = orig
        self.assertEqual(applied, profile.DEFAULT)
        self.assertEqual(why, "prompt-timeout")
        self.assertFalse(profile.state_file().exists(),
                         "超时不该写 profile_state.json")
        self.assertTrue(profile.needs_chooser())

    def test_last_choice_skips_chooser(self):
        profile.remember("novel")
        profile.reset()
        applied, why = profile.select("", allow_prompt=False)
        self.assertEqual((applied, why), ("novel", "last"))
        self.assertFalse(profile.needs_chooser())

    def test_last_choice_default_also_skips(self):
        """在欢迎页选了全量版（default）也要记住，否则下次又被送回选择页。"""
        profile.remember("default")
        profile.reset()
        applied, why = profile.select("", allow_prompt=False)
        self.assertEqual(applied, profile.DEFAULT)
        self.assertEqual(why, "last")
        self.assertFalse(profile.needs_chooser())


class SwitchToTest(ChooserTestBase):
    def test_switch_changes_entry_and_remembers(self):
        self.assertTrue(profile.switch_to("novel"))
        self.assertEqual(profile.active(), "novel")
        self.assertEqual(profile.ui_entry(), "")
        self.assertEqual(profile.source(), "ui")
        self.assertEqual(profile.last_choice(), "novel")

    def test_switch_is_not_reverted_by_restart(self):
        profile.switch_to("dossier")
        profile.reset()
        applied, why = profile.select("", allow_prompt=False)
        self.assertEqual((applied, why), ("dossier", "last"))

    def test_unknown_id_returns_empty_and_keeps_state(self):
        profile.switch_to("novel")
        self.assertEqual(profile.switch_to("nope"), "")
        self.assertEqual(profile.active(), "novel",
                         "非法切换不该把人踢回 default")

    def test_switch_to_default_is_allowed(self):
        profile.switch_to("novel")
        self.assertEqual(profile.switch_to("default"), "default")
        self.assertEqual(profile.ui_entry(), "")


class DescribeAllTest(ChooserTestBase):
    def test_rows_carry_last_and_names(self):
        profile.switch_to("novel")
        data = profile.describe_all()
        self.assertEqual(data["last"], "novel")
        rows = {r["id"]: r for r in data["profiles"]}
        self.assertTrue(rows["novel"]["active"])
        self.assertEqual(len(rows["novel"]["flow_names"]), 4)
        self.assertIn("小说", rows["novel"]["flow_names"])

    def test_unrestricted_profile_lists_every_flow(self):
        """flows=None（不收敛）也要给出具体工种名，卡片才有 chips 可显示。"""
        rows = {r["id"]: r for r in profile.describe_all()["profiles"]}
        names = rows["default"]["flow_names"]
        self.assertTrue(names, "default 应该列出全部工种")
        self.assertGreaterEqual(len(names), 20)
        self.assertGreaterEqual(rows["default"]["flow_count"], 20)

    def test_custom_profile_appears(self):
        """data/profiles/*.json 加一条，清单里就该多一行（欢迎页自动多一张卡）。"""
        d = profile.spec_dir()
        d.mkdir(parents=True, exist_ok=True)
        (d / "bid_only.json").write_text(
            '{"id": "bid_only", "name": "标书专用", "tagline": "只写标书",'
            ' "flows": ["bid_doc"], "order": 30}',
            encoding="utf-8")
        rows = {r["id"]: r for r in profile.describe_all()["profiles"]}
        self.assertIn("bid_only", rows)
        self.assertEqual(rows["bid_only"]["flow_count"], 1)


if __name__ == "__main__":
    unittest.main()
