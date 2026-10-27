# -*- coding: utf-8 -*-
"""成稿台（dossier）：要件清单 + 章节树 + 定稿导出。

**它回答的问题不是「写得好不好」，是「交出去会不会被打回」。**

小说版问「读者会不会追」，成稿台问「要件齐不齐、格式会不会被打回」。这两个
问题不能共用一个页面：作者要的是正文与评审意见，标书师要的是**逐条打勾的
要件表**——漏一项不是扣分，是废标。

三条设计约束：

1. **只读**。这里只读稿件、只算结构，不写盘、不调模型、不连平台。成稿台是
   「交稿前最后一眼」，它自己出故障不能把稿子也带坏。
2. **缺项要说清楚是哪一项**。不做「完整度 82%」这种合成分：作者没法拿 82%
   去补一项漏掉的投标函。逐条给 present / missing / unknown，missing 里再分
   「硬性（废标项）」与「建议」。
3. **没有正文时不假装检查过**。刚建任务、还没落稿时，所有要件是 ``unknown``
   而不是 ``missing``——否则作者一进来就看到满屏红叉，会以为稿子废了。

要件表是数据，不是代码分支：``_TABLES`` 里加一条就是一个新检查项。真源只有
这一处，界面、报告、测试都读它（改档口见 ``table()``）。
"""
from __future__ import annotations

import os
import re
from pathlib import Path

from . import flows, writing

# 成稿台的落点：稿件是一份「整篇文档」而不是逐章连载。
DOSSIER_FLOWS = ("bid_doc", "doc", "tech_proposal", "presentation",
                 "weekly_report", "email", "resume", "translation")

