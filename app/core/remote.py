# -*- coding: utf-8 -*-
"""手机/远程访问：访问令牌 + 多端控制权锁 + 服务地址探测。

令牌：首次启动生成，落盘 data/remote.json。本机（127.0.0.1）请求豁免，
     非 loopback 请求必须带令牌（?token= 或 X-CodeBee-Token 头）。
控制权：同一时刻只有一台设备能执行写操作。空闲自动接管、45s 无心跳自动释放，
       可强制抢夺。纯内存状态，重启即清空。
"""
from __future__ import annotations

import ipaddress
import json
import re
import secrets
import shutil
import subprocess
import sys
import threading
import time
from urllib.parse import urlparse

from . import atomic, paths

# ---------------------------------------------------------------- 访问令牌
_TOK_LOCK = threading.Lock()
_TOKEN = ""


def token() -> str:
    """读取（必要时生成）访问令牌。"""
    global _TOKEN
    if _TOKEN:
        return _TOKEN
    with _TOK_LOCK:
        if _TOKEN:
            return _TOKEN
        p = paths.DATA_DIR / "remote.json"
        try:
            data = json.loads(p.read_text(encoding="utf-8"))
            _TOKEN = str(data.get("token") or "")
        except Exception:
            _TOKEN = ""
        if not _TOKEN:
            # New installations get 256 bits of entropy.  Existing persisted
            # tokens remain valid for backwards compatibility; only the
            # generation format changes.
            _TOKEN = secrets.token_urlsafe(32)
            try:
                atomic.write_json(
                    p,
                    {"token": _TOKEN,
                     "created": time.strftime("%Y-%m-%d %H:%M:%S")},
                    mode=0o600)
            except Exception:
                pass  # 落盘失败也照常工作（内存令牌，重启更换）
        return _TOKEN


# ---------------------------------------------------------------- 反向代理感知
# Cloudflare Tunnel / frp 等都从本机回源：若不感知代理，公网请求一律被当成
# 127.0.0.1 而豁免令牌——等于把控制台裸奔到公网。--trusted-proxy / TUTTI_TRUST_PROXY
# 开启后：带转发头（CF-Connecting-IP / X-Forwarded-For）的请求视为经代理进来的
# 远程请求，必须带令牌；不带转发头的 loopback 仍是真本机（浏览器直接开 localhost）。
# 安全性：Cloudflare 边缘强制注入/覆盖 CF-Connecting-IP，外部无法伪造透传；
# 而能直连本机端口的攻击者伪造转发头只会让自己从"本机豁免"变成"必须带令牌"。
_TRUST_PROXY = False
PUBLIC_URL = ""  # --public-url / 快速隧道：扫码弹框优先展示的公网地址
PUBLIC_URL_KIND = ""  # quick（trycloudflare 临时，重启变）| fixed（自有域名）
_BIND_HOST = "127.0.0.1"  # 默认只监听本机，禁止生成不可达的局域网直连码


def set_trusted_proxy(on: bool, public_url: str = ""):
    global _TRUST_PROXY, PUBLIC_URL
    _TRUST_PROXY = bool(on)
    if public_url:
        PUBLIC_URL = str(public_url).rstrip("/")


def set_bind_host(host: str):
    """Record the HTTP bind address so direct URLs only advertise reachable hops."""
    global _BIND_HOST
    _BIND_HOST = str(host or "").strip().lower()


_HOST_LABEL_RE = re.compile(
    r"^[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?"
    r"(?:\.[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?)*$", re.I)
_LOOPBACK_HOSTS = {"localhost", "127.0.0.1", "::1"}
_WILDCARD_HOSTS = {"0.0.0.0", "::"}


def _normalized_host(value):
    """Return a canonical host from an HTTP Host value, or an empty string.

    Ports are parsed to reject malformed authority values but intentionally
    excluded from identity: reverse proxies commonly expose :443/:80 while
    forwarding to CodeBee's independent local port.
    """
    raw = str(value or "")
    if not raw or raw != raw.strip() or any(ch.isspace() for ch in raw):
        return ""
    if any(ch in raw for ch in "/?#@,\\"):
        return ""
    # Bind addresses are often supplied as bare IPv6 literals (``--host ::``);
    # HTTP Host values still use brackets, which urlparse validates below.
    if not raw.startswith("[") and raw.count(":") > 1:
        try:
            return ipaddress.ip_address(raw).compressed
        except ValueError:
            return ""
    try:
        parsed = urlparse("//" + raw)
        if parsed.username is not None or parsed.password is not None:
            return ""
        host = parsed.hostname
        parsed.port  # validate any supplied port
    except ValueError:
        return ""
    if not host:
        return ""
    host = host.rstrip(".").lower()
    try:
        return ipaddress.ip_address(host).compressed
    except ValueError:
        try:
            host = host.encode("idna").decode("ascii").lower()
        except (UnicodeError, ValueError):
            return ""
        return host if _HOST_LABEL_RE.fullmatch(host) else ""


