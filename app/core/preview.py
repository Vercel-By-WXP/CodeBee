# -*- coding: utf-8 -*-
"""运行预览：把任务工作目录里的网页成品挂成「可实时查看」的页面。

借鉴对话式编程产品（右栏「运行 / index.html / style.css / script.js」页签）：
代码任务跑完，用户要的不是翻文件，而是**直接看到东西跑起来**。这里提供两件事：

1. list_app(run_id)：从本任务的成品文件里挑出可预览的应用——入口 HTML +
   同目录的代码文件（按 html→css→js 排序），供前端渲染页签条。
2. serve(run_id, rel)：只读挂载工作目录里的静态资源，供 iframe / 新窗口直接
   打开（<img>/<link>/<script> 这些子资源请求带不了自定义请求头）。

**访问令牌走路径段**（/preview/<run>/<令牌>/<文件>）：子资源是浏览器按文档
URL 相对解析的，查询串不会被继承——令牌只能放路径里才覆盖得到 style.css /
script.js。鉴权本身直接复用 remote.request_authed（与 /api/* 同一把尺子，
含反向代理下"带转发头的 loopback 算远程"的判断），所以本地免令牌、公网必须
带令牌，两条路都不需要新写一套判断。

安全边界（这是把一个任意目录交给浏览器渲染，必须写清楚）：
- 只认静态扩展名白名单，单文件体积上限；路径 resolve 后必须落在工作目录内。
- 工作目录下的服务端可执行物（.py/.php/.rb/.sh…）不在白名单，不会被预览。
- HTML 响应注入 <base>（相对资源在子路径下也解析得对）+ 一段极小的补丁：
  页面里的 fetch/XHR 指向 /api/ 时自动补上访问令牌，让「对话生成的示例应用」
  能真的连通后端。补丁只加一个请求头；写接口该 423/401 的照旧。
- 前端把预览 iframe 挂在同一来源下、不加 sandbox：预览要**像真的在跑**——生成的
  示例应用大量用 localStorage，sandbox 去掉 same-origin 会让它当场抛 SecurityError，
  而 allow-same-origin 的 sandbox 等于没加（页面可自行摘掉）。这与本地 dev server
  的信任模型一致：跑的是你自己的代码、在你自己的机器上。代价是预览页能读到
  本会话的 localStorage 令牌，故「预览」页签只在用户主动点开时才加载（不预跑）。
"""
from __future__ import annotations

import json
import re
from pathlib import Path

from . import store

# 静态资源白名单：只挂能安全交给浏览器渲染的扩展名。刻意不含 .py/.php/.rb/.sh
# 等工作目录里可能出现的服务端可执行物——预览是只读静态托管，不是网站服务器。
PREVIEW_EXT = {
    ".html", ".htm", ".css", ".js", ".mjs", ".cjs", ".json", ".map",
    ".svg", ".png", ".jpg", ".jpeg", ".gif", ".webp", ".bmp", ".ico", ".avif",
    ".woff", ".woff2", ".ttf", ".otf", ".eot",
    ".mp3", ".wav", ".ogg", ".mp4", ".webm",
    ".txt", ".md", ".csv", ".xml", ".wasm", ".yml", ".yaml", ".toml",
}

# 页签条里列出的扩展名（比可挂载范围窄）：图片/字体是资源不是「文件」，
# 给它们开页签只是噪音。
TAB_EXT = {".html", ".htm", ".css", ".js", ".mjs", ".cjs", ".json", ".svg",
           ".txt", ".md", ".csv", ".xml", ".yml", ".yaml", ".toml", ".map"}

_HTML_EXT = {".html", ".htm"}

# 页签排序权重：入口 → 样式 → 脚本 → 数据 → 其它
_TAB_ORDER = {".html": 0, ".htm": 0, ".css": 1, ".js": 2, ".mjs": 2, ".cjs": 2,
              ".json": 3, ".map": 4, ".svg": 5, ".md": 6, ".txt": 6, ".csv": 6,
              ".xml": 6, ".yml": 6, ".yaml": 6, ".toml": 6}

BASE = "/preview"           # 路由前缀，与 main.py 的 do_GET 分发约定
MAX_FILE = 4 << 20          # 单文件上限：超过不挂（大文件走「成果」页签下载）
MAX_TABS = 12               # 页签上限：防病态目录撑爆页签条
MAX_LIST = 400              # 参与挑选的成品文件上限

