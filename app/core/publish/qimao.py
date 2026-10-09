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
    # 编辑器顶栏明示「正文字数最少 1000 字」——不足时「立即发布」被静默拦截
    "min_chapter_chars": 1000,
    "login_url_marks": ["login", "signin", "passport", "sso"],
    # 章节管理页计数（2026-09-28 实机校准）：Element UI 表格，.el-table__row
    # 只中数据行（表头在独立的 header-wrapper 表里），与发章流程的等待选择器同源
    "count_rows_js":
        "()=>[...document.querySelectorAll('.el-table__row')]"
        ".map(tr => tr.innerText.replace(/\\s+/g, ' ').trim()).filter(Boolean)",
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

# 发章存草稿（2026-10-09 全部发草稿）：内置推测表——真机校准表在
# flows-qimao-calibrated.json（按其 upload_chapter 的「存草稿」段拆出：
# 填好→存草稿→草稿箱验证行在场即成）。草稿不上线；提交发布由用户在
# 草稿箱手工点（立即发布→更正序号→确认发布三层弹窗照旧人工走）。
UPLOAD_CHAPTER_DRAFT = [
    {"do": "navigate", "url": "{editor_url}"},
    {"do": "url_any", "any": ["qimao.com"]},
    {"do": "fill", "sel": "textarea[placeholder*='章节名称']", "key": "chapter_title"},
    {"do": "wait", "sel": "[contenteditable=true],textarea[class*=content],iframe", "timeout": 10},
    {"do": "fill", "sel": "[contenteditable=true],textarea[class*=content]", "key": "chapter_body"},
    {"do": "shot", "name": "chapter-filled-draft"},
    {"do": "click_real", "text": "存草稿", "tries": 25},
    {"do": "sleep", "s": 2},
    {"do": "verify", "url": "{draft_url}", "any": ["{chapter_title}"], "settle": 5},
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
         "upload_chapter_draft": UPLOAD_CHAPTER_DRAFT,
         "check_login": CHECK_LOGIN, "probe_form": PROBE_FORM}


# 标签字段名 → 弹层左侧组显示名（tags 步骤按组切换后点选；每组必选 1-3 个）
TAG_GROUP_LABELS = {"tags_style": "风格", "tags_role": "角色",
                    "tags_plot": "情节", "tags_bg": "背景"}


def values_create_book(meta):
    from .. import bookmeta_catalog as cat
    target_reader = (meta.get("target_reader") or "").strip()
    category_main = (meta.get("category_main") or "").strip()
    category_sub = (meta.get("category_sub") or "").strip()
    # 旧数据兜底：二级有值但一级空/与二级不自洽（如女生专属的「现实故事」
    # 配了男生频道）会让一级 click_text 静默跳过、死在流程中段——按官方
    # 目录跨频道反查对齐，频道与一级跟着二级走
    if category_sub and not (category_main and
                             category_sub in (cat.QIMAO_CATS.get(target_reader)
                                              or {}).get(category_main, [])):
        loc = cat.qimao_locate_sub(category_sub)
        if loc:
            target_reader, category_main = loc
        else:
            category_sub = ""
    return {
        "title": (meta.get("book_name") or "").strip(),
        "summary": (meta.get("summary") or "").strip(),
        "protagonist": (meta.get("protagonist_1") or "").strip(),
        "target_reader": target_reader,
        "category_main": category_main,
        "category_sub": category_sub,
        "status": (meta.get("status") or "连载中").strip(),
    }


def tag_groups(meta):
    out = []
    for key in ("tags_style", "tags_role", "tags_plot", "tags_bg"):
        v = meta.get(key)
        if isinstance(v, list) and v:
            out.append((key, [str(x) for x in v]))
    return out


def chapter_manage_url(book):
    """章节管理页（上线验证用）：有 book_id 直达，否则回作品管理页。"""
    bid = str((book or {}).get("book_id") or "")
    if bid:                                   # 实测 .../front/book-manage/manage?id=
        return CONFIG["book_manage"] + "/manage?id=" + bid
    return CONFIG["book_manage"]


def resolve_book_id(page, book):
    """登记缺 book_id 时按书名在作品管理页找回：点书名卡进详情，从 URL 提取。

    与番茄同款对账通道（fanqie.resolve_book_id）；找不回返回空串不拦死。"""
    import re
    import time
    from . import flow as _flow
    title = str((book or {}).get("title") or "").strip()
    if not title:
        return ""
    try:
        page.navigate(CONFIG["book_manage"], timeout=30)
    except Exception:
        return ""
    r = None
    for _try in range(6):                   # 列表慢渲染
        r = page.call(_flow._click_match_js(), [title], 300)
        if (r or {}).get("ok"):
            break
        time.sleep(1.0)
    if not (r or {}).get("ok"):
        return ""
    time.sleep(2.5)                         # 跳转收尾
    m = re.search(r"manage\?id=(\d+)|draft\?id=(\d+)|information\?id=(\d+)",
                  str(page.url() or ""))
    if not m:
        return ""
    return m.group(1) or m.group(2) or m.group(3) or ""


def draft_url(book):
    """草稿箱页（发章真发布链中转站）。"""
    bid = str((book or {}).get("book_id") or "")
    if bid:
        return CONFIG["book_manage"] + "/draft?id=" + bid
    return CONFIG["book_manage"]


def editor_url(book):
    """章节编辑器直达（绕开会开新 tab 的「上传章节」点击）。

    缺 title 参数会被平台重定向回首页（真机实测），必须带上。"""
    bid = str((book or {}).get("book_id") or "")
    title = str((book or {}).get("title") or "")
    if bid:
        from urllib.parse import quote
        return (CONFIG["book_manage"].rsplit("/", 1)[0]
                + "/book-upload?id=" + bid + "&title=" + quote(title))
    return CONFIG["book_manage"]
