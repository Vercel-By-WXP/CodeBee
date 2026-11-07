#!/usr/bin/env python
"""11-07 18:4x：给「ⓒ 80 本待登记清单」补上消费点（fail-closed 批量登记）。

背景（别再把它当成「只读报告」的一部分）：
    `journey_report.py --outcome-gaps` 与 `data/exports/outcome-gaps-20261107.csv`
    只负责把「哪些书还没登记平台结论」列给人看，明确「脚本绝不补登、不改数据」。
    但人真去平台核对完、把结论填进 CSV 之后，**没有任何工具读那一列**：
    唯一写入口是 `POST /api/publish/task/<id>/outcome`，要手工敲 80 次 curl，
    既没有 task 存在性 / 枚举 / 重复登记 / 贴错书的防错，也没有写后复核。
    上一轮的交付物因此是个「零消费点」的清单：人填了也落不了盘。

本工具的契约（写死在代码里，不靠使用者自觉）：
    1. 默认 `--check` 只读：读本地 CSV + `journey_sources.derive_events` 落盘真源，
       **零 HTTP、零写入**；报「真被判过样本数」（决策 18），空结论行单列 skip，
       绝不把「还没核对」说成「不通过」。
    2. `--apply` 必须同时带 `--confirm-human-reviewed`；缺标志在第一次 IO 之前拒，
       零 POST / 零 GET。
    3. 整批 fail-closed：只要有一行 `blocked`（明确非法）或 `unknown`（判不了），
       **一条都不写**，先回去改 CSV。半批写入事后对不上账，比不写更糟。
    4. 写前重新取 `outcome_gaps.missing` 复核（CSV 编制期间别人可能已经登记过）；
       逐条 POST 后 GET `/api/publish/task/<id>/outcome` 复核 `stage` 真落盘。
       POST 已发生不可回滚：失败如实报 `exit 2`，**不重试、不回滚、不补写**。
    5. 枚举从 `core.publish.outcome.STAGES / PLATFORMS` 取，不另抄一份；
       `needs_quote` 沿用其刻意设计（不是硬校验），批量登记时缺原话判 `unknown`
       并整批拒写 —— 平台确实没给原话时，请人单独走 HTTP 入口登记，别混进批量。
    6. 终态口径（22:0x 补，纯函数实证）：只有 `STAGE_TERMINALS` 里的
       signed / rejected / signing_rejected 登记后让 `registered` +1 且从
       `outcome_gaps.missing` 消失；submitted / in_review / live 是过程态，
       POST 成功、GET 复核也一致，但**覆盖率不变、书仍留在清单里**。工具
       必须分路报「终态 M 本 / 非终态 K 本」，不许把「登记完成」说成闭环；
       并提醒别因清单里还在就重跑 `--apply`（`outcome.record` 的 history
       会追加，重复写污染台账）。口径唯一真源 = `journey_sources.STAGE_TERMINALS`，
       不改判据 / 不改分母 / 不新增 IO。
    7. 空清单守卫（23:1x 补，离线实证）：`data/exports/` 已 gitignore、CSV
       不进仓，换机器或误清空后「只剩表头」会被误报成「0 行结论全空」+
       `exit 0`，「真源目录指错」会打 `真被判过 0/0` 却仍 `exit 0`，
       「文件不存在」直接抛裸 traceback。三者一律判 `unknown` / 人话拒跑 +
       `exit 2`：判不了就不上绿标。0 字节文件原本走表头漂移 `exit 2`，
       保持不变；正常清单（80 行 + 真源 80 待登记）行为零改动。

填写格式（CSV 第 6 列「平台结论(待人工核对后填写)」）：
    平台|阶段|平台原话        例：qimao|signed|
    平台|阶段              例：fanqie|in_review
    驳回类必须带原话：      fanqie|rejected|缺乏吸引力，建议修改开篇
    平台取值 fanqie / qimao；阶段取值 submitted / in_review / live /
    rejected / signing_rejected / signed。留空 = 这本还没核对（skip，不写）。
    第 7 列「核对备注」是导出时的人话提示，**不当 note 写进台账**。

用法（零额度期只跑第 1、2 条）：
    py -3.13 scripts/outcome_register.py --template
    py -3.13 scripts/outcome_register.py --check
    py -3.13 scripts/outcome_register.py --apply --dry-run --confirm-human-reviewed
    py -3.13 scripts/outcome_register.py --apply --confirm-human-reviewed

退出码：0 诊断完成 / 全批通过；2 参数、闸门拒绝、清单不可登记、写后复核失败。
dev / prod：`--data-dir` 默认 `%APPDATA%/CodeBee`（生产库的只读落盘真源），
可用 `--data-dir` 指向隔离实例；`--base` 只影响 `--apply` 的写入口。
"""
from __future__ import annotations

