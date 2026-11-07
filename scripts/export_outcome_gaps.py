#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""ⓒ 回执补登记清单导出（只读落盘 → 本地 CSV，零 HTTP / 零生产写入 / 零代填）。

为什么有这个脚本（11-07 19:xx 只读取证）：
    16:34 交付的 80 行清单第 2 列只从 publish/books.json 取平台书名，
    68 本没绑定 → 那 68 行只剩 task_id + 时间戳，人认不出是哪本书，
    「人工补登记」物理上做不了；生产 tasks/*.json 115/115 带 title，
    80/80 待登记 task 带 workdir。清单还是一次性导出、不可复现。
    另实证原清单第 2 列有 1 处被静默小写（ai → AI），不是 books.json 原文。

21:3x 第二处断点（只读取证）：80 行备注一律写「去平台核对真实结论」，
    但按项目既有 journey_sources.LIVE_ACTIONS + ok=True 口径，生产台账里
    **只有 23 本**有成功投稿动作，**57 本零证据**（多为内部试跑 / 续写稿）。
    让人给这 57 本补「平台结论」等于诱导编造业务事实。故备注补一层台账证据，
    并写明「零证据 ≠ 从未投过、留空合法、不必填满」；证据只作核对线索，
    **绝不进判据、不改 outcome_gaps 分母、不代填第 6 列**。

契约（写死在代码里，不靠使用者自觉）：
    1. 表头从 outcome_register.EXPECTED_HEADER 取，7 列逐字一致：
       它的 judge_rows 会因表头 / 列数漂移整批拒写，绝不另抄一份。
    2. 第 6 列「平台结论」恒空：脚本不代填、不推断平台结论。
    3. 第 2 列只放 books.json 的原始 title（不做 casefold / 去书名号），
       绝不拿 task.title 顶替 —— 试跑名 /「# 重写」不是平台书名。
    4. 任务标题 / 工作目录只进第 7 列「核对备注」，明写「仅定位用」；
       缺字段写「无定位线索」，不猜、不报错。
    5. 目标 CSV 已有任何非空第 6 列 → 拒绝覆盖（人填的结论不能冲掉），
       不提供 --force 绕过；列数漂移判不了也拒。
    6. 零 HTTP：不 import urllib / requests，不连 8765；
       唯一写入是 --out 指定的本地清单，data_dir 全程只读。
    7. 台账证据口径唯一真源 = journey_sources.LIVE_ACTIONS + ok=True；
       不另造动作词表。证据只进第 7 列，第 1-6 列逐字不变。

用法（零额度期可安全跑）：
    py -3.13 scripts/export_outcome_gaps.py --prod
    py -3.13 scripts/export_outcome_gaps.py --prod --out data/exports/<新文件>.csv
    py -3.13 scripts/export_outcome_gaps.py --data-dir data

退出码：0 导出完成；2 参数 / 数据目录 / 覆盖保护拒绝（零写入）。
"""
from __future__ import annotations

import argparse
import collections
import csv
import json
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "app"))
sys.path.insert(0, str(ROOT / "scripts"))

from core.publish import journey_sources  # noqa: E402
import outcome_register  # noqa: E402

HEADER = outcome_register.EXPECTED_HEADER
CONCLUSION_IDX = outcome_register.CONCLUSION_IDX
DEFAULT_OUT = ROOT / "data" / "exports" / "outcome-gaps-20261107.csv"
REMARK_BASE = ("done != 过签；去平台核对真实结论后走 outcome.record "
               "补登，脚本不代填")
HINT_LABEL = "定位线索(仅定位用，非平台书名)："
EVIDENCE_LABEL = "台账证据(仅线索，不是平台结论)："
CHECK_NOTE = ("零证据 ≠ 从未投过（台账可能缺），留空合法；核对结果若是"
              "从未投稿，第 6 列留空即可，不必填满")
NO_EVIDENCE = "无成功投稿动作"


def _tail(value):
    """工作目录只留末两级，避免整条本机路径铺满备注；空串不猜。"""
    parts = str(value or "").replace("/", "\\").split("\\")
    return "\\".join(p for p in parts[-2:] if p)


def load_task_hints(data_dir):
    """只读 tasks/*.json 的 title / workdir；坏文件跳过并计数。"""
    hints = {}
    stat = {"files": 0, "bad_files": 0, "with_title": 0, "with_workdir": 0}
    folder = Path(data_dir) / "tasks"
    for path in sorted(folder.glob("*.json")):
        stat["files"] += 1
        try:
            obj = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError, UnicodeError):
            stat["bad_files"] += 1
            continue
        if not isinstance(obj, dict):
            stat["bad_files"] += 1
            continue
        tid = str(obj.get("id") or path.stem).strip()
        title = str(obj.get("title") or "").strip()
        workdir = _tail(obj.get("workdir"))
        hints[tid] = {"title": title, "workdir": workdir}
        stat["with_title"] += 1 if title else 0
        stat["with_workdir"] += 1 if workdir else 0
    return hints, stat


def load_raw_book_titles(data_dir):
    """只读 publish/books.json 的**原始** title 列表（去重保序）。

    刻意不用 load_book_bindings：那个函数为了精确匹配会 casefold + 去书名号，
    展示给人的书名必须是平台原文（实证原清单有 1 处 ai → AI 被静默改写）。
    """
    path = Path(data_dir) / "publish" / "books.json"
    stat = {"exists": path.exists(), "task_keys": 0, "titles": 0,
            "bad_entries": 0}
    if not path.exists():
        return {}, stat
    try:
        obj = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError, UnicodeError):
        stat["bad_entries"] += 1
        return {}, stat
    if not isinstance(obj, dict):
        stat["bad_entries"] += 1
        return {}, stat
    out = {}
    for tid, plats in obj.items():
        if not isinstance(plats, dict):
            stat["bad_entries"] += 1
            continue
        stat["task_keys"] += 1
        titles = []
        for plat in sorted(plats):
            entry = plats[plat]
            if not isinstance(entry, dict):
                stat["bad_entries"] += 1
                continue
            title = str(entry.get("title") or "").strip()
            if title and title not in titles:
                titles.append(title)
                stat["titles"] += 1
        out[str(tid)] = titles
    return out, stat


def load_publish_evidence(data_dir, with_stat=False):
    """只读 publish/publish-*.jsonl，按 task_id 聚合投稿证据。

    口径严格复用项目既有 journey_sources.LIVE_ACTIONS，且只认 ok=True；
    其它动作（含平台侧成功但不在该集合的操作）一律不计，避免把操作日志
    冒充投稿证据。坏行只计数、不猜、不影响其它行（fail-closed）。

    返回 {task_id: Counter({(platform, action): n})}；
    with_stat=True 时一并返回统计 dict。
    """
    folder = Path(data_dir) / "publish"
    stat = {"files": 0, "lines": 0, "bad_lines": 0, "matched": 0}
    evidence = {}
    allowed = set(journey_sources.LIVE_ACTIONS)
    for path in sorted(folder.glob("publish-*.jsonl")):
        stat["files"] += 1
        try:
            text = path.read_text(encoding="utf-8", errors="replace")
        except OSError:
            stat["bad_lines"] += 1
            continue
        for line in text.splitlines():
            if not line.strip():
                continue
            stat["lines"] += 1
            try:
                event = json.loads(line)
            except (ValueError, TypeError):
                stat["bad_lines"] += 1
                continue
            if not isinstance(event, dict) or event.get("ok") is not True:
                continue
            action = str(event.get("action") or "")
            if action not in allowed:
                continue
            tid = str(event.get("task_id") or "").strip()
            if not tid:
                continue
            platform = str(event.get("platform") or "").strip() or "?"
            counter = evidence.setdefault(tid, collections.Counter())
            counter[(platform, action)] += 1
            stat["matched"] += 1
    if with_stat:
        return evidence, stat
    return evidence


def _evidence_text(counter):
    """把计数说成人话，只报事实，不推断平台结论。"""
    if not counter:
        return NO_EVIDENCE
    return " / ".join("%s %s×%d" % (plat, action, n)
                      for (plat, action), n in sorted(
                          counter.items(), key=lambda kv: (-kv[1], kv[0])))


def build_rows(data_dir):
    """按 outcome_gaps.missing 逐行组装 7 列；返回 (rows, stats)。"""
    derived = journey_sources.derive_events(data_dir=str(data_dir))
    gaps = (derived.get("coverage") or {}).get("outcome_gaps") or {}
    hints, hstat = load_task_hints(data_dir)
    books, bstat = load_raw_book_titles(data_dir)
    evidence, estat = load_publish_evidence(data_dir, with_stat=True)
    rows = []
    hint_rows = 0
    book_rows = 0
    evidence_rows = 0
    for item in gaps.get("missing") or []:
        tid = str((item or {}).get("task_id") or "").strip()
        if not tid:
            continue
        titles = books.get(tid) or []
        hint = hints.get(tid) or {}
        parts = []
        if hint.get("title"):
            parts.append("任务标题=%s" % hint["title"])
        if hint.get("workdir"):
            parts.append("目录=%s" % hint["workdir"])
        if parts:
            remark = REMARK_BASE + "；" + HINT_LABEL + "，".join(parts)
            hint_rows += 1
        else:
            remark = (REMARK_BASE + "；定位线索：无"
                      "（tasks/ 缺 title/workdir，判不了，不猜）")
        counter = evidence.get(tid)
        if counter:
            evidence_rows += 1
        # 口径与证据只进第 7 列：第 1-6 列与无台账时逐字相同
        remark += ("；" + CHECK_NOTE + "；" + EVIDENCE_LABEL
                   + _evidence_text(counter))
        if titles:
            book_rows += 1
        rows.append([
            tid,
            " / ".join(titles),
            str((item or {}).get("status") or ""),
            str((item or {}).get("created_at") or ""),
            str((item or {}).get("reason") or ""),
            "",  # 第 6 列恒空：平台结论只能由人核对后填
            remark,
        ])
    return rows, {"total": len(rows), "hint_rows": hint_rows,
                  "book_rows": book_rows, "evidence_rows": evidence_rows,
                  "task_files": hstat["files"],
                  "bad_task_files": hstat["bad_files"],
                  "bad_ledger_lines": estat["bad_lines"],
                  "ledger_lines": estat["lines"],
                  "ledger_files": estat["files"],
                  "books_exists": bstat["exists"],
                  "books_keys": bstat["task_keys"]}


def guard_existing(path):
    """已填结论的 CSV 绝不覆盖；列数漂移判不了也拒（fail-closed）。"""
    if not path.exists():
        return None
    try:
        with open(path, "r", encoding="utf-8-sig", newline="") as fh:
            rows = list(csv.reader(fh))
    except (OSError, UnicodeError) as exc:
        return "拒绝覆盖：%s 读不了（%s），判不了是否已填结论" % (path, exc)
    filled = []
    for no, row in enumerate(rows[1:], start=2):
        if len(row) != len(HEADER):
            return ("拒绝覆盖：%s 第 %d 行列数 %d ≠ %d，"
                    "判不了结论列在哪，fail-closed" % (path, no, len(row),
                                                  len(HEADER)))
        if str(row[CONCLUSION_IDX] or "").strip():
            filled.append((no, row[0]))
    if filled:
        return ("拒绝覆盖：%s 已有 %d 行填了平台结论（例：第 %d 行 %s）；"
                "别把人工核对成果冲掉，换 --out 另存或先备份"
                % (path, len(filled), filled[0][0], filled[0][1]))
    return None


def write_csv(path, rows):
    with open(path, "w", encoding="utf-8-sig", newline="") as fh:
        writer = csv.writer(fh)
        writer.writerow(HEADER)
        writer.writerows(rows)


def main(argv=None):
    ap = argparse.ArgumentParser(
        description="ⓒ 回执补登记清单导出（只读落盘 → 本地 CSV）")
    src = ap.add_mutually_exclusive_group(required=True)
    src.add_argument("--prod", action="store_true",
                     help="读 %APPDATA%/CodeBee（只读）")
    src.add_argument("--data-dir", help="其它真实落盘数据目录（只读）")
    ap.add_argument("--out", default=str(DEFAULT_OUT),
                    help="输出 CSV 路径（默认 %s）" % DEFAULT_OUT)
    args = ap.parse_args(argv)
    if args.prod:
        appdata = os.environ.get("APPDATA", "")
        if not appdata:
            print("拒绝：APPDATA 未设置，定位不了生产库")
            return 2
        data_dir = Path(appdata) / "CodeBee"
    else:
        data_dir = Path(args.data_dir)
        if not data_dir.is_absolute():
            data_dir = (Path.cwd() / data_dir).resolve()
    if not data_dir.is_dir():
        print("拒绝：数据目录不存在 %s（不推断、不造样本）" % data_dir)
        return 2
    out = Path(args.out)
    if not out.is_absolute():
        out = (Path.cwd() / out).resolve()
    if not out.parent.is_dir():
        print("拒绝：输出目录不存在 %s（不自动建目录）" % out.parent)
        return 2
    reason = guard_existing(out)
    if reason:
        print(reason)
        return 2

    rows, stats = build_rows(data_dir)
    write_csv(out, rows)
    print("ⓒ 补登记清单导出（只读落盘 → 本地 CSV；零 HTTP、零生产写入）")
    print("  真被判过（能给出定位线索）：%d/%d 行"
          % (stats["hint_rows"], stats["total"]))
    print("  带 books.json 真实平台书名：%d 行；仅任务标题/目录定位：%d 行"
          % (stats["book_rows"], stats["total"] - stats["book_rows"]))
    print("  tasks/ 文件 %d 个（坏文件 %d）；books.json %s（%d 键）"
          % (stats["task_files"], stats["bad_task_files"],
             "存在" if stats["books_exists"] else "不存在",
             stats["books_keys"]))
    print("  台账证据：%d 行有成功投稿动作 / %d 行零证据"
          "（仅线索，不进判据；零证据 ≠ 从未投过）"
          % (stats["evidence_rows"],
             stats["total"] - stats["evidence_rows"]))
    print("  publish-*.jsonl %d 个文件 / %d 行（坏行 %d，已计数不猜）"
          % (stats["ledger_files"], stats["ledger_lines"],
             stats["bad_ledger_lines"]))
    print("  第 6 列恒空：平台结论只能由人核对后填，脚本绝不代填")
    print("  核对结果若是从未投稿 → 留空合法，不必填满 %d 行"
          % stats["total"])
    print("  输出：%s（7 列，表头取自 outcome_register.EXPECTED_HEADER）" % out)
    return 0


if __name__ == "__main__":
    sys.exit(main())
