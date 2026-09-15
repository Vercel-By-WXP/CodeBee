# -*- coding: utf-8 -*-
"""智能体目录（catalog）：本机可管理/可编排的 CLI 清单。

data/catalog.json 是唯一事实来源，首次运行自动生成；用户可直接编辑补充
安装命令（install/upgrade）与编排模板（orch.argv_template），保存后点 UI 的
"重新加载"或重启生效。
"""
from __future__ import annotations

import copy
import json
import re
import threading

from . import paths

# config.format 取值:
#   toml-line : 按行正则读写 `model = "..."`（适合 codex config.toml）
#   json      : 整体 JSON 读写 "model" 键（适合 claude settings.json）
#   jsonc     : 正则读写 "model": "..."（适合 opencode.jsonc，显示为主）
#   yaml-line : 按行读写 YAML 嵌套标量；model_key 写点号路径（段.键）
#               （适合 dsh 的 "agent-default-model.model"）
# orch.kind 取值: codex | claude | opencode | qwen | aider | generic | null
#   null = 仅管理，不参与编排（桌面端等）
# launch 字段（「一键打开」）：kind=web 后台起服务并自动开浏览器（command 里
#   自带 --port 时必须与 port 一致，port 同时是就绪探测的依据）；
#   kind=console 新开终端窗口跑交互 TUI。command 是完整命令行（cmd /c 执行）。
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
        "note": "通义千问编码 CLI（gemini-cli 系）；无头走 stdin；绑定凭据经打开注入"
                "写 settings.json 的 env 段（OPENAI_*，qwen 存在即优先 openai 兼容通道）",
        "detect": {"cli": "qwen"},
        "orch": {"kind": "qwen", "command": "qwen"},
        # settings.json 顶层 "model" 已是 legacy（qwen 忽略并告警，新格式是
        # model.name）——模型落盘由打开注入器写 OPENAI_MODEL env 负责，
        # 这里不再走 write_model
        "config": {"path": "~/.qwen/settings.json", "format": None, "model_key": None},
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
        "note": "月之暗面 Kimi 编码 CLI（TypeScript 版，需 Node ≥22.19）；旧 Python 版 kimi-cli 正在下线",
        "detect": {"cli": "kimi"},
        "orch": {"kind": "generic", "command": "kimi", "argv_template": ["-p", "{prompt}"]},
        "config": {"path": "~/.kimi/config.json", "format": "json", "model_key": None},
        "install": "npm install -g @moonshot-ai/kimi-code",
        "upgrade": "npm install -g @moonshot-ai/kimi-code@latest",
        "default_enabled": False,
    },
    {
        "id": "mimo-code", "name": "MiMo Code", "cli_group": "installable",
        "note": "小米 MiMo Code（opencode 衍生）；无头调用是子命令 mimo run \"提示词\"，-p 在该 CLI 是 --password",
        "detect": {"cli": "mimo"},
        "orch": {"kind": "generic", "command": "mimo", "argv_template": ["run", "{prompt}"],
                 "resume_argv_template": ["run", "-s", "{session}"]},
        "config": {"path": "~/.mimo/config.json", "format": "json", "model_key": None},
        "install": "npm install -g @mimo-ai/cli",
        "upgrade": "npm install -g @mimo-ai/cli@latest",
        "default_enabled": False,
    },
    {
        "id": "grok-build", "name": "Grok Build", "cli_group": "installable",
        "note": "xAI 终端编码智能体；可执行名是 grok（不是 grok-build）",
        "detect": {"cli": "grok"},
        "orch": {"kind": "generic", "command": "grok", "argv_template": ["-p", "{prompt}"]},
        "config": {"path": "~/.grok/config.json", "format": "json", "model_key": None},
        "install": "npm install -g @xai-official/grok",
        "upgrade": "npm install -g @xai-official/grok@latest",
        "default_enabled": False,
    },
    {
        "id": "pi", "name": "Pi", "cli_group": "installable",
        "note": "Earendil Works 的 Pi 编码 CLI（需 Node ≥22.19）；包名必须带 @earendil-works/ 前缀",
        "detect": {"cli": "pi"},
        "orch": {"kind": "generic", "command": "pi", "argv_template": ["-p", "{prompt}"]},
        "config": {"path": "~/.pi/config.json", "format": "json", "model_key": None},
        "install": "npm install -g --ignore-scripts @earendil-works/pi-coding-agent",
        "upgrade": "npm install -g --ignore-scripts @earendil-works/pi-coding-agent@latest",
        "default_enabled": False,
    },
    {
        "id": "deepseek-harness", "name": "DeepSeek Harness", "cli_group": "installable",
        "note": "DeepSeek 官方 agent harness（dsh，profile 插件架构）；无头是「一次性任务」——"
                "答完即退、无交互后续、不支持会话恢复；任务只走位置参数（超长提示词受 Windows "
                "命令行上限约 32k 约束）；模型写进 ~/.dsh/settings.yaml 的 agent-default-model.model；"
                "密钥由「CLI 绑定」注入 DEEPSEEK_API_KEY（优先级最高）；端点注入 DEEPSEEK_BASE_URL，"
                "但若 settings.yaml 已固定 llm-deepseek.baseURL，则以它为准（settings 高于 env）",
        "detect": {"cli": "dsh"},
        "orch": {"kind": "generic", "command": "dsh",
                 "argv_template": ["--profile", "headless", "{prompt}"]},
        "config": {"path": "~/.dsh/settings.yaml", "format": "yaml-line",
                   "model_key": "agent-default-model.model"},
        "install": "npm install -g @deepseek-ai/dsh",
        "upgrade": "npm install -g @deepseek-ai/dsh@latest",
        "default_enabled": False,
    },
]

