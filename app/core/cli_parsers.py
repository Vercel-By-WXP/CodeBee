# -*- coding: utf-8 -*-
"""Provider-specific CLI output parsers and small argument builders."""
from __future__ import annotations

import json
import re


# 新版 codex 会把 config.toml 里已废弃的键作为 error item 播报一次
# （"Codex is ignoring N unrecognized configuration settings ... is ignored."）。
# 这是启动噪音，既不是本轮失败原因，也不该参与错误分类。
_CODEX_CONFIG_NOISE = re.compile(
    r"unrecognized configuration settings|is ignored\.?|ignoring \d+ unrecognized",
    re.I)


def _codex_config_noise(text):
    """True 当消息只是 config.toml 旧键告警（非本轮失败原因）。"""
    return bool(text) and bool(_CODEX_CONFIG_NOISE.search(str(text)))


def _parse_codex_jsonl(stdout):
    """解析 codex exec JSONL 事件流。返回 (text, usage, sid)。

    usage 细分来自 turn.completed：input_tokens（含 cached）、cached_input_tokens、
    output_tokens、reasoning_output_tokens；多 turn 累加。
    sid 来自 thread.started 的 thread_id（§07 T1.1：供后续 revise/fix 复用会话，
    会话内前缀按供应商缓存读计价）。
    """
    text = ""
    sid = ""
    usage = {"input": 0, "output": 0, "cached": 0, "reasoning": 0, "total": 0}
    for line in stdout.splitlines():
        line = line.strip()
        if not line.startswith("{"):
            continue
        try:
            ev = json.loads(line)
        except Exception:
            continue
        if ev.get("type") == "thread.started":
            sid = str(ev.get("thread_id") or sid)
        elif ev.get("type") == "item.completed":
            item = ev.get("item") or {}
            if item.get("type") == "agent_message" and item.get("text"):
                text = item["text"]
        elif ev.get("type") == "turn.completed":
            u = ev.get("usage") or {}
            inp = int(u.get("input_tokens") or 0)
            out = int(u.get("output_tokens") or 0)
            usage["input"] += inp
            usage["output"] += out
            usage["cached"] += int(u.get("cached_input_tokens") or 0)
            usage["reasoning"] += int(u.get("reasoning_output_tokens") or 0)
            total = u.get("total_tokens")
            usage["total"] += int(total) if total is not None else inp + out
    return text, usage, sid


def _codex_work_events(stdout):
    """统计本轮事件流里真实「动了手」的事件数（命令执行/文件改动/MCP 工具）。

    实现步空转闸的客观判据：2026-09-17 mo-so 实现步模型一条命令都没发，纯口头
    谎报「进程执行被策略拦截」却 exit 0 + 一段像模像样的总结——text 看不出敷衍，
    command_execution 计数为 0 才是硬证据。"""
    n = 0
    for line in stdout.splitlines():
        line = line.strip()
        if not line.startswith("{"):
            continue
        try:
            ev = json.loads(line)
        except Exception:
            continue
        if ev.get("type") == "item.completed" and (
                ev.get("item") or {}).get("type") in (
                "command_execution", "file_change", "mcp_tool_call"):
            n += 1
    return n


def _codex_silent_no_answer(stdout):
    """检测 codex「轮次静默空答」，返回 (是否静默, 最后一条 agent_message 文本)。

    形态=事件流里有工具动作（command_execution/file_change/mcp_tool_call），
    但没有任何一条 agent_message 晚于最后一个工具动作——模型干完活没交结论
    就 turn.completed。2026-10-16 神瞳实案：qa 轮吐完开场白、跑完 6 条读文件
    命令后正常收尾（exit 0），开场白被当成最终答案展示。纯说话零动手的轮次
    不算（那是正常短答）。"""
    seq = 0
    last_msg = -1
    last_work = -1
    msg_text = ""
    for line in (stdout or "").splitlines():
        line = line.strip()
        if not line.startswith("{"):
            continue
        try:
            ev = json.loads(line)
        except Exception:
            continue
        if ev.get("type") != "item.completed":
            continue
        item = ev.get("item") or {}
        t = item.get("type")
        if t == "agent_message" and str(item.get("text") or "").strip():
            seq += 1
            last_msg = seq
            msg_text = str(item.get("text"))
        elif t in ("command_execution", "file_change", "mcp_tool_call"):
            seq += 1
            last_work = seq
    return (last_work >= 0 and last_msg < last_work), msg_text


