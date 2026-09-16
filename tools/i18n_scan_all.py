#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""全仓扫描：找出所有可能进入 UI 的中文字符串（后端 Python + 前端 js/html/css），
标注来源文件与行号，用于人工判断哪些还缺 t() 包装 / 字典词条。

排除：测试、日志、提示词模板（prompts）、文档、data/ 下的运行时数据。
"""
import io
import re
import sys
from pathlib import Path

ROOT = Path(r"E:\GoOut\MultiAgentOrchestration")
CJK = re.compile(r"[一-鿿]")

SKIP_DIRS = {"__pycache__", ".git", "node_modules", ".mimosa", "_out", "runs", "tasks"}
SKIP_FILES = {"i18n.js"}   # 字典本身不扫


def iter_files():
    for pat in ("app/**/*.py", "app/**/*.js", "app/**/*.html", "app/**/*.css"):
        for p in ROOT.glob(pat):
            if any(part in SKIP_DIRS for part in p.parts):
                continue
            if p.name in SKIP_FILES:
                continue
            yield p


def scan_py(path):
    """Python：抓字符串字面量与 f-string 里的中文（含相邻隐式拼接）。"""
    src = path.read_text(encoding="utf-8", errors="replace")
    out = []
    for m in re.finditer(r'(["\'])((?:\\.|(?!\1)[^\\])*)\1', src):
        s = m.group(2)
        if CJK.search(s):
            out.append((src.count("\n", 0, m.start()) + 1, s))
    return out


def scan_js(path):
    src = path.read_text(encoding="utf-8", errors="replace")
    out = []
    for m in re.finditer(r'(["\'])((?:\\.|(?!\1)[^\\])*)\1', src):
        s = m.group(2)
        if CJK.search(s):
            # 跳过注释行
            line_start = src.rfind("\n", 0, m.start()) + 1
            prefix = src[line_start:m.start()]
            if prefix.lstrip().startswith("//") or prefix.lstrip().startswith("*"):
                continue
            out.append((src.count("\n", 0, m.start()) + 1, s))
    return out


def main():
    totals = {}
    for p in sorted(iter_files()):
        rel = p.relative_to(ROOT).as_posix()
        rows = scan_py(p) if p.suffix == ".py" else scan_js(p)
        if not rows:
            continue
        totals[rel] = rows
    print("=== 含中文的 UI 相关文件（按条数降序）===")
    for rel, rows in sorted(totals.items(), key=lambda kv: -len(kv[1])):
        print(f"{len(rows):5d}  {rel}")
    print()
    print("总条数:", sum(len(v) for v in totals.values()))
    # 明细落盘
    out = ROOT / "tests" / "_out" / "cjk_all_report.txt"
    out.parent.mkdir(parents=True, exist_ok=True)
    with out.open("w", encoding="utf-8") as f:
        for rel, rows in sorted(totals.items()):
            f.write(f"\n===== {rel} ({len(rows)}) =====\n")
            for ln, s in rows:
                f.write(f"{ln:6d}  {s[:120]!r}\n")
    print("明细 →", out)


if __name__ == "__main__":
    main()
