# -*- coding: utf-8 -*-
"""内置智能体 P0 基础件工具回归：局部编辑、追加、文件管理、分段读取、全文搜索。

2026-09-25 用户实测反馈（codebee 功能增强建议 P0）：改大文件只能整份重写、
删/移/改名只能绕 shell、超 64KB 中段被截、找内容只能逐文件读——四件补齐后
逐项验收；路径守卫（工作目录锁死）与 schema 可选参数一并覆盖。
"""
import os
import tempfile
import unittest
from pathlib import Path

from base import BaseTest


class BuiltinToolTest(BaseTest):
    def setUp(self):
        super().setUp()
        self.workdir = tempfile.mkdtemp(prefix="tutti_tools_")
        self.wd = str(self.workdir)

    def _call(self, name, **args):
        from app.core import builtin_agent as BA
        return BA._exec_tool(self.wd, name, args)

    def _write(self, rel, text, encoding="utf-8"):
        p = Path(self.wd, rel)
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(text, encoding=encoding)
        return p


class TestEditFile(BuiltinToolTest):
    def test_single_hit_replaces(self):
        self._write("a.txt", "hello world\nsecond line\n")
        out = self._call("edit_file", path="a.txt",
                         old_text="world", new_text="there")
        self.assertIn("已替换 1 处", out)
        self.assertIn("hello there", Path(self.wd, "a.txt").read_text(encoding="utf-8"))

    def test_miss_reports_and_hints_segmented_read(self):
        self._write("a.txt", "abc")
        out = self._call("edit_file", path="a.txt",
                         old_text="xyz", new_text="q")
        self.assertIn("找不到逐字符匹配", out)
        self.assertIn("offset/lines", out)

    def test_multi_hit_requires_disambiguation(self):
        self._write("a.txt", "x\nx\nx\n")
        out = self._call("edit_file", path="a.txt", old_text="x", new_text="y")
        self.assertIn("命中 3 处", out)
        self.assertEqual(Path(self.wd, "a.txt").read_text(encoding="utf-8"), "x\nx\nx\n")
        out2 = self._call("edit_file", path="a.txt", old_text="x", new_text="y",
                          replace_all="true")
        self.assertIn("已替换 3 处", out2)
        self.assertEqual(Path(self.wd, "a.txt").read_text(encoding="utf-8"), "y\ny\ny\n")

    def test_crlf_file_keeps_line_ending_style(self):
        p = self._write("win.txt", "line one\r\nline two\r\n")
        out = self._call("edit_file", path="win.txt",
                         old_text="line two", new_text="line 2")
        self.assertIn("已替换 1 处", out)
        raw = p.read_bytes().decode("utf-8")
        self.assertIn("line 2", raw)
        self.assertIn("\r\n", raw)          # CRLF 风格未被翻转
        self.assertNotIn("line two", raw)

    def test_binary_rejected_and_empty_old_text_rejected(self):
        p = Path(self.wd, "img.bin")
        p.write_bytes(b"\x89PNG\r\n\x1a\n" + b"\x00" * 64)
        self.assertIn("二进制", self._call("edit_file", path="img.bin",
                                            old_text="a", new_text="b"))
        self._write("a.txt", "abc")
        self.assertIn("old_text 不能为空",
                      self._call("edit_file", path="a.txt",
                                 old_text="", new_text="b"))

    def test_path_guard(self):
        self.assertIn("非法路径", self._call("edit_file", path="../a.txt",
                                              old_text="a", new_text="b"))
        self.assertIn("不存在", self._call("edit_file", path="nope.txt",
                                            old_text="a", new_text="b"))


class TestAppendFile(BuiltinToolTest):
    def test_create_then_append(self):
        out = self._call("append_file", path="logs/app.log", content="line1\n")
        self.assertIn("已追加 6 字符", out)
        out2 = self._call("append_file", path="logs/app.log", content="line2\n")
        self.assertIn("已追加 6 字符", out2)
        self.assertEqual(Path(self.wd, "logs/app.log").read_text(encoding="utf-8"),
                         "line1\nline2\n")

    def test_binary_rejected_empty_rejected(self):
        p = Path(self.wd, "img.bin")
        p.write_bytes(b"\xff\xd8\xff" + b"\x00" * 64)
        self.assertIn("二进制", self._call("append_file", path="img.bin", content="x"))
        self.assertIn("content 不能为空",
                      self._call("append_file", path="a.txt", content=""))


class TestFsManage(BuiltinToolTest):
    def test_delete_file_and_dir(self):
        self._write("a.txt", "x")
        self._write("d/sub/b.txt", "y")
        self.assertIn("已删除", self._call("fs_manage", action="delete", path="a.txt"))
        self.assertFalse(Path(self.wd, "a.txt").exists())
        self.assertIn("已删除目录", self._call("fs_manage", action="delete", path="d"))
        self.assertFalse(Path(self.wd, "d").exists())

    def test_move_rename_copy(self):
        self._write("a.txt", "data")
        self.assertIn("已重命名", self._call("fs_manage", action="rename",
                                              path="a.txt", dest="b.txt"))
        self.assertIn("已移动", self._call("fs_manage", action="move",
                                            path="b.txt", dest="sub/b.txt"))
        self.assertIn("已复制", self._call("fs_manage", action="copy",
                                            path="sub/b.txt", dest="c.txt"))
        self.assertFalse(Path(self.wd, "a.txt").exists())
        self.assertTrue(Path(self.wd, "sub/b.txt").exists())
        self.assertEqual(Path(self.wd, "c.txt").read_text(encoding="utf-8"), "data")

    def test_dest_exists_rejected(self):
        self._write("a.txt", "1")
        self._write("b.txt", "2")
        out = self._call("fs_manage", action="copy", path="a.txt", dest="b.txt")
        self.assertIn("拒绝覆盖", out)
        self.assertEqual(Path(self.wd, "b.txt").read_text(encoding="utf-8"), "2")

    def test_bad_action_and_guard(self):
        self.assertIn("action 须为", self._call("fs_manage", action="chmod", path="a"))
        self.assertIn("不存在", self._call("fs_manage", action="delete", path="ghost.txt"))
        self.assertIn("非法路径", self._call("fs_manage", action="delete", path="../x"))


