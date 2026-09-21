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

# 叙事架构层信号（借鉴 sepia 2.7k★/StoryScope 研究 2026：AI 小说 93.2% 靠
# 叙事架构特征检出，人工改写措辞后检出率仅从 95.5% 降到 93.9%——措辞层
# 改不掉的架构级指纹才是真破绽）。三类可确定性检测的架构信号：
NARRATIVE_TELLS = {
    "顿悟说教": ("终于明白", "这才明白", "明白了，", "意识到，自己", "懂得了",
               "原来，成长", "原来，生活", "原来，所谓"),
    "情绪身体化": ("心脏猛地", "指尖冰凉", "指尖发凉", "喉咙发紧", "喉头发紧",
                "胃里一阵", "胃部一阵", "后背一凉", "血液仿佛", "呼吸一滞"),
    "成长式收束": ("释然", "和解", "放下了", "接纳了", "与自己和解", "轻轻松了口气",
                "内心归于平静"),
}
# 架构信号告警线比措辞层低：这些表达在好小说里本就该稀缺
NARRATIVE_ALERT_PER_KILO = 2.0
# 「成长式收束」只在结尾才构成架构指纹（中段出现多半是剧情词），只扫尾部
ENDING_SCAN_CHARS = 600


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


def narrative_analyze(text):
    """统计叙事架构层信号。返回 {cats: {类: 次数}, per_kilo, alert, ending_hits}。

    - 前两类全篇统计；「成长式收束」只统计末尾 ENDING_SCAN_CHARS 字
      （中段的「和解/放下」是剧情词，结尾的才是成长式收束指纹）。
    - per_kilo 为三类合计密度；alert 判据：合计 ≥ 告警线 或 收束类命中 ≥ 2。
    """
    text = text or ""
    total = len(text)
    cats, ending_hits = {}, 0
    if total:
        tail = text[-ENDING_SCAN_CHARS:]
        for cat, phrases in NARRATIVE_TELLS.items():
            n = 0
            for p in phrases:
                if cat == "成长式收束":
                    n += tail.count(p)
                else:
                    n += text.count(p)
            if n:
                cats[cat] = n
        ending_hits = cats.get("成长式收束", 0)
    per_kilo = round(sum(cats.values()) * 1000.0 / total, 2) if (total and cats) else 0.0
    alert = bool(cats) and (per_kilo >= NARRATIVE_ALERT_PER_KILO or ending_hits >= 2)
    return {"cats": cats, "per_kilo": per_kilo, "alert": alert, "ending_hits": ending_hits}


def report_line(text):
    """生成注入评审提示词的报告行（措辞层 + 叙事架构层）；全部无命中返回空串。"""
    lines = []
    r = analyze(text)
    if r["hits"]:
        top = "、".join("「%s」×%d" % (k, v)
                        for k, v in sorted(r["hits"].items(), key=lambda x: -x[1])[:8])
        line = "- [AI味检测] 确定性统计：套话密度 %.1f/千字（%s）。" % (r["per_kilo"], top)
        if r["alert"]:
            line += "密度超过告警线 %.0f/千字，请重点评审译制腔与套话问题。" % ALERT_PER_KILO
        lines.append(line)
    nr = narrative_analyze(text)
    if nr["cats"]:
        top = "、".join("「%s」×%d" % (k, v) for k, v in nr["cats"].items())
        line = ("- [叙事架构信号] 确定性统计（措辞改写不掉的架构级指纹）："
                "%s，合计 %.1f/千字。" % (top, nr["per_kilo"]))
        if nr["alert"]:
            line += ("出现架构级 AI 指纹（顿悟说教/情绪只写身体反应/成长式收束），"
                     "请评审情节结构：主题是否被叙述者直接说破、情绪是否只有身体描写、"
                     "结尾是否靠主角想通收束。")
        lines.append(line)
    return "\n".join(lines)
