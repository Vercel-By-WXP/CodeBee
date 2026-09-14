# -*- coding: utf-8 -*-
"""自更新模块单测：模式判定 / 版本比较 / 发布名一致性 / 升级门控 / 重启参数校验。

不真跑 npm（网络+全局安装副作用），只测纯逻辑与防御分支。
"""
from __future__ import annotations

import json
from pathlib import Path

from base import BaseTest


class TestSelfUpdate(BaseTest):
    def runTest(self):
        import app.core.selfupdate as su

        # 0) 发布名一致性：_PKG_NAME 必须等于 package.json 的 name（防品牌重命名漏改）
        pkg = json.loads((Path(__file__).resolve().parents[1] / "package.json")
                         .read_text(encoding="utf-8"))
        self.assertEqual(su._PKG_NAME, pkg["name"])
        # 版本号合法 semver
        self.assertRegex(pkg["version"], r"^\d+\.\d+\.\d+")

        # 1) 安装模式：本测试环境是开发仓库 → repo（永不自动升级）
        self.assertEqual(su.install_mode(), "repo")
        info = su.check()
        self.assertFalse(info["has_update"])
        self.assertIn("git pull", info["note"])

        # 2) repo 模式拒绝自动升级（门控）
        res = su.apply_upgrade()
        self.assertIn("error", res)

        # 3) 版本比较：semver 元组逐段比（0.1.9 < 0.1.10 < 0.2.0）
        self.assertGreater(su._ver_tuple("0.1.10"), su._ver_tuple("0.1.9"))
        self.assertGreater(su._ver_tuple("0.2.0"), su._ver_tuple("0.1.10"))

        # 4) package_version 能读到真实版本
        self.assertEqual(su.package_version(), pkg["version"])

        # 5) relaunch 端口校验（非法端口拒绝，不拉进程）
        for bad in (0, -1, 70000):
            self.assertFalse(su.relaunch(bad))

        # 6) _port_free：未占用端口为 True（1 端口几乎不可能被我们监听）
        self.assertTrue(su._port_free(1))
