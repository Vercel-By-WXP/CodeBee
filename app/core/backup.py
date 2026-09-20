# -*- coding: utf-8 -*-
"""整机数据备份：导出 / 导入（换电脑、换路径安装时的数据搬家）。

备份包是一个 zip，布局：
    manifest.json                     清单（app/schema/旧路径/计数/未打包目录）
    data/…                            整个数据目录（见下方排除清单）
    workspace/<相对默认保存路径的路径>  任务工作目录（手稿/成品文件——真正的劳动成果）

排除不打包（易失或机器相关，导入后也无意义）：
  - 发布浏览器 profile（data/publish/profiles 迁移残留 + 家目录 ~/.codebee/
    publish_profiles 本来就在数据目录外）——GB 级且登录态 cookie 与机器绑定；
  - 日志（data 根 *.log）、pet.lock、chat_cache、pending（运行中信箱）、
    exports/imports（备份模块自己的输出/暂存区）。
运行过程日志（runs/*/steps/*.log）默认打包，include_logs=False 可去掉减小体积。

导入路径重映射：任务记录/设置里存的是旧机器的绝对路径。manifest 记下旧
data 目录与旧默认保存路径，导入时把所有 JSON 值里以旧路径开头的字符串改写
为新机器对应路径（长前缀优先，避免父子前缀互吞），工作目录文件按相对路径
落到新默认保存路径下。导入前自动把当前小体积状态（根 *.json + tasks/*.json）
备份到 <data>/imports/pre-import-<ts>.zip 供反悔。

导入要求没有运行中/排队中的任务（记录与文件正被使用）；完成后必须重启
服务（各模块的内存态与 import 期绑定只认启动时的数据目录）。
"""
from __future__ import annotations

import json
import os
import re
import shutil
import time
import zipfile
from pathlib import Path

from . import paths, settings as settings_mod

_SEG_SPLIT = re.compile(r"[\\/]")
_DRIVE_RE = re.compile(r"^[A-Za-z]:")

MANIFEST_NAME = "manifest.json"
SCHEMA = 1
APP_ID = "codebee"

# 数据目录内不打包的一级目录 / 文件名（易失、缓存或备份模块自留地）
_EXCLUDE_TOP_DIRS = {"exports", "imports", "chat_cache", "pending"}
_EXCLUDE_TOP_FILES = {"pet.lock"}
# publish/profiles 是 GB 级浏览器 profile 迁移残留（登录态与机器绑定），不打包
_LOG_SUFFIXES = (".log", ".tmp")


def _app_version():
    try:
        pkg = paths.ROOT / "package.json"
        return str(json.loads(pkg.read_text(encoding="utf-8")).get("version") or "")
    except Exception:
        return ""


def _iter_files(base: Path):
    """yield (绝对路径, 相对 Path)。符号坏链等交给调用方逐文件兜底。"""
    for cur, dirs, files in os.walk(base):
        cur_p = Path(cur)
        for f in files:
            p = cur_p / f
            try:
                yield p, p.relative_to(base)
            except ValueError:
                continue


def _export_skip(rel: Path, include_logs=True):
    """导出时是否跳过该相对路径（rel 相对 DATA_DIR）。"""
    parts = rel.parts
    if not parts:
        return True
    if parts[0] in _EXCLUDE_TOP_DIRS:
        return True
    if parts[0] == "publish" and len(parts) > 1 and parts[1] == "profiles":
        return True
    if parts[0] in _EXCLUDE_TOP_FILES:
        return True
    if rel.suffix.lower() in _LOG_SUFFIXES:
        # 运行过程日志按开关保留；数据根的服务日志一律不打包（易失）
        if not (include_logs and parts[0] == "runs"):
            return True
    return False


def _collect_workspace_dirs():
    """收集该打包的任务工作目录。

    只打包「默认保存路径」下的目录（当初由默认路径自动放置的 app 管辖区，
    与 store.migrate_task_workdirs 同一口径）；落在数据目录里的（如 demo）
    随 data/ 打包；两者之外的是用户自己的目录（代码仓库等），不打包、只在
    manifest.external_workdirs 里点名提醒。
    返回 (根目录, [相对 Path…], [外部目录…])。
    """
    root = Path(settings_mod.default_workdir())
    from . import store
    wds = set()
    try:
        for t in store.list_tasks(limit=10 ** 6):
            wd = str(t.get("workdir") or "").strip()
            if wd:
                wds.add(Path(wd))
    except Exception:
        pass
    try:
        from . import automation
        for at in automation.list_tasks():
            wd = str(at.get("workdir") or "").strip()
            if wd:
                wds.add(Path(wd))
    except Exception:
        pass
    inside, external = [], []
    seen = set()
    for wd in sorted(wds, key=lambda p: str(p).lower()):
        s = str(wd)
        if s.lower() in seen:
            continue
        seen.add(s.lower())
        try:
            rel = wd.resolve().relative_to(root.resolve())
        except (ValueError, OSError):
            try:
                wd.resolve().relative_to(Path(paths.DATA_DIR).resolve())
                continue  # 数据目录内的随 data/ 打包，不重复收
            except (ValueError, OSError):
                external.append(s)
                continue
        if wd.is_dir():
            inside.append(rel)
    return root, inside, external


