# -*- coding: utf-8 -*-
"""aider 接入 + 瞬态换将回归（2026-09-20 连载评审四家全灭案）。

四家评审 CLI 同晚全挂，各有病根：
- aider：litellm 靠 provider 前缀路由 + 只认 ANTHROPIC_API_KEY（不认
  ANTHROPIC_AUTH_TOKEN），旧注入两样都没给 → LLM Provider NOT provided，
  且 aider 撞模型错误仍退出码 0，步骤被记成 done、正文全是终端噪声；
- codex 网关断流（stream disconnected）/ opencode 服务端 500（Unexpected
  server error）：都是重试或换将就能活的瞬态病，旧瞬态表判成终态，
  链上健康后继从未被尝试。
真实端到端连通已在会话中实测（anthropic/glm-5.1 回复「连通正常」）；
这里固化不花钱的部分：argv/env 形状 + 瞬态判定。
"""
from __future__ import annotations

import shutil
import subprocess
from unittest import mock

from base import BaseTest


class TestAiderCallShape(BaseTest):
    def runTest(self):
        from app.core import runner as R

        # anthropic 面条目：env 带 ANTHROPIC_BASE_URL → 裸模型名自动加 anthropic/ 前缀
        agent = {"id": "aider", "kind": "aider", "mode": "real", "command": "aider",
                 "env": {"ANTHROPIC_BASE_URL": "https://x/api/anthropic"}}
        argv, _, _, _ = R._build_call(agent, "aider", "", True, "glm-5.1", "p")
        i = argv.index("--model")
        self.assertEqual(argv[i + 1], "anthropic/glm-5.1")
        self.assertIn("--no-show-model-warnings", argv)   # 网关模型警告刷屏闸
        self.assertIn("--no-gitignore", argv)             # 无头执行不改用户仓库
        self.assertIn("--no-pretty", argv)                # Windows 服务无控制台
        self.assertIn("--no-stream", argv)

        # openai 兼容面条目 → openai/ 前缀
        agent2 = {"id": "aider", "kind": "aider", "mode": "real", "command": "aider",
                  "env": {"OPENAI_API_BASE": "https://y/v1"}}
        argv2, _, _, _ = R._build_call(agent2, "aider", "", True, "glm-5.1", "p")
        self.assertEqual(argv2[argv2.index("--model") + 1], "openai/glm-5.1")

        # 已带前缀的模型名不动；无注入（CLI 本机默认）也不动
        argv3, _, _, _ = R._build_call(agent, "aider", "", True, "zai/glm-5.1", "p")
        self.assertEqual(argv3[argv3.index("--model") + 1], "zai/glm-5.1")
        agent4 = {"id": "aider", "kind": "aider", "mode": "real", "command": "aider", "env": {}}
        argv4, _, _, _ = R._build_call(agent4, "aider", "", True, "glm-5.1", "p")
        self.assertEqual(argv4[argv4.index("--model") + 1], "glm-5.1")

    def test_no_fancy_input_keeps_prompt_toolkit_noise_out_of_logs(self):
        """无头执行没有控制台：不关花式输入，aider 会打一行 prompt toolkit 报错。

        那行排在输出最前，而 UI 的失败摘要只取头部（runner._build_call 之后的
        `tail` 切片）→ 真错误被噪声顶掉（2026-09-22 mo-so 实案整条失败原因显示
        成这句）。--no-fancy-input 让它不建 PromptSession，噪声从源头消失。
        """
        from app.core import runner as R

        agent = {"id": "aider", "kind": "aider", "mode": "real",
                 "command": "aider", "env": {}}
        argv, _, _, _ = R._build_call(agent, "aider", "", True, "glm-5.1", "p")
        self.assertIn("--no-fancy-input", argv)

    def test_corrupt_git_repo_is_reported_before_aider_starts(self):
        from app.core import runner as R

        ok = mock.Mock(returncode=0, stdout=".git\n", stderr="")
        bad = mock.Mock(returncode=128, stdout="", stderr="missing pack-a52.idx")
        with mock.patch.object(R.os.path, "isdir", return_value=True), \
             mock.patch.object(R.os.path, "exists", return_value=True), \
             mock.patch.object(R.subprocess, "run", side_effect=[ok, bad]):
            issue = R._git_repo_issue("C:/broken")
        self.assertIn("Git 仓库损坏", issue)
        self.assertIn("git fsck", issue)

    def test_orphan_pack_without_idx_is_reported(self):
        """pack 缺同名 .idx：git 本体只警告跳过，aider 的 gitdb 直接整步失败。

        2026-09-22 mo-so 实案：pack-a52e675e.pack 没有 .idx（内容在别的包里
        还有一份），git status/fsck/rev-parse 全绿所以旧预检放行，aider 换将后
        跑满 14 分钟才报「Unable to list files in git repo」；报错头还是
        prompt toolkit 噪声，用户看不到真正病因。这里按同样形状造坏：一个
        健康的包 + 一份没有 .idx 的冗余包。
        """
        from app.core import runner as R

        real = self.workdir / "repo"
        real.mkdir()
        subprocess.run(["git", "init", "-q", str(real)], check=True)
        (real / "a.txt").write_text("hi\n", encoding="utf-8")
        subprocess.run(["git", "-C", str(real), "add", "a.txt"], check=True)
        subprocess.run(["git", "-C", str(real), "-c", "user.email=t@t",
                        "-c", "user.name=t", "commit", "-qm", "init"], check=True)
        subprocess.run(["git", "-C", str(real), "gc", "-q"], check=True)

        pack_dir = real / ".git" / "objects" / "pack"
        good = sorted(pack_dir.glob("pack-*.pack"))
        self.assertTrue(good, "git gc 后应至少有一个 pack")
        # 复制一份带新名字的冗余包，但不复制 .idx —— 正是 mo-so 的坏法
        orphan = pack_dir / ("pack-" + "f" * 40 + ".pack")
        shutil.copyfile(str(good[0]), str(orphan))

        # 前提：git 本体对这个坏包不敏感（否则旧预检早就拦下了）
        st = subprocess.run(["git", "-C", str(real), "status", "--porcelain",
                             "--untracked-files=no"],
                            stdout=subprocess.PIPE, stderr=subprocess.PIPE, check=False)
        self.assertEqual(st.returncode, 0, "本用例要复现的正是 git status 放行的情形")

        issue = R._git_repo_issue(str(real))
        self.assertIn("pack 索引缺失", issue)
        self.assertIn(orphan.name, issue)
        self.assertIn("git index-pack", issue)

        # 按提示重建索引后必须放行（修复动作真的有效）
        subprocess.run(["git", "-C", str(pack_dir), "index-pack", orphan.name],
                       check=True, stdout=subprocess.DEVNULL)
        self.assertEqual(R._git_repo_issue(str(real)), "")

    def test_orphan_pack_probe_is_quiet_on_healthy_repo(self):
        """健康仓库不得误报（全量套跑里每个 aider 步骤都会走这个预检）。"""
        from app.core import runner as R

        real = self.workdir / "healthy"
        real.mkdir()
        self.assertEqual(R._git_repo_issue(str(real)), "")  # 非 git 目录
        subprocess.run(["git", "init", "-q", str(real)], check=True)
        self.assertEqual(R._git_repo_issue(str(real)), "")  # 空仓库