# ---- 要件表 -------------------------------------------------------------
# 字段：key / label / group / hard / keywords / hint
#   hard=True 缺项即失去资格（标书里叫「废标项」）；
#   keywords 必须是**具体词**——「函」会把「公函」也算成投标函，宁可漏检。
_TABLES = {
    "bid_doc": [
        # ---- 商务文件
        {"key": "bid_letter", "label": "投标函及投标函附录", "group": "商务文件",
         "hard": True, "keywords": ["投标函"]},
        {"key": "legal_rep", "label": "法定代表人身份证明 / 授权委托书",
         "group": "商务文件", "hard": True,
         "keywords": ["法定代表人身份证明", "授权委托书", "法人授权", "法定代表人授权"]},
        {"key": "bid_bond", "label": "投标保证金 / 投标保函", "group": "商务文件",
         "hard": True, "keywords": ["投标保证金", "投标保函"]},
        {"key": "price_sheet", "label": "开标一览表 / 报价表", "group": "商务文件",
         "hard": True, "keywords": ["开标一览表", "报价表", "投标报价"]},
        {"key": "business_deviation", "label": "商务偏离表", "group": "商务文件",
         "hard": False, "keywords": ["商务偏离"]},
        # ---- 资格与资质
        {"key": "license", "label": "营业执照", "group": "资格与资质",
         "hard": True, "keywords": ["营业执照"]},
        {"key": "qualification", "label": "资质证书 / 资质等级证明",
         "group": "资格与资质", "hard": True,
         "keywords": ["资质证书", "资质等级", "资质证明", "资质要求"]},
        {"key": "financial", "label": "财务状况 / 审计报告", "group": "资格与资质",
         "hard": True, "keywords": ["财务状况", "审计报告", "财务报告"]},
        {"key": "tax_social", "label": "纳税与社会保障资金缴纳证明",
         "group": "资格与资质", "hard": True,
         "keywords": ["纳税", "社会保障资金", "社保", "缴税", "税收"]},
        {"key": "no_violation", "label": "无重大违法记录声明", "group": "资格与资质",
         "hard": True, "keywords": ["无重大违法", "重大违法记录", "违法记录声明",
                                    "经营活动中有重大违法记录"]},
        {"key": "capability", "label": "履约能力声明（设备与专业技术能力）",
         "group": "资格与资质", "hard": False,
         "keywords": ["履约能力", "设备和专业技术能力", "专业技术能力"]},
        # ---- 技术文件
        {"key": "technical_plan", "label": "技术方案 / 施工组织设计",
         "group": "技术文件", "hard": True,
         "keywords": ["技术方案", "施工组织设计", "实施方案", "技术路线"]},
        {"key": "tech_deviation", "label": "技术偏离表", "group": "技术文件",
         "hard": False, "keywords": ["技术偏离", "偏离表"]},
        {"key": "schedule", "label": "工期 / 交货期 / 服务期承诺",
         "group": "技术文件", "hard": False,
         "keywords": ["工期", "交货期", "交付期", "服务期"]},
        {"key": "quality_service", "label": "质量保证与售后服务",
         "group": "技术文件", "hard": False,
         "keywords": ["质量保证", "售后服务", "质保", "售后"]},
        {"key": "performance", "label": "类似项目业绩", "group": "技术文件",
         "hard": False, "keywords": ["类似项目", "业绩", "案例"]},
        {"key": "team", "label": "项目负责人及团队", "group": "技术文件",
         "hard": False, "keywords": ["项目负责人", "项目经理", "项目团队", "拟投入"]},
        # ---- 其他
        {"key": "other", "label": "招标文件要求的其他材料", "group": "其他",
         "hard": False, "keywords": ["其他材料", "招标文件要求", "附件清单"]},
    ],
    "doc": [
        {"key": "context", "label": "背景 / 目的", "group": "结构", "hard": False,
         "keywords": ["背景", "目的", "前言", "概述"]},
        {"key": "scope", "label": "适用范围", "group": "结构", "hard": False,
         "keywords": ["适用范围", "范围"]},
        {"key": "body", "label": "主体内容", "group": "结构", "hard": False,
         "keywords": ["步骤", "流程", "方案", "做法", "说明"]},
        {"key": "conclusion", "label": "结论 / 建议", "group": "结构", "hard": False,
         "keywords": ["结论", "建议", "总结", "综上"]},
        {"key": "reference", "label": "参考 / 附录", "group": "结构", "hard": False,
         "keywords": ["参考", "附录", "参考资料"]},
    ],
    "tech_proposal": [
        {"key": "context", "label": "问题与背景", "group": "结构", "hard": False,
         "keywords": ["背景", "问题", "现状"]},
        {"key": "goal", "label": "目标与需求", "group": "结构", "hard": False,
         "keywords": ["目标", "需求"]},
        {"key": "design", "label": "方案设计", "group": "结构", "hard": False,
         "keywords": ["方案", "架构", "设计", "技术路线"]},
        {"key": "plan", "label": "实施计划", "group": "结构", "hard": False,
         "keywords": ["实施", "计划", "里程碑", "进度"]},
        {"key": "risk", "label": "风险与应对", "group": "结构", "hard": False,
         "keywords": ["风险", "应对"]},
        {"key": "cost", "label": "成本与资源", "group": "结构", "hard": False,
         "keywords": ["成本", "预算", "资源", "人力"]},
        {"key": "acceptance", "label": "验收标准", "group": "结构", "hard": False,
         "keywords": ["验收", "标准", "指标"]},
    ],
    "presentation": [
        {"key": "cover", "label": "封面 / 主题", "group": "结构", "hard": False,
         "keywords": ["封面", "主题"]},
        {"key": "agenda", "label": "目录 / 议程", "group": "结构", "hard": False,
         "keywords": ["目录", "议程", "结构"]},
        {"key": "pages", "label": "逐页要点", "group": "结构", "hard": False,
         "keywords": ["页", "要点", "讲稿"]},
        {"key": "action", "label": "结论 / 行动号召", "group": "结构", "hard": False,
         "keywords": ["结论", "行动", "下一步", "总结"]},
    ],
    "weekly_report": [
        {"key": "focus", "label": "本期重点", "group": "结构", "hard": False,
         "keywords": ["重点", "完成", "进展"]},
        {"key": "data", "label": "数据支撑", "group": "结构", "hard": False,
         "keywords": ["数据", "指标", "同比", "环比"]},
        {"key": "risk", "label": "问题与风险", "group": "结构", "hard": False,
         "keywords": ["问题", "风险", "阻塞"]},
        {"key": "next", "label": "下一步计划", "group": "结构", "hard": False,
         "keywords": ["下一步", "计划", "下周"]},
    ],
    "email": [
        {"key": "subject", "label": "主题行", "group": "结构", "hard": False,
         "keywords": ["主题", "标题"]},
        {"key": "greeting", "label": "称呼", "group": "结构", "hard": False,
         "keywords": ["您好", "尊敬的", "各位"]},
        {"key": "ask", "label": "诉求", "group": "结构", "hard": False,
         "keywords": ["请", "希望", "申请", "需要"]},
        {"key": "action", "label": "行动项与时间", "group": "结构", "hard": False,
         "keywords": ["截止", "回复", "确认", "时间"]},
        {"key": "signoff", "label": "落款", "group": "结构", "hard": False,
         "keywords": ["此致", "敬礼", "谢谢", "祝"]},
    ],
    "resume": [
        {"key": "contact", "label": "基本信息", "group": "结构", "hard": False,
         "keywords": ["姓名", "电话", "邮箱"]},
        {"key": "intent", "label": "求职意向", "group": "结构", "hard": False,
         "keywords": ["求职", "意向", "应聘"]},
        {"key": "history", "label": "教育 / 工作经历", "group": "结构", "hard": False,
         "keywords": ["教育", "工作经历", "任职", "项目经历"]},
        {"key": "impact", "label": "成果量化", "group": "结构", "hard": False,
         "keywords": ["提升", "降低", "增长", "节省", "完成"]},
        {"key": "skills", "label": "技能", "group": "结构", "hard": False,
         "keywords": ["技能", "熟练", "掌握"]},
    ],
    "translation": [
        {"key": "source", "label": "原文", "group": "结构", "hard": False,
         "keywords": ["原文", "source"]},
        {"key": "target", "label": "译文", "group": "结构", "hard": False,
         "keywords": ["译文", "translation", "译"]},
        {"key": "glossary", "label": "术语表", "group": "结构", "hard": False,
         "keywords": ["术语"]},
    ],
}

