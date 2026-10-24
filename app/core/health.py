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
import re
import threading
import time
from pathlib import Path

from . import atomic

log = logging.getLogger(__name__)
_OUTPUT_CAPTURE_LIMIT_MARKER = "输出超过捕获上限："

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
# EMPTY 也按模型级记账（2026-10-24 stepaudio 实案）：音频系/能力不匹配的模型
# 对纯文本任务恒空答——HTTP 层全程成功、input 照扣费、output 恒 0，文本空
# 输出闸在步内判 EMPTY 后若无模型级熔断，同一模型每一步每一轮都被重新选
# 上，评审人数永远凑不齐。空答是「该模型对该用途不可用」，不是供应商级
# 故障——进模型熔断（首次观测即 300s 短路、真实成功即清、独苗兜底放行），
# 不进 consecutive_failures，免得同供应商其它健康模型连坐。
# 瞬态码（2026-10-24 用户拍板「中转站按模型分快慢：一个模型慢，换一个
# 模型可能很快」）：超时/网络/上游异常/限流后先把该模型按板凳 300s，绑定
# 解析立即换同站健康模型顶位；同时双记账继续供应商级计数——供应商整体
# 断流时供应商熔断仍能开闸驱动跨 CLI 换将（维云 aider 实案：claude-opus-5-5
# 五连撞没人给它记账，下一轮又被原样选回）。
_MODEL_FAILURE_CODES = frozenset({
    "FORBIDDEN", "MODEL_UNAVAILABLE", "EMPTY",
    "TIMEOUT", "NETWORK", "UPSTREAM_SERVER", "RATE_LIMIT",
})
_TRANSIENT_MODEL_CODES = frozenset({
    "TIMEOUT", "NETWORK", "UPSTREAM_SERVER", "RATE_LIMIT",
})
# 证据分级（LayerTrace 借鉴，2026-10-22）：健康处置必须区分「观测到的事实」与
# 「从文本推断的嫌疑」——健康误报复发家族（误记雪崩/快照闸假告警/剪行掩盖成功）
# 的共性真凶都是推断被当成观测进了处置。推断级只告警送检（探针），观测级才够格
# 动手（熔断/短路）；未知级（探不了/异常中断）不刷时间戳。
EVIDENCE_OBSERVED = "observed"   # 观测：runner 从真实上游响应解析的结构化码 / 探针实返回
EVIDENCE_INFERRED = "inferred"   # 推断：从错误文本分类出的供应商信号（CLI 信封/文案匹配）
EVIDENCE_UNKNOWN = "unknown"     # 未知：无可分类信号（本地异常、探不了的通道）
_EVIDENCE_LABELS = {"observed": "观测", "inferred": "推断", "unknown": "未知"}

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
            atomic.write_json(path, {"providers": _PROVIDERS}, mode=0o600,
                              indent=2)
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
    if _OUTPUT_CAPTURE_LIMIT_MARKER in text:
        return True
    if ("CLI 输出不是 UTF-8/GBK" in text
            or "本地 CLI 输出编码已降级" in text):
        return True
    if _is_sandbox_gate_rejection(text):
        return True
    code = str(st.get("last_error_code") or "").upper()
    if code in {"TOOL_LAUNCH", "CONTEXT_OVERFLOW"}:
        return True
    if "未启动外部 CLI" in text[:160] or _LAUNCH_FAILURE_PREFIX in text[:80]:
        return True
    if "stderr/stdout:" not in text[:160]:
        return False
    try:
        from .error_codes import classify_error_text
        # VENDOR_ERROR is runner's generic fallback, not evidence that the
        # provider failed.  Only discard an envelope when no stronger signal
        # can be classified from its text.
        if classify_error_text(text) is None:
            # 分类器修正后（2026-10-21：grep 命中行的行号 429/5xx 曾被旧版
            # 归成 UPSTREAM_SERVER/NETWORK 落盘），这类信封已不再携带供应商
            # 信号——无论当年记的码是什么，凡通用/瞬态族一律按误报清掉。
            # 结构化账号级死错（QUOTA/AUTH 等）文本仍应可分类，落不进这里。
            return code in {"", "VENDOR_ERROR", "UPSTREAM_SERVER", "NETWORK",
                            "TIMEOUT", "RATE_LIMIT", "LOCAL_STATE"}
        return False
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
            st["evidence"] = EVIDENCE_OBSERVED   # 真实成功调用＝一手观测
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