def allowed_request_host(host: str, forwarded: str = "") -> bool:
    """Whether an HTTP Host belongs to this CodeBee listener.

    This prevents DNS-rebinding sites from reaching a loopback service with
    their own Origin/Host pair. Explicit bind addresses, the LAN/Tailscale
    interface addresses used by phone links, and the configured public URL are
    accepted. An explicitly trusted proxy can also retain its named Host when
    it supplies a forwarded client address.
    """
    candidate = _normalized_host(host)
    if not candidate:
        return False
    # Preserve named reverse-proxy hosts when the proxy is explicitly trusted
    # and supplied its client-address header. A browser reaching loopback via
    # DNS rebinding cannot set these restricted headers, while forwarded
    # requests still pass through the existing token check.
    if _TRUST_PROXY and forwarded:
        return True
    allowed = set(_LOOPBACK_HOSTS)
    bind_host = _normalized_host(_BIND_HOST)
    if bind_host and bind_host not in _WILDCARD_HOSTS:
        allowed.add(bind_host)
    try:
        public = urlparse(str(PUBLIC_URL or "").strip())
        public_host = _normalized_host(public.hostname or "")
        if public.scheme.lower() in ("http", "https") and public_host:
            allowed.add(public_host)
    except (TypeError, ValueError):
        pass
    if candidate in allowed:
        return True
    if bind_host not in _WILDCARD_HOSTS:
        return False
    # Hostnames not explicitly configured above are rejected without doing
    # interface discovery; this keeps rebinding probes cheap under load.
    try:
        ipaddress.ip_address(candidate)
    except ValueError:
        return False
    for discover in (lan_ip, tailscale_ip):
        try:
            address = _normalized_host(discover())
        except Exception:
            address = ""
        if address:
            allowed.add(address)
    return candidate in allowed


# ---------------------------------------------------------------- 快速隧道（trycloudflare）
# 安装即公网：本机装了 cloudflared 就自动开一条 Cloudflare 快速隧道，
# 拿到随机 *.trycloudflare.com 地址——零账号、零域名、零配置。
# 安全强绑定：开隧道必然同时开启 trusted-proxy（否则回源 loopback 豁免=裸奔）。
# 代价：每次重启地址会变（手机重新扫码即可）；要固定域名见 README 公网章节。
_QUICK_PROC = None  # cloudflared 子进程，随主服务退出
_QUICK_ON_DEMAND = None  # (port, on_url, enabled)：手机配对时按需拉隧道的参数
_QUICK_INSTALLING = False
_QUICK_INSTALL_RETRY_AT = 0.0


def setup_quick_tunnel(port: int, on_url, enabled: bool):
    """记录按需拉起快速隧道所需的参数，供手机配对动作触发。

    启动期的自动开隧道有 host 前置条件（host=127.0.0.1 的默认启动不开）；
    但用户点「手机配对」本身就是要「任何网络连上」，此时隧道应补开。
    """
    global _QUICK_ON_DEMAND
    _QUICK_ON_DEMAND = (port, on_url, bool(enabled))


def ensure_quick_tunnel() -> bool:
    """手机配对时按需拉起快速隧道；已开/已在拉/被禁用/无凭据拦截时不动。

    返回 True 表示这次调用真的触发了一次启动（URL 数秒后经回调出现）。
    """
    if _QUICK_PROC is not None or PUBLIC_URL or not _QUICK_ON_DEMAND:
        return False
    port, on_url, enabled = _QUICK_ON_DEMAND
    if not enabled or has_local_creds():
        return False
    global _QUICK_INSTALLING, _QUICK_INSTALL_RETRY_AT
    if not cloudflared_exe():
        now = time.time()
        if _QUICK_INSTALLING or now < _QUICK_INSTALL_RETRY_AT:
            return False
        _QUICK_INSTALLING = True

        def _install_then_start():
            global _QUICK_INSTALLING, _QUICK_INSTALL_RETRY_AT
            try:
                if ensure_cloudflared():
                    current = _QUICK_ON_DEMAND
                    if current and _QUICK_PROC is None and not PUBLIC_URL and current[2]:
                        start_quick_tunnel(current[0], current[1])
                else:
                    _QUICK_INSTALL_RETRY_AT = time.time() + 60
            finally:
                _QUICK_INSTALLING = False

        threading.Thread(target=_install_then_start, daemon=True,
                         name="codebee-cloudflared-install").start()
        return True
    return start_quick_tunnel(port, on_url)


