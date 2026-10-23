"""本地确定性兜底引擎。

外部模型全部不可用时（欠费、七天额度上限、没装 CLI、密钥为空），流程不该
停摆。本模块提供两条独立的本地兜底路径，都不需要任何网络调用：

1. ``local_review``：用确定性算法给稿件打分。检测项全部来自既有的本地质量
   模块（``novel_quality`` / ``chaptersafe``）：整句重复、近似重复、陈词滥调、
   结构完整性、开头信号、连载连续性。每个扣分项都带原文引证，分数可解释、
   可复现。它评的是**可检测**的质量维度——不假装能判断"人物是否立体"，
   因此对无法机测的维度给出保守的中性分，并在 summary 里说明局限。

2. ``local_draft``：本地模板起草。产出"任务拆解与执行计划"文档：把目标、
   约束、建议步骤、验收标准写清楚，作为人工继续加工的起点。明确标注
   ``draft_source="local-template"``，不冒充模型创作。

两条路径的共同原则：**可以成功，但必须诚实**。兜底产物一律带来源标记，
报告里写明"未经外部模型评审/创作"，让用户一眼看出哪些结论是机测的、
哪些需要人来补。
"""

import math
import os
import re
import time

try:
    from . import chaptersafe, novel_quality
except Exception:  # pragma: no cover - 本地模块缺失时降级为空实现
    chaptersafe = None
    novel_quality = None

HEURISTIC = "local-heuristic"
TEMPLATE = "local-template"

# 机测能覆盖的维度 → 用对应检测项打分；不在内的维度给中性分。
_MEASURABLE_HINTS = (
    ("文笔", ("文笔", "语言", "文字", "表达")),
    ("节奏", ("节奏", "结构", "篇幅", "连贯", "一致性")),
    ("情节", ("情节", "故事", "剧情", "内容")),
    ("吸引力", ("吸引力", "开头", "钩子", "可读")),
)

_NEUTRAL_SCORE = 7.0
_MIN_SCORE = 1.0
_MAX_SCORE = 10.0


def _clamp(value):
    try:
        value = float(value)
    except (TypeError, ValueError):
        return _NEUTRAL_SCORE
    if math.isnan(value):
        return _NEUTRAL_SCORE
    return max(_MIN_SCORE, min(_MAX_SCORE, round(value, 1)))


def real_channels(agents):
    """真实（非 mock）且已启用的 CLI 列表。"""
    return [a for a in (agents or [])
            if isinstance(a, dict) and a.get("mode") == "real" and a.get("id")]


def any_live_channel(agents, benched=None):
    """还有没有**可能调得通**的真实 CLI。

    ``benched`` 是本场停用台账（撞过账号级死错的 id）。只按"配置里有没有
    real CLI"判断会漏掉最常见的情形：三条通道都在，但三条都欠费/撞了七天
    上限——那依然是全灭，任何调用都是白烧。台账里全灭时才返回 False。
    """
    real = real_channels(agents)
    if not real:
        return False
    if benched is None:
        return True
    try:
        dead = {str(x) for x in benched}
    except TypeError:
        return True
    return any(str(a.get("id")) not in dead for a in real)


def _sentences(text):
    parts = re.split(r"[。！？!?;\n]+", text or "")
    return [p.strip() for p in parts if len(p.strip()) >= 6]


def _structure_notes(text):
    """结构完整性：段落数、有无小标题、平均段长。"""
    notes = []
    body = (text or "").strip()
    if not body:
        notes.append("稿件为空：没有任何可评审内容")
        return notes
    paras = [p.strip() for p in re.split(r"\n\s*\n", body) if p.strip()]
    if len(paras) < 2 and len(body) > 200:
        notes.append("全文只有一个自然段（%d 字），缺少分段" % len(body))
    long_paras = [p for p in paras if len(p) > 600]
    if long_paras:
        notes.append("有 %d 个超长段落（>600 字），阅读压力大" % len(long_paras))
    if len(body) < 120:
        notes.append("正文仅 %d 字，信息量不足" % len(body))
    return notes


