# -*- coding: utf-8 -*-
"""知识库（Knowledge）：运行产出自动整理形成的可复用知识 + 个人知识管理入口。

与经验库（skills.py）的分工边界：
  - 经验（lessons）记「下次怎么做」——流程规范、实践方法和评审暴露的教训；
  - 知识（knowledge）记「已知是什么」——领域事实、外部规则和可验证结论。
运行产出中的实践方法会分流进经验库，事实才进入知识库，避免同一方法双写。

质量闸门：条目默认直接转正（approved）参与注入——用户拍板：人工把关太重，
提炼提示词里的「只保留有明确复用价值的」约束兜质量。status 字段保留 draft
枚举向后兼容，手动新建同样直接 approved；migrate_drafts_approved() 在启动时
把历史草稿一次性转正。

注入纪律（与教训反着来）：教训是全量小注（top-8），知识默认不注入——
scope 命中且库里有 approved 条目才成块，按与任务目标的相关性取 top，
独立预算截断（KNOWLEDGE_BUDGET），绝不挤占经验包/圣经的空间。
选择依据在单次注入内恒定（goal 固定、id 唯一），同一任务字节稳定，
不碎供应商前缀缓存。

事实会过期：竞品/平台类知识每条带 as_of（事实采集日），超过 STALE_DAYS
注入时自动标注「可能过期」，提示模型自行核实时效。
"""
from __future__ import annotations

import hashlib
import json
import re
import threading
import time
from pathlib import Path

from . import paths, revisions, skills

_LOCK = threading.RLock()
_FILE = paths.DATA_DIR / "knowledge.json"

KNOWLEDGE_BUDGET = 3000     # 注入块字符预算（独立于经验包的 MAX_INJECT_CHARS）
KNOWLEDGE_MAX_INJECT = 6    # 单次注入条数上限
STALE_DAYS = 90             # as_of 超过该天数标注「可能过期」


def _now():
    return time.strftime("%Y-%m-%d %H:%M:%S")


def _load():
    try:
        return json.loads(_FILE.read_text(encoding="utf-8"))
    except Exception:
        return {}


def _save(data):
    try:
        _FILE.parent.mkdir(parents=True, exist_ok=True)
        _FILE.write_text(json.dumps(data, ensure_ascii=False, indent=1),
                         encoding="utf-8")
    except Exception:
        pass


def _entry_id(scope, title):
    """去重指纹：scope + 归一化标题（沿用教训库的 _norm_title 标点剥离）。"""
    h = hashlib.sha256(("%s|%s" % (scope, skills._norm_title(title)))
                       .encode("utf-8")).hexdigest()[:12]
    return "kb-" + h


def list_entries(scope=None, status=None, tag=None, only_enabled=False):
    with _LOCK:
        items = list(_load().get("entries") or [])
    if scope:
        items = [x for x in items if x.get("scope") in (scope, "*")]
    if status:
        items = [x for x in items if (x.get("status") or "draft") == status]
    if tag:
        items = [x for x in items if tag in (x.get("tags") or [])]
    if only_enabled:
        items = [x for x in items if x.get("enabled", True)]
    items.sort(key=lambda x: (x.get("updated_at") or x.get("created_at") or ""),
               reverse=True)
    return items


def revision_for_task(task) -> dict:
    """Return approved knowledge references selected by task scope."""
    scope = str((task or {}).get("type") or "*")
    entries = list_entries(scope=scope, status="approved", only_enabled=True)
    refs = []
    for entry in entries:
        body = entry.get("body") or ""
        digest = revisions.content_sha256(body)
        stored_digest = str(entry.get("content_sha256") or "")
        drift = bool(stored_digest and stored_digest != digest)
        refs.append({"id": entry.get("id") or "",
                     "revision_id": (entry.get("revision_id")
                                     if stored_digest == digest else
                                     revisions.revision_id("kbrev", digest)) or
                     revisions.revision_id("kbrev", digest),
                     "content_sha256": digest,
                     "revision_drift": drift})
    payload = {"scope": scope, "entries": refs}
    digest = revisions.stable_digest(payload)
    return {"revision_id": revisions.revision_id("knowledge", digest),
            "content_sha256": digest, "scope": scope, "entries": refs}


