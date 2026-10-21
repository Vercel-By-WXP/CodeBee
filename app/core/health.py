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
CLI_FAILURE_THRESHOLD = 3
CLI_COOLDOWN_SECONDS = 300
_PERMANENT_FAILURE_CODES = frozenset({
    "QUOTA", "AUTH", "MISSING_CREDENTIAL", "FORBIDDEN", "MODEL_UNAVAILABLE",
})
_MODEL_FAILURE_CODES = frozenset({"FORBIDDEN", "MODEL_UNAVAILABLE"})

_LOCK = threading.RLock()
_PROVIDERS = {}              # name -> 状态 dict
_FILE = None                 # init() 注入（data/provider_health.json）
_PROBE_THREAD = None
_STARTED = False
_UNSOURCED_LOG_COOLDOWN_S = 300.0
_UNSOURCED_LOGGED = {}


def init(data_dir=None, *, start_probe=True):
    """注入存储路径并从磁盘恢复状态（服务重启不丢告警上下文）。

    ``start_probe=False`` 供单元测试等受控场景使用，避免留下跨用例的
    daemon 探针线程。
    """
    global _FILE, _PROVIDERS, _STARTED, _PROBE_THREAD
    with _LOCK:
        _UNSOURCED_LOGGED.clear()
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
        # Older versions recorded runner's generic VENDOR_ERROR for local CLI
        # crashes (the error envelope itself had no upstream signal).  Those
        # rows survive restarts unless explicitly migrated, so remove them
        # before exposing the first health snapshot.  A real classified signal
        # such as HTTP 5xx is retained.
        stale_false_positives = [
            name for name, st in _PROVIDERS.items()
            if _is_local_false_positive(st)
        ]
        if stale_false_positives:
            for name in stale_false_positives:
                _PROVIDERS.pop(name, None)
            _persist()
            log.info("[health] 清除旧版 CLI 本地故障误报：%s",
                     ", ".join(stale_false_positives))
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
        # 快照闸误记案的数据修复（2026-10-17）：last_error 是「未启动外部
        # CLI」族的条目，故障全部来自本地起跑闸而非供应商（mimo/opencode
        # 被嵌套 worktree 快照拒绝连记 4 次假告警），启动即解除；report_failure
        # 入口已拦同类错误，不会再新增。
        poisoned = [k for k, st in _PROVIDERS.items()
                    if "未启动外部 CLI" in str(st.get("last_error") or "")]
        if poisoned:
            for k in poisoned:
                _PROVIDERS[k].update({"status": "recovered",
                                      "consecutive_failures": 0,
                                      "alerted": False, "probe_next_at": 0,
                                      "recovered_at": _now()})
            _persist()
            log.info("[health] 清除快照闸误记的健康故障：%s", ", ".join(poisoned))
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


def _allow_unsourced_log(provider, model, text):
    """Rate-limit repetitive local CLI crashes without hiding new signatures."""
    import re
    signature = re.sub(r"\d+", "N", str(text or "").lower())
    signature = re.sub(r"\s+", " ", signature).strip()[:240]
    key = (str(provider or ""), str(model or ""), signature)
    now = time.monotonic()
    with _LOCK:
        previous = _UNSOURCED_LOGGED.get(key, 0.0)
        if now - previous < _UNSOURCED_LOG_COOLDOWN_S:
            return False
        _UNSOURCED_LOGGED[key] = now
        if len(_UNSOURCED_LOGGED) > 512:
            cutoff = now - _UNSOURCED_LOG_COOLDOWN_S
            for old_key, at in list(_UNSOURCED_LOGGED.items()):
                if at < cutoff:
                    _UNSOURCED_LOGGED.pop(old_key, None)
            while len(_UNSOURCED_LOGGED) > 512:
                _UNSOURCED_LOGGED.pop(next(iter(_UNSOURCED_LOGGED)))
        return True


