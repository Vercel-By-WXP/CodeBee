# -*- coding: utf-8 -*-
"""内置执行智能体：不经外部 CLI，直连供应商 API 完成直连（direct）任务。

为什么：direct 是快档位，典型诉求是问答/轻量改写。拉起 codex/claude CLI 要付
进程与框架开销，且 CLI 的事件流日志（启动命令、提示词回显、重连报错）会污染
对话视图。内置智能体直接打 chat API + 工具循环：
  - 模型选择：编排者配置（resolve_orchestrator）优先；未启用时扫首个可用供应商；
  - 工具：list_files / read_file / write_file（锁死工作目录内）+ run_command
    （本机全权执行命令，产品定位全信任，与 codex/claude 全权沙箱同档）；
  - 协议：anthropic / openai 走原生工具循环；google 无工具协议（纯文本直答）。
密钥冷却/多 KEY 展开复用 modelhub 既有记账（note_key_ok / note_key_error）。
"""
from __future__ import annotations

import json
import os
import re
import secrets
import threading
import time
import urllib.parse
import urllib.request

from . import modelhub, tlsctx

from .builtin_agent_tools import (  # noqa: F401 - 旧调用面再导出
    CMD_DEFAULT_TIMEOUT, CMD_MAX_TIMEOUT, CMD_OUT_HEAD, CMD_OUT_TAIL, CREATE_TASK_SPEC,
    IMAGE_JPEG_BYTES, IMAGE_MAX_BYTES, IMAGE_MAX_EDGE, LIST_MAX_ENTRIES, MAX_IMAGES,
    READ_EDIT_MAX_BYTES, READ_HEAD_BYTES, READ_MAX_BYTES, READ_SEGMENT_DEFAULT_LINES,
    READ_SEGMENT_MAX_LINES, READ_TAIL_BYTES, SCHEDULE_TASK_SPEC,
    SEARCH_DEFAULT_RESULTS, SEARCH_MAX_FILE_BYTES,
    TOOLS_SPEC, _BIN_MAGIC, _TOOL_IMPL, _WALK_PRUNE,
    _anthropic_tools, _available_skill_rows, _bin_format, _command_timeout,
    _exec_tool, _exec_tool_impl, _guard_rel, _guess_image_mime, _is_linklike, _looks_binary,
    _mcp_specs, _openai_tools, _prep_images, _read_text_capped, _sandbox_path_allowed,
    _split_tool_args, _tool_append_file, _tool_create_task, _tool_edit_file, _tool_find_files,
    _tool_fs_manage, _tool_list_files, _tool_list_skills, _tool_read_file,
    _tool_read_task_output, _tool_run_command, _tool_search_content, _tool_use_skill,
    _tool_web_search, _tool_write_file, _trim_partial_utf8,
)

MAX_TOOL_ITERS = 32          # 单步工具循环上限（防模型打转；收尾另留 1 轮）
THINK_MAX_CHARS = 20000        # 单次生成保留的思考正文上限（防超长思维链撑爆 run.json）
STREAM_MAX_CHARS = 20000       # 运行中实时回答的保留上限（与 THINK_MAX_CHARS 同口径）
BASE_MAX_TOKENS = 8000         # 单次生成输出预算（推理模型的思考 token 也计入此额度）
MAX_TOKENS_CAP = 16000         # 思考撑爆预算后的提额上限（每轮 run 只提一次）
THINK_EXHAUST_MIN_CHARS = 1500  # 零正文时思考达到此长度 ≈ 思考占满输出预算
EMPTY_TEXT_MAX_STREAK = 3      # 连续零正文轮数上限（防同参数盲重试烧穿预算）

TOOL_HISTORY_KEEP_ROUNDS = 4
TOOL_HISTORY_OLD_RESULT_CHARS = 1200
MAX_DIRECT_CONTEXT_CHARS = 48000  # ~12K tokens; keep direct chat within small model windows
_TOOL_OUTPUT_MARKER_RE = re.compile(
    r"\n…\[较早结果已卸载到本机；需查看全文请调用 read_tool_output，"
    r"ref=([a-f0-9]{32}) \]…\n")

_SYSTEM_PROMPT = """你是 CodeBee 的内置执行智能体，直接完成用户交代的任务。用户的目标、背景与工作目录内的附件就是全部输入。

## 工作方式
- 需要读文件、看目录、写文件时调用工具；所有路径都是工作目录内的相对路径。
- 改文件的优先级：小改动用 edit_file（精确查找替换，不必整份重写）＞追加用 append_file＞整份重建才用 write_file。删除/移动/重命名/复制用 fs_manage，不要绕 shell。
- 大文件（超 64KB）默认只读头尾、中段省略；要看全用 read_file 的 offset/lines 分段读（返回头会提示下一段的 offset）。找内容用 search_content（带行号的 grep），找文件用 find_files，不要逐个文件读。
- read_file 只能读文本文件；图片、压缩包等二进制文件读不了，如实告知用户即可，不要反复尝试。
- 较早工具结果若提示已卸载并给出 ref，使用 read_tool_output 按需读取（可传 offset 分段）；不要假设压缩标记就是完整结果。
- 用户要求使用某项已安装技能时，先用 use_skill 按名称读取，再遵循技能正文；不确定名称可先用 list_skills。需要公开网页信息时用 web_search，任务状态和输出用 read_task_output。已配置的 MCP 工具可直接调用。
- 用户消息中的图片附件会直接出现在对话里，可直接看图作答，无需用工具读取。
- run_command 直接在用户的电脑上执行命令并回传退出码与输出：诊断、修复、改配置、重启服务等操作会真实生效。命令跑完看输出再决定下一步，不要一次性罗列步骤让用户自己敲。
- 当工具列表中提供 create_task 时，如果用户明确要求开始另一项 CodeBee 工作（例如“新建连载小说任务”“再开一个代码任务”），调用它发起独立任务；不要只在当前回答里描述应该怎么做。先从用户话里提取任务类型和目标，缺少关键目标时再追问。
- Windows 上需要管理员权限的命令（flushdns、winsock reset、防火墙、系统服务等），用 powershell -Command "Start-Process <程序> -ArgumentList '<参数>' -Verb RunAs -Wait" 触发 UAC——用户屏幕会弹窗，点允许即提权执行；提权进程的输出拿不到，之后要用普通命令复核效果。macOS/Linux 用 sudo 并在输出里提示用户输密码不可行时改写临时脚本让用户跑。
- 避免跑长驻/交互式命令（ping -t、top、要按键应答的安装器），它们会拖满超时被强杀。
- 产出文件一律 UTF-8 编码。
- 回答用户的语言与用户一致（默认中文）。直接给结论和内容，不要输出任何机器标记或协议行。
- 核心产出完成后立即停止探索并总结；不要为“再确认一次”重复读取或重复写入。
- 每次工具调用后都要判断是否已经达到目标；如果工具轮次即将耗尽，先完成已开始的操作，随后直接给出已完成内容、产出文件和未完成项。
"""


