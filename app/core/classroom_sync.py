# -*- coding: utf-8 -*-
"""番茄作家课堂每日自动沉淀：抓取→模型提炼→知识库入库。

调度形态与 cleanup/zentao 联动同款（自节流）：由 knowledge 的启动收口
（migrate_drafts_approved）拉起常驻 daemon 线程，先等 90 秒避开启动高峰，
之后每 30 分钟自检一次，距上次成功 ≥20 小时才真跑——一天至多沉淀一轮。
检测零模型成本（只比对文章 id）；只有发现新文章才调编排模型逐篇提炼，
单轮上限 3 篇控成本。TUTTI_CLASSROOM_SYNC=0 一键关停。

SPA 抓取配方（2026-10-12 实测，文章 id 稳定）：
- 列表页 HTML 内嵌 JSON：正则 ``u002F<id>`` 提文章 id，标题/发布日在其
  前方近邻的同形 JSON 字段里；
- 文章页正文在 ``window._ROUTER_DATA.loaderData["article.(id)/page"].content``，
  转义 HTML，JSON 终点为首个 ``</script>``。

分层与防环（架构策略 L_i 只准 import L_j, j>=i）：本模块登记在层 0，
可静态引 paths/tlsctx（L0）与 modelhub（L1）；knowledge 在函数内引本模块
并把自身作为 kb 注入 start_daemon——本模块不静态 import knowledge，
静态图无环。出站 HTTPS 一律走 tlsctx.context() + 房规 _validate_host
（协议/解析 IP 边界校验），host 另钉死官方域名、不跟随重定向，
不接任何外部输入的 URL，无 SSRF 面。
"""
from __future__ import annotations

import html as htmllib
import json
import os
import re
import threading
import time
import urllib.parse
import urllib.request

from . import prompt_templates

from . import modelhub, paths, tlsctx

FANQIE_TUTORIAL_URL = "https://fanqienovel.com/writer/zone/tutorial?enter_from=menu"
FANQIE_ARTICLE_URL = "https://fanqienovel.com/writer/zone/article/"
_HOST = "fanqienovel.com"
_UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
       "(KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36")

COOLDOWN_SECS = 20 * 3600    # 一天至多真跑一轮（留 4 小时漂移余量）
POLL_SECS = 30 * 60          # daemon 自检节奏
FIRST_WAIT_SECS = 90         # 启动后首跑前的静默（避开启动高峰）
MAX_ARTICLES_PER_RUN = 3     # 单轮提炼篇数上限（控模型成本）
BODY_CAP = 1400              # 提炼正文上限（知识库 1500 闸内留余量）
TEXT_SNIPPET = 6000          # 喂给模型的单篇正文截断

_KB = None                   # start_daemon 注入的 knowledge 模块
_THREAD = None
_STOP = threading.Event()

_DISTILL_PROMPT = """你是网文平台规则管理员。下面是番茄小说官方作家课堂一篇文章的全文。
只提炼「平台怎么规定的」硬事实——资格条件、次数、时限、金额、流程、禁区，
保留原文数字口径；忽略营销话术、评论区互动题、截图/按钮操作描述。
只输出一个 ```json 代码块，不要其他内容：
{"title": "≤20字的规则主题", "body": "≤500字规则要点，自含上下文"}
文章没有可长期复用的硬规则（如纯功能公告）就返回 {"title": "", "body": ""}。

## 文章：__TITLE__

__BODY__"""


# ---------------------------------------------------------------- 抓取

class _NoRedirect(urllib.request.HTTPRedirectHandler):
    """3xx 一律不跟随：官方课堂两个端点直出 200，不跟重定向防被引离官方域。"""

    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


_OPENER = urllib.request.build_opener(
    urllib.request.HTTPSHandler(context=tlsctx.context()), _NoRedirect)


