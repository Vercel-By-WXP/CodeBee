# -*- coding: utf-8 -*-
"""发布渠道口径：0.y.z 预览版 / 1.0.0+ 正式版。

这些断言锁的是「口径同源」：代码判定、README/QUICKSTART 的对外说法、npm dist-tag
三者不能各说各话。历史上 README 写「稳定版」而版本号还在 0.x，用户按稳定版的兼容
承诺使用，遇到 CLI 参数变更就成事故。
"""
import json
import os
import re
import unittest

from app.core import release_channel as rc

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def _read(name):
    with open(os.path.join(ROOT, name), encoding="utf-8") as fh:
        return fh.read()


class ChannelOfTest(unittest.TestCase):
    def test_preview_for_zero_major(self):
        for version in ("0.1.120", "0.0.1", "0.99.0"):
            self.assertEqual(rc.channel_of(version), rc.PREVIEW, version)
            self.assertEqual(rc.label_of(version), "预览版")

    def test_stable_from_one(self):
        for version in ("1.0.0", "1.4.2", "10.0.0"):
            self.assertEqual(rc.channel_of(version), rc.STABLE, version)
            self.assertEqual(rc.label_of(version), "正式版")

    def test_prerelease_is_preview_even_after_one(self):
        # 1.0.0-rc.1 不是稳定版：不能让用户按正式版的兼容承诺去用。
        self.assertEqual(rc.channel_of("1.0.0-rc.1"), rc.PREVIEW)

    def test_unknown_is_not_guessed(self):
        for value in ("", None, "v1.2", "abc", "0.1"):
            self.assertEqual(rc.channel_of(value), rc.UNKNOWN, value)
            self.assertEqual(rc.label_of(value), "未知版本")

    def test_dist_tags(self):
        self.assertEqual(rc.dist_tags("0.1.120"), ("latest", "preview"))
        self.assertEqual(rc.dist_tags("1.0.0"), ("latest",))
        self.assertEqual(rc.dist_tags("bogus"), ())


class RepoContractTest(unittest.TestCase):
    def test_current_matches_package_json(self):
        with open(os.path.join(ROOT, "package.json"), encoding="utf-8") as fh:
            packaged = json.load(fh)["version"]
        info = rc.current()
        self.assertEqual(info["version"], packaged)
        self.assertEqual(info["channel"], rc.channel_of(packaged))
        self.assertNotEqual(info["channel"], rc.UNKNOWN)

    def test_version_file_agrees_with_package_json(self):
        # VERSION 与 package.json 是两处独立来源，发版时最容易漏掉其中一处。
        version_file = _read("VERSION").strip()
        with open(os.path.join(ROOT, "package.json"), encoding="utf-8") as fh:
            packaged = json.load(fh)["version"]
        self.assertEqual(version_file, packaged)

    def test_readme_states_both_channels(self):
        readme = _read("README.md")
        self.assertIn("版本与发布渠道", readme)
        self.assertIn("预览版", readme)
        self.assertIn("正式版", readme)
        # dist-tag 说法必须和代码一致：预览期一定提到 preview 通道。
        if rc.channel_of(rc.current()["version"]) == rc.PREVIEW:
            self.assertIn("codebee@preview", readme)

    def test_quickstart_states_current_channel(self):
        quickstart = _read("QUICKSTART.md")
        label = rc.label_of(rc.current()["version"])
        self.assertIn("版本渠道", quickstart)
        self.assertIn(label, quickstart)

    def test_install_command_uses_latest(self):
        for name in ("README.md", "QUICKSTART.md"):
            self.assertIn("codebee@latest", _read(name), name)
        # 不许出现未加 tag 的 npx 形式：会把预览期与正式期混成一个默认通道。
        for name in ("README.md", "QUICKSTART.md"):
            raw = _read(name)
            self.assertIsNone(re.search(r"npx\s+codebee(?![@\w])", raw), name)


if __name__ == "__main__":
    unittest.main()
