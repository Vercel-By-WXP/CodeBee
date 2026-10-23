# -*- coding: utf-8 -*-
"""Standard-library helpers for decoding and presenting CLI output."""
from __future__ import annotations

import json
import re


_FALLBACK_ENCODINGS = ("gb18030", "cp936", "mbcs", "cp1252", "latin-1")


def _decode_invalid_utf8_runs(text):
    """把 ``surrogateescape`` 保留的坏字节按连续片段转成可读文本。

    Windows CLI 经常把 UTF-8 正文和系统代码页错误混在同一条输出流里。
    先用 surrogateescape 解码可以保留所有合法 UTF-8；只把真正无法解码的
    字节片段交给 GB18030/系统代码页处理，避免原来「整行回退 GBK」导致的
    中文双重乱码。返回 ``(text, degraded, encodings)``。
    """
    if not text or not any(0xDC80 <= ord(ch) <= 0xDCFF for ch in text):
        return text, False, ("utf-8",)
    out = []
    encodings = ["utf-8"]
    run = []

    def flush():
        if not run:
            return
        blob = bytes(ord(ch) - 0xDC00 for ch in run)
        run.clear()
        chosen = None
        for encoding in _FALLBACK_ENCODINGS:
            try:
                chosen = blob.decode(encoding)
                encodings.append(encoding)
                break
            except (LookupError, UnicodeDecodeError):
                continue
        if chosen is None:
            chosen = blob.decode("latin-1", "replace")
            encodings.append("latin-1")
        out.append(chosen)

    for ch in text:
        if 0xDC80 <= ord(ch) <= 0xDCFF:
            run.append(ch)
        else:
            flush()
            out.append(ch)
    flush()
    return "".join(out), True, tuple(dict.fromkeys(encodings))


def decode_output_info(data):
    """Decode bytes and report whether local code-page recovery was needed.

    The text return value remains usable by existing callers. ``decode_degraded``
    is diagnostic metadata only: it means the local process emitted bytes that
    were not valid UTF-8, not that the upstream model failed.
    """
    if not data:
        return {"text": "", "decode_degraded": False, "encodings": ("",)}
    try:
        text = data.decode("utf-8")
        return {"text": text, "decode_degraded": False, "encodings": ("utf-8",)}
    except UnicodeDecodeError:
        text, degraded, encodings = _decode_invalid_utf8_runs(
            data.decode("utf-8", "surrogateescape"))
        return {"text": text, "decode_degraded": degraded,
                "encodings": encodings}


def _decode_line(blob):
    """单行解码，保留混合 UTF-8/Windows 代码页里的合法 UTF-8。"""
    return decode_output_info(blob)["text"]


def decode_output(data):
    """子进程输出解码：UTF-8 严格解码失败时**逐行** UTF-8→GBK 兜底。

    整块 GBK 回退已废（2026-10-16 pwsh 命令输出实案）：块里只要混进一个非
    UTF-8 字节（rg/Get-Content 吐的杂字），整块 UTF-8 就失败，GBK 又几乎
    来者不拒——于是纯 UTF-8 中文被整块误读成「鏍煎紡妫€」双重乱码写进日志
    与步骤摘要（16:29 连载 run 的评审维度名全灭即此案）。GBK/UTF-8 的多字
    节序列都不含 0x0A，按行切不会切坏字符：行级 UTF-8 优先、GBK 兜底、
    replace 收尾，混合编码日志两边都保住（2026-09-20 aider 日志实证的
    场景不变）。"""
    return decode_output_info(data)["text"]


def read_text_any_enc(path):
    """读文本文件，编码纪律同 decode_output（UTF-8 严格 → GBK 回退 → replace 兜底）。

    工作区文件由 CLI 子代理落盘，中文 Windows 上 PowerShell Set-Content 缺省写
    GBK；消费侧（章节/diff/故事圣经）统一走本函数，不再因编码混编出 U+FFFD。
    文件不存在/读失败抛 OSError，由调用方兜底（与 open 语义一致）。"""
    with open(path, "rb") as f:
        data = f.read()
    return decode_output(data)