def _get(url):
    """出站 GET：host 钉死官方域名 + 房规 SSRF 校验（协议/解析 IP 边界），
    不跟随重定向。"""
    split = urllib.parse.urlsplit(url)
    if split.scheme != "https" or (split.hostname or "") != _HOST:
        raise ValueError("classroom_sync: 非预期出站地址 %r" % url)
    host, err = modelhub._validate_host(url, allow_private=False)
    if host is None:
        raise ValueError("classroom_sync: SSRF 校验拒绝（%s）" % err)
    req = urllib.request.Request(url, headers={"User-Agent": _UA})
    with _OPENER.open(req, timeout=30) as r:
        return r.read().decode("utf-8", errors="ignore")


def parse_article_list(html):
    """列表页 → [{id,title,time}]（按出现序去重）。

    官方 JSON 字段序：title 在 id 前方近邻，time 紧跟 id 之后——标题回望、
    日期前瞻，各取最近一个。"""
    out, seen = [], set()
    for m in re.finditer(r"u002F(\d{16,25})", html):
        aid = m.group(1)
        if aid in seen:
            continue
        back = html[max(0, m.start() - 3000):m.start()]
        t = re.findall(r'"title":"([^"]{1,120})"', back)
        tm = re.findall(r'"time":"([\d-]{8,10})"',
                        html[m.end():m.end() + 300])
        seen.add(aid)
        out.append({"id": aid, "title": t[-1] if t else "",
                    "time": tm[0] if tm else ""})
    return out


_TAGMAP = [
    (re.compile(r"<h1[^>]*>", re.I), "\n\n# "),
    (re.compile(r"<h2[^>]*>", re.I), "\n\n## "),
    (re.compile(r"<h3[^>]*>", re.I), "\n\n### "),
    (re.compile(r"<h4[^>]*>", re.I), "\n\n#### "),
    (re.compile(r"<li[^>]*>", re.I), "\n- "),
    (re.compile(r"<(p|div|blockquote|tr|table|ul|ol)[^>]*>", re.I), "\n"),
    (re.compile(r"<br\s*/?>", re.I), "\n"),
]


def html_to_text(hhtml):
    """课堂正文 HTML → 纯文本（标题/条目保结构，其余标签剥离）。"""
    t = hhtml
    for rx, rep in _TAGMAP:
        t = rx.sub(rep, t)
    t = re.sub(r"<[^>]+>", "", t)
    t = htmllib.unescape(t).replace("\u00a0", " ")
    t = re.sub(r"\n{3,}", "\n\n", t)
    return re.sub(r"[ \t]+\n", "\n", t).strip()


def parse_article_content(html):
    """文章页 → {"title","text"}；无 ROUTER_DATA 或无正文返回 None。"""
    i = html.find("window._ROUTER_DATA = ")
    if i < 0:
        return None
    blob = html[i + len("window._ROUTER_DATA = "):]
    j = blob.find("</script>")
    if j < 0:
        return None
    try:
        data = json.loads(blob[:j].rstrip().rstrip(";"))
    except Exception:
        return None
    for v in (data.get("loaderData") or {}).values():
        if isinstance(v, dict) and v.get("content"):
            return {"title": str(v.get("title") or ""),
                    "text": html_to_text(str(v["content"]))}
    return None


def list_articles():
    return parse_article_list(_get(FANQIE_TUTORIAL_URL))


def fetch_article_text(aid):
    return parse_article_content(_get(FANQIE_ARTICLE_URL + str(aid)))


# ---------------------------------------------------------------- 提炼入库

def _extract_json(text):
    """模型回复取 JSON（直解 → ```json 围栏；runner.extract_json 的窄版）。"""
    if not text:
        return None
    text = text.strip()
    try:
        return json.loads(text)
    except Exception:
        pass
    m = re.search(r"```(?:json)?\s*(\{.*?\})\s*```", text, re.S)
    if m:
        try:
            return json.loads(m.group(1))
        except Exception:
            return None
    return None


def _distill(title, text):
    """调编排模型提炼单篇。返回 (title, body) / ("","")(无干货) / None(失败重试)。"""
    orch = modelhub.resolve_orchestrator()
    if not orch:
        return None
    prov, model = orch
    prompt = prompt_templates.render_values(_DISTILL_PROMPT, {
        "__TITLE__": title[:80],
        "__BODY__": text[:TEXT_SNIPPET],
    })
    res = modelhub.chat(prov["id"], model, prompt, max_tokens=4000, timeout=300)
    if not res.get("ok"):
        return None
    data = _extract_json(res.get("text") or "")
    if not isinstance(data, dict):
        return None
    return (str(data.get("title") or "").strip(),
            str(data.get("body") or "").strip()[:BODY_CAP])


