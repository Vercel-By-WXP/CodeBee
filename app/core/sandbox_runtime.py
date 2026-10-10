"""Optional process sandbox launchers.

The default CodeBee install has no container dependency. Docker is an explicit
opt-in backend selected by a task policy and a configured image; this module
only builds argv and never silently falls back to a weaker backend.
"""
from __future__ import annotations

import os
import re
import shutil
from pathlib import Path


class SandboxUnavailable(RuntimeError):
    pass


def requested_backend(sandbox):
    value = str((sandbox or {}).get("backend") or "native").strip().lower()
    return value or "native"


def docker_argv(command, *, sandbox, workdir, image=None, extra_args=(),
                container_workdir=None,
                container_command=("/bin/sh", "-lc")):
    """Build a restricted ``docker run`` argv for a command or stdio server."""
    if requested_backend(sandbox) != "docker":
        raise SandboxUnavailable("Docker 后端未被任务策略启用")
    docker = shutil.which("docker")
    if not docker:
        raise SandboxUnavailable("当前系统没有可用的 Docker CLI")
    root = Path(workdir or ".").expanduser().resolve(strict=True)
    image = str(image or (sandbox or {}).get("docker_image") or "").strip()
    if not image or len(image) > 240 or not re.match(r"^[A-Za-z0-9][A-Za-z0-9._/:@-]*$", image):
        raise SandboxUnavailable("Docker 后端必须配置可信镜像")
    roots = []
    for value in (sandbox or {}).get("allowed_roots") or [str(root)]:
        candidate = Path(value).expanduser().resolve(strict=True)
        # Docker currently mounts exactly one root. Require the task workspace
        # itself; narrower roots need a separately resolved container cwd and
        # must never accidentally broaden into an ancestor mount.
        if candidate != root:
            raise SandboxUnavailable("Docker 目前只支持挂载任务工作目录")
        if candidate not in roots:
            roots.append(candidate)
    if not roots:
        raise SandboxUnavailable("Docker 后端没有允许目录")
    selected_root = next((candidate for candidate in roots
                          if candidate == root or candidate in root.parents
                          or root in candidate.parents), None)
    if selected_root is None:
        raise SandboxUnavailable("当前工作目录不在 Docker 允许目录内")
    if len(roots) != 1:
        raise SandboxUnavailable("Docker 暂只支持一个明确的允许目录")
    argv = [docker, "run", "--rm", "--init", "--read-only",
            "--security-opt", "no-new-privileges", "--cap-drop=ALL",
            "--ipc=none", "--pids-limit", "256", "--memory", "2g", "--cpus", "2",
            "--tmpfs", "/tmp:rw,noexec,nosuid,size=64m"]
    if hasattr(os, "getuid") and hasattr(os, "getgid"):
        argv += ["--user", "%d:%d" % (os.getuid(), os.getgid())]
    else:
        argv += ["--user", "65534:65534"]
    argv += ["--network", "none" if not bool((sandbox or {}).get("network", True)) else "bridge"]
    for name in (sandbox or {}).get("env_allowlist") or []:
        name = str(name)
        if name and name in os.environ:
            # Value is supplied through the filtered Docker client environment;
            # never place credentials in argv, where process/log inspection sees them.
            argv += ["--env", name]
    for candidate in roots:
        if "," in str(candidate):
            raise SandboxUnavailable("Docker 允许目录包含不支持的逗号")
        argv += ["--mount", "type=bind,src=%s,dst=/workspace" % str(candidate)]
    if container_workdir is None:
        rel_workdir = root.relative_to(selected_root).as_posix()
        container_workdir = "/workspace" + ("/" + rel_workdir if rel_workdir != "." else "")
    if not str(container_workdir).startswith("/") or ".." in Path(container_workdir).parts:
        raise SandboxUnavailable("Docker 容器工作目录无效")
    argv += ["--workdir", str(container_workdir), *list(extra_args), image]
    if isinstance(command, (list, tuple)):
        argv += [str(x) for x in command]
    else:
        argv += list(container_command) + [str(command)]
    return argv


def docker_env(sandbox):
    """Docker receives only explicitly allowed host environment values."""
    out = {"PATH": "/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin",
           "HOME": "/tmp", "TMPDIR": "/tmp"}
    for name in (sandbox or {}).get("env_allowlist") or []:
        if name in os.environ:
            out[str(name)] = os.environ[str(name)]
    return out
