# -*- coding: utf-8 -*-
"""测试夹具：按提示词角色返回不同质量的输出。

- 大纲（「网文主编」）→ 输出合规 JSON 大纲（模拟编排者正常工作）
- 评审（「严格的评审」）→ 输出不可解析的垃圾（模拟评审模型故障）
- 起草/修订（提示词含 chapter-NN.md 章稿标记）→ 落盘足量章稿再退出
  （「成品是文件不是退出码」验收收紧后，起草必须真写文件才过闸）
- 其余 → 退出码 0，不写任何文件

用于验证流水线的质量闸门：评审拿不到分数时必须中止运行，
而不是把「评不上」当成 0 分继续往下写。
仅测试使用，不参与产品逻辑。
"""
import json
import re
import sys

blob = " ".join(sys.argv[1:])
try:
    blob += "\n" + sys.stdin.read()
except Exception:
    pass

if "网文主编" in blob and "严格的评审" not in blob:
    print(json.dumps({
        "book_title": "闸门测试之书",
        "chapters": [
            {"title": "第 1 章 初雪", "beats": "主角当街被退婚，立誓三年之约",
             "hook": "退婚者袖中滑出的玉佩刻着母亲的名字"},
            {"title": "第 2 章 夜行", "beats": "夜探仇家商队，发现父亲旧部",
             "hook": "旧部说父亲还活着"},
            {"title": "第 3 章 破局", "beats": "以一敌三反杀追兵，拿到账册",
             "hook": ""},
        ]}, ensure_ascii=False))
elif "严格的评审" in blob:
    # 评审角色：退出码 0 但内容不是 JSON（评审将无法解析出分数）
    print(".role-cli non-json output for this role.")
else:
    m = re.search(r"chapter-(\d+)\.md", blob)
    if m:
        # 起草/修订角色：向 cwd（=任务工作目录）落盘足量章稿（≥0.6×wpc 验收线）
        body = "第 %s 章正文。风雪落在长街上，主角握紧了拳，往事一幕幕翻涌，" \
               "他记得那句话，也记得那块玉佩，路还长，账总要算，天亮之前他必须出城，" \
               "马蹄声碎，灯火渐远。" % m.group(1)
        with open(m.group(0), "w", encoding="utf-8") as f:
            f.write("# 第 %s 章\n\n%s\n" % (m.group(1), body * 30))
    else:
        print(".role-cli non-json output for this role.")
