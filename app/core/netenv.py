# -*- coding: utf-8 -*-
"""死本地代理自愈（2026-10-26 头七实案驱动）。

Windows 系统代理开着（注册表 ProxyServer=127.0.0.1:7897），但 Clash 已退出、
端口无人监听：urllib 家族按注册表走代理，所有出站请求立刻
ConnectionRefusedError(10061)——Bigmodel 健康探测/评测整夜全灭；子进程 CLI
（codex/reqwest、aider/litellm）继承同一代理地址同样全灭。供应商链六七成
通道陪葬，每个步骤都要在死通道上磨完重试梯才轮到活通道，run 拖成小时级，
评审打在从未修订的稿件上，任务以「评审未达标」收场——真实死因是代理，
不是稿件。

本模块做四件事（全部进程内，绝不动用户注册表/浏览器代理）：
1. 解析代理目标（环境变量 + 注册表）里指向本机回环的 (host, port)；
2. 端口已死 → 从 os.environ 剥离代理变量（子进程继承即干净），并接管
   urllib.request.getproxies 返回空表——自建 opener 的 ProxyHandler 也走
   它，单点覆盖全部进程内 urllib 出站；
3. 定期复检（TTL）：端口复活（用户重开 Clash）→ 原样恢复 env 与
   getproxies，境外通道回归；
4. 只碰回环地址代理——公司内网代理等非本地代理一律不探测不剥离
   （内网端点反而必须走它）。

进程外覆盖：runner.run_process 生成的子进程 env 继承已剥离的 os.environ，
另有 strip_dead() 在 spawn 前对 env 副本兜底（覆盖 TTL 窗口内新死的端口）。
"""
import logging
import os
import socket
import threading
import time
import urllib.request

log = logging.getLogger(__name__)

_PROXY_VALUE_KEYS = ("http_proxy", "https_proxy", "all_proxy", "ftp_proxy")
_PROXY_ENV_KEYS = tuple(_PROXY_VALUE_KEYS) + tuple(
    k.upper() for k in _PROXY_VALUE_KEYS)
_LOOPBACK_HOSTS = ("127.0.0.1", "localhost", "::1")
_RECHECK_S = 60.0
_PROBE_TIMEOUT = 0.8

_LOCK = threading.RLock()
_STATE = {
    "originals": None,   # 剥离前的 env 快照（复活时原样恢复）
    "patched": False,    # getproxies 是否处于接管中
    "dead": (),          # 当前判死的 (host, port) 元组
    "checked_at": 0.0,
    "note": "",
}
_orig_getproxies = None


def _split_target(value):
    """"http://127.0.0.1:7897" / "127.0.0.1:7897" / "socks5://localhost:7891"
    → ("127.0.0.1", 7897)；非回环或缺端口返回 None。"""
    s = str(value or "").strip()
    if not s:
        return None
    if "://" in s:
        s = s.split("://", 1)[1]
    if "@" in s:
        s = s.rsplit("@", 1)[1]
    host, _, port = s.partition(":")
    host = host.strip("[]").strip().lower()
    try:
        port = int(port)
    except (TypeError, ValueError):
        return None
    if host in _LOOPBACK_HOSTS and 0 < port < 65536:
        # 回环写法归一：注册表记 127.0.0.1、env 记 localhost 是同一个代理，
        # 不归一会让 strip_dead 对不上号、探测也重复三遍。
        return ("127.0.0.1", port)
    return None


def _targets_from_proxies(proxies):
    out = set()
    for v in (proxies or {}).values():
        t = _split_target(v)
        if t:
            out.add(t)
    return out


def _proxy_sources():
    """(环境变量代理, 注册表代理) 两路都看——env 盖不住注册表来源，
    注册表只在 Windows 存在。可注入点（测试 monkeypatch 这里）。"""
    env_proxies = urllib.request.getproxies_environment()
    reg_proxies = {}
    getter = getattr(urllib.request, "getproxies_registry", None)
    if getter is not None:
        try:
            reg_proxies = getter() or {}
        except Exception:
            reg_proxies = {}
    return env_proxies or {}, reg_proxies or {}


