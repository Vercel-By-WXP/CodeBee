# -*- coding: utf-8 -*-
"""AI 味确定性检测（借鉴 oh-story-claudecode 的去AI味思路）。

模型评审是主观分，这里补一道客观参考线：统计译制腔/套话高频词的
密度，产出报告行注入评审提示词，让评审官把「套话密度」纳入评分
参考。只做参考不做门禁——「仿佛」「缓缓」在正常小说里也常见，
误伤风险由评审官（能读懂上下文）兜底，脚本只负责指认位置与密度。
"""
from __future__ import annotations

# 译制腔/套话词表（按误导性排序，持续运维）
AI_PHRASES = (
    "空气仿佛凝固", "眸中闪过一丝", "嘴角勾起一抹", "心中一动",
    "值得注意的是", "总而言之", "综上所述", "不难发现",
    "在这一刻", "仿佛在诉说着", "无声地诉说着", "见证着",
    "深深地看了一眼", "似乎在", "空气仿佛", "一抹微笑",
    "不禁", "仿佛",
)

# 密度告警线（每千字命中次数）：超过即提示评审官重点关注
ALERT_PER_KILO = 8.0


def analyze(text):
    """统计套话命中。返回 {hits: {短语: 次数}, per_kilo: 每千字密度, alert: bool}。"""
    text = text or ""
    total = len(text)
    hits = {}
    for p in AI_PHRASES:
        p = p.strip()
        if not p:
            continue
        n = text.count(p)
        if n:
            hits[p] = n
    n_hits = sum(hits.values())
    per_kilo = round(n_hits * 1000.0 / total, 2) if total else 0.0
    return {"hits": hits, "per_kilo": per_kilo, "alert": per_kilo >= ALERT_PER_KILO}


def report_line(text):
    """生成注入评审提示词的一行报告；无命中返回空串。"""
    r = analyze(text)
    if not r["hits"]:
        return ""
    top = "、".join("「%s」×%d" % (k, v)
                    for k, v in sorted(r["hits"].items(), key=lambda x: -x[1])[:8])
    line = "- [AI味检测] 确定性统计：套话密度 %.1f/千字（%s）。" % (r["per_kilo"], top)
    if r["alert"]:
        line += "密度超过告警线 %.0f/千字，请重点评审译制腔与套话问题。" % ALERT_PER_KILO
    return line
