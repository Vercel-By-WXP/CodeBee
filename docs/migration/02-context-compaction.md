# 主题 2：上下文压缩与会话事件溯源（Phase 2）

> dsh 的会话是只追加 `SessionEvent` 日志 + 投影派生 model 历史 + 三段式压缩。
> Tutti 现状是"扁平 JSONL 消息"，长任务易撑爆。本主题引入事件溯源 + 压缩。

---

## 1C. token-meter（压力估算）

**目标**：把 runner 已抓到的 `usage` 累加到一个 `TokenMeter`，按 model capacity 给出"压力比"，作为 1B 压缩触发条件。

**dsh 参考**：[`packages/llm/token-meter/src/index.ts:74`](E:/GoOut/_dsh_ref/packages/llm/token-meter/src/index.ts#L74) `ReplayTokenMeter`：滑动窗口 + usage 锚点 + 失败重定价。

**Tutti 痛点**：暂无直接痛点，但 1B 必需；当前 `usage` 只回传给 UI 显示，无累计。

**改动范围**：
- 新增 `app/core/token_meter.py`（约 60 行）
- `runner.py:_parse_*` 解析后调 `meter.accumulate(run_id, model, usage)`
- `pipeline.py` 调 `meter.pressure_ratio(run_id)` 判断是否触发压缩

**代码骨架**：
```python
# app/core/token_meter.py
import threading, time
from collections import deque

# 模型 capacity 表（可放 data/model_capacity.json 热改）
DEFAULT_CAPACITY = {
    "claude-sonnet-4-5": 200_000,
    "gpt-5": 400_000,
    "deepseek-chat": 128_000,
    "deepseek-reasoner": 64_000,
    "qwen3-max": 256_000,
}

class TokenMeter:
    def __init__(self, capacity=None):
        self.capacity = capacity or DEFAULT_CAPACITY
        self._lock = threading.Lock()
        self._windows = {}  # run_id -> deque[(ts, input, output, reasoning)]

    def accumulate(self, run_id: str, model: str, usage: dict):
        with self._lock:
            window = self._windows.setdefault(run_id, deque(maxlen=100))
            window.append((
                time.time(),
                usage.get("input", 0),
                usage.get("output", 0),
                usage.get("reasoning", 0),
            ))

    def used(self, run_id: str) -> int:
        """估算总占用（input + output + reasoning）。"""
        with self._lock:
            window = self._windows.get(run_id, [])
            return sum(i + o + r for _, i, o, r in window)

    def pressure_ratio(self, run_id: str, model: str) -> float:
        """已用 / 容量。>0.8 触发 1B。"""
        cap = self.capacity.get(model, 128_000)
        return self.used(run_id) / max(cap, 1)

    def reset(self, run_id: str):
        with self._lock:
            self._windows.pop(run_id, None)

token_meter = TokenMeter()
```

**集成点**（`runner.py`）：
```python
# 在 _parse_codex_jsonl / _parse_claude_json 末尾
token_meter.accumulate(run_id, model, usage)
```

**测试**（`tests/test_token_meter.py`）：
- 累加 3 次 usage → `used()` = 3 次之和
- 累加到 80% capacity → `pressure_ratio()` ≥ 0.8
- `reset()` 后 used = 0
- 并发 accumulate → lock 不死锁

**风险**：极低，纯累加器。

**回退**：默认 capacity 表不命中时用 128K 兜底，保守触发。

**依赖**：无。

---

## 1A. surface 日志 + 派生（事件溯源）

**目标**：把 session 内容从"扁平 JSONL 消息列表"改为"追加事件流 + surface 派生"，system 消息统一从日志注入，不再散落在 6 套 prompt 模板里。

**dsh 参考**：
- [`docs/architecture.zh.md#会话日志`](E:/GoOut/_dsh_ref/docs/architecture.zh.md#会话日志) — 关键不变量："模型可见即已记录"
- [`packages/core/session/src/types.ts:222-271`](E:/GoOut/_dsh_ref/packages/core/session/src/types.ts#L222) `SessionEvent` 类型
- [`packages/core/session/src/surface.ts:140-167`](E:/GoOut/_dsh_ref/packages/core/session/src/surface.ts#L140) surface 同步校验

**Tutti 痛点**：
- 跨多轮上下文撑爆
- 失败重试偶发漏写 system message（`pipeline.py:184-230,493-595` 6 套 prompt 模板手工 `__GOAL__/__SUBTASK__/__CONTEXT__` 替换）
- 多个 flow 的 prompt 模板不一致

**改动范围**：
- 新增 `app/core/session_log.py`（约 250 行）：`Session` 类 + `append(type, data, surfaceOp)` + `derive_messages()`
- 新增 `app/core/session_projection.py`（约 100 行）：fold helpers
- `pipeline.py:_run_step` 改造（**大改**，建议新写 `pipeline_v2.py`，旧版保留）

**代码骨架**：
```python
# app/core/session_log.py
import json, threading, time
from dataclasses import dataclass, field
from typing import Literal, Optional

@dataclass
class SessionEvent:
    seq: int
    type: Literal[
        "session_start", "turn_start", "step_start",
        "system_message", "user_message", "assistant_message",
        "tool_call", "tool_result",
        "compaction_start", "compaction_summary", "compaction_end",
        "decision",
        "turn_end", "session_end",
    ]
    data: dict
    ts: float = field(default_factory=time.time)
    surface_op: Optional[str] = None  # None|"append"|{"op":"replace","start_seq":N,"end_seq":M}
    turn_id: Optional[str] = None

    def to_dict(self):
        d = {"seq": self.seq, "type": self.type, "data": self.data, "ts": self.ts}
        if self.surface_op is not None:
            d["surface_op"] = self.surface_op
        if self.turn_id:
            d["turn_id"] = self.turn_id
        return d

class Session:
    """只追加事件流；表面通过 surface_op 折叠。

    关键不变量：append 是同步原子；append 后立即可 derive_messages()。
    """
    def __init__(self, run_id: str, store_path: str):
        self.run_id = run_id
        self.store_path = store_path
        self._events: list[SessionEvent] = []
        self._lock = threading.Lock()
        self._seq = 0
        self._replace_generation = 0

    def append(self, ev_type: str, data: dict, *, surface_op=None, turn_id=None) -> SessionEvent:
        with self._lock:
            self._seq += 1
            ev = SessionEvent(seq=self._seq, type=ev_type, data=data,
                              surface_op=surface_op, turn_id=turn_id)
            self._events.append(ev)
            self._persist_one(ev)
            if isinstance(surface_op, dict) and surface_op.get("op") == "replace":
                self._replace_generation += 1
            return ev

    def derive_messages(self) -> list[dict]:
        """按 surface 折叠出模型输入消息列表。
        模型仅看到：system + 进入的 user + 已 settle 的 assistant + tool result。
        """
        # ... 按 surface_op 折叠：
        #  1. 维护一个 surface list（初始空）
        #  2. append 时按 surface_op 决定 add 或 replace(start..end)
        #  3. 返回当前 surface 列表
        ...

    def _persist_one(self, ev: SessionEvent):
        """追加到 JSONL（原子写：先写 .tmp 后 rename）。"""
        with open(self.store_path, "a", encoding="utf-8") as f:
            f.write(json.dumps(ev.to_dict(), ensure_ascii=False) + "\n")

    def replace_generation(self) -> int:
        return self._replace_generation
```

**集成点**（`pipeline_v2.py`）：
```python
# 替换 _run_step 内字符串拼 prompt 的逻辑
session = Session(run_id, store_path)
# 注入 system（按角色）
session.append("system_message", {"role": step_role, "content": system_prompt_for_role(step_role)})
# 注入本步输入
session.append("user_message", {"content": user_prompt})
# 派生模型输入
messages = session.derive_messages()
# 调 CLI
result = runner.run_agent(messages, ...)
# 注入 assistant 回复
session.append("assistant_message", {"content": result.text, "usage": result.usage, "finish_reason": result.finish_reason})
# 工具结果（如果有）
for call, tool_result in result.tool_calls:
    session.append("tool_call", call)
    session.append("tool_result", tool_result)
```

**测试**（`tests/test_session_log.py`）：
- append 三个 user_message → derive_messages 返回 3 条 user
- append 一个 surface_op=replace(start=1,end=2) → derive_messages 折叠为 2 条 user（不是 3）
- 并发 append → seq 单调
- _persist_one 写一半崩溃 → reload 时 JSON 跳过坏行（lazy skip）
- 旧 store.jsonl（无 seq 字段） → Session 启动时转换层补 seq

**风险**：
- **改造面广**。`pipeline.py` 大部分内容需重写。
- **策略**：先写 `pipeline_v2.py` + 新 flag `use_v2=True`，老 flow 仍走旧版；灰度切换。
- 灰度期间 store.jsonl 格式两种并存 → reader 兼容层。

**回退**：flag 关，回旧 pipeline。

**依赖**：1C（pressure 触发）、1B（压缩后调 append）。

---

## 1B. 三段式压缩

**目标**：跑长任务前自动把"上次会话尾巴 + 本轮工具输出"的超大文本先头尾剪掉，再触发一次 LLM 摘要把整段压缩成一个 `user_message(surfaceOp='replace')` 节点。

**dsh 参考**：
- [`packages/compaction/compaction-basic/src/region.ts:117-155`](E:/GoOut/_dsh_ref/packages/compaction/compaction-basic/src/region.ts#L117) `selectCompactableRange`（跳过 system head、保留尾预算、不切断 tool call/result 配对）
- [`packages/compaction/compaction-basic/src/summarizer.ts:31-179`](E:/GoOut/_dsh_ref/packages/compaction/compaction-basic/src/summarizer.ts#L31) 摘要器复用 system prompt + tools + prefix 触发 prefix-cache 复用
- [`packages/compaction/compaction-tool-result-pruner/src/index.ts:83-122`](E:/GoOut/_dsh_ref/packages/compaction/compaction-tool-result-pruner/src/index.ts#L83) 头尾剪枝

**Tutti 痛点**：长任务 max_tokens 触发、planner 长输出偶发超时（v5 已修，但底层风险仍在）。

**改动范围**：
- 新增 `app/core/compaction.py`（约 200 行）：`prune_tool_results(session)` + `compact_region(session, start, end, llm_caller)` + `select_range(session, retain_tail_tokens)`
- `pipeline_v2.py:_run_step` 前调 `maybe_compact(session, pressure)`

**代码骨架**：
```python
# app/core/compaction.py
from typing import Callable
from .token_meter import token_meter
from .session_log import Session

def _estimate_tokens(text: str) -> int:
    """极简估算：4 字符/token。"""
    return max(1, len(text) // 4)

def select_range(session: Session, *, retain_tail_tokens=8000, max_region_tokens=60_000) -> tuple[int, int]:
    """返回 (start_seq, end_seq)，跳过 system head + 工具配对。
    保留尾部 N token；中间部分不能超过 max_region_tokens。
    """
    events = session._events
    # 1. 找到最早 user_message 的 seq（system 不参与压缩）
    first_user_seq = next((e.seq for e in events if e.type == "user_message"), 1)
    # 2. 倒推保留尾部 token
    tail_budget = 0
    tail_start = len(events) - 1
    for i in range(len(events) - 1, -1, -1):
        ev_text = str(events[i].data.get("content", "") or events[i].data.get("stdout", ""))
        tail_budget += _estimate_tokens(ev_text)
        if tail_budget >= retain_tail_tokens:
            tail_start = i
            break
    # 3. region = [first_user_seq, tail_start)
    region_end = min(tail_start, first_user_seq + 50)  # 不超过 50 个事件
    # 4. 不切断 tool_call/tool_result 配对：end 必须是偶数配对完成的位置
    while region_end < tail_start:
        ev_at_end = events[region_end]
        if ev_at_end.type in ("tool_call", "tool_result"):
            region_end -= 1
        else:
            break
    return (first_user_seq, region_end)

def prune_tool_results(events, *, head=8192, marker="...[pruned]...", tail=4096) -> list[dict]:
    """对每个 tool_result 内容做头尾剪枝，纯无 LLM 调用。"""
    out = []
    for ev in events:
        if ev.type == "tool_result":
            content = ev.data.get("content", "")
            if _estimate_tokens(content) > head + tail + 1000:
                pruned = (content[:head] + "\n\n" + marker + "\n\n"
                          + content[-tail:])
                ev = ev.__class__(**{**ev.__dict__, "data": {**ev.data, "content": pruned}})
        out.append(ev)
    return out

def compact_region(session: Session, start_seq: int, end_seq: int,
                   llm_caller: Callable[[list[dict]], str], *, reason: str = "pressure"):
    """三件事件：compaction_start → compaction_summary → user_message(surfaceOp=replace) → compaction_end。
    失败仍 append compaction_end {error} 保证日志可见。
    """
    tx = session.append("compaction_start", {"start_seq": start_seq, "end_seq": end_seq, "reason": reason})
    try:
        # 1. 收集 region 文本
        region_text = "\n\n".join(
            str(e.data.get("content", "") or e.data.get("stdout", ""))
            for e in session._events[start_seq-1:end_seq]
        )
        # 2. 先剪枝
        region_text = prune_tool_results_text(region_text)  # 纯文本版
        # 3. LLM 摘要
        summary = llm_caller([{
            "role": "system",
            "content": "请把以下对话压缩成简洁摘要，保留关键决策、文件路径、未完成项、错误信息。"
        }, {
            "role": "user",
            "content": region_text,
        }])
        # 4. 写 compaction_summary（记录 raw）
        session.append("compaction_summary", {
            "raw_tokens": _estimate_tokens(region_text),
            "summary": summary,
            "model": llm_caller.__self__.current_model,
        })
        # 5. surface replace
        session.append("user_message", {
            "content": f"[系统压缩摘要：先前 {end_seq-start_seq} 步已压缩]\n\n{summary}",
        }, surface_op={"op": "replace", "start_seq": start_seq, "end_seq": end_seq})
    except Exception as e:
        session.append("compaction_summary", {"error": repr(e)})
    finally:
        session.append("compaction_end", {"tx_seq": tx.seq})

def maybe_compact(session: Session, *, model: str, pressure_threshold=0.8,
                  llm_caller=None) -> bool:
    """检查压力比；超阈值则触发压缩。返回 True 表示做了压缩。"""
    if llm_caller is None:
        return False  # 没人调用 LLM 就别做
    ratio = token_meter.pressure_ratio(session.run_id, model)
    if ratio < pressure_threshold:
        return False
    start, end = select_range(session)
    if end <= start:
        return False
    compact_region(session, start, end, llm_caller, reason=f"pressure={ratio:.2f}")
    return True
```

**集成点**（`pipeline_v2.py:_run_step`）：
```python
# 在 spawn CLI 前
maybe_compact(session, model=step_model, llm_caller=runner.chat_for_compaction)
messages = session.derive_messages()
result = runner.run_agent(messages, ...)
session.append("assistant_message", {...})
```

**测试**（`tests/test_compaction.py`）：
- `select_range`：给 100 个 user_message → 返回 (1, 90)
- 不切断 tool_call/tool_result 配对 → region_end 在 user_message 上
- `prune_tool_results`：8192 字符以上 → head+marker+tail
- `compact_region`：mock llm_caller → 三件事件写入 + surface replace 触发 replace_generation+1
- 失败路径：llm_caller 抛异常 → compaction_end {error} 仍写入
- `maybe_compact`：压力比 < 0.8 → 不做压缩；> 0.8 且 llm_caller 存在 → 做

**风险**：
- **多一次 LLM 调用**：费用 ↑，可设 `pressure_threshold=0.9` 减少频率；可加开关 `data/orchestration.json: compaction.enabled`
- **摘要信息丢失**：选 compact region 错了 → 关键决策被吞
  - **方案**：`retain_tail_tokens=8000` 偏保守；可选 `data/retain.json` 调
- **重复触发**：LLM 调用期间 usage 又累加 → 再触发。**方案**：`maybe_compact` 加 mutex

**回退**：`data/orchestration.json: compaction.enabled = false` 即可，所有路径跳过。

**依赖**：1A（surface 日志）、1C（pressure 触发）。

---

## 1D. 重试复用已渲染 prompt

**目标**：失败的 step 内重试只在"产生了 durable 进展"时才走，并把已渲染好的 prompt 复用，不再重拼。

**dsh 参考**：[`packages/core/agent-loop/src/agent.ts:307-378`](E:/GoOut/_dsh_ref/packages/core/agent-loop/src/agent.ts#L307) 重试在"前进 surface generation"时返回 `{kind:'retry'}`，复用渲染好的 `PromptAssembly`，不重跑 `pre-step` / `system-prompt/assemble` / 用户 message 准入。

**Tutti 痛点**：
- 失败重试时偶尔重复组装（planner → outline → runner 的串接在重试里重拼）
- 漏写 system message（review→fix 循环里）

**改动范围**：
- `app/core/pipeline.py`（或 `pipeline_v2.py`）：`_run_step` 记录 `step.assembly`（已渲染 prompt + 当前 surface gen）
- `runner.run_agent` 返回 `CONTEXT_OVERFLOW` 时，**只在 compact 完成后 surface gen +1** 才走"重试复用"路径
- `pipeline.py:_run_code` 的 review→fix 循环复用 system message

**代码骨架**：
```python
# app/core/pipeline_v2.py
@dataclass
class StepAssembly:
    run_id: str
    role: str
    step_idx: int
    messages: list[dict]            # 已渲染的 LLM 输入
    surface_generation_at_call: int  # spawn 时的 generation
    caller_kwargs: dict             # 含 model、max_tokens 等

def _run_step(session, role, step_idx, *, llm_caller, ...):
    # 1. 渲染 prompt（一次性）
    messages = build_prompt_for_role(session, role, step_idx)
    assembly = StepAssembly(
        run_id=session.run_id, role=role, step_idx=step_idx,
        messages=messages,
        surface_generation_at_call=session.replace_generation(),
        caller_kwargs={"model": step_model, "max_tokens": step_max_tokens},
    )
    # 2. spawn
    result = runner.run_agent(assembly.messages, **assembly.caller_kwargs)
    # 3. 失败重试：只在 surface gen 前进时才复用
    if not result.ok and result.error_code == "CONTEXT_OVERFLOW":
        # 触发 compact → surface gen +1
        compacted = maybe_compact(session, model=step_model, llm_caller=...)
        if compacted and session.replace_generation() > assembly.surface_generation_at_call:
            # 重用 messages 但 derive_messages 重算（surface 已变）
            new_messages = session.derive_messages()
            result = runner.run_agent(new_messages, **assembly.caller_kwargs)
    # 4. 记日志
    session.append("assistant_message", {...})
    return result
```

**测试**（`tests/test_step_retry.py`）：
- mock runner.run_agent：第一次 CONTEXT_OVERFLOW → 触发 compact → 重试
- mock compact 不前进 generation → 不重试，沿用原错误
- 重试时 messages 与首次不同（surface gen 已变）

**风险**：低，纯重试逻辑。

**回退**：保留旧 `_run_step` 路径。

**依赖**：1A、1B。

---

## Phase 2 实施清单

按依赖顺序：

1. **1C token-meter**（独立，立刻可做）
2. **1A surface 日志**（大改，先写 pipeline_v2.py 灰度）
3. **1B 三段式压缩**（依赖 1A）
4. **1D 重试复用**（依赖 1A + 1B）

每完成一项：pipeline_v2.py 灰度开关 → 跑回归 → 切默认。

---

## Phase 2 风险与回退

- 整体改造面广，但**纯追加**（旧 session.jsonl 兼容）。
- 默认灰度开关 `data/orchestration.json: session_v2 = false`。
- 观察 1 个月（30+ run 无回归）后切默认 true。
- 万一出问题：`session_v2 = false` 回旧版，老 store.jsonl 完整可用。