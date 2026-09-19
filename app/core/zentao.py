# -*- coding: utf-8 -*-
"""禅道 Bug 自动修复对接：定时扫描激活 Bug → 自动建 CodeBee 修复任务 → 跑完回写。

数据落盘 <data>/zentao.json（tmp + os.replace 原子写；TUTTI_DATA 环境变量感知）。
修复走与 /api/tasks 完全相同的链路（store.create_task → store.create_run →
jobs.enqueue，code 引擎=实现→验证→评审→修复），不自造运行器。调度挂在
automation._tick（同 publish/auto.fire_due 模式：这里只当被调度者），内部按
interval_hours 节流；停机不追赶，重启后从下一个周期继续。

禅道 REST API v1（开源版 15.x+；请求头 Token: <token>）：
  POST {base}/api.php/v1/tokens               {account, password} → {token}
  GET  {base}/api.php/v1/products/{pid}/bugs  分页 {bugs:[...], page, total, limit}
  GET  {base}/api.php/v1/bugs/{id}            单查（回写前确认状态防谎报）
  POST {base}/api.php/v1/bugs/{id}/resolve    {resolution, resolvedBuild, comment, assignedTo}
  PUT  {base}/api.php/v1/bugs/{id}            {comment}（修复失败说明用，容错）

认领范围（防呆）：products（产品 ID 列表，列表接口按产品维度，必填）+
assigned_to（只认领指派给该账号的 bug，可选）+ severity_cap（严重度上限，
1 最严重，0=不限）。三者 AND；过滤全空 = 一条也不认领。

claim 生命周期（claims[bug_id].state）：
  fixing → run done：auto_merge 且有任务分支 → 先 merge（失败=merge_failed 留
           人工，绝不谎报 resolved）→ resolve(fixed)+报告评论+指回报告人 → resolved
         → run failed/cancelled：PUT 评论说明 + 群通知 → commented（留人工）
  resolve API 连续失败 3 次 → resolve_failed（终态，群通知）

出网边界（SSRF 防护，_guard_url）：本模块的请求目标完全来自用户自己配置的
禅道地址——禅道多部署在内网，因此私网/环回按设计放行；但强制 http(s) 协议、
解析主机并阻断云元数据与链路本地地址、禁跟随重定向。config 只有本机持有
令牌的用户能改，攻击面与「用户自己在浏览器里打开禅道」等价。
"""
from __future__ import annotations

import html as _html
import ipaddress
import json
import logging
import os
import re
import socket
import threading
import time
import urllib.error
import urllib.request
from datetime import datetime, timedelta
from pathlib import Path

from . import jobs, paths, settings, store, tlsctx

log = logging.getLogger(__name__)

_LOCK = threading.RLock()
_FILE = paths.DATA_DIR / "zentao.json"
_STATE = {
    "config": {},        # 持久配置（见 _CFG_DEFAULTS）
    "claims": {},        # str(bug_id) → claim dict
    "last_scan": "",     # 上次实际扫描时间
    "next_scan": "",     # 下次扫描时间（fire_due 节流闸）
    "last_error": "",    # 最近一次扫描/回写的人话错误
}
_LOADED = False

TOKEN_TTL = 23 * 3600          # token 缓存上限（禅道 token 跟随会话过期，宁早勿晚）
HTTP_TIMEOUT = 15
PAGE_LIMIT = 100               # 列表分页大小；总上限 500 条防失控
MAX_BUGS = 500
RESOLVE_MAX_ATTEMPTS = 3       # resolve 连续失败次数上限（超过即终态留人工）
RETRY_DELAY_MIN = 30           # 扫描失败后的重试间隔（分钟）；成功按 interval_hours
INTERVAL_MIN, INTERVAL_MAX = 1, 168   # interval_hours 合法区间（小时）

