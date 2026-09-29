# -*- coding: utf-8 -*-
"""Deterministic quality and release gates for generated fiction.

LLM reviewers provide judgements; this module decides whether the evidence is
valid enough to support a release action.  It deliberately treats missing,
malformed, out-of-range and non-consensus scores as unevaluated.
"""
from __future__ import annotations

import math
import re
from pathlib import Path


MIN_REVIEWERS = 2
DEFAULT_MAX_SPREAD = 2.0
_CHAPTER_RE = re.compile(r"^chapter-(\d{1,4})\.(?:md|txt)$", re.I)


def normalize_scores(raw, dimensions):
    """Return (scores, errors); incomplete scores are invalid as a set."""
    raw = raw if isinstance(raw, dict) else {}
    scores, errors = {}, {}
    for dim in dimensions or []:
        value = raw.get(dim)
        try:
            number = float(value)
        except (TypeError, ValueError):
            errors[dim] = "非数字"
            continue
        if not math.isfinite(number) or number < 1.0 or number > 10.0:
            errors[dim] = "必须在 1-10 之间"
            continue
        scores[dim] = round(number, 2)
    if errors or len(scores) != len(list(dimensions or [])):
        return {}, errors or {str(d): "缺失" for d in dimensions or [] if d not in scores}
    return scores, {}


def aggregate_reviews(reviews, dimensions, threshold=7.0,
                      min_reviewers=MIN_REVIEWERS,
                      max_spread=DEFAULT_MAX_SPREAD):
    """Validate reviewers, calculate means, and require reviewer consensus."""
    dims = list(dimensions or [])
    valid = []
    invalid = []
    seen = set()
    for idx, review in enumerate(reviews or []):
        review = review if isinstance(review, dict) else {}
        reviewer_id = str(review.get("id") or "reviewer-%d" % idx)
        if reviewer_id in seen:
            continue
        seen.add(reviewer_id)
        scores, errors = normalize_scores(review.get("scores"), dims)
        if errors:
            invalid.append({"id": reviewer_id, "errors": errors})
        else:
            valid.append({"id": reviewer_id, "scores": scores})

    reasons = []
    eligible = len(valid) >= int(min_reviewers)
    if not eligible:
        reasons.append("至少需要 %d 名有效评审，当前 %d 名" %
                       (int(min_reviewers), len(valid)))
    means = {}
    spreads = {}
    for dim in dims:
        values = [r["scores"][dim] for r in valid]
        if values:
            means[dim] = round(sum(values) / len(values), 1)
            spreads[dim] = round(max(values) - min(values), 1)

    consensus = bool(eligible and means and
                     all(spreads.get(dim, float("inf")) <= float(max_spread)
                         for dim in dims))
    if eligible and not consensus:
        bad = [dim for dim in dims if spreads.get(dim, float("inf")) > float(max_spread)]
        reasons.append("评审分歧超过 %.1f 分：%s" % (float(max_spread), "、".join(bad)))
    passed = bool(consensus and means and
                  all(value >= float(threshold) for value in means.values()))
    if consensus and not passed:
        reasons.append("综合分存在低于 %.1f 的维度" % float(threshold))
    return {
        "eligible": eligible,
        "consensus": consensus,
        "passed": passed,
        "means": means,
        "spreads": spreads,
        "valid_reviewers": valid,
        "invalid_reviewers": invalid,
        "reviewer_count": len(valid),
        "reasons": reasons,
    }


def local_version(workdir):
    """Return the local manuscript version used for a release decision."""
    root = Path(workdir or "")
    rows = []
    if root.is_dir():
        for path in root.iterdir():
            match = _CHAPTER_RE.match(path.name)
            if not match or not path.is_file():
                continue
            try:
                text = path.read_text(encoding="utf-8", errors="replace")
            except OSError:
                continue
            rows.append((int(match.group(1)), len(re.sub(r"\s", "", text))))
    return {
        "local_total": len(rows),
        "local_latest": max((n for n, _ in rows), default=0),
        "local_words": sum(words for _, words in rows),
    }


