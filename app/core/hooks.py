# -*- coding: utf-8 -*-
"""任务生命周期钩子（借鉴 ZCode hooks，收窄为三个事件——2026-09-22 用户立项）：

- task_start     任务开跑前（编排/直连都触发）：stdout 可注入文本，追加进任务上下文
- message_submit 用户消息送达前：stdout 可注入文本，随消息进提示词头
- run_end        每轮运行结束：纯副作用型（回写知识库等），输出被忽略

协议：钩子命令从 stdin 收 JSON（事件+上下文）；stdout 若为 {"inject": "..."}
取该字段为注入文本，否则把非空 stdout 整体当注入文本。
失败/超时一律静默跳过（外部脚本不稳绝不拖死编排主流程）。

命令执行：shlex 分词（保 Windows 反斜杠）后走参数列表，不经 shell——
不支持管道/重定向等 shell 语法，需要时写 .py/.bat 包装脚本。
配置：DATA_DIR/hooks.json，纯本机设置。"""
from __future__ import annotations

import json
import logging
import os
import shlex
import subprocess
import time

from . import hook_diagnostics

from . import paths

log = logging.getLogger(__name__)

EVENTS = ("task_start", "message_submit", "run_end")
_DEFAULT_TIMEOUT = 10
_FILE = None          # 测试重绑（同 modelhub._FILE 惯例）

_CREATE_NO_WINDOW = 0x08000000 if hasattr(subprocess, "CREATE_NO_WINDOW") else 0


def _path():
    return _FILE or (paths.DATA_DIR / "hooks.json")


def _default_list():
    return []


def load_list():
    """读钩子配置列表；损坏/缺省回落空表。"""
    try:
        data = json.loads(_path().read_text(encoding="utf-8"))
        return data if isinstance(data, list) else _default_list()
    except Exception:
        return _default_list()


def save_list(items):
    """整表保存（UI 全量提交）。字段归一 + 事件白名单。"""
    if not isinstance(items, list):
        raise ValueError("hooks 必须是数组")
    out = []
    for i, h in enumerate(items):
        if not isinstance(h, dict):
            continue
        name = str(h.get("name") or "").strip()[:60]
        event = str(h.get("event") or "").strip()
        cmd = str(h.get("cmd") or "").strip()
        if not cmd:
            continue
        if event not in EVENTS:
            raise ValueError("第 %d 条钩子 event 非法（%s）" % (i + 1, event))
        try:
            timeout = max(1, min(120, int(h.get("timeout_s") or _DEFAULT_TIMEOUT)))
        except (TypeError, ValueError):
            timeout = _DEFAULT_TIMEOUT
        out.append({"id": str(h.get("id") or ("hk-%d" % (i + 1)))[:40],
                    "name": name or ("钩子 %d" % (i + 1)),
                    "event": event, "cmd": cmd,
                    "enabled": bool(h.get("enabled", True)),
                    "timeout_s": timeout})
    tmp = _path().with_suffix(".tmp")
    tmp.write_text(json.dumps(out, ensure_ascii=False, indent=1), encoding="utf-8")
    tmp.replace(_path())
    return out


def _argv(cmd):
    """命令串 → 参数列表。Windows 反斜杠路径保真（posix=False），支持引号分组；
    分词后剥掉 token 首尾引号（CreateProcess 不认字面引号路径）。"""
    toks = shlex.split(str(cmd or ""), posix=False)
    out = []
    for t in toks:
        if len(t) >= 2 and t[0] == t[-1] and t[0] in ('"', "'"):
            t = t[1:-1]
        if t:
            out.append(t)
    return out


def _execute(h, payload):
    """Execute once and report the real process outcome without raising."""
    start = time.monotonic()

    def finish(status, *, output=None, exit_code=None, error=""):
        error = hook_diagnostics.safe_error(error)
        result = {"ok": status == "succeeded", "status": status,
                  "output": output, "error": error, "exit_code": exit_code}
        try:
            hook_diagnostics.record(paths.DATA_DIR, h, payload, status=status, exit_code=exit_code,
                                    duration_ms=(time.monotonic() - start) * 1000, error=error)
        except Exception:
            pass  # Diagnostic storage never changes hook execution semantics.
        return result

    try:
        argv = _argv(h.get("cmd"))
        if not argv:
            return finish("invalid_command", error="Hook command is empty")
    except ValueError as exc:
        return finish("invalid_command", error=str(exc))
    try:
        env = os.environ.copy()
        env["PYTHONIOENCODING"] = "utf-8"
        proc = subprocess.run(
            argv, shell=False,
            input=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
            capture_output=True, timeout=int(h.get("timeout_s") or _DEFAULT_TIMEOUT),
            creationflags=_CREATE_NO_WINDOW, env=env)
    except subprocess.TimeoutExpired:
        return finish("timeout", error="Hook timed out")
    except Exception as exc:
        return finish("spawn_failed", error=str(exc))
    if proc.returncode != 0:
        stderr = (proc.stderr or b"").decode("utf-8", "replace")
        return finish("failed", exit_code=proc.returncode,
                      error="Hook exit %s: %s" % (proc.returncode, stderr))
    out = (proc.stdout or b"").decode("utf-8", "replace").strip()
    try:
        data = json.loads(out)
        if isinstance(data, dict):
            out = str(data.get("inject") or "")
    except (ValueError, TypeError):
        pass
    return finish("succeeded", output=out or None, exit_code=0)


def _run_one(h, payload):
    result = _execute(h, payload)
    return result["output"] if result["ok"] else None


def diagnostics():
    return hook_diagnostics.view(paths.DATA_DIR)


def run_event(event, ctx=None, task_id=None, run_id=None, text=None):
    """触发事件：依次跑启用的钩子，合并注入文本。任何异常不外抛。

    ctx 附带任务/运行摘要；text 仅 message_submit 时传用户消息原文。
    返回拼接好的注入文本（可为空串）。"""
    if event not in EVENTS:
        return ""
    try:
        items = [h for h in load_list() if h.get("enabled") and h.get("event") == event]
    except Exception:
        return ""
    if not items:
        return ""
    payload = {"event": event, "task_id": task_id or "", "run_id": run_id or "",
               "text": text or ""}
    if ctx is not None:
        payload["ctx"] = ctx
    outs = []
    for h in items:
        piece = _run_one(h, payload)
        if piece:
            outs.append(piece.strip())
    return "\n\n".join(outs)


def test_one(cmd, event="task_start", timeout_s=None):
    """设置页「测试」：手动跑一条钩子，返回 (ok, 输出/错误人话)。"""
    h = {"name": "test", "event": event if event in EVENTS else "task_start",
         "cmd": cmd, "timeout_s": timeout_s or _DEFAULT_TIMEOUT}
    result = _execute(h, {"event": h["event"], "task_id": "", "run_id": "", "text": ""})
    if not result["ok"]:
        return False, result["error"]
    return True, result["output"] or "（无输出，未注入）"
