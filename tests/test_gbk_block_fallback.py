# -*- coding: utf-8 -*-
"""子进程输出解码纪律回归：整块 GBK 回退废案（2026-10-16 pwsh 命令输出实案）。

16:29 连载 run 与 17:24 qa run 实证：命令输出块里混进一个非 UTF-8 字节
（rg/Get-Content 吐的杂字），旧逻辑「整块 GBK 回退」把纯 UTF-8 中文整块误
读成「鏍煎紡妫€」双重乱码写进日志与步骤摘要——评审维度名全灭
（"鎯呰妭"≠"情节"，normalize_scores 按名取分全落空）。

锁定行为：
1. 块里一个杂字只伤所在行，其余行保持可读（旧行为=全块灭）；
2. 纯 GBK 块仍可读（2026-09-20 aider 中文控制台实案不回归）；
3. 混合编码日志（UTF-8 审计头 + GBK 正文行）两边都保住；
4. tail_decoded 尾部对齐后同样走新纪律。
"""
from __future__ import annotations

from base import BaseTest


class TestDecodeOutputNoBlockGbk(BaseTest):
    def test_stray_byte_no_longer_poisons_block(self):
        from app.core import runner as R
        raw = ("【命令】第一章 格式检查通过\n".encode("utf-8")
               + b"\x96\n"
               + "【结论】达到发布标准，全书共三卷\n".encode("utf-8"))
        out = R.decode_output(raw)
        # 旧行为：整块 GBK 化，「第一行/结论」全部变「绗€涓...」双重乱码
        self.assertIn("格式检查通过", out)
        self.assertIn("达到发布标准，全书共三卷", out)

    def test_pure_gbk_block_still_readable(self):
        from app.core import runner as R
        out = R.decode_output("格式检查与投递建议".encode("gbk"))
        self.assertEqual(out, "格式检查与投递建议")

    def test_mixed_encoding_log_lines_survive(self):
        from app.core import runner as R
        raw = ("===== 下达 2026-10-16 =====\n".encode("utf-8")
               + "控制台GBK输出：评审判定通过\n".encode("gbk"))
        out = R.decode_output(raw)
        self.assertIn("===== 下达 2026-10-16 =====", out)
        self.assertIn("控制台GBK输出：评审判定通过", out)

    def test_tail_decoded_uses_line_discipline(self):
        from app.core import runner as R
        body = "中文正文行\n".encode("utf-8") * 10 + b"\x96"
        out = R.tail_decoded(body, 40)
        self.assertIn("中文正文行", out)
