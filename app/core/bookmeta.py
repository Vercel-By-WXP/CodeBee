# -*- coding: utf-8 -*-
"""作品信息一键生成：按发布平台（番茄/七猫）产出建书表单要填的开书资料。

用户在详情页「成果」分区点按钮触发（不做自动生成），后台线程调编排者模型
提炼 大纲 + 故事圣经 + 已有章节 的精华，按平台表单字段归一化后存任务
book_meta 字段，同时在工作目录落一份 Markdown 归档。降级链与大纲一致：
编排者 API → 作者 CLI → 模板兜底（模板只做搬运归纳，字段留待用户补）。
"""
from __future__ import annotations

import os
import time
from pathlib import Path

PLATFORMS = {
    "fanqie": {"label": "番茄", "file": "作品信息-番茄.md"},
    "qimao": {"label": "七猫", "file": "作品信息-七猫.md"},
}
# 单平台生成上限：编排者提炼一段简介级文本是轻任务，900s 足够含重试
BOOKMETA_TIMEOUT = 900
_FIRST_CH_HEAD = 1500   # 注入提示词的第一章开头字符数


def template_only():
    """只走模板兜底（跳过编排者/作者 CLI）：env TUTTI_BOOKMETA_TEMPLATE_ONLY=1。

    离线环境、不想为开书资料再花一次模型调用、以及测试要确定性输出时使用；
    产出仍是结构完整的字段，只是书名/简介直接取大纲与目标，其余留「待补充」。"""
    return str(os.environ.get("TUTTI_BOOKMETA_TEMPLATE_ONLY") or "").strip() in ("1", "true", "yes")


def needs_book_meta(task):
    """这本书是否需要开书资料：连载首批（start_chapter <= 1）才需要。

    开书资料属于「这本书」而不是某一批章节——续写批次沿用第一批的书名/简介/
    标签即可，重复生成只会覆盖成同内容的近义版本。非连载任务一律不需要。"""
    serial = (task or {}).get("serial")
    if not isinstance(serial, dict):
        return False
    try:
        return int(serial.get("start_chapter") or 1) <= 1
    except (TypeError, ValueError):
        return True


# 各平台建书表单的字段展示顺序（前端卡片与 Markdown 归档共用）。
# 番茄官方表单（2026-09 实抓）：签约模式=连载模式/完本模式；阅读标签=主分类(必选1)
# +主题/角色/情节(各≤2)；内容标签=情节(≤4)/情感(≤2)/人设(≤4)/世界观(≤1)。
# 七猫官方表单：一级→二级级联按频道；作品标签四组每组必选 1-3 个。
FIELD_LABELS = {
    "fanqie": [
        ("book_name", "作品名"), ("signing_mode", "签约模式"),
        ("target_reader", "目标读者"), ("category", "主分类"),
        ("tags_theme", "主题标签"), ("tags_role", "角色标签"),
        ("tags_plot", "情节标签"),
        ("content_plot", "内容·情节"), ("content_emotion", "内容·情感"),
        ("content_character", "内容·人设"), ("content_world", "内容·世界观"),
        ("protagonist_1", "主角名1"),
        ("protagonist_2", "主角名2"), ("summary", "作品简介"),
    ],
    "qimao": [
        ("book_name", "作品名称"), ("target_reader", "目标读者"),
        ("category_main", "一级分类"), ("category_sub", "二级分类"),
        ("tags_style", "风格标签"), ("tags_role", "角色标签"),
        ("tags_plot", "情节标签"), ("tags_bg", "背景标签"),
        ("protagonist_1", "主角名1"),
        ("protagonist_2", "主角名2"), ("status", "作品状态"),
        ("summary", "作品简介"),
    ],
}

PROMPT_HEAD = """你是网文编辑，熟悉 __PLATFORM__ 建书表单的填法。

__SKILLS__
请依据下方的小说素材（目标/故事圣经/大纲/已有章节开头），为这本书生成一套
「作品信息」，直接可用于在 __PLATFORM__ 创建作品。只输出一个 ```json 代码块，
不要输出其他内容。JSON 结构：
__SCHEMA__
硬性要求：
- 字段值必须贴合素材内容，不编造素材里没有的设定；简介不超过 500 字，突出卖点与钩子；
- 每个标签数组字段都必须至少给 1 个、并尽量贴着上限给满，只从对应组的选项表里选；
  任何一组留空数组、漏掉字段或自造表外词都算错误；
- __OPTIONS_RULE__
- 题材健康，无违规内容，符合平台签约调性。
"""