def _is_local_false_positive(st):
    """Whether a persisted provider row is known to be a local-only failure."""
    if not isinstance(st, dict):
        return False
    # The gate prefix is a fixed local marker, stronger than any generic
    # classification attached to the row; check it before the code gate.
    text = str(st.get("last_error") or "")
    if _is_sandbox_gate_rejection(text):
        return True
    code = str(st.get("last_error_code") or "").upper()
    if code in {"TOOL_LAUNCH", "CONTEXT_OVERFLOW"}:
        return True
    if code and code != "VENDOR_ERROR":
        return False
    if "未启动外部 CLI" in text[:160] or _LAUNCH_FAILURE_PREFIX in text[:80]:
        return True
    if "stderr/stdout:" not in text[:160]:
        return False
    try:
        from .error_codes import classify_error_text
        # VENDOR_ERROR is runner's generic fallback, not evidence that the
        # provider failed.  Only discard an envelope when no stronger signal
        # can be classified from its text.
        return classify_error_text(text) is None
    except Exception:
        return False


def _fmt_ts(epoch):
    if not epoch:
        return ""
    return time.strftime("%m-%d %H:%M", time.localtime(epoch))


# ---------------------------------------------------------------- 上报入口

def report_success(provider: str, *, model: str = ""):
    """一次真实成功调用（CLI 或 API 直连）。"""
    if not provider:
        return
    with _LOCK:
        st = _PROVIDERS.get(provider)
        if st is None:
            return  # 没失败过就不建档（零开销）
        model = str(model or "").strip()
        model_circuits = st.setdefault("model_circuits", {})
        if model and model in model_circuits:
            model_circuits.pop(model, None)
        if st.get("status") in ("failing", "down"):
            st["status"] = "recovered"
            st["recovered_at"] = _now()
            st["consecutive_failures"] = 0
            st["last_ok_at"] = _now()
            st["probe_next_at"] = 0
            st["alerted"] = False
            st["silenced"] = False       # 静默只管一次故障期；恢复后重新武装
            st["silence_until"] = 0
            st["half_open"] = False
            st["circuit_open_until"] = 0
            st.pop("permanent_error_code", None)
            log.info("[health] %s 已恢复（告警自动解除）", provider)
        else:
            st["last_ok_at"] = _now()
            st["consecutive_failures"] = 0
        _persist()


# run_process 包裹 Popen 启动异常的固定前缀。与 error_codes._TOOL_LAUNCH 的
# 判定标记同源双写（error_codes 在 L0，health 在 L1，策略禁止 L1→L0 import；
# 供应商侧任何报错都不会带这个前缀，单靠前缀即可零误判）。
_LAUNCH_FAILURE_PREFIX = "启动失败"

# pipeline._external_sandbox_block_reason 的固定文案族：起跑前的本地策略闸门。
# 供应商一次请求都没收到，记进健康台账会把健康通道推进 down 并踢出候选链
# （2026-10-21 实案：存量任务沙箱里 allowed_tools=None 二次规范化失败，
# MiMo/OpenCode/维云/Bigmodel 四个供应商被同一条本地拒绝连坐）。
_SANDBOX_GATE_PREFIXES = ("沙箱拒绝：", "权限拒绝：")


def _is_sandbox_gate_rejection(text):
    return str(text or "").lstrip().startswith(_SANDBOX_GATE_PREFIXES)


def _inactive_config(provider_id, provider, model):
    """Return whether a known configured provider/model is disabled.

    A late result from a task that was already removed from scheduling must
    not recreate a health alert. Unknown synthetic providers are left alone so
    health telemetry remains usable before model configuration is initialized.
    """
    if not provider_id and not provider:
        return False
    try:
        from . import modelhub
        configured = _configured_provider(modelhub.providers(), provider_id, provider)
        if not configured:
            return False
        if configured.get("enabled", True) is False:
            return True
        wanted = str(model or "").strip()
        if not wanted:
            return False
        row = next(
            (item for item in (configured.get("models") or [])
             if isinstance(item, dict)
             and str(item.get("name") or "").strip() == wanted),
            None,
        )
        return bool(row and (row.get("enabled", True) is False
                             or row.get("hidden")))
    except Exception:
        return False


def _configured_provider(plist, provider_id="", provider=""):
    """Resolve a configured provider using its canonical ID when available."""
    wanted_id = str(provider_id or "")
    if wanted_id:
        return next((item for item in plist
                     if str(item.get("id") or "") == wanted_id), None)
    wanted_name = str(provider or "")
    if wanted_name:
        return next((item for item in plist
                     if str(item.get("name") or "") == wanted_name), None)
    return None


