#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""只读生成「端到端交付指标」报告（零额度、零写入、零推断）。

用法：
    py -3.13 scripts/journey_report.py --events <events.jsonl>
    py -3.13 scripts/journey_report.py --events <events.jsonl> --json

输入是 append-only 的 jsonl，每行一个事件，至少带 ``task_id`` 与 ``kind``：
    {"task_id":"t-...","kind":"task_created","ts":"..."}
kind 白名单见 ``app.core.publish.journey.VALID_KINDS``；不认识的行会被判成
unknown 并单列报数，**不会静默丢弃、不会当成成功**。

dev / prod：
- dev 用手写 fixture 验证口径即可，不需要起服务；
- prod 只读本地 jsonl，不连 8765、不写任何台账、不改调度。
- 本脚本不自动生成事件；接入事件采集是下一步（见交接单第 3 节），
  在没有真实事件流之前，报告会诚实显示 unknown / 无样本，不假绿。
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "app"))

from core.publish import journey  # noqa: E402


def read_events(path):
    """逐行读 jsonl；坏行也保留成事件，让 classify_journey 判 unknown。"""
    out = []
    with open(path, "r", encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if not line:
                continue
            try:
                rec = json.loads(line)
            except (ValueError, TypeError):
                rec = None
            out.append(rec if isinstance(rec, dict) else {"kind": "__bad_line__"})
    return out


def main(argv=None):
    ap = argparse.ArgumentParser(description="CodeBee 端到端交付指标（只读）")
    ap.add_argument("--events", required=True, help="事件 jsonl 路径")
    ap.add_argument("--json", action="store_true", help="输出机器可读 JSON")
    args = ap.parse_args(argv)

    path = Path(args.events)
    if not path.is_absolute():
        path = (Path(os.getcwd()) / path).resolve()
    if not path.exists():
        print("拒绝：事件文件不存在 %s（不推断、不造样本）" % path, file=sys.stderr)
        return 2

    events = read_events(str(path))
    summary = journey.from_events(events)
    if args.json:
        print(json.dumps(summary, ensure_ascii=False, indent=2, sort_keys=True))
    else:
        print(journey.human_report(summary))
    return 0


if __name__ == "__main__":
    sys.exit(main())