def tail_decoded(data, tail):
    """按字节取尾部再解码；切片可能落在 UTF-8 多字节字符中间，先丢弃开头的
    continuation 字节（10xxxxxx，至多 3 个）对齐字符边界，否则残缺字节会被
    GBK 回退解码成乱码字符。"""
    if not data:
        return ""
    chunk = data[-tail:] if tail and len(data) > tail else data
    i = 0
    while i < len(chunk) and i < 3 and (chunk[i] & 0xC0) == 0x80:
        i += 1
    return decode_output(chunk[i:])


# 终端控制序列：CSI（颜色/光标/清行，`\x1b[91m`）、OSC（窗口标题）、其余两字符转义
_ANSI_RE = re.compile(
    r"\x1b\[[0-9;?]*[ -/]*[@-~]"
    r"|\x1b\][^\x07\x1b]*(?:\x07|\x1b\\)?"
    r"|\x1b[@-Z\\-_]"
)
# 被切片/前端截断掉 ESC 的「裸序列」残尾（`[91m`、`[0m`）：只在行首止血，
# 正文里合法的 [数字+字母]（如 [1m] 引用）不动——后面紧跟 `]` 的不算残尾
_ANSI_HEAD_RE = re.compile(r"^\[[0-9;?]{1,6}[A-Za-z](?!\])")
# CLI 拿来做进度条/画框/旋转动画的图元，成串出现时人读不出任何信息
_NOISE_RUN_RE = re.compile(r"([\u2500-\u259f\u25a0-\u25ff\u2800-\u28ff])\1{7,}")
# 解不出来的字节（replace 兜底）成串即「乱码墙」——多数等宽字体把 U+FFFD 画成
# 带问号的菱形，一屏看起来就是 ◆◆◆◆
_FFFD_RUN_RE = re.compile(r"\ufffd{3,}")
_CTRL_RE = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]")


def strip_ansi(text):
    """剥掉 ANSI 转义序列（颜色/光标/进度），并清掉行首被截断的裸序列残尾。"""
    if not text:
        return ""
    out = _ANSI_RE.sub("", text)
    # 残尾可能连着几个（`[91m[0mError:`），一次替换只够消掉最前面那个
    while True:
        nxt = "\n".join(_ANSI_HEAD_RE.sub("", ln) for ln in out.split("\n"))
        if nxt == out:
            return out
        out = nxt


def clean_cli_text(text):
    """CLI 原始输出 → 人类可读文本（只用于错误摘要/日志展示，不碰模型正文）。

    终端噪声四种，都是实测踩过的：
    1. ANSI 转义：`\\x1b[91m\\x1b[1mError:` 在浏览器里渲染成 `[91m[1mError:`；
    2. 裸 `\\r` 覆写：进度条/旋转动画反复回退改写同一行，原样展示会叠成一片；
       `\\r\\n` 是行结束符不是覆写，先归一化再按覆写取「该行最终形态」；
    3. 画线/进度图元成串（`────…`、`████…`）与 U+FFFD 乱码墙（编码不可解）；
    4. 剩余控制字符。
    """
    if not text:
        return ""
    out = strip_ansi(text).replace("\r\n", "\n")
    if "\r" in out:
        lines = []
        for ln in out.split("\n"):
            if "\r" in ln:
                parts = ln.split("\r")
                ln = next((p for p in reversed(parts) if p.strip()), parts[-1])
            lines.append(ln)
        out = "\n".join(lines)
    out = _CTRL_RE.sub("", out)
    out = _FFFD_RUN_RE.sub(
        lambda m: "…（%d 个字符无法解码：CLI 输出不是 UTF-8/GBK）" % len(m.group(0)), out)
    out = _NOISE_RUN_RE.sub(lambda m: m.group(1) * 3 + "…", out)
    return re.sub(r"\n{4,}", "\n\n\n", out)


_TS_PREFIX = re.compile(r"^\d{4}-\d{2}-\d{2}T[\d:.,]+Z?\s*")


