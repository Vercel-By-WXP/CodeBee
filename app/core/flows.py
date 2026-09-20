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

ENGINES = ("code", "review", "direct")

# 引擎默认参数：自定义流程留空时的兜底
ENGINE_DEFAULTS = {
    "review": {"manuscript": "output.md", "rubric": ["内容", "结构", "表达"],
               "threshold": 7.0, "rounds": 2, "best_of": 1},
    "code": {"verify_command": ""},
    # direct：无参数——目标+附件即全部输入，跑完即止
}

# icon 约定："i-*" = 前端精灵表单色线性图标（随日/夜主题黑白）；其他值（emoji）原样显示，
# 供自定义流程兜底。预置图标不进 overrides（见 _EDITABLE），保证升级后老数据也拿到新图标。
BUILTIN_FLOWS = [
    {"id": "direct", "name": "直接执行", "icon": "i-chat", "engine": "direct", "builtin": True,
     "goal_hint": "让 AI 直接做什么（一句话，可带附件）",
     "note": "CodeBee 直连模型 API 干活（无 CLI 进程），无可用供应商时回退本机 CLI；无拆解/评审（快）"},
    {"id": "code", "name": "代码", "icon": "i-code", "engine": "code", "builtin": True,
     "goal_hint": "要实现/修复什么（一句话）",
     "note": "实现 → 验证命令 → 跨厂商评审 → 自动修复/换将"},
    {"id": "novel", "name": "小说", "icon": "i-book-open", "engine": "review", "builtin": True,
     "manuscript": "manuscript.md",
     "rubric": ["情节", "人物", "文笔", "节奏", "吸引力"],
     "threshold": 7.0, "rounds": 2,
     "goal_hint": "写什么（题材 / 篇幅 / 风格）",
     "note": "起草 → 多维评审 → 修订循环 → 发布门禁"},
    {"id": "serial_novel", "name": "连载小说", "icon": "i-library", "engine": "review", "builtin": True,
     "manuscript": "manuscript.md",
     "rubric": ["情节", "人物", "文笔", "节奏", "吸引力"],
     "threshold": 7.0, "rounds": 2,
     "serial": {"chapters": 8, "words_per_chapter": 2500},
     "goal_hint": "题材/受众/卖点 + 总字数（例：2 万字都市女频，可签约平台）",
     "note": "大纲 → 逐章起草 → 每章多维评审修订 → 全局一致性评审 → 合并（可断点续跑）"},
    {"id": "article", "name": "自媒体文章", "icon": "i-news", "engine": "review", "builtin": True,
     "manuscript": "article.md",
     "rubric": ["选题与标题", "开头吸引力", "结构节奏", "平台适配", "传播性"],
     "threshold": 7.0, "rounds": 2,
     "goal_hint": "什么主题、投哪个平台（公众号/头条/知乎）、给谁看",
     "note": "公众号/头条风格文章：起草 → 多维评审 → 修订循环 → 发布门禁"},
    {"id": "video_script", "name": "短视频脚本", "icon": "i-clapper", "engine": "review", "builtin": True,
     "manuscript": "script.md",
     "rubric": ["黄金3秒钩子", "节奏密度", "口播流畅", "画面可执行", "互动引导"],
     "threshold": 7.0, "rounds": 2,
     "goal_hint": "什么主题、多长时长、投哪个平台（抖音/B站/视频号）",
     "note": "分镜 + 口播脚本：起草 → 多维评审 → 修订循环 → 发布门禁"},
    {"id": "doc", "name": "文档", "icon": "i-file-text", "engine": "review", "builtin": True,
     "manuscript": "document.md",
     "rubric": ["准确性", "结构清晰", "表达流畅", "实用价值"],
     "threshold": 7.0, "rounds": 2,
     "goal_hint": "要写什么文档、给谁看",
     "note": "起草 → 多维评审 → 修订循环 → 发布门禁"},
    {"id": "translation", "name": "翻译", "icon": "i-lang", "engine": "review", "builtin": True,
     "manuscript": "translation.md",
     "rubric": ["忠实度", "流畅度", "术语一致性", "风格贴合"],
     "threshold": 7.0, "rounds": 2,
     "goal_hint": "翻译什么（源文本位置 / 目标语言 / 要求）",
     "note": "起草 → 多维评审 → 修订循环 → 发布门禁"},
    {"id": "rank_scan", "name": "扫榜选材", "icon": "i-chart", "engine": "direct", "builtin": True,
     "goal_hint": "想写哪个方向（一句话，可留空默认分析总榜热门题材）",
     "note": "抓取七猫+番茄排行榜公开数据 → AI 提炼跨平台热门题材/人设/差异化切入点（快档直出报告）"},
    {"id": "research", "name": "调研报告", "icon": "i-file-search", "engine": "review", "builtin": True,
     "manuscript": "report.md",
     "rubric": ["全面性", "深度", "论据可靠", "可读性", "结论质量"],
     "threshold": 7.0, "rounds": 2,
     "goal_hint": "调研什么问题、产出给谁用",
     "note": "起草 → 多维评审 → 修订循环 → 发布门禁"},
    {"id": "speech", "name": "演讲稿", "icon": "i-mic", "engine": "review", "builtin": True,
     "manuscript": "speech.md",
     "rubric": ["主题聚焦", "结构", "感染力", "语言风格"],
     "threshold": 7.0, "rounds": 2,
     "goal_hint": "演讲场合 / 听众 / 时长 / 核心信息",
     "note": "起草 → 多维评审 → 修订循环 → 发布门禁"},
    {"id": "weekly_report", "name": "工作汇报", "icon": "i-clip-check", "engine": "review", "builtin": True,
     "manuscript": "weekly.md",
     "rubric": ["重点突出", "数据支撑", "结构清晰", "下一步可执行"],
     "threshold": 7.0, "rounds": 2,
     "goal_hint": "周报/月报/述职？汇报给谁、做了什么（可贴流水账让小队提炼）",
     "note": "周报/月报/述职材料：起草 → 多维评审 → 修订循环 → 发布门禁"},
    {"id": "email", "name": "商务邮件", "icon": "i-mail", "engine": "review", "builtin": True,
     "manuscript": "email.md",
     "rubric": ["目的明确", "语气得体", "信息完整", "简洁度"],
     "threshold": 7.0, "rounds": 2,
     "goal_hint": "给谁写、要达成什么、对方背景与你们的关系",
     "note": "商务/客户/跨部门邮件：起草 → 多维评审 → 修订循环 → 发布门禁"},
    {"id": "tech_proposal", "name": "技术方案", "icon": "i-blocks", "engine": "review", "builtin": True,
     "manuscript": "proposal.md",
     "rubric": ["可行性", "方案完整性", "风险识别", "成本与收益"],
     "threshold": 7.0, "rounds": 2,
     "goal_hint": "要解决什么问题、约束条件（工期/技术栈/预算）、给谁评审",
     "note": "技术选型/架构/实施方案：起草 → 多维评审 → 修订循环 → 发布门禁"},
    {"id": "resume", "name": "简历", "icon": "i-idcard", "engine": "review", "builtin": True,
     "manuscript": "resume.md",
     "rubric": ["真实可信", "岗位匹配", "成果量化", "关键词覆盖", "简洁度"],
     "threshold": 7.0, "rounds": 2,
     "goal_hint": "目标岗位 + 个人经历（可贴旧简历附件），几年经验投什么职级",
     "note": "简历优化/定制：起草 → 多维评审 → 修订循环 → 发布门禁"},
]