# 签约模式真实选项（番茄建书表单 2026-09 实抓）：连载模式 / 完本模式
FANQIE_MODES = ("连载模式", "完本模式")

FANQIE_SCHEMA = ('{"book_name": "作品名(15字内)", "signing_mode": "连载模式 或 完本模式", '
                 '"target_reader": "男频 或 女频", "category": "主分类(必选且只能选一个，从下方分类表选)", '
                 '"tags_theme": ["主题标签", …最多2个], "tags_role": ["角色标签", …最多2个], '
                 '"tags_plot": ["情节标签", …最多2个], '
                 '"content_plot": ["内容标签·情节", …最多4个], '
                 '"content_emotion": ["内容标签·情感", …最多2个], '
                 '"content_character": ["内容标签·人设", …最多4个], '
                 '"content_world": ["内容标签·世界观", …最多1个], '
                 '"protagonist_1": "主角名1(5字内)", '
                 '"protagonist_2": "主角名2(5字内，没有就空串)", "summary": "作品简介(500字内)"}')

QIMAO_SCHEMA = ('{"book_name": "作品名称(18字内)", "target_reader": "男生 或 女生", '
                '"category_main": "一级分类(从该频道一级分类表选一个)", '
                '"category_sub": "二级分类(必须是该一级下的二级)", '
                '"tags_style": ["风格标签", 必选1-3个], '
                '"tags_role": ["角色标签", 必选1-3个], '
                '"tags_plot": ["情节标签", 必选1-3个], '
                '"tags_bg": ["背景标签", 必选1-3个], '
                '"protagonist_1": "主角名1(5字内)", '
                '"protagonist_2": "主角名2(5字内，没有就空串)", "status": "连载中 或 已完结", '
                '"summary": "作品简介(200字内)"}')


def _fq_tables(reader):
    """番茄按目标读者取对应选项表（男频/女频两套完全独立的分类与标签）。"""
    from . import bookmeta_catalog as cat
    if reader == "女频":
        return (cat.FANQIE_FEMALE_CATEGORIES, cat.FANQIE_FEMALE_TAGS_THEME,
                cat.FANQIE_FEMALE_TAGS_ROLE, cat.FANQIE_FEMALE_TAGS_PLOT)
    return (cat.FANQIE_MALE_CATEGORIES, cat.FANQIE_MALE_TAGS_THEME,
            cat.FANQIE_MALE_TAGS_ROLE, cat.FANQIE_MALE_TAGS_PLOT)


def _fq_content_tables():
    """番茄内容标签四组（男女性共用）：情节/情感/人设/世界观。"""
    from . import bookmeta_catalog as cat
    return (cat.FANQIE_CONTENT_PLOT, cat.FANQIE_CONTENT_EMOTION,
            cat.FANQIE_CONTENT_CHARACTER, cat.FANQIE_CONTENT_WORLD)


def _fq_options_block(goal):
    """番茄提示词的真实选项注入：按 goal 推断读者给出对应分类与标签表。"""
    reader = "女频" if "女" in (goal or "") else "男频"
    cats, theme, role, plot = _fq_tables(reader)
    c_plot, c_emo, c_char, c_world = _fq_content_tables()
    return ("目标读者（%s）可用的主分类表（必选且只能选一个）：%s\n"
            "阅读标签三组各最多选 2 个——主题组：%s\n角色组：%s\n情节组（部分，选最贴合的）：%s\n"
            "内容标签四组——情节组（最多4个，部分）：%s\n情感组（最多2个）：%s\n"
            "人设组（最多4个，部分）：%s\n世界观组（最多1个）：%s"
            % (reader, "、".join(cats), "、".join(theme), "、".join(role),
               "、".join(plot[:50]), "、".join(c_plot[:60]), "、".join(c_emo),
               "、".join(c_char[:60]), "、".join(c_world)))


def _qm_options_block(goal):
    """七猫提示词的真实选项注入：按频道给级联 + 四组标签池（每组必选 1-3）。"""
    from . import bookmeta_catalog as cat
    channel = cat.QIMAO_CATS.get("女生" if "女" in (goal or "") else "男生")
    cascade = ["%s（二级：%s）" % (m, "、".join(v)) for m, v in channel.items()]
    pool = ["%s组（必选1-3个）：%s" % (g, "、".join(t)) for g, t in cat.QIMAO_TAG_GROUPS.items()]
    return ("一级分类表（选一个）：%s\n一级→二级级联（二级必须从所属一级下选）：%s\n"
            "作品标签四组：%s"
            % ("、".join(channel), "；".join(cascade), "；".join(pool)))


