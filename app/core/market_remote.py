# -*- coding: utf-8 -*-
"""外部插件目录（market_remote）：把公开生态的市场清单接入插件市场。

分工：market.py 负责安装机制（避让/标记/记账），本模块只做三件事：
  1) 拉取：从公开市场清单（marketplace.json，ZCode CDN 与 Anthropic 生态同一份
     格式契约）拉目录，缓存到 data/market_remote/<source_id>.json。只在用户点
     「拉取更新」时联网，页面加载只读缓存，绝不后台偷跑；
  2) 甄别：目录阶段先按元数据做「不适配」预分类（含 MCP/钩子/命令组件的灰显）；
     安装阶段下载插件包（zip+sha256 或 git 子目录）后做权威的「纯技能类」白名单
     检查——技能文本会被注入给智能体当守则，任何可执行件（脚本/钩子/MCP 配置）
     都等于供应链注入面，一律拒绝；
  3) 落地：把 skills/*/SKILL.md 重写 frontmatter（加 market 安装标记）转成
     skillpack，复用 market.install_files 的安装通道（避让、记账、卸载全沿用）。

网络边界（安全约束）：仅 https；请求前解析 host 并拒绝环回/私有/保留地址；
响应体、解包总量、文本体量都有上限，防炸弹。清单格式两家略有差异（zcode 是
zip 直链，Anthropic 生态是 git 仓库子目录），解析时归一成同一种条目。
"""
from __future__ import annotations

import hashlib
import io
import os
import ipaddress
import json
import re
import shutil
import socket
import subprocess
import tarfile
import tempfile
import time
import urllib.error
import urllib.request
import zipfile
from pathlib import Path
from urllib.parse import urlparse

from . import market

_LOCK = market._LOCK   # 安装/记账与 market 共用一把锁，避免交叉写 market.json

# ---------------------------------------------------------------- 目录来源

# 公开市场清单；urls 按序试（raw.githubusercontent 在部分网络不可达，镜像优先）。
# kind: marketplace=标准 marketplace.json；clawhub=ClawHub 注册表（列表+trending
# 合成目录，zip 直下）。repo=条目用相对路径指子目录时，克隆这个仓库。
SOURCES = [
    {"id": "zcode", "name": "ZCode 官方", "kind": "marketplace",
     "urls": ["https://cdn-zcode.z.ai/zcode/official-plugin/marketplace.json"]},
    {"id": "anthropic", "name": "Anthropic 生态", "kind": "marketplace",
     "urls": [
         "https://cdn.jsdelivr.net/gh/anthropics/claude-plugins-official@main/.claude-plugin/marketplace.json",
         "https://raw.githubusercontent.com/anthropics/claude-plugins-official/main/.claude-plugin/marketplace.json",
     ]},
    {"id": "anthropic-skills", "name": "Anthropic 官方技能", "kind": "marketplace",
     "repo": "https://github.com/anthropics/skills.git",
     "urls": [
         "https://cdn.jsdelivr.net/gh/anthropics/skills@main/.claude-plugin/marketplace.json",
         "https://raw.githubusercontent.com/anthropics/skills/main/.claude-plugin/marketplace.json",
     ]},
    {"id": "claude-skills", "name": "社区技能库（Codex/Gemini 兼容）", "kind": "marketplace",
     "repo": "https://github.com/alirezarezvani/claude-skills.git",
     "urls": [
         "https://cdn.jsdelivr.net/gh/alirezarezvani/claude-skills@main/.claude-plugin/marketplace.json",
         "https://raw.githubusercontent.com/alirezarezvani/claude-skills/main/.claude-plugin/marketplace.json",
     ]},
    {"id": "clawhub", "name": "ClawHub（OpenClaw 生态）", "kind": "clawhub",
     "urls": ["https://clawhub.ai/api/v1/skills?limit=50",
              "https://clawhub.ai/api/v1/trending"]},
    {"id": "cocoloop", "name": "CocoLoop 技能商店", "kind": "cocoloop",
     "urls": ["https://api.cocoloop.cn/api/v1/store/skills",
              "https://api.cocoloop.com/api/v1/store/skills"]},
]

# 外部目录视图：服务端对全量缓存过滤后分页下发（缓存全在本地，翻页零成本）。
# 单页默认 60，UI 滚动到底自动续下一页。
_PAGE_LIMIT = 60

_CLAWHUB_BASE = "https://clawhub.ai/api/v1"
_CLAWHUB_PAGES = 4        # 技能列表最多翻页数（50/页，覆盖最新 ~200 个）
_CLAWHUB_TRENDING = 20    # trending 榕入目录的条数

_COLOLOOP_PAGES = 3       # CocoLoop 商店最多翻页数（50/页，覆盖热门 ~150 个）
_COLOLOOP_PAGE_SIZE = 50

SOURCES_BY_ID = {s["id"]: s for s in SOURCES}

# 体量上限（防炸弹）：清单 5MB、插件包下载 80MB、解包总量 120MB、文件数 500、
# 单文本文件 512KB、转成 skillpack 的文本总量 4MB
_CAP_MANIFEST = 5 * 1024 * 1024
_CAP_DOWNLOAD = 80 * 1024 * 1024
_CAP_UNPACKED = 120 * 1024 * 1024
_CAP_FILES = 500
_CAP_FILE_TEXT = 512 * 1024
_CAP_TOTAL_TEXT = 4 * 1024 * 1024


def _cache_dir():
    """清单缓存目录：随 paths.DATA_DIR 现取（测试重定向后自动跟随）。"""
    return market.paths.DATA_DIR / "market_remote"