# ---------------------------------------------------------------- 导出

def export_data(target_dir="", include_logs=True, include_workspace=True):
    """打包当前数据目录 + 任务工作目录到 zip。返回信息 dict；参数非法抛 ValueError。"""
    data_dir = Path(paths.DATA_DIR)
    if not data_dir.is_dir():
        raise ValueError("数据目录不存在: %s" % data_dir)
    target = Path(target_dir).expanduser() if str(target_dir or "").strip() \
        else (Path.home() / "Downloads")
    if not target.is_absolute():
        raise ValueError("导出位置必须是绝对路径")
    try:
        target.mkdir(parents=True, exist_ok=True)
    except OSError as e:
        raise ValueError("导出位置不可创建: %s（%s）" % (target, e))

    root, ws_rels, external = _collect_workspace_dirs()
    manifest = {
        "app": APP_ID, "schema": SCHEMA,
        "exported_at": time.strftime("%Y-%m-%d %H:%M:%S"),
        "version": _app_version(),
        "data_dir": str(data_dir),
        "default_workdir": str(root),
        "include_logs": bool(include_logs),
        "include_workspace": bool(include_workspace),
        "external_workdirs": external,
        "workspace_dirs": [str(r) for r in ws_rels],
    }
    out = target / ("codebee-backup-" + time.strftime("%Y%m%d-%H%M%S") + ".zip")
    counts = {"data_files": 0, "workspace_files": 0, "tasks": 0, "runs": 0}
    warnings = []
    try:
        counts["tasks"] = sum(1 for _ in (data_dir / "tasks").glob("t-*.json"))
        rdir = data_dir / "runs"
        counts["runs"] = sum(1 for _ in rdir.iterdir()) if rdir.is_dir() else 0
    except OSError:
        pass
    manifest["counts"] = counts

    zf = None
    try:
        zf = zipfile.ZipFile(str(out), "w", zipfile.ZIP_DEFLATED)
        for p, rel in _iter_files(data_dir):
            if _export_skip(rel, include_logs=include_logs) or p == out:
                continue
            try:
                zf.write(str(p), str(Path("data") / rel))
                counts["data_files"] += 1
            except OSError as e:
                warnings.append("跳过 %s（%s）" % (rel, e.strerror or e))
        if include_workspace:
            for rel in ws_rels:
                base = root / rel
                for p, frel in _iter_files(base):
                    try:
                        zf.write(str(p), str(Path("workspace") / rel / frel))
                        counts["workspace_files"] += 1
                    except OSError as e:
                        warnings.append("跳过 %s（%s）" % (rel / frel, e.strerror or e))
        # manifest 最后写：此刻 data/workspace 计数才是终值
        zf.writestr(MANIFEST_NAME, json.dumps(manifest, ensure_ascii=False, indent=2))
    finally:
        if zf is not None:
            zf.close()
    manifest["counts"] = counts
    return {"path": str(out), "size": out.stat().st_size,
            "tasks": counts["tasks"], "runs": counts["runs"],
            "data_files": counts["data_files"],
            "workspace_files": counts["workspace_files"],
            "external_workdirs": external, "warnings": warnings}


# ---------------------------------------------------------------- 预览

def _read_manifest(zf):
    try:
        raw = zf.read(MANIFEST_NAME)
    except KeyError:
        raise ValueError("不是 CodeBee 备份包（缺少 manifest.json）")
    try:
        m = json.loads(raw.decode("utf-8"))
    except Exception:
        raise ValueError("备份包 manifest.json 无法解析")
    if not isinstance(m, dict) or m.get("app") != APP_ID:
        raise ValueError("不是 CodeBee 备份包（manifest 不匹配）")
    if int(m.get("schema") or 0) > SCHEMA:
        raise ValueError("备份包版本较新（schema %s > %d），请先升级 CodeBee 再导入"
                         % (m.get("schema"), SCHEMA))
    return m


