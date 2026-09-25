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
import signal
import subprocess
import tempfile
import threading
import time

from . import beekeeper
from . import upstream
from .env_scrub import scrub_env
from .error_codes import (
    ErrorCode, classify_error_text, error_code_value,
    is_auth_error as _shared_auth_error,
    is_forbidden_error as _shared_forbidden_error,
    is_quota_error as _shared_quota_error,
    is_rate_limited_error as _shared_rate_limited_error,
    is_transient_error as _shared_transient_error,
)

# 非 Windows 必须置 0：POSIX 的 Popen 对非零 creationflags 直接抛 ValueError，
# 置 0 则两边通用（remote.py 同款守卫）。
CREATE_NO_WINDOW = 0x08000000 if os.name == "nt" else 0
DEFAULT_TIMEOUT = 1200  # 单步 20 分钟
DEFAULT_STREAM_ACTIVITY_TIMEOUT_S = 180  # 流式 CLI 的活动延长窗口

_BASH_CANDIDATES = [
    r"D:\Git\usr\bin\bash.exe",
    r"C:\Program Files\Git\usr\bin\bash.exe",
    r"C:\Program Files (x86)\Git\usr\bin\bash.exe",
]
_bash_cache = {"path": None, "done": False}


def find_git_bash():
    """Windows 专供：claude 原生 exe 找不到 bash 会拒绝启动，指给 Git Bash。
    macOS/Linux 有系统 bash，claude 自会找到；强行设 CLAUDE_CODE_GIT_BASH_PATH
    反而可能指错（/bin/bash 与 Git Bash 行为有差异），故非 Windows 一律 None。"""
    if os.name != "nt":
        return None
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


def _orphan_pack_files(workdir):
    """列出缺少同名 .idx 的 pack 文件名（无则空列表）。

    Git 本体扫目录时发现 pack 没有对应 .idx，只打一行 warning 就跳过该包，
    所以 git status/fsck 全都正常；aider 走的 gitpython/gitdb 却按
    pack-*.pack 逐个打开同名 .idx，缺一次就抛 FileNotFoundError，被它记成
    「is your git repo corrupted?」——2026-09-22 mo-so 实案：pack-a52e675e
    .idx 缺失，aider 换将后跑满 14 分钟才失败，报错头还是 prompt toolkit 噪声。
    """
    try:
        probe = subprocess.run(
            ["git", "-C", workdir, "rev-parse", "--git-path", "objects/pack"],
            stdout=subprocess.PIPE, stderr=subprocess.PIPE,
            text=True, timeout=5, check=False,
        )
    except (OSError, subprocess.SubprocessError):
        return []
    rel = (probe.stdout or "").strip()
    if probe.returncode != 0 or not rel:
        return []
    pack_dir = rel if os.path.isabs(rel) else os.path.join(workdir, rel)
    try:
        names = set(os.listdir(pack_dir))
    except OSError:
        return []
    return sorted(n for n in names
                  if n.startswith("pack-") and n.endswith(".pack")
                  and (n[:-len(".pack")] + ".idx") not in names)


def _git_repo_issue(workdir):
    """返回工作目录 Git 仓库的可读性问题；无 Git 仓库时返回空串。

    Aider 会在启动后自行读取索引。索引包缺失时它通常先写入 .gitignore，
    再在深层调用中才报错，用户会误以为任务卡住；提前做一次廉价预检能把
    错误变成可操作的任务失败信息。
    """
    if not workdir or not os.path.isdir(workdir):
        return ""
    git_dir = os.path.join(workdir, ".git")
    if not os.path.exists(git_dir):
        return ""
    try:
        probe = subprocess.run(
            ["git", "-C", workdir, "rev-parse", "--git-dir"],
            stdout=subprocess.PIPE, stderr=subprocess.PIPE,
            text=True, timeout=5, check=False,
        )
    except (OSError, subprocess.SubprocessError):
        return "Git 命令不可用，无法读取仓库；请安装 Git 或改用非 Aider 实现器"
    if probe.returncode != 0:
        detail = (probe.stderr or probe.stdout or "仓库元数据不可读").strip()
        return "Git 仓库不可读：%s；请先执行 git fsck/恢复 .git 对象后重试" % detail[-300:]
    try:
        probe = subprocess.run(
            ["git", "-C", workdir, "status", "--porcelain", "--untracked-files=no"],
            stdout=subprocess.PIPE, stderr=subprocess.PIPE,
            text=True, timeout=8, check=False,
        )
    except subprocess.TimeoutExpired:
        return "Git 仓库检查超时；请先执行 git status/git fsck 修复后重试"
    except OSError:
        return "Git 命令不可用，无法读取仓库；请安装 Git 或改用非 Aider 实现器"
    if probe.returncode != 0:
        detail = (probe.stderr or probe.stdout or "仓库对象或索引不可读").strip()
        return "Git 仓库损坏或对象缺失：%s；请先执行 git fsck 并恢复仓库后重试" % detail[-300:]
    # git status 对「pack 缺 .idx」不敏感（只 warning 跳过该包），必须单独查：
    # aider 的 gitdb 按 pack-*.pack 逐个开同名 .idx，缺一个就整步失败。
    orphans = _orphan_pack_files(workdir)
    if orphans:
        return ("Git 仓库 pack 索引缺失：%s 没有同名 .idx（git 本体只警告跳过，"
                "Aider 的 gitdb 会直接报「Unable to list files in git repo」）。"
                "修复：在 %s 目录对每个 pack 执行 `git index-pack <pack文件>` "
                "重建索引，或临时改用 Codex/Claude 实现器。"
                % (", ".join(orphans[:3]), os.path.join(".git", "objects", "pack")))
    return ""


def _npm_shim_bypass(argv):
    """generic 类 CLI 把提示词嵌进 argv；经 cmd /c 重解析时引号/特殊字符会把
    提示词截烂（kimi 实测：模型只看到 UTF-8 须知、任务本体整段丢失）。识别
    标准 npm .cmd 垫片（%_prog% + %dp0% 目标脚本），改 node 直启绕开 cmd。
    识别不出标准形态原样返回。"""
    if len(argv) >= 3 and argv[0] == "cmd" and argv[1] == "/c" \
            and str(argv[2]).lower().endswith((".cmd", ".bat")):
        shim = argv[2]
        try:
            with open(shim, encoding="utf-8", errors="replace") as fh:
                text = fh.read()
        except Exception:
            return argv
        m = re.search(r'%_prog%"\s+"?%dp0%(\\[^"\n]+?\.(?:mjs|js))"?', text)
        if not m:
            return argv
        script = os.path.normpath(
            os.path.join(os.path.dirname(shim), m.group(1).lstrip("\\")))
        node = os.path.join(os.path.dirname(shim), "node.exe")
        return [node if os.path.isfile(node) else "node", script] + list(argv[3:])
    return argv


def _kill_tree(pid):
    """杀整棵进程树。Windows：先 TerminateProcess 直接杀根进程（毫秒级，绝不
    挂），再 taskkill /T 兜底扫子孙（短等待，卡死即放弃——真实案例 2026-09-19：
    某些机器状态下 taskkill /F /T 对普通进程也挂死 30s+，等满 15s 超时会让每次
    超时杀进程都卡 20s，看门狗/超时全被拖死；漏杀的孙进程由启动孤儿清扫兜底）。
    POSIX 靠 spawn 时的 start_new_session（子进程自成一个进程组，pgid==pid）
    用 killpg 连孙带杀。

    返回是否确认整树清空（借鉴 WorkDSH 取消结算分层：kill 动作完成 ≠ 停稳）：
    Windows 以 taskkill /T 在 1.2s 内退出且返回 0 为准；POSIX 以 killpg 成功
    为准。返回 False = 停稳未知（孙进程可能仍在），调用方据此落
    quiescence_unknown 诊断，不改变任何终态语义。"""
    if os.name != "nt":
        try:
            os.killpg(pid, signal.SIGKILL)
            return True
        except Exception:
            pass
        try:
            os.kill(pid, signal.SIGKILL)   # 组杀失败退而杀根：子孙已不可确认
        except Exception:
            pass
        return False
    # Windows 没有 signal.SIGKILL（直接引用会 AttributeError）；os.kill 带任意
    # 信号值都走 TerminateProcess，用 SIGTERM
    try:
        os.kill(pid, signal.SIGTERM)   # Windows 语义 = TerminateProcess，只杀根进程
    except OSError:
        pass                           # 根进程可能已被 taskkill 抢先杀掉
    except Exception:
        pass
    try:
        tk = subprocess.Popen(
            ["taskkill", "/F", "/T", "/PID", str(pid)],
            stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL, close_fds=True,   # 不继承被杀进程的管道句柄，否则 EOF 永不来、drain 烧满超时
            creationflags=CREATE_NO_WINDOW)
        tk.wait(timeout=1.2)           # 健康机器 <1s；卡死就不再陪等（后台自行结束）
        return tk.returncode == 0
    except Exception:
        return False


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
    # Killing the tree does not necessarily reap the root process on Windows.
    # Reap it with the remaining bounded budget before callers inspect
    # ``returncode``; otherwise long-running timeout tests can leak a live
    # Popen handle and emit ResourceWarning during interpreter shutdown.
    remaining = max(0.0, deadline - time.time())
    # proc 可为 None（旧契约：只收线程不收割进程的调用方，tests/test_runner_drain）
    if proc is not None and remaining > 0 and proc.poll() is None:
        try:
            proc.wait(timeout=remaining)
        except (subprocess.TimeoutExpired, OSError):
            pass


