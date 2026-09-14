# -*- coding: utf-8 -*-
"""数据目录选择逻辑单测：TUTTI_DATA 覆盖 / 仓库 data 在用沿用 / 全新装落用户目录。"""
from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from app.core import paths


class TestDefaultDataDir(unittest.TestCase):
    def _repo(self, with_data=True, sentinel="catalog.json"):
        d = Path(tempfile.mkdtemp(prefix="tutti-paths-"))
        if with_data:
            (d / "data").mkdir()
            if sentinel:
                (d / "data" / sentinel).write_text("{}", encoding="utf-8")
        return d

    def test_env_override_wins(self):
        env = {"TUTTI_DATA": str(Path(tempfile.mkdtemp()) / "elsewhere")}
        self.assertEqual(str(paths.default_data_dir(repo=self._repo(), environ=env)),
                         env["TUTTI_DATA"])

    def test_repo_data_in_use_is_kept(self):
        # 仓库 data/ 有 sentinel 文件 → 沿用（开发仓库 / 老安装升级不丢数据）
        for sent in paths._DATA_SENTINELS:
            r = self._repo(sentinel=sent)
            self.assertEqual(paths.default_data_dir(repo=r, environ={}), r / "data")

    def test_empty_repo_data_falls_back_to_user(self):
        # 仅有 ensure_dirs 建出的空 data/ 目录（无 sentinel）→ 视为全新安装
        r = self._repo(sentinel=None)
        out = paths.default_data_dir(repo=r, environ={"APPDATA": "C:/App"})
        self.assertEqual(out, Path("C:/App") / "Tutti")

    def test_no_repo_data_uses_user(self):
        r = self._repo(with_data=False)
        out = paths.default_data_dir(repo=r, environ={"APPDATA": "C:/App"})
        self.assertEqual(out, Path("C:/App") / "Tutti")

    def test_user_dir_per_platform(self):
        self.assertEqual(paths._user_data_dir("win32", {"APPDATA": "C:/App"}, "C:/Users/x"),
                         Path("C:/App") / "Tutti")
        self.assertEqual(paths._user_data_dir("win32", {}, "C:/Users/x"),
                         Path("C:/Users/x") / "AppData" / "Roaming" / "Tutti")
        self.assertEqual(paths._user_data_dir("darwin", {}, "/Users/x"),
                         Path("/Users/x") / ".tutti")
        self.assertEqual(paths._user_data_dir("linux", {}, "/home/y"),
                         Path("/home/y") / ".tutti")


if __name__ == "__main__":
    unittest.main()
