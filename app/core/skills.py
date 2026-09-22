# -*- coding: utf-8 -*-
"""经验库（Skills）：内置写作/工程规范包 + 用户自建包 + 运行中自动沉淀的教训。

设计稿：docs/migration/04-skills-seam.md §3A/§3B（按 Tutti 实际架构落地：
模型在外部 CLI 中无法按需调「skill 工具」，注入式 block_for 是唯一通道，
故 dsh 的「按需正文加载」不适用；落地的是多源 provider + persona + mtime 热缓存）。

三层内容：
  1) **内置经验包**（app/core/skillpacks/*.md）：人工维护的领域规范（如七猫签约标准），
     按流程类型（scope）匹配注入；
  2) **用户自建包**（data/skillpacks/*.md）：frontmatter 声明 name/scopes/persona，
     无需改代码即可沉淀领域规范；目录 mtime 缓存，改文件即生效（3A 多源分层）；
  3) **自动教训**（data/skills.json）：每次运行结束后由编排者（或退化规则）总结本次
     评审暴露的问题，去重沉淀为可复用教训，下次同类任务自动带上——**越跑越好**。

Persona（3B）：包的 frontmatter `persona:` 字段注入为独立的「角色设定」块，
排在正文之前（不与规范正文混排）。
"""
from __future__ import annotations

import hashlib
import json
import re
import threading
import time
from pathlib import Path

from . import paths

_LOCK = threading.RLock()
_FILE = paths.DATA_DIR / "skills.json"
PACK_DIR = paths.APP_DIR / "core" / "skillpacks"


def _user_pack_dir():
    """用户自建包目录：每次现取 paths.DATA_DIR（测试重定向后自动跟随）。"""
    return paths.DATA_DIR / "skillpacks"

MAX_INJECT_CHARS = 9000      # 单次注入上限（防止提示词爆炸；七猫+番茄双平台包并存后上调）
MAX_LESSONS_INJECT = 8       # 注入的自动教训条数上限
WILDCARD_PACK_CHAR_CAP = 2400  # wildcard（scope=*）包单包注入预算：市场通配技能动辄数万字，全文注入会挤掉项目教训

# 自动教训的问题分类：闭集枚举，对齐评审维度。沉淀时由复盘官归类（兜底路径按评审
# 维度关键词映射），UI 据此分类过滤查看。刻意保持小而稳，避免类别爆炸让过滤失去意义。
LESSON_CATEGORIES = ["情节逻辑", "人物塑造", "节奏爽点", "文笔风格", "一致性", "流程规范"]
LESSON_UNCATEGORIZED = "未分类"   # 无法归类的兜底
# dim（评审维度名）/标题/正文 → 分类：按关键词就近命中，首个匹配者胜。
# 顺序敏感：流程规范排在一致性前（「不一致」这类工程标题不该误入一致性类）；
# 一致性刻意不收裸「一致」。文笔风格不收裸「重复」（易误伤「重复修复」类工程教训）。
_CATEGORY_KEYWORDS = [
    ("情节逻辑", ("情节", "剧情", "主线", "冲突", "逻辑", "事件", "伏笔", "填坑", "转折")),
    ("人物塑造", ("人物", "角色", "弧光", "人设", "性格", "动机", "ooc", "崩人设")),
    ("节奏爽点", ("节奏", "爽点", "钩子", "吸引力", "开篇", "黄金三章", "追读", "断章", "高潮")),
    ("流程规范", ("流程", "规范", "格式", "字数", "签约", "交稿", "工程", "测试", "部署",
                "接口", "验证", "评审", "验收", "修复", "单测", "用例", "verify", "review",
                "失败", "定位", "诊断", "回归", "空转")),
    ("一致性",  ("连贯", "吃书", "时间线", "前后矛盾", "前后不一", "连续性", "设定冲突", "人设统一")),
    ("文笔风格", ("文笔", "语言", "描写", "对白", "对话", "文风", "措辞", "病句")),
]


def _category_from(text):
    """把模型归类文本或评审维度名就近映射到闭集分类；命中不了返回 None。"""
    s = str(text or "").strip().lower()
    if not s:
        return None
    for cat, kws in _CATEGORY_KEYWORDS:
        for kw in kws:
            if kw.lower() in s:
                return cat
    return None