def _resolve_named_model(provider_id, provider, error_text):
    """把模型级错误文本里点名的模型对账回配置表的规范 id。

    不带 model 位的上报（如链候选裸调 grok CLI）错误文本里仍然点名了模型
    （grok「Couldn't set model 'glm-5.3'」/智谱「暂未开放X权限」）。按大小写
    不敏感匹配配置表，保证模型级熔断的 key 与路由层后续查询的一致——
    对不上就返回空串（宁可走供应商级旧路，不造一个查不回的键）。
    """
    try:
        from .error_codes import model_name_from_error
        named = model_name_from_error(error_text).casefold()
        if not named:
            return ""
        from . import modelhub
        configured = _configured_provider(modelhub.providers(),
                                          provider_id, provider)
        if not configured:
            return ""
        for item in (configured.get("models") or []):
            if isinstance(item, dict):
                name = str(item.get("name") or "").strip()
                if name and name.casefold() == named:
                    return name
    except Exception:
        pass
    return ""


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
    text = str(error or "")
    if _inactive_config(provider_id, provider, model):
        log.info("[health] 忽略已停用供应商/模型的失败：%s / %s",
                 provider, model or "默认模型")
        return
    if _LAUNCH_FAILURE_PREFIX in str(error or "")[:80]:
        log.warning("[health] 本地 CLI 启动失败（与供应商 %s 无关，不记健康故障）：%s",
                    provider, str(error or "")[:160])
        return
    # run_process appends this marker only when its bounded local stdout/stderr
    # capture discarded bytes. The response is incomplete, so do not turn the
    # local buffer limit (or an untrustworthy partial error fragment) into a
    # provider circuit failure.
    if _OUTPUT_CAPTURE_LIMIT_MARKER in text:
        log.warning("[health] CLI 输出超过本地捕获上限（供应商 %s 未判故障）：%s",
                    provider, text[:180])
        return
    # The runner marks mixed UTF-8/code-page output explicitly.  This is a
    # local PowerShell/console decoding issue; recording it as a provider
    # failure would open a false circuit and strand a single-CLI run.
    if (text.startswith("本地 CLI 输出编码已降级")
            or "CLI 输出不是 UTF-8/GBK" in text):
        log.warning("[health] 本地 CLI 输出编码降级（供应商 %s 未判故障）：%s",
                    provider, text[:180])
        return
    if _is_sandbox_gate_rejection(text):
        log.warning("[health] 起跑前沙箱/权限闸拒绝（供应商 %s 未收到请求，"
                    "不记健康故障）：%s", provider, text[:160])
        return
    # Deterministic account/model failures should open a short circuit on the
    # first observation.  Waiting for the generic three-failure threshold makes
    # every chapter or reviewer retry a request that cannot succeed.  But the
    # evidence level is recorded alongside: only runner-parsed structured codes
    # (real wire responses) count as observed; text-only classifications are
    # inferences and do not unlock the first-observation short circuit.
    evidence = EVIDENCE_UNKNOWN
    try:
        from .error_codes import classify_error_text, error_code_value
        supplied_code = error_code_value(error_code)
        detected_code = error_code_value(classify_error_text(text))
        if supplied_code not in ("", "VENDOR_ERROR"):
            code, evidence = supplied_code, EVIDENCE_OBSERVED
        elif detected_code:
            code, evidence = detected_code, EVIDENCE_INFERRED
        else:
            code = supplied_code
    except Exception:
        code = getattr(error_code, "value", str(error_code or ""))
        evidence = EVIDENCE_OBSERVED if code else EVIDENCE_UNKNOWN
    if code == "TOOL_LAUNCH":
        # Some CLIs (notably OpenCode) report failures from bundled local tools
        # after their process has started, so they do not carry the Popen
        # "启动失败" prefix. The shared classifier recognizes those signatures
        # as TOOL_LAUNCH; keep them out of provider health just like missing CLI
        # executables, otherwise one local ripgrep crash can poison every model
        # behind the provider.
        log.warning("[health] 本地 CLI 工具链失败（供应商 %s 未判故障）：%s",
                    provider, text[:180])
        return
    lower_text = text.lower()
    if ("unknown model" in lower_text or "invalid model" in lower_text
            or "model not found" in lower_text or "unknown model id" in lower_text):
        code = "MODEL_UNAVAILABLE"
        if evidence != EVIDENCE_OBSERVED:
            evidence = EVIDENCE_INFERRED   # 文案匹配出的模型错，非结构化观测
    # 裸「退出码 N」（stderr/stdout 尾段为空时 runner 不拼包装前缀）＝CLI
    # 本地硬崩溃的另一种形态，全文没有任何供应商侧信号——与带尾段的同族
    # 崩溃同等豁免（2026-10-22 opencode 退出码 3221226505 连记 3 次把免费
    # 通道打成供应商故障实案：旧豁免只认包装前缀，裸形态漏网照记台账）。
    bare_exit_crash = re.fullmatch(r"退出码 -?\d+", text.strip()) is not None
    if "stderr/stdout:" in text[:160] or bare_exit_crash:
        try:
            from .error_codes import classify_error_text
            # Prefer the structured code already produced by runner. Long CLI
            # envelopes may contain truncation/diagnostic text that obscures a
            # reliable signal when the whole string is classified again.
            # VENDOR_ERROR is runner's generic fallback.  It is not a
            # structured upstream signal, so a plain CLI stderr envelope with
            # that code must still be treated as local/unknown noise.
            unclassified = (code in ("", "VENDOR_ERROR", "UPSTREAM_SERVER",
                                     "NETWORK", "TIMEOUT", "RATE_LIMIT",
                                     "LOCAL_STATE")
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
            "evidence": EVIDENCE_UNKNOWN,
        })
        st["name"] = provider
        if provider_id and not st.get("provider_id"):
            st["provider_id"] = provider_id
        if model and not st.get("model"):
            st["model"] = model
        model = str(model or "").strip()
        if code in _MODEL_FAILURE_CODES and not model:
            # 模型级死错但上报没带 model 位（链候选裸调 CLI 的形态）：错误
            # 文本里点名了模型——对账配置表找回规范名，按模型级熔断记账，
            # 不让单个模型的死错连坐整个供应商（2026-10-22 GLM包月 的 grok
            # 不认 glm 模型 id 连续失败 2 次即打穿供应商熔断实案）。
            named = _resolve_named_model(provider_id, provider, str(error or ""))
            if named:
                model = named
        if code in _MODEL_FAILURE_CODES and model:
            circuits = st.setdefault("model_circuits", {})
            previous = circuits.get(model) or {}
            row = {
                "error_code": code,
                "failures": int(previous.get("failures") or 0) + 1,
                "last_error": str(error or "")[:300],
                "updated_at": _now(),
            }
            # 处置闸：只有观测级证据（或探针探不了的通道——文本证据是它唯一的
            # 证据形态，沿用 1017 死锁案的有界冷却设计）才开模型熔断；推断级
            # 记计数与最近错误但不落 open_until，等探针/真实调用的观测证据定谳。
            if evidence == EVIDENCE_OBSERVED or st.get("unprobeable"):
                row["open_until"] = _now() + CLI_COOLDOWN_SECONDS
            circuits[model] = row
            if code in _TRANSIENT_MODEL_CODES:
                # 瞬态：模型级熔断记完账继续供应商级计数（双记账），供应商
                # 熔断与跨 CLI 换将的既有行为保持不变。
                _persist()
            else:
                # Model-specific errors must not contaminate provider-level
                # failure counters or timestamps used by the provider circuit.
                st["last_error_code"] = code
                st["evidence"] = evidence
                _persist()
                return
        n = int(st.get("consecutive_failures") or 0) + 1
        st["consecutive_failures"] = n
        st["last_fail_at"] = _now()
        st["last_error"] = str(error or "")[:300]
        st["last_error_code"] = code
        st["evidence"] = evidence
        if st.get("status") in ("ok", "recovered", ""):
            st["status"] = "failing"
            st["first_fail_at"] = _now()
            st["probe_next_at"] = _now() + PROBE_BACKOFF[0]
            st["probe_backoff_idx"] = 0
        # 处置闸：永久码的「首次观测即短路」只认观测级证据（或探不了的通道）。
        # 推断级的永久码（CLI 信封里文本分类出的 quota/auth 等）降级走连续
        # 次数阈值——告警照发，但先送探针拿一手观测，不再让一次文本命中直接
        # 把供应商踢出候选链（健康误报复发家族的处置半环）。
        if code in _PERMANENT_FAILURE_CODES and (
                evidence == EVIDENCE_OBSERVED or st.get("unprobeable")):
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
                log.warning("[health] ⚠️ 供应商告警：%s 不可用（连续 %d 次失败，"
                            "证据=%s，%s）",
                            provider, n,
                            _EVIDENCE_LABELS.get(st.get("evidence") or "",
                                                 st.get("evidence") or "未知"),
                            st["last_error"][:120])
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
                "evidence": st.get("evidence") or "",
                "evidence_label": _EVIDENCE_LABELS.get(st.get("evidence") or "", ""),
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


