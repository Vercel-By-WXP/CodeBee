#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""抽取后端内置「展示型」中文字符串（flows 名称/提示/说明/rubric、catalog note、
automation 模板、内置经验包名/说明、import 来源名/说明、selfupdate note），
打印成清单供翻译。运行时数据（自动教训等）不抽。"""
import io
import json
import re
from pathlib import Path

ROOT = Path(r"E:\GoOut\MultiAgentOrchestration")
CJK = re.compile(r"[一-鿿]")
seen = set()


def add(s):
    s = s.replace('\\"', '"').replace("\\n", "\n")
    if CJK.search(s) and s not in seen:
        seen.add(s)


# 1) flows.py：name / goal_hint / note / rubric 数组
src = (ROOT / "app" / "core" / "flows.py").read_text(encoding="utf-8")
for m in re.finditer(r'"(name|goal_hint|note)"\s*:\s*"((?:[^"\\]|\\.)*)"', src):
    add(m.group(2))
for m in re.finditer(r'"rubric"\s*:\s*\[([^\]]*)\]', src):
    for sm in re.finditer(r'"([^"]*)"', m.group(1)):
        add(sm.group(1))

# 2) skills.py：内置包 name/note
src = (ROOT / "app" / "core" / "skills.py").read_text(encoding="utf-8")
head = src[:4000]   # 内置表在文件头部
for m in re.finditer(r'"(name|note)"\s*:\s*"((?:[^"\\]|\\.)*)"', head):
    add(m.group(2))

# 3) market.py：内置市场条目 name/desc
src = (ROOT / "app" / "core" / "market.py").read_text(encoding="utf-8")
for m in re.finditer(r'"(name|desc)"\s*:\s*"((?:[^"\\]|\\.)*)"', src):
    add(m.group(2))

# 4) automation.py：模板 name/desc
src = (ROOT / "app" / "core" / "automation.py").read_text(encoding="utf-8")
for m in re.finditer(r'"(name|desc)"\s*:\s*"((?:[^"\\]|\\.)*)"', src):
    add(m.group(2))

# 5) data/catalog.json：note
try:
    cat = json.loads((ROOT / "data" / "catalog.json").read_text(encoding="utf-8"))

    def walk(o):
        if isinstance(o, dict):
            for k, v in o.items():
                if k == "note" and isinstance(v, str):
                    add(v)
                else:
                    walk(v)
        elif isinstance(o, list):
            for x in o:
                walk(x)
    walk(cat)
except Exception as e:
    print("catalog err:", e)

# 6) modelhub.py：来源名 label + note（仅静态提示，跳过带 %s 的动态串）
src = (ROOT / "app" / "core" / "modelhub.py").read_text(encoding="utf-8")
for m in re.finditer(r'"((?:[^"\\]|\\.)*[一-鿿](?:[^"\\]|\\.)*)"', src):
    s = m.group(1)
    if "%s" in s or "%d" in s:
        continue
    add(s)

# 7) selfupdate.py：note
src = (ROOT / "app" / "core" / "selfupdate.py").read_text(encoding="utf-8")
for m in re.finditer(r'"((?:[^"\\]|\\.)*[一-鿿](?:[^"\\]|\\.)*)"', src):
    add(m.group(1))

out = sorted(seen)
print(len(out))
for s in out:
    print("|" + s)
