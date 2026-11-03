# -*- coding: utf-8 -*-
"""clean_cli_text 快速短路回归：短路必须与原实现逐字节一致。

背景（2026-11-03 优化点③）：为缩短冷启动（读几百个 run + 全量清洗）给
clean_cli_text 加了「干净文本快速短路」。第一版漏了 `\n{4,}` 连续换行折叠，
导致含 4+ 连续换行的文本短路后不再折叠成 3 个（行为回归，复查时抓到）。
这里把原实现（无短路）与现实现逐一比对，任何清洗特征漏网都会红。
"""
from __future__ import annotations

import re
import unittest

from app.core import cli_text


def _orig_clean(text):
    """无短路的原实现逻辑（内联复刻，避免依赖当前函数）。"""
    if not text:
        return ""
    out = cli_text.strip_ansi(text).replace("\r\n", "\n")
    if "\r" in out:
        lines = []
        for ln in out.split("\n"):
            if "\r" in ln:
                parts = ln.split("\r")
                ln = next((p for p in reversed(parts) if p.strip()), parts[-1])
            lines.append(ln)
        out = "\n".join(lines)
    out = cli_text._CTRL_RE.sub("", out)
    out = cli_text._FFFD_RUN_RE.sub(
        lambda m: "…（%d 个字符无法解码：CLI 输出不是 UTF-8/GBK）" % len(m.group(0)), out)
    out = cli_text._NOISE_RUN_RE.sub(lambda m: m.group(1) * 3 + "…", out)
    return re.sub(r"\n{4,}", "\n\n\n", out)


class CleanCliTextShortCircuitTests(unittest.TestCase):
    def _assert_equivalent(self, text):
        self.assertEqual(cli_text.clean_cli_text(text), _orig_clean(text),
                         "快速短路与原实现输出不一致：%r" % text)

    def test_clean_texts_bypass_unchanged(self):
        for text in ("普通干净中文", "多行\n换行\n正常", "[2026]时间戳", ""):
            self._assert_equivalent(text)

    def test_ansi_escape_cleaned(self):
        self._assert_equivalent("\x1b[91mError:\x1b[0m 报错")

    def test_carriage_return_overwrite_cleaned(self):
        self._assert_equivalent("a\rb 覆写")

    def test_fffd_mojibake_collapsed(self):
        self._assert_equivalent("a\ufffd\ufffd\ufffdb")

    def test_bare_ansi_tail_cleaned(self):
        self._assert_equivalent("[91mError: 裸残尾")

    def test_control_char_removed(self):
        self._assert_equivalent("a\x00b 控制字符")

    def test_noise_glyph_run_collapsed(self):
        self._assert_equivalent("──────连续图元".replace("──", "─" * 8))

    def test_extra_newlines_still_collapsed(self):
        # 关键回归：4+ 连续换行必须仍折叠成 3（第一版短路漏过这个特征）。
        for text in ("有换行\n\n\n\n\n超多", "四换行\n\n\n\n 边界", "结尾\n\n\n\n"):
            self._assert_equivalent(text)


if __name__ == "__main__":
    unittest.main()