def cooldown_remaining(provider: str):
    """供应商剩余熔断秒数（换将「最快解禁先用」排序用，magpie smart 借鉴）。

    provider 级 circuit_open_until 与模型级 model_circuits[*].open_until 取
    最小正值。返回 None=台账无此供应商，或 down 而无任何可预期解禁时间；
    0.0=没有在计时的熔断（未被拦或已到点待探针确认）。"""
    with _LOCK:
        st = _PROVIDERS.get(provider)
        if not st:
            return None
        now = _now()
        cands = []
        until = float(st.get("circuit_open_until") or 0)
        if until > now:
            cands.append(until - now)
        for row in (st.get("model_circuits") or {}).values():
            if not isinstance(row, dict):
                continue
            u = float(row.get("open_until") or 0)
            if u > now:
                cands.append(u - now)
        if cands:
            return min(cands)
        if st.get("status") == "down":
            return None
        return 0.0


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
    """对单个故障 provider 发轻量探活。返回 (ok, gone, wire)：
    ok=是否恢复；gone=供应商已从配置里删除（健康记录应随之清掉）；
    wire=是否拿到线上一手结果（真实响应，区分于本地异常探不了）。"""
    try:
        from . import modelhub
        pid = st.get("provider_id") or ""
        model = st.get("model") or ""
        plist = modelhub.providers()
        prov = _configured_provider(plist, pid, st.get("name"))
        if not prov or prov.get("enabled", True) is False:
            return False, True, False   # 已删除/停用：不再探测，也不保留旧故障
        if model:
            model_row = next((item for item in (prov.get("models") or [])
                              if isinstance(item, dict)
                              and str(item.get("name") or "").strip() == model), None)
            if model_row and (model_row.get("enabled", True) is False
                              or model_row.get("hidden")):
                return False, True, False
        if not prov.get("api_key"):
            st["unprobeable"] = True   # CLI 原生免费通道没有密钥，HTTP 探不了
            return False, False, False  # 供应商在但没密钥：无法探测，保持状态
        if not model:
            try:
                names = modelhub._enabled_models(prov)
                model = names[0]["name"] if names else ""
            except Exception:
                model = ""
        if not model:
            st["unprobeable"] = True
            return False, False, False
        st["unprobeable"] = False
        res = modelhub.chat(prov["id"], model, "1", max_tokens=8, timeout=20)
        return bool(res.get("ok")), False, isinstance(res, dict)
    except Exception as e:
        log.debug("probe %s error: %s", st.get("name"), e)
        return False, False, False


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


