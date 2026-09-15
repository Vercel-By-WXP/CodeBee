# -*- coding: utf-8 -*-
"""赛马夹具：按角色分流的假 CLI。

generic argv_template 带 {prompt} 时提示词经 **argv** 送达（见 runner 的
generic 分支），调用形如：python fixtures_race_cli.py <good|bad> <prompt>

- 起草（prompt 含「本章任务」）：提取章号/变体号写稿；tag=good 时稿内
  埋「高光情节」标记，tag=bad 时埋「注水情节」（两稿长度都过审稿门槛）。
- 评审（prompt 含「待评审稿件」）：稿件含「高光情节」→ 全维度 9.5 分；
  含「注水情节」→ 4.0 分。
- 其余（大纲/规划）：输出可解析的最小大纲 JSON。
"""
import re
import sys
from pathlib import Path

tag = sys.argv[1] if len(sys.argv) > 1 else "good"
if len(sys.argv) > 2 and sys.argv[2]:
    prompt = sys.argv[2]
else:
    try:
        prompt = sys.stdin.read() or ""
    except Exception:
        prompt = ""


def _fill(mark, total):
    body = mark + "。"
    while len(body) < total:
        body += "他推开窗，远处灯火沉沉浮浮。"
    return body


if "本章任务" in prompt:
    mm = re.search(r"chapter-(\d+)(?:-v(\d+))?\.md", prompt or "")
    if not mm:
        print("no file marker found")
        sys.exit(1)
    # 文件名只由 int 格式化构造（章号/变体号强转整数），外部文本零透传，
    # 路径穿越在构造层面不可能发生
    chapter_no = int(mm.group(1))
    variant_no = int(mm.group(2)) if mm.group(2) else None
    suffix = ("-v%d" % variant_no) if variant_no is not None else ""
    name = "chapter-%02d%s.md" % (chapter_no, suffix)
    mark = "高光情节" if tag == "good" else "注水情节"
    target = Path(name)
    target.write_text("# 章\n\n" + _fill(mark, 900), encoding="utf-8")
    print("第 X 章完成（约 900 字）")
elif "待评审稿件" in prompt:
    score = 9.5 if "高光情节" in prompt else 4.0
    dims = ["情节", "人物", "文笔", "节奏", "吸引力"]
    print("```json\n{\"scores\": {%s}, \"issues\": [], \"summary\": \"race review\"}\n```"
          % ", ".join('"%s": %s' % (d, score) for d in dims))
else:
    print('```json\n{"book_title": "赛马测试书", "chapters": ['
          '{"title": "第一章", "beats": "主角登场，埋下铜钥匙伏笔", "hook": "门外传来敲门声"}]}'
          '\n```')