def _penalty(notes, per_item, cap):
    return min(cap, per_item * len(notes or []))


def _detect_issues(text, task_type=""):
    """汇总机测发现的问题，返回 ``(issues, penalties)``。

    ``penalties`` 是 dict：``dup`` / ``cliche`` / ``structure`` / ``opening`` /
    ``continuity`` / ``empty``，调用方按维度映射。
    """
    issues = []
    penalties = {"dup": 0.0, "cliche": 0.0, "structure": 0.0,
                 "opening": 0.0, "continuity": 0.0, "empty": 0.0}
    body = chaptersafe.strip_internal_blocks(text or "") if chaptersafe else (text or "")
    if not body.strip():
        penalties["empty"] = 8.0
        issues.append("稿件为空：没有任何可评审内容")
        return issues, penalties

    # 1) 整句/整段重复。duplicate_notes 把同类重复合并成一条记录，但 count 是
    #    实际重复次数——按次数加权，否则「三段一字不差」只按一处扣，太轻。
    try:
        dups = novel_quality.duplicate_notes(body) or []
    except Exception:
        dups = []
    if dups:
        repeats = sum(max(1, int(d.get("count", 1) or 1)) for d in dups)
        heavy = sum(1 for d in dups if "整段" in str(d.get("kind") or ""))
        penalties["dup"] = min(5.0, 1.2 * repeats + 1.5 * heavy)
        for d in dups[:4]:
            issues.append("%s ×%d：「%s」" % (
                d.get("kind") or "重复", d.get("count", 2),
                str(d.get("quote", ""))[:60]))

    # 2) 近似重复/重复意象（跨段）
    try:
        chapters = [{"text": p} for p in
                    re.split(r"\n\s*\n", body) if p.strip()]
        reps = novel_quality.repetition_notes(chapters) or []
    except Exception:
        reps = []
    if reps:
        penalties["dup"] += _penalty(reps, 0.8, 2.5)
        for r in reps[:3]:
            issues.append("跨段重复：%s" % str(r)[:80])

    # 3) 陈词滥调
    try:
        craft = novel_quality.chapter_craft_notes(body) or []
    except Exception:
        craft = []
    stock = [c for c in craft if "滥" in str(c) or "套" in str(c)
             or "cliche" in str(c).lower()]
    if stock:
        penalties["cliche"] = _penalty(stock, 0.5, 2.0)
        for s in stock[:3]:
            issues.append("表达问题：%s" % str(s)[:80])

    # 4) 结构
    struct = _structure_notes(body)
    if struct:
        penalties["structure"] = _penalty(struct, 1.5, 3.0)
        issues.extend(struct[:3])

    # 5) 开头信号（只对长文本有意义）
    if len(body) > 800:
        try:
            opening = novel_quality.opening_signal_notes(body) or []
        except Exception:
            opening = []
        if opening:
            penalties["opening"] = _penalty(opening, 0.7, 2.0)
            for o in opening[:2]:
                issues.append("开头信号：%s" % str(o)[:80])
    return issues, penalties


def _score_for(dim, penalties, text_len):
    """把一个评分维度映射到机测扣分。

    能机测的维度（文笔/节奏/结构类）吃对应扣分；纯创作维度（人物/氛围类）
    机测无从判断，给中性分——不假装看过，也不冤枉稿件。

    空稿与极短稿一票否决：没有任何维度该拿中性分，"信息量不足"本身就是
    可机测的结论。
    """
    dim = str(dim)
    if penalties.get("empty"):
        return _MIN_SCORE
    if text_len < 120:
        return 3.0
    matched = None
    for key, hints in _MEASURABLE_HINTS:
        if any(h in dim for h in hints):
            matched = key
            break
    if matched is None:
        return _NEUTRAL_SCORE
    if matched == "文笔":
        base = _NEUTRAL_SCORE + 1.0
        cut = penalties["dup"] * 0.5 + penalties["cliche"]
    elif matched == "节奏":
        base = _NEUTRAL_SCORE + 0.5
        cut = penalties["structure"] * 0.6 + penalties["dup"] * 0.3
    elif matched == "情节":
        base = _NEUTRAL_SCORE
        cut = (penalties["dup"] + penalties["structure"] * 0.5 +
               penalties["empty"])
    elif matched == "吸引力":
        base = _NEUTRAL_SCORE
        cut = penalties["opening"] + penalties["structure"] * 0.5
    else:  # pragma: no cover - 表外键
        base, cut = _NEUTRAL_SCORE, 0.0
    if text_len < 120:
        cut += 3.0
    return _clamp(base - cut)


