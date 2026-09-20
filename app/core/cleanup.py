# -*- coding: utf-8 -*-
"""每日垃圾清理：只清 CodeBee 自己产生的可再生数据，绝不碰用户成果。

垃圾来源（实测 data/ 923MB 构成）：
  - runs/<id>/steps/*.log   运行过程日志，单文件可达 22MB（保留 run.json
                            与 report.md，运行记录/结果页签不受影响）
  - publish/shots/          发布过程截图证据
  - data 根 *.bak*          配置改写工具留下的备份残留
  - pending/                运行中指挥信箱的过期附件
  - publish/profiles/       浏览器 profile 迁移残留（新版已搬家目录，
                            旧位置 688MB 实测；仅当家目录同款平台目录
                            存在——即迁移已完成——才按废弃清理）
  - exports/ imports/       备份模块自己的历史产物
  - data 根 *.log           服务日志按体量截尾，不是按天删

调度挂在 automation._tick（fire_due 自节流：启用 + 当天没清过才真跑）。
配置在 data/settings.json（cleanup_enabled / cleanup_retention_days），
状态在 data/cleanup.json（last_run/last_freed/history 供设置页展示）。

发布浏览器 profile（家目录 ~/.codebee/publish_profiles，约 1GB）含平台
登录态，清了要重新扫码——绝不进每日自动清理，只留手动入口
（run_cleanup(include_profiles=True)）。
"""
from __future__ import annotations

import json
import os
import time
from pathlib import Path

from . import paths, settings as settings_mod

DEFAULT_RETENTION_DAYS = 14
LOG_MAX_BYTES = 5 * 1024 * 1024      # 服务日志超过这个体量才截
LOG_KEEP_BYTES = 1 * 1024 * 1024     # 截尾保留的末段
STATE_NAME = "cleanup.json"


# ---------------------------------------------------------------- 状态

def _state_path():
    return Path(paths.DATA_DIR) / STATE_NAME


def _load_state():
    try:
        d = json.loads(_state_path().read_text(encoding="utf-8"))
        return d if isinstance(d, dict) else {}
    except Exception:
        return {}


def _save_state(st):
    p = _state_path()
    p.parent.mkdir(parents=True, exist_ok=True)
    tmp = p.with_suffix(".tmp")
    tmp.write_text(json.dumps(st, ensure_ascii=False, indent=2), encoding="utf-8")
    os.replace(str(tmp), str(p))


def _profiles_base(home=None):
    """家目录浏览器 profile 根（与 publish/manager 同款落点）。home 供测试注入。"""
    if home:
        return Path(home) / ".codebee" / "publish_profiles"
    return Path.home() / ".codebee" / "publish_profiles"


def _parse_local_dt(s):
    try:
        return time.mktime(time.strptime(str(s)[:19], "%Y-%m-%d %H:%M:%S"))
    except Exception:
        return None


# ---------------------------------------------------------------- 扫描

def _run_ended(run_dir, cutoff_ts):
    """run 目录整体是否过期：看 run.json 的 ended_at；没有就退回目录 mtime。"""
    rj = run_dir / "run.json"
    if rj.is_file():
        ts = None
        try:
            ts = _parse_local_dt(json.loads(rj.read_text(encoding="utf-8"))
                                 .get("ended_at"))
        except Exception:
            ts = None
        if ts:
            return ts < cutoff_ts
    try:
        return run_dir.stat().st_mtime < cutoff_ts
    except OSError:
        return False