_LOCK = threading.RLock()
_CACHE = {"entries": None}

# generic 类 CLI 实测出会话恢复方式后在这里登记（{session}/{prompt} 占位），
# load() 幂等补进已有 data/catalog.json——用户手改过的字段不覆盖。
ORCH_RESUME_PATCH = {
    "mimo-code": ["run", "-s", "{session}"],
}

# 「一键打开」配置（{id: launch 字段}）：load() 幂等补进没有 launch 的条目，
# 用户在 data/catalog.json 里手写过的 launch 不覆盖。
LAUNCH_PATCH = {    "codex-cli": {"kind": "console", "command": "codex"},
    "claude-code": {"kind": "console", "command": "claude"},
    "opencode": {"kind": "console", "command": "opencode"},
    "qwencode": {"kind": "console", "command": "qwen"},
    "aider": {"kind": "console", "command": "aider"},
    "openclaw": {"kind": "console", "command": "openclaw"},
    "kimi-code": {"kind": "console", "command": "kimi"},
    "mimo-code": {"kind": "console", "command": "mimo"},
    "grok-build": {"kind": "console", "command": "grok"},
    "pi": {"kind": "console", "command": "pi"},
    # dsh 自带浏览器 UI（dsh web）；端口固定以便「已在运行就直接开页面」的复用
    # 判断，18790 避开 CodeBee 自身与常用测试端口。--no-open 关掉 dsh 自己开浏览器
    # 的行为（否则它会和 CodeBee 就绪后各开一个标签页）
    "deepseek-harness": {"kind": "web",
                         "command": "dsh web --port 18790 --no-open", "port": 18790},
}


# config.format 修正（{id: config 字段}）：load() 幂等覆盖。qwen 的 settings.json
# 顶层 "model" 是 legacy（被忽略并告警）——老用户的 catalog.json 里 qwencode 还是
# format=json，不修正的话每次打开都会写无效字段；覆盖无风险（写了也不生效）。
CONFIG_PATCH = {
    "qwencode": {"path": "~/.qwen/settings.json", "format": None, "model_key": None},
}


