# -*- coding: utf-8 -*-
"""禅道 Bug 自动修复对接：多产品档案 + 排查定责路由 + 转派流转。

数据落盘 <data>/zentao.json（tmp + os.replace 原子写；TUTTI_DATA 环境变量感知）。
修复走与 /api/tasks 完全相同的链路（store.create_task → store.create_run →
jobs.enqueue，code 引擎=实现→验证→评审→修复），不自造运行器。调度挂在
automation._tick（同 publish/auto.fire_due 模式），内部按 interval_minutes 节流。

禅道 REST API v1（开源版 15.x+；请求头 Token: <token>）：
  POST {base}/api.php/v1/tokens               {account, password} → {token}
  GET  {base}/api.php/v1/products/{pid}/bugs  分页 {bugs:[...], page, total, limit}
  GET  {base}/api.php/v1/bugs/{id}            单查（回写前确认状态防谎报）
  POST {base}/api.php/v1/bugs/{id}/resolve    {resolution, resolvedBuild, comment, assignedTo}
  PUT  {base}/api.php/v1/bugs/{id}            {assignedTo, comment}（转派/失败说明）
  GET  {base}/api.php/v1/products|users       产品/账号清单（前端下拉辅助）

地址自适应：根路径 tokens 404 时自动试 {base}/zentao/api.php/v1（官方一键安装包
默认子路径部署），探测成功缓存在 _APIBASE（key=用户填的原始地址）。

产品档案（product_profiles）：每产品一份 {指派过滤, 严重度, 我方端 our_sides,
后端/前端仓库(workdir/git_rev/verify_command), repo_hints, 负责人 owners,
模块路由 module_routes}。老版扁平配置（products+单仓库）load 时自动迁移。

排查（_triage）：模块路由按 bug.module 精确匹配优先 → AI 兜底（triage_ai 开时
modelhub 单次调用，bookmeta 同款配方）→ unknown。判定 side ∈
backend | frontend | both | not_ours | unknown。

流转规则（转派目标只认排查结论，与 bug 当前 assignedTo 无关——测试提错人也
照样改派）：
  我方端问题    → 建修复任务；成功=合并+resolve(fixed)+报告评论+指回报告人
  双端/我方一端 → 我方端修完（合并落库）后转派另一端负责人+评论，不 resolve
  纯对方端问题  → 不建任务，直接转派该端负责人+排查结论评论
  非我方        → 转派报告人（或 owners.not_ours）+评论；只转派不解决
  unknown       → 不碰 bug，need_manual + 群通知（下轮扫描 bug 仍激活则重排查）
  修复任务失败  → 评论尝试记录 + 转派该端负责人（模块路由 account > 端负责人）；
                  CodeBee 自身崩溃（内部异常）不转派——那是工具故障不是修不动，
                  只评论+群通知人工；负责人未配置同样只评论。
                  只评论也绝不盲写 PUT：禅道 PUT /bugs/{id} 缺 assignedTo 会
                  清空指派人，必须带回当前指派人（#27783 案）。

落库纪律：修复任务落单即带基线（档案配了用档案，没配取工作目录当前 HEAD）
走任务分支隔离，修完的代码自动提交在任务分支上，对账合并后才算落库；
resolve 前再验一遍——没基线的任务改动只在工作区，有未提交的已跟踪改动
不 resolve（转 done_manual 人工收口），auto_merge 关闭同理。
对账（_reconcile）认任务最新一次运行：人工重试换 run 不影响回写判断。

出网边界（SSRF 防护，_guard_url）：请求目标来自用户自配禅道地址——内网按设计
放行；强制 http(s)、解析主机并阻断云元数据/链路本地地址、禁跟随重定向。
"""
from __future__ import annotations

import base64
import html as _html
import hashlib
import io
import ipaddress
import json
import logging
import os
import re
import socket
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timedelta
from pathlib import Path

from . import jobs, operations, paths, settings, store, tlsctx

log = logging.getLogger(__name__)

_LOCK = threading.RLock()
_SCAN_LOCK = threading.Lock()  # 手动/定时扫描单飞，避免重复拉取和重复认领
_FILE = paths.DATA_DIR / "zentao.json"
_STATE = {
    "config": {},        # 持久配置（_CFG_DEFAULTS）
    "claims": {},        # str(bug_id) → claim dict（v2：含 triage/tasks）
    "last_scan": "",
    "next_scan": "",
    "last_error": "",
}
_LOADED = False
_BOOT_TIMER = None     # 启动补对账的兜底 Timer（测试里要能拿到并取消）

TOKEN_TTL = 23 * 3600
HTTP_TIMEOUT = 15
PAGE_LIMIT = 100
MAX_BUGS = 500
RESOLVE_MAX_ATTEMPTS = 3
RETRY_DELAY_MIN = 30
INTERVAL_MIN, INTERVAL_MAX = 5, 10080   # 扫描间隔（分钟）：5 分钟 ~ 7 天
INTERVAL_DEFAULT = 5
_LEGACY_INTERVAL_HOURS_DEFAULT = 2      # 老配置 interval_hours 缺省值（迁移用）

SIDES = ("backend", "frontend")
TRIAGE_SIDES = ("backend", "frontend", "both", "not_ours")   # unknown 单列
SIDE_CN = {"backend": "后端", "frontend": "前端"}

# 图片证据：#27754 案——「功能未对齐，具体如图」被纯文本排查脑补成前端样式问题。
# 排查/修复前先尽量把图抓到手；抓不到就按守卫留人工，绝不盲判。
_IMG_REF_RE = re.compile(r"如图|见图|截图|附图|下图|上图|图示|图片|图像|录屏|视频")
TRIAGE_MAX_IMAGES = 4          # 排查提示词最多随附几张图（请求体与 token 预算）
IMG_CACHE_TTL = 900            # bug 图片按 bug_id 短缓存（排查+建任务两处共用）
_IMG_CACHE = {}                # str(bug_id) → (ts, [(mime, b64)])；空结果也缓存防重复下载

_CFG_DEFAULTS = {
    "base_url": "",
    "account": "",
    "password": "",
    "product_profiles": [],    # 产品档案列表（_norm_profile 形状）
    "auto_resolve": True,
    "auto_merge": True,
    "triage_ai": True,         # 模块路由未命中时用 AI 兜底排查
    "poll_enabled": False,
    "interval_minutes": INTERVAL_DEFAULT,
}

_REPO_DEFAULTS = {"workdir": "", "git_rev": "", "verify_command": ""}

_PROFILE_DEFAULTS = {
    "product": 0,              # 产品 ID（必填唯一）
    "assigned_to": "",         # 只认领指派给该账号的 bug；空=不按指派过滤
    "severity_cap": 0,         # 严重度上限（1 最严重）；0=不限
    "our_sides": ["backend"],  # 我方端（CodeBee 自动修）；空=纯排查转派不修
    "repos": {"backend": dict(_REPO_DEFAULTS), "frontend": dict(_REPO_DEFAULTS)},
    "repo_hints": {"backend": "", "frontend": ""},   # AI 排查时的一句仓库描述
    "owners": {"backend": "", "frontend": "", "not_ours": ""},
    "module_routes": [],       # [{module:int, side:backend|frontend|both|not_ours, account:""}]
}

_UPDATABLE = ("base_url", "account", "password", "product_profiles",
              "auto_resolve", "auto_merge", "triage_ai",
              "poll_enabled", "interval_minutes")


# ---------------------------------------------------------------- 出网边界（SSRF）

_META_HOSTS = {"metadata.google.internal", "metadata.goog"}
_NO_REDIRECT = None


def _no_redirect_opener():
    """HTTP 重定向一概不跟：目标必须就是用户配置的那台禅道。"""
    global _NO_REDIRECT
    if _NO_REDIRECT is None:
        class _Stop(urllib.request.HTTPRedirectHandler):
            def redirect_request(self, req, fp, code, msg, headers, newurl):
                return None
        _NO_REDIRECT = urllib.request.build_opener(
            _Stop(), urllib.request.HTTPSHandler(context=tlsctx.context()))
    return _NO_REDIRECT


def _guard_url(url):
    """出网前的边界校验：协议白名单 + 主机解析阻断云元数据/链路本地地址。

    私网与环回按设计放行（禅道自建在内网是主场景，配置者即本机用户）。
    校验失败抛 ZenError。返回原 url。
    """
    from urllib.parse import urlparse
    u = urlparse(url)
    if u.scheme not in ("http", "https"):
        raise ZenError("禅道地址只允许 http/https 协议")
    host = (u.hostname or "").lower()
    if not host:
        raise ZenError("禅道地址缺少主机名")
    if host in _META_HOSTS:
        raise ZenError("不允许访问云元数据地址")
    try:
        infos = socket.getaddrinfo(host, None)
    except OSError:
        raise ZenError("禅道主机解析失败：%s" % host)
    for info in infos:
        ip = ipaddress.ip_address(info[4][0])
        if ip.is_link_local or ip.is_reserved and not ip.is_private:
            raise ZenError("不允许访问链路本地/保留地址：%s" % ip)
        if ip.is_multicast or ip.is_unspecified:
            raise ZenError("不允许访问多播/未指定地址：%s" % ip)
    return url


# ---------------------------------------------------------------- 持久化

def _save_locked():
    _FILE.parent.mkdir(parents=True, exist_ok=True)
    tmp = _FILE.with_suffix(".tmp")
    tmp.write_text(json.dumps({"version": 2, **_STATE},
                              ensure_ascii=False, indent=2), encoding="utf-8")
    os.replace(str(tmp), str(_FILE))


def _normalize_claim(d):
    """claim 归一：v1 单任务形状（task_id/run_id）→ v2 tasks 数组。"""
    c = dict(d) if isinstance(d, dict) else {}
    if isinstance(c.get("tasks"), list) and c["tasks"]:
        tasks = [dict(t) for t in c["tasks"] if isinstance(t, dict)]
    else:
        tasks = [{"side": "backend", "task_id": c.get("task_id") or "",
                  "run_id": c.get("run_id") or "", "state": "fixing"}] \
            if (c.get("task_id") or c.get("run_id")) else []
    c["tasks"] = tasks
    c.setdefault("triage", {"side": "", "reason": "", "by": "", "account": ""})
    if not isinstance(c.get("triage"), dict):
        c["triage"] = {"side": "", "reason": "", "by": "", "account": ""}
    c.setdefault("note", "")
    c.setdefault("attempts", 0)
    return c


def _migrate_legacy(cfg):
    """老扁平配置（products+单仓库）→ product_profiles。就地改写返回。"""
    if cfg.get("product_profiles"):
        return cfg
    products = cfg.get("products") or []
    if not isinstance(products, list) or not products:
        return cfg
    try:
        pids = [int(p) for p in products if str(p).strip()]
    except (TypeError, ValueError):
        return cfg
    backend = dict(_REPO_DEFAULTS)
    for k in _REPO_DEFAULTS:
        backend[k] = str(cfg.get(k) or "")
    our = ["backend"]
    if not backend["workdir"]:
        our = []          # 老配置连工作目录都没配：纯路由
    for pid in pids:
        cfg.setdefault("product_profiles", []).append({
            "product": pid,
            "assigned_to": str(cfg.get("assigned_to") or ""),
            "severity_cap": int(cfg.get("severity_cap") or 0),
            "our_sides": list(our),
            "repos": {"backend": backend, "frontend": dict(_REPO_DEFAULTS)},
            "repo_hints": {"backend": "", "frontend": ""},
            "owners": {"backend": "", "frontend": "", "not_ours": ""},
            "module_routes": [],
        })
    return cfg


def load(force=False):
    """从磁盘加载状态（幂等）。返回 claims 数。测试可重绑 _FILE 后 force=True。"""
    global _LOADED
    with _LOCK:
        if _LOADED and not force:
            return len(_STATE["claims"])
        try:
            data = json.loads(_FILE.read_text(encoding="utf-8"))
        except Exception:
            data = {}
        cfg = data.get("config") if isinstance(data, dict) else None
        # 老扁平配置先迁移成产品档案（老键 products/workdir/... 不在新 defaults 里，
        # 必须在按 _CFG_DEFAULTS 过滤之前完成迁移）
        if isinstance(cfg, dict) and not cfg.get("product_profiles"):
            cfg = _migrate_legacy(dict(cfg))
        # 老配置用 interval_hours（小时），新配置改用 interval_minutes（分钟）：
        # 用户没动过间隔（还是老默认 2 小时）就跟随新默认 5 分钟；真改过的按小时换算保留。
        if isinstance(cfg, dict) and "interval_minutes" not in cfg \
                and "interval_hours" in cfg:
            try:
                hours = int(cfg.get("interval_hours") or _LEGACY_INTERVAL_HOURS_DEFAULT)
            except (TypeError, ValueError):
                hours = _LEGACY_INTERVAL_HOURS_DEFAULT
            cfg = dict(cfg)
            mins = INTERVAL_DEFAULT if hours == _LEGACY_INTERVAL_HOURS_DEFAULT else hours * 60
            cfg["interval_minutes"] = max(INTERVAL_MIN, min(INTERVAL_MAX, mins))
        merged = dict(_CFG_DEFAULTS)
        if isinstance(cfg, dict):
            merged.update({k: v for k, v in cfg.items() if k in _CFG_DEFAULTS})
        claims = data.get("claims") if isinstance(data, dict) else None
        _STATE["config"] = merged
        _STATE["claims"] = {str(k): _normalize_claim(v)
                            for k, v in (claims or {}).items()} \
            if isinstance(claims, dict) else {}
        _STATE["last_scan"] = str(data.get("last_scan") or "") if isinstance(data, dict) else ""
        _STATE["next_scan"] = str(data.get("next_scan") or "") if isinstance(data, dict) else ""
        _STATE["last_error"] = str(data.get("last_error") or "") if isinstance(data, dict) else ""
        _LOADED = True
        return len(_STATE["claims"])