_CFG_DEFAULTS = {
    "base_url": "",            # 禅道根地址，如 https://zentao.example.com（子目录部署带子目录）
    "account": "",             # 专用账号（建议建 codebee 账号收 bug）
    "password": "",
    "products": [],            # 产品 ID 列表（int）；列表接口按产品维度，必填
    "assigned_to": "",         # 只认领指派给该账号的 bug；空=不按指派过滤
    "severity_cap": 0,         # 严重度上限（1 最严重）；0=不限
    "workdir": "",             # 修复任务的工作目录（空=跟随默认保存路径）
    "git_rev": "",             # 基线分支/提交（检出任务分支 tutti/<id>；空=直改工作目录）
    "verify_command": "",      # 验证命令（code 引擎跑完实现先跑它；空=靠评审把关）
    "auto_resolve": True,      # 修复达标后自动 resolve bug（fixed + 报告评论 + 指回报告人）
    "auto_merge": True,        # 自动把任务分支合并回基线分支（仅配置了 git_rev 时生效）
    "poll_enabled": False,     # 定时扫描总开关
    "interval_hours": 2,       # 扫描间隔（小时）
}

_UPDATABLE = tuple(_CFG_DEFAULTS.keys())


# ---------------------------------------------------------------- 出网边界（SSRF）

_META_HOSTS = {"metadata.google.internal", "metadata.goog"}
_NO_REDIRECT = None            # 进程内缓存：禁重定向的 opener


def _no_redirect_opener():
    """HTTP 重定向一概不跟：目标必须就是用户配置的那台禅道。"""
    global _NO_REDIRECT
    if _NO_REDIRECT is None:
        class _Stop(urllib.request.HTTPRedirectHandler):
            def redirect_request(self, req, fp, code, msg, headers, newurl):
                return None      # 返回 None → urllib 抛 HTTPError，由调用方报人话
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
    except OSError as e:
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
    tmp.write_text(json.dumps({"version": 1, **_STATE},
                              ensure_ascii=False, indent=2), encoding="utf-8")
    os.replace(str(tmp), str(_FILE))


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
        merged = dict(_CFG_DEFAULTS)
        if isinstance(cfg, dict):
            merged.update({k: v for k, v in cfg.items() if k in _CFG_DEFAULTS})
        claims = data.get("claims") if isinstance(data, dict) else None
        _STATE["config"] = merged
        _STATE["claims"] = claims if isinstance(claims, dict) else {}
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


# ---------------------------------------------------------------- 禅道客户端

class ZenError(Exception):
    """禅道接口错误（message 人话，可直接展示）。"""


_TOKEN = {"v": "", "at": 0.0}   # 进程内 token 缓存（账号维度不区分：单实例单配置）


def _reset_token():
    _TOKEN["v"] = ""
    _TOKEN["at"] = 0.0


def _api_base(base_url):
    b = str(base_url or "").strip().rstrip("/")
    if not b:
        raise ZenError("禅道地址未配置")
    if not b.startswith(("http://", "https://")):
        raise ZenError("禅道地址必须以 http:// 或 https:// 开头")
    return b + "/api.php/v1"


def _fetch_token(base_url, account, password):
    """获取新 token 并缓存。失败抛 ZenError。"""
    url = _guard_url(_api_base(base_url) + "/tokens")
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
    if not tok:
        raise ZenError("禅道响应里没有 token——请确认版本 ≥15 且已开启 REST API")
    _TOKEN["v"] = tok
    _TOKEN["at"] = time.time()
    return tok


def _token(cfg, force=False):
    if force or not _TOKEN["v"] or time.time() - _TOKEN["at"] > TOKEN_TTL:
        return _fetch_token(cfg.get("base_url"), cfg.get("account"), cfg.get("password"))
    return _TOKEN["v"]


def _api(method, path, cfg=None, body=None):
    """调禅道 API。返回 dict（非 dict 响应返回 {}）。401/403 自动重取 token 重试一次。"""
    c = cfg or _cfg()
    need_auth = True
    for attempt in (1, 2):
        headers = {"Accept": "application/json"}
        if need_auth:
            headers["Token"] = _token(c, force=(attempt == 2))
        url = _guard_url(_api_base(c.get("base_url")) + path)
        data = None
        if body is not None:
            data = json.dumps(body, ensure_ascii=False).encode("utf-8")
            headers["Content-Type"] = "application/json"
        req = urllib.request.Request(url, data=data, method=method, headers=headers)
        try:
            with _no_redirect_opener().open(req, timeout=HTTP_TIMEOUT) as r:
                out = json.loads((r.read() or b"{}").decode("utf-8", "replace"))
            return out if isinstance(out, dict) else {}
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