def evaluate_release(verdict, action="publish", target_chapter=None,
                     platform_version=None, force=False,
                     force_confirmed=False, force_reason=""):
    """Decide whether a publish/signing action is allowed.

    ``continue`` is intentionally separate: writers may continue improving an
    unready manuscript, while publishing and signing remain gated.
    """
    verdict = verdict if isinstance(verdict, dict) else {}
    action = str(action or "publish").strip().lower()
    if action == "continue":
        return {"allowed": True, "status": "continue_only", "forced": False,
                "blockers": [], "warnings": []}

    blockers = []
    warnings = []
    if verdict.get("publishable") is not True:
        blockers.append("最近一次质量评审未达标")
    if verdict.get("global_pass") is False:
        blockers.append("全局一致性评审未通过")
    if verdict.get("global_reviewer_count", 0) < MIN_REVIEWERS:
        blockers.append("有效全局评审不足 %d 名" % MIN_REVIEWERS)
    if verdict.get("global_reviewer_consensus") is False:
        blockers.append("全局评审未形成共识")
    chapter_scores = verdict.get("chapter_scores") or []
    if target_chapter is not None:
        try:
            target = int(target_chapter)
        except (TypeError, ValueError):
            target = 0
        try:
            reviewed_end = int(verdict.get("end_chapter") or 0)
        except (TypeError, ValueError):
            reviewed_end = 0
        if target > 0 and reviewed_end < target:
            blockers.append("目标章节超出最近评审版本（目标第 %d 章，评审至第 %d 章）" %
                            (target, reviewed_end))
    if chapter_scores and any(c.get("passed") is not True for c in chapter_scores):
        blockers.append("存在未达标章节")
    if any(c.get("reviewer_count", MIN_REVIEWERS) < MIN_REVIEWERS or
           c.get("reviewer_consensus") is False for c in chapter_scores):
        blockers.append("存在评审人数不足或无共识章节")
    majors = [x for x in (verdict.get("major_issues") or [])
              if isinstance(x, dict) and x.get("severity") == "major"]
    if majors:
        blockers.append("存在 major 问题（共 %d 条）" % len(majors))

    if action == "signing":
        checkpoint = verdict.get("signing_checkpoint") or {}
        if checkpoint.get("passed") is not True:
            blockers.append("2 万字签约检查点未通过")
        if checkpoint.get("reviewer_count", MIN_REVIEWERS) < MIN_REVIEWERS:
            blockers.append("签约检查点有效评审不足 %d 名" % MIN_REVIEWERS)
        if checkpoint.get("reviewer_consensus") is False:
            blockers.append("签约检查点评审未形成共识")
        remote = platform_version if isinstance(platform_version, dict) else {}
        remote_n = remote.get("remote_published")
        if remote_n is None:
            remote_n = remote.get("remote_total")
        end_chapter = int(target_chapter or verdict.get("end_chapter") or 0)
        if end_chapter and (remote_n is None or int(remote_n or 0) < end_chapter):
            blockers.append("平台版本与申请评审版本不一致（本地第 %d 章，平台已发布第 %d 章）" %
                            (end_chapter, int(remote_n or 0)))

    forced = bool(force)
    if blockers and forced:
        if not force_confirmed:
            blockers.append("强制放行必须二次确认")
        if not str(force_reason or "").strip():
            blockers.append("强制放行必须填写人工复核原因")
        if force_confirmed and str(force_reason or "").strip() and \
                all("强制放行必须" not in x for x in blockers):
            warnings = list(blockers)
            blockers = []
    allowed = not blockers
    return {"allowed": allowed, "status": "allowed" if allowed else "blocked",
            "forced": bool(allowed and forced), "blockers": blockers,
            "warnings": warnings}
