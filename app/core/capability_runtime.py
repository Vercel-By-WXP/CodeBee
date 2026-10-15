# -*- coding: utf-8 -*-
"""Resolve and audit the capabilities used by one CodeBee run.

The prompt builders historically loaded skills in several places.  That made
the direct path and a few revision paths silently skip the same knowledge.
This module gives every run one cached, inspectable capability snapshot while
leaving actual MCP execution to the builtin agent's normal tool loop.
"""
from __future__ import annotations

import re
import threading

from . import knowledge, plugins, skills

_LOCK = threading.RLock()
_RUNS = {}
_MAX_RUNS = 200
# Optional plugin prose must not crowd out the task/manuscript prompt.  The
# canonical CodeBee skill block has its own budget in skills.py; these caps
# cover only supplemental plugin defaults and external Skill excerpts.
_PLUGIN_PROMPT_BUDGET = 2400
_EXTERNAL_SKILL_BUDGET = 4000
_EXTERNAL_SKILL_ITEM_BUDGET = 1200
_EXTERNAL_SKILL_MAX_ITEMS = 4


def _task_text(task):
    task = task or {}
    return " ".join(str(task.get(k) or "") for k in ("type", "title", "goal", "context")).lower()


def _relevant(row, text):
    terms = set(re.findall(r"[a-z0-9][a-z0-9_-]{2,}", text.lower()))
    cjk = "".join(re.findall(r"[\u4e00-\u9fff]", text))
    terms.update(cjk[i:i + 2] for i in range(max(0, len(cjk) - 1)))
    hay = " ".join(str(row.get(k) or "") for k in ("id", "name", "display_name", "description")).lower()
    if not terms:
        return False
    return any(term in hay for term in terms)


def _plugin_block(task, used_pack_ids):
    """Include enabled plugin defaults only when their installed skill is used.

    A plugin manifest is a declaration, not an instruction to execute.  Its
    default prompt becomes task input only after the plugin is enabled and its
    skill pack was selected for this task; keyword matching covers MCP-only or
    otherwise prompt-only plugins without injecting every installed plugin.
    """
    text = _task_text(task)
    rows = plugins.view().get("plugins") or []
    out, ids = [], []
    for row in rows:
        if not row.get("enabled") or row.get("available") is False:
            continue
        # A catalog row can be visible while the plugin lacks an explicit
        # capability grant.  Do not turn an unauthorized plugin's prose into
        # model instructions.  Missing authorization is tolerated for legacy
        # catalog rows and test doubles; installed rows from plugins.view()
        # always carry the authorization result.
        authorization = row.get("authorization")
        if isinstance(authorization, dict) and not authorization.get("allowed", False):
            continue
        defaults = [str(x).strip() for x in (row.get("default_prompt") or []) if str(x).strip()]
        if not defaults:
            continue
        linked = set(str(x) for x in (row.get("skill_pack_ids") or []))
        selected = bool(linked & set(used_pack_ids or ())) or _relevant(row, text)
        if not selected:
            continue
        block = "### 【插件默认提示：%s】\n%s" % (
            row.get("display_name") or row.get("name"),
            "\n".join("- " + x for x in defaults))
        separator = 2 if out else 0  # ``\n\n`` added by the final join
        used_chars = sum(len(x) for x in out)
        remaining = _PLUGIN_PROMPT_BUDGET - used_chars - separator
        if remaining <= 0:
            break
        out.append(block[:remaining])
        ids.append(str(row.get("id") or row.get("name") or ""))
    return "\n\n".join(out), ids


def _external_skill_block(task):
    """Select matching Codex/Agents skills without loading the whole catalog."""
    text = _task_text(task)
    rows = plugins.capabilities_view().get("skills") or []
    candidates = []
    platform_terms = ("qimao", "七猫", "fanqie", "番茄")
    for row in rows:
        if row.get("source_type") not in ("codex", "agents", "system"):
            continue
        if not row.get("enabled") or row.get("available") is False or not _relevant(row, text):
            continue
        prompt = str(row.get("prompt") or "").strip()
        if not prompt:
            continue
        hay = " ".join(str(row.get(k) or "") for k in ("id", "name", "display_name", "description")).lower()
        # Platform-specific packs outrank broad writing/engineering skills.  The
        # built-in qimao/fanqie packs are still injected separately and remain
        # the authoritative rules; this ordering only chooses supplemental
        # local instructions.
        priority = 0 if any(term in hay for term in platform_terms) else 1
        candidates.append((priority, str(row.get("display_name") or row.get("name") or "").lower(), row, prompt))
    candidates.sort(key=lambda item: (item[0], item[1], str(item[2].get("id") or "")))
    out, ids, used = [], [], 0
    for _, _, row, prompt in candidates:
        if len(out) >= _EXTERNAL_SKILL_MAX_ITEMS or used >= _EXTERNAL_SKILL_BUDGET:
            break
        separator = 2 if out else 0  # ``\n\n`` added by the final join
        remaining = _EXTERNAL_SKILL_BUDGET - used - separator
        prefix = "### 【匹配的外部 Skill：%s】\n" % (row.get("display_name") or row.get("name"))
        room = min(_EXTERNAL_SKILL_ITEM_BUDGET, remaining)
        if room <= len(prefix):
            break
        excerpt = prompt[:room - len(prefix)]
        block = prefix + excerpt
        if len(prompt) > len(excerpt):
            marker = "\n…（外部 Skill 已按运行预算截断）"
            block = (prefix + prompt[:max(0, room - len(prefix) - len(marker))] + marker)[:room]
        out.append(block)
        ids.append(str(row.get("id") or ""))
        used += separator + len(block)
    return "\n\n".join(out), ids


def prepare(task, run_id=""):
    """Resolve one stable capability snapshot for a run."""
    key = str(run_id or "")
    with _LOCK:
        if key and key in _RUNS:
            return _RUNS[key]
    task = dict(task or {})
    scope = str(task.get("type") or "*")
    sk, skill_ids = skills.block_for(task, stable_order=True, run_id=run_id)
    kb = knowledge.block_for(task)
    plugin_text, plugin_ids = _plugin_block(task, skill_ids)
    external_text, external_ids = _external_skill_block(task)
    sections = [x for x in (plugin_text, external_text, sk, kb) if x]
    result = {
        "scope": scope,
        "skills": sorted(str(x) for x in skill_ids),
        "knowledge": bool(kb),
        "plugins": sorted(plugin_ids),
        "external_skills": sorted(external_ids),
        "mcp": {"discovered": 0, "called": []},
        "block": "\n\n".join(sections),
    }
    if key:
        with _LOCK:
            if len(_RUNS) >= _MAX_RUNS:
                _RUNS.clear()
            _RUNS[key] = result
    return result


def block_for(task, run_id=""):
    return prepare(task, run_id).get("block") or ""


def evidence(task, run_id=""):
    """Return JSON-safe evidence without the prompt body."""
    item = prepare(task, run_id)
    return {k: item[k] for k in ("scope", "skills", "knowledge", "plugins", "external_skills", "mcp")}


def note_mcp(run_id, *, discovered=None, called=None):
    with _LOCK:
        item = _RUNS.get(str(run_id or ""))
        if not item:
            return
        if discovered is not None:
            item["mcp"]["discovered"] = max(int(item["mcp"].get("discovered") or 0), int(discovered or 0))
        if called:
            item["mcp"]["called"] = sorted(set(item["mcp"].get("called") or ()) | {str(x) for x in called if x})


def forget(run_id):
    with _LOCK:
        _RUNS.pop(str(run_id or ""), None)
