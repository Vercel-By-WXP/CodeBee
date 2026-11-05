"""本地 wire 桥：把 Anthropic Messages 面翻译成 OpenAI Chat Completions 面。

为什么需要它（2026-11-05 实案）
--------------------------------
`claude-code` 只认 Anthropic 协议（`bindable_protocols("claude-code") == ("anthropic",)`），
而很多可用的模型网关（本机 Hy4 网关 `http://127.0.0.1:8788/v1`、各种 one-api 派生面）
**只开 OpenAI 面，`/v1/messages` 直接 404**。于是「有额度但绑不上」——
每步都退化成换将，最后整条链没一个能写正文的。

在此之前 CodeBee 只有「协议挑面」（`wire_caps` 探哪条面能用），没有「协议转换」。
这个模块补的就是那层：起一个 127.0.0.1 上的小服务，对外说 Anthropic 协议，
对内把请求改写成 OpenAI 协议打给上游，再把上游的流式增量重新编码成
Anthropic 的 SSE 事件序列。

边界（写清楚，免得被当成万能适配）
----------------------------------
- 只做 **Anthropic → OpenAI** 一个方向；反方向不做。
- 只做文本 + 工具调用；thinking/redacted_thinking 块丢弃（上游一般不回，
  claude-code 缺了也能跑）。
- 不落盘、不缓存、不记账：调用统计仍由 runner 从 CLI 日志解析，这里只搬运。

多租户：每份 (upstream_base, api_key, model) 注册一个 token，对外地址是
`http://127.0.0.1:<port>/<token>`——claude 会自己拼 `/v1/messages`，
所以不同绑定的请求天然隔离，不需要额外路由表。
"""
from __future__ import annotations

import json
import threading
import uuid

from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import urllib.error
import urllib.request

_LOCK = threading.Lock()
_ROUTES = {}          # token -> {"base": str, "key": str, "model": str}
_SERVER = None
_PORT = 0

DEFAULT_TIMEOUT = 900

_STOP_MAP = {
    "stop": "end_turn",
    "length": "max_tokens",
    "tool_calls": "tool_use",
    "function_call": "tool_use",
    "content_filter": "end_turn",
}


# ---------------------------------------------------------------- 注册与复用
def register(base_url, api_key, model=""):
    """登记一份上游，返回可交给 claude 的 base url（含 token）。"""
    base = (base_url or "").strip().rstrip("/")
    key = (api_key or "").strip()
    if not base:
        return ""
    token = _ensure_server()
    with _LOCK:
        for tok, cfg in _ROUTES.items():
            if cfg["base"] == base and cfg["key"] == key and cfg["model"] == (model or ""):
                return "http://127.0.0.1:%d/%s" % (_PORT, tok)
        tok = uuid.uuid4().hex[:16]
        _ROUTES[tok] = {"base": base, "key": key, "model": (model or "").strip()}
    return "http://127.0.0.1:%d/%s" % (_PORT, tok)


def _ensure_server():
    global _SERVER, _PORT
    if _SERVER is not None:
        return ""
    with _LOCK:
        if _SERVER is not None:
            return ""
        srv = ThreadingHTTPServer(("127.0.0.1", 0), _Handler)
        srv.daemon_threads = True
        _PORT = srv.server_address[1]
        _SERVER = srv
        t = threading.Thread(target=srv.serve_forever, name="wire-bridge", daemon=True)
        t.start()
    return ""


def port():
    return _PORT


# ---------------------------------------------------------------- 请求翻译
def _flatten(content):
    """Anthropic 的 content（str 或 block 列表）→ 纯文本 + 图片占位 + 工具结果。"""
    if isinstance(content, str):
        return content, []
    texts, tools = [], []
    for blk in content or []:
        if not isinstance(blk, dict):
            continue
        kind = blk.get("type")
        if kind == "text":
            texts.append(blk.get("text") or "")
        elif kind == "tool_result":
            body = blk.get("content")
            if isinstance(body, list):
                body = "\n".join(str(b.get("text") or "") for b in body
                                 if isinstance(b, dict))
            tools.append({"id": blk.get("tool_use_id") or "", "text": body or ""})
        elif kind == "image":
            texts.append("[image omitted]")
        elif kind == "thinking":
            continue
    return "\n".join(t for t in texts if t), tools


