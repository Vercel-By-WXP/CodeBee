# -*- coding: utf-8 -*-
"""Safe local plugin lifecycle for CodeBee.

Plugins use the Codex ``.codex-plugin/plugin.json`` contract.  This module owns
discovery and installation metadata; skills are still installed through the
existing ``market``/``skills`` path and MCP servers are only exposed as
declarations to the existing stdio MCP client.  Plugin scripts, hooks and app
bundles are never executed by this module.
"""
from __future__ import annotations

import json
import os
import re
import shutil
import threading
import time
from pathlib import Path

from . import market, paths, skills

_LOCK = threading.RLock()
_NAME_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]{0,63}$")
_VERSION_RE = re.compile(r"^\d+\.\d+\.\d+(?:[-+][0-9A-Za-z.-]+)?$")
_MAX_MANIFEST_BYTES = 1024 * 1024
_MAX_SKILL_FILES = 128
_MAX_SKILL_BYTES = 1024 * 1024
_MAX_TOTAL_SKILL_BYTES = 8 * 1024 * 1024
_MAX_PLUGIN_FILES = 512
_MAX_PLUGIN_BYTES = 32 * 1024 * 1024


class PluginError(ValueError):
    """A user-actionable plugin validation or lifecycle error."""


def _registry_file():
    return Path(paths.DATA_DIR) / "plugins.json"


def _install_root():
    return Path(paths.DATA_DIR) / "plugins"


def _now():
    return time.strftime("%Y-%m-%d %H:%M:%S")


def _load_registry():
    try:
        data = json.loads(_registry_file().read_text(encoding="utf-8"))
    except (OSError, ValueError, TypeError):
        return {"installed": {}}
    if not isinstance(data, dict):
        return {"installed": {}}
    installed = data.get("installed")
    data["installed"] = installed if isinstance(installed, dict) else {}
    return data


def _save_registry(data):
    target = _registry_file()
    target.parent.mkdir(parents=True, exist_ok=True)
    tmp = target.with_suffix(".tmp")
    tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    tmp.replace(target)


def _invalidate_mcp_cache():
    try:
        from . import mcp_client
        mcp_client.invalidate_tools_cache()
    except Exception:
        pass


def _inside(root, value, *, must_exist=False):
    """Resolve a manifest-relative path without allowing traversal or symlinks."""
    root = Path(root).resolve()
    raw = str(value or "").strip()
    if not raw or Path(raw).is_absolute():
        raise PluginError("插件路径必须是非空相对路径: %s" % raw)
    candidate = (root / raw).resolve()
    try:
        candidate.relative_to(root)
    except ValueError:
        raise PluginError("插件路径越界: %s" % raw)
    if must_exist and not candidate.exists():
        raise PluginError("插件文件不存在: %s" % raw)
    return candidate


def _read_json(path):
    try:
        if path.stat().st_size > _MAX_MANIFEST_BYTES:
            raise PluginError("插件 manifest 过大")
        return json.loads(path.read_text(encoding="utf-8"))
    except PluginError:
        raise
    except (OSError, UnicodeError, ValueError) as exc:
        raise PluginError("无法读取插件 manifest: %s" % exc)


def _path_values(value):
    if value is None:
        return []
    if isinstance(value, str):
        return [value]
    if isinstance(value, list) and all(isinstance(x, str) for x in value):
        return list(value)
    raise PluginError("manifest 路径字段必须是字符串或字符串数组")


