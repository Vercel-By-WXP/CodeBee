# -*- coding: utf-8 -*-
"""测试夹具：无论什么角色都输出不可解析的非 JSON。

用于验证大纲降级闸门：作者模型只回垃圾时，连载任务必须中止，
而不是按「第 1 章/第 2 章」空模板开写两万字。
仅测试使用，不参与产品逻辑。
"""
import sys

blob = " ".join(sys.argv[1:])
try:
    blob += "\n" + sys.stdin.read()
except Exception:
    pass
print(".bad-cli garbage output (no json). argv=%s" % blob[:40])
