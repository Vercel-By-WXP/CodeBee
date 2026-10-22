# -*- coding: utf-8 -*-
"""模型输出解析：评审 JSON 的宽容提取与散文字段兜底。

从 runner 拆出（单文件行数治理），runner 原样再导出这些名字：
  - extract_json：严格 JSON → ```json 围栏 → 平衡花括号扫描；
  - _loads_lenient：修复字符串内未转义引号后重试；
  - as_scores / extract_scores_from_text / scores_from_prose：评审三道兜底；
  - verdict_brief / verdict_digest：评审裁定 JSON → 人读摘要（步骤
    summary 单行版 / 对话时间线多行版），非裁定原文返回 None。
"""
from __future__ import annotations

import json
import re


def _loads_lenient(text):
    """容错 json.loads：修复字符串内未转义引号后再解析。

    模型高频病：评审 note 里带英文直引号（"第5章"冷战三天"使用…"），
    严格 JSON 被打碎。判据：字符串内遇到 `"` 时向后看第一个非空白字符，
    是 , } ] : 视为结构性收尾引号，否则按字面引号转义。"""
    out = []
    i, n = 0, len(text)
    in_str = False
    while i < n:
        c = text[i]
        if in_str:
            if c == "\\" and i + 1 < n:
                out.append(text[i:i + 2])
                i += 2
                continue
            if c == '"':
                j = i + 1
                while j < n and text[j] in " \t\r\n":
                    j += 1
                nxt = text[j] if j < n else ""
                if nxt in ",}]:":
                    in_str = False
                    out.append(c)
                else:
                    out.append('\\"')
                i += 1
                continue
            out.append(c)
            i += 1
            continue
        if c == '"':
            in_str = True
        out.append(c)
        i += 1
    return json.loads("".join(out))


def extract_json(text):
    """从模型回复中提取 JSON：直接解析 → ```json 围栏 → 平衡花括号扫描。"""
    if not text:
        return None
    text = text.strip()
    try:
        return json.loads(text)
    except Exception:
        pass
    m = re.search(r"```(?:json)?\s*(\{.*?\})\s*```", text, re.S)
    if m:
        try:
            return json.loads(m.group(1))
        except Exception:
            pass
        try:
            return _loads_lenient(m.group(1))
        except Exception:
            pass
    start = text.find("{")
    while start != -1:
        depth = 0
        for i in range(start, len(text)):
            c = text[i]
            if c == "{":
                depth += 1
            elif c == "}":
                depth -= 1
                if depth == 0:
                    try:
                        return json.loads(text[start:i + 1])
                    except Exception:
                        break
        start = text.find("{", start + 1)
    return None


def as_scores(gj):
    """评审解析第二道网：花括号兜底扫描会掉进第一个可平衡的子对象——
    外层 JSON 病得重时返回的是「维度→分数」本体（无 scores 键），这里包回
    评审形状，否则良评审被误判成「输出不可解析」（2026-09-18 七猫案：
    kimi 正常出分却因内嵌引号整轮判评审全挂，连环白烧自动续跑）。"""
    if isinstance(gj, dict) and not isinstance(gj.get("scores"), dict):
        vals = {k: v for k, v in gj.items()
                if isinstance(v, (int, float)) and not isinstance(v, bool)}
        if vals and len(vals) == len(gj) and len(vals) >= 3:
            return {"scores": vals, "issues": [], "summary": ""}
    return gj if isinstance(gj, dict) else None


def extract_scores_from_text(text):
    """评审解析第四道网（借鉴 BAML 的宽容提取）：模型把分数写成散文键值对
    完全不出 JSON 时（2026-09-18 真实案例：kimi 正文提分），从文本直接抓
    「维度：N 分」模式。返回 scores dict 或 {}。
    2026-09-25 批1 补：分隔符不止冒号——短横线「正确性 - 9 分」、全角等号
    「＝」、箭头「→」三种真实输出形态此前全漏，格式错被误判质量差白烧修复轮。"""
    if not text:
        return {}
    scores = {}
    for m in re.finditer(
            r"([\u4e00-\u9fa5A-Za-z][\u4e00-\u9fa5A-Za-z0-9 ]{0,11})"
            r"\s*[:：\-＝=→]\s*(\d{1,2}(?:\.\d)?)\s*分?", text):
        dim = m.group(1).strip()
        try:
            val = float(m.group(2))
        except ValueError:
            continue
        if not dim or dim in scores or not (1.0 <= val <= 10.0):
            continue
        scores[dim] = round(val, 1)
    return scores if len(scores) >= 3 else {}


