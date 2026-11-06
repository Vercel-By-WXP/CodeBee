# -*- coding: utf-8 -*-
"""CLI 账号停用台账与后台恢复探测。

实案（2026-10-15《头七结账人》复评）：qwen 欠费 403「预扣费额度失败」
被分类成 FORBIDDEN 后，连载评审每一章仍按 critic 名单把 qwen 原样送场，
同一条死链连撞 31 次、白烧 4 小时——失败早已证明该账号本场不可能恢复，
后续章节应该直接绕开它、让活着的评审顶上，全死才收场。

设计边界：
· 本场台账（key=run_id）立即阻止同一 run 重试；账号健康台账跨 run 落盘，
  只把已经被账号级死错证明不可用的账号暂时熔断。后台用低频、短提示词探活，
  成功后自动解除熔断，避免充值/登录后还要手动重启服务。
· 只登记账号级死错（error_codes.ACCOUNT_DEAD_CODES：QUOTA/AUTH/
  MISSING_CREDENTIAL/FORBIDDEN/TOOL_LAUNCH）；限流/超时/网络抖动是
  transient，退避重试的既有路径不变，不进台账。
· 上游不可用类死错（UPSTREAM_SERVER / UPSTREAM_CONFIG）不是账号死错，但同一
  CLI 同因连撞达阈值也会进本场停用名单（见 note_blocked_failure）：blocked
  只说明「不怪产出」，不代表「值得一直撞」。
· 一切读接口静默容错：台账挂了不能连累编排主链路。
"""
from __future__ import annotations

import threading
import time
import json
import logging
from pathlib import Path

from . import atomic
from .error_codes import (ErrorCode, classify_error_text, describe_error_text,
                          error_code_value, is_account_dead_error,
                          safe_error_summary)

_LOCK = threading.Lock()
# run_id -> {agent_id: {"code": str, "summary": str, "at": float}}
_BENCHED = {}
# run_id -> {"provider_id|model": {...}}：本场模型格熔断（见 bench_model）
_BENCHED_MODELS = {}
_MAX_RUNS = 256   # 长驻服务的内存护栏：超限丢最老条目（run 已终态，台账自然失效）

# agent_id -> persistent account health state.  This is deliberately separate
# from _BENCHED: the latter is an execution-scoped exclusion set and must keep
# its old semantics for callers/tests that use it directly.
_ACCOUNTS = {}
_FILE = None
_PROBE_THREAD = None
_PROBE_STARTED = False
_ACCOUNT_PROBE_TICK = 30
_ACCOUNT_PROBE_DELAY = 300
_ACCOUNT_PROBE_TIMEOUT = 35
_LOG = logging.getLogger(__name__)


def _normalize_state(agent_id, raw):
    """Return a safe, JSON-compatible account state loaded from disk.

    Older/hand-edited health files must not be able to stop the probe loop: a
    single malformed row is treated as a quarantined account with a prompt
    retry rather than making all other accounts unmonitored.
    """
    now = time.time()
    value = dict(raw) if isinstance(raw, dict) else {}
    value["agent_id"] = str(agent_id)
    value["status"] = "down" if value.get("status") == "down" else "recovered"
    value["code"] = error_code_value(value.get("code")) or ErrorCode.VENDOR_ERROR.value
    value["summary"] = str(value.get("summary") or safe_error_summary(value["code"]))[:200]
    for key in ("failed_at", "last_probe_at", "next_probe_at", "recovered_at"):
        try:
            value[key] = max(0.0, float(value.get(key) or 0))
        except (TypeError, ValueError):
            value[key] = 0.0
    try:
        value["probe_failures"] = max(0, int(value.get("probe_failures") or 0))
    except (TypeError, ValueError):
        value["probe_failures"] = 0
    value["last_error"] = str(value.get("last_error") or "")[:300]
    value["ever_succeeded"] = bool(value.get("ever_succeeded") or
                                    (value.get("status") == "recovered" and
                                     (value.get("recovered_at") or
                                      value.get("last_success_at"))))
    try:
        value["last_success_at"] = max(0.0, float(value.get("last_success_at") or 0))
    except (TypeError, ValueError):
        value["last_success_at"] = 0.0
    if value["status"] == "down" and value["next_probe_at"] <= 0:
        value["next_probe_at"] = now + _probe_delay(value)
    if value["status"] == "recovered":
        value["next_probe_at"] = 0.0
    return value


