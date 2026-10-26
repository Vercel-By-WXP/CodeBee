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
    # 开编辑器即填名并等自动保存落库——新作立刻以该名进草稿箱，
    # 建书后的按名对账（_short_draft_row_id）才能拿到 id
    {"do": "fill", "sel": "textarea[placeholder*='短故事名称']", "key": "title"},
    {"do": "sleep", "s": 3},
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
    # 「有刚刚更新的草稿」弹窗盖住编辑器时真实键盘路径全失效（2026-10-26
    # 头七实案：正文只进 4/13207 字）——发布链 10-23 已补此步，上传链漏配
    {"do": "dismiss_stale"},
    {"do": "fill", "sel": "textarea[placeholder*='短故事名称']", "key": "chapter_title"},
    {"do": "fill", "sel": ".ProseMirror", "key": "chapter_body"},
    {"do": "shot", "name": "fanqie-short-body-filled"},
    # 标题填入触发自动保存后恢复提示可能才弹出：填正文前再查一次（同发布链）
    {"do": "dismiss_stale", "timeout": 8},
    {"do": "click_real", "text": "存草稿", "tries": 25},
    {"do": "sleep", "s": 2},
    {"do": "expect_text", "any": ["保存成功", "已保存", "草稿保存成功",
                                   "存草稿成功"], "timeout": 15},
    # 番茄短故事未发布前只进「草稿箱」，主列表始终为空（2026-10-22 实测：
    # 主列表验证误报"章节未真正发布"）。verify 落草稿箱页断言标题。
    {"do": "verify", "url": "{short_manage_url}", "any": ["草稿箱"],
     "settle": 4},
    {"do": "click_text", "text": "草稿箱", "contains": False},
    {"do": "expect_text", "any": ["{book_name}"], "timeout": 15},
]

