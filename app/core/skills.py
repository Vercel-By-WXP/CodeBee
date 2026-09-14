# -*- coding: utf-8 -*-
"""经验库（Skills）：内置写作/工程规范包 + 运行中自动沉淀的教训，自动注入提示词。

三层内容：
  1) **内置经验包**（app/core/skillpacks/*.md）：人工维护的领域规范（如七猫签约标准），
     按流程类型（scope）匹配注入；
  2) **自动教训**（data/skills.json）：每次运行结束后由编排者（或退化规则）总结本次
     评审暴露的问题，去重沉淀为可复用教训，下次同类任务自动带上——**越跑越好**；
  3) 注入时统计命中（hits），高频教训排前面；可停用/删除。

注入点：连载大纲、逐章起草、评审提示词（评审也要按平台标准判）。
全部自动，无需人工干预；总结失败不影响任务本身（异步 + 兜底）。
"""
from __future__ import annotations

import hashlib
import json
import re
import threading
import time

from . import paths

_LOCK = threading.RLock()
_FILE = paths.DATA_DIR / "skills.json"
PACK_DIR = paths.APP_DIR / "core" / "skillpacks"

MAX_INJECT_CHARS = 6000      # 单次注入上限（防止提示词爆炸）
MAX_LESSONS_INJECT = 8       # 注入的自动教训条数上限

# 内置经验包：文件 → 适用流程（scope）；scope 为空表示适用全部
BUILTIN_PACKS = [
    {"id": "qimao-signing", "name": "七猫签约标准与写作规范",
     "file": "qimao-signing.md",
     "scopes": ["novel", "serial_novel"],
     "note": "黄金一章、爽点纪律、期待感三源、人物红线、自检清单"},
]


def _now():
    return time.strftime("%Y-%m-%d %H:%M:%S")


def _load():
    try:
        data = json.loads(_FILE.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}
    except Exception:
        return {}


def _save(data):
    _FILE.parent.mkdir(parents=True, exist_ok=True)
    tmp = _FILE.with_suffix(".tmp")
    tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    tmp.replace(_FILE)


# ---------------------------------------------------------------- 内置经验包

def pack_text(pack):
    """读取内置包正文（文件缺失返回空串）。"""
    try:
        p = (PACK_DIR / pack["file"]).resolve()
        if PACK_DIR.resolve() not in p.parents or not p.is_file():
            return ""
        return p.read_text(encoding="utf-8", errors="replace")
    except Exception:
        return ""


def list_packs():
    with _LOCK:
        state = _load().get("packs") or {}
    out = []
    for p in BUILTIN_PACKS:
        st = state.get(p["id"]) or {}
        out.append({"id": p["id"], "name": p["name"], "scopes": p["scopes"],
                    "note": p["note"], "builtin": True,
                    "enabled": bool(st.get("enabled", True)),
                    "chars": len(pack_text(p))})
    return out


def _pack_enabled(pid):
    with _LOCK:
        st = (_load().get("packs") or {}).get(pid) or {}
    return bool(st.get("enabled", True))


# ---------------------------------------------------------------- 自动教训

def _norm_title(t):
    """标题归一化：去掉空白与中英文标点（含全角），用于跨次运行去重。"""
    s = re.sub(r"[\s\.,;:!?，。、；：！？…·—－\-_/\\|@#$%^&*+=~`'\"“”‘’（）()【】\[\]《》<>]+",
               "", str(t or ""))
    return s[:40]


def _lesson_id(scope, title):
    h = hashlib.sha256(("%s|%s" % (scope, _norm_title(title))).encode("utf-8")).hexdigest()[:12]
    return "sk-" + h


def list_lessons(scope=None, only_enabled=False):
    with _LOCK:
        items = list((_load().get("lessons") or []))
    if scope:
        items = [x for x in items if x.get("scope") in (scope, "*")]
    if only_enabled:
        items = [x for x in items if x.get("enabled", True)]
    items.sort(key=lambda x: (-int(x.get("hits") or 0), -int(x.get("seen") or 1),
                              x.get("created_at") or ""))
    return items


def upsert_lesson(scope, title, content, source=""):
    """写入/合并一条教训：同 scope 同标题视为同一条（seen+1，内容取新的）。"""
    title = str(title or "").strip()[:60]
    content = str(content or "").strip()[:1200]
    if not title or not content:
        return None
    lid = _lesson_id(scope, title)
    with _LOCK:
        data = _load()
        items = data.setdefault("lessons", [])
        for it in items:
            if it.get("id") == lid:
                it["content"] = content
                it["seen"] = int(it.get("seen") or 1) + 1
                it["updated_at"] = _now()
                if source:
                    it["source"] = source
                _save(data)
                return it
        it = {"id": lid, "scope": scope, "title": title, "content": content,
              "source": source, "hits": 0, "seen": 1, "enabled": True,
              "created_at": _now(), "kind": "lesson"}
        items.append(it)
        _save(data)
        return it