def init(data_dir=None, *, start_probe=True):
    """Load persistent account health and optionally start the probe loop.

    The loop is daemonized and best-effort: a health-file or probe failure can
    never make the orchestration service fail to start.
    """
    global _FILE, _ACCOUNTS, _PROBE_THREAD, _PROBE_STARTED
    try:
        if data_dir is None:
            from . import paths
            data_dir = paths.DATA_DIR
        base = Path(data_dir)
        base.mkdir(parents=True, exist_ok=True)
        path = base / "account_health.json"
    except Exception:
        path = None
    with _LOCK:
        _FILE = path
        _ACCOUNTS = {}
        if path:
            try:
                raw = json.loads(path.read_text(encoding="utf-8"))
                accounts = raw.get("accounts") if isinstance(raw, dict) else {}
                for aid, state in (accounts or {}).items():
                    if isinstance(state, dict) and aid:
                        _ACCOUNTS[str(aid)] = _normalize_state(aid, state)
            except (OSError, ValueError, TypeError):
                pass
        if start_probe and not _PROBE_STARTED:
            _PROBE_STARTED = True
            _PROBE_THREAD = threading.Thread(
                target=_probe_loop, name="account-health-probe", daemon=True)
            _PROBE_THREAD.start()


def _persist():
    path = _FILE
    if path is None:
        return
    try:
        atomic.write_json(path, {"accounts": _ACCOUNTS}, mode=0o600, indent=2)
    except OSError as exc:
        _LOG.warning("[bench] 无法持久化账号健康状态到 %s: %s", path, exc)


def _probe_delay(state):
    # 5m, 10m, 20m, 30m cap: cheap enough for a long-running desktop service
    # while still reacting promptly after a user tops up or re-authenticates.
    n = max(0, int(state.get("probe_failures") or 0))
    return min(_ACCOUNT_PROBE_DELAY * (2 ** min(n, 3)), 1800)


def record_failure(agent_id, code, error="", at=None):
    """Persist an account-level dead error and schedule a future probe."""
    if not agent_id or not is_account_dead_error(error_code_value(code)):
        return False
    aid = str(agent_id)
    now = float(at if at is not None else time.time())
    value = error_code_value(code)
    # 2026-11-06：额度类死错的人话必须带「X 时刻后恢复」，否则账号卡只说
    # 「触发速率或并发限制」，把人引去降并发/加退避。describe_error_text 只返回
    # 固定模板（不落任意 stderr，凭据不会进台账）；仅当原文判类与调用方已判的
    # code 一致时才采用，避免一句噪声原文改写台账归因。
    summary = safe_error_summary(value)
    if error and error_code_value(classify_error_text(error)) == value:
        summary = describe_error_text(error) or summary
    with _LOCK:
        state = _ACCOUNTS.get(aid)
        new_episode = not state or state.get("status") != "down"
        if state is None:
            state = {
            "agent_id": aid, "status": "down", "code": value,
            "summary": summary, "failed_at": now,
            "last_error": "", "last_probe_at": 0, "next_probe_at": now + _ACCOUNT_PROBE_DELAY,
            "probe_failures": 0, "recovered_at": 0,
            "ever_succeeded": False, "last_success_at": 0,
            }
            _ACCOUNTS[aid] = state
        state.update({"agent_id": aid, "status": "down", "code": value,
                      "summary": summary,
                      # Runner stderr is arbitrary provider/CLI output and may
                      # contain credentials. Persist a canonical summary only
                      # (describe_error_text 返回的是固定模板，不是原文).
                      "last_error": summary})
        # Do not postpone an already-due probe on every repeated failure.
        if new_episode:
            state["failed_at"] = now
        if not state.get("next_probe_at") or state["next_probe_at"] < now:
            state["next_probe_at"] = now + _probe_delay(state)
        state["recovered_at"] = 0
        _persist()
    return True