def resolve(provider_id="", model="", difficulty="default"):
    """选内置智能体可用的 (provider, model)：编排者配置优先，否则首个可用供应商。

    返回 {prov, model, provider_id, provider_name} 或 None（无可用供应商 → 调用方
    回退 CLI 路径）。"""
    provider_id = str(provider_id or "").strip()
    model = str(model or "").strip()
    if provider_id:
        for p in modelhub.providers():
            if p.get("id") != provider_id or not p.get("enabled", True) or not p.get("api_key"):
                continue
            selected = model
            if selected:
                try:
                    enabled_names = [m.get("name") for m in modelhub._enabled_models(p)]
                except Exception:
                    enabled_names = []
                if selected not in enabled_names:
                    return None
            if not selected:
                # 默认/难度模型键位与 models[] 启停开关不联动：键位指向已停用
                # 模型时对账启用名单回落，不冒充可用（停用的模型不准用）
                try:
                    selected = modelhub._usable_default_model(
                        p, (p.get("model_hard") if difficulty == "hard" else
                            p.get("model_easy") if difficulty == "easy" else
                            p.get("model")) or "")
                except Exception:
                    selected = (p.get("model") or "").strip()
            if selected:
                return {"prov": p, "model": selected,
                        "selection_source": "task.direct_provider",
                        "provider_id": p.get("id") or "",
                        "provider_name": p.get("name") or p.get("id") or ""}
        return None
    orch = None
    try:
        orch = modelhub.resolve_orchestrator()
    except Exception:
        orch = None
    if orch:
        prov, model = orch
        return {"prov": prov, "model": model,
                "selection_source": "orchestrator",
                "provider_id": prov.get("id") or "",
                "provider_name": prov.get("name") or prov.get("id") or ""}
    for p in modelhub.providers():
        if not p.get("enabled", True) or not p.get("api_key"):
            continue
        try:
            m = modelhub._usable_default_model(
                p, (p.get("model_hard") if difficulty == "hard" else
                    p.get("model_easy") if difficulty == "easy" else
                    p.get("model")) or "")
        except Exception:
            m = (p.get("model") or "").strip()
        if m:
            return {"prov": p, "model": m, "provider_id": p.get("id") or "",
                    "selection_source": "provider.default",
                    "provider_name": p.get("name") or p.get("id") or ""}
    return None



def _post_json(url, headers, body, allow_private, timeout):
    """传输层单点（测试在这里打桩）。"""
    return modelhub._post_json_http(url, headers, body, allow_private, timeout=timeout)


def _call_with_optional_deadline(fn, args, kwargs, deadline):
    """Keep older test/integration transport shims source-compatible."""
    if deadline is None:
        return fn(*args, **kwargs)
    try:
        return fn(*args, **kwargs, deadline=deadline)
    except TypeError as exc:
        if "unexpected keyword argument 'deadline'" not in str(exc):
            raise
        return fn(*args, **kwargs)


def _post_interruptible(url, headers, body, allow_private, timeout, cancel_event,
                        deadline=None):
    """可打断的模型调用：cancel_event 置位即刻放弃等待返回。

    单次生成最长可跑满 timeout，取消不能陪跑到自然结束——HTTP 交给守护
    线程自行收尾（响应被丢弃，socket 随线程结束释放），主流程立即返回。
    服务端仍会把这次生成跑完（token 已花），但任务本身即刻终止。"""
    box = {}

    def _go():
        try:
            box["r"] = _post_json(url, headers, body, allow_private, timeout)
        except Exception as e:
            box["r"] = (0, None, repr(e))

    th = threading.Thread(target=_go, daemon=True)
    th.start()
    wait_until = deadline if deadline is not None else time.monotonic() + timeout + 10
    while th.is_alive():
        if cancel_event is not None and cancel_event.is_set():
            break
        if time.monotonic() >= wait_until:
            return 0, None, ("任务总时限已到" if deadline is not None else "响应超时")
        th.join(0.5)
    if cancel_event is not None and cancel_event.is_set():
        return 0, None, "已取消"
    return box.get("r") or (0, None, "无响应")


# ---------------------------------------------------------------- 思考过程（流式）
# 用户诉求（2026-09-22）：「把思考过程打印出来」——直连对话此前整轮只有三点
# 打字动画，模型的思维链（DeepSeek/vLLM 系 reasoning_content、Anthropic
# thinking 块）被 _sse_parse 明确丢弃。这里自带一份「认思考」的增量解析：
# 边收边把思考与正文分别攒起来，交给调用方实时落盘 + 结束后存进步骤记录。

def _reason_from_delta(proto, obj):
    """从一条流式事件里取「思考增量」。三协议字段各异，取不到返回空串。

    openai 兼容面最杂：reasoning_content（DeepSeek/vLLM/云知声实测）、
    reasoning（部分网关）、reasoning_details[].text（OpenRouter 系）。
    anthropic 是 thinking_delta（另有 redacted_thinking 不文本化，跳过）。"""
    if proto == "openai":
        total = ""
        for ch in obj.get("choices") or []:
            d = ch.get("delta") or {}
            for k in ("reasoning_content", "reasoning"):
                v = d.get(k)
                if isinstance(v, str):
                    total += v
            for det in (d.get("reasoning_details") or []):
                if isinstance(det, dict) and isinstance(det.get("text"), str):
                    total += det["text"]
        return total
    if proto == "anthropic" and obj.get("type") == "content_block_delta":
        d = obj.get("delta") or {}
        if d.get("type") == "thinking_delta":
            return d.get("thinking") or ""
        return ""
    if proto == "google":
        total = ""
        for cand in obj.get("candidates") or []:
            for p in ((cand.get("content") or {}).get("parts") or []):
                if isinstance(p, dict) and p.get("thought"):
                    total += p.get("text") or ""
        return total
    return ""


def _reason_from_message(proto, data):
    """非流式响应里的思考字段（网关不支持流式时的兜底）。"""
    if not isinstance(data, dict):
        return ""
    if proto == "anthropic":
        return "\n".join(b.get("thinking", "") for b in (data.get("content") or [])
                         if isinstance(b, dict) and b.get("type") == "thinking")
    if proto == "google":
        total = ""
        for cand in data.get("candidates") or []:
            for p in ((cand.get("content") or {}).get("parts") or []):
                if isinstance(p, dict) and p.get("thought"):
                    total += p.get("text") or ""
        return total
    msg = ((data.get("choices") or [{}])[0].get("message") or {})
    total = ""
    for k in ("reasoning_content", "reasoning"):
        v = msg.get(k)
        if isinstance(v, str):
            total += v
    for det in (msg.get("reasoning_details") or []):
        if isinstance(det, dict) and isinstance(det.get("text"), str):
            total += det["text"]
    return total