def _ensure_loaded():
    if not _LOADED:
        load()


def _cfg():
    cfg = dict(_CFG_DEFAULTS)
    cfg.update({k: v for k, v in (_STATE.get("config") or {}).items()
                if k in _CFG_DEFAULTS})
    return cfg


def _profiles(cfg=None):
    out = []
    for p in (cfg or _cfg()).get("product_profiles") or []:
        prof = dict(_PROFILE_DEFAULTS)
        prof.update({k: v for k, v in (p or {}).items() if k in _PROFILE_DEFAULTS})
        repos = dict(_PROFILE_DEFAULTS["repos"])
        for side in SIDES:
            r = dict(_REPO_DEFAULTS)
            r.update({k: v for k, v in ((prof.get("repos") or {}).get(side) or {}).items()
                      if k in _REPO_DEFAULTS})
            repos[side] = r
        prof["repos"] = repos
        hints = dict(_PROFILE_DEFAULTS["repo_hints"])
        for side in SIDES:
            hints[side] = str((prof.get("repo_hints") or {}).get(side) or "")
        prof["repo_hints"] = hints
        owners = dict(_PROFILE_DEFAULTS["owners"])
        for k in owners:
            owners[k] = str((prof.get("owners") or {}).get(k) or "").strip()
        prof["owners"] = owners
        prof["our_sides"] = [s for s in SIDES if s in (prof.get("our_sides") or [])]
        prof["module_routes"] = [r for r in (prof.get("module_routes") or [])
                                 if isinstance(r, dict)]
        out.append(prof)
    return out


def _profile_for(cfg, product_id):
    try:
        pid = int(product_id or 0)
    except (TypeError, ValueError):
        return None
    for p in _profiles(cfg):
        if p["product"] == pid:
            return p
    return None


# ---------------------------------------------------------------- 禅道客户端

class ZenError(Exception):
    """禅道接口错误（message 人话，可直接展示）。"""


_TOKEN = {"v": "", "at": 0.0}
# 原始地址 → 探测成功的 api 基址（一键安装包常部署在 /zentao 子路径下，自动补探）
_APIBASE = {}
# 通道自适应：原始地址 → "rest"（≥15 REST v1）/ "old"（老版 module-method JSON 接口）
_MODE = {}
# web 端路由形态：原始地址 → "pathinfo"（伪静态 bug-view-N.html，官方默认）
# / "get"（index.php?m=bug&f=view&bugID=N）。bug 详情链接用（2026-09-23 真机实证
# 伪静态部署对 GET 式链接不路由）。
_WEBSTYLE = {}
_OLD = {"api": "", "sid": "", "at": 0.0}
_OLD_FORM = {"form": ""}      # 命中的密码形态 "plain" | "md5chain"，缓存避免重复试
_REST_LAST_ERR = {"msg": ""}
_OLD_TTL = 20 * 60


def _reset_token():
    _TOKEN["v"] = ""
    _TOKEN["at"] = 0.0


def resolved_base_url(base_url):
    """探测成功后的有效地基（含自动补出的子路径）；没探测过返回空串。"""
    b = str(base_url or "").strip().rstrip("/")
    hit = _APIBASE.get(b) or (_OLD.get("api") if _MODE.get(b) == "old" else "")
    if not hit:
        return ""
    return re.sub(r"/api\.php/v1$", "", hit)


def probe_web_style(base_url):
    """探测禅道 web 端路由形态（bug 详情链接用）：GET web 根（裸根 + 常见 /zentao
    子路径都试），看登录跳转里是伪静态路由（user-login-xxx.html）还是 GET 式
    （index.php?m=user）。尽力而为：任何网络/守卫异常回空串，绝不影响登录与
    扫描主流程；探出即缓存。"""
    try:
        b = _raw_base(base_url)
    except ZenError:
        return ""
    if _WEBSTYLE.get(b):
        return _WEBSTYLE[b]
    style = ""
    for root in (b + "/", b + "/zentao/"):
        body = ""
        try:
            req = urllib.request.Request(_guard_url(root), headers={"Accept": "text/html"})
            with _no_redirect_opener().open(req, timeout=HTTP_TIMEOUT) as r:
                body = (r.read(65536) or b"").decode("utf-8", "replace")
        except Exception:
            body = ""
        if "user-login-" in body or re.search(r"[a-z]+-view-\d+[^\"]*\.html", body):
            style = "pathinfo"
        elif "index.php?m=" in body:
            style = "get"
        if style:
            break
    if style:
        _WEBSTYLE[b] = style
    return style


def _raw_base(base_url):
    b = str(base_url or "").strip().rstrip("/")
    if not b:
        raise ZenError("禅道地址未配置")
    if not b.startswith(("http://", "https://")):
        raise ZenError("禅道地址必须以 http:// 或 https:// 开头")
    return b


def _api_base(base_url):
    b = _raw_base(base_url)
    hit = _APIBASE.get(b)
    if hit:
        return hit
    return b + "/api.php/v1"


def _base_candidates(b):
    """api 基址候选：用户填的根路径 → 官方一键安装包常见的 /zentao 子路径。"""
    out, seen = [], set()
    for c in (b + "/api.php/v1", b + "/zentao/api.php/v1"):
        if c not in seen:
            seen.add(c)
            out.append(c)
    return out


def _rest_login(base_url, account, password):
    """REST v1 通道取 token。成功返回 token（缓存基址）；
    通道不可用（404 / 只认应用 code 的 200 信封）返回 None；
    账密被 REST 明确拒绝（401/403）或网络/守卫失败抛 ZenError。"""
    b = _raw_base(base_url)
    for api in _base_candidates(b):
        url = _guard_url(api + "/tokens")
        body = json.dumps({"account": str(account or ""), "password": str(password or "")},
                          ensure_ascii=False).encode("utf-8")
        req = urllib.request.Request(url, data=body, method="POST", headers={
            "Content-Type": "application/json", "Accept": "application/json"})
        try:
            with _no_redirect_opener().open(req, timeout=HTTP_TIMEOUT) as r:
                data = json.loads((r.read() or b"{}").decode("utf-8", "replace"))
        except urllib.error.HTTPError as e:
            try:
                detail = json.loads((e.read() or b"").decode("utf-8", "replace"))
                msg = (detail.get("error") or "") if isinstance(detail, dict) else ""
            except Exception:
                msg = ""
            if e.code in (401, 403):
                raise ZenError("禅道账号或密码不对（%s）%s" % (e.code, msg))
            if e.code == 404:
                _REST_LAST_ERR["msg"] = "REST 接口不存在（404）"
                continue        # 换下一个候选基址
            raise ZenError("禅道返回 %s：%s" % (e.code, msg or "获取令牌失败"))
        except ZenError:
            raise
        except Exception as e:
            raise ZenError("连不上禅道（%s）——请检查地址与网络" % e)
        tok = ""
        if isinstance(data, dict):
            tok = str(data.get("token") or "")
            if not tok and isinstance(data.get("data"), dict):
                tok = str(data["data"].get("token") or "")
        if tok:
            _APIBASE[b] = api
            _TOKEN["v"] = tok
            _TOKEN["at"] = time.time()
            probe_web_style(b)      # bug 链接形态探测，失败不影响登录
            return tok
        # HTTP 200 但没有 token：REST 在但不认账密（如只认应用 code 的部署）
        _REST_LAST_ERR["msg"] = "REST 接口不认账密（%s）" % (
            str(data.get("errmsg") or data.get("error") or "响应里没有 token"))
    return None


def _token(cfg, force=False):
    if force or not _TOKEN["v"] or time.time() - _TOKEN["at"] > TOKEN_TTL:
        tok = _rest_login(cfg.get("base_url"), cfg.get("account"), cfg.get("password"))
        if not tok:
            raise ZenError(_REST_LAST_ERR.get("msg") or "REST 通道不可用")
        return tok
    return _TOKEN["v"]


def _api(method, path, cfg=None, body=None):
    """调禅道 API。返回 dict（非 dict 响应返回 {}）。401/403 自动重取 token 重试一次。"""
    c = cfg or _cfg()
    for attempt in (1, 2):
        headers = {"Accept": "application/json", "Token": _token(c, force=(attempt == 2))}
        url = _guard_url(_api_base(c.get("base_url")) + path)
        data = None
        if body is not None:
            data = json.dumps(body, ensure_ascii=False).encode("utf-8")
            headers["Content-Type"] = "application/json"
        req = urllib.request.Request(url, data=data, method=method, headers=headers)
        try:
            with _no_redirect_opener().open(req, timeout=HTTP_TIMEOUT) as r:
                out = json.loads((r.read() or b"{}").decode("utf-8", "replace"))
            return out if isinstance(out, dict) else {"_list": out}
        except urllib.error.HTTPError as e:
            raw = ""
            try:
                raw = (e.read() or b"").decode("utf-8", "replace")
            except Exception:
                pass
            detail = ""
            try:
                d = json.loads(raw)
                if isinstance(d, dict):
                    detail = str(d.get("error") or d.get("message") or "")
            except Exception:
                pass
            if e.code in (401, 403) and attempt == 1:
                _reset_token()
                continue
            raise ZenError("禅道接口 %s（%s）%s" % (path, e.code, detail or "调用失败"))
        except ZenError:
            raise
        except Exception as e:
            raise ZenError("连不上禅道（%s）" % e)
    raise ZenError("禅道认证失败（token 两次获取后仍被拒绝，检查账号权限）")


# ---------------------------------------------------------------- 老版 JSON 接口适配
# 禅道 15 以前的经典入口：/zentao/api-getsessionid.json 取会话 → user-login.json
# 账密登录（Cookie zentaosid）→ {module}-{method}-{参数}.json 调业务。响应是双层
# 信封 {"status","data":"<json 字符串>"}。有的部署 REST 只认应用 code，账密只能
# 走这条通道，故在 _call 里自动探测分路。REST 路径在这里翻译成老接口形态。

def _old_parse(raw):
    """老信封解析。非 JSON（多半是登录重定向 HTML）返回 None = 会话死/不可用。"""
    try:
        outer = json.loads(raw.decode("utf-8", "replace"))
    except Exception:
        return None
    if isinstance(outer, dict) and isinstance(outer.get("data"), str):
        try:
            outer["data"] = json.loads(outer["data"])
        except Exception:
            pass
    return outer if isinstance(outer, dict) else None


def _old_fail(outer):
    """status=failed / result=fail → 人话错误文本；成功返回空串。"""
    if not isinstance(outer, dict):
        return ""
    if outer.get("status") == "failed" or outer.get("result") == "fail":
        return str(outer.get("reason") or outer.get("message")
                   or outer.get("error") or "调用失败")
    return ""


def _old_get(api, path):
    url = _guard_url(api + path)
    req = urllib.request.Request(url, headers={"Accept": "application/json"})
    with _no_redirect_opener().open(req, timeout=HTTP_TIMEOUT) as r:
        return r.read() or b""


def _old_post(api, path, form):
    url = _guard_url(api + path)
    data = urllib.parse.urlencode(form or {}).encode("utf-8")
    req = urllib.request.Request(url, data=data, method="POST", headers={
        "Content-Type": "application/x-www-form-urlencoded",
        "Accept": "application/json"})
    with _no_redirect_opener().open(req, timeout=HTTP_TIMEOUT) as r:
        return r.read() or b""


def _old_session(api):
    """取新会话。返回 (sid, rand)；通道不可用返回 None。"""
    try:
        raw = _old_get(api, "/api-getsessionid.json")
    except urllib.error.HTTPError:
        return None
    except ZenError:
        raise
    except Exception as e:
        raise ZenError("连不上禅道（%s）——请检查地址与网络" % e)
    outer = _old_parse(raw)
    if not outer or not isinstance(outer.get("data"), dict):
        return None
    d = outer["data"]
    return str(d.get("sessionID") or ""), str(d.get("rand") or "")


def _old_probe_authed(api, sid):
    """登录成功的兜底判据：带会话调需登录端点拿得到 JSON 信封（未登录会被
    重定向到登录页 HTML，解析出来是 None）。"""
    try:
        raw = _old_get(api, "/my-index.json?zentaosid=" + sid)
    except Exception:
        return False
    return _old_parse(raw) is not None


