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

from urllib.parse import quote

CONFIG = {
    "id": "fanqie",
    "label": "番茄",
    "home": "https://fanqienovel.com/main/writer/",   # 实测 writer. 子域不存在(DNS 000)；快照实抓为主站路径
    # 短故事频道（2026-10-22 实机探测）：与小说平级的独立作品形态。一键建书
    # = 直达 /publish-short/new（同 tab 创建"未命名短故事"并进编辑器，URL 不
    # 带 id，book_id 由存草稿后按名对账提取）；列表在 short-manage。
    # "发表超6000字即有机会签约"是软门槛；硬字数界限未实测，首跑校准。
    "short_manage": "https://fanqienovel.com/main/writer/short-manage",
    "short_create_url": "https://fanqienovel.com/main/writer/publish-short/new",
    "short_story_body_min_chars": 1000,
    "short_story_body_max_chars": 50000,
    # 导航后 URL 含任一标记 → 未登录（跳到了登录/通行证页）
    "login_url_marks": ["login", "passport", "sso", "account/signin"],
    # 章节管理页计数（2026-09-28 实机校准；2026-10-08 补跨页/跨卷）：
    # Arco 表格，数据行=含 ≥4 个 td 的 tr（表头行是 th，天然排除）。
    # 逐行 innerText 交 manager 按状态关键词分桶。表每页 15 行且带分卷筛选，
    # 只数当前页会把 30 章已发报成 15（校准少计实案）——pager_next_js 翻页、
    # volume_js 换卷，manager._collect_all_volumes 负责循环。
    "count_rows_js":
        "()=>[...document.querySelectorAll('tr')]"
        # Chromium's Element.querySelectorAll(':scope > td') is inconsistent
        # on this page and returned zero for real six-cell rows.  Count the
        # direct cell children through children instead.
        ".filter(tr => [...tr.children].filter(c => c.tagName === 'TD').length >= 4)"
        ".map(tr => tr.innerText.replace(/\\s+/g, ' ').trim()).filter(Boolean)",
    # 新建作品尚未提交章节时，章节管理页没有 tr，但会明确显示该空状态。
    # 对账应把它当作远端 0 章，而不是改版/登录失效。
    "empty_chapter_markers": ["暂无章节内容"],
    "pager_next_js":
        "()=>{const n=document.querySelector('.arco-pagination-item-next');"
        "if(!n)return{done:true};"
        "if(/item-disabled/.test(n.className))return{done:true};"
        "n.click();return{done:false};}",
    # 分卷下拉是自绘 byte-select（非 arco-select），弹层在 click 后下一帧才
    # 渲染——四段式由 manager 用 sleep 隔开：current 读当前卷、open 开弹层、
    # options 读选项（弹层开着读）、pick 在已开的弹层里点选某卷（选中即收起）。
    # 无「全部」项，逐卷收齐全量；收完恢复进入时的卷。
    "volume_js": {
        "current":
            "()=>{const v=document.querySelector('.chapter-select-left .byte-select-view');"
            "return v?(v.innerText||'').trim():'';}",
        "open":
            "()=>{const v=document.querySelector('.chapter-select-left .byte-select-view');"
            "if(!v)return{ok:false};v.click();return{ok:true};}",
        "options":
            "()=>{const dd=[...document.querySelectorAll('.byte-select-popup,"
            "[class*=select-dropdown]')].find(e=>e.getBoundingClientRect().width>0);"
            "return dd?[...dd.querySelectorAll('li,[class*=option]')]"
            ".map(e=>(e.innerText||'').trim()).filter(Boolean):[];}",
        "pick":
            "(t)=>{const dd=[...document.querySelectorAll('.byte-select-popup,"
            "[class*=select-dropdown]')].find(e=>e.getBoundingClientRect().width>0);"
            "if(!dd)return{ok:false};"
            "const hit=[...dd.querySelectorAll('li,[class*=option]')]"
            ".find(e=>((e.innerText||'').trim())===t);"
            "if(!hit)return{ok:false};hit.click();return{ok:true};}",
    },
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
# 番茄编辑器「第 [序号] 章 [标题]」三件套分开填：chapter_no=阿拉伯章号
# （序号框只认数字，空着被平台打回）、chapter_name=正题、chapter_body=正文
# （标题行已在 read_chapter 剥掉）。volume 步骤按 values.volume_name 选卷/
# 建卷（自动分卷，无计划时空值跳过）。内置默认表选择器为推测，真机以
# flows-fanqie-calibrated.json / data 覆盖表为准。
UPLOAD_CHAPTER = [
    {"do": "navigate", "url": "{home}"},
    {"do": "url_any", "any": ["fanqienovel.com"]},
    {"do": "click_text", "text": "{book_name}", "contains": True,
     "scope": "a,span,div[class*=title],div[class*=book]"},
    {"do": "click_text", "text": "新建章节", "contains": True, "scope": "button,a,[role=button],span"},
    {"do": "probe", "note": "章节编辑器"},
    {"do": "wait", "sel": "[contenteditable=true],textarea[class*=content],div[class*=editor]", "timeout": 10},
    {"do": "volume", "key": "volume_name", "optional": True,
     "open_sel": "input[type=number],input[class*=serial],div[class*=volume]",
     "modal_sel": "[class*=volume][class*=modal],[class*=modal][class*=volume]",
     "item_sel": "[class*=volume] li,[class*=volume-item]",
     "add_sel": "[class*=add-volume],[class*=volume] button",
     "confirm_sel": "[class*=confirm]", "settle": 1.5},
    {"do": "fill", "sel": "input[type=number],input[class*=serial],input[class*=byte]", "key": "chapter_no"},
    {"do": "fill", "sel": "input[placeholder*='章节'],input[placeholder*='标题']", "key": "chapter_name"},
    {"do": "wait", "sel": "[contenteditable=true],textarea[class*=content],div[class*=editor]", "timeout": 10},
    {"do": "fill", "sel": "[contenteditable=true],textarea[class*=content]", "key": "chapter_body"},
    {"do": "shot", "name": "chapter-filled"},
    {"do": "submit", "sel": "button[class*=publish],button[class*=submit]"},
    {"do": "js_click", "text": "提交", "scope": "button", "tries": 20,
     "optional": True},
]

# 发章存草稿（2026-10-09 全部发草稿；2026-10-10 假成功案重构）：
# 与 UPLOAD_CHAPTER 同一张填表前段，终点不同——点编辑器头部「存草稿」直接
# 落草稿箱，不走「下一步→内容检测→发布提示→提交」发布链。三点教训入表：
# 1) submit 带 always=true：草稿流程跑在 auto_submit=false 语义下，而这里的
#    「存草稿」点击本身就是保存动作——被人工闸吞掉时 manager 把「填好未存」
#    记成成功（44 章假成功实案）。
# 2) expect_text 等编辑器页保存反馈为主凭据（标记文案以真机校准为准，失败
#    自动落 fail 截图）；「已保存」这类自动保存字样刻意不入标记——编辑器
#    自动保存不可依赖，不能当本次点击的证据。
# 3) verify 仍走章节管理页兜底（新章草稿行同样在列表里）；对「已发布章重复
#    起草」它必然假通过——该情形由起草前对账（auto 层）剔除，不靠本表。
UPLOAD_CHAPTER_DRAFT = [
    {"do": "navigate", "url": "{editor_url}"},
    {"do": "url_any", "any": ["fanqienovel.com"]},
    {"do": "wait", "sel": "[contenteditable=true],textarea[class*=content],div[class*=editor]", "timeout": 10},
    {"do": "volume", "key": "volume_name", "optional": True,
     "open_sel": "input[type=number],input[class*=serial],div[class*=volume]",
     "modal_sel": "[class*=volume][class*=modal],[class*=modal][class*=volume]",
     "item_sel": "[class*=volume] li,[class*=volume-item]",
     "add_sel": "[class*=add-volume],[class*=volume] button",
     "confirm_sel": "[class*=confirm]", "settle": 1.5},
    {"do": "fill", "sel": "input[type=number],input[class*=serial],input[class*=byte]", "key": "chapter_no"},
    {"do": "fill", "sel": "input[placeholder*='章节'],input[placeholder*='标题']", "key": "chapter_name"},
    {"do": "wait", "sel": "[contenteditable=true],textarea[class*=content],div[class*=editor]", "timeout": 10},
    {"do": "fill", "sel": "[contenteditable=true],textarea[class*=content]", "key": "chapter_body"},
    {"do": "shot", "name": "chapter-filled-draft"},
    {"do": "submit", "text": "存草稿", "scope": "button", "tries": 15, "always": True},
    {"do": "sleep", "s": 1.2},
    {"do": "shot", "name": "chapter-draft-clicked"},
    {"do": "expect_text",
     "any": ["保存成功", "存草稿成功", "草稿已保存", "已存草稿"], "timeout": 12},
    {"do": "verify", "url": "{chapter_manage_url}",
     "any": ["第{chapter_no}章", "{chapter_name}"], "settle": 6, "new_tab": True},
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

# 短故事建书（2026-10-22 实机探测）：一键建——直达 /publish-short/new 即
# 创建"未命名短故事"进编辑器（同 tab，URL 暂不带 id）。名称/正文/分类/AI
# 声明都在编辑器链路里，由 upload_short_story_draft 与首跑校准补——本表只
# 负责"把作品开出来"。book_id 由 runner 按名对账（reconcile_short_story_
# book_id）提取。
CREATE_SHORT_STORY = [
    {"do": "navigate", "url": "{home}"},
    {"do": "url_any", "any": ["fanqienovel.com"]},
    {"do": "navigate", "url": "{short_create_url}", "timeout": 40},
    {"do": "wait", "sel": ".ProseMirror", "timeout": 20},
    {"do": "shot", "name": "short-story-created"},
    {"do": "url_any", "any": ["publish-short/"]},
]

# 短故事正文上传：编辑器页填名称（textarea）+ 正文（ProseMirror）→ 存草稿。
# 第二步设置区（封面/是否使用AI/作品分类/试读/发布协议）同页右侧，存草稿若
# 被必填项拦下，首跑以 fail-shot 校准补步（AI 声明按作者口径应选「是」）。
UPLOAD_SHORT_STORY_DRAFT = [
    {"do": "navigate", "url": "{short_editor_url}"},
    {"do": "url_any", "any": ["fanqienovel.com"]},
    {"do": "wait", "sel": ".ProseMirror", "timeout": 20},
    {"do": "fill", "sel": "textarea[placeholder*='短故事名称']", "key": "chapter_title"},
    {"do": "fill", "sel": ".ProseMirror", "key": "chapter_body"},
    {"do": "shot", "name": "fanqie-short-body-filled"},
    {"do": "click_real", "text": "存草稿", "tries": 25},
    {"do": "sleep", "s": 2},
    {"do": "expect_text", "any": ["保存成功", "已保存", "草稿保存成功",
                                   "存草稿成功"], "timeout": 15},
    {"do": "verify", "url": "{short_manage_url}", "any": ["{book_name}"],
     "settle": 5},
]

FLOWS = {"create_book": CREATE_BOOK, "upload_chapter": UPLOAD_CHAPTER,
         "upload_chapter_draft": UPLOAD_CHAPTER_DRAFT,
         "create_short_story": CREATE_SHORT_STORY,
         "upload_short_story_draft": UPLOAD_SHORT_STORY_DRAFT,
         "check_login": CHECK_LOGIN, "probe_form": PROBE_FORM}


def short_story_editor_url(book):
    """短故事编辑器直达（publish-short/<id>；一键建后的作品页）。"""
    bid = str((book or {}).get("book_id") or "")
    if bid:
        return CONFIG["home"] + "publish-short/" + bid
    return CONFIG["short_manage"]


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


def book_id_valid(book_id):
    """Return whether *book_id* is a canonical Fanqie writer-console id."""
    value = str(book_id or "").strip()
    return value.isdigit() and len(value) >= 15


def chapter_manage_url(book):
    """章节管理页（上线验证用）。"""
    bid = str((book or {}).get("book_id") or "")
    if bid:
        # The current writer console appends the work title as a query-like
        # path segment (``/<book_id>&<title>``).  Without it the page can
        # render the shell but leave the chapter table unhydrated, making the
        # preflight ledger guard stop every unattended batch.
        title = str((book or {}).get("title") or "").strip()
        suffix = ("&" + quote(title, safe="") if title else "")
        return "https://fanqienovel.com/main/writer/chapter-manage/" + bid + suffix
    return CONFIG["home"] + "book-manage"


# 作品编号提取（番茄小说 book-info/<id>/chapter-manage/<id>/<id>/publish；
# 短故事 publish-short/<id>）。runner 与对账共用。
BOOK_ID_URL_RE = (r"book-info/(\d+)|chapter-manage/(\d+)|writer/(\d+)/publish"
                  r"|publish-short/(\d+)")


def resolve_book_id(page, book):
    """登记缺 book_id 时按书名在作家后台找回：点书卡进详情，从 URL 提取。

    建书成功但 id 提取落空（跳转链没走完/auto_submit=false 人工提交）的书，
    发章前经 manager._resolve_book_id 调到这里补账；找不回返回空串不拦死。
    小说与短故事两个列表先后各找一轮（撞名兜底不关心作品形态）。"""
    import re
    import time
    from . import flow as _flow
    title = str((book or {}).get("title") or "").strip()
    if not title:
        return ""
    for list_url in (CONFIG["home"] + "book-manage", CONFIG["short_manage"]):
        try:
            page.navigate(list_url, timeout=30)
        except Exception:
            continue
        r = None
        for _try in range(6):               # 书列表慢渲染
            r = page.call(_flow._click_match_js(), [title], 300)
            if (r or {}).get("ok"):
                break
            time.sleep(1.0)
        if not (r or {}).get("ok"):
            continue
        time.sleep(2.5)                     # 详情页跳转收尾
        m = re.search(BOOK_ID_URL_RE, str(page.url() or ""))
        if m:
            return next((g for g in m.groups() if g), "")
    return ""


def reconcile_short_story_book_id(page, book):
    """短故事版对账：列表在 short-manage，点击作品跳 publish-short/<id>
    （2026-10-22 实机）。与 reconcile_book_id 同契约 (found, book_id, error)。"""
    import re
    import time
    title = str((book or {}).get("title") or "").strip()
    if not title:
        return False, "", "作品名为空"
    try:
        page.navigate(CONFIG["short_manage"], timeout=30)
        for _try in range(6):
            result = page.real_click_text(title, scope="a,button,[role=button],span,li,div,section",
                                          contains=False)
            if (result or {}).get("ok"):
                time.sleep(2.5)
                match = re.search(BOOK_ID_URL_RE, str(page.url() or ""))
                if match:
                    return True, next((g for g in match.groups() if g), ""), ""
                return False, "", "已点中同名短故事，但无法从平台地址提取作品编号"
            time.sleep(1.0)
        probe = page.call(
            "(title)=>{const vis=e=>{const r=e.getBoundingClientRect();return r.width>0&&r.height>0};"
            "const titleVisible=[...document.querySelectorAll('a,button,[role=button],span,li,div,section')]."
            "some(e=>vis(e)&&(e.innerText||'').trim()===title);"
            "const create=[...document.querySelectorAll('button,a,[role=button],span')].some(e=>vis(e)&&"
            "(e.innerText||'').trim().includes('新建短故事'));"
            "const loading=[...document.querySelectorAll('[class*=loading],[aria-busy=true]')].some(vis);"
            "return{ready:document.readyState==='complete'&&location.href.includes('/short-manage'),"
            "create,loading,titleVisible};}", title, 500)
        if (probe and probe.get("ready") and probe.get("create") and
                not probe.get("loading") and not probe.get("titleVisible")):
            return False, "", ""
        if probe and probe.get("titleVisible"):
            return False, "", "番茄已显示同名短故事，但无法可靠打开并提取作品编号"
        return False, "", "番茄短故事列表未完整加载"
    except Exception as exc:
        return False, "", str(exc)[:240]


def reconcile_book_id(page, book):
    """Return (found, book_id, error); only trust a miss on a loaded works page."""
    import re
    import time
    title = str((book or {}).get("title") or "").strip()
    if not title:
        return False, "", "作品名为空"
    try:
        page.navigate(CONFIG["home"] + "book-manage", timeout=30)
        for _try in range(6):
            result = page.real_click_text(title, scope="a,button,[role=button],span,li,div,section",
                                          contains=False)
            if (result or {}).get("ok"):
                time.sleep(2.5)
                match = re.search(r"book-info/(\d+)|chapter-manage/(\d+)|writer/(\d+)/publish",
                                  str(page.url() or ""))
                if match:
                    return True, match.group(1) or match.group(2) or match.group(3), ""
                return False, "", "已点中同名作品，但无法从平台地址提取作品编号"
            time.sleep(1.0)
        probe = page.call(
            "(title)=>{const vis=e=>{const r=e.getBoundingClientRect();return r.width>0&&r.height>0};"
            "const titleVisible=[...document.querySelectorAll('a,button,[role=button],span,li,div,section')]."
            "some(e=>vis(e)&&(e.innerText||'').trim()===title);"
            "const create=[...document.querySelectorAll('button,a,[role=button],span')].some(e=>vis(e)&&"
            "(e.innerText||'').trim().includes('创建作品'));"
            "const loading=[...document.querySelectorAll('.arco-spin,[aria-busy=true]')].some(vis);"
            "return{ready:document.readyState==='complete'&&location.href.includes('/main/writer/')"
            "&&location.href.includes('book-manage'),create,loading,titleVisible};}" , title, 500)
        if (probe and probe.get("ready") and probe.get("create") and
                not probe.get("loading") and not probe.get("titleVisible")):
            return False, "", ""
        if probe and probe.get("titleVisible"):
            return False, "", "番茄已显示同名作品，但无法可靠打开并提取作品编号"
        return False, "", "番茄作品管理列表未完整加载"
    except Exception as exc:
        return False, "", str(exc)[:240]
