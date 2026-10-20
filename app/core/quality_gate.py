# -*- coding: utf-8 -*-
"""Deterministic quality and release gates for generated fiction.

LLM reviewers provide judgements; this module decides whether the evidence is
valid enough to support a release action.  It deliberately treats missing,
malformed, out-of-range and non-consensus scores as unevaluated.
"""
from __future__ import annotations

import hashlib
import math
import re
from pathlib import Path


MIN_REVIEWERS = 2
DEFAULT_MAX_SPREAD = 2.0
_CHAPTER_RE = re.compile(r"^chapter-(\d{1,4})\.(?:md|txt)$", re.I)


def _manuscript_name(value):
    """Match pipeline manuscript-name sanitization without importing pipeline."""
    name = re.sub(r"[\\/\x00]+", "_", str(value or "")).strip()
    name = re.sub(r"\.{2,}", "_", name).lstrip(".")
    return Path(name).name or "manuscript.md"


def has_complete_local_prefix(workdir, start_chapter):
    """Match the pipeline's rule for whether archived prose fills a prefix gap."""
    root = Path(workdir or "")
    start = _chapter_number(start_chapter)
    if start is None or start < 1:
        return False
    if start == 1:
        return True
    for chapter in range(1, start):
        path = root / ("chapter-%02d.md" % chapter)
        try:
            if not path.read_bytes().decode("utf-8", errors="replace").strip():
                return False
        except OSError:
            return False
    return True


def fingerprint_texts(rows):
    """Hash named text evidence with unambiguous boundaries."""
    digest = hashlib.sha256()
    for name, text in rows:
        digest.update(str(name).encode("utf-8"))
        digest.update(b"\0")
        digest.update(str(text or "").encode("utf-8"))
        digest.update(b"\0")
    return digest.hexdigest()


def _nonnegative_int(value):
    """Parse integer counters without accepting booleans or fractional values."""
    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        return value if value >= 0 else None
    if isinstance(value, str) and re.fullmatch(r"[0-9]+", value.strip()):
        try:
            return int(value.strip())
        except (ValueError, OverflowError):
            return None
    return None


def _chapter_number(value):
    """Parse a chapter index within the repository's four-digit filename range."""
    number = _nonnegative_int(value)
    return number if number is not None and number <= 9999 else None


def _valid_score_map(value):
    """Require a nonempty map of finite rubric scores in the supported range."""
    if not isinstance(value, dict) or not value:
        return False
    for dimension, score in value.items():
        if (not isinstance(dimension, str) or not dimension.strip() or
                isinstance(score, bool) or not isinstance(score, (int, float))):
            return False
        try:
            number = float(score)
        except (OverflowError, ValueError):
            return False
        if not math.isfinite(number) or number < 1.0 or number > 10.0:
            return False
    return True


def _scores_meet_threshold(scores, threshold):
    """Recompute a pass decision from validated persisted score evidence."""
    if not _valid_score_map(scores):
        return False
    if isinstance(threshold, bool) or not isinstance(threshold, (int, float)):
        return False
    try:
        minimum = float(threshold)
    except (OverflowError, ValueError):
        return False
    return (math.isfinite(minimum) and 1.0 <= minimum <= 10.0 and
            all(float(score) >= minimum for score in scores.values()))


def _chapter_threshold(threshold):
    """Return the serial pipeline's relaxed chapter threshold, if valid."""
    if isinstance(threshold, bool) or not isinstance(threshold, (int, float)):
        return None
    try:
        minimum = float(threshold)
    except (OverflowError, ValueError):
        return None
    if not math.isfinite(minimum) or not 1.0 <= minimum <= 10.0:
        return None
    return minimum - 0.5 if minimum >= 7.5 else minimum


def normalize_scores(raw, dimensions):
    """Return (scores, errors); incomplete scores are invalid as a set."""
    raw = raw if isinstance(raw, dict) else {}
    dims = list(dimensions or [])
    scores, errors = {}, {}
    for dim in dims:
        value = raw.get(dim)
        if isinstance(value, bool):
            errors[dim] = "非数字"
            continue
        try:
            number = float(value)
        except (TypeError, ValueError, OverflowError):
            errors[dim] = "非数字"
            continue
        if not math.isfinite(number) or number < 1.0 or number > 10.0:
            errors[dim] = "必须在 1-10 之间"
            continue
        scores[dim] = round(number, 2)
    if errors or len(scores) != len(dims):
        return {}, errors or {str(d): "缺失" for d in dims if d not in scores}
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


