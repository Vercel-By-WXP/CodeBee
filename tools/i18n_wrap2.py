#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""i18n 第二轮机械改写（v2）：把 app.js 中尚未国际化的「可见文案」包上 t()。

与 tools/i18n_extract.py（v1）的区别：
  v1 跳过了所有含 HTML 标签的字面量 → 本轮专门处理拼接 HTML 里的可见文案
  （文本节点 + title/placeholder/aria-label 属性值），tag/onclick 等结构不动。

改写方式：把字面量拆成「原文片段 + t("文案") + 原文片段」的字符串拼接，
原文片段直接从源码切片（保持原有转义），t() 的 key 用 JS 双引号安全转义。

跳过规则（防止误伤逻辑）：
  1. 已在 t(...) 调用内
  2. 比较运算（== != === !==）紧跟的字面量（与服务端中文数据比对）
  3. .split( / .includes( / .startsWith( / .indexOf( / .replace( 等结构用法
  4. 字面量后紧跟冒号（对象键 / case 标签）
  5. 模块加载期常量表（TAB_TITLES 等 6 个，使用点已包 t()，定义处必须留中文 key）
  6. 显式跳过清单（document.title 的中文分支等）
  7. 文案片段含引号/反斜杠/换行（转义风险，留给人工）
"""
import re
import sys
from pathlib import Path

ROOT = Path(r"E:\GoOut\MultiAgentOrchestration")
APP_JS = ROOT / "app" / "ui" / "app.js"

sys.path.insert(0, str(ROOT / "tools"))
from i18n_extract import parse_js, has_cjk  # noqa: E402

# 模块加载期常量：定义处字面量是数据 key / 由使用点翻译，绝不能在定义处包 t()
CONST_NAMES = ("TAB_TITLES", "SU_MODE_TXT", "AUTO_WD", "GIT_STATUS_LABEL",
               "GIT_STATE_CHIP", "CAT_COLOR")

# 显式跳过的整串内容
SKIP_CONTENTS = {
    "CodeBee · 多智能体编排台",   # document.title 三元里显式的中文分支（en 分支即译文）
}

ATTR_RE = re.compile(r'(title|placeholder|aria-label)\s*=\s*(\\?")((?:[^"\\]|\\.)*?)\2')


def js_escape(s: str) -> str:
    return s.replace("\\", "\\\\").replace('"', '\\"').replace("\n", "\\n").replace("\r", "")


def const_spans(src: str):
    """const NAME = ...; 的源码区间（到深度归零的 ; 为止）"""
    spans = []
    for name in CONST_NAMES:
        m = re.search(r"\bconst " + name + r"\b", src)
        if not m:
            continue
        i, depth = m.start(), 0
        while i < len(src):
            c = src[i]
            if c in "{[(":
                depth += 1
            elif c in "}])":
                depth -= 1
            elif c == ";" and depth <= 0:
                break
            i += 1
        spans.append((m.start(), i))
    return spans


def in_t_call(src, pos):
    depth = 0
    i = pos - 1
    while i >= 0 and pos - i < 4000:
        c = src[i]
        if c == ")":
            depth += 1
        elif c == "(":
            if depth == 0:
                j = i - 1
                while j >= 0 and src[j] in " \t\n":
                    j -= 1
                if j >= 0 and src[j] == "t":
                    prev = src[j - 1] if j > 0 else ""
                    if not re.match(r"[A-Za-z0-9_$]", prev):
                        return True
                return False
            depth -= 1
        i -= 1
    return False


def preceded_by(src, pos, pattern):
    """pos 前的紧邻非空白文本是否以 pattern 结尾"""
    j = pos - 1
    while j >= 0 and src[j] in " \t\n":
        j -= 1
    tail = src[max(0, j - 16):j + 1]
    return bool(re.search(pattern + r"\s*$", tail))


def wrap_spans(content):
    """在字面量内容里找出该包 t() 的 (start, end, text) 区间。

    两类目标：
      a) 文本节点里的中文：按 <tag> 切段后，文本段取「最后一个 > 之后」的部分
         （段首可能是上一行拼接遗留的属性尾巴，如 \\')">）
      b) title/placeholder/aria-label 属性值里的中文
    """
    spans = []
    # (a) 文本节点
    parts = re.split(r"(<[^<>]*>)", content)
    off = 0
    for p in parts:
        if p.startswith("<") and p.endswith(">"):
            off += len(p)
            continue
        cut = p.rfind(">")
        text_start = cut + 1 if cut >= 0 else 0
        seg = p[text_start:]
        if has_cjk(seg) and not any(ch in seg for ch in ("\"", "'", "\\", "${", "`")):
            spans.append((off + text_start, off + len(p), seg))
        off += len(p)
    # (b) 属性值
    for m in ATTR_RE.finditer(content):
        val = m.group(3)
        if has_cjk(val) and not any(ch in val for ch in ("\"", "'", "\\", "${", "`")):
            spans.append((m.start(3), m.end(3), val))
    # 去重叠（属性值在 tag 内、文本节点在 tag 外，理论上不重叠；保险起见）
    spans.sort()
    out = []
    for s, e, t in spans:
        if out and s < out[-1][1]:
            continue
        out.append((s, e, t))
    return out


def main():
    src = APP_JS.read_text(encoding="utf-8")
    spans_const = const_spans(src)
    lits = parse_js(src)
    edits = []       # (start, end, replacement)
    keys = set()
    skipped = []
    for (s, e, q, content, kind) in lits:
        if kind != "s" and kind != "d":
            continue
        if not has_cjk(content):
            continue
        if in_t_call(src, s):
            continue
        if any(a <= s < b for a, b in spans_const):
            continue
        if content in SKIP_CONTENTS:
            continue
        if preceded_by(src, s, r"[=!]="):          # == / != / === / !==
            skipped.append((s, "comparison", content))
            continue
        if preceded_by(src, s, r"\.(split|includes|startsWith|endsWith|indexOf|lastIndexOf|replace|replaceAll|match)\($"):
            skipped.append((s, "structural", content))
            continue
        if preceded_by(src, s, r"\bcase$"):
            skipped.append((s, "case", content))
            continue
        # 后面紧跟冒号 = 对象键；但带 HTML 标签的（三元 false 分支等）不算键
        j = e
        while j < len(src) and src[j] in " \t\n":
            j += 1
        if j < len(src) and src[j] == ":" and "<" not in content and ">" not in content:
            skipped.append((s, "objkey", content))
            continue
        ws = wrap_spans(content)
        if not ws:
            continue
        # 重组：原字面量切片 + t() 调用
        pieces = []
        cur = 0
        for (ws_, we_, txt) in ws:
            if ws_ > cur:
                pieces.append(("lit", content[cur:ws_]))
            pieces.append(("t", txt))
            cur = we_
        if cur < len(content):
            pieces.append(("lit", content[cur:]))
        out = []
        for kind2, val in pieces:
            if kind2 == "lit":
                # 原文片段保留原始转义序列（content 切片即含 \' 等），同引号原样重包即合法
                out.append(q + val + q)
            else:
                keys.add(val)
                out.append('t("' + js_escape(val) + '")')
        edits.append((s, e, " + ".join(out)))
        keys.update(t for _, _, t in ws)

    for (s, e, new) in sorted(edits, key=lambda x: -x[0]):
        src = src[:s] + new + src[e:]
    APP_JS.write_text(src, encoding="utf-8")

    print(f"改写 {len(edits)} 处，新增 key {len(keys)} 条")
    if skipped:
        print("跳过（人工复核）：")
        for s, why, c in skipped:
            line = src.count("\n", 0, min(s, len(src))) + 1
            print(f"  [{why}] {line}: {c!r}")
    # 新 key 落盘供翻译
    out = ROOT / "tools" / "i18n_new_keys.txt"
    out.write_text("\n".join(sorted(keys)), encoding="utf-8")
    print(f"新增 key 写入 {out}")


if __name__ == "__main__":
    main()