def record_success(agent_id, at=None):
    """Record successful use and clear any account quarantine."""
    if not agent_id:
        return False
    aid = str(agent_id)
    now = float(at if at is not None else time.time())
    with _LOCK:
        state = _ACCOUNTS.get(aid)
        if state is None:
            _ACCOUNTS[aid] = {
                "agent_id": aid, "status": "recovered", "code": "",
                "summary": "", "failed_at": 0, "last_error": "",
                "last_probe_at": now, "next_probe_at": 0,
                "probe_failures": 0, "recovered_at": now,
                "ever_succeeded": True, "last_success_at": now,
            }
            _persist()
            return True
        if state.get("status") != "down":
            state.update({"ever_succeeded": True, "last_success_at": now,
                          "last_probe_at": now})
            _persist()
            return False
        state.update({"status": "recovered", "recovered_at": now,
                      "last_probe_at": now, "next_probe_at": 0,
                      "probe_failures": 0, "ever_succeeded": True,
                      "last_success_at": now})
        # Recovery is account-wide: remove stale run-local exclusions too, so
        # every still-running schedule can use the account again immediately.
        for entries in _BENCHED.values():
            entries.pop(aid, None)
        _persist()
    _LOG.info("[bench] 账号 %s 已恢复，重新纳入调度", aid)
    return True


def is_globally_benched(agent_id):
    try:
        with _LOCK:
            return bool(_ACCOUNTS.get(str(agent_id), {}).get("status") == "down")
    except Exception:
        return False


def global_reason(agent_id):
    try:
        with _LOCK:
            state = _ACCOUNTS.get(str(agent_id)) or {}
        if state.get("status") != "down":
            return ""
        return "%s（%s）" % (state.get("summary") or state.get("code") or "账号级错误",
                              state.get("code") or "")
    except Exception:
        return ""


def snapshot():
    """Return a UI/API-safe snapshot of persistent account health."""
    try:
        with _LOCK:
            rows = [dict(v) for v in _ACCOUNTS.values()]
        rows.sort(key=lambda x: (0 if x.get("status") == "down" else 1,
                                 str(x.get("agent_id") or "")))
        for row in rows:
            row["alerting"] = (row.get("status") == "down" and
                                bool(row.get("ever_succeeded")))
        return {"accounts": rows,
                "alerts": [r for r in rows if r.get("alerting")],
                "any_alerting": any(r.get("alerting") for r in rows)}
    except Exception:
        return {"accounts": [], "alerts": [], "any_alerting": False}


def bench(run_id, agent_id, code, at=None):
    """把 agent 记入 run 的本场停用名单。非账号级死错静默忽略。

    首报即停用：账号级死错没有「再试一次说不定好了」的空间——
    重试本身就是浪费（每次失败都是一次完整 CLI 冷启动）。
    """
    if not run_id or not agent_id:
        return False
    value = error_code_value(code)
    if not is_account_dead_error(value):
        return False
    row = {
        "code": value,
        "summary": safe_error_summary(value),
        "at": float(at if at is not None else time.time()),
    }
    with _LOCK:
        entries = _BENCHED.setdefault(str(run_id), {})
        if agent_id in entries:
            return False   # 已停用，保留首报现场
        entries[agent_id] = row
        if len(_BENCHED) > _MAX_RUNS:
            oldest = min(_BENCHED, key=lambda k: max(
                (v.get("at") or 0) for v in _BENCHED[k].values()) or 0)
            _BENCHED.pop(oldest, None)
    return True


def reset(run_id):
    """run 收场时清掉台账（幂等；测试也用它隔离用例）。"""
    with _LOCK:
        _BENCHED.pop(str(run_id), None)
        _NETDEAD.pop(str(run_id), None)
        _BLOCKED_STREAK.pop(str(run_id), None)


def is_benched(run_id, agent_id):
    """该 CLI 是否被本场或持久化账号健康台账停用。

    任何台账异常都按「未停用」处理——宁白试不误杀。
    """
    try:
        with _LOCK:
            local = str(agent_id) in _BENCHED.get(str(run_id), {})
            global_down = _ACCOUNTS.get(str(agent_id), {}).get("status") == "down"
            return local or global_down
    except Exception:
        return False


