# -*- coding: utf-8 -*-
"""备份包远程推送（WebDAV / S3 兼容 PUT 端点）：data/ 不在 git 里、磁盘一坏
全没——本机产品额外给备份包一条出站活路。

L0 自由层（读 settings + 依赖 runner 起进程都合法）。curl -T 流式上传：
备份包可能 GB 级（含工作目录），绝不能整包读进内存。密钥只存本机
settings.json，推送时才带 Basic auth 发给用户自己配的端点。
"""
from __future__ import annotations

import json

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
    from . import runner
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