def _acct(v):
    """assignedTo/openedBy 兼容：新版返回 {account,...} 用户对象，老版直接是账号串。"""
    if isinstance(v, dict):
        return str(v.get("account") or "").strip()
    return str(v or "").strip()


def _strip_html(raw):
    """steps 字段是富文本 HTML：剥标签转纯文本（禅道编辑器产物，保真即可不必优雅）。"""
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


def _claimable(bug, cfg):
    """认领过滤：active + 产品 + 指派 + 严重度（AND）。防呆：过滤全空不认领。"""
    if str(bug.get("status") or "") != "active":
        return False
    products = [int(p) for p in (cfg.get("products") or []) if str(p).strip()]
    assigned = str(cfg.get("assigned_to") or "").strip()
    cap = int(cfg.get("severity_cap") or 0)
    if not products and not assigned:
        return False
    if products:
        try:
            if int(bug.get("product") or 0) not in products:
                return False
        except (TypeError, ValueError):
            return False
    if assigned and _acct(bug.get("assignedTo")) != assigned:
        return False
    if cap and _severity(bug) > cap:
        return False
    return True


def list_bugs(cfg, product_id):
    """拉一个产品下的 bug（分页，总量封顶 MAX_BUGS）。返回原始 bug dict 列表。"""
    out = []
    for page in range(1, 6):
        d = _api("GET", "/products/%s/bugs?page=%d&limit=%d" % (product_id, page, PAGE_LIMIT),
                 cfg=cfg)
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


def test_connection(base_url=None, account=None, password=None):
    """连接测试：取 token + 拉第一个产品的 bug 列表（配置了产品时）。返回 (ok, 人话结果)。"""
    c = _cfg()
    base_url = str(base_url if base_url is not None else c.get("base_url") or "").strip()
    account = str(account if account is not None else c.get("account") or "").strip()
    password = str(password if password is not None else c.get("password") or "").strip()
    if not (base_url and account and password):
        return False, "地址、账号、密码都要填全"
    try:
        _fetch_token(base_url, account, password)
    except ZenError as e:
        return False, str(e)
    try:
        products = [str(p) for p in (c.get("products") or []) if str(p).strip()]
        if products:
            bugs = list_bugs({"base_url": base_url, "account": account, "password": password},
                             products[0])
            return True, "连接成功，产品 %s 可访问（当前 %d 条 bug 在列表里）" % (products[0], len(bugs))
    except ZenError as e:
        return False, "令牌拿到了，但拉 bug 列表失败：%s" % e
    return True, "连接成功（未配产品 ID，跳过列表探测）"


# ---------------------------------------------------------------- 修复任务拉起

def _workdir(cfg):
    wd = str(cfg.get("workdir") or "").strip()
    if wd:
        return wd
    return settings.default_workdir()


def _goal_text(bug):
    """bug → 修复目标提示词。只描述事实 + 约束，不带任何解决方案臆测。"""
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
    lines.append("")
    lines.append("【要求】只修这个 bug，不做无关重构；改动最小化；"
                 "修完自查不引入回归。完成后给出修改说明。")
    return "\n".join(lines)


def _launch_fix(bug, cfg):
    """为一个 bug 建修复任务并入队。与 automation._launch_run 同一条链。返回 (task, run)。"""
    bid = bug.get("id")
    title = ("[禅道#%s] %s" % (bid, str(bug.get("title") or "").strip())).strip()[:60]
    payload = {"type": "code", "title": title, "goal": _goal_text(bug),
               "workdir": _workdir(cfg)}
    if str(cfg.get("git_rev") or "").strip():
        payload["git_rev"] = str(cfg["git_rev"]).strip()
    if str(cfg.get("verify_command") or "").strip():
        payload["verify_command"] = str(cfg["verify_command"]).strip()
    task = store.create_task(payload)
    run = store.create_run("orchestration", task["title"], task_id=task["id"])
    store.update_task_status(task["id"], "queued")
    jobs.enqueue({"kind": "orchestration", "run_id": run["id"], "task_id": task["id"]})
    return task, run