def local_review(text, dims, task_type=""):
    """对 ``text`` 做确定性评审，返回与模型评审同构的 dict。

    不带 ``review_degraded``：这是一次**真实完成**的评审，只是评审者是本地
    算法。来源用 ``review_fallback="local-heuristic"`` 标注，供报告与诊断
    使用，不参与门禁拦截。
    """
    dims = list(dims or [])
    issues, penalties = _detect_issues(text, task_type)
    scores = {}
    for dim in dims:
        scores[str(dim)] = _score_for(dim, penalties, len(text or ""))
    measurable = [str(d) for d in dims
                  if any(h in str(d) for _k, hints in _MEASURABLE_HINTS
                         for h in hints)]
    if measurable:
        summary = ("本地启发式评审（无外部模型可用）：已机测 %s，"
                   "其余维度机测无从判断，按中性分 %s 计。"
                   % ("、".join(measurable), _NEUTRAL_SCORE))
    else:
        summary = ("本地启发式评审（无外部模型可用）：评分维度均无机测手段，"
                   "全部按中性分 %s 计，请人工复核。" % _NEUTRAL_SCORE)
    if issues:
        summary += "发现 %d 处可检测问题。" % len(issues)
    return {
        "scores": scores,
        "issues": [{"severity": "medium", "detail": i} for i in issues],
        "summary": summary,
        "review_fallback": HEURISTIC,
        "review_fallback_reason": "外部模型全部不可用（欠费/额度上限/无可用 CLI）",
        "heuristic_issues": issues,
    }


# ------------------------------------------------------------------ 本地起草

_DOC_SKELETON = """# __TITLE__

> 本文档由本地模板引擎生成（外部模型全部不可用）。它是可编辑的执行起点，
> 不是成品：事实性内容需要人工补齐，带「待补充」的段落就是待办。

## 一、目标

__GOAL__

## 二、背景与约束

- 任务类型：__TYPE__
- 生成时间：__TS__
- 生成方式：本地模板（未调用外部模型）

## 三、执行计划

1. 明确交付物的验收标准（谁来看、看到什么算合格）。
2. 按目标拆出 3-5 个可独立验证的小步骤，逐个落地。
3. 每步完成后对照验收标准自检，再进入下一步。
4. 全部步骤完成后做一次整体通读，修掉前后矛盾与重复表述。

## 四、交付物清单

- [ ] 主交付物（待补充）
- [ ] 必要的说明/使用文档（待补充）

## 五、待补充事项

- 与「__GOAL__」直接相关的具体内容，需要人工或恢复可用的模型后补齐。
"""


def _slug(text, limit=40):
    text = re.sub(r"\s+", " ", str(text or "")).strip()
    return text[:limit] or "未命名任务"


def local_draft(task, workdir, ms_name="manuscript.md"):
    """本地模板起草：写一份结构化执行计划到 ``ms_name``。

    返回 ``(ok, text, note)``。只在外部模型全部不可用时调用——它的产物是
    起点而不是成品，但保证流程有东西可评、有东西可交，任务不停摆。
    """
    del workdir  # 内容由调用方落盘，这里只负责生成
    task = task or {}
    body = _DOC_SKELETON.replace("__TITLE__", _slug(task.get("title"), 60))
    body = body.replace("__GOAL__", _slug(task.get("goal"), 400))
    body = body.replace("__TYPE__", str(task.get("type") or "通用"))
    body = body.replace("__TS__", time.strftime("%Y-%m-%d %H:%M:%S"))
    return True, body, ("本地模板起草：外部模型全部不可用，已生成结构化执行"
                        "计划作为起点（内容待人工补齐）")