def reason(run_id, agent_id):
    """停用原因的一句话说明（未停用返回空串），供步骤备注/错误文案拼接。"""
    try:
        with _LOCK:
            row = _BENCHED.get(str(run_id), {}).get(str(agent_id))
        if not row:
            return global_reason(agent_id)
        return "%s（%s）" % (row.get("summary") or row.get("code") or "账号级错误",
                            row.get("code") or "")
    except Exception:
        return ""


def benched_agents(run_id):
    """run 的停用名单快照：{agent_id: 原因说明}。读侧容错，异常返回空 dict。"""
    try:
        with _LOCK:
            entries = dict(_BENCHED.get(str(run_id), {}))
            global_entries = {aid: dict(v) for aid, v in _ACCOUNTS.items()
                             if v.get("status") == "down"}
        out = {aid: "%s（%s）" % (v.get("summary") or v.get("code") or "账号级错误",
                                 v.get("code") or "")
               for aid, v in entries.items()}
        for aid, v in global_entries.items():
            out.setdefault(aid, "%s（%s）" % (
                v.get("summary") or v.get("code") or "账号级错误", v.get("code") or ""))
        return out
    except Exception:
        return {}


def benched_ids(run_id):
    """停用 agent id 集合——换将/补位的 exclude 名单直接并入即可。"""
    try:
        with _LOCK:
            local = set(_BENCHED.get(str(run_id), {}))
            global_ids = {aid for aid, v in _ACCOUNTS.items()
                          if v.get("status") == "down"}
        return local | global_ids
    except Exception:
        return set()


def model_key(provider_id, model):
    """模型格的稳定键：同一 (供应商, 模型) 才互认。"""
    return "%s|%s" % (str(provider_id or "").strip(),
                      str(model or "").strip())


def bench_model(run_id, provider_id, model, code, at=None):
    """把 (供应商, 模型) 记入本场模型级停用名单。

    模型级死错（MODEL_UNAVAILABLE：模型名不存在、该订阅不含此模型）只证明
    「这一格」不可用，同一条 CLI 换个模型照常能干活，所以绝不能停用整条 CLI
    （``test_bench_model_unavailable_scope`` 锁死这条边界）。

    但**同一 run 内**更不该重复撞：2026-10-23 实案 grok-build 没配绑定、被推荐
    了 6 个它不认的模型名，每个步骤重新把整条链试一遍，单个任务拖到 54 分钟
    才收场。模型级死错按 (供应商, 模型) 在本场记住，后续步骤直接跳过。
    """
    if not run_id:
        return False
    provider = str(provider_id or "").strip()
    name = str(model or "").strip()
    if not name:
        return False
    value = error_code_value(code)
    if value != ErrorCode.MODEL_UNAVAILABLE.value:
        return False
    row = {"code": value, "summary": safe_error_summary(value),
           "provider_id": provider, "model": name,
           "at": float(at if at is not None else time.time())}
    with _LOCK:
        entries = _BENCHED_MODELS.setdefault(str(run_id), {})
        key = model_key(provider, name)
        if key in entries:
            return False   # 已停用，保留首报现场
        entries[key] = row
        if len(_BENCHED_MODELS) > _MAX_RUNS:
            oldest = min(_BENCHED_MODELS, key=lambda k: max(
                (v.get("at") or 0) for v in _BENCHED_MODELS[k].values()) or 0)
            _BENCHED_MODELS.pop(oldest, None)
    return True


def model_benched_keys(run_id):
    """本场已确认不可用的 ``(provider_id, model)`` 集合。"""
    try:
        with _LOCK:
            entries = dict(_BENCHED_MODELS.get(str(run_id), {}))
        out = set()
        for row in entries.values():
            out.add((str(row.get("provider_id") or ""),
                     str(row.get("model") or "")))
        return out
    except Exception:
        return set()


