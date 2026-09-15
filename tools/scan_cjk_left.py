# -*- coding: utf-8 -*-
"""扫 app.js 里未包 t() 的纯中文文案串（跳过注释/HTML片段/模板表达式）。"""
import re
from pathlib import Path

src = Path("app/ui/app.js").read_text(encoding="utf-8")
cjk = re.compile(r"[\u4e00-\u9fff]")
BACKSLASH = chr(92)
hits = []
i, n = 0, len(src)
while i < n:
    c = src[i]
    if c == "/" and i + 1 < n and src[i + 1] == "/":
        j = src.find("\n", i)
        i = n if j < 0 else j + 1
        continue
    if c == "/" and i + 1 < n and src[i + 1] == "*":
        j = src.find("*/", i)
        i = n if j < 0 else j + 2
        continue
    if c in ('"', "'", "`"):
        q = c
        j = i + 1
        buf = []
        while j < n:
            cc = src[j]
            if cc == BACKSLASH and j + 1 < n:
                buf.append(cc)
                buf.append(src[j + 1])
                j += 2
                continue
            if cc == q:
                break
            if cc == "\n":
                break
            buf.append(cc)
            j += 1
        content = "".join(buf)
        line = src.count("\n", 0, i) + 1
        prefix = src[max(0, i - 4): i]
        if cjk.search(content) and "t(" not in prefix:
            hits.append((line, content))
        i = j + 1 if j < n else n
        continue
    i += 1

pure = [(l, c) for (l, c) in hits if not any(ch in c for ch in ("<", ">", "${", "="))]
print(f"未包 t() 的纯中文文案串: {len(pure)}")
seen = set()
for l, c in sorted(pure):
    if c in seen:
        continue
    seen.add(c)
    print(f"{l}: {c[:90]!r}")
