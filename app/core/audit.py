# -*- coding: utf-8 -*-
"""轻量审计日志：不可逆操作留痕（当前仅任务删除）。

删除任务连运行记录一起物理清除且无回收站，出争议时无据可查
（2026-09-28 连载根任务被删案：谁删的、何时删的全无线索）。
这里按月追加 jsonl 到 data/audit/，一行一对象、正序追加，
与用量台账同款纪律。审计失败绝不拖垮主流程。
"""
from __future__ import annotations

import json
import time
from pathlib import Path

from . import paths


def record(event: str, detail: dict) -> None:
    """追加一条审计记录（event 即台账名，如 task_delete）。"""
    try:
        day = time.strftime("%Y-%m")
        d = Path(paths.DATA_DIR) / "audit"
        d.mkdir(parents=True, exist_ok=True)
        line = json.dumps(
            {"ts": time.strftime("%Y-%m-%d %H:%M:%S"), "event": event, **detail},
            ensure_ascii=False)
        with open(d / ("%s-%s.jsonl" % (event, day)), "a", encoding="utf-8") as f:
            f.write(line + "\n")
    except Exception:
        pass