def _collect_material(task):
    """开书资料素材：目标 + 附件上下文 + 故事圣经 + 最近一次大纲 + 第一章开头。"""
    from . import store  # 惰性导入，同 planner：避免测试环境导入顺序问题
    parts = ["## 小说目标\n" + (task.get("goal") or "（无）")]
    ctx = (task.get("context") or "").strip()
    if ctx:
        parts.append("## 背景与上下文\n" + ctx[:2000])
    _, bible, _ = store.read_story_bible(task["id"])
    if bible:
        parts.append("## 故事圣经\n" + bible[:6000])
    outline = None
    for r in store.task_runs(task["id"]):
        o = r.get("outline")
        if o and o.get("chapters") and not o.get("degraded"):
            outline = o
            break
    if outline:
        lines = ["书名：%s" % (outline.get("book_title") or "（未定）")]
        for k, c in enumerate(outline["chapters"]):
            lines.append("第 %d 章《%s》：%s" % (k + 1, c.get("title", ""),
                                               str(c.get("beats") or "")[:120]))
        parts.append("## 已有大纲\n" + "\n".join(lines))
    head = ""
    try:
        p = Path(task.get("workdir") or "") / "chapter-001.md"
        if p.is_file():
            from . import runner
            head = runner.read_text_any_enc(p)[:_FIRST_CH_HEAD]
    except OSError:
        pass
    if head:
        parts.append("## 第一章开头\n" + head)
    return "\n\n".join(parts), outline


def _tags(v, limit=6):
    """（旧字段兜底保留）通用标签列表清洗：去重/去空/截断。"""
    if not isinstance(v, list):
        return []
    out = []
    for x in v:
        s = str(x).strip()[:12]
        if s and s not in out:
            out.append(s)
        if len(out) >= limit:
            break
    return out


def _name(v, limit):
    return str(v or "").strip().replace("\n", " ")[:limit]


def _summary(v):
    return str(v or "").strip()[:500]


def _in_table(v, table, limit=10):
    """值必须在平台真实选项表内；不在返回空串（宁缺勿错——填了假选项用户还得手动改）。"""
    s = _name(v, limit)
    return s if s in (table or []) else ""


def _filter_table(v, table, limit=6):
    """多选标签：只保留表内项（保持模型给的顺序），去重截断。"""
    if not isinstance(v, list):
        return []
    out = []
    for x in v:
        s = str(x).strip()[:12]
        if s and s in (table or []) and s not in out:
            out.append(s)
        if len(out) >= limit:
            break
    return out