import argparse
import csv
import json
import os
import sys
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "app"))

from core.publish import journey_sources, outcome  # noqa: E402

BASE = "http://127.0.0.1:8765"
CSV_REL = "data/exports/outcome-gaps-20261107.csv"
EXPECTED_HEADER = [
    "task_id",
    "书名(来自 publish/books.json，真实)",
    "status",
    "created_at",
    "缺口原因",
    "平台结论(待人工核对后填写)",
    "核对备注",
]
CONCLUSION_IDX = 5
HUMAN_FLAG = "--confirm-human-reviewed"


def default_data_dir():
    """与 `journey_report.py --prod` 同口径：生产库只读落盘真源。"""
    appdata = os.environ.get("APPDATA", "")
    return str(Path(appdata) / "CodeBee") if appdata else str(ROOT / "data")


def api_get(base, path):
    with urllib.request.urlopen(base + path, timeout=15) as resp:
        return json.load(resp)


def api_post(base, path, body):
    req = urllib.request.Request(
        base + path, data=json.dumps(body).encode("utf-8"),
        headers={"Content-Type": "application/json"}, method="POST")
    with urllib.request.urlopen(req, timeout=15) as resp:
        return json.load(resp)


def load_missing_ids(data_dir):
    """只读取「当前仍待登记」的 task_id 集合 + 覆盖率快照。

    真源是 `journey_sources.derive_events` 的落盘数据，不是 `/api/state` 的
    压缩摘要；守测用 mock 注入，绝不碰生产库。
    """
    derived = journey_sources.derive_events(data_dir=data_dir)
    gaps = (derived.get("coverage") or {}).get("outcome_gaps") or {}
    ids = set()
    for row in gaps.get("missing") or []:
        tid = str((row or {}).get("task_id") or "").strip()
        if tid:
            ids.add(tid)
    return ids, gaps


def terminal_stages():
    """终态口径唯一真源：不另抄 stage 词表。"""
    return frozenset(journey_sources.STAGE_TERMINALS)


def is_terminal_stage(stage):
    """该 stage 登记后是否让覆盖率 +1、从待登记清单消失。

    非终态不是「填错」：它是合法的平台结论，只是覆盖率口径按终态计。
    """
    return str(stage or "").strip() in terminal_stages()


class CsvMissingError(Exception):
    """清单 CSV 不存在 / 不是文件：data/exports 已 gitignore，需重新导出。"""


def read_csv(path):
    """返回 (表头, 数据行)；行保留成 list，列数漂移要能被判据看见。

    文件不存在时不抛裸 FileNotFoundError：给出可执行的恢复命令，
    由调用方统一转 exit 2（零 POST / 零 GET）。
    """
    if not Path(path).is_file():
        raise CsvMissingError(
            "清单 CSV 不存在或不是文件：%s；data/exports 已 gitignore、不进仓，"
            "先跑 `py -3.13 scripts/export_outcome_gaps.py --prod` 重新导出"
            % path)
    with open(path, "r", encoding="utf-8-sig", newline="") as fh:
        rows = list(csv.reader(fh))
    if not rows:
        return [], []
    return [c.strip() for c in rows[0]], rows[1:]


def parse_conclusion(cell):
    """解析第 6 列。四路：ok / skip / blocked（明确非法）/ unknown（判不了）。

    判不了与不通过必须分开报（决策 18 / 11-07 15:30 的口径）：
    平台确实没给驳回原话不是「填错」，是这次判不了，不能拿它当 blocked 骂人。
    """
    text = str(cell or "").strip()
    if not text:
        return {"verdict": "skip", "reason": "结论列空白：这本还没核对"}
    parts = text.split("|", 2)
    if len(parts) < 2:
        return {"verdict": "blocked",
                "reason": "格式不对，写 `平台|阶段|平台原话`（至少两段）"}
    platform = parts[0].strip().lower()
    stage = parts[1].strip().lower()
    quote = parts[2].strip() if len(parts) > 2 else ""
    if platform not in outcome.PLATFORMS:
        return {"verdict": "blocked",
                "reason": "平台 %s 不在 %s 里" % (
                    platform or "?", "/".join(sorted(outcome.PLATFORMS)))}
    if stage not in outcome.STAGES:
        return {"verdict": "blocked",
                "reason": "阶段 %s 不在 outcome.STAGES 里" % (stage or "?")}
    meta = outcome.STAGES[stage]
    if meta.get("needs_quote") and not quote:
        return {"verdict": "unknown",
                "reason": "%s 需要平台原话，但第 3 段为空：判不了，"
                          "别把没原文的结论混进批量补登" % stage}
    return {"verdict": "ok", "platform": platform, "stage": stage,
            "quote": quote, "reason": ""}


