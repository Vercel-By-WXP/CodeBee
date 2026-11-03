"""错误知识库：模式库（跨项目）+ 修复档案（本机累积）+ 学习与蒸馏。

三层结构：
  seed  / knowledge/patterns/*.json   随技能分发的通用模式（只读）
  user  ~/.zfix/knowledge/patterns/   从实际修复中蒸馏出的模式（可写）
  live  ~/.zfix/knowledge/fixes.jsonl 原始修复档案，学习与报表的原料
"""

from __future__ import annotations

import os
import re
from typing import Any, Dict, Iterable, List, Optional, Tuple

from . import util
from .config import Config
from .models import ROOT_CAUSES, Bug, FixRecord, Pattern

PKG_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SEED_PATTERN_DIR = os.path.join(PKG_ROOT, "knowledge", "patterns")


def user_home() -> str:
    return os.path.join(os.path.expanduser("~"), ".zfix")


def user_pattern_dir() -> str:
    return os.path.join(user_home(), "knowledge", "patterns")


def fixes_path() -> str:
    return os.path.join(user_home(), "knowledge", "fixes.jsonl")


# ---------------------------------------------------------------- 读取


def _load_dir(folder: str) -> List[Pattern]:
    out: List[Pattern] = []
    if not os.path.isdir(folder):
        return out
    for name in sorted(os.listdir(folder)):
        if not name.lower().endswith(".json"):
            continue
        data = util.read_json(os.path.join(folder, name), None)
        if isinstance(data, dict):
            rows = data.get("patterns") or [data]
        elif isinstance(data, list):
            rows = data
        else:
            rows = []
        for row in rows:
            if isinstance(row, dict) and row.get("id"):
                out.append(Pattern.from_dict(row))
    return out


