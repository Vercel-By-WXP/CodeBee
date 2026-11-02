# -*- coding: utf-8 -*-
"""控制台子进程输出的解码守测（2026-11-02 用户「咋乱码了」实案）。

Windows 上 netstat / PowerShell 被重定向输出时，按**控制台代码页**编码
（简中机器 = 936/GBK），而服务端按 UTF-8 解 → 「微信开发者工具」变成
一排 `�` 方块，且整列进程名一起报废。

修法两层，缺一不可：
1. `_decode_console()`：先严格 UTF-8，失败回落本地代码页 / GBK，最后 replace
   兜底——宁可个别字损失，也不让整列变方块；
2. 调 PowerShell 时前置 `[Console]::OutputEncoding=UTF8`，让新机器直接吐
   UTF-8（老系统/混合语言环境下第 1 层仍是必需的安全网）。
"""

import json
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.core import portscan  # noqa: E402


class TestConsoleDecode(unittest.TestCase):

    def test_utf8_passthrough(self):
        self.assertEqual(portscan._decode_console("微信开发者工具".encode("utf-8")),
                         "微信开发者工具",
                         "UTF-8 输出必须原样返回（PowerShell 已钉 UTF8 的正常路径）")

    def test_gbk_falls_back_correctly(self):
        # 模拟 PowerShell 5.1 用 936 代码页吐的中文进程名
        raw = "微信开发者工具".encode("gbk")
        self.assertEqual(portscan._decode_console(raw), "微信开发者工具",
                         "GBK 输出没有回落解码：进程名会整列变成 \ufffd 方块")

    def test_no_replacement_chars_leak(self):
        for text in ("微信开发者工具", "企业微信", "腾讯会议", "CodeBee 编排台"):
            for enc in ("gbk", "utf-8"):
                out = portscan._decode_console(text.encode(enc))
                self.assertNotIn("\ufffd", out, "%s(%s) 解码后仍含替换字符" % (text, enc))

    def test_shorter_codepoints_roundtrip(self):
        # UTF-8 解 GBK 字节如果不报错会得到一串「合法但没意义」的乱码（mojibake），
        # 那比方块更难发现——这里确认 strict 解码会抛错、走回落分支。
        raw = "微信开发者工具".encode("gbk")
        with self.assertRaises(UnicodeDecodeError):
            raw.decode("utf-8")
        self.assertEqual(portscan._decode_console(raw).encode("utf-8"),
                         "微信开发者工具".encode("utf-8"))

    def test_empty_and_garbage_are_safe(self):
        self.assertEqual(portscan._decode_console(b""), "")
        self.assertEqual(portscan._decode_console(None), "")
        # 无法解码的字节流不能抛异常（扫描接口会被单条坏数据整个打断）
        self.assertIsInstance(portscan._decode_console(b"\xff\xfe\x81\x00"), str)

    def test_powershell_command_pins_utf8(self):
        src = open(portscan.__file__, "r", encoding="utf-8").read()
        self.assertIn("[Console]::OutputEncoding=[System.Text.Encoding]::UTF8", src,
                      "PowerShell 调用丢了 OutputEncoding 钉定：中文进程名会重新变方块")
        self.assertIn("-NonInteractive", src,
                      "PowerShell 需要 -NonInteractive，否则偶发等输入把扫描卡死")
        self.assertIn("json.loads(_decode_console(", src,
                      "PowerShell 的 JSON 输出必须走 _decode_console，不能裸 decode")

    def test_gbk_json_payload_parses(self):
        payload = [{"Id": 49376, "ProcessName": "微信开发者工具"},
                   {"Id": 1556, "ProcessName": "CodeBee"}]
        raw = json.dumps(payload, ensure_ascii=False).encode("gbk")
        got = json.loads(portscan._decode_console(raw))
        self.assertEqual(got[0]["ProcessName"], "微信开发者工具",
                         "带中文的进程名 JSON 解析失败：端口页会把名字显示成方块")


if __name__ == "__main__":
    unittest.main()