def _stream_accumulate(proto, obj, acc):
    """把一条 SSE 事件并入累加器（就地改写 acc）。返回是否有「有意义的进展」。

    acc = {text, reasoning, tools, usage}；工具参数按 index 归并——openai 的
    function.arguments 与 anthropic 的 input_json_delta 都是分片到达的。"""
    progress = False
    if proto == "anthropic":
        t = obj.get("type")
        if t == "content_block_start":
            blk = obj.get("content_block") or {}
            if blk.get("type") == "tool_use":
                idx = int(obj.get("index") or 0)
                acc["tools"][idx] = {"id": blk.get("id") or "",
                                     "name": blk.get("name") or "", "raw": ""}
                progress = True
        elif t == "content_block_delta":
            d = obj.get("delta") or {}
            if d.get("type") == "text_delta" and d.get("text"):
                acc["text"] += d["text"]
                progress = True
            elif d.get("type") == "input_json_delta":
                idx = int(obj.get("index") or 0)
                slot = acc["tools"].setdefault(idx, {"id": "", "name": "", "raw": ""})
                slot["raw"] += d.get("partial_json") or ""
                progress = True
        elif t == "message_start":
            u = (obj.get("message") or {}).get("usage") or obj.get("usage") or {}
            acc["usage"].update({"input": int(u.get("input_tokens") or 0),
                                 "output": int(u.get("output_tokens") or 0),
                                 "cached": (int(u.get("cache_read_input_tokens") or 0)
                                            + int(u.get("cache_creation_input_tokens") or 0))})
        elif t == "message_delta":
            u = obj.get("usage") or {}
            if u.get("output_tokens"):
                acc["usage"]["output"] = int(u["output_tokens"])
    elif proto == "google":
        for cand in obj.get("candidates") or []:
            for p in ((cand.get("content") or {}).get("parts") or []):
                if not isinstance(p, dict):
                    continue
                if p.get("thought"):
                    acc["reasoning"] += p.get("text") or ""
                    progress = True
                elif p.get("functionCall"):
                    fc = p["functionCall"]
                    acc["tools"][len(acc["tools"])] = {
                        "id": "call_%d" % (len(acc["tools"]) + 1),
                        "name": fc.get("name") or "",
                        "raw": json.dumps(fc.get("args") or {}, ensure_ascii=False)}
                    progress = True
                elif p.get("text"):
                    acc["text"] += p["text"]
                    progress = True
        u = obj.get("usageMetadata")
        if isinstance(u, dict):
            acc["usage"].update({"input": int(u.get("promptTokenCount") or 0),
                                 "output": int(u.get("candidatesTokenCount") or 0),
                                 "total": int(u.get("totalTokenCount") or 0)})
    else:
        for ch in obj.get("choices") or []:
            d = ch.get("delta") or {}
            if isinstance(d.get("content"), str) and d["content"]:
                acc["text"] += d["content"]
                progress = True
            for i, tc in enumerate(d.get("tool_calls") or []):
                if not isinstance(tc, dict):
                    continue
                idx = int(tc.get("index") if tc.get("index") is not None else i)
                slot = acc["tools"].setdefault(idx, {"id": "", "name": "", "raw": ""})
                if tc.get("id"):
                    slot["id"] = tc["id"]
                fn = tc.get("function") or {}
                if fn.get("name"):
                    slot["name"] = fn["name"]
                if fn.get("arguments"):
                    slot["raw"] += fn["arguments"]
                progress = True
        u = obj.get("usage")
        if isinstance(u, dict):
            acc["usage"].update({"input": int(u.get("prompt_tokens") or 0),
                                 "output": int(u.get("completion_tokens") or 0),
                                 "total": int(u.get("total_tokens") or 0),
                                 "cached": int(((u.get("prompt_tokens_details") or {})
                                                .get("cached_tokens")) or 0)})
    return progress


def _calls_from_acc(acc):
    """累加器里的工具分片 → [{id, name, args}]（参数 JSON 解析失败给空对象）。"""
    out = []
    for idx in sorted(acc["tools"].keys()):
        slot = acc["tools"][idx]
        name = slot.get("name") or ""
        if not name:
            continue
        try:
            args = json.loads(slot.get("raw") or "{}")
        except Exception:
            args = {}
        if not isinstance(args, dict):
            args = {}
        out.append({"id": slot.get("id") or ("call_%d" % (idx + 1)),
                    "name": name, "args": args})
    return out


def _post_sse_stream(url, headers, body, allow_private, timeout, proto,
                     on_reason=None, on_text=None, on_tick=None, cancel_event=None,
                     deadline=None):
    """流式（SSE）模型调用 → dict(status/text/reasoning/calls/usage/error/events)。

    与 _post_interruptible 同款的可打断语义：读取放守护线程，cancel_event 置位
    即刻放弃等待（socket 随线程结束释放）。三个回调：on_reason 每收到一段思考
    增量（调用方据此实时打印「思考过程」），on_text 收到**累计**正文（只在真的
    变长时触发，运行中直接当正文预览），on_tick 是无文本进展时的心跳。
    events=0 表示网关根本没按流回（多半忽略了 stream 字段）——调用方据此回落
    非流式；events>0 而正文为空则是模型真没说话，不该重发（白烧一次生成）。

    首字延迟与吞吐（§07 体验面）：`first_token_ms` = 发出请求到第一个**带内容**
    的增量（思考 / 正文 / 工具参数）之间的毫秒数；`tokens_per_sec` 用 output
    token 除以「首个到末个内容增量」的窗口，窗口过短或拿不到 output 时记 0
    （0 = 不可测，不是"一秒 0 个 token"）。两者只有走流式才有值，非流式回落
    路径与 CLI 子进程路径都给 0。"""
    acc = {"text": "", "reasoning": "", "tools": {}, "usage": {}}
    box = {"status": 0, "err": "", "events": 0, "sent_text": None,
           "first_ms": None, "last_ms": None}
    lock = threading.Lock()

    def _emit(cb, payload):
        if cb is None:
            return
        try:
            cb(payload)
        except Exception:
            pass
 
    def _go():
        try:
            parsed = urllib.parse.urlsplit(url)
            if parsed.scheme not in ("http", "https"):
                box["err"] = "协议必须是 http/https"
                return
            host, herr = modelhub._validate_host(url, allow_private)
            if host is None:
                box["err"] = herr
                return
            t_send = time.monotonic()      # 首字延迟基线：请求发出，不含 DNS/连接前准备
            req = urllib.request.Request(
                url, method="POST",
                headers=dict(headers, **{"Content-Type": "application/json",
                                         "Accept": "text/event-stream"}),
                data=json.dumps(body).encode("utf-8"))
            with modelhub._opener().open(req, timeout=timeout) as resp:
                box["status"] = resp.status
                if not 200 <= resp.status < 300:
                    raw = resp.read(65536).decode("utf-8", "replace")
                    box["err"] = "HTTP %s %s" % (resp.status, raw[:200])
                    return
                for raw in resp:
                    line = raw.decode("utf-8", "replace").strip()
                    if not line.startswith("data:"):
                        continue
                    payload = line[5:].strip()
                    if not payload or payload == "[DONE]":
                        continue
                    try:
                        obj = json.loads(payload)
                    except Exception:
                        continue          # 半行/坏行静默跳过，不中断整流
                    if not isinstance(obj, dict):
                        continue
                    box["events"] += 1
                    reason = _reason_from_delta(proto, obj)
                    with lock:
                        if reason:
                            acc["reasoning"] += reason
                        moved = _stream_accumulate(proto, obj, acc)
                        text_now = acc["text"]
                    if reason or moved:
                        now_ms = (time.monotonic() - t_send) * 1000.0
                        if box["first_ms"] is None:
                            box["first_ms"] = now_ms
                        box["last_ms"] = now_ms
                    if reason:
                        _emit(on_reason, reason)
                    if text_now != box["sent_text"]:
                        box["sent_text"] = text_now
                        _emit(on_text, text_now)     # 累计正文（运行中实时打印）
                    elif moved and not reason:
                        _emit(on_tick, None)         # 只有工具/用量在推进的心跳
                    if sum(len(x) for x in (acc["text"], acc["reasoning"])) > 4 * 1024 * 1024:
                        break             # 防失控
        except Exception as e:
            box["err"] = box["err"] or tlsctx.humanize(repr(e)[:300])

    th = threading.Thread(target=_go, daemon=True)
    th.start()
    wait_until = deadline if deadline is not None else time.monotonic() + timeout + 10
    while th.is_alive():
        if cancel_event is not None and cancel_event.is_set():
            break
        if time.monotonic() >= wait_until:
            break
        th.join(0.5)
    with lock:
        out = {"status": box["status"], "text": acc["text"],
               "reasoning": acc["reasoning"], "usage": dict(acc["usage"]),
               "calls": _calls_from_acc(acc), "events": box["events"],
               "first_token_ms": round(box["first_ms"], 1) if box["first_ms"] is not None else 0.0,
               "tokens_per_sec": 0.0, "error": box["err"]}
        gen_s = 0.0
        if box["first_ms"] is not None and box["last_ms"] is not None:
            gen_s = (box["last_ms"] - box["first_ms"]) / 1000.0
        out_tok = int(acc["usage"].get("output") or 0)
        if gen_s > 0.2 and out_tok > 0:
            out["tokens_per_sec"] = round(out_tok / gen_s, 2)
    if cancel_event is not None and cancel_event.is_set():
        out["error"], out["status"] = "已取消", 0
        return out
    if th.is_alive():
        out["error"], out["status"] = ("任务总时限已到" if deadline is not None
                                         else "响应超时"), 0
        out["deadline_exceeded"] = deadline is not None
    return out