_ID_RE = re.compile(r"^[a-z][a-z0-9_-]{0,31}$")
# 可编辑字段（预置流程的 overrides 与自定义流程共用同一套规范化）。
# icon 刻意不进来：预置图标跟版本走（老 overrides 里钉的旧 emoji 会被忽略），
# 自定义流程的 icon 在 upsert_flow 里直接落盘，不走 overrides。
_EDITABLE = ("name", "goal_hint", "note", "manuscript", "rubric",
             "threshold", "rounds", "serial", "draft_prompt", "critique_prompt",
             "verify_command", "best_of")


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
        elif k == "best_of":
            try:
                f["best_of"] = max(1, min(3, int(v)))
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
        return None, "engine 必须是 code、review 或 direct"
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
        try:
            flow["best_of"] = max(1, min(3, int(payload.get("best_of") or dflt.get("best_of") or 1)))
        except Exception:
            flow["best_of"] = 1
        serial = _norm_serial(payload.get("serial"))
        if serial:
            flow["serial"] = serial
        for key in ("draft_prompt", "critique_prompt"):
            v = str(payload.get(key) or "").strip()
            if v:
                flow[key] = v[:4000]
    elif engine == "code":
        flow["verify_command"] = str(payload.get("verify_command") or "")[:200]
    # direct：无流程参数（目标+附件即全部输入）
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