# 未登记的文档类流程：按「结构四件套」保守检查，不编造行业要件。
_GENERIC = _TABLES["doc"]

# ---- 章节树 -------------------------------------------------------------
# 只认能明确区分层级的行；列表项（- / *）不当标题，避免把正文拆成树。
_HEAD = (
    (1, re.compile(r"^#\s+(.+?)\s*#*\s*$")),
    (2, re.compile(r"^##\s+(.+?)\s*#*\s*$")),
    (3, re.compile(r"^#{3,6}\s+(.+?)\s*#*\s*$")),
    (1, re.compile(r"^第[一二三四五六七八九十百零\d]+[章节部分篇]\s*(.*)$")),
    (2, re.compile(r"^[一二三四五六七八九十]+[、．.]\s*(.+)$")),
    (3, re.compile(r"^（[一二三四五六七八九十]+）\s*(.+)$")),
    (3, re.compile(r"^\d+(?:\.\d+)*[、．.]\s*(.+)$")),
)


def _match_head(line):
    for level, pat in _HEAD:
        m = pat.match(line)
        if m:
            title = (m.group(1) or "").strip()
            if title:
                return level, title[:80]
    return None


def outline_nodes(body):
    """把正文拆成扁平章节树（level 决定缩进）。

    扁平而不是嵌套：前端只需要按 level 缩进渲染，嵌套树在缺级（有二级没一级）
    时会丢节点，而真实标书里缺级很常见。
    """
    nodes = []
    cur = None
    buf = []

    def flush():
        if cur is not None:
            cur["words"] = writing.count_text("\n".join(buf))
            nodes.append(cur)

    for raw in (body or "").splitlines():
        line = raw.strip()
        hit = _match_head(line) if line else None
        if hit:
            flush()
            cur = {"level": hit[0], "title": hit[1], "words": 0}
            buf = []
        elif cur is not None:
            buf.append(line)
    flush()
    if not nodes and (body or "").strip():
        nodes = [{"level": 1, "title": "正文", "words": writing.count_text(body)}]
    return nodes


# ---- 正文读取 -----------------------------------------------------------

def _read_text(path):
    """UTF-8 → GBK 顺序读；读不到返回空串（与 writing.py 同口径）。"""
    for enc in ("utf-8", "gbk"):
        try:
            return Path(path).read_text(encoding=enc)
        except (OSError, UnicodeDecodeError):
            continue
    return ""


def manuscript_name(task):
    """稿件文件名：任务字段优先，缺省取流程定义；只允许工作目录内的文件名。"""
    task = task or {}
    name = str(task.get("manuscript") or "").strip()
    if not name:
        flow = flows.get_flow(task.get("type")) or {}
        name = str(flow.get("manuscript") or "").strip()
    if not name:
        return ""
    if os.path.basename(name) != name:
        name = os.path.basename(name)
    return name


def read_body(task):
    """读当前稿件正文；没有工作目录或文件不存在时返回空串。"""
    task = task or {}
    workdir = str(task.get("workdir") or "").strip()
    name = manuscript_name(task)
    if not workdir or not name:
        return ""
    return _read_text(os.path.join(workdir, name))


# ---- 要件检查 -----------------------------------------------------------

def _snippet(text, keyword):
    idx = text.find(keyword)
    if idx < 0:
        return ""
    start = text.rfind("\n", 0, idx) + 1
    end = text.find("\n", idx)
    if end < 0:
        end = len(text)
    return text[start:end].strip()[:140]


def _hit(text, keywords):
    """返回命中片段；没命中返回空串。"""
    for kw in keywords:
        got = _snippet(text, kw)
        if got:
            return got
    return ""