# 短故事发布（2026-10-23 实机校准的桌面链路）：
# 编辑器同页右侧设置区 → 封面（隐藏 file input 走 DOM.setFileInputFiles）
# → 是否使用AI=是 → 作品分类（自绘下拉，主分类）→ 试读比例 30% → 发布协议
# （ARCO 复选框，首次勾选弹授权说明）→ 下一步 → 发布提示「继续发布」→
# 提交确认「确定」→ 「发布成功／已提交，预计1天内审核完成」→ 短故事管理页
# 核验「审核中」。缺封面/分类时对应步骤按 if_key 整体跳过。
PUBLISH_SHORT_STORY = [
    {"do": "navigate", "url": "{short_editor_url}"},
    {"do": "url_any", "any": ["fanqienovel.com"]},
    {"do": "wait", "sel": ".ProseMirror", "timeout": 25},
    # 旧稿恢复提示必须在填表前放弃：点「继续编辑」会拿旧稿顶掉本次正文
    {"do": "dismiss_stale"},
    {"do": "fill", "sel": "textarea[placeholder*='短故事名称']", "key": "chapter_title"},
    # 该提示还会在标题自动保存之后异步弹出（服务端存在更新草稿时）——
    # 步骤3 的检查窗口抓不到，模态抢焦点让正文 insertText 只落几字
    # （2026-10-23 实案：4/7820 字，连挂两次）。填正文前再查一次。
    {"do": "dismiss_stale", "timeout": 8},
    {"do": "fill", "sel": ".ProseMirror", "key": "chapter_body"},
    {"do": "shot", "name": "fanqie-short-publish-filled"},
    # 封面：配置块 → 抽屉 → 本地上传 → file input → 确认上传 → 预览确定
    {"do": "click", "sel": ".publish-short-config-book-cover-content",
     "if_key": "_cover_path"},
    {"do": "sleep", "s": 1.5, "if_key": "_cover_path"},
    {"do": "click_text", "text": "本地上传", "contains": True, "tries": 8,
     "scope": ".byte-drawer-content button,.byte-drawer-content [class*=tab],"
              ".arco-tabs-header-title",
     "if_key": "_cover_path"},
    {"do": "wait", "sel": ".byte-drawer-content input[type=file]",
     "timeout": 15, "if_key": "_cover_path"},
    {"do": "set_file", "sel": ".byte-drawer-content input[type=file]",
     "key": "_cover_path", "if_key": "_cover_path"},
    {"do": "click_real", "text": "确认上传", "tries": 20,
     "scope": ".byte-drawer-content button,[class*=drawer] button",
     "if_key": "_cover_path"},
    # 确认上传后的「确定」有时在预览模态、有时留在抽屉里（重传场景
    # 2026-10-23 实测只在抽屉出现），两处 scope 都覆盖
    {"do": "click_real", "text": "确定", "tries": 20,
     "scope": ".byte-modal-wrapper button,.arco-modal button,"
              "[role=dialog] button,.byte-drawer-content button,"
              "[class*=drawer] button",
     "if_key": "_cover_path"},
    {"do": "sleep", "s": 1.2, "if_key": "_cover_path"},
    {"do": "shot", "name": "fanqie-short-cover-uploaded", "if_key": "_cover_path"},
    # 是否使用AI：作者口径一律选「是」（value 1）
    {"do": "radio", "key": "ai_use", "map": {"是": "1", "否": "2"},
     "scope_sel": ".publish_short_config_use_ai"},
    # 作品分类：主分类下拉（102 项里当前 Tab 可见的 24 个主分类）
    {"do": "select_item", "name": "作品分类",
     "open_sel": ".publish-short-category-select",
     "item_sel": ".publish-short-category-select-item",
     "any_key": "_categories"},
    # 试读比例：未设置时按钮是「去设置」，不设会拦下「下一步」
    {"do": "fanqie_short_trial"},
    # 发布协议：首次勾选会弹授权说明，点「我已阅读并同意」后才落 checked
    {"do": "check", "name": "发布协议",
     "sel": ".publish-short-config-signLicense .arco-checkbox",
     "checked_sel": ".publish-short-config-signLicense .arco-checkbox-checked",
     "confirm": "我已阅读并同意"},
    {"do": "shot", "name": "fanqie-short-config-done"},
    {"do": "js_click", "text": "下一步", "scope": "button", "tries": 10},
    # 发布前平台要跑完「智能纠错」才弹确认链，纠错占用渲染主线程期间
    # CDP 求值被拖到超时、150s 预算被慢调用吃光（2026-10-23 实案两次
    # 循环超时）。放宽到 240s 让循环活过纠错窗口。
    {"do": "fanqie_short_publish", "timeout": 240},
    {"do": "shot", "name": "fanqie-short-published"},
    # 发布后的硬凭据：短故事管理页里该书名带「审核中」
    {"do": "verify", "url": "{short_manage_url}",
     "any": ["{book_name}", "审核中"], "settle": 6, "new_tab": True,
     "tries": 3},
]

FLOWS = {"create_book": CREATE_BOOK, "upload_chapter": UPLOAD_CHAPTER,
         "upload_chapter_draft": UPLOAD_CHAPTER_DRAFT,
         "create_short_story": CREATE_SHORT_STORY,
         "upload_short_story_draft": UPLOAD_SHORT_STORY_DRAFT,
         "publish_short_story": PUBLISH_SHORT_STORY,
         "check_login": CHECK_LOGIN, "probe_form": PROBE_FORM}


def short_editor_url(book):
    """短故事编辑器直达（publish-short/<id>；一键建后的作品页）。
    命名对齐 manager._url_values 的扫描键（short_editor_url）。"""
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


# 番茄短故事「主分类」实机清单（2026-10-23 编辑器下拉实抓 24 项）。
_SHORT_CATEGORY_NAMES = (
    "婚姻家庭", "女生生活", "男生生活", "现言甜宠", "虐心婚恋", "青春虐恋",
    "男生情感", "女性成长", "悬疑惊悚", "玄幻仙侠", "宫斗宅斗", "男频衍生",
    "女频衍生", "年代", "纯爱", "其他", "古言甜宠", "古风世情", "都市日常",
    "男频脑洞", "女频脑洞", "民国旧影", "古言虐恋", "历史古代",
)

