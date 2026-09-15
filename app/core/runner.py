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

from .env_scrub import scrub_env
from .error_codes import ErrorCode

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


def _drain_streams(proc, t_out, t_err, *, timeout=10):
    """kill_tree 后等管道线程读完剩余字节。

    daemon=True 线程 join 超时即结束（不会卡死主流程）。
    设计稿：docs/migration/01-defense-patterns.md §5B。
    参考 dsh docs/defensive-patterns.zh.md 第 21 行（"dispose 必须达到完全停稳"）。
    """
    deadline = time.time() + timeout
    for t in (t_out, t_err):
        remaining = max(0.0, deadline - time.time())
        if remaining > 0:
            t.join(timeout=remaining)


def _pipe_reader(stream, chunks, log_fh):
    """持续读子进程管道并实时落盘。

    必须用 read1()：BufferedReader.read(n) 会阻塞到凑满 n 字节或 EOF，
    在长命令（npm 安装等）上等于"进程结束才一次性返回"，日志面板全程空白。
    read1() 只要有数据就返回，日志才能真正边跑边看。
    """
    while True:
        b = stream.read1(65536)
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


def tail_decoded(data, tail):
    """按字节取尾部再解码；切片可能落在 UTF-8 多字节字符中间，先丢弃开头的
    continuation 字节（10xxxxxx，至多 3 个）对齐字符边界，否则残缺字节会被
    GBK 回退解码成乱码字符。"""
    if not data:
        return ""
    chunk = data[-tail:] if tail and len(data) > tail else data
    i = 0
    while i < len(chunk) and i < 3 and (chunk[i] & 0xC0) == 0x80:
        i += 1
    return decode_output(chunk[i:])


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
    full_env = scrub_env(os.environ.copy(), mode="drop")
    if env:
        # 5A：env 关键字环境变量注入用户传入的 env（属于有意注入，例如模型 API key）
        full_env.update({str(k): str(v) for k, v in env.items()})
    log_fh = open(log_path, "ab") if log_path else None
    # 审计：调用下达前先把「执行的命令 + 发给智能体的指令原文」写进日志，
    # 运行中点开步骤就能看到"编排者下了什么令"，不用等结束猜。
    # env 绝不写（含 API key）；argv 里只有 base_url/env_key 名，无密钥值。
    # 指令超 12000 字符截断（章节正文可能很长），标注原始长度防误读。
    if log_fh:
        try:
            head = ["===== 下达 %s =====" % time.strftime("%Y-%m-%d %H:%M:%S"),
                    "$ " + " ".join(str(a) for a in argv)]
            if stdin_text:
                capped = stdin_text[:12000]
                head.append("--- 指令（%d 字符%s）---" % (
                    len(stdin_text), "，已截断" if len(stdin_text) > 12000 else ""))
                head.append(capped)
            log_fh.write(("\n".join(head) + "\n--- 输出 ---\n").encode("utf-8", "replace"))
            log_fh.flush()
        except Exception:
            pass
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
                _drain_streams(proc, t_out, t_err, timeout=10)
                break
            if time.time() - start > timeout:
                timed_out = True
                _kill_tree(proc.pid)
                _drain_streams(proc, t_out, t_err, timeout=5)
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
    """解析 codex exec JSONL 事件流。返回 (text, usage, sid)。

    usage 细分来自 turn.completed：input_tokens（含 cached）、cached_input_tokens、
    output_tokens、reasoning_output_tokens；多 turn 累加。
    sid 来自 thread.started 的 thread_id（§07 T1.1：供后续 revise/fix 复用会话，
    会话内前缀按供应商缓存读计价）。
    """
    text = ""
    sid = ""
    usage = {"input": 0, "output": 0, "cached": 0, "reasoning": 0, "total": 0}
    for line in stdout.splitlines():
        line = line.strip()
        if not line.startswith("{"):
            continue
        try:
            ev = json.loads(line)
        except Exception:
            continue
        if ev.get("type") == "thread.started":
            sid = str(ev.get("thread_id") or sid)
        elif ev.get("type") == "item.completed":
            item = ev.get("item") or {}
            if item.get("type") == "agent_message" and item.get("text"):
                text = item["text"]
        elif ev.get("type") == "turn.completed":
            u = ev.get("usage") or {}
            inp = int(u.get("input_tokens") or 0)
            out = int(u.get("output_tokens") or 0)
            usage["input"] += inp
            usage["output"] += out
            usage["cached"] += int(u.get("cached_input_tokens") or 0)
            usage["reasoning"] += int(u.get("reasoning_output_tokens") or 0)
            usage["total"] += (u.get("total_tokens") if u.get("total_tokens") is not None
                               else inp + out)
    return text, usage, sid


