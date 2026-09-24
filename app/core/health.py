# -*- coding: utf-8 -*-
"""供应商健康监测与告警：连不上模型/厂商时自动降级并告警，恢复自动解除。

设计稿：docs/migration/07-token-cost.md 的延伸（2026-09-15 连载验收实战需求：
cavoti 网关宕机 5 小时，系统只会反复失败重试，没有主动告知「通道已不可用」）。

状态机（按 provider）：
  ok       正常（有近期成功或不活跃）
  failing  连续失败 ≥FAILING_AFTER（进入探测模式）
  down     failing 持续 ≥DOWN_AFTER_SECONDS 或连续失败 ≥DOWN_AFTER_FAILURES
           → 触发告警（未静默时）
  recovered down/failing 后再次成功 → 自动解除告警，记一笔恢复

探针策略（省 token）：正常时不主动探测（靠真实调用信号）；failing/down 后按
退避（60s→120s→300s 封顶）发轻量探活（modelhub.chat，max_tokens=8）。
恢复 → 回正常节奏。

手动操作：
  silence(provider)  静默本次告警（不再提醒，直到恢复或手动 reset）
  reset(provider)    手动清除状态视为恢复（下次失败重新计数）

线程模型：上报（runner/pipeline/modelhub 多线程调用）走锁；探针单线程 daemon。
"""
from __future__ import annotations

import json
import logging
import threading
import time
from pathlib import Path

log = logging.getLogger(__name__)

FAILING_AFTER = 2            # 连续失败 ≥2 → failing（进入探测）
DOWN_AFTER_FAILURES = 4      # 连续失败 ≥4 → down（告警）
DOWN_AFTER_SECONDS = 600     # 或 failing 持续 10 分钟 → down
PROBE_BACKOFF = (60, 120, 300)   # 探测退避秒数（封顶 300）
SILENCE_FOREVER = -1         # 手动静默不自动过期（恢复时才清除）

_LOCK = threading.RLock()
_PROVIDERS = {}              # name -> 状态 dict
_FILE = None                 # init() 注入（data/provider_health.json）
_PROBE_THREAD = None
_STARTED = False


def init(data_dir=None, *, start_probe=True):
    """注入存储路径并从磁盘恢复状态（服务重启不丢告警上下文）。

    ``start_probe=False`` 供单元测试等受控场景使用，避免留下跨用例的
    daemon 探针线程。
    """
    global _FILE, _PROVIDERS, _STARTED, _PROBE_THREAD
    with _LOCK:
        base = Path(data_dir) if data_dir else Path(_default_dir())
        base.mkdir(parents=True, exist_ok=True)
        _FILE = base / "provider_health.json"
        _PROVIDERS = {}
        try:
            data = json.loads(_FILE.read_text(encoding="utf-8"))
            for name, st in (data.get("providers") or {}).items():
                if isinstance(st, dict) and st.get("name"):
                    _PROVIDERS[name] = st
        except (OSError, json.JSONDecodeError):
            pass
        # 「绑定链·」静态告警胶囊已从产品移除（2026-09-18：普通用户误伤，
        # 用户拍板）——老版本持久化的条目启动即清，别让 0.1.6 的状态阴魂不散。
        stale = [k for k in _PROVIDERS if k.startswith("绑定链·")]
        if stale:
            for k in stale:
                _PROVIDERS.pop(k, None)
            _persist()
        # 供应商已删除的幽灵条目启动即清；运行期新增的幽灵由探针在探活时
        # 发现（gone 标记）随手清——探针循环里不做全量扫描，避免和测试
        # 夹具/运行期建条目赛跑。
        _prune_missing()
        if start_probe and not _STARTED:
            _STARTED = True
            _PROBE_THREAD = threading.Thread(
                target=_probe_loop, name="health-probe", daemon=True)
            _PROBE_THREAD.start()


def _default_dir():
    from . import paths
    return str(paths.DATA_DIR)


def _persist():
    # This is telemetry state: a storage failure must not turn a successful or
    # failed model call into a task failure. Callers already hold _LOCK, but
    # keeping the path and snapshot read under it also protects future callers.
    with _LOCK:
        path = _FILE
        if path is None:
            return
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            tmp = path.with_suffix(".tmp")
            tmp.write_text(
                json.dumps({"providers": _PROVIDERS}, ensure_ascii=False, indent=2),
                encoding="utf-8")
            tmp.replace(path)
        except OSError as e:
            log.warning("[health] 无法持久化供应商健康状态到 %s: %s", path, e)


def _now():
    return time.time()