def _validate_manifest(plugin_dir, manifest):
    if not isinstance(manifest, dict):
        raise PluginError("plugin.json 顶层必须是对象")
    name = manifest.get("name")
    if not isinstance(name, str) or not _NAME_RE.fullmatch(name):
        raise PluginError("插件 name 非法")
    if name != Path(plugin_dir).name:
        raise PluginError("插件 name 必须与目录名一致")
    version = manifest.get("version")
    if not isinstance(version, str) or not _VERSION_RE.fullmatch(version):
        raise PluginError("插件 version 必须是语义化版本号")
    if not str(manifest.get("description") or "").strip():
        raise PluginError("插件缺少 description")
    author = manifest.get("author")
    if not isinstance(author, dict) or not str(author.get("name") or "").strip():
        raise PluginError("插件缺少 author.name")
    interface = manifest.get("interface")
    if not isinstance(interface, dict):
        raise PluginError("插件缺少 interface")
    for key in ("displayName", "shortDescription", "longDescription", "developerName", "category"):
        if not str(interface.get(key) or "").strip():
            raise PluginError("插件 interface 缺少 %s" % key)
    if not interface.get("defaultPrompt"):
        raise PluginError("插件 interface 缺少 defaultPrompt")
    if "capabilities" in interface and not isinstance(interface["capabilities"], list):
        raise PluginError("interface.capabilities 必须是数组")

    skill_paths = []
    for rel in _path_values(manifest.get("skills")):
        path = _inside(plugin_dir, rel, must_exist=True)
        if path.is_dir():
            skill_paths.append(path)
        elif path.suffix.lower() == ".md":
            skill_paths.append(path)
        else:
            raise PluginError("skills 路径必须指向目录或 Markdown 文件: %s" % rel)

    mcp = manifest.get("mcpServers")
    mcp_path = None
    if isinstance(mcp, str):
        mcp_path = _inside(plugin_dir, mcp, must_exist=True)
        if not mcp_path.is_file():
            raise PluginError("mcpServers 文件不存在")
    elif mcp is not None and not isinstance(mcp, dict):
        raise PluginError("mcpServers 必须是对象或 JSON 文件路径")
    if "apps" in manifest:
        for rel in _path_values(manifest.get("apps")):
            _inside(plugin_dir, rel, must_exist=True)
    if "hooks" in manifest:
        raise PluginError("暂不支持 hooks 插件；请使用 MCP 或技能声明")
    return {
        "manifest": manifest,
        "dir": str(Path(plugin_dir).resolve()),
        "name": name,
        "version": version,
        "display_name": str(interface["displayName"]),
        "description": str(manifest["description"]),
        "short_description": str(interface.get("shortDescription") or ""),
        "long_description": str(interface.get("longDescription") or ""),
        "author": str((author or {}).get("name") or ""),
        "category": str(interface.get("category") or "Productivity"),
        "capabilities": [str(x) for x in (interface.get("capabilities") or [])],
        "skill_paths": skill_paths,
        "mcp_path": str(mcp_path) if mcp_path else "",
        "mcp_inline": mcp if isinstance(mcp, dict) else {},
        "unsupported": [],
    }


def load_manifest(plugin_dir):
    """Load and validate a plugin directory, returning a normalized descriptor."""
    root = Path(plugin_dir).expanduser().resolve()
    if not root.is_dir():
        raise PluginError("插件目录不存在: %s" % plugin_dir)
    manifest_path = root / ".codex-plugin" / "plugin.json"
    if not manifest_path.is_file():
        raise PluginError("缺少 .codex-plugin/plugin.json")
    return _validate_manifest(root, _read_json(manifest_path))


def _default_roots():
    # app/plugins is the repo/team location; ~/plugins follows the Codex
    # personal plugin convention; data/plugins contains installed copies.
    return [Path(paths.APP_DIR) / "plugins", Path.home() / "plugins", _install_root()]


def _iter_plugin_dirs(roots):
    seen = set()
    if isinstance(roots, (str, os.PathLike)):
        roots = [roots]
    for raw in roots:
        root = Path(raw).expanduser()
        if not root.exists():
            continue
        candidates = [root] if (root / ".codex-plugin" / "plugin.json").is_file() else list(root.iterdir())
        for item in candidates:
            try:
                key = str(item.resolve())
            except OSError:
                continue
            if key in seen or not item.is_dir():
                continue
            seen.add(key)
            if (item / ".codex-plugin" / "plugin.json").is_file():
                yield item


