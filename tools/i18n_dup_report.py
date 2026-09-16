#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""统计 i18n.js 字典重复 key（复用 i18n_dup_check 的抓取逻辑，不手工写字面量正则）。"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from i18n_dup_check import raw_pairs  # noqa: E402
import collections  # noqa: E402

pairs = raw_pairs()
by_key = collections.defaultdict(list)
for k, v in pairs:
    by_key[k].append(v)
dupes = {k: v for k, v in by_key.items() if len(v) > 1}
print("重复 key:", len(dupes))
diff = 0
for k, vs in sorted(dupes.items()):
    if len(set(vs)) > 1:
        diff += 1
        print("不一致:", repr(k[:60]))
        for v in vs:
            print("   →", repr(v[:60]))
print("值不一致:", diff, "；值一致（无害，后者覆盖）:", len(dupes) - diff)
