#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""抽取目录/编目类后端数据里 UI 可见的中文字符串，供 i18n 决策。
覆盖：app/core/flows.py（流程 name/goal_hint/note）、data/catalog.json（agent note）、
自动化模板（app/core/automation.py desc/name）、技能预置包名/摘要。"""
import io
import re
import json
from pathlib import Path

ROOT = Path(r"E:\GoOut\MultiAgentOrchestration")
CJK = re.compile(r"[一-鿿]")


def cjk(s):
    return bool(s) and bool(CJK.search(s))


out = {"flows": set(), "catalog_note": set(), "auto_tpl": set(), "other": set()}

# flows.py 里的 name / goal_hint / note 字段
src = (ROOT / "app" / "core" / "flows.py").read_text(encoding="utf-8")
for field in ("name", "goal_hint", "note"):
    for m in re.finditer(r'"' + field + r'"\s*:\s*"((?:[^"\\]|\\.)*)"', src):
        v = m.group(1)
        if cjk(v):
            out["flows"].add(v.encode().decode("unicode_escape") if "\\u" in v else v.replace('\\"', '"'))

# data/catalog.json 的 note 字段
try:
    cat = json.loads((ROOT / "data" / "catalog.json").read_text(encoding="utf-8"))
    items = cat if isinstance(cat, list) else (cat.get("agents") or cat.get("items") or [])
    def walk(o):
        if isinstance(o, dict):
            for k, v in o.items():
                if k in ("note", "label", "title") and isinstance(v, str) and cjk(v):
                    out["catalog_note"].add(v)
                walk(v)
        elif isinstance(o, list):
            for x in o:
                walk(x)
    walk(cat)
except Exception as e:
    print("catalog err", e)

# automation.py 的 name / desc 模板
try:
    asrc = (ROOT / "app" / "core" / "automation.py").read_text(encoding="utf-8")
    for field in ("name", "desc", "suggested"):
        for m in re.finditer(r'"' + field + r'"\s*:\s*"((?:[^"\\]|\\.)*)"', asrc):
            if cjk(m.group(1)):
                out["auto_tpl"].add(m.group(1))
except Exception as e:
    print("auto err", e)

for key in ("flows", "catalog_note", "auto_tpl"):
    print("\n#### %s: %d" % (key, len(out[key])))
    for s in sorted(out[key]):
        print("  |", s[:90])