def _safe_join(base: Path, name: str):
    """zip 条目名 → base 下安全路径（zip slip 三道闸，拒绝一切逃逸形态）。

    ①条目按 / 与 \\ 切段后，出现 .. 段、盘符（C:）或空壳即拒绝——这一步在
    构造路径之前完成，静态可证；②只用纯段名 join 回基线（base / 带盘符的
    name 在 Windows 会整个替换掉 base，故绝不直接拼）；③normpath 后必须
    仍在 base 之内（双保险）。"""
    parts = [p for p in _SEG_SPLIT.split(name) if p not in ("", ".")]
    if (not parts or any(p == ".." for p in parts)
            or _DRIVE_RE.match(parts[0])):
        raise ValueError("备份包含非法路径条目: %s" % name[:120])
    resolved = Path(os.path.normpath(os.path.join(str(base), *parts)))
    try:
        resolved.relative_to(base)
    except ValueError:
        raise ValueError("备份包含非法路径条目: %s" % name[:120])
    return resolved


def _busy_runs():
    from . import store
    out = []
    try:
        for r in store.list_runs(limit=10 ** 6):
            if r.get("status") in ("queued", "running"):
                out.append(r.get("id"))
    except Exception:
        pass
    return out


def inspect_backup(zip_path):
    """解析备份包，返回预览信息（不落任何盘）。包打不开/不是本家包抛 ValueError。"""
    p = Path(str(zip_path or "").strip())
    if not p.is_file():
        raise ValueError("备份包不存在: %s" % p)
    try:
        zf = zipfile.ZipFile(str(p))
    except Exception as e:
        raise ValueError("备份包打不开：%s" % e)
    with zf:
        m = _read_manifest(zf)
        names = zf.namelist()
    data_n = sum(1 for n in names if n.startswith("data/"))
    ws_n = sum(1 for n in names if n.startswith("workspace/"))
    old_data = str(m.get("data_dir") or "")
    old_wd = str(m.get("default_workdir") or "")
    cur_data = str(Path(paths.DATA_DIR))
    cur_wd = settings_mod.default_workdir()
    return {
        "exported_at": m.get("exported_at"), "version": m.get("version"),
        "tasks": (m.get("counts") or {}).get("tasks"),
        "runs": (m.get("counts") or {}).get("runs"),
        "data_files": data_n, "workspace_files": ws_n,
        "include_logs": bool(m.get("include_logs", True)),
        "external_workdirs": m.get("external_workdirs") or [],
        "old_data_dir": old_data, "old_workdir": old_wd,
        "cur_data_dir": cur_data, "cur_workdir": cur_wd,
        "remap_needed": (old_data.lower() != cur_data.lower()
                         or old_wd.lower() != cur_wd.lower()),
        "busy_runs": _busy_runs(),
        "same_machine": old_data.lower() == cur_data.lower(),
    }


# ---------------------------------------------------------------- 导入

def _pre_import_backup(ts):
    """把当前小体积状态备份到 <data>/imports/pre-import-<ts>.zip（反悔用）。"""
    data_dir = Path(paths.DATA_DIR)
    bdir = data_dir / "imports"
    bdir.mkdir(parents=True, exist_ok=True)
    out = bdir / ("pre-import-" + ts + ".zip")
    with zipfile.ZipFile(str(out), "w", zipfile.ZIP_DEFLATED) as zf:
        for p in sorted(data_dir.glob("*.json")):
            try:
                zf.write(str(p), p.name)
            except OSError:
                pass
        tdir = data_dir / "tasks"
        if tdir.is_dir():
            for p in sorted(tdir.glob("*.json")):
                try:
                    zf.write(str(p), str(Path("tasks") / p.name))
                except OSError:
                    pass
    return out


def _remap_value(v, pairs):
    """字符串值按前缀对改写（长前缀优先）；命中返回 (新串, True)。"""
    if not isinstance(v, str) or not v:
        return v, False
    for old, new in pairs:  # pairs 已按 len(old) 降序
        if old.lower() == v.lower():
            return new, True
        lo = v.lower()
        if lo.startswith(old.lower()):
            tail = v[len(old):]
            if tail and tail[0] not in "\\/":
                continue  # 只是碰巧同前缀（如 E:\GoOut2），不是旧路径
            return new + tail, True
    return v, False


def _remap_obj(o, pairs, stat):
    if isinstance(o, dict):
        return {k: _remap_obj(v, pairs, stat) for k, v in o.items()}
    if isinstance(o, list):
        return [_remap_obj(v, pairs, stat) for v in o]
    nv, hit = _remap_value(o, pairs)
    if hit:
        stat[0] += 1
    return nv