def _old_identify(api, sid, rand, account, password, form):
    """老接口账密登录。返回 (ok, 人话错误)。form=plain|md5chain。"""
    pw = str(password or "")
    if form == "md5chain":
        pw = hashlib.md5((hashlib.md5(pw.encode("utf-8")).hexdigest()
                          + str(rand)).encode("utf-8")).hexdigest()
    try:
        raw = _old_post(api, "/user-login.json?zentaosid=" + sid,
                        {"account": str(account or ""), "password": pw})
    except Exception as e:
        return False, "连不上禅道（%s）" % e
    outer = _old_parse(raw)
    if outer is None:
        return False, "登录响应不可识别"
    err = _old_fail(outer)
    if err:
        return False, err
    # 成功响应形状各版本不一：user 可能在 data 里（老）或顶层（实测某老版部署）
    d = outer.get("data")
    if (isinstance(d, dict) and isinstance(d.get("user"), dict)) \
            or isinstance(outer.get("user"), dict) or _old_probe_authed(api, sid):
        return True, ""
    return False, "登录未被接受（账号或密码不对，或账号被锁）"


def _old_login(cfg, rest_err=""):
    """老接口通道登录（带 /zentao 子路径候选）。成功置 _OLD 缓存与密码形态。"""
    b = _raw_base(cfg.get("base_url"))
    account, password = cfg.get("account"), cfg.get("password")
    cands, seen = [], set()
    for c in (b + "/zentao", b):
        if c not in seen:
            seen.add(c)
            cands.append(c)
    last = ""
    for api in cands:
        sess = _old_session(api)
        if not sess:
            continue
        sid, rand = sess
        forms = [_OLD_FORM["form"] or "plain", "md5chain", "plain"]
        tried = set()
        for form in [f for f in forms if not (f in tried or tried.add(f))]:
            ok, err = _old_identify(api, sid, rand, account, password, form)
            if ok:
                _OLD.update(api=api, sid=sid, at=time.time())
                _OLD_FORM["form"] = form
                _MODE[b] = "old"
                probe_web_style(b)  # bug 链接形态探测，失败不影响登录
                return
            last = err
        break       # 同一台服务，账密结果与子路径无关，别再烧尝试次数
    if not last:
        raise ZenError("禅道老版接口不可用（会话接口无响应——地址若是子目录部署要带上子目录）"
                       + ("；%s" % rest_err if rest_err else ""))
    if "账号" in last or "密码" in last or "锁定" in last:
        raise ZenError(last)
    raise ZenError("禅道老接口登录失败：%s" % last)


def _old_call(method, path, cfg, body=None):
    """老接口执行：翻译 REST 路径 → module-method.json，返回 REST 同形状的 dict。"""
    b = _raw_base(cfg.get("base_url"))
    if _MODE.get(b) != "old" or not _OLD["sid"] or time.time() - _OLD["at"] > _OLD_TTL:
        _old_login(cfg)
    parts = urllib.parse.urlsplit(path)
    q = dict(urllib.parse.parse_qsl(parts.query))
    p = parts.path
    for attempt in (1, 2):
        try:
            return _old_route(_OLD["api"], method, p, q, body)
        except _OldSessionDead:
            if attempt == 2:
                break
            _OLD["sid"] = ""
            _old_login(cfg)
        except urllib.error.HTTPError as e:
            raise ZenError("禅道老接口 %s（%s）调用失败" % (p, e.code))
    raise ZenError("禅道会话两次登录后仍失效（检查账号权限）")


class _OldSessionDead(Exception):
    pass


def _pairs_to_list(pairs, id_key, name_key):
    """{id/name: 值} 形态的 pairs → [{id,name}] 列表（值可能为串或对象）。"""
    out = []
    for k, v in (pairs or {}).items():
        if isinstance(v, dict):
            item = dict(v)
            item.setdefault(id_key, k)
        else:
            item = {id_key: k, name_key: v}
        out.append(item)
    return out


def _module_items(d):
    """老版模块树形状兼容（2026-09-21 真机反馈「响应形状不认识」）：
    sons/modules 数组、{id: {name,...}} 字典、children/sons 嵌套树，统一
    递归展开成 [{id,name}] 平铺清单（id=0 是树根容器，跳过）；全数字键的
    {id: 名字} 扁平映射也认（部分版本的模块摘要形状）。"""
    out = []

    def walk(node):
        if isinstance(node, list):
            for x in node:
                walk(x)
        elif isinstance(node, dict):
            if node.get("id") not in (None, "", 0):
                out.append(node)
                for k in ("children", "sons"):
                    if isinstance(node.get(k), (list, dict)):
                        walk(node[k])
            else:
                if node and all(str(k).isdigit() for k in node.keys()):
                    flat = [{"id": k, "name": str(v)}
                            for k, v in node.items() if str(v or "").strip()]
                    if flat:
                        out.extend(flat)
                        return
                for v in node.values():
                    walk(v)

    if isinstance(d, dict):
        # tree 优先：真机 tree=带 children 的完整嵌套树，sons 只有顶层扁平表
        # （走 sons 会漏子模块）
        walk(d.get("tree") or d.get("sons") or d.get("modules") or d)
    elif isinstance(d, list):
        walk(d)
    return out


def _old_route(api, method, p, q, body):
    """路径翻译 + 请求 + 归一。会话死抛 _OldSessionDead。"""

    def go(raw, translator=None):
        outer = _old_parse(raw)
        if outer is None:
            txt = raw.decode("utf-8", "replace")
            if "user-login" in txt:
                raise _OldSessionDead()     # 登录重定向 = 会话死
            # 其余 HTML 是 js::locate/alert 回包（老禅道 POST 动作成功就回这种）
            return {"_js": True}
        err = _old_fail(outer)
        if err:
            raise ZenError("禅道老接口%s：%s" % (translator or "", err))
        return outer.get("data")

    def get(path):
        try:
            raw = _old_get(api, path + ("&" if "?" in path else "?")
                           + "zentaosid=" + _OLD["sid"])
        except urllib.error.HTTPError as e:
            raw = e.read() or b""
            if _old_parse(raw) is None:
                raise _OldSessionDead()     # 非 JSON：多半被重定向到登录页
        return raw

    def post(path, form):
        try:
            raw = _old_post(api, path + ("&" if "?" in path else "?")
                            + "zentaosid=" + _OLD["sid"], form)
        except urllib.error.HTTPError as e:
            raw = e.read() or b""
            if e.code in (401, 403) or _old_parse(raw) is None:
                raise _OldSessionDead()
        return raw

    m = re.match(r"^/products/(\d+)/bugs$", p)
    if m and method == "GET":
        pid = m.group(1)
        page = max(1, int(q.get("page") or 1))
        out, total, seen_ids = [], 0, set()
        # 翻页用路径参数形态（实测部分老版不认 query 翻页参数）：带 branch 段；
        # 若 recPerPage 没生效（老版本无 branch 段会错位解析）回落 query 形态。
        branch_form = True
        for pg in range(page, page + 6):     # 去重兜底：翻页参数不被认时不会死循环
            if branch_form:
                pathq = "/bug-browse-%s-0-unclosed-0-id_desc-0-%d-%d.json" % (pid, PAGE_LIMIT, pg)
            else:
                pathq = "/bug-browse-%s.json?browseType=unclosed&orderBy=id_desc" \
                        "&recPerPage=%d&pagerID=%d" % (pid, PAGE_LIMIT, pg)
            d = go(get(pathq), "（拉 bug 列表）")
            if branch_form and isinstance(d, dict):
                try:
                    got = int((d.get("pager") or {}).get("recPerPage") or 0)
                except (TypeError, ValueError):
                    got = 0
                if got != PAGE_LIMIT:
                    branch_form = False
                    continue                 # 形态没对上：换 query 形态重拉本页
            if not isinstance(d, dict):
                break
            bugs = d.get("bugs") if isinstance(d.get("bugs"), list) else []
            fresh = [x for x in bugs if isinstance(x, dict)
                     and str(x.get("id")) not in seen_ids]
            for x in fresh:
                seen_ids.add(str(x.get("id")))
            out.extend(fresh)
            try:
                total = int((d.get("pager") or {}).get("recTotal") or 0)
            except (TypeError, ValueError, AttributeError):
                total = 0
            if not fresh or (total and len(out) >= total) or len(out) >= MAX_BUGS:
                break
        return {"bugs": out[:MAX_BUGS], "total": total or len(out)}

    m = re.match(r"^/products/(\d+)/modules$", p)
    if m and method == "GET":
        # 2026-09-22 真机实探：该版模块行 type=story，viewType=module 查出空树
        # （status 仍 success）——「响应形状不认识」的真根因。改按 bug 视图拉
        # （bug 表单模块下拉用的就是这棵树），story/case 兜底。
        pid = m.group(1)
        last, saw_js = None, False
        for vt in ("bug", "story", "case"):
            d = go(get("/tree-browse-%s-%s.json" % (pid, vt)), "（拉模块清单）")
            last = d
            if isinstance(d, dict) and d.get("_js"):
                saw_js = True            # 接口回了页面脚本：换视图再试
                continue
            items = [x for x in _module_items(d) if isinstance(x, dict) and x.get("id")]
            if items:
                return {"_list": items}
        if saw_js:
            raise ZenError("禅道老接口（拉模块清单）：回了页面脚本而不是数据"
                           "（模块树接口不在或被重定向）——请在禅道产品视图 URL 里查模块 ID 手工填写")
        keys = ",".join(sorted(str(k) for k in last.keys())) \
            if isinstance(last, dict) else type(last).__name__
        raise ZenError("该产品模块树为空（bug/story/case 三个视图都没拉到模块，"
                       "响应顶层键：%s）——请到禅道确认产品下建过模块，或手工填模块 ID" % keys)

    if p == "/products" and method == "GET":
        d = go(get("/api-getmodel-product-getpairs.json"), "（拉产品清单）")
        prods = d.get("products") if isinstance(d, dict) else None
        if isinstance(prods, list):     # 部分老版回数组形状 [{id,name,...}]
            return {"_list": [x for x in prods if isinstance(x, dict) and x.get("id")]}
        pairs = prods if isinstance(prods, dict) else d
        return {"_list": _pairs_to_list(pairs, "id", "name")}

    if p == "/users" and method == "GET":
        d = go(get("/api-getmodel-user-getpairs.json"), "（拉账号清单）")
        return {"_list": _pairs_to_list(d, "account", "realname")}

    m = re.match(r"^/bugs/(\d+)$", p)
    if m and method == "GET":
        d = go(get("/bug-view-%s.json" % m.group(1)), "（查 bug）")
        bug = d.get("bug") if isinstance(d, dict) and isinstance(d.get("bug"), dict) else d
        # 老接口的 files 附件在 data 顶层，不在 bug 里：合并进 bug dict（REST 形状兼容）
        if isinstance(bug, dict) and isinstance(d, dict) \
                and isinstance(d.get("files"), (dict, list)):
            bug = dict(bug)
            bug["files"] = d["files"]
        return bug

    m = re.match(r"^/bugs/(\d+)/resolve$", p)
    if m and method == "POST":
        form = {k: str(v or "") for k, v in (body or {}).items()}
        go(post("/bug-resolve-%s.json" % m.group(1), form), "（resolve）")
        return {"ok": True}

    m = re.match(r"^/bugs/(\d+)$", p)
    if m and method == "PUT":
        form = {k: str(v or "") for k, v in (body or {}).items() if k in ("assignedTo", "comment")}
        go(post("/bug-assignTo-%s.json" % m.group(1), form), "（转派）")
        return {"ok": True}

    raise ZenError("禅道老接口不认识该调用：%s %s（请升级禅道到 ≥15 用 REST 接口）"
                   % (method, p))


def _call(method, path, cfg=None, body=None):
    """统一入口：REST v1 优先；通道探明后按模式分发（账密不被 REST 接受时自动
    切老版 JSON 接口）。所有业务调用都走这里，拿到的都是 REST 形状。"""
    c = cfg or _cfg()
    b = _raw_base(c.get("base_url"))
    mode = _MODE.get(b)
    if not mode:
        tok = _rest_login(b, c.get("account"), c.get("password"))
        if tok:
            _MODE[b] = mode = "rest"
        else:
            _old_login(c, _REST_LAST_ERR.get("msg", ""))
            mode = "old"
    if mode == "rest":
        return _api(method, path, cfg=c, body=body)
    return _old_call(method, path, c, body=body)


def _acct(v):
    """assignedTo/openedBy 兼容：新版是 {account,...} 用户对象，老版可能是
    [账号, 姓名] 数组或账号串。"""
    if isinstance(v, dict):
        return str(v.get("account") or "").strip()
    if isinstance(v, (list, tuple)) and v:
        return str(v[0] or "").strip()
    return str(v or "").strip()


def _strip_html(raw):
    """steps 字段是富文本 HTML：剥标签转纯文本。"""
    txt = str(raw or "")
    txt = re.sub(r"(?is)<(script|style).*?>.*?</\1>", " ", txt)
    txt = re.sub(r"(?i)<br\s*/?>|</p>|</li>|</div>|</tr>", "\n", txt)
    txt = re.sub(r"<[^>]+>", " ", txt)
    txt = _html.unescape(txt)
    txt = re.sub(r"[ \t\r]+", " ", txt)
    txt = re.sub(r"\n\s*\n+", "\n", txt)
    return txt.strip()


def _severity(bug):
    try:
        return int(bug.get("severity") or 0)
    except (TypeError, ValueError):
        return 0


# ---------------------------------------------------------------- bug 图片证据