# 作品信息关键词 → 主分类候选（按优先级）。meta 里的分类是平台/模型口径，
# 与短故事分类名不同名（如「女频悬疑」→「悬疑惊悚」），按语义映射；
# 全部不中时落「其他」——平台主分类必选，不能空着提交。
_SHORT_CATEGORY_RULES = (
    (("悬疑", "推理", "惊悚", "灵异", "怪谈", "恐怖"), ("悬疑惊悚",)),
    (("婚姻", "婚恋", "婆媳"), ("婚姻家庭", "虐心婚恋")),
    (("青春",), ("青春虐恋",)),
    (("虐",), ("古言虐恋", "虐心婚恋")),
    (("女性成长", "成长", "逆袭"), ("女性成长",)),
    (("宫斗", "宅斗"), ("宫斗宅斗",)),
    (("玄幻", "仙侠", "修真", "奇幻"), ("玄幻仙侠",)),
    (("民国",), ("民国旧影",)),
    (("历史", "古代", "古装"), ("历史古代",)),
    (("年代",), ("年代",)),
    (("纯爱",), ("纯爱",)),
    (("世情",), ("古风世情",)),
    (("古言", "古风"), ("古言甜宠",)),
    (("都市", "职场", "现实"), ("都市日常",)),
    (("脑洞",), ("女频脑洞", "男频脑洞")),
    (("现言", "言情", "甜宠", "感情", "爱情"), ("现言甜宠",)),
    (("女生", "女频"), ("女生生活", "女性成长")),
    (("男生", "男频"), ("男生生活", "男频脑洞")),
    (("衍生",), ("女频衍生", "男频衍生")),
)


def short_story_categories(meta):
    """作品信息 → 短故事主分类候选（有序；流程按序匹配，落不到就取首项）。"""
    meta = meta or {}
    raw = []
    for key in ("short_category", "category", "category_main", "tags_theme",
                "tags_plot", "theme"):
        value = meta.get(key)
        if isinstance(value, (list, tuple)):
            raw.extend(str(v) for v in value)
        elif value:
            raw.append(str(value))
    text = "／".join(raw)
    out = [name for name in _SHORT_CATEGORY_NAMES if name in text]
    for keys, opts in _SHORT_CATEGORY_RULES:
        if any(k in text for k in keys):
            out.extend(opts)
    out.append("其他")
    return list(dict.fromkeys(out))


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


def _short_draft_row_id(page, title):
    """草稿箱按标题直读作品 id（行 DOM 自带长数字 id，无需点进详情）。

    番茄短故事未发布前只进草稿箱、主列表恒空（2026-10-22 实测）；一键建的
    新作在编辑器里填过名并自动保存后，也会以该名出现在草稿箱。"""
    import time
    try:
        page.navigate(CONFIG["short_manage"], timeout=30)
        time.sleep(2.0)
        from . import flow as _flow
        for _try in range(4):
            page.call(_flow._click_text_js(), "草稿箱", "button,a,span,div", False)
            time.sleep(1.5)
            r = page.call(r"""(title) => {
              const row = [...document.querySelectorAll('div,li,section')]
                .find(e => (e.innerText || '').includes(title)
                           && (e.innerText || '').includes('字')
                           && e.querySelectorAll('*').length < 80);
              if (!row) return '';
              const m = row.outerHTML.match(/\d{15,20}/);
              return m ? m[0] : '';
            }""", title, 300)
            if r:
                return str(r)
            time.sleep(1.5)
    except Exception:
        pass
    return ""