class TestAiderChainEnv(BaseTest):
    """链条目必须给 aider 递上它能读到的钥匙（litellm 不认 AUTH_TOKEN）。"""

    def runTest(self):
        from app.core import modelhub as M
        prov = {"id": "prov-x", "name": "X", "protocol": "anthropic",
                "base_url": "https://x/api/anthropic", "api_key": "sk-test",
                "enabled": True}
        e = M._chain_entry_env(prov, "glm-5.1", target="aider")
        self.assertEqual(e["env"].get("ANTHROPIC_API_KEY"), "sk-test")
        self.assertIn("ANTHROPIC_BASE_URL", e["env"])

        # claude 目标保持不注入 ANTHROPIC_API_KEY（x-api-key / Bearer 网关挑食，不混）
        e2 = M._chain_entry_env(prov, "glm-5.1", target="claude-code")
        self.assertNotIn("ANTHROPIC_API_KEY", e2["env"])
        self.assertIn("ANTHROPIC_AUTH_TOKEN", e2["env"])

        # openai 兼容面：aider 拿 OPENAI_API_KEY + OPENAI_API_BASE
        prov2 = dict(prov, protocol="openai", base_url="https://y/v1")
        e3 = M._chain_entry_env(prov2, "glm-5.1", target="aider")
        self.assertEqual(e3["env"].get("OPENAI_API_KEY"), "sk-test")
        self.assertEqual(e3["env"].get("OPENAI_API_BASE"), "https://y/v1")


class TestTransientNewPatterns(BaseTest):
    def runTest(self):
        from app.core import runner as R
        # codex 网关断流（_codex_fail_msg 原文进 error）
        self.assertTrue(R._transient_error(
            "codex: stream disconnected before completion: "
            "stream closed before response.completed（退出码 1）"))
        # opencode 服务端 500
        self.assertTrue(R._transient_error(
            '退出码 1；stderr/stdout: Error: {"name": "UnknownError", '
            '"data": {"message": "Unexpected server error."}}'))
        # kimi 连接错误旧表就认（回归护栏）
        self.assertTrue(R._transient_error(
            "error: failed to run prompt: provider.connection_error: Connection error."))
        # 2026-09-22 mo-so 实案：claude 端点 DNS 解析失败（ENOTFOUND）被判成
        # 终态，跨厂商链上健康的后继模型从未被尝试就跳去换将别的 CLI
        self.assertTrue(R._transient_error(
            "claude 返回 is_error: API Error: Can't reach the API server — "
            "check your internet or DNS (ENOTFOUND)"))
