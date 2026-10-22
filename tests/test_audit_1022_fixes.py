# -*- coding: utf-8 -*-
"""2026-10-22 全量审查修复的形状回归（源码扫描锁定，防复发）。

对应修复：
  1) /api/settings/default-workdir 响应必须走 settings.public_view（密钥泄露 P1）
  2) GET /mcp/orchestration 必须在 /api/ 前缀块之前分流（原缩进在块内永不可达）
  3) hooks 令牌比较必须 compare_digest
  4) start.bat 必须纯 ASCII（cmd GBK 字节漂移类故障）
  5) 发版闸门禁止清单必须含 /scratch/
  6) app/.npmignore 必须排除 scratch 与无引用 demo 页
  7) collect_desktop_release 必需资产不得再要求未构建的 linux 产物
  8) sync_version 必须校验 desktop lock 两处 version
  9) npm files 必须带 VERSION（市场版本闸依赖）
  10) board.js 轮询必须有在飞保护 + 超时
  11) 续会话下拉的项目路径段必须 esc（XSS P1）
"""
from __future__ import annotations

import json
import re
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def _read(rel: str) -> str:
    return (ROOT / rel).read_text(encoding="utf-8", errors="replace")


class AuditFixShapeTests(unittest.TestCase):
    def test_default_workdir_response_uses_public_view(self):
        src = _read("app/main.py")
        block = src.split('"/api/settings/default-workdir"', 1)[1].split('"/api/orchestrator"', 1)[0]
        self.assertIn('settings.public_view(view)', block)
        self.assertNotIn('"settings": view', block)

    def test_mcp_get_route_outside_api_block(self):
        src = _read("app/main.py")
        api_block = src.index('if path.startswith("/api/")')
        mcp_branch = src.index('if path == "/mcp/orchestration"')
        self.assertLess(mcp_branch, api_block,
                        "MCP GET 分流必须在 /api/ 前缀块之前，否则永远不可达")

    def test_hooks_token_compare_digest(self):
        src = _read("app/main.py")
        block = src.split("令牌不匹配", 1)[0][-1500:]
        self.assertIn("secrets.compare_digest", block)

    def test_start_bat_pure_ascii(self):
        raw = (ROOT / "start.bat").read_bytes()
        raw.decode("ascii")  # 非 ASCII 直接 UnicodeDecodeError

    def test_release_gate_forbids_scratch(self):
        self.assertIn('"/scratch/"', _read("scripts/release_gate.py"))

    def test_npmignore_scratch_and_demo(self):
        txt = _read("app/.npmignore")
        self.assertIn("/scratch/", txt)
        self.assertIn("/demo_apple_style.html", txt)
        self.assertIn("/demo_workbench.html", txt)

    def test_collect_required_assets_no_linux(self):
        mjs = _read("scripts/collect_desktop_release.mjs")
        block = mjs.split("REQUIRED_ASSETS = [", 1)[1].split("];", 1)[0]
        self.assertNotIn("linux", block,
                         "CI 已不构建 Linux，必需清单再要 linux 会必败发版（v0.1.118/119 实案）")
        for key in ("win-x64:exe", "mac-arm64:dmg", "mac-x64:dmg"):
            self.assertIn(key, block)

    def test_sync_version_covers_desktop_lock(self):
        src = _read("scripts/sync_version.py")
        self.assertIn("DESKTOP_LOCK", src)

    def test_npm_files_include_version(self):
        files = json.loads(_read("package.json"))["files"]
        self.assertIn("VERSION", files)

    def test_board_poll_guards(self):
        src = _read("app/ui/board.js")
        self.assertIn("_pollBusy", src)
        self.assertIn("AbortController", src)

    def test_sessions_option_escapes_project_segment(self):
        src = _read("app/ui/app.js")
        m = re.search(r'const who = s\.project.*?";', src)
        self.assertIsNotNone(m, "续会话 who 拼接行还在")
        self.assertIn("esc(", m.group(0), "项目路径段必须过 esc()")


if __name__ == "__main__":
    unittest.main()