def _pipe_reader(stream, chunks, log_fh, stamp=None):
    """持续读子进程管道并实时落盘。

    必须用 read1()：BufferedReader.read(n) 会阻塞到凑满 n 字节或 EOF，
    在长命令（npm 安装等）上等于"进程结束才一次性返回"，日志面板全程空白。
    read1() 只要有数据就返回，日志才能真正边跑边看。
    stamp：共享 [最后输出时刻]，停滞看门狗据此判定进程是否卡死。
    关闭也归本线程：EOF（或杀树后的异常）后自关。主线程绝不能 close()——
    close 要抢 BufferedReader 的锁，而锁被还在 read1 等 EOF 的本线程攥着，
    孙进程漏杀时它攥着管道到自然死，主线程就陪锁到天荒地老（2026-09-22
    run_command 超时杀 ping 实测：taskkill /T 失手 → close 卡满命令时长）。
    """
    try:
        while True:
            b = stream.read1(65536)
            if not b:
                break
            chunks.append(b)
            if stamp is not None:
                stamp[0] = time.time()
                if len(stamp) > 1:
                    stamp[1] = True
                if len(stamp) > 2:
                    stamp[2] = time.monotonic()
            if log_fh:
                try:
                    log_fh.write(b)
                    log_fh.flush()
                except Exception:
                    pass
    except Exception:
        pass                   # 杀树瞬间句柄作废：ValueError/OSError 都算正常收场
    finally:
        try:
            stream.close()
        except Exception:
            pass


def _decode_line(blob):
    """单行解码：UTF-8 → GBK → replace（与 decode_output 同一编码纪律）。"""
    try:
        return blob.decode("utf-8")
    except UnicodeDecodeError:
        pass
    try:
        return blob.decode("gbk")
    except UnicodeDecodeError:
        return blob.decode("utf-8", "replace")


def decode_output(data):
    """子进程输出解码：UTF-8 严格解码失败时回退 GBK（中文 Windows 控制台）。

    整块 UTF-8 与整块 GBK 都失败时逐行兜底——日志文件是混合编码（我方 UTF-8
    审计头 + CLI 自家 GBK 输出），整块回退会把能读的部分一起牺牲：要么头部中文
    变乱码，要么整篇落进 replace 分支铺成 U+FFFD 墙（2026-09-20 aider 日志实证，
    详情页卡片显示成一片 ◆）。逐行解码两边都保住（GBK/UTF-8 的多字节序列都不
    含 0x0A，按行切不会切坏字符）。"""
    if not data:
        return ""
    try:
        return data.decode("utf-8")
    except UnicodeDecodeError:
        pass
    try:
        return data.decode("gbk")
    except UnicodeDecodeError:
        pass
    return "\n".join(_decode_line(ln) for ln in data.split(b"\n"))


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


# 终端控制序列：CSI（颜色/光标/清行，`\x1b[91m`）、OSC（窗口标题）、其余两字符转义
_ANSI_RE = re.compile(
    r"\x1b\[[0-9;?]*[ -/]*[@-~]"
    r"|\x1b\][^\x07\x1b]*(?:\x07|\x1b\\)?"
    r"|\x1b[@-Z\\-_]"
)
# 被切片/前端截断掉 ESC 的「裸序列」残尾（`[91m`、`[0m`）：只在行首止血，
# 正文里合法的 [数字+字母]（如 [1m] 引用）不动——后面紧跟 `]` 的不算残尾
_ANSI_HEAD_RE = re.compile(r"^\[[0-9;?]{1,6}[A-Za-z](?!\])")
# CLI 拿来做进度条/画框/旋转动画的图元，成串出现时人读不出任何信息
_NOISE_RUN_RE = re.compile(r"([\u2500-\u259f\u25a0-\u25ff\u2800-\u28ff])\1{7,}")
# 解不出来的字节（replace 兜底）成串即「乱码墙」——多数等宽字体把 U+FFFD 画成
# 带问号的菱形，一屏看起来就是 ◆◆◆◆
_FFFD_RUN_RE = re.compile(r"\ufffd{3,}")
_CTRL_RE = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]")


def strip_ansi(text):
    """剥掉 ANSI 转义序列（颜色/光标/进度），并清掉行首被截断的裸序列残尾。"""
    if not text:
        return ""
    out = _ANSI_RE.sub("", text)
    # 残尾可能连着几个（`[91m[0mError:`），一次替换只够消掉最前面那个
    while True:
        nxt = "\n".join(_ANSI_HEAD_RE.sub("", ln) for ln in out.split("\n"))
        if nxt == out:
            return out
        out = nxt