# ---------------------------------------------------------------- 网络熔断
# run 内「同一上游连续网络死错」的连败台账（2026-10-26 实案：死本地代理把
# 大半供应商打成 10061 连接被拒，同一 aider 通道在一场 run 里反复送死 20+
# 次，每个死候选都是完整 CLI 冷启动+自带重试梯，步骤拖成小时级）。与
# _BENCHED 的区别：只认 NETWORK（确定性「端点不可达」族，不含 TIMEOUT——
# 慢不是死），计数跨步骤共享，任一成功即复位，进程内存不落盘（恢复探活由
# 下一次真实尝试天然承担）。阈值 3：单次抖动不熔断，连死三轮必绕行。

_NET_FUSE_THRESHOLD = 3
# run_id -> {upstream: 连续网络死错次数}
_NETDEAD = {}


def note_network_failure(run_id, upstream, at=None):
    """记一次上游网络死错；连败达到阈值时该上游进入本场网络熔断名单。"""
    if not run_id:
        return False
    upstream = str(upstream or "").strip()
    if not upstream or upstream == "unknown":
        return False
    with _LOCK:
        counters = _NETDEAD.setdefault(str(run_id), {})
        counters[upstream] = counters.get(upstream, 0) + 1
        if len(_NETDEAD) > _MAX_RUNS:
            oldest = min(_NETDEAD, key=lambda k: max(
                _NETDEAD[k].values()) if _NETDEAD[k] else 0)
            _NETDEAD.pop(oldest, None)
        return counters[upstream] >= _NET_FUSE_THRESHOLD


def note_upstream_ok(run_id, upstream):
    """链上任一次成功即复位该上游的连败计数（恢复探活语义）。"""
    if not run_id:
        return False
    upstream = str(upstream or "").strip()
    if not upstream:
        return False
    with _LOCK:
        counters = _NETDEAD.get(str(run_id))
        if counters and upstream in counters:
            counters.pop(upstream, None)
            if not counters:
                _NETDEAD.pop(str(run_id), None)
        return True


def network_dead_upstreams(run_id):
    """本场已熔断（连续网络死错达阈值）的 upstream 键集合。"""
    try:
        with _LOCK:
            counters = _NETDEAD.get(str(run_id), {})
            return {u for u, n in counters.items()
                    if n >= _NET_FUSE_THRESHOLD}
    except Exception:
        return set()


# ------------------------------------------- 阻塞型死错连败熔断（2026-11-05）

# 上游不可用类死错（UPSTREAM_SERVER / UPSTREAM_CONFIG）在判决轴上属 blocked，
# 刻意不在 ACCOUNT_DEAD_CODES 里：一次 502 不该把一条 CLI 停掉，也不该算
# 「候选链连续失败」。但 blocked 不等于「可以一直撞」——2026-11-05 生产实案：
# 一日 92 条 UPSTREAM_SERVER 全是 codex 上游 503，摊在 26 个不同步骤上（同一本
# 书 8 个章节的评审席），每一步都把同一个 CLI 重新选上，时间线排出 13 张失败
# 卡。那次连供应商级熔断都没建立（provider 归属为空 80 条、命中已停用配置被
# 丢弃 28 条），于是「谁都没数」——护栏只保护它数得到的动作。
# 这一层只做一件事：同一 CLI 同一错误码**连续** N 次（中途该 CLI 无成功）→
# 记入本场停用名单；既有换将/补位的 exclude 经 benched_ids 直接吃到，run 收场
# 随 reset() 一起清。
# 粒度取舍：不做账号级（那是 ACCOUNT_DEAD_CODES 的事，会落盘跨 run 生效），
# 也不做上游级（runner 的 NETWORK 熔断按 upstream 记，前提是拿得到归属）——
# 按 CLI 记是「归属为空」时唯一还数得清的量。
_BLOCKED_FUSE_THRESHOLD = 3
# 「要不要把码集放宽」这件事已经有实测答案，别再猜：拿 11-05 全量 step 失败
# （124 条 / 15 个 run）按时间重放进这套台账，只认 UPSTREAM_* 时 124 → 56
# （绕开 68 条）；把 VENDOR_ERROR 也收进来**多省 0 步**，再收 TIMEOUT /
# PARSE_FAIL / NETWORK 仍然 0——24 条 VENDOR_ERROR 全在 codex 名下，而同一条
# CLI 的 UPSTREAM_SERVER 早已先把它熔断，后来的每一条都落在「已绕开」里。
# 扩码集只会放大误判面（把「慢/本地崩溃」的通道也赶下场），收益是零。
_BLOCKED_FUSE_CODES = frozenset({
    ErrorCode.UPSTREAM_SERVER.value,
    ErrorCode.UPSTREAM_CONFIG.value,
})
# run_id -> {agent_id: {"code": str, "n": int, "at": float}}
_BLOCKED_STREAK = {}


