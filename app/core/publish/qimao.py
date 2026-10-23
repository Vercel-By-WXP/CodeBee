# -*- coding: utf-8 -*-
"""七猫作者后台的发布流程定义（默认表：选择器为合理推测，待实测校准）。

校准方式同番茄：data/publish/flows-qimao.json 覆盖 + 「探测表单」dump。
URL 依据：tools/qimao_bookmeta.json 的 source 记录 2026-09-17 登录态实抓
zuozhe.qimao.com/api/pc/v1/book/book-option——作者后台 PC 端在 zuozhe.qimao.com。

七猫建书表单结构（同次实抓）：频道级联（男/女生 → 一级 → 二级）+ 四组标签
（风格/角色/情节/背景，每组 1-3 个）+ 文本字段。
"""
from __future__ import annotations

CHAPTER_TITLE_SELECTOR = (
    "textarea[placeholder='请输入章节名称，最多20个字'],"
    "input[placeholder*='章节'],input[placeholder*='标题'],input[placeholder*='章名'],"
    "textarea[placeholder*='章节'],textarea[placeholder*='标题'],"
    "textarea[placeholder*='章名'],"
    "[contenteditable=true][data-placeholder*='章节'],"
    "[contenteditable=true][data-placeholder*='标题'],"
    "[contenteditable=true][data-placeholder*='章名']"
)

CONFIG = {
    "id": "qimao",
    "label": "七猫",
    "home": "https://zuozhe.qimao.com/",
    # 作品管理页：建书入口（「新建小说」按钮所在）；建书向导第二步表单在其下
    "book_manage": "https://zuozhe.qimao.com/front/book-manage",
    # 短故事频道（2026-10-22 实机校准）：作品形态与小说平级，列表/建书入口独立；
    # 分类 id 空间与小说不同（client_id=5，同名「玄幻奇幻」小说=7/短故事=476），
    # 全量目录实抓存 tools/qimao_shortstory_bookoption.json。UI 流程按名称点选，
    # 天然隔离两套 id——绝不在流程表里直写分类数字 id。
    "short_book_manage": "https://zuozhe.qimao.com/front/book-manage/short",
    "short_create_url": "https://zuozhe.qimao.com/front/book-manage/short/information",
    # 短故事编辑器顶栏明示：正文最少 4000 字、最多 70000 字（与小说的 1000 字下限不同）
    "min_chapter_chars": 1000,
    "short_story_body_min_chars": 4000,
    "short_story_body_max_chars": 70000,
    "login_url_marks": ["login", "signin", "passport", "sso"],
    # 章节管理页计数（2026-09-28 实机校准）：Element UI 表格，.el-table__row
    # 只中数据行（表头在独立的 header-wrapper 表里），与发章流程的等待选择器同源
    "count_rows_js":
        "()=>[...document.querySelectorAll('.el-table__row')]"
        ".map(tr => tr.innerText.replace(/\\s+/g, ' ').trim()).filter(Boolean)",
}

