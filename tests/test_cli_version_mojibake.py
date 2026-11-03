# -*- coding: utf-8 -*-
"""manager._cli_version_output：cmd「找不到命令」的 GBK mojibake 不得当版本号。

实测来源（2026-11-03，中文 Windows）：catalog 已装 CLI 的版本探测走
`cmd /c <cli> --version`，PATH 上没有该命令时 cmd 往 stderr 吐 GBK 报错
「'claude' 不是内部或外部命令…」。其中部分 GBK 字节对碰巧构成合法 UTF-8
序列（如 DA DA→ⲿ），decode_output 的逐段 GB18030 兜底救不回来，mojibake
（`²»ÊÇÄڲ炕…` + PUA 区字符）被当版本串写进 /api/catalog 的 version
字段，智能体管理页（EN 模式 CDP 扫描）整行乱码。

守测打桩 runner.run_process：非零退出 + 无数字 / mojibake 特征 → None。
"""
import unittest
from unittest import mock

from app.core import manager


def _looks_mojibake_cases():
    return [
        # 实测样本（截断）：Latin-1 高区位连续 + PUA 区
        "'claude' ²»ÊÇÄڲ炕蛲ⲿÃüÁ\ue8ecҲ²»Êǿ稍诵еĳ绦蚧蚺",
    ]


class LooksMojibakeTests(unittest.TestCase):
    def test_gbk_mojibake_detected(self):
        for s in _looks_mojibake_cases():
            self.assertTrue(manager._looks_mojibake(s), s[:40])

    def test_normal_versions_not_flagged(self):
        for s in ["claude 1.2.3", "v2.0.0 (Claude Code)", "版本 1.0",
                  "codex-cli 0.11.0", "mimo-code 0.5.1-alpha"]:
            self.assertFalse(manager._looks_mojibake(s), s)

    def test_pua_characters_flagged(self):
        self.assertTrue(manager._looks_mojibake("x\ue000y"))
        self.assertFalse(manager._looks_mojibake("x"))

    def test_empty_is_safe(self):
        self.assertFalse(manager._looks_mojibake(""))
        self.assertFalse(manager._looks_mojibake(None))


class CliVersionOutputTests(unittest.TestCase):
    def _probe(self, stdout="", stderr="", rc=1):
        ret = {"stdout": stdout, "stderr": stderr, "returncode": rc}
        with mock.patch.object(manager.runner, "run_process",
                               return_value=ret) as m:
            m.return_value = ret
            return manager._cli_version_output("claude")

    def test_cmd_not_found_mojibake_returns_none(self):
        """cmd 找不到命令（GBK stderr + rc=1 + 无数字）→ None，不显示乱码。"""
        out = self._probe(
            stderr="'claude' ²»ÊÇÄڲ炕蛲ⲿÃüÁ\ue8ecҲ²»Êǿ稍诵еĳ"
                   "绦蚧蚺\ue303\ue78c砦ļþ¡£",
            rc=1)
        self.assertIsNone(out)

    def test_mojibake_even_with_rc0_returns_none(self):
        out = self._probe(stdout="claude ²»ÊÇÄڲ炕蛲ⲿ", rc=0)
        self.assertIsNone(out)

    def test_normal_version_kept(self):
        out = self._probe(stdout="claude 1.0.66 (Claude Code)\n", rc=0)
        self.assertEqual(out, "claude 1.0.66 (Claude Code)")

    def test_nonzero_rc_with_digits_kept(self):
        """有些 CLI --version 退出码非零但确有版本号：不能误杀。"""
        out = self._probe(stdout="mimo-code 0.5.1 (warn)", rc=1)
        self.assertEqual(out, "mimo-code 0.5.1 (warn)")

    def test_empty_output_returns_none(self):
        self.assertIsNone(self._probe(stdout="", stderr="", rc=0))


if __name__ == "__main__":
    unittest.main()
