# -*- coding: utf-8 -*-
"""i18n EN 字典去重：删除后值覆盖前的重复行（后者胜出是静默 bug 源）。
三个含义冲突键按使用现场裁定正确值：
  " 条" -> " item(s)"（任务/经验/知识/蜂巢列表通用计数）
  " 字" -> " words"（章节字数）
  "章"  -> ""        （t("第")+N+t("章") 拼出 Chapter N；" chapters" 会渲染成 Chapter N chapters）
"""
import io
import re

p = 'app/ui/i18n.js'
s = io.open(p, encoding='utf-8').read()
m = re.search(r"(const EN = \{)([\s\S]*?)(\n  \};)", s)
head, body, tail = m.group(1), m.group(2), m.group(3)

OVERRIDE = {" 条": " item(s)", " 字": " words", "章": ""}
entry_re = re.compile(r'^(\s{4})"((?:[^"\\]|\\.)*)":\s*"((?:[^"\\]|\\.)*)",?\s*$')
seen = set()
out_lines = []
dropped = 0
for line in body.split("\n"):
    em = entry_re.match(line)
    if not em:
        out_lines.append(line)
        continue
    key = em.group(2)
    if key in seen:
        dropped += 1
        continue  # 丢弃后值覆盖的重复行
    seen.add(key)
    if key in OVERRIDE:
        line = '%s"%s": "%s",' % (em.group(1), key, OVERRIDE[key])
    out_lines.append(line)

s2 = s[:m.start()] + head + "\n".join(out_lines) + tail + s[m.end():]
io.open(p, 'w', encoding='utf-8', newline='\n').write(s2)
print("dropped dup lines:", dropped, "| keys kept:", len(seen))