_IMG_MAGIC = ((b"\x89PNG", "image/png"), (b"\xff\xd8\xff", "image/jpeg"),
              (b"GIF8", "image/gif"))


def _img_mime(raw):
    """按魔数判图片类型；非图返回空串（下载到错误页 HTML 时靠它拦住）。"""
    raw = bytes(raw or b"")
    for pre, mime in _IMG_MAGIC:
        if raw.startswith(pre):
            return mime
    if raw[:4] == b"RIFF" and raw[8:12] == b"WEBP":
        return "image/webp"
    return ""


def _img_ext_mime(ext):
    return {"png": "image/png", "jpg": "image/jpeg", "jpeg": "image/jpeg",
            "gif": "image/gif", "webp": "image/webp", "bmp": "image/bmp"}.get(
        str(ext or "").strip().lstrip(".").lower(), "")


def _img_srcs(steps_html):
    """steps 富文本里的 <img src> 候选（含 data: URI）。"""
    return re.findall(r'(?is)<img\b[^>]*?\bsrc\s*=\s*["\']([^"\']+)["\']',
                      str(steps_html or ""))


def _bug_mentions_images(bug):
    """描述是否指向图片证据：steps 带 <img>、文字提「如图/截图…」、或附件含图片。"""
    html = str(bug.get("steps") or "")
    if "<img" in html.lower():
        return True
    text = str(bug.get("title") or "") + "\n" + _strip_html(html)
    if _IMG_REF_RE.search(text):
        return True
    files = bug.get("files")
    vals = files.values() if isinstance(files, dict) else (files if isinstance(files, list) else [])
    for e in vals:
        if isinstance(e, dict) and _img_ext_mime(
                e.get("extension") or str(e.get("title") or "").rsplit(".", 1)[-1]):
            return True
    return False


def _decode_data_uri(src):
    m = re.match(r"(?is)^data:(image/[\w.+-]+);base64,(.+)$", src.strip())
    if not m:
        return "", b""
    try:
        raw = base64.b64decode(m.group(2))
    except Exception:
        return "", b""
    mime = _img_mime(raw) or m.group(1).lower()
    return mime, raw


def _download_same_origin(src, cfg):
    """下载禅道站内图片字节：仅同源（host:port 与配置的禅道一致），带会话多形态
    尝试（zentaosid 查询参——新老两通道都认；再裸 GET 兜底），魔数校验。
    非 200 / 非图 / 网络失败返回 b""。"""
    try:
        base = _raw_base(cfg.get("base_url"))
    except ZenError:
        return b""
    url = src if "://" in str(src) else base + "/" + str(src).lstrip("/")
    try:
        u, bu = urllib.parse.urlsplit(url), urllib.parse.urlsplit(base)
    except ValueError:
        return b""
    if (u.scheme or bu.scheme, u.netloc) != (bu.scheme, bu.netloc):
        return b""          # 第三方图床一律不取（SSRF 边界外只放行同源）
    url = _guard_url("%s://%s%s" % (bu.scheme, bu.netloc, u.path or "/")
                     + (("?" + u.query) if u.query else ""))
    tries = []
    for sid in (str(_OLD.get("sid") or ""), str(_TOKEN.get("v") or "")):
        if sid:
            tries.append(url + ("&" if "?" in url else "?") + "zentaosid=" + sid)
    tries.append(url)
    for t in tries:
        try:
            req = urllib.request.Request(t, headers={"Accept": "*/*"})
            with _no_redirect_opener().open(req, timeout=HTTP_TIMEOUT) as r:
                if r.status != 200:
                    continue
                raw = r.read(20 * 1024 * 1024)
        except Exception:
            continue
        if _img_mime(raw):
            return raw
    return b""


def _file_entry_urls(fid, entry):
    """详情 files 条目 → 下载候选 URL（entry 自带地址优先，再猜经典路由）。"""
    out = []
    if isinstance(entry, dict):
        for k in ("download", "url", "href"):
            v = str(entry.get(k) or "").strip()
            if v.startswith(("http://", "https://", "/")):
                out.append(v)
        ext = _img_ext_mime(entry.get("extension")
                            or str(entry.get("title") or "").rsplit(".", 1)[-1])
    else:
        ext = ""
    if ext:
        ext = ext.split("/", 1)[1]
        for route in ("file-read-%s.%s", "file-download-%s.%s"):
            out.append("/" + route % (fid, ext))
    return out


def _prep_image_pairs(pairs):
    """[(mime, 原始字节)] → [(mime, b64)]。缩放规则与内置智能体同款（长边 1568、
    超大转 JPEG q85、单张 5MB 剔除）；就地实现不跨层 import（架构 L2→L1 反向禁）。"""
    try:
        from PIL import Image
    except Exception:
        Image = None
    out = []
    for mime, raw in pairs or []:
        data = raw
        if Image is not None:
            try:
                im = Image.open(io.BytesIO(raw))
                im.load()
                w, h = im.size
                edge = max(w, h, 1)
                if edge > 1568:
                    r = 1568 / float(edge)
                    im = im.resize((max(1, round(w * r)), max(1, round(h * r))),
                                   Image.LANCZOS)
                fmt = {"image/png": "PNG", "image/jpeg": "JPEG",
                       "image/gif": "GIF", "image/webp": "WEBP"}.get(mime, "PNG")
                buf = io.BytesIO()
                im.save(buf, format=fmt)
                data = buf.getvalue()
            except Exception:
                data = raw                # 解码失败退回原始字节
            if mime != "image/jpeg" and len(data) > 3500 * 1024:
                try:                      # 仍过大：转 JPEG q85（RGB 拍平 alpha）
                    im = Image.open(io.BytesIO(data)).convert("RGB")
                    buf = io.BytesIO()
                    im.save(buf, format="JPEG", quality=85)
                    data, mime = buf.getvalue(), "image/jpeg"
                except Exception:
                    pass
        if len(data) > 5 * 1024 * 1024:
            continue
        out.append((mime, base64.b64encode(data).decode("ascii")))
    return out


def _bug_images(bug_id, steps_html, cfg=None):
    """bug 关联图片 → [(mime, b64)]：steps 内嵌 <img>（含 data: URI）优先，
    读不到再看详情 files 附件。下载带禅道会话、魔数校验、缩放管线同内置智能体。
    拿不到返回 []（也缓存，15 分钟内不重复下载）。"""
    key = str(bug_id or "")
    now = time.time()
    hit = _IMG_CACHE.get(key)
    if hit and now - hit[0] < IMG_CACHE_TTL:
        return hit[1]
    cfg = cfg or _cfg()
    pairs = []
    for src in _img_srcs(steps_html):
        s = src.strip()
        if s.lower().startswith("data:"):
            mime, raw = _decode_data_uri(s)
            if raw:
                pairs.append((mime or "image/png", raw))
        elif not s.lower().startswith("javascript:"):
            raw = _download_same_origin(s, cfg)
            if raw:
                pairs.append((_img_mime(raw), raw))
    if not pairs and key:
        try:
            detail = _call("GET", "/bugs/%s" % key, cfg=cfg)
            files = detail.get("files") if isinstance(detail, dict) else None
            entries = (list(files.items()) if isinstance(files, dict)
                       else [(str(i), e) for i, e in enumerate(files or [])])
            for fid, entry in entries:
                mime = _img_ext_mime(
                    (entry.get("extension") if isinstance(entry, dict) else "")
                    or str(entry.get("title") if isinstance(entry, dict) else "").rsplit(".", 1)[-1])
                if not mime:
                    continue          # 日志/压缩包等非图附件不取
                for u in _file_entry_urls(fid, entry):
                    raw = _download_same_origin(u, cfg)
                    if raw:
                        pairs.append((_img_mime(raw), raw))
                        break
        except Exception:
            log.debug("zentao: bug 附件图片获取失败", exc_info=True)
    imgs = _prep_image_pairs(pairs[:TRIAGE_MAX_IMAGES])
    if len(_IMG_CACHE) > 128:
        for k in sorted(_IMG_CACHE, key=lambda k: _IMG_CACHE[k][0])[:64]:
            _IMG_CACHE.pop(k, None)
    _IMG_CACHE[key] = (now, imgs)
    return imgs


def _save_bug_attachments(workdir, bug_id, imgs):
    """bug 截图落工作目录 _attachments/（create_task 的 dict 清单形态直用）。
    返回 [{name,size,mime,path}]；失败返回 []。"""
    if not imgs:
        return []
    try:
        adir = Path(workdir) / "_attachments"
        adir.mkdir(parents=True, exist_ok=True)
        ext = {"image/png": ".png", "image/jpeg": ".jpg", "image/gif": ".gif",
               "image/webp": ".webp"}
        items = []
        for i, (mime, b64) in enumerate(imgs, 1):
            name = "zenbug%s-%d%s" % (bug_id, i, ext.get(mime, ".png"))
            data = base64.b64decode(b64)
            (adir / name).write_bytes(data)
            items.append({"name": name, "size": len(data), "mime": mime,
                          "path": "_attachments/" + name})
        return items
    except Exception:
        log.debug("zentao: bug 截图落附件失败", exc_info=True)
        return []


def _claimable(bug, profile):
    """认领过滤：active + 指派 + 严重度（产品由档案本身界定）。"""
    if str(bug.get("status") or "") != "active":
        return False
    assigned = str(profile.get("assigned_to") or "").strip()
    cap = int(profile.get("severity_cap") or 0)
    if assigned and _acct(bug.get("assignedTo")) != assigned:
        return False
    if cap and _severity(bug) > cap:
        return False
    return True


def list_bugs(cfg, product_id, profile=None):
    """拉一个产品下的激活 bug（分页，总量封顶 MAX_BUGS）。

    REST v1 支持时把状态/指派人过滤下推给禅道，减少无关 bug 的分页传输；
    本地的 _claimable 仍保留，兼容老接口或禅道忽略未知查询参数的情况。
    """
    out = []
    for page in range(1, 6):
        query = {"page": page, "limit": PAGE_LIMIT, "status": "active"}
        assigned = str((profile or {}).get("assigned_to") or "").strip()
        if assigned:
            query["assignedTo"] = assigned
        d = _call("GET", "/products/%s/bugs?%s" % (
            product_id, urllib.parse.urlencode(query)), cfg=cfg)
        bugs = d.get("bugs") if isinstance(d, dict) else None
        if not isinstance(bugs, list):
            raise ZenError("禅道 bug 列表响应形状不对（预期 bugs 数组）")
        out.extend(b for b in bugs if isinstance(b, dict))
        total = 0
        try:
            total = int(d.get("total") or 0)
        except (TypeError, ValueError):
            pass
        if len(out) >= MAX_BUGS or (total and len(out) >= total) or len(bugs) < PAGE_LIMIT:
            break
    return out[:MAX_BUGS]


def weekly_brief(limit=12):
    """本周工作素材（工作汇报起草注入用，2026-09-23 Weekly Report Generator 借鉴
    ——「从工作系统取数入素材」）：分配给我的活跃任务 + 指派给我的活跃 Bug 摘要。
    尽力而为：未配置/不可达/响应形状不对一律返回空串，绝不阻塞汇报起草。
    多产品档案逐个聚合（同 _scan 口径），条目总量封顶 limit*2。"""
    lines = []
    try:
        cfg = _cfg()
        for profile in _profiles(cfg):
            assigned = str(profile.get("assigned_to") or "").strip()
            if not assigned:
                continue
            try:
                d = _call("GET", "/tasks?%s" % urllib.parse.urlencode(
                    {"assignedTo": assigned, "limit": limit}), cfg=cfg)
                tasks = d.get("tasks") if isinstance(d, dict) else None
                for t in (tasks or [])[:limit]:
                    if isinstance(t, dict) and t.get("status") in ("wait", "doing"):
                        lines.append("- [任务#%s] %s（%s）" % (
                            t.get("id"), t.get("name") or "", t.get("status") or ""))
            except Exception:
                pass
            try:
                for b in list_bugs(cfg, profile.get("product"), profile)[:limit]:
                    lines.append("- [Bug#%s] %s（严重级 %s）" % (
                        b.get("id"), b.get("title") or "",
                        b.get("severity") or b.get("status") or ""))
            except Exception:
                pass
    except Exception:
        return ""
    return "\n".join(lines[:limit * 2])


def fetch_modules(product_id):
    """拉产品模块清单（模块路由配置辅助）。接口不存在/失败报人话，提示手填 ID。"""
    _ensure_loaded()
    cfg = _cfg()
    try:
        d = _call("GET", "/products/%s/modules" % product_id, cfg=cfg)
    except ZenError as e:
        msg = str(e)
        # 只有「接口不存在」类错误才补「没有该接口」提示；老通道自己报的
        # 空树/脚本回包已带人话结论，别再叠一层误导
        if "404" in msg or "不认识" in msg:
            msg += "——也可能你的禅道没有该接口：请在禅道产品视图 URL 里查模块 ID 手工填写"
        return {"ok": False, "error": msg}
    # 形状兼容器（{_list} / {modules:[...]} / {id:{...}} 字典 / 裸数组）
    items = _list_items(d, "modules")
    if items is None:
        items = d.get("_list") if isinstance(d.get("_list"), list) else None
    out = []
    for m in items or []:
        if isinstance(m, dict) and m.get("id"):
            out.append({"id": m.get("id"), "name": str(m.get("name") or "")})
    if not out:
        if isinstance(d, dict) and set(d.keys()) == {"_list"}:
            # 自家归一包装（裸数组进 _api/_old_route 时打的包）：空清单=
            # 产品真没模块，不是形状问题，别拿顶层键吓人
            return {"ok": False,
                    "error": "禅道回了空模块清单（该产品可能没建模块）——请手工填模块 ID"}
        # 带响应形状摘要帮排查（顶层键名，不吐正文——用户实测反馈
        # 「响应形状不认识」却看不到真实形状，没法报修）
        keys = ",".join(sorted(str(k) for k in d.keys())) if isinstance(d, dict) else type(d).__name__
        return {"ok": False,
                "error": "模块清单为空或响应形状不认识（响应顶层键：%s）——请手工填模块 ID" % (keys or "非字典")}
    return {"ok": True, "modules": out[:200]}


