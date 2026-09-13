# -*- coding: utf-8 -*-
"""流程注册表重写：预置流程可编辑/可恢复默认 + 自定义流程极简配置。

设计：
  - 预置流程（code/novel/serial_novel/doc/...）**可编辑**（阈值/轮数/维度/章节数/
    提示词），改动存 data/flows.json 的 overrides；「恢复默认」一键回滚；
  - 自定义流程**只需名称 + 引擎**：其余全部有合理默认（产出文件名按 id 生成、
    维度用引擎默认、阈值 7.0、轮数 2）；进阶项留空即默认，不阻塞创建。
"""
from __future__ import annotations

import json
import re
import threading

from . import paths

_LOCK = threading.RLock()
_FILE = paths.DATA_DIR / "flows.json"

ENGINES = ("code", "review")

# 引擎默认参数：自定义流程留空时的兜底
ENGINE_DEFAULTS = {
    "review": {"manuscript": "output.md", "rubric": ["内容", "结构", "表达"],
               "threshold": 7.0, "rounds": 2},
    "code": {"verify_command": ""},
}

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
    {"id": "serial_novel", "name": "连载小说", "icon": "📚", "engine": "review", "builtin": True,
     "manuscript": "manuscript.md",
     "rubric": ["情节", "人物", "文笔", "节奏", "吸引力"],
     "threshold": 7.0, "rounds": 2,
     "serial": {"chapters": 8, "words_per_chapter": 2500},
     "goal_hint": "题材/受众/卖点 + 总字数（例：2 万字都市女频，可签约平台）",
     "note": "大纲 → 逐章起草 → 每章多维评审修订 → 全局一致性评审 → 合并（可断点续跑）"},
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
# 可编辑字段（预置流程的 overrides 与自定义流程共用同一套规范化）
_EDITABLE = ("name", "icon", "goal_hint", "note", "manuscript", "rubric",
             "threshold", "rounds", "serial", "draft_prompt", "critique_prompt",
             "verify_command")


def _read():
    try:
        data = json.loads(_FILE.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}
    except Exception:
        return {}


def _write(data):
    _FILE.parent.mkdir(parents=True, exist_ok=True)
    tmp = _FILE.with_suffix(".tmp")
    tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    tmp.replace(_FILE)


def _norm_serial(serial):
    if not isinstance(serial, dict) or not serial.get("chapters"):
        return None
    try:
        return {"chapters": max(2, min(20, int(serial["chapters"]))),
                "words_per_chapter": max(500, min(8000, int(serial.get("words_per_chapter") or 2500)))}
    except Exception:
        return None


def _norm_rubric(rubric):
    if isinstance(rubric, str):
        rubric = re.split(r"[,，、\n]+", rubric)
    if not isinstance(rubric, list):
        return None
    out = [str(d).strip() for d in rubric if str(d).strip()][:8]
    return out or None


def _apply_overrides(base, ov):
    """把 overrides 合并到预置流程上（只认白名单字段）。"""
    f = dict(base)
    if not isinstance(ov, dict):
        return f
    for k in _EDITABLE:
        if k not in ov:
            continue
        v = ov[k]
        if k == "rubric":
            v = _norm_rubric(v)
            if v:
                f["rubric"] = v
        elif k == "serial":
            v = _norm_serial(v)
            if v:
                f["serial"] = v
            else:
                f.pop("serial", None)
        elif k == "threshold":
            try:
                f["threshold"] = max(1.0, min(10.0, float(v)))
            except Exception:
                pass
        elif k == "rounds":
            try:
                f["rounds"] = max(1, min(5, int(v)))
            except Exception:
                pass
        elif k in ("manuscript", "verify_command"):
            s = re.sub(r"[\\/]+", "_", str(v or "")).strip()
            s = re.sub(r"\.{2,}", "_", s).lstrip(".")
            if s:
                f[k] = s
        elif k in ("draft_prompt", "critique_prompt"):
            s = str(v or "").strip()
            if s:
                f[k] = s[:4000]
            else:
                f.pop(k, None)
        else:
            f[k] = str(v or "")[:80]
    return f


def _custom():
    data = _read().get("flows")
    return [f for f in data if isinstance(f, dict) and f.get("id")] if isinstance(data, list) else []


def _overrides():
    ov = _read().get("overrides")
    return ov if isinstance(ov, dict) else {}


def list_flows():
    """自定义流程在前（便于看到新增），预置流程按定义顺序，均已套用 overrides。"""
    with _LOCK:
        custom, ov = _custom(), _overrides()
    builtin = [_apply_overrides(b, ov.get(b["id"])) for b in BUILTIN_FLOWS]
    for f in builtin:
        f["edited"] = bool(ov.get(f["id"]))
    return custom + builtin


