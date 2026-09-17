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


def _pipe_reader(stream, chunks, log_fh, stamp=None):
    """持续读子进程管道并实时落盘。

    必须用 read1()：BufferedReader.read(n) 会阻塞到凑满 n 字节或 EOF，
    在长命令（npm 安装等）上等于"进程结束才一次性返回"，日志面板全程空白。
    read1() 只要有数据就返回，日志才能真正边跑边看。
    stamp：共享 [最后输出时刻]，停滞看门狗据此判定进程是否卡死。
    """
    while True:
        b = stream.read1(65536)
        if not b:
            break
        chunks.append(b)
        if stamp is not None:
            stamp[0] = time.time()
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


def read_text_any_enc(path):
    """读文本文件，编码纪律同 decode_output（UTF-8 严格 → GBK 回退 → replace 兜底）。

    工作区文件由 CLI 子代理落盘，中文 Windows 上 PowerShell Set-Content 缺省写
    GBK；消费侧（章节/diff/故事圣经）统一走本函数，不再因编码混编出 U+FFFD。
    文件不存在/读失败抛 OSError，由调用方兜底（与 open 语义一致）。"""
    with open(path, "rb") as f:
        data = f.read()
    return decode_output(data)


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


_TS_PREFIX = re.compile(r"^\d{4}-\d{2}-\d{2}T[\d:.,]+Z?\s*")


def collapse_dup_lines(text, max_group=4):
    """折叠「归一化后重复」的邻近行，保留每种首行原文并标注折叠数。

    真实案例：codex_otel 遥测对网关自定义模型名（如 [opencode]xxx，方括号是
    非法 OTel tag 字符）每个 SSE 事件刷一对 WARN——counter/duration 两种文案
    交替出现，且每条带微秒时间戳：剥掉时间戳前缀后相邻行仍不相等，必须按
    「邻近组」折叠——组内至多 max_group 个键，新键只有组满才结算上一组，
    这样交替刷屏的各个文案都归入同一组（计数各自累计）。空行先结算所在组
    再原样保留。超过组宽的零散重复（中间隔着足量新行）不受影响。
    """
    if not text:
        return ""
    out = []
    keys, first, cnt = [], {}, {}

    def _flush():
        for k in keys:
            out.append(first[k])
            if cnt[k] > 1:
                out.append("⋯（上行重复 ×%d 已折叠）" % (cnt[k] - 1))
        keys.clear()
        first.clear()
        cnt.clear()

    for ln in text.splitlines():
        k = _TS_PREFIX.sub("", ln).strip()
        if not k:
            _flush()          # 先结算扣住的组，保证空行前后顺序不失真
            out.append(ln)
            continue
        if k in cnt:
            cnt[k] += 1
        elif len(keys) >= max_group:
            _flush()
            keys.append(k)
            first[k] = ln
            cnt[k] = 1
        else:
            keys.append(k)
            first[k] = ln
            cnt[k] = 1
    _flush()
    if text.endswith("\n"):
        out.append("")
    return "\n".join(out)


def _fmt_codex_item(it, cap):
    t = it.get("type") or ""
    if t == "agent_message":
        return "【消息】" + str(it.get("text") or "")[:cap]
    if t == "reasoning":
        return "【思考】" + str(it.get("text") or "")[:cap]
    if t == "command_execution":
        line = "【命令】%s（退出码 %s）" % (it.get("command") or "?", it.get("exit_code", "?"))
        outp = str(it.get("aggregated_output") or "").strip()
        if outp:
            line += "\n  | " + outp[:600].replace("\n", "\n  | ")
        return line
    if t == "file_change":
        names = ", ".join(str(c.get("path") or "?") for c in (it.get("changes") or [])
                          if isinstance(c, dict))
        return "【文件改动】" + (names or "?")
    if t == "mcp_tool_call":
        return "【工具】%s %s" % (it.get("tool") or "?",
                                  json.dumps(it.get("arguments") or "", ensure_ascii=False)[:200])
    if t == "web_search":
        return "【搜索】" + str(it.get("query") or "")
    if t == "error":
        return "【错误】" + str(it.get("message") or "")
    return None


