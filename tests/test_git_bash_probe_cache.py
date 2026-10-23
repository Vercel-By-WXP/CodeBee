# -*- coding: utf-8 -*-
"""Git Bash 探测负缓存回归（对应审查 C2）。

`find_git_bash()` 原来把「没找到」也写进进程级 `done` 标记，于是服务起跑
之后再装 Git Bash（或把安装目录从 D:\\Git 迁到 C:\\Program Files\\Git）时，
claude 原生 exe 一直起不来，只有重启服务才自愈——和 `_fallback_probe_cli`
里「PATH 是启动快照」是同一种病。修法：正结果永久缓存，负结果只缓存 60s。

用 os 桩把平台固定成 Windows，避免在 Linux/macOS 上跑不到分支。
"""
import types
import unittest
from unittest import mock

from app.core import cli_commands


def _fake_os(is_file):
    return types.SimpleNamespace(name="nt",
                                 path=types.SimpleNamespace(isfile=is_file))


class GitBashProbeCacheTests(unittest.TestCase):
    def setUp(self):
        cli_commands._bash_cache["path"] = None
        cli_commands._bash_cache["checked_at"] = 0.0
        self.addCleanup(setattr, cli_commands, "_bash_cache",
                        {"path": None, "checked_at": 0.0})
        self.now = [1000.0]
        self.addCleanup(mock.patch.stopall)
        mock.patch.object(cli_commands.time, "monotonic",
                          side_effect=lambda: self.now[0]).start()

    def _run(self, is_file, which=None):
        which_mock = mock.Mock(side_effect=which or (lambda name: None))
        with mock.patch.object(cli_commands, "os", _fake_os(is_file)), \
                mock.patch.object(cli_commands.shutil, "which", which_mock):
            result = cli_commands.find_git_bash()
        return result, which_mock

    def test_positive_result_is_cached_permanently(self):
        calls = []

        def is_file(path):
            calls.append(path)
            return path == cli_commands._BASH_CANDIDATES[0]

        first, which1 = self._run(is_file)
        self.assertEqual(first, cli_commands._BASH_CANDIDATES[0])
        self.assertEqual(which1.call_count, 2, "一次探测查 bash.exe / bash 两个名字")
        self.now[0] += 10_000          # 远超负缓存 TTL，正结果也不该重探
        second, which2 = self._run(is_file)
        self.assertEqual(second, first)
        self.assertEqual(which2.call_count, 0, "正结果必须永久命中，不再探盘")

    def test_which_hit_is_preferred_and_cached(self):
        found, which = self._run(lambda path: False,
                                 which=lambda name: r"D:\Git\usr\bin\bash.exe"
                                 if name == "bash.exe" else None)
        self.assertEqual(found, r"D:\Git\usr\bin\bash.exe")
        self.assertEqual(which.call_count, 1)   # 命中 bash.exe 就停手

    def test_negative_result_reprobes_after_ttl(self):
        # 第一次：盘上什么都没有
        miss, which1 = self._run(lambda path: False)
        self.assertIsNone(miss)
        # TTL 内：不重复探盘
        self.now[0] += 30
        again, which2 = self._run(lambda path: False)
        self.assertIsNone(again)
        self.assertEqual(which2.call_count, 0, "TTL 内不该反复探盘")
        # 过了 TTL：必须重探（用户刚装好 Git Bash 的唯一自愈路径）
        self.now[0] += cli_commands._BASH_NEGATIVE_TTL_S + 1
        hit, which3 = self._run(
            lambda path: path == cli_commands._BASH_CANDIDATES[1])
        self.assertEqual(hit, cli_commands._BASH_CANDIDATES[1],
                         "装好后必须无需重启服务即可探到")
        self.assertEqual(which3.call_count, 2)

    def test_non_windows_returns_none_without_probing(self):
        with mock.patch.object(cli_commands, "os",
                               types.SimpleNamespace(name="posix")), \
                mock.patch.object(cli_commands.shutil, "which",
                                  mock.Mock(side_effect=AssertionError("不该探盘"))):
            self.assertIsNone(cli_commands.find_git_bash())


if __name__ == "__main__":
    unittest.main()