def report_failure(provider: str, error: str = "", *, model: str = "",
                   provider_id: str = "", error_code: str = ""):
    """一次真实失败调用。provider 用展示名（与 usage 台账一致）。

    本地 CLI 启动失败（找不到可执行文件等）与供应商无关，记进来会把健康
    供应商的评测结论作废、链被解析层过滤成死链——2026-09-30 claude 垫片被
    清案：Bigmodel 全程健康却被连续启动失败拖进 down。只打日志提醒修本地
    环境，不进健康台账。

    同理，CLI 进程捕获型失败（runner 的「…；stderr/stdout: <尾部>」包装）若
    全文分类不出任何供应商侧信号，也不进台账（2026-10-16 起草全灭案）：
    opencode 横幅秒退的已知瞬时签名「退出码 1；stderr/stdout: > build · …」
    （0930 实案：数分钟后同进程自愈）、死链闸与沙箱闸的起跑前拒绝，责任都
    不在上游——健康的 Bigmodel 被这种崩溃文本连续作废评测，所有绑定它的
    CLI 连锁死链。不带该包装的无分类错误（新措辞的限额文案等）保持照记：
    误报的代价只是白跑几次注定失败的尝试，漏报的代价是健康供应商被雪崩
    误杀，后者远重——拿不准时宁可不记。"""
    if not provider:
        return
    if _inactive_config(provider_id, provider, model):
        log.info("[health] 忽略已停用供应商/模型的失败：%s / %s",
                 provider, model or "默认模型")
        return
    if _LAUNCH_FAILURE_PREFIX in str(error or "")[:80]:
        log.warning("[health] 本地 CLI 启动失败（与供应商 %s 无关，不记健康故障）：%s",
                    provider, str(error or "")[:160])
        return
    text = str(error or "")
    if _is_sandbox_gate_rejection(text):
        log.warning("[health] 起跑前沙箱/权限闸拒绝（供应商 %s 未收到请求，"
                    "不记健康故障）：%s", provider, text[:160])
        return
    # Deterministic account/model failures should open a short circuit on the
    # first observation.  Waiting for the generic three-failure threshold makes
    # every chapter or reviewer retry a request that cannot succeed.
    try:
        from .error_codes import classify_error_text, error_code_value
        supplied_code = error_code_value(error_code)
        detected_code = error_code_value(classify_error_text(text))
        code = (detected_code
                if supplied_code in ("", "VENDOR_ERROR") and detected_code
                else supplied_code or detected_code)
    except Exception:
        code = getattr(error_code, "value", str(error_code or ""))
    lower_text = text.lower()
    if ("unknown model" in lower_text or "invalid model" in lower_text
            or "model not found" in lower_text or "unknown model id" in lower_text):
        code = "MODEL_UNAVAILABLE"
    if "stderr/stdout:" in text[:160]:
        try:
            from .error_codes import classify_error_text
            # Prefer the structured code already produced by runner. Long CLI
            # envelopes may contain truncation/diagnostic text that obscures a
            # reliable signal when the whole string is classified again.
            # VENDOR_ERROR is runner's generic fallback.  It is not a
            # structured upstream signal, so a plain CLI stderr envelope with
            # that code must still be treated as local/unknown noise.
            unclassified = (code in ("", "VENDOR_ERROR")
                            and classify_error_text(text) is None)
        except Exception:
            unclassified = False
        if unclassified:
            if _allow_unsourced_log(provider, model, text):
                log.warning("[health] CLI 崩溃无供应商侧信号（与供应商 %s 无关，"
                            "不记健康故障）：%s", provider, text[:160])
            return
    # Prompt-size failures are local input problems. They should trigger the
    # compaction/retry path, never poison provider health or flood the console.
    if code == "CONTEXT_OVERFLOW":
        return
    # 快照闸起跑前拒绝（checkpoints.begin_git_step 的固定文案族）是本地闸门
    # 决定，供应商侧零参与——记进去会把健康的 CLI 通道按连续失败推进 down
    # 并踢出候选链（2026-10-16 实案：mimo/opencode 连记 4 次假告警）。与
    # _LAUNCH_FAILURE_PREFIX 同款同源双写：health 在 L1 不能 import
    # checkpoints，只认固定后缀；该后缀不出现在任何供应商侧报错里。
    if "未启动外部 CLI" in text[:160]:
        log.warning("[health] 快照闸起跑前拒绝（与供应商 %s 无关，不记健康故障）：%s",
                    provider, text[:160])
        return

    # A failed live call invalidates capability evidence for this upstream.
    # Keep this hook lazy and best-effort: health telemetry must never make a
    # model call fail, and importing evaluation at module load would create a
    # dependency cycle during startup.  ``provider_id`` is the canonical
    # identity used by the evaluation ledger; the display name is not safe to
    # use as a substitute because aliases can point at the same host.
    if provider_id:
        try:
            from . import evaluation
            evaluation.invalidate_provider(provider_id, model=model)
        except Exception:
            log.debug("[health] 无法使评测结论失效", exc_info=True)
    with _LOCK:
        # The configuration can be disabled between the fast pre-check above
        # and this write. Recheck while holding the health lock so a late
        # result cannot recreate a row after disable() has cleared it.
        if _inactive_config(provider_id, provider, model):
            log.info("[health] 忽略已停用供应商/模型的迟到失败：%s / %s",
                     provider, model or "默认模型")
            return
        st = _PROVIDERS.setdefault(provider, {
            "name": provider, "provider_id": provider_id or "",
            "model": model or "", "status": "ok",
            "consecutive_failures": 0, "first_fail_at": 0,
            "last_fail_at": 0, "last_ok_at": 0, "last_error": "",
            "alerted": False, "silenced": False, "silence_until": 0,
            "probe_next_at": 0, "probe_backoff_idx": 0,
            "recovered_at": 0, "model_circuits": {},
        })
        st["name"] = provider
        if provider_id and not st.get("provider_id"):
            st["provider_id"] = provider_id
        if model and not st.get("model"):
            st["model"] = model
        model = str(model or "").strip()
        if code in _MODEL_FAILURE_CODES and model:
            circuits = st.setdefault("model_circuits", {})
            previous = circuits.get(model) or {}
            circuits[model] = {
                "open_until": _now() + CLI_COOLDOWN_SECONDS,
                "error_code": code,
                "failures": int(previous.get("failures") or 0) + 1,
                "last_error": str(error or "")[:300],
                "updated_at": _now(),
            }
            # Model-specific errors must not contaminate provider-level
            # failure counters or timestamps used by the provider circuit.
            st["last_error_code"] = code
            _persist()
            return
        n = int(st.get("consecutive_failures") or 0) + 1
        st["consecutive_failures"] = n
        st["last_fail_at"] = _now()
        st["last_error"] = str(error or "")[:300]
        st["last_error_code"] = code
        if st.get("status") in ("ok", "recovered", ""):
            st["status"] = "failing"
            st["first_fail_at"] = _now()
            st["probe_next_at"] = _now() + PROBE_BACKOFF[0]
            st["probe_backoff_idx"] = 0
        if code in _PERMANENT_FAILURE_CODES:
            st["status"] = "down"
            st["circuit_open_until"] = _now() + CLI_COOLDOWN_SECONDS
            st["permanent_error_code"] = code
        elif n >= CLI_FAILURE_THRESHOLD:
            st["status"] = "down"
            st["circuit_open_until"] = _now() + CLI_COOLDOWN_SECONDS
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
                   "probe_next_at": 0, "recovered_at": _now(),
                   "circuit_open_until": 0, "half_open": False,
                   "model_circuits": {}})
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
    _prune_inactive_configured()
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
        result = {
            "providers": out,
            "alerts": [p for p in out if p["alerting"]],
            "any_alerting": any(p["alerting"] for p in out),
        }
        # CLI account health is maintained by bench to keep provider/API
        # telemetry independent.  Expose it through the same health endpoint
        # so the UI and external monitors see recovery without a second poll.
        try:
            from . import bench
            account_health = bench.snapshot()
            result["accounts"] = account_health.get("accounts", [])
            result["account_alerts"] = account_health.get("alerts", [])
            result["any_alerting"] = bool(result["any_alerting"] or
                                           account_health.get("any_alerting"))
        except Exception:
            result["accounts"] = []
            result["account_alerts"] = []
        return result


