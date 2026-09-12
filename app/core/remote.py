# -*- coding: utf-8 -*-
"""手机/远程访问：访问令牌 + 多端控制权锁 + 服务地址探测。

令牌：首次启动生成，落盘 data/remote.json。本机（127.0.0.1）请求豁免，
     非 loopback 请求必须带令牌（?token= 或 X-Tutti-Token 头）。
控制权：同一时刻只有一台设备能执行写操作。空闲自动接管、45s 无心跳自动释放，
       可强制抢夺。纯内存状态，重启即清空。
"""
from __future__ import annotations

import json
import re
import secrets
import shutil
import subprocess
import threading
import time

from . import paths

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
            _TOKEN = secrets.token_hex(4)  # 8 位十六进制，手机好输
            try:
                p.parent.mkdir(parents=True, exist_ok=True)
                p.write_text(json.dumps(
                    {"token": _TOKEN, "created": time.strftime("%Y-%m-%d %H:%M:%S")},
                    ensure_ascii=False, indent=2), encoding="utf-8")
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
PUBLIC_URL = ""  # --public-url：扫码弹框优先展示的公网地址


def set_trusted_proxy(on: bool, public_url: str = ""):
    global _TRUST_PROXY, PUBLIC_URL
    _TRUST_PROXY = bool(on)
    PUBLIC_URL = str(public_url or "").rstrip("/")


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
        urls.append({"label": "公网 · 任何网络",
                     "url": "%s/?token=%s" % (PUBLIC_URL, token())})
    ts = tailscale_ip()
    if ts:
        urls.append({"label": "Tailscale · 外网随时随地",
                     "url": "http://%s:%d/?token=%s" % (ts, port, token())})
    lan = lan_ip()
    if lan:
        urls.append({"label": "局域网 · 同一 WiFi",
                     "url": "http://%s:%d/?token=%s" % (lan, port, token())})
    return urls