def _norm(data, platform, goal=""):
    """规范化平台字段输出；不合规返回 None。

    分类/标签按平台真实选项表校验（bookmeta_catalog）：分类单选不在表内清空、
    标签多选只留表内项；七猫二级按级联校验（一级无级联记录时宽松放行）。"""
    if not isinstance(data, dict):
        return None
    book_name = _name(data.get("book_name"), 15 if platform == "fanqie" else 15)
    summary = _summary(data.get("summary"))
    if not book_name and not summary:
        return None
    if platform == "fanqie":
        reader = str(data.get("target_reader") or "").strip()
        if reader not in ("男频", "女频"):
            reader = "女频" if "女" in (goal or "") else "男频"
        mode = str(data.get("signing_mode") or "").strip()
        if mode not in FANQIE_MODES:
            mode = "连载模式"
        cats, theme, role, plot = _fq_tables(reader)
        c_plot, c_emo, c_char, c_world = _fq_content_tables()
        # 兼容旧字段：read_tags/content_tags 并入情节/主题组（老数据不丢）
        legacy_read = data.get("read_tags") if isinstance(data.get("read_tags"), list) else []
        return {"book_name": book_name, "signing_mode": mode, "target_reader": reader,
                "category": _in_table(data.get("category"), cats),
                "tags_theme": _filter_table(data.get("tags_theme"), theme, 2),
                "tags_role": _filter_table(data.get("tags_role"), role, 2),
                "tags_plot": _filter_table(data.get("tags_plot"), plot, 2),
                "content_plot": _filter_table(data.get("content_plot"), c_plot, 4),
                "content_emotion": _filter_table(data.get("content_emotion"), c_emo, 2),
                "content_character": _filter_table(data.get("content_character"), c_char, 4),
                "content_world": _filter_table(data.get("content_world"), c_world, 1),
                "legacy_read_tags": _filter_table(legacy_read, plot + theme + role),
                "protagonist_1": _name(data.get("protagonist_1"), 5),
                "protagonist_2": _name(data.get("protagonist_2"), 5),
                "summary": summary}
    from . import bookmeta_catalog as cat
    reader = str(data.get("target_reader") or "").strip()
    if reader not in ("男生", "女生"):
        reader = "女生" if "女" in (goal or "") else "男生"
    status = str(data.get("status") or "").strip()
    if status not in ("连载中", "已完结"):
        status = "连载中"
    channel = cat.QIMAO_CATS.get(reader) or {}
    c_main = _in_table(data.get("category_main"), list(channel))
    c_sub = _name(data.get("category_sub"), 10)
    if c_sub and not (c_main and c_sub in (channel.get(c_main) or [])):
        # 一级/二级不自洽（一级空、或二级不属于该频道——如女生专属的
        # 「现实故事」配了男生频道）：跨频道反查官方目录纠正，频道与
        # 一级跟着二级走（表单级联的选项集以此为准）；全目录都没有
        # 才清空——表单选不出的词不硬填
        loc = cat.qimao_locate_sub(c_sub)
        if loc:
            reader, c_main = loc
        else:
            c_sub = ""
    # 作品标签四组（官方每组必选 1-3）：逐组过滤 + 截到 3；老数据 tags 并入
    legacy_tags = data.get("tags") if isinstance(data.get("tags"), list) else []
    groups = {}
    for key, gname in (("tags_style", "风格"), ("tags_role", "角色"),
                       ("tags_plot", "情节"), ("tags_bg", "背景")):
        got = _filter_table(data.get(key), cat.QIMAO_TAG_GROUPS[gname], 3)
        if len(got) < 3 and legacy_tags:
            for t in _filter_table(legacy_tags, cat.QIMAO_TAG_GROUPS[gname], 3):
                if t not in got:
                    got.append(t)
                    if len(got) >= 3:
                        break
        groups[key] = got
    return {"book_name": book_name, "target_reader": reader,
            "category_main": c_main, "category_sub": c_sub,
            "tags_style": groups["tags_style"], "tags_role": groups["tags_role"],
            "tags_plot": groups["tags_plot"], "tags_bg": groups["tags_bg"],
            "protagonist_1": _name(data.get("protagonist_1"), 5),
            "protagonist_2": _name(data.get("protagonist_2"), 5),
            "status": status, "summary": summary}


def _template_meta(task, platform, outline):
    """兜底模板：只做素材搬运归纳（书名/简介来自大纲与目标），其余留空待补。

    标签/分类尽力而为：扫描素材文本（goal/圣经/大纲书名）里恰好命中平台
    选项表的词就带上——模板没有模型理解力，不做语义归纳，命中多少算多少，
    其余留「待补充」让用户手选（宁缺勿错，不填表外词）。"""
    goal = task.get("goal") or ""
    book_name = str((outline or {}).get("book_title") or task.get("title") or "").strip()
    summary = goal.splitlines()[0][:500] if goal.strip() else ""
    if not summary:
        summary = book_name or "（待补充）"    # 全空素材也要有返回，_norm 才不会拒绝
    material = _material_text(task, outline)
    data = {"book_name": book_name, "summary": summary}
    if platform == "fanqie":
        reader = "女频" if "女" in goal else "男频"
        cats, theme, role, plot = _fq_tables(reader)
        c_plot, c_emo, c_char, c_world = _fq_content_tables()
        data.update({"target_reader": reader,
                     "category": _scan_table(material, cats, 1),
                     "tags_theme": _scan_table(material, theme, 2),
                     "tags_role": _scan_table(material, role, 2),
                     "tags_plot": _scan_table(material, plot, 2),
                     "content_plot": _scan_table(material, c_plot, 4),
                     "content_emotion": _scan_table(material, c_emo, 2),
                     "content_character": _scan_table(material, c_char, 4),
                     "content_world": _scan_table(material, c_world, 1)})
    else:
        from . import bookmeta_catalog as cat
        reader = "女生" if "女" in goal else "男生"
        data["target_reader"] = reader
        for main, subs in (cat.QIMAO_CATS.get(reader) or {}).items():
            if main in material:
                data["category_main"] = main
                for s in subs:               # 二级名撞素材即选（如「宫闱宅斗」）
                    if s in material:
                        data["category_sub"] = s
                        break
                break
        for key, gname in (("tags_style", "风格"), ("tags_role", "角色"),
                           ("tags_plot", "情节"), ("tags_bg", "背景")):
            data[key] = _scan_table(material, cat.QIMAO_TAG_GROUPS[gname], 3)
    return _norm(data, platform, goal)