def _list_items(d, key):
    """禅道 REST v1 清单响应形状兼容：{key:[...]} / 裸数组(_list) / {id:{...}} 字典。"""
    if not isinstance(d, dict):
        return None
    if isinstance(d.get(key), list):
        return d[key]
    if isinstance(d.get("_list"), list):
        return d["_list"]
    vals = [v for v in d.values() if isinstance(v, dict) and v.get("id")]
    return vals or None


def fetch_products():
    """拉产品清单（产品 ID 下拉选择辅助）。接口不存在/失败返回 ok:False+人话。"""
    _ensure_loaded()
    cfg = _cfg()
    out, total = [], 0
    try:
        page = 1
        while page <= 3:
            d = _call("GET", "/products?page=%d&limit=100" % page, cfg=cfg)
            items = _list_items(d, "products")
            if not items:
                break
            out.extend(it for it in items
                       if isinstance(it, dict) and str(it.get("id") or "") != "")
            try:
                total = int(d.get("total") or 0)
            except (TypeError, ValueError, AttributeError):
                total = 0
            if not total or len(out) >= total:
                break
            page += 1
    except ZenError as e:
        return {"ok": False, "error": "%s——请先「测试连接」确认地址与账号可用" % e}
    if not out:
        return {"ok": False, "error": "禅道里没有产品，或响应形状不认识（REST API 需 ≥15）"}
    seen, uniq = set(), []
    for m in out:
        try:
            pid = int(m["id"])
        except (TypeError, ValueError):
            pid = m["id"]
        if pid in seen:
            continue
        seen.add(pid)
        uniq.append({"id": pid, "name": str(m.get("name") or ""),
                     "status": str(m.get("status") or "")})
    uniq.sort(key=lambda x: str(x["id"]))
    return {"ok": True, "products": uniq[:500]}


def fetch_users():
    """拉禅道账号清单（负责人/转派下拉辅助）。失败返回 ok:False，前端仍可手填。"""
    _ensure_loaded()
    cfg = _cfg()
    try:
        d = _call("GET", "/users?limit=1000", cfg=cfg)
    except ZenError as e:
        return {"ok": False, "error": "%s——账号清单拉不到，仍可手填账号" % e}
    items = None
    if isinstance(d, dict):
        if isinstance(d.get("users"), list):
            items = d["users"]
        elif isinstance(d.get("_list"), list):
            items = d["_list"]
        else:
            items = []
            for k, v in d.items():      # {账号: 用户} 字典形状
                if not isinstance(v, dict):
                    continue
                u = dict(v)
                u.setdefault("account", str(k))
                items.append(u)
    out, seen = [], set()
    for u in items or []:
        if not isinstance(u, dict):
            continue
        acct = str(u.get("account") or "").strip()
        if not acct or acct in seen:
            continue
        seen.add(acct)
        out.append({"account": acct, "realname": str(u.get("realname") or "")})
    if not out:
        return {"ok": False, "error": "账号清单为空或响应形状不认识——仍可手填账号"}
    out.sort(key=lambda x: x["account"])
    return {"ok": True, "users": out[:500]}


def test_connection(base_url=None, account=None, password=None):
    """连接测试：探明通道登录 + 拉第一个产品的 bug 列表（有档案时）。返回 (ok, 人话结果)。"""
    c = _cfg()
    base_url = str(base_url if base_url is not None else c.get("base_url") or "").strip()
    account = str(account if account is not None else c.get("account") or "").strip()
    password = str(password if password is not None else c.get("password") or "").strip()
    if not (base_url and account and password):
        return False, "地址、账号、密码都要填全"
    b = base_url.strip().rstrip("/")
    probe = {"base_url": b, "account": account, "password": password}
    try:
        tok = _rest_login(b, account, password)
    except ZenError as e:
        return False, str(e)
    if tok:
        _MODE[b] = "rest"
    else:
        try:
            _old_login(probe, _REST_LAST_ERR.get("msg", ""))
            _MODE[b] = "old"
        except ZenError as e:
            return False, str(e)
    tag = "老版 JSON 接口" if _MODE.get(b) == "old" else "REST v1"
    try:
        profiles = _profiles(c)
        if profiles:
            bugs = list_bugs(probe, profiles[0]["product"], profiles[0])
            return True, "连接成功（%s），产品 %s 可访问（当前 %d 条 bug 在列表里）" % (
                tag, profiles[0]["product"], len(bugs))
    except ZenError as e:
        return False, "登录成功（%s），但拉 bug 列表失败：%s" % (tag, e)
    return True, "连接成功（%s；未配产品档案，跳过列表探测）" % tag


# ---------------------------------------------------------------- 排查（triage）

def _ai_triage(bug, profile):
    """AI 兜底排查：单次 LLM 调用判端。不可用/解析失败返回 None（bookmeta 同款配方）。

    图片守卫（#27754 案）：描述指向截图而图片读不到 → unknown 留人工，绝不拿
    纯文本盲判。排查模型不会看图时也不甩人工——自动全库找看图候选（声明
    image_in 的优先，名字推断兜底），候选逐个试、实测吃图成功回写能力标记。
    """
    try:
        from . import modelhub, runner
        images = []
        if _bug_mentions_images(bug):
            try:
                images = _bug_images(bug.get("id"), bug.get("steps"))
            except Exception:
                log.debug("zentao: bug 图片获取失败", exc_info=True)
            if not images:
                return {"side": "unknown",
                        "reason": "描述指向截图/图片但图片未能读取，证据不足，"
                                  "留人工确认后定责",
                        "imgs": 0}
        orch = modelhub.resolve_orchestrator()
        prov, model = orch if orch else (None, None)
        repos = profile.get("repos") or {}
        hints = profile.get("repo_hints") or {}

        def _repo_desc(side):
            hint = str(hints.get(side) or "").strip()
            if hint:
                return hint
            wd = str((repos.get(side) or {}).get("workdir") or "").strip()
            if wd:
                return "目录 " + Path(wd).name
            return "（未配置）"

        lines = ["你是缺陷分诊员：根据缺陷描述判断问题属于哪个仓库端。", "",
                 "【缺陷】#%s %s" % (bug.get("id"), str(bug.get("title") or ""))]
        steps = _strip_html(bug.get("steps"))
        if steps:
            lines.append(steps[:3000])
        if images:
            lines.append("【截图】随本消息附 %d 张 bug 截图，判定前先看图，"
                         "截图与文字冲突时以截图为准。" % len(images))
        lines.append("")
        lines.append("【仓库背景】后端仓库：%s；前端仓库：%s" % (_repo_desc("backend"),
                                                        _repo_desc("frontend")))
        lines.append("")
        lines.append('只输出 JSON（不要别的文字）：{"side": "backend|frontend|both|not_ours", '
                     '"reason": "一句话依据"}')
        lines.append("判定口径：backend=纯后端问题；frontend=纯前端问题；both=两端都要改；"
                     "not_ours=与这两个仓库无关（第三方服务/环境/需求变更/数据问题等）。")
        prompt = "\n".join(lines)
        if images:
            cand = modelhub.vision_candidates(
                prefer_provider_id=(prov or {}).get("id") or "",
                prefer_model=model or "")
            if not cand:
                return {"side": "unknown",
                        "reason": "所有已启用模型都不支持图片输入，无法看图定责"
                                  "（请在模型管理里启用一个视觉模型）",
                        "imgs": 0}
            res, last_err = None, ""
            for p, m, inferred in cand:
                r = modelhub.chat(p["id"], m, prompt, max_tokens=500, timeout=90,
                                  images=images)
                if r.get("ok"):
                    res = r
                    if inferred:
                        # 名字推断的候选实测吃图成功：回写能力标记（幂等），
                        # 模型管理页图徽章同步可见，后续调用直接命中
                        err = modelhub.set_model_caps(p["id"], m, True)
                        if err:
                            log.debug("zentao: 自动标记 image_in 失败：%s", err)
                    break
                last_err = str(r.get("error") or "")
                log.warning("zentao: 视觉排查候选 %s/%s 不可用：%s",
                            p.get("id"), m, last_err[:160])
            if not res:
                return {"side": "unknown",
                        "reason": "看图排查全部候选失败（%s）" % last_err[:160],
                        "imgs": 0}
        else:
            if not orch:
                return None
            res = modelhub.chat(prov["id"], model, prompt, max_tokens=500, timeout=90)
            if not res.get("ok"):
                log.warning("zentao: AI 排查失败：%s", res.get("error"))
                return None
        data = runner.extract_json(res.get("text") or "")
        side = str((data or {}).get("side") or "").strip().lower()
        if side not in TRIAGE_SIDES:
            return None
        return {"side": side, "reason": str((data or {}).get("reason") or "")[:300],
                "imgs": len(images)}
    except Exception:
        log.debug("zentao: AI 排查异常", exc_info=True)
        return None


def _triage(bug, profile, cfg):
    """排查定责：模块路由 > AI > unknown。返回 {side, reason, by, account}。"""
    mid = str(bug.get("module") or "")
    if mid:
        for r in profile.get("module_routes") or []:
            try:
                if int(r.get("module") or 0) != int(mid):
                    continue
            except (TypeError, ValueError):
                continue
            side = str(r.get("side") or "")
            if side in TRIAGE_SIDES:
                return {"side": side, "reason": "模块 #%s 路由规则" % mid,
                        "by": "rule", "account": str(r.get("account") or "").strip()}
    if cfg.get("triage_ai"):
        res = _ai_triage(bug, profile)
        if res:
            res["by"] = "ai"
            res["account"] = ""
            return res
    return {"side": "unknown", "reason": "模块未命中路由且 AI 排查不可用", "by": "fallback",
            "account": ""}


# ---------------------------------------------------------------- 修复任务拉起

def _repo_of(profile, side):
    return (profile.get("repos") or {}).get(side) or dict(_REPO_DEFAULTS)


def _goal_text(bug, side=None, images=0):
    """bug → 修复目标提示词。side 给出时附端约束；images>0 时提示先读附件截图。"""
    bid = bug.get("id")
    lines = ["修复禅道 Bug #%s：%s" % (bid, str(bug.get("title") or "").strip())]
    steps = _strip_html(bug.get("steps"))
    if steps:
        lines.append("")
        lines.append("【重现步骤/问题描述】")
        lines.append(steps[:4000])
    sev, pri = _severity(bug), str(bug.get("pri") or "").strip()
    meta = []
    if sev:
        meta.append("严重度 %s（1 最严重）" % sev)
    if pri:
        meta.append("优先级 %s" % pri)
    if str(bug.get("module") or "").strip():
        meta.append("模块 #%s" % bug["module"])
    env = " / ".join(x for x in (str(bug.get("os") or "").strip(),
                                 str(bug.get("browser") or "").strip()) if x)
    if env:
        meta.append("环境 " + env)
    kw = str(bug.get("keywords") or "").strip()
    if kw:
        meta.append("关键字 " + kw)
    if meta:
        lines.append("")
        lines.append("【元信息】" + "；".join(meta))
    if images:
        lines.append("")
        lines.append("【截图】bug 截图共 %d 张，已放在工作目录 _attachments/（文件名 "
                     "zenbug*）；先读图再动手——需求以截图为准，不要凭文字想象。"
                     % images)
    lines.append("")
    lines.append("【要求】只修这个 bug，不做无关重构；改动最小化；修完自查不引入回归。")
    if side in SIDES:
        lines.append("【端约束】本任务只负责【%s】部分；%s部分由别人处理，不要越界改动。"
                     % (SIDE_CN[side],
                        SIDE_CN["frontend" if side == "backend" else "backend"]))
    lines.append("完成后给出修改说明。")
    return "\n".join(lines)