def local_version(workdir, manuscript=None, start_chapter=None,
                  end_chapter=None, include_archive=False):
    """Return word-count metadata and a stable fingerprint of manuscript files."""
    root = Path(workdir or "")
    start_filter = None if start_chapter is None else _chapter_number(start_chapter)
    end_filter = None if end_chapter is None else _chapter_number(end_chapter)
    # Scope values can originate in persisted checkpoints or API input.  Do
    # not compare malformed strings/bools with chapter numbers or accidentally
    # treat ``True`` as chapter 1; an unverifiable snapshot must fail closed.
    if ((start_chapter is not None and start_filter is None) or
            (end_chapter is not None and end_filter is None) or
            (start_filter is not None and end_filter is not None and
             end_filter < start_filter)):
        return {"local_total": 0, "local_latest": 0, "local_words": 0,
                "fingerprint": ""}
    rows = []
    fingerprint_rows = []
    if root.is_dir():
        candidates = []
        if start_chapter is not None or end_chapter is not None:
            for path in root.iterdir():
                match = _CHAPTER_RE.match(path.name)
                if not match or not path.is_file():
                    continue
                chapter_number = int(match.group(1))
                if ((start_filter is None or chapter_number >= start_filter) and
                        (end_filter is None or chapter_number <= end_filter)):
                    candidates.append(path)
        if manuscript:
            manuscript_path = root / _manuscript_name(manuscript)
            if manuscript_path.is_file():
                candidates.append(manuscript_path)
        elif start_chapter is None and (root / "manuscript.md").is_file():
            candidates.append(root / "manuscript.md")
        archive = root / "已成稿"
        if include_archive and archive.is_dir():
            seen_stems = set()
            for path in sorted(archive.iterdir(), key=lambda item: item.name.casefold()):
                stem = path.stem.casefold()
                if (not path.is_file() or path.suffix.lower() not in (".md", ".txt")
                        or "目录" in path.name or stem in seen_stems):
                    continue
                candidates.append(path)
                seen_stems.add(stem)
        unique_candidates = {}
        for path in candidates:
            key = path.relative_to(root).as_posix().casefold()
            unique_candidates.setdefault(key, path)
        for path in sorted(unique_candidates.values(),
                           key=lambda item: item.relative_to(root).as_posix().casefold()):
            try:
                content = path.read_bytes()
            except OSError:
                continue
            relative = path.relative_to(root).as_posix()
            fingerprint_rows.append((relative, content))
            match = _CHAPTER_RE.match(path.name)
            if match and path.parent == root:
                text = content.decode("utf-8", errors="replace")
                rows.append((int(match.group(1)), len(re.sub(r"\s", "", text))))
    digest = hashlib.sha256()
    for relative, content in fingerprint_rows:
        digest.update(relative.encode("utf-8"))
        digest.update(b"\0")
        digest.update(content)
        digest.update(b"\0")
    return {
        "local_total": len(rows),
        "local_latest": max((n for n, _ in rows), default=0),
        "local_words": sum(words for _, words in rows),
        "fingerprint": digest.hexdigest() if fingerprint_rows else "",
    }