def discover(roots=None):
    """Discover valid plugins. Invalid entries are returned as blocked catalog rows."""
    out = []
    for item in _iter_plugin_dirs(_default_roots() if roots is None else roots):
        try:
            out.append(_catalog_row(load_manifest(item)))
        except PluginError as exc:
            out.append({"id": item.name, "name": item.name, "state": "blocked",
                        "available": True, "installed": False, "error": str(exc),
                        "source": str(item.resolve())})
    return out


def _skill_files(desc):
    files = []
    for base in desc["skill_paths"]:
        if base.is_file():
            files.append(base)
            continue
        for path in sorted(base.rglob("*.md")):
            if path.is_file() and not path.is_symlink():
                files.append(path)
    if len(files) > _MAX_SKILL_FILES:
        raise PluginError("插件技能文件超过 %d 个" % _MAX_SKILL_FILES)
    total = 0
    out = {}
    package_id = "plugin-" + desc["name"]
    for index, path in enumerate(files):
        size = path.stat().st_size
        if size > _MAX_SKILL_BYTES:
            raise PluginError("技能文件过大: %s" % path.name)
        total += size
        if total > _MAX_TOTAL_SKILL_BYTES:
            raise PluginError("插件技能总量过大")
        stem = re.sub(r"[^A-Za-z0-9_.-]+", "-", path.stem).strip("-") or str(index)
        rel = "market-%s%s.md" % (package_id, "" if index == 0 else "-%d-%s" % (index, stem))
        raw = path.read_text(encoding="utf-8", errors="replace")
        out[rel] = _mark_skill(raw, desc, path.stem, package_id)
    return out


def _mark_skill(raw, desc, stem, package_id):
    """Add market metadata while preserving a skill's existing frontmatter."""
    if raw.startswith("---"):
        end = raw.find("\n---", 3)
        if end >= 0:
            head = raw[3:end]
            body = raw[end + 4:]
            lines = head.splitlines()
            keys = {line.split(":", 1)[0].strip() for line in lines if ":" in line}
            if "name" not in keys:
                lines.insert(0, "name: %s - %s" % (desc["display_name"], stem))
            if "scopes" not in keys:
                lines.extend(["scopes:", '- "*"'])
            lines.extend(["source: market", "market_id: " + package_id,
                          "plugin_id: " + desc["name"]])
            return "---\n" + "\n".join(lines) + "\n---" + body
    return ("---\nname: %s - %s\nnote: %s\nscopes:\n- \"*\"\n"
            "source: market\nmarket_id: %s\nplugin_id: %s\n---\n\n%s"
            % (desc["display_name"], stem, desc["short_description"], package_id,
               desc["name"], raw))


def _mcp_for_desc(desc):
    data = desc.get("mcp_inline") or {}
    if desc.get("mcp_path"):
        raw = _read_json(Path(desc["mcp_path"]))
        data = raw.get("mcpServers") if isinstance(raw, dict) else {}
    if isinstance(data, dict) and isinstance(data.get("mcpServers"), dict):
        data = data["mcpServers"]
    if not isinstance(data, dict):
        raise PluginError("MCP 配置必须是对象")
    out = []
    for name, cfg in data.items():
        if not isinstance(cfg, dict):
            continue
        item = dict(cfg)
        item["name"] = str(name)
        item["plugin_id"] = desc["name"]
        out.append(item)
    return out


def mcp_servers(plugin_id, roots=None):
    """Return declared stdio-compatible MCP configs for a plugin."""
    desc = _descriptor_for(plugin_id, roots=roots)
    if not desc:
        return []
    return _mcp_for_desc(desc)