def _m_openai(m):
    """内部消息 → openai 消息。tool_results: [(call_id, 结果文本)]。"""
    if m["role"] == "assistant":
        out = {"role": "assistant", "content": m.get("content") or ""}
        if m.get("tool_calls"):
            out["tool_calls"] = [{"id": tc["id"], "type": "function",
                                  "function": {"name": tc["name"],
                                               "arguments": json.dumps(tc["args"], ensure_ascii=False)}}
                                 for tc in m["tool_calls"]]
        return out
    if m["role"] == "tool_results":
        return [{"role": "tool", "tool_call_id": cid, "content": text}
                for cid, text in m["tool_results"]]
    imgs = m.get("images") or []
    if imgs:   # 多模态：content 变数组，图跟在文本后（role=tool 只收文本，故图只走 user）
        content = [{"type": "text", "text": m.get("content") or ""}]
        content += [{"type": "image_url",
                     "image_url": {"url": "data:%s;base64,%s" % (mime, b64)}}
                    for mime, b64 in imgs]
        return {"role": "user", "content": content}
    return {"role": "user", "content": m.get("content") or ""}


def _m_anthropic(m):
    if m["role"] == "assistant":
        blocks = []
        if m.get("content"):
            blocks.append({"type": "text", "text": m["content"]})
        for tc in (m.get("tool_calls") or []):
            blocks.append({"type": "tool_use", "id": tc["id"], "name": tc["name"],
                           "input": tc["args"]})
        return {"role": "assistant", "content": blocks or [{"type": "text", "text": ""}]}
    if m["role"] == "tool_results":
        return {"role": "user", "content": [
            {"type": "tool_result", "tool_use_id": cid, "content": text}
            for cid, text in m["tool_results"]]}
    imgs = m.get("images") or []
    if imgs:
        blocks = [{"type": "text", "text": m.get("content") or ""}]
        blocks += [{"type": "image",
                    "source": {"type": "base64", "media_type": mime, "data": b64}}
                   for mime, b64 in imgs]
        return {"role": "user", "content": blocks}
    return {"role": "user", "content": [{"type": "text", "text": m.get("content") or ""}]}


def _compact_old_tool_results(messages, keep_rounds=TOOL_HISTORY_KEEP_ROUNDS,
                              old_result_chars=TOOL_HISTORY_OLD_RESULT_CHARS,
                              run_id=""):
    """Bound old tool output while preserving recent call/result pairs."""
    result_indexes = [i for i, msg in enumerate(messages)
                      if msg.get("role") == "tool_results"]
    keep_rounds = max(0, int(keep_rounds))
    compact_indexes = result_indexes[:max(0, len(result_indexes) - keep_rounds)]
    for idx in compact_indexes:
        msg = messages[idx]
        compacted = []
        for call_id, value in msg.get("tool_results") or []:
            value = str(value or "")
            if len(value) > old_result_chars:
                existing_ref = _existing_tool_output_ref(value, run_id, call_id)
                if existing_ref:
                    # Idempotent across repeated context passes: never save the
                    # already-truncated marker as a new "full" tool output.
                    compacted.append((call_id, value))
                    continue
                head = old_result_chars * 2 // 3
                tail = old_result_chars - head
                try:
                    from . import tool_outputs
                    ref = tool_outputs.save(run_id, call_id, value) if run_id else ""
                except Exception:
                    ref = ""
                if ref:
                    marker = ("\n…[较早结果已卸载到本机；需查看全文请调用 read_tool_output，"
                              "ref=%s ]…\n" % ref)
                else:
                    marker = "\n…[较早工具结果已压缩；需要时请重新读取对应文件/调用工具]…\n"
                value = value[:head] + marker + value[-tail:]
            compacted.append((call_id, value))
        msg["tool_results"] = compacted


def _existing_tool_output_ref(value, run_id, call_id):
    """Return a ref only when a marker previews this call's stored full output.

    Tool output is untrusted. A bare ``ref=<hex>`` substring is not evidence of a
    CodeBee marker: require the complete framing and verify the opaque reference
    against this call's stored full output before treating it as already unloaded.
    """
    match = _TOOL_OUTPUT_MARKER_RE.search(str(value or ""))
    if not match or not str(run_id or "").strip():
        return ""
    try:
        from . import tool_outputs
        is_match = tool_outputs.matches_compacted_output(
            run_id, call_id, match.group(1), value[:match.start()], value[match.end():])
        return match.group(1) if is_match else ""
    except Exception:
        return ""


def _direct_context_chars(messages):
    return sum(len(json.dumps(msg, ensure_ascii=False, default=str)) for msg in messages)


