# -*- coding: utf-8 -*-
"""事务式无损搬家：预检 → 复制 → 校验 → 原路径改名备份 → 建目录联接 → 释放。

七步里任何一步失败都调用同一个回滚函数，把现场恢复成「什么都没发生过」：
摘掉刚建的联接、把 .codebee-bak 改回原名、把目标副本归档（不硬删）。
备份走系统回收站而不是 rmtree，既释放了空间又留了找回的余地。

``confirm=False`` 一律只返回预检结果；``rollback`` / ``restore`` 同样要显式
确认。所有会写文件系统的分支都先过 risk 红线，不信任调用方传来的路径。
"""
from __future__ import annotations

import os
import shutil
import time
from pathlib import Path

from . import ledger, links, risk, rules, scanner, winapi

BACKUP_SUFFIX = ".codebee-bak"


_CACHE = {"ts": 0.0, "rows": None}
_CACHE_TTL = 60.0


def candidates(env=None, refresh=False) -> list[dict]:
    """可搬家的目录（含已经搬过、当前是链接的），补体积与链接状态。"""
    now = time.monotonic()
    if (not refresh and env is None and _CACHE["rows"] is not None
            and now - _CACHE["ts"] < _CACHE_TTL):
        return [dict(x) for x in _CACHE["rows"]]
    out = []
    for row in rules.resolved_targets(kind="migrate", env=env):
        path = row.get("path")
        if not path or risk.is_forbidden(path):
            continue
        linked = links.is_link(path)
        if linked:
            size, files = 0, 0
        else:
            if not os.path.isdir(path):
                continue
            try:
                size, files = scanner.calc_size(path)
            except OSError:
                continue
        item = dict(row)
        item.update({"size": int(size), "files": int(files),
                     "size_human": scanner.human(size),
                     "is_linked": bool(linked),
                     "link_target": links.link_target(path) if linked else None})
        out.append(item)
    out.sort(key=lambda x: -x["size"])
    if env is None:
        _CACHE["rows"] = [dict(x) for x in out]
        _CACHE["ts"] = time.monotonic()
    return out


def _free_bytes(path):
    p = Path(path)
    while not p.exists() and p != p.parent:
        p = p.parent
    try:
        return int(shutil.disk_usage(str(p)).free)
    except OSError:
        return 0


def _inside(child, parent):
    try:
        return os.path.normcase(os.path.commonpath(
            [os.path.abspath(child), os.path.abspath(parent)])) == \
            os.path.normcase(os.path.abspath(parent))
    except (ValueError, OSError):
        return False


def precheck(src, dst) -> dict:
    """只读预检。任何一项不过，run 都不会进入写阶段。"""
    src = os.path.normpath(os.fspath(src))
    dst = os.path.normpath(os.fspath(dst))
    checks = []

    def add(name, ok, detail=""):
        checks.append({"name": name, "ok": bool(ok), "detail": str(detail)})

    exists = os.path.isdir(src)
    add("源目录存在", exists, src)
    forbidden = risk.is_forbidden(src)
    add("未命中安全红线", not forbidden)
    reparse = links.is_link(src)
    add("源目录不是链接", not reparse, "已是链接的目录不能重复搬家")
    add("目标不在源目录内部", not _inside(dst, src), dst)
    dst_ok = (not os.path.exists(dst)) or (os.path.isdir(dst) and not os.listdir(dst))
    add("目标位置为空或不存在", dst_ok, dst)

    size, files = (0, 0)
    if exists and not reparse:
        try:
            size, files = scanner.calc_size(src)
        except OSError:
            size, files = 0, 0
    free = _free_bytes(dst)
    add("目标盘空间充足", free >= size * 1.1,
        "需要 %s，可用 %s" % (scanner.human(int(size * 1.1)), scanner.human(free)))

    lock = winapi.probe_lock(src) if exists else {"locked": False, "reason": ""}
    add("源目录未被占用", not lock.get("locked"), lock.get("reason", ""))

    rule = rules.match_rule(src)
    running = winapi.blocking_processes((rule or {}).get("processes") or [])
    if running:
        add("相关进程", True, "建议先退出：%s" % ", ".join(
            "%s(%s)" % (x["name"], x["pid"]) for x in running[:6]))

    blockers = [c["name"] for c in checks if not c["ok"]]
    return {"ok": not blockers, "checks": checks, "blockers": blockers,
            "locked": bool(lock.get("locked")),
            "source_bytes": int(size), "source_files": int(files),
            "target_free": free, "elevated": winapi.is_elevated(),
            "src": src, "dst": dst}