def lesson_op(lesson_id, op):
    """启用/停用/删除教训。返回错误或 None。"""
    if op not in ("enable", "disable", "delete"):
        return "未知操作 " + str(op)
    with _LOCK:
        data = _load()
        items = data.get("lessons") or []
        hit = next((x for x in items if x.get("id") == lesson_id), None)
        if not hit:
            return "教训不存在"
        if op == "delete":
            data["lessons"] = [x for x in items if x.get("id") != lesson_id]
        else:
            hit["enabled"] = (op == "enable")
        _save(data)
    return None


def pack_op(pack_id, op):
    """启用/停用内置经验包（不可删除，只能停用）。"""
    if op not in ("enable", "disable"):
        return "未知操作 " + str(op)
    if pack_id not in {p["id"] for p in BUILTIN_PACKS}:
        return "经验包不存在"
    with _LOCK:
        data = _load()
        packs = data.setdefault("packs", {})
        packs[pack_id] = {"enabled": (op == "enable")}
        _save(data)
    return None


# ---------------------------------------------------------------- 注入

def block_for(task, scope_override=None):
    """生成注入提示词的经验块。命中即计数。返回 (文本, 命中的 id 列表)。"""
    scope = scope_override or task.get("type") or "*"
    parts, used = [], []

    for p in BUILTIN_PACKS:
        if scope not in p["scopes"]:
            continue
        if not _pack_enabled(p["id"]):
            continue
        txt = pack_text(p)
        if txt:
            parts.append("### 【%s】\n%s" % (p["name"], txt.strip()))
            used.append(p["id"])

    lessons = list_lessons(scope, only_enabled=True)[:MAX_LESSONS_INJECT]
    if lessons:
        lines = []
        for x in lessons:
            lines.append("- **%s**：%s" % (x["title"], x["content"]))
            used.append(x["id"])
        parts.append("### 【本项目已沉淀的教训（历史评审反复出现，务必规避）】\n" + "\n".join(lines))

    if not parts:
        return "", []
    text = "## 经验库（写作/工程规范 + 历史教训，必须遵守）\n\n" + "\n\n".join(parts)
    if len(text) > MAX_INJECT_CHARS:
        text = text[:MAX_INJECT_CHARS] + "\n…（已截断）"
    if used:
        bump_hits(used)
    return text, used


def bump_hits(ids):
    ids = [i for i in (ids or []) if i]
    if not ids:
        return
    with _LOCK:
        data = _load()
        changed = False
        for it in (data.get("lessons") or []):
            if it.get("id") in ids:
                it["hits"] = int(it.get("hits") or 0) + 1
                changed = True
        if changed:
            _save(data)


# ---------------------------------------------------------------- 运行后自动总结（自学习闭环）

LEARN_PROMPT = """你是编排系统的复盘官。下面是刚结束的一次任务运行的评审结果与主要问题。
请把**可复用到下次同类任务**的经验教训提炼出来（不要复述本次剧情，不要写泛泛的套话）。
只输出一个 ```json 代码块，不要输出其他内容。JSON 结构：
{"lessons": [{"title": "≤14 字的问题归类", "content": "下次必须怎么做/避免什么（≤120 字，具体可执行）"}]}
最多 5 条，只保留反复出现或影响过稿/验收的关键问题；没有值得沉淀的就返回空数组。

## 任务类型
__TYPE__

## 任务目标（摘要）
__GOAL__

## 本次结论
__VERDICT__

## 评审主要问题
__ISSUES__"""


