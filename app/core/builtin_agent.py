# -*- coding: utf-8 -*-
"""内置执行智能体：不经外部 CLI，直连供应商 API 完成直连（direct）任务。

为什么：direct 是快档位，典型诉求是问答/轻量改写。拉起 codex/claude CLI 要付
进程与框架开销，且 CLI 的事件流日志（启动命令、提示词回显、重连报错）会污染
对话视图。内置智能体直接打 chat API + 工具循环：
  - 模型选择：编排者配置（resolve_orchestrator）优先；未启用时扫首个可用供应商；
  - 工具：list_files / read_file / write_file，全部锁死在工作目录内；
  - 协议：anthropic / openai 走原生工具循环；google 无工具协议（纯文本直答）。
密钥冷却/多 KEY 展开复用 modelhub 既有记账（note_key_ok / note_key_error）。
"""
from __future__ import annotations

import base64
import io
import json
import mimetypes
import os
import threading
from pathlib import Path

from . import modelhub, runner

MAX_TOOL_ITERS = 16          # 单步工具循环上限（防模型打转）
READ_MAX_BYTES = 64 * 1024   # read_file 单次读取上限
LIST_MAX_ENTRIES = 200
MAX_IMAGES = 8                 # 单次最多随消息传几张图（请求体防爆）
IMAGE_MAX_EDGE = 1568          # 长边上限（视觉模型通行建议值，超出等比缩）
IMAGE_JPEG_BYTES = 3500 * 1024   # 缩放后仍超此大小 → 转 JPEG q85
IMAGE_MAX_BYTES = 5 * 1024 * 1024   # 单张最终字节硬上限（超出剔除并日志）

_SYSTEM_PROMPT = """你是 CodeBee 的内置执行智能体，直接完成用户交代的任务。用户的目标、背景与工作目录内的附件就是全部输入。

## 工作方式
- 需要读文件、看目录、写文件时调用工具；所有路径都是工作目录内的相对路径。
- read_file 只能读文本文件；图片、压缩包等二进制文件读不了，如实告知用户即可，不要反复尝试。
- 用户消息中的图片附件会直接出现在对话里，可直接看图作答，无需用工具读取。
- 产出文件一律 UTF-8 编码。
- 回答用户的语言与用户一致（默认中文）。直接给结论和内容，不要输出任何机器标记或协议行。"""


def resolve():
    """选内置智能体可用的 (provider, model)：编排者配置优先，否则首个可用供应商。

    返回 {prov, model, provider_id, provider_name} 或 None（无可用供应商 → 调用方
    回退 CLI 路径）。"""
    orch = None
    try:
        orch = modelhub.resolve_orchestrator()
    except Exception:
        orch = None
    if orch:
        prov, model = orch
        return {"prov": prov, "model": model,
                "provider_id": prov.get("id") or "",
                "provider_name": prov.get("name") or prov.get("id") or ""}
    for p in modelhub.providers():
        if not p.get("enabled", True) or not p.get("api_key"):
            continue
        m = (p.get("model") or "").strip()
        if not m:
            try:
                names = modelhub._enabled_models(p)
            except Exception:
                names = []
            m = names[0]["name"] if names else ""
        if m:
            return {"prov": p, "model": m, "provider_id": p.get("id") or "",
                    "provider_name": p.get("name") or p.get("id") or ""}
    return None


# ---------------------------------------------------------------- 工作目录工具
# 模型给的相对路径不可信：逐段拒绝 .. 与盘符，resolve() 归一后确认仍位于
# 工作目录之下（base not in p.parents 即越界，同 skills/market 的守卫惯用法）。