def _archive(path, batch, base, tag):
    root = ledger.store_dir(base) / "trash" / batch
    root.mkdir(parents=True, exist_ok=True)
    dst = root / ("%s-%s" % (tag, os.path.basename(str(path)) or "item"))
    try:
        shutil.move(str(path), str(dst))
        return {"ok": True, "to": str(dst), "action": "archive"}
    except OSError as exc:
        return {"ok": False, "error": getattr(exc, "strerror", None)
                or type(exc).__name__}


def _dispose(path, batch, base, tag):
    """优先回收站，失败归档；绝不 rmtree。"""
    if winapi.recycle(path):
        return {"action": "recycle", "ok": True}
    done = _archive(path, batch, base, tag)
    done["action"] = "archive" if done.get("ok") else "failed"
    return done


def _rollback_now(src, dst, backup, batch, base):
    """把现场恢复成动之前的样子；每一步都尽力而为，不抛异常。"""
    steps = []
    if os.path.lexists(src) and links.is_link(src):
        steps.append(("摘掉刚建的联接", links.remove_link(src, confirm=True)))
    if os.path.isdir(backup) and not os.path.exists(src):
        try:
            os.rename(backup, src)
            steps.append(("原目录改回原名", {"ok": True}))
        except OSError as exc:
            steps.append(("原目录改回原名", {"ok": False, "error": str(exc)}))
    if os.path.isdir(dst):
        steps.append(("目标副本归档", _archive(dst, batch, base, "rollback")))
    return steps


def run(src, dst, confirm=False, on_progress=None, base=None) -> dict:
    """执行搬家。confirm=False 只回预检；失败自动回滚并返回 rolled_back=True。"""
    src = os.path.normpath(os.fspath(src))
    dst = os.path.normpath(os.fspath(dst))
    pre = precheck(src, dst)
    if confirm is not True:
        return {"dry_run": True, "precheck": pre}
    # 预检里的 is_forbidden 只是「看一眼」，真正放行必须由红线断言把关：
    # 直接调本函数（不经 API）时也不能绕过，目标盘同样不许落在红线目录里。
    risk.assert_action_allowed(src, "migrate")
    risk.assert_action_allowed(dst, "migrate")
    if not pre["ok"]:
        return {"ok": False, "error": "预检未通过：%s" % "、".join(pre["blockers"]),
                "steps": [], "precheck": pre}

    batch = time.strftime("%Y%m%d-%H%M%S")
    backup = src + BACKUP_SUFFIX
    steps = []

    def progress(phase, detail=""):
        steps.append({"phase": phase, "detail": detail})
        if on_progress:
            try:
                on_progress({"phase": phase, "current": detail or src,
                             "done": len(steps), "total": 7,
                             "freed": 0})
            except Exception:
                pass

    if os.path.exists(backup):
        return {"ok": False, "error": "同名的历史备份还在，请先处理它", "steps": []}
    try:
        progress("预检", "通过")
        if os.path.isdir(dst) and not os.listdir(dst):
            os.rmdir(dst)
        shutil.copytree(src, dst)
        progress("复制", "已复制到目标盘")
        got_bytes, got_files = scanner.calc_size(dst)
        if (got_bytes, got_files) != (pre["source_bytes"], pre["source_files"]):
            raise OSError("复制后校验不一致：%s/%s vs %s/%s" % (
                got_bytes, got_files, pre["source_bytes"], pre["source_files"]))
        progress("校验", "文件数与字节数一致")
        os.rename(src, backup)
        progress("改名备份", backup)
        linked = links.create_junction(src, dst, confirm=True)
        if not linked.get("ok"):
            raise OSError(str(linked.get("error") or "建目录联接失败"))
        progress("建目录联接", src)
        disposed = _dispose(backup, batch, base, "backup")
        progress("备份处置", disposed.get("action", ""))
        ledger.add_migration({
            "source": src, "target": dst, "kind": "junction",
            "bytes": pre["source_bytes"], "files": pre["source_files"],
            "backup_action": disposed.get("action"),
            "created_at": time.strftime("%Y-%m-%d %H:%M:%S")}, base)
        ledger.append_audit({"action": "migrate", "path": src,
                             "target": dst, "bytes": pre["source_bytes"],
                             "batch": batch}, base)
        progress("记账本", "完成")
        return {"ok": True, "steps": steps, "source": src, "target": dst,
                "bytes": pre["source_bytes"], "backup_action": disposed.get("action")}
    except (OSError, ValueError, shutil.Error, risk.UnsafePath) as exc:
        rolled = _rollback_now(src, dst, backup, batch, base)
        return {"ok": False, "rolled_back": True,
                "error": "搬家失败，已回滚：%s" % (
                    getattr(exc, "strerror", None) or str(exc)),
                "steps": steps, "rollback": rolled}