def _port_alive(host, port, timeout=_PROBE_TIMEOUT):
    try:
        with socket.create_connection((host, port), timeout=timeout):
            return True
    except OSError:
        return False


def _apply_scrub(dead):
    st = _STATE
    if st["originals"] is None:
        st["originals"] = {k: os.environ[k] for k in _PROXY_ENV_KEYS
                           if k in os.environ}
    for k in _PROXY_ENV_KEYS:
        os.environ.pop(k, None)
    global _orig_getproxies
    if not st["patched"]:
        if _orig_getproxies is None:
            _orig_getproxies = urllib.request.getproxies

        def _no_proxies():  # 判死期间一律免代理；复活由 _restore 归还
            return {}

        urllib.request.getproxies = _no_proxies
        st["patched"] = True
    st["dead"] = tuple(dead)


def _restore():
    st = _STATE
    global _orig_getproxies
    if st["originals"]:
        for k, v in st["originals"].items():
            os.environ[k] = v
    if st["patched"] and _orig_getproxies is not None:
        urllib.request.getproxies = _orig_getproxies
        _orig_getproxies = None
    st["patched"] = False
    st["originals"] = None
    st["dead"] = ()
    st["note"] = ""


def ensure(*, force=False):
    """按 TTL 复检死本地代理并自愈；返回当前判死的 ("host", port) 元组。

    端口已死 → 剥离 env + 接管 getproxies；端口复活 → 原样恢复。
    任何异常都吞掉并按「无死代理」处理——自愈绝不能反过来弄挂主流程。
    """
    try:
        return _ensure_inner(force=force)
    except Exception:
        log.debug("死本地代理自愈探测失败", exc_info=True)
        return _STATE.get("dead") or ()


def _ensure_inner(*, force=False):
    with _LOCK:
        now = time.monotonic()
        if not force and now - _STATE["checked_at"] < _RECHECK_S:
            return _STATE["dead"]
        _STATE["checked_at"] = now
        env_proxies, reg_proxies = _proxy_sources()
        targets = (_targets_from_proxies(env_proxies)
                   | _targets_from_proxies(reg_proxies))
        dead = tuple(sorted(t for t in targets if not _port_alive(*t)))
        if dead:
            _apply_scrub(dead)
            note = "、".join("%s:%d" % t for t in dead)
            if _STATE["note"] != note:
                log.warning(
                    "本地代理端口无进程监听（%s），已剥离进程代理改为直连；"
                    "端口复活后自动恢复。此前出站请求会以 10061 连接被拒全灭"
                    "（2026-10-26 实案：Clash 退出后系统代理残留）。", note)
                _STATE["note"] = note
        elif _STATE["patched"]:
            _restore()
            log.info("本地代理端口已复活，已恢复代理环境变量与系统代理读取")
        _STATE["dead"] = dead
        return dead


def strip_dead(env):
    """从 env dict 原地删除指向已判死回环端口的代理变量。

    spawn 前最后一道闸：run_process 的 full_env 基于 os.environ 副本再注入
    每调用 env，两次 ensure() 复检之间新死的端口由这里按既有判死结果剥掉。
    只删代理赋值键，不碰 no_proxy。
    """
    dead = _STATE.get("dead") or ()
    if not dead or not isinstance(env, dict):
        return env
    dead_set = set(dead)
    for k in list(env.keys()):
        if not isinstance(k, str) or k.lower() not in _PROXY_VALUE_KEYS:
            continue
        if _split_target(env.get(k)) in dead_set:
            del env[k]
    return env


def dead_targets():
    """当前判死的回环代理目标快照（健康面板/诊断用）。"""
    return _STATE.get("dead") or ()


def note():
    """一句话现状说明（未触发自愈时为空串）。"""
    return _STATE.get("note") or ""


def reset_for_tests():
    """测试隔离：恢复一切现场。"""
    with _LOCK:
        _restore()
        _STATE["checked_at"] = 0.0
