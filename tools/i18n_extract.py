#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""一次性 i18n 提取 + 重写：把 app.js 里所有含中文的字符串字面量包成 t('...')，
把 index.html 里所有可见文案（文本/placeholder/title/aria-label）剥成 data-i18n，
并输出唯一中文串列表供翻译。

执行后产物：
  - app/ui/app.js         被原地改写
  - app/ui/index.html     被原地改写（加 data-i18n）
  - tools/i18n_strings.txt  唯一中文串（供翻译用）
"""
import re
import sys
from pathlib import Path

ROOT = Path(r"E:\GoOut\MultiAgentOrchestration")
APP_JS = ROOT / "app" / "ui" / "app.js"
INDEX = ROOT / "app" / "ui" / "index.html"
OUT = ROOT / "tools" / "i18n_strings.txt"

CJK_RE = re.compile(r"[\u4e00-\u9fff\u3000-\u303f\uff00-\uffef]")


def has_cjk(s: str) -> bool:
    return bool(CJK_RE.search(s))


# ---------- JS 解析：扫出每个字符串字面量（' " `）的起止偏移与内容 ----------
def parse_js(src: str):
    out = []  # (start, end, quote, content, kind) kind='s'|'d'|'t'
    i, n = 0, len(src)
    while i < n:
        c = src[i]
        # 行注释
        if c == "/" and i + 1 < n and src[i + 1] == "/":
            j = src.find("\n", i)
            i = n if j < 0 else j + 1
            continue
        # 块注释
        if c == "/" and i + 1 < n and src[i + 1] == "*":
            j = src.find("*/", i)
            i = n if j < 0 else j + 2
            continue
        # 字符串
        if c in ("'", '"'):
            q = c
            j = i + 1
            buf = []
            while j < n:
                cc = src[j]
                if cc == "\\" and j + 1 < n:
                    buf.append(cc)
                    buf.append(src[j + 1])
                    j += 2
                    continue
                if cc == q:
                    break
                if cc == "\n":
                    # 字符串内换行 = 错误，跳出
                    break
                buf.append(cc)
                j += 1
            out.append((i, j + 1 if j < n else j, q, "".join(buf), "s" if q == "'" else "d"))
            i = j + 1 if j < n else n
            continue
        # 模板字面量
        if c == "`":
            j = i + 1
            buf = []
            while j < n:
                cc = src[j]
                if cc == "\\" and j + 1 < n:
                    buf.append(cc)
                    buf.append(src[j + 1])
                    j += 2
                    continue
                if cc == "`":
                    break
                if cc == "$" and j + 1 < n and src[j + 1] == "{":
                    buf.append("${")
                    j += 2
                    depth = 1
                    while j < n and depth > 0:
                        if src[j] == "{":
                            depth += 1
                        elif src[j] == "}":
                            depth -= 1
                            if depth == 0:
                                buf.append("}")
                                j += 1
                                break
                        buf.append(src[j])
                        j += 1
                    continue
                buf.append(cc)
                j += 1
            out.append((i, j + 1 if j < n else j, "`", "".join(buf), "t"))
            i = j + 1 if j < n else n
            continue
        i += 1
    return out


# ---------- 对字面量做最小改写 ----------
# 已包过 t(...) 的不重复；模板字面量整体不动（人工按需处理）
def rewrite_app_js() -> set:
    src = APP_JS.read_text(encoding="utf-8")
    lits = parse_js(src)
    # 收集中文 key
    cjk_keys = set()
    # 收集已包过 t() 的字面量：扫描所有 t('...') / t("...") 的内容记下来，避免重包
    already_t = set()
    for m in re.finditer(r"\bt\(\s*(['\"])((?:\\.|(?!\1).)*)\1", src):
        already_t.add(m.group(2))

    # 从后往前改写（偏移不变）
    edits = []
    for (s, e, q, content, kind) in lits:
        if not has_cjk(content):
            continue
        if kind == "t":
            # 模板字面量不在自动 wrap 范围（HTML + 表达式整体翻译要人工）；
            # 内部片段即使有中文也不进 key 列表，避免污染字典
            continue
        # kind s/d：静态串
        # 含 HTML 标签 / 实体 / 等号+引号（HTML 属性模式）的都当作 HTML 片段跳过
        if any(ch in content for ch in ("<", ">", "&")):
            continue
        if '="' in content or "='" in content:
            continue
        # 单/双引号配 `=` 也是 HTML 属性（无空格）
        if re.search(r"[a-zA-Z]\s*=\s*[\"']", content):
            continue
        cjk_keys.add(content)
        if content in already_t:
            continue
        escaped = content.replace("\\", "\\\\").replace(q, "\\" + q)
        new = "t('" + escaped + "')" if q == "'" else 't("' + escaped + '")'
        edits.append((s, e, new))

    # 应用编辑
    for (s, e, new) in sorted(edits, key=lambda x: -x[0]):
        src = src[:s] + new + src[e:]
    APP_JS.write_text(src, encoding="utf-8")
    return cjk_keys


# ---------- index.html：给所有含中文的元素加 data-i18n ----------
def rewrite_index_html(cjk_keys: set):
    src = INDEX.read_text(encoding="utf-8")
    # 处理 <script>、<style>、<pre><code> 内的内容不替换
    skip_ranges = []
    for tag in ("script", "style", "pre", "code"):
        for m in re.finditer(r"<" + tag + r"\b[^>]*>", src, re.IGNORECASE):
            open_end = m.end()
            close = re.search(r"</" + tag + r"\s*>", src[open_end:], re.IGNORECASE)
            if close:
                skip_ranges.append((m.start(), open_end + close.end()))
    skip_ranges.sort()
    merged = []
    for s, e in skip_ranges:
        if merged and s <= merged[-1][1]:
            merged[-1] = (merged[-1][0], max(merged[-1][1], e))
        else:
            merged.append((s, e))

    def in_skip(pos: int) -> bool:
        for s, e in merged:
            if s <= pos < e:
                return True
        return False

    # 跳过结构性 / 无文本内容的标签
    SKIP_TAGS = {"html", "head", "body", "meta", "link", "title", "script", "style",
                 "pre", "code", "svg", "symbol", "defs", "lineargradient"}

    edits = []
    tag_re = re.compile(r"<([a-zA-Z][a-zA-Z0-9-]*)\b([^>]*)>")
    for m in tag_re.finditer(src):
        if in_skip(m.start()):
            continue
        tag = m.group(1).lower()
        if tag in SKIP_TAGS:
            continue
        attrs = m.group(2)
        tag_start = m.start()
        tag_end = m.end()
        if attrs.strip().endswith("/"):
            continue
        # 找关闭标签
        close = re.search(r"</" + tag + r"\s*>", src[tag_end:], re.IGNORECASE)
        if not close:
            continue
        content_start = tag_end
        content_end = tag_end + close.start()
        content = src[content_start:content_end]
        # 解析属性
        existing = {}
        for am in re.finditer(r'([a-zA-Z][\w:-]*)\s*=\s*"([^"]*)"', attrs):
            existing[am.group(1).lower()] = am.group(2)
        for am in re.finditer(r"([a-zA-Z][\w:-]*)\s*=\s*'([^']*)'", attrs):
            existing[am.group(1).lower()] = am.group(2)
        # 纯文本 = 去除子标签
        text_only = re.sub(r"<[^>]+>", "", content).strip()
        # 只处理「无子标签」的纯文本节点（混合内容容易误伤）
        has_child_tags = "<" in content
        # 文本翻译
        if text_only and not has_child_tags and has_cjk(text_only):
            if "data-i18n" not in attrs:
                edits.append((tag_start, tag_start + len("<" + tag),
                              '<{0} data-i18n="{1}"'.format(tag, _esc_attr(text_only))))
                cjk_keys.add(text_only)
        # placeholder / title / aria-label
        for attr in ("placeholder", "title", "aria-label"):
            v = existing.get(attr, "")
            if v and has_cjk(v):
                cjk_keys.add(v)
                key_attr = "data-i18n-" + _short(attr)
                if key_attr not in attrs:
                    # 插到 </tag> 之前（在 attrs 末尾）
                    edits.append((tag_end - 1, tag_end - 1,
                                  ' {0}="{1}"'.format(key_attr, _esc_attr(v))))

    # 从后往前应用
    for (s, e, new) in sorted(edits, key=lambda x: -x[0]):
        src = src[:s] + new + src[e:]
    INDEX.write_text(src, encoding="utf-8")


def _short(attr: str) -> str:
    return {"aria-label": "aria", "placeholder": "ph", "title": "title"}.get(attr, attr)


def _esc_attr(s: str) -> str:
    return s.replace("&", "&amp;").replace('"', "&quot;")


def main():
    keys = rewrite_app_js()
    print(f"[1/2] app.js 改写完成，{len(keys)} 条中文 key 收集")
    rewrite_index_html(keys)
    print(f"[2/2] index.html 加 data-i18n 完成，{len(keys)} 条中文 key 收集")
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text("\n".join(sorted(keys)), encoding="utf-8")
    print(f"    唯一中文串写入 {OUT}")


if __name__ == "__main__":
    main()
