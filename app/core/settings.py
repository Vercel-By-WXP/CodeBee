# -*- coding: utf-8 -*-
"""运行设置（data/settings.json）：并发 worker 数 + 默认保存路径。"""
from __future__ import annotations

import json
import threading
from pathlib import Path

from . import paths

_LOCK = threading.RLock()
_FILE = paths.DATA_DIR / "settings.json"

# default_workdir 为空表示未自定义，用 builtin_workdir() 回落；
# telemetry_errors：匿名错误回传开关（默认开；关掉后版本 ping/错误上传/诊断包遥测部分全部停发，
# 「导出诊断包」是用户手动操作不受此开关限制）
# publish_daily_cap / publish_fail_streak：自动发布护栏——每任务每平台每日
# 成功发章上限、平台连续失败几次后暂停自动发布（publish/auto.py 读取）
DEFAULTS = {"max_concurrent_jobs": 6, "default_workdir": "", "hooks_token": "",
            "telemetry_errors": True, "publish_daily_cap": 10,
            "publish_fail_streak": 3}
# 并发上限 12：worker 只是拉起 CLI 子进程的调度位，跨任务无共享资源；
# 同任务单飞守卫在 jobs 层。默认 6 对齐「多任务并行不排队」的使用预期。
MIN_WORKERS, MAX_WORKERS = 1, 12


def builtin_workdir():
    """内置默认保存路径：<data 目录同级>/workspace。"""
    return str(paths.DATA_DIR.parent / "workspace")


def default_workdir():
    """当前生效的默认保存路径（自定义优先，回落内置值）。"""
    raw = str(load().get("default_workdir") or "").strip()
    return raw if raw else builtin_workdir()


def _norm_workdir(v):
    """展开 ~ 并转绝对路径；空值合法（回落内置）。返回 (标准化串, 错误)。"""
    v = str(v or "").strip()
    if not v:
        return "", None
    if v.startswith("~"):
        v = str(Path(v).expanduser())
    p = Path(v)
    if not p.is_absolute():
        return "", "默认保存路径必须是绝对路径"
    return str(p), None


def load():
    with _LOCK:
        try:
            data = json.loads(_FILE.read_text(encoding="utf-8"))
        except Exception:
            data = {}
    out = dict(DEFAULTS)
    if isinstance(data, dict):
        out.update({k: v for k, v in data.items() if k in DEFAULTS})
    try:
        out["max_concurrent_jobs"] = max(MIN_WORKERS, min(MAX_WORKERS, int(out["max_concurrent_jobs"])))
    except Exception:
        out["max_concurrent_jobs"] = DEFAULTS["max_concurrent_jobs"]
    wd, _ = _norm_workdir(out.get("default_workdir"))
    out["default_workdir"] = wd
    out["default_workdir_effective"] = wd if wd else builtin_workdir()
    return out


def save(patch):
    """合并保存。返回 (view, 错误)。"""
    if not isinstance(patch, dict):
        return load(), "payload 必须是对象"
    with _LOCK:
        cur = load()
        if "max_concurrent_jobs" in patch:
            try:
                cur["max_concurrent_jobs"] = int(patch["max_concurrent_jobs"])
            except Exception:
                return cur, "max_concurrent_jobs 必须是整数"
            if not MIN_WORKERS <= cur["max_concurrent_jobs"] <= MAX_WORKERS:
                return cur, "max_concurrent_jobs 取值 %d-%d" % (MIN_WORKERS, MAX_WORKERS)
        if "default_workdir" in patch:
            wd, err = _norm_workdir(patch.get("default_workdir"))
            if err:
                return cur, err
            cur["default_workdir"] = wd
        if "hooks_token" in patch:
            cur["hooks_token"] = str(patch.get("hooks_token") or "").strip()[:128]
        if "telemetry_errors" in patch:
            cur["telemetry_errors"] = bool(patch.get("telemetry_errors"))
        if "publish_daily_cap" in patch:
            try:
                cur["publish_daily_cap"] = max(1, min(50, int(patch.get("publish_daily_cap"))))
            except (TypeError, ValueError):
                return cur, "publish_daily_cap 必须是 1-50 的整数"
        if "publish_fail_streak" in patch:
            try:
                cur["publish_fail_streak"] = max(1, min(10, int(patch.get("publish_fail_streak"))))
            except (TypeError, ValueError):
                return cur, "publish_fail_streak 必须是 1-10 的整数"
        _FILE.parent.mkdir(parents=True, exist_ok=True)
        tmp = _FILE.with_suffix(".tmp")
        tmp.write_text(json.dumps(cur, ensure_ascii=False, indent=2), encoding="utf-8")
        tmp.replace(_FILE)
    cur["default_workdir_effective"] = cur["default_workdir"] if cur["default_workdir"] else builtin_workdir()
    return cur, None