def _title_sim(a, b):
    """标题近似度：字符 bigram 的包含度（交集 / 较短者，中文友好零分词）。
    用包含度而非 Jaccard：「X」vs「X 指南」的较短者完全被包含 →1.0，
    Jaccard 却因并集分母只有 0.71——追加后缀是自动提炼最常见的重复形态。
    复用 skills 的 bigram 工具，口径与教训库一致。"""
    ga, gb = skills._text_bigrams(a), skills._text_bigrams(b)
    if not ga or not gb:
        return 0.0
    return len(ga & gb) / float(min(len(ga), len(gb)))


_NEAR_DUP_SIM = 0.8    # 同 scope 标题包含度 ≥0.8 视为同一条（近似题合并）


def upsert_entry(scope, title, body, tags=None, source="", source_file="",
                 as_of=None, status="draft", confidence=None):
    """写入/合并一条知识。同 scope 同标题视为同一条（指纹去重）：
    - 已有条目是 approved 且新条是 draft：不覆盖正文（人工确认过的内容优先），
      新内容记入 revisions 作为修订候选，seen+1；
    - 其余情况（已有是 draft，或新条是 approved）：正文取新。
    近似题合并（自动学习防膨胀）：同 scope 标题 bigram 包含度 ≥0.8 也
    视为同一条——自动提炼每次 done 运行都跑，「X 优化」与「X 优化指南」
    会各建一条把库撑爆；归并走与精确同题完全相同的合并/修订语义。
    返回条目；title/body 为空返回 None。"""
    title = str(title or "").strip()[:80]
    body = str(body or "").strip()[:1500]
    if not title or not body:
        return None
    tags = [str(t).strip()[:20] for t in (tags or []) if str(t).strip()][:6]
    status = status if status in ("draft", "approved") else "draft"
    scope = str(scope or "*").strip() or "*"
    kid = _entry_id(scope, title)
    with _LOCK:
        data = _load()
        items = data.setdefault("entries", [])
        hit = next((x for x in items if x.get("id") == kid), None)
        if hit is None:
            # 近似题扫描（精确未命中才做；<4 字符标题噪声大，只认精确同题）
            if len(title) >= 4:
                for x in items:
                    if x.get("scope") != scope:
                        continue
                    xt = str(x.get("title") or "")
                    if len(xt) >= 4 and _title_sim(title, xt) >= _NEAR_DUP_SIM:
                        hit = x
                        break
        if hit is not None:
            it = hit
            it["seen"] = int(it.get("seen") or 1) + 1
            old_body = str(it.get("body") or "")
            old_rev = it.get("revision_id") or ""
            new_rev = revisions.make_revision("kbrev", body, source=source)
            if status == "approved" or it.get("status") != "approved":
                if old_body and old_body != body:
                    it.setdefault("revisions", []).append(
                        revisions.revision_record(
                            "kbrev", old_body, source=it.get("source") or "",
                            extra={"body": old_body, "revision_id": old_rev or
                                   revisions.revision_id("kbrev", revisions.content_sha256(old_body))}))
                    it["revisions"] = it["revisions"][-5:]
                it["body"] = body
                it.update({k: new_rev[k] for k in ("revision_id", "content_sha256")})
                if status == "approved":
                    it["status"] = "approved"
            else:
                it.setdefault("revisions", []).append(
                    revisions.revision_record("kbrev", body, source=source,
                                               extra={"body": body}))
                it["revisions"] = it["revisions"][-5:]   # 最多留 5 条修订候选
            for tg in tags:
                if tg not in (it.get("tags") or []):
                    it["tags"] = (it.get("tags") or []) + [tg]
            it["tags"] = (it.get("tags") or [])[:8]
            if as_of:
                it["as_of"] = str(as_of)[:10]
            if confidence:   # 合并时也可提升/回落置信度（high 覆盖 medium 合理）
                it["confidence"] = confidence
            if source:
                it["source"] = source
            if source_file:
                it["source_file"] = source_file
            it["updated_at"] = _now()
            _save(data)
            return it
        rev = revisions.make_revision("kbrev", body, source=source)
        it = {"id": kid, "scope": scope, "title": title, "body": body,
              "tags": tags, "status": status, "enabled": True,
              "source": source, "source_file": source_file,
              "as_of": str(as_of or _now()[:10])[:10],
              "confidence": confidence or "medium",
              "revisions": [], "hits": 0, "seen": 1,
              "created_at": _now(), "updated_at": _now(), "kind": "knowledge"}
        it.update({k: rev[k] for k in ("revision_id", "content_sha256")})
        items.append(it)
        _save(data)
        return it


