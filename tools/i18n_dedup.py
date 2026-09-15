#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""去重 i18n.js 字典里的重复 key：最后一次出现为准。"""
import re
from pathlib import Path

p = Path(r"E:\GoOut\MultiAgentOrchestration\app\ui\i18n.js")
src = p.read_text(encoding="utf-8")
lines = src.split("\n")
last = {}  # key -> (value, line_index)
order = []  # key insertion order
for i, line in enumerate(lines):
    m = re.match(r'^\s*"((?:[^"\\]|\\.)*)":\s*"((?:[^"\\]|\\.)*)"\s*,?\s*$', line)
    if not m:
        continue
    k, v = m.group(1), m.group(2)
    if k not in last:
        order.append(k)
    last[k] = (v, i)

# 重写 EN 对象：用 last 字典，按 order 顺序输出
out = []
in_dict = False
brace_depth = 0
for i, line in enumerate(lines):
    if "const EN = {" in line:
        in_dict = True
        out.append(line)
        # 找到 } 结束位置之前的所有 key 行，替换为合并后的
        # 简单方案：先把所有后续"key: value"行替换为空，最后在 } 前插入
        brace_depth = 1
        continue
    if in_dict and line.strip() == "};":
        # 关闭前插入合并后的键值
        for k in order:
            v = last[k][0]
            # 注意：k 和 v 可能含 \n 等特殊字符，原文件按字面写
            out.append('    "' + k + '": "' + v + '",')
        out.append(line)
        in_dict = False
        continue
    if in_dict:
        # 跳过原 key/value 行（保留空行 / 注释）
        m = re.match(r'^\s*"((?:[^"\\]|\\.)*)":\s*"((?:[^"\\]|\\.)*)"\s*,?\s*$', line)
        if m:
            continue  # 丢掉原重复定义
    out.append(line)

p.write_text("\n".join(out), encoding="utf-8")
print(f"去重完成：{len(last)} 条唯一 key")