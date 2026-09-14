# 主题 3：状态外置与任务板（Phase 3）

> dsh 的 goal / plan / todo 三件套正交解决不同时间尺度：goal（长期目标）+ plan（协作模式）+ todo（单 agent 任务清单）。
> Tutti 当前 goal/plan 信息隐在 LLM 调用里，没有持久任务板。本主题外置 + 加 CAS + 续行驱动器。

---

## 2A. Goal/Plan 状态外置

**目标**：把 Tutti 当前隐藏在 `planner.py` LLM 调用里的"目标/章节大纲"，抽成独立 `GoalService`，每目标带 `revision` CAS、`phase`（active/paused/complete）、`roundsStarted`；持久化用现有 `app/core/store.py`。

**dsh 参考**：
- [`packages/goal/goal/`](E:/GoOut/_dsh_ref/packages/goal/goal/) — 状态 100% 来自事件溯源（`goal/change` 事件），唯一当前 goal，`revision` 走 CAS
- [`docs/subsystems/goal.zh.md:67-117`](E:/GoOut/_dsh_ref/docs/subsystems/goal.zh.md#L67) 进程本地 `activation: armed/disarmed`

**Tutti 痛点**：拆解后子任务之间没有持久"任务板/信箱"，跨轮上下文散落。

**改动范围**：
- 新增 `app/core/goal_service.py`（约 200 行）
- 拆分 `app/core/planner.py`：原 `planner.py` 保留为 LLM 调用层，新 `goal_service.py` 状态层
- `app/core/store.py`：events.jsonl 加 `goal/change` 事件类型
- `app/core/jobs.py`：订阅 goal phase 变化做续行（2B）

**代码骨架**：
```python
# app/core/goal_service.py
from dataclasses import dataclass, field
from typing import Optional, List
import threading, time, uuid
from .store import append_event, read_events

@dataclass
class Goal:
    goal_id: str
    title: str
    description: str
    phase: str = "active"            # "active" | "paused" | "complete" | "abandoned"
    revision: int = 0
    rounds_started: int = 0
    rounds_max: int = 5               # 续行上限
    created_at: float = field(default_factory=time.time)
    updated_at: float = field(default_factory=time.time)
    parent_goal_id: Optional[str] = None
    metadata: dict = field(default_factory=dict)

class StaleRevisionError(Exception):
    pass

class GoalService:
    """单一当前 goal（per session）。所有变更走 CAS revision。"""
    def __init__(self, store_path: str):
        self.store_path = store_path
        self._lock = threading.Lock()
        self._current: Optional[Goal] = None
        self._load()

    def _load(self):
        events = read_events(self.store_path, types=["goal_change"])
        if not events:
            return
        # 折叠出当前 goal 状态（取最后一个 goal_change 事件）
        latest = events[-1]
        self._current = Goal(**latest["data"]["goal"])

    def current(self) -> Optional[Goal]:
        return self._current

    def create(self, *, title: str, description: str, parent_goal_id=None,
               rounds_max=5) -> Goal:
        with self._lock:
            if self._current is not None:
                raise ValueError(f"已有当前 goal {self._current.goal_id}，需先 complete 或 abandon")
            goal = Goal(
                goal_id=str(uuid.uuid4()),
                title=title, description=description,
                parent_goal_id=parent_goal_id, rounds_max=rounds_max,
            )
            self._persist(goal, action="create")
            self._current = goal
            return goal

    def update(self, goal_id: str, *, expected_revision: int, **changes) -> Goal:
        with self._lock:
            if self._current is None or self._current.goal_id != goal_id:
                raise ValueError(f"no current goal {goal_id}")
            if self._current.revision != expected_revision:
                raise StaleRevisionError(
                    f"expected rev {expected_revision}, current {self._current.revision}")
            for k, v in changes.items():
                setattr(self._current, k, v)
            self._current.revision += 1
            self._current.updated_at = time.time()
            self._persist(self._current, action="update")
            return self._current

    def increment_rounds(self, goal_id: str, *, expected_revision: int) -> Goal:
        return self.update(goal_id, expected_revision=expected_revision,
                           rounds_started=self._current.rounds_started + 1)

    def _persist(self, goal: Goal, *, action: str):
        append_event(self.store_path, {
            "type": "goal_change",
            "data": {"action": action, "goal": goal.__dict__},
            "ts": time.time(),
        })
```

**集成点**（`planner.py`）：
```python
# 替换原 planner 里的"目标字典"逻辑
goal_svc = GoalService(store_path)
goal = goal_svc.current() or goal_svc.create(
    title=task.title,
    description=task.description,
    rounds_max=getattr(task, "max_rounds", 5),
)
# ... LLM 拆解时把 goal 状态注入 prompt
```

**测试**（`tests/test_goal_service.py`）：
- create → current 返回新 goal
- 二次 create → 抛 ValueError
- update expected_revision 正确 → revision +1
- update expected_revision 错 → StaleRevisionError
- 并发 update 同一 goal → 一个成功一个失败
- abandon → current 仍可访问（但 phase=abandoned）
- reload from store → 折叠出正确状态

**风险**：
- 中。事件日志格式扩展（schema_version 升级）。
- **对策**：旧 event 无 `goal_change` 类型 → loader 跳过；新事件可独立写。

**回退**：`goal_service = None` 时走老 planner 路径。

**依赖**：无（planner.py 不依赖 job，但 jobs.py 订阅它）。

---

## 2B. Goal Round Driver（无人值守续行）

> **⚠️ 2026-09-14 缓期：与 Tutti v5 自驱闭环重叠。**
> 复核发现 Tutti 已有等价机制：`jobs.resume_interrupted()` + `_maybe_auto_resume()`
> （连载任务断点续跑，v5 落地并经 2 万字零干预验收）。再建独立的 Round Driver
> 会造成双通道续行——同一任务既被 resume 逻辑接手又被 driver 排队，产生重复入队。
> **GoalService（2A）已落地**，独立目标状态就绪；未来若需要"不挂任务的独立目标"
> （如跨任务长期目标），把 driver 挂到 goal_service.current() 的 armed 边沿即可，
> 前置条件是给 jobs.enqueue 加去重键。

**目标**：当 goal `active && armed && roundsStarted<maxGoalRounds` 且当前 agent 空闲时，自动排入下一条 `<goal_round>` 提示词；用 `agent/pre-step` 做 CAS 竞态防护。

**dsh 参考**：[`packages/goal/goal-round-driver/`](E:/GoOut/_dsh_ref/packages/goal/goal-round-driver/) `README.zh.md:62-72` active + armed + rounds < max 时排入 `<goal_round>` user message；只负责轮次调度，不判断"是否真的完成"。

**Tutti 痛点**：无结构化的"持续接力 / 自驱打磨"机制（v5 自驱闭环已部分实现但耦合在 pipeline.py）。

**改动范围**：
- 新增 `app/core/goal_round_driver.py`（约 100 行）
- `app/core/jobs.py`：订阅 GoalService phase=active 且 armed + rounds<max → 排下一轮

**代码骨架**：
```python
# app/core/goal_round_driver.py
import threading, time, logging
log = logging.getLogger(__name__)

STEERING_PROMPT = """\
[Goal Round {round}/{max}]
当前目标：{title}
描述：{description}

上轮总结（来自前一轮 assistant_message）：{prev_summary}

请继续推进。如果遇到阻塞，调用 abort 标记 goal 为 paused。
"""

class GoalRoundDriver:
    def __init__(self, goal_svc, runner, jobs_svc, *, idle_check_fn):
        self.goal_svc = goal_svc
        self.runner = runner
        self.jobs_svc = jobs_svc
        self.idle_check = idle_check_fn   # run_id -> bool
        self._stop = threading.Event()
        self._thread = threading.Thread(target=self._loop, daemon=True)
        self._thread.start()

    def _loop(self):
        while not self._stop.is_set():
            try:
                self._tick()
            except Exception as e:
                log.exception("goal round driver tick failed: %r", e)
            self._stop.wait(5.0)  # 5s 心跳

    def _tick(self):
        goal = self.goal_svc.current()
        if goal is None or goal.phase != "active":
            return
        if goal.rounds_started >= goal.rounds_max:
            log.info("goal %s hit max rounds %d", goal.goal_id, goal.rounds_max)
            return
        if not self.idle_check(goal.goal_id):
            return  # 还在跑
        # CAS：先读 revision，再 increment；中间被改就跳过这一 tick
        try:
            goal = self.goal_svc.increment_rounds(goal.goal_id, expected_revision=goal.revision)
        except StaleRevisionError:
            return  # 下个 tick 再试
        # 排下一轮
        prompt = STEERING_PROMPT.format(
            round=goal.rounds_started, max=goal.rounds_max,
            title=goal.title, description=goal.description,
            prev_summary=self._last_summary(goal.goal_id),
        )
        self.jobs_svc.enqueue_round(goal.goal_id, prompt)

    def _last_summary(self, goal_id):
        # 从 store 读最近一条 assistant_message 摘要
        return "(no previous round)"

    def stop(self):
        self._stop.set()
        self._thread.join(timeout=10)
```

**集成点**（`jobs.py`）：
```python
# 启动时
goal_round_driver = GoalRoundDriver(
    goal_svc, runner, self,
    idle_check_fn=lambda run_id: not self.is_busy(run_id),
)
# 停止时
goal_round_driver.stop()
```

**测试**（`tests/test_goal_round_driver.py`）：
- goal phase=active, rounds=0/5, idle=True → enqueue_round 被调用 1 次
- goal phase=paused → 不调用
- rounds=5/5 → 停止
- idle=False → 不调用
- CAS 失败（中间被改） → 不调用
- 异常 → _loop 不死

**风险**：
- **无界续行**：`rounds_max=5` 是唯一物理闸。建议 5 起步，按需放宽。
- **摘要质量**：`_last_summary` 当前只读最后一条 assistant_message，可能丢关键信息。**对策**：先简单实现，留扩展点。

**回退**：`goal_round_driver = None` 即关闭。

**依赖**：2A。

---

## 2C. revision + CAS 任务板

> **⚠️ 2026-09-14 实施时按 Tutti 实际架构最小化。**
> 设计稿原假设 jobs.py 有可变 Job 板（多执行方并发改同一 Job 状态）。实际 Tutti
> 的 jobs.py 是 worker 池 + `store.update_run` 集中状态迁移（LOCK 保护），
> 不存在"Job 板"结构。真实风险点是防御模式「异步状态不是同步状态」：
> 陈旧 worker（被取消/崩溃恢复前）覆盖新状态。
> **落地形式**：`store.update_run(run_id, expected_status=None, **fields)` ——
> expected_status 非 None 时做 CAS，不匹配返回 None 不写入。
> 8 测试绿（tests/test_run_cas.py），含 recover_orphaned_runs 重放安全用例。

原设计稿（保留备查）：把 `jobs.py` 的 `Job` 模型加上 `revision: int` 字段；任何状态变更走 CAS（`expectedRevision` 不匹配即失败），失败方重新读最新板。

**目标**：把 `jobs.py` 的 `Job` 模型加上 `revision: int` 字段；任何状态变更走 CAS（`expectedRevision` 不匹配即失败），失败方重新读最新板。

**dsh 参考**：[`docs/subsystems/goal.zh.md:128-136`](E:/GoOut/_dsh_ref/docs/subsystems/goal.zh.md#L128) `goal/change` 全量快照 + revision CAS。

**Tutti 痛点**：并发 agent 完成后没有结构化的多轮辩论 / 议程推进；多 jobs 同时改同一 Job 状态时无并发保护。

**改动范围**：
- `app/core/jobs.py`：Job dataclass 加 `revision`；状态变更统一走 `update_job(job_id, expected_rev, **changes)`
- `app/core/store.py`：job 状态写盘带 revision

**代码骨架**：
```python
# app/core/jobs.py（增量改动）
@dataclass
class Job:
    job_id: str
    run_id: str
    role: str
    status: str = "pending"            # pending|running|done|failed|skipped
    revision: int = 0
    payload: dict = field(default_factory=dict)
    result: Optional[dict] = None
    error_code: str = ""
    created_at: float = field(default_factory=time.time)
    updated_at: float = field(default_factory=time.time)

class StaleJobRevisionError(Exception):
    pass

class JobBoard:
    def update(self, job_id: str, *, expected_revision: int, **changes) -> Job:
        with self._lock:
            job = self._jobs[job_id]
            if job.revision != expected_revision:
                raise StaleJobRevisionError(...)
            for k, v in changes.items():
                setattr(job, k, v)
            job.revision += 1
            job.updated_at = time.time()
            self._persist(job)
            return job
```

**集成点**：
```python
# 所有改 Job.status 的地方
try:
    board.update(job_id, expected_revision=current_rev, status="running")
except StaleJobRevisionError:
    job = board.get(job_id)  # 重读
    # 重新判断状态
```

**测试**（`tests/test_job_cas.py`）：
- update 正确 revision → 成功 + revision+1
- update 错误 revision → StaleJobRevisionError
- 并发 5 个 update 同一 job → 一个成功四个失败
- 失败方重新读 → 看到最新状态

**风险**：中。需要全链路加 CAS。建议**先在 `JobBoard.update` 一次性加好**，调用方渐进切换（保留旧 `set_status` 方法做 wrapper）。

**回退**：旧 `set_status` 内部强制 `expected_revision=job.revision`（永远成功）。

**依赖**：2A（共用概念）。

---

## 4B. CredentialRef + resolve()-per-call

> **⚠️ 2026-09-14 实施时发现设计前提不成立，本项取消。**
> 复核 `app/core/modelhub.py`：`providers()`（L481）每次调用都执行 `_load()` 现读
> `data/models.json`，`bind_agent()` 在每次 run_agent 调用时组装链路——
> **改密钥本就无需重启，per-call 热生效已经成立**（`chat()` L1841 同样每次现读）。
> 设计稿当时依据的"api_key 多处直读、需重启"痛点来自早期版本，已被
> 「运行时模型统一到 CLI 绑定页」的重构解决。
> 若未来出现新的凭据来源分层需求（如 managed file > env 的优先级合成），再重启本项。

**目标**：把"凭据分散在 main.py + runner.py"统一到 `credentials.py`，分层源（inherited env > managed file > user config），`resolve(ref)` 每次从层叠现读，热改密钥立即生效。

**dsh 参考**：
- [`packages/credentials/credentials/src/index.ts:14-115`](E:/GoOut/_dsh_ref/packages/credentials/credentials/src/index.ts#L14) `CredentialRef` / `CredentialKey` 双键空间
- [`packages/credentials/credentials/src/index.ts:183`](E:/GoOut/_dsh_ref/packages/credentials/credentials/src/index.ts#L183) `resolve()` 按调用现读不缓存
- [`packages/credentials/credentials-local/src/index.ts:106-112`](E:/GoOut/_dsh_ref/packages/credentials/credentials-local/src/index.ts#L106) 文件锁 + 0600/0700 权限

**Tutti 痛点**：
- `prov["api_key"]` 多处直读
- 改密钥后需重启服务
- 凭据落盘格式不统一（有的在 orchestration.json，有的在 modelhub.py）

**改动范围**：
- 新增 `app/core/credentials.py`（约 150 行）
- `app/main.py` + `app/core/runner.py` + `app/core/modelhub.py` 改用 `creds.resolve(ref)` 替代直读

**代码骨架**：
```python
# app/core/credentials.py
import os, json, threading, time
from dataclasses import dataclass

@dataclass
class CredentialRef:
    """POSIX 标识 env 名（如 "OPENAI_API_KEY"）或命名引用（如 "openai/main"）。"""
    name: str

class CredentialNotFound(Exception):
    pass

class CredentialStore:
    """分层源：inherited env > managed file (data/credentials.json) > user config。

    resolve() 每次现读，**不缓存**——热改密钥立即生效。
    """
    def __init__(self, home: str):
        self.home = home
        self.managed_path = os.path.join(home, "credentials.json")
        self._lock = threading.Lock()
        self._managed_cache = None
        self._managed_mtime = 0

    def resolve(self, ref: CredentialRef) -> str:
        # 1. inherited env
        v = os.environ.get(ref.name)
        if v:
            return v
        # 2. managed file（每次检查 mtime，热改立即生效）
        with self._lock:
            try:
                mtime = os.path.getmtime(self.managed_path)
            except OSError:
                mtime = 0
            if mtime != self._managed_mtime or self._managed_cache is None:
                try:
                    with open(self.managed_path, encoding="utf-8") as f:
                        self._managed_cache = json.load(f)
                except (OSError, json.JSONDecodeError):
                    self._managed_cache = {}
                self._managed_mtime = mtime
        v = self._managed_cache.get(ref.name)
        if v:
            return v
        raise CredentialNotFound(f"credential {ref.name} not found")

    def describe(self, ref: CredentialRef) -> dict:
        """返回 {'configured': bool, 'writable': bool, 'source': str}——不含值。"""
        # ...
        pass

    def modify_record(self, ref: CredentialRef, *, value: str):
        """read-modify-write 加锁。"""
        with self._lock:
            try:
                with open(self.managed_path, encoding="utf-8") as f:
                    data = json.load(f)
            except (OSError, json.JSONDecodeError):
                data = {}
            data[ref.name] = value
            tmp = self.managed_path + ".tmp"
            with open(tmp, "w", encoding="utf-8") as f:
                json.dump(data, f, indent=2)
                f.flush()
            os.replace(tmp, self.managed_path)  # 原子
            try:
                os.chmod(self.managed_path, 0o600)
            except OSError:
                pass

creds = CredentialStore(os.path.join(os.path.expanduser("~"), ".tutti"))
```

**集成点**（`runner.py`）：
```python
# 替换
api_key = prov["api_key"]
# 为
from .credentials import creds, CredentialRef
try:
    api_key = creds.resolve(CredentialRef("OPENAI_API_KEY"))
except CredentialNotFound:
    api_key = None
```

**测试**（`tests/test_credentials.py`）：
- env 优先 → resolve 返回 env 值
- env 无 + managed 有 → 返回 managed
- 修改 managed → 下次 resolve 返回新值（热改）
- 都不存在 → CredentialNotFound
- modify_record → 文件权限 0600
- 并发 modify_record → 锁不冲突

**风险**：
- **改造面**：3+ 文件需替换直读
- **回退**：所有调用方加 try/except，CredentialNotFound 时回退到原 prov["api_key"]

**回退**：把 `creds.resolve` 包成 `def get_cred(name, fallback=None)`，fallback 链路保留。

**依赖**：无（独立模块）。

---

## Phase 3 实施清单

按依赖顺序：

1. **4B CredentialRef**（独立，立刻可做，热改密钥立即生效）
2. **2A Goal 外置**（独立，重构 planner.py）
3. **2C Job CAS**（依赖 2A 概念）
4. **2B Goal Round Driver**（依赖 2A + 2C，灰度开启）

每完成一项：jobs.py 默认 `revision` 字段 + planner.py 默认读 goal_svc。