_MIME = {
    ".html": "text/html; charset=utf-8", ".htm": "text/html; charset=utf-8",
    ".css": "text/css; charset=utf-8", ".js": "text/javascript; charset=utf-8",
    ".mjs": "text/javascript; charset=utf-8", ".cjs": "text/javascript; charset=utf-8",
    ".json": "application/json; charset=utf-8", ".map": "application/json; charset=utf-8",
    ".svg": "image/svg+xml", ".png": "image/png", ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg", ".gif": "image/gif", ".webp": "image/webp",
    ".bmp": "image/bmp", ".ico": "image/x-icon", ".avif": "image/avif",
    ".woff": "font/woff", ".woff2": "font/woff2", ".ttf": "font/ttf",
    ".otf": "font/otf", ".eot": "application/vnd.ms-fontobject",
    ".mp3": "audio/mpeg", ".wav": "audio/wav", ".ogg": "audio/ogg",
    ".mp4": "video/mp4", ".webm": "video/webm",
    ".txt": "text/plain; charset=utf-8", ".md": "text/plain; charset=utf-8",
    ".csv": "text/plain; charset=utf-8", ".xml": "text/xml; charset=utf-8",
    ".wasm": "application/wasm",
    ".yml": "text/plain; charset=utf-8", ".yaml": "text/plain; charset=utf-8",
    ".toml": "text/plain; charset=utf-8",
}

_TXT = {".html", ".htm", ".css", ".js", ".mjs", ".cjs", ".json", ".map", ".svg",
        ".txt", ".md", ".csv", ".xml", ".yml", ".yaml", ".toml"}

_HEAD_RE = re.compile(r"<head\b[^>]*>", re.I)
_HTML_RE = re.compile(r"<html\b[^>]*>", re.I)


def _ext(name):
    return Path(str(name or "")).suffix.lower()


def safe_rel(rel):
    """归一化工作目录内相对路径。返回 None 表示越界/非法。"""
    rel = str(rel or "").replace("\\", "/").lstrip("/")
    if not rel:
        return None
    parts = [p for p in rel.split("/") if p not in ("", ".")]
    if not parts or any(p == ".." for p in parts):
        return None
    # 隐藏目录（.git/.zcode/.claude…）是工具过程文件，不是成品，一律不给挂
    if any(p.startswith(".") for p in parts):
        return None
    return "/".join(parts)


def resolve(run_id, rel):
    """把 rel 解析到运行工作目录内的真实文件。返回 (Path|None, 错误文本)。

    与 store.read_run_file 同一道守卫：resolve 之后必须仍在工作目录内，
    顺带挡掉软链接指向工作目录之外的形态。
    """
    wd = store.run_workdir(run_id)
    if not wd:
        return None, "该运行没有关联的工作目录"
    safe = safe_rel(rel)
    if not safe:
        return None, "非法路径"
    if _ext(safe) not in PREVIEW_EXT:
        return None, "该类型不支持预览"
    root = Path(wd).resolve()
    try:
        p = (root / safe).resolve()
    except OSError:
        return None, "非法路径"
    if root != p and root not in p.parents:
        return None, "非法路径"
    if not p.is_file():
        return None, "文件不存在"
    return p, None


def _base_href(run_id, tok, rel):
    """入口 HTML 的 <base>：挂在 /preview/<run>/<令牌>/<入口目录>/ 上。

    令牌放在路径里是为了让 style.css / ./js/a.js / ../x.css 这些相对子资源
    自动继承它（查询串不会被子资源继承）；这个绝对路径也让相对资源在
    子目录入口（web/index.html）下解析得对。
    """
    d = rel.rsplit("/", 1)[0] if "/" in rel else ""
    return "%s/%s/%s/%s" % (BASE, run_id, tok, (d + "/") if d else "")


def _patch_token(tok):
    """注入页面脚本：预览页里的 fetch/XHR 打 /api/ 时自动补访问令牌。

    生成的示例应用常直接 fetch("/api/...")，而子资源与脚本发起的请求带不了
    自定义请求头——不补的话预览里一切接口调用都 401，用户会以为是自己代码写
    错了。补丁只加一个请求头；写接口该 423/401 的照旧，不放行任何服务端能力。
    """
    tk = json.dumps(str(tok or ""))
    return (
        "<script>(function(){var TK=%s;if(!TK)return;try{"
        "var isApi=function(u){u=String(u||'');return u.indexOf('/api/')===0"
        "||u.indexOf('api/')===0;};"
        "var _f=window.fetch;if(_f){window.fetch=function(i,o){o=o||{};"
        "var u=(typeof i==='string')?i:((i&&i.url)||'');"
        "if(isApi(u)){try{o.headers=new Headers(o.headers||{});"
        "o.headers.set('X-CodeBee-Token',TK);}catch(e){}}"
        "return _f.call(this,i,o);};}"
        "var _o=XMLHttpRequest.prototype.open;"
        "XMLHttpRequest.prototype.open=function(m,u){"
        "this.__cbApi=isApi(u);return _o.apply(this,arguments);};"
        "var _s=XMLHttpRequest.prototype.send;"
        "XMLHttpRequest.prototype.send=function(b){"
        "if(this.__cbApi){try{this.setRequestHeader('X-CodeBee-Token',TK);}"
        "catch(e){}}return _s.apply(this,arguments);};"
        "}catch(e){}})();</script>" % tk
    )