def _tool_list_files(workdir, args):
    rel = str(args.get("path") or "").replace("\\", "/").strip("/")
    if ".." in Path(rel).parts or any(":" in seg for seg in Path(rel).parts):
        return "（非法路径: %s）" % rel
    base = Path(workdir or ".").resolve()
    p = (base / rel).resolve() if rel else base
    if base not in p.parents and p != base:
        return "（路径越界: %s）" % rel
    if not p.is_dir():
        return "（目录不存在: %s）" % (rel or ".")
    out = []
    for root, dirs, files in os.walk(str(p)):
        dirs[:] = [d for d in dirs if d not in (".git", "__pycache__", "node_modules")]
        for f in files:
            fp = os.path.join(root, f)
            relp = os.path.relpath(fp, str(base)).replace(os.sep, "/")
            try:
                size = os.path.getsize(fp)
            except OSError:
                size = -1
            out.append("%s (%d B)" % (relp, size))
            if len(out) >= LIST_MAX_ENTRIES:
                return "\n".join(out) + "\n…（截断，共 200+ 项）"
    return "\n".join(out) or "（空目录）"


_BIN_MAGIC = (
    (b"\x89PNG\r\n\x1a\n", "PNG 图片"),
    (b"\xff\xd8\xff", "JPEG 图片"),
    (b"GIF8", "GIF 图片"),
    (b"%PDF-", "PDF 文档"),
    (b"PK\x03\x04", "ZIP 压缩包"),
    (b"\x1f\x8b", "GZIP 压缩包"),
    (b"7z\xbc\xaf\x27\x1c", "7z 压缩包"),
    (b"Rar!\x1a\x07", "RAR 压缩包"),
    (b"\x00\x00\x01\x00", "ICO 图标"),
    (b"OggS", "OGG 音频"),
    (b"ID3", "MP3 音频"),
)


def _bin_format(head):
    """常见二进制魔数 → 人类可读格式名（写进给模型的提示，让它立刻止损）。"""
    if len(head) >= 12 and head[:4] == b"RIFF":
        return {"WEBP": "WEBP 图片", "WAVE": "WAV 音频",
                "AVI ": "AVI 视频"}.get(head[8:12].decode("ascii", "replace"),
                                        "RIFF 媒体")
    if len(head) >= 8 and head[4:8] == b"ftyp":
        return "MP4/MOV 视频"
    for magic, name in _BIN_MAGIC:
        if head.startswith(magic):
            return name
    return "二进制文件"


def _looks_binary(head):
    """文本判定从宽：NUL 字节即判二进制（git 同款启发式），控制字符占比兜底
    （部分二进制头 8KB 内无 NUL）。"""
    if not head:
        return False
    if b"\x00" in head:
        return True
    sample = head[:8192]
    ctrl = sum(1 for b in sample if b < 32 and b not in (9, 10, 12, 13))
    return ctrl * 20 > len(sample)   # >5% 视为二进制


def _trim_partial_utf8(chunk):
    """头部截断可能把多字节字符切成两半：剥掉尾部不完整序列，否则 UTF-8
    strict 失败会误落 GBK 解出错字（runner.tail_decoded 的头截断对偶）。"""
    i = len(chunk) - 1
    while i >= 0 and i >= len(chunk) - 3 and (chunk[i] & 0xC0) == 0x80:
        i -= 1
    if 0 <= i < len(chunk):
        b = chunk[i]
        need = 4 if (b & 0xF8) == 0xF0 else 3 if (b & 0xF0) == 0xE0 \
            else 2 if (b & 0xC0) == 0xC0 else 1
        if len(chunk) - i < need:
            return chunk[:i]
    return chunk


def _guess_image_mime(path, raw=b""):
    m = mimetypes.guess_type(str(path))[0]
    if m:
        return m
    if raw.startswith(b"\x89PNG"):
        return "image/png"
    if raw.startswith(b"\xff\xd8"):
        return "image/jpeg"
    if raw.startswith(b"GIF8"):
        return "image/gif"
    if raw.startswith(b"RIFF"):
        return "image/webp"
    return "image/png"


