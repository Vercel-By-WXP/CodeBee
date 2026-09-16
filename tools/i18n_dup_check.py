#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""列出 i18n.js 字典里的重复 key（JS 运行时语义：后者覆盖前者），
按「值是否一致」分组——值一致是无害冗余，值不一致要看哪个该留。"""
import collections
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from i18n_coverage import dict_keys  # noqa: E402

I18N = Path(r"E:\GoOut\MultiAgentOrchestration\app\ui\i18n.js")


def raw_pairs():
    src = I18N.read_text(encoding="utf-8")
    m = re.search(r"\bconst EN\s*=\s*\{", src)
    start = m.end()
    # 截到字典收尾的 "\n  };"，避免把后面工具函数里的 "en"/"zh" 当词条
    close = re.search(r"\n  \};", src[start:])
    body = src[start:start + close.start()] if close else src[start:]
    # key: value 成对抓取
    out = []
    for m in re.finditer(r"(\"(?:[^\"\\]|\\.)*\"|'(?:[^'\\]|\\.)*')\s*:\s*(['\"])", body):
        key_raw, q = m.group(1), m.group(2)
        # 从 value 引号开始找到配对闭引号
        vstart = m.end()
        i = vstart
        while i < len(body):
            if body[i] == "\\":
                i += 2
                continue
            if body[i] == q:
                break
            i += 1
        val_raw = body[vstart:i]
        key = key_raw[1:-1].replace('\\"', '"').replace("\\\\", "\\").replace("\\n", "\n")
        val = val_raw.replace("\\" + q, q).replace("\\\\", "\\").replace("\\n", "\n")
        out.append((key, val))
    return out


def main():
    pairs = raw_pairs()
    by_key = collections.defaultdict(list)
    for k, v in pairs:
        by_key[k].append(v)
    dupes = {k: v for k, v in by_key.items() if len(v) > 1}
    print("重复 key:", len(dupes))
    same = diff = 0
    for k, vs in sorted(dupes.items()):
        uniq = set(vs)
        if len(uniq) == 1:
            same += 1
        else:
            diff += 1
            print("不一致:", repr(k[:50]))
            for v in vs:
                print("    →", repr(v[:60]))
    print(f"值一致（无害）: {same}，值不一致（需裁决）: {diff}")


if __name__ == "__main__":
    main()
