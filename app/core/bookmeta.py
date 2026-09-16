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


# 各平台建书表单的字段展示顺序（前端弹框与 Markdown 归档共用）
FIELD_LABELS = {
    "fanqie": [
        ("book_name", "作品名"), ("signing_mode", "签约模式"),
        ("target_reader", "目标读者"), ("read_tags", "阅读标签"),
        ("content_tags", "内容标签"), ("protagonist_1", "主角名1"),
        ("protagonist_2", "主角名2"), ("summary", "作品简介"),
    ],
    "qimao": [
        ("book_name", "作品名称"), ("target_reader", "目标读者"),
        ("category_main", "一级分类"), ("category_sub", "二级分类"),
        ("tags", "作品标签"), ("protagonist_1", "主角名1"),
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
- 字段值必须贴合素材内容，不编造素材里没有的设定；简介 50-500 字，突出卖点与钩子；
- 标签选平台热门且与题材匹配的，不要堆砌；
- 题材健康，无违规内容，符合平台签约调性。
"""

FANQIE_SCHEMA = ('{"book_name": "作品名(15字内)", "signing_mode": "连载模式 或 完本模式", '
                 '"target_reader": "男频 或 女频", "read_tags": ["阅读标签", …最多6个], '
                 '"content_tags": ["内容标签", …最多6个], "protagonist_1": "主角名1(5字内)", '
                 '"protagonist_2": "主角名2(5字内，没有就空串)", "summary": "作品简介(50-500字)"}')

QIMAO_SCHEMA = ('{"book_name": "作品名称(18字内)", "target_reader": "男生 或 女生", '
                '"category_main": "一级分类", "category_sub": "二级分类", '
                '"tags": ["作品标签", …最多6个], "protagonist_1": "主角名1(5字内)", '
                '"protagonist_2": "主角名2(5字内，没有就空串)", "status": "连载中 或 已完结", '
                '"summary": "作品简介(50-500字)"}')


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


def _norm(data, platform, goal=""):
    """规范化平台字段输出；不合规返回 None。"""
    if not isinstance(data, dict):
        return None
    book_name = _name(data.get("book_name"), 15 if platform == "fanqie" else 18)
    summary = _summary(data.get("summary"))
    if not book_name and not summary:
        return None
    if platform == "fanqie":
        reader = str(data.get("target_reader") or "").strip()
        if reader not in ("男频", "女频"):
            reader = "女频" if "女" in (goal or "") else "男频"
        mode = str(data.get("signing_mode") or "").strip()
        if mode not in ("连载模式", "完本模式"):
            mode = "连载模式"
        return {"book_name": book_name, "signing_mode": mode, "target_reader": reader,
                "read_tags": _tags(data.get("read_tags")),
                "content_tags": _tags(data.get("content_tags")),
                "protagonist_1": _name(data.get("protagonist_1"), 5),
                "protagonist_2": _name(data.get("protagonist_2"), 5),
                "summary": summary}
    reader = str(data.get("target_reader") or "").strip()
    if reader not in ("男生", "女生"):
        reader = "女生" if "女" in (goal or "") else "男生"
    status = str(data.get("status") or "").strip()
    if status not in ("连载中", "已完结"):
        status = "连载中"
    return {"book_name": book_name, "target_reader": reader,
            "category_main": _name(data.get("category_main"), 10),
            "category_sub": _name(data.get("category_sub"), 10),
            "tags": _tags(data.get("tags")),
            "protagonist_1": _name(data.get("protagonist_1"), 5),
            "protagonist_2": _name(data.get("protagonist_2"), 5),
            "status": status, "summary": summary}


def _template_meta(task, platform, outline):
    """兜底模板：只做素材搬运归纳（书名/简介来自大纲与目标），其余留空待补。"""
    goal = task.get("goal") or ""
    book_name = str((outline or {}).get("book_title") or task.get("title") or "").strip()
    summary = goal.splitlines()[0][:500] if goal.strip() else ""
    if not summary:
        summary = book_name or "（待补充）"    # 全空素材也要有返回，_norm 才不会拒绝
    return _norm({"book_name": book_name, "summary": summary}, platform, goal)


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
    from . import modelhub, planner, runner, skills  # 惰性导入，同 planner
    material, outline = _collect_material(task)
    schema = {"fanqie": FANQIE_SCHEMA, "qimao": QIMAO_SCHEMA}.get(platform) or ""
    plat_label = PLATFORMS[platform]["label"]
    sk_block, _ = skills.block_for(task)
    prompt = (PROMPT_HEAD.replace("__PLATFORM__", plat_label)
              .replace("__SKILLS__", sk_block)
              .replace("__SCHEMA__", schema) + "\n\n## 小说素材\n" + material)
    goal = task.get("goal") or ""

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
                return meta, "编排者(%s · %s)" % (prov.get("name", prov["id"]), model)
        orch_err = str(res.get("error") or "返回内容无法解析为作品信息")[:200]

    if author_agent and author_agent.get("mode") == "real" and not template_only():
        res = runner.run_agent(modelhub.bind_agent(author_agent), prompt,
                               workdir=task.get("workdir"), readonly=True,
                               timeout=BOOKMETA_TIMEOUT, log_path=log_path)
        planner._log_usage("bookmeta", "bookmeta", task, res, agent=author_agent)
        if res["ok"]:
            meta = _norm(runner.extract_json(res.get("text") or ""), platform, goal)
            if meta:
                return meta, "llm(%s)" % author_agent["id"]
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
    lines.append("> 各字段可直接复制进 %s 建书表单；简介请保持 50-500 字。" % plat)
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
