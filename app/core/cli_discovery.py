"""Dependency-free local CLI capability discovery."""
from __future__ import annotations

import json
import os
import shutil
import subprocess
from pathlib import Path

from . import atomic

DEFAULT_CLIS = (
    {"name": "codex", "executable": "codex"},
    {"name": "claude", "executable": "claude"},
    {"name": "qwen", "executable": "qwen"},
    {"name": "aider", "executable": "aider"},
    {"name": "opencode", "executable": "opencode"},
)

# 探测并发度。串行探测 19 条实测 48.4s，超出前端该请求的超时；并行后进到 10s
# 量级。不再往上压：并发越高，.CMD 包装器偶发拿不到输出（version 与 error 双空）
# 的概率越大，重试只能兜住一部分。
_PROBE_WORKERS = 6


def cache_path(data_dir):
    return Path(data_dir) / "cli_capabilities.json"


_TRACEBACK_HEAD = "Traceback (most recent call last)"


def _version_text(text):
    """把版本命令的输出拆成 (version, error)。

    CLI 自身启动失败时会往 stderr 打 Python traceback，首行恒为
    "Traceback (most recent call last):"。原样存进 version 会让智能体管理页
    显示「版本：Traceback (most recent call last):」，把崩溃伪装成版本号，
    也埋没了唯一有用的最后一行（如 ImportError 原因）。这里把首行让给
    version，异常输出转成 error 并只取末行摘要——调用方据此把该 CLI 标为
    不可用，而不是「已安装，版本未知」。
    """
    first = text.splitlines()[0][:200] if text else ""
    if first.startswith(_TRACEBACK_HEAD):
        reason = ""
        for line in reversed(text.splitlines()):
            if line.strip() and not line.startswith((" ", "\t")):
                reason = line.strip()[:160]
                break
        return "", "版本识别失败：CLI 启动即报错%s" % ("（%s）" % reason if reason else "")
    return first, ""


def _run_capture(path, flag, timeout):
    """跑一次版本命令：返回 (returncode, stdout, stderr, timed_out)。

    不用 ``subprocess.run(timeout=)``：在 Windows 上它**不构成墙钟上限**。版本
    命令多经 ``.CMD`` 包装器（cmd.exe → node.exe），超时后 run() 只杀掉 cmd.exe，
    而继承管道的 node.exe 仍握着 stdout/stderr 句柄，``communicate()`` 会一直等
    到它自己退出。实测 ``timeout=3`` 时 mimo-code 探了 22.8s、pi 19.6s、gemini
    17.3s —— 这就是能力面板「点刷新转圈」的真实原因，不是条目多。

    这里自己 Popen，超时交给 runner 已验证的 ``_kill_tree``（连孙进程一起杀，见
    tests/test_runner_timeout_kill.py），再给 5s 收尾。
    """
    proc = subprocess.Popen([path, flag], stdout=subprocess.PIPE,
                            stderr=subprocess.PIPE, text=True, errors="replace",
                            start_new_session=(os.name != "nt"))
    try:
        out, err = proc.communicate(timeout=timeout)
        return proc.returncode, out or "", err or "", False
    except subprocess.TimeoutExpired:
        try:
            from .runner import _kill_tree
            _kill_tree(proc.pid)      # 连孙进程：只杀 cmd.exe 会留下 node 孤儿
        except Exception:
            proc.kill()
        try:
            out, err = proc.communicate(timeout=5)
        except subprocess.TimeoutExpired:
            # 连杀树后仍有进程握着管道（极少见）：硬杀根进程并收掉句柄，
            # 别把 fd 泄漏给下一次探测。
            proc.kill()
            out, err = "", ""
            for stream in (proc.stdout, proc.stderr):
                try:
                    stream and stream.close()
                except Exception:
                    pass
        return None, out or "", err or "", True


