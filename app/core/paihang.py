# -*- coding: utf-8 -*-
"""扫榜选材：抓取公开排行榜页，产出选题分析素材。

数据源（四源聚合，任一失败不影响其余源；2026-09-24 实测全 200）：
- 七猫：www.qimao.com/paihang/（106KB）
- 番茄 Web 版：fanqienovel.com/rank（题材分类+书名在中文 a 标签里；
  App 端接口有 SecuritySign/X-Argus 签名——那才是难路，Web 版榜单页
  不需要签名，别走弯路）
- 起点移动版：m.qidian.com/rank（106KB，直接出书名；主站 www.qidian.com
  被 WAF 拦 202/209B，移动页不需要签名）
- 纵横：www.zongheng.com/rank（65KB，书名+统计标签混排，靠噪音词过滤）

下载走 curl 子进程（与 covergen 同款），解析用宽松正则——页面结构变了
宁可返回空（调用方回落普通直连提示词），不做脆弱的强解析。
"""
from __future__ import annotations

import logging
import re

log = logging.getLogger(__name__)

QIMAO_RANK_URL = "https://www.qimao.com/paihang/"
FANQIE_RANK_URL = "https://fanqienovel.com/rank"
QIDIAN_M_RANK_URL = "https://m.qidian.com/rank/"
ZONGHENG_RANK_URL = "https://www.zongheng.com/rank/"
_UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
       "(KHTML, like Gecko) Chrome/126 Safari/537.36")

# 页面 UI 噪音词（非书目/分类）：页面 chrome、导航、tab 名、登录/搜索、
# 榜单 tab 与统计标签（起点移动版 tab 名、纵横月票/人气/点击等）
_NOISE = {"加入书架", "立即阅读", "开始阅读", "免费阅读", "全部", "分类", "排行",
          "排行榜", "完本", "连载中", "阅读", "书城", "男生", "女生",
          "帮助中心", "作家助手", "客服", "登录", "注册", "立即登录",
          "搜索", "热门作品排行", "大热榜", "新书榜", "完结榜", "收藏榜",
          "更新榜", "日榜", "月榜", "蝉联", "榜首", "热度", "免费", "畅销",
          "下载", "客户端", "首页", "我的", "设置", "更多", "返回", "关于",
          "月票榜", "阅读榜", "书友榜", "推荐榜", "签约榜", "新人榜",
          "畅销榜", "大家都在搜", "男生小说排行榜", "女生小说排行榜",
          "男频阅读榜", "女频阅读榜", "男频新书榜", "番茄小说",
          "登录后获得更多特色功能", "安装起点读书客户端", "看更多正版好书",
          "月票", "人气", "点击", "万字", "推荐", "收藏", "鲜花", "打赏", "推荐票"}

_CJK_RE = re.compile(r">([\u4e00-\u9fa5]{2,12})<")


def _curl_text(url):
    """curl 子进程抓页面文本；失败返回空串。"""
    from . import runner
    try:
        r = runner.run_process(
            argv=["curl", "-sS", "--max-time", "20", "-A", _UA, url],
            timeout=30)
    except Exception:
        return ""
    if not r.get("ok"):
        return ""
    return r.get("stdout") or ""


def _extract_cjk(html, limit):
    """从 HTML 提取中文元素材（去 UI 噪音、保序去重）。"""
    items, seen = [], set()
    for t in _CJK_RE.findall(html or ""):
        if t in _NOISE or t in seen:
            continue
        seen.add(t)
        items.append(t)
        if len(items) >= limit:
            break
    return items


def fetch_qimao_rank(limit=30):
    """七猫排行榜：返回元素列表，抓取失败返回 []。"""
    return _extract_cjk(_curl_text(QIMAO_RANK_URL), limit)


def fetch_fanqie_rank(limit=30):
    """番茄 Web 版榜单：返回元素列表，抓取失败返回 []。"""
    return _extract_cjk(_curl_text(FANQIE_RANK_URL), limit)


def fetch_qidian_rank(limit=30):
    """起点移动版榜单：返回元素列表，抓取失败返回 []。"""
    return _extract_cjk(_curl_text(QIDIAN_M_RANK_URL), limit)


def fetch_zongheng_rank(limit=30):
    """纵横排行榜：返回元素列表，抓取失败返回 []。"""
    return _extract_cjk(_curl_text(ZONGHENG_RANK_URL), limit)


# 源清单：顺序即素材拼接顺序（七猫/番茄为元老源保持在前）。
# 存函数名、调用期 globals() 解析——mock.patch 换模块属性才能生效
# （存函数引用会把定义期原函数钉死，测试会穿透打真网络）。
_SOURCES = (
    ("七猫排行榜素材", "fetch_qimao_rank"),
    ("番茄排行榜素材", "fetch_fanqie_rank"),
    ("起点排行榜素材", "fetch_qidian_rank"),
    ("纵横排行榜素材", "fetch_zongheng_rank"),
)


def fetch_rank_items(limit=40):
    """四源聚合：各抓一份（跨源去重合并，按源清单顺序）。

    全部失败才返回 []——单源挂了其余源仍可用，扫榜永不因单点挡任务。"""
    merged, seen = [], set()
    for _label, fname in _SOURCES:
        for t in globals()[fname](limit):
            if t not in seen:
                seen.add(t)
                merged.append(t)
    return merged


def rank_scan_prompt(goal):
    """组装扫榜选材分析提示词。四源全空返回 None（调用方回落直连提示词）。"""
    blocks, seen = [], set()
    for label, fname in _SOURCES:
        items = [t for t in globals()[fname](30) if t not in seen]
        seen.update(items)
        if items:
            blocks.append("### %s\n%s" % (label, "、".join(items)))
    if not blocks:
        return None
    material = "\n\n".join(blocks)
    return (
        "你是网文选题分析师。以下是刚刚抓取的各平台排行榜页面的书目与分类素材：\n\n"
        "## 榜单元素材\n%s\n\n"
        "## 用户想写的方向\n%s\n\n"
        "## 你的产出（Markdown 报告）\n"
        "1. **热门题材 Top3**：各自的共同特征与上榜代表书目（跨平台重合的题材单独点出——多平台都热说明是真风口）\n"
        "2. **高频人设/套路总结**：3-5 条，点名反复出现的元素\n"
        "3. **差异化切入建议**：2-3 个，结合用户方向给出「题材+人设」组合与一句话理由\n"
        "直接输出分析报告，不要复述素材清单，不要输出与报告无关的内容。"
        % (material, goal or "（用户未指定方向，按大盘热门分析）"))
