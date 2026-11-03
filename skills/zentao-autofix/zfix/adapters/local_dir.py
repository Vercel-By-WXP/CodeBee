"""本地收件目录适配器：离线 / 演示 / 任意来源的兜底。

目录内支持 .json（单个或数组）与 .csv（表头需含 id,title 等列名）。
这是把 Jira / 云效 / 飞书表格接进来的最省事方式：导出即可用。
"""

from __future__ import annotations

import csv
import io
import os
from typing import Any, Dict, List

from ..models import Bug
from .. import util
from .base import BugSource, register
from .zentao_v1 import _extract_stack, _s

_ALIASES = {
    "id": ("id", "bugid", "bug_id", "编号", "缺陷编号"),
    "title": ("title", "name", "summary", "标题", "缺陷标题"),
    "status": ("status", "状态",),
    "severity": ("severity", "严重程度", "严重级别"),
    "pri": ("pri", "priority", "优先级"),
    "steps": ("steps", "detail", "description", "复现步骤", "描述"),
    "module": ("module", "模块"),
    "product": ("product", "产品"),
    "type": ("type", "类型"),
    "opened_by": ("openedby", "opened_by", "创建者", "提bug人"),
    "assigned_to": ("assignedto", "assigned_to", "指派给"),
    "created": ("openeddate", "created", "创建时间"),
}


def _pick(row: Dict[str, Any], key: str) -> str:
    lowered = {str(k).strip().lower().replace(" ", ""): v for k, v in row.items()}
    for alias in _ALIASES.get(key, (key,)):
        if alias in lowered:
            return _s(lowered[alias])
    return ""


@register
class LocalDirSource(BugSource):
    kind = "local"

    def folder(self) -> str:
        rel = str(self.cfg.get("source.local_dir") or ".zfix/inbox")
        return os.path.join(self.cfg.root, rel)

    def health(self) -> Dict[str, Any]:
        folder = self.folder()
        files = self._files()
        return {
            "ok": os.path.isdir(folder),
            "kind": "local",
            "folder": folder,
            "files": len(files),
            "error": "" if os.path.isdir(folder) else f"目录不存在：{folder}",
        }

    def _files(self) -> List[str]:
        folder = self.folder()
        if not os.path.isdir(folder):
            return []
        out = []
        for name in sorted(os.listdir(folder)):
            if name.lower().endswith((".json", ".csv")):
                out.append(os.path.join(folder, name))
        return out

    def fetch(self, status: str = "", limit: int = 0, **kwargs: Any) -> List[Bug]:
        rows: List[Dict[str, Any]] = []
        for path in self._files():
            if path.lower().endswith(".csv"):
                rows.extend(self._read_csv(path))
            else:
                data = util.read_json(path, [])
                if isinstance(data, dict):
                    data = data.get("bugs") or data.get("data") or [data]
                if isinstance(data, list):
                    rows.extend([r for r in data if isinstance(r, dict)])
        bugs = [self.normalize(r) for r in rows]
        if status and status != "all":
            bugs = [b for b in bugs if b.status == status]
        bugs.sort(key=lambda b: (b.opened_by, b.id))
        return bugs[: int(limit)] if limit else bugs

    def _read_csv(self, path: str) -> List[Dict[str, Any]]:
        raw = util.read_text(path, "")
        if not raw.strip():
            return []
        try:
            return [dict(r) for r in csv.DictReader(io.StringIO(raw))]
        except csv.Error:
            return []

    def normalize(self, row: Dict[str, Any]) -> Bug:
        raw_id = _pick(row, "id")
        steps = _pick(row, "steps")
        return Bug(
            id=raw_id or util.slug(_pick(row, "title"))[:12],
            title=_pick(row, "title") or f"Bug {raw_id}",
            project=str(self.cfg.name),
            source="local",
            status=_pick(row, "status") or "active",
            severity=_pick(row, "severity") or "3",
            pri=_pick(row, "pri") or "3",
            product=_pick(row, "product"),
            module=_pick(row, "module"),
            type=_pick(row, "type"),
            steps=steps,
            opened_by=_pick(row, "opened_by"),
            assigned_to=_pick(row, "assigned_to"),
            created=_pick(row, "created"),
            stack=_extract_stack(steps),
            extra={"file": path_hint(row)},
        )


def path_hint(row: Dict[str, Any]) -> str:
    return str(row.get("__file__") or "")


register(LocalDirSource)