def pretty_cli_log(text, max_event_chars=4000):
    """codex --json 的 JSONL 事件流 → 人类可读行；非 JSONL 行原样保留。

    --json 模式下 stdout 全是机器事件（thread/turn/item…），智能体真正在说的
    话被埋在转义 JSON 里；这里逐行翻译成【消息】【思考】【命令】，让日志抽屉
    读到的是「蜂在干什么」。解析失败或未识别的事件类型原样保留，不吞内容。
    """
    out = []
    for ln in (text or "").splitlines():
        s = ln.strip()
        ev = None
        if s.startswith("{") and s.endswith("}"):
            try:
                ev = json.loads(s)
            except Exception:
                ev = None
        if not isinstance(ev, dict):
            out.append(ln)
            continue
        typ = ev.get("type") or ""
        if typ in ("thread.started", "turn.started"):
            continue
        if typ == "turn.completed":
            u = ev.get("usage") or {}
            out.append("— 一轮完成（tokens 入 %s / 出 %s）—" % (
                u.get("input_tokens", "?"), u.get("output_tokens", "?")))
            continue
        if typ == "turn.failed":
            e = ev.get("error")
            out.append("— 一轮失败：%s —" % (e.get("message") if isinstance(e, dict) else e))
            continue
        if typ in ("item.completed", "item.started", "item.updated"):
            if typ != "item.completed" or not isinstance(ev.get("item"), dict):
                continue  # started/updated 是过程噪音，completed 才有内容
            line = _fmt_codex_item(ev["item"], max_event_chars)
            if line:
                out.append(line)
            continue
        out.append(ln)
    return "\n".join(out)