def _scan_table(text, table, limit):
    """素材文本里命中的选项表词（按表序取，保证真实存在）。"""
    if not text:
        return []
    out = []
    for word in table:
        if len(word) >= 2 and word in text:
            out.append(word)
            if len(out) >= limit:
                break
    return out


def _material_text(task, outline):
    """标签词面扫描用的素材文本：goal + 圣经 + 大纲书名（模板兜底同源）。"""
    material = task.get("goal") or ""
    try:
        _, bible, _ = store_read_bible(task)
        material += "\n" + (bible or "")
    except Exception:
        pass
    if outline:
        material += "\n" + str(outline.get("book_title") or "")
    return material


def _fill_tag_gaps(task, platform, meta, outline):
    """llm 归一化后仍为空的标签组：用素材词面扫描补一轮（命中多少算多少）。

    模型偶尔整组漏给（实测 codex-cli 只填了内容标签前两组）；空着用户就得
    手选，扫描命中的是官方表内词，补上好过留白——已有的组不动，不覆盖模型。"""
    material = _material_text(task, outline)
    if not material:
        return meta
    goal = task.get("goal") or ""
    if platform == "fanqie":
        reader = meta.get("target_reader") or ("女频" if "女" in goal else "男频")
        cats, theme, role, plot = _fq_tables(reader)
        c_plot, c_emo, c_char, c_world = _fq_content_tables()
        pairs = [("tags_theme", theme, 2), ("tags_role", role, 2), ("tags_plot", plot, 2),
                 ("content_plot", c_plot, 4), ("content_emotion", c_emo, 2),
                 ("content_character", c_char, 4), ("content_world", c_world, 1)]
    else:
        from . import bookmeta_catalog as cat
        pairs = [(k, cat.QIMAO_TAG_GROUPS[g], 3) for k, g in
                 (("tags_style", "风格"), ("tags_role", "角色"),
                  ("tags_plot", "情节"), ("tags_bg", "背景"))]
    for key, table, limit in pairs:
        if not meta.get(key):
            meta[key] = _scan_table(material, table, limit)
    return meta


def store_read_bible(task):
    """圣经读取的小包装（模板兜底用；异常向上抛由调用方吞掉）。"""
    from . import store
    fp, text, err = store.read_story_bible(task.get("id") or "")
    if err:
        raise ValueError(err)
    return fp, text, None


def _resolve_author(task):
    """作者 CLI 兜底用：按「实现」角色选一个 real 智能体（同连载起草的路由口径）。"""
    try:
        from . import catalog, manager, registry, router
        agents = registry.effective_agents(catalog.load(), manager.detect_all())
        agent, _ = router.pick([a for a in agents if a.get("mode") == "real"],
                               "implement", task.get("type") or "serial_novel")
        return agent
    except Exception:
        return None


def make_book_meta(task, platform, author_agent=None, log_path=None):
    """生成一个平台的作品信息。返回 (meta dict, source 字符串)；模板兜底永远有返回。"""
    from . import knowledge, modelhub, planner, runner, skills  # 惰性导入，同 planner
    material, outline = _collect_material(task)
    schema = {"fanqie": FANQIE_SCHEMA, "qimao": QIMAO_SCHEMA}.get(platform) or ""
    plat_label = PLATFORMS[platform]["label"]
    sk_block, _ = skills.block_for(task)
    kb_block = knowledge.block_for(task)
    if kb_block:
        sk_block = (sk_block + "\n\n" + kb_block) if sk_block else kb_block
    goal = task.get("goal") or ""
    # 真实选项注入：分类/标签表按平台与读者频段给全（模型只能从表里选）
    options = (_fq_options_block(goal) if platform == "fanqie" else _qm_options_block(goal))
    prompt = (PROMPT_HEAD.replace("__PLATFORM__", plat_label)
              .replace("__SKILLS__", sk_block)
              .replace("__SCHEMA__", schema)
              .replace("__OPTIONS_RULE__", options) + "\n\n## 小说素材\n" + material)

    orch = None
    if not template_only():
        try:
            orch = modelhub.resolve_orchestrator()
        except Exception:
            orch = None
    orch_err = ""
    if orch:
        prov, model = orch
        res = modelhub.chat(prov["id"], model, prompt, max_tokens=4000, timeout=300)
        planner._log_usage("bookmeta", "bookmeta", task, res, model=model,
                           provider=prov.get("name", prov.get("id", "")),
                           provider_id=prov.get("id", ""))
        if res["ok"]:
            meta = _norm(runner.extract_json(res.get("text") or ""), platform, goal)
            if meta:
                return _fill_tag_gaps(task, platform, meta, outline), \
                    "编排者(%s · %s)" % (prov.get("name", prov["id"]), model)
        orch_err = str(res.get("error") or "返回内容无法解析为作品信息")[:200]

    if author_agent and author_agent.get("mode") == "real" and not template_only():
        res = runner.run_agent(modelhub.bind_agent(author_agent), prompt,
                               workdir=task.get("workdir"), readonly=True,
                               timeout=BOOKMETA_TIMEOUT, log_path=log_path)
        planner._log_usage("bookmeta", "bookmeta", task, res, agent=author_agent)
        if res["ok"]:
            meta = _norm(runner.extract_json(res.get("text") or ""), platform, goal)
            if meta:
                return _fill_tag_gaps(task, platform, meta, outline), "llm(%s)" % author_agent["id"]
        orch_err = orch_err or str(res.get("error") or "")[:200]

    meta = _template_meta(task, platform, outline)
    meta["source"] = ("模板兜底（编排者不可用：%s）" % orch_err) if orch_err else "模板兜底"
    return meta, meta["source"]


