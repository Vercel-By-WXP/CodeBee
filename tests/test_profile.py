# -*- coding: utf-8 -*-
"""产品形态（profile）单测。

只测纯逻辑：不读用户数据目录、不启动服务、不调用任何 CLI。
"""
from __future__ import annotations

import os
import unittest

from core import profile


class ProfileDefaultTest(unittest.TestCase):
    def setUp(self):
        self._saved = os.environ.pop(profile.ENV_VAR, None)
        profile.reset()

    def tearDown(self):
        profile.reset()
        os.environ.pop(profile.ENV_VAR, None)
        if self._saved is not None:
            os.environ[profile.ENV_VAR] = self._saved

    def test_default_when_nothing_set(self):
        self.assertEqual(profile.active(), profile.DEFAULT)
        self.assertEqual(profile.ui_entry(), "")

    def test_default_keeps_all_flows(self):
        rows = [{"id": "code"}, {"id": "novel"}, {"id": "bid_doc"}]
        self.assertEqual(profile.filter_flows(rows), rows)

    def test_unknown_profile_falls_back(self):
        self.assertEqual(profile.set_active("does-not-exist"), profile.DEFAULT)
        self.assertEqual(profile.active(), profile.DEFAULT)

    def test_env_var_selects_profile(self):
        os.environ[profile.ENV_VAR] = "novel"
        profile.reset()
        self.assertEqual(profile.active(), "novel")

    def test_explicit_set_beats_env(self):
        os.environ[profile.ENV_VAR] = "novel"
        profile.reset()
        profile.set_active("default")
        self.assertEqual(profile.active(), "default")


class ProfileNovelTest(unittest.TestCase):
    def setUp(self):
        self._saved = os.environ.pop(profile.ENV_VAR, None)
        profile.set_active("novel")

    def tearDown(self):
        profile.reset()
        os.environ.pop(profile.ENV_VAR, None)
        if self._saved is not None:
            os.environ[profile.ENV_VAR] = self._saved

    def test_flows_are_narrowed_in_order(self):
        rows = [
            {"id": "code"}, {"id": "novel"}, {"id": "bid_doc"},
            {"id": "short_story"}, {"id": "serial_novel"}, {"id": "rank_scan"},
        ]
        got = [row["id"] for row in profile.filter_flows(rows)]
        self.assertEqual(got, ["novel", "short_story", "serial_novel", "rank_scan"])

    def test_flow_allowed(self):
        self.assertTrue(profile.flow_allowed("short_story"))
        self.assertFalse(profile.flow_allowed("code"))
        self.assertFalse(profile.flow_allowed(""))

    def test_ui_entry(self):
        # v2 独立页面（novel/index.html 等）已整体删除，形态不再有专属入口页，
        # 空串＝走统一工作台。
        self.assertEqual(profile.ui_entry(), "")

    def test_features_hide_code_surfaces(self):
        features = profile.current()["features"]
        self.assertFalse(features["code_tasks"])
        self.assertFalse(features["git_workbench"])
        self.assertTrue(features["publish"])

    def test_describe_is_serializable_and_detached(self):
        payload = profile.describe()
        self.assertEqual(payload["id"], "novel")
        self.assertIsInstance(payload["flows"], list)
        # 改返回值不能污染注册表
        payload["flows"].append("code")
        payload["features"]["code_tasks"] = True
        self.assertNotIn("code", profile.current()["flows"])
        self.assertFalse(profile.current()["features"]["code_tasks"])

    def test_flow_whitelist_matches_builtin_flows(self):
        """白名单里的每个 id 都必须真的存在于 flows 注册表，否则界面会空白。"""
        from core import flows
        existing = {row["id"] for row in flows.list_flows()}
        for flow_id in profile.current()["flows"]:
            self.assertIn(flow_id, existing, "profile 引用了不存在的流程 %s" % flow_id)


if __name__ == "__main__":
    unittest.main()
