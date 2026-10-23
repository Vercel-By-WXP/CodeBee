import unittest

from app.core import runner


class AiderReadonlyIsolationTests(unittest.TestCase):
    """评审位不得在稿件目录留下 git 产物。

    aider 0.86 默认会在工作目录初始化 git 仓库并改写 .gitignore，评审是只读
    场景却会污染交付目录。readonly 调用固定追加 --no-git；写模式保留默认。
    """

    def _agent(self):
        return {"id": "aider", "kind": "aider", "command": "aider", "env": {}}

    def _argv(self, readonly):
        argv, _stdin, _prompt, _tmp = runner._build_call(
            self._agent(), "aider", "", readonly, None, "只回复OK")
        return list(argv)

    def test_readonly_review_disables_git(self):
        argv = self._argv(True)
        self.assertIn("--no-git", argv)
        self.assertIn("--no-auto-commits", argv)

    def test_write_mode_keeps_git_available(self):
        argv = self._argv(False)
        self.assertNotIn("--no-git", argv)


if __name__ == "__main__":
    unittest.main()
