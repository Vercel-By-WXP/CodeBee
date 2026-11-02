# -*- coding: utf-8 -*-
"""记忆总结：把分散的项目记忆 / 经验 / 知识综合成 四段式散文
（工作背景 / 个人背景 / 当前关注 / 近期动态）。

优先用编排者 LLM 生成自然通顺的中文；无可用模型或调用失败时回落到前端
的确定性模板（app/ui/app.js 的 memorySummaryHtml），保证离线可读、内容全部
来自真实沉淀数据、不编造。LLM 结果按源数据哈希缓存，避免每次刷新都打模型。
"""
from __future__ import annotations

import hashlib
import json
import time
from pathlib import Path

from core import paths

# 四段固定顺序；既是提示词约束，也是前端渲染时识别小节标题的依据
SECTIONS = ("工作背景", "个人背景", "当前关注", "近期动态")


def gather_memory_data():
    """收集与 /api/memory 一致的记忆数据（projects + global 经验/知识）。"""
    from core import knowledge, project_memory, skills, store
    tasks = store.list_tasks(archived=False) + store.list_tasks(archived=True)
    return {
        "projects": project_memory.project_index(tasks),
        "global": {
            "skills": skills.view(),
            "knowledge": knowledge.view(),
        },
    }


def source_hash(data):
    payload = json.dumps(data, ensure_ascii=False, sort_keys=True, default=str)
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()[:24]


def _cache_path():
    return paths.DATA_DIR / "memory-summary" / "cache.json"


def _build_prompt(data, lang="zh"):
    """严格提示词：固定四段、纯文本小节标题、内容只来自真实条目。"""
    projects = data.get("projects") or []
    blocks = []
    for p in projects:
        name = p.get("name") or "项目"
        entries = p.get("entries") or []
        if not entries:
            continue
        blocks.append("工作区「%s」（%d 条记忆）：" % (name, len(entries)))
        for e in entries[:12]:
            title = e.get("title") or ""
            content = (e.get("content") or "").strip()
            status = e.get("status") or ""
            blocks.append("- [%s] %s\n  %s" % (status, title, content))
    src = "\n".join(blocks)
    if not src.strip():
        src = "（暂无项目记忆）"
    if lang == "en":
        return (
            "You are CodeBee's memory organizer. Below are real project memory "
            "entries from the user's workspaces. Synthesize them into a 'Memory "
            "Summary' the user can skim.\n\n"
            "Strict rules:\n"
            "1. Output only these four sections, in this fixed order, each header on its own line (no #, no bold markers):\n"
            "   Work background\n   Personal background\n   Current focus\n   Recent activity\n"
            "2. Work background: 2-4 natural sentences on which workspaces and task types the user works in (derived, not copied verbatim).\n"
            "3. Personal background: 2-3 sentences on interface language, settings level, theme and work habits shown in the entries.\n"
            "4. Current focus: what the user is working on now (latest approved entry), 1-2 sentences.\n"
            "5. Recent activity: a '- ' bulleted list of the latest entries' gist (with time if known).\n"
            "6. Only use facts present below; never invent names, numbers or projects. If thin, write 'None'.\n"
            "7. No preamble or closing; start directly with the first header.\n\n"
            "## Real memory entries\n" + src)
    return (
        "你是 CodeBee 的记忆整理助手。下面是一批真实的项目记忆条目（来自用户的"
        "工作区，每条含标题与字段）。请综合成一份给用户看的「记忆总结」，帮助用户"
        "快速回顾自己在这个工具里沉淀了什么。\n\n"
        "严格要求：\n"
        "1. 只输出以下四个小节，顺序固定，小节标题单独成行（不要加 # 号、不要加粗符号）：\n"
        "   工作背景\n   个人背景\n   当前关注\n   近期动态\n"
        "2. 工作背景：用 2-4 句通顺中文概括用户主要在哪些工作区、做什么类型的事"
        "（从条目里提炼，不要照抄条目编号或字段名）。\n"
        "3. 个人背景：从界面语言、设置档、主题等偏好，以及条目体现的工作习惯"
        "（如是否重视质量、是否高频做文档/数据类工作）写 2-3 句。\n"
        "4. 当前关注：写用户最近正在推进的事（取最近一条已批准记忆的要点），1-2 句。\n"
        "5. 近期动态：用「- 」开头的要点列表，列最近几条记忆的概要（带时间若可知）。\n"
        "6. 全部内容必须来自下面的真实条目，不得编造任何事实、数字或项目名；"
        "若信息不足就写「暂无」。\n"
        "7. 不要输出任何解释、前言或结语，直接从小节标题开始。\n\n"
        "## 真实记忆条目\n" + src)