def _cache_file(source_id):
    return _cache_dir() / ("%s.json" % source_id)


# ---------------------------------------------------------------- 网络边界

def assert_public_url(url):
    """SSRF 防护：仅 https；host 解析出的所有 IP 都不得是环回/私有/保留地址。
    返回 (host, port)，不合法直接抛 ValueError。"""
    u = urlparse(url or "")
    if u.scheme != "https":
        raise ValueError("仅允许 https 地址: %s" % url)
    host = u.hostname
    if not host:
        raise ValueError("URL 缺少主机名: %s" % url)
    if u.port is not None and not (0 < u.port < 65536):
        raise ValueError("端口非法: %s" % url)
    port = u.port or 443
    infos = socket.getaddrinfo(host, port, proto=socket.IPPROTO_TCP)
    if not infos:
        raise ValueError("主机无法解析: %s" % host)
    for info in infos:
        ip = ipaddress.ip_address(info[4][0])
        if (ip.is_loopback or ip.is_private or ip.is_link_local
                or ip.is_reserved or ip.is_multicast or ip.is_unspecified):
            raise ValueError("拒绝非公网地址 %s（host=%s）" % (ip, host))
    return host, port


def _fetch(url, cap=_CAP_MANIFEST):
    """带体量上限的 https GET；返回 bytes。

    两段式网络策略：先走默认通道（Windows 上 urllib 会读注册表系统代理，
    依赖代理上网的环境靠它），失败再自动直连重试一次（实测本机代理对部分
    CDN 文件返回 404/篡改，直连正常；反过来需要代理的网络第一段就成功）。
    体量超限属确定性错误，不重试。"""
    assert_public_url(url)
    req = urllib.request.Request(url, headers={
        "User-Agent": "CodeBee-Market/1.0",
        "Accept": "*/*",
    })

    def _read(open_fn):
        with open_fn() as resp:
            chunks, total = [], 0
            while True:
                chunk = resp.read(256 * 1024)
                if not chunk:
                    break
                total += len(chunk)
                if total > cap:
                    raise ValueError("响应超过体量上限（%d MB）: %s" % (cap // 1048576, url))
                chunks.append(chunk)
        return b"".join(chunks)

    try:
        return _read(lambda: urllib.request.urlopen(req, timeout=30))   # 默认 opener：含系统代理
    except (urllib.error.HTTPError, urllib.error.URLError):
        # 直连重试：ProxyHandler({}) 显式清空代理
        opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
        return _read(lambda: opener.open(req, timeout=30))


# ---------------------------------------------------------------- 清单解析

def _pick_i18n(entry, *keys):
    """zcode 条目的 *_i18n.zh-CN 优先，缺省回落同名字段。"""
    for k in keys:
        i18n = entry.get(k + "_i18n") or {}
        if isinstance(i18n, dict) and i18n.get("zh-CN"):
            return str(i18n["zh-CN"]).strip()
        if entry.get(k):
            return str(entry[k]).strip()
    return ""


def _normalize(source_meta, entry):
    """各家清单条目 → 统一形态；id 直接用 market 记账 id（remote-<源>-<名>），
    同时是安装标记 market_id（标记字符集只允许 [A-Za-z0-9_.-]，正好兼容）。"""
    name = str(entry.get("name") or "").strip()
    if not name:
        return None
    src = entry.get("source")
    if isinstance(src, dict):
        src_d = dict(src)
    else:
        # 仓库相对路径（"./engineering" 或 "./"）：克隆清单所属仓库取子目录；
        # entry.skills 显式列出本插件包含的技能目录（安装时按名过滤）
        src_d = {"source": "repo-relative", "path": str(src or "./")}
    if src_d.get("type") == "zip" and src_d.get("url"):
        install = {"kind": "zip", "url": str(src_d["url"]),
                   "sha256": str(src_d.get("sha256") or ""), "path": str(src_d.get("path") or "")}
    elif src_d.get("source") == "git-subdir" and src_d.get("url"):
        install = {"kind": "git", "url": str(src_d["url"]),
                   "sha256": "", "path": str(src_d.get("path") or ""),
                   "ref": str(src_d.get("ref") or "")}
    elif src_d.get("source") == "repo-relative":
        if not source_meta.get("repo"):
            install = {"kind": "unsupported"}
        else:
            install = {"kind": "git", "url": source_meta["repo"], "sha256": "",
                       "path": str(src_d.get("path") or "").lstrip("./"),
                       "ref": "",
                       "skills": _skill_names(entry.get("skills"))}
    elif src_d.get("source") == "clawhub":
        install = {"kind": "clawhub", "slug": str(src_d.get("slug") or ""),
                   "reference": str(src_d.get("reference") or ""),
                   "url": src_d.get("url") or ""}
    elif src_d.get("source") == "cocoloop":
        # CocoLoop 商店：download_url 是 zip 直链（同 zcode 的 zip 来源，
        # 但清单里没有 sha256，靠 https 传输 + 解包后的纯技能白名单兜底）
        url = str(src_d.get("url") or "").strip()
        install = ({"kind": "zip", "url": url, "sha256": "", "path": ""}
                   if url else {"kind": "unsupported"})
    else:
        install = {"kind": "unsupported"}
    author = entry.get("author") or {}
    if isinstance(author, dict):
        author = author.get("name") or ""
    return {
        "id": "remote-%s-%s" % (source_meta["id"], re.sub(r"[^A-Za-z0-9_.-]", "-", name).strip("-")),
        "name": name,
        "title": _pick_i18n(entry, "displayName") or name,
        "desc": _pick_i18n(entry, "description"),
        "category": str(entry.get("category") or "").strip(),
        "author": str(author or "").strip(),
        "version": str(entry.get("version") or "").strip(),
        "homepage": str(entry.get("homepage") or "").strip(),
        "keywords": [str(k).lower() for k in (entry.get("keywords") or []) if isinstance(k, str)],
        "source_id": source_meta["id"],
        "source_name": source_meta["name"],
        "install": install,
    }


def _skill_names(skills_list):
    """entry.skills 的 ["./skills/xlsx", ...] → 技能目录名 ["xlsx"]。"""
    out = []
    for s in skills_list or []:
        parts = [p for p in str(s).replace("\\", "/").split("/") if p and p != "."]
        if parts:
            out.append(parts[-1])
    return out


# 不适配预分类：只看元数据给 UI 灰显（权威判定在安装时逐文件检查）。
# 宽松些没关系——灰显条目装不了只是少个入口，误灰可点不进来但安装期还会拦。
_MCP_RE = re.compile(r"\bMCP\b")
_HOOK_RE = re.compile(r"\bhooks?\b", re.IGNORECASE)
_CMD_WORDS = {"commands", "slash-commands", "hooks", "mcp", "scripts"}


def _compat_block(entry):
    """返回 None=预检通过，或不适配原因字符串。
    剥离式安装后，脚本/钩子/MCP 组件在安装时自动剔除，不再作为灰显依据——
    只有来源类型本身不支持（无法下载）才预灰显。"""
    if entry["install"]["kind"] == "unsupported":
        return "来源类型不支持（仅支持 zip 直链、git 子目录与 ClawHub）"
    return None


def _load_cache():
    """读全部来源缓存 → {source_id: {"fetched_at","url","catalog"(原始 dict)}}。"""
    out = {}
    try:
        for p in _cache_dir().glob("*.json"):
            try:
                d = json.loads(p.read_text(encoding="utf-8"))
            except Exception:
                continue
            if isinstance(d, dict) and d.get("catalog"):
                out[p.stem] = d
    except OSError:
        pass
    return out


def _entries_from_cache(cache=None):
    """缓存清单 → 统一条目列表（附 installed / compat）。"""
    cache = cache if cache is not None else _load_cache()
    src_meta = {s["id"]: s for s in SOURCES}
    installed = market.installed_ids()
    entries = []
    for sid in [s["id"] for s in SOURCES]:
        c = cache.get(sid)
        if not c:
            continue
        sm = src_meta.get(sid) or {"id": sid, "name": sid}
        for raw in (c["catalog"].get("plugins") or []):
            e = _normalize(sm, raw if isinstance(raw, dict) else {})
            if not e:
                continue
            e["compat"] = "blocked" if _compat_block(e) else "ok"
            e["block_reason"] = _compat_block(e) or ""
            e["installed"] = e["id"] in installed
            e["installable"] = e["compat"] == "ok"
            entries.append(e)
    entries.sort(key=lambda x: (x["source_id"], x["name"].lower()))
    return entries


def view(offset=0, limit=_PAGE_LIMIT, source=None, q=None):
    """外部目录视图（服务端过滤 + 分页）。缓存全在本地，对全量条目过滤再切页
    零成本——搜索/来源筛选不再受「已加载页」限制，滚动翻页逛完全部目录。
    返回 {sources, entries(本页), total(过滤后全量), offset, has_more,
    categories}。"""
    cache = _load_cache()
    entries = _entries_from_cache(cache)
    sources = []
    for s in SOURCES:
        c = cache.get(s["id"]) or {}
        sources.append({"id": s["id"], "name": s["name"],
                        "fetched_at": c.get("fetched_at") or "",
                        "count": len((c.get("catalog") or {}).get("plugins") or [])})
    cats = []
    for e in entries:
        if e["category"] and e["category"] not in cats:
            cats.append(e["category"])
    if source:
        entries = [e for e in entries if e["source_id"] == source]
    qq = str(q or "").strip().lower()
    if qq:
        entries = [e for e in entries if qq in (e["name"] or "").lower()
                   or qq in (e["title"] or "").lower()
                   or qq in (e["desc"] or "").lower()]
    offset = max(0, int(offset or 0))
    limit = max(1, min(int(limit or _PAGE_LIMIT), 500))
    page = entries[offset:offset + limit]
    return {"sources": sources, "entries": page, "total": len(entries),
            "offset": offset, "has_more": offset + limit < len(entries),
            "categories": cats}


def _fetch_clawhub_catalog():
    """ClawHub 注册表 → 合成标准 plugins 目录：最新技能列表翻几页 +
    trending 榜（打 trending 关键词），按 slug 去重。"""
    seen, plugins = set(), []

    def add(item, extra_keywords=()):
        slug = str(item.get("slug") or "").strip()
        if not slug or slug in seen:
            return
        seen.add(slug)
        stats = item.get("stats") or {}
        latest = item.get("latestVersion") or {}
        tags = item.get("tags") or {}
        # reference：合成条目带在 source 里，原生 skills 列表带在 install 里
        ref = (str((item.get("source") or {}).get("reference") or "")
               or str((item.get("install") or {}).get("reference") or "")
               or slug)
        plugins.append({
            "name": slug,
            "displayName": str(item.get("displayName") or slug),
            "description": str(item.get("summary") or item.get("description")
                               or item.get("displayName") or slug)[:400],
            "version": str(tags.get("latest") or latest.get("version") or ""),
            "keywords": [str(t) for t in (item.get("topics") or [])][:8] + list(extra_keywords),
            "stats": {"downloads": stats.get("downloads")},
            "source": {"source": "clawhub", "slug": slug, "reference": ref},
        })

    url = SOURCES_BY_ID["clawhub"]["urls"][0]
    cursor = ""
    for _page in range(_CLAWHUB_PAGES):
        page_url = url + ("&cursor=" + cursor if cursor else "")
        d = json.loads(_fetch(page_url).decode("utf-8", errors="replace"))
        for it in (d.get("items") or []):
            add(it if isinstance(it, dict) else {})
        cursor = str(d.get("nextCursor") or "")
        if not cursor:
            break
    try:
        d = json.loads(_fetch(SOURCES_BY_ID["clawhub"]["urls"][1]).decode("utf-8", errors="replace"))
        for it in (d.get("items") or [])[:_CLAWHUB_TRENDING]:
            if isinstance(it, dict):
                ref = str((it.get("install") or {}).get("reference") or "")
                add({"slug": ref.split("/")[-1] if ref else "",
                     "displayName": it.get("displayName"),
                     "summary": it.get("summary"),
                     "source": {"source": "clawhub", "slug": ref.split("/")[-1] if ref else "",
                                "reference": ref}}, ["trending"])
    except Exception:   # trending 拉不到不影响主列表
        pass
    return {"name": "clawhub", "description": "ClawHub skills", "plugins": plugins}


def _fetch_cocoloop_catalog():
    """CocoLoop 商店（api.cocoloop.cn）→ 合成标准 plugins 目录。
    按下载量排序翻几页取热门技能；安全等级（S+/S/A…）榕进 keywords 供 UI 展示。
    api.cocoloop.com 会 302 到 .cn 域名，urllib 自动跟随，两镜像等效。"""
    base = SOURCES_BY_ID["cocoloop"]["urls"][0]
    seen, plugins = set(), []
    for page in range(1, _COLOLOOP_PAGES + 1):
        url = ("%s?page=%d&page_size=%d&sort=downloads"
               % (base, page, _COLOLOOP_PAGE_SIZE))
        d = json.loads(_fetch(url).decode("utf-8", errors="replace"))
        if not isinstance(d, dict) or d.get("code") not in (0, None):
            raise ValueError("CocoLoop 响应异常: %s" % (d.get("message") if isinstance(d, dict) else d))
        items = (d.get("data") or {}).get("items") or []
        for it in items:
            if not isinstance(it, dict):
                continue
            name = str(it.get("name") or "").strip()
            dl = str(it.get("download_url") or "").strip()
            if not name or not dl or name in seen:
                continue
            seen.add(name)
            sec = str(it.get("security_level") or "").strip()
            keywords = [str(k) for k in (it.get("tags") or [])][:8] if isinstance(it.get("tags"), list) else []
            if sec:
                keywords.append("安全评级:%s" % sec.upper())
            plugins.append({
                "name": name,
                "displayName": str(it.get("subtitle") or "").strip() or name,
                "description": str(it.get("brief") or it.get("subtitle")
                                   or name)[:400],
                "author": {"name": str(it.get("author") or "").strip()},
                "version": "",
                "category": str(it.get("category") or "").strip(),
                "keywords": keywords,
                "stats": {"downloads": str(it.get("downloads") or "")},
                "source": {"source": "cocoloop", "url": dl},
            })
        total = int((d.get("data") or {}).get("pages") or 0)
        if page >= max(total, 1):
            break
    if not plugins:
        raise ValueError("CocoLoop 目录为空")
    return {"name": "cocoloop", "description": "CocoLoop skills", "plugins": plugins}


def refresh(source_id=None):
    """拉取目录清单（全部或指定来源），成功即写缓存。返回 view()。"""
    results = []
    for s in SOURCES:
        if source_id and s["id"] != source_id:
            continue
        err, raw, used = None, None, ""
        if s["kind"] == "clawhub":
            try:
                raw = _fetch_clawhub_catalog()
                used = s["urls"][0]
            except Exception as e:
                err = "%s: %s" % ("clawhub.ai", e)
        elif s["kind"] == "cocoloop":
            try:
                raw = _fetch_cocoloop_catalog()
                used = s["urls"][0]
            except Exception as e:
                err = "%s: %s" % (urlparse(s["urls"][0]).hostname, e)
        else:
            for url in s["urls"]:
                try:
                    raw = json.loads(_fetch(url).decode("utf-8", errors="replace"))
                    used = url
                    break
                except Exception as e:   # 换下一个镜像
                    err = "%s: %s" % (url.split("/")[2], e)
        if raw is None or not isinstance(raw, dict):
            results.append({"id": s["id"], "ok": False, "error": err or "响应不是 JSON 对象"})
            continue
        plugins = raw.get("plugins")
        if not isinstance(plugins, list):
            results.append({"id": s["id"], "ok": False, "error": "清单缺 plugins 数组"})
            continue
        f = _cache_file(s["id"])
        f.parent.mkdir(parents=True, exist_ok=True)
        tmp = f.with_suffix(".tmp")
        tmp.write_text(json.dumps({
            "fetched_at": time.strftime("%Y-%m-%d %H:%M:%S"),
            "url": used,
            "catalog": {"name": raw.get("name") or s["id"],
                        "description": raw.get("description") or "",
                        "plugins": plugins},
        }, ensure_ascii=False), encoding="utf-8")
        tmp.replace(f)
        results.append({"id": s["id"], "ok": True, "count": len(plugins)})
    v = view()
    v["refresh"] = results
    return v


# ---------------------------------------------------------------- 纯技能类检查

# 目录黑名单：这些组件需要宿主执行环境或外部服务，本平台没有对应通道
_BLOCK_DIRS = {"scripts", "hooks", "commands", "agents"}
# 可执行扩展名（出现即拒）：技能文本会诱导智能体执行，等于供应链注入
_EXEC_EXT = {".py", ".sh", ".bash", ".js", ".mjs", ".cjs", ".ts", ".exe", ".bat",
             ".cmd", ".ps1", ".dll", ".so", ".dylib", ".bin", ".jar", ".php",
             ".rb", ".pl", ".lua", ".com", ".scr", ".vbs", ".wsf"}
# 允许的文本扩展（会被转成 skillpack 内容或随包资料）
_TEXT_EXT = {".md", ".markdown", ".txt", ".csv", ".json", ".yaml", ".yml"}
# 允许的图片扩展（随包资料；注入通道只带文本，图片仅落 assets）
_IMG_EXT = {".png", ".jpg", ".jpeg", ".gif", ".svg", ".webp", ".ico"}
# 无扩展名放行名单（仓库常见元文件）；其余无扩展名文件（多为二进制/可执行）拒收
_NO_EXT_OK = {".gitignore", ".gitattributes", ".editorconfig", ".npmignore",
              "license", "notice", "readme", "changelog", "codeowners", "authors"}


def _rel_parts(rel):
    return [p for p in rel.replace("\\", "/").split("/") if p not in ("", ".")]


def _is_junk(rel):
    """zip 常见垃圾：macOS 元数据等，直接忽略不算违规。"""
    parts = _rel_parts(rel)
    return (not parts or "__MACOSX" in parts
            or any(p in (".DS_Store", "Thumbs.db") for p in parts)
            or any(p.startswith("._") for p in parts))


def inspect_tree(root, whitelist=None):
    """对插件根目录做剥离式安全检查：可执行件与脚本/钩子/命令/agents 目录、
    MCP 配置不拒绝而是**剔除**（本平台不执行它们，它们只是供应链攻击面），
    纯技能内容（文本/图片）保留。白名单给定时（多插件同仓库的 repo-relative
    来源）范围收敛到白名单技能目录——同仓库其他插件的内容不参与。
    返回 (files [(rel, abs)], stripped [rel])；硬错误（文件数超限）抛 ValueError。"""
    files, stripped = [], []
    wl = set(whitelist or ())
    scanned = 0
    for p in sorted(Path(root).rglob("*")):
        if p.is_dir():
            continue
        scanned += 1
        if scanned > _CAP_FILES * 4:
            raise ValueError("文件数超过上限（%d）" % (_CAP_FILES * 4))
        rel = p.relative_to(root).as_posix()
        if _is_junk(rel):
            continue
        parts = _rel_parts(rel)
        if wl and not any(seg in wl for seg in parts[:-1]):
            continue   # 白名单外的内容不参与本插件的检查与安装
        if parts[0].lower() in _BLOCK_DIRS:
            stripped.append(rel)
            continue
        if parts[-1].lower() == ".mcp.json":
            stripped.append(rel)
            continue
        ext = Path(parts[-1]).suffix.lower()
        if (ext in _EXEC_EXT
                or (not ext and parts[-1].lower() not in _NO_EXT_OK)
                or (ext and ext not in _TEXT_EXT and ext not in _IMG_EXT)):
            stripped.append(rel)   # 可执行/未知类型一律剥离
            continue
        files.append((rel, p))
    if len(files) > _CAP_FILES:
        raise ValueError("文件数超过上限（%d）" % _CAP_FILES)
    return files, stripped


def find_skills(root, files):
    """从文件清单里找技能：任何 SKILL.md 都算一个技能（名=所在目录名），
    根布局 SKILL.md 记为 main——适配 skills/<名>/SKILL.md、社区库的
    <分类>/<名>/SKILL.md 与 ClawHub 的根布局等多种形态。"""
    skills = []
    for rel, _abs in files:
        parts = _rel_parts(rel)
        if parts[-1].lower() != "skill.md":
            continue
        if len(parts) == 1:
            skills.append(("main", rel))
        else:
            skills.append((parts[-2], rel))
    out = []
    for name, rel in skills:
        prefix = "/".join(_rel_parts(rel)[:-1]) + "/"
        extras = [(r, a) for r, a in files
                  if r.startswith(prefix) and _rel_parts(r)[-1].lower() != "skill.md"
                  and Path(r).suffix.lower() in _TEXT_EXT]
        out.append({"name": name, "skill_md": rel, "extras": extras})
    return out


# ---------------------------------------------------------------- 下载

def _safe_extract(zf, dest):
    """防炸弹解包：先校验成员名与总量，再逐成员手动写出（不用 extractall——
    Windows 打包的 zip 成员名常带反斜杠 fork_core\\x.py，extractall 按「/」
    建父目录在 Windows 上会 FileNotFoundError）。落盘三重防线：段字符白名单、
    resolve 收容校验、落点复查。"""
    members = []
    total, names = 0, 0
    for zi in zf.infolist():
        if zi.is_dir():
            continue
        name = zi.filename.replace("\\", "/")
        if name.startswith("/") or ":" in name.split("/")[0] or ".." in _rel_parts(name):
            raise ValueError("zip 内路径可疑: %s" % zi.filename)
        total += zi.file_size
        names += 1
        if total > _CAP_UNPACKED:
            raise ValueError("解包总量超过上限")
        if names > _CAP_FILES * 4:
            raise ValueError("zip 内文件数过多")
        members.append((zi, name))
    dest = Path(dest)
    dest.mkdir(parents=True, exist_ok=True)
    base = dest.resolve()
    seg_ok = re.compile(r"^\.{0,2}[A-Za-z0-9_][A-Za-z0-9_. ()\[\]-]*$")
    for zi, name in members:
        parts = _rel_parts(name)
        if not parts or any(seg in ("..", ".") or not seg_ok.match(seg) for seg in parts):
            raise ValueError("zip 内路径可疑: %s" % zi.filename)
        p = Path(os.path.join(dest, *parts)).resolve()
        if base != p and base not in p.parents:
            raise ValueError("zip 内路径可疑: %s" % zi.filename)
        p.parent.mkdir(parents=True, exist_ok=True)
        with zf.open(zi) as src:
            p.write_bytes(src.read())
    for p in dest.rglob("*"):
        rp = p.resolve()
        if base != rp and base not in rp.parents:
            raise ValueError("解包落点越界: %s" % p)
    return dest


def _download_zip(entry, tmp):
    """下载 zip（校验 sha256）→ 安全解包 → 返回插件根目录。"""
    data = _fetch(entry["install"]["url"], cap=_CAP_DOWNLOAD)
    want = (entry["install"].get("sha256") or "").lower()
    if want:
        got = hashlib.sha256(data).hexdigest()
        if got != want:
            raise ValueError("sha256 校验不符（期望 %s，实际 %s）" % (want[:12], got[:12]))
    dest = _safe_extract(zipfile.ZipFile(io.BytesIO(data)), tmp / "unzip")
    return _plugin_root(dest)


def _gh_split(url):
    """github 仓库地址 → (owner, repo)；非 github 地址返回 None。"""
    m = re.match(r"(?i)^https://[^/]*github\.com/([A-Za-z0-9_.-]+)/([A-Za-z0-9_.-]+?)(\.git)?/?$",
                 str(url or "").strip())
    return (m.group(1), m.group(2)) if m else None


def _safe_extract_tar(data, dest):
    """防炸弹 tar.gz 解包：成员名校验（拒绝绝对路径/穿越/盘符/链接）、体量上限；
    逐成员以字节写出（不用 extractall，绕开链接与权限面）。返回解包目录。"""
    dest = Path(dest)
    dest.mkdir(parents=True, exist_ok=True)
    tf = tarfile.open(fileobj=io.BytesIO(data), mode="r:gz")
    total = 0
    with tf:
        members = [m for m in tf.getmembers() if m.isfile() and not (m.issym() or m.islnk())]
        if len(members) > _CAP_FILES * 4:
            raise ValueError("包内文件数过多")
        # codeload 形态：所有成员共享同一个顶层目录段（<repo>-<ref>）→ 剥掉它；
        # 不是统一包裹的 tar 就原样保留
        firsts = {_rel_parts(m.name)[0] for m in members if len(_rel_parts(m.name)) > 1}
        strip_top = len(firsts) == 1 and all(len(_rel_parts(m.name)) > 1 for m in members)
        for m in members:
            name = m.name.replace("\\", "/")
            if name.startswith("/") or ":" in name.split("/")[0] or ".." in _rel_parts(name):
                raise ValueError("包内路径可疑: %s" % m.name)
            total += m.size
            if total > _CAP_UNPACKED:
                raise ValueError("解包总量超过上限")
            parts = _rel_parts(name)
            rel = "/".join(parts[1:]) if (strip_top and len(parts) > 1) else name
            if not rel:
                continue
            src = tf.extractfile(m)
            if src is None:
                continue
            p = dest / rel
            p.parent.mkdir(parents=True, exist_ok=True)
            p.write_bytes(src.read())
    return dest


def _gh_tarball(entry, tmp):
    """git 类来源的主通道：codeload.github.com 的 tar.gz 单请求拉整树。
    完整、与上游最新一致（jsdelivr 的分支快照会滞后，树索引甚至列已删文件）。
    _fetch 自带「系统代理优先→直连重试」，覆盖 git 协议被干扰的网络。"""
    gh = _gh_split(entry["install"].get("url"))
    if not gh:
        raise ValueError("not-github")
    owner, repo = gh
    ref = str(entry["install"].get("ref") or "").strip()
    tries = (["refs/heads/%s" % ref, "refs/tags/%s" % ref] if ref else ["HEAD"])
    last = None
    for spec in tries:
        url = "https://codeload.github.com/%s/%s/tar.gz/%s" % (owner, repo, urllib.parse.quote(spec, safe="/"))
        try:
            data = _fetch(url, cap=_CAP_DOWNLOAD)
        except (urllib.error.HTTPError, urllib.error.URLError) as e:
            last = e
            continue
        try:
            dest = _safe_extract_tar(data, tmp / "tarball")
        except tarfile.TarError as e:   # 不是有效 tar.gz（被劫持/截断/空响应）
            raise ValueError("响应不是有效的 tar.gz: %s" % e)
        # 顶层包裹段已在解包时剥掉，dest 即仓库根；按条目 path 取子目录
        root = dest
        prefix = (entry["install"].get("path") or "").strip("/")
        if prefix:
            root = dest / prefix
            if dest.resolve() not in root.resolve().parents:
                raise ValueError("插件子目录路径可疑: %s" % prefix)
        if not root.is_dir():
            raise ValueError("插件子目录不存在: %s" % prefix)
        return root
        return root
    raise ValueError("codeload 拉取失败: %s/%s（%s）" % (owner, repo, last))


def _gh_fetch_files(entry, tmp):
    """git 类来源的 jsdelivr 文件级兜底通道：目录树走 data.jsdelivr，
    逐文件走 cdn.jsdelivr，返回插件根目录。注意 jsdelivr 的分支快照可能
    滞后于上游（树列着已删文件、新文件 301 到被墙的 raw），单文件缺失跳过、
    缺失过多判坏包；仅支持 github 仓库。"""
    gh = _gh_split(entry["install"].get("url"))
    if not gh:
        raise ValueError("not-github")   # 调用方据此回落 git clone
    owner, repo = gh
    prefix = (entry["install"].get("path") or "").strip("/")
    prefix = prefix + "/" if prefix else ""
    refs = []
    r0 = str(entry["install"].get("ref") or "").strip()
    if r0:
        refs.append(r0)
    refs += ["main", "master"]
    last_err = None
    for ref in refs:
        try:
            tree = json.loads(_fetch(
                "https://data.jsdelivr.com/v1/packages/gh/%s/%s@%s?structure=flat"
                % (owner, repo, urllib.parse.quote(ref, safe="")),
                cap=_CAP_MANIFEST).decode("utf-8", errors="replace"))
        except Exception as e:
            last_err = e
            continue
        files = [f for f in (tree.get("files") or [])
                 if isinstance(f, dict) and not f.get("is_dir")
                 and str(f.get("name") or "").startswith("/" + prefix)]
        if not files:
            continue   # 该 ref 拉不到或子目录不存在，换下一个 ref
        total = sum(int(f.get("size") or 0) for f in files)
        if total > _CAP_UNPACKED or len(files) > _CAP_FILES * 4:
            raise ValueError("插件体量超上限（%d 文件 / %d MB）" % (len(files), total // 1048576))
        dest = tmp / "gh"
        skipped = 0
        for f in files:
            rel = str(f["name"]).lstrip("/")
            if int(f.get("size") or 0) > _CAP_FILE_TEXT * 8:
                continue   # 单文件超大的（多为二进制产物）跳过，检查器会兜底
            try:
                data = _fetch("https://cdn.jsdelivr.net/gh/%s/%s@%s%s"
                              % (owner, repo, urllib.parse.quote(ref, safe=""),
                                 urllib.parse.quote("/" + rel)),
                              cap=_CAP_FILE_TEXT * 8)
            except urllib.error.HTTPError:
                # jsdelivr 的目录树索引与 CDN 缓存偶有不同步（列了但取不到），
                # 单文件缺失跳过；缺得过多按坏包处理
                skipped += 1
                continue
            p = dest / rel
            p.parent.mkdir(parents=True, exist_ok=True)
            p.write_bytes(data)
        if skipped > max(3, len(files) // 5):
            raise ValueError("jsdelivr 文件缺失过多（%d/%d），包不完整" % (skipped, len(files)))
        root = (dest / prefix[:-1]).resolve() if prefix else dest.resolve()
        if dest.resolve() != root and dest.resolve() not in root.parents:
            raise ValueError("插件子目录路径可疑: %s" % prefix)
        return root
    raise ValueError("jsdelivr 拉取失败: %s/%s（%s）" % (owner, repo, last_err))


def _download_git(entry, tmp):
    """git 浅克隆取子目录（Anthropic 生态的 git-subdir 来源）。"""
    if shutil.which("git") is None:
        raise ValueError("本机没有 git，无法安装 git-subdir 来源的插件")
    inst = entry["install"]
    dst = tmp / "git"
    sub = str(inst.get("path") or "").replace("\\", "/")
    if sub.startswith("/") or ":" in sub or ".." in _rel_parts(sub):
        raise ValueError("插件子目录路径可疑: %s" % inst.get("path"))
    cmd = ["git", "clone", "--depth", "1", "--single-branch", "--quiet"]
    if inst.get("ref"):
        cmd += ["--branch", inst["ref"]]
    cmd += [inst["url"], str(dst)]
    try:
        proc = subprocess.run(cmd, capture_output=True, timeout=300,
                              text=True, encoding="utf-8", errors="replace")
    except subprocess.TimeoutExpired:
        raise ValueError("git 克隆超时: %s" % inst["url"])
    if proc.returncode != 0:
        raise ValueError("git 克隆失败: %s" % (proc.stderr or "").strip()[-200:])
    root = dst / sub if sub else dst
    # 双保险：解析后必须仍在克隆目录内（防符号链接等绕过）
    if dst.resolve() != root.resolve() and dst.resolve() not in root.resolve().parents:
        raise ValueError("插件子目录路径可疑: %s" % inst.get("path"))
    if not root.is_dir():
        raise ValueError("插件子目录不存在: %s" % inst["path"])
    return root


def _download_git_any(entry, tmp):
    """git 类来源安装：codeload tarball 优先（完整、最新）→ jsdelivr 逐文件
    兜底（codeload 不可达时）→ git 浅克隆收尾（非 github 托管 / 精确 ref）。"""
    try:
        return _gh_tarball(entry, tmp)
    except ValueError as e:
        if "not-github" in str(e):
            return _download_git(entry, tmp)
        first = str(e)
    try:
        return _gh_fetch_files(entry, tmp)
    except ValueError as e:
        if "not-github" in str(e):
            return _download_git(entry, tmp)
        raise ValueError("%s；%s" % (first, e))


def _download_clawhub(entry, tmp):
    """ClawHub 注册表 zip 直下（/api/v1/download?slug=&reference=）。"""
    inst = entry["install"]
    q = urllib.parse.quote
    url = "%s/download?slug=%s&reference=%s" % (
        _CLAWHUB_BASE, q(inst["slug"], safe=""), q(inst["reference"] or inst["slug"], safe=""))
    data = _fetch(url, cap=_CAP_DOWNLOAD)
    dest = _safe_extract(zipfile.ZipFile(io.BytesIO(data)), tmp / "clawhub")
    return _plugin_root(dest)


def _plugin_root(base):
    """定位插件根：有 plugin.json 标记就用；否则单一顶层目录时下钻。"""
    for marker in (".claude-plugin/plugin.json", ".zcode-plugin/plugin.json",
                   "plugin.json"):
        if (base / marker).is_file():
            return base
    entries = list(base.iterdir())
    dirs = [p for p in entries if p.is_dir()]
    if len(dirs) == 1 and not any(p.is_file() for p in entries):
        return _plugin_root(dirs[0])
    return base


# ---------------------------------------------------------------- 转换与安装

def _read_text(p):
    try:
        if p.stat().st_size > _CAP_FILE_TEXT:
            return None
        return p.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return None


def _one_line(s, cap=100):
    s = re.sub(r"\s+", " ", str(s or "")).strip()
    return s[:cap]


def _rewrite_skill_md(content, title, note, pack_id):
    """SKILL.md → skillpack：重写 frontmatter（带 market 安装标记），正文原样保留。"""
    body = content
    m = re.match(r"(?s)\A---\s*\n(.*?)\n---\s*\n?", content)
    if m:
        body = content[m.end():]
        fm = m.group(1)
        m_name = re.search(r"(?m)^name:\s*(.+)$", fm)
        m_desc = re.search(r"(?m)^description:\s*(.+)$", fm)
        title = _one_line(m_name.group(1)) if m_name else title
        note = _one_line(m_desc.group(1)) if m_desc else note
    head = ('---\nname: %s\nnote: %s\nscopes:\n- "*"\nsource: market\n'
            "market_id: %s\n---\n\n" % (_one_line(title, 60), _one_line(note), pack_id))
    return head + body.lstrip("\r\n")


def build_files(entry, root):
    """插件树 → market.install_files 的 files dict：
    主 SKILL.md 落顶层（注入件），其余文本附件落 market-assets/<id>/ 原相对路径。
    条目显式声明了 skills 白名单（如 anthropics/skills 的 skills: [...]）时，
    检查与打包都只看白名单技能目录——同仓库其他插件的内容不越界。
    返回 (files dict, 错误, stripped 剔除清单)。"""
    whitelist = (entry.get("install") or {}).get("skills") or []
    try:
        files, stripped = inspect_tree(root, whitelist=whitelist or None)
    except ValueError as e:
        return None, str(e), []
    skills = find_skills(root, files)
    if not skills:
        return None, "未找到技能（该插件剔除脚本/钩子/MCP 组件后没有纯技能内容）", stripped
    if whitelist:
        skills = [s for s in skills if s["name"] in set(whitelist)]
        if not skills:
            return None, "白名单技能与包内容不匹配: %s" % ",".join(whitelist[:5]), stripped
    pack_id = entry["id"]
    out, total = {}, 0
    for i, sk in enumerate(skills):
        content = _read_text(root / sk["skill_md"])
        if not content or not content.strip():
            continue
        if i == 0:
            key = "market-%s.md" % pack_id
        else:
            key = "market-%s__%s.md" % (pack_id, re.sub(r"[^A-Za-z0-9_.-]", "-", sk["name"]))
        out[key] = _rewrite_skill_md(content, entry["title"], entry["desc"], pack_id)
        total += len(out[key])
        for rel, abs_p in sk["extras"]:
            text = _read_text(abs_p)
            if text is None:      # 超限/读不到的附件直接舍弃（图片本就不带）
                continue
            total += len(text)
            if total > _CAP_TOTAL_TEXT:
                return None, "插件文本总量超过上限（%d KB）" % (_CAP_TOTAL_TEXT // 1024), stripped
            out[rel] = text
    if not out:
        return None, "技能内容为空", stripped
    return out, None, stripped


def install_remote(entry_id):
    """安装外部目录插件：下载 → 纯技能检查 → 转换 → 复用 market 安装通道。
    返回 (结果 dict, 错误)。"""
    entries = _entries_from_cache()
    entry = next((e for e in entries if e["id"] == entry_id), None)
    if not entry:
        return None, "外部目录中没有这个插件（先「拉取更新」再试）: %s" % entry_id
    if entry["compat"] == "blocked":
        return None, entry["block_reason"] or "该插件不适配 CodeBee"
    if entry["install"]["kind"] == "unsupported":
        return None, "来源类型不支持（仅支持 zip 直链、git 子目录与 ClawHub）"
    tmp = Path(tempfile.mkdtemp(prefix="codebee-mkt-"))
    try:
        kind = entry["install"]["kind"]
        if kind == "zip":
            root = _download_zip(entry, tmp)
        elif kind == "clawhub":
            root = _download_clawhub(entry, tmp)
        else:
            root = _download_git_any(entry, tmp)
        files, blocked, stripped = build_files(entry, root)
        if blocked:
            return None, blocked
        res, err = market.install_files(entry_id, entry["title"], files, extra={
            "remote": {"source": entry["source_id"], "name": entry["name"],
                       "version": entry["version"], "homepage": entry["homepage"]},
        })
        if err:
            return None, err
        res["skills"] = sum(1 for k in files if k.endswith(".md") and "/" not in k)
        res["stripped"] = sorted(stripped)
        return res, None
    except ValueError as e:
        return None, str(e)
    except Exception as e:   # 网络/解包等意外错误也要兜成用户可读的一句话
        return None, "下载或解析插件失败: %s" % e
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def remove_remote(entry_id):
    """卸载外部插件：记账与标记齐全时走 market 既有卸载；否则报错。"""
    return market.remove(entry_id)