# ---------------------------------------------------------------- 对账回写

def _diffstat(workdir, claim):
    """任务分支相对基线的改动统计（人话一行）。拿不到返回空串（尽力而为）。"""
    try:
        from . import gitmod, runner
        task = store.get_task(claim.get("task_id") or "")
        if task is None:
            return ""
        br = gitmod.branch_name(task["id"])
        r = runner.run_process(
            argv=["git", "-C", workdir, "diff", "--shortstat", "HEAD..." + br],
            timeout=20)
        if r.get("ok"):
            return (r.get("stdout") or "").strip()
    except Exception:
        log.debug("zentao: diffstat 失败", exc_info=True)
    return ""


def _report_text(claim, run, task, cfg):
    """回写评论：只写确定性事实（状态/验证/合并/改动统计），不抄模型输出。"""
    bid = claim.get("bug_id")
    lines = ["【CodeBee 自动修复报告】", "Bug：#%s %s" % (bid, claim.get("title") or "")]
    lines.append("修复任务：%s" % (task.get("title") if task else claim.get("task_id") or "?"))
    git = run.get("git") or {}
    commit = str(git.get("commit") or "").strip()
    from_branch = str(git.get("from_branch") or "").strip()
    if commit:
        lines.append("代码提交：%s" % commit)
    if from_branch:
        lines.append("已合并回基线分支 %s（自动裁决）" % from_branch)
    elif task is not None and task.get("git_rev"):
        lines.append("代码在任务分支上，待人工到任务详情页「版本」页签裁决合并")
    stat = _diffstat(_workdir(cfg), claim)
    if stat:
        lines.append("改动统计：%s" % stat)
    v = run.get("verdict") or {}
    if v:
        lines.append("验证结论：%s" % ("通过（评审达标）" if v.get("pass") or v.get("publishable")
                                       else "完成"))
    lines.append("（本条由 CodeBee 禅道集成自动回写）")
    return "\n".join(lines)


def _fail_text(claim, run):
    why = str(run.get("error") or "").strip() or ("运行状态 " + str(run.get("status") or ""))
    return ("【CodeBee 自动修复未成功】\nBug：#%s %s\n原因：%s\n"
            "已在 CodeBee 留下修复任务（%s），可人工续跑或接管；本 bug 保持待处理。"
            % (claim.get("bug_id"), claim.get("title") or "", why[:400],
               claim.get("task_id") or "?"))


def _bug_opened_by(cfg, bug_id):
    """回指报告人用：拉当前 bug 详情取 openedBy；拿不到返回空串（不指派）。"""
    try:
        d = _api("GET", "/bugs/%s" % bug_id, cfg=cfg)
        return _acct(d.get("openedBy"))
    except ZenError:
        return ""


def _ensure_resolved(cfg, bug_id, comment, assign_to):
    """resolve（幂等）：已是 resolved/closed 视为成功，不再重复 resolve。"""
    cur = _api("GET", "/bugs/%s" % bug_id, cfg=cfg)
    if str(cur.get("status") or "") in ("resolved", "closed"):
        return True
    body = {"resolution": "fixed", "resolvedBuild": "trunk", "comment": comment}
    if assign_to:
        body["assignedTo"] = assign_to
    _api("POST", "/bugs/%s/resolve" % bug_id, cfg=cfg, body=body)
    return True


def _comment_only(cfg, bug_id, text):
    """修复失败说明（容错）：PUT comment 字段；接口不支持/失败也无所谓（群通知兜底）。"""
    try:
        _api("PUT", "/bugs/%s" % bug_id, cfg=cfg, body={"comment": text})
        return True
    except ZenError as e:
        log.warning("zentao: bug %s 评论失败（群通知兜底）：%s", bug_id, e)
        return False


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