def _state_path():
    return paths.DATA_DIR / "classroom_sync.json"


def _load_state():
    try:
        st = json.loads(_state_path().read_text(encoding="utf-8"))
        if isinstance(st, dict):
            st.setdefault("synced", {})
            return st
    except Exception:
        pass
    return {"ok_at": 0, "synced": {}}


def _save_state(st):
    try:
        p = _state_path()
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(json.dumps(st, ensure_ascii=False, indent=1),
                     encoding="utf-8")
    except Exception:
        pass


def sync_once(force=False, kb=None, fetch=None):
    """跑一轮沉淀：列新文章→逐篇提炼→approved 知识入库。

    返回 {"new":N,"made":N,"errors":[...]} 或 {"skipped":"cooldown"/"no-kb"}。
    提炼失败的篇目不记账，下轮自动重试；模型判「无硬规则」的篇目记账
    消费不再重炼。force=True 绕过 20 小时冷却（测试/手动）。
    """
    kb = kb or _KB
    if kb is None:
        return {"skipped": "no-kb"}
    if fetch is None:
        import sys
        fetch = sys.modules[__name__]
    st = _load_state()
    now = time.time()
    if not force and now - float(st.get("ok_at") or 0) < COOLDOWN_SECS:
        return {"skipped": "cooldown"}
    try:
        arts = fetch.list_articles()
    except Exception as e:
        return {"error": "list: %s" % str(e)[:200]}
    synced = st.setdefault("synced", {})
    fresh = [a for a in arts if str(a.get("id") or "") not in synced]
    made, errors = 0, []
    for a in fresh[:MAX_ARTICLES_PER_RUN]:
        aid = str(a.get("id") or "")
        try:
            art = fetch.fetch_article_text(aid) or {}
            text = str(art.get("text") or "")
            if not text.strip():
                synced[aid] = {"title": str(art.get("title")
                                            or a.get("title") or "")[:80],
                               "empty": True}
                continue
            got = _distill(str(a.get("title") or ""), text)
            if got is None:
                errors.append({"id": aid, "error": "distill"})
                continue
            t, b = got
            if t and b:
                as_of = str(a.get("time") or "")
                if not re.match(r"^\d{4}-\d{2}-\d{2}$", as_of):
                    as_of = time.strftime("%Y-%m-%d")
                kb.upsert_entry("serial_novel", ("番茄课堂｜" + t)[:80], b,
                                tags=["番茄", "平台规则", "作家课堂"],
                                source=FANQIE_ARTICLE_URL + aid,
                                as_of=as_of, status="approved",
                                confidence="medium")
                made += 1
            synced[aid] = {"title": str(art.get("title")
                                        or a.get("title") or "")[:80]}
        except Exception as e:
            errors.append({"id": aid, "error": str(e)[:200]})
    if not errors:
        st["ok_at"] = now
    _save_state(st)
    return {"new": len(fresh), "made": made, "errors": errors}


# ---------------------------------------------------------------- 调度

def _loop():
    _STOP.wait(FIRST_WAIT_SECS)
    while not _STOP.is_set():
        try:
            sync_once()
        except Exception:
            pass
        _STOP.wait(POLL_SECS)


def start_daemon(kb):
    """启动收口（knowledge.migrate_drafts_approved 调用）：注入 kb 并拉起
    常驻 daemon 线程。幂等（重复调用只认第一个线程）；
    TUTTI_CLASSROOM_SYNC=0 关停。返回线程或 None。"""
    global _KB, _THREAD
    if os.environ.get("TUTTI_CLASSROOM_SYNC", "") == "0":
        return None
    _KB = kb
    if _THREAD is not None and _THREAD.is_alive():
        return _THREAD
    _STOP.clear()
    _THREAD = threading.Thread(target=_loop, name="kb-classroom", daemon=True)
    _THREAD.start()
    return _THREAD
