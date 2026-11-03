# -*- coding: utf-8 -*-
"""禅道修复链路的知识桥：把 skills/zentao-autofix 的 zfix 引擎接进 zentao.py。

既有链路（认领→排查→建任务→对账→resolve）强在流转，弱在记忆：同一个错误
第二次出现不认识、根因不沉淀、修复任务拿不到历史教训。本模块补上：
  指纹    —— 同类错误跨 bug_id 可识别（抹行号/地址/目录前缀，保留文件名）
  根因    —— 16 类固定词表初判（确定性，可聚合）
  沉淀    —— resolve/失败都落 fix 档案（知识库唯一写入口）
  预防    —— 重复指纹蒸馏成规则，注入修复任务的 goal（AI 开工前必读）

设计纪律：
  1. 桥要薄、异常全兜——本模块任何故障都不允许影响禅道主链路（每层调用
     都有 try/except 返回中性值）；
  2. 引擎物理上只有一份（skills/zentao-autofix/zfix，随 npm 包分发），
     本模块只做动态加载与数据落地位置适配，绝不复制算法；
  3. 知识落 <data>/zfix-knowledge（跟随 TUTTI_DATA 重定向），不写 ~/.zfix
     ——npm 全局安装的数据目录纪律（见 paths.py）；
  4. 引擎不可用（目录缺失/导入失败）时优雅降级：指纹给空、规则给空、
     不落档案，主链路行为与没有本模块时完全一致。

加载方式说明：sys.path 前插 + import zfix.*。zfix 是零依赖纯标准库包，
名字在 pip 生态无冲突；insert(0) 保证优先于任何同名 site-packages。
"""
from __future__ import annotations

import logging
import re
import sys
import threading
import time
from pathlib import Path

from . import paths

log = logging.getLogger(__name__)

_LOCK = threading.RLock()
_ENGINE = {}          # 惰性加载的引擎模块表 {"knowledge": …, "triage": …, "models": …}
_LOAD_TRIED = False   # 加载只试一次：目录不存在时别每条 bug 都撞一遍 IO
_LEARN_TS = {"last": 0.0}
_LEARN_INTERVAL = 6 * 3600     # 蒸馏节流：6 小时一次（automation tick 高频触发）

# 与 zfix.knowledge._PREVENTION_TEMPLATE 同源的中文标签（展示用；不复制规则正文）
CAUSE_CN = {
    "null_ref": "空值/未初始化", "boundary": "边界与越界", "type_mismatch": "类型不匹配",
    "concurrency": "并发与竞态", "resource": "资源泄漏", "config": "配置与环境",
    "dependency": "依赖与接口", "network": "网络与超时", "permission": "权限与鉴权",
    "encoding": "编码与本地化", "state": "状态机遗漏", "compat": "兼容性回归",
    "data": "脏数据", "ui": "界面与交互", "perf": "性能与容量", "unknown": "未归类",
}

_TAG_RE = re.compile(r"<[^>]+>")
_WS_RE = re.compile(r"\s+")


def _strip_html(raw):
    """轻量 HTML 剥离（steps 是禅道富文本；不复用 zentao._strip_html 避免循环 import）。"""
    txt = _TAG_RE.sub(" ", str(raw or ""))
    return _WS_RE.sub(" ", txt).replace("&nbsp;", " ").strip()


def engine_dir():
    return Path(paths.ROOT) / "skills" / "zentao-autofix"


# ---------------------------------------------------------------- 引擎加载


def _load_engine():
    """惰性加载 zfix 引擎；失败返回 {}（此后所有能力优雅降级）。"""
    global _LOAD_TRIED
    if _LOAD_TRIED:
        return _ENGINE
    _LOAD_TRIED = True
    edir = engine_dir()
    if not (edir / "zfix" / "knowledge.py").is_file():
        log.info("zfix_bridge: 引擎不存在（%s），禅道知识能力降级", edir)
        return {}
    s = str(edir)
    if s not in sys.path:
        sys.path.insert(0, s)
    try:
        from zfix import knowledge as k, models as m, triage as t

        # 知识落盘位置改指 CodeBee 数据目录：lambda 每次现取 paths.DATA_DIR，
        # TUTTI_DATA 重定向（测试多实例）自动跟随
        k.user_home = lambda: str(Path(paths.DATA_DIR) / "zfix-knowledge")
        _ENGINE.update({"knowledge": k, "models": m, "triage": t})
        log.info("zfix_bridge: zfix 引擎已加载（%s）", edir)
    except Exception:
        log.exception("zfix_bridge: zfix 引擎导入失败，知识能力降级")
        _ENGINE.clear()
    return _ENGINE