def _set_claim(bid, **patch):
    with _LOCK:
        c = _STATE["claims"].get(str(bid))
        if c is None:
            return
        c.update(patch)
        c["at"] = time.strftime("%Y-%m-%d %H:%M:%S")
        _save_locked()


def _finish_ok(claim, run, task, cfg):
    """run done 的回写：先合并（可选）再 resolve（可选）。状态推进与群通知。"""
    bid = str(claim.get("bug_id"))
    merged = False
    if task is not None and task.get("git_rev"):
        if cfg.get("auto_merge"):
            ok, err, _info = _merge_branch(_workdir(cfg), task)
            if not ok:
                _set_claim(bid, state="merge_failed",
                           note="任务分支合并失败：%s（bug 未 resolve，留人工处理）" % err)
                _notify("🐛❌ 禅道 Bug #%s 修复代码合并失败：%s\n修复任务：%s"
                        % (bid, err, claim.get("task_id") or "?"))
                return
            merged = True
        # auto_merge 关：代码留任务分支人工裁决，bug 是否 resolve 跟随 auto_resolve
    if not cfg.get("auto_resolve"):
        _set_claim(bid, state="done_manual",
                   note="修复完成；auto_resolve 已关，请人工确认后到禅道解决 bug")
        return
    report = _report_text(claim, run, task, cfg)
    opened = _bug_opened_by(cfg, claim.get("bug_id"))
    try:
        _ensure_resolved(cfg, claim.get("bug_id"), report, opened)
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
    _set_claim(bid, state="resolved",
               note="已 resolve(fixed)%s%s" % ("，已合并回 " + str((run.get("git") or {}).get("from_branch") or "")
                                              if merged else "",
                                              "，指回报告人 " + opened if opened else ""))
    _notify("🐛✅ 禅道 Bug #%s 已修复并 resolve\n%s\n修复任务：%s"
            % (bid, claim.get("title") or "", claim.get("task_id") or "?"))


def _reconcile(cfg):
    """对账：fixing 中的 claim 查 run 终态并回写。单条异常只跳过该条。"""
    with _LOCK:
        fixing = [dict(c) for c in _STATE["claims"].values() if c.get("state") == "fixing"]
    for claim in fixing:
        bid = str(claim.get("bug_id"))
        try:
            run = store.get_run(claim.get("run_id") or "")
            if run is None:
                _set_claim(bid, state="lost", note="运行记录不存在（可能被清理）")
                continue
            st = str(run.get("status") or "")
            if st in ("queued", "running"):
                continue
            task = store.get_task(claim.get("task_id") or "")
            if st == "done":
                _finish_ok(claim, run, task, cfg)
            else:   # failed / cancelled
                _comment_only(cfg, claim.get("bug_id"), _fail_text(claim, run))
                _set_claim(bid, state="commented",
                           note="修复未成功（%s），已评论说明，留人工" % st)
                _notify("🐛❌ 禅道 Bug #%s 自动修复未成功（%s），已在禅道留评论\n修复任务：%s"
                        % (bid, st, claim.get("task_id") or "?"))
        except Exception:
            log.exception("zentao: claim %s 对账异常，跳过", bid)


# ---------------------------------------------------------------- 扫描主流程

def _scan(cfg):
    """拉全部配置产品的 bug，认领新 bug 建修复任务。返回认领数。"""
    seen = _STATE["claims"]
    claimed = 0
    wd = Path(_workdir(cfg))
    if not wd.is_absolute():
        raise ZenError("工作目录必须是绝对路径：%s" % wd)
    products = [str(p).strip() for p in (cfg.get("products") or []) if str(p).strip()]
    if not products:
        raise ZenError("未配置产品 ID——禅道列表接口按产品维度，请先在配置里填产品 ID")
    for pid in products:
        for bug in list_bugs(cfg, pid):
            if not _claimable(bug, cfg):
                continue
            bid = str(bug.get("id") or "")
            if not bid or bid in seen:
                continue
            try:
                task, run = _launch_fix(bug, cfg)
            except Exception as e:
                log.warning("zentao: bug %s 建修复任务失败：%s", bid, e)
                _STATE["last_error"] = "Bug #%s 建任务失败：%s" % (bid, e)
                continue
            with _LOCK:
                _STATE["claims"][bid] = {
                    "bug_id": bug.get("id"), "product": pid,
                    "title": str(bug.get("title") or "")[:120],
                    "task_id": task["id"], "run_id": run["id"],
                    "state": "fixing", "note": "",
                    "attempts": 0, "claimed_at": time.strftime("%Y-%m-%d %H:%M:%S"),
                }
                _save_locked()
            claimed += 1
            _notify("🐛🔍 认领禅道 Bug #%s：%s\n修复任务已排队：%s"
                    % (bid, bug.get("title") or "", task["id"]))
    return claimed


