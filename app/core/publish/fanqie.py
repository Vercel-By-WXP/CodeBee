# -*- coding: utf-8 -*-
"""番茄作家后台的发布流程定义（默认表：选择器为合理推测，待实测校准）。

校准方式（不改代码）：把真实步骤写进 data/publish/flows-fanqie.json：
  {"create_book": [ {"do":"navigate","url":"..."}, {"do":"fill","sel":"...","key":"title"} ... ],
   "upload_chapter": [ ... ]}
存在即整体覆盖本文件的 FLOWS；用「探测表单」按钮（probe 流程）dump 出
页面真实元素清单后照着写即可。每次失败的截图也会指出卡在哪一步。

URL 依据：tools/fanqie_bookmeta.json 的 source 记录了 2026-09-17 实抓
fanqienovel.com/main/writer/create 建书弹层；入口走作家后台首页点
「创建作品」，不硬编码深链（深链随改版漂移，入口按钮最稳）。
"""
from __future__ import annotations

CONFIG = {
    "id": "fanqie",
    "label": "番茄",
    "home": "https://writer.fanqienovel.com/",
    # 导航后 URL 含任一标记 → 未登录（跳到了登录/通行证页）
    "login_url_marks": ["login", "passport", "sso", "account/signin"],
}

# 建书：入口 → 弹层/页面 → 文本字段直填；分类/签约模式/标签走文本点击。
# 文本输入类的选择器优先用 placeholder 模糊匹配（改版后 id/class 易变，
# 「书名」「简介」这类 placeholder 语料最稳定）。
CREATE_BOOK = [
    {"do": "navigate", "url": "{home}"},
    {"do": "url_any", "any": ["writer.fanqienovel.com"]},
    {"do": "click_text", "text": "创建作品", "contains": True, "scope": "button,a,[role=button],span"},
    {"do": "probe", "note": "建书表单"},
    {"do": "wait", "sel": "input[placeholder*='书名'],input[placeholder*='作品名'],input[maxlength]", "timeout": 10},
    {"do": "fill", "sel": "input[placeholder*='书名'],input[placeholder*='作品名']", "key": "title"},
    {"do": "fill", "sel": "textarea[placeholder*='简介'],textarea", "key": "summary"},
    {"do": "fill", "sel": "input[placeholder*='主角']", "key": "protagonist"},
    {"do": "click_text", "text": "{signing_mode}", "contains": False,
     "scope": "[class*=mode] label,label,span,div"},
    {"do": "click_text", "text": "{category}", "contains": False,
     "scope": "[class*=categor] li,span,div"},
    {"do": "shot", "name": "create-book-filled"},
    {"do": "submit", "sel": "button[class*=submit],button[class*=primary]"},
]

# 发章：后台首页 → 按书名点进作品 → 新建章节 → 填标题与正文。
# book_name 由 manager 从作品登记（books.json）带入 values，click_text 复用。
UPLOAD_CHAPTER = [
    {"do": "navigate", "url": "{home}"},
    {"do": "url_any", "any": ["writer.fanqienovel.com"]},
    {"do": "click_text", "text": "{book_name}", "contains": True,
     "scope": "a,span,div[class*=title],div[class*=book]"},
    {"do": "click_text", "text": "新建章节", "contains": True, "scope": "button,a,[role=button],span"},
    {"do": "probe", "note": "章节编辑器"},
    {"do": "fill", "sel": "input[placeholder*='章节'],input[placeholder*='标题']", "key": "chapter_title"},
    {"do": "wait", "sel": "[contenteditable=true],textarea[class*=content],div[class*=editor]", "timeout": 10},
    {"do": "fill", "sel": "[contenteditable=true],textarea[class*=content]", "key": "chapter_body"},
    {"do": "shot", "name": "chapter-filled"},
    {"do": "submit", "sel": "button[class*=publish],button[class*=submit]"},
]

# 登录态探测：打开后台首页，URL 被踢到登录页 → 未登录
CHECK_LOGIN = [
    {"do": "navigate", "url": "{home}"},
    {"do": "url_any", "any": ["writer.fanqienovel.com"]},
]

# 表单探测（校准辅助）：开建书入口后 dump 全部可交互元素
PROBE_FORM = [
    {"do": "navigate", "url": "{home}"},
    {"do": "url_any", "any": ["writer.fanqienovel.com"]},
    {"do": "click_text", "text": "创建作品", "contains": True, "scope": "button,a,[role=button],span"},
    {"do": "probe", "note": "建书表单"},
]

FLOWS = {"create_book": CREATE_BOOK, "upload_chapter": UPLOAD_CHAPTER,
         "check_login": CHECK_LOGIN, "probe_form": PROBE_FORM}


def values_create_book(meta):
    """bookmeta 字段 → 建书表单值。标签/分类逐项点击由流程按 values.tags 展开
    （manager 组装时把 tags 列表拍平成 click_text 步骤追加）。"""
    return {
        "title": (meta.get("book_name") or "").strip(),
        "summary": (meta.get("summary") or "").strip(),
        "protagonist": (meta.get("protagonist_1") or "").strip(),
        "signing_mode": (meta.get("signing_mode") or "连载模式").strip(),
        "category": (meta.get("category") or "").strip(),
    }


def tag_groups(meta):
    """标签字段名 → 值列表（manager 用来生成逐个 click_text 步骤）。"""
    out = []
    for key in ("tags_theme", "tags_role", "tags_plot",
                "content_plot", "content_emotion", "content_character", "content_world"):
        v = meta.get(key)
        if isinstance(v, list) and v:
            out.append((key, [str(x) for x in v]))
    return out
