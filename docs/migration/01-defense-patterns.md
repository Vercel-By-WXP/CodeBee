# 主题 1：防御模式与错误归一（Phase 1）

> dsh 把"已发布或差点发布的缺陷类别"文档化成 7 条不变量（[`docs/defensive-patterns.zh.md`](E:/GoOut/_dsh_ref/docs/defensive-patterns.zh.md)）。
> Tutti 已有部分对应，但多数没显式落实。本主题把它们逐条落地。

---

## 5A. 环境变量净化（env scrub）

**目标**：启动 CLI 子进程前过滤含 KEY/SECRET/TOKEN/PASSWORD 的环境变量，防止凭据泄漏到命令输出 / spill 文件 / 子进程内存。

**dsh 参考**：[`docs/defensive-patterns.zh.md#L31`](E:/GoOut/_dsh_ref/docs/defensive-patterns.zh.md#L31)；`run_process` 路径对所有 spawn 一致应用。

**Tutti 痛点**：[`app/core/runner.py:112`](E:/GoOut/MultiAgentOrchestration/app/core/runner.py#L112) `full_env = os.environ.copy()` 后未过滤，子进程可能通过 `/proc/self/environ`、`env` 命令、调试输出泄漏本机凭据。

**改动范围**：
- 新增 `app/core/env_scrub.py`（约 60 行）
- `app/core/runner.py:run_process` 在 `full_env = os.environ.copy()` 后立刻 `_scrub_env(full_env)`

**代码骨架**：
```python
# app/core/env_scrub.py
import re, logging
log = logging.getLogger(__name__)

# 黑名单：环境变量名含这些子串即剥离
_DENY_RE = re.compile(r"(KEY|SECRET|TOKEN|PASSWORD|PASSWD|CREDENTIAL)", re.I)

# 白名单：精确匹配（不分大小写），用于覆盖黑名单
_ALLOW_EXACT = {
    "PATH", "PATHEXT", "SYSTEMROOT", "TEMP", "TMP", "USERPROFILE",
    "LANG", "LC_ALL", "HOME", "SHELL", "TERM", "PYTHONIOENCODING",
    "PYTHONPATH", "PYTHONUNBUFFERED",
    "CLAUDE_CODE_ENTRYPOINT",  # Claude CLI 内部用
    "DSH_HOME", "TUTTI_HOME",
    # 厂商自有：vendor CLI 启动所需，但 base url/key 走我们的 env 体系
}
# 白名单前缀（精确字串前缀，区分大小写以匹配 Windows 习惯）
_ALLOW_PREFIX = (
    "CODEX_", "CLAUDE_CODE_", "OPENAI_", "ANTHROPIC_",
    "QWEN_", "AIDER_", "OPENCODE_",
    "TUTTI_", "ALLINONE_",
    "LANG", "LC_",  # locale 系列
)

def _should_keep(k: str) -> bool:
    if k in _ALLOW_EXACT:
        return True
    for p in _ALLOW_PREFIX:
        if k.startswith(p):
            return True
    return False

def scrub_env(env: dict, *, mode: str = "drop") -> dict:
    """返回净化后的 env 副本。

    mode:
      "drop"  - 直接删除命中黑名单的 key
      "stub"   - 把值替换为 __REDACTED__（更稳，但子进程若检查值类型可能崩）
    """
    out, dropped = {}, []
    for k, v in env.items():
        if _should_keep(k):
            out[k] = v
        elif _DENY_RE.search(k):
            if mode == "stub":
                out[k] = "__REDACTED__"
            dropped.append(k)
        else:
            out[k] = v
    if dropped:
        log.info("env-scrub dropped %d keys: %s", len(dropped), dropped[:20])
    return out
```

**测试**（`tests/test_env_scrub.py`）：
- `test_drop_obvious_secrets`：FOO_TOKEN、MY_API_KEY、GITHUB_PASSWORD → 删除
- `test_keep_vendor_keys`：CLAUDE_CODE_GIT_BASH_PATH、CODEX_HOME、OPENAI_BASE_URL → 保留
- `test_stub_mode`：mode="stub" 时删除项的值变成 `__REDACTED__`
- `test_no_leak_in_subprocess_env`：通过 `run_process(["cmd", "/c", "set"])` 抓子进程环境，断言不含 FOO_TOKEN

**风险**：
- **误杀**（低）：deny 只命中 KEY/SECRET/TOKEN/PASSWORD 等明显密钥名
- **漏过**（中）：某些 vendor 用 `MYVAR_KEY` 命名 + 不在前缀白名单 → 被删导致 CLI 失败
  - **对策**：白名单做成 `data/env_allowlist.json`，CLI 启动失败日志里高亮"env 净化"被删 key
- **影响范围**：仅 `run_process` 子进程；main 进程的 `os.environ` 不动

**回退**：白名单走 `data/env_allowlist.json` 可热改；如出问题把白名单全开即可退回原行为。

**依赖**：无。可独立提交。

---

## 5B. CLI 进程清理（kill-tree 后排空流）

**目标**：取消 / 超时后等待管道线程真正读完剩余字节，避免日志面板尾部残缺 + 孤儿子进程。

**dsh 参考**：[`docs/defensive-patterns.zh.md#L21`](E:/GoOut/_dsh_ref/docs/defensive-patterns.zh.md#L21)（dispose 必须达到完全停稳）；`packages/core/agent-loop/src/agent.ts:336-345` 的 `Session.append` 抛出让 turn-end 兜底。

**Tutti 痛点**：[`runner.py:154-159`](E:/GoOut/MultiAgentOrchestration/app/core/runner.py#L154) `_kill_tree` 立刻 break，只 `t_out.join(timeout=5)`，管道线程未必读完剩余字节 → 进程被 taskkill 后仍有未消费 stdout 残留在 `_pipe_reader`。

**改动范围**：单文件 `app/core/runner.py`，加 `_drain_streams(proc, t_out, t_err, timeout=10)`；`run_process` 在 `_kill_tree` 后调用它而不是裸 break。

**代码骨架**：
```python
# app/core/runner.py 内新增
def _drain_streams(proc, t_out: threading.Thread, t_err: threading.Thread, *, timeout=10):
    """taskkill 后等管道线程读完剩余字节。daemon=True 线程 join 超时即丢弃。"""
    deadline = time.time() + timeout
    for t in (t_out, t_err):
        remaining = max(0.0, deadline - time.time())
        t.join(timeout=remaining)

# run_process 改造
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
```

**测试**（`tests/test_runner_drain.py`）：
- 启动一个每秒 `print` 的 Python 子进程 → `cancel_event.set()` → 断言 `_drain_streams` 后 out_chunks 包含至少 N 行
- 启动一个 30s 后退出的 Python 子进程 → 给 1s timeout → 断言 `_drain_streams` 在 ≤ 5s 内返回，剩余字节已被读出

**风险**：
- 极低。drain 是只读剩余字节，不会与 taskkill 冲突。
- 边缘 case：Windows 下 taskkill /F 后子进程被强杀，Python 写入阻塞中的 stdout 会抛 `OSError`，`_pipe_reader` 已 try/except 兜住。

**回退**：把 `_drain_streams` 调用注释即可。

**依赖**：无。

---

## 5C. 重复 CLI 调用检测（repeat-call reminder）

**目标**：检测"同一个 step role 用同一份 prompt 模板连续调用 3/5/8 次"，向模型追加 user-message "请换方法"提示，防止死循环。

**dsh 参考**：[`packages/guard/repeat-tool-reminder/src/index.ts`](E:/GoOut/_dsh_ref/packages/guard/repeat-tool-reminder/src/index.ts) + README：监听 `tools/post-execute`，按 `WeakMap<Agent, Chain>` 跟踪 `(tool, canonical_args)`；3/5/8 阈值。

**Tutti 痛点**：缺重复检测，编排者反复用同一 prompt 调同一 CLI 直至 token 撑爆（`tests/test_quality_gates.py:62-75` 锁过类似场景但没自动防御）。

**改动范围**：
- 新增 `app/core/repeat_guard.py`（约 80 行）
- `app/core/pipeline.py:_run_step` 在 spawn CLI 前调用 `guard.check(role, prompt, run_id)` 拿结果，若有"提醒文本"则注入下一步 prompt

**代码骨架**：
```python
# app/core/repeat_guard.py
import hashlib, threading, logging
log = logging.getLogger(__name__)

DEFAULT_THRESHOLDS = (3, 5, 8)  # 渐进：3 提醒，5 再提醒，8 强制停止

class RepeatGuard:
    """每 (run_id, step_role) 独立计数；key = hash(prompt_template)。
    不做精确 args 比对（CLI prompt 太长，hash 已经足够分辨）。
    """
    def __init__(self, thresholds=DEFAULT_THRESHOLDS):
        self.thresholds = thresholds
        self._chains = {}  # (run_id, role) -> [(ts, key)]
        self._lock = threading.Lock()

    def _sig(self, prompt: str) -> str:
        # 仅取 prompt 中"非内容变化部分"——用首行 + 长度指纹（CLI prompt 头几行通常是模板）
        head = prompt[:200]
        return hashlib.sha1(f"{head}|{len(prompt)}".encode()).hexdigest()[:12]

    def check(self, run_id: str, role: str, prompt: str) -> dict:
        """返回 {'count': N, 'reminder': str | None, 'should_stop': bool}"""
        key = self._sig(prompt)
        chain_key = (run_id, role)
        with self._lock:
            chain = self._chains.setdefault(chain_key, [])
            chain.append((time.time(), key))
            # 仅统计相同 key 连续出现
            count = 1
            for ts, k in reversed(chain[:-1]):
                if k == key:
                    count += 1
                else:
                    break
            self._chains[chain_key] = chain[-50:]  # 保留尾窗
        reminder = None
        stop = False
        for thr in self.thresholds:
            if count == thr:
                reminder = ("⚠️ 上一步使用了几乎相同的输入（已连续 %d 次）。"
                            "请换一种方法、读不同的文件、或问用户澄清。" % count)
            if count > self.thresholds[-1]:
                stop = True
                reminder = "已连续相同输入 %d 次，超过阈值，停止本 step。" % count
        return {"count": count, "reminder": reminder, "should_stop": stop}

    def reset(self, run_id: str):
        with self._lock:
            for k in list(self._chains.keys()):
                if k[0] == run_id:
                    del self._chains[k]
```

**集成点**（`pipeline.py:_run_step`）：
```python
guard_result = repeat_guard.check(run_id, step_role, rendered_prompt)
if guard_result["reminder"]:
    rendered_prompt = guard_result["reminder"] + "\n\n---\n\n" + rendered_prompt
if guard_result["should_stop"]:
    return StepResult(stopped="repeat_guard", reason=guard_result["reminder"])
```

**测试**（`tests/test_repeat_guard.py`）：
- 同 role + 同 prompt 调 3 次 → 第 3 次返回 reminder 不为空
- 同 role + 不同 prompt 调 5 次 → 不返回 reminder
- 超过 8 次 → should_stop=True
- `reset(run_id)` 后再次调 → count 归 1
- 并发：5 个线程同时 check → 内部 lock 不死锁

**风险**：
- 误报（中）：如果 prompt 模板本身很短且变化小，可能误判。**对策**：阈值用更宽松的 `(5, 10, 15)`，并把 `_sig` 改成"prompt 中数字 ID + 文件路径列表的 hash"——更稳的指纹（暂不引入，留扩展点）。
- 漏报（低）：如果 hash 命中但实际不同（例如 prompt 改了 1 个字符）→ 提醒略多余但无害。

**回退**：`pipeline.py` 不调 `guard.check` 即可。

**依赖**：无。

---

## 5D. failure 规范化（公共约定两侧都遵守）

**目标**：把 runner 返回的 failure 信息归一成固定 dataclass，所有 pipeline 调用方消费同一形状，不再"codex 走 stdout 尾部 / claude 走 is_error / cancelled vs timeout 混在 ok"。

**dsh 参考**：[`docs/defensive-patterns.zh.md#L13`](E:/GoOut/_dsh_ref/docs/defensive-patterns.zh.md#L13)；`LlmRuntime.stream()` 规范化为 finish 分片。

**Tutti 痛点**：
- [`runner.py:176-180`](E:/GoOut/MultiAgentOrchestration/app/core/runner.py#L176) `ok = exit_code == 0 and not cancelled and not timed_out`，三个正交事实被压缩成 bool
- [`runner.py:407-418`](E:/GoOut/MultiAgentOrchestration/app/core/runner.py#L407) claude 的 `is_error` 没规范化为同结构
- [`runner.py:422-423`](E:/GoOut/MultiAgentOrchestration/app/core/runner.py#L422) codex 解析失败走尾部 → 无结构

**改动范围**：
- 新增 `app/core/run_result.py`（约 50 行）— `RunResult` dataclass
- `runner.py` 增加 `_normalize_failure(raw, vendor, parsed)` 工具函数；所有 `run_process` / `_parse_codex_jsonl` / `_parse_claude_json` 的失败返回改用它
- `pipeline.py:_run_step` 等调用点按 `RunResult` 字段判分支（**调用方小改**）

**代码骨架**：
```python
# app/core/run_result.py
from dataclasses import dataclass, field, asdict
from typing import Optional

@dataclass
class RunResult:
    """统一 CLI 执行结果——所有 vendor 都返回这个。"""
    ok: bool
    vendor: str                # "codex" | "claude" | "qwen" | ...
    exit_code: Optional[int]
    cancelled: bool
    timed_out: bool
    signal_ignored: bool = False  # 新增：发了 SIGTERM 但子进程未退出（5G 验收点）

    # 文本输出
    text: str = ""
    stderr_tail: str = ""      # 截尾 4KB 即可

    # 解析后的元信息（vendor 各自填）
    usage: dict = field(default_factory=dict)  # {"input":N,"output":N,"cached":N,"reasoning":N,"total":N}
    structured: Optional[dict] = None  # 解析出的完整 JSON（如 claude 的 total_cost_usd）
    finish_reason: str = ""     # "stop" | "length" | "tool_use" | "error" | "cancelled"

    # 错误分类（2D 错误码体系配套）
    error_code: str = ""        # "" | "VENDOR_ERROR" | "VENDOR_REFUSAL" | "MAX_TOKENS" | "TIMEOUT" | "CANCELLED" | "EMPTY" | "PARSE_FAIL" | "SIGNAL_IGNORED"
    error_message: str = ""

    def to_dict(self):
        return asdict(self)
```

**集成点**（`runner.py`）：
```python
# 替换现有返回 dict 的位置
result = RunResult(
    ok=exit_code == 0 and not cancelled and not timed_out,
    vendor=vendor_name,
    exit_code=exit_code,
    cancelled=cancelled,
    timed_out=timed_out,
    text=stdout,
    stderr_tail=stderr[-4096:],
    usage=usage,
    structured=structured,
    finish_reason=finish_reason,
    error_code=_classify(...),
    error_message=...,
)
return result
```

**测试**（`tests/test_run_result.py`）：
- `_normalize_failure({"ok":False,"exit_code":1,"timed_out":False,"cancelled":False})` 转 RunResult → `error_code="VENDOR_ERROR"`
- 同输入但 `timed_out=True` → `error_code="TIMEOUT"` 且 `timed_out=True`
- 同输入但 `cancelled=True` → `error_code="CANCELLED"`
- claude 解析出 `is_error=True` → `error_code="VENDOR_REFUSAL"`，`finish_reason="error"`
- stdout 为空 + exit_code=0 → `error_code="EMPTY"`
- 旧调用方（pipeline.py）按 `.ok` 判 → 行为不变；按 `.error_code` 判 → 新能力

**风险**：
- **调用方**：`pipeline.py` 等 ~10 处用 `result.get("ok")` 判分支的代码需改成 `result.ok`。改动面小但**全项目 grep 后改一遍**，建议一次性发 PR。
- **向兼容**：保留 `to_dict()` 输出与原 dict 形状 100% 一致，旧调用方可渐进迁移。

**回退**：保留旧返回 dict 的别名 wrapper（如 `_run_process_legacy`），让 pipeline 渐进切换。

**依赖**：无。但建议与 2D（错误码体系）一起做。

---

## 1E. crash-recovery 合成 interrupted 事件

**目标**：run 启动时如果发现上一次 `turn_start` 没有 `turn/end`，补一条 `turn/end {kind:'interrupted'}` 再开始，让 store 状态自洽。

**dsh 参考**：[`docs/subsystems/persistence.zh.md:108-114`](E:/GoOut/_dsh_ref/docs/subsystems/persistence.zh.md#L108) `interruptedTurnClosers`：仅 resume 路径回写 `turn/end {kind:'interrupted'}`。

**Tutti 痛点**：[`pipeline.py:1295-1366`](E:/GoOut/MultiAgentOrchestration/app/core/pipeline.py) worker 崩溃后，store 里残留"running"状态无 turn 终点；resume 时无法判断"上次是不是中途崩了"。

**改动范围**：
- `app/core/sessions.py`（或新 `app/core/run_recovery.py`）：新增 `reconcile_run(run_id) -> List[Event]`
- `pipeline.py:execute_run` 入口调用：读 store，对比"上次 `turn_start` 的 seq"，如有 open turn 补 `turn/end {kind:'interrupted'}` 事件

**代码骨架**：
```python
# app/core/sessions.py (新增函数)
def reconcile_run(store_path: str, run_id: str) -> int:
    """返回补写的事件条数。0 表示已一致。"""
    events = read_jsonl(store_path)  # list of {seq, type, data, ts}
    open_turns = [e for e in events if e["type"] == "turn_start"]
    close_turns = [e for e in events if e["type"] == "turn_end"]
    open_pairs = {e["turn_id"] for e in open_turns} - {e.get("turn_id") for e in close_turns}
    patched = 0
    for turn_id in open_pairs:
        # 补一条 interrupted turn_end
        new_ev = {
            "seq": max(e["seq"] for e in events) + 1 + patched,
            "type": "turn_end",
            "data": {"turn_id": turn_id, "kind": "interrupted",
                     "reason": "recovered at startup", "ts": time.time()},
        }
        events.append(new_ev)
        patched += 1
    if patched:
        # 原子写回（写 .tmp 后 rename）
        write_jsonl_atomic(store_path, events)
    return patched
```

**测试**（`tests/test_run_recovery.py`）：
- 写一个 store.jsonl 含 `turn_start` 无对应 `turn_end` → `reconcile_run` 补 1 条
- 写一个完整 turn_start/turn_end 对 → `reconcile_run` 返回 0
- 写多个 open turn → 全部补齐
- 验证：补写后文件可读、`seq` 单调、JSON 安全（可被旧 parser 解析）

**风险**：
- 极低。只在启动路径同步执行，不阻塞主流程。
- 边缘：写回时崩溃 → 下次启动再补一条（幂等性靠 `turn_id` 唯一性保证）。

**回退**：注释调用即可，store 维持原状。

**依赖**：无。

---

## 2D. 失败错误码体系（fatal vs item）

**目标**：把 `jobs.py` 当前的 `error: str` 升级为 `{ code, message }` 错误码，区分 fatal（脚本/参数错，必须人工）和 item（子任务错，可重试）。

**dsh 参考**：[`docs/subsystems/workflow.zh.md:108-120`](E:/GoOut/_dsh_ref/docs/subsystems/workflow.zh.md#L108) `WorkflowErrorCode`（`SCRIPT_PARSE` / `AGENT_START` / `RESULT_UNSERIALIZABLE` / 等）。

**Tutti 痛点**：
- [`jobs.py`](E:/GoOut/MultiAgentOrchestration/app/core/jobs.py) 失败信息是字符串
- `runner.py` 不区分 "codex 拒答" vs "代码 bug" vs "网络断"

**改动范围**：
- 新增 `app/core/error_codes.py`（约 30 行）— `ErrorCode` 枚举 + `classify_failure(result) -> (code, is_fatal)`
- `runner.py:_normalize_failure` 填 `error_code`（即 5D 的字段）
- `jobs.py` 失败处理按 `code` 分流：fatal 直接标 done，item 进入重试队列
- `pipeline.py` 跨厂商评审时按 `code` 选"换将 vs 修正 prompt"

**代码骨架**：
```python
# app/core/error_codes.py
from enum import Enum

class ErrorCode(str, Enum):
    # fatal：脚本/参数错，必须人工修
    VENDOR_ERROR = "VENDOR_ERROR"          # vendor 内部崩溃（exit != 0）
    VENDOR_REFUSAL = "VENDOR_REFUSAL"      # 明确拒绝（claude is_error / qwen refusal）
    PARSE_FAIL = "PARSE_FAIL"              # 输出无法解析
    ENV_BLOCK = "ENV_BLOCK"                # 5G approval 拒
    SIGNAL_IGNORED = "SIGNAL_IGNORED"      # 5B 杀不掉
    # item：可重试
    TIMEOUT = "TIMEOUT"
    CANCELLED = "CANCELLED"
    MAX_TOKENS = "MAX_TOKENS"              # 模型撑爆
    EMPTY = "EMPTY"                        # 空输出
    NETWORK = "NETWORK"                    # 连接失败
    CONTEXT_OVERFLOW = "CONTEXT_OVERFLOW"  # 与 MAX_TOKENS 不同：触发 1B 压缩

FATAL_CODES = {ErrorCode.VENDOR_ERROR, ErrorCode.VENDOR_REFUSAL,
               ErrorCode.PARSE_FAIL, ErrorCode.ENV_BLOCK,
               ErrorCode.SIGNAL_IGNORED}
```

**集成点**（`runner.py:_normalize_failure`）：
```python
def _classify(result_raw, parsed) -> ErrorCode:
    if result_raw["cancelled"]:
        return ErrorCode.CANCELLED
    if result_raw["timed_out"]:
        return ErrorCode.TIMEOUT
    if parsed and parsed.get("is_error"):
        return ErrorCode.VENDOR_REFUSAL
    if parsed and parsed.get("finish_reason") == "length":
        return ErrorCode.MAX_TOKENS
    if result_raw["exit_code"] != 0:
        return ErrorCode.VENDOR_ERROR
    if not parsed or not parsed.get("text", "").strip():
        return ErrorCode.EMPTY
    return ""  # ok
```

**测试**（`tests/test_error_codes.py`）：
- 各错误场景 → 期望 ErrorCode
- `FATAL_CODES` 集合断言
- `jobs.py` 重试逻辑 mock 测试：item 错误进入重试，fatal 直接 done

**风险**：低，纯枚举 + 分类器。

**依赖**：5D（同一个 RunResult 数据结构）。

---

## 5E. 每 CLI 独立超时（per-vendor timeout）

**目标**：把统一 `DEFAULT_TIMEOUT=1200` 拆为 catalog entry 级 `orch.timeout_ms`，区分"硬截止"vs"软警告"。

**dsh 参考**：[`packages/guard/timeout-policy/src/index.ts`](E:/GoOut/_dsh_ref/packages/guard/timeout-policy/src/index.ts) `ctx.tools.get(name)?.timeoutMs` 每工具独立；`deadline(signal, ms, TOOL_TIMEOUT)` 包 `exec.signal`。

**Tutti 痛点**：
- `runner.DEFAULT_TIMEOUT=1200`（20 分钟）对编排者/大纲两步过长（之前压成 60s/120s 修过，但仍写在 hardcode 里）
- 跨厂商评审耗时差异大（codex 短、claude 长），统一超时浪费

**改动范围**：
- `app/core/catalog.py`：每 entry 加 `orch.timeout_ms: int = 1200` + `orch.soft_warn_ms: int = 0`（0 表示无软警告）
- `app/core/runner.py:run_process` 接 `timeout` 参数（已有），把 catalog 的字段透传
- `app/core/pipeline.py` 用 `catalog_entry.orch.timeout_ms` 替代 hardcode

**代码骨架**：
```python
# app/core/catalog.py 新字段
{
  "id": "codex-orchestrator",
  "kind": "orchestrator",
  "orch": {
    "timeout_ms": 90_000,       # 1.5 分钟硬截止
    "soft_warn_ms": 60_000,     # 1 分钟时插入 steering "你已过半时间"
    "kill_grace_ms": 5_000,     # 5B 排空时长
  },
  ...
}

# runner.py run_process 加注释（不改主函数体）：
#   timeout 参数已支持，调用方传 catalog_entry["orch"]["timeout_ms"] 即可
```

**测试**（`tests/test_per_vendor_timeout.py`）：
- catalog entry 有 `orch.timeout_ms=1000`，启动一个 sleep 5s 的子进程 → 1s 后超时
- `orch.timeout_ms=0` → 用 `DEFAULT_TIMEOUT` 兜底
- `orch.soft_warn_ms=500` + 1s 子进程 → 0.5s 时触发"软警告"事件（可订阅）

**风险**：
- 中。需要 catalog schema 迁移（老 entry 无 `orch` 字段）。
- **对策**：在 `catalog.load()` 做字段缺省填充，老 entry 自动获默认 1200。

**回退**：catalog 字段改为可选，缺省即原行为。

**依赖**：无。但建议在 5D 之后做（共用错误码）。

---

## 5F. invariant 注册表（runtime self-check）

**目标**：把"质量闸门"从 pytest 测试升级为运行时注册表，pipeline 在 step finish 前跑断言（如"评审全部失败→禁止记 0 分"），失败抛 `InvariantError`。

**dsh 参考**：[`packages/runtime-diagnostics/invariants/src/index.ts`](E:/GoOut/_dsh_ref/packages/runtime-diagnostics/invariants/src/index.ts) + README；`enabled/package_allowlist/package_blocklist` 三过滤；失败抛 `InvariantError(packageName)`。

**Tutti 痛点**：[`tests/test_quality_gates.py`](E:/GoOut/MultiAgentOrchestration/tests/test_quality_gates.py) 只测几条路径，质量闸门在生产中悄悄失效时无运行时告警。

**改动范围**：
- 新增 `app/core/diagnostics.py`（约 80 行）
- `app/core/pipeline.py:_run_step` 末尾（`step/end` 前）调用 `invariants.run_for("pipeline", ctx)`
- `app/core/quality_gates.py` 已有断言移过来，注册为 invariant

**代码骨架**：
```python
# app/core/diagnostics.py
import logging, threading
log = logging.getLogger(__name__)

class InvariantError(Exception):
    def __init__(self, source: str, check_name: str, msg: str):
        super().__init__(f"[{source}/{check_name}] {msg}")
        self.source = source
        self.check_name = check_name

class InvariantRegistry:
    def __init__(self):
        self._checks = {}  # source -> {name: fn(ctx) -> Optional[str]}
        self._lock = threading.Lock()

    def register(self, source: str, name: str, fn):
        with self._lock:
            self._checks.setdefault(source, {})[name] = fn

    def run_for(self, source: str, ctx: dict, *, fail_fast=False):
        """返回 [(source, name, message)]；fail_fast=True 时首次失败抛 InvariantError。"""
        fails = []
        for name, fn in self._checks.get(source, {}).items():
            try:
                msg = fn(ctx)
            except Exception as e:
                msg = f"check raised: {e!r}"
            if msg:
                fails.append((source, name, msg))
                log.warning("invariant FAIL: %s/%s: %s", source, name, msg)
                if fail_fast:
                    raise InvariantError(source, name, msg)
        return fails

invariants = InvariantRegistry()  # 单例

# 注册样例（移到 quality_gates.py）
def register_default_checks():
    invariants.register("pipeline", "review_no_all_fail_zero", lambda ctx: (
        "评审全部 0 分但 run 标 success" if (
            ctx.get("review_scores") and all(s == 0 for s in ctx["review_scores"])
            and ctx.get("final_status") == "success"
        ) else None))
    invariants.register("pipeline", "step_count_consistency", lambda ctx: (
        f"step count {ctx.get('actual_steps')} != expected {ctx.get('expected_steps')}"
        if ctx.get("actual_steps") != ctx.get("expected_steps") else None))
```

**测试**（`tests/test_diagnostics.py`）：
- 注册一个故意失败的 check → `run_for` 返回 [(source, name, msg)]
- `fail_fast=True` → 抛 InvariantError
- 并发注册 + 运行 → 不死锁
- 关闭某个 source → `run_for` 跳过

**风险**：
- 中。质量闸门从"测试期"挪到"运行期"需要先以只读告警形式上线，确认无 false positive 后再 fail_fast。

**回退**：默认 `fail_fast=False`；生产观察 1 周后再切。

**依赖**：无。

---

## 5G. approval seam（一次性 + 失败关闭）

**目标**：catalog 标记 `sensitive:true` 的 step，在无人应答时**默认拒绝**而非让它跑完或假装 ok。

**dsh 参考**：[`docs/subsystems/user-approval.zh.md:12-50`](E:/GoOut/_dsh_ref/docs/subsystems/user-approval.zh.md#L12)；策略 `ask` / `never`；结果 `allowed-once | rejected | cancelled | unavailable`；缺应答者 = `unavailable` → 拒绝。

**Tutti 痛点**：无人值守场景（CI / 离线调度）下，"敏感 step"如果直接跑就是越权；如果静默跳过就是假装成功。

**改动范围**：
- `app/core/catalog.py`：加 `sensitive: bool = False`
- `app/core/runner.py` 新增 `ApprovalPolicy`（`ask` / `never`），由 `data/orchestration.json` 配
- `app/core/pipeline.py:_run_step` 前判断：若 sensitive 且 policy=never → 直接 fail，error_code=ENV_BLOCK
- `app/ui/app.js`：交互场景显示"待审批"对话框

**代码骨架**：
```python
# app/core/runner.py
class ApprovalPolicy(str, Enum):
    ASK = "ask"      # 交互场景：弹窗问用户
    NEVER = "never"  # 无人值守：直接拒绝

def _check_approval(catalog_entry, policy: ApprovalPolicy, ctx) -> tuple[bool, str]:
    if not catalog_entry.get("sensitive"):
        return True, ""
    if policy == ApprovalPolicy.NEVER:
        return False, f"sensitive step {catalog_entry['id']} blocked by policy=never"
    # policy=ask 路径（需 UI hook，5H 后做）
    return _request_user_approval(catalog_entry, ctx)
```

**测试**（`tests/test_approval_seam.py`）：
- catalog entry `sensitive=False` + policy=NEVER → 放行
- catalog entry `sensitive=True` + policy=NEVER → 拒绝，error_code=ENV_BLOCK
- catalog entry `sensitive=True` + policy=ASK → 调用 `_request_user_approval`（mock）

**风险**：
- 高。先做 NEVER 一条线（无人值守场景），ASK 等 UI 流程打通再做。
- 误拒：catalog 误标 sensitive → 步骤永远跑不动。**对策**：catalog 加 schema 校验，必须有 `reason: str` 字段说明为何敏感。

**回退**：policy 配 `ASK`（即使没 UI 也会走 mock 路径放过），等价关闭。

**依赖**：5D（共用 RunResult.error_code）、UI 协同（`app/ui/app.js` 弹窗）。

**Phase 建议**：Phase 1 仅做 NEVER 一条线；ASK 留 Phase 5。

---

## 5H. session-scoped 审计事件

**目标**：每 step 持久化 `start/content/end` + `decision: allow/deny/skip` 事件，使 resume 时可重建状态、UI 可审计"哪步被拒过"。

**dsh 参考**：[`docs/subsystems/user-approval.zh.md:78-86`](E:/GoOut/_dsh_ref/docs/subsystems/user-approval.zh.md#L78) `approval/asked`/`approval/decided` 配对写入日志，**不进模型上下文**。

**Tutti 痛点**：
- `store.write_report` 是线性文字
- resume 时不知之前状态（"上一步是被拒还是跳过？"）
- 错误被吞时无审计线索

**改动范围**：
- `app/core/store.py`：events.jsonl schema 升级（`schema_version` + 新字段）
- `app/core/pipeline.py`：每 step 写入 `step/start`、`step/content`、`step/end` + `decision/*`
- `app/core/sessions.py`：reader 支持新 schema，缺省字段填充

**代码骨架**：
```python
# app/core/store.py 新事件类型
{
  "schema_version": 2,
  "events": [
    {"seq": 1, "type": "step_start", "ts": ..., "data": {"role": "orchestrator", "step_idx": 0}},
    {"seq": 2, "type": "step_content", "ts": ..., "data": {"prompt_sha": "abc", "stdout_sha": "def", "usage": {...}}},
    {"seq": 3, "type": "step_end", "ts": ..., "data": {"ok": True, "error_code": ""}},
    {"seq": 4, "type": "decision", "ts": ..., "data": {"kind": "approval", "value": "allow", "by": "user"}},
    # 缺省字段：旧事件缺 schema_version → reader 视为 v1
  ]
}
```

**测试**（`tests/test_audit_events.py`）：
- pipeline 跑一个 3 step 的 run → store.events 包含 3*3+1=10 条事件
- resume 时 read events → 重建 step 状态
- 旧 store.jsonl (v1) → reader 兼容（缺 schema_version 视为 v1，所有字段缺省 None）

**风险**：
- 高。schema 迁移 + 旧数据兼容 + 所有写点要改。
- **对策**：先在 `data/audit/` 子目录写新格式，旧目录不变；运行 1 个月后再迁。

**回退**：events.jsonl 与 store.jsonl 并存，UI 优先读 store。

**依赖**：5D、5G（共用 decision 类型）。

**Phase 建议**：留到 Phase 5（与 5G ASK 路径一起做）。

---

## Phase 1 实施清单

按依赖顺序：

1. **5A env 净化**（独立，立刻可做）
2. **5B kill-tree 排空**（独立，立刻可做）
3. **5D failure 归一 + 2D 错误码**（合并做，共用 RunResult）
4. **5C 重复 CLI 检测**（独立，依赖 5D）
5. **1E crash-recovery**（独立，依赖 sessions.py）
6. **5E per-vendor timeout**（依赖 5D + catalog）
7. **5F invariant 注册表**（独立，可延后）
8. **5G approval NEVER 一线**（依赖 5D，Phase 5 做完整版）
9. **5H 审计事件**（Phase 5）

每个提交后更新 [README.md](../README.md) 优先级表。