def run_process(argv=None, shell_cmd=None, stdin_text=None, cwd=None, env=None,
                timeout=DEFAULT_TIMEOUT, cancel_event=None, log_path=None,
                stall_timeout=0):
    """通用子进程执行：并发读管道防死锁；超时/取消杀整棵进程树。

    stall_timeout：停滞看门狗（秒，0=关闭）——超过该时长 stdout/stderr 无任何
    新输出即判卡死，提前杀树返回（timed_out=True + stalled=True）。只对输出
    持续流动的 CLI 开（codex JSONL 事件流）；claude json 到结束才一次性输出，
    开了会把正常长任务误杀。

    返回 {ok, exit_code, stdout, stderr, duration, cancelled, timed_out, stalled}。
    """
    if shell_cmd:
        argv = ["cmd", "/c", shell_cmd]
    if argv is None:
        return {"ok": False, "exit_code": None, "stdout": "", "stderr": "argv 为空",
                "duration": 0.0, "cancelled": False, "timed_out": False, "stalled": False}
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
        stamp = [time.time()]   # 最后输出时刻（两条管道共同刷新）
        t_out = threading.Thread(target=_pipe_reader, args=(proc.stdout, out_chunks, log_fh, stamp), daemon=True)
        t_err = threading.Thread(target=_pipe_reader, args=(proc.stderr, err_chunks, log_fh, stamp), daemon=True)
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
        cancelled = timed_out = stalled = False
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
            if stall_timeout and time.time() - stamp[0] > stall_timeout:
                # 停滞看门狗：长静默多为卡死（网关挂起/CLI 假死），与其耗满总
                # 超时不如提前杀——错误按超时归类，走既有的换模型/换将链路
                stalled = True
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
        elif stalled:
            stderr += "\n[输出停滞 %ss，已终止进程树]" % stall_timeout
        elif timed_out:
            stderr += "\n[超时 %ss，已终止进程树]" % timeout
        return {
            "ok": exit_code == 0 and not cancelled and not timed_out,
            "exit_code": exit_code, "stdout": stdout, "stderr": stderr,
            "duration": duration, "cancelled": cancelled, "timed_out": timed_out,
            "stalled": stalled,
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


def _codex_work_events(stdout):
    """统计本轮事件流里真实「动了手」的事件数（命令执行/文件改动/MCP 工具）。

    实现步空转闸的客观判据：2026-09-17 mo-so 实现步模型一条命令都没发，纯口头
    谎报「进程执行被策略拦截」却 exit 0 + 一段像模像样的总结——text 看不出敷衍，
    command_execution 计数为 0 才是硬证据。"""
    n = 0
    for line in stdout.splitlines():
        line = line.strip()
        if not line.startswith("{"):
            continue
        try:
            ev = json.loads(line)
        except Exception:
            continue
        if ev.get("type") == "item.completed" and (
                ev.get("item") or {}).get("type") in (
                "command_execution", "file_change", "mcp_tool_call"):
            n += 1
    return n


def _codex_fail_msg(stdout):
    """从 JSONL 事件流提取终态失败消息；无失败返回 ""。

    2026-09-16 实测：配额/限流只出现在 error 与 turn.failed 事件里，旧解析器
    两者都丢——进程退出码非 0 时错误串里只剩 "Reading prompt from stdin..."，
    _quota_error/_transient_error 判不出可降级，健康后继模型从未被尝试。
    Reconnecting... 是 CLI 内部重试噪音（可能自愈），不取；turn.failed 是
    终态优先于裸 error（后者取最后一条兜底）。
    """
    terminal, last_err = "", ""
    for line in (stdout or "").splitlines():
        line = line.strip()
        if not line.startswith("{"):
            continue
        try:
            ev = json.loads(line)
        except Exception:
            continue
        t = ev.get("type")
        if t == "turn.failed":
            e = ev.get("error")
            terminal = e.get("message") if isinstance(e, dict) else str(e or "")
        elif t == "error":
            m = str(ev.get("message") or "")
            if m and "reconnecting" not in m.lower():
                last_err = m
    return terminal or last_err


def _parse_claude_json(stdout):
    data = None
    try:
        data = json.loads(stdout)
    except Exception:
        # stream-json：逐行事件取最后一条 type=="result"（与旧单 JSON 同构）
        for line in stdout.splitlines():
            line = line.strip()
            if not line.startswith("{"):
                continue
            try:
                ev = json.loads(line)
            except Exception:
                continue
            if isinstance(ev, dict) and ev.get("type") == "result":
                data = ev
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
              "输出停滞",
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


# 欠费/配额耗尽：换 KEY 与换厂商都该继续（同厂商另一账号往往还能用）。
# 与 modelhub._QUOTA_HINTS 同源，这里独立一份避免 core 模块间循环依赖。
_QUOTA = ("insufficient", "quota", "balance", "credit", "billing", "arrears",
          "payment required", "402", "欠费", "余额", "额度", "exceeded")


def _quota_error(err):
    err = (err or "").lower()
    return any(k in err for k in _QUOTA)


def _report_key(att, out):
    """把这次尝试的结果回写到 KEY 账本：欠费/失败 → 冷却，成功 → 清错误。

    回写失败绝不能影响主流程（账本是旁路），所以整体吞异常。
    """
    pid, kid = att.get("provider_id") or "", att.get("key_id") or ""
    if not (pid and kid):
        return
    try:
        from . import modelhub
        if out.get("ok"):
            modelhub.note_key_ok(pid, kid)
        elif out.get("error"):
            modelhub.note_key_error(pid, kid, out["error"])
    except Exception:
        pass


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

    链条目可能带 key_id/provider_id（同一厂商多把 KEY 展开成的多条）——失败时
    据此把「哪把 KEY 不行」回写冷却，后续解析自动切备用。
    """
    chain = agent.get("call_chain") or []
    if chain:
        return [{"model": (e.get("model") or "").strip() or None,
                 "env": dict(e.get("env") or {}),
                 "from_chain": True,
                 "own_cp": "codex_provider" in e,
                 "codex_provider": e.get("codex_provider"),
                 "provider_id": e.get("provider_id") or "",
                 "key_id": e.get("key_id") or ""} for e in chain]
    base_model = agent.get("model")
    fb = [m for m in (agent.get("model_fallbacks") or []) if m and m != base_model]
    models_to_try = ([base_model] if base_model else []) + fb
    return [{"model": m or None, "env": {}, "from_chain": False, "own_cp": False,
             "codex_provider": None, "provider_id": "", "key_id": ""}
            for m in (models_to_try or [None])[:3]]


def _codex_sandbox(readonly):
    """codex 沙箱档位。写步默认 danger-full-access（2026-09-17 用户拍板「默认给全部
    权限」）：workspace-write 禁网+禁盘外写，模型偶发还会误判沙箱受限、谎报
    「进程执行被策略拦截」直接躺平（mo-so BUG#27596 实测，沙箱探针证明命令本可跑）。
    只读步（评审/规划）仍 read-only——评审者可写会污染 git diff 裁决。
    env TUTTI_CODEX_SANDBOX 可钉死某档（workspace-write / read-only / danger-full-access）。"""
    if readonly:
        return "read-only"
    env = os.environ.get("TUTTI_CODEX_SANDBOX", "").strip().lower()
    if env in ("workspace-write", "read-only", "danger-full-access"):
        return env
    return "danger-full-access"


def _build_call(agent, kind, sid, readonly, model, prompt, images=None, workdir=None):
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
            argv += ["-c", 'sandbox_mode="%s"' % _codex_sandbox(readonly)]
            if cp:
                argv += _codex_provider_args(cp)
        else:
            argv = resolve_command(agent["command"]) + [
                "exec", "--skip-git-repo-check", "--json",
                "-s", _codex_sandbox(readonly)]
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
        # stream-json（2026-09-17 起）：逐行事件边跑边吐——停滞看门狗有得看，
        # 步骤日志实时可读；result 末行与旧单 JSON 同构，解析器双形态兼容
        argv = resolve_command(agent["command"]) + [
            "-p", "--output-format", "stream-json", "--verbose"]
        if sid:
            argv += ["--resume", sid]
        if model:
            argv += ["--model", model]
        if readonly:
            # 实测本机自定义网关在 -p 模式下工具续接会丢最终结果（用工具必空）。
            # 评审/规划所需的上下文已内嵌在提示词中，显式禁用工具最稳。
            prompt = "（请勿使用任何工具，直接依据下方内容回答。）\n\n" + prompt
        elif os.environ.get("TUTTI_CLAUDE_PERMS", "").strip() == "acceptEdits":
            argv += ["--permission-mode", "acceptEdits"]
        else:
            # 无人值守 -p 下 acceptEdits 只自动放行改文件，Bash 一律权限拒绝 →
            # 模型以为环境受限谎报「被策略拦截」躺平（同 codex 沙箱误判案）。
            # 默认跳过全部权限检查（2026-09-17 用户拍板），env 可收回旧行为。
            argv += ["--dangerously-skip-permissions"]
        stdin_text = prompt
    elif kind == "opencode":
        argv = resolve_command(agent["command"]) + ["run"]
        if workdir:
            # opencode 的 shell 不总跟进程 cwd 走（服务端复用时会落在旧项目根，
            # 2026-09-17 mo-so 实测 agent 在 Temp 里全盘找代码）——显式钉死
            argv += ["--dir", str(workdir)]
        if sid:
            argv += ["-s", sid]  # 无头续会话：-s 指定会话 id（-c 只能接最近一次）
        if model:
            # opencode 只认 provider/model 全名（裸名直接 UnknownError，实测）；
            # CodeBee 同步进其配置的 provider id 固定为 orch，故裸名补前缀。
            # 绑定里已写全名（含 /）的尊重原值。
            argv += ["--model", model if "/" in model else "orch/" + model]
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


def _stall_timeout(env_name, default):
    """停滞看门狗秒数：stdout/stderr 持续无新输出超过该时长 → 判卡死提前杀，
    错误按超时归类走既有换模型/换将链路。只对事件流持续流动的 CLI 启用
    （codex JSONL / claude stream-json）；qwen/opencode/generic 结束才一次性
    输出，开了会误杀正常长任务，仍靠总超时兜底。env 可调，0=关闭。"""
    try:
        return max(0, int(os.environ.get(env_name, str(default))))
    except Exception:
        return default


def run_agent(agent, prompt, workdir=None, readonly=True,
              timeout=DEFAULT_TIMEOUT, cancel_event=None, log_path=None, resume=None,
              images=None, require_tools=False):
    """执行一次智能体调用，返回统一结构
    {ok, text, json, cost_usd, tokens, error, error_code, raw}。
    agent 来自 registry.effective_agents()；resume 为已有会话 id，仅真实智能体生效
    （codex: exec resume；claude: --resume；opencode/mimo: run -s；qwen: -r；
    generic: catalog orch.resume_argv_template）。
    images：任务图片附件的绝对路径，仅 codex 原生支持（-i）；其余智能体靠
    提示词里的 _attachments/ 路径 + 自身读文件能力获取，无读图工具时静默忽略。
    require_tools：实现步空转闸（仅 codex 事件流可判）——True 时本轮零
    command_execution/file_change/mcp_tool_call 事件即判 VENDOR_REFUSAL，
    防模型纯口头谎报「环境受限」蒙混过关（2026-09-17 mo-so 实测）。

    模型尝试顺序来自 _resolve_attempts：跨厂商链（每条独立 env）或
    主模型 + 降级备选；瞬态错误才换下一条，取消/超时/解析失败不降级。

    5E：catalog `orch.timeout_ms`（毫秒）优先于 caller 传入的 timeout。
    """
    # 5E：catalog orch.timeout_ms 优先
    orch_timeout_ms = (agent.get("orch") or {}).get("timeout_ms")
    if orch_timeout_ms:
        timeout = float(orch_timeout_ms) / 1000.0
    kind = agent.get("kind", "generic")
    stall_t = (_stall_timeout("TUTTI_CODEX_STALL_TIMEOUT", 600) if kind == "codex"
               else _stall_timeout("TUTTI_CLAUDE_STALL_TIMEOUT", 600)
               if kind == "claude" else 0)
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
        if kind == "codex":
            # 网关自定义模型名（如 [opencode]xxx）是合法 API 模型名不能改，但方括号
            # 是非法 OTel tag 值 → codex_otel 每个 SSE 事件刷 2 条 WARN 淹没真输出。
            # 只静音遥测模块（codex 不认 RUST_LOG 也无害），其余 WARN 全保留。
            env.setdefault("RUST_LOG", "codex_otel=off")
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
                                                       images=images if kind == "codex" else None,
                                                       workdir=workdir)
            res = run_process(argv=argv, stdin_text=stdin_text, cwd=workdir, env=env,
                              timeout=timeout, cancel_event=cancel_event, log_path=log_path,
                              stall_timeout=stall_t)
            out = {"ok": res["ok"], "text": "", "json": None, "cost_usd": 0.0,
                   "tokens": 0, "usage": None, "error": "", "error_code": "",
                   "sid": "", "raw": res, "kind": kind, "model": att["model"]}
            if not res["ok"]:
                # stderr 与 stdout 都要进错误串：codex 把 "Reading prompt from
                # stdin..." 打在 stderr，真正的配额/限流错误全在 stdout 的 JSONL
                # 里——只取其一会让 _quota_error/_transient_error 判空。
                tail = ((res["stderr"] or "") + "\n" + (res["stdout"] or "")).strip()[-600:]
                head = ("输出停滞 %ss（stall timed out，疑似卡死已提前终止）" % stall_t
                        if res.get("stalled")
                        else "超时" if res["timed_out"]
                        else "取消" if res["cancelled"]
                        else "退出码 %s" % res["exit_code"])
                out["error"] = head + ("；stderr/stdout: " + tail if tail else "")
                if kind == "codex":
                    fm = _codex_fail_msg(res["stdout"])
                    if fm:
                        # 事件流终态错误比原始 JSONL 尾部可读，也是链降级判定依据
                        out["error"] = "codex: %s（退出码 %s）" % (fm, res["exit_code"])
                # 5D+2D：错误码归一
                if kind == "claude":
                    parsed_err = _parse_claude_json(res["stdout"] or "")
                    if parsed_err and parsed_err.get("is_error") and parsed_err.get("text"):
                        out["error"] = "claude 返回 is_error: " + parsed_err["text"][:500]
                if kind == "codex" and att.get("provider_id"):
                    el = out["error"].lower()
                    if (("wire_api" in el and "no longer supported" in el)
                            or "no route matched" in el):
                        # codex 撞 chat-only 供应商（0.154 只讲 responses）：
                        # 自动冷却该供应商 30 分钟，链展开/路由绑定分随之
                        # 自动绕开——不用人工改绑定（2026-09-17 mo-so 实测）
                        try:
                            from . import modelhub as _mh
                            _mh.note_codex_wire_dead(att["provider_id"])
                        except Exception:
                            pass
                out["error_code"] = _classify_failure(res, kind=kind)
                break
            if kind == "codex":
                out["text"], out["usage"], out_sid = _parse_codex_jsonl(res["stdout"])
                out["sid"] = out_sid  # §07 T1.1：会话 id 供 revise/fix 复用
                out["tokens"] = out["usage"]["total"]
                # 退出码 0 不代表成功：配额耗尽时 turn.failed 收尾、进程仍正常退出，
                # 不判失败的话编排者会把错误信息当成果往下传
                fm = _codex_fail_msg(res["stdout"])
                if fm:
                    out["ok"] = False
                    out["error"] = "codex: %s" % fm
                    out["error_code"] = ErrorCode.VENDOR_ERROR
                elif not out["text"]:  # 事件流解析失败时退化为取 stdout 尾部
                    out["text"] = res["stdout"][-2000:]
                if require_tools and out["ok"] and _codex_work_events(res["stdout"]) == 0:
                    # 实现步空转闸：exit 0 + 有话但零动手 → 判拒绝（fatal 语义正确，
                    # 不把谎报的「已改完」静默传给下游；auto 模式换将逻辑按 ok 触发，
                    # 仍可换别的 CLI 再试）。原文尾段留进错误供人核对。
                    out["ok"] = False
                    out["error"] = ("实现步零工具调用：模型未执行任何命令/文件改动，"
                                    "仅口头汇报（典型如谎报环境受限）。回答尾段: %s"
                                    % (out["text"] or "")[-300:])
                    out["error_code"] = ErrorCode.VENDOR_REFUSAL
                if not out["text"] and out["ok"]:
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
        _report_key(att, out)
        if out["ok"] or ai == len(attempts) - 1:
            return out
        # 瞬态网络错误 → 换下一条；欠费/配额耗尽同样换（可能是同厂商的备用 KEY，
        # 也可能是另一家厂商）——账单断了死磕同一把 KEY 没有任何意义。
        if not (_transient_error(out.get("error")) or _quota_error(out.get("error"))):
            return out  # 非瞬态（取消/超时/解析失败）不降级
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