def scores_from_prose(text, dims):
    """评审解析第三道网：agentic CLI 有时把 JSON 写进文件、stdout 只留中文
    总结（「情节 8 / 人物 8 / …」）。JSON 全灭后按维度名从正文提分；
    至少命中 3 个维度才认（防普通行文里的巧合数字）。"""
    if not text or not dims:
        return {}
    found = {}
    for d in dims:
        m = re.search(re.escape(d) + r"\s*[:：/是]?\s*([0-9]+(?:\.[0-9]+)?)", text)
        if m:
            try:
                found[d] = float(m.group(1))
            except (TypeError, ValueError):
                pass
    return found if len(found) >= 3 else {}


def _verdict_parts(text):
    """评审裁定识别：文本宽容解析出「带全数值 scores 的 JSON 对象」时返回
    (对象, 归一分数表)，其余返回 None。判定刻意收紧——scores 缺失或混入
    非数值一律不算裁定：直连回答、配置 JSON、普通引用到 "scores" 的文本
    都不能误伤成「已摘要」。"""
    if not text or (isinstance(text, str) and "scores" not in text):
        return None
    cj = text if isinstance(text, dict) else extract_json(text)
    if not isinstance(cj, dict):
        return None
    raw = cj.get("scores")
    if not isinstance(raw, dict) or not raw:
        return None
    scores = {}
    for k, v in raw.items():
        if isinstance(v, bool):   # true/false 不是分数（与 as_scores 同口径）
            return None
        try:
            fv = float(v)
        except (TypeError, ValueError):
            return None
        scores[str(k)] = fv
    return (cj, scores) if scores else None


def _verdict_issues(cj):
    """裁定里的意见列表：content 流叫 issues、code 流叫 findings，通吃。"""
    for key in ("issues", "findings"):
        v = cj.get(key)
        if isinstance(v, list) and v:
            return [x for x in v if isinstance(x, dict)]
    return []


def _issue_line(it, cap=110):
    """单条意见压成一行：[severity] 维度/文件：要点。"""
    dim = str(it.get("dim") or it.get("path") or "").strip()
    sev = str(it.get("severity") or "").strip().lower()
    note = " ".join(str(it.get("note") or it.get("header") or it.get("detail") or "").split())
    if len(note) > cap:
        note = note[:cap] + "…"
    head = "[%s] " % sev if sev else ""
    if dim and note:
        return "%s%s：%s" % (head, dim, note)
    return head + (dim or note)


def verdict_brief(text, limit=260):
    """评审裁定 JSON → 单行短摘要（步骤 summary 用）；非裁定原文返回 None。

    评审官的 ```json 裁定原文曾整段截 600 字塞进 summary，检查器/蜂巢/
    详情一行根本放不下（2026-10-22 用户反馈「一大堆根本没人看」）。与
    mock 步骤「均分 X：总结」同语系；意见只留计数与首条，细节看日志。"""
    parts = _verdict_parts(text)
    if not parts:
        return None
    cj, scores = parts
    avg = sum(scores.values()) / len(scores)
    dims = "·".join("%s %.1f" % (k, v) for k, v in
                    list(scores.items())[:5])
    out = "均分 %.1f（%s）" % (avg, dims)
    if isinstance(cj.get("pass"), bool):
        out = ("评审通过，" if cj["pass"] else "评审未通过，") + out
    concl = " ".join(str(cj.get("summary") or "").split())
    if concl:
        out += "：%s" % concl[:80]
    issues = _verdict_issues(cj)
    if issues:
        n_major = sum(1 for it in issues if str(it.get("severity") or "")
                      .lower() in ("blocker", "critical", "major"))
        out += "；意见 %d 条%s" % (len(issues),
                                   ("（含 major %d）" % n_major) if n_major else "")
        first = _issue_line(issues[0], cap=70)
        if first:
            out += "：%s" % first
    return out[:limit]


def verdict_digest(text, limit=1000):
    """评审裁定 JSON → 多行人读摘要（对话时间线气泡用）；非裁定返回 None。

    分数一行带全维度、结论一行、意见逐条（封顶 6 条、单条截断）——保住
    评审里有用的血肉，砍掉 JSON 语法噪音；完整原文永远在该步日志里。"""
    parts = _verdict_parts(text)
    if not parts:
        return None
    cj, scores = parts
    head = "评审均分 %.1f" % (sum(scores.values()) / len(scores))
    if isinstance(cj.get("pass"), bool):
        head = ("评审通过，" if cj["pass"] else "评审未通过，") + head
    lines = [head + "（" + " · ".join("%s %.1f" % (k, v)
                                     for k, v in scores.items()) + "）"]
    concl = " ".join(str(cj.get("summary") or "").split())
    if concl:
        lines.append("结论：" + concl[:200])
    issues = _verdict_issues(cj)
    if issues:
        lines.append("意见 %d 条：" % len(issues))
        for it in issues[:6]:
            ln = _issue_line(it)
            if ln:
                lines.append("· " + ln)
        if len(issues) > 6:
            lines.append("· …其余 %d 条见该步日志" % (len(issues) - 6))
    return "\n".join(lines)[:limit]