def _plan_items(retention_days=None, include_profiles=False, home=None, now=None):
    """扫描各类垃圾，返回条目列表。每条：key/label/note/bytes/count/files，
    files 元素为 {path, op}，op ∈ delete | rmtree | truncate。"""
    now = now or time.time()
    try:
        days = int(retention_days if retention_days is not None
                   else settings_mod.load().get("cleanup_retention_days"))
    except (TypeError, ValueError):
        days = DEFAULT_RETENTION_DAYS
    days = max(1, min(365, days))
    cutoff = now - days * 86400
    data_dir = Path(paths.DATA_DIR)
    items = []

    def add(key, label, note, files):
        size = 0
        good = []
        for f in files:
            try:
                p = Path(f["path"])
                if f["op"] == "truncate":
                    sz = p.stat().st_size - LOG_KEEP_BYTES
                elif p.is_dir():
                    sz = sum(x.stat().st_size for x in p.rglob("*") if x.is_file())
                else:
                    sz = p.stat().st_size
            except OSError:
                continue
            # truncate 只在有得砍时才留；delete/rmtree 即使 0 字节（空目录）也要清
            if sz > 0 or f["op"] != "truncate":
                good.append(f)
            size += max(0, sz)
        if good:
            items.append({"key": key, "label": label, "note": note,
                          "bytes": size, "count": len(good), "files": good})

    # 1) 过期运行的过程日志（保留 run.json / report.md / 封面）
    runs_dir = data_dir / "runs"
    files = []
    if runs_dir.is_dir():
        for rd in runs_dir.iterdir():
            if not rd.is_dir() or not _run_ended(rd, cutoff):
                continue
            steps = rd / "steps"
            if steps.is_dir():
                files.append({"path": str(steps), "op": "rmtree"})
    add("runs_old_logs", "运行过程日志",
        "保留运行记录与结果报告，只删过程中的原始日志", files)

    # 2) 发布截图
    shots = data_dir / "publish" / "shots"
    add("publish_shots", "发布截图", "发布过程留档截图，超期即清",
        [{"path": str(p), "op": "delete"}
         for p in (shots.iterdir() if shots.is_dir() else [])
         if p.is_file() and p.stat().st_mtime < cutoff])

    # 3) 配置备份残留 *.bak*
    add("bak_files", "配置备份残留", "改配置时自动留的 .bak 副本，超期即清",
        [{"path": str(p), "op": "delete"}
         for p in data_dir.glob("*.bak*")
         if p.is_file() and p.stat().st_mtime < cutoff])

    # 4) 过期指挥信箱（运行早已结束，附件永远不会被消费）
    pend = data_dir / "pending"
    files = []
    for p in (pend.iterdir() if pend.is_dir() else []):
        try:
            if p.stat().st_mtime >= cutoff:
                continue
        except OSError:
            continue
        files.append({"path": str(p), "op": "rmtree" if p.is_dir() else "delete"})
    add("pending_stale", "过期指挥信箱", "已结束任务的运行中留言附件",
        files)

    # 5) 浏览器 profile 迁移残留：家目录同款平台目录存在（迁移已完成）才清
    legacy = data_dir / "publish" / "profiles"
    files = []
    if legacy.is_dir():
        for p in legacy.iterdir():
            if not p.is_dir():
                continue
            try:
                if (_profiles_base(home) / p.name).exists():
                    files.append({"path": str(p), "op": "rmtree"})
            except OSError:
                continue
    add("legacy_profiles", "旧版浏览器缓存残留",
        "发布浏览器 profile 已迁往用户主目录，旧位置属废弃残留", files)

    # 6) 历史备份包（导出 zip / 导入前自动备份，超期即清）
    files = []
    for d in ("exports", "imports"):
        base = data_dir / d
        for p in (base.iterdir() if base.is_dir() else []):
            try:
                if p.stat().st_mtime < cutoff:
                    files.append({"path": str(p),
                                  "op": "rmtree" if p.is_dir() else "delete"})
            except OSError:
                continue
    add("backups_old", "历史备份包", "导出与导入前自动备份的旧 zip", files)

    # 7) 服务日志截尾（不删文件，砍掉前段）
    files = [{"path": str(p), "op": "truncate"}
             for p in data_dir.glob("*.log")
             if p.is_file() and p.stat().st_size > LOG_MAX_BYTES]
    add("logs_truncate", "服务日志瘦身", "只保留每个日志的末尾 1MB", files)

    # 8) 手动：发布浏览器缓存（含登录态，清了要重新扫码；绝不自动清）
    if include_profiles:
        base = _profiles_base(home)
        files = [{"path": str(p), "op": "rmtree"}
                 for p in (base.iterdir() if base.is_dir() else []) if p.is_dir()]
        add("publish_profiles", "发布浏览器缓存",
            "含平台登录态，清理后发布时需重新扫码登录", files)
    return {"retention_days": days, "items": items,
            "total_bytes": sum(i["bytes"] for i in items)}


def plan(retention_days=None, include_profiles=False, home=None):
    return _plan_items(retention_days=retention_days,
                       include_profiles=include_profiles, home=home)


def status():
    """设置页一次拉全：配置 + 上次清理状态 + 当前可清理预估。"""
    cfg = settings_mod.load()
    return {"config": {"enabled": bool(cfg.get("cleanup_enabled")),
                       "retention_days": cfg.get("cleanup_retention_days")},
            "state": _load_state(), "plan": plan()}


# ---------------------------------------------------------------- 执行

def _delete_path(p: Path, op):
    if op == "rmtree":
        import shutil
        shutil.rmtree(str(p), ignore_errors=False)
    elif op == "truncate":
        with open(str(p), "rb") as f:
            f.seek(-LOG_KEEP_BYTES, 2)
            tail = f.read()
        tmp = p.with_suffix(".tmp-cl")
        tmp.write_bytes(tail)
        os.replace(str(tmp), str(p))
    else:
        p.unlink()


def run_cleanup(retention_days=None, include_profiles=False):
    """按当前扫描结果执行清理。返回汇总并落状态（供设置页展示）。"""
    scan = _plan_items(retention_days=retention_days,
                       include_profiles=include_profiles)
    freed = 0
    done = []
    errors = []
    for item in scan["items"]:
        got = 0
        n = 0
        for f in item["files"]:
            try:
                p = Path(f["path"])
                if f["op"] == "truncate":
                    sz = p.stat().st_size - LOG_KEEP_BYTES
                elif p.is_dir():
                    sz = sum(x.stat().st_size for x in p.rglob("*") if x.is_file())
                else:
                    sz = p.stat().st_size
                _delete_path(p, f["op"])
                got += max(0, sz)
                n += 1
            except OSError as e:
                errors.append("%s：%s" % (f["path"], e.strerror or e))
        if n:
            done.append({"key": item["key"], "label": item["label"],
                         "bytes": got, "count": n})
            freed += got
    result = {"ts": time.strftime("%Y-%m-%d %H:%M:%S"), "freed": freed,
              "items": done, "errors": errors[:10],
              "include_profiles": bool(include_profiles)}
    st = _load_state()
    st["last_run"] = result["ts"]
    st["last_freed"] = freed
    st["last_result"] = result
    hist = st.get("history") or []
    hist.insert(0, {"ts": result["ts"], "freed": freed})
    st["history"] = hist[:30]
    _save_state(st)
    try:
        from . import store
        store.bump_state()
    except Exception:
        pass
    return result


def fire_due():
    """automation._tick 每拍调用：启用且当天没清过才真跑，否则零开销返回。"""
    try:
        cfg = settings_mod.load()
    except Exception:
        return None
    if not cfg.get("cleanup_enabled"):
        return None
    today = time.strftime("%Y-%m-%d")
    if str(_load_state().get("last_run") or "").startswith(today):
        return None
    return run_cleanup()
