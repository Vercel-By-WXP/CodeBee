# -*- coding: utf-8 -*-
"""手动发布包：把机器人会填的东西，整理成作者能直接粘贴的材料。

发布链是 CDP 模拟点平台页面，平台一次改版就全线打不通；而这时作者已经
写完三万字。重试按钮救不了这种情况——唯一保命的出口是把按键要填的东西
原样交到作者手里，让他自己发一次。

三条底线（写这个模块的全部理由）：

1. **正文切法与真实发布链同一套逻辑**。短篇分节直接复用
   ``manager._story_body_with_sections``，不另写一份：手工发和机器人发
   必须切得一模一样，否则七猫「每章 500~1500 字」的硬校验会把稿子拦下。
2. **拿不到就写拿不到**，绝不编。作品信息没生成就说没生成，简介差几个字
   就说差几个字——作者拿着这份材料是要去平台过校验的，编一个字数会让他
   在建书表单前反复试错。
3. **只读**。不写盘、不连平台、不起浏览器。这是保命出口，不能自己再出
   故障，所以整条路径只有文件读取和字符串拼接。
"""
from __future__ import annotations

import re
import time

PLATFORM_LABEL = {"fanqie": "番茄", "qimao": "七猫"}

# 短篇分节规格：只写有实机依据的硬约束。
# 七猫：发布硬校验「≥2 个章节标题」且每章 500~1500 字（2026-10-22 实案：
# 两章各 2300 字被拦，4 章 ×~1190 字通过）——这是硬的，标了就必须守。
# 番茄：短故事是单文档，分节只是阅读分隔，平台不按章节校验，所以只给
# 建议值不给下限（标 soft，界面不报错，只在过长时提醒）。
_SHORT_BLOCK = {
    "fanqie": {"target": 1500, "min": 0, "max": 0, "hard": False},
    "qimao": {"target": 1200, "min": 500, "max": 1500, "hard": True},
}

_MARKS = ["一", "二", "三", "四", "五", "六", "七", "八", "九", "十"]
_MARK_SET = set(_MARKS)

_FIELD_ORDER = {
    "fanqie": [("book_name", "书名"), ("summary", "简介"), ("category", "分类"),
               ("tags_theme", "主题标签"), ("tags_role", "角色标签"),
               ("tags_plot", "情节标签"), ("protagonist_1", "主角"),
               ("protagonist_2", "配角"), ("signing_mode", "签约模式")],
    "qimao": [("book_name", "书名"), ("summary", "简介"), ("target_reader", "频道"),
              ("category_main", "一级分类"), ("category_sub", "二级分类"),
              ("tags_style", "风格标签"), ("tags_role", "角色标签"),
              ("tags_plot", "情节标签"), ("tags_bg", "背景标签")],
}

_REQUIRED = {
    # 番茄短故事只锁书名：名称/正文在编辑器里，建书表单不吃简介分类
    # （publish_create._create_preflight 同口径）。
    ("fanqie", "short"): {"book_name"},
    ("fanqie", "serial"): {"book_name", "summary", "category",
                           "tags_theme", "tags_role", "tags_plot"},
    ("qimao", "short"): {"book_name", "summary", "category_main", "category_sub",
                         "tags_style", "tags_role", "tags_plot", "tags_bg"},
    ("qimao", "serial"): {"book_name", "summary", "category_main", "category_sub",
                          "tags_style", "tags_role", "tags_plot", "tags_bg"},
}

_SUMMARY_LIMITS = {"fanqie": (50, 500)}      # 建书表单硬校验（实际抓到过）


def _now():
    return time.strftime("%Y-%m-%d %H:%M:%S")


def _is_serial(task):
    """连载：与 novel.js 的 isSerial 同口径（页面按钮据此分流，别各判一套）。"""
    task = task or {}
    return (str(task.get("type") or "") == "serial_novel"
            or bool((task.get("serial") or {}).get("chapters")))


def _count(text):
    from .. import writing
    return writing.count_text(text)


def _read(path):
    from .. import runner
    try:
        return runner.read_text_any_enc(path)
    except Exception:
        return ""


