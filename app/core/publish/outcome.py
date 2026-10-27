# -*- coding: utf-8 -*-
"""平台结果回流：这本书在平台上到底怎么了（审核/签约/阅读），下次开书时带上。

为什么要单独一份台账
--------------------
发布台账（`ledger.py`）记的是「我们做了什么动作」，作品登记（`books.json`）
记的是「这本书绑到哪个 book_id」。两者都不记**平台对这本书的判决**：
审核过没过、签约给不给、拒签理由是什么、有多少人读。这正是「经验库越跑
越准」缺的那一环——没有它，每本书都是零经验重开。

与 `novel_feedback`（拒稿复盘）的分工，边界要守住
------------------------------------------------
- 这里只存**平台事实**：谁、哪本、什么结果、平台原话、多少阅读、什么时候。
- `novel_feedback` 存**诊断**：为什么被拒、改了什么、复验结果。那是要人做
  判断的东西，自动写只会造假。
- 两个块都会进提示词，但标签分得很清楚：一个是「平台事实」，一个是
  「复盘意见」。模型把事实当结论、或把意见当规则，都是这里最容易犯的错。

三条写死的规则
--------------
1. **自动与人工分栏**：校准（`sync_published`）只能写 `progress`（客观进度：
   已发布几章、几章在审），绝不动 `stage`。审核结论只认作者登记——平台没
   把「未通过」摆到章节列表上，猜出来的结论会让作者拿着假结论改稿。
2. **人工结论只增不改**：`history` 追加，最新一条为准；旧记录不删（一本书
   可能先被拒、改后过、再签约，时间线本身就是经验）。
3. **只读不猜**：拿不到就留空。`reads=0` 和「没采集」是两件事，前者是数据，
   后者是缺失，界面上必须显示成「—」。
"""
from __future__ import annotations

import html
import json
import re
import threading
import time

from .. import atomic, paths

LOCK = threading.RLock()
MAX_HISTORY = 40
MAX_QUOTE_CHARS = 600
MAX_BLOCK_CHARS = 1100
MAX_RECORDS = 2

PLATFORMS = {"fanqie": "番茄", "qimao": "七猫"}

# 结果阶段：顺序即「一本书的正常生命周期」，界面按这个顺序给选项。
# needs_quote 不是硬校验（平台有时只说「不符合规范」不给细节），而是界面
# 提示：没有原文时，下次评审只知道「被拒过」，不知道该改什么。
STAGES = {
    "submitted": {"label": "已提交待审", "kind": "wait", "needs_quote": False},
    "in_review": {"label": "审核中", "kind": "wait", "needs_quote": False},
    "live": {"label": "已发布", "kind": "good", "needs_quote": False},
    "rejected": {"label": "审核未通过", "kind": "bad", "needs_quote": True},
    "signing_rejected": {"label": "签约被拒", "kind": "bad", "needs_quote": True},
    "signed": {"label": "已签约", "kind": "good", "needs_quote": False},
}
STAGE_ORDER = ("submitted", "in_review", "live", "rejected",
               "signing_rejected", "signed")
BAD_STAGES = frozenset(s for s, v in STAGES.items() if v["kind"] == "bad")

_NOVEL_TYPES = ("novel", "serial_novel", "short_story")


def _now() -> str:
    return time.strftime("%Y-%m-%d %H:%M:%S")


def _file():
    return paths.PUBLISH_DIR / "outcomes.json"


def _clean(value, limit=0) -> str:
    """平台文本是不可信输入：转义标记 + 压掉控制字符 + 截断。

    这段会进提示词，所以它必须当「引用数据」处理，不能当指令。与
    `novel_feedback._prompt_text` 同一纪律（那里是复盘原文，这里是平台原文，
    两条来源都不受我们控制）。
    """
    text = str(value or "").strip()
    if limit:
        text = text[:limit]
    text = html.escape(text, quote=True)
    return re.sub(r"[\x00-\x1f\x7f]",
                  lambda m: "\\u%04x" % ord(m.group(0)), text)


