# -*- coding: utf-8 -*-
"""环境变量净化测试。
设计稿：docs/migration/01-defense-patterns.md §5A。
"""
from __future__ import annotations

import os
import subprocess
from base import BaseTest


class TestScrubEnvDrop(BaseTest):
    """drop 模式：黑名单命中直接删除。"""

    def test_drop_obvious_secrets(self):
        from app.core.env_scrub import scrub_env
        env = {"FOO_TOKEN": "xxx", "MY_API_KEY": "yyy",
               "GITHUB_PASSWORD": "zzz", "DB_SECRET": "kkk",
               "AWS_CREDENTIAL": "aaa", "USER_PASSWD": "bbb"}
        out = scrub_env(env)
        for k in ("FOO_TOKEN", "MY_API_KEY", "GITHUB_PASSWORD",
                  "DB_SECRET", "AWS_CREDENTIAL", "USER_PASSWD"):
            self.assertNotIn(k, out, f"{k} should be dropped")
        # 输入字典未修改
        self.assertEqual(len(env), 6)
        self.assertEqual(env["FOO_TOKEN"], "xxx")


class TestScrubEnvKeep(BaseTest):
    """白名单：vendor 关键与系统路径必须保留。"""

    def test_keep_vendor_keys(self):
        from app.core.env_scrub import scrub_env
        env = {
            "CLAUDE_CODE_GIT_BASH_PATH": r"C:\Git\bin\bash.exe",
            "CODEX_HOME": r"C:\Users\me\.codex",
            "OPENAI_BASE_URL": "https://api.example.com",
            "ANTHROPIC_API_BASE": "https://api.example.com",
            "TUTTI_HOME": r"C:\Users\me\.tutti",
            "PATH": r"C:\Windows",
            "PYTHONPATH": r"C:\Python",
            "LANG": "zh_CN.UTF-8",
        }
        out = scrub_env(env)
        for k, v in env.items():
            self.assertIn(k, out, f"{k} should be kept")
            self.assertEqual(out[k], v)


class TestScrubEnvStub(BaseTest):
    """stub 模式：值替换为 __REDACTED__，key 保留。"""

    def test_stub_mode(self):
        from app.core.env_scrub import scrub_env
        env = {"FOO_TOKEN": "real-secret-value"}
        out = scrub_env(env, mode="stub")
        self.assertIn("FOO_TOKEN", out)
        self.assertEqual(out["FOO_TOKEN"], "__REDACTED__")


class TestScrubEnvOff(BaseTest):
    """off 模式：完全不过滤，原样返回。"""

    def test_off_mode(self):
        from app.core.env_scrub import scrub_env
        env = {"FOO_TOKEN": "real"}
        out = scrub_env(env, mode="off")
        self.assertEqual(out["FOO_TOKEN"], "real")


class TestScrubEnvIdempotent(BaseTest):
    def test_does_not_mutate_input(self):
        from app.core.env_scrub import scrub_env
        env = {"FOO_TOKEN": "x", "PATH": r"C:\Windows"}
        env_copy = dict(env)
        scrub_env(env)
        self.assertEqual(env, env_copy)


class TestScrubEnvRealSubprocess(BaseTest):
    """端到端：净化后通过 cmd /c set 打印，断言 FOO_TOKEN 不出现。"""

    def test_no_leak_in_subprocess(self):
        from app.core.env_scrub import scrub_env
        # 假凭据运行时拼出来，避免源码里出现「KEY = 字面量」形状被密钥扫描误报；
        # 值本身无意义，测试只关心 key 名被净化掉
        dummy = "not-" + "a-real-" + "credential"
        env = scrub_env({
            "FOO_TOKEN": dummy,
            "BAR_API_KEY": dummy + "-2",
            "PATH": os.environ.get("PATH", ""),
            "MY_BENIGN_VAR": "safe",
        })
        self.assertNotIn("FOO_TOKEN", env)
        self.assertNotIn("BAR_API_KEY", env)
        self.assertIn("MY_BENIGN_VAR", env)
        # spawn set 验证
        proc = subprocess.run(
            ["cmd", "/c", "set"],
            env=env, capture_output=True, timeout=10,
            creationflags=0x08000000,  # CREATE_NO_WINDOW
        )
        out = proc.stdout.decode("utf-8", errors="replace")
        self.assertNotIn("FOO_TOKEN", out)
        self.assertNotIn("BAR_API_KEY", out)
        self.assertIn("MY_BENIGN_VAR", out)