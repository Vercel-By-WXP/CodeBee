# -*- coding: utf-8 -*-
"""per-key 四维额度计量 + 派单前节流（2026-11-01，借鉴 Freellmapi 的
RPM/RPD/TPM/TPD 四维计量与「请求前主动节流」）。

背景：429/额度上限的实案一箩筐（1311 权限 429 熔断整条目、Bigmodel 七天上限
不换将、番茄日上限静默拒发……），根因都是**事后熔断**——等平台打了 429 才知道
额度没了。而免费档的限速是**已知常量**：既然知道，就该在派单前查表节流，把
「这一单发给注定被 429 的通道」避免掉。健康面板从「验尸」升级为「体检」。

数据源：复用用量台账（usage.jsonl，每次真实调用已自动记账），零新增埋点。
窗口口径（与 Freellmapi 对齐）：
  RPM = 前 60 秒调用数        RPD = 当日调用数
  TPM = 前 60 秒 token 数     TPD = 当日 token 数

上限配置：data/quota-limits.json（GET/POST /api/quota 维护），形如
  {"providers": {"<pid>": {"rpm": 0, "rpd": 0, "tpm": 0, "tpd": 0}}}
0/缺省 = 该维不限。供应商条目里的 `quota_limits` 字段可作预设来源（优先级
低于显式配置文件）。

节流语义（check/filter_candidates）：
- 只有「已配置且已用尽」才拦；没配额信息的通道一律放行；
- token 维度支持 est_tokens 预估：used + est > limit 即拦（快跑大单前问一句）；
- **fail-open**：台账读不到 / 全部候选都被节流时，保持原候选顺序放行——
  节流层故障绝不能把业务锁死（与 workdir_lease 同一纪律）。

粒度声明：按 provider 记账 == 按**当前生效 KEY** 计量（modelhub 的 keys 轮换
后 api_key 镜像为当前 key，但用量台账不含 key 指纹，换 key 不重置计数——
如实记录，下期可把 key 指纹加进 usage 记录升级为真 per-key）。
"""
from __future__ import annotations

import json
import threading
import time

from . import paths

_LOCK = threading.RLock()
_SNAPSHOT_CACHE = {"at": 0.0, "data": None}
_SNAPSHOT_TTL = 15.0   # 路由调用频繁，台账扫描 15s 缓存足够新鲜

DIMS = ("rpm", "rpd", "tpm", "tpd")
DIM_LABELS = {"rpm": "每分钟调用", "rpd": "每日调用",
              "tpm": "每分钟 tokens", "tpd": "每日 tokens"}


def _limits_file():
    return paths.DATA_DIR / "quota-limits.json"


def load_limits():
    """读上限配置。坏文件/缺文件返回空表，绝不抛错。"""
    try:
        data = json.loads(_limits_file().read_text(encoding="utf-8"))
        provs = data.get("providers") if isinstance(data, dict) else None
        if not isinstance(provs, dict):
            return {}
        out = {}
        for pid, lim in provs.items():
            if not isinstance(lim, dict):
                continue
            out[str(pid)] = {d: _nonneg(lim.get(d)) for d in DIMS}
        return out
    except Exception:
        return {}


def _nonneg(v):
    try:
        v = float(v or 0)
    except (TypeError, ValueError):
        return 0.0
    return v if v > 0 else 0.0


def save_limits(provider_id, limits):
    """写上限配置（四维任意子集；0/None=不限）。返回落盘后的该供应商配置。"""
    pid = str(provider_id or "").strip()
    if not pid:
        raise ValueError("provider_id 不能为空")
    lim = {}
    for d in DIMS:
        v = _nonneg((limits or {}).get(d))
        if v:
            lim[d] = v
    with _LOCK:
        path = _limits_file()
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
            if not isinstance(data, dict):
                data = {}
        except Exception:
            data = {}
        provs = data.get("providers")
        if not isinstance(provs, dict):
            provs = {}
        if lim:
            provs[pid] = lim
        else:
            provs.pop(pid, None)
        data["providers"] = provs
        data["updated_at"] = time.strftime("%Y-%m-%d %H:%M:%S")
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(data, ensure_ascii=False, indent=2),
                        encoding="utf-8")
    return lim


def effective_limits(provider=None):
    """该供应商的生效上限：显式配置文件 > 供应商条目 `quota_limits` 字段。
    0 维（不限）剔除后返回。"""
    pid = str((provider or {}).get("id") or "").strip()
    if not pid:
        return {}
    explicit = {d: v for d, v in (load_limits().get(pid) or {}).items() if v}
    if explicit:
        return explicit
    preset = (provider or {}).get("quota_limits")
    if isinstance(preset, dict):
        out = {d: _nonneg(preset.get(d)) for d in DIMS}
        return {d: v for d, v in out.items() if v}
    return {}


# ---------------------------------------------------------------- 消耗快照

def _day_bound(ts_epoch, seconds):
    return time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(ts_epoch - seconds))


def _today():
    return time.strftime("%Y-%m-%d")


