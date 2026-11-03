"""上下文包：把「AI 修复一个 bug 需要的全部信息」压成一页 Markdown。

这是整个技能的核心交付物 —— 平台无关的可读中间格式。
任何 AI（Codex / Claude Code / CodeBee / OpenCode）读同一份文件，
得到的判断依据完全一致，换平台不产生行为漂移。
"""

from __future__ import annotations

import os
from typing import Any, Dict, List, Optional

from . import util
from .config import Config
from .knowledge import KnowledgeBase
from .locate import Locator
from .models import Bug, FixRecord, Location, ROOT_CAUSES
from .triage import cause_label, top_frames


def build(
    cfg: Config,
    bug: Bug,
    kb: KnowledgeBase,
    locations: Optional[List[Location]] = None,
    locate_limit: int = 10,
) -> str:
    if locations is None:
        locations = Locator(cfg.repo).locate(bug, limit=locate_limit)
    history = kb.fixes_for_fingerprint(bug.fingerprint, limit=3)
    related = kb.match(bug, limit=4)
    commands = recipe_commands(cfg)

    lines: List[str] = [
        f"# 缺陷上下文 · #{bug.id} {bug.title}",
        "",
        "> 本文件由 `zfix context` 生成，供 AI 修复使用。修改代码前先读完，不要跳过「验收命令」。",
        "",
        "## 1. 基本信息",
        "",
        f"| 字段 | 值 |",
        f"| --- | --- |",
        f"| 项目 | `{cfg.name}` |",
        f"| 仓库 | `{cfg.repo}` |",
        f"| 禅道 | {bug.url or '—'} |",
        f"| 状态 / 严重度 / 优先级 | {bug.status or '—'} / {bug.severity} / {bug.pri} |",
        f"| 模块 / 类型 | {bug.module or '—'} / {bug.type or '—'} |",
        f"| 报告人 / 指派 | {bug.opened_by or '—'} / {bug.assigned_to or '—'} |",
        f"| 指纹 | `{bug.fingerprint}` |",
        f"| 根因初判 | `{bug.root_cause}`（{cause_label(bug.root_cause)}） |",
        f"| 可修性 | **{bug.decision or 'manual'}** · {bug.triage_note or '—'} |",
        "",
    ]

    lines += ["## 2. 问题描述", "", bug.steps.strip() or "_（禅道未填写复现步骤，需从堆栈与关键词推断）_", ""]

    if bug.stack:
        lines += ["### 堆栈", "", "```", bug.stack.strip()[:3000], "```", ""]
    frames = top_frames(bug.text)
    if frames:
        lines += ["### 关键帧", ""] + [f"{i+1}. `{f}`" for i, f in enumerate(frames)] + [""]

    lines += ["## 3. 候选代码位置（按可疑度排序）", ""]
    if not locations:
        lines += ["_未定位到候选文件。请先用 `zfix locate` 并提供更完整的复现步骤或堆栈。_", ""]
    for idx, loc in enumerate(locations, 1):
        lines.append(f"### 3.{idx} `{loc.path}`" + (f":{loc.line}" if loc.line else "") + f" · 评分 {loc.score}")
        lines.append("")
        lines.append("依据：" + "；".join(loc.reasons))
        if loc.snippet:
            lines += ["", "```", loc.snippet, "```"]
        lines.append("")

    lines += ["## 4. 历史经验（同类问题怎么修过）", ""]
    if history:
        for record in history:
            lines.append(
                f"- **#{record.bug_id}**（{record.created_at[:10]} · {cause_label(record.root_cause)}）："
                f"{record.summary or record.reason[:120]}"
            )
            if record.files:
                lines.append(f"  - 涉及：{', '.join(f'`{f}`' for f in record.files[:6])}")
            if record.verify.get("ok"):
                lines.append(f"  - 验证：`{record.verify.get('cmd', '')}` 通过")
    else:
        lines.append("_无同指纹历史修复记录，本次将是首次。修完请务必 `zfix record`。_")
    lines.append("")

    if related:
        lines += ["## 5. 相关错误模式（防复发规则）", ""]
        for pattern, score in related:
            lines.append(f"- **{pattern.title}**（相关度 {score} · 根因 `{pattern.root_cause}`）")
            lines.append(f"  - 规则：{pattern.rule}")
            if pattern.fix_hint:
                lines.append(f"  - 参考修法：{pattern.fix_hint[:200]}")
        lines.append("")

    scope = cfg.get("fix.write_scope") or []
    forbidden = cfg.get("fix.forbidden") or []
    lines += [
        f"## {'6' if related else '5'}. 改动边界（硬约束）",
        "",
        f"- 允许修改：{', '.join(f'`{s}`' for s in scope) if scope else '整个仓库（未限定，请保持最小改动）'}",
        f"- 禁止修改：{', '.join(f'`{s}`' for s in forbidden) if forbidden else '—'}",
        f"- 单次最多改 `{cfg.get('fix.max_files')}` 个文件；超出说明定位错了，回去重新 `zfix locate`。",
        "- 不做无关重构、不动格式化、不加未要求的依赖。",
        "",
    ]

    lines += [
        f"## {'7' if related else '6'}. 验收命令（必须自己跑过再报完成）",
        "",
    ]
    if commands:
        for name, cmd in commands.items():
            if cmd:
                lines.append(f"- **{name}**：`{cmd}`")
    else:
        lines.append("_未识别技术栈，请在 `.zfix/config.toml` 的 `[stack]` 里显式写 test/lint 命令。_")
    lines += [
        "",
        "```bash",
        f"zfix verify {bug.id}          # 跑上面的命令并留档",
        f"zfix record {bug.id} --root-cause {bug.root_cause or 'unknown'} --reason \"<一句话根因>\"",
        "```",
        "",
    ]

    lines += [
        f"## {'8' if related else '7'}. 完成标准",
        "",
        "1. 复现路径能走通，原错误消失；",
        "2. 验收命令全绿（红灯不许报完成）；",
        "3. 新增或补强一条能卡住这次回归的用例；",
        "4. `zfix record` 写清根因——这是知识库唯一的输入，写「改了某行」等于没写。",
        "",
        "---",
        f"_由 zfix 生成于 {util.now_iso()}_",
    ]
    return "\n".join(lines)


def recipe_commands(cfg: Config) -> Dict[str, str]:
    from .verify import resolve_commands

    return resolve_commands(cfg)


def save(cfg: Config, bug: Bug, content: str) -> str:
    path = os.path.join(cfg.context_dir(), f"{util.slug(bug.id)}.md")
    util.write_text(path, content)
    return path