def _poll(force=False):
    """一次完整轮询：对账回写 + （到点/强制时）扫描认领。返回摘要 dict。

    异常不外抛：记 last_error 返回 ok=False，绝不影响 automation 调度线程。
    """
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
    delay = timedelta(hours=max(INTERVAL_MIN, int(cfg.get("interval_hours") or 2)))
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
    """手动「立即扫描」：绕过轮询闸与节流。返回 _poll 摘要。"""
    return _poll(force=True)


def start():
    """服务启动接线：加载状态。若启用轮询但 next_scan 缺失，下一个 tick 即扫描。"""
    n = load()
    with _LOCK:
        cfg = _STATE.get("config") or {}
        if cfg.get("poll_enabled") and not _STATE.get("next_scan"):
            _STATE["next_scan"] = time.strftime("%Y-%m-%d %H:%M:%S")
            _save_locked()
    return n


# ---------------------------------------------------------------- 配置管理

def _norm_products(v):
    if v is None:
        return None
    if not isinstance(v, list):
        raise ValueError("products 必须是产品 ID 数组")
    out = []
    for x in v:
        try:
            n = int(x)
        except (TypeError, ValueError):
            raise ValueError("产品 ID 必须是整数：%r" % (x,))
        if n > 0:
            out.append(n)
    return out


def save_config(patch):
    """部分更新配置（password 缺省或空串=不改）。校验失败抛 ValueError。返回脱敏视图。"""
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
            elif k in ("account", "assigned_to", "workdir", "git_rev", "verify_command"):
                v = str(v or "").strip()
                if k == "workdir" and v:
                    p = Path(v).expanduser()
                    if not p.is_absolute():
                        raise ValueError("工作目录必须是绝对路径")
                    v = str(p)
            elif k == "password":
                v = str(v or "")
                if not v:
                    continue          # 空 = 不修改密码
            elif k == "products":
                v = _norm_products(v)
                if v is None:
                    continue
            elif k == "severity_cap":
                try:
                    v = max(0, min(4, int(v)))
                except (TypeError, ValueError):
                    raise ValueError("severity_cap 必须是 0-4 的整数")
            elif k == "interval_hours":
                try:
                    v = max(INTERVAL_MIN, min(INTERVAL_MAX, int(v)))
                except (TypeError, ValueError):
                    raise ValueError("interval_hours 必须是 %d-%d 的整数"
                                     % (INTERVAL_MIN, INTERVAL_MAX))
            elif k in ("auto_resolve", "auto_merge", "poll_enabled"):
                v = bool(v)
            cfg[k] = v
        _STATE["config"] = cfg
        # 轮询节奏变化即刻生效：下一拍即按新节奏扫描
        if cfg.get("poll_enabled"):
            _STATE["next_scan"] = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        _save_locked()
    return view()["config"]


def view():
    """前端视图：配置脱敏（password 只回是否已设）+ claims 列表（新在前）。"""
    _ensure_loaded()
    with _LOCK:
        cfg = dict(_cfg())
        has_pw = bool(cfg.get("password"))
        cfg["password"] = ""
        cfg["has_password"] = has_pw
        claims = sorted(_STATE["claims"].values(),
                        key=lambda c: str(c.get("claimed_at") or ""), reverse=True)
        return {"config": cfg,
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
        globals()["_LOADED"] = True