def _load() -> dict:
    path = _file()
    if not path.exists():
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8-sig"))
    except (OSError, ValueError):
        return {}
    return data if isinstance(data, dict) else {}


def _save(data: dict) -> None:
    paths.PUBLISH_DIR.mkdir(parents=True, exist_ok=True)
    atomic.write_json(_file(), data, indent=1)


# ---------------------------------------------------------------- 任务侧信息
def _meta_data(task, plat) -> dict:
    """任务在某平台的作品信息（连载链共用一份；拿不到就当没有）。"""
    try:
        from .. import store
        entry = store.inherited_book_meta(task, plat) or {}
    except Exception:
        entry = ((task.get("book_meta") or {}).get(plat)) or {}
    data = entry.get("data")
    return data if isinstance(data, dict) else {}


def _words(task) -> int:
    try:
        from .. import writing
        got = writing.cached_summary(task) or {}
        return int(got.get("words") or 0)
    except Exception:
        return 0


def _tags_of(data: dict) -> list:
    out = []
    for key in ("tags_theme", "tags_role", "tags_plot"):
        value = data.get(key)
        if isinstance(value, list):
            out.extend(str(x).strip() for x in value if str(x).strip())
        elif isinstance(value, str) and value.strip():
            out.append(value.strip())
    return out[:12]


def _snapshot(task, plat) -> dict:
    data = _meta_data(task, plat)
    return {
        "work": str(task.get("title") or "").strip()[:80],
        "category": str(data.get("category") or "").strip()[:40],
        "tags": _tags_of(data),
        "words": _words(task),
        "platform": plat,
    }


# ---------------------------------------------------------------- 写入
def record(task, platform, stage, quote="", note="", reads=None,
           source="manual") -> dict:
    """登记一条平台结果。返回这本书在该平台的最新条目。

    只有人工登记会走到这里（`source="manual"`）。自动校准请用
    `note_progress`——两者写同一个条目，但字段不同，绝不互相覆盖。
    """
    platform = str(platform or "").strip()
    if platform not in PLATFORMS:
        raise ValueError("platform 必须是 fanqie 或 qimao")
    stage = str(stage or "").strip()
    if stage not in STAGES:
        raise ValueError("未知结果阶段：%s（可选：%s）"
                         % (stage, "、".join(STAGE_ORDER)))
    task_id = str(task.get("id") or "").strip()
    if not task_id:
        raise ValueError("任务缺少 id")
    quote = str(quote or "").strip()[:MAX_QUOTE_CHARS]
    note = str(note or "").strip()[:300]
    reads = _reads_value(reads)
    at = _now()

    with LOCK:
        data = _load()
        book = data.setdefault(task_id, {})
        entry = dict(book.get(platform) or {})
        snap = _snapshot(task, platform)
        history = list(entry.get("history") or [])
        history.append({"stage": stage, "at": at, "source": "manual",
                        "quote": quote[:200]})
        entry.update({
            "platform": platform,
            "stage": stage,
            "at": at,
            "source": "manual",
            "quote": quote,
            "note": note,
            "reads": reads,
            "work": snap["work"] or entry.get("work") or "",
            "category": snap["category"] or entry.get("category") or "",
            "tags": snap["tags"] or entry.get("tags") or [],
            "words": snap["words"] or entry.get("words") or 0,
            "history": history[-MAX_HISTORY:],
        })
        book[platform] = entry
        _save(data)
    return entry


def _reads_value(value):
    """阅读量：空 → None（没采集），不把缺失写成 0。"""
    if value is None or value == "":
        return None
    try:
        n = int(float(value))
    except (TypeError, ValueError):
        return None
    return max(0, n)


