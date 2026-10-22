# -*- coding: utf-8 -*-
"""i18n 词典重复键检查（发版前巡检项：后值会静默覆盖前值）。"""
import collections
import re

src = open("app/ui/i18n.js", encoding="utf-8").read()
keys = re.findall(r'^    "((?:[^"\\]|\\.)*)":', src, re.M)
dups = [k for k, c in collections.Counter(keys).items() if c > 1]
print("entries:", len(keys), "| duplicates:", dups if dups else "NONE")
