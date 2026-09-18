# -*- coding: utf-8 -*-
"""遥测回传（L2）+ 版本 ping（L3）+ 诊断包构建（L1）。

设计原则：
- 默认只传「匿名错误元数据」：errorlog 台账里的结构化失败记录（厂商/模型/原因码/
  脱敏摘录/版本/OS），不含任何任务正文、章节内容、密钥、绝对路径；
- 上传前对每条记录再兜一道 scrub_text（不信任入台账时的第一道）；
- 开关 settings.telemetry_errors（默认开）；关掉后什么都不出机器（含版本 ping）；
- 网络失败静默吞掉：遥测永远不影响业务，也不制造新错误；
- 端点：CloudBase HTTP 函数（国内可达、零运维），可用环境变量
  TUTTI_TELEMETRY_URL 覆盖；端点为空/未配置时整个模块自动休眠。

SSRF 防线（端点可被 env 覆盖，所以请求前必须自证安全）：
  https 限定 + 域名后缀白名单 + 解析 IP 拒绝私网/环回/链路本地。

线程模型：start_background() 起一条 daemon 线程，首轮延迟 45s（不挡启动），
之后每 6 小时一轮（ping 每轮一次，错误按游标增量上传）。
"""
from __future__ import annotations

import io
import ipaddress
import json
import os
import platform
import socket
import threading
import time
import urllib.request
import zipfile

from . import errorlog, paths, settings

# 端点：部署后把 CloudBase HTTP 函数 URL 填到这里（见 cloudfunctions/telemetry-collect/）
ENDPOINT = os.environ.get("TUTTI_TELEMETRY_URL", "").strip()

# 请求只允许发往我们自己的收集域（后缀匹配，大小写不敏感）；
# 测试需要假端点时 monkeypatch 本常量或直接 patch _post。
ALLOWED_SUFFIXES = (".tcloudbase.com", ".tencentyun.com", ".tencentcs.com")

FIRST_DELAY_S = 45
INTERVAL_S = 6 * 3600
BATCH_LIMIT = 200
TIMEOUT_S = 10

_thread_started = False


def _cursor_path():
    """游标文件路径惰性计算：测试会把 paths.ERRORS_DIR 重定向到临时目录，
    import 期绑定会钉死在真实数据目录（与 settings._FILE 同一个坑）。"""
    return paths.ERRORS_DIR / "upload.cursor"


def enabled():
    try:
        return bool(settings.load().get("telemetry_errors", True))
    except Exception:
        return False


def _os_tag():
    return {"win32": "windows", "darwin": "macos"}.get(platform.system().lower(),
                                                      platform.system().lower())


def _version():
    try:
        from . import selfupdate
        return str(selfupdate.package_version() or "dev")
    except Exception:
        return "dev"


def _endpoint_safe(url):
    """端点自证安全：https + 域名后缀白名单 + 解析 IP 全部公网。
    不合格返回 ""（调用方静默跳过——遥测绝不为安全边界让步）。"""
    if not url:
        return ""
    try:
        from urllib.parse import urlparse
        p = urlparse(url)
        if p.scheme != "https" or not p.hostname:
            return ""
        host = p.hostname.lower()
        if not any(host == s.lstrip(".") or host.endswith(s)
                   for s in ALLOWED_SUFFIXES):
            return ""
        for info in socket.getaddrinfo(host, 443, proto=socket.IPPROTO_TCP):
            ip = ipaddress.ip_address(str(info[4][0]))
            if (ip.is_private or ip.is_loopback or ip.is_link_local
                    or ip.is_reserved or ip.is_multicast or ip.is_unspecified):
                return ""
        return url
    except Exception:
        return ""


def _post(url, payload):
    """POST JSON 到已通过 _endpoint_safe 校验的 URL；返回状态码，异常返回 0。"""
    safe = _endpoint_safe(url)
    if not safe:
        return 0
    try:
        req = urllib.request.Request(
            safe, data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
            headers={"Content-Type": "application/json",
                     "User-Agent": "CodeBee-Telemetry/1"},
            method="POST")
        with urllib.request.urlopen(req, timeout=TIMEOUT_S) as resp:
            return int(resp.status or 0)
    except Exception:
        return 0


# ---------------------------------------------------------------- 上报循环

def _read_cursor():
    try:
        return _cursor_path().read_text(encoding="utf-8").strip()
    except Exception:
        return ""


def _write_cursor(v):
    try:
        p = _cursor_path()
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(str(v), encoding="utf-8")
    except Exception:
        pass


def _sanitize_record(r):
    """上传前的最后一道闸：只挑白名单字段，detail 再过一次 scrub。"""
    keep = ("id", "ts", "day", "category", "reason", "provider", "model",
            "tool", "role", "run_id", "task_id", "step", "exit_code",
            "app_version", "os")
    out = {k: r.get(k) for k in keep if k in r}
    out["detail"] = errorlog.scrub_text(r.get("detail") or "", limit=600)
    return out


def run_once():
    """一轮上报：版本 ping + 增量错误批量。返回 (ping_ok, uploaded_n)。"""
    if not enabled() or not ENDPOINT:
        return (False, 0)
    ping_ok = _post(ENDPOINT, {
        "kind": "ping", "app": "codebee", "version": _version(),
        "os": _os_tag(), "python": "%d.%d" % platform.python_version_tuple()[:2],
        "ts": time.strftime("%Y-%m-%d %H:%M:%S"),
    }) == 200
    uploaded = 0
    cursor = _read_cursor()
    batch, new_cursor = errorlog.pending_since(cursor, limit=BATCH_LIMIT)
    if batch:
        payload = {"kind": "errors", "records": [_sanitize_record(r) for r in batch]}
        if _post(ENDPOINT, payload) == 200:
            _write_cursor(new_cursor)
            uploaded = len(batch)
    return (ping_ok, uploaded)


