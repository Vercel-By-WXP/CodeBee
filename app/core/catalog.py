# -*- coding: utf-8 -*-
"""智能体目录（catalog）：本机可管理/可编排的 CLI 清单。

data/catalog.json 是唯一事实来源，首次运行自动生成；用户可直接编辑补充
安装命令（install/upgrade）与编排模板（orch.argv_template），保存后点 UI 的
"重新加载"或重启生效。
"""
from __future__ import annotations

import json
import threading

from . import paths

# config.format 取值:
#   toml-line : 按行正则读写 `model = "..."`（适合 codex config.toml）
#   json      : 整体 JSON 读写 "model" 键（适合 claude settings.json）
#   jsonc     : 正则读写 "model": "..."（适合 opencode.jsonc，显示为主）
# orch.kind 取值: codex | claude | opencode | qwen | aider | generic | null
#   null = 仅管理，不参与编排（桌面端等）
DEFAULT_CATALOG = [
    {
        "id": "codex-cli", "name": "Codex CLI", "cli_group": "installed",
        "note": "ChatGPT 官方编程智能体；本机走自定义 provider（gpt-5.5）",
        "detect": {"cli": "codex"},
        "orch": {"kind": "codex", "command": "codex"},
        "config": {"path": "~/.codex/config.toml", "format": "toml-line", "model_key": "model"},
        "install": "npm install -g @openai/codex",
        "upgrade": "npm install -g @openai/codex@latest",
        "default_enabled": True,
    },
    {
        "id": "claude-code", "name": "Claude Code", "cli_group": "installed",
        "note": "Anthropic 官方 CLI；无头调用自动注入 Git Bash 路径与输出上限",
        "detect": {"cli": "claude"},
        "orch": {"kind": "claude", "command": "claude"},
        "config": {"path": "~/.claude/settings.json", "format": "json", "model_key": "model"},
        "install": "winget install -e --id Anthropic.ClaudeCode",
        "upgrade": "winget upgrade -e --id Anthropic.ClaudeCode",
        "default_enabled": True,
    },
    {
        "id": "opencode", "name": "OpenCode CLI", "cli_group": "installable",
        "note": "无头模式：opencode run（stdin 传入提示词）",
        "detect": {"cli": "opencode"},
        "orch": {"kind": "opencode", "command": "opencode"},
        "config": {"path": "~/.config/opencode/opencode.jsonc", "format": "jsonc", "model_key": "model"},
        "install": "npm install -g opencode-ai",
        "upgrade": "npm install -g opencode-ai@latest",
        "default_enabled": False,
    },
    {
        "id": "qwencode", "name": "QwenCode", "cli_group": "installable",
        "note": "通义千问编码 CLI（gemini-cli 系）；无头走 stdin",
        "detect": {"cli": "qwen"},
        "orch": {"kind": "qwen", "command": "qwen"},
        "config": {"path": "~/.qwen/settings.json", "format": "json", "model_key": "model"},
        "install": "npm install -g @qwen-code/qwen-code",
        "upgrade": "npm install -g @qwen-code/qwen-code@latest",
        "default_enabled": False,
    },
    {
        "id": "aider", "name": "Aider", "cli_group": "installable",
        "note": "Python 系结对编程 CLI；用 py -3.13 安装，模型经其配置/环境变量设置",
        "detect": {"cli": "aider"},
        "orch": {"kind": "aider", "command": "aider"},
        "config": {"path": "~/.aider.conf.yml", "format": None, "model_key": None},
        "install": "py -3.13 -m pip install -U aider-chat",
        "upgrade": "py -3.13 -m pip install -U aider-chat",
        "default_enabled": False,
    },
    {
        "id": "openclaw", "name": "OpenClaw", "cli_group": "installable",
        "note": "网关型个人 AI 智能体（原 Clawdbot）；编排模板装好后需验证",
        "detect": {"cli": "openclaw"},
        "orch": {"kind": "generic", "command": "openclaw", "argv_template": ["{prompt}"]},
        "config": {"path": "~/.openclaw/openclaw.json", "format": "json", "model_key": None},
        "install": "npm install -g openclaw",
        "upgrade": "npm install -g openclaw@latest",
        "default_enabled": False,
    },
    {
        "id": "kimi-code", "name": "Kimi Code", "cli_group": "installable",
        "note": "月之暗面 Kimi 编码 CLI；安装命令待确认，可编辑 data/catalog.json",
        "detect": {"cli": "kimi"},
        "orch": {"kind": "generic", "command": "kimi", "argv_template": ["-p", "{prompt}"]},
        "config": {"path": "~/.kimi/config.json", "format": "json", "model_key": None},
        "install": None,
        "upgrade": None,
        "default_enabled": False,
    },
    {
        "id": "mimo-code", "name": "MiMo Code", "cli_group": "installable",
        "note": "小米 MiMo 编码 CLI；安装命令待确认，可编辑 data/catalog.json",
        "detect": {"cli": "mimo"},
        "orch": {"kind": "generic", "command": "mimo", "argv_template": ["-p", "{prompt}"]},
        "config": {"path": "~/.mimo/config.json", "format": "json", "model_key": None},
        "install": None,
        "upgrade": None,
        "default_enabled": False,
    },
    {
        "id": "grok-build", "name": "Grok Build", "cli_group": "installable",
        "note": "xAI 编码 CLI；安装命令待确认，可编辑 data/catalog.json",
        "detect": {"cli": "grok-build"},
        "orch": {"kind": "generic", "command": "grok-build", "argv_template": ["-p", "{prompt}"]},
        "config": {"path": "~/.grok/config.json", "format": "json", "model_key": None},
        "install": None,
        "upgrade": None,
        "default_enabled": False,
    },
    {
        "id": "pi", "name": "Pi", "cli_group": "installable",
        "note": "Pi 编码 CLI；安装命令待确认，可编辑 data/catalog.json",
        "detect": {"cli": "pi"},
        "orch": {"kind": "generic", "command": "pi", "argv_template": ["-p", "{prompt}"]},
        "config": {"path": "~/.pi/config.json", "format": "json", "model_key": None},
        "install": None,
        "upgrade": None,
        "default_enabled": False,
    },
]

_LOCK = threading.RLock()
_CACHE = {"entries": None}


def load(force=False):
    with _LOCK:
        if _CACHE["entries"] is not None and not force:
            return _CACHE["entries"]
        paths.ensure_dirs()
        if not paths.CATALOG_FILE.exists():
            paths.CATALOG_FILE.write_text(
                json.dumps(DEFAULT_CATALOG, ensure_ascii=False, indent=2), encoding="utf-8")
        try:
            entries = json.loads(paths.CATALOG_FILE.read_text(encoding="utf-8"))
        except Exception:
            entries = [dict(e) for e in DEFAULT_CATALOG]
        _CACHE["entries"] = entries
        return entries


def by_id(entry_id):
    for e in load():
        if e.get("id") == entry_id:
            return e
    return None


def reset_to_default():
    """把 catalog.json 恢复为内置默认（用户改坏时的逃生门）。"""
    with _LOCK:
        paths.CATALOG_FILE.write_text(
            json.dumps(DEFAULT_CATALOG, ensure_ascii=False, indent=2), encoding="utf-8")
        _CACHE["entries"] = None
