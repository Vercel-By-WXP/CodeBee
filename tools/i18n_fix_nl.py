#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""把 app.js 里 t("...") 双引号实参内的字面 \\\\n（反斜杠+n）归一成真换行转义 \\n。

背景：历史代码里部分 uiConfirm / 提示文案在 JS 字符串中写成了 "\\\\n"，
运行时得到的是字面「反斜杠+n」两字符，而 i18n.js 字典对应词条用的是真换行，
两边 key 不相等 —— en 模式匹配不上回落中文，且 esc() + white-space:pre-wrap
会把这两字符原样显示成 \n（确认框里是可见的乱码换行）。
归一后：运行时 key 与字典对齐，换行正常渲染。

只动 t("...") 双引号实参内的 \\\\n，不碰其它字面量（如 split("\\n") 逻辑串）。
"""
import re
from pathlib import Path

APP_JS = Path(r"E:\GoOut\MultiAgentOrchestration\app\ui\app.js")

src = APP_JS.read_text(encoding="utf-8")

changed = 0


def repl(m):
    global changed
    inner = m.group(1)
    new_inner = inner.replace("\\\\n", "\\n")
    if new_inner != inner:
        changed += inner.count("\\\\n")
    return 't("' + new_inner + '"' + m.group(2)


# t("...") 后紧跟 , 或 ) —— 仅静态双引号实参
out = re.sub(r't\("((?:\\.|[^"\\])*)"([,)])', repl, src)
with APP_JS.open("w", encoding="utf-8", newline="") as f:
    f.write(out)
print(f"归一 {changed} 处 \\\\n → \\n")