def _parse_claude_json(stdout):
    try:
        data = json.loads(stdout)
    except Exception:
        return None
    if not isinstance(data, dict):
        return None
    u = data.get("usage") or {}
    inp = int(u.get("input_tokens") or 0)
    out = int(u.get("output_tokens") or 0)
    # 缓存读 + 缓存写都计入 cached（读是省钱的部分，写是额外消耗的部分）
    cached = int(u.get("cache_read_input_tokens") or 0) + \
        int(u.get("cache_creation_input_tokens") or 0)
    usage = {"input": inp, "output": out, "cached": cached, "reasoning": 0,
             "total": inp + out + cached}
    return {
        "text": data.get("result") or "",
        "cost_usd": data.get("total_cost_usd") or 0.0,
        "usage": usage,
        "tokens": usage["total"],
        "is_error": bool(data.get("is_error")),
        # §07 T1.1：claude -p 返回本次会话 id，供 --resume 复用
        "sid": str(data.get("session_id") or ""),
    }


def _codex_provider_args(cp):
    """把外部供应商注入为一次性的 codex model_provider（-c 覆盖，不改配置文件）。"""
    name = cp.get("name", "orch")
    return ["-c", 'model_provider="%s"' % name,
            "-c", 'model_providers.%s.name="%s"' % (name, name),
            "-c", 'model_providers.%s.base_url="%s"' % (name, cp.get("base_url", "")),
            "-c", 'model_providers.%s.env_key="%s"' % (name, cp.get("env_key", "ORCH_API_KEY")),
            "-c", 'model_providers.%s.wire_api="%s"' % (name, cp.get("wire_api", "responses"))]


def _model_flag(kind, model):
    if kind in ("codex", "qwen"):
        return ["-m", model]
    return ["--model", model]  # claude / opencode / aider


_TRANSIENT = ("503", "502", "529", "429", "no available channel", "temporarily",
              "unavailable", "overloaded", "rate limit", "timeout", "timed out",
              # 2026-09-15 连载验收实测：网关故障形态远不止 HTTP 5xx——
              # Z.ai 报 "400 [1211] Unknown Model"（模型临时下架）、qwen 连本地
              # 端点 ECONNREFUSED、codex initialize 空响应，这些都被旧表判成
              # 「非瞬态不降级」，导致跨厂商链上健康的后继模型从未被尝试。
              "unknown model", "1211", "connection error", "econnrefused",
              "connection aborted", "initialize", "reset by peer",
              "channel is closed", "no route to host")


def _transient_error(err):
    err = (err or "").lower()
    return any(k in err for k in _TRANSIENT)


def _classify_failure(res, *, parsed=None, kind="", attempt_done=False, empty_output=False):
    """根据 run_process 结果 + 解析结果，返回 ErrorCode 字符串。

    设计稿：docs/migration/01-defense-patterns.md §5D + §2D。

    Args:
        res: run_process 返回的 dict（含 ok/cancelled/timed_out/exit_code/stdout/stderr）
        parsed: claude 解析后的 dict（或 None）；codex 由 caller 处理
        kind: "claude" | "codex" | "generic" | ...
        attempt_done: claude 空响应是否已重试一次（True=已是第二次）
        empty_output: 调用方已确认 stdout 为空（用于 generic/codex 的空响应）

    Returns:
        ErrorCode 枚举值字符串；成功返回 ""。
    """
    if res.get("cancelled"):
        return ErrorCode.CANCELLED
    if res.get("timed_out"):
        return ErrorCode.TIMEOUT
    # claude 解析失败（进程 ok 但 JSON 不可解析）
    if kind == "claude" and parsed is None:
        return ErrorCode.PARSE_FAIL
    # claude 明确 is_error
    if parsed and parsed.get("is_error"):
        return ErrorCode.VENDOR_REFUSAL
    # claude 两次都空
    if kind == "claude" and attempt_done and not (parsed or {}).get("text", "").strip():
        return ErrorCode.EMPTY
    # generic/codex 空输出（caller 已确认）
    if empty_output and not parsed:
        return ErrorCode.EMPTY
    # 退出码非 0（vendor 内部崩溃）
    ec = res.get("exit_code")
    if ec is not None and ec != 0:
        return ErrorCode.VENDOR_ERROR
    return ""