def _fmt_ts(epoch):
    if not epoch:
        return ""
    return time.strftime("%m-%d %H:%M", time.localtime(epoch))


# ---------------------------------------------------------------- 上报入口

def report_success(provider: str):
    """一次真实成功调用（CLI 或 API 直连）。"""
    if not provider:
        return
    with _LOCK:
        st = _PROVIDERS.get(provider)
        if st is None:
            return  # 没失败过就不建档（零开销）
        if st.get("status") in ("failing", "down"):
            st["status"] = "recovered"
            st["recovered_at"] = _now()
            st["consecutive_failures"] = 0
            st["last_ok_at"] = _now()
            st["probe_next_at"] = 0
            st["alerted"] = False
            st["silenced"] = False       # 静默只管一次故障期；恢复后重新武装
            st["silence_until"] = 0
            log.info("[health] %s 已恢复（告警自动解除）", provider)
        else:
            st["last_ok_at"] = _now()
            st["consecutive_failures"] = 0
        _persist()


def report_failure(provider: str, error: str = "", *, model: str = "",
                   provider_id: str = ""):
    """一次真实失败调用。provider 用展示名（与 usage 台账一致）。"""
    if not provider:
        return
    with _LOCK:
        st = _PROVIDERS.setdefault(provider, {
            "name": provider, "provider_id": provider_id or "",
            "model": model or "", "status": "ok",
            "consecutive_failures": 0, "first_fail_at": 0,
            "last_fail_at": 0, "last_ok_at": 0, "last_error": "",
            "alerted": False, "silenced": False, "silence_until": 0,
            "probe_next_at": 0, "probe_backoff_idx": 0,
            "recovered_at": 0,
        })
        st["name"] = provider
        if provider_id and not st.get("provider_id"):
            st["provider_id"] = provider_id
        if model and not st.get("model"):
            st["model"] = model
        n = int(st.get("consecutive_failures") or 0) + 1
        st["consecutive_failures"] = n
        st["last_fail_at"] = _now()
        st["last_error"] = str(error or "")[:300]
        if st.get("status") in ("ok", "recovered", ""):
            st["status"] = "failing"
            st["first_fail_at"] = _now()
            st["probe_next_at"] = _now() + PROBE_BACKOFF[0]
            st["probe_backoff_idx"] = 0
        # failing → down：连续次数或持续时间任一达到
        if st["status"] == "failing":
            dur = _now() - (st.get("first_fail_at") or _now())
            if n >= DOWN_AFTER_FAILURES or dur >= DOWN_AFTER_SECONDS:
                st["status"] = "down"
        if st["status"] == "down" and not st.get("silenced"):
            if not st.get("alerted"):
                st["alerted"] = True
                log.warning("[health] ⚠️ 供应商告警：%s 不可用（连续 %d 次失败，%s）",
                            provider, n, st["last_error"][:120])
        _persist()


# ------------------------------------------------- 静态死链告警（绑定层）

# ---------------------------------------------------------------- 手动操作

def silence(provider: str, minutes: int = 0):
    """手动静默告警（minutes=0 表示静默到恢复为止）。"""
    with _LOCK:
        st = _PROVIDERS.get(provider)
        if not st:
            return False, "未知的供应商"
        st["silenced"] = True
        st["silence_until"] = (_now() + minutes * 60) if minutes else SILENCE_FOREVER
        st["alerted"] = False
        _persist()
        return True, ""


def reset(provider: str):
    """手动清除状态视为恢复（下次失败重新计数）。"""
    with _LOCK:
        st = _PROVIDERS.get(provider)
        if not st:
            return False, "未知的供应商"
        st.update({"status": "recovered", "consecutive_failures": 0,
                   "alerted": False, "silenced": False, "silence_until": 0,
                   "probe_next_at": 0, "recovered_at": _now()})
        _persist()
        return True, ""


# ---------------------------------------------------------------- 查询

def _effective_silenced(st):
    """静默是否仍有效（永久静默或未到过期时间）。"""
    if not st.get("silenced"):
        return False
    until = st.get("silence_until") or 0
    return until == SILENCE_FOREVER or until > _now()