def entry_op(entry_id, op, fields=None):
    """新建/编辑/转正/启停/删除。返回错误或 None。

    create：手动新建直接 approved（人工录入即确认）；edit：改标题/正文/标签/
    范围/账龄，不改状态——转正必须显式走 approve，保持人工闸门。
    """
    if op not in ("create", "edit", "approve", "enable", "disable", "delete"):
        return "未知操作 " + str(op)
    if op == "create":
        f = fields or {}
        it = upsert_entry(f.get("scope") or "*", f.get("title") or "",
                          f.get("body") or "", tags=f.get("tags"),
                          as_of=(f.get("as_of") or "").strip() or None,
                          status="approved")
        return None if it else "标题与正文不能为空"
    with _LOCK:
        data = _load()
        items = data.get("entries") or []
        hit = next((x for x in items if x.get("id") == entry_id), None)
        if not hit:
            return "知识条目不存在"
        if op == "delete":
            data["entries"] = [x for x in items if x.get("id") != entry_id]
        elif op == "approve":
            hit["status"] = "approved"
            hit["updated_at"] = _now()
        elif op == "edit":
            f = fields or {}
            if str(f.get("title") or "").strip():
                hit["title"] = str(f["title"]).strip()[:80]
            if str(f.get("body") or "").strip():
                new_body = str(f["body"]).strip()[:1500]
                old_body = str(hit.get("body") or "")
                if old_body != new_body:
                    hit.setdefault("revisions", []).append(
                        revisions.revision_record(
                            "kbrev", old_body, source=hit.get("source") or "",
                            extra={"body": old_body}))
                    hit["revisions"] = hit["revisions"][-5:]
                    hit["body"] = new_body
                    hit.update({k: revisions.make_revision("kbrev", new_body)[k]
                                for k in ("revision_id", "content_sha256")})
            if "tags" in f:
                hit["tags"] = [str(t).strip()[:20]
                               for t in (f.get("tags") or []) if str(t).strip()][:8]
            if str(f.get("scope") or "").strip():
                hit["scope"] = str(f["scope"]).strip()[:30]
            if str(f.get("as_of") or "").strip():
                hit["as_of"] = str(f["as_of"]).strip()[:10]
            hit["updated_at"] = _now()
        else:
            hit["enabled"] = (op == "enable")
        _save(data)
    return None


def _is_stale(as_of):
    try:
        d = time.strptime(str(as_of or "")[:10], "%Y-%m-%d")
        return (time.mktime(time.localtime()) - time.mktime(d)) / 86400.0 > STALE_DAYS
    except Exception:
        return False


def block_for(task):
    """生成注入提示词的知识块。默认不注入：scope 命中且存在 approved 条目才成块，
    按与任务目标/上下文的相关性取 top（复用教训库的 bigram 检索），独立预算截断。
    无命中返回 ""——字节稳定，不碎供应商前缀缓存。"""
    scope = (task or {}).get("type") or "*"
    entries = [x for x in list_entries(scope, only_enabled=True)
               if (x.get("status") or "draft") == "approved"]
    if not entries:
        return ""
    probe = (skills._text_bigrams((task or {}).get("goal"))
             | skills._text_bigrams((task or {}).get("context"))
             | skills._text_bigrams((task or {}).get("title")))
    if probe:
        def rank(x):
            grams = (skills._text_bigrams(x.get("title"))
                     | skills._text_bigrams(x.get("body"))
                     | set(x.get("tags") or []))
            # 过期降权（inkos 检索保留来源/位置的启发）：同等相关性下，
            # 可能过期的事实排在新鲜事实之后——旧知识不该压过新知识。
            # 命中热度平级决胜（教训库 karma 的轻量同构）：_bump_hits 记的
            # 注入命中数此前只进不出；相关性同档时高频命中条目优先——
            # 被反复召回的知识已被任务面验证过可用性，压过从未被选中的
            return (-len(probe & grams), -(int(x.get("hits") or 0)),
                    1 if _is_stale(x.get("as_of")) else 0,
                    x.get("id") or "")
        entries = sorted(entries, key=rank)
    else:
        entries.sort(key=lambda x: x.get("id") or "")
    entries = entries[:KNOWLEDGE_MAX_INJECT]

    lines, used = [], []
    for x in entries:
        stale = _is_stale(x.get("as_of"))
        mark = ("（事实截至 %s，可能过期，请自行核实时效）" % x.get("as_of") if stale
                else ("（事实截至 %s）" % x.get("as_of") if x.get("as_of") else ""))
        # 置信标注（引用核验续）：medium 不标（默认噪音为零），low 早已
        # 在学习口丢弃——只有 high 显式加冕，模型据以分配采信权重
        conf = str(x.get("confidence") or "medium").strip().lower()
        if conf == "high":
            mark += "［有据］"
        lines.append("- **%s**%s：%s" % (x["title"], mark, x["body"]))
        used.append(x["id"])
    text = "## 知识库（已确认的领域知识，供参考）\n\n" + "\n".join(lines)
    if len(text) > KNOWLEDGE_BUDGET:
        text = text[:KNOWLEDGE_BUDGET] + "\n…（已截断）"
    if used:
        _bump_hits(used)
    return text