def _loop():
    while True:
        try:
            run_once()
        except Exception:
            pass
        time.sleep(INTERVAL_S)


def start_background():
    """启动上报线程（daemon，首轮延迟 45s）。重复调用安全；未配端点直接休眠。"""
    global _thread_started
    if _thread_started or not ENDPOINT:
        return
    _thread_started = True

    def _delayed():
        time.sleep(FIRST_DELAY_S)
        try:
            run_once()
        except Exception:
            pass
        _loop()

    threading.Thread(target=_delayed, name="telemetry-up", daemon=True).start()


# ---------------------------------------------------------------- 诊断包（L1）

def build_bundle_bytes(days=30):
    """构建「诊断包」zip 字节流：脱敏错误台账 + 用量台账 + 环境元信息。

    只装可安全外发的聚合数据——绝不打包 tasks/runs/models.json（后者含密钥）。
    错误记录出包前再 scrub 一遍（与上传同一道闸，出机器的东西不过两道不放行）。
    """
    meta = {
        "app": "CodeBee",
        "version": _version(),
        "os": _os_tag(),
        "os_version": platform.platform(),
        "python": platform.python_version(),
        "telemetry_enabled": enabled(),
        "generated_at": time.strftime("%Y-%m-%d %H:%M:%S"),
        "note": "此包只含匿名错误元数据与用量统计；已剥离密钥、路径与任务内容。",
    }
    errors = [_sanitize_record(r) for r in errorlog.iter_records(days)]
    buf = io.BytesIO()
    stamp = time.strftime("%Y%m%d-%H%M%S")
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
        z.writestr("codebee-diag-%s/meta.json" % stamp,
                   json.dumps(meta, ensure_ascii=False, indent=2))
        z.writestr("codebee-diag-%s/errors-%ddays.jsonl" % (stamp, days),
                   "\n".join(json.dumps(r, ensure_ascii=False) for r in errors))
        usage_lines = []
        try:
            from . import usage
            for r in usage._iter_records(days):
                usage_lines.append(json.dumps(r, ensure_ascii=False))
        except Exception:
            pass
        z.writestr("codebee-diag-%s/usage-%ddays.jsonl" % (stamp, days),
                   "\n".join(usage_lines))
    return buf.getvalue()


# ---------------------------------------------------------------- 一键反馈 Issue（免费通道）

# 反馈目标仓库（Issue 预填正文，由用户在浏览器亲手提交——不自动回传）
ISSUE_URL = "https://github.com/Vercel-By-WXP/CodeBee/issues/new"


def issue_report(days=30, max_groups=8, max_recent=10, detail_chars=120):
    """本地错误台账 → GitHub Issue 预填摘要。返回 {title, body}。

    聚合 + 明细全部来自已脱敏的 errorlog 台账，出正文前再 scrub 一道
    （与上传/诊断包同一纪律：出机器的东西过两道不放行）。明细压成单行，
    方便 URL 预填。days=0 表示全部历史。
    """
    errors = errorlog.iter_records(days)
    groups = {}
    for r in errors:
        key = (str(r.get("reason") or "UNKNOWN"),
               str(r.get("provider") or "-"),
               str(r.get("model") or "-"))
        g = groups.setdefault(key, {"n": 0, "last": ""})
        g["n"] += 1
        if str(r.get("ts") or "") > g["last"]:
            g["last"] = str(r.get("ts") or "")
    top = sorted(groups.items(), key=lambda kv: -kv[1]["n"])[:max_groups]
    version = _version()

    if top:
        t_reason, t_prov, t_model = top[0][0]
        title = "错误反馈：%s ×%d（v%s / %s）" % (
            t_reason, top[0][1]["n"], version, _os_tag())
    else:
        title = "CodeBee 错误反馈（无自动记录的错误）"

    lines = [
        "## CodeBee 错误反馈", "",
        "- 版本：v%s（%s）" % (version, _os_tag()),
        "- Python：%s" % platform.python_version(),
        "- 生成时间：%s" % time.strftime("%Y-%m-%d %H:%M:%S"),
        "",
        ("近 %d 天失败聚合（原因 × 次数）" % days) if days
        else "全部历史失败聚合（原因 × 次数）",
    ]
    if top:
        lines.append("| 原因 | 供应商/模型 | 次数 | 最近一次 |")
        lines.append("|---|---|---|---|")
        for (reason, prov, model), g in top:
            lines.append("| %s | %s/%s | %d | %s |" % (reason, prov, model,
                                                       g["n"], g["last"]))
    else:
        lines.append("（本机错误台账为空）")
    lines += ["", "## 最近失败明细（最多 %d 条，已脱敏）" % max_recent]
    if errors:
        for r in errors[-max_recent:]:
            detail = errorlog.scrub_text(r.get("detail") or "", limit=detail_chars)
            detail = detail.replace("\r", " ").replace("\n", " ")
            lines.append("- `%s` %s [%s/%s] %s" % (
                str(r.get("ts") or ""), str(r.get("reason") or "UNKNOWN"),
                str(r.get("provider") or "-"), str(r.get("model") or "-"), detail))
    else:
        lines.append("（无）")
    lines += ["", "> 提示：可在 设置 → 关于与更新 → 导出诊断包 生成 zip 附在本 Issue"
              "（含更完整的脱敏错误记录与用量统计）。", ""]
    return {"title": title, "body": "\n".join(lines)}
