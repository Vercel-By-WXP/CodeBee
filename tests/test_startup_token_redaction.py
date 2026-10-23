# -*- coding: utf-8 -*-
"""启动输出不得把远程访问令牌写进日志。

桌面端把 sidecar 的 stdout 原样写进 `<数据目录>/desktop-logs/backend.log`
（desktop/lib/backend.cjs），所以「控制台打印带 token 的配对地址」等于把
服务凭据写进一个会被上传、同步、转发的文件：拿到日志即可读全部任务与产物、
并通过已授权 API 建任务。默认打码，`--print-token` 保留纯命令行用户的出口。
"""
from __future__ import annotations

import re
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
MAIN = ROOT / "app" / "main.py"

sys.path.insert(0, str(ROOT / "app"))

from main import access_url  # noqa: E402  (需要 app/ 在 sys.path 上)


class AccessUrlMaskingTest(unittest.TestCase):
    def test_default_masks_the_token(self):
        url = access_url("https://random-words.trycloudflare.com", "s3cr3t-token")
        self.assertIn("token=***", url)
        self.assertNotIn("s3cr3t-token", url)
        self.assertTrue(url.startswith("https://random-words.trycloudflare.com/?token="))

    def test_lan_url_keeps_host_and_port(self):
        url = access_url("http://192.168.1.5:8765", "s3cr3t-token")
        self.assertIn("http://192.168.1.5:8765/?token=", url)
        self.assertNotIn("s3cr3t-token", url)

    def test_print_token_is_the_only_way_to_emit_it(self):
        url = access_url("http://127.0.0.1:8765", "s3cr3t-token", print_token=True)
        self.assertIn("token=s3cr3t-token", url)


class MainSourceContractTest(unittest.TestCase):
    def setUp(self):
        self.source = MAIN.read_text(encoding="utf-8", errors="replace")
        self.lines = self.source.splitlines()

    def _line_of(self, needle):
        return [n for n, line in enumerate(self.lines, 1) if needle in line]

    def test_token_is_never_interpolated_directly_into_a_url(self):
        # 只允许 access_url() 里那一处拼 `token=%s`；任何 print 直接拼都是回归。
        occurrences = self._line_of("token=%s")
        self.assertEqual(len(occurrences), 1, f"token=%s 出现在 {occurrences}")
        line = occurrences[0]
        surrounding = "\n".join(self.lines[line - 2:line + 1])
        self.assertIn("print_token", surrounding)
        self.assertNotIn("print(", "\n".join(self.lines[line - 3:line + 2]))

    def test_no_print_statement_emits_the_token_variable(self):
        offenders = [n for n, text in enumerate(self.lines, 1)
                     if "print(" in text and re.search(r"\btok\b", text)
                     and "?" in text]
        self.assertEqual(offenders, [], f"第 {offenders} 行仍在打印令牌")

    def test_opt_in_flag_exists(self):
        self.assertIn("--print-token", self.source)


if __name__ == "__main__":
    unittest.main()
