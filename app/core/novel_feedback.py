"""Versioned rejection records: platform feedback -> experiment -> sample recheck.

Use ``python -m app.core.novel_feedback --help`` to maintain the local ledger.
Internal sample assessments never become platform acceptance or policy.
"""
from __future__ import annotations

import argparse
import copy
import hashlib
import json
import os
import tempfile
import threading
from pathlib import Path

from . import paths

_LOCK = threading.RLock()
SEED_FILE = Path(__file__).with_name("skillpacks") / "novel-feedback.json"
MAX_BLOCK_CHARS = 2200
LABELS = {"platform_explicit": "平台明确意见", "manuscript_observation": "正文观察",
          "adaptation_hypothesis": "平台适配假设", "unverified": "未验证项"}


def _read(path: Path) -> list[dict]:
    if not path.exists():
        return []
    data = json.loads(path.read_text(encoding="utf-8-sig"))
    if not isinstance(data, list):
        raise ValueError("复盘文件必须是记录数组")
    return data


def _file() -> Path:
    return paths.DATA_DIR / "novel-feedback.json"


def _save(rows: list[dict]) -> None:
    target = _file()
    target.parent.mkdir(parents=True, exist_ok=True)
    fd, name = tempfile.mkstemp(prefix="novel-feedback-", suffix=".tmp", dir=target.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as stream:
            json.dump(rows, stream, ensure_ascii=False, indent=2)
        os.replace(name, target)
    finally:
        Path(name).unlink(missing_ok=True)


def _required(row: dict, key: str) -> str:
    value = row.get(key)
    if not isinstance(value, str) or not value.strip():
        raise ValueError("缺少非空文本字段：%s" % key)
    return value.strip()


def validate(row: dict) -> dict:
    if not isinstance(row, dict):
        raise ValueError("复盘记录必须是对象")
    row = copy.deepcopy(row)
    for key in ("id", "platform", "work", "channel", "feedback_date", "submission_version",
                "submission_scope", "original_quote", "source", "judgment_type", "evidence",
                "change_level", "experiment", "recheck_question"):
        row[key] = _required(row, key)
    if row["platform"] not in ("qimao", "fanqie"):
        raise ValueError("platform 必须是 qimao 或 fanqie")
    if row["judgment_type"] not in LABELS:
        raise ValueError("未知证据类别")
    if row["change_level"] not in ("立项", "结构", "段落", "格式"):
        raise ValueError("未知改动层级")
    if row.get("can_reapply") is not None and not isinstance(row["can_reapply"], bool):
        raise ValueError("can_reapply 必须为 true/false/null（未知）")
    row.setdefault("can_reapply", None)
    tags = row.get("tags")
    if not isinstance(tags, list) or not tags or any(not isinstance(t, str) or not t.strip() for t in tags):
        raise ValueError("tags 必须是非空文本数组")
    trials = row.get("trials", [])
    if not isinstance(trials, list):
        raise ValueError("trials 必须是数组")
    row["trials"] = [_validate_trial(t, check_hashes=True) for t in trials]
    if len({t["id"] for t in row["trials"]}) != len(row["trials"]):
        raise ValueError("复验 id 重复")
    return row


def list_feedback() -> list[dict]:
    """Local amendments override seed records by id; seed trials cannot be invented."""
    with _LOCK:
        rows = {r["id"]: r for r in map(validate, _read(SEED_FILE))}
        rows.update({r["id"]: r for r in map(validate, _read(_file()))})
    return copy.deepcopy(list(rows.values()))


def record_feedback(row: dict) -> dict:
    row = validate(row)
    if row.get("trials"):
        raise ValueError("复验须通过 record_trial 单独录入")
    with _LOCK:
        rows = _read(_file())
        current = next((r for r in list_feedback() if r["id"] == row["id"]), None)
        immutable = ("platform", "work", "channel", "feedback_date", "submission_version",
                     "submission_scope", "original_quote", "source", "judgment_type", "can_reapply")
        if current and any(current[k] != row[k] for k in immutable):
            raise ValueError("原反馈来源与送审版本不可覆盖；新反馈请使用新 id")
        row["trials"] = (current or {}).get("trials", [])
        rows = [r for r in rows if r.get("id") != row["id"]] + [row]
        _save(rows)
    return row


def _validate_trial(trial: dict, *, check_hashes: bool = False) -> dict:
    if not isinstance(trial, dict):
        raise ValueError("复验必须是对象")
    trial = copy.deepcopy(trial)
    for key in ("id", "date", "baseline_version", "revision_version", "change", "method",
                "read_scope", "baseline_excerpt", "revision_excerpt", "baseline_quote",
                "revision_quote", "assessment", "result"):
        trial[key] = _required(trial, key)
    if trial["baseline_version"] == trial["revision_version"]:
        raise ValueError("复验必须区分新旧稿版本")
    if trial["result"] not in ("improved", "not_improved", "inconclusive"):
        raise ValueError("仅允许内部复验结果；平台反馈需新增独立记录")
    for side in ("baseline", "revision"):
        quote, excerpt = trial[side + "_quote"], trial[side + "_excerpt"]
        if len(quote) < 8 or quote not in excerpt:
            raise ValueError("%s 引文不在送审小样中或过短" % side)
        digest = hashlib.sha256(excerpt.encode("utf-8")).hexdigest()
        if check_hashes and trial.get(side + "_sha256") != digest:
            raise ValueError("复验正文摘要不匹配")
        trial[side + "_sha256"] = digest
    if trial["baseline_excerpt"] == trial["revision_excerpt"]:
        raise ValueError("正文未变化，不能记为修改实验")
    return trial


def record_trial(feedback_id: str, trial: dict) -> dict:
    """Append a cited before/after assessment; never infer a platform outcome."""
    trial = _validate_trial(trial)
    with _LOCK:
        rows = list_feedback()
        row = next((r for r in rows if r["id"] == feedback_id), None)
        if row is None:
            raise ValueError("复盘记录不存在")
        trials = row.setdefault("trials", [])
        if any(t["id"] == trial["id"] for t in trials):
            raise ValueError("复验 id 已存在；历史复验不可覆盖")
        trials.append(trial)
        local = [r for r in _read(_file()) if r.get("id") != feedback_id] + [row]
        _save(local)
    return trial


def relevant(task: dict, limit: int = 3) -> list[dict]:
    if task.get("type") not in ("novel", "serial_novel"):
        return []
    text = " ".join(str(task.get(k) or "") for k in ("title", "goal", "context")).lower()
    # A target named in the goal wins over historical platform quotes in context.
    target = " ".join(str(task.get(k) or "") for k in ("title", "goal")).lower()
    platforms = {p for p, label in (("qimao", "七猫"), ("fanqie", "番茄")) if p in target or label in target}
    if not platforms:
        platforms = {p for p, label in (("qimao", "七猫"), ("fanqie", "番茄")) if p in text or label in text}
    if task.get("platform") in ("qimao", "fanqie"):
        platforms = {task["platform"]}
    matches = []
    for row in list_feedback():
        if platforms and row["platform"] not in platforms:
            continue
        own = row["work"].lower() in text
        hits = sum(t.lower() in text for t in row["tags"])
        if own or hits:
            matches.append((own, hits, row["feedback_date"], row["id"], row))
    matches.sort(key=lambda item: item[:4], reverse=True)
    return [item[4] for item in matches[:limit]]


def block_for(task: dict) -> str:
    try:
        rows = relevant(task)
    except (OSError, ValueError, KeyError, TypeError):
        return ""  # A broken optional ledger must not stop manuscript generation.
    if not rows:
        return ""
    header = ("### 【相关小说拒稿复盘：案例，非平台规则】\n"
              "仅作当前稿诊断线索；不得当成另一平台的意见或过签概率。每轮先定位正文，"
              "说明改动如何改变事件、选择或后果，再对照新旧小样复验。内部 improved 不等于平台接受。\n")
    parts = [header]
    for row in rows:
        trials = row.get("trials") or []
        last = trials[-1] if trials else None
        entry = ("[%s][%s][%s]《%s》；频道：%s；日期：%s；送审版本：%s；范围：%s\n"
                 "原记录：%s\n正文观察（非编辑补充意见）：%s\n改动层级：%s；实验：%s\n复验问题：%s\n"
                 "复验：%s；来源：%s\n" % (
                     row["id"], row["platform"], LABELS[row["judgment_type"]], row["work"],
                     row["channel"], row["feedback_date"], row["submission_version"], row["submission_scope"],
                     row["original_quote"], row["evidence"], row["change_level"], row["experiment"],
                     row["recheck_question"],
                     ("内部 %s，%s→%s，%s" % (last["result"], last["baseline_version"],
                         last["revision_version"], last["assessment"])) if last else "待完成，未验证改后效果",
                     row["source"]))
        if last:
            entry += "复验方法：%s；实际阅读：%s；旧稿引文：%s；新稿引文：%s\n" % (
                last["method"][:80], last["read_scope"][:100],
                last["baseline_quote"][:100], last["revision_quote"][:100])
        if row.get("can_reapply") is False:
            entry += "该原作品无法再次申请签约；保留旧稿，新立项须实质重构，不以改名重复申请。\n"
        if sum(map(len, parts)) + len(entry) > MAX_BLOCK_CHARS:
            continue  # Keep complete evidence records; do not truncate their boundaries.
        parts.append(entry)
    return "\n".join(parts) if len(parts) > 1 else ""


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=("list", "record", "trial"))
    parser.add_argument("file", nargs="?", help="UTF-8 JSON 对象文件")
    parser.add_argument("--feedback-id", help="trial 所对应的反馈 id")
    args = parser.parse_args()
    try:
        if args.action == "list":
            result = list_feedback()
        else:
            if not args.file:
                parser.error("record/trial 需要 JSON 文件")
            data = json.loads(Path(args.file).read_text(encoding="utf-8-sig"))
            result = record_feedback(data) if args.action == "record" else record_trial(args.feedback_id, data)
        print(json.dumps(result, ensure_ascii=False, indent=2))
    except (OSError, ValueError, KeyError, TypeError) as exc:
        parser.exit(1, "复盘记录失败：%s\n" % exc)


if __name__ == "__main__":
    main()
