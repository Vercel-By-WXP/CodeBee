# -*- coding: utf-8 -*-
"""最少代码经验包（minimal-code）注册与 scope 隔离回归。

DietrichGebert/ponytail（MIT）方法论蒸馏（2026-10-22）：包只挂 code scope，
小说链不得被注入；本文件锁定登记条目、注入命中与隔离、红线内容在包。
TUTTI_DATA 在 import 前指向临时目录：block_for 会读写 skills.json，
不碰生产数据。
"""
import os
import tempfile
import unittest

os.environ["TUTTI_DATA"] = tempfile.mkdtemp(prefix="minimal-code-pack-")

from app.core import skills


def _entry():
    return next(p for p in skills.BUILTIN_PACKS if p["id"] == "minimal-code")


class MinimalCodePackTest(unittest.TestCase):
    def test_registered_with_code_scope_only(self):
        entry = _entry()
        self.assertEqual(entry["file"], "minimal-code.md")
        self.assertEqual(entry["scopes"], ["code"])

    def test_pack_file_exists_with_upstream_attribution(self):
        text = skills.pack_text(_entry())
        self.assertTrue(text.strip())
        self.assertIn("ponytail", text)
        self.assertIn("MIT", text)

    def test_block_for_code_task_hits_pack(self):
        text, used = skills.block_for(
            {"type": "code", "title": "回归", "goal": "验证"})
        self.assertIn("minimal-code", used)
        self.assertIn("最少代码", text)

    def test_block_for_serial_novel_skips_pack(self):
        text, used = skills.block_for(
            {"type": "serial_novel", "title": "回归", "goal": "验证"})
        self.assertNotIn("minimal-code", used)
        self.assertNotIn("最少代码工程纪律", text)

    def test_never_cut_red_line_stays_in_pack(self):
        text = skills.pack_text(_entry())
        for kw in ("永不省", "输入校验", "错误处理", "安全"):
            self.assertIn(kw, text)


if __name__ == "__main__":
    unittest.main()