def to_openai(body, cfg):
    """Anthropic /v1/messages 请求体 → OpenAI /chat/completions 请求体。"""
    msgs = []
    sys_txt, _ = _flatten(body.get("system"))
    if sys_txt:
        msgs.append({"role": "system", "content": sys_txt})
    for m in body.get("messages") or []:
        role = m.get("role")
        content = m.get("content")
        if role == "assistant":
            text, _ = _flatten(content)
            calls = []
            if isinstance(content, list):
                for blk in content:
                    if isinstance(blk, dict) and blk.get("type") == "tool_use":
                        calls.append({
                            "id": blk.get("id") or "",
                            "type": "function",
                            "function": {
                                "name": blk.get("name") or "",
                                "arguments": json.dumps(blk.get("input") or {},
                                                        ensure_ascii=False),
                            },
                        })
            item = {"role": "assistant", "content": text or ("" if calls else "")}
            if calls:
                item["tool_calls"] = calls
            msgs.append(item)
        else:
            text, tools = _flatten(content)
            for t in tools:
                msgs.append({"role": "tool", "tool_call_id": t["id"],
                             "content": t["text"]})
            if text or not tools:
                msgs.append({"role": "user", "content": text})
    out = {
        "model": cfg.get("model") or body.get("model") or "",
        "messages": msgs,
        "max_tokens": int(body.get("max_tokens") or 4096),
    }
    if body.get("temperature") is not None:
        out["temperature"] = body["temperature"]
    if body.get("top_p") is not None:
        out["top_p"] = body["top_p"]
    if body.get("stop_sequences"):
        out["stop"] = body["stop_sequences"]
    tools = []
    for t in body.get("tools") or []:
        if not isinstance(t, dict) or not t.get("name"):
            continue
        tools.append({"type": "function",
                      "function": {"name": t["name"],
                                   "description": t.get("description") or "",
                                   "parameters": t.get("input_schema")
                                   or {"type": "object", "properties": {}}}})
    if tools:
        out["tools"] = tools
        choice = body.get("tool_choice") or {}
        kind = (choice or {}).get("type")
        if kind == "any":
            out["tool_choice"] = "required"
        elif kind == "tool" and choice.get("name"):
            out["tool_choice"] = {"type": "function",
                                  "function": {"name": choice["name"]}}
        elif kind == "none":
            out["tool_choice"] = "none"
        else:
            out["tool_choice"] = "auto"
    return out


# ---------------------------------------------------------------- 响应编码
def _msg_start(model, input_tokens=0):
    return {"type": "message_start",
            "message": {"id": "msg_" + uuid.uuid4().hex[:20], "type": "message",
                        "role": "assistant", "model": model, "content": [],
                        "stop_reason": None, "stop_sequence": None,
                        "usage": {"input_tokens": input_tokens,
                                  "output_tokens": 0}}}


def _sse(evt):
    return ("event: %s\ndata: %s\n\n" % (evt["type"],
                                         json.dumps(evt, ensure_ascii=False))).encode("utf-8")


def from_openai_full(data, model):
    """非流式：OpenAI 完整响应 → Anthropic 完整响应。"""
    choice = (data.get("choices") or [{}])[0]
    msg = choice.get("message") or {}
    content = []
    text = msg.get("content")
    if text:
        content.append({"type": "text", "text": text})
    for call in msg.get("tool_calls") or []:
        fn = call.get("function") or {}
        try:
            args = json.loads(fn.get("arguments") or "{}")
        except Exception:
            args = {}
        content.append({"type": "tool_use", "id": call.get("id") or "",
                        "name": fn.get("name") or "", "input": args})
    usage = data.get("usage") or {}
    return {"id": "msg_" + uuid.uuid4().hex[:20], "type": "message",
            "role": "assistant", "model": data.get("model") or model,
            "content": content,
            "stop_reason": _STOP_MAP.get(choice.get("finish_reason") or "stop", "end_turn"),
            "stop_sequence": None,
            "usage": {"input_tokens": usage.get("prompt_tokens") or 0,
                      "output_tokens": usage.get("completion_tokens") or 0}}


