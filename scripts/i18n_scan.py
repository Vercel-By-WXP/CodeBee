"""扫描 UI JS 里未接 i18n 的中文字面量（排查工具，非闸门）。

用法：
  python scripts/i18n_scan.py app/ui/app.js                # 摘要 + 逐条列出
  python scripts/i18n_scan.py app/ui/app.js out.jsonl      # 摘要 + JSONL 落盘

判定为「已接」的两种形态：
  1. t("中文") / esc(t("中文")) —— 调用点直包；
  2. data-i18n="中文"（含 -ph/-title/-aria/-alt 变体）—— applyI18n 按键翻译。
注释一律剥掉后再扫；模板字符串、单双引号都识别。
注意：经变量/三元间接传给 t() 的字面量会被误报为未接（人工复核），
发给模型/CLI 的中文提示词不属于 UI 文案，不应包 t()。
"""
import io
import json
import re
import sys

sys.stdout.reconfigure(encoding="utf-8")

BS = chr(92)


def strip_comments(src):
    out = []
    i, n = 0, len(src)
    state = None
    while i < n:
        c = src[i]
        nxt = src[i + 1] if i + 1 < n else ""
        if state is None:
            if c == "/" and nxt == "/":
                state = "line"
                i += 2
                continue
            if c == "/" and nxt == "*":
                state = "block"
                i += 2
                continue
            if c in ('"', "'", "`"):
                state = c
                out.append(c)
                i += 1
                continue
            out.append(c)
            i += 1
        elif state in ('"', "'", "`"):
            if c == BS:
                out.append(c)
                out.append(nxt)
                i += 2
                continue
            out.append(c)
            if c == state:
                state = None
            i += 1
        elif state == "line":
            if c == "\n":
                state = None
                out.append("\n")
            i += 1
        elif state == "block":
            if c == "*" and nxt == "/":
                state = None
                i += 2
                continue
            if c == "\n":
                out.append("\n")
            i += 1
    return "".join(out)


def main():
    path = sys.argv[1] if len(sys.argv) > 1 else "app/ui/app.js"
    src = strip_comments(io.open(path, encoding="utf-8").read())
    lit = re.compile(r'''(["'])((?:\\.|(?!\1).)*?[\u4e00-\u9fff](?:\\.|(?!\1).)*?)\1''')
    unwrapped, wrapped_n = [], 0
    for m in lit.finditer(src):
        q, s = m.group(1), m.group(2)
        back = src[max(0, m.start() - 40):m.start()]
        if re.search(r"(?<![\w.$])t\(\s*$", back) or \
                re.search(r"data-i18n(?:-ph|-title|-aria|-alt)?=\s*$", back):
            wrapped_n += 1
            continue
        unwrapped.append((src.count("\n", 0, m.start()) + 1, s))
    print(f"{path}: 中文字面量（去注释后）已接={wrapped_n}  未接={len(unwrapped)}")
    if len(sys.argv) > 2:
        with io.open(sys.argv[2], "w", encoding="utf-8") as fh:
            for pos, s in unwrapped:
                fh.write(json.dumps({"line": pos, "text": s}, ensure_ascii=False) + "\n")
    else:
        for pos, s in unwrapped:
            print(f"L{pos}: {s[:72]!r}")


if __name__ == "__main__":
    main()
