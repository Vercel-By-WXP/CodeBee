# -*- coding: utf-8 -*-
"""知识库（Knowledge）：运行产出自动整理形成的可复用知识 + 个人知识管理入口。

与经验库（skills.py）的分工边界：
  - 教训（lessons）记「别这么做」——负面规则，来源=评审暴露的问题（verdict/issues）；
  - 知识（knowledge）记「已知是这样」——领域事实/平台规则/结论/方法论，
    来源=run 产出材料（调研报告/文档）。两者输入源不同，提炼互不双写。

质量闸门：自动提炼的条目一律落 draft（草稿态），人工「转正」后才参与注入——
垃圾知识进了提示词比没有知识更糟；手动新建即视为已确认（直接 approved）。

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

from . import paths, skills

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


def upsert_entry(scope, title, body, tags=None, source="", source_file="",
                 as_of=None, status="draft"):
    """写入/合并一条知识。同 scope 同标题视为同一条（指纹去重）：
    - 已有条目是 approved 且新条是 draft：不覆盖正文（人工确认过的内容优先），
      新内容记入 revisions 作为修订候选，seen+1；
    - 其余情况（已有是 draft，或新条是 approved）：正文取新。
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
        for it in items:
            if it.get("id") != kid:
                continue
            it["seen"] = int(it.get("seen") or 1) + 1
            if status == "approved" or it.get("status") != "approved":
                it["body"] = body
                if status == "approved":
                    it["status"] = "approved"
            else:
                it.setdefault("revisions", []).append(
                    {"at": _now(), "body": body, "source": source})
                it["revisions"] = it["revisions"][-5:]   # 最多留 5 条修订候选
            for tg in tags:
                if tg not in (it.get("tags") or []):
                    it["tags"] = (it.get("tags") or []) + [tg]
            it["tags"] = (it.get("tags") or [])[:8]
            if as_of:
                it["as_of"] = str(as_of)[:10]
            if source:
                it["source"] = source
            if source_file:
                it["source_file"] = source_file
            it["updated_at"] = _now()
            _save(data)
            return it
        it = {"id": kid, "scope": scope, "title": title, "body": body,
              "tags": tags, "status": status, "enabled": True,
              "source": source, "source_file": source_file,
              "as_of": str(as_of or _now()[:10])[:10],
              "revisions": [], "hits": 0, "seen": 1,
              "created_at": _now(), "updated_at": _now(), "kind": "knowledge"}
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
                hit["body"] = str(f["body"]).strip()[:1500]
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
            return (-len(probe & grams), x.get("id") or "")
        entries = sorted(entries, key=rank)
    else:
        entries.sort(key=lambda x: x.get("id") or "")
    entries = entries[:KNOWLEDGE_MAX_INJECT]

    lines, used = [], []
    for x in entries:
        stale = _is_stale(x.get("as_of"))
        mark = ("（事实截至 %s，可能过期，请自行核实时效）" % x.get("as_of") if stale
                else ("（事实截至 %s）" % x.get("as_of") if x.get("as_of") else ""))
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
请从产出中提炼**可长期复用的知识条目**：领域事实、平台规则、结论、方法论。
注意：只提炼事实性/结论性内容；「下次要避免什么」这类负面教训由另一个复盘流程负责，你不要写。
只输出一个 ```json 代码块，不要输出其他内容。JSON 结构：
{"entries": [{"title": "≤20 字的知识标题", "body": "具体结论（≤200 字，自含上下文，脱离本次任务也能看懂）", "tags": ["1-3 个检索标签"], "as_of": "YYYY-MM-DD（事实采集日）"}]}
最多 3 条，只保留有明确复用价值的；产出里没有值得沉淀的就返回空数组。

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
    """运行结束后由编排者从产出材料提炼知识条目（草稿态）。返回写入条数。

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
            as_of = str(x.get("as_of") or "").strip()
            if not re.match(r"^\d{4}-\d{2}-\d{2}$", as_of):
                as_of = _now()[:10]
            if upsert_entry(task.get("type") or "*", x["title"], x["body"],
                            tags=x.get("tags"), source=run_id,
                            as_of=as_of, status="draft"):
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
