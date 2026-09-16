#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""精确定位 index.html 中「父元素含子节点、且父级的可见文本节点未包 data-i18n」的中文。
applyI18n 只处理带 data-i18n 的元素；混合内容（文本+子标签）若父级无标记，文本节点不会被翻译。
输出每个问题的行号、标签、未标记文本、以及已有的 data-i18n 标记情况。"""
import io
import re
from pathlib import Path

HTML = Path(r"E:\GoOut\MultiAgentOrchestration\app\ui\index.html")
CJK = re.compile(r"[一-鿿]")
VOID = {"area", "base", "br", "col", "embed", "hr", "img", "input",
        "link", "meta", "param", "source", "track", "wbr"}
SKIP = {"html", "head", "body", "meta", "link", "title", "script",
        "style", "pre", "code", "svg", "symbol", "defs", "lineargradient"}


def main():
    src = HTML.read_text(encoding="utf-8")
    # 计算 script/style/pre 跳过区间
    skip = []
    for tag in ("script", "style", "pre"):
        for m in re.finditer(r"<" + tag + r"\b[^>]*>", src, re.I):
            c = re.search(r"</" + tag + r"\s*>", src[m.end():], re.I)
            if c:
                skip.append((m.start(), m.end() + c.end()))

    def in_skip(p):
        return any(a <= p < b for a, b in skip)

    # 标签匹配（含闭合）
    tag_re = re.compile(r"<(/?)([a-zA-Z][a-zA-Z0-9-]*)\b([^>]*?)(/?)>", re.S)
    # 用栈跟踪嵌套；对每个元素，找它「直属文本节点」中含中文、且该元素没有 data-i18n 的情况。
    # 简化：对每个开标签，取其内容（到配对闭合），把「子元素整体」替换为占位符，
    # 剩下的文本即该元素的直属文本。若含中文且无 data-i18n → 报告。
    issues = []
    stack = []  # (tag, attrs, content_start)
    matches = list(tag_re.finditer(src))
    # 先建立每个开标签 → 配对闭标签的映射
    open_close = {}
    st = []
    for i, m in enumerate(matches):
        closing, tag, attrs, selfclose = m.group(1), m.group(2).lower(), m.group(3), m.group(4)
        if tag in VOID or selfclose:
            continue
        if closing:
            for j in range(len(st) - 1, -1, -1):
                if st[j][1] == tag:
                    ot, oi, ocontent = st[j][0], st[j][2], st[j][3]
                    open_close[oi] = (m.start(), ocontent)
                    st.pop(j)
                    break
        else:
            st.append((tag, m, i, m.end()))
    for oi, (m, content_end, content_start) in ((k, v) for k, v in open_close.items()):
        mm = matches[oi] if False else None
    # 上面配对逻辑太绕，直接重写
    issues = find_mixed(src, skip, in_skip)
    out = Path(r"E:\GoOut\MultiAgentOrchestration\tests\_out\i18n_html_mixed.txt")
    out.parent.mkdir(exist_ok=True)
    lines = []
    seen = set()
    for ln, tag, text, has_mark in issues:
        sig = (ln, text)
        if sig in seen:
            continue
        seen.add(sig)
        lines.append(f"{ln:5d} <{tag}>{'' if has_mark else ' [未标记]'}  {text!r}")
    out.write_text("\n".join(lines), encoding="utf-8")
    print(f"{len(lines)} 处混合内容未标记 → {out}")


def find_mixed(src, skip_ranges, in_skip):
    tag_re = re.compile(r"<([a-zA-Z][a-zA-Z0-9-]*)\b([^>]*)>")
    issues = []
    for m in tag_re.finditer(src):
        if in_skip(m.start()):
            continue
        tag = m.group(1).lower()
        attrs = m.group(2)
        if tag in SKIP or tag in VOID or attrs.rstrip().endswith("/"):
            continue
        # 找配对闭合（简化：同层最近的一个未配对 </tag>）
        close = re.search(r"(?!</?)[^<]*?</" + tag + r"\s*>", src[m.end():], re.I)
        if not close:
            continue
        content = src[m.end():m.end() + close.start()]
        # 内容里有子标签吗
        if "<" not in content:
            continue
        # 把子标签整段（标签及其文本）删掉，只留直属文本：
        # 简单法：按子标签的开/闭边界切开，取标签外的文本
        direct = []
        depth = 0
        seg_start = 0
        i = 0
        inner = re.compile(r"<(/?)([a-zA-Z][a-zA-Z0-9-]*)\b[^>]*?(/?)>", re.I)
        for g in inner.finditer(content):
            if g.start() > seg_start:
                direct.append(content[seg_start:g.start()])
            closing, t2, selfclose = g.group(1), g.group(2).lower(), g.group(3)
            if t2 in VOID or selfclose:
                pass
            elif closing:
                depth = max(0, depth - 1)
            else:
                depth += 1
            seg_start = g.end()
        if seg_start < len(content):
            direct.append(content[seg_start:])
        text = "".join(direct)
        ts = re.sub(r"\s+", " ", text).strip()
        if ts and CJK.search(ts):
            ln = src.count("\n", 0, m.start()) + 1
            issues.append((ln, tag, ts[:60], "data-i18n" in attrs))
    return issues


if __name__ == "__main__":
    main()