def quick_tunnel_pending() -> bool:
    """Whether a quick tunnel is being established but has no public URL yet."""
    if PUBLIC_URL:
        return False
    if _QUICK_INSTALLING:
        return True
    if _QUICK_PROC is None:
        return False
    try:
        return _QUICK_PROC.poll() is None
    except AttributeError:
        # Unit-test fakes and older process wrappers may not expose poll().
        return True


def cloudflared_exe() -> str:
    import os
    exe = shutil.which("cloudflared")
    if exe:
        return exe
    if sys.platform == "darwin":
        # Homebrew（Apple Silicon /usr/local 旧装）不在 PATH 时的常见落点
        for guess in ("/opt/homebrew/bin/cloudflared", "/usr/local/bin/cloudflared"):
            if os.path.isfile(guess):
                return guess
        return ""
    for base in (os.environ.get("LOCALAPPDATA", ""),
                 os.environ.get("ProgramFiles(x86)", "")):
        if not base:
            continue
        for sub in ("Microsoft\\WinGet\\Packages\\Cloudflare.cloudflared_"
                    "Microsoft.Winget.Source_8wekyb3d8bbwe",
                    "cloudflared"):
            guess = os.path.join(base, sub, "cloudflared.exe")
            if os.path.isfile(guess):
                return guess
    return ""


def ensure_cloudflared() -> bool:
    """自动安装 cloudflared（Windows winget / macOS brew）。

    安装后重新检测可执行文件；返回 True 表示安装成功且文件可用。
    不弹窗、不阻塞主线程超过 3 分钟（winget 一般 30 秒内完成）。
    """
    if cloudflared_exe():
        return True
    import subprocess
    try:
        if sys.platform == "win32":
            proc = subprocess.run(
                ["winget", "install", "--accept-source-agreements",
                 "--accept-package-agreements", "--silent",
                 "Cloudflare.cloudflared"],
                capture_output=True, timeout=180,
                creationflags=0x08000000)  # CREATE_NO_WINDOW
        else:
            proc = subprocess.run(
                ["brew", "install", "cloudflared"],
                capture_output=True, timeout=180)
        if proc.returncode != 0:
            return False
    except Exception:
        return False
    return bool(cloudflared_exe())


def has_local_creds() -> bool:
    """~/.cloudflared 下有隧道凭据（用户已玩过 named tunnel）。

    实测（2026-09）：本机存在凭据时 cloudflared 会把 quick tunnel 降级为
    named 模式运行，随机域名恒 404；此时应走固定域名路径而不是 quick。
    """
    import os
    d = os.path.join(os.path.expanduser("~"), ".cloudflared")
    try:
        return any(f.endswith(".json") for f in os.listdir(d))
    except Exception:
        return False