def _inject(html, run_id, tok, rel):
    """把 <base> + 令牌补丁插到入口 HTML 的最前面（head 内最优先）。"""
    inject = ('<base href="%s">' % _base_href(run_id, tok, rel)) + _patch_token(tok)
    m = _HEAD_RE.search(html)
    if m:
        return html[:m.end()] + inject + html[m.end():]
    m = _HTML_RE.search(html)
    if m:
        return html[:m.end()] + inject + html[m.end():]
    return inject + html


def _decode(data):
    for enc in ("utf-8", "gbk"):
        try:
            return data.decode(enc)
        except UnicodeDecodeError:
            continue
    return data.decode("utf-8", "replace")


def serve(run_id, rel, tok=""):
    """读取一个预览资源。返回 (bytes|None, content-type, 错误文本)。"""
    p, err = resolve(run_id, rel)
    if err:
        return None, "", err
    try:
        if p.stat().st_size > MAX_FILE:
            return None, "", "文件过大，不支持预览"
        data = p.read_bytes()
    except OSError as e:
        return None, "", "读取失败: %s" % e
    ext = p.suffix.lower()
    ctype = _MIME.get(ext, "application/octet-stream")
    if ext in _HTML_EXT:
        # 入口页：注入 <base> 与令牌补丁，并按 UTF-8 重编码（非 UTF-8 落 GBK
        # 回退——子代理在中文 Windows 上常把文件写成 GBK）
        data = _inject(_decode(data), run_id, tok, rel).encode("utf-8")
    elif ext in _TXT:
        data = _decode(data).encode("utf-8")
    return data, ctype, None


def _pick_entry(avail):
    """挑入口 HTML：优先工作目录根的 index.html，其次最浅、最新。

    先只看根目录（单页应用的标准形态），根目录没有 HTML 时才退到最浅的子目录
    入口——`web/index.html` 这类布局也该能预览。
    """
    htmls = [f for f in avail if _ext(f["name"]) in _HTML_EXT]
    if not htmls:
        return ""
    roots = [f for f in htmls if "/" not in f["name"]]
    cands = roots or htmls
    best = None
    for f in cands:
        name = f["name"]
        base = name.rsplit("/", 1)[-1].lower()
        key = (0 if base in ("index.html", "index.htm") else 1,
               name.count("/"), -int(f.get("mtime") or 0), name)
        if best is None or key < best[0]:
            best = (key, name)
    return best[1] if best else ""


def _tab_key(f):
    name = f["name"]
    return (_TAB_ORDER.get(_ext(name), 9), name.count("/"),
            -int(f.get("mtime") or 0), name)


def preview_path(run_id, tok):
    """预览根路径（带路径令牌）：前端据此拼 iframe src 与新窗口地址。

    令牌为空时落「-」占位段：远程开着时它会自然 401，本机（remote 未配令牌
    或 loopback 豁免）照常放行——路径里永远有一段，前端不用做两种拼接。
    """
    return "%s/%s/%s/" % (BASE, run_id, tok or "-")


def list_app(run_id, tok=""):
    """本任务可预览的网页应用。返回前端渲染页签条所需的全部信息。

    {"ok": False, "reason": "no_run"|"no_workdir"|"no_steps"|"no_html"} 表示没有
    可预览的东西（前端据此把「预览」页签整体隐藏，不打扰非前端任务）。
    """
    if not store.get_run(run_id):
        return {"ok": False, "reason": "no_run"}
    wd, files = store.run_artifacts(run_id, limit=MAX_LIST)
    if not wd or not Path(wd).is_dir():
        return {"ok": False, "reason": "no_workdir"}
    # 与「成果」同一口径：一步没跑出来过的任务没有成品可言（历次都在检出等
    # 前置环节失败时，工作目录里的变动是并行活动的噪音）
    run = store.get_run(run_id) or {}
    if not store.task_step_count(run.get("task_id") or ""):
        return {"ok": False, "reason": "no_steps"}
    avail = [f for f in (files or [])
             if _ext(f["name"]) in PREVIEW_EXT
             and int(f.get("size") or 0) <= MAX_FILE]
    entry = _pick_entry(avail)
    if not entry:
        return {"ok": False, "reason": "no_html", "workdir": wd}
    edir = entry.rsplit("/", 1)[0] if "/" in entry else ""
    tabs = [f for f in avail
            if _ext(f["name"]) in TAB_EXT
            and (f["name"].rsplit("/", 1)[0] if "/" in f["name"] else "") == edir]
    tabs.sort(key=_tab_key)
    names, seen = [], set()
    for f in tabs:
        if f["name"] in seen:
            continue
        seen.add(f["name"])
        names.append(f["name"])
        if len(names) >= MAX_TABS:
            break
    # 入口一定在，且一定排第一（页签条第一格就是"这个应用"，运行视图另占一格）
    names = [entry] + [n for n in names if n != entry]
    return {"ok": True, "entry": entry, "workdir": wd,
            "base": preview_path(run_id, tok),
            "files": [{"name": n, "ext": _ext(n).lstrip(".")} for n in names]}