def _normalize_category(category, dim=None):
    """把任意输入归一到闭集分类：先精确命中枚举（category 与 dim 都走精确），
    再按关键词映射 category，再退到 dim 关键词，最后 None（调用方据此落未分类）。"""
    c = str(category or "").strip()
    if c in LESSON_CATEGORIES:
        return c
    d = str(dim or "").strip()
    if d in LESSON_CATEGORIES:
        return d
    return _category_from(c) or _category_from(d) or None

# 内置经验包：文件 → 适用流程（scope）；scope 为空表示适用全部
BUILTIN_PACKS = [
    {"id": "qimao-signing", "name": "七猫签约标准与写作规范",
     "file": "qimao-signing.md",
     "scopes": ["novel", "serial_novel"],
     "note": "黄金一章、爽点纪律、期待感三源、人物红线、自检清单"},
    {"id": "fanqie-novel", "name": "番茄小说写作与流量守则",
     "file": "fanqie-novel.md",
     "scopes": ["novel", "serial_novel"],
     "note": "算法流量池/完读追读、黄金三章整体验、题材标签匹配、更新纪律、合同要点"},
]

# ---------------------------------------------------------------- 用户自建包（3A）

# 用户包 mtime 缓存：路径 → (mtime, 解析结果)。文件改动即失效（零依赖替代 watchdog）
_user_pack_cache = {}
_user_dir_mtime = {"ts": 0.0, "ids": None}


def _parse_frontmatter(text):
    """解析 markdown 头部 ```--- frontmatter ---```（YAML 子集：key: value / list）。

    返回 (meta: dict, body: str)。无 frontmatter 时 meta 为空、body 为原文。
    """
    if not text.startswith("---"):
        return {}, text
    end = text.find("\n---", 3)
    if end < 0:
        return {}, text
    head = text[3:end].strip("\r\n")
    body = text[end + 4:].lstrip("\r\n")
    meta = {}
    cur_list_key = None
    for line in head.splitlines():
        line = line.rstrip()
        if not line.strip() or line.strip().startswith("#"):
            continue
        if line.lstrip().startswith("- "):
            if cur_list_key:
                meta.setdefault(cur_list_key, []).append(
                    line.lstrip()[2:].strip().strip("'\""))
            continue
        m = re.match(r"^([A-Za-z_][A-Za-z0-9_]*)\s*:\s*(.*)$", line)
        if m:
            key, val = m.group(1), m.group(2).strip()
            if val == "":
                meta[key] = []
                cur_list_key = key
            else:
                meta[key] = val.strip("'\"")
                cur_list_key = None
    return meta, body


def _user_pack_dir_mtime():
    try:
        return _user_pack_dir().stat().st_mtime
    except OSError:
        return 0.0


def _list_user_pack_files():
    """data/skillpacks/*.md 文件清单（目录 mtime 缓存）。"""
    with _LOCK:
        dts = _user_pack_dir_mtime()
        if _user_dir_mtime["ids"] is not None and dts == _user_dir_mtime["ts"]:
            return _user_dir_mtime["ids"]
        files = []
        try:
            _user_pack_dir().mkdir(parents=True, exist_ok=True)
            for p in sorted(_user_pack_dir().glob("*.md")):
                files.append(p)
        except OSError:
            pass
        _user_dir_mtime["ts"] = dts
        _user_dir_mtime["ids"] = files
        return files


def _load_user_pack(path):
    """读取并解析一个用户包（文件 mtime 缓存）。文件缺失/不可读返回 None。"""
    try:
        mt = path.stat().st_mtime
    except OSError:
        return None
    key = str(path)
    with _LOCK:
        hit = _user_pack_cache.get(key)
        if hit and hit[0] == mt:
            return hit[1]
    try:
        raw = path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return None
    meta, body = _parse_frontmatter(raw)
    stem = path.stem
    pack = {
        "id": "user-" + hashlib.sha256(stem.encode("utf-8")).hexdigest()[:10],
        "name": str(meta.get("name") or stem),
        "file": key,
        "scopes": [str(s) for s in (meta.get("scopes") or ["*"])] or ["*"],
        "note": str(meta.get("note") or ""),
        "persona": str(meta.get("persona") or ""),
        "builtin": False,
        "user": True,
    }
    pack["body"] = body
    with _LOCK:
        _user_pack_cache[key] = (mt, pack)
    return pack