def _bump_hits(ids):
    with _LOCK:
        data = _load()
        dirty = False
        for it in (data.get("entries") or []):
            if it.get("id") in ids:
                it["hits"] = int(it.get("hits") or 0) + 1
                dirty = True
        if dirty:
            _save(data)


# ---------------------------------------------------------------- 自动整理

KNOWLEDGE_PROMPT = """你是编排系统的知识管理员。下面是一次任务的目标与它的产出材料（调研报告/文档等）。
请从产出中提炼可长期复用的内容，并明确分成两类：
- fact：回答「是什么」，只含领域事实、外部规则和可验证结论，进入知识库；
- practice：回答「怎么做」，含流程、操作方法和正向实践，进入经验库；此类必须填写 category。
「下次要避免什么」这类负面教训由评审复盘流程负责，不要重复提炼。

**可信度分级（引用核验借鉴）**：每条 fact 必须给出 confidence：
- "high"：产出材料中有明确来源/数据支撑（引用了来源、给了数字口径）；
- "medium"：材料内部自洽但未标来源（模型整理所得）；
- "low"：你不确定、可能过时或与材料其他部分矛盾——这类宁可不要，直接丢弃。
fact 缺 confidence 视为 medium。

只输出一个 ```json 代码块，不要输出其他内容。JSON 结构：
{"entries": [{"kind": "fact 或 practice", "title": "≤20 字的标题", "body": "具体内容（≤200 字，自含上下文，脱离本次任务也能看懂）", "category": "practice 的经验分类", "tags": ["1-3 个检索标签"], "as_of": "YYYY-MM-DD（fact 的事实采集日）", "confidence": "high/medium/low（fact 必填）"}]}
category 只能从以下枚举选择：__CATEGORIES__。
最多 3 条，只保留有明确复用价值的；low 置信的直接丢弃；产出里没有值得沉淀的就返回空数组。

## 任务类型
__TYPE__

## 任务目标
__GOAL__

## 产出材料（节选）
__MATERIAL__"""


def _read_text(p):
    """产物读取：utf-8 严格优先，GBK 兜底（codex/pwsh 落盘编码不一）。"""
    try:
        return p.read_text(encoding="utf-8")
    except Exception:
        try:
            return p.read_text(encoding="gbk", errors="replace")
        except Exception:
            return ""


def _pick_material(run):
    """挑本次 run 的产出材料喂给编排者：最近的 .md/.txt 文档各截 4000 字，
    外加运行目录里的评审报告。总量封顶 10000 字。"""
    from . import store
    try:
        wd, files = store.run_artifacts(run.get("id"))
    except Exception:
        return ""
    chunks = []
    try:
        rp = paths.RUNS_DIR / (run.get("id") or "") / "report.md"
        if rp.is_file():
            t = _read_text(rp)
            if t.strip():
                chunks.append("### 评审报告（节选）\n" + t[:4000])
    except Exception:
        pass
    docs = [f for f in (files or [])
            if str(f.get("name", "")).lower().endswith((".md", ".txt"))
            and int(f.get("size") or 0) < 500_000]
    for f in docs[:3]:
        t = _read_text(Path(wd) / f["name"])
        if t.strip():
            chunks.append("### " + f["name"] + "\n" + t[:4000])
    return "\n\n".join(chunks)[:10000]