def get_flow(flow_id):
    for f in list_flows():
        if f["id"] == flow_id:
            return f
    return None


def upsert_flow(payload):
    """新增/更新流程。返回 (flow, 错误)。

    - 预置流程 id：写入 overrides（可编辑、可恢复默认）；
    - 新 id：创建自定义流程——**只需 name + engine**，其余字段留空走引擎默认。
    """
    fid = str(payload.get("id") or "").strip()
    name = str(payload.get("name") or "").strip()
    engine = str(payload.get("engine") or "review").strip()
    if not _ID_RE.match(fid):
        return None, "流程 ID 只能是小写字母开头的字母/数字/-/_（≤32 位）"
    if engine not in ENGINES:
        return None, "engine 必须是 code 或 review"
    builtin_ids = {b["id"] for b in BUILTIN_FLOWS}

    if fid in builtin_ids:
        base = next(b for b in BUILTIN_FLOWS if b["id"] == fid)
        if name and name != base["name"]:
            pass  # 允许改名
        ov = dict(payload)
        ov.pop("id", None)
        ov["engine"] = base["engine"]   # 引擎不可改（避免语义错乱）
        merged = _apply_overrides(base, ov)
        with _LOCK:
            data = _read()
            ovs = data.get("overrides") if isinstance(data.get("overrides"), dict) else {}
            ovs[fid] = {k: merged[k] for k in _EDITABLE if k in merged}
            data["overrides"] = ovs
            _write(data)
        merged["edited"] = True
        return merged, None

    if not name:
        return None, "名称不能为空"
    flow = {"id": fid, "name": name[:20], "icon": str(payload.get("icon") or "✨")[:4],
            "engine": engine, "builtin": False,
            "goal_hint": str(payload.get("goal_hint") or "")[:80],
            "note": str(payload.get("note") or "")[:80]}
    if engine == "review":
        dflt = ENGINE_DEFAULTS["review"]
        ms = re.sub(r"[\\/]+", "_", str(payload.get("manuscript") or (fid + ".md"))).strip()
        ms = re.sub(r"\.{2,}", "_", ms).lstrip(".") or (fid + ".md")
        flow["manuscript"] = ms
        flow["rubric"] = _norm_rubric(payload.get("rubric")) or list(dflt["rubric"])
        try:
            flow["threshold"] = max(1.0, min(10.0, float(payload.get("threshold") or dflt["threshold"])))
        except Exception:
            flow["threshold"] = dflt["threshold"]
        try:
            flow["rounds"] = max(1, min(5, int(payload.get("rounds") or dflt["rounds"])))
        except Exception:
            flow["rounds"] = dflt["rounds"]
        serial = _norm_serial(payload.get("serial"))
        if serial:
            flow["serial"] = serial
        for key in ("draft_prompt", "critique_prompt"):
            v = str(payload.get(key) or "").strip()
            if v:
                flow[key] = v[:4000]
    else:
        flow["verify_command"] = str(payload.get("verify_command") or "")[:200]
    with _LOCK:
        data = _read()
        flows = data.get("flows") if isinstance(data.get("flows"), list) else []
        flows = [f for f in flows if isinstance(f, dict) and f.get("id") != fid]
        flows.append(flow)
        data["flows"] = flows
        _write(data)
    return flow, None


def delete_flow(flow_id):
    """删除自定义流程；预置流程只能「恢复默认」不能删除。"""
    if flow_id in {b["id"] for b in BUILTIN_FLOWS}:
        return "预置流程不可删除（可点「恢复默认」回滚改动）"
    with _LOCK:
        data = _read()
        flows = data.get("flows") if isinstance(data.get("flows"), list) else []
        kept = [f for f in flows if not (isinstance(f, dict) and f.get("id") == flow_id)]
        if len(kept) == len(flows):
            return "流程不存在"
        data["flows"] = kept
        _write(data)
    return None


def reset_flow(flow_id):
    """预置流程恢复默认（清掉 overrides）。返回错误或 None。"""
    if flow_id not in {b["id"] for b in BUILTIN_FLOWS}:
        return "只有预置流程支持恢复默认"
    with _LOCK:
        data = _read()
        ovs = data.get("overrides") if isinstance(data.get("overrides"), dict) else {}
        if flow_id in ovs:
            ovs.pop(flow_id, None)
            data["overrides"] = ovs
            _write(data)
    return None