def judge_rows(header, rows, missing_ids):
    """纯函数：整批判据。默认路径只读，零写入、零 HTTP。

    `judged` 是**真被判过样本数**（跑完解析 + 清单核对的行），空结论 skip
    单列不算；调用方必须把它报出来，不许拿 80 行当 80 本判过。
    """
    out = {"ok": [], "skip": [], "blocked": [], "unknown": [],
           "total": len(rows), "judged": 0, "notes": []}
    if header != EXPECTED_HEADER:
        out["blocked"].append({
            "row": 1, "task_id": "",
            "reason": "表头漂移：期望 7 列逐字一致，实际 %r；"
                      "列错位会让结论贴到别的书上，整批拒写" % (header,)})
        return out
    if not rows:
        out["unknown"].append({
            "row": 1, "task_id": "",
            "reason": "清单 0 数据行：文件可能被清空 / 贴错 CSV，判不了；"
                      "先跑 export_outcome_gaps.py --prod 重新导出"})
    missing = set(missing_ids or ())
    seen = set()
    for row_no, row in enumerate(rows, start=2):
        if len(row) != len(EXPECTED_HEADER):
            out["unknown"].append({
                "row": row_no, "task_id": "",
                "reason": "列数 %d ≠ 7：判不了这行对应哪本书" % len(row)})
            continue
        tid = str(row[0] or "").strip()
        if not tid:
            out["unknown"].append({
                "row": row_no, "task_id": "",
                "reason": "task_id 空白：判不了是哪本书"})
            continue
        parsed = parse_conclusion(row[CONCLUSION_IDX])
        if parsed["verdict"] == "skip":
            out["skip"].append({"row": row_no, "task_id": tid,
                                "reason": parsed["reason"]})
            continue
        out["judged"] += 1
        if parsed["verdict"] != "ok":
            out[parsed["verdict"]].append({
                "row": row_no, "task_id": tid, "reason": parsed["reason"]})
            continue
        if tid in seen:
            out["blocked"].append({
                "row": row_no, "task_id": tid,
                "reason": "同一 task_id 在 CSV 里出现多次：会重复写台账"})
            continue
        seen.add(tid)
        if tid not in missing:
            out["blocked"].append({
                "row": row_no, "task_id": tid,
                "reason": "不在当前待登记清单（可能已登记 / 不是 done 小说 / "
                          "贴错 CSV）：先重跑 --check 刷新清单"})
            continue
        out["ok"].append({"row": row_no, "task_id": tid,
                          "platform": parsed["platform"],
                          "stage": parsed["stage"],
                          "quote": parsed["quote"],
                          "terminal": is_terminal_stage(parsed["stage"])})
    if not missing:
        out["notes"].append("待登记清单为空：判不了任何一行归不归这本，fail-closed")
        out["unknown"].append({
            "row": 1, "task_id": "",
            "reason": "真源零待登记：--data-dir 可能指错或落盘真源丢失，"
                      "判不了任何一行归不归这本；先核对 --data-dir"})
    return out


def print_verdict(result, gaps):
    """把四路判据说成人话，含真被判过样本数。"""
    print("ⓒ 补登记清单判据（只读）")
    print("  真被判过：%d/%d 行；空结论未判：%d 行" % (
        result["judged"], result["total"], len(result["skip"])))
    print("  ok=%d  blocked=%d  unknown=%d" % (
        len(result["ok"]), len(result["blocked"]), len(result["unknown"])))
    if result["ok"]:
        term = sum(1 for x in result["ok"] if x.get("terminal"))
        non = len(result["ok"]) - term
        print("  其中终态 %d 本（覆盖率 +%d、从待登记清单消失）；"
              "非终态 %d 本（覆盖率不变，仍会出现在清单，别重跑）"
              % (term, term, non))
    total = (gaps or {}).get("total")
    registered = (gaps or {}).get("registered")
    rate = (gaps or {}).get("registration_rate")
    if total is not None:
        print("  回执登记覆盖：真被判过 %s/%s%s" % (
            registered, total,
            "（%.1f%%）" % (rate * 100) if isinstance(rate, (int, float)) else ""))
        if not total:
            print("  [提醒] 真源分母为 0：0/0 不是覆盖率达标，"
                  "核对 --data-dir 是否指错或落盘真源是否丢失")
    for kind, label in (("blocked", "不通过"), ("unknown", "判不了")):
        for item in result[kind][:10]:
            print("  [%s 第 %s 行 %s] %s" % (
                label, item["row"], item["task_id"], item["reason"]))
        if len(result[kind]) > 10:
            print("  [%s] 还有 %d 条，先修 CSV 再看" % (
                label, len(result[kind]) - 10))
    for n in result["notes"]:
        print("  [提醒] %s" % n)