# ---------------------------------------------------------------- HTTP 处理
class _Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def log_message(self, *a):        # 关掉 stderr 噪声
        return

    def _cfg(self):
        token = (self.path or "").strip("/").split("/")[0]
        with _LOCK:
            return _ROUTES.get(token)

    def _send(self, code, payload, ctype="application/json"):
        raw = payload if isinstance(payload, bytes) else json.dumps(
            payload, ensure_ascii=False).encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(raw)))
        self.end_headers()
        try:
            self.wfile.write(raw)
        except Exception:
            pass

    def do_GET(self):
        if self.path.endswith("/health"):
            return self._send(200, {"ok": True, "routes": len(_ROUTES)})
        return self._send(404, {"error": "not found"})

    def do_POST(self):
        cfg = self._cfg()
        if not cfg:
            return self._send(404, {"error": {"message": "unknown bridge token",
                                              "type": "invalid_request_error"}})
        try:
            length = int(self.headers.get("Content-Length") or 0)
            body = json.loads(self.rfile.read(length).decode("utf-8") or "{}")
        except Exception as exc:
            return self._send(400, {"error": {"message": "bad json: %s" % exc,
                                              "type": "invalid_request_error"}})
        if self.path.endswith("/count_tokens"):
            txt = json.dumps(body.get("messages") or [], ensure_ascii=False)
            return self._send(200, {"input_tokens": max(1, len(txt) // 2)})
        stream = bool(body.get("stream"))
        req = to_openai(body, cfg)
        req["stream"] = True          # 上游统一走流式，非流式请求在本地聚合
        if stream:
            req["stream_options"] = {"include_usage": True}
        url = cfg["base"] + "/chat/completions"
        data = json.dumps(req, ensure_ascii=False).encode("utf-8")
        headers = {"Content-Type": "application/json",
                   "Authorization": "Bearer " + cfg["key"],
                   "Accept": "text/event-stream"}
        try:
            resp = urllib.request.urlopen(
                urllib.request.Request(url, data=data, headers=headers),
                timeout=DEFAULT_TIMEOUT)
        except urllib.error.HTTPError as exc:
            detail = ""
            try:
                detail = exc.read().decode("utf-8", "ignore")[:400]
            except Exception:
                pass
            return self._send(exc.code, {"type": "error", "error": {
                "type": "api_error", "message": "upstream %s: %s"
                % (exc.code, detail or exc.reason)}})
        except Exception as exc:
            return self._send(502, {"type": "error", "error": {
                "type": "api_error", "message": "upstream unreachable: %s" % exc}})

        model = req["model"]
        if stream:
            return self._pump(resp, model)
        return self._aggregate(resp, model)

    def _iterate(self, resp):
        buf = b""
        for chunk in resp:
            buf += chunk
            while b"\n" in buf:
                line, buf = buf.split(b"\n", 1)
                line = line.strip()
                if not line.startswith(b"data:"):
                    continue
                payload = line[5:].strip()
                if payload == b"[DONE]":
                    return
                try:
                    yield json.loads(payload.decode("utf-8"))
                except Exception:
                    continue

    def _pump(self, resp, model):
        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream; charset=utf-8")
        self.send_header("Cache-Control", "no-store")
        self.send_header("Connection", "keep-alive")
        self.end_headers()
        out = self.wfile.write
        out(_sse(_msg_start(model)))
        block_idx = -1
        block_kind = None          # "text" | "tool"
        tools = {}                 # 上游 tool_calls 的 index → 我方 block index
        finish = None
        usage = {}
        try:
            for chunk in self._iterate(resp):
                if chunk.get("usage"):
                    usage = chunk["usage"]
                for choice in chunk.get("choices") or []:
                    delta = choice.get("delta") or {}
                    if choice.get("finish_reason"):
                        finish = choice["finish_reason"]
                    text = delta.get("content")
                    if text:
                        if block_kind != "text":
                            if block_idx >= 0:
                                out(_sse({"type": "content_block_stop",
                                          "index": block_idx}))
                            block_idx += 1
                            block_kind = "text"
                            out(_sse({"type": "content_block_start", "index": block_idx,
                                      "content_block": {"type": "text", "text": ""}}))
                        out(_sse({"type": "content_block_delta", "index": block_idx,
                                  "delta": {"type": "text_delta", "text": text}}))
                    for call in delta.get("tool_calls") or []:
                        up_idx = call.get("index", 0)
                        fn = call.get("function") or {}
                        if up_idx not in tools:
                            if block_idx >= 0 and block_kind == "text":
                                out(_sse({"type": "content_block_stop",
                                          "index": block_idx}))
                            block_idx += 1
                            block_kind = "tool"
                            tools[up_idx] = block_idx
                            out(_sse({"type": "content_block_start",
                                      "index": block_idx,
                                      "content_block": {
                                          "type": "tool_use",
                                          "id": call.get("id") or "toolu_" + uuid.uuid4().hex[:16],
                                          "name": fn.get("name") or "",
                                          "input": {}}}))
                        frag = fn.get("arguments")
                        if frag:
                            out(_sse({"type": "content_block_delta",
                                      "index": tools[up_idx],
                                      "delta": {"type": "input_json_delta",
                                                "partial_json": frag}}))
            if block_idx >= 0:
                out(_sse({"type": "content_block_stop", "index": block_idx}))
            out(_sse({"type": "message_delta",
                      "delta": {"stop_reason": _STOP_MAP.get(finish or "stop", "end_turn"),
                                "stop_sequence": None},
                      "usage": {"output_tokens": usage.get("completion_tokens") or 0}}))
            out(_sse({"type": "message_stop"}))
        except Exception as exc:
            try:
                out(_sse({"type": "error", "error": {"type": "api_error",
                                                     "message": str(exc)}}))
            except Exception:
                pass
        finally:
            try:
                resp.close()
            except Exception:
                pass

    def _aggregate(self, resp, model):
        text, calls, finish, usage = [], {}, None, {}
        try:
            for chunk in self._iterate(resp):
                if chunk.get("usage"):
                    usage = chunk["usage"]
                for choice in chunk.get("choices") or []:
                    delta = choice.get("delta") or {}
                    if choice.get("finish_reason"):
                        finish = choice["finish_reason"]
                    if delta.get("content"):
                        text.append(delta["content"])
                    for call in delta.get("tool_calls") or []:
                        slot = calls.setdefault(call.get("index", 0),
                                                {"id": "", "name": "", "args": ""})
                        fn = call.get("function") or {}
                        if call.get("id"):
                            slot["id"] = call["id"]
                        if fn.get("name"):
                            slot["name"] = fn["name"]
                        if fn.get("arguments"):
                            slot["args"] += fn["arguments"]
        finally:
            try:
                resp.close()
            except Exception:
                pass
        content = []
        joined = "".join(text)
        if joined:
            content.append({"type": "text", "text": joined})
        for _, slot in sorted(calls.items()):
            try:
                args = json.loads(slot["args"] or "{}")
            except Exception:
                args = {}
            content.append({"type": "tool_use", "id": slot["id"] or "",
                            "name": slot["name"], "input": args})
        payload = {"id": "msg_" + uuid.uuid4().hex[:20], "type": "message",
                   "role": "assistant", "model": model, "content": content,
                   "stop_reason": _STOP_MAP.get(finish or "stop", "end_turn"),
                   "stop_sequence": None,
                   "usage": {"input_tokens": usage.get("prompt_tokens") or 0,
                             "output_tokens": usage.get("completion_tokens") or 0}}
        return self._send(200, payload)
