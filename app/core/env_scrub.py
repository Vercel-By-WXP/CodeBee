# -*- coding: utf-8 -*-
"""环境变量净化：剥离含 KEY/SECRET/TOKEN/PASSWORD 等密钥名的变量。

设计稿：docs/migration/01-defense-patterns.md §5A。
参考 dsh：docs/defensive-patterns.zh.md 第 31 行（"绝不将环境变量或可预测路径暴露给不可信输出"）。
"""
from __future__ import annotations

import logging
import re

log = logging.getLogger(__name__)

# 黑名单：环境变量名含这些子串（不区分大小写）即剥离。
_DENY_RE = re.compile(r"(KEY|SECRET|TOKEN|PASSWORD|PASSWD|CREDENTIAL)", re.IGNORECASE)

# 白名单：精确匹配（不分大小写），用于覆盖黑名单。
_ALLOW_EXACT = frozenset({
    # 系统/环境
    "PATH", "PATHEXT", "SYSTEMROOT", "TEMP", "TMP", "USERPROFILE", "USERDNSDOMAIN",
    "HOMEPATH", "HOMEDRIVE",
    "LANG", "LC_ALL", "LC_CTYPE",
    "HOME", "SHELL", "TERM", "PYTHONIOENCODING",
    "PYTHONPATH", "PYTHONUNBUFFERED",
    "COMSPEC", "WINDIR",
    # Tutti / DSH 内部
    "DSH_HOME", "TUTTI_HOME", "ALLINONE_HOME",
    # Claude CLI 自有（不是密钥本身，是配置）
    "CLAUDE_CODE_ENTRYPOINT", "CLAUDE_CODE_SSE_PORT",
    # PowerShell / Windows
    "PSMODULEPATH", "PROMPT",
})

# 白名单前缀（区分大小写以匹配 Windows 习惯）；含前缀视为放行。
_ALLOW_PREFIX = (
    "CODEX_", "CLAUDE_CODE_", "OPENAI_", "ANTHROPIC_",
    "QWEN_", "AIDER_", "OPENCODE_", "GEMINI_",
    "TUTTI_", "ALLINONE_", "DSH_",
    "LANG", "LC_",  # locale 系列
    "PYTHON",       # PYTHONPATH / PYTHONIOENCODING 等
    "CHROME", "EDGE", "PLAYWRIGHT",  # 本机测试工具
)


def _should_keep(k: str) -> bool:
    if k in _ALLOW_EXACT:
        return True
    for p in _ALLOW_PREFIX:
        if k.startswith(p):
            return True
    return False


def scrub_env(env: dict, *, mode: str = "drop") -> dict:
    """返回净化后的 env 副本（原 dict 不修改）。

    Args:
        env: 原始环境变量字典
        mode:
          "drop" - 直接删除命中黑名单的 key（默认）
          "stub" - 把值替换为 "__REDACTED__"（子进程若检查值类型可能崩）
          "off"  - 完全不过滤（用于测试 / 排查）
    """
    if mode == "off":
        return dict(env)
    out: dict = {}
    dropped: list = []
    stubbed: list = []
    for k, v in env.items():
        if _should_keep(k):
            out[k] = v
        elif _DENY_RE.search(k):
            if mode == "stub":
                out[k] = "__REDACTED__"
                stubbed.append(k)
            else:
                dropped.append(k)
        else:
            out[k] = v
    if dropped:
        log.info("env-scrub dropped %d keys: %s", len(dropped), dropped[:20])
    if stubbed:
        log.info("env-scrub stubbed %d keys: %s", len(stubbed), stubbed[:20])
    return out