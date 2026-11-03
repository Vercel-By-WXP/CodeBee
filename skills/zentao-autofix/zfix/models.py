"""领域模型：Bug / 定位结果 / 修复档案 / 错误模式。"""

from __future__ import annotations

import os
from dataclasses import dataclass, field, asdict
from typing import Any, Dict, List, Optional

# 根因分类：固定词表，保证知识库可聚合、报表可统计
ROOT_CAUSES: Dict[str, str] = {
    "null_ref": "空值/未初始化",
    "boundary": "边界与越界",
    "type_mismatch": "类型不匹配/隐式转换",
    "concurrency": "并发与竞态",
    "resource": "资源泄漏/未释放",
    "config": "配置与环境差异",
    "dependency": "依赖版本/接口变更",
    "network": "网络与超时",
    "permission": "权限与鉴权",
    "encoding": "编码与本地化",
    "state": "状态机与流程遗漏",
    "compat": "兼容性/回归",
    "data": "脏数据与数据契约",
    "ui": "界面与交互",
    "perf": "性能与容量",
    "unknown": "未归类",
}

SEVERITIES = ("1", "2", "3", "4")  # 禅道 severity：1 最重
AUTO_DECISION = ("auto", "manual", "skip")


@dataclass
class Bug:
    """跨平台统一的缺陷模型。适配器只负责把它填满。"""

    id: str
    title: str
    project: str = "default"
    source: str = "zentao"
    status: str = ""
    severity: str = "3"
    pri: str = "3"
    product: str = ""
    module: str = ""
    type: str = ""
    steps: str = ""
    opened_by: str = ""
    assigned_to: str = ""
    created: str = ""
    updated: str = ""
    url: str = ""
    stack: str = ""
    resolution: str = ""
    extra: Dict[str, Any] = field(default_factory=dict)
    # 由 triage 计算
    fingerprint: str = ""
    root_cause: str = ""
    decision: str = ""
    triage_note: str = ""
    duplicate_of: str = ""

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "Bug":
        known = {f for f in cls.__dataclass_fields__}  # type: ignore[attr-defined]
        payload = {k: v for k, v in data.items() if k in known}
        payload.setdefault("id", "")
        payload.setdefault("title", "")
        return cls(**payload)

    @property
    def text(self) -> str:
        return "\n".join(x for x in (self.title, self.steps, self.stack) if x)

    @property
    def severity_rank(self) -> int:
        try:
            return int(self.severity)
        except (TypeError, ValueError):
            return 3


@dataclass
class Location:
    """代码候选位置。score 越大越可疑。"""

    path: str
    line: int = 0
    symbol: str = ""
    score: int = 0
    reasons: List[str] = field(default_factory=list)
    snippet: str = ""

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


@dataclass
class FixRecord:
    """一次修复的完整档案 —— 知识库的原始素材。"""

    bug_id: str
    project: str
    fingerprint: str
    root_cause: str = "unknown"
    reason: str = ""
    summary: str = ""
    files: List[str] = field(default_factory=list)
    branch: str = ""
    commit: str = ""
    verify: Dict[str, Any] = field(default_factory=dict)
    duration_s: int = 0
    agent: str = ""
    created_at: str = ""
    tags: List[str] = field(default_factory=list)

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "FixRecord":
        known = {f for f in cls.__dataclass_fields__}  # type: ignore[attr-defined]
        payload = {k: v for k, v in data.items() if k in known}
        payload.setdefault("bug_id", "")
        payload.setdefault("project", "default")
        payload.setdefault("fingerprint", "")
        return cls(**payload)


@dataclass
class Pattern:
    """一条可复用、可拦截的错误模式。"""

    id: str
    title: str
    root_cause: str = "unknown"
    keywords: List[str] = field(default_factory=list)
    exceptions: List[str] = field(default_factory=list)
    frames: List[str] = field(default_factory=list)
    detect_regex: List[str] = field(default_factory=list)
    file_glob: List[str] = field(default_factory=list)
    prevention: str = ""
    symptom: str = ""
    fix_hint: str = ""
    occurrences: int = 0
    projects: List[str] = field(default_factory=list)
    source: str = "seed"
    created_at: str = ""

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "Pattern":
        known = {f for f in cls.__dataclass_fields__}  # type: ignore[attr-defined]
        payload = {k: v for k, v in data.items() if k in known}
        payload.setdefault("id", "")
        payload.setdefault("title", "")
        return cls(**payload)

    @property
    def rule(self) -> str:
        return self.prevention.strip() or self.title


def rel(path: str, root: str = ".") -> str:
    """统一成正斜杠相对路径，跨平台比较只看这一个口径。"""
    try:
        return os.path.relpath(path, root).replace("\\", "/")
    except ValueError:
        return path.replace("\\", "/")