def _bound_direct_context(messages, *, max_chars=MAX_DIRECT_CONTEXT_CHARS,
                          keep_rounds=TOOL_HISTORY_KEEP_ROUNDS, run_id=""):
    """Fold complete old tool rounds when a direct-agent conversation grows large.

    Unlike pipeline steps, direct chat has no session-log compaction surface. Keep the
    original request and the newest tool interactions, but cap older outputs and replace
    discarded call/result pairs with a bounded factual note. Full large outputs remain
    available through read_tool_output references.
    """
    try:
        max_chars = max(1000, int(max_chars))
        keep_rounds = max(1, int(keep_rounds))
    except (TypeError, ValueError):
        max_chars, keep_rounds = MAX_DIRECT_CONTEXT_CHARS, TOOL_HISTORY_KEEP_ROUNDS
    rounds = []
    for index, msg in enumerate(messages[:-1]):
        if msg.get("role") == "assistant" and msg.get("tool_calls") \
                and messages[index + 1].get("role") == "tool_results":
            rounds.append((index, index + 1))
    if not rounds:
        return
    if _direct_context_chars(messages) <= max_chars:
        return

    # First compact all tool results; recent full output is useful only while the
    # conversation has room. Oversized output is recoverable by its scoped ref.
    result_budget = max(160, min(TOOL_HISTORY_OLD_RESULT_CHARS,
                                 max_chars // max(1, keep_rounds * 8)))
    _compact_old_tool_results(messages, keep_rounds=0,
                              old_result_chars=result_budget, run_id=run_id)
    note_index = 1 if messages and messages[0].get("role") == "user" else 0
    previous_note = (messages[note_index].get("content") or "") \
        if note_index < len(messages) \
        and messages[note_index].get("_direct_compaction_note") else ""
    folded = ([previous_note.split("\n", 1)[1]]
              if previous_note.startswith("## 更早的工具轮次摘要\n") else [])
    while len(rounds) > keep_rounds and _direct_context_chars(messages) > max_chars:
        start, end = rounds.pop(0)
        assistant, result_msg = messages[start], messages[end]
        calls = assistant.get("tool_calls") or []
        tools = ", ".join(str(call.get("name") or "tool")[:80] for call in calls[:8])
        detail = str(assistant.get("content") or "").strip().replace("\n", " ")[:240]
        refs = []
        for call_id, output in (result_msg.get("tool_results") or [])[:5]:
            value = str(output or "")
            existing_ref = _existing_tool_output_ref(value, run_id, call_id)
            if existing_ref:
                refs.append(existing_ref)
            elif run_id:
                try:
                    from . import tool_outputs
                    ref = tool_outputs.save(run_id, call_id, value)
                    if ref:
                        refs.append(ref)
                except Exception:
                    pass
        piece = "- 工具 %s；模型说明：%s%s" % (
            tools or "unknown", detail or "（无）",
            ("；全文 ref=" + ",".join(refs)) if refs else "")
        folded.append(piece[:500])
        del messages[start:end + 1]
        # Tool payloads are untrusted external data: summarize call intent and refs,
        # never copy output excerpts outside their prompt-guard fences.
        note = "## 更早的工具轮次摘要\n" + "\n".join(folded)
        note_limit = min(8000, max(1400, max_chars // 4))
        if len(note) > note_limit:
            note = note[-note_limit:]
        insert_at = 1 if messages and messages[0].get("role") == "user" else 0
        if insert_at < len(messages) and messages[insert_at].get("_direct_compaction_note"):
            messages[insert_at]["content"] = note
        else:
            messages.insert(insert_at, {"role": "user", "content": note,
                                        "_direct_compaction_note": True})
        # Earlier deletions shift indices; rebuild complete pairs.
        rounds = [(i, i + 1) for i, msg in enumerate(messages[:-1])
                  if msg.get("role") == "assistant" and msg.get("tool_calls")
                  and messages[i + 1].get("role") == "tool_results"]


def _build_request(proto, base, use_key, model, system, msgs, with_tools, stream=False,
                   max_tokens=BASE_MAX_TOKENS, extra_specs=None, disabled_tools=None,
                   sandbox=None, workdir=None):
    """按协议构造 (url, headers, body)。msgs 为内部统一形状。

    stream=True 时按协议打开流式：google 换 :streamGenerateContent?alt=sse，
    openai/anthropic 置 stream 字段（openai 另要 include_usage，否则收尾
    chunk 不带 usage，这一步的 token 记账会凭空少一截）。"""
    base = (base or "").rstrip("/")
    if proto == "google":
        if base.endswith("/v1beta"):
            prefix = base + "/models/%s"
        else:
            prefix = base + "/v1beta/models/%s"
        url = (prefix + (":streamGenerateContent?alt=sse" if stream
                         else ":generateContent")) % model
        headers = {"x-goog-api-key": use_key}
        contents = []
        for m in msgs:
            if m["role"] not in ("user", "assistant"):
                continue
            parts = [{"text": m.get("content") or ""}]
            parts += [{"inline_data": {"mime_type": mime, "data": b64}}
                      for mime, b64 in (m.get("images") or [])]
            contents.append({"role": ("user" if m["role"] == "user" else "model"),
                             "parts": parts})
        body = {"contents": contents,
                "systemInstruction": {"parts": [{"text": system}]},
                "generationConfig": {"maxOutputTokens": max_tokens}}
        return url, headers, body
    path = "/messages" if proto == "anthropic" else "/chat/completions"
    url = (base + path) if base.endswith("/v1") else (base + "/v1" + path)
    if proto == "anthropic":
        headers = {"x-api-key": use_key, "anthropic-version": "2023-06-01"}
        body = {"model": model, "max_tokens": max_tokens, "system": system,
                "messages": [_m_anthropic(m) for m in msgs]}
        if with_tools:
            body["tools"] = _anthropic_tools(extra_specs, disabled_tools,
                                              sandbox=sandbox, workdir=workdir)
        if stream:
            body["stream"] = True
        return url, headers, body
    headers = {"Authorization": "Bearer " + use_key}
    # tool_results 消息展开成多条 role=tool（_m_openai 对它返回列表，不能嵌套）
    msgs_wire = []
    for m in msgs:
        w = _m_openai(m)
        if isinstance(w, list):
            msgs_wire.extend(w)
        else:
            msgs_wire.append(w)
    body = {"model": model, "max_tokens": max_tokens,
            "messages": [{"role": "system", "content": system}] + msgs_wire}
    if with_tools:
        body["tools"] = _openai_tools(extra_specs, disabled_tools,
                                      sandbox=sandbox, workdir=workdir)
    if stream:
        body["stream"] = True
        body["stream_options"] = {"include_usage": True}
    return url, headers, body


def _parse_reply(proto, data):
    """从响应解析 → (文本, [tool_call], usage)。
    tool_call = {id, name, args}；usage = {input, output, cached, total}。"""
    usage = {"input": 0, "output": 0, "cached": 0, "total": 0}
    if proto == "google":
        cand = (data.get("candidates") or [{}])[0]
        parts = (cand.get("content") or {}).get("parts") or []
        text = "\n".join(p.get("text", "") for p in parts if isinstance(p, dict))
        um = data.get("usageMetadata") or {}
        usage["input"] = int(um.get("promptTokenCount") or 0)
        usage["output"] = int(um.get("candidatesTokenCount") or 0)
        usage["total"] = int(um.get("totalTokenCount") or 0)
        return text.strip(), [], usage
    if proto == "anthropic":
        blocks = data.get("content") or []
        text = "\n".join(b.get("text", "") for b in blocks
                         if isinstance(b, dict) and b.get("type") == "text")
        calls = [{"id": b.get("id") or "", "name": b.get("name") or "",
                  "args": b.get("input") or {}}
                 for b in blocks if isinstance(b, dict) and b.get("type") == "tool_use"]
        u = data.get("usage") or {}
        usage["input"] = int(u.get("input_tokens") or 0)
        usage["output"] = int(u.get("output_tokens") or 0)
        usage["cached"] = (int(u.get("cache_read_input_tokens") or 0)
                           + int(u.get("cache_creation_input_tokens") or 0))
        usage["total"] = usage["input"] + usage["output"] + usage["cached"]
        return text.strip(), calls, usage
    choice = (data.get("choices") or [{}])[0]
    msg = choice.get("message") or {}
    calls = []
    for tc in (msg.get("tool_calls") or []):
        fn = tc.get("function") or {}
        try:
            args = json.loads(fn.get("arguments") or "{}")
        except Exception:
            args = {}
        if not isinstance(args, dict):
            args = {}
        calls.append({"id": tc.get("id") or ("call_%d" % (len(calls) + 1)),
                      "name": fn.get("name") or "", "args": args})
    u = data.get("usage") or {}
    usage["input"] = int(u.get("prompt_tokens") or 0)
    usage["output"] = int(u.get("completion_tokens") or 0)
    usage["total"] = int(u.get("total_tokens") or 0) or (usage["input"] + usage["output"])
    return (msg.get("content") or "").strip(), calls, usage


# ---------------------------------------------------------------- 主循环

def _norm_usage(usage):
    """三协议 usage → {input, output, cached, total}（缺 total 用三项相加补齐）。"""
    out = {"input": 0, "output": 0, "cached": 0, "total": 0}
    for k in out:
        try:
            out[k] = int((usage or {}).get(k) or 0)
        except Exception:
            out[k] = 0
    if not out["total"]:
        out["total"] = out["input"] + out["output"] + out["cached"]
    return out


def run(bi, prompt, workdir, timeout=180, cancel_event=None, log=None, images=None,
        on_reason=None, on_stream=None, on_activity=None, stream=True, deadline=None,
        task_creator=None, schedule_gateway=None, sandbox=None, checkpoint_run_id=""):
    """跑一次内置智能体（内部自带工具循环直到给出最终回答）。

    bi: resolve() 的返回；prompt: 本轮完整输入（目标/续轮块由 pipeline 拼）；
    images: 用户随本轮消息提供的图片绝对路径列表（任务附件/对话追话）；
    log: 追加一行日志的回调（步骤日志）。返回 runner 风格统一结果：
    {ok, text, usage, error, model, provider_name, provider_id, iterations,
     cost_usd, reasoning}。

    思考过程可视化（2026-09-22 用户诉求「把思考过程打印出来」）：stream=True 走
    SSE，模型的思维链（reasoning_content / thinking 块）边收边经 on_reason 回调，
    正文增量经 on_stream（累计值），工具活动经 on_activity——调用方据此在对话
    时间线上实时打印，不再让用户干看三点打字动画。网关忽略 stream（零事件）时
    自动回落非流式，思考字段仍从响应体兜底抽取（on_reason 补发一次）。"""
    prov = bi["prov"]
    model = bi["model"]
    from . import policy
    sandbox = policy.normalize_sandbox(sandbox, workdir)
    disabled_tools = sandbox.get("disabled_tools") or []
    allow_private = bool(prov.get("allow_private"))
    system = _SYSTEM_PROMPT + "\n\n## 工作目录\n%s" % os.path.abspath(workdir or ".")
    imgs = _prep_images(images, log) if images else []
    if imgs and not modelhub._model_image_in(prov, model):
        # 能力闸门：模型未声明图片输入就不塞图（网关多半会拒），改为显式告知
        if log:
            log("[图片] 模型 %s 未开启图片输入，%d 张图不随消息发送" % (model, len(imgs)))
        prompt = ("（用户提供了 %d 张图片，但当前模型未开启图片输入；请在回答开头提示用户"
                  "到绑定页为该模型开启「图」开关，或改绑多模态模型。本条回答请基于"
                  "文字部分完成。）\n\n%s" % (len(imgs), prompt))
        imgs = []
    msgs = [{"role": "user", "content": prompt, "images": imgs}]
    candidates = list(modelhub._protocol_candidates(prov))
    tools_ok = all(proto != "google" for proto, _ in candidates)   # google wire 无工具协议
    # 工具动作验证提示（借鉴 oc-ferry「可用·仅对话」分档）：判定是提示不是闸门，
    # 不改行为——探测偶有误判，拦工具会误伤能干活的模型；只把风险亮给用户。
    if log:
        try:
            _tv = modelhub.tool_verdict(prov.get("id") or "", model=model)
        except Exception:
            _tv = ""
        if _tv == "chat_only":
            log("[动作验证] 模型 %s 上次工具动作验证为「仅对话」，本轮工具调用可能"
                "不被执行；可在设置页该供应商卡片重新验证" % model)
    extra_specs = [s for s, on in ((CREATE_TASK_SPEC, bool(task_creator)),
                                   (SCHEDULE_TASK_SPEC, bool(schedule_gateway))) if on] or None
    total_usage = {"input": 0, "cached": 0, "output": 0, "total": 0}
    mcp_called = []
    mcp_discovered = 0
    try:
        mcp_specs = _mcp_specs(sandbox=sandbox, workdir=workdir)
        mcp_discovered = len(mcp_specs)
        sandbox["known_mcp_tools"] = [str(item.get("full_name") or "")
                                      for item in mcp_specs if item.get("full_name")]
    except Exception:
        mcp_discovered = 0
        sandbox["known_mcp_tools"] = []
    sandbox["approval_task"] = str(sandbox.get("approval_task") or checkpoint_run_id or "")
    tool_output_run_id = checkpoint_run_id or secrets.token_hex(16)
    text = ""
    iters = 0
    last_err = ""
    ok = False
    reasons = []            # 各迭代的思考正文（按迭代顺序拼接，供步骤记录展示）
    max_tokens = BASE_MAX_TOKENS   # 思考占满预算时提额重试（跨迭代保持，见空正文分支）
    escalated = False       # 本轮 run 是否已提额（只提一次，防无限翻倍）
    empty_streak = 0        # 连续零正文轮数（拿到正文/工具即清零）
    last_action_was_tools = False
    repeat_tool_signature = ""
    repeat_tool_streak = 0
    repeat_stop = False
    finalize_attempted = False
    perf = {"ttft": [], "tps": []}   # 各次流式调用的首字延迟(ms)与吞吐(tok/s)

    def _perf():
        """本次 run 的体验指标：跨迭代取均值；无流式样本时返回空 dict。"""
        if not perf["ttft"] and not perf["tps"]:
            return {}
        return {"first_token_ms": (round(sum(perf["ttft"]) / len(perf["ttft"]), 1)
                                   if perf["ttft"] else 0.0),
                "tokens_per_sec": (round(sum(perf["tps"]) / len(perf["tps"]), 2)
                                   if perf["tps"] else 0.0)}

    def _deadline_fail():
        return {"ok": False, "text": "", "usage": dict(total_usage),
                "error": "任务总时限已到", "model": model,
                "provider_name": bi["provider_name"],
                "provider_id": bi["provider_id"], "iterations": iters,
                "cost_usd": 0.0, "reasoning": "\n\n".join(r for r in reasons if r)[:THINK_MAX_CHARS],
                "raw": {"timed_out": True, "deadline_exceeded": True}}

    def _fire(cb, *cb_args):
        if cb is None:
            return
        try:
            cb(*cb_args)
        except Exception:
            pass        # 可视化是增强：回调炸了不能拖垮生成

    def _take_reason(reason):
        """收一段思考：攒进本轮正文并实时回调（限长，防超长思维链撑爆 run.json）。"""
        if not reason:
            return
        if not reasons:
            reasons.append("")
        if len(reasons[-1]) < THINK_MAX_CHARS:
            reasons[-1] += reason
        _fire(on_reason, reason)

    def _fail(err):
        return {"ok": False, "text": "", "usage": dict(total_usage), "error": err,
                "model": model, "provider_name": bi["provider_name"],
                "provider_id": bi["provider_id"], "iterations": iters, "cost_usd": 0.0,
                # Keep capability evidence on failed/early-return calls too;
                # otherwise a run that discovered MCP but hit a provider error
                # is indistinguishable from one that never had MCP available.
                "raw": dict(_perf(), mcp_calls=list(mcp_called),
                             mcp_discovered=mcp_discovered),
                "reasoning": "\n\n".join(r for r in reasons if r)[:THINK_MAX_CHARS]}

    def _cancel_fail():
        """取消终态带结构化标记：下游惩罚账（KEY 冷却/供应商健康/评测作废）靠标记
        认出取消，不靠「已取消」字样匹配。"""
        out = _fail("已取消")
        out["cancelled"] = True
        return out

    # 工具上限后最多再给一次“只输出总结”的机会。这样最后一轮已经
    # 成功写文件/抓数据但尚未总结时，不会把真实成果误报成失败。
    for it in range(1, MAX_TOOL_ITERS + 2):
        force_finalize = it > MAX_TOOL_ITERS or repeat_stop
        if force_finalize and (finalize_attempted or not last_action_was_tools):
            break
        if force_finalize and not finalize_attempted:
            finalize_attempted = True
            msgs.append({"role": "user", "content":
                         "请停止调用工具，只根据已经完成的操作直接给出最终答复。"
                         "列出已完成内容、产出文件和仍未完成的事项；不要继续探索。"})
            if log:
                log("[收尾] 工具循环已到边界，发起一次无工具最终总结")
            _fire(on_activity, "工具循环到边界，正在生成最终总结")
        if deadline is not None and time.monotonic() >= deadline:
            return _deadline_fail()
        if cancel_event is not None and cancel_event.is_set():
            return _cancel_fail()
        reasons.append("")   # 每轮迭代各占一段思考（跨轮拼接时分段，join 时滤空段）
        done = False
        reBump = False         # 本轮要提额重试（同一迭代重跑，不烧迭代数）
        stopit = False         # 本轮判死（连续零正文），直接终局不再空转
        for _bump in range(2):                      # 第 2 圈 = 提额后的同轮重试
            for proto, pbase in candidates:
                keys = modelhub._chain_keys(prov) or [{"key": prov.get("api_key") or "", "id": ""}]
                for kk in keys:
                    got = False        # 本 KEY 拿到了响应（区别于 HTTP 失败换下家）
                    stripped = False   # thinking 对象被网关 400 拒后已摘除重发
                    while True:
                        if deadline is not None and time.monotonic() >= deadline:
                            return _deadline_fail()
                        req_timeout = min(float(timeout), max(0.1, deadline - time.monotonic())) \
                            if deadline is not None else timeout
                        allow_tools = tools_ok and not force_finalize
                        url, headers, body = _build_request(
                            proto, pbase, kk["key"], model, system, msgs,
                            allow_tools, max_tokens=max_tokens,
                            extra_specs=extra_specs,
                            disabled_tools=disabled_tools, sandbox=sandbox,
                            workdir=workdir)
                        sbody = None
                        if stream:
                            # 流式体单独构造（+stream / include_usage）；非流式体留给回落重发
                            _, _, sbody = _build_request(
                                proto, pbase, kk["key"], model, system, msgs,
                                allow_tools, stream=True, max_tokens=max_tokens,
                                extra_specs=extra_specs,
                                disabled_tools=disabled_tools, sandbox=sandbox,
                                workdir=workdir)
                        effort = str(bi.get("reasoning_effort") or "").strip().lower()
                        # reasoning_effort 是 OpenAI wire 字段；Anthropic thinking 使用
                        # 另一套对象结构，向兼容网关硬塞该字段会直接得到 400。
                        if effort in ("low", "medium", "high") and proto == "openai":
                            body["reasoning_effort"] = effort
                            if sbody is not None:
                                sbody["reasoning_effort"] = effort
                        if effort in ("low", "medium") and proto == "anthropic" \
                                and not stripped:
                            # 「思考程度」此前只对 openai 面生效；GLM 这类推理模型
                            # 在 Anthropic 兼容面上默认全功率思考，能把 max_tokens
                            # 整个烧光（2026-09-22 glm-5.3-flash 47 分钟零正文案）。
                            # low/medium 显式给思考预算，high 留默认（模型自决=放开想）。
                            body["thinking"] = {
                                "type": "enabled",
                                "budget_tokens": 1024 if effort == "low" else 4096}
                            if sbody is not None:
                                sbody["thinking"] = dict(body["thinking"])
                        text, calls, usage = "", [], {}
                        streamed = False
                        if sbody is not None:
                            part = _call_with_optional_deadline(
                                _post_sse_stream,
                                (url, headers, sbody, allow_private, req_timeout, proto),
                                {"on_reason": _take_reason, "on_text": on_stream,
                                 "cancel_event": cancel_event}, deadline)
                            if part.get("deadline_exceeded"):
                                return _deadline_fail()
                            if part["error"] == "已取消":
                                return _cancel_fail()
                            if not part["error"] and part["events"]:
                                # 网关确实按流回了：正文/工具取增量累加结果
                                text, calls = part["text"], part["calls"]
                                usage = part["usage"]
                                streamed = True
                                if part.get("first_token_ms"):
                                    perf["ttft"].append(float(part["first_token_ms"]))
                                if part.get("tokens_per_sec"):
                                    perf["tps"].append(float(part["tokens_per_sec"]))
                                if not reasons or not reasons[-1]:
                                    # 调用方没挂实时回调（无可视化诉求）时思考只在
                                    # 累加器里：这里兜底收进结果，落库口径一致
                                    _take_reason(part["reasoning"])
                            else:
                                # 零事件（网关忽略 stream）或流式这一步失败（不认 stream 参数、
                                # 断流等）：都退回同一条 wire 的非流式重发——比直接判死这家
                                # 供应商稳妥；非流式也失败才走下面的记账/换链。
                                last_err = part["error"]
                        if not streamed:
                            status, data, err = _call_with_optional_deadline(
                                _post_interruptible,
                                (url, headers, body, allow_private, req_timeout, cancel_event),
                                {}, deadline)
                            if err == "任务总时限已到":
                                return _deadline_fail()
                            if cancel_event is not None and cancel_event.is_set():
                                # 取消先于一切记账：健康 KEY 不能因被放弃的请求背上冷却
                                return _cancel_fail()
                            if status == 0 or not (200 <= status < 300):
                                msg = ""
                                if isinstance(data, dict):
                                    e = data.get("error")
                                    msg = e.get("message", "") if isinstance(e, dict) else str(e)
                                if status == 400 and isinstance(body.get("thinking"), dict) \
                                        and "thinking" in str(msg).lower() and not stripped:
                                    # 网关不认 thinking 对象：摘掉同 KEY 立即重发，
                                    # 别让「思考程度」偏好把健康 KEY 送进冷却
                                    body.pop("thinking", None)
                                    if sbody is not None:
                                        sbody.pop("thinking", None)
                                    stripped = True
                                    continue
                                last_err = err or ("HTTP %s %s" % (status, str(msg)[:200]))
                                try:
                                    modelhub.note_key_error(bi["provider_id"], kk.get("id") or "", last_err)
                                except Exception:
                                    pass
                                break            # 换下一个 KEY
                            text, calls, usage = _parse_reply(proto, data)
                            # 非流式响应里的思考字段：补发一次，可视化与流式路径同形
                            _take_reason(_reason_from_message(proto, data))
                        try:
                            modelhub.note_key_ok(bi["provider_id"], kk.get("id") or "")
                        except Exception:
                            pass
                        usage = _norm_usage(usage)
                        for k in total_usage:
                            total_usage[k] += int(usage.get(k) or 0)
                        iters = it
                        got = True
                        break
                    if not got:
                        continue                 # 本 KEY 失败，换下一个
                    if calls and force_finalize:
                        # 收尾请求明确禁用工具；模型若仍返回 tool call，不能再
                        # 开启新一轮，否则会绕过止损边界。
                        last_err = "收尾时模型仍请求工具"
                        last_action_was_tools = False
                        done = True
                        break
                    if calls:
                        for _call in calls:
                            _name = str(_call.get("name") or "")
                            if _name.startswith("mcp__") and _name not in mcp_called:
                                mcp_called.append(_name)
                        sig = json.dumps(
                            [(c.get("name"), c.get("args") or {}) for c in calls],
                            ensure_ascii=False, sort_keys=True, separators=(",", ":"))
                        if sig == repeat_tool_signature:
                            repeat_tool_streak += 1
                        else:
                            repeat_tool_signature = sig
                            repeat_tool_streak = 1
                        if repeat_tool_streak >= 3:
                            repeat_stop = True
                            if log:
                                log("[迭代 %d] 检测到同一工具调用连续重复，转入收尾" % it)
                            _fire(on_activity, "检测到重复工具调用，正在收尾")
                        if log:
                            log("[迭代 %d] %s 请求工具: %s" % (
                                it, model, ", ".join(c["name"] for c in calls)))
                        _fire(on_activity, "请求工具: " + "、".join(c["name"] for c in calls))
                        results = []
                        for c in calls:
                            out = _exec_tool(workdir, c["name"], c["args"], cancel_event,
                                             deadline=deadline, task_creator=task_creator,
                                             disabled_tools=disabled_tools, sandbox=sandbox,
                                             checkpoint_run_id=checkpoint_run_id,
                                             tool_output_run_id=tool_output_run_id,
                                             schedule_gateway=schedule_gateway)
                            if deadline is not None and time.monotonic() >= deadline:
                                return _deadline_fail()
                            if cancel_event is not None and cancel_event.is_set():
                                return _cancel_fail()
                            # 运行期注入警示层：外部内容（网页/命令输出/文件）一律
                            # 包围栏进上下文；检测命中升级强围栏（prompt_guard）。
                            try:
                                from . import prompt_guard
                                out = prompt_guard.wrap_external(out, c["name"])
                            except Exception:
                                pass
                            if log:
                                brief = out if len(out) <= 120 else out[:120] + "…"
                                log("[工具] %s → %s" % (c["name"], brief.replace("\n", " ⏎ ")))
                            _fire(on_activity, "%s %s" % (
                                c["name"], str(c["args"].get("path")
                                               or c["args"].get("command") or "")[:80]))
                            results.append((c["id"], out))
                        msgs.append({"role": "assistant", "content": text, "tool_calls": calls})
                        msgs.append({"role": "tool_results", "tool_results": results})
                        _bound_direct_context(msgs, run_id=tool_output_run_id)
                        empty_streak = 0
                        last_action_was_tools = True
                        done = True
                        break
                    if (text or "").strip():
                        if log:
                            log("[迭代 %d] 最终回答（%d 字）" % (it, len(text)))
                        ok = True
                        empty_streak = 0
                        last_action_was_tools = False
                        done = True
                        break
                    # —— 零正文零工具：分辨「思考占满输出预算」还是「模型真没说话」。
                    # 推理模型的思考计入 max_tokens：想满了流会正常收，正文却是空的。
                    last_action_was_tools = False
                    rlen = len(reasons[-1] or "")
                    out_tok = int(usage.get("output") or 0)
                    exhausted = (rlen >= THINK_EXHAUST_MIN_CHARS
                                 or out_tok >= int(max_tokens * 0.9))
                    empty_streak += 1
                    if exhausted and not escalated:
                        # 同轮提额重试一次：8K 想不完的 16K 大多就想完了
                        escalated = True
                        old = max_tokens
                        max_tokens = min(max_tokens * 2, MAX_TOKENS_CAP)
                        last_err = ("模型思考占满输出预算（max_tokens=%d，思考 %d 字零正文）"
                                    % (old, rlen))
                        if log:
                            log("[第 %d 轮] 思考 %d 字仍无正文/工具 → max_tokens %d→%d 同轮重试"
                                % (it, rlen, old, max_tokens))
                        _fire(on_activity, "思考占满输出预算，提高输出预算重试")
                        reBump = True
                        break
                    if empty_streak >= EMPTY_TEXT_MAX_STREAK:
                        last_err = ("模型思考连续占满输出预算（max_tokens 已提到 %d 仍无正文）"
                                    % max_tokens) if exhausted else \
                            ("模型连续 %d 轮零正文零工具" % empty_streak)
                        done = True
                        stopit = True
                        break
                    last_err = "模型未返回文本"
                    if log:
                        log("[第 %d 轮] 模型零正文零工具（思考 %d 字），重试" % (it, rlen))
                    _fire(on_activity, "模型零正文，重试")
                    done = True
                    break
                if done or reBump:
                    break
            if reBump:
                continue        # 同一迭代用新预算重来
            break               # 非提额路径一锤定音
        if not done:
            break   # 所有 wire/KEY 都失败
        if ok or stopit:
            break
    if not ok and not text and finalize_attempted:
        # 工具已经执行过且收尾调用也未能给正文：把真实执行结果作为“部分完成”
        # 返回。pipeline 会按成功步骤落盘，用户可以继续追问而不是被迫重跑。
        partial = _fail(last_err or "已完成工具操作，但未生成最终总结")
        partial["ok"] = True
        partial["partial"] = True
        partial["text"] = ("部分完成：已执行部分操作，但未能生成最终总结。"
                            "请检查工作目录中的产出文件，或继续追问以完成剩余事项。")
        partial["error"] = last_err or "已完成工具操作，但未生成最终总结"
        partial.setdefault("raw", {})["partial"] = True
        partial.setdefault("raw", {})["mcp_calls"] = list(mcp_called)
        partial["raw"]["mcp_discovered"] = mcp_discovered
        return partial
    if not ok and not text:
        return _fail(last_err or "工具循环达上限仍无最终回答")
    return {"ok": True, "text": (text or "").strip(), "usage": dict(total_usage),
            "error": "", "model": model, "provider_name": bi["provider_name"],
            "provider_id": bi["provider_id"], "iterations": iters, "cost_usd": 0.0,
            "raw": dict(_perf(), mcp_calls=list(mcp_called), mcp_discovered=mcp_discovered),
            "reasoning": "\n\n".join(r for r in reasons if r)[:THINK_MAX_CHARS]}