def write_local_draft(task, workdir, ms_name="manuscript.md"):
    """落盘版 :func:`local_draft`，返回 ``(ok, text, note)``。"""
    ok, body, note = local_draft(task, workdir, ms_name)
    if not ok:
        return False, "", note
    try:
        path = os.path.join(str(workdir), ms_name)
        os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
        with open(path, "w", encoding="utf-8") as fh:
            fh.write(body)
    except OSError as exc:
        return False, "", "本地模板落盘失败：%s" % exc
    return True, body, note


# ------------------------------------------------------------------ 本地修订

def local_revise(text, issues=None):
    """对机测能修的问题做确定性修订，返回 ``(new_text, changes)``。

    只修**确定是问题**的：完全相同的重复段（保留首次出现）、超长段落（按句号
    边界拆段）、连续空段。语义级问题（人物动机、节奏）不碰——那需要人。
    """
    changes = []
    body = text or ""
    if not body.strip():
        return body, changes

    # 1) 完全相同的重复段：保留首次出现
    paras = re.split(r"(\n\s*\n)", body)
    seen = {}
    out = []
    dropped = 0
    for chunk in paras:
        if not chunk.strip() or not re.match(r"^\n\s*\n$", chunk or ""):
            if chunk.strip():
                key = re.sub(r"\s+", "", chunk)
                if key in seen and len(key) >= 20:
                    dropped += 1
                    continue
                seen[key] = True
            out.append(chunk)
    if dropped:
        body = "".join(out)
        changes.append("删除 %d 个完全重复的段落（保留首次出现）" % dropped)

    # 2) 超长段落按句号边界拆开
    paras = [p for p in re.split(r"\n\s*\n", body) if p.strip()]
    rebuilt = []
    split_count = 0
    for para in paras:
        if len(para) > 600:
            sentences = re.split(r"(?<=[。！？])", para)
            sentences = [x for x in sentences if x.strip()]
            buf, size = [], 0
            pieces = []
            for sent in sentences:
                buf.append(sent)
                size += len(sent)
                if size >= 300:
                    pieces.append("".join(buf))
                    buf, size = [], 0
            if buf:
                pieces.append("".join(buf))
            if len(pieces) > 1:
                rebuilt.extend(pieces)
                split_count += 1
                continue
            # 没有句号边界的超长段（例如整段列举）：按长度硬切，读者视角
            # 只是分段，不丢内容。
            if len(para) > 600:
                hard = [para[i:i + 300] for i in range(0, len(para), 300)]
                rebuilt.extend(hard)
                split_count += 1
                continue
        rebuilt.append(para)
    if split_count:
        body = "\n\n".join(rebuilt)
        changes.append("拆分 %d 个超长段落（>600 字）" % split_count)

    # 3) 收敛多余空行
    squeezed = re.sub(r"\n{3,}", "\n\n", body).strip() + "\n"
    if squeezed != body:
        body = squeezed
        changes.append("归一空行")
    return body, changes


def write_local_revise(workdir, ms_name, text=None):
    """落盘版 :func:`local_revise`，返回 ``(ok, new_text, changes, note)``。"""
    if text is None:
        text = _read(workdir, ms_name)
    new_text, changes = local_revise(text)
    if not changes:
        return True, text, [], "本地修订：未发现可确定性修复的问题"
    try:
        path = os.path.join(str(workdir), str(ms_name))
        os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
        with open(path, "w", encoding="utf-8") as fh:
            fh.write(new_text)
    except OSError as exc:
        return False, text, [], "本地修订落盘失败：%s" % exc
    return True, new_text, changes, "本地修订：%s" % "；".join(changes)


def _read(workdir, ms_name):
    try:
        path = os.path.join(str(workdir), str(ms_name))
        if not os.path.isfile(path):
            return ""
        with open(path, "r", encoding="utf-8", errors="replace") as fh:
            return fh.read()
    except OSError:
        return ""
