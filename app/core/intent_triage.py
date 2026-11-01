# -*- coding: utf-8 -*-
"""对话意图 triage 兜底（C0 阶段③，2026-11-01，决策 15）。

确定性解析器（前端 chat-intent.js 的 PARSER 切片）没接住的短句，让编排者
模型起草一个**意图草稿**；草稿绝不直接执行——前端照常渲染计划确认卡，用户
点「确认执行」才真正动作。确认卡是硬闸，所以分类器可以弱：分错了顶多出一张
被取消的卡，不会出任何副作用。

成本纪律：
- 门控在前端（创作动词守卫 + 书名/命令词启发 + ≤60 字）：绝大多数建任务句
  零调用零延迟，只有「像命令但没解析出来」的句子才到这儿；
- 每次至多一次模型调用（prompt 约 250 token，max_tokens 120，超时 8 秒）；
- 任何失败（无编排者/超时/答非所问）返回 None，前端原样走建任务——
  fail-open，triage 永不拦住用户的正常提交；
- 记账口径与 jev/vision 兜底相同：辅助调用不进用量台账（金额极小）；
  将来接更便宜的档位时换 resolve 入口即可。

开关：settings["chat_triage_enabled"]（默认开）。
"""
from __future__ import annotations

import json
import re

_KINDS = ("publish", "pause", "resume", "retry", "query", "unschedule")
_PLATS = ("", "fanqie", "qimao")
_MODES = ("", "direct", "draft", "schedule")
_TIME_RE = re.compile(r"^([01]?\d|2[0-3]):([0-5]\d)$")

_PROMPT_TMPL = (
    "你是 CodeBee 的意图分类器。用户在任务输入框说了一句话，请判断它想触发"
    "哪个动作。动作类型：publish=发布/直发/存草稿/定时发布已写好的章；"
    "pause=暂停；resume=恢复运行；retry=重试；query=查进度/字数/状态；"
    "unschedule=取消定时；none=都不是（想新建任务、写新内容或闲聊）。\n"
    "已有的书（book 字段必须从下面原样选一个，列表里没有就填空字符串）：\n%s\n"
    "只输出一行 JSON，不要任何解释或代码块标记：\n"
    '{"kind":"publish或pause或resume或retry或query或unschedule或none",'
    '"book":"书名或空","count":0,"platform":"","mode":"","time":""}\n'
    "count=要发的章数（整数，没说填0）；platform=fanqie或qimao或空；"
    "mode=direct或draft或schedule（schedule=定时发布，time填HH:MM，"
    "其余动作这些字段都填默认空值）。\n"
    "用户的话：「%s」")


def _books_block(books):
    return "\n".join("%d. %s" % (i + 1, b) for i, b in enumerate(books)) or "（无）"


def _parse_reply(text, books):
    """严格解析模型回复；任何不符合契约的部分都让整个结果作废（返回 None）。"""
    if not text:
        return None
    start, end = text.find("{"), text.rfind("}")
    if start < 0 or end <= start:
        return None
    try:
        j = json.loads(text[start:end + 1])
    except ValueError:
        return None
    if not isinstance(j, dict):
        return None
    kind = str(j.get("kind") or "").strip()
    if kind not in _KINDS:
        return None
    book = str(j.get("book") or "").strip()[:40]
    if book and book not in books:
        book = ""          # 书名必须来自真实清单，防幻觉
    try:
        count = int(j.get("count") or 0)
    except (TypeError, ValueError):
        count = 0
    if count < 0 or count > 99:
        count = 0
    platform = str(j.get("platform") or "").strip()
    if platform not in _PLATS:
        platform = ""
    mode = str(j.get("mode") or "").strip()
    if mode not in _MODES:
        mode = ""
    t = str(j.get("time") or "").strip()
    if not _TIME_RE.match(t):
        t = ""
    if kind == "none":
        return None
    it = {"kind": kind, "book": book, "count": count,
          "platform": platform, "mode": mode, "time": t}
    if it["kind"] == "publish" and it["mode"] == "":
        it["mode"] = "direct"
    if it["kind"] != "publish":
        # 控制类/查询只要 kind+book，其余字段不带（防下游误读）
        it = {"kind": kind, "book": book, "count": 0,
              "platform": "", "mode": "", "time": ""}
    return it


def triage(text, books):
    """意图兜底主入口。text=用户原话（调用方截断），books=可选书名清单。

    返回意图 dict 或 None（none/失败/开关关都归 None，前端照旧建任务）。"""
    text = str(text or "").strip()
    if not text or len(text) > 200:
        return None
    try:
        from . import settings
        if not settings.load().get("chat_triage_enabled", True):
            return None
    except Exception:
        pass
    books = [str(b or "").strip()[:40] for b in (books or []) if str(b or "").strip()]
    books = list(dict.fromkeys(books))[:40]
    try:
        from . import modelhub
        orch = modelhub.resolve_orchestrator()
        if not orch:
            return None
        prov, model = orch
        r = modelhub.chat(prov.get("id"), model,
                          _PROMPT_TMPL % (_books_block(books), text),
                          max_tokens=120, timeout=8)
        text_out = (r.get("text") or "") if r.get("ok") else ""
        return _parse_reply(text_out, books)
    except Exception:
        return None
