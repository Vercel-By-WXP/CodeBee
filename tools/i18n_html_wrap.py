#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""把 index.html 里「有子元素、且直属文本节点含中文、且自身无 data-i18n」的
可见元素，其每个直属中文文本段用 <span data-i18n="原文"> 包起来，让 applyI18n 能命中。

只处理叶子文本节点（在元素开/闭标签之间、且不被更深元素包裹的部分）。
跳过 script/style/pre/code/svg/use/symbol 等区域。保守：属性值、注释、
含 &实体 的、以及已经带 data-i18n 的元素一律不动。"""
import re
import sys
from pathlib import Path

INDEX = Path(r"E:\GoOut\MultiAgentOrchestration\app\ui\index.html")
CJK = re.compile(r"[一-鿿]")
VOID = {"area", "base", "br", "col", "embed", "hr", "img", "input", "link",
        "meta", "param", "source", "track", "wbr"}
SKIP_TAGS = {"html", "head", "body", "meta", "link", "title", "script",
             "style", "pre", "code", "svg", "use", "symbol", "path", "circle",
             "lineargradient", "stop", "defs", "rect", "g"}


def build_skip(src):
    spans = []
    for tag in ("script", "style", "pre", "code"):
        for m in re.finditer(r"<" + tag + r"\b[^>]*>", src, re.I):
            c = re.search(r"</" + tag + r"\s*>", src[m.end():], re.I)
            if c:
                spans.append((m.start(), m.end() + c.end()))
    return spans


TOKEN = re.compile(r"<!--.*?-->|</?[a-zA-Z][a-zA-Z0-9-]*\b[^>]*?/?>", re.S)


def process(src):
    skip = build_skip(src)

    def in_skip(p):
        return any(a <= p < b for a, b in skip)

    # 逐元素：用栈找出每个元素的「内容区间」和它的直属文本节点。
    # 简化稳妥：把整篇按标签流扫描，维护元素栈；每当遇到一个文本 chunk（标签之间），
    # 判断它是否直接挂在某个「非跳过、自身无 data-i18n」元素下，且含 CJK 且无 < >。
    edits = []  # (text_start, text_end, wrapped_html)
    stack = []  # list of (tag, attrs, open_end, has_datai18n)
    pos = 0
    for m in TOKEN.finditer(src):
        if in_skip(m.start()):
            # 标签之间的文本若落在 skip 区也不处理；但我们要照常推进栈
            pass
        chunk = src[pos:m.start()]
        if chunk and stack:
            consider_text(chunk, pos, stack[-1], edits)
        pos = m.end()
        tok = m.group(0)
        if tok.startswith("<!--"):
            continue
        close = tok[1] == "/"
        tm = re.match(r"</?([a-zA-Z][a-zA-Z0-9-]*)", tok)
        if not tm:
            continue
        tag = tm.group(1).lower()
        if close:
            # 弹到匹配的开标签
            for j in range(len(stack) - 1, -1, -1):
                if stack[j][0] == tag:
                    del stack[j]
                    break
            continue
        if tag in VOID or tok.rstrip().endswith("/"):
            continue
        attrs = tok[len(tm.group(0)):-1]
        stack.append((tag, attrs, m.end(), "data-i18n" in attrs))
    tail = src[pos:]
    if tail and stack:
        consider_text(tail, pos, stack[-1], edits)

    # 从后往前应用
    for (s, e, html) in sorted(edits, key=lambda x: -x[0]):
        src = src[:s] + html + src[e:]
    return src


def consider_text(chunk, start, parent, edits):
    tag, attrs, _, has_mark = parent
    if tag in SKIP_TAGS:
        return
    if has_mark:
        return
    if "<" in chunk or ">" in chunk:
        return
    # 拆前导/尾随空白，只包中间含中文的部分
    lead = chunk[:len(chunk) - len(chunk.lstrip())]
    trail = chunk[len(chunk.rstrip()):]
    core = chunk.strip()
    if not core or not CJK.search(core):
        return
    if "&" in core:
        return
    key = re.sub(r"\s+", " ", core)
    esc_key = key.replace("&", "&amp;").replace('"', "&quot;")
    s0 = start + len(lead)
    s1 = start + len(lead) + len(core)
    edits.append((s0, s1, '<span data-i18n="%s">%s</span>' % (esc_key, core)))


def main():
    src = INDEX.read_text(encoding="utf-8")
    before = len(re.findall(r"data-i18n=", src))
    out = process(src)
    INDEX.write_text(out, encoding="utf-8")
    after = len(re.findall(r"data-i18n=", src) if False else re.findall(r"data-i18n=", out))
    print(f"data-i18n 标记：{before} → {after}（新增 {after - before}）")


if __name__ == "__main__":
    main()
