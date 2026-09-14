# -*- coding: utf-8 -*-
"""测试夹具：大纲正常产出；起草角色把章稿写盘后以退出码 1 崩溃。

用于验证「成品是文件不是退出码」：CLI 调用失败但章稿已完整落盘时，
流水线应把稿件送评审门，而不是整章作废（终章长文超时实测场景的快速等价）。
仅测试使用，不参与产品逻辑。
"""
import json
import re
import sys
from pathlib import Path

blob = " ".join(sys.argv[1:])
try:
    blob += "\n" + sys.stdin.read()
except Exception:
    pass

if "网文主编" in blob:
    print(json.dumps({
        "book_title": "崩溃恢复之书",
        "chapters": [
            {"title": "第 1 章 开端", "beats": "主角立誓", "hook": "玉佩"},
            {"title": "第 2 章 收束", "beats": "兑现约定", "hook": ""},
        ]}, ensure_ascii=False))
elif "网文作者" in blob:
    m = re.search(r"(chapter-\d+\.md)", blob)
    fname = m.group(1) if m else "chapter-01.md"
    body = ("# %s\n\n" % fname) + ("夜色压下来，她把誓言刻进心里，一步一步走向那扇门。"
                                   "灯亮起的一瞬，所有蛰伏的线头同时绷紧——这一局，她不会再输。") * 12
    # 只写当前目录下的章稿文件（文件名已由正则限定为 chapter-NN.md）
    Path.cwd().joinpath(fname).write_bytes(body.encode("utf-8"))
    print("起草器崩溃（模拟超时/异常退出）", file=sys.stderr)
    sys.exit(1)
else:
    print(".crash-cli idle")
