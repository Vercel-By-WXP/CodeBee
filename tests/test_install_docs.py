# -*- coding: utf-8 -*-
"""安装文档契约：npm 是 P0 分发渠道，不能只写在 package.json 里。

2026-10-23 核验发现：README 与 QUICKSTART 全部从 `git clone` 讲起，没有任何
`npm i -g codebee` / `npx codebee` 命令 —— 用户从 npm 页面进来等于没有安装路径。
本测试锁住三件事：npm 入口在文档里、文档写的参数 CLI 真支持、文档里的命令能过
bin/tutti.js 的参数白名单（反斜杠路径会被拒，文档不能示范）。
"""
import re
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
QUICKSTART = (ROOT / "QUICKSTART.md").read_text(encoding="utf-8")
README = (ROOT / "README.md").read_text(encoding="utf-8")
MAIN_PY = (ROOT / "app" / "main.py").read_text(encoding="utf-8")
SHIM = (ROOT / "bin" / "tutti.js").read_text(encoding="utf-8")

FENCE = re.compile(r"^~~~[a-z]*$")


def _code_blocks(text):
    """只取 ~~~ 围栏内的命令行，避免把正文里以 npm 开头的句子当成命令。"""
    out, current, inside = [], [], False
    for line in text.splitlines():
        if FENCE.match(line.strip()):
            if inside:
                out.append("\n".join(current))
                current = []
            inside = not inside
            continue
        if inside:
            current.append(line)
    return [b for b in out if b.strip()]


class InstallDocsTests(unittest.TestCase):
    def test_quickstart_documents_npm_and_npx(self):
        self.assertIn("npm i -g codebee", QUICKSTART)
        self.assertIn("npx codebee@latest", QUICKSTART)

    def test_readme_quickstart_shows_runnable_command(self):
        section = README.split("## 快速开始", 1)[1].split("\n## ", 1)[0]
        self.assertIn("npx codebee@latest", section)
        self.assertIn("QUICKSTART.md", section)

    def test_quickstart_states_node_requirement(self):
        # npm 方式需要 Node；不写清楚会让只装了 Python 的用户卡在 npx 报错上。
        self.assertRegex(QUICKSTART, r"Node\.js\s*14")
        self.assertIn("Python 3.8", QUICKSTART)

    def test_documented_flags_exist_in_cli(self):
        # 只看「CodeBee 自己的参数」：参数表的行与 codebee 命令里的 flag。
        # python --version 之类是解释器参数，不属于本契约。
        lines = [l for l in QUICKSTART.splitlines()
                 if l.strip().startswith(("| `--", "codebee"))]
        flags = set(re.findall(r"--[a-z][a-z-]+", "\n".join(lines)))
        self.assertTrue(flags, "QUICKSTART 应给出 CodeBee 的启动参数")
        for flag in flags:
            self.assertIn('"%s"' % flag, MAIN_PY,
                          "文档写了 CLI 不认识的参数：%s" % flag)

    def test_documented_npm_commands_pass_shim_whitelist(self):
        allowed = re.search(r"SAFE_ARG = /\^(.+?)\$/", SHIM).group(1)
        self.assertEqual(allowed, r"[A-Za-z0-9_\-.:=/@% ]+")
        pattern = re.compile(r"^%s$" % allowed)
        checked = 0
        for block in _code_blocks(QUICKSTART):
            for line in block.splitlines():
                line = line.strip()
                if not line or line.startswith("#"):
                    continue
                if not line.startswith(("codebee", "npx ", "npm ")):
                    continue
                checked += 1
                self.assertRegex(line, pattern,
                                 "该命令会被 bin/tutti.js 白名单拒绝：%s" % line)
        self.assertGreater(checked, 0, "未在 QUICKSTART 中找到 npm/npx 命令")


class TroubleshootingDocsTests(unittest.TestCase):
    """排障文档必须和代码同源：换将名单、错误码都不能凭记忆写。"""

    @classmethod
    def setUpClass(cls):
        cls.doc = (ROOT / "TROUBLESHOOTING.md").read_text(encoding="utf-8")
        cls.error_codes = (ROOT / "app" / "core" / "error_codes.py").read_text(encoding="utf-8")
        cls.routing = (ROOT / "app" / "core" / "routing_strategy.py").read_text(encoding="utf-8")

    def test_doc_exists_and_is_linked(self):
        self.assertTrue((ROOT / "TROUBLESHOOTING.md").is_file())
        self.assertIn("TROUBLESHOOTING.md", README)
        self.assertIn("TROUBLESHOOTING.md", QUICKSTART)

    def test_account_dead_codes_are_documented(self):
        # 账号级死错决定「这个 CLI 本场还能不能用」，文档必须点全，
        # 否则用户会以为换个提示词就能重试。
        block = self.error_codes.split("ACCOUNT_DEAD_CODES = frozenset({", 1)[1].split("})", 1)[0]
        codes = set(re.findall(r"ErrorCode\.([A-Z_]+)\.value", block))
        self.assertGreaterEqual(len(codes), 5)
        for code in codes:
            self.assertIn(code, self.doc, "账号级死错 %s 未写进排障文档" % code)

    def test_fallback_whitelist_matches_code(self):
        block = self.routing.split("def should_fallback", 1)[1].split("if isinstance(error, dict)", 1)[0]
        whitelist = set(re.findall(r"ErrorCode\.([A-Z_]+)\.value", block))
        self.assertGreaterEqual(len(whitelist), 10)
        for code in whitelist:
            self.assertIn(code, self.doc,
                          "换将白名单里的 %s 未写进排障文档" % code)


if __name__ == "__main__":
    unittest.main()
