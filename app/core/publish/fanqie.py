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
    "home": "https://fanqienovel.com/main/writer/",   # 实测 writer. 子域不存在(DNS 000)；快照实抓为主站路径
    # 导航后 URL 含任一标记 → 未登录（跳到了登录/通行证页）
    "login_url_marks": ["login", "passport", "sso", "account/signin"],
}

# 建书：入口 → 弹层/页面 → 文本字段直填；分类/签约模式/标签走文本点击。
# 文本输入类的选择器优先用 placeholder 模糊匹配（改版后 id/class 易变，
# 「书名」「简介」这类 placeholder 语料最稳定）。
CREATE_BOOK = [
    {"do": "navigate", "url": "{home}"},
    {"do": "url_any", "any": ["fanqienovel.com"]},
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
    {"do": "url_any", "any": ["fanqienovel.com"]},
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
    {"do": "url_any", "any": ["fanqienovel.com"]},
]

# 表单探测（校准辅助）：开建书入口后 dump 全部可交互元素
PROBE_FORM = [
    {"do": "navigate", "url": "{home}"},
    {"do": "url_any", "any": ["fanqienovel.com"]},
    {"do": "click_text", "text": "创建作品", "contains": True, "scope": "button,a,[role=button],span"},
    {"do": "probe", "note": "建书表单"},
]

FLOWS = {"create_book": CREATE_BOOK, "upload_chapter": UPLOAD_CHAPTER,
         "check_login": CHECK_LOGIN, "probe_form": PROBE_FORM}


def _first(meta, key):
    v = meta.get(key)
    return str(v[0]).strip() if isinstance(v, list) and v else str(v or "").strip()


def values_create_book(meta):
    """bookmeta 字段 → 建书表单值。标签弹层的分区点选用单值字段
    （每组选 1 个最稳：番茄主题/角色/情节各≤2、内容组各有上限），
    数组全量点选由 tag_groups 路径兼容。"""
    return {
        "title": (meta.get("book_name") or "").strip(),
        "summary": (meta.get("summary") or "").strip(),
        "protagonist": (meta.get("protagonist_1") or "").strip(),
        "protagonist2": (meta.get("protagonist_2") or "").strip(),
        "signing_mode": (meta.get("signing_mode") or "连载模式").strip(),
        "category": (meta.get("category") or "").strip(),
        "target_reader": (meta.get("target_reader") or "男频").strip(),
        "tag_theme": _first(meta, "tags_theme"),
        "tag_role": _first(meta, "tags_role"),
        "tag_plot": _first(meta, "tags_plot"),
        "tag_content_emotion": _first(meta, "content_emotion"),
        "tag_content_character": _first(meta, "content_character"),
        "tag_content_world": _first(meta, "content_world"),
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


def editor_url(book):
    """章节编辑器直达（真机实测）：/main/writer/<book_id>/publish/。"""
    bid = str((book or {}).get("book_id") or "")
    if bid:
        return "https://fanqienovel.com/main/writer/%s/publish/" % bid
    return CONFIG["home"]


def chapter_manage_url(book):
    """章节管理页（上线验证用）。"""
    bid = str((book or {}).get("book_id") or "")
    if bid:
        return "https://fanqienovel.com/main/writer/chapter-manage/" + bid
    return CONFIG["home"] + "book-manage"


def resolve_book_id(page, book):
    """登记缺 book_id 时按书名在作家后台找回：点书卡进详情，从 URL 提取。

    建书成功但 id 提取落空（跳转链没走完/auto_submit=false 人工提交）的书，
    发章前经 manager._resolve_book_id 调到这里补账；找不回返回空串不拦死。"""
    import re
    import time
    from . import flow as _flow
    title = str((book or {}).get("title") or "").strip()
    if not title:
        return ""
    try:
        page.navigate(CONFIG["home"], timeout=30)
    except Exception:
        return ""
    r = None
    for _try in range(6):                   # 书列表慢渲染
        r = page.call(_flow._click_match_js(), [title], 300)
        if (r or {}).get("ok"):
            break
        time.sleep(1.0)
    if not (r or {}).get("ok"):
        return ""
    time.sleep(2.5)                         # 详情页跳转收尾
    m = re.search(r"book-info/(\d+)|chapter-manage/(\d+)|writer/(\d+)/publish",
                  str(page.url() or ""))
    if not m:
        return ""
    return m.group(1) or m.group(2) or m.group(3) or ""