class TestSearchContent(BuiltinToolTest):
    def test_regex_hit_with_line_numbers(self):
        self._write("src/a.py", "import os\ndef handle():\n    return PRICE + 1\n")
        self._write("src/b.py", "PRICE = 3\n")
        out = self._call("search_content", pattern=r"PRICE\s*\+")
        self.assertIn("src/a.py:3:", out)
        self.assertIn("共 1 行命中", out)
        out2 = self._call("search_content", pattern="PRICE")
        self.assertIn("src/a.py:3:", out2)
        self.assertIn("src/b.py:1:", out2)

    def test_glob_and_subdir_filters(self):
        self._write("src/a.py", "needle here\n")
        self._write("src/a.md", "needle too\n")
        out = self._call("search_content", pattern="needle", glob="*.py")
        self.assertIn("src/a.py:1:", out)
        self.assertNotIn("a.md", out)
        out2 = self._call("search_content", pattern="needle", path="src")
        self.assertIn("needle", out2)
        self.assertIn("非法路径", self._call("search_content", pattern="x", path="../out"))

    def test_invalid_regex_falls_back_to_literal(self):
        self._write("a.txt", "cost (CNY) 5\n")
        out = self._call("search_content", pattern="(CNY")   # 未闭合括号=非法正则
        self.assertIn("字面量", out)
        self.assertIn("a.txt:1:", out)

    def test_binary_and_big_files_skipped(self):
        Path(self.wd, "img.bin").write_bytes(b"\x89PNG\r\n\x1a\n" + b"needle" + b"\x00" * 32)
        big = Path(self.wd, "big.txt")
        big.write_text("needle\n" * 10, encoding="utf-8")
        big.write_bytes(b"x" * (2 * 1024 * 1024 + 1) + b"\nneedle\n")   # 超 2MB
        out = self._call("search_content", pattern="needle")
        self.assertNotIn("img.bin", out)
        self.assertNotIn("big.txt", out)
        self.assertIn("无命中", out)

    def test_max_results_cap(self):
        self._write("a.txt", "hit\n" * 30)
        out = self._call("search_content", pattern="hit", max_results="5")
        self.assertIn("已达 5 行上限", out)


class TestFindFiles(BuiltinToolTest):
    def test_name_and_path_patterns(self):
        self._write("src/mod/main.py", "x")
        self._write("docs/readme.md", "y")
        out = self._call("find_files", pattern="*.py")
        self.assertIn("src/mod/main.py", out)
        out2 = self._call("find_files", pattern="docs/*.md")
        self.assertIn("docs/readme.md", out2)
        self.assertIn("无匹配", self._call("find_files", pattern="*.xyz"))
        self.assertIn("非法 pattern", self._call("find_files", pattern="../x"))


class TestReadSegment(BuiltinToolTest):
    def test_segmented_read_with_range_header(self):
        body = "\n".join("line %d" % i for i in range(1, 101))
        self._write("big.txt", body)
        out = self._call("read_file", path="big.txt", offset="40", lines="10")
        self.assertIn("第 40–49 行", out)
        self.assertIn("共 100 行", out)
        self.assertIn("offset=50 续读", out)
        self.assertIn("line 45", out)
        self.assertNotIn("line 39\n", out)

    def test_offset_beyond_total(self):
        self._write("a.txt", "one\ntwo\n")
        self.assertIn("超出文件总行数 2", self._call("read_file", path="a.txt",
                                                     offset="9"))
        # 末段续读提示 offset 超界时仍是有效输出
        out = self._call("read_file", path="a.txt", offset="2", lines="10")
        self.assertIn("第 2–2 行", out)
        self.assertIn("two", out)

    def test_default_behavior_unchanged_for_small_file(self):
        self._write("a.txt", "hello\n")
        out = self._call("read_file", path="a.txt")
        self.assertEqual(out.strip(), "hello")


class TestToolSchema(BuiltinToolTest):
    def test_optional_args_drop_from_required(self):
        from app.core import builtin_agent as BA
        props, required = BA._split_tool_args(
            {"name": "x", "args": {"path": "必填", "?limit": "可选"}})
        self.assertEqual(required, ["path"])
        self.assertIn("limit", props)
        self.assertNotIn("?limit", props)
        # 两面 schema 都过 optional 通道且工具齐全
        o = BA._openai_tools()
        a = BA._anthropic_tools()
        self.assertEqual({t["function"]["name"] for t in o},
                         {t["name"] for t in BA.TOOLS_SPEC})
        self.assertEqual({t["name"] for t in a},
                         {t["name"] for t in BA.TOOLS_SPEC})
        rd = [t for t in o if t["function"]["name"] == "read_file"][0]
        self.assertEqual(rd["function"]["parameters"]["required"], ["path"])
        fm = [t for t in a if t["name"] == "fs_manage"][0]
        self.assertEqual(fm["input_schema"]["required"], ["action", "path"])


if __name__ == "__main__":
    unittest.main()