# 建书成功后各入口跳转 URL 里的作品编号提取（番茄 book-info/<id>；七猫小说
# information?id=<id>、短故事 book-upload?id=<id> 直跳编辑器；列表点击回
# manage/draft）。单一真源：建书 runner 与 reconcile 共用，加新入口在这里补。
BOOK_ID_URL_RE = (r"book-info/(\d+)|information\?id=(\d+)"
                  r"|book-upload\?id=(\d+)|manage\?id=(\d+)|draft\?id=(\d+)")

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
    # 七猫真机（2026-10-14）：作品章节管理页的“新建章节”才会创建带
    # cid 的编辑器草稿；直接拼 book-upload?id= 会落到空白编辑器，随后
    # “立即发布”只更新草稿或被页面静默拦截。
    {"do": "navigate", "url": "{chapter_manage_url}", "timeout": 40},
    {"do": "url_any", "any": ["qimao.com"]},
    {"do": "wait", "sel": "a.create-new-chapter-btn,a[class*='create-new-chapter']",
     "timeout": 20},
    {"do": "click_text", "text": "新建章节", "contains": True,
     "scope": "a,button,[role=button]"},
    {"do": "wait", "sel": "textarea[placeholder='请输入章节名称，最多20个字']",
     "timeout": 20},
    {"do": "fill", "sel": "textarea[placeholder='请输入章节名称，最多20个字']",
     "key": "chapter_title"},
    # 七猫新建章节会沿用一个不可靠的默认序号（实测新稿直接跳到第 10/11 章）。
    # 发布前必须显式写入本地稿件章号，否则平台弹窗会把当前章当成错误序号，
    # 点击“更正序号去发布”也不会稳定落到目标章。
    {"do": "fill", "sel": ".chapter-index-input-input input",
     "key": "chapter_no"},
    {"do": "wait", "sel": ".q-contenteditable.book.font-size-16.edit-mask",
     "timeout": 15},
    {"do": "fill", "sel": ".q-contenteditable.book.font-size-16.edit-mask",
     "key": "chapter_body"},
    {"do": "shot", "name": "chapter-filled"},
    {"do": "expect_text", "any": ["已保存到云端"], "timeout": 15},
    {"do": "submit", "gate": True},
    {"do": "click_real", "text": "立即发布",
     "scope": "a,button,[role=button],span", "tries": 25},
    {"do": "js_click", "text": "立即发布",
     "scope": "a,button,[role=button]", "tries": 10,
     "skip_if_modal": True},
    {"do": "expect_text", "any": ["立即发布确认", "检测到章节序号不连续"],
     "timeout": 12, "visible_only": True},
    {"do": "shot", "name": "after-immediate-publish"},
    {"do": "sleep", "s": 1.2},
    {"do": "js_click", "text": "更正序号去发布",
     "scope": "a,button,[role=button]", "tries": 12, "optional": True},
    {"do": "sleep", "s": 0.8},
    {"do": "js_click", "text": "确认发布",
     "scope": ".el-dialog a,.el-dialog button", "tries": 15},
    {"do": "expect_text_absent", "any": ["立即发布确认"], "timeout": 15,
     "visible_only": True},
    {"do": "sleep", "s": 2.5},
    {"do": "verify_chapter_status", "url": "{chapter_manage_url}",
     "timeout": 35, "settle": 4, "new_tab": True,
     "row_sel": ".el-table__row, tr",
     "live_status": ["待审核", "审核中", "已发布"], "draft_status": ["草稿"]},
]

# 发章存草稿（2026-10-09 全部发草稿）：内置推测表——真机校准表在
# flows-qimao-calibrated.json（按其 upload_chapter 的「存草稿」段拆出：
# 填好→存草稿→草稿箱验证行在场即成）。草稿不上线；提交发布由用户在
# 草稿箱手工点（立即发布→更正序号→确认发布三层弹窗照旧人工走）。
UPLOAD_CHAPTER_DRAFT = [
    {"do": "navigate", "url": "{editor_url}"},
    {"do": "url_any", "any": ["qimao.com"]},
    {"do": "fill", "sel": CHAPTER_TITLE_SELECTOR, "key": "chapter_title"},
    {"do": "wait", "sel": "[contenteditable=true],textarea[class*=content],iframe", "timeout": 10},
    {"do": "fill", "sel": "[contenteditable=true],textarea[class*=content]", "key": "chapter_body"},
    {"do": "shot", "name": "chapter-filled-draft"},
    {"do": "click_real", "text": "存草稿", "tries": 25},
    {"do": "sleep", "s": 2},
    {"do": "expect_text", "any": ["保存成功", "存草稿成功", "草稿保存成功",
                                     "保存草稿成功", "已保存"], "timeout": 12},
    {"do": "verify", "url": "{draft_url}", "any": ["{chapter_title}"], "settle": 5},
]