def snapshot(now=None):
    """从用量台账聚合各供应商四维消耗：{pid: {rpm, rpd, tpm, tpd}}。

    15s 进程内缓存（台账 append-only，TTL 内不重扫）。台账读不到=空表
    （调用方 fail-open 放行）。带 key_fp 的记录另入 per_key 分账
    （P3：{pid: {key_fp: 四维}}），view 透出供多 KEY 供应商分账。"""
    now = time.time() if now is None else float(now)
    with _LOCK:
        cached = _SNAPSHOT_CACHE
        if cached["data"] is not None and now - cached["at"] <= _SNAPSHOT_TTL:
            return cached["data"]
    out = {}
    per_key = {}
    try:
        from . import usage
        rpm_min = _day_bound(now, 60)
        today = _today()
        for r in usage._iter_records(2):
            pid = str(r.get("provider") or "").strip()
            if not pid:
                continue
            g = out.setdefault(pid, {"rpm": 0, "rpd": 0, "tpm": 0, "tpd": 0})
            kfp = str(r.get("key_fp") or "").strip()
            kg = (per_key.setdefault(pid, {})
                  .setdefault(kfp, {"rpm": 0, "rpd": 0, "tpm": 0, "tpd": 0})
                  ) if kfp else None
            ts = str(r.get("ts") or "")
            day = str(r.get("day") or "")
            toks = max(0, usage._parse_int(r.get("total")))
            if ts >= rpm_min:
                g["rpm"] += 1
                g["tpm"] += toks
                if kg:
                    kg["rpm"] += 1
                    kg["tpm"] += toks
            if day == today:
                g["rpd"] += 1
                g["tpd"] += toks
                if kg:
                    kg["rpd"] += 1
                    kg["tpd"] += toks
    except Exception:
        out = {}
        per_key = {}
    out = {"providers": out, "per_key": per_key}
    with _LOCK:
        _SNAPSHOT_CACHE["at"] = time.time()
        _SNAPSHOT_CACHE["data"] = out
    return out


def invalidate_cache():
    with _LOCK:
        _SNAPSHOT_CACHE["at"] = 0.0
        _SNAPSHOT_CACHE["data"] = None


# ---------------------------------------------------------------- 节流判定

def _flat_snapshot(snap):
    """兼容两种形态：新 {"providers": {...}, "per_key": {...}} 与旧扁平
    {pid: 四维}（测试 mock / 旧调用方）。返回 (flat, per_key)。"""
    if isinstance(snap, dict) and isinstance(snap.get("providers"), dict):
        return snap["providers"], snap.get("per_key") or {}
    return snap or {}, {}


def check(provider_id, est_tokens=0, provider=None, snap=None, now=None):
    """该通道还能不能接单。返回：
    {"provider": pid, "limits": {...生效上限}, "used": {...}, "throttled": bool,
     "reasons": ["rpd 12/10", ...]}

    只有「已配置且已用尽」才 throttled；没配上限=永远放行。
    token 维度带 est_tokens 时按 used+est 判定（预留这单的量）。"""
    pid = str(provider_id or "").strip()
    limits = effective_limits(provider or ({"id": pid}))
    used_all = snapshot(now) if snap is None else snap
    flat, _pk = _flat_snapshot(used_all)
    used = dict(flat.get(pid) or {"rpm": 0, "rpd": 0, "tpm": 0, "tpd": 0})
    reasons = []
    if limits.get("rpm") and used["rpm"] >= limits["rpm"]:
        reasons.append("rpm %d/%d" % (used["rpm"], limits["rpm"]))
    if limits.get("rpd") and used["rpd"] >= limits["rpd"]:
        reasons.append("rpd %d/%d" % (used["rpd"], limits["rpd"]))
    if limits.get("tpm") and used["tpm"] + max(0, int(est_tokens or 0)) > limits["tpm"]:
        reasons.append("tpm %d+%d/%d" % (used["tpm"], int(est_tokens or 0), limits["tpm"]))
    if limits.get("tpd") and used["tpd"] + max(0, int(est_tokens or 0)) > limits["tpd"]:
        reasons.append("tpd %d+%d/%d" % (used["tpd"], int(est_tokens or 0), limits["tpd"]))
    return {"provider": pid, "limits": limits, "used": used,
            "throttled": bool(reasons), "reasons": reasons}


def filter_candidates(candidates, snap=None, now=None):
    """把被节流的候选从列表里摘掉（保持原序）。

    返回 (kept, blocked)：kept 为放行候选；**全部被节流时返回原列表**——
    宁可撞 429 也不让业务无单可派（fail-open 纪律）。"""
    entries = list(candidates or [])
    if not entries:
        return entries, []
    snap = snapshot(now) if snap is None else snap
    kept, blocked = [], []
    for e in entries:
        pid = str((e or {}).get("provider_id") or "").strip()
        if pid and check(pid, snap=snap, now=now)["throttled"]:
            blocked.append(pid)
        else:
            kept.append(e)
    if not kept:
        # 全军覆没：原样放行（fail-open），但 blocked 照常返回供诊断/审计
        return entries, blocked
    return kept, blocked


def view(providers=None):
    """健康面板视图：各供应商上限/消耗/节流态；带 key_fp 的记录附 per-key
    分账（P3：多 KEY 供应商不再只有「当前生效 key」一本账）。"""
    provs = {str(p.get("id") or ""): p for p in (providers or []) if isinstance(p, dict)}
    snap = snapshot()
    flat, per_key = _flat_snapshot(snap)
    pids = (set(flat) | set(per_key) | set(load_limits())
            | {k for k in provs if effective_limits(provs[k])})
    rows = []
    for pid in sorted(pids):
        c = check(pid, provider=provs.get(pid), snap=snap)
        c["name"] = str((provs.get(pid) or {}).get("name") or pid)
        keys = per_key.get(pid) or {}
        if keys:
            c["per_key"] = [
                {"key_fp": k or "(无指纹)", **dims} for k, dims in sorted(keys.items())
            ]
        rows.append(c)
    return {"generated_at": time.strftime("%Y-%m-%d %H:%M:%S"),
            "dims": DIM_LABELS, "providers": rows}