def is_down(provider: str) -> bool:
    """链降级查询：该供应商当前是否 down（供 resolve_binding 跳过）。"""
    with _LOCK:
        st = _PROVIDERS.get(provider)
        if not st:
            return False
        if st.get("status") != "down":
            return False
        until = float(st.get("circuit_open_until") or 0)
        if until and until <= _now():
            # Reserve exactly one probe while half-open. Other callers keep
            # seeing the circuit as down until that probe reports recovery.
            if st.get("half_open"):
                return True
            st["half_open"] = True
            return False
        return True


def is_model_down(provider: str, model: str) -> bool:
    """Return whether one provider/model pair is in a short model circuit."""
    provider = str(provider or "")
    model = str(model or "").strip()
    if not provider or not model:
        return False
    with _LOCK:
        st = _PROVIDERS.get(provider) or {}
        circuits = st.get("model_circuits") or {}
        row = circuits.get(model)
        if not isinstance(row, dict):
            return False
        until = float(row.get("open_until") or 0)
        if until and until <= _now():
            circuits.pop(model, None)
            _persist()
            return False
        return bool(until)


def down_models() -> set:
    """Return active ``(provider, model)`` model-level circuits."""
    now = _now()
    out = set()
    with _LOCK:
        changed = False
        for provider, st in _PROVIDERS.items():
            circuits = st.get("model_circuits") or {}
            for model, row in list(circuits.items()):
                if not isinstance(row, dict) or float(row.get("open_until") or 0) <= now:
                    circuits.pop(model, None)
                    changed = True
                else:
                    out.add((provider, model))
        if changed:
            _persist()
    return out