def _resolve_attempts(agent):
    """把 agent 的模型配置展开为逐次尝试列表。

    优先用跨厂商链 call_chain（每条自带 env / codex_provider，来自不同供应商）；
    无链时退回 model + model_fallbacks（同一 CLI 进程内换 -m，沿用 agent 级注入）。
    """
    chain = agent.get("call_chain") or []
    if chain:
        return [{"model": (e.get("model") or "").strip() or None,
                 "env": dict(e.get("env") or {}),
                 "from_chain": True,
                 "own_cp": "codex_provider" in e,
                 "codex_provider": e.get("codex_provider")} for e in chain]
    base_model = agent.get("model")
    fb = [m for m in (agent.get("model_fallbacks") or []) if m and m != base_model]
    models_to_try = ([base_model] if base_model else []) + fb
    return [{"model": m or None, "env": {}, "from_chain": False, "own_cp": False,
             "codex_provider": None}
            for m in (models_to_try or [None])[:3]]


def _build_call(agent, kind, sid, readonly, model, prompt, images=None):
    """构建一次 CLI 调用的 (argv, stdin_text, prompt)。model 可为 None=CLI 默认。
    images 为图片附件绝对路径：codex 用 -i 原生附图；其余 kind 忽略（调用方已过滤）。"""
    env = {}
    argv = None
    stdin_text = None
    imgs = [str(p) for p in (images or []) if p]
    if kind == "codex":
        cp = agent.get("codex_provider")
        if sid:
            # resume 子命令不支持 -s 也不支持 --full-auto（0.154 实测：
            # "unexpected argument '--full-auto'"）——读/写模式都用 -c sandbox_mode
            argv = resolve_command(agent["command"]) + [
                "exec", "resume", sid, "-",
                "--skip-git-repo-check", "--json"]
            if model:
                argv += ["-m", model]
            argv += ["-c", 'sandbox_mode="%s"' %
                     ("read-only" if readonly else "workspace-write")]
            if cp:
                argv += _codex_provider_args(cp)
        else:
            argv = resolve_command(agent["command"]) + [
                "exec", "--skip-git-repo-check", "--json",
                "-s", "read-only" if readonly else "workspace-write"]
            if model:
                argv += ["-m", model]
            if cp:
                argv += _codex_provider_args(cp)
        # codex exec 与 exec resume 都支持 -i：图片直接附到 prompt（exec resume 的
        # -i 附在恢复后发送的首条消息上，即本次 stdin prompt）
        for p in imgs:
            argv += ["-i", p]
        stdin_text = prompt
    elif kind == "claude":
        argv = resolve_command(agent["command"]) + ["-p", "--output-format", "json"]
        if sid:
            argv += ["--resume", sid]
        if model:
            argv += ["--model", model]
        if readonly:
            # 实测本机自定义网关在 -p 模式下工具续接会丢最终结果（用工具必空）。
            # 评审/规划所需的上下文已内嵌在提示词中，显式禁用工具最稳。
            prompt = "（请勿使用任何工具，直接依据下方内容回答。）\n\n" + prompt
        else:
            argv += ["--permission-mode", "acceptEdits"]
        stdin_text = prompt
    elif kind == "opencode":
        argv = resolve_command(agent["command"]) + ["run"]
        if sid:
            argv += ["-s", sid]  # 无头续会话：-s 指定会话 id（-c 只能接最近一次）
        if model:
            argv += ["--model", model]
        stdin_text = prompt  # 无位置参数且 stdin 有内容时读 stdin
    elif kind == "qwen":  # gemini-cli 系：无参数且 stdin 有内容时读 stdin
        argv = resolve_command(agent["command"])
        if sid:
            argv += ["-r", sid]  # 恢复指定会话（~/.qwen/projects/*/chats/<sid>.jsonl）
        if model:
            argv += ["-m", model]
        stdin_text = prompt
    elif kind == "aider":
        argv = resolve_command(agent["command"]) + [
            "--yes-always", "--no-auto-commits", "--no-check-update", "--message", prompt]
        if model:
            argv += ["--model", model]
    else:  # generic：模板把 {prompt}/{session} 嵌进参数（注意 cmd 行长度限制）
        tmpl = agent.get("argv_template") or ["-p", "{prompt}"]
        if sid and agent.get("resume_argv_template"):
            tmpl = agent["resume_argv_template"]
        argv = resolve_command(agent["command"]) + [
            str(a).replace("{prompt}", prompt).replace("{session}", sid) for a in tmpl]
        if "{prompt}" not in tmpl:
            stdin_text = prompt  # 恢复模板不带 {prompt}：提示词走 stdin（mimo 实测支持）
    return argv, stdin_text, prompt


