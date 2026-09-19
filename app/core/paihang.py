# -*- coding: utf-8 -*-
"""扫榜选材：抓取七猫排行榜公开页，产出选题分析素材。

数据源：www.qimao.com/paihang/（公开可抓，2026-09-19 实测 200/106KB，
书名与分类在 a 标签中文文本里）。下载走 curl 子进程（与 covergen 同款，
Python 不经手响应体以外的东西），解析用宽松正则——页面结构变了宁可
返回空（调用方回落普通直连提示词），不做脆弱的强解析。
"""
from __future__ import annotations

import logging
import re

log = logging.getLogger(__name__)

RANK_URL = "https://www.qimao.com/paihang/"
_UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
       "(KHTML, like Gecko) Chrome/126 Safari/537.36")

# 页面 UI 噪音词（非书目/分类）
_NOISE = {"加入书架", "立即阅读", "开始阅读", "免费阅读", "全部", "分类", "排行",
          "排行榜", "完本", "连载中", "阅读", "书城", "男生", "女生"}


def fetch_rank_items(limit=40):
    """抓七猫排行榜页并提取中文元素材（书名/分类混排，去 UI 噪音）。

    返回元素列表（保序去重，最多 limit 个）；抓取失败返回 []——调用方
    回落普通直连提示词，扫榜永远不挡任务。"""
    from . import runner
    try:
        r = runner.run_process(
            argv=["curl", "-sS", "--max-time", "20", "-A", _UA, RANK_URL],
            timeout=30)
    except Exception:
        return []
    if not r.get("ok"):
        return []
    items, seen = [], set()
    for t in re.findall(r">([\u4e00-\u9fa5]{2,12})<", r.get("stdout") or ""):
        if t in _NOISE or t in seen:
            continue
        seen.add(t)
        items.append(t)
        if len(items) >= limit:
            break
    return items


def rank_scan_prompt(goal):
    """组装扫榜选材分析提示词。榜单抓取失败返回 None（调用方回落直连提示词）。"""
    items = fetch_rank_items()
    if not items:
        return None
    material = "、".join(items)
    return (
        "你是网文选题分析师。以下是刚刚抓取的七猫排行榜页面的书目与分类素材：\n\n"
        "## 榜单元素材\n%s\n\n"
        "## 用户想写的方向\n%s\n\n"
        "## 你的产出（Markdown 报告）\n"
        "1. **热门题材 Top3**：各自的共同特征与上榜代表书目\n"
        "2. **高频人设/套路总结**：3-5 条，点名反复出现的元素\n"
        "3. **差异化切入建议**：2-3 个，结合用户方向给出「题材+人设」组合与一句话理由\n"
        "直接输出分析报告，不要复述素材清单，不要输出与报告无关的内容。"
        % (material, goal or "（用户未指定方向，按大盘热门分析）"))
