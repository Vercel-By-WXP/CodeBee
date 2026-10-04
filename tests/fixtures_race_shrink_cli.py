# -*- coding: utf-8 -*-
"""赛马复赛降级夹具：记录每次收到的提示词体量；前两次调用必败。

模拟容量受限通道（2026-09-17 讯飞 35B 实测形态）：全量长提示词挂起/秒拒。
每次调用把 {"len", "shrunk"} 写进独立状态文件 call-<pid>.json（并行变体各写
各的，避免同文件互踩）；状态目录由 orch.env 的 RACE_SHRINK_STATE_DIR 给定。
调用形如：python fixtures_race_shrink_cli.py <prompt>

- 第 1、2 次调用（= 复赛前的首轮两路变体）exit 1 不写稿；
- 其后正常写稿：稿内埋「高光情节」标记（评审夹具按此给 9.5 过门禁）。
"""
import json
import os
import re
import sys
from pathlib import Path

state_dir = Path(os.environ["RACE_SHRINK_STATE_DIR"])
prompt = sys.argv[1] if len(sys.argv) > 1 else ""
# Long generic CLI prompts are passed as a bounded file hint on Windows.
hint = re.search(r"本次完整指令因命令行长度限制已写入文件：([^\r\n]+)", prompt)
if hint:
    try:
        prompt = Path(hint.group(1).strip()).read_text(encoding="utf-8")
    except OSError:
        pass

is_draft = "本章任务" in prompt
if is_draft:
    # 只登记起草调用（大纲/评审夹具照常成功），状态文件即起草调用流水
    (state_dir / ("call-%d.json" % os.getpid())).write_text(json.dumps({
        "len": len(prompt),
        "shrunk": "已因上下文容量限制精简" in prompt,
    }, ensure_ascii=False), encoding="utf-8")
    if len(list(state_dir.glob("call-*.json"))) <= 2:
        sys.exit(1)               # 复赛前两路：全量提示词必挂


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
    # 文件名只由 int 格式化构造（章号/变体号强转整数），外部文本零透传
    chapter_no = int(mm.group(1))
    variant_no = int(mm.group(2)) if mm.group(2) else None
    suffix = ("-v%d" % variant_no) if variant_no is not None else ""
    Path("chapter-%02d%s.md" % (chapter_no, suffix)).write_text(
        "# 章\n\n" + _fill("高光情节", 900), encoding="utf-8")
    print("第 X 章完成（约 900 字）")
elif "待评审稿件" in prompt or "全书整体" in prompt:
    dims = ["情节", "人物", "文笔", "节奏", "吸引力"]
    print("```json\n{\"scores\": {%s}, \"issues\": [], \"summary\": \"race review\"}\n```"
          % ", ".join('"%s": 9.5' % d for d in dims))
else:
    print('```json\n{"book_title": "降级赛马书", "chapters": ['
          '{"title": "第一章", "beats": "主角登场，埋下铜钥匙伏笔", "hook": "门外传来敲门声"}]}'
          '\n```')