def available():
    """引擎是否可用（UI/接口探针用）。"""
    return bool(_load_engine())


# ---------------------------------------------------------------- bug → 引擎模型


def _to_bug(bug, project="zentao"):
    """禅道 bug dict → zfix.models.Bug。字段口径见 zentao.list_bugs。"""
    eng = _load_engine()
    m = eng.get("models") if eng else None
    if m is None or not bug:
        return None
    steps = _strip_html(bug.get("steps"))
    text = "\n".join(x for x in (str(bug.get("title") or ""), steps) if x)
    return m.Bug(
        id=str(bug.get("id") or ""),
        title=str(bug.get("title") or ""),
        project=str(project or "zentao"),
        steps=steps,
        stack="",
        severity=str(bug.get("severity") or "3"),
        extra={"text": text[:4000]},
    )


def fingerprint(bug, project="zentao"):
    """指纹；引擎降级时返回空串（调用方按「无指纹」处理，不阻断）。"""
    eng = _load_engine()
    bug2 = _to_bug(bug, project)
    if not eng or bug2 is None:
        return ""
    try:
        return eng["triage"].bug_fingerprint(bug2)
    except Exception:
        log.debug("zfix_bridge: 指纹计算失败", exc_info=True)
        return ""


def classify_cause(*texts):
    """根因初判（喂排查结论/运行错误/验证文本，越多越准）；降级返回 unknown。"""
    eng = _load_engine()
    if not eng:
        return "unknown"
    try:
        return eng["triage"].classify_root_cause("\n".join(str(x or "") for x in texts))
    except Exception:
        return "unknown"


def cause_label(cause):
    return CAUSE_CN.get(str(cause or ""), cause or "未归类")


# ---------------------------------------------------------------- 读取（历史/规则）


def repeat_info(fp):
    """同指纹历史修复概况：{'count': N, 'last': 'YYYY-MM-DD', 'bugs': [id…]}。

    count>0 即「同类问题第 N+1 次出现」——认领文案与修复 goal 都该喊出来。
    """
    eng = _load_engine()
    if not eng or not fp:
        return {"count": 0, "last": "", "bugs": []}
    try:
        records = eng["knowledge"].KnowledgeBase().fixes_for_fingerprint(fp, limit=50)
        return {
            "count": len(records),
            "last": str(records[0].created_at or "")[:10] if records else "",
            "bugs": [str(r.bug_id) for r in records[:8]],
        }
    except Exception:
        log.debug("zfix_bridge: 历史查询失败", exc_info=True)
        return {"count": 0, "last": "", "bugs": []}


def goal_extras(bug, project="zentao"):
    """给修复任务 goal 的附加段：历史同类修法 + 防复发规则。

    返回可直接拼接的文本（含换行）；无内容/降级时返回 ""（goal 原样）。
    规则上限 5 条：goal 是给 AI 的一次性上下文，塞多了等于没有。
    """
    eng = _load_engine()
    if not eng or not bug:
        return ""
    try:
        m = _to_bug(bug, project)
        if m is None:
            return ""
        fp = eng["triage"].bug_fingerprint(m)
        m.fingerprint = fp
        kb = eng["knowledge"].KnowledgeBase()
        history = kb.fixes_for_fingerprint(fp, limit=2)
        matched = kb.match(m, limit=5)
        lines = []
        if history:
            lines.append("【历史经验】本缺陷与以下已修复问题同指纹（第 %d 次出现），"
                         "先对照历史修法再动手：" % (len(history) + 1))
            for r in history:
                when = str(r.created_at or "")[:10]
                why = (r.reason or r.summary or "")[:160]
                files = "、".join(r.files[:4]) if r.files else ""
                lines.append("  - #%s（%s·%s）：%s%s"
                             % (r.bug_id, when, cause_label(r.root_cause), why,
                                "｜改过 %s" % files if files else ""))
        if matched:
            if not lines:
                lines.append("【历史经验】知识库命中相关错误模式：")
            for p, score in matched[:5]:
                lines.append("  - %s：%s" % (p.title, p.rule))
        if lines:
            lines.append("  （以上由缺陷知识库自动附注；同指纹问题反复出现时优先找共性根因，"
                         "别只修表象。）")
            return "\n".join(lines)
        return ""
    except Exception:
        log.debug("zfix_bridge: goal 附加段生成失败", exc_info=True)
        return ""