def note_progress(task_id, platform, live=0, pending=0, total=0,
                  rejected=0) -> None:
    """自动校准写客观进度（已发布/在审/未通过章数），不动人工结论。

    为什么必须分开：章节列表里「待审核 → 已发布」是可观测的；但**书本级**
    的审核结论与拒签理由通常只在站内信里。用列表猜书本结论，会把「还没审
    完」误报成「整本被拒」，作者会拿着一句没发生过的拒签去改稿。
    章节级「未通过」是真数据（列表上有这一状态），所以照实带出来——它只
    说明某章被打回，不等于整本没戏。
    """
    task_id = str(task_id or "").strip()
    platform = str(platform or "").strip()
    if not task_id or platform not in PLATFORMS:
        return
    at = _now()
    with LOCK:
        data = _load()
        entry = dict((data.get(task_id) or {}).get(platform) or {})
        try:
            snapshot = {"live": max(0, int(live)), "pending": max(0, int(pending)),
                        "rejected": max(0, int(rejected)),
                        "total": max(0, int(total)), "at": at}
        except (TypeError, ValueError):
            return
        if entry.get("progress") == snapshot:
            return                      # 没变化不落盘、不刷时间戳
        entry["progress"] = snapshot
        data.setdefault(task_id, {})[platform] = entry
        _save(data)


# ---------------------------------------------------------------- 读取
def entries(task_id) -> dict:
    """这本书在两平台的最新条目（没有的平台不出现）。"""
    task_id = str(task_id or "").strip()
    if not task_id:
        return {}
    return dict(_load().get(task_id) or {})


def summary(task_id) -> dict:
    """界面口径：阶段标签 + 结论好坏 + 原文 + 时间，未登记给空壳。"""
    raw = entries(task_id)
    out = {}
    for pid, label in PLATFORMS.items():
        entry = raw.get(pid) or {}
        stage = str(entry.get("stage") or "")
        meta = STAGES.get(stage) or {}
        progress = entry.get("progress") or {}
        out[pid] = {
            "platform": pid,
            "platform_label": label,
            "stage": stage,
            "label": meta.get("label") or "未登记",
            "kind": meta.get("kind") or "none",
            "quote": str(entry.get("quote") or ""),
            "note": str(entry.get("note") or ""),
            "reads": entry.get("reads"),
            "at": str(entry.get("at") or ""),
            "work": str(entry.get("work") or ""),
            "category": str(entry.get("category") or ""),
            "words": int(entry.get("words") or 0),
            "history": list(entry.get("history") or [])[-6:],
            "live": int(progress.get("live") or 0),
            "pending": int(progress.get("pending") or 0),
            "rejected": int(progress.get("rejected") or 0),
            "progress_at": str(progress.get("at") or ""),
        }
    return out


# ---------------------------------------------------------------- 提示词注入
def _title_key(value) -> str:
    text = str(value or "").strip().casefold()
    return text.strip("《》〈〉『』「」\"' \t\r\n")


def _task_categories(task) -> dict:
    """新书的目标平台与分类。

    平台优先看结构化字段（`task["platform"]`），其次看标题/目标里点名的
    平台名；两个平台都被点名时按「不清楚」处理——跨平台的规则不能混着用，
    这跟 `novel_feedback.relevant` 的口径一致。
    """
    text = " ".join(str(task.get(k) or "") for k in ("title", "goal")).casefold()
    named = {pid for pid, label in PLATFORMS.items()
             if pid in text or label in text}
    platform = str(task.get("platform") or "").strip()
    if platform in PLATFORMS:
        platforms = {platform}
    else:
        platforms = named if len(named) == 1 else set()
    cats = {}
    for pid in (platforms or PLATFORMS.keys()):
        data = _meta_data(task, pid)
        cat = str(data.get("category") or "").strip()
        if cat:
            cats[pid] = cat.casefold()
    return platforms, cats


