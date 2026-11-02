# -*- coding: utf-8 -*-
"""品牌纯净度：产品对外只叫 CodeBee，源码里不许出现别家产品名。

背景（2026-11-02 用户拍板）：本项目早期直接照外部桌面端抄版式，注释、文档、
提示词里留下了大量参考产品名。用户明确「我们是 CodeBee，不是那家产品」，
所以这些名字既不该出现在界面上，也不该出现在注释与文档里——注释是给下一位
同事读的，写别的产品名等于把身份混掉。禁词就是下面 FORBIDDEN 那条正则。

本测静态扫描 ``app/`` 与 ``DESIGN.md``：命中即红，附文件与行号，方便一次修完。
唯一不在扫描范围内的是 ``scripts/release_gate.py``——那里的同名条目是**打包
黑名单里的目录名**（作用是阻止这类本地数据目录被打进 npm 包），属安全装置，
不是文案。
"""
from __future__ import annotations

import re
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

# 只扫这些后缀，避免把图片/二进制当文本读
TEXT_SUFFIXES = {
    ".py", ".js", ".mjs", ".cjs", ".css", ".html", ".htm",
    ".md", ".json", ".txt", ".bat", ".cmd", ".yml", ".yaml", ".toml",
}

# 大小写不敏感，允许中间带空格/连字符（防止换个写法绕过）
FORBIDDEN = re.compile(r"work[\s_-]*buddy", re.IGNORECASE)


def _targets():
    files = []
    app_dir = ROOT / "app"
    if app_dir.is_dir():
        for p in app_dir.rglob("*"):
            # output/ 是运行期产物（含用户自己的稿件与导出物），不属源码；
            # __pycache__ 是字节码。都不该拿来判品牌。
            if any(part in ("output", "__pycache__", ".git") for part in p.parts):
                continue
            if p.is_file() and p.suffix.lower() in TEXT_SUFFIXES:
                files.append(p)
    for name in ("DESIGN.md", "README.md"):
        p = ROOT / name
        if p.is_file():
            files.append(p)
    return files


class BrandCopyTest(unittest.TestCase):
    def test_no_external_product_name_in_source(self):
        hits = []
        for path in _targets():
            try:
                text = path.read_text(encoding="utf-8", errors="ignore")
            except OSError:  # 读不到就别把整轮测拖死，交给别的守护测去报
                continue
            for lineno, line in enumerate(text.splitlines(), 1):
                if FORBIDDEN.search(line):
                    rel = path.relative_to(ROOT).as_posix()
                    hits.append("%s:%d  %s" % (rel, lineno, line.strip()[:100]))
        self.assertEqual(
            [], hits,
            "源码里出现了外部产品名，请改成 CodeBee 自己的说法：\n" + "\n".join(hits),
        )

    def test_scan_actually_covers_files(self):
        """防空转：扫描面塌成 0 个文件时，上一条会假绿。"""
        self.assertGreater(len(_targets()), 50)


if __name__ == "__main__":
    unittest.main()
