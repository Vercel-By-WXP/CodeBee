# -*- coding: utf-8 -*-
"""大屏（/board.html）样式与文案的三条守卫。

board.css 不共享主应用的 style.css（那是 370KB 的组件样式，大屏只需要颜色），
所以皮肤 token 是**抄**过来的一份。抄写会漂移，这三条测试就是防漂移的：

1. test_board_skin_tokens_match_app —— board.css 里每个 data-skin/data-theme 块
   的共享 token 值，必须与 style.css 同名块逐字相等。
2. test_board_strings_have_en_entries —— 大屏用到的中文字符串（board.js 的 t()
   与 board.html 的 data-i18n*）必须都在 i18n.js 英文词典里，否则英文态回落中文。
3. test_i18n_en_dict_no_duplicate_keys —— 词典不许出现重复键（重复键 JS 静默
   取后者，等于悄悄改掉别处的译文）。
"""
import io
import os
import re
import collections

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
UI = os.path.join(ROOT, "app", "ui")

# 大屏与主应用共用的一组颜色 token；布局尺寸（圆角/间距）不在同步范围内
SHARED = ["--bg", "--sidebar", "--panel", "--panel2", "--border", "--border-strong",
          "--text", "--muted", "--accent", "--accent2", "--ok", "--warn", "--bad",
          "--log-bg", "--log-text", "--shadow"]


def _read(name):
    with io.open(os.path.join(UI, name), encoding="utf-8") as f:
        return f.read()


def _norm(v):
    """比较用归一：小写、压缩空白、#000000 与 #000 视为同值。"""
    v = " ".join(v.strip().split())
    v = re.sub(r",\s*", ", ", v).lower()
    m = re.fullmatch(r"#([0-9a-f])([0-9a-f])([0-9a-f])([0-9a-f])([0-9a-f])([0-9a-f])", v)
    if m and m.group(1) == m.group(2) and m.group(3) == m.group(4) and m.group(5) == m.group(6):
        v = "#%s%s%s" % (m.group(1), m.group(3), m.group(5))
    return v


def _selector_key(sel):
    """把选择器归一成 (skin, mode)。skin=classic 表示无皮肤属性兜底。"""
    skin = (re.search(r'data-skin="([^"]+)"', sel) or [None, "classic"])[1]
    if ":not([data-skin])" in sel:
        skin = "classic"
    mode = (re.search(r'data-theme="([^"]+)"', sel) or [None, "dark"])[1]
    return (skin, mode)


def _parse_blocks(src, skip_root=False):
    """返回 {(skin, mode): {token: value}}；同键多块按书写顺序合并（后者覆盖，
    与 CSS 层叠一致——style.css 里 ocean/light 就有三份，末份才生效）。"""
    src = re.sub(r"/\*.*?\*/", " ", src, flags=re.S)   # 注释尾巴会被当成选择器的一部分
    out = collections.defaultdict(dict)
    for sel, body in re.findall(r'([^{}]+)\{([^{}]*)\}', src):
        sel = " ".join(sel.split())[-160:]            # 只取紧邻的这一段选择器
        if sel.endswith(":root"):
            if skip_root:
                continue
            key = ("classic", "dark")          # 大屏 :root 兜底 = 经典·夜间
        elif re.search(r'html\[data-', sel):
            key = _selector_key(sel)
        else:
            continue
        for name, val in re.findall(r'(--[a-z0-9-]+)\s*:\s*([^;]+);', body):
            out[key][name] = _norm(val)
    return out


def test_board_skin_tokens_match_app():
    """逐皮肤逐明暗比对共享 token；漂移即失败并点名差异。"""
    board = _parse_blocks(_read("board.css"))
    app = _parse_blocks(_read("style.css"), skip_root=True)
    bad = []
    for key in sorted(board):
        if key not in app:
            bad.append("%s/%s：style.css 里找不到对应块" % key)
            continue
        for tok in SHARED:
            b, a = board[key].get(tok), app[key].get(tok)
            if b is None:
                bad.append("%s/%s：board.css 漏了 %s" % (key[0], key[1], tok))
            elif a is not None and b != a:
                bad.append("%s/%s %s：board=%r app=%r" % (key[0], key[1], tok, b, a))
    assert not bad, "board.css 皮肤 token 与 style.css 不一致：\n  " + "\n  ".join(bad)


def _board_strings():
    js, html = _read("board.js"), _read("board.html")
    need = {m.group(1) for m in re.finditer(r't\(\s*"([^"]+)"', js)}
    for attr in ("data-i18n", "data-i18n-ph", "data-i18n-title", "data-i18n-aria"):
        need |= {m.group(1) for m in re.finditer(attr + r'="([^"]+)"', html)}
    return {k for k in need if re.search(r"[一-鿿]", k)}


def _dict_keys():
    return set(re.findall(r'^\s*"((?:[^"\\]|\\.)+)"\s*:', _read("i18n.js"), flags=re.M))


def test_board_strings_have_en_entries():
    """英文态靠词典命中；漏词条不会报错，只会安静地显示中文。"""
    missing = sorted(k for k in _board_strings() if k not in _dict_keys())
    assert not missing, "i18n.js 缺英文词条：%s" % "、".join(missing)


def test_i18n_en_dict_no_duplicate_keys():
    src = _read("i18n.js")
    head = src.index("const EN = {")
    keys = re.findall(r'^\s*"((?:[^"\\]|\\.)+)"\s*:', src[head:], flags=re.M)
    dup = [k for k, c in collections.Counter(keys).items() if c > 1]
    assert not dup, "英文词典有重复键（后者会静默覆盖前者）：%s" % "、".join(sorted(dup))