def cmd_check(csv_path, data_dir):
    try:
        header, rows = read_csv(csv_path)
    except CsvMissingError as exc:
        print("拒判：%s" % exc)
        return 2
    missing_ids, gaps = load_missing_ids(data_dir)
    result = judge_rows(header, rows, missing_ids)
    print_verdict(result, gaps)
    if result["blocked"] or result["unknown"]:
        print("清单不可登记：有 %d 条不通过 / %d 条判不了，整批拒写" % (
            len(result["blocked"]), len(result["unknown"])))
        return 2
    if not result["ok"]:
        print("清单还没核对（%d 行结论全空）：别跑 --apply，"
              "先去平台核对后填第 6 列" % len(result["skip"]))
        print("留空合法：核对结果若是从未投稿（台账零证据），第 6 列留空"
              "即可，不必填满 %d 行；零证据 ≠ 从未投过，证据只是线索"
              % result["total"])
        return 0
    term = sum(1 for x in result["ok"] if x.get("terminal"))
    print("可登记 %d 本（终态 %d 本 → 覆盖率 +%d；非终态 %d 本 → "
          "覆盖率不变）；确认无误后跑 --apply --dry-run，再看 --apply"
          % (len(result["ok"]), term, term, len(result["ok"]) - term))
    return 0


def cmd_apply(csv_path, data_dir, base, dry_run, confirmed):
    # ① 确认标志：在第一次 IO 之前判，拒写零 GET / 零 POST / 零读盘。
    if not confirmed:
        print("拒写：--apply 必须同时带 %s（人已逐本核对平台结论）" % HUMAN_FLAG)
        return 2
    try:
        header, rows = read_csv(csv_path)
    except CsvMissingError as exc:
        print("拒写：%s" % exc)
        return 2
    # ② 清单判据 + ③ 写前复核：重新取一次落盘真源，防 CSV 编制期间别人登记过。
    missing_ids, gaps = load_missing_ids(data_dir)
    result = judge_rows(header, rows, missing_ids)
    print_verdict(result, gaps)
    if result["blocked"] or result["unknown"]:
        print("整批拒写：有 %d 条不通过 / %d 条判不了，零 POST。先修 CSV" % (
            len(result["blocked"]), len(result["unknown"])))
        return 2
    if not result["ok"]:
        print("整批拒写：没有一行填了结论，零 POST。先去平台核对")
        return 2
    # 第二次取真源：判据跑完到真 POST 之间可能有人已登记过（TOCTOU）。
    # 复用判据那一次的 missing_ids 等于没复核，别把「刚取过」当「写前取过」。
    fresh_ids, _ = load_missing_ids(data_dir)
    stale = [x["task_id"] for x in result["ok"]
             if x["task_id"] not in fresh_ids]
    if stale:
        print("整批拒写：%d 本在写前复核时已不在待登记清单，零 POST" % len(stale))
        return 2
    print("写前复核通过：%d 本仍在 outcome_gaps.missing" % len(result["ok"]))
    if dry_run:
        print("--dry-run：以下 %d 条**不会**发出（零 POST / 零 GET）" %
              len(result["ok"]))
        for x in result["ok"]:
            print("  POST /api/publish/task/%s/outcome  %s|%s" % (
                x["task_id"], x["platform"], x["stage"]))
        return 0

    # ④ 真写：逐条 POST + GET 复核。POST 已发生不可回滚，失败如实报、不重试。
    bad = []
    for x in result["ok"]:
        path = "/api/publish/task/%s/outcome" % x["task_id"]
        body = {"platform": x["platform"], "stage": x["stage"],
                "quote": x["quote"]}
        try:
            api_post(base, path, body)
            view = api_get(base, path)
        except Exception as exc:
            bad.append("%s：请求异常 %s（POST 可能已发生，别重跑这一本）" % (
                x["task_id"], str(exc)[:120]))
            continue
        got = ((view or {}).get("platforms") or {}).get(x["platform"]) or {}
        if str(got.get("stage") or "") != x["stage"]:
            bad.append("%s：写后 stage=%r ≠ 期望 %s（POST 已发生，别重跑）" % (
                x["task_id"], got.get("stage"), x["stage"]))
    print("登记完成：%d 本写后复核通过，%d 本异常" % (
        len(result["ok"]) - len(bad), len(bad)))
    term = sum(1 for x in result["ok"] if x.get("terminal"))
    non = len(result["ok"]) - term
    if non:
        print("  其中非终态 %d 本：覆盖率不变，这些书仍会出现在待登记清单"
              "（正常）；别因清单里还在就重跑 --apply（会重复写 history）"
              % non)
    for line in bad:
        print("  [异常] %s" % line)
    _, after = load_missing_ids(data_dir)
    if (after or {}).get("total") is not None:
        print("写后覆盖：真被判过 %s/%s" % (
            after.get("registered"), after.get("total")))
    if bad:
        print("别重跑整批：先把上面几本按 /api/runs 或回执页面人工核对")
        return 2
    return 0