def clean_cli_text(text):
    """CLI 原始输出 → 人类可读文本（只用于错误摘要/日志展示，不碰模型正文）。

    终端噪声四种，都是实测踩过的：
    1. ANSI 转义：`\\x1b[91m\\x1b[1mError:` 在浏览器里渲染成 `[91m[1mError:`；
    2. 裸 `\\r` 覆写：进度条/旋转动画反复回退改写同一行，原样展示会叠成一片；
       `\\r\\n` 是行结束符不是覆写，先归一化再按覆写取「该行最终形态」；
    3. 画线/进度图元成串（`────…`、`████…`）与 U+FFFD 乱码墙（编码不可解）；
    4. 剩余控制字符。
    """
    if not text:
        return ""
    out = strip_ansi(text).replace("\r\n", "\n")
    if "\r" in out:
        lines = []
        for ln in out.split("\n"):
            if "\r" in ln:
                parts = ln.split("\r")
                ln = next((p for p in reversed(parts) if p.strip()), parts[-1])
            lines.append(ln)
        out = "\n".join(lines)
    out = _CTRL_RE.sub("", out)
    out = _FFFD_RUN_RE.sub(
        lambda m: "…（%d 个字符无法解码：CLI 输出不是 UTF-8/GBK）" % len(m.group(0)), out)
    out = _NOISE_RUN_RE.sub(lambda m: m.group(1) * 3 + "…", out)
    return re.sub(r"\n{4,}", "\n\n\n", out)


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

    claude stream-json 同场翻译（2026-09-22 真实案：3.2MB 步骤日志 15828 条
    thinking_tokens 心跳，每 SSE 分片一条、数值只涨 1~2）：思考心跳按邻近组
    折成一行；init/api_retry/assistant/result 翻译成可读行——api_retry 是
    429/断连诊断金矿，逐条保留不折叠。只影响抽屉显示，原始日志与解析不动。
    """
    out = []
    hb = [0, 0]   # claude 思考心跳邻近组：[条数, 最大估算 tokens]
    msg_seen = [False]   # 是否已出过【消息】（防 result 再重复一遍正文）

    def _flush_thinking():
        if hb[0]:
            out.append("— 思考中（心跳 ×%d 已折叠，估算 ~%d tokens）—" % (hb[0], hb[1]))
            hb[0] = hb[1] = 0

    for ln in (text or "").splitlines():
        s = ln.strip()
        ev = None
        if s.startswith("{") and s.endswith("}"):
            try:
                ev = json.loads(s)
            except Exception:
                ev = None
        if isinstance(ev, dict) and ev.get("type") == "system" \
                and (ev.get("subtype") or "") == "thinking_tokens":
            try:
                n = int(ev.get("estimated_tokens") or 0)
            except Exception:
                n = 0
            hb[0] += 1
            if n > hb[1]:
                hb[1] = n
            continue
        _flush_thinking()
        if not isinstance(ev, dict):
            out.append(ln)
            continue
        typ = ev.get("type") or ""
        if typ == "system":   # claude 其余系统事件
            sub = ev.get("subtype") or ""
            if sub == "init":
                out.append("— 会话启动（model=%s）—" % (ev.get("model") or "?"))
            elif sub == "api_retry":
                out.append("— API 重试 %s/%s（等 %sms）：%s —" % (
                    ev.get("attempt", "?"), ev.get("max_retries", "?"),
                    ev.get("retry_delay_ms", "?"), ev.get("error") or "?"))
            else:
                out.append(ln)   # 未知 subtype 原样保留，不吞内容
            continue
        if typ == "assistant":   # claude 正文/思考/工具调用都在 content 块里
            for blk in ((ev.get("message") or {}).get("content") or []):
                if not isinstance(blk, dict):
                    continue
                bt = blk.get("type") or ""
                if bt == "thinking":
                    th = " ".join(str(blk.get("thinking") or "").split())
                    if th:
                        out.append("【思考】" + th[:200])
                elif bt == "text":
                    tx = str(blk.get("text") or "")
                    if tx:
                        out.append("【消息】" + tx[:max_event_chars])
                        msg_seen[0] = True
                elif bt == "tool_use":
                    out.append("【命令】%s %s" % (
                        blk.get("name") or "?",
                        json.dumps(blk.get("input") or "", ensure_ascii=False)[:200]))
            continue
        if typ in ("user", "stream_event"):
            continue   # claude 工具回包/流片段是过程噪音
        if typ == "result":   # claude 收尾：统计一行；报错带原因
            u = ev.get("usage") or {}
            if ev.get("is_error"):
                out.append("— 完成（报错）：" + str(ev.get("result") or "")[:300] + " —")
            else:
                cost = ev.get("total_cost_usd")
                if not msg_seen[0]:
                    tx = str(ev.get("result") or "")
                    if tx:   # 旧单 JSON 模式没有 assistant 事件，正文在这补上
                        out.append("【消息】" + tx[:max_event_chars])
                        msg_seen[0] = True
                out.append("— 完成（tokens 入 %s / 出 %s，%s）—" % (
                    u.get("input_tokens", "?"), u.get("output_tokens", "?"),
                    ("$%.2f" % cost) if isinstance(cost, (int, float)) else "?"))
            continue
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


def _normalize_abort_markers(value):
    """把重复错误配置规范成 ``[(marker, count), ...]``。

    旧调用方传的是单个 ``(marker, count)`` 元组，必须继续支持；新的
    调用方可以传字典或元组列表，以便同一 CLI 同时对多种断流文案止损。
    """
    if not value:
        return []
    if isinstance(value, str):
        return [(value, 1)]
    if isinstance(value, dict):
        raw = list(value.items())
    elif (isinstance(value, (tuple, list)) and len(value) == 2
          and isinstance(value[0], str)):
        raw = [value]
    else:
        try:
            raw = list(value)
        except TypeError:
            return []
    out = []
    for item in raw:
        if isinstance(item, str):
            marker, limit = item, 1
        else:
            try:
                marker, limit = item
            except (TypeError, ValueError):
                continue
        marker = str(marker or "")
        try:
            limit = max(1, int(limit))
        except (TypeError, ValueError):
            continue
        if marker:
            out.append((marker, limit))
    return out


def run_process(argv=None, shell_cmd=None, stdin_text=None, cwd=None, env=None,
                timeout=DEFAULT_TIMEOUT, cancel_event=None, log_path=None,
                stall_timeout=0, repeat_abort=None, deadline=None,
                abort_markers=None, activity_timeout=0, audit_notes=None):
    """通用子进程执行：并发读管道防死锁；超时/取消杀整棵进程树。

    stall_timeout：停滞看门狗（秒，0=关闭）——超过该时长 stdout/stderr 无任何
    新输出即判卡死，提前杀树返回（timed_out=True + stalled=True）。只对输出
    持续流动的 CLI 开（codex JSONL 事件流）；claude json 到结束才一次性输出，
    开了会把正常长任务误杀。

    activity_timeout：基础 timeout 到期后，最近仍有真实输出的流式 CLI 允许继续
    运行的最长静默窗口（秒，0=关闭）。它独立于 stall_timeout，避免环境把
    停滞看门狗设为 0 后，Claude/Codex 又在基础超时处被硬杀；总延长仍有上限。

    repeat_abort：可选 (marker, count)，也接受多个 ``(marker, count)``；同一
    致命错误达到次数即提前杀树，处理 CLI 自身不断打印重连消息、因此永远
    触发不了静默看门狗的假运行。abort_markers 是兼容扩展，用于不改变旧
    repeat_abort 参数形状的情况下追加多种一次性断流标记。

    deadline：可选的 ``time.monotonic()`` 绝对截止时刻。它比 timeout 优先，
    到点会杀掉进程树并返回 deadline_exceeded=True，调用方可把整个任务收口
    为 timeout，而不是把它误记成普通供应商失败。

    audit_notes：可选字符串列表，逐行追加在审计头命令行下方——用于把
    argv/env 里看不出的运行形态钉进日志（如 claude 凭据=绑定链注入/本机默认），
    事后诊断不用靠猜。只写形态名，绝不写密钥值。

    返回 {ok, exit_code, stdout, stderr, duration, cancelled, timed_out, stalled,
    deadline_exceeded, abort_marker}；杀树后未能确认整树清空时额外带
    quiescence_unknown=True（孙进程可能仍在，诊断用，不改变终态语义）。
    """
    if shell_cmd:
        # shell 串的解析器随平台：重定向/引号语法两边通用，只是解释器不同
        argv = ["cmd", "/c", shell_cmd] if os.name == "nt" else ["/bin/sh", "-c", shell_cmd]
    if argv is None:
        return {"ok": False, "exit_code": None, "stdout": "", "stderr": "argv 为空",
                "duration": 0.0, "cancelled": False, "timed_out": False,
                "stalled": False, "deadline_exceeded": False, "abort_marker": None}
    try:
        deadline = float(deadline) if deadline is not None else None
    except (TypeError, ValueError):
        deadline = None
    try:
        timeout = max(0.0, float(timeout))
    except (TypeError, ValueError):
        timeout = float(DEFAULT_TIMEOUT)
    try:
        activity_timeout = max(0.0, float(activity_timeout))
    except (TypeError, ValueError):
        activity_timeout = 0.0
    if deadline is not None and time.monotonic() >= deadline:
        return {"ok": False, "exit_code": None, "stdout": "",
                "stderr": "[任务总时限已到，未启动子进程]", "duration": 0.0,
                "cancelled": False, "timed_out": True, "stalled": False,
                "deadline_exceeded": True, "abort_marker": None}
    aborts = _normalize_abort_markers(repeat_abort)
    aborts.extend(_normalize_abort_markers(abort_markers))
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
            for _note in (audit_notes or []):
                if _note:
                    head.append("· " + str(_note))
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
                creationflags=CREATE_NO_WINDOW,
                start_new_session=(os.name != "nt"))  # POSIX 需独立进程组供 killpg 杀树；Windows 忽略该参数
        except Exception as e:
            return {"ok": False, "exit_code": None, "stdout": "",
                    "stderr": "启动失败: %r" % e, "duration": 0.0,
                    "cancelled": False, "timed_out": False, "stalled": False,
                    "deadline_exceeded": False, "abort_marker": None}
        # 挂巢（beekeeper）：服务无论以何种方式退出（含被硬杀），蜜蜂 CLI
        # 一起带走。挂巢失败只降级，绝不影响本步起跑。
        beekeeper.adopt(proc)
        out_chunks, err_chunks = [], []
        stamp = [time.time(), False, time.monotonic()]  # [墙钟、是否有输出、单调时钟]
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
        start_mono = time.monotonic()
        cancelled = timed_out = stalled = repeat_aborted = False
        deadline_exceeded = False
        abort_marker = None
        tree_quiet = None   # 杀树结果：None=未杀，True=确认清空，False=停稳未知
        while True:
            try:
                proc.wait(timeout=0.4)
                break
            except subprocess.TimeoutExpired:
                pass
            if cancel_event is not None and cancel_event.is_set():
                cancelled = True
                tree_quiet = _kill_tree(proc.pid)
                _drain_streams(proc, t_out, t_err, timeout=10)
                break
            if deadline is not None and time.monotonic() >= deadline:
                deadline_exceeded = True
                timed_out = True
                tree_quiet = _kill_tree(proc.pid)
                _drain_streams(proc, t_out, t_err, timeout=5)
                break
            elapsed = time.monotonic() - start_mono
            if elapsed > timeout:
                # Claude/Codex 的工具循环可能持续吐 stream-json 事件而迟迟
                # 不给最终 result。活动延长与 stall 看门狗分离：无输出的挂死
                # 仍按原单模型 timeout 快速换将，收到过输出则最多再给一个活动窗口。
                active = (activity_timeout > 0 and stamp[1]
                          and time.monotonic() - stamp[2] < activity_timeout
                          and elapsed < timeout + activity_timeout)
                if not active:
                    timed_out = True
                    tree_quiet = _kill_tree(proc.pid)
                    _drain_streams(proc, t_out, t_err, timeout=5)
                    break
            if stall_timeout and time.time() - stamp[0] > stall_timeout:
                # 停滞看门狗：长静默多为卡死（网关挂起/CLI 假死），与其耗满总
                # 超时不如提前杀——错误按超时归类，走既有的换模型/换将链路
                stalled = True
                timed_out = True
                tree_quiet = _kill_tree(proc.pid)
                _drain_streams(proc, t_out, t_err, timeout=5)
                break
            if aborts:
                try:
                    recent = decode_output(b"".join(out_chunks)[-8000:] +
                                           b"\n" + b"".join(err_chunks)[-8000:])
                    for marker, limit in aborts:
                        if recent.count(marker) >= limit:
                            repeat_aborted = True
                            abort_marker = marker
                            timed_out = True
                            tree_quiet = _kill_tree(proc.pid)
                            _drain_streams(proc, t_out, t_err, timeout=5)
                            break
                    if repeat_aborted:
                        break
                except (TypeError, ValueError):
                    pass
        duration = round(time.time() - start, 1)
        t_out.join(timeout=5)
        t_err.join(timeout=5)
        exit_code = proc.returncode
        stdout = decode_output(b"".join(out_chunks))
        stderr = decode_output(b"".join(err_chunks))
        # stdout/stderr 的 close 已归 _pipe_reader 自线程（EOF 后自关）：这里再
        # close 会抢 read1 手里的锁，孙进程漏杀攥着管道时就死等（见其 docstring）。
        # stdin 无读线程，_feed 写完自关；这里兜底一次（DEVNULL 时本就无事）。
        try:
            if proc.stdin:
                proc.stdin.close()
        except Exception:
            pass
        if cancelled:
            stderr += "\n[已被用户取消]"
        elif repeat_aborted:
            limit = next((n for m, n in aborts if m == abort_marker), "?")
            if len(aborts) == 1 and abort_markers is None:
                # 保留旧版 UI/测试已经依赖的文案；多标记配置使用更具体的
                # 标记名，方便定位究竟是哪一种断流触发了止损。
                stderr += "\n[同一网络错误重复 %s 次，已提前终止进程树]" % limit
            else:
                stderr += "\n[错误标记 %s 重复 %s 次，已提前终止进程树]" % (
                    abort_marker, limit)
        elif deadline_exceeded:
            stderr += "\n[任务总时限已到，已终止进程树]"
        elif stalled:
            stderr += "\n[输出停滞 %ss，已终止进程树]" % stall_timeout
        elif timed_out:
            stderr += "\n[超时 %ss，已终止进程树]" % timeout
        result = {
            "ok": exit_code == 0 and not cancelled and not timed_out,
            "exit_code": exit_code, "stdout": stdout, "stderr": stderr,
            "duration": duration, "cancelled": cancelled, "timed_out": timed_out,
            "stalled": stalled, "deadline_exceeded": deadline_exceeded,
            "abort_marker": abort_marker,
            "activity_extended": bool(activity_timeout and stamp[1]
                                       and duration > timeout),
            "activity_timeout": activity_timeout,
            "last_output_age": max(0.0, time.time() - stamp[0]),
        }
        if tree_quiet is False:
            # 杀树完成但未确认整树清空（taskkill 卡死放弃 / killpg 失手只杀了
            # 根）：孙进程可能仍在运行。只落诊断标记，不改变 cancelled/timed_out
            # 等任何终态语义——KEY 冷却豁免、健康惩罚仍按结构化取消标记判断。
            result["quiescence_unknown"] = True
            result["stderr"] += "\n[进程树未确认清空：孙进程可能仍在运行]"
        return result
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
    Reconnecting... 是 CLI 内部重试噪音（可能自愈），不取作终态错误；重复
    重连由进程看门狗提前中断。turn.failed 优先于裸 error（后者取最后一条兜底）。
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
    # 网关实服模型：请求 deepseek-v4.1-flash 却被网关映射成 gemini-3.8-flash
    # 之类（2026-09-23 夜班实案）——用量统计记错名、失败排查看错病根，都
    # 靠 result 行的 modelUsage 对出来
    served = sorted({str(k) for k in ((data.get("modelUsage") or {}) or {})})
    return {
        "text": data.get("result") or "",
        "cost_usd": data.get("total_cost_usd") or 0.0,
        "usage": usage,
        "tokens": usage["total"],
        "is_error": bool(data.get("is_error")),
        # §07 T1.1：claude -p 返回本次会话 id，供 --resume 复用
        "sid": str(data.get("session_id") or ""),
        "served_models": served,
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


def _transient_error(err):
    """Shared retryable upstream/transport classification."""
    return _shared_transient_error(err)


def _quota_error(err):
    """Quota and rate-limit checks share the taxonomy used by key health."""
    return _shared_quota_error(err)


def _auth_error(err):
    """Only explicit credential failures are auth errors; 403 is separate."""
    if _permission_error(err):
        return False
    return _shared_auth_error(err)


def _permission_error(err):
    """识别明确的 HTTP 403 权限拒绝；它是上游故障，不等同于无效 API Key。"""
    return _shared_forbidden_error(err)


def _attempt_credential(att):
    """绑定链中凭据的非敏感身份；不把密钥值写入日志或尝试记录。"""
    return (str((att or {}).get("provider_id") or ""),
            str((att or {}).get("key_id") or ""))


# 限流（429）专项：与一般瞬态不同，它是「等一个窗口就常能自愈」的病——
# 2026-09-22 四连败实测：claude ENOTFOUND 换将 codex 后仍与同一上游撞 429，
# 链上无第三条路时整步立刻判死；而限流窗口通常按分钟计，原地等一个窗口
# 再试一次，好过把整轮 run 直接烧成 failed（欠费不同源，不适用宽限）。
RATE_LIMIT_GRACE_S = 45      # 宽限等待时长：限流窗口通常按分钟计，等一个再试
RATE_LIMIT_GRACE_N = 1       # 每步宽限次数：只兜一次，防限流长拖整轮时间


def _refusal_error(err):
    return classify_error_text(err) == ErrorCode.VENDOR_REFUSAL


def _attempt_upstream(att):
    """返回规范化上游身份；同一 host 换模型不重复烧超时。"""
    provider = (att or {}).get("provider") or {}
    base_url = str(provider.get("base_url") or "").strip()
    normalized = upstream.normalize_upstream(base_url)
    if normalized:
        return normalized
    provider_id = str((att or {}).get("provider_id") or "").strip()
    return "provider:" + provider_id if provider_id else "unknown"


def _claude_drift_note(att_model, parsed):
    """请求模型 vs 网关实服模型不一致时的注记；一致或无从判断返回空。"""
    served = (parsed or {}).get("served_models") or []
    if not served or not att_model or served == [att_model]:
        return ""
    return "[模型漂移] 请求 %s，上游实服 %s" % (att_model, ",".join(served))


def _claude_login_hint(error_code, error_text):
    """claude 登录闸门的修复指引；非缺凭据或非登录措辞返回空。

    「无链回落本机默认」时 runner 不注入任何凭据（_resolve_attempts 的 env
    恒空），本机 claude 又没登录 → 每步必死还烧满退避。把修法直接写进
    错误串，别让一句「Not logged in」冒充死因（2026-09-25 Mac 端实案：
    init JSON 里 apiKeySource=none，链上零候选可换将）。"""
    if error_code_value(error_code) != ErrorCode.MISSING_CREDENTIAL:
        return ""
    if "not logged in" not in str(error_text or "").lower():
        return ""
    return ("；claude CLI 未登录且本步未注入绑定链凭据，修复二选一："
            "① 绑定页为 claude-code 配置供应商链（如 Bigmodel anthropic 面）；"
            "② 在 ~/.claude/settings.json 的 env 里配 ANTHROPIC_BASE_URL"
            " + ANTHROPIC_AUTH_TOKEN")


def _claude_cred_note(argv):
    """claude 步骤审计头的凭据模式行（run_process audit_notes 用）。

    argv 里有没有 --settings 是绑定链凭据是否注入的真值——_build_call 只在
    env 带 ANTHROPIC_* 时落它。2026-09-25 Mac 实案：纯模型条目零注入，进程
    死在登录闸门后只能靠 init JSON 的 apiKeySource=none 反推死因；起跑就把
    凭据模式钉进日志头，审计行一眼可读。"""
    if any(str(a) == "--settings" for a in (argv or [])):
        return "凭据=绑定链（一次性 --settings 注入 ANTHROPIC_*）"
    return ("凭据=本机默认（未注入 ANTHROPIC_*，走本机 claude 登录态；"
            "未登录则 -p 必死在登录闸门）")


def _log_note(log_path, text):
    if not log_path or not text:
        return
    try:
        with open(log_path, "a", encoding="utf-8") as fh:
            fh.write("\n[%s]\n" % text)
    except OSError:
        pass


def _rate_limited(err):
    return _shared_rate_limited_error(err)


# 审计日志错误行探针：只认强信号，避免把回显提示词里的普通词当错误
_LOG_ERR_HINT = re.compile(
    r"(403|forbidden|request not allowed|permission denied|access denied"
    r"|429|402|rate[_ ]?limit|quota|余额|欠费|insufficient|billing|arrears"
    r"|401|unauthorized|unauthorised|invalid[_ ]api[_ ]key|authentication[_ ]error"
    r"|ENOTFOUND|ECONNREFUSED|failed to run prompt|api_error|overloaded)",
    re.I)


def _augment_error_from_log(out, log_path, limit=300):
    """stdout/stderr 尾段没有错误信号时，从流式审计日志里补最后一条错误行。
    真错误有时只走流式管道（kimi 撞 429 时捕获输出只剩启动横幅），不补的
    话路由层与用户都只看到「退出码 1；stderr/stdout: kimi version 2.0.2」。"""
    if not log_path or not out.get("error"):
        return
    try:
        with open(log_path, "rb") as fh:
            fh.seek(0, 2)
            size = fh.tell()
            fh.seek(max(0, size - 8192))
            blob = fh.read().decode("utf-8", "ignore")
    except OSError:
        return
    hits = [ln.strip() for ln in blob.splitlines() if _LOG_ERR_HINT.search(ln)]
    if not hits:
        return
    out["error"] = (out["error"] + "；日志错误行: " + hits[-1][:limit])[-800:]


def _grace_wait(cancel_event, seconds, log_path=None, why=""):
    """链尾限流宽限等待：小片睡眠随时响应取消。返回 False=已取消（别再重试）。"""
    if log_path:
        try:
            with open(log_path, "a", encoding="utf-8") as fh:
                fh.write("\n[限流宽限] %s：等待 %ds 后原地重试一次\n"
                         % (why or "上游限流（429）", seconds))
        except Exception:
            pass
    end = time.monotonic() + seconds
    while time.monotonic() < end:
        if cancel_event is not None and cancel_event.is_set():
            return False
        time.sleep(min(3, max(0.5, end - time.monotonic())))
    return cancel_event is None or not cancel_event.is_set()


def attempt_cancelled(res):
    """这次调用是不是被用户取消的。

    取消是用户意志不是上游表现：KEY 冷却、供应商健康、评测作废三本惩罚账都必须
    跳过（docs/execution-standard.md 统一执行算法第 3 条与 CANCELLED 行）。只看
    错误文案不行——取消串是「取消；stderr/stdout: <被杀进程尾部>」，尾部碰巧含
    429/欠费字样就会把健康 KEY 打进 30 分钟冷却。
    """
    res = res if isinstance(res, dict) else {}
    raw = res.get("raw") if isinstance(res.get("raw"), dict) else {}
    return (bool(res.get("cancelled")) or bool(raw.get("cancelled"))
            or error_code_value(res.get("error_code")) == ErrorCode.CANCELLED)


def _report_key(att, out):
    """把这次尝试的结果回写到 KEY 账本：欠费/失败 → 冷却，成功 → 清错误。

    回写失败绝不能影响主流程（账本是旁路），所以整体吞异常。
    """
    pid, kid = att.get("provider_id") or "", att.get("key_id") or ""
    if not (pid and kid) or attempt_cancelled(out):
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
    if not res.get("ok") or (parsed and parsed.get("is_error")):
        detail = "\n".join((str(res.get("stderr") or ""),
                            str(res.get("stdout") or ""),
                            str((parsed or {}).get("text") or "")))
        if _permission_error(detail):
            return ErrorCode.FORBIDDEN
    # Preserve actionable upstream distinctions before collapsing non-zero exits
    # into VENDOR_ERROR. The shared classifier is also used by modelhub and routing.
    if not res.get("ok") or (parsed and parsed.get("is_error")):
        detail = "\n".join((str(res.get("stderr") or ""),
                            str(res.get("stdout") or ""),
                            str((parsed or {}).get("text") or "")))
        classified = classify_error_text(detail)
        if classified is not None:
            return classified
    # claude 解析失败（进程 ok 但无 result 事件）：先按 stdout 认错误——未登录
    # 闸门只吐一行「Not logged in · Please run /login」就退出，认不出才是
    # 真正的解析失败
    if kind == "claude" and parsed is None:
        return classify_error_text(res.get("stdout") or "") or ErrorCode.PARSE_FAIL
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
                 "provider": e.get("provider") or {},
                 "from_chain": True,
                 "own_cp": "codex_provider" in e,
                 "codex_provider": e.get("codex_provider"),
                 "provider_id": e.get("provider_id") or "",
                 "key_id": e.get("key_id") or ""} for e in chain]
    base_model = agent.get("model")
    fb = [m for m in (agent.get("model_fallbacks") or []) if m and m != base_model]
    models_to_try = ([base_model] if base_model else []) + fb
    provider = agent.get("provider") or {}
    return [{"model": m or None, "env": {}, "provider": provider,
             "from_chain": False, "own_cp": False,
             "codex_provider": None,
             "provider_id": provider.get("id") if isinstance(provider, dict) else "",
             "key_id": ""}
            for m in (models_to_try or [None])[:3]]


def _attempt_protocol(att):
    """尝试身份里的协议：链/供应商条目自带 protocol 才记，取不到留空——
    留空表示未声明，不得回填猜测（chat/responses 之分要靠适配层实测）。"""
    prov = att.get("provider")
    if isinstance(prov, dict) and prov.get("protocol"):
        return str(prov["protocol"])[:24]
    return ""


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


def _prompt_to_file(prompt, workdir):
    """超长提示词落盘（工作目录优先，保证评审 CLI 沙箱内可读），返回绝对路径。"""
    try:
        d = workdir if workdir and os.path.isdir(workdir) else None
        fd, path = tempfile.mkstemp(prefix="tutti_prompt_", suffix=".md", dir=d)
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            f.write(prompt)
        return path
    except Exception:
        return None


# Windows 上不少 CLI 先经过 cmd.exe/.cmd 垫片，实际安全上限接近 8191，
# 而不是 CreateProcess 的理论 32767。留出命令、路径和环境参数余量，超过
# 该值统一落盘，避免 MiMo/Kimi 评审在启动前直接报「命令行太长」。
_ARGV_PROMPT_SAFE = 20000
_ARGV_PROMPT_SHIM_SAFE = 6000


def _build_call(agent, kind, sid, readonly, model, prompt, images=None, workdir=None):
    """构建一次 CLI 调用的 (argv, stdin_text, prompt, tmp_files)。model 可为 None=CLI 默认。
    images 为图片附件绝对路径：codex 用 -i 原生附图；其余 kind 忽略（调用方已过滤）。
    tmp_files：为绕开命令行长度限制落盘的指令临时文件，调用方用完（进程结束后）删除。"""
    env = {}
    argv = None
    stdin_text = None
    tmp_files = []
    imgs = [str(p) for p in (images or []) if p]
    if kind == "codex":
        cp = agent.get("codex_provider")
        reasoning = str(agent.get("reasoning_effort") or "").strip().lower()
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
        if reasoning in ("low", "medium", "high", "xhigh"):
            argv += ["-c", 'model_reasoning_effort="%s"' % reasoning]
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
        # 绑定链 env 落一次性 --settings：claude CLI 里用户 settings.json 的 env
        # 会覆盖进程 env（2026-09-22 双监听器实测），光靠 run_process 的 env 注入，
        # 链首供应商会被用户配置顶掉（a.test 毒配置案的放大器）。--settings 的
        # 优先级高于用户 settings.json（同日实测），临时文件进程结束随 tmp_files 删。
        ant = {k: v for k, v in (agent.get("env") or {}).items()
               if k.startswith("ANTHROPIC_")}
        if ant:
            try:
                fd, sp = tempfile.mkstemp(prefix="tutti_claude_settings_", suffix=".json")
                with os.fdopen(fd, "w", encoding="utf-8") as f:
                    json.dump({"env": ant}, f)
                argv += ["--settings", sp]
                tmp_files.append(sp)
            except Exception:
                pass   # 落盘失败退回纯 env 注入（旧路径），不拦运行
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
            "--yes-always", "--no-auto-commits", "--no-check-update",
            "--no-gitignore", "--no-pretty", "--no-stream",
            # 无头执行没有 Windows 控制台，aider 建 PromptSession 抛错并打一行
            # "Can't initialize prompt toolkit: No Windows console found..."
            # （非致命，但它排在输出最前，UI 失败摘要只取头部 → 真错误被顶掉，
            # 2026-09-22 mo-so 实案整条失败原因显示成这句噪声）。关掉花式输入
            # 就不再建 PromptSession，噪声从源头消失。
            "--no-fancy-input",
            # 网关自定义模型名（glm-5.1 等）litellm 全都不认识，警告页+建议列表
            # 纯属刷屏（2026-09-20 实测占满步骤日志头部）
            "--no-show-model-warnings", "--message", prompt]
        if model:
            # litellm 靠 provider 前缀路由，裸模型名直接报
            # "LLM Provider NOT provided"。前缀按本条尝试实际注入的端点协议定：
            # ANTHROPIC_BASE_URL=anthropic 面（Bigmodel 等）、OPENAI_API_BASE=
            # openai 兼容面；都没有=CLI 本机默认场景，模型名保持用户原样
            envd = agent.get("env") or {}
            if "/" not in model:
                if envd.get("ANTHROPIC_BASE_URL"):
                    model = "anthropic/" + model
                elif envd.get("OPENAI_API_BASE"):
                    model = "openai/" + model
            argv += ["--model", model]
    else:  # generic：模板把 {prompt}/{session} 嵌进参数（注意 cmd 行长度限制）
        tmpl = agent.get("argv_template") or ["-p", "{prompt}"]
        if sid and agent.get("resume_argv_template"):
            tmpl = agent["resume_argv_template"]
        argv = resolve_command(agent["command"]) + [
            str(a).replace("{prompt}", prompt).replace("{session}", sid) for a in tmpl]
        # 提示词在 argv 里 → 必须绕开 cmd 重解析（见 _npm_shim_bypass）
        argv = _npm_shim_bypass(argv)
        if "{prompt}" not in tmpl:
            stdin_text = prompt  # 恢复模板不带 {prompt}：提示词走 stdin（mimo 实测支持）
    # Windows CreateProcess 命令行上限 32767 字符：全局评审会把全书文本（约 6 万
    # 字）嵌进 argv，直接 WinError 206 启动失败（2026-09-18 七猫甜宠案，kimi 实证）。
    # 超长时落盘临时文件、参数位换成读文件指令——评审/作者 CLI 非交互模式均带
    # 读文件工具（kimi 0.43 实测可读），读不到的调用输出不可解析，由评审全挂
    # 防线兜底判失败，绝不静默降级。
    prompt_safe = _ARGV_PROMPT_SAFE
    if argv:
        # 垫片检测扫前三个 token：cmd /c xxx.CMD 形态下 argv[0] 是 cmd.exe，
        # 真正的 .cmd 垫片在 argv[2]——只看 argv[0] 会漏判，中等长度提示词
        # （6000-20000 字）留在指令行直接撞 cmd.exe 8191 上限
        # （2026-09-24 评审「命令行太长」实案）
        heads = [str(a).lower() for a in argv[:3]]
        if any(a.endswith((".cmd", ".bat")) or
               os.path.basename(a) in ("npm", "npx", "pnpm", "yarn", "bun")
               for a in heads):
            prompt_safe = _ARGV_PROMPT_SHIM_SAFE
        elif os.name == "nt" and kind == "generic":
            # Vendor wrappers are not always recognisable as npm shims. Keep
            # generic prompts below the cmd.exe limit in that case too.
            prompt_safe = _ARGV_PROMPT_SHIM_SAFE
    if argv is not None and stdin_text is None and len(prompt) >= prompt_safe \
            and argv.count(prompt) == 1:
        pf = _prompt_to_file(prompt, workdir)
        if pf:
            argv[argv.index(prompt)] = (
                "[系统] 本次完整指令因命令行长度限制已写入文件：%s\n"
                "请先用读文件工具完整读取该文件，然后把文件内容当作你的任务指令执行。" % pf)
            # Keep the original payload on stdin as a compatibility path for
            # simple generic adapters and test shims that read stdin. CLI tools
            # that intentionally use argv still receive the bounded file hint.
            if kind == "generic" and stdin_text is None and len(tmpl) > 1:
                # 模板明确将提示词放进 argv 时，原文只保存在文件里；同时再
                # 写入 stdin 会让部分包装器重复读取，也无法解决 Windows 命令行上限。
                stdin_text = None
            tmp_files.append(pf)
    return argv, stdin_text, prompt, tmp_files


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


def _activity_timeout(env_name, default=DEFAULT_STREAM_ACTIVITY_TIMEOUT_S):
    """流式 CLI 的活动延长窗口，独立于停滞看门狗配置。"""
    try:
        return max(0, int(os.environ.get(env_name, str(default))))
    except Exception:
        return default


def _max_model_attempts(has_deadline=False):
    """返回一次 runner 调用允许消耗的候选数。

    生产任务会传入统一 deadline，默认收敛到 3 个候选；没有 deadline 的
    旧式直接调用保留最多 4 个候选，以兼容已有的「同一上游两模型后换
    供应商」策略。运维可用环境变量进一步收紧/放宽，最少 1 个。
    """
    default = 3 if has_deadline else 4
    try:
        value = int(os.environ.get("TUTTI_MAX_MODEL_ATTEMPTS", str(default)))
        return max(1, min(12, value))
    except (TypeError, ValueError):
        return default


_INVALID_MODEL_NAMES = frozenset(("auto", "default", "none"))


def _invalid_model(model):
    return str(model or "").strip().lower() in _INVALID_MODEL_NAMES


def run_agent(agent, prompt, workdir=None, readonly=True,
              timeout=DEFAULT_TIMEOUT, cancel_event=None, log_path=None, resume=None,
              images=None, require_tools=False, deadline=None):
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
    主模型 + 降级备选；瞬态/配额/超时/上游拒答换下一条，取消与解析
    失败不降级；同一上游超时两次即跳过该上游剩余模型。

    5E：catalog `orch.timeout_ms`（毫秒）优先于 caller 传入的 timeout；
    单模型时限由该配置或 caller timeout 决定，任务 deadline 可进一步收紧；
    持续 stream 活动交由 stall_timeout 看门狗收口。deadline 是
    任务共享的 ``time.monotonic()`` 绝对截止时刻；传入后所有模型、空响应
    重试和限流宽限共用同一剩余预算。
    """
    # 5E：catalog orch.timeout_ms 优先
    orch_timeout_ms = (agent.get("orch") or {}).get("timeout_ms")
    if orch_timeout_ms:
        timeout = float(orch_timeout_ms) / 1000.0
    try:
        timeout = float(timeout)
    except (TypeError, ValueError):
        timeout = float(DEFAULT_TIMEOUT)
    timeout = max(0.0, timeout)
    try:
        deadline = float(deadline) if deadline is not None else None
    except (TypeError, ValueError):
        deadline = None
    if deadline is not None and time.monotonic() >= deadline:
        return {"ok": False, "text": "", "json": None, "cost_usd": 0.0,
                "tokens": 0, "usage": None, "error": "任务总时限已到，未启动模型",
                "error_code": ErrorCode.TIMEOUT,
                "raw": {"exit_code": None, "timed_out": True,
                        "deadline_exceeded": True, "duration": 0.0},
                "kind": agent.get("kind", "generic"), "model": None,
                "attempts": []}
    kind = agent.get("kind", "generic")
    if agent.get("runtime_config_sync_failed"):
        return {"ok": False, "text": "", "json": None, "cost_usd": 0.0,
                "tokens": 0, "usage": None,
                "error": agent.get("runtime_config_sync_error") or
                         "CLI 绑定配置同步失败，已阻断本次执行",
                "error_code": ErrorCode.ENV_BLOCK, "raw": None,
                "kind": kind, "model": None, "attempts": []}
    if kind == "aider":
        repo_issue = _git_repo_issue(workdir)
        if repo_issue:
            return {"ok": False, "text": "", "json": None, "cost_usd": 0.0,
                    "tokens": 0, "usage": None, "error": repo_issue,
                    "error_code": ErrorCode.VENDOR_ERROR, "raw": None,
                    "kind": kind, "model": agent.get("model"), "attempts": []}
    if kind == "codex":
        stall_t = _stall_timeout("TUTTI_CODEX_STALL_TIMEOUT", 600)
        activity_t = _activity_timeout("TUTTI_CODEX_ACTIVITY_TIMEOUT")
    elif kind == "claude":
        stall_t = _stall_timeout("TUTTI_CLAUDE_STALL_TIMEOUT", 600)
        activity_t = _activity_timeout("TUTTI_CLAUDE_ACTIVITY_TIMEOUT")
    else:
        # 数据驱动：catalog orch.stall_timeout_s——给 kimi/qwen 这类评审用
        # CLI 配置后，静默挂死即杀（qwen 900s：2026-09-22 MCP 收尾死锁案）；
        # mimo 等结束才一次性输出的仍留 0（开了必误杀），靠总超时兜底
        try:
            stall_t = max(0, int((agent.get("orch") or {}).get("stall_timeout_s") or 0))
        except Exception:
            stall_t = 0
        activity_t = 0
    # 5G：approval NEVER 一线（无人值守不静默降级）
    ok, reason = _check_approval(agent)
    if not ok:
        return {"ok": False, "text": "", "json": None, "cost_usd": 0.0, "tokens": 0,
                "usage": None, "error": reason,
                "error_code": ErrorCode.ENV_BLOCK,
                "raw": None, "kind": kind, "model": None, "attempts": []}
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
                "sid": "", "raw": None, "kind": kind, "model": None, "attempts": []}

    attempts = _resolve_attempts(agent)
    # 启动前过滤明显的占位模型名。它们常由某个 CLI 的会话标题/默认配置
    # 泄漏进编排链；让子进程自己等待网关返回会把一个确定性配置错拖满
    # 多轮超时。非法项不消耗模型调用预算，后面的真实候选仍可接手。
    filtered_attempts = []
    preflight_failures = []
    for att in attempts:
        if _invalid_model(att.get("model")):
            preflight_failures.append({
                "model": att.get("model"),
                "provider_id": att.get("provider_id") or "",
                "provider": (att.get("provider") or {}).get("name", "")
                            if isinstance(att.get("provider"), dict) else "",
                "key_id": att.get("key_id") or "",
                "protocol": _attempt_protocol(att),
                "ok": False, "skipped": True, "error":
                    "无效模型名 %r，启动前跳过" % att.get("model"),
            })
            continue
        filtered_attempts.append(att)
    attempts = filtered_attempts[:_max_model_attempts(deadline is not None)]
    out = None
    attempt_history = list(preflight_failures)
    if not attempts:
        return {"ok": False, "text": "", "json": None, "cost_usd": 0.0,
                "tokens": 0, "usage": None,
                "error": (preflight_failures[-1].get("error")
                           if preflight_failures else "没有可尝试的模型"),
                "error_code": ErrorCode.VENDOR_ERROR, "raw": None,
                "kind": kind, "model": None, "attempts": attempt_history}
    grace_left = RATE_LIMIT_GRACE_N  # 链尾限流宽限预算：整链撞 429 时原地等一个窗口再试一次
    timed_out_upstreams = {}  # 同一上游两次超时后跳过剩余模型
    skipped_upstreams = set()
    forbidden_upstreams = set()
    auth_failed_credentials = set()
    for ai, att in enumerate(attempts):
        credential = _attempt_credential(att)
        if credential in auth_failed_credentials:
            attempt_history.append({
                "model": att.get("model") or "",
                "provider_id": att.get("provider_id") or "",
                "provider": ((att.get("provider") or {}).get("name", "")
                             if isinstance(att.get("provider"), dict) else ""),
                "kind": kind, "key_id": att.get("key_id") or "",
                "protocol": _attempt_protocol(att),
                "ok": False, "skipped": True,
                "error_code": ErrorCode.VENDOR_ERROR,
                "error": "同一凭据已被认证拒绝，跳过该凭据的其他模型",
            })
            if out is not None:
                out["attempts"] = list(attempt_history)
            continue
        upstream = _attempt_upstream(att)
        if upstream in skipped_upstreams or upstream in forbidden_upstreams:
            continue
        # 有任务 deadline 时，所有候选共享同一个截止时刻；否则每个候选
        # 使用 catalog 配置或 caller 提供的单模型时限。
        model_deadline = (att.get("_deadline") or deadline or
                          (time.monotonic() + timeout))
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
            remaining = model_deadline - time.monotonic()
            if remaining <= 0:
                res = {"ok": False, "exit_code": None, "stdout": "", "stderr": "",
                       "duration": timeout, "cancelled": False, "timed_out": True,
                       "stalled": False,
                       "deadline_exceeded": deadline is not None}
            else:
                argv, stdin_text, prompt_eff, tmp_files = _build_call(
                    eff_agent, kind, sid, readonly,
                    att["model"], prompt,
                    images=images if kind == "codex" else None,
                    workdir=workdir)
                # 凭据模式起跑钉进审计头；argv 真值推导见 _claude_cred_note
                audit_notes = ([_claude_cred_note(argv)]
                               if kind == "claude" else None)
                try:
                    repeat_guard = (("Reconnecting...", 2)
                                    if kind == "codex" else None)
                    stream_abort_markers = (
                        (("stream disconnected", 1),
                         ("stream closed before response.completed", 1))
                        if kind == "codex" else None)
                    # Grok's CLI can keep a dead HTTP connection alive for the
                    # full 20-minute generic timeout. Two identical transport
                    # errors are enough evidence to stop this process and let
                    # the next bound agent try.
                    if agent.get("id") == "grok-build":
                        stream_abort_markers = (
                            ("reqwest error stream", 2),
                            ("error sending request for url", 2),
                        )
                    res = run_process(argv=argv, stdin_text=stdin_text, cwd=workdir, env=env,
                                      timeout=min(timeout, remaining), deadline=deadline,
                                      cancel_event=cancel_event,
                                      log_path=log_path, stall_timeout=stall_t,
                                      activity_timeout=activity_t,
                                      repeat_abort=repeat_guard,
                                      abort_markers=stream_abort_markers,
                                      audit_notes=audit_notes)
                finally:
                    # 超长指令临时文件：CLI 进程已结束（管道已收），即刻清场不污染工作目录
                    for tf in tmp_files:
                        try:
                            os.remove(tf)
                        except OSError:
                            pass
            out = {"ok": res["ok"], "text": "", "json": None, "cost_usd": 0.0,
                   "tokens": 0, "usage": None, "error": "", "error_code": "",
                   "sid": "", "raw": res, "kind": kind, "model": att["model"],
                   "provider_id": att.get("provider_id") or "",
                   "provider": att.get("provider") or {}}
            if not res["ok"]:
                # stderr 与 stdout 都要进错误串：codex 把 "Reading prompt from
                # stdin..." 打在 stderr，真正的配额/限流错误全在 stdout 的 JSONL
                # 里——只取其一会让 _quota_error/_transient_error 判空。
                # 先洗后切：切片会割断 ANSI 序列，留下 `[91m` 这种裸残尾直接进 UI
                tail = clean_cli_text(
                    ((res["stderr"] or "") + "\n" + (res["stdout"] or ""))[-4000:]).strip()[-600:]
                head = ("输出停滞 %ss（stall timed out，疑似卡死已提前终止）" % stall_t
                        if res.get("stalled")
                        else "超时" if res["timed_out"]
                        else "取消" if res["cancelled"]
                        else "退出码 %s" % res["exit_code"])
                if res.get("deadline_exceeded"):
                    head = "任务总时限已到"
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
                        drift = _claude_drift_note(att.get("model"), parsed_err)
                        if drift:
                            out["error"] += "；" + drift
                            _log_note(log_path, drift)
                if kind == "codex" and att.get("provider_id"):
                    el = out["error"].lower()
                    if (("wire_api" in el and "no longer supported" in el)
                            or "no route matched" in el
                            or "同一网络错误重复" in out["error"]):
                        # codex 撞 chat-only 供应商（0.154 只讲 responses）：
                        # 自动冷却该供应商 30 分钟，链展开/路由绑定分随之
                        # 自动绕开——不用人工改绑定（2026-09-17 mo-so 实测）
                        try:
                            from . import modelhub as _mh
                            _mh.note_codex_wire_dead(att["provider_id"])
                        except Exception:
                            pass
                elif "no model configured" in out["error"].lower():
                    # CLI 本体没配置（kimi 实测）→ 运行期自愈：把绑定注入其
                    # 自家配置，本次换将之后的下一轮即可用
                    try:
                        from . import manager as _mg
                        note = _mg.sync_cli_config_now(agent.get("id", ""))
                        if note and log_path:
                            try:
                                with open(log_path, "a", encoding="utf-8") as fh:
                                    fh.write("\n[自愈] %s\n" % note)
                            except Exception:
                                pass
                    except Exception:
                        pass
                if not (_quota_error(out["error"]) or _transient_error(out["error"])
                        or _rate_limited(out["error"])
                        or _permission_error(out["error"])):
                    # 尾段清洗后没有错误信号而流式审计日志里有：补日志错误行。
                    # 2026-09-22 实测 kimi 撞 429 时 stderr/stdout 只剩启动横幅
                    # （真错误只走了流式管道），路由层看不到配额关键词，换将
                    # 判死因全靠猜。
                    _augment_error_from_log(out, log_path)
                out["error_code"] = (ErrorCode.FORBIDDEN
                                      if _permission_error(out["error"])
                                      else classify_error_text(out["error"])
                                      or _classify_failure(res, kind=kind))
                if kind == "claude":
                    out["error"] += _claude_login_hint(out["error_code"],
                                                       out["error"])
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
                    out["error_code"] = (ErrorCode.FORBIDDEN
                                          if _permission_error(out["error"])
                                          else classify_error_text(out["error"])
                                          or ErrorCode.VENDOR_ERROR)
                elif not out["text"]:  # 事件流解析失败时退化为取 stdout 尾部
                    out["text"] = clean_cli_text(res["stdout"][-2000:])
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
                    out["error"] += _claude_login_hint(out["error_code"], out["error"])
                    break
                out["text"] = parsed["text"]
                out["cost_usd"] = parsed["cost_usd"]
                out["tokens"] = parsed["tokens"]
                out["usage"] = parsed["usage"]
                out["sid"] = parsed.get("sid") or ""  # §07 T1.1
                drift = _claude_drift_note(att.get("model"), parsed)
                if drift:
                    _log_note(log_path, drift)
                if parsed["is_error"]:
                    out["ok"] = False
                    out["error"] = "claude 返回 is_error: " + parsed["text"][:500]
                    if drift:
                        out["error"] += "；" + drift
                    out["error_code"] = _classify_failure(res, kind="claude", parsed=parsed)
                    break
                if not out["text"] and attempt == 0:
                    continue  # 空响应，同模型重试
                if not out["text"] and attempt == 1:
                    out["error_code"] = _classify_failure(
                        res, kind="claude", parsed=parsed, attempt_done=True)
            else:
                # generic（aider/opencode/kimi…）的 stdout 就是终端转录：ANSI 颜色、
                # \r 进度条、画线框全在里面。不清洗的话它同时污染两处——步骤摘要
                # （详情页卡片显示成 ◆/─ 墙）与评审 JSON 解析（转义混进正文）
                out["text"] = clean_cli_text(res["stdout"]).strip()
                if not out["text"]:
                    out["error_code"] = _classify_failure(res, kind=kind, empty_output=True)
            break
        _report_key(att, out)
        raw = out.get("raw") or {}
        attempt_history.append({
            "model": out.get("model") or att.get("model") or "",
            "provider_id": out.get("provider_id") or att.get("provider_id") or "",
            "provider": ((out.get("provider") or {}).get("name", "")
                         if isinstance(out.get("provider"), dict) else ""),
            "kind": kind, "key_id": att.get("key_id") or "",
            "protocol": _attempt_protocol(att),
            "ok": bool(out.get("ok")),
            "duration": raw.get("duration", 0.0),
            "tokens": out.get("tokens") or 0,
            "usage": out.get("usage") or None,
            "cost_usd": out.get("cost_usd") or 0.0,
            "timed_out": bool(raw.get("timed_out")),
            "deadline_exceeded": bool(raw.get("deadline_exceeded")),
            "abort_marker": raw.get("abort_marker"),
            "error_code": error_code_value(out.get("error_code")),
            "error": (out.get("error") or "")[:500],
            "upstream": upstream,
        })
        out["attempts"] = list(attempt_history)
        out["forbidden_upstreams"] = sorted(forbidden_upstreams)
        if out["ok"] or ai == len(attempts) - 1:
            remaining = model_deadline - time.monotonic()
            if (not out["ok"] and grace_left > 0 and _rate_limited(out.get("error"))
                    and remaining > 2
                    and not (cancel_event is not None and cancel_event.is_set())):
                # 链尾限流宽限：enumerate 活列表——把链尾这条 append 回去，
                # 下一轮迭代就是「原地重试一次」；再撞限流时预算已耗尽照常判死。
                grace_left -= 1
                grace_s = min(RATE_LIMIT_GRACE_S, remaining - 1)
                if _grace_wait(cancel_event, grace_s, log_path=log_path):
                    retry = dict(att, _deadline=model_deadline)
                    attempts.append(retry)
                    continue
            return out
        if _auth_error(out.get("error")):
            auth_failed_credentials.add(credential)
            if not any(_attempt_credential(candidate) not in auth_failed_credentials
                       for candidate in attempts[ai + 1:]):
                return out
            continue
        if _permission_error(out.get("error")):
            # 403 通常是该网关/上游拒绝当前请求；同一 host 换模型只会重撞，
            # 但不同 host 的候选仍可能有权限，应继续尝试异上游链项。
            if upstream != "unknown":
                forbidden_upstreams.add(upstream)
            out["forbidden_upstreams"] = sorted(forbidden_upstreams)
            if not any(_attempt_upstream(candidate) not in forbidden_upstreams
                       for candidate in attempts[ai + 1:]):
                return out
            continue
        # 换将闸门：什么失败值得烧链上下一个候选。
        # · 瞬态/配额（文本特征，含 CLI 内部 api_retry 的 429/5xx 字样）照旧；
        # · 超时：显式可换（看 res 标志位）——慢上游换快候选可能救回。
        #   2026-09-23 夜班实案：旧逻辑里超时能否换将全赌输出尾部碰巧含有
        #   "timeout" 字样（当时是步骤里读到的代码 timeout=900 撞的表）。
        # · 上游内容拒答（refusal）：是那家模型的过滤决定，不是任务终态，
        #   换异上游候选常常就活；判死则整 run 白烧（同案：gemini 拒答后
        #   链上云知声候选从未被尝试）。
        raw = out.get("raw") or {}
        if (raw.get("cancelled")   # 取消是用户意志，绝不降级（尾段可能带瞬态字样）
                or not (_transient_error(out.get("error")) or _quota_error(out.get("error"))
                        or _refusal_error(out.get("error"))
                        or _permission_error(out.get("error"))
                        or raw.get("timed_out"))):
            return out  # 取消/解析失败等真终态不降级
        # 同一上游的模型连续两次超时就收手——
        # 链上候选绑同一上游时换将=换壳不换命（2026-09-22 429 集群教训；
        # 2026-09-23 夜班实案同一慢上游连烧 3×20 分钟全超时零产出），最多
        # 两个模型预算后转向其他上游。其余瞬态/拒答仍按链长全部尝试：各家病根
        # 不同，每个候选都值得一次机会。
        if raw.get("timed_out"):
            timed_out_upstreams[upstream] = timed_out_upstreams.get(upstream, 0) + 1
            if timed_out_upstreams[upstream] >= 2:
                skipped_upstreams.add(upstream)
    return out


