# -*- coding: utf-8 -*-
"""可复用的专家工作模板。

专家模板只描述工作方法和交付约束，执行仍由现有 flows/pipeline 负责。
任务创建时会把模板内容写入 expert_snapshot，保证后续模板升级不改旧任务。
"""
from __future__ import annotations

import copy
import hashlib
import json


_EXPERTS = {
    "research": {
        "id": "research", "name": "调研分析师", "version": "1.0.0",
        "flow_id": "research", "description": "把问题拆成证据、结论和行动建议。",
        "prompt": "先定义问题与范围，再区分事实、推断和假设；引用来源并标注不确定性，最后给出可执行建议。",
        "skills": ["问题拆解", "证据核验", "竞品与市场分析"],
        "knowledge_scope": ["公开来源", "项目记忆", "用户附件"],
        "deliverables": ["执行摘要", "关键发现与证据", "风险与下一步建议"],
    },
    "code_fix": {
        "id": "code_fix", "name": "代码修复工程师", "version": "1.0.0",
        "flow_id": "code", "description": "定位根因、最小改动修复，并用验证结果闭环。",
        "prompt": "先复现或定位问题，再说明根因和影响范围；保持最小必要改动，补充必要测试并报告验证证据。",
        "skills": ["代码阅读", "根因分析", "测试与回归"],
        "knowledge_scope": ["代码库", "测试结果", "项目记忆"],
        "deliverables": ["根因说明", "代码改动", "验证结果与剩余风险"],
    },
    "article": {
        "id": "article", "name": "内容编辑", "version": "1.0.0",
        "flow_id": "article", "description": "围绕读者和传播目标完成可发布文章。",
        "prompt": "先明确读者、平台和核心观点；用清晰结构承载信息，避免空泛套话，完成后检查事实、标题和开头吸引力。",
        "skills": ["选题提炼", "结构写作", "平台适配"],
        "knowledge_scope": ["用户附件", "平台规范", "项目记忆"],
        "deliverables": ["可发布正文", "标题与摘要备选", "事实与表达检查"],
    },
    "translation": {
        "id": "translation", "name": "专业译者", "version": "1.0.0",
        "flow_id": "translation", "description": "在忠实、流畅和术语一致之间保持平衡。",
        "prompt": "先识别语境、受众和术语；保持原文事实与结构意图，译后统一术语并检查遗漏、歧义和语气。",
        "skills": ["术语管理", "语境判断", "双语校对"],
        "knowledge_scope": ["源文档", "术语表", "用户附件"],
        "deliverables": ["目标语言译文", "术语表", "疑难点与取舍说明"],
    },
    "presentation": {
        "id": "presentation", "name": "演示设计师", "version": "1.0.0",
        "flow_id": "presentation", "description": "把复杂信息组织成可讲、可看的演示结构。",
        "prompt": "先明确听众和决策目标；每页只承载一个核心信息，安排证据与转场，并同时提供讲解提示。",
        "skills": ["信息架构", "叙事设计", "演讲适配"],
        "knowledge_scope": ["用户材料", "受众约束", "项目记忆"],
        "deliverables": ["逐页结构", "每页要点与视觉建议", "演讲者备注"],
    },
    "work_report": {
        "id": "work_report", "name": "工作汇报教练", "version": "1.0.0",
        "flow_id": "weekly_report", "description": "把流水账整理成面向结果和下一步的汇报。",
        "prompt": "优先呈现目标、结果和数据；区分已完成、进行中和风险，最后明确需要的决策、资源或下一步动作。",
        "skills": ["成果提炼", "数据表达", "风险与行动项"],
        "knowledge_scope": ["Git 提交", "禅道素材", "项目记忆"],
        "deliverables": ["核心结论", "成果与数据", "风险、诉求和下一步"],
    },
}


def _copy(item):
    return copy.deepcopy(item) if isinstance(item, dict) else None


def list_experts():
    return [_copy(x) for x in _EXPERTS.values()]


def get_expert(expert_id):
    return _copy(_EXPERTS.get(str(expert_id or "").strip()))


def content_sha256(expert):
    if not isinstance(expert, dict):
        return ""
    basis = {k: expert.get(k) for k in
             ("id", "version", "flow_id", "description", "prompt", "skills",
              "knowledge_scope", "deliverables")}
    blob = json.dumps(basis, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(blob.encode("utf-8")).hexdigest()


def snapshot(expert):
    """返回不含运行时可变引用的任务快照。"""
    item = _copy(expert) or {}
    item["content_sha256"] = content_sha256(item)
    return item


def context_block(expert_snapshot):
    """将任务创建时钉住的模板转成执行上下文，避免运行时读取新版本。"""
    if not isinstance(expert_snapshot, dict) or not expert_snapshot.get("id"):
        return ""
    lines = [
        "## 专家工作模板（任务创建时已固定版本）",
        "- 专家：%s（版本 %s）" % (expert_snapshot.get("name") or expert_snapshot["id"],
                                  expert_snapshot.get("version") or "未知"),
        "- 工作方法：%s" % (expert_snapshot.get("prompt") or ""),
    ]
    skills = expert_snapshot.get("skills") or []
    knowledge_scope = expert_snapshot.get("knowledge_scope") or []
    deliverables = expert_snapshot.get("deliverables") or []
    if skills:
        lines.append("- 技能范围：%s" % "、".join(str(x) for x in skills[:12]))
    if knowledge_scope:
        lines.append("- 知识范围：%s" % "、".join(str(x) for x in knowledge_scope[:12]))
    if deliverables:
        lines.append("- 交付清单：%s" % "、".join(str(x) for x in deliverables[:12]))
    lines.append("请遵循上述工作方法，并在最终交付中逐项回应交付清单；模板内容属于用户任务约束。")
    return "\n".join(lines)