def template():
    print("CSV 第 6 列填写契约（第 7 列提示文案不写进台账）：")
    print("  <平台>|<阶段>|<平台原话>")
    print("  平台：%s" % "/".join(sorted(outcome.PLATFORMS)))
    print("  阶段：%s" % "/".join(outcome.STAGE_ORDER))
    print("  终态（登记后覆盖率 +1、从待登记清单消失）：%s"
          % "/".join(s for s in outcome.STAGE_ORDER
                     if is_terminal_stage(s)))
    print("  非终态（覆盖率不变、书仍在清单里；别重跑）：%s"
          % "/".join(s for s in outcome.STAGE_ORDER
                     if not is_terminal_stage(s)))
    print("  驳回类（%s）必须带第 3 段原话，否则整批判不了、拒写" %
          "/".join(sorted(k for k, v in outcome.STAGES.items()
                          if v.get("needs_quote"))))
    print("示例：")
    print("  qimao|signed|")
    print("  fanqie|in_review   ← 非终态：覆盖率不变，别重跑")
    print("  fanqie|rejected|缺乏吸引力，建议修改开篇")
    print("留空 = 这本还没核对（skip，不写）；核对结果若是「从未投稿」，"
          "留空合法，不必填满（零证据 ≠ 从未投过，台账证据只是线索）。"
          "填完依次跑：")
    print("  py -3.13 scripts/outcome_register.py --check")
    print("  py -3.13 scripts/outcome_register.py --apply --dry-run " + HUMAN_FLAG)
    print("  py -3.13 scripts/outcome_register.py --apply " + HUMAN_FLAG)
    return 0


def main(argv=None):
    ap = argparse.ArgumentParser(
        description="ⓒ 人工补登记：把 outcome-gaps CSV 变成有消费点的批量登记工具")
    mode = ap.add_mutually_exclusive_group()
    mode.add_argument("--check", action="store_true",
                      help="默认：只读判据，零 HTTP、零写入")
    mode.add_argument("--apply", action="store_true",
                      help="真写（必须带 " + HUMAN_FLAG + "）")
    ap.add_argument("--dry-run", action="store_true",
                    help="与 --apply 合用：走完全部闸但零 POST / 零 GET")
    ap.add_argument(HUMAN_FLAG, action="store_true", dest="confirmed",
                    help="人已逐本核对平台结论的显式确认")
    ap.add_argument("--template", action="store_true",
                    help="只打印填写契约，零 IO")
    ap.add_argument("--csv", default=str(ROOT / CSV_REL),
                    help="清单 CSV 路径（默认 %s）" % CSV_REL)
    ap.add_argument("--data-dir", default=default_data_dir(),
                    help="落盘真源（默认 %%APPDATA%%/CodeBee，只读）")
    ap.add_argument("--base", default=BASE,
                    help="写入口（默认 %s，只影响 --apply）" % BASE)
    args = ap.parse_args(argv)
    if args.template:
        return template()
    if args.apply:
        return cmd_apply(args.csv, args.data_dir, args.base,
                         args.dry_run, args.confirmed)
    return cmd_check(args.csv, args.data_dir)


if __name__ == "__main__":
    sys.exit(main())