def start_quick_tunnel(port: int, on_url) -> bool:
    """后台起快速隧道，解析到随机 URL 后回调 on_url(url)（如在主线程打印+推送）。

    返回 False 表示没装 cloudflared 或启动失败，调用方给出提示。
    """
    global _QUICK_PROC
    exe = cloudflared_exe()
    if not exe or not port:
        return False
    import subprocess
    flags = 0x08000000 if hasattr(subprocess, "CREATE_NO_WINDOW") else 0  # CREATE_NO_WINDOW
    try:
        proc = subprocess.Popen(
            # 127.0.0.1 而非 localhost：Windows 上 localhost 会先解析成 IPv6
            # [::1]，服务只监听 IPv4 时回源全部被拒（2026-10-16 实案，手机
            # 经隧道访问整页报错）。
            [exe, "tunnel", "--url", "http://127.0.0.1:%d" % port, "--no-autoupdate"],
            stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
            creationflags=flags, text=True, encoding="utf-8", errors="replace")
    except Exception:
        _QUICK_PROC = None
        return False
    _QUICK_PROC = proc
    from . import beekeeper
    beekeeper.adopt(proc)   # 挂巢：服务死（含硬杀）隧道一起带走，防孤儿 cloudflared
    import threading

    def _watch():
        global PUBLIC_URL, PUBLIC_URL_KIND, _QUICK_PROC
        deadline = time.time() + 25
        found = False
        try:
            for line in proc.stdout:
                if not found:
                    m = re.search(r"https://[a-z0-9-]+\.trycloudflare\.com", line or "")
                    if m:
                        set_trusted_proxy(True)  # 公网暴露 ⇆ 强制反代感知，防回源豁免
                        PUBLIC_URL = m.group(0)
                        PUBLIC_URL_KIND = "quick"
                        on_url(PUBLIC_URL)
                        found = True
                        # 不 break：必须继续消费日志，否则 cloudflared 写满管道
                        # 缓冲后会整体阻塞，边缘连接注册无法完成（实测 530）
                if time.time() > deadline and not found:
                    try:
                        proc.terminate()
                    except Exception:
                        pass
                    if _QUICK_PROC is proc:
                        _QUICK_PROC = None
                    break
        except Exception:
            pass

    threading.Thread(target=_watch, daemon=True).start()
    return True


def stop_quick_tunnel():
    global _QUICK_PROC
    if _QUICK_PROC:
        try:
            _QUICK_PROC.terminate()
        except Exception:
            pass
        _QUICK_PROC = None


def effective_ip(socket_ip: str, forwarded: str) -> str:
    """信任代理时从转发头取真实客户端 IP（第一跳），否则用 socket 地址。"""
    if _TRUST_PROXY and forwarded:
        first = forwarded.split(",")[0].strip()
        if first:
            return first
    return socket_ip


def request_authed(client_ip: str, forwarded: str, query_token: str, header_token: str) -> bool:
    """本机豁免；远程（或经代理回源）请求必须带正确令牌。"""
    loopback = client_ip in ("127.0.0.1", "::1")
    if loopback and not (_TRUST_PROXY and forwarded):
        return True  # 真本机（trust_proxy 下不带转发头的 loopback 也算）
    tok = token()
    if not tok:
        return True
    return secrets.compare_digest(str(query_token or ""), tok) or \
        secrets.compare_digest(str(header_token or ""), tok)


def same_origin(origin: str, host: str) -> bool:
    """Validate a browser ``Origin`` against the request ``Host``.

    Empty Origin is allowed for non-browser clients (curl, CLI integrations),
    while ``null`` and malformed origins are rejected.  Default HTTP(S) ports
    are normalized so a browser's explicit ``:80``/``:443`` does not produce a
    false rejection.  The comparison is host-only because the service may sit
    behind an HTTPS reverse proxy while speaking HTTP on its local socket.
    """
    raw_origin = str(origin or "").strip()
    if not raw_origin:
        return True
    if raw_origin.lower() == "null":
        return False
    parsed = urlparse(raw_origin)
    if parsed.scheme.lower() not in ("http", "https") or not parsed.hostname:
        return False
    raw_host = str(host or "").strip()
    if not raw_host:
        return False
    try:
        request_host = urlparse("//" + raw_host)
        request_name = (request_host.hostname or "").rstrip(".").lower()
        request_port = request_host.port
        origin_name = parsed.hostname.rstrip(".").lower()
        origin_port = parsed.port
    except ValueError:
        return False
    if not request_name or request_name != origin_name:
        return False
    if request_port is None:
        request_port = 443 if parsed.scheme.lower() == "https" else 80
    if origin_port is None:
        origin_port = 443 if parsed.scheme.lower() == "https" else 80
    return request_port == origin_port


# ---------------------------------------------------------------- 控制权锁
CTRL_TTL = 45.0  # 秒；持锁设备停止心跳后自动释放
_CTRL = {"client_id": "", "name": "", "expires_at": 0.0}
# RLock：acquire/release 持锁期间会调 control_view，它也要拿同一把锁
_CTRL_LOCK = threading.RLock()


def _live_ctrl():
    """返回未过期的持锁信息；过期则视为空闲（惰性过期，无需定时线程）。"""
    if _CTRL["client_id"] and time.time() > _CTRL["expires_at"]:
        _CTRL.update({"client_id": "", "name": "", "expires_at": 0.0})
    return _CTRL


