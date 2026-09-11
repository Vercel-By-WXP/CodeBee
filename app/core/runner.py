# -*- coding: utf-8 -*-
"""运行器：把各家 CLI 的无头调用统一成一个接口。

实测验证过的接入方式（2026-09，本机）：
  codex  : codex exec --skip-git-repo-check --json -s <sandbox> [prompt从stdin]
           stdout 为 JSONL 事件流；item.completed(type=agent_message)=最终回答，
           turn.completed=token 用量。pnpm 的 .cmd 垫片需 cmd /c 包装。
  claude : claude -p --output-format json [prompt从stdin]
           stdout 为单个 JSON：result / total_cost_usd / usage。
           需环境变量 CLAUDE_CODE_GIT_BASH_PATH（原生 exe 找不到 bash 会拒绝启动）
           与 CLAUDE_CODE_MAX_OUTPUT_TOKENS（自定义网关模型常见 32768 上限）。
"""
from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import threading
import time

CREATE_NO_WINDOW = 0x08000000
DEFAULT_TIMEOUT = 1200  # 单步 20 分钟

_BASH_CANDIDATES = [
    r"D:\Git\usr\bin\bash.exe",
    r"C:\Program Files\Git\usr\bin\bash.exe",
    r"C:\Program Files (x86)\Git\usr\bin\bash.exe",
]
_bash_cache = {"path": None, "done": False}


def find_git_bash():
    if _bash_cache["done"]:
        return _bash_cache["path"]
    _bash_cache["done"] = True
    p = shutil.which("bash.exe") or shutil.which("bash")
    if p:
        _bash_cache["path"] = p
        return p
    for cand in _BASH_CANDIDATES:
        if os.path.isfile(cand):
            _bash_cache["path"] = cand
            return cand
    return None


def resolve_command(command):
    """命令名 → 可直接 spawn 的 argv 前缀（.cmd/.bat 垫片必须经 cmd /c）。"""
    path = shutil.which(command) or command
    low = path.lower()
    if low.endswith(".cmd") or low.endswith(".bat"):
        return ["cmd", "/c", path]
    return [path]


def _kill_tree(pid):
    try:
        subprocess.run(
            ["taskkill", "/F", "/T", "/PID", str(pid)],
            capture_output=True, creationflags=CREATE_NO_WINDOW, timeout=15)
    except Exception:
        pass


def _pipe_reader(stream, chunks, log_fh):
    while True:
        b = stream.read(65536)
        if not b:
            break
        chunks.append(b)
        if log_fh:
            try:
                log_fh.write(b)
                log_fh.flush()
            except Exception:
                pass


def decode_output(data):
    """子进程输出解码：UTF-8 严格解码失败时回退 GBK（中文 Windows 控制台）。"""
    if not data:
        return ""
    try:
        return data.decode("utf-8")
    except UnicodeDecodeError:
        pass
    try:
        return data.decode("gbk")
    except UnicodeDecodeError:
        return data.decode("utf-8", "replace")


def run_process(argv=None, shell_cmd=None, stdin_text=None, cwd=None, env=None,
                timeout=DEFAULT_TIMEOUT, cancel_event=None, log_path=None):
    """通用子进程执行：并发读管道防死锁；超时/取消杀整棵进程树。

    返回 {ok, exit_code, stdout, stderr, duration, cancelled, timed_out}。
    """
    if shell_cmd:
        argv = ["cmd", "/c", shell_cmd]
    if argv is None:
        return {"ok": False, "exit_code": None, "stdout": "", "stderr": "argv 为空",
                "duration": 0.0, "cancelled": False, "timed_out": False}
    full_env = os.environ.copy()
    if env:
        full_env.update({str(k): str(v) for k, v in env.items()})
    log_fh = open(log_path, "ab") if log_path else None
    try:
        try:
            proc = subprocess.Popen(
                [str(a) for a in argv], cwd=cwd, env=full_env,
                stdin=subprocess.PIPE if stdin_text is not None else subprocess.DEVNULL,
                stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                creationflags=CREATE_NO_WINDOW)
        except Exception as e:
            return {"ok": False, "exit_code": None, "stdout": "",
                    "stderr": "启动失败: %r" % e, "duration": 0.0,
                    "cancelled": False, "timed_out": False}
        out_chunks, err_chunks = [], []
        t_out = threading.Thread(target=_pipe_reader, args=(proc.stdout, out_chunks, log_fh), daemon=True)
        t_err = threading.Thread(target=_pipe_reader, args=(proc.stderr, err_chunks, log_fh), daemon=True)
        t_out.start()
        t_err.start()
        if stdin_text is not None:
            def _feed():
                try:
                    proc.stdin.write(stdin_text.encode("utf-8"))
                except Exception:
                    pass
                finally:
                    try:
                        proc.stdin.close()
                    except Exception:
                        pass
            threading.Thread(target=_feed, daemon=True).start()
        start = time.time()
        cancelled = timed_out = False
        while True:
            try:
                proc.wait(timeout=0.4)
                break
            except subprocess.TimeoutExpired:
                pass
            if cancel_event is not None and cancel_event.is_set():
                cancelled = True
                _kill_tree(proc.pid)
                break
            if time.time() - start > timeout:
                timed_out = True
                _kill_tree(proc.pid)
                break
        duration = round(time.time() - start, 1)
        t_out.join(timeout=5)
        t_err.join(timeout=5)
        exit_code = proc.returncode
        stdout = decode_output(b"".join(out_chunks))
        stderr = decode_output(b"".join(err_chunks))
        for stream in (proc.stdin, proc.stdout, proc.stderr):
            try:
                if stream:
                    stream.close()
            except Exception:
                pass
        if cancelled:
            stderr += "\n[已被用户取消]"
        elif timed_out:
            stderr += "\n[超时 %ss，已终止进程树]" % timeout
        return {
            "ok": exit_code == 0 and not cancelled and not timed_out,
            "exit_code": exit_code, "stdout": stdout, "stderr": stderr,
            "duration": duration, "cancelled": cancelled, "timed_out": timed_out,
        }
    finally:
        if log_fh:
            try:
                log_fh.close()
            except Exception:
                pass