def evaluate_release(verdict, action="publish", target_chapter=None,
                     platform_version=None, force=False,
                     force_confirmed=False, force_reason="", serial=None,
                     current_manuscript_fingerprint=None):
    """Decide whether a publish/signing action is allowed.

    ``continue`` is intentionally separate: writers may continue improving an
    unready manuscript, while publishing and signing remain gated.
    """
    verdict = verdict if isinstance(verdict, dict) else {}
    action = str(action or "publish").strip().lower()
    if action == "continue":
        return {"allowed": True, "status": "continue_only", "forced": False,
                "blockers": [], "warnings": []}
    if action == "draft":
        # 存草稿不是发布：内容不公开展示（读者不可见），正式提交前用户还会
        # 在平台上逐章过目——质量闸只在「发布/签约」这类上线动作上拦，
        # 拦草稿只会把「先落平台再人工把关」的通路堵死（2026-10-09 全部
        # 发草稿需求：质量未达标的 53 章先存草稿，发布仍受本闸约束）。
        return {"allowed": True, "status": "draft_only", "forced": False,
                "blockers": [], "warnings": []}

    blockers = []
    warnings = []
    if "manuscript_fingerprint" in verdict:
        reviewed_fingerprint = verdict.get("manuscript_fingerprint")
        if (not isinstance(reviewed_fingerprint, str) or not reviewed_fingerprint or
                not isinstance(current_manuscript_fingerprint, str) or
                not current_manuscript_fingerprint):
            blockers.append("当前正文版本无法核验，请重新评审当前稿件")
        elif reviewed_fingerprint != current_manuscript_fingerprint:
            blockers.append("评审后正文已变化，请重新评审当前稿件")
    if verdict.get("publishable") is not True:
        blockers.append("最近一次质量评审未达标")
    # 连载小说 verdict 带有全局/逐章证据；普通文章、文档等 review
    # 任务只有通用 publishable/scores 结论，不能被连载专用字段反向拦截。
    # 任务配置和 verdict 任一表明连载时都保留完整门禁；未知的旧格式
    # 继续要求连载证据，只有明确的单篇评审使用通用结论。
    serial_mode = (bool(serial) or bool(verdict.get("serial")) or
                   "chapter_scores" in verdict or
                   "global_reviewer_count" in verdict or action == "signing" or
                   (serial is None and not isinstance(verdict.get("scores"), dict)))
    if serial_mode and verdict.get("global_pass") is not True:
        blockers.append("全局一致性评审未通过")
    if serial_mode and (_nonnegative_int(verdict.get("global_reviewer_count")) or 0) < MIN_REVIEWERS:
        blockers.append("有效全局评审不足 %d 名" % MIN_REVIEWERS)
    if serial_mode and verdict.get("global_reviewer_consensus") is not True:
        blockers.append("全局评审未形成共识")
    threshold = verdict.get("threshold")
    global_scores = verdict.get("global_scores")
    global_scores_valid = _valid_score_map(global_scores)
    if serial_mode and not global_scores_valid:
        blockers.append("全局评分证据缺失或无效")
    if serial_mode and not _scores_meet_threshold(global_scores, threshold):
        blockers.append("全局评分未达到有效发布门槛")
    chapter_threshold = _chapter_threshold(threshold)
    chapter_scores_value = verdict.get("chapter_scores")
    chapter_scores = chapter_scores_value if serial_mode and isinstance(
        chapter_scores_value, list) else []
    if serial_mode and (not isinstance(chapter_scores_value, list) or not chapter_scores_value):
        blockers.append("逐章评审数据缺失或格式无效")
    if serial_mode and target_chapter is not None:
        target = _chapter_number(target_chapter)
        reviewed_end = _chapter_number(verdict.get("end_chapter")) or 0
        if target is None or target < 1:
            blockers.append("目标章节号无效")
            target = 0
        if target > 0 and reviewed_end < target:
            blockers.append("目标章节超出最近评审版本（目标第 %d 章，评审至第 %d 章）" %
                            (target, reviewed_end))
    malformed_chapters = any(not isinstance(chapter, dict) for chapter in chapter_scores)
    if malformed_chapters:
        blockers.append("逐章评审数据格式无效")
    chapter_scores = [chapter for chapter in chapter_scores if isinstance(chapter, dict)]
    if serial_mode:
        start_chapter = _chapter_number(verdict.get("start_chapter"))
        end_chapter = _chapter_number(verdict.get("end_chapter"))
        chapter_numbers = [_chapter_number(chapter.get("chapter"))
                           for chapter in chapter_scores]
        chapter_number_set = set(chapter_numbers)
        complete_coverage = False
        if (start_chapter is None or start_chapter < 1 or end_chapter is None or
                end_chapter < start_chapter or
                any(number is None or number < 1 for number in chapter_numbers)):
            blockers.append("逐章评审范围或章节编号无效")
        else:
            # Narrow after the validation above so type checkers and the proof
            # below both operate on actual chapter numbers only.
            valid_chapter_numbers = [number for number in chapter_numbers
                                     if number is not None]
            chapter_number_set = set(valid_chapter_numbers)
            expected_count = end_chapter - start_chapter + 1
            # Avoid materializing range(start, end + 1): persisted verdicts are
            # untrusted input and a forged huge end_chapter must not allocate
            # memory proportional to that number.  Unique in-range chapter IDs
            # with exactly the expected count prove complete coverage.
            complete_coverage = (
                len(chapter_number_set) == len(valid_chapter_numbers) == expected_count and
                all(start_chapter <= number <= end_chapter
                    for number in chapter_number_set))
        if (start_chapter is not None and start_chapter >= 1 and
                end_chapter is not None and end_chapter >= start_chapter and
                all(number is not None and number >= 1 for number in chapter_numbers) and
                not complete_coverage):
            blockers.append("逐章评审未完整覆盖声明范围（第 %d-%d 章）" %
                            (start_chapter, end_chapter))
        requested_chapter = _chapter_number(target_chapter)
        if (target_chapter is not None and requested_chapter is not None and
                requested_chapter not in chapter_number_set):
            blockers.append("目标章节没有对应的逐章评审证据")
    if chapter_scores and any(c.get("passed") is not True for c in chapter_scores):
        blockers.append("存在未达标章节")
    if serial_mode and any(not _valid_score_map(chapter.get("means"))
                           for chapter in chapter_scores):
        blockers.append("逐章评分证据缺失或无效")
    if serial_mode and any(not _scores_meet_threshold(chapter.get("means"), chapter_threshold)
                           for chapter in chapter_scores):
        blockers.append("存在评分低于逐章发布门槛的章节")
    if serial_mode and global_scores_valid and isinstance(global_scores, dict) and any(
            _valid_score_map(chapter.get("means")) and
            set(chapter["means"]) != set(global_scores)
            for chapter in chapter_scores):
        blockers.append("逐章与全局评分维度不一致")
    if any((_nonnegative_int(c.get("reviewer_count")) or 0) < MIN_REVIEWERS or
           c.get("reviewer_consensus") is not True
           for c in chapter_scores):
        blockers.append("存在评审人数不足或无共识章节")
    major_issues_value = verdict.get("major_issues")
    if serial_mode and not isinstance(major_issues_value, list):
        blockers.append("全书 major 问题评审数据缺失或格式无效")
    major_issues = major_issues_value if isinstance(major_issues_value, list) else []
    if serial_mode and any(
            not isinstance(issue, dict) or issue.get("severity") != "major" or
            issue.get("anchored") is False
            for issue in major_issues):
        blockers.append("全书 major 问题评审数据格式无效")
    majors = [x for x in major_issues
              if isinstance(x, dict) and x.get("severity") == "major"]
    if majors:
        blockers.append("存在 major 问题（共 %d 条）" % len(majors))

    if action == "signing":
        checkpoint_value = verdict.get("signing_checkpoint")
        checkpoint = checkpoint_value if isinstance(checkpoint_value, dict) else {}
        if checkpoint_value is not None and not isinstance(checkpoint_value, dict):
            blockers.append("签约检查点数据格式无效")
        if checkpoint.get("passed") is not True:
            blockers.append("2 万字签约检查点未通过")
        if not _valid_score_map(checkpoint.get("means")):
            blockers.append("签约检查点评分证据缺失或无效")
        if not _scores_meet_threshold(checkpoint.get("means"), threshold):
            blockers.append("签约检查点评分未达到有效发布门槛")
        checkpoint_words = _nonnegative_int(checkpoint.get("at_words"))
        if checkpoint_words is None or checkpoint_words < 20000:
            blockers.append("签约检查点字数证据不足 20000 字")
        checkpoint_chapter = _chapter_number(checkpoint.get("chapter"))
        reviewed_end = _chapter_number(verdict.get("end_chapter"))
        if (checkpoint_chapter is None or checkpoint_chapter < 1 or
                reviewed_end is None or checkpoint_chapter > reviewed_end or
                checkpoint_chapter not in chapter_number_set):
            blockers.append("签约检查点章节不在已评审范围内")
        if (_nonnegative_int(checkpoint.get("reviewer_count")) or 0) < MIN_REVIEWERS:
            blockers.append("签约检查点有效评审不足 %d 名" % MIN_REVIEWERS)
        if checkpoint.get("reviewer_consensus") is not True:
            blockers.append("签约检查点评审未形成共识")
        remote = platform_version if isinstance(platform_version, dict) else {}
        remote_n = remote.get("remote_published")
        if remote_n is None:
            remote_n = remote.get("remote_total")
        end_chapter = _chapter_number(
            target_chapter if target_chapter is not None else verdict.get("end_chapter")) or 0
        remote_chapter = _chapter_number(remote_n)
        if end_chapter and (remote_chapter is None or remote_chapter < end_chapter):
            blockers.append("平台版本与申请评审版本不一致（本地第 %d 章，平台已发布第 %d 章）" %
                            (end_chapter, remote_chapter or 0))

    # These flags authorize bypassing blockers; JSON strings such as "false"
    # must never become truthy and a string must never count as confirmation.
    forced = force is True
    if blockers and forced:
        if force_confirmed is not True:
            blockers.append("强制放行必须二次确认")
        if not str(force_reason or "").strip():
            blockers.append("强制放行必须填写人工复核原因")
        if force_confirmed is True and str(force_reason or "").strip() and \
                all("强制放行必须" not in x for x in blockers):
            warnings = list(blockers)
            blockers = []
    allowed = not blockers
    return {"allowed": allowed, "status": "allowed" if allowed else "blocked",
            "forced": bool(allowed and forced), "blockers": blockers,
            "warnings": warnings}