def control_view(client_id: str = "") -> dict:
    """对外的控制权状态：free / held（mine 标记是否是请求方自己持有）。

    expires_in 按 10s 桶化：SSE 靠对比该 dict 判断"控制权是否变了"，
    精确到秒的倒计时会造成每秒一次假变化 → 全量推送。
    """
    with _CTRL_LOCK:
        c = _live_ctrl()
        if not c["client_id"]:
            return {"mode": "free", "mine": False}
        return {"mode": "held", "mine": bool(client_id) and c["client_id"] == client_id,
                "holder": c["name"] or "其他设备",
                "expires_in": max(10, int(c["expires_at"] - time.time()) // 10 * 10)}


def acquire(client_id: str, name: str, force: bool = False):
    """接管控制权。已持有则顺延心跳；空闲直接拿到；他人持有时除非 force 否则拒绝。

    返回 (ok, control_view)。
    """
    client_id = str(client_id or "")
    if not client_id:
        client_id = "anon-" + secrets.token_hex(4)
    with _CTRL_LOCK:
        c = _live_ctrl()
        if c["client_id"] == client_id:
            c["expires_at"] = time.time() + CTRL_TTL
            return True, control_view(client_id)
        if c["client_id"] and not force:
            return False, control_view(client_id)
        _CTRL.update({"client_id": client_id,
                      "name": _safe_name(name) or "其他设备",
                      "expires_at": time.time() + CTRL_TTL})
        return True, control_view(client_id)


def release(client_id: str) -> dict:
    with _CTRL_LOCK:
        if client_id and _CTRL["client_id"] == client_id:
            _CTRL.update({"client_id": "", "name": "", "expires_at": 0.0})
        return control_view(client_id)


def heartbeat(client_id: str):
    """持锁续期。返回 (是否仍持有, control_view)。"""
    ok, view = acquire(client_id, "")
    return ok, view


def _safe_name(s) -> str:
    return re.sub(r"\s+", " ", str(s or "")).strip()[:24]


# ---------------------------------------------------------------- 地址探测
_TS_CACHE = {"ip": "", "ts": 0.0}  # tailscale CLI 调用有开销，30s 缓存


def lan_ip() -> str:
    """本机局域网 IP（UDP connect 只选路由不发包）。"""
    import socket
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        s.connect(("223.5.5.5", 80))
        return s.getsockname()[0]
    except Exception:
        return ""
    finally:
        s.close()


def tailscale_ip(max_age: float = 30.0) -> str:
    """Tailscale 虚拟网 IP；未安装/未启动返回空。结果缓存 30s。"""
    if max_age > 0 and time.time() - _TS_CACHE["ts"] < max_age:
        return _TS_CACHE["ip"]
    ip = _tailscale_ip_once()
    _TS_CACHE.update({"ip": ip, "ts": time.time()})
    return ip


def _tailscale_ip_once() -> str:
    exe = shutil.which("tailscale")
    if not exe:
        return ""
    try:
        out = subprocess.run([exe, "ip", "-4"], capture_output=True,
                             text=True, timeout=5)
        for line in (out.stdout or "").splitlines():
            ip = line.strip()
            if re.match(r"^\d+\.\d+\.\d+\.\d+$", ip):
                return ip
    except Exception:
        pass
    return ""


def build_connect_urls(port: int) -> list:
    """手机扫码可用的连接地址，按优先级排序：公网域名 > Tailscale > 局域网。"""
    urls = []
    if PUBLIC_URL:
        label = ("公网 · 任何网络（重启会变，连不上重新扫码）" if PUBLIC_URL_KIND == "quick"
                 else "公网 · 任何网络")
        urls.append({"label": label, "url": "%s/?token=%s" % (PUBLIC_URL, token())})
    # The default server binds 127.0.0.1. Advertising its LAN address in that
    # mode produces a QR code that opens a page but can never reach the service.
    # Direct interface URLs are only valid when the listener was explicitly
    # exposed (for example --host 0.0.0.0).
    loopback_only = _BIND_HOST in ("127.0.0.1", "localhost", "::1")
    if not loopback_only:
        ts = tailscale_ip()
        if ts:
            urls.append({"label": "Tailscale · 外网随时随地",
                         "url": "http://%s:%d/?token=%s" % (ts, port, token())})
        lan = lan_ip()
        if lan:
            urls.append({"label": "局域网 · 同一 WiFi",
                         "url": "http://%s:%d/?token=%s" % (lan, port, token())})
    return urls