# ---------------------------------------------------------------- 档案与蒸馏


def record_fix(bug, project, root_cause, reason, files=None, verify_ok=None,
               outcome="fixed"):
    """落一条修复档案（知识库唯一写入口）。失败静默（档案缺一条不影响主链路）。

    outcome: fixed（修好 resolve）/ failed（修复任务失败，也值得记——反复
    修不动的指纹本身就是最高价值的预防线索）。
    """
    eng = _load_engine()
    if not eng or not bug:
        return
    try:
        m = _to_bug(bug, project)
        if m is None:
            return
        fp = eng["triage"].bug_fingerprint(m)
        if not fp:
            return
        rec = eng["models"].FixRecord(
            bug_id=str(bug.get("id") or ""),
            project=str(project or "zentao"),
            fingerprint=fp,
            root_cause=str(root_cause) if root_cause in CAUSE_CN else "unknown",
            reason=str(reason or "")[:400],
            summary=_strip_html(bug.get("title"))[:120],
            files=[str(f) for f in (files or [])][:12],
            verify={"ok": bool(verify_ok)} if verify_ok is not None else {},
            duration_s=0,
            agent="codebee-zentao",
            tags=[outcome],
        )
        eng["knowledge"].KnowledgeBase().add_fix(rec)
    except Exception:
        log.debug("zfix_bridge: 档案落盘失败", exc_info=True)


def learn_if_due(force=False):
    """蒸馏重复指纹为预防模式（节流 6h）。automation tick 高频触发，必须便宜。

    返回本次数目（0=无/未到点）；失败静默。
    """
    eng = _load_engine()
    if not eng:
        return 0
    now = time.time()
    with _LOCK:
        if not force and now - _LEARN_TS["last"] < _LEARN_INTERVAL:
            return 0
        _LEARN_TS["last"] = now
    try:
        kb = eng["knowledge"].KnowledgeBase()
        candidates = kb.learn(min_occurrences=2)
        for p in candidates:
            kb.save_pattern(p)
        return len(candidates)
    except Exception:
        log.debug("zfix_bridge: 蒸馏失败", exc_info=True)
        return 0


def knowledge_view():
    """知识库概况（zentao.view() 附带透出；引擎降级返回 {"available": False}）。"""
    eng = _load_engine()
    if not eng:
        return {"available": False}
    try:
        kb = eng["knowledge"].KnowledgeBase()
        fixes = kb.fixes()
        by_cause = {}
        fp_counts = {}
        for r in fixes:
            by_cause[r.root_cause] = by_cause.get(r.root_cause, 0) + 1
            if r.fingerprint:
                fp_counts[r.fingerprint] = fp_counts.get(r.fingerprint, 0) + 1
        repeats = {k: v for k, v in fp_counts.items() if v > 1}
        return {
            "available": True,
            "fixes": len(fixes),
            "patterns": len(kb.patterns),
            "repeat_fingerprints": len(repeats),
            "by_cause": sorted(
                ({"cause": k, "label": cause_label(k), "count": v}
                 for k, v in by_cause.items()), key=lambda x: -x["count"]),
        }
    except Exception:
        return {"available": False}


def _test_reset():
    """测试钩子：重置加载与节流状态（配合 TUTTI_DATA 临时目录重定向）。"""
    global _LOAD_TRIED
    with _LOCK:
        _LEARN_TS["last"] = 0.0
        _LOAD_TRIED = False
        _ENGINE.clear()
        s = str(engine_dir())
        if s in sys.path:
            sys.path.remove(s)
        for mod_name in [m for m in list(sys.modules) if m.startswith("zfix")]:
            del sys.modules[mod_name]