def collapse_dup_lines(text, max_group=4):
    """折叠「归一化后重复」的邻近行，保留每种首行原文并标注折叠数。

    真实案例：codex_otel 遥测对网关自定义模型名（如 [opencode]xxx，方括号是
    非法 OTel tag 字符）每个 SSE 事件刷一对 WARN——counter/duration 两种文案
    交替出现，且每条带微秒时间戳：剥掉时间戳前缀后相邻行仍不相等，必须按
    「邻近组」折叠——组内至多 max_group 个键，新键只有组满才结算上一组，
    这样交替刷屏的各个文案都归入同一组（计数各自累计）。空行先结算所在组
    再原样保留。超过组宽的零散重复（中间隔着足量新行）不受影响。
    """
    if not text:
        return ""
    out = []
    keys, first, cnt = [], {}, {}

    def _flush():
        for k in keys:
            out.append(first[k])
            if cnt[k] > 1:
                out.append("⋯（上行重复 ×%d 已折叠）" % (cnt[k] - 1))
        keys.clear()
        first.clear()
        cnt.clear()

    for ln in text.splitlines():
        k = _TS_PREFIX.sub("", ln).strip()
        if not k:
            _flush()          # 先结算扣住的组，保证空行前后顺序不失真
            out.append(ln)
            continue
        if k in cnt:
            cnt[k] += 1
        elif len(keys) >= max_group:
            _flush()
            keys.append(k)
            first[k] = ln
            cnt[k] = 1
        else:
            keys.append(k)
            first[k] = ln
            cnt[k] = 1
    _flush()
    if text.endswith("\n"):
        out.append("")
    return "\n".join(out)


def _fmt_codex_item(it, cap):
    t = it.get("type") or ""
    if t == "agent_message":
        return "【消息】" + str(it.get("text") or "")[:cap]
    if t == "reasoning":
        return "【思考】" + str(it.get("text") or "")[:cap]
    if t == "command_execution":
        line = "【命令】%s（退出码 %s）" % (it.get("command") or "?", it.get("exit_code", "?"))
        outp = str(it.get("aggregated_output") or "").strip()
        if outp:
            line += "\n  | " + outp[:600].replace("\n", "\n  | ")
        return line
    if t == "file_change":
        names = ", ".join(str(c.get("path") or "?") for c in (it.get("changes") or [])
                          if isinstance(c, dict))
        return "【文件改动】" + (names or "?")
    if t == "mcp_tool_call":
        return "【工具】%s %s" % (it.get("tool") or "?",
                                  json.dumps(it.get("arguments") or "", ensure_ascii=False)[:200])
    if t == "web_search":
        return "【搜索】" + str(it.get("query") or "")
    if t == "error":
        return "【错误】" + str(it.get("message") or "")
    return None