def blocked_threshold():
    """连败熔断阈值。文案与判据共用这一处，避免改了阈值文案还对不上。"""
    return _BLOCKED_FUSE_THRESHOLD


def note_blocked_failure(run_id, agent_id, code, at=None):
    """记一次阻塞型死错；同因连败达阈值即把该 CLI 记入本场停用名单。

    返回 True 表示「这一条把 CLI 熔断了」，调用方可在步骤备注里说明原因。
    同因判定只看错误码：换码说明换了一种故障，重新计数——「连续失败」指的是
    同一件事反复失败，混码累计会把一次抖动叠成死错。

    只认 UPSTREAM_SERVER / UPSTREAM_CONFIG。TIMEOUT 是慢不是死；NETWORK 有
    自己的 upstream 级熔断（见 note_network_failure）；QUOTA/FORBIDDEN 走
    账号级首报即停。放进这里会让三类各自的有效语义互相打架。
    """
    if not run_id or not agent_id:
        return False
    value = error_code_value(code)
    if value not in _BLOCKED_FUSE_CODES:
        return False
    now = float(at if at is not None else time.time())
    aid = str(agent_id)
    with _LOCK:
        rows = _BLOCKED_STREAK.setdefault(str(run_id), {})
        row = rows.get(aid) or {}
        n = int(row.get("n") or 0) + 1 if row.get("code") == value else 1
        rows[aid] = {"code": value, "n": n, "at": now}
        if len(_BLOCKED_STREAK) > _MAX_RUNS:
            oldest = min(_BLOCKED_STREAK,
                         key=lambda k: max((r.get("at") or 0)
                                           for r in _BLOCKED_STREAK[k].values()) or 0)
            _BLOCKED_STREAK.pop(oldest, None)
        if n < _BLOCKED_FUSE_THRESHOLD:
            return False
        entries = _BENCHED.setdefault(str(run_id), {})
        if aid in entries:
            return False   # 已停用，保留首报现场（与 bench() 同语义）
        entries[aid] = {
            "code": value,
            "summary": safe_error_summary(value),
            "at": now,
            "source": "blocked",
            "streak": n,
        }
        if len(_BENCHED) > _MAX_RUNS:
            oldest = min(_BENCHED, key=lambda k: max(
                (v.get("at") or 0) for v in _BENCHED[k].values()) or 0)
            _BENCHED.pop(oldest, None)
    _LOG.info("[bench] %s 同因连续 %d 次 %s，本场绕开", aid, n, value)
    return True


def note_blocked_ok(run_id, agent_id):
    """该 CLI 真实成功一次：连败清零，并把「连败熔断」摘掉。

    只摘 source=blocked 的那条——账号级死错要等 record_success 的统一恢复
    语义处理，不能靠一次成功把「欠费」也当没事。熔断中的 CLI 本不会被选中，
    所以这条路主要服务「熔断后又被兜底选上、居然成功了」的情形。
    """
    if not run_id or not agent_id:
        return False
    aid = str(agent_id)
    with _LOCK:
        rows = _BLOCKED_STREAK.get(str(run_id))
        cleared = False
        if rows and aid in rows:
            rows.pop(aid, None)
            cleared = True
            if not rows:
                _BLOCKED_STREAK.pop(str(run_id), None)
        entries = _BENCHED.get(str(run_id)) or {}
        if (entries.get(aid) or {}).get("source") == "blocked":
            entries.pop(aid, None)
            if not entries:
                _BENCHED.pop(str(run_id), None)
            cleared = True
        return cleared


