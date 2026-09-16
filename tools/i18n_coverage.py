#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""核对字典覆盖率：列出 app.js / index.html 里所有会进 t() 的中文 key，
对照 i18n.js 的 EN 字典，报告缺失翻译的 key。"""
import re
import sys
import json
from pathlib import Path

ROOT = Path(r"E:\GoOut\MultiAgentOrchestration")
APP_JS = ROOT / "app" / "ui" / "app.js"
INDEX = ROOT / "app" / "ui" / "index.html"
I18N = ROOT / "app" / "ui" / "i18n.js"

sys.path.insert(0, str(ROOT / "tools"))
from i18n_extract import parse_js, has_cjk  # noqa: E402


def js_unescape(s):
    """把 JS 字符串字面量内容按常见转义还原成运行时真实值。"""
    out = []
    i = 0
    n = len(s)
    while i < n:
        c = s[i]
        if c == "\\" and i + 1 < n:
            nx = s[i + 1]
            mapping = {"n": "\n", "t": "\t", "r": "\r", "\\": "\\",
                       '"': '"', "'": "'", "`": "`", "$": "$", "/": "/"}
            if nx in mapping:
                out.append(mapping[nx])
                i += 2
                continue
            out.append(c)
            i += 1
            continue
        out.append(c)
        i += 1
    return "".join(out)


def dict_keys():
    """从 i18n.js 抓 EN = { ... } 里所有字符串 key（被引号包裹、紧跟冒号者）。
    兼容一行多对：\"周一\": \"Mon\", \"周二\": \"Tue\", ..."""
    src = I18N.read_text(encoding="utf-8")
    # 只取 EN = { ... }; 区间，避免匹配到注释/函数体里的引号
    m = re.search(r"\bconst EN\s*=\s*\{", src)
    body = src[m.end():] if m else src
    keys = set()
    # 任一 "…" 或 '…' 后面紧跟 : 视为 key
    for km in re.finditer(r"(\"(?:[^\"\\]|\\.)*\"|'(?:[^'\\]|\\.)*')\s*:", body):
        raw = km.group(1)[1:-1]
        keys.add(js_unescape(raw))
    return keys


def js_keys():
    """app.js 中所有 t("...") / t('...') 实参字面量（静态串）。"""
    src = APP_JS.read_text(encoding="utf-8")
    out = set()
    for m in re.finditer(r"\bt\(\s*(['\"])((?:\\.|(?!\1).)*)\1\s*[,)]", src):
        out.add(js_unescape(m.group(2)))
    return out


def html_keys():
    src = INDEX.read_text(encoding="utf-8")
    out = set()
    for attr in ("data-i18n", "data-i18n-ph", "data-i18n-title", "data-i18n-aria"):
        for m in re.finditer(attr + r'="((?:[^"\\]|\\.)*)"', src):
            out.add(m.group(1).replace("&amp;", "&").replace("&quot;", '"'))
    return out


def const_js_keys():
    """模块常量表里定义、但由使用点 t() 渲染的中文 key：
    GIT_STATUS_LABEL/GIT_STATE_CHIP/CAT_COLOR/AUTO_WD/SU_MODE_TXT/TAB_TITLES。"""
    src = APP_JS.read_text(encoding="utf-8")
    out = set()
    names = ["GIT_STATUS_LABEL", "GIT_STATE_CHIP", "CAT_COLOR", "AUTO_WD",
             "SU_MODE_TXT", "TAB_TITLES"]
    for name in names:
        m = re.search(r"\bconst " + name + r"\b[^=]*=\s*(\{|\[)", src)
        if not m:
            continue
        openc = m.group(1)
        closec = "}" if openc == "{" else "]"
        # 抓整块
        i = m.start(1)
        depth = 0
        while i < len(src):
            if src[i] == openc:
                depth += 1
            elif src[i] == closec:
                depth -= 1
                if depth == 0:
                    break
            i += 1
        block = src[m.start(1):i + 1]
        for sm in re.finditer(r"(['\"])((?:\\.|(?!\1).)*)\1", block):
            if has_cjk(sm.group(2)):
                out.add(sm.group(2).replace("\\'", "'").replace("\\\\", "\\"))
    return out


def main():
    dk = dict_keys()
    needed = js_keys() | html_keys() | const_js_keys()
    # 只看含中文的 key（纯符号/英文 key 不需要翻译）
    needed_cjk = {k for k in needed if has_cjk(k)}
    missing = sorted(k for k in needed_cjk if k not in dk)
    print(f"字典现有 key: {len(dk)}")
    print(f"引用中文 key: {len(needed_cjk)}")
    print(f"缺翻译: {len(missing)}")
    print()
    print("\n".join(repr(k) for k in missing))
    # 落盘供翻译脚本消费
    (ROOT / "tools" / "i18n_missing.json").write_text(
        json.dumps(missing, ensure_ascii=False, indent=0), encoding="utf-8")


if __name__ == "__main__":
    main()