def _collect_issues(run):
    """从 verdict / 报告里提取 major 问题与低分维度（确定性兜底用）。"""
    v = run.get("verdict") or {}
    issues = []
    for c in (v.get("chapter_scores") or []):
        m = c.get("means") or {}
        if not m:
            continue
        low = [d for d, s in m.items() if float(s) < float(v.get("threshold") or 7.0)]
        if low:
            issues.append({"dim": "、".join(low), "severity": "major",
                           "note": "第 %s 章《%s》维度偏低（%s）" % (
                               c.get("chapter"), c.get("title"),
                               "，".join("%s %.1f" % (d, m[d]) for d in low))})
    gs = v.get("global_scores") or {}
    low_g = [d for d, s in gs.items() if float(s) < float(v.get("threshold") or 7.0)]
    if low_g:
        issues.append({"dim": "、".join(low_g), "severity": "major",
                       "note": "全书一致性评审偏低：%s" % "，".join("%s %.1f" % (d, gs[d]) for d in low_g)})
    # 报告里的 major 明细（比 verdict 更细）
    try:
        p = paths.RUNS_DIR / run["id"] / "report.md"
        if p.is_file():
            txt = p.read_text(encoding="utf-8", errors="replace")
            tail = txt.split("## 主要问题")[-1] if "## 主要问题" in txt else ""
            for line in tail.splitlines():
                line = line.strip()
                if line.startswith("- [") and len(issues) < 30:
                    issues.append({"dim": "报告", "severity": "major", "note": line[2:][:200]})
    except Exception:
        pass
    return issues[:30]


def _fallback_lessons(task, run):
    """无编排者时的确定性兜底：按维度把反复出现的问题聚成教训。"""
    v = run.get("verdict") or {}
    out = []
    weak = {}
    for c in (v.get("chapter_scores") or []):
        for d, s in (c.get("means") or {}).items():
            if float(s) < float(v.get("threshold") or 7.0):
                weak.setdefault(d, []).append((c.get("chapter"), float(s)))
    for d, lst in sorted(weak.items(), key=lambda kv: -len(kv[1]))[:3]:
        chs = "、".join("第 %s 章(%.1f)" % (c, s) for c, s in lst[:4])
        out.append({"title": "%s 维度反复不达标" % d,
                    "content": "历史运行中 %s 的「%s」多次低于阈值（%s）。写这一维度前先对照经验包自检，"
                               "宁可少写事件也要把该维度做扎实。" % (task.get("type"), d, chs)})
    for d, s in (v.get("global_scores") or {}).items():
        if float(s) < float(v.get("threshold") or 7.0):
            out.append({"title": "全书「%s」被一致性评审扣分" % d,
                        "content": "单章达标但全书「%s」仅 %.1f 分。下一部作品在章纲阶段就要规划该维度的"
                                   "整体曲线（而不是逐章各写各的）。" % (d, float(s))})
    return out[:5]


def learn_from_run(run_id, use_orchestrator=True):
    """运行结束后自动总结教训并沉淀。返回写入条数。"""
    from . import store
    run = store.get_run(run_id)
    if not run:
        return 0
    task = store.get_task(run.get("task_id")) if run.get("task_id") else None
    if not task:
        return 0
    # mock 运行不沉淀（没有真实评审信号）
    if all((s.get("agent") or "").startswith("mock") for s in (run.get("steps") or [])):
        return 0
    v = run.get("verdict") or {}
    if not v:
        return 0
    issues = _collect_issues(run)
    lessons = []
    if use_orchestrator:
        try:
            from . import modelhub, runner
            orch = modelhub.resolve_orchestrator()
            if orch:
                prov, model = orch
                verdict_txt = json.dumps({k: v[k] for k in v if k not in ("route", "chapter_scores")},
                                         ensure_ascii=False)[:1200]
                prompt = (LEARN_PROMPT.replace("__TYPE__", str(task.get("type")))
                          .replace("__GOAL__", (task.get("goal") or "")[:600])
                          .replace("__VERDICT__", verdict_txt)
                          .replace("__ISSUES__",
                                   "\n".join("- %s" % i.get("note", "") for i in issues)[:3000] or "（无）"))
                # 推理模型的思考会吞掉全部预算：max_tokens 给足才有正文可解析
                res = modelhub.chat(prov["id"], model, prompt, max_tokens=8000, timeout=300)
                if res.get("ok"):
                    data = runner.extract_json(res.get("text") or "")
                    raw = (data or {}).get("lessons") if isinstance(data, dict) else None
                    if isinstance(raw, list):
                        for x in raw[:5]:
                            if isinstance(x, dict) and x.get("title") and x.get("content"):
                                lessons.append({"title": str(x["title"]), "content": str(x["content"])})
        except Exception:
            lessons = []
    if not lessons:
        lessons = _fallback_lessons(task, run)
    n = 0
    for x in lessons:
        if upsert_lesson(task.get("type") or "*", x["title"], x["content"], source=run_id):
            n += 1
    return n


def learn_async(run_id):
    """异步总结（不阻塞任务收尾）。"""
    def _run():
        try:
            learn_from_run(run_id)
        except Exception:
            pass
    threading.Thread(target=_run, name="skill-learn", daemon=True).start()


def view():
    """经验库总览（给 UI/API）。"""
    return {"packs": list_packs(), "lessons": list_lessons()}