# ---------------------------------------------------------------- 各家适配

def _parse_codex_jsonl(stdout):
    text, tokens = "", 0
    for line in stdout.splitlines():
        line = line.strip()
        if not line.startswith("{"):
            continue
        try:
            ev = json.loads(line)
        except Exception:
            continue
        if ev.get("type") == "item.completed":
            item = ev.get("item") or {}
            if item.get("type") == "agent_message" and item.get("text"):
                text = item["text"]
        elif ev.get("type") == "turn.completed":
            usage = ev.get("usage") or {}
            tokens += usage.get("input_tokens", 0) + usage.get("output_tokens", 0)
    return text, tokens


def _parse_claude_json(stdout):
    try:
        data = json.loads(stdout)
    except Exception:
        return None
    if not isinstance(data, dict):
        return None
    return {
        "text": data.get("result") or "",
        "cost_usd": data.get("total_cost_usd") or 0.0,
        "tokens": ((data.get("usage") or {}).get("input_tokens", 0)
                   + (data.get("usage") or {}).get("output_tokens", 0)),
        "is_error": bool(data.get("is_error")),
    }


def _codex_provider_args(cp):
    """把外部供应商注入为一次性的 codex model_provider（-c 覆盖，不改配置文件）。"""
    name = cp.get("name", "orch")
    return ["-c", 'model_provider="%s"' % name,
            "-c", 'model_providers.%s.name="%s"' % (name, name),
            "-c", 'model_providers.%s.base_url="%s"' % (name, cp.get("base_url", "")),
            "-c", 'model_providers.%s.env_key="%s"' % (name, cp.get("env_key", "ORCH_API_KEY")),
            "-c", 'model_providers.%s.wire_api="%s"' % (name, cp.get("wire_api", "responses"))]


