# -*- coding: utf-8 -*-
"""P1 实案转回归（第一刀）：把 data/errors 台账里的真实失败冻成分类器回归夹具。

口径（迭代计划 P1，原料修正版）：复核假设的 `data/publish/shots` 里没有
fail-*.png（0 个，115 张全是步骤截图）——实案原料的真源是 **data/errors
台账**（612 条，2026-09 起）。本脚本：
  1. 全量读台账，对每条 detail 跑 error_codes.classify_error_text；
  2. 分类结果 == 台账 reason 的 → 冻结为**一致性夹具**（锁分类器对真实
     实案原文的行为，改坏分类即红）；
  3. 不一致/None 的 → 计入缺口清单打印（≠ bug：reason 可能来自结构化
     信号如退出码/超时标志，不来自文本），不给归零承诺；
  4. 每类上限 4 条（优先最近、detail 更长者），另把历史著名实案的关键词
     （1311 权限 429 / 七天上限 / 余额不足 / MISSING_.*_KEY 内嵌形式）
     强制收录——它们是分类器补丁的直接出处。

产物：tests/fixtures/error_cases.json（含 ledger 溯源：day+record 序号）。
可重复跑：台账新增实案后重跑即扩充（幂等去重按 detail 指纹）。
"""
from __future__ import annotations

import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "app"))

from core.error_codes import classify_error_text, ErrorCode  # noqa: E402

LEDGER_GLOB = "data/errors/errors-*.jsonl"
OUT = ROOT / "tests" / "fixtures" / "error_cases.json"
PER_CLASS_CAP = 4

# 历史著名实案强制收录（分类器补丁的直接出处，见 error_codes.py 注释）。
# 每个模式绑定**既定类**：从全台账取真实行、分类结果==既定类才冻结——
# 锁的是「这段真实报错必须归这个类」的历史裁决，不改台账 reason 是否一致。
MUST_PATTERNS = {
    "quota_weekly_cap": (re.compile(r"达到[^\n]{0,12}(?:使用|用量)上限"),
                         "QUOTA"),
    "quota_balance": (re.compile(r"余额|额度|欠费|预扣费", re.I), "QUOTA"),
    "auth_401": (re.compile(r"401"), "AUTH"),
    "forbidden_403": (re.compile(r"403"), "FORBIDDEN"),
    "missing_key_inline": (re.compile(r"MISSING_\w+_KEY|_API_KEY\s+(?:is\s+)?not\s+set", re.I),
                           "MISSING_CREDENTIAL"),
    "model_unavailable": (re.compile(r"模型不可用|无可用渠道|model is not configured", re.I),
                          "MODEL_UNAVAILABLE"),
    "tool_launch": (re.compile(r"not recognized|ENOENT|spawn|无法找到", re.I),
                    "TOOL_LAUNCH"),
}


def load_ledger():
    rows = []
    for p in sorted(ROOT.glob(LEDGER_GLOB)):
        day = p.stem.replace("errors-", "")
        for i, line in enumerate(p.read_text(encoding="utf-8",
                                             errors="replace").splitlines()):
            line = line.strip()
            if not line.startswith("{"):
                continue
            try:
                r = json.loads(line)
            except ValueError:
                continue
            r["_day"] = day
            r["_idx"] = i
            rows.append(r)
    return rows


def main():
    rows = load_ledger()
    agreed, gap = [], 0
    for r in rows:
        detail = str(r.get("detail") or "")
        reason = str(r.get("reason") or "")
        if not detail or not reason:
            continue
        got = classify_error_text(detail)
        want = reason.split(".")[-1]          # ErrorCode.VENDOR_ERROR → VENDOR_ERROR
        if got is not None and str(got.value) == want:
            agreed.append(r)
        else:
            gap += 1

    # 每类选代表：优先命中强制实案关键词，其次 detail 更长（信号更富）、最近
    by_class = {}
    for r in agreed:
        by_class.setdefault(str(r.get("reason")), []).append(r)

    chosen, seen_fp = [], set()
    reasons = sorted(by_class)

    def take(r, tag, expect=None):
        fp = re.sub(r"\s+", " ", str(r.get("detail") or ""))[:200]
        if fp in seen_fp:
            return
        seen_fp.add(fp)
        chosen.append({
            "reason": str(r.get("reason")),
            "expect_class": expect or str(r.get("reason")),
            "detail": str(r.get("detail"))[:600],
            "incident": "%s#%s" % (r.get("_day"), r.get("_idx")),
            "tool": str(r.get("tool") or "")[:24],
            "tag": tag,
        })

    # 强制实案：模式命中 + 分类==既定类 才冻结（锁历史裁决）
    must_n = 0
    for tag, (pat, want) in MUST_PATTERNS.items():
        n = 0
        for r in rows:   # 全台账，不限一致行
            if n >= 2:
                break
            detail = str(r.get("detail") or "")
            if not pat.search(detail):
                continue
            got = classify_error_text(detail)
            if got is not None and str(got.value) == want:
                take(r, tag, expect=want)
                n += 1
                must_n += 1
    # 每类一致性代表补齐
    for reason in reasons:
        pool = sorted(by_class[reason],
                      key=lambda r: (-len(str(r.get("detail") or "")),
                                     str(r.get("_day"))))
        for r in pool[:PER_CLASS_CAP]:
            take(r, "class-rep")

    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps({
        "generated": __import__("time").strftime("%Y-%m-%d %H:%M:%S"),
        "ledger_scope": LEDGER_GLOB,
        "ledger_records": len(rows),
        "agreed": len(agreed),
        "gap": gap,
        "must_cases": must_n,
        "cases": chosen,
    }, ensure_ascii=False, indent=1), encoding="utf-8")
    print("ledger=%d agreed=%d gap=%d -> fixtures=%d (must=%d) (%s)"
          % (len(rows), len(agreed), gap, len(chosen), must_n, OUT))


if __name__ == "__main__":
    main()