def _split_blocks(sectioned):
    """按「一/二/…」独立行切成分节。返回 [(label, text)]，空节丢弃。"""
    blocks, label, lines = [], "", []
    for ln in (sectioned or "").splitlines():
        s = ln.strip()
        if s in _MARK_SET:
            if label or any(x.strip() for x in lines):
                blocks.append((label, "\n".join(lines).strip("\n")))
            label, lines = s, []
        else:
            lines.append(ln)
    if label or any(x.strip() for x in lines):
        blocks.append((label, "\n".join(lines).strip("\n")))
    return [(lab, txt) for lab, txt in blocks if txt.strip()]


def _split_even(text, target):
    """把一段均分成若干片，每片 ≤ target。行边界优先，超长单行按字符硬切。

    为什么不贪心装满：1600 字按「装满 1500」会切成 1500+100，后一片直接
    卡在七猫 500 字下限上——作者拿到手还得自己再分一次。均分才不会把
    读者的一段话切在中间，也不会造出不合格的碎片。
    """
    total = _count(text)
    if total <= target:
        return [text]
    k = max(2, -(-total // target))
    want = max(1, total // k)
    lines = text.splitlines()
    longest = max((_count(x) for x in lines), default=0)
    if longest > want:
        flat = "".join(lines)
        return [flat[i:i + want] for i in range(0, len(flat), want)
                if flat[i:i + want].strip()]
    out, buf, cum = [], [], 0
    for ln in lines:
        if buf and cum >= want * (len(out) + 1):
            out.append("\n".join(buf).strip("\n"))
            buf = []
        buf.append(ln)
        cum += _count(ln)
    if buf:
        out.append("\n".join(buf).strip("\n"))
    return [x for x in out if x.strip()]


def _label(i):
    return _MARKS[i] if i < len(_MARKS) else str(i + 1)


def _short_chapters(task, plat):
    """短篇：稿件 → 分节 → 规整成 [{no,label,title,text,words,ok,problem}]。"""
    from . import manager as pub
    import os
    workdir = str(task.get("workdir") or "")
    name = os.path.basename(str(task.get("manuscript") or "").strip() or "story.md")
    path = os.path.join(workdir, name) if workdir else ""
    if not path or not os.path.isfile(path):
        return [], "稿件文件 %s 不存在（工作目录：%s）" % (name, workdir or "未设置")
    _no, _title, body, err = pub.read_chapter(path)
    if err:
        return [], err
    if not body.strip():
        return [], "稿件 %s 是空的" % name
    spec = _SHORT_BLOCK[plat]
    sectioned = pub._story_body_with_sections(body)
    raw = _split_blocks(sectioned)
    texts = []
    for _lab, txt in raw:
        if spec["hard"] and spec["max"] and _count(txt) > spec["max"]:
            texts.extend(_split_even(txt, spec["max"]))
        else:
            texts.append(txt)
    # 七猫硬校验「≥2 个章节标题」：只有一节时对半切，别让作者拿回去自己分。
    if spec["hard"] and len(texts) < 2 and texts and \
            _count(texts[0]) >= 2 * spec["min"]:
        texts = _split_even(texts[0], max(spec["min"], _count(texts[0]) // 2))
    rows = []
    # 短故事的「章」不是真章节：番茄一个正文框（只分节），七猫要求每节当章节
    # 标题——界面上的叫法必须跟着平台走，否则作者会去找一个平台上不存在的字段。
    unit = "章" if plat == "qimao" else "节"
    for i, txt in enumerate(texts):
        words = _count(txt)
        problem = ""
        if spec["hard"]:
            if words > spec["max"]:
                problem = "本章 %d 字，超出平台单章上限 %d 字" % (words, spec["max"])
            elif words < spec["min"]:
                problem = "本章 %d 字，未达平台单章下限 %d 字" % (words, spec["min"])
        rows.append({"no": i + 1, "label": _label(i),
                     "title": "第 %d %s" % (i + 1, unit),
                     "text": txt, "words": words,
                     "ok": not problem, "problem": problem})
    return rows, ""


def _serial_chapters(task):
    """连载：工作目录里的 chapter-NN.md 逐章取标题与正文（与发章链同解析）。"""
    from . import manager as pub
    import os
    workdir = str(task.get("workdir") or "")
    if not workdir or not os.path.isdir(workdir):
        return [], "工作目录不存在：%s" % (workdir or "未设置")
    files = []
    for name in os.listdir(workdir):
        m = re.match(r"^chapter-(\d+)\.md$", name, re.I)
        if m:
            files.append((int(m.group(1)), os.path.join(workdir, name)))
    if not files:
        return [], "工作目录里没有 chapter-NN.md 章文件（还没写出章节？）"
    files.sort()
    rows, used = [], set()
    for no, path in files:
        ch_no, title, body, err = pub.read_chapter(path)
        if err or not body.strip():
            continue
        # 章号重复（如 chapter-01 与 chapter-1 并存）只取第一个，避免发重章
        key = ch_no or no
        if key in used:
            continue
        used.add(key)
        rows.append({"no": key, "label": "", "title": title or ("第 %d 章" % key),
                     "text": body, "words": _count(body), "ok": True, "problem": ""})
    return rows, ""


def _fmt_value(v):
    if isinstance(v, (list, tuple)):
        vals = [str(x).strip() for x in v if str(x).strip()]
        return "、".join(vals)
    return str(v or "").strip()


def _book_fields(task, plat, short):
    """建书字段：取整条连载链共用的那一份（与建书链同源）。

    没生成过作品信息时**不铺 8 行「必填但为空」**：那不是 8 个待办，是一件事
    ——作者去点「生成作品信息」，或者自己照着平台表单填。列 8 行只会让他以为
    系统坏了一排，所以压成一条说明 + 书名（至少书名是现成的）。"""
    from .. import store
    meta = store.inherited_book_meta(task, plat) or {}
    data = meta.get("data") or {}
    state = {
        "status": str(meta.get("status") or "none"),
        "at": str(meta.get("at") or ""),
        "source": str(meta.get("source") or ""),
        "generated": bool(data),
    }
    if not data:
        title = str(task.get("title") or "").strip()
        rows = [{"key": "book_name", "label": "书名", "value": title,
                 "required": True, "ok": bool(title),
                 "problem": "" if title else "必填但为空"}]
        missing = ["作品信息还没生成（%s）：书名/简介/分类/标签 要么回 CodeBee 点"
                   "「生成作品信息」，要么照着平台表单自己写"
                   % PLATFORM_LABEL[plat]]
        return rows, missing, state

    required = _REQUIRED.get((plat, "short" if short else "serial"), {"book_name"})
    lo, hi = _SUMMARY_LIMITS.get(plat, (0, 0))
    rows, missing = [], []
    for key, label in _FIELD_ORDER[plat]:
        value = _fmt_value(data.get(key))
        need = key in required
        problem = ""
        if not value and need:
            problem = "必填但为空"
        elif key == "summary" and value and lo and need:
            # 只在该平台真的校验简介长度时才报（番茄短故事建书不吃简介，
            # 拿 50 字下限去吓作者是错的）。
            n = len(value)
            if n < lo or n > hi:
                problem = "简介 %d 字，平台要求 %d-%d 字" % (n, lo, hi)
        if problem:
            missing.append("%s（%s）" % (label, problem))
        rows.append({"key": key, "label": label, "value": value,
                     "required": need, "ok": not problem, "problem": problem})
    return rows, missing, state


def _steps(plat, short, book, cover_path):
    """手动发布的操作清单——按平台真实发布链的顺序写，不按功能菜单写。"""
    title = book.get("title") or "书名"
    cat = book.get("category") or ""
    if plat == "fanqie" and short:
        return [
            "打开番茄短故事管理页（%s）并登录" % "https://fanqienovel.com/main/writer/short-manage",
            "在管理页点「新建短故事」进入编辑器——不要在浏览器地址栏直接开编辑器，"
            "从管理页点进去平台才会立刻建档",
            "「短故事名称」填：%s" % title,
            "正文按顺序粘贴：编辑器只有一个正文框，第 1 章的「一」标记要单独占一行，"
            "后面各章接着粘",
            "上传封面%s" % ("（本地文件：%s）" % cover_path if cover_path
                            else "（还没有封面，先在 CodeBee 里生成，或自己上传一张）"),
            "「是否使用 AI」选「是」",
            "作品分类选：%s" % (cat or "（未生成，按内容自己选）"),
            "设置试读比例 30%——不设置会拦住「下一步」",
            "勾选发布协议；首次勾选会弹说明，点「我已阅读并同意」",
            "点「下一步」→「继续发布」→「确定」，等智能纠错跑完（1-2 分钟）",
            "回短故事管理页，确认书名后面显示「审核中」",
            "如果中途弹出「有刚刚更新的草稿」，选放弃/丢弃——选「继续编辑」会把"
            "本次粘贴的正文顶掉",
        ]
    if plat == "qimao" and short:
        return [
            "打开七猫短故事管理页（%s）并登录" % "https://zuozhe.qimao.com/front/book-manage/short",
            "建作品：书名填「%s」，简介按上面那段粘贴，频道/一级/二级/四组标签按上面列的点选"
            % title,
            "进编辑器粘贴正文；把每章的「一/二/三」标记行设成「章节标题」（格式里的标题块）"
            "——七猫硬校验 ≥2 个章节标题、每章 500~1500 字",
            "等页面出现「已保存到云端」",
            "点「提交」→「立即发布」",
            "回短故事管理页，确认这本显示「待审核/审核中/已发布」",
        ]
    return [
        "打开%s作者后台（%s）并登录" % (PLATFORM_LABEL[plat],
                                    "https://fanqienovel.com/main/writer/"
                                    if plat == "fanqie" else "https://zuozhe.qimao.com/"),
        "没有这本书就先建作品：书名「%s」，简介/分类/标签按上面那段填" % title,
        "进「章节管理」→「新建章节」",
        "每章分别粘贴：章节标题填下面的章名，正文粘该章内容（单章 ≥1000 字）",
        "逐章提交发布；定时发布按平台设置，不影响先发首章",
        "回章节管理页核对章数与状态，确认没有漏章",
    ]


def _urls(plat, task):
    from . import manager as pub
    cfg = getattr(pub.PLATFORMS.get(plat), "CONFIG", {}) or {}
    out = {"home": cfg.get("home") or ""}
    if plat == "fanqie":
        out["manage"] = cfg.get("short_manage") or ""
        out["create"] = cfg.get("short_create_url") or ""
    else:
        out["manage"] = (cfg.get("short_book_manage") or cfg.get("book_manage") or "")
        out["create"] = cfg.get("short_create_url") or ""
    return {k: v for k, v in out.items() if v}


def build(task, platform="fanqie"):
    """组装手动发布包。返回可直接给界面/导出的结构（全部为纯文本字段）。"""
    task = task or {}
    plat = platform if platform in PLATFORM_LABEL else "fanqie"
    short = not _is_serial(task)
    title = str(task.get("title") or "").strip() or "未命名作品"

    from .. import store
    book_rows, missing, book_state = _book_fields(task, plat, short)
    book_title = next((r["value"] for r in book_rows
                       if r["key"] == "book_name" and r["value"]), "")
    if book_title:
        title = book_title
    book = {"title": title,
            "summary": next((r["value"] for r in book_rows
                             if r["key"] == "summary"), ""),
            "category": next((r["value"] for r in book_rows
                              if r["key"] in ("category", "category_sub")), ""),
            "fields": book_rows,
            "state": book_state}

    if short:
        chapters, err = _short_chapters(task, plat)
    else:
        chapters, err = _serial_chapters(task)
    if err:
        missing.append(err)

    cover_path = ""
    if short:
        try:
            from . import manager as pub
            cover_path = pub.short_cover_path(task)
        except Exception:
            cover_path = ""

    body_min = 0
    if short:
        from . import manager as pub
        cfg = getattr(pub.PLATFORMS.get(plat), "CONFIG", {}) or {}
        body_min = int(cfg.get("short_story_body_min_chars") or 0)
    total_words = sum(c["words"] for c in chapters)
    if body_min and total_words and total_words < body_min:
        missing.append("正文共 %d 字，未达该平台短故事下限 %d 字"
                       % (total_words, body_min))
    if not chapters:
        missing.append("没有可导出的正文")

    spec = dict(_SHORT_BLOCK[plat] if short
                else {"target": 0, "min": 1000, "max": 20000, "hard": False})
    spec["body_min"] = body_min
    payload = {
        "task_id": str(task.get("id") or ""),
        "platform": plat,
        "platform_label": PLATFORM_LABEL[plat],
        "kind": "short" if short else "serial",
        # 同为「短故事」，两平台的校验口径不一样：番茄是单文档（只分节），
        # 七猫按章节硬校验 —— 标题上就得说清，否则作者会照番茄的做法去发七猫。
        "kind_label": (("短故事 · 单文档" if plat == "fanqie" else "短故事 · 分章校验")
                       if short else "连载（按章）"),
        "title": title,
        "ready": not missing,
        "missing": missing,
        "book": book,
        "chapters": chapters,
        "cover": cover_path,
        "stats": {"chapters": len(chapters), "words": total_words},
        "spec": spec,
        "steps": _steps(plat, short, book, cover_path),
        "urls": _urls(plat, task),
        "generated_at": _now(),
    }
    # 纯文本只在下载时渲染：JSON 里各章正文已经有一份，再塞一份全文会让
    # 三万字的稿子变成两倍体积（约 120KB），界面不需要。
    return payload


def render_text(payload):
    """把发布包渲染成一份纯文本（可直接复制、可存成 .txt 交给作者）。"""
    p = payload or {}
    L = []
    L.append("【CodeBee 手动发布包】《%s》 · %s · %s"
             % (p.get("title") or "", p.get("platform_label") or "",
                p.get("kind_label") or ""))
    L.append("生成时间：%s        正文合计：%d 章 / %d 字"
             % (p.get("generated_at") or "", (p.get("stats") or {}).get("chapters", 0),
                (p.get("stats") or {}).get("words", 0)))
    if p.get("missing"):
        L.append("待补齐：")
        for x in p["missing"]:
            L.append("  · %s" % x)
    else:
        L.append("材料齐备：下面的字段与正文可以直接粘贴到平台表单。")
    L.append("")

    L.append("══ 一、建书资料 ══")
    state = (p.get("book") or {}).get("state") or {}
    if not state.get("generated"):
        L.append("（作品信息未生成，下面只有书名可用）")
    for row in (p.get("book") or {}).get("fields") or []:
        val = row.get("value") or ""
        if not val:
            if row.get("required"):
                L.append("%s：（必填，还没有）" % row["label"])
            continue
        n = len(val)
        L.append("%s（%d 字）：" % (row["label"], n) if row.get("key") == "summary"
                 else "%s：%s" % (row["label"], val))
        if row.get("key") == "summary":
            L.append(val)
        if row.get("problem"):
            L.append("  ！%s" % row["problem"])
    if p.get("cover"):
        L.append("封面文件：%s" % p["cover"])
    L.append("")

    L.append("══ 二、正文 ══")
    spec = p.get("spec") or {}
    if p.get("kind") == "short" and spec.get("hard"):
        L.append("（平台规格：每章 %s~%s 字，至少 2 章；下面已按此切好）"
                 % (spec.get("min"), spec.get("max")))
    elif p.get("kind") == "short":
        L.append("（平台规格：整篇 %s 字起；分节只是阅读分隔，每节约 %s 字，"
                 "可按发布效果自行合并）"
                 % (spec.get("body_min") or "1000", spec.get("target") or "1500"))
    else:
        L.append("（平台规格：单章 ≥1000 字）")
    L.append("")
    for ch in p.get("chapters") or []:
        head = "▷ 第 %s 章 · %s" % (ch.get("no"), ch.get("title") or "")
        L.append("%s（%d 字）" % (head, ch.get("words") or 0))
        if ch.get("problem"):
            L.append("  ！%s" % ch["problem"])
        L.append(ch.get("text") or "")
        L.append("")
        L.append("────────────────────────────")
        L.append("")

    L.append("══ 三、手动发布步骤（%s）══" % (p.get("platform_label") or ""))
    for i, s in enumerate(p.get("steps") or [], 1):
        L.append("%d. %s" % (i, s))
    urls = p.get("urls") or {}
    if urls.get("manage"):
        L.append("")
        L.append("管理页：%s" % urls["manage"])
    return "\n".join(L)


def filename(payload):
    """导出文件名：书名 + 平台 + 时间，去掉 Windows 非法字符。"""
    title = re.sub(r'[\\/:*?"<>|\r\n\t]+', "_", str((payload or {}).get("title") or "作品"))
    plat = str((payload or {}).get("platform_label") or "")
    return "%s-%s-手动发布包-%s.txt" % (title[:40], plat, time.strftime("%Y%m%d-%H%M"))