# 短故事正文上传（2026-10-22 校准：编辑器路由与正文框和小说同款
# q-contenteditable，单文档无章节标题/序号字段——整篇一次填入，存草稿后
# 由用户在草稿箱人工点发布）。字数预检（4000-70000，编辑器顶栏实抓）在
# manager 侧做，流程表只管填与存。正文框沿用小说校准选择器（font-size-16
# 编辑器组件同款）；首跑如有出入以 fail-shot 为准校准。
UPLOAD_SHORT_STORY_DRAFT = [
    {"do": "navigate", "url": "{editor_url}"},
    {"do": "url_any", "any": ["qimao.com"]},
    {"do": "wait", "sel": ".q-contenteditable.book", "timeout": 20},
    {"do": "fill", "sel": ".q-contenteditable.book.font-size-16.edit-mask",
     "key": "chapter_body"},
    # 「一/二…」标记行 → h3 章节标题块（发布硬校验 ≥2 章、每章 500~1500 字；
    # 分节数由 manager 按字数实切，标记按上限列、缺段落跳过——与发布表同款）
    {"do": "sleep", "s": 1},
    {"do": "format_block", "text": "一", "block": "h3"},
    {"do": "format_block", "text": "二", "block": "h3"},
    {"do": "format_block", "text": "三", "block": "h3", "optional": True},
    {"do": "format_block", "text": "四", "block": "h3", "optional": True},
    {"do": "format_block", "text": "五", "block": "h3", "optional": True},
    {"do": "format_block", "text": "六", "block": "h3", "optional": True},
    {"do": "format_block", "text": "七", "block": "h3", "optional": True},
    {"do": "format_block", "text": "八", "block": "h3", "optional": True},
    {"do": "format_block", "text": "九", "block": "h3", "optional": True},
    {"do": "format_block", "text": "十", "block": "h3", "optional": True},
    {"do": "format_block", "text": "十一", "block": "h3", "optional": True},
    {"do": "format_block", "text": "十二", "block": "h3", "optional": True},
    {"do": "shot", "name": "short-story-body-filled"},
    # 已有草稿的编辑器按钮是「更新草稿」（真机 2026-10-23 实案）
    {"do": "click_real", "text": "存为草稿", "alt": ["更新草稿"], "tries": 25},
    {"do": "sleep", "s": 2},
    {"do": "expect_text", "any": ["已保存到云端", "保存成功", "存草稿成功",
                                   "草稿保存成功", "更新成功", "已保存"],
     "timeout": 15},
    {"do": "verify", "url": "{short_book_manage}", "any": ["{book_name}"],
     "settle": 5},
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

# 短故事建书（2026-10-22 登录态实测校准：真建一本草稿走通全表后删除）。
# 与小说建书的差异：入口直达 information 页；频道是 el-radio；一级/二级分类
# 是 qm-select 按名称点选（`:has` 选择器实机验证可用）；简介是富文本（页内
# 唯一 contenteditable）；标签弹层与小说同构（.tags-wrap[data-type-id]，
# _tag_click_js 原样可用，四类型各必选 1-3 个）；主角名选填且三个 textarea
# 无稳定区分选择器，v1 不填（缺失由平台表单校验兜底）。声明默认「本人原创」，
# 含 AI 内容时由作者在后台改（disclaimer_type 是接口字段，UI 流程不碰）。
CREATE_SHORT_STORY = [
    {"do": "navigate", "url": "{home}"},
    {"do": "url_any", "any": ["qimao.com"]},
    {"do": "navigate", "url": "{short_create_url}", "timeout": 40},
    {"do": "wait", "sel": "textarea[placeholder*='作品名称']", "timeout": 20},
    {"do": "fill", "sel": "textarea[placeholder*='作品名称']", "key": "title"},
    {"do": "click_text", "text": "{target_reader}", "contains": True,
     "scope": ".el-radio"},
    {"do": "click", "sel": ".el-select:has(input[placeholder*='一级分类'])"},
    {"do": "click_text", "text": "{category_main}", "contains": False,
     "scope": ".el-select-dropdown__item"},
    {"do": "click", "sel": ".el-select:has(input[placeholder*='二级分类'])"},
    {"do": "click_text", "text": "{category_sub}", "contains": False,
     "scope": ".el-select-dropdown__item"},
    {"do": "fill", "sel": ".q-highlight-box-content [contenteditable=true]",
     "key": "summary"},
    # 主角名是必填（2026-10-22 fail-shot 实证：缺它确认创建置灰）；三个
    # textarea 无区分选择器，按标签填（连载校准表同款 fill_label）
    {"do": "fill_label", "label": "主角名", "key": "protagonist"},
    {"do": "click_text", "text": "添加标签", "contains": True,
     "scope": "a,button,span,div"},
    {"do": "tags"},
    # 关弹层必须真实鼠标：el.click() 在弹层动画/遮罩下会报 ok 但被平台吞掉
    # （2026-10-22 实案：弹层开着点「确认创建」= 点空；同坑先例见标签芯片
    # 0928 事故注）。click_text/click_real 双保险。
    {"do": "click_text", "text": "确定", "contains": False},
    {"do": "click_real", "text": "确定", "tries": 10},
    {"do": "sleep", "s": 1},
    {"do": "shot", "name": "short-story-create-filled"},
    {"do": "submit", "sel": ".js-save-book-infor-btn", "text": "确认创建"},
    # 实测提交成功后页面停在原处弹 toast（不一定跳编辑器）——book_id 由
    # runner 的按名对账（含新 tab 兜底）补齐
    {"do": "expect_text", "any": ["创建成功", "保存成功", "发布成功"], "timeout": 15},
]

# 短故事发布（2026-10-23 边界收口）：与小说发章同一编辑器组件，顶栏
# 「立即发布」→ 确认弹层链由 qimao_short_publish 步推进（「确认发布」
# 合成点击会被吞、必须真实鼠标；内容声明发布后不可改，选不上即中止；
# 合规框/序号提示自动过）。硬凭据 = 短故事管理页书名行带审核状态
# （verify any_of）。编辑器与选择器沿用草稿链实抓口径；确认链首跑如有
# 出入以 fail-shot 为准校准。
PUBLISH_SHORT_STORY = [
    {"do": "navigate", "url": "{editor_url}"},
    {"do": "url_any", "any": ["qimao.com"]},
    {"do": "wait", "sel": ".q-contenteditable.book", "timeout": 20},
    {"do": "fill", "sel": ".q-contenteditable.book.font-size-16.edit-mask",
     "key": "chapter_body"},
    # 「一/二…」标记行 → h3 章节标题块（发布硬校验 ≥2 章、每章 500~1500 字；
    # 分节数由 manager 按字数实切，标记按上限列、缺段落跳过）
    {"do": "sleep", "s": 1},
    {"do": "format_block", "text": "一", "block": "h3"},
    {"do": "format_block", "text": "二", "block": "h3"},
    {"do": "format_block", "text": "三", "block": "h3", "optional": True},
    {"do": "format_block", "text": "四", "block": "h3", "optional": True},
    {"do": "format_block", "text": "五", "block": "h3", "optional": True},
    {"do": "format_block", "text": "六", "block": "h3", "optional": True},
    {"do": "format_block", "text": "七", "block": "h3", "optional": True},
    {"do": "format_block", "text": "八", "block": "h3", "optional": True},
    {"do": "format_block", "text": "九", "block": "h3", "optional": True},
    {"do": "format_block", "text": "十", "block": "h3", "optional": True},
    {"do": "format_block", "text": "十一", "block": "h3", "optional": True},
    {"do": "format_block", "text": "十二", "block": "h3", "optional": True},
    {"do": "shot", "name": "short-story-publish-filled"},
    {"do": "expect_text", "any": ["已保存到云端", "保存成功", "已保存"],
     "timeout": 15},
    {"do": "submit", "gate": True},
    {"do": "click_real", "text": "立即发布",
     "scope": "a,button,[role=button],span", "tries": 25},
    {"do": "qimao_short_publish", "timeout": 120},
    {"do": "shot", "name": "short-story-published"},
    {"do": "verify", "url": "{short_book_manage}", "any": ["{book_name}"],
     "any_of": ["待审核", "审核中", "已发布"],
     "settle": 6, "new_tab": True, "tries": 3},
]

FLOWS = {"create_book": CREATE_BOOK, "upload_chapter": UPLOAD_CHAPTER,
         "upload_chapter_draft": UPLOAD_CHAPTER_DRAFT,
         "create_short_story": CREATE_SHORT_STORY,
         "upload_short_story_draft": UPLOAD_SHORT_STORY_DRAFT,
         "publish_short_story": PUBLISH_SHORT_STORY,
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


def values_create_short_story(meta):
    """短故事建书表单取值：与小说同字段名（title/summary/target_reader/
    category_main/category_sub），UI 全按名称点选——分类 id 空间差异由平台
    下拉自行隔离（短故事频道 client_id=5）。无 status 字段（短故事没有
    连载中/已完结选项）。"""
    return {
        "title": (meta.get("book_name") or "").strip(),
        "summary": (meta.get("summary") or "").strip(),
        "protagonist": (meta.get("protagonist_1") or "").strip(),
        "target_reader": (meta.get("target_reader") or "").strip(),
        "category_main": (meta.get("category_main") or "").strip(),
        "category_sub": (meta.get("category_sub") or "").strip(),
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


def _book_id_from_open_tabs(page):
    """从该浏览器的全部标签里捞作品编号。

    短故事场景的标题/「新建短故事」链接都是 target=_blank——点开的新 tab
    才带 publish-short/<id>，而 reconcile 手里只有旧 tab 的 page 对象，
    page.url() 永远拿不到（2026-10-22 实案：对账点中同名作品却报
    「无法从平台地址提取作品编号」）。"""
    import re
    try:
        from .browser import _http_json
        port = getattr(page, "_port", 0)
        if not port:
            return ""
        for t in _http_json("http://127.0.0.1:%d/json/list" % int(port)):
            m = re.search(BOOK_ID_URL_RE, str(t.get("url") or ""))
            if m:
                return next((g for g in m.groups() if g), "")
    except Exception:
        pass
    return ""


def resolve_book_id(page, book):
    """登记缺 book_id 时按书名在作品管理页找回：点书名卡进详情，从 URL 提取。

    与番茄同款对账通道（fanqie.resolve_book_id）；找不回返回空串不拦死。
    小说与短故事是两个列表（book-manage / book-manage/short），先后各找
    一轮——撞名兜底不该关心作品形态，多走一步只发生在小说列表没命中的
    时候（正常路径零开销）。"""
    import re
    import time
    from . import flow as _flow
    title = str((book or {}).get("title") or "").strip()
    if not title:
        return ""
    for list_url in (CONFIG["book_manage"], CONFIG["short_book_manage"]):
        try:
            page.navigate(list_url, timeout=30)
        except Exception:
            continue
        r = None
        for _try in range(6):               # 列表慢渲染
            r = page.call(_flow._click_match_js(), [title], 300)
            if (r or {}).get("ok"):
                break
            time.sleep(1.0)
        if not (r or {}).get("ok"):
            continue
        time.sleep(2.5)                     # 跳转收尾
        m = re.search(BOOK_ID_URL_RE, str(page.url() or ""))
        if m:
            return next((g for g in m.groups() if g), "")
        tab_id = _book_id_from_open_tabs(page)
        if tab_id:
            return tab_id
    return ""


def reconcile_book_id(page, book):
    """Return (found, book_id, error); empty results are trusted only on a loaded list."""
    import re
    import time
    title = str((book or {}).get("title") or "").strip()
    if not title:
        return False, "", "作品名为空"
    try:
        page.navigate(CONFIG["book_manage"], timeout=30)
        for _try in range(6):
            result = page.real_click_text(title, scope="a,button,[role=button],span,li,div,section",
                                          contains=False)
            if (result or {}).get("ok"):
                time.sleep(2.5)
                match = re.search(BOOK_ID_URL_RE, str(page.url() or ""))
                if match:
                    return True, next((g for g in match.groups() if g), ""), ""
                return False, "", "已点中同名作品，但无法从平台地址提取作品编号"
            time.sleep(1.0)
        probe = page.call(
            "(title)=>{const vis=e=>{const r=e.getBoundingClientRect();return r.width>0&&r.height>0};"
            "const titleVisible=[...document.querySelectorAll('a,button,[role=button],span,li,div,section')]."
            "some(e=>vis(e)&&(e.innerText||'').trim()===title);"
            "const create=[...document.querySelectorAll('button,a,[role=button],span')].some(e=>vis(e)&&"
            "(e.innerText||'').trim().includes('创建作品'));"
            "const loading=[...document.querySelectorAll('.el-loading-mask,[aria-busy=true]')].some(vis);"
            "return{ready:document.readyState==='complete'&&location.href.includes('/front/book-manage'),"
            "create,loading,titleVisible};}" , title, 500)
        if (probe and probe.get("ready") and probe.get("create") and
                not probe.get("loading") and not probe.get("titleVisible")):
            return False, "", ""
        if probe and probe.get("titleVisible"):
            return False, "", "七猫已显示同名作品，但无法可靠打开并提取作品编号"
        return False, "", "七猫作品管理列表未完整加载"
    except Exception as exc:
        return False, "", str(exc)[:240]


def reconcile_short_story_book_id(page, book):
    """短故事频道版对账：列表在 book-manage/short（与小说列表分页），点击
    同名作品跳 book-upload?id=<id>（2026-10-22 实机）。与 reconcile_book_id
    同契约 (found, book_id, error)。"""
    import re
    import time
    title = str((book or {}).get("title") or "").strip()
    if not title:
        return False, "", "作品名为空"
    try:
        page.navigate(CONFIG["short_book_manage"], timeout=30)
        for _try in range(6):
            result = page.real_click_text(title, scope="a,button,[role=button],span,li,div,section",
                                          contains=False)
            if (result or {}).get("ok"):
                time.sleep(2.5)
                match = re.search(BOOK_ID_URL_RE, str(page.url() or ""))
                if match:
                    return True, next((g for g in match.groups() if g), ""), ""
                # 新 tab 开出有延迟：多轮枚举，别一轮定生死（2026-10-22 实案）
                for _tab_try in range(4):
                    tab_id = _book_id_from_open_tabs(page)
                    if tab_id:
                        return True, tab_id, ""
                    time.sleep(2.0)
                return False, "", "已点中同名短故事，但无法从平台地址提取作品编号"
            time.sleep(1.0)
        probe = page.call(
            "(title)=>{const vis=e=>{const r=e.getBoundingClientRect();return r.width>0&&r.height>0};"
            "const titleVisible=[...document.querySelectorAll('a,button,[role=button],span,li,div,section')]."
            "some(e=>vis(e)&&(e.innerText||'').trim()===title);"
            "const create=[...document.querySelectorAll('button,a,[role=button],span')].some(e=>vis(e)&&"
            "(e.innerText||'').trim().includes('新建短故事'));"
            "const loading=[...document.querySelectorAll('.el-loading-mask,[aria-busy=true]')].some(vis);"
            "return{ready:document.readyState==='complete'&&location.href.includes('/front/book-manage/short'),"
            "create,loading,titleVisible};}", title, 500)
        if (probe and probe.get("ready") and probe.get("create") and
                not probe.get("loading") and not probe.get("titleVisible")):
            tab_id = _book_id_from_open_tabs(page)
            if tab_id:
                return True, tab_id, ""
            return False, "", ""
        if probe and probe.get("titleVisible"):
            return False, "", "七猫已显示同名短故事，但无法可靠打开并提取作品编号"
        return False, "", "七猫短故事列表未完整加载"
    except Exception as exc:
        return False, "", str(exc)[:240]


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
