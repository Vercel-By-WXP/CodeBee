# -*- coding: utf-8 -*-
"""专家导出/导入 Markdown（expert_md）回归。

核心语义（2026-11-01，借鉴 OpenMausBot bot 团队 MD 导入导出）：
1. 导出 = frontmatter（name/flow/focus/.../gates/hidden）+ 自由正文；
2. 导入 = frontmatter 解析 → data/agents/<flow>.json（用户覆盖层）；
3. flow 缺省按 name 生成 slug（CJK 保留）；
4. 撞名不覆盖除非 overwrite=True；
5. 敏感/内部字段（seats/builtin/panels）不进导出——凭据不出机器；
6. 回告 flow_exists（能否真起任务取决于 flows）。

数据目录走 _TempData 夹具（同 test_agents_registry 模式），不碰真实数据。
"""
from __future__ import annotations

import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from core import agents, expert_md, flows


class _TempData(unittest.TestCase):
    def setUp(self):
        self._dir = tempfile.TemporaryDirectory()
        self.root = Path(self._dir.name)
        self._saved = (agents._agents_dir, agents._CACHE.copy())
        self._saved_flows = None
        agents._agents_dir = lambda: self.root / "agents"
        agents._CACHE.update({"sig": None, "data": None})

    def tearDown(self):
        (agents._agents_dir, cache) = self._saved
        agents._CACHE.update(cache)
        self._dir.cleanup()

    def _flow(self, fid="serial_novel"):
        fake = {"id": fid, "name": "连载家", "type": fid}
        patcher = mock.patch.object(flows, "get_flow", return_value=fake)
        patcher.start()
        self.addCleanup(patcher.stop)
        return fake


class ExportTests(_TempData):
    def test_export_unregistered_returns_none(self):
        self.assertIsNone(expert_md.export_md("no-such-flow"))

    def test_export_contains_frontmatter_and_no_seats(self):
        spec = {"flow": "serial_novel", "name": "连载家",
                "focus": "长篇连载的章节起草与评审",
                "archetype": "studio", "surface": "p", "panels": ["a"],
                "metric": "评审均分", "deliverable": "章稿",
                "primary_action": "开一个任务", "seats": {"impl": "secret"},
                "gates": ["字数闸"], "hidden": False, "builtin": True}
        with mock.patch.object(agents, "get", return_value=spec):
            md = expert_md.export_md("serial_novel")
        self.assertTrue(md.startswith("---\n"))
        self.assertIn("name: 连载家", md)
        self.assertIn("flow: serial_novel", md)
        self.assertIn('gates: ["字数闸"]', md)
        # 敏感/内部字段不导出
        self.assertNotIn("seats", md)
        self.assertNotIn("secret", md)
        self.assertNotIn("panels", md)
        self.assertNotIn("builtin", md)


class ParseTests(unittest.TestCase):
    def test_parse_roundtrip(self):
        md = "---\nname: 译匠\nflow: translation\ngates: [\"a\", \"b\"]\nhidden: false\n---\n\n正文说明\n"
        meta, body = expert_md.parse_frontmatter(md)
        self.assertEqual(meta["name"], "译匠")
        self.assertEqual(meta["flow"], "translation")
        self.assertEqual(meta["gates"], ["a", "b"])
        self.assertFalse(meta["hidden"])
        self.assertIn("正文说明", body)

    def test_parse_no_frontmatter(self):
        meta, body = expert_md.parse_frontmatter("纯正文")
        self.assertEqual(meta, {})
        self.assertIn("纯正文", body)

    def test_parse_unclosed_fence(self):
        meta, _ = expert_md.parse_frontmatter("---\nname: x\n")
        self.assertEqual(meta, {})


class ImportTests(_TempData):
    def test_import_writes_override_file(self):
        self._flow("serial_novel")
        md = ("---\nname: 连载家二号\nflow: serial_novel\n"
              "focus: 更狠的连载数度\n---\n\n人设说明。\n")
        path, info = expert_md.import_md(md)
        self.assertEqual(info["flow"], "serial_novel")
        self.assertTrue(info["flow_exists"])
        self.assertFalse(info["overwritten"])
        raw = json.loads(Path(path).read_text(encoding="utf-8"))
        self.assertEqual(raw["name"], "连载家二号")
        self.assertEqual(raw["note"], "人设说明。")
        # 覆盖层生效：registry 能读到
        agents._CACHE.update({"sig": None, "data": None})
        self.assertEqual(agents.get("serial_novel")["name"], "连载家二号")

    def test_import_slug_from_name(self):
        with mock.patch.object(flows, "get_flow", return_value=None):
            path, info = expert_md.import_md("---\nname: 我的翻译专家\n---\n")
        self.assertEqual(info["flow"], "我的翻译专家")
        self.assertFalse(info["flow_exists"])
        self.assertTrue(Path(path).exists())

    def test_import_conflict_requires_overwrite(self):
        md = "---\nname: 甲\nflow: f-1\n---\n"
        expert_md.import_md(md)
        with self.assertRaises(ValueError):
            expert_md.import_md("---\nname: 乙\nflow: f-1\n---\n")
        _path, info = expert_md.import_md("---\nname: 乙\nflow: f-1\n---\n",
                                          overwrite=True)
        self.assertTrue(info["overwritten"])
        self.assertEqual(json.loads(
            (self.root / "agents" / "f-1.json").read_text(encoding="utf-8"))["name"], "乙")

    def test_import_requires_name(self):
        with self.assertRaises(ValueError):
            expert_md.import_md("---\nflow: x\n---\n")

    def test_import_flow_id_sanitized(self):
        with mock.patch.object(flows, "get_flow", return_value=None):
            _p, info = expert_md.import_md(
                "---\nname: X\nflow: bad/slash:name\n---\n")
        self.assertNotIn("/", info["flow"])
        self.assertNotIn(":", info["flow"])


if __name__ == "__main__":
    unittest.main()