def _relevant(task, limit=MAX_RECORDS) -> list:
    """同平台同分类的历史结果，加本书自己的全部记录。

    分类是平台给的受控词（番茄「悬疑惊悚」、七猫「悬疑推理」），两边不一定
    同名，所以只在**同一平台内**按分类比。拿七猫的分类去匹配番茄的书，会把
    不相干的判决搬过来——这正是要避免的「跨平台串味」。
    """
    title = _title_key(task.get("title"))
    platforms, cats = _task_categories(task)
    picked = []
    for task_id, per_plat in _load().items():
        if not isinstance(per_plat, dict):
            continue
        for pid, raw in per_plat.items():
            # 老记录可能没写 platform（早期手动改盘/迁移），以所在键为准，
            # 否则提示词里会冒出「[?]」这种没人看得懂的平台名。
            entry = dict(raw if isinstance(raw, dict) else {}, platform=pid)
            stage = str(entry.get("stage") or "")
            if stage not in STAGES or not entry.get("at"):
                continue
            own = bool(title) and _title_key(entry.get("work")) == title
            same_cat = bool(cats.get(pid)) and \
                str(entry.get("category") or "").strip().casefold() == cats.get(pid)
            if own:
                picked.append((True, str(entry.get("at")), entry))
            elif stage in BAD_STAGES or stage == "signed":
                # 正面的「已签约」也值得知道（同分类什么路子能签），但只有
                # 平台明确时才收；审核中/已发布这类中性阶段不进提示词——
                # 它们不构成对新书的任何约束，只会占预算。
                if platforms and pid not in platforms:
                    continue
                if same_cat:
                    picked.append((False, str(entry.get("at")), entry))
    picked.sort(key=lambda x: (x[0], x[1]), reverse=True)
    return [entry for _own, _at, entry in picked[:max(0, int(limit))]]


def block_for(task, limit=MAX_RECORDS) -> str:
    """给提示词用的「平台结果」块；没有可用的记录返回空串。"""
    if str(task.get("type") or "") not in _NOVEL_TYPES:
        return ""
    try:
        rows = _relevant(task, limit)
    except (OSError, ValueError, KeyError, TypeError):
        return ""      # 台账坏了不能拖垮写作；宁可这轮没有回流
    if not rows:
        return ""
    parts = ["### 【平台结果回流（平台事实，不是编辑意见；不得外推成其他平台的规则或过签概率）】"]
    for entry in rows:
        stage = STAGES.get(str(entry.get("stage") or ""))
        if not stage:
            continue
        head = "[%s]《%s》" % (_clean(PLATFORMS.get(entry.get("platform"), "?"), 8),
                              _clean(entry.get("work"), 40))
        facts = []
        if entry.get("category"):
            facts.append(_clean(entry.get("category"), 20))
        if entry.get("words"):
            facts.append("%d 字" % int(entry["words"]))
        reads = entry.get("reads")
        if reads is not None:
            facts.append("平台阅读 %d" % int(reads))
        line = "%s%s\n结果：%s（登记于 %s）" % (
            head, ("（" + "；".join(facts) + "）") if facts else "",
            stage["label"], _clean(entry.get("at"), 20))
        if entry.get("quote"):
            line += "\n平台原话：「%s」" % _clean(entry.get("quote"), 300)
        if entry.get("note"):
            line += "\n作者备注：%s" % _clean(entry.get("note"), 120)
        if stage["kind"] == "bad":
            line += ("\n（新稿只参考这条事实自查同类问题；被拒不等于同类必被拒，"
                     "也不得据此降低其他平台的写作标准。）")
        rendered = "\n".join(parts)
        if len(rendered) + len(line) + 40 > MAX_BLOCK_CHARS:
            break
        parts.append(line)
    if len(parts) == 1:
        return ""
    return "\n".join(parts)


def preview(task, limit=MAX_RECORDS) -> dict:
    """界面上「下次同题材开书，评审会看到这段」——直接给拼好的文本。"""
    text = block_for(task)
    return {"text": text, "records": len(_relevant(task, limit))}


def view(task) -> dict:
    """接口口径：两平台摘要 + 可选阶段 + 提示词预览。"""
    task_id = str((task or {}).get("id") or "")
    return {
        "task_id": task_id,
        "platforms": summary(task_id),
        "stages": [{"key": key, "label": STAGES[key]["label"],
                    "kind": STAGES[key]["kind"],
                    "needs_quote": STAGES[key]["needs_quote"]}
                   for key in STAGE_ORDER],
        "preview": preview(task or {})["text"],
    }