def _remap_all_json(pairs):
    """数据目录内所有 JSON 的字符串值做旧→新路径改写。返回 (文件数, 值数)。"""
    data_dir = Path(paths.DATA_DIR)
    files = hits = 0
    for p in data_dir.rglob("*.json"):
        rel = p.relative_to(data_dir)
        if rel.parts[0] in ("imports", "exports"):
            continue
        try:
            raw = p.read_text(encoding="utf-8")
            obj = json.loads(raw)
            if not isinstance(obj, (dict, list)):
                continue
            stat = [0]
            new_obj = _remap_obj(obj, pairs, stat)
            if stat[0]:
                tmp = p.with_suffix(".tmp")
                tmp.write_text(json.dumps(new_obj, ensure_ascii=False, indent=2),
                               encoding="utf-8")
                os.replace(str(tmp), str(p))
                files += 1
                hits += stat[0]
        except Exception:
            continue  # 单个文件坏/被锁不阻塞整体导入
    return files, hits


def _extract(zf, names, base, prefix, skip=None):
    n = 0
    for name in names:
        if not name.startswith(prefix) or name == prefix:
            continue
        rel = name[len(prefix):]
        if not rel:
            continue
        if skip and skip(rel):
            continue
        target = _safe_join(base, rel)
        if name.endswith("/"):
            target.mkdir(parents=True, exist_ok=True)
            continue
        target.parent.mkdir(parents=True, exist_ok=True)
        with zf.open(name) as src, open(str(target), "wb") as dst:
            shutil.copyfileobj(src, dst)
        n += 1
    return n


def apply_import(zip_path, mode="merge", remap=True, include_workspace=True):
    """应用备份包。mode=merge 现有数据保留、同名覆盖；replace 先清空再落（不碰
    imports/ 与 pet.lock）。返回信息 dict；校验失败/有运行中任务抛 ValueError。"""
    p = Path(str(zip_path or "").strip())
    if not p.is_file():
        raise ValueError("备份包不存在: %s" % p)
    mode = mode if mode in ("merge", "replace") else "merge"
    busy = _busy_runs()
    if busy:
        raise ValueError("有 %d 个任务正在运行或排队（%s…），导入会踩到正在写的"
                         "文件；等它们跑完或取消后再试"
                         % (len(busy), "、".join(busy[:3])))

    cur_wd = settings_mod.default_workdir()
    data_dir = Path(paths.DATA_DIR)
    zf = zipfile.ZipFile(str(p))
    try:
        m = _read_manifest(zf)
        names = [n for n in zf.namelist() if not n.endswith("/")]
        # 事前把全部条目做 zip-slip 校验，任何一个不合法就整体拒绝
        for name in names:
            if name.startswith("data/"):
                _safe_join(data_dir, name[len("data/"):])
            elif name.startswith("workspace/"):
                _safe_join(Path(cur_wd), name[len("workspace/"):])
            elif name != MANIFEST_NAME:
                raise ValueError("备份包含无法识别的条目: %s" % name)
        ts = time.strftime("%Y%m%d-%H%M%S")
        backup_path = _pre_import_backup(ts)
        if mode == "replace":
            for child in data_dir.iterdir():
                if child.name in ("imports", "pet.lock"):
                    continue
                try:
                    if child.is_dir():
                        shutil.rmtree(str(child), ignore_errors=True)
                    else:
                        child.unlink()
                except OSError:
                    pass
        n_data = _extract(zf, names, data_dir, "data/")
        n_ws = 0
        if include_workspace:
            Path(cur_wd).mkdir(parents=True, exist_ok=True)
            n_ws = _extract(zf, names, Path(cur_wd), "workspace/")
    finally:
        zf.close()

    # 路径重映射：长前缀优先（工作目录可能在数据目录之下，避免被短前缀截走）
    old_data = str(m.get("data_dir") or "")
    old_wd = str(m.get("default_workdir") or "")
    pairs = []
    if remap:
        for old, new in ((old_wd, cur_wd), (old_data, str(data_dir))):
            if old and old.lower() != new.lower():
                pairs.append((old, new))
        pairs.sort(key=lambda x: len(x[0]), reverse=True)
    remapped_files = remapped_values = 0
    if pairs:
        remapped_files, remapped_values = _remap_all_json(pairs)

    # 导入的 settings.json 里若带着旧机器默认路径，上面已重映射；把当前生效值对齐
    try:
        from . import store
        with store.LOCK:
            store._TASKS.clear()
            store._RUNS.clear()
        store.load_all()
        store.bump_state()
    except Exception:
        pass
    return {"mode": mode, "data_files": n_data, "workspace_files": n_ws,
            "remap": {"applied": bool(pairs), "files": remapped_files,
                      "values": remapped_values, "pairs": [[o, n] for o, n in pairs]},
            "backup_path": str(backup_path),
            "external_workdirs": m.get("external_workdirs") or [],
            "restart_required": True}