def cli_status(cli: str):
    """Return circuit-breaker state for a local CLI/provider."""
    with _LOCK:
        st = _PROVIDERS.get(cli) or {}
        until = float(st.get("circuit_open_until") or 0)
        return {"cli": cli, "status": st.get("status", "ok"),
                "consecutive_failures": int(st.get("consecutive_failures") or 0),
                "last_error": st.get("last_error", ""),
                "open_until": until, "half_open": bool(st.get("half_open")),
                "available": not is_down(cli)}


def down_names() -> set:
    """链降级剔除名单。

    无密钥的 CLI 原生通道（探针探不了）例外：它们的 down 没有探针复活路径，
    唯一的恢复信号是真实调用——熔断冷却（circuit_open_until，每次真实失败
    顺延 CLI_COOLDOWN_SECONDS）一过就放行回候选，成则 report_success 翻绿，
    败则顺延冷却，有界重试而不是永久踢出（2026-10-17 mimo/opencode 死锁案：
    down → 剔出候选 → 永远等不到 report_success → 永锁）。"""
    with _LOCK:
        out = set()
        for n, st in _PROVIDERS.items():
            if st.get("status") != "down":
                continue
            if st.get("unprobeable"):
                until = float(st.get("circuit_open_until") or 0)
                if not until or until <= _now():
                    continue
            out.add(n)
        return out


# ---------------------------------------------------------------- 探针

def _probe_one(st):
    """对单个故障 provider 发轻量探活。返回 (ok, gone)：
    ok=是否恢复；gone=供应商已从配置里删除（健康记录应随之清掉）。"""
    try:
        from . import modelhub
        pid = st.get("provider_id") or ""
        model = st.get("model") or ""
        plist = modelhub.providers()
        prov = _configured_provider(plist, pid, st.get("name"))
        if not prov or prov.get("enabled", True) is False:
            return False, True   # 已删除/停用：不再探测，也不保留旧故障
        if model:
            model_row = next((item for item in (prov.get("models") or [])
                              if isinstance(item, dict)
                              and str(item.get("name") or "").strip() == model), None)
            if model_row and (model_row.get("enabled", True) is False
                              or model_row.get("hidden")):
                return False, True
        if not prov.get("api_key"):
            st["unprobeable"] = True   # CLI 原生免费通道没有密钥，HTTP 探不了
            return False, False  # 供应商在但没密钥：无法探测，保持状态
        if not model:
            try:
                names = modelhub._enabled_models(prov)
                model = names[0]["name"] if names else ""
            except Exception:
                model = ""
        if not model:
            st["unprobeable"] = True
            return False, False
        st["unprobeable"] = False
        res = modelhub.chat(prov["id"], model, "1", max_tokens=8, timeout=20)
        return bool(res.get("ok")), False
    except Exception as e:
        log.debug("probe %s error: %s", st.get("name"), e)
        return False, False


