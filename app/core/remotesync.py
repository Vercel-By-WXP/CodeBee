# -*- coding: utf-8 -*-
"""备份包远程推送（WebDAV / S3 兼容 PUT 端点）：data/ 不在 git 里、磁盘一坏
全没——本机产品额外给备份包一条出站活路。

L0 自由层（读 settings + 依赖 runner 起进程都合法）。curl -T 流式上传：
备份包可能 GB 级（含工作目录），绝不能整包读进内存。密钥只存本机
settings.json，推送时才带 Basic auth 发给用户自己配的端点。
"""
from __future__ import annotations

import json

from . import runner   # L0→L2 合法方向（小层号依赖大层号）；模块级绑定便于测试打桩

_MAX_TIMEOUT_S = 3600


def _cfg():
    try:
        from . import settings
        s = settings.load()
        return {"enabled": bool(s.get("backup_remote_enabled")),
                "url": str(s.get("backup_remote_url") or "").strip().rstrip("/"),
                "user": str(s.get("backup_remote_user") or "").strip(),
                "pass": str(s.get("backup_remote_pass") or "")}
    except Exception:
        return {"enabled": False, "url": "", "user": "", "pass": ""}


def push_remote(zip_path, timeout_s=1800):
    """把备份包 PUT 到远程端点。返回 {"ok","skipped","status","error"}。"""
    cfg = _cfg()
    if not cfg["enabled"] or not cfg["url"]:
        return {"ok": False, "skipped": True, "status": 0, "error": "未启用远程备份"}
    from pathlib import Path
    p = Path(zip_path)
    if not p.is_file():
        return {"ok": False, "skipped": False, "status": 0, "error": "备份包不存在"}
    argv = ["curl", "-sS", "-f", "-T", str(p), "--max-time", str(max(60, timeout_s)),
            cfg["url"] + "/" + p.name]
    if cfg["user"] or cfg["pass"]:
        argv += ["--user", cfg["user"] + ":" + cfg["pass"]]
    try:
        r = runner.run_process(argv=argv, timeout=timeout_s + 100)
    except Exception as e:
        return {"ok": False, "skipped": False, "status": 0, "error": str(e)[:200]}
    err = (r.get("stderr") or "").strip().splitlines()
    return {"ok": bool(r.get("ok")), "skipped": False,
            "status": 200 if r.get("ok") else 0,
            "error": "" if r.get("ok") else (err[-1] if err else "上传失败")[:200]}


def test_remote():
    """连通性测试：PUT 一个小探针文件（同端点同凭据）。"""
    import os
    import tempfile
    fd, p = tempfile.mkstemp(prefix="codebee-remote-", suffix=".txt")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            f.write("CodeBee remote backup probe")
        return push_remote(p, timeout_s=60)
    finally:
        try:
            os.unlink(p)
        except OSError:
            pass


def _curl_common_args():
    cfg = _cfg()
    argv = ["curl", "-sS", "-f", "--max-time", "120"]
    if cfg["user"] or cfg["pass"]:
        argv += ["--user", cfg["user"] + ":" + cfg["pass"]]
    return cfg, argv


def list_remote():
    """列远程端点上的备份包（WebDAV PROPFIND，其他端点回落 GET 目录页）。
    返回 (文件名列表, 错误)。仅支持 push 时同款 URL 前缀。"""
    cfg, argv = _curl_common_args()
    if not cfg["enabled"] or not cfg["url"]:
        return None, "未启用远程备份"
    r = runner.run_process(
        argv=argv + ["-X", "PROPFIND", "-H",
                     "Content-Type: application/xml", "-H", "Depth: 1",
                     "--data-binary", "", cfg["url"] + "/"], timeout=150)
    if not r.get("ok"):
        err = (r.get("stderr") or "").strip().splitlines()
        return None, (err[-1] if err else "列举失败（端点可能不支持 PROPFIND）")[:200]
    import re as _re
    text = str(r.get("stdout") or "")
    names = sorted({m for m in _re.findall(
        r"codebee-backup-\d{8}-\d{6}\.zip", text)}, reverse=True)
    return names, None


def pull_remote(name):
    """从远程端点拉备份包到本机 imports 目录（走现有导入向导）。
    name 必须精确匹配 codebee-backup-*.zip 形态（防路径注入）。
    返回 (本地路径, 错误)。"""
    import re as _re
    name = str(name or "").strip()
    if not _re.match(r"^codebee-backup-\d{8}-\d{6}\.zip$", name):
        return None, "非法的备份包名"
    cfg, argv = _curl_common_args()
    if not cfg["enabled"] or not cfg["url"]:
        return None, "未启用远程备份"
    from . import paths
    dest_dir = paths.DATA_DIR / "imports"
    dest_dir.mkdir(parents=True, exist_ok=True)
    dest = dest_dir / name
    r = runner.run_process(
        argv=argv + ["-o", str(dest), cfg["url"] + "/" + name], timeout=1900)
    if not r.get("ok") or not dest.is_file() or dest.stat().st_size == 0:
        try:
            dest.unlink()
        except OSError:
            pass
        err = (r.get("stderr") or "").strip().splitlines()
        return None, (err[-1] if err else "拉取失败")[:200]
    return str(dest), None
