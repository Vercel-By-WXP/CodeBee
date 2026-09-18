# -*- coding: utf-8 -*-
"""七猫作者后台的发布流程定义（默认表：选择器为合理推测，待实测校准）。

校准方式同番茄：data/publish/flows-qimao.json 覆盖 + 「探测表单」dump。
URL 依据：tools/qimao_bookmeta.json 的 source 记录 2026-09-17 登录态实抓
zuozhe.qimao.com/api/pc/v1/book/book-option——作者后台 PC 端在 zuozhe.qimao.com。

七猫建书表单结构（同次实抓）：频道级联（男/女生 → 一级 → 二级）+ 四组标签
（风格/角色/情节/背景，每组 1-3 个）+ 文本字段。
"""
from __future__ import annotations

CONFIG = {
    "id": "qimao",
    "label": "七猫",
    "home": "https://zuozhe.qimao.com/",
    # 作品管理页：建书入口（「新建小说」按钮所在）；建书向导第二步表单在其下
    "book_manage": "https://zuozhe.qimao.com/front/book-manage",
    "login_url_marks": ["login", "signin", "passport", "sso"],
}

CREATE_BOOK = [
    {"do": "navigate", "url": "{home}"},
    {"do": "url_any", "any": ["qimao.com"]},
    {"do": "click_text", "text": "创建作品", "contains": True, "scope": "button,a,[role=button],span"},
    {"do": "probe", "note": "建书表单"},
    {"do": "wait", "sel": "input[placeholder*='作品'],input[placeholder*='书名'],input[maxlength]", "timeout": 10},
    {"do": "fill", "sel": "input[placeholder*='作品'],input[placeholder*='书名']", "key": "title"},
    {"do": "fill", "sel": "textarea[placeholder*='简介'],textarea", "key": "summary"},
    {"do": "fill", "sel": "input[placeholder*='主角']", "key": "protagonist"},
    # 频道级联：目标读者 → 一级分类 → 二级分类（值来自 bookmeta 的级联字段）
    {"do": "click_text", "text": "{target_reader}", "contains": False,
     "scope": "[class*=channel] label,label,span"},
    {"do": "click_text", "text": "{category_main}", "contains": False,
     "scope": "[class*=categor] li,option,span"},
    {"do": "click_text", "text": "{category_sub}", "contains": False,
     "scope": "[class*=categor] li,option,span"},
    {"do": "shot", "name": "create-book-filled"},
    {"do": "submit", "sel": "button[class*=submit],button[class*=primary],button[class*=confirm]"},
]

UPLOAD_CHAPTER = [
    {"do": "navigate", "url": "{home}"},
    {"do": "url_any", "any": ["qimao.com"]},
    {"do": "click_text", "text": "{book_name}", "contains": True,
     "scope": "a,span,div[class*=title],div[class*=book]"},
    {"do": "click_text", "text": "新建章节", "contains": True, "scope": "button,a,[role=button],span"},
    {"do": "probe", "note": "章节编辑器"},
    {"do": "fill", "sel": "input[placeholder*='章节'],input[placeholder*='标题'],input[placeholder*='章名']", "key": "chapter_title"},
    {"do": "wait", "sel": "[contenteditable=true],textarea[class*=content],iframe", "timeout": 10},
    {"do": "fill", "sel": "[contenteditable=true],textarea[class*=content]", "key": "chapter_body"},
    {"do": "shot", "name": "chapter-filled"},
    {"do": "submit", "sel": "button[class*=publish],button[class*=submit]"},
]

CHECK_LOGIN = [
    {"do": "navigate", "url": "{home}"},
    {"do": "url_any", "any": ["qimao.com"]},
]

PROBE_FORM = [
    {"do": "navigate", "url": "{home}"},
    {"do": "url_any", "any": ["qimao.com"]},
    {"do": "click_text", "text": "创建作品", "contains": True, "scope": "button,a,[role=button],span"},
    {"do": "probe", "note": "建书表单"},
]

FLOWS = {"create_book": CREATE_BOOK, "upload_chapter": UPLOAD_CHAPTER,
         "check_login": CHECK_LOGIN, "probe_form": PROBE_FORM}


# 标签字段名 → 弹层左侧组显示名（tags 步骤按组切换后点选；每组必选 1-3 个）
TAG_GROUP_LABELS = {"tags_style": "风格", "tags_role": "角色",
                    "tags_plot": "情节", "tags_bg": "背景"}


def values_create_book(meta):
    return {
        "title": (meta.get("book_name") or "").strip(),
        "summary": (meta.get("summary") or "").strip(),
        "protagonist": (meta.get("protagonist_1") or "").strip(),
        "target_reader": (meta.get("target_reader") or "").strip(),
        "category_main": (meta.get("category_main") or "").strip(),
        "category_sub": (meta.get("category_sub") or "").strip(),
        "status": (meta.get("status") or "连载中").strip(),
    }


def tag_groups(meta):
    out = []
    for key in ("tags_style", "tags_role", "tags_plot", "tags_bg"):
        v = meta.get(key)
        if isinstance(v, list) and v:
            out.append((key, [str(x) for x in v]))
    return out