class KnowledgeBase:
    def __init__(self, cfg: Optional[Config] = None, extra_dirs: Optional[Iterable[str]] = None):
        self.cfg = cfg
        self.dirs = [SEED_PATTERN_DIR, user_pattern_dir()]
        if cfg is not None:
            self.dirs.insert(0, cfg.state_dir("patterns"))
        self.dirs.extend(extra_dirs or [])
        self._patterns: Optional[List[Pattern]] = None

    # ------------------------------------------------------------ 模式
    @property
    def patterns(self) -> List[Pattern]:
        if self._patterns is None:
            seen: Dict[str, Pattern] = {}
            # 后加载的覆盖先加载的：项目 > 用户 > 种子
            for pattern in _load_dir(SEED_PATTERN_DIR):
                seen[pattern.id] = pattern
            for pattern in _load_dir(user_pattern_dir()):
                seen[pattern.id] = pattern
            for folder in self.dirs[:1] if self.cfg else []:
                for pattern in _load_dir(folder):
                    seen[pattern.id] = pattern
            self._patterns = sorted(seen.values(), key=lambda p: (-p.occurrences, p.id))
        return self._patterns

    def get(self, pattern_id: str) -> Optional[Pattern]:
        for p in self.patterns:
            if p.id == pattern_id:
                return p
        return None

    def match(self, bug: Bug, limit: int = 5) -> List[Tuple[Pattern, int]]:
        """按 指纹/异常类型/堆栈帧/关键词 给模式打分。返回 [(模式, 分数)]。"""
        text = bug.text.lower()
        frames = _frames_of(text)
        scored: List[Tuple[Pattern, int]] = []
        for pattern in self.patterns:
            score = 0
            if bug.fingerprint and pattern.id and bug.fingerprint in pattern.id:
                score += 60
            for exc in pattern.exceptions:
                if exc and exc.lower() in text:
                    score += 25
            hits = sum(1 for kw in pattern.keywords if kw and kw.lower() in text)
            score += min(hits * 8, 32)
            frame_hits = sum(1 for fr in pattern.frames if fr and any(fr.lower() in f for f in frames))
            score += min(frame_hits * 15, 30)
            if score >= 15:
                scored.append((pattern, score))
        scored.sort(key=lambda item: (-item[1], -item[0].occurrences))
        return scored[:limit]

    # ------------------------------------------------------------ 修复档案
    def add_fix(self, record: FixRecord) -> None:
        if not record.created_at:
            record.created_at = util.now_iso()
        util.append_jsonl(fixes_path(), record.to_dict())

    def fixes(self, project: str = "") -> List[FixRecord]:
        rows = util.read_jsonl(fixes_path())
        out = [FixRecord.from_dict(r) for r in rows]
        if project:
            out = [r for r in out if r.project == project]
        return out

    def fixes_for_fingerprint(self, fp: str, limit: int = 5) -> List[FixRecord]:
        if not fp:
            return []
        rows = [r for r in self.fixes() if r.fingerprint == fp]
        rows.sort(key=lambda r: r.created_at, reverse=True)
        return rows[:limit]

    def fixes_for_bug(self, bug_id: str) -> Optional[FixRecord]:
        for record in reversed(self.fixes()):
            if str(record.bug_id) == str(bug_id):
                return record
        return None

    # ------------------------------------------------------------ 学习
    def learn(self, min_occurrences: int = 2, project: str = "") -> List[Pattern]:
        """把重复出现的修复档案蒸馏成候选模式（不落盘）。"""
        groups: Dict[str, List[FixRecord]] = {}
        for record in self.fixes(project):
            if not record.fingerprint or record.root_cause == "unknown":
                continue
            if not record.reason.strip():
                continue
            groups.setdefault(record.fingerprint, []).append(record)
        existing = {p.id for p in self.patterns}
        candidates: List[Pattern] = []
        for fp, records in groups.items():
            pattern_id = f"learned-{fp}"
            if pattern_id in existing or len(records) < min_occurrences:
                continue
            latest = records[-1]
            cause = latest.root_cause if latest.root_cause in ROOT_CAUSES else "unknown"
            keywords = _top_keywords(" ".join(r.summary + " " + r.reason for r in records), 6)
            candidates.append(
                Pattern(
                    id=pattern_id,
                    title=f"[{ROOT_CAUSES[cause]}] {latest.summary[:60] or '重复缺陷'}",
                    root_cause=cause,
                    keywords=keywords,
                    prevention=_prevention_from(cause, keywords),
                    symptom=latest.reason[:200],
                    fix_hint=latest.reason[:200],
                    occurrences=len(records),
                    projects=sorted({r.project for r in records}),
                    source="learned",
                    created_at=util.now_iso(),
                )
            )
        candidates.sort(key=lambda p: -p.occurrences)
        return candidates

    def save_pattern(self, pattern: Pattern) -> str:
        folder = util.ensure_dir(user_pattern_dir())
        path = os.path.join(folder, f"{util.slug(pattern.id)}.json")
        util.write_json(path, pattern.to_dict())
        self._patterns = None
        return path

    # ------------------------------------------------------------ 规则注入
    def rules_markdown(self, project: str = "", max_rules: int = 20) -> str:
        """给 AI 的「开工前必读」——只放高频、可执行的硬规则。"""
        scored = []
        for pattern in self.patterns:
            if project and pattern.projects and project not in pattern.projects:
                continue
            if not pattern.rule:
                continue
            weight = pattern.occurrences * 3 + (10 if pattern.detect_regex else 0)
            scored.append((weight, pattern))
        scored.sort(key=lambda item: (-item[0], item[1].id))
        lines = [
            "<!-- zfix:rules:begin · 由 zfix 自动生成，请勿手改；更新用 `zfix preflight` -->",
            "## 本项目已知错误模式（历史缺陷蒸馏，编码前先读）",
            "",
        ]
        if not scored:
            lines.append("_暂无沉淀的模式。修复缺陷后运行 `zfix learn --apply` 积累。_")
        for _, pattern in scored[:max_rules]:
            scope = f"（{pattern.occurrences} 次）" if pattern.occurrences else ""
            lines.append(f"- **{pattern.title}**{scope}：{pattern.rule}")
            if pattern.fix_hint and pattern.fix_hint != pattern.rule:
                lines.append(f"  - 参考修法：{pattern.fix_hint[:160]}")
        lines.append("<!-- zfix:rules:end -->")
        return "\n".join(lines) + "\n"