def _launch_fix(bug, profile, side, cfg):
    """为一个 bug 的某一端建修复任务并立即启动。"""
    bid = bug.get("id")
    repo = _repo_of(profile, side)
    wd = str(repo.get("workdir") or "").strip() or settings.default_workdir()
    title = ("[禅道#%s][%s] %s" % (bid, SIDE_CN.get(side, side),
                                   str(bug.get("title") or "").strip())).strip()[:60]
    # bug 截图随任务走：修复智能体跟排查同样需要看图，否则修出来的就是想象中的 bug
    imgs = []
    try:
        if _bug_mentions_images(bug):
            imgs = _bug_images(bid, bug.get("steps"))
    except Exception:
        imgs = []
    atts = _save_bug_attachments(str(wd), bid, imgs)
    payload = {"type": "code", "title": title, "goal": _goal_text(bug, side, images=len(atts)),
               "workdir": wd}
    if atts:
        payload["attachments"] = atts
    base = str(repo.get("git_rev") or "").strip()
    if not base:
        # 档案没配基线就取落单时点的 HEAD：让修复走任务分支隔离——修完自动
        # 提交在任务分支上，对账合并才真正落库，resolve 才站得住（#27697 案：
        # 无基线任务修完只躺在工作区，闭环永远不会替你提交）。
        base = _head_rev(wd)
    if base:
        payload["git_rev"] = base
    if str(repo.get("verify_command") or "").strip():
        payload["verify_command"] = str(repo["verify_command"]).strip()
    task = None
    run = None
    try:
        task = store.create_task(payload)
        run = store.create_run("orchestration", task["title"], task_id=task["id"])
        store.update_task_status(task["id"], "queued")
        jobs.enqueue({"kind": "orchestration", "run_id": run["id"],
                      "task_id": task["id"]})
        return task, run
    except Exception:
        log.exception("zentao: 修复任务启动失败 bug=%s side=%s task=%s run=%s",
                      bid, side, (task or {}).get("id"), (run or {}).get("id"))
        if run:
            try:
                store.update_run(run["id"], status="failed",
                                 error="禅道修复任务启动失败，本次未排队，请稍后重试",
                                 ended_at=time.strftime("%Y-%m-%d %H:%M:%S"))
            except Exception:
                log.exception("zentao: 修复运行失败收口失败 run=%s", run.get("id"))
        if task:
            try:
                store.update_task_status(task["id"], "failed")
            except Exception:
                log.exception("zentao: 修复任务失败收口失败 task=%s", task.get("id"))
        raise


# ---------------------------------------------------------------- 回写文本

def _diffstat(workdir, task_id):
    """任务分支相对基线的改动统计（人话一行）。拿不到返回空串。"""
    try:
        from . import gitmod, runner
        if task_id and workdir:
            br = gitmod.branch_name(task_id)
            r = runner.run_process(
                argv=["git", "-C", workdir, "diff", "--shortstat", "HEAD..." + br],
                timeout=20)
            if r.get("ok"):
                return (r.get("stdout") or "").strip()
    except Exception:
        log.debug("zentao: diffstat 失败", exc_info=True)
    return ""


def _fix_summary(claim, run, profile, side):
    """我方某一端的修复摘要（转派评论与 resolve 报告共用的事实部分）。"""
    lines = []
    git = (run or {}).get("git") or {}
    commit = str(git.get("commit") or "").strip()
    from_branch = str(git.get("from_branch") or "").strip()
    tid = _task_of(claim, side).get("task_id") or ""
    if commit:
        lines.append("【%s】修复提交 %s%s" % (SIDE_CN.get(side, side), commit,
                     "（已合并回 %s）" % from_branch if from_branch else ""))
    stat = _diffstat(str(_repo_of(profile, side).get("workdir") or ""), tid)
    if stat:
        lines.append("【%s】改动统计：%s" % (SIDE_CN.get(side, side), stat))
    v = (run or {}).get("verdict") or {}
    if v:
        lines.append("【%s】验证结论：%s" % (SIDE_CN.get(side, side),
                     "通过（评审达标）" if v.get("pass") or v.get("publishable") else "完成"))
    return lines


def _task_of(claim, side):
    for t in claim.get("tasks") or []:
        if t.get("side") == side:
            return t
    return {}


def _report_text(claim, runs, profile, cfg):
    """resolve 评论：确定性事实（逐端汇总）。"""
    tri = claim.get("triage") or {}
    lines = ["【CodeBee 自动修复报告】",
             "Bug：#%s %s" % (claim.get("bug_id"), claim.get("title") or "")]
    if tri.get("side"):
        lines.append("排查结论：%s问题（%s）" % (tri.get("side"),
                                               tri.get("reason") or tri.get("by") or "按规则"))
    for t in claim.get("tasks") or []:
        lines.extend(_fix_summary(claim, runs.get(t.get("run_id")), profile, t.get("side")))
    lines.append("（本条由 CodeBee 禅道集成自动回写）")
    return "\n".join(lines)


def _transfer_text(claim, profile, fixed_runs, target_side):
    """转派评论：排查结论 + 我方已做工作 + 请对方继续。"""
    tri = claim.get("triage") or {}
    lines = ["【CodeBee 排查转派】",
             "Bug：#%s %s" % (claim.get("bug_id"), claim.get("title") or ""),
             "排查结论：%s问题——%s" % (SIDE_CN.get(target_side, target_side),
                                      tri.get("reason") or tri.get("by") or "按规则")]
    if tri.get("imgs"):
        lines.append("（判定依据含 %d 张 bug 截图）" % tri["imgs"])
    for side, run in (fixed_runs or []):
        lines.extend(_fix_summary(claim, run, profile, side))
    lines.append("请%s负责人接手处理；本 bug 保持激活，处理完请按正常流程解决。"
                 % SIDE_CN.get(target_side, target_side))
    lines.append("（本条由 CodeBee 禅道集成自动回写）")
    return "\n".join(lines)


def _fail_text(claim, failed_tasks, runs, internal=False):
    tri = claim.get("triage") or {}
    lines = ["【CodeBee 自动修复未成功】",
             "Bug：#%s %s" % (claim.get("bug_id"), claim.get("title") or "")]
    if tri.get("side"):
        lines.append("排查结论：%s（%s）" % (tri.get("side"), tri.get("reason") or ""))
    for t in failed_tasks:
        r = runs.get(t.get("run_id")) or {}
        why = str(r.get("error") or "").strip() or ("运行状态 " + str(r.get("status") or ""))
        lines.append("【%s】失败：%s" % (SIDE_CN.get(t.get("side"), t.get("side")), why[:300]))
    if internal:
        lines.append("（失败原因是 CodeBee 工具自身异常，不代表修复结论；"
                     "请人工排查 CodeBee 或续跑修复任务）")
    lines.append("CodeBee 修复任务：%s（可人工续跑或接管）；本 bug 保持待处理。"
                 % "、".join(t.get("task_id") or "?" for t in claim.get("tasks") or []))
    return "\n".join(lines)


# ---------------------------------------------------------------- 禅道写回动作

def _bug_opened_by(cfg, bug_id):
    try:
        d = _call("GET", "/bugs/%s" % bug_id, cfg=cfg)
        return _acct(d.get("openedBy"))
    except ZenError:
        return ""


def _ensure_resolved(cfg, bug_id, comment, assign_to):
    """resolve（幂等）：已是 resolved/closed 视为成功。"""
    cur = _call("GET", "/bugs/%s" % bug_id, cfg=cfg)
    if str(cur.get("status") or "") in ("resolved", "closed"):
        return True
    body = {"resolution": "fixed", "resolvedBuild": "trunk", "comment": comment}
    if assign_to:
        body["assignedTo"] = assign_to
    operation_id = operations.begin("zentao:bug:%s:resolve" % bug_id, body,
                                    metadata={"bug_id": bug_id, "action": "resolve"})
    try:
        result = _call("POST", "/bugs/%s/resolve" % bug_id, cfg=cfg, body=body)
        receipt = str((result or {}).get("id") or bug_id)
        operations.confirm(operation_id, remote_receipt=receipt,
                           metadata={"bug_id": bug_id, "action": "resolve"})
    except Exception as exc:
        operations.finish_exception(operation_id, exc)
        raise
    return True


def _transfer(cfg, bug_id, target, comment):
    """转派：PUT assignedTo+comment（bug 保持激活）。target 空=只评论。抛 ZenError。

    禅道 PUT /bugs/{id} 不带 assignedTo 会把指派人清空（#27783 案：只评论
    也出了「指派给(空)」记录），空 target 必须先 GET 带回当前指派人；GET
    不到就整个不发——宁可不评论，不可盲写清指派。
    """
    body = {"comment": comment}
    if target:
        body["assignedTo"] = target
    else:
        cur = _call("GET", "/bugs/%s" % bug_id, cfg=cfg)
        body["assignedTo"] = _acct(cur.get("assignedTo")) or ""
    operation_id = operations.begin("zentao:bug:%s:transfer" % bug_id, body,
                                    metadata={"bug_id": bug_id, "action": "transfer"})
    try:
        result = _call("PUT", "/bugs/%s" % bug_id, cfg=cfg, body=body)
        receipt = str((result or {}).get("id") or bug_id)
        operations.confirm(operation_id, remote_receipt=receipt,
                           metadata={"bug_id": bug_id, "action": "transfer"})
    except Exception as exc:
        operations.finish_exception(operation_id, exc)
        raise


def _notify(text):
    try:
        from . import notify
        notify.push_text(text)
    except Exception:
        log.debug("zentao: 群通知失败", exc_info=True)


def _merge_branch(workdir, task):
    try:
        from . import gitmod
        return gitmod.merge_task_branch(workdir, task)
    except Exception as e:
        return False, "合并异常：%s" % e, None


def _head_rev(workdir):
    """工作目录当前 HEAD 短哈希（作任务隔离基线）。非仓库/拿不到返回空串。"""
    try:
        from . import gitmod, runner
        r = runner.run_process(
            argv=["git", "-C", str(workdir), "rev-parse", "--short", "HEAD"], timeout=20)
        if r.get("ok"):
            rev = (r.get("stdout") or "").strip()
            if rev and gitmod.valid_rev(rev):
                return rev
    except Exception:
        log.debug("zentao: 读取 HEAD 失败", exc_info=True)
    return ""


def _uncommitted(workdir):
    """工作目录里有没有未提交的已跟踪改动（untracked 不算——CLI 噪音文件太多）。

    返回 (是否脏, 人话明细)。目录不是 git 仓库视为不脏（无提交语义，放行）。
    """
    try:
        from . import runner
        if not workdir:
            return False, ""
        r = runner.run_process(
            argv=["git", "-C", str(workdir), "status", "--porcelain", "-uno", "--", "."],
            timeout=20)
        if r.get("ok"):
            dirty = [ln.strip() for ln in (r.get("stdout") or "").splitlines() if ln.strip()]
            if dirty:
                return True, "未提交改动 %d 处（如 %s）" % (len(dirty), dirty[0][:80])
    except Exception:
        log.debug("zentao: 工作区状态检查失败", exc_info=True)
    return False, ""


def _set_claim(bid, **patch):
    with _LOCK:
        c = _STATE["claims"].get(str(bid))
        if c is None:
            return
        c.update(patch)
        c["at"] = time.strftime("%Y-%m-%d %H:%M:%S")
        _save_locked()


def _route_account(profile, tri, side):
    """转派/升级目标：模块路由 account > 端负责人。"""
    if tri and tri.get("account"):
        return str(tri["account"]).strip()
    return str((profile.get("owners") or {}).get(side) or "").strip()


# ---------------------------------------------------------------- 对账回写