def requirements(flow):
    """某个流程的要件表（不可变副本，调用方可放心改字段）。"""
    rows = _TABLES.get(str(flow or ""), _GENERIC)
    return [dict(r, keywords=list(r.get("keywords") or [])) for r in rows]


def check(task, body=None):
    """逐条核对要件。

    ``body`` 为 None 时自动读稿；显式传空串表示「确认没有正文」，此时所有
    条目的状态是 ``unknown`` 而不是 ``missing``——没写不能等于写错。
    """
    task = task or {}
    if body is None:
        body = read_body(task)
    has_body = bool((body or "").strip())
    items = []
    for row in requirements(task.get("type")):
        if not has_body:
            status, evidence = "unknown", ""
        else:
            evidence = _hit(body, row["keywords"])
            status = "present" if evidence else "missing"
        items.append({
            "key": row["key"],
            "label": row["label"],
            "group": row["group"],
            "hard": bool(row.get("hard")),
            "status": status,
            "evidence": evidence,
        })
    return items


def _summary(items, has_body):
    total = len(items)
    present = sum(1 for i in items if i["status"] == "present")
    missing = [i for i in items if i["status"] == "missing"]
    hard_missing = [i for i in missing if i["hard"]]
    if not has_body:
        state, label = "empty", "还没有正文"
    elif hard_missing:
        state = "blocked"
        label = "%d 项废标风险" % len(hard_missing)
    elif missing:
        state = "warn"
        label = "%d 项建议补充" % len(missing)
    else:
        state, label = "ok", "要件齐全"
    return {
        "total": total,
        "present": present,
        "missing": len(missing),
        "hard_missing": len(hard_missing),
        "missing_hard_keys": [i["key"] for i in hard_missing],
        "state": state,
        "label": label,
    }


# ---- 对外载荷 -----------------------------------------------------------

def summarize(task):
    """列表用的轻量摘要（不读全文，只给字数与状态）。"""
    task = task or {}
    flow = str(task.get("type") or "")
    info = flows.get_flow(flow) or {}
    try:
        wc = writing.cached_summary(task)
    except Exception:
        wc = {"words": 0, "known": False}
    return {
        "id": str(task.get("id") or ""),
        "title": str(task.get("title") or "未命名文档"),
        "type": flow,
        "flow_name": str(info.get("name") or flow),
        "status": str(task.get("status") or ""),
        "words": int(wc.get("words") or 0),
        "known": bool(wc.get("known")),
    }


def build(task):
    """成稿台一屏所需的全部数据。"""
    task = task or {}
    flow = str(task.get("type") or "")
    info = flows.get_flow(flow) or {}
    body = read_body(task)
    has_body = bool(body.strip())
    items = check(task, body)
    return {
        "task": {
            "id": str(task.get("id") or ""),
            "title": str(task.get("title") or "未命名文档"),
            "type": flow,
            "flow_name": str(info.get("name") or flow),
            "status": str(task.get("status") or ""),
            "manuscript": manuscript_name(task),
        },
        "has_body": has_body,
        "words": writing.count_text(body),
        "outline": outline_nodes(body),
        "checklist": items,
        "summary": _summary(items, has_body),
        "export": {
            "available": has_body,
            "filename": filename(task),
        },
    }


def _safe_title(task):
    title = str((task or {}).get("title") or "").strip() or "未命名文档"
    title = re.sub(r"[\\/:*?\"<>|\r\n\t]+", " ", title).strip()
    return title[:60] or "未命名文档"


def filename(task):
    """导出文件名：``<标题>.md``。中文用 RFC5987 编码在 HTTP 头里传。"""
    return "%s.md" % _safe_title(task)


def export_text(task):
    """定稿导出：标题 + 正文。

    正文已经以 Markdown 标题开头时不再补标题，避免出现两个书名号式的一级标题。
    没有正文返回空串——调用方据此回 409，而不是导出一个只有标题的空文件。
    """
    body = read_body(task)
    if not body.strip():
        return ""
    if body.lstrip().startswith("#"):
        return body.rstrip() + "\n"
    return "# %s\n\n%s\n" % (_safe_title(task), body.rstrip())


def table():
    """全量要件表（给报告/对照表/前端说明用）。"""
    out = []
    for flow in DOSSIER_FLOWS:
        info = flows.get_flow(flow) or {}
        rows = requirements(flow)
        out.append({
            "flow": flow,
            "name": str(info.get("name") or flow),
            "items": len(rows),
            "hard": sum(1 for r in rows if r.get("hard")),
            "groups": sorted({r["group"] for r in rows}),
        })
    return out
