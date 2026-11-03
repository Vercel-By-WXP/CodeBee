# -*- coding: utf-8 -*-
"""欢迎页（形态选择器）的单测。

覆盖三条决策：什么时候给选择页、切换怎么落盘、清单里要带哪些字段。
不启动服务、不调用 CLI；数据目录指向临时目录，不碰真实 state。
"""
from __future__ import annotations

import json
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
        """超时是「没人回答」，不是「选了默认」——不能写进 state。

        2026-11-03 菜单收敛后默认只有一个形态，不再走交互菜单，所以这条
        用一个临时自建形态把清单撑回两个，专门保住「超时不落 state」的语义。
        """
        d = paths.DATA_DIR / "profiles"
        d.mkdir(parents=True, exist_ok=True)
        (d / "custom.json").write_text(
            json.dumps({"name": "自定义版", "flows": ["novel"]}, ensure_ascii=False),
            encoding="utf-8")
        profile.reset()
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

    def test_single_profile_skips_menu_entirely(self):
        """2026-11-03 收敛：菜单只剩一个选项时不弹、不问、不等 12 秒。"""
        applied, why = profile.select("", allow_prompt=True, quiet=True)
        self.assertEqual(applied, profile.DEFAULT)
        self.assertEqual(why, "default")
        self.assertFalse(profile.state_file().exists(),
                         "没选过任何东西就不写 state")

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
        # 2026-11-03 菜单收敛：欢迎页清单（describe_all）只列 registry 里的
        # 形态，legacy（novel 等）不再出现——但 last 记录仍能解析。
        profile.switch_to("novel")
        data = profile.describe_all()
        self.assertEqual(data["last"], "novel")
        self.assertEqual(data["active"], "novel",
                         "当前生效形态照实报，即使它是 legacy")
        rows = {r["id"]: r for r in data["profiles"]}
        self.assertEqual(list(rows), ["default"],
                         "菜单收敛后欢迎页只应有全量版一张卡")

    def test_legacy_flow_names_still_resolvable(self):
        """legacy 形态本身的定义还能查（spec），不再靠欢迎页清单。"""
        spec = profile.spec("novel")
        self.assertEqual(len(spec["flows"]), 4)
        self.assertIn("novel", spec["flows"])

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