def pretty_cli_log(text, max_event_chars=4000):
    """codex --json 的 JSONL 事件流 → 人类可读行；非 JSONL 行原样保留。

    --json 模式下 stdout 全是机器事件（thread/turn/item…），智能体真正在说的
    话被埋在转义 JSON 里；这里逐行翻译成【消息】【思考】【命令】，让日志抽屉
    读到的是「蜂在干什么」。解析失败或未识别的事件类型原样保留，不吞内容。

    claude stream-json 同场翻译（2026-09-22 真实案：3.2MB 步骤日志 15828 条
    thinking_tokens 心跳，每 SSE 分片一条、数值只涨 1~2）：思考心跳按邻近组
    折成一行；init/api_retry/assistant/result 翻译成可读行——api_retry 是
    429/断连诊断金矿，逐条保留不折叠。只影响抽屉显示，原始日志与解析不动。
    """
    out = []
    hb = [0, 0]   # claude 思考心跳邻近组：[条数, 最大估算 tokens]
    msg_seen = [False]   # 是否已出过【消息】（防 result 再重复一遍正文）

    def _flush_thinking():
        if hb[0]:
            out.append("— 思考中（心跳 ×%d 已折叠，估算 ~%d tokens）—" % (hb[0], hb[1]))
            hb[0] = hb[1] = 0

    for ln in (text or "").splitlines():
        s = ln.strip()
        ev = None
        if s.startswith("{") and s.endswith("}"):
            try:
                ev = json.loads(s)
            except Exception:
                ev = None
        if isinstance(ev, dict) and ev.get("type") == "system" \
                and (ev.get("subtype") or "") == "thinking_tokens":
            try:
                n = int(ev.get("estimated_tokens") or 0)
            except Exception:
                n = 0
            hb[0] += 1
            if n > hb[1]:
                hb[1] = n
            continue
        _flush_thinking()
        if not isinstance(ev, dict):
            out.append(ln)
            continue
        typ = ev.get("type") or ""
        if typ == "system":   # claude 其余系统事件
            sub = ev.get("subtype") or ""
            if sub == "init":
                out.append("— 会话启动（model=%s）—" % (ev.get("model") or "?"))
            elif sub == "api_retry":
                out.append("— API 重试 %s/%s（等 %sms）：%s —" % (
                    ev.get("attempt", "?"), ev.get("max_retries", "?"),
                    ev.get("retry_delay_ms", "?"), ev.get("error") or "?"))
            elif sub == "task_started":
                out.append("— 工具任务启动：%s —" % (ev.get("description") or "?"))
            else:
                out.append(ln)   # 未知 subtype 原样保留，不吞内容
            continue
        if typ == "assistant":   # claude 正文/思考/工具调用都在 content 块里
            for blk in ((ev.get("message") or {}).get("content") or []):
                if not isinstance(blk, dict):
                    continue
                bt = blk.get("type") or ""
                if bt == "thinking":
                    th = " ".join(str(blk.get("thinking") or "").split())
                    if th:
                        out.append("【思考】" + th[:200])
                elif bt == "text":
                    tx = str(blk.get("text") or "")
                    if tx:
                        out.append("【消息】" + tx[:max_event_chars])
                        msg_seen[0] = True
                elif bt == "tool_use":
                    out.append("【命令】%s %s" % (
                        blk.get("name") or "?",
                        json.dumps(blk.get("input") or "", ensure_ascii=False)[:200]))
            continue
        if typ in ("user", "stream_event", "tool_progress"):
            continue   # claude 工具回包/流片段是过程噪音
        if typ == "result":   # claude 收尾：统计一行；报错带原因
            u = ev.get("usage") or {}
            if ev.get("is_error"):
                out.append("— 完成（报错）：" + str(ev.get("result") or "")[:300] + " —")
            else:
                cost = ev.get("total_cost_usd")
                if not msg_seen[0]:
                    tx = str(ev.get("result") or "")
                    if tx:   # 旧单 JSON 模式没有 assistant 事件，正文在这补上
                        out.append("【消息】" + tx[:max_event_chars])
                        msg_seen[0] = True
                out.append("— 完成（tokens 入 %s / 出 %s，%s）—" % (
                    u.get("input_tokens", "?"), u.get("output_tokens", "?"),
                    ("$%.2f" % cost) if isinstance(cost, (int, float)) else "?"))
            continue
        if typ in ("thread.started", "turn.started"):
            continue
        if typ == "turn.completed":
            u = ev.get("usage") or {}
            out.append("— 一轮完成（tokens 入 %s / 出 %s）—" % (
                u.get("input_tokens", "?"), u.get("output_tokens", "?")))
            continue
        if typ == "turn.failed":
            e = ev.get("error")
            out.append("— 一轮失败：%s —" % (e.get("message") if isinstance(e, dict) else e))
            continue
        if typ in ("item.completed", "item.started", "item.updated"):
            if typ != "item.completed" or not isinstance(ev.get("item"), dict):
                continue  # started/updated 是过程噪音，completed 才有内容
            line = _fmt_codex_item(ev["item"], max_event_chars)
            if line:
                out.append(line)
            continue
        out.append(ln)
    return "\n".join(out)


