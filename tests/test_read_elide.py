# -*- coding: utf-8 -*-
"""read_file 头尾保留中段省略（TokenJuice 借鉴）单测。

跑法：python -m unittest discover -s tests -p "test_read_elide.py" -v
"""
from __future__ import annotations

from base import BaseTest


class ReadElideTests(BaseTest):
    def _big(self, name, marker_head, marker_tail, size=120 * 1024):
        p = self.workdir / name
        mid = "x" * size
        body = marker_head + "\n" + mid + "\n" + marker_tail
        p.write_bytes(body.encode("utf-8"))   # 字节精确写入，避开 Windows CRLF 转换
        return p, len(body.encode("utf-8"))

    def test_head_and_tail_kept_middle_elided(self):
        from app.core import builtin_agent as ba
        p, total = self._big("big.log", "HEADLINE-START", "ERROR: tail-crash")
        out = ba._tool_read_file(str(self.workdir), {"path": "big.log"})
        self.assertIn("HEADLINE-START", out)          # 头保留
        self.assertIn("ERROR: tail-crash", out)       # 尾保留（旧实现纯截头会丢）
        self.assertIn("中段省略", out)                 # 省略标注
        self.assertLess(len(out), 70 * 1024)          # 输出仍受 64k 级预算约束

    def test_small_file_unchanged(self):
        from app.core import builtin_agent as ba
        (self.workdir / "s.txt").write_bytes("短文件\n第二行".encode("utf-8"))
        out = ba._tool_read_file(str(self.workdir), {"path": "s.txt"})
        self.assertEqual(out, "短文件\n第二行")        # 无截断无标注

    def test_chinese_tail_not_garbled(self):
        """尾段从多字节中间起读时剥残缺头，中文不乱码。"""
        from app.core import builtin_agent as ba
        p, _ = self._big("cn.log", "A", "错误：中文结论在最后", size=80 * 1024)
        out = ba._tool_read_file(str(self.workdir), {"path": "cn.log"})
        self.assertIn("错误：中文结论在最后", out)

    def test_path_guard_still_enforced(self):
        from app.core import builtin_agent as ba
        self.assertIn("非法路径", ba._tool_read_file(str(self.workdir),
                                                     {"path": "../etc/passwd"}))
        self.assertIn("文件不存在", ba._tool_read_file(str(self.workdir),
                                                       {"path": "nope.txt"}))


if __name__ == "__main__":
    import unittest
    unittest.main()