def _assert_no_symlinks(root):
    count = 0
    total = 0
    for base, dirs, files in os.walk(root, followlinks=False):
        for name in list(dirs) + list(files):
            if (Path(base) / name).is_symlink():
                raise PluginError("插件包不允许包含符号链接: %s" % name)
        for name in files:
            count += 1
            if count > _MAX_PLUGIN_FILES:
                raise PluginError("插件文件超过 %d 个" % _MAX_PLUGIN_FILES)
            try:
                total += (Path(base) / name).stat().st_size
            except OSError as exc:
                raise PluginError("无法读取插件文件: %s" % exc)
            if total > _MAX_PLUGIN_BYTES:
                raise PluginError("插件包超过 %d MB" % (_MAX_PLUGIN_BYTES // 1048576))


def _copy_plugin(source, target):
    _assert_no_symlinks(source)
    target.parent.mkdir(parents=True, exist_ok=True)
    if target.exists():
        raise PluginError("安装目标已存在: %s" % target.name)
    shutil.copytree(source, target, symlinks=False)


def _descriptor_for(plugin_id, roots=None):
    for row in discover(roots):
        if row.get("id") == plugin_id and row.get("source"):
            try:
                return load_manifest(row["source"])
            except PluginError:
                return None
    rec = _load_registry().get("installed", {}).get(plugin_id) or {}
    path = rec.get("install_dir")
    if path:
        try:
            return load_manifest(path)
        except PluginError:
            return None
    return None


def _installed_record(plugin_id):
    return _load_registry().get("installed", {}).get(plugin_id)


def install(plugin_id, roots=None):
    """Install a discovered plugin and its skills without executing code."""
    plugin_id = str(plugin_id or "").strip()
    desc = _descriptor_for(plugin_id, roots=roots)
    if not desc:
        return None, "插件不存在或 manifest 无效: %s" % plugin_id
    with _LOCK:
        old = _installed_record(plugin_id)
        if old and Path(str(old.get("install_dir") or "")).is_dir():
            return {"ok": True, "id": plugin_id, "already": True,
                    "skill_count": len(old.get("skill_pack_ids") or [])}, None
        source = Path(desc["dir"]).resolve()
        target = (_install_root() / plugin_id).resolve()
        try:
            target.relative_to(_install_root().resolve())
            _copy_plugin(source, target)
            installed_desc = load_manifest(target)
            files = _skill_files(installed_desc)
            market_id = "plugin-" + plugin_id
            skill_result = None
            market_written = False
            if files:
                skill_result, err = market.install_files(
                    market_id, installed_desc["display_name"], files,
                    extra={"plugin_id": plugin_id, "plugin_version": installed_desc["version"]})
                if err:
                    raise PluginError(err)
            market_written = not bool((skill_result or {}).get("already"))
            skill_pack_ids = []
            if files:
                names = set((skill_result or {}).get("files") or files.keys())
                for pack in skills.user_packs():
                    if Path(str(pack.get("file") or "")).name in names:
                        skill_pack_ids.append(pack["id"])
            reg = _load_registry()
            reg.setdefault("installed", {})[plugin_id] = {
                "install_dir": str(target), "source": str(source),
                "market_id": market_id if files else "", "skill_pack_ids": skill_pack_ids,
                "enabled": False, "installed_at": _now(),
                "has_mcp": bool(installed_desc.get("mcp_path") or installed_desc.get("mcp_inline")),
            }
            _save_registry(reg)
            for pack_id in skill_pack_ids:
                err = skills.pack_op(pack_id, "disable")
                if err:
                    raise PluginError(err)
            _invalidate_mcp_cache()
        except (OSError, PluginError) as exc:
            if market_written:
                try:
                    market.remove(market_id)
                except Exception:
                    pass
            if target.exists() and target != source:
                shutil.rmtree(target, ignore_errors=True)
            return None, str(exc)
    return {"ok": True, "id": plugin_id, "already": False,
            "skill_count": len(skill_pack_ids),
            "has_mcp": bool(installed_desc.get("mcp_path") or installed_desc.get("mcp_inline"))}, None


def toggle(plugin_id, enabled):
    with _LOCK:
        reg = _load_registry()
        rec = (reg.get("installed") or {}).get(str(plugin_id))
        if not rec:
            return "插件尚未安装"
        pack_ids = list(rec.get("skill_pack_ids") or [])
        changed = []
        action = "enable" if enabled else "disable"
        for pack_id in pack_ids:
            err = skills.pack_op(pack_id, action)
            if err:
                rollback = "disable" if enabled else "enable"
                for changed_id in reversed(changed):
                    skills.pack_op(changed_id, rollback)
                return err
            changed.append(pack_id)
        rec["enabled"] = bool(enabled)
        _save_registry(reg)
        _invalidate_mcp_cache()
    return None


def remove(plugin_id, roots=None):
    plugin_id = str(plugin_id or "").strip()
    with _LOCK:
        reg = _load_registry()
        rec = (reg.get("installed") or {}).get(plugin_id)
        if not rec:
            return "插件尚未安装"
        market_id = str(rec.get("market_id") or "")
        if market_id:
            err = market.remove(market_id)
            if err:
                return err
        target = Path(str(rec.get("install_dir") or "")).resolve()
        try:
            target.relative_to(_install_root().resolve())
        except ValueError:
            return "插件安装路径非法，拒绝删除"
        if target.is_dir():
            shutil.rmtree(target)
        reg["installed"].pop(plugin_id, None)
        _save_registry(reg)
        _invalidate_mcp_cache()
    return None


def _catalog_row(desc):
    skill_names = []
    for base in desc.get("skill_paths") or []:
        if base.is_file():
            skill_names.append(base.name)
        elif base.is_dir():
            skill_names.extend(p.relative_to(base).as_posix() for p in base.rglob("*.md") if p.is_file())
    return {
        "id": desc["name"], "name": desc["name"],
        "display_name": desc["display_name"], "description": desc["description"],
        "short_description": desc["short_description"], "version": desc["version"],
        "author": desc["author"], "category": desc["category"],
        "capabilities": desc["capabilities"], "skills": sorted(skill_names),
        "skill_count": len(skill_names), "has_mcp": bool(desc.get("mcp_path") or desc.get("mcp_inline")),
        "unsupported": desc["unsupported"], "source": desc["dir"],
        "available": True, "installed": False, "state": "available",
    }


def view(roots=None):
    rows = {x.get("id"): x for x in discover(roots) if x.get("id")}
    reg = _load_registry().get("installed") or {}
    for plugin_id, rec in reg.items():
        target_raw = str(rec.get("install_dir") or "").strip()
        target = Path(target_raw) if target_raw else None
        if plugin_id not in rows and target is not None and target.is_dir():
            try:
                rows[plugin_id] = _catalog_row(load_manifest(target))
            except PluginError as exc:
                rows[plugin_id] = {"id": plugin_id, "name": plugin_id, "state": "broken",
                                   "available": False, "installed": True, "error": str(exc)}
        row = rows.get(plugin_id)
        if not row:
            continue
        row["installed"] = bool(target is not None and target.is_dir())
        row["state"] = "enabled" if rec.get("enabled", True) else "disabled"
        row["enabled"] = bool(rec.get("enabled", True))
        row["install_dir"] = str(target) if target is not None else ""
        row["skill_pack_ids"] = list(rec.get("skill_pack_ids") or [])
    items = sorted(rows.values(), key=lambda x: (str(x.get("category") or ""), str(x.get("display_name") or x.get("name") or "")))
    cats = sorted({str(x.get("category")) for x in items if x.get("category")})
    return {"plugins": items, "categories": cats, "total": len(items)}


def active_mcp_servers():
    """Return enabled plugin MCP declarations in existing client format."""
    out = []
    for plugin_id, rec in (_load_registry().get("installed") or {}).items():
        if not rec.get("enabled", True):
            continue
        path = Path(str(rec.get("install_dir") or ""))
        try:
            out.extend(mcp_servers(plugin_id, roots=[path]))
        except PluginError:
            continue
    return [x for x in out if isinstance(x, dict) and x.get("command")]
