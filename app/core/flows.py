# -*- coding: utf-8 -*-
"""任务类型（流程）注册表：内置类型 + 用户自定义流程。

两种引擎：
  code   实现 → 确定性验证 → 跨厂商评审 → 自动修复/换将；
  review 起草 → 多维评审打分 → 修订循环 → 发布门禁（小说/文档/翻译/调研…通用）。

内置流程不可删除、不可覆盖（保证回归与演示稳定）；自定义流程存
data/flows.json，字段在创建任务时固化到任务上（之后改流程不影响已建任务）。
flow 可选覆盖 draft_prompt / critique_prompt（占位符同内置模板）。
"""
from __future__ import annotations

import json
import re
import threading

from . import paths

_LOCK = threading.RLock()
_FILE = paths.DATA_DIR / "flows.json"

ENGINES = ("code", "review")

BUILTIN_FLOWS = [
    {"id": "code", "name": "代码", "icon": "💻", "engine": "code", "builtin": True,
     "goal_hint": "要实现/修复什么（一句话）",
     "note": "实现 → 验证命令 → 跨厂商评审 → 自动修复/换将"},
    {"id": "novel", "name": "小说", "icon": "📖", "engine": "review", "builtin": True,
     "manuscript": "manuscript.md",
     "rubric": ["情节", "人物", "文笔", "节奏", "吸引力"],
     "threshold": 7.0, "rounds": 2,
     "goal_hint": "写什么（题材 / 篇幅 / 风格）",
     "note": "起草 → 多维评审 → 修订循环 → 发布门禁"},
    {"id": "doc", "name": "文档", "icon": "📝", "engine": "review", "builtin": True,
     "manuscript": "document.md",
     "rubric": ["准确性", "结构清晰", "表达流畅", "实用价值"],
     "threshold": 7.0, "rounds": 2,
     "goal_hint": "要写什么文档、给谁看",
     "note": "起草 → 多维评审 → 修订循环 → 发布门禁"},
    {"id": "translation", "name": "翻译", "icon": "🌐", "engine": "review", "builtin": True,
     "manuscript": "translation.md",
     "rubric": ["忠实度", "流畅度", "术语一致性", "风格贴合"],
     "threshold": 7.0, "rounds": 2,
     "goal_hint": "翻译什么（源文本位置 / 目标语言 / 要求）",
     "note": "起草 → 多维评审 → 修订循环 → 发布门禁"},
    {"id": "research", "name": "调研报告", "icon": "🔍", "engine": "review", "builtin": True,
     "manuscript": "report.md",
     "rubric": ["全面性", "深度", "论据可靠", "可读性", "结论质量"],
     "threshold": 7.0, "rounds": 2,
     "goal_hint": "调研什么问题、产出给谁用",
     "note": "起草 → 多维评审 → 修订循环 → 发布门禁"},
    {"id": "speech", "name": "演讲稿", "icon": "🎤", "engine": "review", "builtin": True,
     "manuscript": "speech.md",
     "rubric": ["主题聚焦", "结构", "感染力", "语言风格"],
     "threshold": 7.0, "rounds": 2,
     "goal_hint": "演讲场合 / 听众 / 时长 / 核心信息",
     "note": "起草 → 多维评审 → 修订循环 → 发布门禁"},
]

_ID_RE = re.compile(r"^[a-z][a-z0-9_-]{0,31}$")


def _custom():
    try:
        data = json.loads(_FILE.read_text(encoding="utf-8"))
        return data.get("flows") if isinstance(data, dict) else None
    except Exception:
        return None


def _save_custom(flows):
    _FILE.parent.mkdir(parents=True, exist_ok=True)
    tmp = _FILE.with_suffix(".tmp")
    tmp.write_text(json.dumps({"flows": flows}, ensure_ascii=False, indent=2),
                   encoding="utf-8")
    tmp.replace(_FILE)


def list_flows():
    """内置 + 自定义（自定义在前便于看到新加的），各自保持定义顺序。"""
    with _LOCK:
        custom = [f for f in (_custom() or []) if isinstance(f, dict) and f.get("id")]
        return custom + [dict(f) for f in BUILTIN_FLOWS]


def get_flow(flow_id):
    for f in list_flows():
        if f["id"] == flow_id:
            return f
    return None


def upsert_flow(payload):
    """新增/更新自定义流程。返回 (flow, 错误)。内置 id 拒绝覆盖。"""
    fid = str(payload.get("id") or "").strip()
    name = str(payload.get("name") or "").strip()
    engine = str(payload.get("engine") or "review").strip()
    if not _ID_RE.match(fid):
        return None, "流程 ID 只能是小写字母开头的字母/数字/-/_（≤32 位）"
    if fid in {f["id"] for f in BUILTIN_FLOWS}:
        return None, "内置流程不可修改，请换一个 ID"
    if not name:
        return None, "名称不能为空"
    if engine not in ENGINES:
        return None, "engine 必须是 code 或 review"
    flow = {"id": fid, "name": name[:20], "icon": str(payload.get("icon") or "✨")[:4],
            "engine": engine, "builtin": False,
            "goal_hint": str(payload.get("goal_hint") or "")[:80],
            "note": str(payload.get("note") or "")[:80]}
    if engine == "review":
        ms = re.sub(r"[\\/]+", "_", str(payload.get("manuscript") or (fid + ".md"))).strip()
        ms = re.sub(r"\.{2,}", "_", ms).lstrip(".") or (fid + ".md")
        flow["manuscript"] = ms
        rubric = payload.get("rubric")
        rubric = [str(d).strip() for d in (rubric or []) if str(d).strip()][:8] if isinstance(rubric, list) else []
        if not rubric:
            return None, "至少配置一个评审维度"
        flow["rubric"] = rubric
        try:
            flow["threshold"] = max(1.0, min(10.0, float(payload.get("threshold") or 7.0)))
        except Exception:
            flow["threshold"] = 7.0
        try:
            flow["rounds"] = max(1, min(5, int(payload.get("rounds") or 2)))
        except Exception:
            flow["rounds"] = 2
        for key in ("draft_prompt", "critique_prompt"):
            v = str(payload.get(key) or "").strip()
            if v:
                flow[key] = v[:4000]
    with _LOCK:
        flows = [f for f in (_custom() or []) if isinstance(f, dict) and f.get("id") != fid]
        flows.append(flow)
        _save_custom(flows)
    return flow, None


def delete_flow(flow_id):
    """删除自定义流程。内置流程返回错误。"""
    if flow_id in {f["id"] for f in BUILTIN_FLOWS}:
        return "内置流程不可删除"
    with _LOCK:
        flows = [f for f in (_custom() or []) if isinstance(f, dict) and f.get("id")]
        kept = [f for f in flows if f.get("id") != flow_id]
        if len(kept) == len(flows):
            return "流程不存在"
        _save_custom(kept)
    return None
