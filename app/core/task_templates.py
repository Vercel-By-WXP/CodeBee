"""Built-in task templates and user-managed custom templates."""
from __future__ import annotations

import json
import secrets
import threading
from pathlib import Path

from . import paths

_FILE = paths.DATA_DIR / "task-templates.json"
_LOCK = threading.RLock()
BUILTINS = [
    {"id": "refactor", "name": "代码重构", "type": "code", "goal": "重构目标代码，保持行为兼容并补充验证。", "rubric": ["正确性", "可维护性", "安全性"], "max_calls": 8},
    {"id": "bugfix", "name": "Bug 修复", "type": "code", "goal": "定位并修复问题，增加回归测试。", "rubric": ["正确性", "可读性", "安全性"], "max_calls": 6},
    {"id": "feature", "name": "新功能实现", "type": "code", "goal": "实现新功能，包含接口、测试和文档。", "rubric": ["正确性", "可维护性", "性能"], "max_calls": 10},
    {"id": "review", "name": "代码审查", "type": "review", "goal": "审查代码并输出按优先级排序的可执行意见。", "rubric": ["正确性", "安全性", "性能", "可维护性"], "max_calls": 4},
    {"id": "docs", "name": "文档生成", "type": "doc", "goal": "根据代码和现有约定生成准确、可维护的文档。", "rubric": ["正确性", "完整性", "可读性"], "max_calls": 4},
    {"id": "tests", "name": "测试补全", "type": "code", "goal": "补齐关键路径测试并保持测试隔离。", "rubric": ["正确性", "覆盖率", "可维护性"], "max_calls": 8},
]


def _read():
    try:
        value = json.loads(Path(_FILE).read_text(encoding="utf-8"))
        return value if isinstance(value, list) else []
    except (OSError, ValueError):
        return []


def list_templates():
    return [dict(x, builtin=True) for x in BUILTINS] + [dict(x, builtin=False) for x in _read()]


def get(template_id):
    return next((x for x in list_templates() if x.get("id") == str(template_id)), None)


def save(template):
    if not isinstance(template, dict) or not str(template.get("name") or "").strip():
        raise ValueError("模板名称不能为空")
    row = {"id": str(template.get("id") or "custom-" + secrets.token_hex(5)),
           "name": str(template["name"]).strip()[:80],
           "type": str(template.get("type") or "direct")[:40],
           "goal": str(template.get("goal") or "")[:2000],
           "rubric": [str(x).strip()[:80] for x in (template.get("rubric") or []) if str(x).strip()][:8],
           "max_calls": max(0, min(100, int(template.get("max_calls") or 0)))}
    with _LOCK:
        rows = [x for x in _read() if x.get("id") != row["id"]]
        rows.append(row)
        Path(_FILE).parent.mkdir(parents=True, exist_ok=True)
        temp = Path(_FILE).with_suffix(".tmp")
        temp.write_text(json.dumps(rows, ensure_ascii=False, indent=2), encoding="utf-8")
        temp.replace(_FILE)
    return row


def remove(template_id):
    with _LOCK:
        rows = _read()
        kept = [x for x in rows if x.get("id") != str(template_id)]
        if len(kept) == len(rows):
            return False
        Path(_FILE).write_text(json.dumps(kept, ensure_ascii=False, indent=2), encoding="utf-8")
        return True