def _record_probe_result(name, st, ok, gone, wire=False):
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
        elif wire:
            # 探针拿到线上一手失败＝观测级证据：升级本行证据等级；已 down 的
            # 行此时才落熔断冷却——推断级告警只送检，探针证实即动手（处置闸
            # 的观测半环）。failing 行不动手（还没 down，继续按退避探测）。
            st["evidence"] = EVIDENCE_OBSERVED
            if (st.get("status") == "down"
                    and not float(st.get("circuit_open_until") or 0)):
                st["circuit_open_until"] = _now() + CLI_COOLDOWN_SECONDS
            _persist()
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
                ok, gone, wire = _probe_one(st)
                _record_probe_result(name, st, ok, gone, wire=wire)
        except Exception:
            log.exception("health probe loop error")
        time.sleep(15)  # 探针调度心跳


def force_probe(join_timeout: float = 25.0):
    """手动「重新体检」：立即并行重探全部异常通道，返回（目标数，完成数）。

    通道体检页的「重新体检」原先只重读台账（availability.payload 是只读
    聚合），真实探测只属于后台循环——60/120/300s 退避、熔断未过冷却的行
    根本不进探测名单，按钮点了状态永远不动（2026-10-22 用户实案）。这里
    把异常行的探测退避清零并立即并行重探一轮，结果走 _record_probe_result
    原链路（_LOCK + 身份校验，与后台探针并发无害）。join_timeout 盖住
    _probe_one 的 20s 探测上限；到点没探完的行 probe_next_at 已清零，
    后台循环下个心跳（15s）会接着探。

    同轮并发重探 CLI 账号隔离台账（bench 的「账号失效」行）：账号探针
    此前只有 5→30 分钟退避的自动路径，手动体检对它们毫无作用——用户
    登录/充值后最长干等 30 分钟（2026-10-22 实案）。单条账号探针上限
    35s，join 到点未完的行由 bench 后台循环按退避接力。
    """
    with _LOCK:
        targets = [(name, st) for name, st in _PROVIDERS.items()
                   if not st.get("static")   # 静态死链无端点可探，同探针循环
                   and st.get("status") in ("failing", "down")]
        for _name, st in targets:
            st["probe_next_at"] = 0
            st["probe_backoff_idx"] = 0
    account_ids = []
    try:
        from . import bench
        account_ids = bench.quarantined_ids()
    except Exception:
        account_ids = []
    if not targets and not account_ids:
        return 0, 0

    def _run(name, st):
        try:
            ok, gone, wire = _probe_one(st)
            _record_probe_result(name, st, ok, gone, wire=wire)
        except Exception:
            log.exception("[health] 手动探测 %s 失败", name)

    def _run_account(agent_id):
        try:
            from . import bench
            bench.probe_account(agent_id)
        except Exception:
            log.exception("[health] 手动账号探活 %s 失败", agent_id)

    threads = []
    for name, st in targets:
        t = threading.Thread(target=_run, args=(name, st),
                             name="health-probe-manual", daemon=True)
        t.start()
        threads.append(t)
    for agent_id in account_ids:
        t = threading.Thread(target=_run_account, args=(agent_id,),
                             name="bench-account-probe-manual", daemon=True)
        t.start()
        threads.append(t)
    deadline = time.time() + float(join_timeout)
    for t in threads:
        t.join(max(0.0, deadline - time.time()))
        if time.time() >= deadline:
            break
    done = sum(1 for t in threads if not t.is_alive())
    return len(targets) + len(account_ids), done