def _loads_lenient(text):
    """容错 json.loads：修复字符串内未转义引号后再解析。

    模型高频病：评审 note 里带英文直引号（"第5章"冷战三天"使用…"），
    严格 JSON 被打碎。判据：字符串内遇到 `"` 时向后看第一个非空白字符，
    是 , } ] : 视为结构性收尾引号，否则按字面引号转义。"""
    out = []
    i, n = 0, len(text)
    in_str = False
    while i < n:
        c = text[i]
        if in_str:
            if c == "\\" and i + 1 < n:
                out.append(text[i:i + 2])
                i += 2
                continue
            if c == '"':
                j = i + 1
                while j < n and text[j] in " \t\r\n":
                    j += 1
                nxt = text[j] if j < n else ""
                if nxt in ",}]:":
                    in_str = False
                    out.append(c)
                else:
                    out.append('\\"')
                i += 1
                continue
            out.append(c)
            i += 1
            continue
        if c == '"':
            in_str = True
        out.append(c)
        i += 1
    return json.loads("".join(out))


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
        try:
            return _loads_lenient(m.group(1))
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


def as_scores(gj):
    """评审解析第二道网：花括号兜底扫描会掉进第一个可平衡的子对象——
    外层 JSON 病得重时返回的是「维度→分数」本体（无 scores 键），这里包回
    评审形状，否则良评审被误判成「输出不可解析」（2026-09-18 七猫案：
    kimi 正常出分却因内嵌引号整轮判评审全挂，连环白烧自动续跑）。"""
    if isinstance(gj, dict) and not isinstance(gj.get("scores"), dict):
        vals = {k: v for k, v in gj.items()
                if isinstance(v, (int, float)) and not isinstance(v, bool)}
        if vals and len(vals) == len(gj) and len(vals) >= 3:
            return {"scores": vals, "issues": [], "summary": ""}
    return gj if isinstance(gj, dict) else None