def _prep_images(paths, log=None):
    """图片路径 → [(mime, b64)]。Pillow 可用时：长边超 1568px 等比缩（万级像素
    原图既烧 token 又常被网关拒），仍超 3.5MB 转 JPEG q85；Pillow 缺失或解码
    失败时原图直传，单张超 5MB 剔除并日志。最多 MAX_IMAGES 张。"""
    try:
        from PIL import Image
    except Exception:
        Image = None
    out = []
    for p in (paths or [])[:MAX_IMAGES]:
        try:
            raw = Path(p).read_bytes()
        except OSError:
            if log:
                log("[图片] 跳过（读取失败）: %s" % os.path.basename(str(p)))
            continue
        mime = _guess_image_mime(p, raw)
        data = raw
        if Image is not None:
            try:
                im = Image.open(io.BytesIO(raw))
                im.load()
                w, h = im.size
                edge = max(w, h, 1)
                if edge > IMAGE_MAX_EDGE:
                    r = IMAGE_MAX_EDGE / float(edge)
                    im = im.resize((max(1, round(w * r)), max(1, round(h * r))),
                                   Image.LANCZOS)
                fmt = {"image/png": "PNG", "image/jpeg": "JPEG",
                       "image/gif": "GIF", "image/webp": "WEBP"}.get(mime, "PNG")
                buf = io.BytesIO()
                im.save(buf, format=fmt)   # GIF 多帧仅存首帧，可接受
                data = buf.getvalue()
            except Exception:
                data = raw                 # 解码/编码失败退回原始字节
        if (Image is not None and mime != "image/jpeg"
                and len(data) > IMAGE_JPEG_BYTES):
            try:                           # 仍过大：转 JPEG q85（RGB 拍平 alpha）
                im = Image.open(io.BytesIO(data)).convert("RGB")
                buf = io.BytesIO()
                im.save(buf, format="JPEG", quality=85)
                data = buf.getvalue()
                mime = "image/jpeg"
            except Exception:
                pass
        if len(data) > IMAGE_MAX_BYTES:
            if log:
                log("[图片] 剔除（过大 %d MB）: %s"
                    % (len(data) // (1024 * 1024), os.path.basename(str(p))))
            continue
        out.append((mime, base64.b64encode(data).decode("ascii")))
    return out


def _tool_read_file(workdir, args):
    rel = str(args.get("path") or "").replace("\\", "/").strip("/")
    if not rel or ".." in Path(rel).parts or any(":" in seg for seg in Path(rel).parts):
        return "（非法路径: %s）" % rel
    base = Path(workdir or ".").resolve()
    p = (base / rel).resolve()
    if base not in p.parents:
        return "（路径越界: %s）" % rel
    if not p.is_file():
        return "（文件不存在: %s）" % rel
    data = p.read_bytes()[:READ_MAX_BYTES + 1]
    truncated = len(data) > READ_MAX_BYTES
    head = data[:READ_MAX_BYTES]
    if not head:
        return "（空文件）"
    if _looks_binary(head):
        try:
            size = p.stat().st_size
        except OSError:
            size = len(head)
        fmt = _bin_format(head)
        if "图片" in fmt:
            return ("（图片文件: %s — %s，%d B，无法按文本读取。用户以附件发来的图片会"
                    "直接出现在对话中，无需用工具读取；若需要目录里这张图的内容，"
                    "请提示用户把它作为附件发送。）" % (rel, fmt, size))
        return ("（二进制文件，无法按文本读取: %s — %s，%d B。read_file 只支持文本文件；"
                "请如实告知用户该文件内容无法以文本方式查看，不要反复重读。）"
                % (rel, fmt, size))
    if truncated:
        head = _trim_partial_utf8(head)
    text = runner.decode_output(head)
    return ("…（超过 64KB 已截断）\n" if truncated else "") + text


def _tool_write_file(workdir, args):
    rel = str(args.get("path") or "").replace("\\", "/").strip("/")
    if not rel or ".." in Path(rel).parts or any(":" in seg for seg in Path(rel).parts):
        return "（非法路径: %s）" % rel
    content = str(args.get("content") if args.get("content") is not None else "")
    base = Path(workdir or ".").resolve()
    dest = (base / rel).resolve()
    if base not in dest.parents:
        return "（路径越界: %s）" % rel
    dest.parent.mkdir(parents=True, exist_ok=True)
    dest.write_text(content, encoding="utf-8")
    return "已写入 %s（%d 字符，UTF-8）" % (rel, len(content))


TOOLS_SPEC = [
    {"name": "list_files", "description": "列出工作目录（或其子目录）下的文件",
     "args": {"path": "子目录相对路径，留空=根目录"}},
    {"name": "read_file", "description": "读取工作目录内一个文本文件（UTF-8/GBK 自动识别，超 64KB 截断；图片等二进制文件无法读取）",
     "args": {"path": "文件相对路径"}},
    {"name": "write_file", "description": "把文本内容写入工作目录内一个文件（UTF-8，父目录自动创建）",
     "args": {"path": "文件相对路径", "content": "完整文本内容"}},
]

_TOOL_IMPL = {"list_files": _tool_list_files, "read_file": _tool_read_file,
              "write_file": _tool_write_file}


def _exec_tool(workdir, name, args):
    fn = _TOOL_IMPL.get(name or "")
    if fn is None:
        return "（未知工具: %s）" % name
    try:
        return fn(workdir, args or {})
    except Exception as e:
        return "工具执行失败: %s" % (e)


# ---------------------------------------------------------------- 协议适配

def _openai_tools():
    return [{"type": "function", "function": {
        "name": t["name"], "description": t["description"],
        "parameters": {"type": "object",
                       "properties": {k: {"type": "string", "description": v}
                                      for k, v in t["args"].items()},
                       "required": list(t["args"].keys())}}}
        for t in TOOLS_SPEC]


def _anthropic_tools():
    return [{"name": t["name"], "description": t["description"],
             "input_schema": {"type": "object",
                              "properties": {k: {"type": "string", "description": v}
                                             for k, v in t["args"].items()},
                              "required": list(t["args"].keys())}}
            for t in TOOLS_SPEC]


def _post_json(url, headers, body, allow_private, timeout):
    """传输层单点（测试在这里打桩）。"""
    return modelhub._post_json_http(url, headers, body, allow_private, timeout=timeout)


def _post_interruptible(url, headers, body, allow_private, timeout, cancel_event):
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
    if cancel_event is None:
        th.join(timeout + 10)
    else:
        while th.is_alive() and not cancel_event.wait(0.5):
            pass
    if cancel_event is not None and cancel_event.is_set():
        return 0, None, "已取消"
    return box.get("r") or (0, None, "无响应")


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


def _build_request(proto, base, use_key, model, system, msgs, with_tools):
    """按协议构造 (url, headers, body)。msgs 为内部统一形状。"""
    base = (base or "").rstrip("/")
    if proto == "google":
        if base.endswith("/v1beta"):
            url = base + "/models/%s:generateContent" % model
        else:
            url = base + "/v1beta/models/%s:generateContent" % model
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
                "generationConfig": {"maxOutputTokens": 8000}}
        return url, headers, body
    path = "/messages" if proto == "anthropic" else "/chat/completions"
    url = (base + path) if base.endswith("/v1") else (base + "/v1" + path)
    if proto == "anthropic":
        headers = {"x-api-key": use_key, "anthropic-version": "2023-06-01"}
        body = {"model": model, "max_tokens": 8000, "system": system,
                "messages": [_m_anthropic(m) for m in msgs]}
        if with_tools:
            body["tools"] = _anthropic_tools()
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
    body = {"model": model, "max_tokens": 8000,
            "messages": [{"role": "system", "content": system}] + msgs_wire}
    if with_tools:
        body["tools"] = _openai_tools()
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

def run(bi, prompt, workdir, timeout=180, cancel_event=None, log=None, images=None):
    """跑一次内置智能体（内部自带工具循环直到给出最终回答）。

    bi: resolve() 的返回；prompt: 本轮完整输入（目标/续轮块由 pipeline 拼）；
    images: 用户随本轮消息提供的图片绝对路径列表（任务附件/对话追话）；
    log: 追加一行日志的回调（步骤日志）。返回 runner 风格统一结果：
    {ok, text, usage, error, model, provider_name, provider_id, iterations, cost_usd}。
    """
    prov = bi["prov"]
    model = bi["model"]
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
    total_usage = {"input": 0, "cached": 0, "output": 0, "total": 0}
    text = ""
    iters = 0
    last_err = ""
    ok = False

    def _fail(err):
        return {"ok": False, "text": "", "usage": dict(total_usage), "error": err,
                "model": model, "provider_name": bi["provider_name"],
                "provider_id": bi["provider_id"], "iterations": iters, "cost_usd": 0.0}

    for it in range(1, MAX_TOOL_ITERS + 1):
        if cancel_event is not None and cancel_event.is_set():
            return _fail("已取消")
        done = False
        for proto, pbase in candidates:
            keys = modelhub._chain_keys(prov) or [{"key": prov.get("api_key") or "", "id": ""}]
            for kk in keys:
                url, headers, body = _build_request(proto, pbase, kk["key"], model,
                                                    system, msgs, tools_ok)
                status, data, err = _post_interruptible(url, headers, body,
                                                        allow_private, timeout,
                                                        cancel_event)
                if cancel_event is not None and cancel_event.is_set():
                    # 取消先于一切记账：健康 KEY 不能因被放弃的请求背上冷却
                    return _fail("已取消")
                if status == 0 or not (200 <= status < 300):
                    msg = ""
                    if isinstance(data, dict):
                        e = data.get("error")
                        msg = e.get("message", "") if isinstance(e, dict) else str(e)
                    last_err = err or ("HTTP %s %s" % (status, str(msg)[:200]))
                    try:
                        modelhub.note_key_error(bi["provider_id"], kk.get("id") or "", last_err)
                    except Exception:
                        pass
                    continue
                try:
                    modelhub.note_key_ok(bi["provider_id"], kk.get("id") or "")
                except Exception:
                    pass
                text, calls, usage = _parse_reply(proto, data)
                for k in total_usage:
                    total_usage[k] += int(usage.get(k) or 0)
                iters = it
                if calls:
                    if log:
                        log("[迭代 %d] %s 请求工具: %s" % (
                            it, model, ", ".join(c["name"] for c in calls)))
                    results = []
                    for c in calls:
                        out = _exec_tool(workdir, c["name"], c["args"])
                        if log:
                            brief = out if len(out) <= 120 else out[:120] + "…"
                            log("[工具] %s → %s" % (c["name"], brief.replace("\n", " ⏎ ")))
                        results.append((c["id"], out))
                    msgs.append({"role": "assistant", "content": text, "tool_calls": calls})
                    msgs.append({"role": "tool_results", "tool_results": results})
                    done = True
                    break
                if (text or "").strip():
                    if log:
                        log("[迭代 %d] 最终回答（%d 字）" % (it, len(text)))
                    ok = True
                    done = True
                    break
                last_err = "模型未返回文本"
                done = True
                break
            if done:
                break
        if not done:
            break   # 所有 wire/KEY 都失败
        if ok:
            break
    if not ok and not text:
        return _fail(last_err or "工具循环达上限仍无最终回答")
    return {"ok": True, "text": (text or "").strip(), "usage": dict(total_usage),
            "error": "", "model": model, "provider_name": bi["provider_name"],
            "provider_id": bi["provider_id"], "iterations": iters, "cost_usd": 0.0}