def render_markdown(task, platform, meta):
    """落工作目录的归档 Markdown（与前端弹框同一份字段顺序）。"""
    plat = PLATFORMS[platform]["label"]
    lines = ["# 作品信息（%s）" % plat, "",
             "> 任务：%s　生成于 %s　来源：%s" % (
                 task.get("title") or task.get("id") or "",
                 time.strftime("%Y-%m-%d %H:%M"), meta.get("source") or "")]
    lines.append("")
    for key, label in FIELD_LABELS[platform]:
        v = meta.get(key)
        if isinstance(v, list):
            v = "、".join(v) if v else "（待补充）"
        elif key == "summary":
            v = (v or "").strip() or "（待补充）"
        else:
            v = str(v or "").strip() or "（待补充）"
        lines.append("- **%s**：%s" % (label, v))
    lines.append("")
    lines.append("> 各字段可直接复制进 %s 建书表单；分类与标签均选自平台真实选项表。" % plat)
    return "\n".join(lines)


def generate_async(task_id, platform, author_agent=None):
    """后台线程入口：跑生成并把终态写回任务 book_meta；成功时落工作目录归档。

    状态机：running（路由置入）→ done / failed。任何异常都落 failed，不留悬挂。"""
    from . import store
    task = store.get_task(task_id)
    if not task:
        return
    try:
        meta, source = make_book_meta(task, platform, author_agent=author_agent)
        entry = {"status": "done", "data": meta, "source": source,
                 "at": time.strftime("%Y-%m-%d %H:%M:%S")}
        fp = Path(task.get("workdir") or "") / PLATFORMS[platform]["file"]
        if str(fp.parent) and fp.parent.is_dir():
            fp.write_text(render_markdown(task, platform, meta), encoding="utf-8")
            entry["file"] = PLATFORMS[platform]["file"]
    except Exception as e:                       # 兜底线程不能静默死掉
        entry = {"status": "failed", "error": str(e)[:300],
                 "at": time.strftime("%Y-%m-%d %H:%M:%S")}
    cur = store.get_task(task_id)
    if not cur:
        return
    prev = (cur.get("book_meta") or {}).get(platform) or {}
    if prev.get("status") != "running":          # 期间被删/被重置：丢弃结果
        return
    store.set_book_meta(task_id, platform, entry)


def recover_orphans():
    """启动收尸：后台生成线程随进程重启死掉后，落盘的 running 无人写终态——
    前端永远「生成中」（按钮禁用）+ 接口幂等拒绝，卡死不可自愈。启动时把
    遗留 running 统一改判 failed，交还「重试」按钮。返回改判条数。"""
    from . import store
    n = 0
    for task in store.list_tasks(limit=10 ** 9):
        for plat, entry in (task.get("book_meta") or {}).items():
            if isinstance(entry, dict) and entry.get("status") == "running":
                store.set_book_meta(task["id"], plat, {
                    "status": "failed",
                    "error": "生成随服务重启中断，请点重试",
                    "at": time.strftime("%Y-%m-%d %H:%M:%S")})
                n += 1
    return n