def extract_scores_from_text(text):
    """评审解析第四道网（借鉴 BAML 的宽容提取）：模型把分数写成散文键值对
    完全不出 JSON 时（2026-09-18 真实案例：kimi 正文提分），从文本直接抓
    「维度：N 分」模式。返回 scores dict 或 {}。
    2026-09-25 批1 补：分隔符不止冒号——短横线「正确性 - 9 分」、全角等号
    「＝」、箭头「→」三种真实输出形态此前全漏，格式错被误判质量差白烧修复轮。"""
    if not text:
        return {}
    scores = {}
    for m in re.finditer(
            r"([\u4e00-\u9fa5A-Za-z][\u4e00-\u9fa5A-Za-z0-9 ]{0,11})"
            r"\s*[:：\-＝=→]\s*(\d{1,2}(?:\.\d)?)\s*分?", text):
        dim = m.group(1).strip()
        try:
            val = float(m.group(2))
        except ValueError:
            continue
        if not dim or dim in scores or not (1.0 <= val <= 10.0):
            continue
        scores[dim] = round(val, 1)
    return scores if len(scores) >= 3 else {}


def scores_from_prose(text, dims):
    """评审解析第三道网：agentic CLI 有时把 JSON 写进文件、stdout 只留中文
    总结（「情节 8 / 人物 8 / …」）。JSON 全灭后按维度名从正文提分；
    至少命中 3 个维度才认（防普通行文里的巧合数字）。"""
    if not text or not dims:
        return {}
    found = {}
    for d in dims:
        m = re.search(re.escape(d) + r"\s*[:：/是]?\s*([0-9]+(?:\.[0-9]+)?)", text)
        if m:
            try:
                found[d] = float(m.group(1))
            except (TypeError, ValueError):
                pass
    return found if len(found) >= 3 else {}