def run_agent(agent, prompt, workdir=None, readonly=True,
              timeout=DEFAULT_TIMEOUT, cancel_event=None, log_path=None, resume=None):
    """执行一次智能体调用，返回统一结构
    {ok, text, json, cost_usd, tokens, error, raw}。
    agent 来自 registry.effective_agents()；resume 为已有会话 id
    （codex: exec resume；claude: --resume），仅真实智能体生效。
    """
    kind = agent.get("kind", "generic")
    env = dict(agent.get("env") or {})
    argv = None
    stdin_text = None
    sid = (resume or "").strip() if agent.get("mode") == "real" else ""

    if kind == "codex":
        cp = agent.get("codex_provider")
        if sid:
            # resume 子命令不支持 -s：读模式用 -c sandbox_mode，写模式用 --full-auto
            argv = resolve_command(agent["command"]) + [
                "exec", "resume", sid, "-",
                "--skip-git-repo-check", "--json"]
            if agent.get("model"):
                argv += ["-m", agent["model"]]
            argv += (["-c", 'sandbox_mode="read-only"'] if readonly else ["--full-auto"])
            if cp:
                argv += _codex_provider_args(cp)
        else:
            argv = resolve_command(agent["command"]) + [
                "exec", "--skip-git-repo-check", "--json",
                "-s", "read-only" if readonly else "workspace-write"]
            if agent.get("model"):
                argv += ["-m", agent["model"]]
            if cp:
                argv += _codex_provider_args(cp)
        stdin_text = prompt
    elif kind == "claude":
        argv = resolve_command(agent["command"]) + ["-p", "--output-format", "json"]
        if sid:
            argv += ["--resume", sid]
        if agent.get("model"):
            argv += ["--model", agent["model"]]
        bash = find_git_bash()
        if bash:
            env.setdefault("CLAUDE_CODE_GIT_BASH_PATH", bash)
        env.setdefault("CLAUDE_CODE_MAX_OUTPUT_TOKENS", "16000")
        if readonly:
            # 实测本机自定义网关在 -p 模式下工具续接会丢最终结果（用工具必空）。
            # 评审/规划所需的上下文已内嵌在提示词中，显式禁用工具最稳。
            prompt = "（请勿使用任何工具，直接依据下方内容回答。）\n\n" + prompt
        else:
            argv += ["--permission-mode", "acceptEdits"]
        stdin_text = prompt
    elif kind == "opencode":
        argv = resolve_command(agent["command"]) + ["run"]
        if agent.get("model"):
            argv += ["--model", agent["model"]]
        stdin_text = prompt  # 版本差异待装后验证
    elif kind == "qwen":  # gemini-cli 系：无参数且 stdin 有内容时读 stdin
        argv = resolve_command(agent["command"])
        if agent.get("model"):
            argv += ["-m", agent["model"]]
        stdin_text = prompt
    elif kind == "aider":
        argv = resolve_command(agent["command"]) + [
            "--yes-always", "--no-auto-commits", "--no-check-update", "--message", prompt]
        if agent.get("model"):
            argv += ["--model", agent["model"]]
    else:  # generic：模板把 {prompt} 嵌进参数（注意 cmd 行长度限制）
        tmpl = agent.get("argv_template") or ["-p", "{prompt}"]
        argv = resolve_command(agent["command"]) + [str(a).replace("{prompt}", prompt) for a in tmpl]

    out = None
    for attempt in range(2):  # claude 偶发空响应（0 token）自动重试一次
        res = run_process(argv=argv, stdin_text=stdin_text, cwd=workdir, env=env,
                          timeout=timeout, cancel_event=cancel_event, log_path=log_path)
        out = {"ok": res["ok"], "text": "", "json": None, "cost_usd": 0.0,
               "tokens": 0, "error": "", "raw": res, "kind": kind}
        if not res["ok"]:
            out["error"] = (("超时" if res["timed_out"] else "取消" if res["cancelled"]
                             else "退出码 %s" % res["exit_code"])
                            + ("；stderr: " + res["stderr"][-500:] if res["stderr"] else ""))
            return out
        if kind == "codex":
            out["text"], out["tokens"] = _parse_codex_jsonl(res["stdout"])
            if not out["text"]:  # 事件流解析失败时退化为取 stdout 尾部
                out["text"] = res["stdout"][-2000:]
        elif kind == "claude":
            parsed = _parse_claude_json(res["stdout"])
            if parsed is None:
                out["ok"] = False
                out["error"] = "claude 输出无法解析为 JSON；stdout 尾部: " + res["stdout"][-500:]
                return out
            out["text"] = parsed["text"]
            out["cost_usd"] = parsed["cost_usd"]
            out["tokens"] = parsed["tokens"]
            if parsed["is_error"]:
                out["ok"] = False
                out["error"] = "claude 返回 is_error: " + parsed["text"][:500]
                return out
            if not out["text"] and attempt == 0:
                continue  # 空响应，重试
        else:
            out["text"] = res["stdout"].strip()
        return out
    return out


def extract_json(text):
    """从模型回复中提取 JSON：直接解析 → ```json 围栏 → 平衡花括号扫描。"""
    if not text:
        return None
    text = text.strip()
    try:
        return json.loads(text)
    except Exception:
        pass
    m = re.search(r"```(?:json)?\s*(\{.*?\})\s*```", text, re.S)
    if m:
        try:
            return json.loads(m.group(1))
        except Exception:
            pass
    start = text.find("{")
    while start != -1:
        depth = 0
        for i in range(start, len(text)):
            c = text[i]
            if c == "{":
                depth += 1
            elif c == "}":
                depth -= 1
                if depth == 0:
                    try:
                        return json.loads(text[start:i + 1])
                    except Exception:
                        break
        start = text.find("{", start + 1)
    return None