def probe(spec, *, timeout=3, environ=None):
    spec = dict(spec or {})
    executable = spec.get("executable") or spec.get("name")
    path = shutil.which(executable, path=(environ or os.environ).get("PATH"))
    result = {"name": spec.get("name") or executable, "executable": executable,
              "path": path or "", "available": bool(path), "version": "",
              "supports_stdin": bool(spec.get("supports_stdin", True)),
              "output_format": spec.get("output_format", "text"), "error": ""}
    if not path:
        result["install_hint"] = "安装 %s 并确保其位于 PATH" % executable
        return result
    for flag in (spec.get("version_flag", "--version"), "--help"):
        try:
            code, out, err, timed_out = _run_capture(path, flag, timeout)
            text = (out or err or "").strip()
            if timed_out and not text:
                # 慢 ≠ 坏。冷启动的 node 版 CLI 实测要 7–22s 才吐版本，标成
                # 「探测异常」会让健康 CLI 背锅；单独标 slow，界面另用一套措辞。
                result["slow"] = True
                result["error"] = "版本命令 %ss 内无输出（可单独复测）" % timeout
                break
            if flag == spec.get("version_flag", "--version"):
                result["version"], result["error"] = _version_text(text)
            result["help_available"] = code == 0 or bool(text)
            if text and "json" in text.lower():
                result["output_format"] = "json"
            break
        except (OSError, subprocess.SubprocessError) as exc:
            result["error"] = str(exc)[:200]
    return result


def default_specs():
    """默认探测集：按 catalog 全集生成，不再用手写的 5 条。

    历史默认是 ``DEFAULT_CLIS``（codex/claude/qwen/aider/opencode），与 catalog 的
    19 条 ID 不同名（``codex`` vs ``codex-cli``）。能力面板因此最多显示 5 个 CLI，
    其余 14 条既不显示「可用」也不显示「未检测到」—— 是根本没被探测，而用户读到
    的是「没装」。catalog 导入失败时退回旧集合：面板不能因为目录坏了就打不开。
    """
    try:
        from . import catalog
        specs = []
        for entry in catalog.DEFAULT_CATALOG:
            entry_id = str(entry.get("id") or "").strip()
            orch = entry.get("orch") or {}
            detect = entry.get("detect") or {}
            executable = orch.get("command") or detect.get("cli") or entry_id
            if entry_id and executable:
                specs.append({"name": entry_id, "executable": executable})
        if specs:
            return specs
    except Exception:
        pass
    return [dict(spec) for spec in DEFAULT_CLIS]


def _probe_with_retry(spec, timeout, environ):
    row = probe(spec, timeout=timeout, environ=environ)
    # 批量探测 .CMD/.bat 包装器时偶尔拿不到任何输出（已复现：available=True 但
    # version 与 error 都空）。单独复测通常正常，重试一次即可——这种抖动不能被
    # 记成「装了但版本识别失败」，否则面板会凭空多出一条故障。
    if row.get("available") and not row.get("version") and not row.get("error"):
        again = probe(spec, timeout=timeout, environ=environ)
        if again.get("version") or again.get("error"):
            return again
    return row


def discover(specs=None, *, timeout=3, environ=None, workers=None):
    """并行探测，**保持传入顺序**。

    顺序必须稳定：缓存文件与界面列表都按它排，顺序跳动会让用户以为 CLI 变了。
    """
    specs = list(specs) if specs is not None else default_specs()
    if not specs:
        return []
    try:
        width = int(workers if workers is not None else _PROBE_WORKERS)
    except (TypeError, ValueError):
        width = _PROBE_WORKERS
    width = max(1, min(width, len(specs)))
    if width == 1:
        return [_probe_with_retry(spec, timeout, environ) for spec in specs]
    from concurrent.futures import ThreadPoolExecutor
    with ThreadPoolExecutor(max_workers=width, thread_name_prefix="cli-probe") as pool:
        return list(pool.map(lambda spec: _probe_with_retry(spec, timeout, environ), specs))


def refresh(path, specs=None, *, timeout=3, environ=None):
    """Probe configured CLIs and atomically write the cache."""
    rows = discover(specs=specs, timeout=timeout, environ=environ)
    target = Path(path)
    atomic.write_json(target, {"clis": rows})
    return rows


def save(results, path):
    atomic.write_json(Path(path), {"clis": results})


def load(path):
    try:
        data = json.loads(Path(path).read_text(encoding="utf-8"))
        return data.get("clis", []) if isinstance(data, dict) else []
    except (OSError, ValueError):
        return []