# ---------------------------------------------------------------- 内部


def _frames_of(lowered_text: str) -> List[str]:
    return [line.strip() for line in lowered_text.splitlines() if line.strip()]


_STOPWORDS = {
    "the", "and", "for", "with", "that", "this", "from", "when", "then", "null", "none",
    "true", "false", "error", "exception", "failed", "cannot", "could", "should", "would",
    "什么", "问题", "出现", "导致", "无法", "一个", "这个", "那个", "时候", "进行", "没有",
    "需要", "可能", "应该", "但是", "并且", "而且", "然后", "如果", "因为", "所以",
}


def _top_keywords(text: str, limit: int = 6) -> List[str]:
    """无第三方分词：英文标识符 + 代码符号 + 中文 2~4 字滑窗，按出现次数取前 N。"""
    counts: Dict[str, int] = {}
    for token in re.findall(r"[A-Za-z_][A-Za-z0-9_.]{2,}", text):
        low = token.lower()
        if low in _STOPWORDS or len(low) < 3:
            continue
        counts[low] = counts.get(low, 0) + 1
    for chunk in re.findall(r"[\u4e00-\u9fa5]{2,}", text):
        if chunk in _STOPWORDS:
            continue
        for size in (4, 3, 2):
            for i in range(0, max(len(chunk) - size + 1, 0)):
                gram = chunk[i : i + size]
                if gram in _STOPWORDS:
                    continue
                counts[gram] = counts.get(gram, 0) + 1
    ranked = sorted(counts.items(), key=lambda kv: (-kv[1], -len(kv[0])))
    return [word for word, count in ranked[:limit] if count >= 1]


_PREVENTION_TEMPLATE = {
    "null_ref": "取用外部返回值/查询结果前先判空，并对「查不到」给出显式分支，不要用 `or {}` 静默吞掉。",
    "boundary": "对索引、分页、时间区间做边界校验（空集合、首尾元素、off-by-one），并为边界写单测。",
    "type_mismatch": "跨层传参保持类型契约一致；字符串数字在入口处集中转换，不要在中途隐式转换。",
    "concurrency": "共享可变状态必须加锁或用不可变快照；异步回调里不要直接改外部状态。",
    "resource": "文件/连接/游标/子进程一律用上下文管理器或 finally 释放。",
    "config": "配置项缺失时给出显式默认值与启动期校验，禁止运行到中途才发现。",
    "dependency": "升级依赖前读 changelog 并跑全量测试；锁定版本，不在业务代码里依赖未声明的传递依赖。",
    "network": "所有外部调用设置超时 + 重试上限 + 降级分支，禁止裸调用。",
    "permission": "权限判断放在服务端入口统一处理，前端隐藏不等于鉴权。",
    "encoding": "读写文件显式指定 encoding='utf-8'；比较字符串前先归一化。",
    "state": "状态流转用显式状态机/白名单校验，非法流转要报错而不是静默跳过。",
    "compat": "改动共用函数前先搜全部调用点；对外接口保持向后兼容或同步升级调用方。",
    "data": "对外部数据做契约校验与清洗，脏数据走隔离分支而非污染主流程。",
    "ui": "交互态（空/加载/错误/超长）四态都要有可见反馈；布局改动需在窄屏复核。",
    "perf": "批量操作先评估数据量级，避免 N+1 与全量加载；加索引或分页。",
    "unknown": "修复后补一条回归用例，把这次的触发条件固化下来。",
}


def _prevention_from(cause: str, keywords: List[str]) -> str:
    base = _PREVENTION_TEMPLATE.get(cause, _PREVENTION_TEMPLATE["unknown"])
    if keywords:
        base += f"（高频相关词：{'、'.join(keywords[:3])}）"
    return base