def _codex_fail_msg(stdout):
    """从 JSONL 事件流提取终态失败消息；无失败返回 ""。

    配额/限流可能出现在 error、turn.failed 或 item.completed(type=error)
    事件中；若遗漏，非零退出时错误串可能只剩
    "Reading prompt from stdin..."，导致分类与后备 CLI 都失效。
    Reconnecting... 是 CLI 内部重试噪音（可能自愈），不取作终态错误；重复
    重连由进程看门狗提前中断。turn.failed 优先于裸 error（后者取最后一条兜底）。
    """
    terminal, last_err = "", ""
    for line in (stdout or "").splitlines():
        line = line.strip()
        if not line.startswith("{"):
            continue
        try:
            ev = json.loads(line)
        except Exception:
            continue
        t = ev.get("type")
        if t == "turn.failed":
            e = ev.get("error")
            detail = (e.get("message") or e.get("data") or ""
                      if isinstance(e, dict) else e)
            if detail and not _codex_config_noise(str(detail)):
                terminal = str(detail)
        elif t in ("item.completed", "item.failed"):
            item = ev.get("item") or {}
            if isinstance(item, dict) and item.get("type") == "error":
                detail = item.get("message") or item.get("error") or item.get("text")
                if isinstance(detail, dict):
                    detail = detail.get("message") or detail.get("data") or detail
                # config.toml 里被新版 CLI 忽略的旧键会作为 error item 出现。
                # 它不是本轮失败原因，拿它当终态错误会把真实原因替换成配置
                # 告警（2026-10-23 实案：用户只看到 "disable_response_storage
                # is ignored"，看不到真正的退出原因）。
                if detail and not _codex_config_noise(str(detail)):
                    terminal = str(detail)
        elif t == "error":
            detail = ev.get("message") or ev.get("error") or ""
            if isinstance(detail, dict):
                detail = detail.get("message") or detail.get("data") or detail
            m = str(detail)
            if m and "reconnecting" not in m.lower() and not _codex_config_noise(m):
                last_err = m
    return terminal or last_err


def _parse_claude_json(stdout):
    data = None
    try:
        data = json.loads(stdout)
    except Exception:
        # stream-json：逐行事件取最后一条 type=="result"（与旧单 JSON 同构）
        for line in stdout.splitlines():
            line = line.strip()
            if not line.startswith("{"):
                continue
            try:
                ev = json.loads(line)
            except Exception:
                continue
            if isinstance(ev, dict) and ev.get("type") == "result":
                data = ev
    if not isinstance(data, dict):
        return None
    u = data.get("usage") or {}
    inp = int(u.get("input_tokens") or 0)
    out = int(u.get("output_tokens") or 0)
    # 缓存读 + 缓存写都计入 cached（读是省钱的部分，写是额外消耗的部分）
    cached = int(u.get("cache_read_input_tokens") or 0) + \
        int(u.get("cache_creation_input_tokens") or 0)
    usage = {"input": inp, "output": out, "cached": cached, "reasoning": 0,
             "total": inp + out + cached}
    # 网关实服模型：请求 deepseek-v4.1-flash 却被网关映射成 gemini-3.8-flash
    # 之类（2026-09-23 夜班实案）——用量统计记错名、失败排查看错病根，都
    # 靠 result 行的 modelUsage 对出来
    served = sorted({str(k) for k in ((data.get("modelUsage") or {}) or {})})
    return {
        "text": data.get("result") or "",
        "cost_usd": data.get("total_cost_usd") or 0.0,
        "usage": usage,
        "tokens": usage["total"],
        "is_error": bool(data.get("is_error")),
        # §07 T1.1：claude -p 返回本次会话 id，供 --resume 复用
        "sid": str(data.get("session_id") or ""),
        "served_models": served,
    }


def _codex_provider_args(cp):
    """把外部供应商注入为一次性的 codex model_provider（-c 覆盖，不改配置文件）。"""
    name = cp.get("name", "orch")
    return ["-c", 'model_provider="%s"' % name,
            "-c", 'model_providers.%s.name="%s"' % (name, name),
            "-c", 'model_providers.%s.base_url="%s"' % (name, cp.get("base_url", "")),
            "-c", 'model_providers.%s.env_key="%s"' % (name, cp.get("env_key", "ORCH_API_KEY")),
            "-c", 'model_providers.%s.wire_api="%s"' % (name, cp.get("wire_api", "responses"))]


def _model_flag(kind, model):
    if kind in ("codex", "qwen"):
        return ["-m", model]
    return ["--model", model]