def _finish_ok(claim, cfg):
    """我方任务全部 done：合并 → （需要则）转派 → 否则 resolve。"""
    bid = str(claim.get("bug_id"))
    profile = _profile_for(cfg, claim.get("product")) or {}
    tri = claim.get("triage") or {}
    tasks = claim.get("tasks") or []
    runs = {t.get("run_id"): store.get_run(t.get("run_id") or "") for t in tasks}
    # 1) 逐任务合并（任一失败即停：不转派不 resolve，绝不带着没落库的修复转派）
    if not cfg.get("auto_merge"):
        _set_claim(bid, state="done_manual",
                   note="修复完成；auto_merge 已关，请人工合并落库后到禅道解决 bug")
        return
    for t in tasks:
        task = store.get_task(t.get("task_id") or "")
        if task is None or not task.get("git_rev"):
            continue
        ok, err, _info = _merge_branch(str(_repo_of(profile, t.get("side")).get("workdir")
                                           or settings.default_workdir()), task)
        if not ok:
            _set_claim(bid, state="merge_failed",
                       note="【%s】任务分支合并失败：%s（不转派不 resolve，留人工）"
                            % (SIDE_CN.get(t.get("side"), t.get("side")), err))
            _notify("🐛❌ 禅道 Bug #%s 的%s修复代码合并失败：%s\n修复任务：%s"
                    % (bid, SIDE_CN.get(t.get("side"), ""), err, t.get("task_id") or "?"))
            return
    # 1.5) 落库闸：没基线的任务不走任务分支隔离，改动只躺在工作区——带着
    # 未提交的已跟踪改动点 resolve 是对禅道撒谎（#27697 案），拦下转人工。
    for t in tasks:
        task = store.get_task(t.get("task_id") or "")
        if task is None or task.get("git_rev"):
            continue
        dirty, detail = _uncommitted(str(_repo_of(profile, t.get("side")).get("workdir")
                                         or settings.default_workdir()))
        if dirty:
            _set_claim(bid, state="done_manual",
                       note="修复完成但工作区有未提交改动（%s），已阻止自动 resolve"
                            "——请人工提交后到禅道解决" % detail)
            _notify("🐛⚠️ 禅道 Bug #%s 修复完成但未提交落库，已阻止自动 resolve\n%s"
                    % (bid, claim.get("title") or ""))
            return
    # 2) 需要转派的端 = 判定端 - 我方端（both 且我方只管一端时非空）
    verdict_sides = ([tri["side"]] if tri.get("side") in SIDES
                     else list(SIDES) if tri.get("side") == "both" else [])
    other_sides = [s for s in verdict_sides if s not in (profile.get("our_sides") or [])]
    if other_sides:
        targets = {}
        for s in other_sides:
            tgt = _route_account(profile, tri, s)
            if not tgt:
                _set_claim(bid, state="need_manual",
                           note="我方已修完，但未配置%s负责人，无法转派——请人工转派" % SIDE_CN[s])
                _notify("🐛⚠️ 禅道 Bug #%s 我方部分已修完，但未配置%s负责人，请人工转派"
                        % (bid, SIDE_CN[s]))
                return
            targets[s] = tgt
        fixed_runs = [(t.get("side"), runs.get(t.get("run_id"))) for t in tasks]
        for s in other_sides:
            try:
                _transfer(cfg, bid, targets[s], _transfer_text(claim, profile, fixed_runs, s))
            except ZenError as e:
                attempts = int(claim.get("attempts") or 0) + 1
                if attempts >= RESOLVE_MAX_ATTEMPTS:
                    _set_claim(bid, attempts=attempts, state="resolve_failed",
                               note="转派连续 %d 次失败：%s" % (attempts, e))
                    _notify("🐛⚠️ 禅道 Bug #%s 修完但转派失败：%s" % (bid, e))
                else:
                    _set_claim(bid, attempts=attempts, state="fixing",
                               note="转派第 %d 次失败，下轮重试：%s" % (attempts, e))
                return
        _set_claim(bid, state="transferred",
                   note="我方（%s）已修完并合并，转派给 %s"
                        % ("/".join(SIDE_CN.get(t.get("side"), "") for t in tasks),
                           "、".join(SIDE_CN[s] + ":" + targets[s] for s in other_sides)))
        _notify("🐛🔁 禅道 Bug #%s 我方已修完，转派 %s\n%s"
                % (bid, "、".join(SIDE_CN[s] + ":" + targets[s] for s in other_sides),
                   claim.get("title") or ""))
        return
    # 3) 全部我方端：resolve（幂等）+ 指回报告人
    if not cfg.get("auto_resolve"):
        _set_claim(bid, state="done_manual",
                   note="修复完成；auto_resolve 已关，请人工确认后到禅道解决 bug")
        return
    report = _report_text(claim, runs, profile, cfg)
    opened = _bug_opened_by(cfg, bid)
    try:
        _ensure_resolved(cfg, bid, report, opened)
    except ZenError as e:
        attempts = int(claim.get("attempts") or 0) + 1
        if attempts >= RESOLVE_MAX_ATTEMPTS:
            _set_claim(bid, attempts=attempts, state="resolve_failed",
                       note="resolve 连续 %d 次失败：%s" % (attempts, e))
            _notify("🐛⚠️ 禅道 Bug #%s 修复完成但回写禅道失败：%s" % (bid, e))
        else:
            _set_claim(bid, attempts=attempts, state="fixing",
                       note="resolve 第 %d 次失败，下轮重试：%s" % (attempts, e))
        return
    _set_claim(bid, state="resolved", note="已 resolve(fixed)%s"
               % ("，指回报告人 " + opened if opened else ""))
    _notify("🐛✅ 禅道 Bug #%s 已修复并 resolve\n%s\n修复任务：%s"
            % (bid, claim.get("title") or "", "、".join(t.get("task_id") or "" for t in tasks)))


# CodeBee 自身崩溃形态：run.error 是裸 Python 异常 repr（如 NameError(...)，
# 0.1.63 _TRANSIENT 缩进事故实测形态）或带 Python traceback。这不是「修不动
# bug」——转派给端负责人等于拿自家工具故障甩锅。冒号形态（TimeoutError: x）
# 多为 CLI/被测仓的真实输出，不算内部崩溃。
_CRASH_REPR_RE = re.compile(r"^[A-Za-z_]\w*(?:Error|Exception|Interrupt)\(")


def _looks_internal_crash(err):
    e = str(err or "").strip()
    if not e:
        return False
    return ("Traceback (most recent call last" in e
            or _CRASH_REPR_RE.match(e) is not None)


def _finish_failed(claim, cfg):
    """我方任一任务失败：评论尝试记录 + 转派该端负责人（有配则转）。

    CodeBee 自身崩溃（内部异常）不转派——工具故障不是「修不动」，转出去是
    甩锅，只评论+群通知人工。负责人未配置同样只评论。
    """
    bid = str(claim.get("bug_id"))
    profile = _profile_for(cfg, claim.get("product")) or {}
    tasks = claim.get("tasks") or []
    runs = {t.get("run_id"): store.get_run(t.get("run_id") or "") for t in tasks}
    failed = [t for t in tasks
              if str((runs.get(t.get("run_id")) or {}).get("status") or "") not in
              ("queued", "running", "done")]
    internal = any(_looks_internal_crash((runs.get(t.get("run_id")) or {}).get("error"))
                   for t in failed)
    text = _fail_text(claim, failed, runs, internal=internal)
    # 升级目标：取第一个失败端的路由账号/负责人（内部崩溃不转派）
    target = ""
    if not internal:
        for t in failed:
            target = _route_account(profile, claim.get("triage") or {}, t.get("side"))
            if target:
                break
    try:
        _transfer(cfg, bid, target, text)
        if internal:
            note = "CodeBee 内部异常，已评论说明（不转派，请人工排查工具链）"
            state = "commented"
        elif target:
            note = "已评论说明并转派 %s" % target
            state = "escalated"
        else:
            side = str((failed[0].get("side") if failed else "") or "")
            note = "已评论说明（%s负责人未配置，未转派）" % SIDE_CN.get(side, side or "该端")
            state = "commented"
    except ZenError as e:
        log.warning("zentao: bug %s 失败评论未送达（群通知兜底）：%s", bid, e)
        note = "修复失败，评论未送达：%s" % e
        state = "commented"
    _set_claim(bid, state=state, note=note)
    why = ("CodeBee 内部异常，未转派" if internal
           else ("已转派 " + target if target else "负责人未配置，未转派"))
    _notify("🐛❌ 禅道 Bug #%s 自动修复未成功（%s）\n%s\n修复任务：%s"
            % (bid, why,
               claim.get("title") or "", "、".join(t.get("task_id") or "" for t in tasks)))


def _refresh_claim_runs(claim):
    """把 claim.tasks 的 run_id 对齐到各任务最新一次运行，返回 (tasks, 是否换绑)。

    领单时钉住的 run 失败后，人工重试会换新 run——对账只认旧 run 会把已经
    成功的修复判成失败（2026-09-22 #27697 误报失败评论案）。
    """
    fresh, changed = [], False
    for t in claim.get("tasks") or []:
        tid = str(t.get("task_id") or "")
        if tid:
            runs = store.task_runs(tid)
            rid = str((runs[0].get("id") if runs else "") or "")
            if rid and rid != t.get("run_id"):
                t = dict(t, run_id=rid)
                changed = True
        fresh.append(t)
    return fresh, changed


def _reconcile(cfg):
    """对账：fixing 中的 claim 查各 run 终态并回写。单条异常只跳过该条。"""
    with _LOCK:
        fixing = [dict(c) for c in _STATE["claims"].values() if c.get("state") == "fixing"]
    for claim in fixing:
        bid = str(claim.get("bug_id"))
        try:
            tasks, moved = _refresh_claim_runs(claim)
            if moved:
                _set_claim(bid, tasks=tasks)
                claim = dict(claim, tasks=tasks)
            runs = {t.get("run_id"): store.get_run(t.get("run_id") or "") for t in tasks}
            if any(r is None for r in runs.values()):
                _set_claim(bid, state="lost", note="运行记录不存在（可能被清理）")
                continue
            states = [str((r or {}).get("status") or "") for r in runs.values()]
            if any(s in ("queued", "running") for s in states):
                continue
            if all(s == "done" for s in states):
                _finish_ok(claim, cfg)
            else:
                _finish_failed(claim, cfg)
        except Exception:
            log.exception("zentao: claim %s 对账异常，跳过", bid)


# ---------------------------------------------------------------- 扫描主流程

def _sides_to_fix(profile, tri):
    """判定端 ∩ 我方端。"""
    tri_side = tri.get("side")
    verdict_sides = [tri_side] if tri_side in SIDES else (list(SIDES) if tri_side == "both" else [])
    return [s for s in verdict_sides if s in (profile.get("our_sides") or [])]


def _route_one(bug, profile, cfg, notify=True):
    """认领一个 bug：排查 → 分流（建任务/转派/留人工）。notify=False 用于
    need_manual 存量的重排查（仍无解时不重复群通知）。返回动作文案或 None。"""
    bid = str(bug.get("id") or "")
    tri = _triage(bug, profile, cfg)
    base_claim = {
        "bug_id": bug.get("id"), "product": profile.get("product"),
        "title": str(bug.get("title") or "")[:120],
        "triage": tri, "tasks": [], "state": "fixing", "note": "",
        "attempts": 0, "claimed_at": time.strftime("%Y-%m-%d %H:%M:%S"),
    }
    opened = _acct(bug.get("openedBy"))

    if tri["side"] == "unknown":
        base_claim["state"] = "need_manual"
        base_claim["note"] = tri.get("reason") or "排查失败，留人工"
        with _LOCK:
            _STATE["claims"][bid] = base_claim
            _save_locked()
        if notify:
            _notify("🐛❓ 禅道 Bug #%s 排查不出归属端（%s），留人工\n%s"
                    % (bid, tri.get("reason") or "", bug.get("title") or ""))
        return "need_manual"

    if tri["side"] == "not_ours":
        target = tri.get("account") or str(profile.get("owners").get("not_ours") or "") \
            or opened
        text = ("【CodeBee 排查转派】\nBug：#%s %s\n排查结论：非我方两个仓库的问题——%s\n"
                "%s转回 %s 核实处理。\n（本条由 CodeBee 禅道集成自动回写）"
                % (bid, bug.get("title") or "", tri.get("reason") or "按规则",
                   ("（判定依据含 %d 张 bug 截图）\n" % tri["imgs"]) if tri.get("imgs") else "",
                   target or "报告人"))
        try:
            _transfer(cfg, bid, target, text)
            note = "非我方，已转派 %s" % (target or "（未指派，仅评论）")
        except ZenError as e:
            base_claim.update({"state": "need_manual", "note": "非我方转派失败：%s" % e})
            with _LOCK:
                _STATE["claims"][bid] = base_claim
                _save_locked()
            _notify("🐛⚠️ 禅道 Bug #%s 判定非我方但转派失败：%s" % (bid, e))
            return "need_manual"
        base_claim.update({"state": "transferred", "note": note})
        with _LOCK:
            _STATE["claims"][bid] = base_claim
            _save_locked()
        _notify("🐛↩️ 禅道 Bug #%s 判定非我方，已转派 %s\n%s"
                % (bid, target or "报告人", bug.get("title") or ""))
        return "transferred"

    sides = _sides_to_fix(profile, tri)
    if not sides:
        # 纯对方端问题（含测试指错到我方账号的）：直接转派正确负责人
        other = tri["side"] if tri["side"] in SIDES else None
        if other is None:            # both 但我方两端都没配：整单转不了，留人工
            base_claim["state"] = "need_manual"
            base_claim["note"] = "双端问题但产品档案未配置我方端，请人工处理"
            with _LOCK:
                _STATE["claims"][bid] = base_claim
                _save_locked()
            _notify("🐛❓ 禅道 Bug #%s 为双端问题但未配我方端，留人工" % bid)
            return "need_manual"
        target = _route_account(profile, tri, other)
        if not target:
            base_claim["state"] = "need_manual"
            base_claim["note"] = "判定为%s问题，但未配置%s负责人，无法转派" % (
                SIDE_CN[other], SIDE_CN[other])
            with _LOCK:
                _STATE["claims"][bid] = base_claim
                _save_locked()
            _notify("🐛⚠️ 禅道 Bug #%s 判定为%s问题但未配负责人，请人工转派"
                    % (bid, SIDE_CN[other]))
            return "need_manual"
        text = _transfer_text({"bug_id": bug.get("id"), "title": bug.get("title") or "",
                               "triage": tri, "tasks": []}, profile, [], other)
        try:
            _transfer(cfg, bid, target, text)
        except ZenError as e:
            base_claim["state"] = "need_manual"
            base_claim["note"] = "%s转派失败：%s" % (SIDE_CN[other], e)
            with _LOCK:
                _STATE["claims"][bid] = base_claim
                _save_locked()
            _notify("🐛⚠️ 禅道 Bug #%s 判定%s问题但转派失败：%s" % (bid, SIDE_CN[other], e))
            return "need_manual"
        base_claim.update({"state": "transferred",
                           "note": "%s问题（测试原指派 %s），已转派 %s"
                                   % (SIDE_CN[other], _acct(bug.get("assignedTo")) or "?", target)})
        with _LOCK:
            _STATE["claims"][bid] = base_claim
            _save_locked()
        _notify("🐛🔁 禅道 Bug #%s 判定%s问题，已转派 %s（原指派 %s）\n%s"
                % (bid, SIDE_CN[other], target, _acct(bug.get("assignedTo")) or "?",
                   bug.get("title") or ""))
        return "transferred"

    # 我方端：逐端建修复任务（任一端建不起来 → 整单留人工，不做半截修复）
    tasks = []
    try:
        for side in sides:
            task, run = _launch_fix(bug, profile, side, cfg)
            tasks.append({"side": side, "task_id": task["id"], "run_id": run["id"],
                          "state": "fixing"})
    except Exception as e:
        log.warning("zentao: bug %s 建修复任务失败：%s", bid, e)
        base_claim["state"] = "need_manual"
        base_claim["note"] = "建修复任务失败：%s" % e
        with _LOCK:
            _STATE["claims"][bid] = base_claim
            _save_locked()
        _notify("🐛⚠️ 禅道 Bug #%s 建修复任务失败：%s" % (bid, e))
        return "need_manual"
    base_claim["tasks"] = tasks
    with _LOCK:
        _STATE["claims"][bid] = base_claim
        _save_locked()
    _notify("🐛🔍 认领禅道 Bug #%s（%s问题·%s）：%s\n修复任务：%s"
            % (bid, tri["side"], "由规则" if tri.get("by") == "rule" else "AI 判定",
               bug.get("title") or "", "、".join(t["task_id"] for t in tasks)))
    return "fixing"


