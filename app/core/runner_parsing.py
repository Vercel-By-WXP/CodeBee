# -*- coding: utf-8 -*-
"""模型输出解析：评审 JSON 的宽容提取与散文字段兜底。

从 runner 拆出（单文件行数治理），runner 原样再导出这些名字：
  - extract_json：严格 JSON → ```json 围栏 → 平衡花括号扫描；
  - _loads_lenient：修复字符串内未转义引号后重试；
  - as_scores / extract_scores_from_text / scores_from_prose：评审三道兜底。
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
