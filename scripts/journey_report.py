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
回执、发布台账、人工救火台账派生事件。**判不了一律 unknown**：
``status=done`` 不等于过签、``quality_forced`` 缺来源不可判定、埋点启用
时刻未声明（或建单早于它）的历史书查不到人工痕迹——这些都会以哨兵事件
阻止假绿，报告同时打印事件源覆盖率。

``--telemetry-since "YYYY-MM-DD HH:MM"`` 声明埋点启用时刻。**默认不传**：
历史书一律判不了；只有 11-09 重启埋点真生效后，把那个真实时刻传进来，
之后新建的书才会进分母。别提前传一个未来时间造绿标。

``--outcome-gaps`` 打印「哪些书还没登记平台结论 / 哪些回执挂在非 task 键上」
的**只读清单**（上线前置 ⓒ）。它不是交付完成率，也不会补登数据：孤儿
回执只有 ``books.json`` 标题恰好命中唯一 task 才给候选，其余保持 unknown，
归属必须由人确认后走 ``outcome.record``。``--gap-limit 0`` 看全量。

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


def human_gaps(gaps, limit=20):
    """把回执登记缺口说成人话：这是给人补登记的清单，不是指标。"""
    rate = gaps.get("registration_rate")
    lines = [
        "回执登记缺口（只读清单，脚本不补登、不改数据；≠ 交付完成率）",
        "真被判过（已登记终态结论）%d/%d 本；覆盖率 %s"
        % (gaps.get("registered", 0), gaps.get("total", 0),
           "unknown（无可判样本）" if rate is None else "%.1f%%" % (rate * 100)),
        "分母口径：只含 status=done 或已登记终态结论的书；"
        "在跑 %d / 中途夭折 %d / 其它 %d 本不纳分母"
        % (gaps.get("out_of_scope", {}).get("running", 0),
           gaps.get("out_of_scope", {}).get("aborted", 0),
           gaps.get("out_of_scope", {}).get("other", 0)),
        "「待登记」= 流程已收尾但没结论，是去核对的清单，不等于都投过平台",
    ]
    missing = gaps.get("missing") or []
    lines.append("待登记 %d 本：" % len(missing))
    shown = missing if limit == 0 else missing[:max(0, limit)]
    for m in shown:
        lines.append("  - %s  status=%s  reason=%s  created=%s"
                     % (m["task_id"], m["status"] or "?", m["reason"],
                        m["created_at"] or "?"))
    if len(shown) < len(missing):
        lines.append("  ... 其余 %d 本（--gap-limit 0 看全量）"
                     % (len(missing) - len(shown)))
    orphans = gaps.get("orphans") or []
    lines.append("孤儿回执 %d 键：" % len(orphans))
    for o in (orphans if limit == 0 else orphans[:max(0, limit)]):
        lines.append("  - %s  work=%s  verdict=%s  candidates=%s"
                     % (o["key"], o["work"] or "?", o["verdict"],
                        ",".join(o["candidate_task_ids"]) or "无"))
        lines.append("      " + o["action"])
    return "\n".join(lines)


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
    ap.add_argument("--telemetry-since", default=None,
                    help="埋点启用时刻 YYYY-MM-DD HH:MM[:SS]；不传=未声明，历史书一律判不了")
    ap.add_argument("--outcome-gaps", action="store_true",
                    help="打印回执登记缺口清单（只读，不补登、不改数据）")
    ap.add_argument("--gap-limit", type=int, default=20,
                    help="缺口清单每类最多列几条；0=全量（默认 20）")
    ap.add_argument("--json", action="store_true", help="输出机器可读 JSON")
    args = ap.parse_args(argv)
    if not (args.events or args.data_dir or args.prod):
        ap.error("必须指定 --events / --data-dir / --prod 之一")
    if args.outcome_gaps and args.events:
        ap.error("--outcome-gaps 需要真实落盘（--prod / --data-dir），--events 没有回执数据")
    if args.gap_limit < 0:
        ap.error("--gap-limit 不能为负（0=全量）")

    kind, path = _resolve_source(args, ap)
    if kind == "events":
        events = read_events(str(path))
        summary = journey.from_events(events)
        if args.json:
            print(json.dumps(summary, ensure_ascii=False, indent=2, sort_keys=True))
        else:
            print(journey.human_report(summary))
        return 0

    derived = journey_sources.derive_events(
        str(path), telemetry_since=args.telemetry_since)
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
        if args.outcome_gaps:
            print("")
            print(human_gaps(coverage["outcome_gaps"], limit=args.gap_limit))
    return 0


if __name__ == "__main__":
    sys.exit(main())
