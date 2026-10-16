# -*- coding: utf-8 -*-
"""嵌套 Git 目录不拒起外部 CLI：未跟踪目录条目按豁免处理，不再当文件快照。

2026-10-16 不周山实案：claude 在书仓 .claude/worktrees/agent-* 留下 4 个
agent worktree（嵌套 Git 仓库），git status --porcelain -uall 把它们各报成
一条带尾斜杠的未跟踪目录条目；begin_git_step 按文件处理，对目录
read_bytes 炸 OSError，「已有改动无法完整快照」拒起全部外部 CLI，
mimo/opencode 连挂 4 次触发供应商健康告警。目录条目（嵌套仓库/空目录）
文件级快照本来就覆盖不了，与 _attachments 同款豁免；起跑循环另留
is_dir 兜底跳过。
"""
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from core import checkpoints, gitmod


class CheckpointNestedGitDirTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.addCleanup(self.tmp.cleanup)

    def _git(self, repo, *args):
        return subprocess.run(["git", "-C", str(repo), *args], check=True,
                              stdout=subprocess.PIPE, stderr=subprocess.PIPE)

    def _git_repo(self, name):
        repo = self.root / name
        repo.mkdir()
        self._git(repo, "init", "-q")
        self._git(repo, "config", "user.email", "test@example.invalid")
        self._git(repo, "config", "user.name", "Checkpoint Test")
        return repo

    def _plant_agent_worktrees(self, repo, name="agent-a137d6a1"):
        """复刻 claude 留下的现场：一个真嵌套仓库 + 一个空目录。"""
        nested = repo / ".claude" / "worktrees" / name
        nested.mkdir(parents=True)
        self._git(nested, "init", "-q")
        (nested / "scratch.md").write_text("agent worktree\n", encoding="utf-8")
        self._git(nested, "add", "scratch.md")
        self._git(nested, "-c", "user.email=test@example.invalid",
                  "-c", "user.name=T", "commit", "-qm", "wip")
        (repo / ".claude" / "worktrees" / (name + "-empty")).mkdir(parents=True)

    def test_nested_worktree_dir_does_not_block_external_cli(self):
        with patch.object(checkpoints, "_DIR", self.root / "checkpoints"):
            repo = self._git_repo("repo-nested")
            (repo / "manuscript.md").write_text("第一章\n", encoding="utf-8")
            self._git(repo, "add", "manuscript.md")
            self._git(repo, "commit", "-qm", "baseline")
            self._plant_agent_worktrees(repo)
            dirty = "第一章\n新增一节。\n"
            (repo / "manuscript.md").write_text(dirty, encoding="utf-8")

            entries = checkpoints._git_changed_paths(repo, gitmod)
            rels = [rel for _, rel in entries]
            self.assertIn("manuscript.md", rels)
            self.assertFalse([r for r in rels if r.startswith(".claude/")],
                             "未跟踪目录条目应被豁免：%r" % rels)

            started = checkpoints.begin_git_step("run-nested", repo)
            self.assertTrue(started.get("ok"), started.get("error"))
            self.assertEqual(started.get("before_paths"), ["manuscript.md"])

            # CLI 步骤期间又建了新 worktree 并改稿：对账不因目录条目挂红
            (repo / "manuscript.md").write_text("CLI 重写了全书\n", encoding="utf-8")
            self._plant_agent_worktrees(repo, name="agent-bbbbbbbb")
            finished = checkpoints.finish_git_step(
                "run-nested", repo, started["head"], started["before_paths"])
            self.assertTrue(finished["ok"], finished.get("warning"))
            self.assertEqual(finished.get("failed_paths"), [])

            # 下一步起跑（实案里连续拒起的第二锤）照样放行
            again = checkpoints.begin_git_step("run-nested-2", repo)
            self.assertTrue(again.get("ok"), again.get("error"))

    def test_capture_file_on_directory_refuses_without_crash(self):
        """边界留证：目录没有字节前态，capture_file 诚实拒绝（不假装可恢复）。"""
        with patch.object(checkpoints, "_DIR", self.root / "checkpoints"):
            repo = self._git_repo("repo-dircap")
            (repo / "base.txt").write_text("base\n", encoding="utf-8")
            self._git(repo, "add", "base.txt")
            self._git(repo, "commit", "-qm", "baseline")
            target = repo / ".claude" / "worktrees" / "agent-x"
            target.mkdir(parents=True)
            saved = checkpoints.capture_file("run-dircap", repo, target)
            self.assertFalse(saved.get("ok"))
            self.assertEqual(saved.get("reason"), "snapshot_io_error")


if __name__ == "__main__":
    unittest.main()
