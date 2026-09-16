#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""导出所有目标区域当前精确内容（仅读，不改）。"""
import subprocess
from pathlib import Path

p = Path(r"E:/GoOut/MultiAgentOrchestration/app/ui/app.js")

def find(pat):
    out = subprocess.run(["grep", "-n", pat, str(p)], capture_output=True, text=True).stdout
    nums = [int(x.split(":", 1)[0]) for x in out.splitlines() if x.strip()]
    return nums

def dump(label, pat, start, count=8):
    print("=" * 30, label, "=" * 30)
    nums = find(pat)
    if not nums:
        print("PATTERN NOT FOUND:", pat)
        return
    a = nums[0]
    b = min(nums[0] + count - 1, nums[-1]) if nums else a
    for i in range(a, b + 1):
        # 取该行
        lines = p.read_text(encoding="utf-8").splitlines()
        print(f"{i:5d} {lines[i-1]}")

dump("SKINS", "const SKINS = \[", 4770)
dump("CODE_THEMES", "const CODE_THEMES = [", 4874)
dump("mkCardHtml", "function mkCardHtml", 5974, 14)
dump("probeWire", "已适配：", 1209, 4)
dump("skin-name", "skin-name>", 1, 2)
dump("setSkin", "s ? s.name : id", 1, 14)
dump("CODE_THEMES-use", "CODE_THEMES", 1, 3)
print("DONE")