def user_packs():
    """全部用户自建包（每次现读清单 + mtime 缓存正文——改文件即生效）。"""
    out = []
    for p in _list_user_pack_files():
        pack = _load_user_pack(p)
        if pack:
            out.append(pack)
    return out


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
    """读取包正文（内置包相对 skillpacks/，用户包 file 为绝对路径）。文件缺失返回空串。"""
    try:
        if pack.get("user"):
            p = Path(pack["file"])
            return p.read_text(encoding="utf-8", errors="replace") if p.is_file() else ""
        p = (PACK_DIR / pack["file"]).resolve()
        if PACK_DIR.resolve() not in p.parents or not p.is_file():
            return ""
        return p.read_text(encoding="utf-8", errors="replace")
    except Exception:
        return ""


def all_packs():
    """内置 + 用户自建包的并集（3A 多源分层：同名场景下内置优先）。"""
    builtin = [dict(p) for p in BUILTIN_PACKS]
    builtin_ids = {p["id"] for p in builtin}
    out = list(builtin)
    for up in user_packs():
        if up["id"] not in builtin_ids:
            out.append(up)
    return out


def list_packs():
    with _LOCK:
        state = _load().get("packs") or {}
    out = []
    for p in all_packs():
        st = state.get(p["id"]) or {}
        out.append({"id": p["id"], "name": p["name"], "scopes": p["scopes"],
                    "note": p.get("note", ""), "builtin": bool(p.get("builtin")),
                    "user": bool(p.get("user")),
                    "persona": bool(p.get("persona")),
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


def list_lessons(scope=None, only_enabled=False, category=None):
    with _LOCK:
        items = list((_load().get("lessons") or []))
    if scope:
        items = [x for x in items if x.get("scope") in (scope, "*")]
    if category:
        items = [x for x in items
                 if (x.get("category") or LESSON_UNCATEGORIZED) == category]
    if only_enabled:
        items = [x for x in items if x.get("enabled", True)]
    items.sort(key=lambda x: (-int(x.get("hits") or 0), -int(x.get("seen") or 1),
                              x.get("created_at") or ""))
    return items


def _title_containment(a, b):
    """标题近似度：字符 bigram 包含度（交集/较短者）。与 knowledge._title_sim
    同款度量（追加后缀形态 Jaccard 漏、包含度=1.0），教训/知识去重口径一致。"""
    ga, gb = _text_bigrams(a), _text_bigrams(b)
    if not ga or not gb:
        return 0.0
    return len(ga & gb) / float(min(len(ga), len(gb)))


_LESSON_NEAR_DUP = 0.8   # 同 scope 教训标题包含度 ≥0.8 视为同一条（近似题合并）


def upsert_lesson(scope, title, content, source="", category=None, dim=None):
    """写入/合并一条教训：同 scope 同标题视为同一条（seen+1，内容取新的）。

    category 为闭集枚举之一（见 LESSON_CATEGORIES）；输入非法时退到 dim 关键词映射，
    仍归不出则落「未分类」。id 仍只按 scope+标题哈希，故老教训再沉淀会合并而非分裂。
    近似题合并（与知识库同款防膨胀）：自动复盘每次 done 运行都跑，「节奏拖沓」与
    「节奏拖沓问题」会各占一条；同 scope 标题包含度 ≥0.8 也视为同一条，合并语义
    与精确同题一致。<4 字符短题只认精确（bigram 噪声大）。
    """
    title = str(title or "").strip()[:60]
    content = str(content or "").strip()[:1200]
    if not title or not content:
        return None
    cat = _normalize_category(category, dim) or LESSON_UNCATEGORIZED
    lid = _lesson_id(scope, title)
    with _LOCK:
        data = _load()
        items = data.setdefault("lessons", [])
        hit = next((x for x in items if x.get("id") == lid), None)
        if hit is None and len(title) >= 4:
            for x in items:
                if x.get("scope") != scope:
                    continue
                xt = str(x.get("title") or "")
                if len(xt) >= 4 and _title_containment(title, xt) >= _LESSON_NEAR_DUP:
                    hit = x
                    break
        if hit is not None:
            it = hit
            it["content"] = content
            it["seen"] = int(it.get("seen") or 1) + 1
            it["updated_at"] = _now()
            # 合并时不降级已有分类：除非本次归到了明确类别，或该条原本没有分类
            if cat != LESSON_UNCATEGORIZED or not it.get("category"):
                it["category"] = cat
            if source:
                it["source"] = source
            _save(data)
            return it
        it = {"id": lid, "scope": scope, "title": title, "content": content,
              "source": source, "hits": 0, "seen": 1, "enabled": True,
              "category": cat,
              "created_at": _now(),
              # 稳定 token（procedure=程序性做法 / lesson=规避性教训）；
              # 展示层 view() 翻译成「做法/教训」，持久层不用中文防改文案伤数据
              "kind": ("procedure" if title.startswith("做法：") else "lesson")}
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
    """启用/停用包（内置与用户自建均可停用；用户包不可通过此接口删除，删文件即可）。"""
    if op not in ("enable", "disable"):
        return "未知操作 " + str(op)
    known = {p["id"] for p in all_packs()}
    if pack_id not in known:
        return "经验包不存在"
    with _LOCK:
        data = _load()
        packs = data.setdefault("packs", {})
        packs[pack_id] = {"enabled": (op == "enable")}
        _save(data)
    return None


# ---------------------------------------------------------------- 注入

def _text_bigrams(text):
    """中文友好的零依赖关键词：去空白/标点后取字符 bigram 集合。
    不引入分词依赖，对中文标题/短句的重叠度足够区分相关性。"""
    t = re.sub(r"[\s\W_]+", "", str(text or ""))
    return {t[i:i + 2] for i in range(len(t) - 1)}


def relevance_top(lessons, task, limit):
    """任务相关性 top-k（pro-workflow 思想）：教训积累超过注入上限时，
    按与任务目标/上下文/标题的重叠度选最相关的 limit 条，而不是只看 hits。
    排序依据在单个 run 内恒定（goal 固定、id 唯一），同一任务字节稳定，
    不碎供应商前缀缓存。不超过上限时不重排，保持既有 hits 语义。"""
    if len(lessons) <= limit:
        return lessons
    probe = (_text_bigrams((task or {}).get("goal"))
             | _text_bigrams((task or {}).get("context"))
             | _text_bigrams((task or {}).get("title")))
    if not probe:
        return lessons[:limit]

    # 使用反馈闭环（pmb「量化记忆真实帮助」+ tradememory「按结局加权召回」）：
    # 相关性优先；同分比 outcome 胜负（注入后任务过审 +1 / 未过 -1），再比 hits。
    def rank(x):
        grams = _text_bigrams(x.get("title")) | _text_bigrams(x.get("content"))
        overlap = -len(probe & grams)
        karma = int(x.get("won") or 0) - int(x.get("lost") or 0)
        lid = x.get("id") or ""
        return (overlap, -karma, -int(x.get("hits") or 0), lid)

    return sorted(lessons, key=rank)[:limit]


# 运行级注入登记（outcome 加权用）：run_id → 注入的教训 id 列表。
# 容量有界防泄漏；run 收尾 learn_from_run 时消费清除。
_INJECTED = {}
_INJECTED_MAX = 200


def block_for(task, scope_override=None, *, stable_order=False, run_id=None):
    """生成注入提示词的经验块。命中即计数。返回 (文本, 命中的 id 列表)。

    3A：内置包 + 用户自建包都参与 scope 匹配；3B：带 persona 的包先注入
    「角色设定」块再注入规范正文。
    stable_order=True（docs/migration/07-token-cost.md T1.2'）：教训按 id 排序
    而非 hits——hits 在任务中途变化会让技能块字节级不稳定，打碎供应商的
    前缀缓存（同一任务 8 章应看到完全相同的技能块）。内容不变，只稳排序。

    预算纪律（2026-09-21 巡检实锤引入）：wildcard（scope=*）包动辄数万字，
    39 个全文注入会先把 9000 字全局上限吃光，项目教训排在末尾被整段截掉。
    两道预算：①wildcard 包单包限额（定向命中的包不受限）；②教训保底——
    包区最多吃到「全局上限 − 教训长度」，教训永远完整注入。

    run_id（tradememory 借鉴·outcome 加权）：登记本次注入的教训，run 收尾
    按结局（过审 +1 / 未过 -1）回写 won/lost——好教训在排序中胜出。
    """
    scope = scope_override or task.get("type") or "*"
    parts, used, lesson_ids = [], [], []

    for p in all_packs():
        if scope not in p["scopes"] and "*" not in p["scopes"]:
            continue
        if not _pack_enabled(p["id"]):
            continue
        txt = pack_text(p).strip()
        if not txt and not p.get("persona"):
            continue
        # wildcard 包（仅靠 * 命中，非定向）单包限预算；定向命中不限，走全局
        if scope not in p["scopes"] and len(txt) > WILDCARD_PACK_CHAR_CAP:
            txt = txt[:WILDCARD_PACK_CHAR_CAP] + "\n…（本包超出通配注入预算已截断，完整内容见技能库）"
        # 3B：persona 独立成块（角色设定与规范正文分开，模型更易区分 obey 层级）
        if p.get("persona"):
            parts.append("### 【角色设定：%s】\n%s" % (p["name"], str(p["persona"]).strip()))
        if txt:
            parts.append("### 【%s】\n%s" % (p["name"], txt))
        used.append(p["id"])

    lessons = relevance_top(list_lessons(scope, only_enabled=True), task,
                            MAX_LESSONS_INJECT)
    lesson_part = ""
    if lessons:
        if stable_order:
            lessons.sort(key=lambda x: x.get("id") or "")
        lines = []
        for x in lessons:
            lines.append("- **%s**：%s" % (x["title"], x["content"]))
            used.append(x["id"])
            lesson_ids.append(x["id"])
        lesson_part = ("### 【本项目已沉淀的教训（历史评审反复出现，务必规避）】\n"
                       + "\n".join(lines))

    if not parts and not lesson_part:
        return "", []

    header = "## 经验库（写作/工程规范 + 历史教训，必须遵守）\n\n"
    budget = max(600, MAX_INJECT_CHARS - len(lesson_part))
    body = "\n\n".join(parts)
    if len(body) > budget:
        body = body[:budget] + "\n…（包区已按预算截断，优先保住项目教训）"
    body = (body + "\n\n" + lesson_part) if (body and lesson_part) else (body or lesson_part)
    text = header + body
    if len(text) > MAX_INJECT_CHARS:
        text = text[:MAX_INJECT_CHARS] + "\n…（已截断）"
    if lesson_ids:
        bump_hits(lesson_ids)  # 包 id 不参与教训热度，命中数据只保留一份真源
        if run_id:
            with _LOCK:
                if len(_INJECTED) >= _INJECTED_MAX:
                    _INJECTED.clear()   # 有界兜底：登记超量整体作废（丢信号不丢内存）
                _INJECTED[str(run_id)] = list(lesson_ids)
    return text, used


def note_outcome(run_id, passed):
    """run 收尾回写注入教训的胜负（tradememory outcome 加权）。

    passed=True → won+1（这条教训在场时任务过审）；False → lost+1。
    只清算登记在案的教训；幂等（同一 run 消费后清除登记）。"""
    rid = str(run_id or "")
    if not rid:
        return
    with _LOCK:
        ids = _INJECTED.pop(rid, None)
        if not ids:
            return
        data = _load()
        idset = set(ids)
        for it in (data.get("lessons") or []):
            if it.get("id") in idset:
                key = "won" if passed else "lost"
                it[key] = int(it.get(key) or 0) + 1
        _save(data)


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
请把**可复用到下次同类任务**的经验提炼出来（不要复述本次剧情，不要写泛泛的套话）。
两类都要看：①需要规避的教训（来自问题）；②**已验证有效的做法**（mengram 程序性
记忆借鉴：任务一次通过且分数高时，把「这次做对了什么」提炼成可复用步骤，title
以「做法：」开头，如「做法：先列评分点再逐条应答」）。
只输出一个 ```json 代码块，不要输出其他内容。JSON 结构：
{"lessons": [{"title": "≤14 字的归类（教训直接写；有效做法以「做法：」开头）", "category": "问题分类", "content": "下次必须怎么做/避免什么（≤120 字，具体可执行）"}]}
category 必须从以下固定枚举中选一个（贴合评审维度，不要自创类别）：
__CATEGORIES__
最多 5 条，只保留反复出现或影响过稿/验收的关键项；一次通过的高分运行优先提炼「做法」；没有值得沉淀的就返回空数组。

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
        out.append({"dim": d,
                    "title": "%s 维度反复不达标" % d,
                    "content": "历史运行中 %s 的「%s」多次低于阈值（%s）。写这一维度前先对照经验包自检，"
                               "宁可少写事件也要把该维度做扎实。" % (task.get("type"), d, chs)})
    for d, s in (v.get("global_scores") or {}).items():
        if float(s) < float(v.get("threshold") or 7.0):
            out.append({"dim": d,
                        "title": "全书「%s」被一致性评审扣分" % d,
                        "content": "单章达标但全书「%s」仅 %.1f 分。下一部作品在章纲阶段就要规划该维度的"
                                   "整体曲线（而不是逐章各写各的）。" % (d, float(s))})
    return out[:5]


def learn_from_run(run_id, use_orchestrator=True):
    """运行结束后自动总结教训并沉淀。返回写入条数。"""
    from . import store
    run = store.get_run(run_id)
    if not run:
        return 0
    # outcome 加权（tradememory）：本 run 注入过的教训按结局记胜负——
    # 失败 run 说明在场教训没防住这个问题（lost+1），过审则 won+1。
    # 放最前：无论后续是否沉淀新教训，胜负都要落账。
    try:
        verdict = run.get("verdict") or {}
        note_outcome(run_id, bool(verdict.get("pass")))
    except Exception:
        pass
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
                prompt = (LEARN_PROMPT.replace("__CATEGORIES__", "、".join(LESSON_CATEGORIES))
                          .replace("__TYPE__", str(task.get("type")))
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
                                lessons.append({"title": str(x["title"]), "content": str(x["content"]),
                                                "category": x.get("category")})
        except Exception:
            lessons = []
    if not lessons:
        lessons = _fallback_lessons(task, run)
    n = 0
    for x in lessons:
        if upsert_lesson(task.get("type") or "*", x["title"], x["content"], source=run_id,
                         category=x.get("category"), dim=x.get("dim")):
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
    """经验库总览（给 UI/API）。categories：闭集枚举 + 实际出现过的分类，
    counts 给每类条数，供 UI 下拉过滤与计数显示。
    lessons 每条带展示 kind：「做法」（程序性记忆）/「教训」（规避性）——
    UI 用不同徽章区分（openhuman 记忆可视化借鉴）。持久层是稳定 token
    （procedure/lesson，老数据可能缺失），此处统一翻译+派生兜底。"""
    def _kind(x):
        k = x.get("kind")
        if k == "procedure":
            return "做法"
        if k == "lesson":
            return "教训"
        return "做法" if str(x.get("title") or "").startswith("做法：") else "教训"
    lessons = [{**x, "kind": _kind(x)} for x in list_lessons()]
    counts = {}
    for x in lessons:
        c = x.get("category") or LESSON_UNCATEGORIZED
        counts[c] = counts.get(c, 0) + 1
    cats = list(LESSON_CATEGORIES)
    for c in sorted(counts):     # 历史里出现过的额外分类也带上（不丢过滤项）
        if c not in cats:
            cats.append(c)
    return {"packs": list_packs(), "lessons": lessons,
            "categories": cats, "counts": counts, "total": len(lessons)}


def migrate_lesson_categories():
    """一次性迁移：给分类字段上线前沉淀的教训按 标题→正文 关键词回填 category。

    幂等——已有 category 的条目一律不动（不覆盖复盘官/用户的判断）；
    全部都有分类时零写入。改动前留 .bak。返回回填条数。"""
    with _LOCK:
        data = _load()
        items = data.get("lessons") or []
        dirty = 0
        for it in items:
            if it.get("category"):
                continue
            cat = (_category_from(it.get("title"))
                   or _category_from(it.get("content")))
            if cat:
                it["category"] = cat
                dirty += 1
        if not dirty:
            return 0
        try:
            bak = _FILE.with_suffix(".json.bak")
            if _FILE.is_file() and not bak.is_file():
                bak.write_bytes(_FILE.read_bytes())
        except Exception:
            pass
        _save(data)
        return dirty