def npm_pkg_name(cmd):
    """从 npm 安装命令里取包名（支持 @scope/name@latest）。

    跳过包名之前的 flag：`npm install -g --ignore-scripts @scope/pkg` 必须取到
    @scope/pkg，否则「检查更新」会拿 flag 当包名去查 registry。
    """
    m = re.search(r"npm\s+(?:install|i)\s+(.+)$", cmd or "")
    if not m:
        return None
    for tok in m.group(1).split():
        if tok.startswith("-"):
            continue
        if tok.startswith("@"):
            m2 = re.match(r"(@[^/]+/[^@]+)", tok)
            return m2.group(1) if m2 else None
        return tok.split("@")[0]
    return None


def derive_uninstall(cmd):
    """从安装命令推导卸载命令（npm / winget / pip 三种本机渠道）。

    卸载命令不单独维护一份，避免与安装命令不同步；认不出渠道返回 None，
    此时条目可显式配置 uninstall 字段覆盖。
    """
    c = (cmd or "").strip()
    m = re.search(r"npm\s+(?:install|i)\s+(.+)$", c)
    if m:
        pkg = npm_pkg_name(c)
        return "npm uninstall -g %s" % pkg if pkg else None
    m = re.search(r"winget\s+install\b(.*)$", c)
    if m:
        return ("winget uninstall" + m.group(1)).strip() or None
    m = re.search(r"((?:py\s+-[\d.]+|python3?|pip3?)\s+(?:-m\s+)?pip\s+install)\s+(.+)$", c)
    if m:
        # 去掉 -U / --upgrade 等 flag，只留包名
        pkgs = [t for t in m.group(2).split() if not t.startswith("-")]
        if pkgs:
            head = m.group(1).replace("pip install", "pip uninstall")
            return "%s -y %s" % (head, pkgs[0])
    return None


def uninstall_command(entry):
    """该条目的卸载命令：catalog 显式配置优先，否则由 install/upgrade 推导。"""
    explicit = (entry.get("uninstall") or "").strip()
    if explicit:
        return explicit
    return derive_uninstall(entry.get("install") or entry.get("upgrade") or "")


def _apply_resume_patch(entries):
    for e in entries:
        tmpl = ORCH_RESUME_PATCH.get(e.get("id"))
        orch = e.get("orch")
        if tmpl and isinstance(orch, dict) and orch.get("kind") == "generic" \
                and "resume_argv_template" not in orch:
            orch["resume_argv_template"] = list(tmpl)


def _apply_launch_patch(entries):
    for e in entries:
        if e.get("id") in LAUNCH_PATCH and not e.get("launch"):
            e["launch"] = dict(LAUNCH_PATCH[e["id"]])


def _apply_config_patch(entries):
    for e in entries:
        patch = CONFIG_PATCH.get(e.get("id"))
        if patch and e.get("config") != patch:
            e["config"] = dict(patch)


def _merge_new_defaults(entries):
    """把内置默认里「新增的」条目补进已加载清单（同 id 已存在则原样保留）。

    data/catalog.json 一旦生成就不会再重写，因此后来版本新增的智能体（如
    deepseek-harness）不会自动出现在老用户的清单里；这里按 id 做幂等补齐。
    只补缺失的 id，不动已有条目，也不回写用户文件（与 _apply_resume_patch 一致）。

    深拷贝是必须的：_apply_resume_patch 会就地改 entry["orch"]，浅拷贝会让
    这些改动写回 DEFAULT_CATALOG，进而污染 reset_to_default() 的产物。
    """
    have = {e.get("id") for e in entries}
    for d in DEFAULT_CATALOG:
        if d.get("id") not in have:
            entries.append(copy.deepcopy(d))


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
            entries = copy.deepcopy(DEFAULT_CATALOG)
        _merge_new_defaults(entries)
        _apply_resume_patch(entries)
        _apply_launch_patch(entries)
        _apply_config_patch(entries)
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