def snapshot():
    """给 API/SSE 的完整视图。"""
    with _LOCK:
        out = []
        for name, st in _PROVIDERS.items():
            silenced = _effective_silenced(st)
            alerting = st.get("status") == "down" and not silenced
            out.append({
                "provider": name,
                "provider_id": st.get("provider_id") or "",
                "model": st.get("model") or "",
                "status": st.get("status") or "ok",
                "consecutive_failures": st.get("consecutive_failures") or 0,
                "first_fail_at": _fmt_ts(st.get("first_fail_at")),
                "last_fail_at": _fmt_ts(st.get("last_fail_at")),
                "last_ok_at": _fmt_ts(st.get("last_ok_at")),
                "recovered_at": _fmt_ts(st.get("recovered_at")),
                "last_error": st.get("last_error") or "",
                "alerting": alerting,
                "silenced": silenced,
                "static": bool(st.get("static")),
            })
        # 稳定排序：告警中 > 故障中 > 恢复 > 正常
        rank = {"down": 0, "failing": 1, "recovered": 2, "ok": 3}
        out.sort(key=lambda x: (rank.get(x["status"], 9), x["provider"]))
        return {
            "providers": out,
            "alerts": [p for p in out if p["alerting"]],
            "any_alerting": any(p["alerting"] for p in out),
        }


def is_down(provider: str) -> bool:
    """链降级查询：该供应商当前是否 down（供 resolve_binding 跳过）。"""
    with _LOCK:
        st = _PROVIDERS.get(provider)
        return bool(st and st.get("status") == "down")


def down_names() -> set:
    with _LOCK:
        return {n for n, st in _PROVIDERS.items() if st.get("status") == "down"}


# ---------------------------------------------------------------- 探针

def _probe_one(st):
    """对单个故障 provider 发轻量探活。返回 (ok, gone)：
    ok=是否恢复；gone=供应商已从配置里删除（健康记录应随之清掉）。"""
    try:
        from . import modelhub
        pid = st.get("provider_id") or ""
        model = st.get("model") or ""
        plist = modelhub.providers()
        provs = {p.get("name"): p for p in plist}
        prov = provs.get(st.get("name")) or next(
            (p for p in plist if pid and p.get("id") == pid), None)
        if not prov:
            return False, True   # 供应商已删除：探不了也不再是「没恢复」
        if not prov.get("api_key"):
            return False, False  # 供应商在但没密钥：无法探测，保持状态
        if not model:
            try:
                names = modelhub._enabled_models(prov)
                model = names[0]["name"] if names else ""
            except Exception:
                model = ""
        if not model:
            return False, False
        res = modelhub.chat(prov["id"], model, "1", max_tokens=8, timeout=20)
        return bool(res.get("ok")), False
    except Exception as e:
        log.debug("probe %s error: %s", st.get("name"), e)
        return False, False


def _prune_missing():
    """供应商被删除后清掉它的健康记录。

    幽灵条目探不活也永远不会恢复，还会每轮把 last_fail_at 刷成「刚刚」，
    健康页看起来像它一直在报错（Z.ai 删掉后仍显示 429 的误伤案，2026-09-18）。
    """
    try:
        from . import modelhub
        plist = modelhub.providers()
    except Exception:
        return
    ids = {p.get("id") for p in plist}
    names = {p.get("name") for p in plist}
    with _LOCK:
        stale = [n for n, st in _PROVIDERS.items()
                 if not ((st.get("provider_id") and st.get("provider_id") in ids)
                         or st.get("name") in names)]
        if stale:
            for n in stale:
                _PROVIDERS.pop(n, None)
            _persist()
            log.info("[health] 清除已删除供应商的健康记录：%s", ", ".join(stale))


def _record_probe_result(name, st, ok, gone):
    """只应用仍属于当前状态表的探针结果，忽略跨 init/测试的过期结果。"""
    with _LOCK:
        if _PROVIDERS.get(name) is not st:
            return
        if gone:
            _PROVIDERS.pop(name, None)
            _persist()
            log.info("[health] %s 已删除，健康记录随之清除", name)
        elif ok:
            report_success(name)
            log.info("[health] 探针确认 %s 已恢复", name)
        else:
            st["last_fail_at"] = _now()
            _persist()


def _probe_loop():
    while True:
        try:
            with _LOCK:
                targets = [(name, st) for name, st in _PROVIDERS.items()
                           if not st.get("static")  # 静态死链无端点可探，绑定恢复时自动解除
                           and st.get("status") in ("failing", "down")
                           and (st.get("probe_next_at") or 0) <= _now()]
                for name, st in targets:
                    idx = int(st.get("probe_backoff_idx") or 0)
                    backoff = PROBE_BACKOFF[min(idx, len(PROBE_BACKOFF) - 1)]
                    st["probe_next_at"] = _now() + backoff
                    st["probe_backoff_idx"] = idx + 1
            for name, st in targets:
                ok, gone = _probe_one(st)
                _record_probe_result(name, st, ok, gone)
        except Exception:
            log.exception("health probe loop error")
        time.sleep(15)  # 探针调度心跳