def learn_from_run(run_id):
    """运行结束后从产出提炼事实与实践并分流入库。返回总写入条数。

    知识没有便宜的兜底路径：无编排者/无产出/非真实运行（mock）一律静默跳过，
    宁缺毋滥——教训库的规则兜底搬到这里只会制造垃圾知识。只有正常跑完（done）
    的运行才提炼：失败/取消的运行产物是半成品，据此沉淀的知识会污染知识库。
    """
    from . import store
    run = store.get_run(run_id)
    if not run:
        return 0
    if (run.get("status") or "") != "done":
        return 0
    task = store.get_task(run.get("task_id")) if run.get("task_id") else None
    if not task:
        return 0
    if all((s.get("agent") or "").startswith("mock") for s in (run.get("steps") or [])):
        return 0
    material = _pick_material(run)
    if not material:
        return 0
    n = 0
    try:
        from . import modelhub, runner
        orch = modelhub.resolve_orchestrator()
        if not orch:
            return 0
        prov, model = orch
        prompt = (KNOWLEDGE_PROMPT
                  .replace("__CATEGORIES__", "、".join(skills.LESSON_CATEGORIES))
                  .replace("__TYPE__", str(task.get("type")))
                  .replace("__GOAL__", (task.get("goal") or "")[:600])
                  .replace("__MATERIAL__", material))
        res = modelhub.chat(prov["id"], model, prompt, max_tokens=4000, timeout=300)
        if not res.get("ok"):
            return 0
        data = runner.extract_json(res.get("text") or "")
        raw = (data or {}).get("entries") if isinstance(data, dict) else None
        if not isinstance(raw, list):
            return 0
        for x in raw[:3]:
            if not (isinstance(x, dict) and x.get("title") and x.get("body")):
                continue
            kind = str(x.get("kind") or "fact").strip().lower()
            if kind == "practice":
                if skills.upsert_lesson(task.get("type") or "*", x["title"],
                                        x["body"], source=run_id,
                                        category=x.get("category"),
                                        dim="%s %s" % (x["title"], x["body"])):
                    n += 1
                continue
            # 可信度分级（引用核验借鉴）：low 直接丢弃（宁缺毋滥），
            # 其余归一 high/medium 并随条目落盘；缺失视为 medium
            conf = str(x.get("confidence") or "medium").strip().lower()
            if conf not in ("high", "medium"):
                if conf == "low":
                    continue
                conf = "medium"
            as_of = str(x.get("as_of") or "").strip()
            if not re.match(r"^\d{4}-\d{2}-\d{2}$", as_of):
                as_of = _now()[:10]
            if upsert_entry(task.get("type") or "*", x["title"], x["body"],
                            tags=x.get("tags"), source=run_id,
                            as_of=as_of, status="approved", confidence=conf):
                n += 1
    except Exception:
        return n
    return n


def learn_async(run_id):
    """异步提炼（不阻塞任务收尾，与教训沉淀同款语义）。"""
    def _run():
        try:
            learn_from_run(run_id)
        except Exception:
            pass
    threading.Thread(target=_run, name="kb-learn", daemon=True).start()


def migrate_drafts_approved():
    """一次性迁移：草稿闸门退役（默认直接转正），把历史 draft 全部转正。

    幂等——没有 draft 时零写入。返回转正条数。"""
    with _LOCK:
        data = _load()
        items = data.get("entries") or []
        dirty = [x for x in items if (x.get("status") or "draft") != "approved"]
        if not dirty:
            return 0
        for x in dirty:
            x["status"] = "approved"
            x["updated_at"] = _now()
        _save(data)
        return len(dirty)


def view():
    """知识库总览（给 UI/API）：条目 + 标签聚合 + 草稿数 + 账龄标记。"""
    entries = list_entries()
    tags, drafts = {}, 0
    for x in entries:
        for tg in (x.get("tags") or []):
            tags[tg] = tags.get(tg, 0) + 1
        if (x.get("status") or "draft") != "approved":
            drafts += 1
        x["stale"] = _is_stale(x.get("as_of"))
    return {"entries": entries,
            "tags": sorted(tags, key=lambda t: (-tags[t], t)),
            "tag_counts": tags, "drafts": drafts, "total": len(entries)}