def model_benched_agents(run_id):
    """本场因模型全死而整体不可用的 CLI id（供报告与告警展示）。"""
    try:
        with _LOCK:
            entries = dict(_BENCHED_MODELS.get(str(run_id), {}))
        out = {}
        for row in entries.values():
            aid = str(row.get("agent_id") or "")
            if aid:
                out.setdefault(aid, []).append(str(row.get("model") or ""))
        return out
    except Exception:
        return {}


def _probe_account(agent_id, state):
    """Run a tiny read-only call through the same CLI account.

    The registry/catalog lookup is intentionally performed inside the worker:
    installed CLI state can change while the service is running.  Missing or
    disabled software is a failed probe and remains quarantined until it is
    available again.
    """
    try:
        from . import catalog, manager, registry, runner, paths
        agent = registry.installed_agent(agent_id, catalog.load(), manager.detect_all())
        if not agent:
            return False
        result = runner.run_agent(
            agent,
            "健康探测：只回复 1，不执行工具，不修改文件。",
            workdir=str(paths.DATA_DIR), readonly=True,
            timeout=_ACCOUNT_PROBE_TIMEOUT,
            command_context={"run_id": "account-health-probe",
                             "role": "health-probe", "allow_terminal": False})
        return bool(result and result.get("ok"))
    except Exception as exc:
        _LOG.debug("账号 %s 探活失败：%s", agent_id, exc)
        return False


def _record_probe_result(agent_id, state, ok):
    with _LOCK:
        if _ACCOUNTS.get(agent_id) is not state or state.get("status") != "down":
            return
        now = time.time()
        state["last_probe_at"] = now
        if ok:
            # Inline to avoid a second lookup and preserve the identity guard.
            state.update({"status": "recovered", "recovered_at": now,
                          "next_probe_at": 0, "probe_failures": 0,
                          "alerting": False, "ever_succeeded": True,
                          "last_success_at": now})
            for entries in _BENCHED.values():
                entries.pop(agent_id, None)
            _persist()
            _LOG.info("[bench] 探针确认账号 %s 已恢复，重新纳入调度", agent_id)
        else:
            state["probe_failures"] = int(state.get("probe_failures") or 0) + 1
            state["next_probe_at"] = now + _probe_delay(state)
            _persist()


def _probe_loop():
    while True:
        try:
            _probe_due_accounts()
        except Exception:
            _LOG.exception("account health probe loop error")
        time.sleep(_ACCOUNT_PROBE_TICK)


def _probe_due_accounts(now=None):
    """Probe every quarantined account whose persisted retry time has arrived."""
    now = float(now if now is not None else time.time())
    with _LOCK:
        targets = [(aid, state) for aid, state in _ACCOUNTS.items()
                   if state.get("status") == "down"
                   and float(state.get("next_probe_at") or 0) <= now]
        for _aid, state in targets:
            state["last_probe_at"] = now
    for aid, state in targets:
        _record_probe_result(aid, state, _probe_account(aid, state))


# ---------------------------------------------------------------- 手动体检

def quarantined_ids():
    """当前隔离中的账号 id 快照（手动「重新体检」的账号侧目标名单）。"""
    try:
        with _LOCK:
            return [aid for aid, state in _ACCOUNTS.items()
                    if state.get("status") == "down"]
    except Exception:
        return []


def probe_account(agent_id):
    """立即探活一个隔离账号（手动「重新体检」路径）。

    自动探针按 5→30 分钟退避走场；用户登录/充值后手动体检不该陪跑退避
    （2026-10-22 实案：健康页「重新体检」只探供应商台账，那排「账号失效」
    行最长要干等 30 分钟才轮到自动探针）。探活前把 next_probe_at 顺延一小
    段，防止后台循环把同一账号并发双跑；结果仍走 _record_probe_result
    （身份校验 + 恢复即清全场停用）。未知/已恢复的行按未跑处理。
    """
    aid = str(agent_id or "")
    with _LOCK:
        state = _ACCOUNTS.get(aid)
        if not state or state.get("status") != "down":
            return False
        state["next_probe_at"] = time.time() + 60
    _record_probe_result(aid, state, _probe_account(aid, state))
    return True