def _short_draft_rows_via_api(page, timeout=8.0):
    """页面上下文直调草稿箱列表接口，返回 item_list（不可得时 None）。

    2026-10-26 头七案：草稿箱页 /short-draft 可渲染空白（DOM 对账全瞎），
    但列表接口带登录态直调正常——按名对账必须有一层不看 DOM 的兜底。"""
    js = ("(async () => { try {"
          "const r = await fetch('/api/author/short_article/draft_list/v0/"
          "?aid=2503&app_name=muye_novel&page_count=20&page_index=0"
          "&time_sort=0&image_fmt_list=270x480', {credentials: 'include'});"
          "if (!r.ok) return null;"
          "const j = await r.json();"
          "const d = (j && j.data) || {};"
          "return Array.isArray(d.item_list) ? d.item_list : null;"
          "} catch (e) { return null; } })()")
    try:
        rows = page.call(js, await_promise=True, timeout=timeout)
    except Exception:
        return None
    if not isinstance(rows, list):
        return None
    return rows[:20]


def reconcile_short_story_book_id(page, book, allow_ghost=False):
    """短故事版对账：草稿箱按标题行直读 id（点标题会开新 tab，旧 tab
    page.url 永远拿不到——2026-10-22 实案改行直读）。与 reconcile_book_id
    同契约 (found, book_id, error)。

    2026-10-26 头七案加固：一键建的 new 物化握手被平台 400 拒时，编辑器
    「已保存」是本地假象、标题永不落库，DOM 与接口都找不到名字，只剩一条
    无名空草稿残片。allow_ghost=True（建书后兜底路径）领养 30 分钟内新建
    的无名空稿；更早的无名空稿只报告不领养（可能是别人的弃稿），报错带
    item_id 让人可在「登记已有作品」里人工绑定。"""
    import time
    title = str((book or {}).get("title") or "").strip()
    if not title:
        return False, "", "作品名为空"
    # 接口优先（不导航、不依赖页面渲染）；取不到再走 DOM 原路
    rows = _short_draft_rows_via_api(page)
    if rows is None:
        row_id = _short_draft_row_id(page, title)
        if row_id:
            return True, row_id, ""
        rows = _short_draft_rows_via_api(page)
    ghosts = []
    if rows:
        now = time.time()
        for it in rows:
            if not isinstance(it, dict):
                continue
            titles = [str(x or "").strip() for x in (it.get("multi_title") or [])]
            if title in titles:
                return True, str(it.get("item_id") or ""), ""
            if not any(titles) and str(it.get("word_number") or 0) in ("", "0"):
                ghosts.append((str(it.get("item_id") or ""),
                               int(it.get("create_time") or 0)))
        fresh = [g for g in ghosts if g[1] and (now - g[1]) <= 1800]
        if allow_ghost and fresh:
            return True, fresh[0][0], "领养30分钟内新建的无名空草稿（平台拒挂名的残片）"
        if ghosts and (fresh or allow_ghost):
            gid, ct = ghosts[0]
            when = time.strftime("%m-%d %H:%M", time.localtime(ct)) if ct else "未知时间"
            return False, "", (
                "番茄草稿箱按名未找到《%s》；发现无名空草稿 item_id=%s"
                "（创建于 %s，一键建被平台拒后的残片多半长这样）：确属本作品"
                "可在发布面板「登记已有作品」填该 ID 绑定" % (title, gid, when))
    probe = page.call(
        "(title)=>{const vis=e=>{const r=e.getBoundingClientRect();return r.width>0&&r.height>0};"
        "const titleVisible=[...document.querySelectorAll('a,button,[role=button],span,li,div,section')]."
        "some(e=>vis(e)&&(e.innerText||'').trim()===title);"
        "const create=[...document.querySelectorAll('button,a,[role=button],span')].some(e=>vis(e)&&"
        "(e.innerText||'').trim().includes('新建短故事'));"
        "return{ready:document.readyState==='complete'&&"
        "(location.href.includes('/short-manage')"
        "||location.href.includes('/short-draft')),"
        "create,titleVisible};}", title, 500)
    if probe and probe.get("ready") and probe.get("create"):
        if probe.get("titleVisible"):
            return False, "", "番茄已显示同名短故事，但无法从草稿箱行提取作品编号"
        return False, "", ""
    return False, "", "番茄短故事草稿箱未完整加载"


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