def _scan(cfg):
    """按产品档案逐个拉 bug：认领新 bug + 重试 need_manual 的存量。返回认领数。"""
    claimed = 0
    profiles = _profiles(cfg)
    if not profiles:
        raise ZenError("未配置产品档案——请先在设置页添加产品并配置仓库")
    for profile in profiles:
        bugs = list_bugs(cfg, profile["product"], profile)
        by_id = {str(b.get("id") or ""): b for b in bugs}
        with _LOCK:
            seen = set(_STATE["claims"].keys())
            retry_ids = [k for k, c in _STATE["claims"].items()
                         if c.get("state") == "need_manual" and k in by_id
                         and str(by_id[k].get("status") or "") == "active"]
        for bug in bugs:
            bid = str(bug.get("id") or "")
            if not bid or bid in seen or not _claimable(bug, profile):
                continue
            _route_one(bug, profile, cfg)
            claimed += 1
        # need_manual 重排查（bug 仍激活才出现在列表里）
        for bid in retry_ids:
            with _LOCK:
                if _STATE["claims"].get(bid, {}).get("state") != "need_manual":
                    continue
            _route_one(by_id[bid], profile, cfg, notify=False)
    return claimed


def _poll(force=False):
    """执行一次扫描；同一进程内只允许一个扫描实例。"""
    if not _SCAN_LOCK.acquire(blocking=False):
        return {"ok": False, "claimed": 0, "reconciled": False,
                "error": "扫描正在进行，请稍候", "skipped": "in_progress"}
    try:
        return _poll_unlocked(force=force)
    finally:
        _SCAN_LOCK.release()


def _poll_unlocked(force=False):
    """一次完整轮询：对账回写 + （到点/强制时）扫描认领。异常不外抛。"""
    _ensure_loaded()
    with _LOCK:
        cfg = _cfg()
    out = {"ok": True, "claimed": 0, "reconciled": True, "error": ""}
    try:
        _reconcile(cfg)
    except Exception:
        log.exception("zentao: 对账异常")
    if not force:
        if not cfg.get("poll_enabled"):
            return {"ok": True, "claimed": 0, "reconciled": True, "error": "",
                    "skipped": "poll_disabled"}
        with _LOCK:
            nxt = _STATE.get("next_scan") or ""
        if nxt:
            try:
                if datetime.fromisoformat(nxt) > datetime.now():
                    return {"ok": True, "claimed": 0, "reconciled": True,
                            "error": "", "skipped": "not_due"}
            except ValueError:
                pass
    err = ""
    try:
        out["claimed"] = _scan(cfg)
    except ZenError as e:
        err = str(e)
    except Exception as e:
        err = "扫描失败：%s" % e
        log.warning("zentao: %s", err)
    now = datetime.now()
    delay = timedelta(minutes=max(INTERVAL_MIN, int(cfg.get("interval_minutes") or INTERVAL_DEFAULT)))
    if err:
        delay = timedelta(minutes=RETRY_DELAY_MIN)
    with _LOCK:
        _STATE["last_scan"] = now.strftime("%Y-%m-%d %H:%M:%S")
        _STATE["next_scan"] = (now + delay).strftime("%Y-%m-%d %H:%M:%S")
        _STATE["last_error"] = err
        _save_locked()
    out["error"] = err
    out["ok"] = not err
    return out


def fire_due():
    """automation._tick 每拍调用：内部自节流，没到点/未启用时零开销返回。"""
    _ensure_loaded()
    with _LOCK:
        if not (_STATE.get("config") or {}).get("poll_enabled"):
            return None
    return _poll(force=False)


def scan_now():
    """手动「立即扫描」：绕过轮询闸与节流。"""
    return _poll(force=True)


def _boot_reconcile():
    """服务重启后补一次对账：上个进程死亡窗口里的终态变化别等下个 tick
    （#27697 案：服务死窗 7 小时，失败转派被拖到重启后才发现）。"""
    try:
        _ensure_loaded()
        with _LOCK:
            cfg = _cfg()
        _reconcile(cfg)
    except Exception:
        log.exception("zentao: 启动补对账失败")


def start():
    """服务启动接线：加载状态（含老配置迁移）。"""
    global _BOOT_TIMER
    n = load()
    with _LOCK:
        cfg = _STATE.get("config") or {}
        if cfg.get("poll_enabled") and not _STATE.get("next_scan"):
            _STATE["next_scan"] = time.strftime("%Y-%m-%d %H:%M:%S")
            _save_locked()
        pending = any(c.get("state") == "fixing" for c in _STATE["claims"].values())
    if pending:
        if _BOOT_TIMER is not None:
            _BOOT_TIMER.cancel()
        _BOOT_TIMER = threading.Timer(3.0, _boot_reconcile)
        _BOOT_TIMER.daemon = True
        _BOOT_TIMER.start()
    return n


# ---------------------------------------------------------------- 配置管理

def _norm_profile(p, idx):
    if not isinstance(p, dict):
        raise ValueError("产品档案 #%d 必须是对象" % idx)
    try:
        pid = int(p.get("product") or 0)
    except (TypeError, ValueError):
        raise ValueError("产品档案 #%d 的产品 ID 必须是整数" % idx)
    if pid <= 0:
        raise ValueError("产品档案 #%d 缺产品 ID" % idx)
    prof = dict(_PROFILE_DEFAULTS)
    prof["product"] = pid
    prof["assigned_to"] = str(p.get("assigned_to") or "").strip()
    try:
        prof["severity_cap"] = max(0, min(4, int(p.get("severity_cap") or 0)))
    except (TypeError, ValueError):
        prof["severity_cap"] = 0
    our = [s for s in SIDES if s in (p.get("our_sides") or [])]
    prof["our_sides"] = our
    repos = {"backend": dict(_REPO_DEFAULTS), "frontend": dict(_REPO_DEFAULTS)}
    raw_repos = p.get("repos") if isinstance(p.get("repos"), dict) else {}
    for side in SIDES:
        r = raw_repos.get(side) if isinstance(raw_repos.get(side), dict) else {}
        wd = str(r.get("workdir") or "").strip()
        if wd:
            rp = Path(wd).expanduser()
            if not rp.is_absolute():
                raise ValueError("产品 %d 的%s工作目录必须是绝对路径" % (pid, SIDE_CN[side]))
            wd = str(rp)
        repos[side] = {"workdir": wd,
                       "git_rev": str(r.get("git_rev") or "").strip(),
                       "verify_command": str(r.get("verify_command") or "").strip()}
    # 工作目录留空合法：扫描时回落「默认保存路径」（_launch_fix 同口径），不在此拦截
    prof["repos"] = repos
    hints = {}
    raw_hints = p.get("repo_hints") if isinstance(p.get("repo_hints"), dict) else {}
    for side in SIDES:
        hints[side] = str(raw_hints.get(side) or "").strip()
    prof["repo_hints"] = hints
    owners = {}
    raw_owners = p.get("owners") if isinstance(p.get("owners"), dict) else {}
    for k in ("backend", "frontend", "not_ours"):
        owners[k] = str(raw_owners.get(k) or "").strip()
    prof["owners"] = owners
    routes = []
    for r in (p.get("module_routes") or []):
        if not isinstance(r, dict):
            continue
        try:
            mid = int(r.get("module") or 0)
        except (TypeError, ValueError):
            raise ValueError("产品 %d 的模块路由：模块 ID 必须是整数" % pid)
        if mid <= 0:
            continue
        side = str(r.get("side") or "")
        if side not in TRIAGE_SIDES:
            raise ValueError("产品 %d 的模块路由 side 必须是 %s 之一"
                             % (pid, "/".join(TRIAGE_SIDES)))
        routes.append({"module": mid, "side": side,
                       "account": str(r.get("account") or "").strip()})
    prof["module_routes"] = routes
    return prof


def _norm_profiles(v):
    if v is None:
        return None
    if not isinstance(v, list):
        raise ValueError("product_profiles 必须是数组")
    out, seen = [], set()
    for i, p in enumerate(v):
        prof = _norm_profile(p, i)
        if prof["product"] in seen:
            raise ValueError("产品 %d 配置重复" % prof["product"])
        seen.add(prof["product"])
        out.append(prof)
    return out


def save_config(patch):
    """部分更新配置（password 缺省或空串=不改；product_profiles 整体替换）。
    校验失败抛 ValueError。返回脱敏视图的 config。"""
    _ensure_loaded()
    patch = patch if isinstance(patch, dict) else {}
    with _LOCK:
        cfg = _cfg()
        for k in _UPDATABLE:
            if k not in patch:
                continue
            v = patch[k]
            if k == "base_url":
                v = str(v or "").strip().rstrip("/")
                if v and not v.startswith(("http://", "https://")):
                    raise ValueError("禅道地址必须以 http:// 或 https:// 开头")
            elif k == "account":
                v = str(v or "").strip()
            elif k == "password":
                v = str(v or "")
                if not v:
                    continue
            elif k == "product_profiles":
                v = _norm_profiles(v)
                if v is None:
                    continue
            elif k == "interval_minutes":
                try:
                    v = max(INTERVAL_MIN, min(INTERVAL_MAX, int(v)))
                except (TypeError, ValueError):
                    raise ValueError("interval_minutes 必须是 %d-%d 的整数"
                                     % (INTERVAL_MIN, INTERVAL_MAX))
            elif k in ("auto_resolve", "auto_merge", "triage_ai", "poll_enabled"):
                v = bool(v)
            cfg[k] = v
        _STATE["config"] = cfg
        if cfg.get("poll_enabled"):
            _STATE["next_scan"] = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        _save_locked()
    return view()["config"]


def bug_web_base(cfg=None):
    """给前端拼 bug 详情页用的 web 根：探测出的有效地基（含自动补的 /zentao 子路径）
    优先，没探测过回落用户填的原始地址。返回 stripped 根或空串。"""
    c = dict(cfg) if cfg else _cfg()
    hit = resolved_base_url(c.get("base_url"))
    if hit:
        return hit
    b = re.sub(r"/api\.php/v1$", "", str(c.get("base_url") or "").strip().rstrip("/"))
    return b if b.startswith(("http://", "https://")) else ""


def bug_link_style(cfg=None):
    """bug 详情链接形态：探测缓存优先，没探出来按伪静态（官方默认形态）。"""
    c = dict(cfg) if cfg else _cfg()
    b = str(c.get("base_url") or "").strip().rstrip("/")
    return _WEBSTYLE.get(b) or "pathinfo"


def view():
    """前端视图：配置脱敏（password 只回是否已设）+ claims 列表（新在前）。"""
    _ensure_loaded()
    with _LOCK:
        cfg = dict(_cfg())
        has_pw = bool(cfg.get("password"))
        cfg["password"] = ""
        cfg["has_password"] = has_pw
        bug_base = bug_web_base(cfg)
        claims = sorted(_STATE["claims"].values(),
                        key=lambda c: str(c.get("claimed_at") or ""), reverse=True)
        return {"config": cfg,
                "bug_base": bug_base,
                "bug_style": bug_link_style(cfg),
                "claims": [dict(c) for c in claims],
                "last_scan": _STATE.get("last_scan") or "",
                "next_scan": _STATE.get("next_scan") or "",
                "last_error": _STATE.get("last_error") or ""}


def _test_reset():
    """测试钩子：清空内存状态（配合重绑 _FILE + TUTTI_DATA 临时目录用）。"""
    with _LOCK:
        _STATE["config"] = dict(_CFG_DEFAULTS)
        _STATE["claims"] = {}
        _STATE["last_scan"] = _STATE["next_scan"] = _STATE["last_error"] = ""
        _reset_token()
        _APIBASE.clear()
        _WEBSTYLE.clear()
        _MODE.clear()
        _OLD.update(api="", sid="", at=0.0)
        _OLD_FORM["form"] = ""
        _REST_LAST_ERR["msg"] = ""
        _IMG_CACHE.clear()
        globals()["_LOADED"] = True
