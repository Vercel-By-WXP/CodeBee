#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""只读生成「端到端交付指标」报告（零额度、零写入、零推断）。

两种输入，**二选一**：

    # ① 直接读真实落盘（推荐）：开发库 / 生产库都是只读
    py -3.13 scripts/journey_report.py --data-dir data
    py -3.13 scripts/journey_report.py --prod          # %APPDATA%/CodeBee
    py -3.13 scripts/journey_report.py --prod --json

    # ② 手写 jsonl 事件流（守测 / 口径验证用）
    py -3.13 scripts/journey_report.py --events <events.jsonl>

``--prod`` / ``--data-dir`` 走 ``journey_sources.py``：从 task 文件、平台
回执、发布台账派生事件。**判不了一律 unknown**：三类人工救火动作目前没有
埋点、``status=done`` 不等于过签、``quality_forced`` 缺来源不可判定——这些
都会以哨兵事件阻止假绿，报告同时打印「还差哪几个埋点」。

dev / prod：
- dev 用仓库 ``data/`` 或临时结构夹具验证口径，不需要起服务；
- prod 只读本地/AppData 落盘文件，不连 8765、不写任何台账、不改调度。
- **指标还没上线**：在有完整救火埋点之前，完成率会诚实显示 unknown，
  不许拿它汇报成绩。见交接单第 3 节「22:34 复核后的真缺口」第 ① 条。
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "app"))

from core.publish import journey, journey_sources  # noqa: E402


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


def _resolve_source(args, ap):
    """返回 (kind, path)。目录不存在一律拒绝，绝不推断、绝不造样本。"""
    if args.prod:
        appdata = os.environ.get("APPDATA", "")
        if not appdata:
            ap.error("APPDATA 未设置，无法定位生产库")
        path = Path(appdata) / "CodeBee"
        kind = "prod"
    elif args.data_dir:
        path = Path(args.data_dir)
        if not path.is_absolute():
            path = (Path(os.getcwd()) / path).resolve()
        kind = "data-dir"
    else:
        path = Path(args.events)
        if not path.is_absolute():
            path = (Path(os.getcwd()) / path).resolve()
        kind = "events"
    if kind == "events":
        if not path.exists():
            ap.error("拒绝：事件文件不存在 %s（不推断、不造样本）" % path)
    elif not path.is_dir():
        ap.error("拒绝：数据目录不存在 %s（不推断、不造样本）" % path)
    return kind, path


def main(argv=None):
    ap = argparse.ArgumentParser(description="CodeBee 端到端交付指标（只读）")
    src = ap.add_mutually_exclusive_group()
    src.add_argument("--events", help="事件 jsonl 路径")
    src.add_argument("--data-dir", help="真实落盘数据目录（含 tasks/ 与 publish/）")
    src.add_argument("--prod", action="store_true",
                     help="读生产库 %%APPDATA%%/CodeBee（只读）")
    ap.add_argument("--json", action="store_true", help="输出机器可读 JSON")
    args = ap.parse_args(argv)
    if not (args.events or args.data_dir or args.prod):
        ap.error("必须指定 --events / --data-dir / --prod 之一")

    kind, path = _resolve_source(args, ap)
    if kind == "events":
        events = read_events(str(path))
        summary = journey.from_events(events)
        if args.json:
            print(json.dumps(summary, ensure_ascii=False, indent=2, sort_keys=True))
        else:
            print(journey.human_report(summary))
        return 0

    derived = journey_sources.derive_events(str(path))
    summary = journey.from_events(derived["events"])
    coverage = derived["coverage"]
    if args.json:
        print(json.dumps({"source": kind, "data_dir": str(path),
                          "summary": summary, "coverage": coverage},
                         ensure_ascii=False, indent=2, sort_keys=True))
    else:
        print(journey_sources.human_coverage(coverage))
        print("")
        print(journey.human_report(summary))
    return 0


if __name__ == "__main__":
    sys.exit(main())
