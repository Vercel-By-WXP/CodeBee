# -*- coding: utf-8 -*-
"""超限文件的检查点前态凭证：>1MB 走 git 对象库，不再拒起外部 CLI。

2026-10-10 连载实案：书仓 manuscript.md 随章数累积涨到 1.14MB，超过
_SNAPSHOT_MAX_BYTES 后所有评审步被「已有改动无法完整快照」6 秒本地拒死
（增长型悬崖）。前态改存工作区 git 对象库（blob 内容寻址，批间归档提交后
自然转为被引用），起跑→对账→预览→恢复全链路保持可逆。
独立成文件：test_competitive_features.py 已超 architecture_check 行数基线，
增量零容忍不再往里长。
"""
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from core import checkpoints


class CheckpointOversizeTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.addCleanup(self.tmp.cleanup)

    def _git_repo(self, name):
        repo = self.root / name
        repo.mkdir()

        def git(*args):
            return subprocess.run(["git", "-C", str(repo), *args], check=True,
                                  stdout=subprocess.PIPE, stderr=subprocess.PIPE)

        git("init", "-q")
        git("config", "user.email", "test@example.invalid")
        git("config", "user.name", "Checkpoint Test")
        return repo, git

    def test_oversize_dirty_tracked_file_snapshots_via_git_blob(self):
        with patch.object(checkpoints, "_DIR", self.root / "checkpoints"):
            repo, git = self._git_repo("repo-oversize")
            big = "卷三\n" + ("山脊线在暮色里慢慢沉下去。\n" * 30000)
            (repo / "manuscript.md").write_text(big, encoding="utf-8")
            self.assertGreater((repo / "manuscript.md").stat().st_size,
                               checkpoints._SNAPSHOT_MAX_BYTES)
            git("add", "manuscript.md")
            git("commit", "-qm", "baseline")
            dirty = big + "\n新增一章。\n"
            (repo / "manuscript.md").write_text(dirty, encoding="utf-8")
            started = checkpoints.begin_git_step("run-oversize", repo)
            self.assertTrue(started.get("ok"), started.get("error"))
            self.assertIn("manuscript.md", started.get("before_paths") or [])
            (repo / "manuscript.md").write_text("CLI 重写了全书\n", encoding="utf-8")
            finished = checkpoints.finish_git_step(
                "run-oversize", repo, started["head"], started["before_paths"])
            self.assertTrue(finished["ok"], finished.get("warning"))
            preview = checkpoints.snapshot_preview("run-oversize", repo)
            row = next(r for r in preview["files"] if r["path"] == "manuscript.md")
            self.assertTrue(row["restorable"])
            restored = checkpoints.restore_files("run-oversize", repo)
            self.assertEqual(restored["restored"], 1)
            self.assertEqual((repo / "manuscript.md").read_text(encoding="utf-8"), dirty)

    def test_oversize_untracked_file_captures_and_restores(self):
        with patch.object(checkpoints, "_DIR", self.root / "checkpoints"):
            repo, git = self._git_repo("repo-oversize-new")
            (repo / "base.txt").write_text("base\n", encoding="utf-8")
            git("add", "base.txt")
            git("commit", "-qm", "baseline")
            big_new = bytes(range(256)) * 5000  # 1.28MB 未跟踪新文件
            (repo / "big-new.bin").write_bytes(big_new)
            started = checkpoints.begin_git_step("run-oversize-new", repo)
            self.assertTrue(started.get("ok"), started.get("error"))
            (repo / "big-new.bin").unlink()
            finished = checkpoints.finish_git_step(
                "run-oversize-new", repo, started["head"], started["before_paths"])
            self.assertTrue(finished["ok"], finished.get("warning"))
            restored = checkpoints.restore_files("run-oversize-new", repo)
            self.assertEqual(restored["restored"], 1)
            self.assertEqual((repo / "big-new.bin").read_bytes(), big_new)

    def test_oversize_without_git_still_refuses(self):
        """对象库存不进（非 git 目录）维持拒绝：起跑闸宁拒不假装可恢复。"""
        with patch.object(checkpoints, "_DIR", self.root / "checkpoints"):
            workdir = self.root / "plain"
            workdir.mkdir()
            target = workdir / "big.txt"
            target.write_text("x" * (checkpoints._SNAPSHOT_MAX_BYTES + 1),
                              encoding="utf-8")
            saved = checkpoints.capture_file("run-plain", workdir, target)
            self.assertFalse(saved.get("ok"))
            self.assertEqual(saved.get("reason"), "snapshot_size_limit")


if __name__ == "__main__":
    unittest.main()
