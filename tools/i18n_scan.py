#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""扫描 app.js / index.html 尚未国际化的中文，输出报告供人工修复。

只读不改写。产物打印到 stdout：
  [A] app.js 中含中文、但未包 t() 的静态字符串字面量（含行号）
  [B] app.js 中含中文静态片段的模板字面量（含行号 + 中文片段）
  [C] index.html 中含中文但缺 data-i18n* 标记的可见文本 / 属性
"""
import re
import sys
from pathlib import Path

ROOT = Path(r"E:\GoOut\MultiAgentOrchestration")
APP_JS = ROOT / "app" / "ui" / "app.js"
INDEX = ROOT / "app" / "ui" / "index.html"

sys.path.insert(0, str(ROOT / "tools"))
from i18n_extract import parse_js, has_cjk  # noqa: E402


def lineno(src, pos):
    return src.count("\n", 0, pos) + 1


def in_t_call(src, pos):
    """字面量起点是否位于某个 t( ... ) 的实参区域内（含三元等内部写法）。"""
    depth = 0
    i = pos - 1
    while i >= 0:
        c = src[i]
        if c == ")":
            depth += 1
        elif c == "(":
            if depth == 0:
                # 匹配到最近的开括号：看它是否是 t( 的一部分
                j = i - 1
                while j >= 0 and src[j] in " \t":
                    j -= 1
                if j >= 0 and src[j] == "t":
                    prev = src[j - 1] if j > 0 else ""
                    if not re.match(r"[A-Za-z0-9_$]", prev):
                        return True
                return False
            depth -= 1
        elif c == "\n" and depth == 0 and i - pos > -1:
            pass
        i -= 1
        if pos - i > 4000:   # 防御：超长回溯视为不在 t() 内
            return False
    return False


def scan_app_js():
    src = APP_JS.read_text(encoding="utf-8")
    lits = parse_js(src)
    static_unwrapped = []
    template_cjk = []
    for (s, e, q, content, kind) in lits:
        if not has_cjk(content):
            continue
        if in_t_call(src, s):
            # 已包 t()（含 t(cond ? "a" : "b") 这类内部字面量）
            continue
        if kind in ("s", "d"):
            static_unwrapped.append((lineno(src, s), content))
        else:
            segs = re.split(r"\$\{[^{}]*\}", content)
            cjk = [x.strip() for x in segs if has_cjk(x) and x.strip()]
            if cjk:
                template_cjk.append((lineno(src, s), cjk))
    return static_unwrapped, template_cjk


SKIP_TAGS = {"html", "head", "body", "meta", "link", "title", "script", "style",
             "pre", "code", "svg", "symbol", "defs", "lineargradient"}


def scan_index_html():
    src = INDEX.read_text(encoding="utf-8")
    skip_ranges = []
    for tag in ("script", "style", "pre", "code"):
        for m in re.finditer(r"<" + tag + r"\b[^>]*>", src, re.IGNORECASE):
            open_end = m.end()
            close = re.search(r"</" + tag + r"\s*>", src[open_end:], re.IGNORECASE)
            if close:
                skip_ranges.append((m.start(), open_end + close.end()))
    merged = []
    for s, e in sorted(skip_ranges):
        if merged and s <= merged[-1][1]:
            merged[-1] = (merged[-1][0], max(merged[-1][1], e))
        else:
            merged.append((s, e))

    def in_skip(pos):
        return any(s <= pos < e for s, e in merged)

    problems = []
    tag_re = re.compile(r"<([a-zA-Z][a-zA-Z0-9-]*)\b([^>]*)>")
    for m in tag_re.finditer(src):
        if in_skip(m.start()):
            continue
        tag = m.group(1).lower()
        if tag in SKIP_TAGS:
            continue
        attrs = m.group(2)
        if attrs.strip().endswith("/"):
            continue
        # 语言切换按钮的可见文字是本语言自称（endonym），按惯例不翻译
        if re.search(r"\bdata-lang\b", attrs):
            continue
        close = re.search(r"</" + tag + r"\s*>", src[m.end():], re.IGNORECASE)
        content = ""
        if close:
            content = src[m.end():m.end() + close.start()]
        text_only = re.sub(r"<[^>]+>", "", content).strip()
        has_child = "<" in content
        existing = {}
        for am in re.finditer(r'([a-zA-Z][\w:-]*)\s*=\s*"([^"]*)"', attrs):
            existing[am.group(1).lower()] = am.group(2)
        # 文本未标记
        if text_only and not has_child and has_cjk(text_only) and "data-i18n" not in attrs:
            problems.append((lineno(src, m.start()), tag, "text", text_only[:60]))
        for attr in ("placeholder", "title", "aria-label"):
            v = existing.get(attr, "")
            kattr = {"aria-label": "data-i18n-aria", "placeholder": "data-i18n-ph", "title": "data-i18n-title"}[attr]
            if v and has_cjk(v) and kattr not in attrs:
                problems.append((lineno(src, m.start()), tag, attr, v[:60]))
    return problems


def main():
    su, tc = scan_app_js()
    print("=== [A] app.js 未包 t() 的静态中文字面量：{0} 处".format(len(su)))
    for ln, c in sorted(set(su)):
        print("  {0}: {1!r}".format(ln, c))
    print()
    print("=== [B] app.js 含中文静态片段的模板字面量：{0} 处".format(len(tc)))
    for ln, segs in sorted(tc):
        print("  {0}: {1}".format(ln, " | ".join(repr(x) for x in segs)))
    print()
    htmlp = scan_index_html()
    print("=== [C] index.html 缺 data-i18n* 的中文：{0} 处".format(len(htmlp)))
    for ln, tag, kind, txt in sorted(set(htmlp)):
        print("  {0}: <{1}> {2} = {3!r}".format(ln, tag, kind, txt))


if __name__ == "__main__":
    main()