def _prune_missing():
    """清理已删除或停用供应商/模型的过期健康记录。

    幽灵条目探不活也永远不会恢复，还会每轮把 last_fail_at 刷成「刚刚」，
    健康页看起来像它一直在报错（Z.ai 删掉后仍显示 429 的误伤案，2026-09-18）。
    内置服务自动停用后，旧 transport 告警也不应继续显示或被后台探针触发。
    """
    try:
        from . import modelhub
        plist = modelhub.providers()
    except Exception:
        return
    def active(st):
        provider_id = str(st.get("provider_id") or "")
        prov = _configured_provider(plist, provider_id, st.get("name"))
        if not prov or prov.get("enabled", True) is False:
            return False
        model = str(st.get("model") or "").strip()
        if model:
            row = next((item for item in (prov.get("models") or [])
                        if isinstance(item, dict)
                        and str(item.get("name") or "").strip() == model), None)
            if row and (row.get("enabled", True) is False or row.get("hidden")):
                return False
        return True

    with _LOCK:
        stale = [name for name, st in _PROVIDERS.items() if not active(st)]
        if stale:
            for name in stale:
                _PROVIDERS.pop(name, None)
            _persist()
            log.info("[health] 清除已删除或停用供应商/模型的健康记录：%s",
                     ", ".join(stale))


def clear_inactive():
    """清除已删除或停用供应商/模型留下的健康状态。"""
    _prune_inactive_configured()


def _prune_inactive_configured():
    """Remove rows that match a currently configured disabled item.

    Do not remove unknown rows here: health tests and early-startup telemetry
    can legitimately arrive before the model registry has been initialized.
    """
    try:
        from . import modelhub
        plist = modelhub.providers()
    except Exception:
        return
    def inactive(st):
        provider_id = str(st.get("provider_id") or "")
        prov = _configured_provider(plist, provider_id, st.get("name"))
        if not prov:
            return False
        if prov.get("enabled", True) is False:
            return True
        model = str(st.get("model") or "").strip()
        if not model:
            return False
        row = next((item for item in (prov.get("models") or [])
                    if isinstance(item, dict)
                    and str(item.get("name") or "").strip() == model), None)
        return bool(row and (row.get("enabled", True) is False
                             or row.get("hidden")))

    with _LOCK:
        stale = [name for name, st in _PROVIDERS.items() if inactive(st)]
        if stale:
            for name in stale:
                _PROVIDERS.pop(name, None)
            _persist()
            log.info("[health] 清除已停用供应商/模型的健康记录：%s",
                     ", ".join(stale))


def _record_probe_result(name, st, ok, gone):
    """只应用仍属于当前状态表的探针结果，忽略跨 init/测试的过期结果。"""
    with _LOCK:
        if _PROVIDERS.get(name) is not st:
            return
        if gone:
            _PROVIDERS.pop(name, None)
            _persist()
            log.info("[health] %s 已删除或停用，健康记录随之清除", name)
        elif ok:
            report_success(name)
            log.info("[health] 探针确认 %s 已恢复", name)
        else:
            # 探不了（无密钥 CLI 通道）≠ 探挂了：不刷新 last_fail_at，否则
            # 「最后失败」永远显示成刚刚，告警看着像持续复发（2026-10-17
            # mimo/opencode 弹窗实案：probe_backoff 149 轮全是在刷这个字段）。
            # 恢复信号交给真实调用（report_success / down_names 冷却放行）。
            if not st.get("unprobeable"):
                st["last_fail_at"] = _now()
            _persist()


def _probe_loop():
    while True:
        try:
            with _LOCK:
                targets = [(name, st) for name, st in _PROVIDERS.items()
                           if not st.get("static")  # 静态死链无端点可探，绑定恢复时自动解除
                           and st.get("status") in ("failing", "down")
                           and float(st.get("circuit_open_until") or 0) <= _now()
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