def _check_approval(agent):
    """5G：catalog entry sensitive + policy=NEVER → 拒绝（无人值守不静默降级）。

    Policy 来源：环境变量 TUTTI_APPROVAL_POLICY（默认 "never"）。
    sensitive 字段从 catalog orch.sensitive 读取。

    Returns:
        (True, "") 表示放行；
        (False, reason) 表示拒绝（error_code=ENV_BLOCK）。
    """
    policy = os.environ.get("TUTTI_APPROVAL_POLICY", "never").strip().lower()
    sensitive = bool((agent.get("orch") or {}).get("sensitive"))
    if not sensitive:
        return True, ""
    if policy == "never":
        return False, "sensitive step blocked by policy=never (set TUTTI_APPROVAL_POLICY=ask to require explicit approval)"
    # policy=ask 留 Phase 5 做完整 UI 弹窗流程；当前等价 never
    return False, "sensitive step requires policy=ask UI flow (Phase 5) — currently blocking"


def run_agent(agent, prompt, workdir=None, readonly=True,
              timeout=DEFAULT_TIMEOUT, cancel_event=None, log_path=None, resume=None,
              images=None):
    """执行一次智能体调用，返回统一结构
    {ok, text, json, cost_usd, tokens, error, error_code, raw}。
    agent 来自 registry.effective_agents()；resume 为已有会话 id，仅真实智能体生效
    （codex: exec resume；claude: --resume；opencode/mimo: run -s；qwen: -r；
    generic: catalog orch.resume_argv_template）。
    images：任务图片附件的绝对路径，仅 codex 原生支持（-i）；其余智能体靠
    提示词里的 _attachments/ 路径 + 自身读文件能力获取，无读图工具时静默忽略。

    模型尝试顺序来自 _resolve_attempts：跨厂商链（每条独立 env）或
    主模型 + 降级备选；瞬态错误才换下一条，取消/超时/解析失败不降级。

    5E：catalog `orch.timeout_ms`（毫秒）优先于 caller 传入的 timeout。
    """
    # 5E：catalog orch.timeout_ms 优先
    orch_timeout_ms = (agent.get("orch") or {}).get("timeout_ms")
    if orch_timeout_ms:
        timeout = float(orch_timeout_ms) / 1000.0
    kind = agent.get("kind", "generic")
    # 5G：approval NEVER 一线（无人值守不静默降级）
    ok, reason = _check_approval(agent)
    if not ok:
        return {"ok": False, "text": "", "json": None, "cost_usd": 0.0, "tokens": 0,
                "usage": None, "error": reason,
                "error_code": ErrorCode.ENV_BLOCK,
                "raw": None, "kind": kind, "model": None}
    base_env = dict(agent.get("env") or {})
    if kind == "claude":
        bash = find_git_bash()
        if bash:
            base_env.setdefault("CLAUDE_CODE_GIT_BASH_PATH", bash)
        base_env.setdefault("CLAUDE_CODE_MAX_OUTPUT_TOKENS", "16000")
    sid = (resume or "").strip() if agent.get("mode") == "real" else ""
    if sid and kind == "generic" and not agent.get("resume_argv_template"):
        return {"ok": False, "text": "", "json": None, "cost_usd": 0.0, "tokens": 0,
                "usage": None,
                "error": "该 CLI 未配置会话恢复（catalog orch.resume_argv_template），"
                         "无法在已有会话上继续",
                "error_code": ErrorCode.VENDOR_ERROR,
                "sid": "", "raw": None, "kind": kind, "model": None}

    attempts = _resolve_attempts(agent)
    out = None
    for ai, att in enumerate(attempts):
        env = dict(base_env)
        env.update(att["env"])
        eff_agent = dict(agent)
        eff_agent["env"] = env
        if att["own_cp"]:
            eff_agent["codex_provider"] = att["codex_provider"]
        elif att["from_chain"] and "codex_provider" in eff_agent:
            # 链内条目未注入供应商时不能沿用上一条（可能是另一家厂商）的 -c 覆盖
            del eff_agent["codex_provider"]
        for attempt in range(2):  # claude 偶发空响应（0 token）自动重试一次
            argv, stdin_text, prompt_eff = _build_call(eff_agent, kind, sid, readonly,
                                                       att["model"], prompt,
                                                       images=images if kind == "codex" else None)
            res = run_process(argv=argv, stdin_text=stdin_text, cwd=workdir, env=env,
                              timeout=timeout, cancel_event=cancel_event, log_path=log_path)
            out = {"ok": res["ok"], "text": "", "json": None, "cost_usd": 0.0,
                   "tokens": 0, "usage": None, "error": "", "error_code": "",
                   "sid": "", "raw": res, "kind": kind, "model": att["model"]}
            if not res["ok"]:
                tail = (res["stderr"] or res["stdout"] or "")[-500:]
                out["error"] = (("超时" if res["timed_out"] else "取消" if res["cancelled"]
                                 else "退出码 %s" % res["exit_code"])
                                + ("；stderr/stdout: " + tail if tail else ""))
                # 5D+2D：错误码归一
                if kind == "claude":
                    parsed_err = _parse_claude_json(res["stdout"] or "")
                    if parsed_err and parsed_err.get("is_error") and parsed_err.get("text"):
                        out["error"] = "claude 返回 is_error: " + parsed_err["text"][:500]
                out["error_code"] = _classify_failure(res, kind=kind)
                break
            if kind == "codex":
                out["text"], out["usage"], out_sid = _parse_codex_jsonl(res["stdout"])
                out["sid"] = out_sid  # §07 T1.1：会话 id 供 revise/fix 复用
                out["tokens"] = out["usage"]["total"]
                if not out["text"]:  # 事件流解析失败时退化为取 stdout 尾部
                    out["text"] = res["stdout"][-2000:]
                if not out["text"]:
                    out["error_code"] = _classify_failure(res, kind="codex", empty_output=True)
            elif kind == "claude":
                parsed = _parse_claude_json(res["stdout"])
                if parsed is None:
                    out["ok"] = False
                    out["error"] = "claude 输出无法解析为 JSON；stdout 尾部: " + res["stdout"][-500:]
                    out["error_code"] = _classify_failure(res, kind="claude", parsed=None)
                    break
                out["text"] = parsed["text"]
                out["cost_usd"] = parsed["cost_usd"]
                out["tokens"] = parsed["tokens"]
                out["usage"] = parsed["usage"]
                out["sid"] = parsed.get("sid") or ""  # §07 T1.1
                if parsed["is_error"]:
                    out["ok"] = False
                    out["error"] = "claude 返回 is_error: " + parsed["text"][:500]
                    out["error_code"] = _classify_failure(res, kind="claude", parsed=parsed)
                    break
                if not out["text"] and attempt == 0:
                    continue  # 空响应，同模型重试
                if not out["text"] and attempt == 1:
                    out["error_code"] = _classify_failure(
                        res, kind="claude", parsed=parsed, attempt_done=True)
            else:
                out["text"] = res["stdout"].strip()
                if not out["text"]:
                    out["error_code"] = _classify_failure(res, kind=kind, empty_output=True)
            break
        if out["ok"] or ai == len(attempts) - 1:
            return out
        if not _transient_error(out.get("error")):
            return out  # 非瞬态（取消/超时/解析失败）不降级
        # 瞬态错误 → 换下一条（可能是另一个厂商的模型）
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
