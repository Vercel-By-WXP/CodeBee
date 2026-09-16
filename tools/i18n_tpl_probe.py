#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""检查模板字面量 ${} 表达式内部的中文（parse_js 会把它们整体当一个 literal）。"""
import sys
from pathlib import Path

ROOT = Path(r"E:\GoOut\MultiAgentOrchestration")
sys.path.insert(0, str(ROOT / "tools"))
from i18n_extract import parse_js, has_cjk  # noqa: E402

src = (ROOT / "app" / "ui" / "app.js").read_text(encoding="utf-8")
for (s, e, q, content, kind) in parse_js(src):
    if kind != "t":
        continue
    # 提取 ${...} 内的字符串字面量
    for m in __import__("re").finditer(r"\$\{[^{}]*\}", content):
        expr = m.group(0)
        if has_cjk(expr):
            line = src.count("\n", 0, s) + 1
            print(f"{line}: {expr!r}")