def rollback(src, dst, confirm=False, base=None) -> dict:
    """人工回滚：恢复 .codebee-bak、摘联接、归档目标副本。"""
    src = os.path.normpath(os.fspath(src))
    dst = os.path.normpath(os.fspath(dst))
    if confirm is not True:
        return {"dry_run": True, "src": src, "dst": dst}
    # 这里 src 可能已经是刚建的 Junction，是预期状态。用 "link" 动作过红线：
    # 只挡 forbidden，不像 "migrate" 那样连重解析点一起拒（那样回滚永远调不通）。
    risk.assert_action_allowed(src, "link")
    batch = time.strftime("%Y%m%d-%H%M%S")
    steps = _rollback_now(src, dst, src + BACKUP_SUFFIX, batch, base)
    ledger.update_migration(src, {"rolled_back_at": batch}, base)
    return {"ok": True, "steps": steps, "src": src, "dst": dst}


def restore(src, confirm=False, base=None) -> dict:
    """一键迁回：先把数据完整复制回来，再摘联接，最后把目标副本送回收站。"""
    src = os.path.normpath(os.fspath(src))
    entry = next((x for x in ledger.list_migrations(base)
                  if os.path.normcase(str(x.get("source") or "")) ==
                  os.path.normcase(src)), None)
    if not entry:
        return {"ok": False, "error": "账本里没有这个源目录的迁移记录"}
    target = str(entry.get("target") or "")
    if confirm is not True:
        return {"dry_run": True, "src": src, "target": target}
    # 迁回的前提恰恰是 src 为 Junction；"migrate" 会拒绝重解析点，必须用
    # "link" 过红线，再由下面的 is_link 断言形状，两者缺一不可。
    risk.assert_action_allowed(src, "link")
    if not links.is_link(src):
        return {"ok": False, "error": "源路径已经不是链接，不能按迁回处理"}
    if not os.path.isdir(target):
        return {"ok": False, "error": "目标目录不存在，账本可能已失效"}

    tmp = src + ".codebee-restore"
    if os.path.exists(tmp):
        return {"ok": False, "error": "存在同名的迁回临时目录，请先处理"}
    try:
        shutil.copytree(target, tmp)
        a = scanner.calc_size(tmp)
        b = scanner.calc_size(target)
        if a != b:
            raise OSError("迁回复制校验不一致")
        removed = links.remove_link(src, confirm=True)
        if not removed.get("ok"):
            raise OSError(str(removed.get("error") or "摘链接失败"))
        os.rename(tmp, src)
        batch = time.strftime("%Y%m%d-%H%M%S")
        disposed = _dispose(target, batch, base, "restored-target")
        ledger.drop_migration(src, base)
        ledger.append_audit({"action": "restore", "path": src,
                             "target": target, "batch": batch}, base)
        return {"ok": True, "source": src, "target": target,
                "backup_action": disposed.get("action")}
    except (OSError, ValueError, shutil.Error) as exc:
        if os.path.isdir(tmp) and not os.path.exists(src):
            try:
                os.rename(tmp, src)
            except OSError:
                pass
        return {"ok": False, "rolled_back": True,
                "error": "迁回失败，原链接保持不变：%s" % (
                    getattr(exc, "strerror", None) or str(exc))}
