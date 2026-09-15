# -*- coding: utf-8 -*-
"""修 _api_browse 盘符字符串转义："%s:\" -> "%s:\\" （repr 视角）。"""
import io

p = "app/main.py"
src = io.open(p, encoding="utf-8").read()
BAD = '["%s:\\" % c'    # 实际文本：["%s:\" % c   （反斜杠转义了引号，字符串未闭合）
GOOD = '["%s:\\\\" % c'  # 实际文本：["%s:\\" % c  （两个反斜杠=字面反斜杠+闭合引号）
n = src.count(BAD)
print("找到坏转义:", n)
assert n == 2, "期望 2 处，实际 %d" % n
src = src.replace(BAD, GOOD)
io.open(p, "w", encoding="utf-8", newline="").write(src)
import py_compile
py_compile.compile(p, doraise=True)
print("main.py 语法 OK")