def _has_content(data):
    """有任一真实记忆条目才值得调模型；空数据直接回落前端模板。"""
    for p in data.get("projects") or []:
        if p.get("entries"):
            return True
    return False


# 明显无法产出纯文本的模型（语音合成 / 语音识别 / 文生图 / 文生视频），
# 调了也只会返回空或被网关拒，还会白白阻塞整页 120s。这些名字中文本模型
# 命中概率极低（claude/gemini/glm/qwen/deepseek 等都不含下列词段），故只做
# 保守排除，绝不误伤正常文本模型。
_NON_TEXT_MODEL = (
    "stepaudio", "tts", "asr", "stt", "whisper", "speech", "voice",
    "audio", "wav", "sound", "dall", "flux", "sora", "kling", "wan",
    "veo", "imagen",
)


def _is_text_model(model_name):
    """模型是否可能产出纯文本总结。仅排除明显非文本的语音 / 生成类模型。"""
    n = (model_name or "").lower()
    if n.startswith(("claude", "gemini")):   # 全系多模态但完全支持文本
        return True
    return not any(k in n for k in _NON_TEXT_MODEL)


def generate_summary(force=False, lang="zh"):
    """生成（或取缓存的）四段记忆总结。返回 {text, hash, generated_at, from_cache}
    或 {text: None, hash, reason}。text 为 None 时前端回落到确定性模板。"""
    data = gather_memory_data()
    src_hash = source_hash(data)
    if not _has_content(data):
        return {"text": None, "hash": src_hash, "reason": "no_data"}
    cpath = _cache_path()
    if not force:
        try:
            if cpath.exists():
                cached = json.loads(cpath.read_text(encoding="utf-8"))
                if cached.get("hash") == src_hash and (cached.get("text") or "").strip():
                    return {"text": cached["text"], "hash": src_hash,
                            "generated_at": cached.get("generated_at"), "from_cache": True}
        except Exception:
            pass
    try:
        from core import modelhub
        orch = modelhub.resolve_orchestrator()
        if not orch:
            return {"text": None, "hash": src_hash, "reason": "no_orchestrator"}
        prov, model = orch
        if not _is_text_model(model):
            # 解析到的编排者是一个语音/生成类模型，调了也返回不了文本总结，
            # 直接回落前端确定性模板，避免整页阻塞 120s。
            return {"text": None, "hash": src_hash, "reason": "unsupported_model"}
        prompt = _build_prompt(data, lang=lang)
        res = modelhub.chat(prov.get("id"), model, prompt, max_tokens=1200, timeout=120)
        text = (res.get("text") or "").strip() if res.get("ok") else ""
        if not text:
            return {"text": None, "hash": src_hash, "reason": res.get("error") or "empty"}
    except Exception as exc:  # 离线 / 配置缺失 / 超时：回落前端模板
        return {"text": None, "hash": src_hash, "reason": str(exc)}
    try:
        cpath.parent.mkdir(parents=True, exist_ok=True)
        cpath.write_text(json.dumps({"hash": src_hash, "text": text,
                                     "generated_at": int(time.time())}, ensure_ascii=False),
                        encoding="utf-8")
    except Exception:
        pass
    return {"text": text, "hash": src_hash, "generated_at": int(time.time()), "from_cache": False}
