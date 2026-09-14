# 主题 6：Agent Team 模块（Phase 6，可观望）

> dsh 实验性 Agent Teams：root Session 即 Lead + 具名 teammate（永不重命名）+ 持久 mailbox + 共享任务 DAG + bounded `waitForChange`。
> Tutti 现状 jobs.py 简单并发池，无任务板。本主题完整实现一份 Agent Team（**先观望**，再决定投入）。

---

## 6A. 持久 Roster + Mailbox + TaskBoard

**目标**：在 Tutti 中实现一份完整的 Agent Team：root run 即 Lead；teammate 按 name 命名（永不重用）；mailbox queued-minus-delivered 对账；task board 带 `revision` CAS。

**dsh 参考**：
- [`packages/experimental/agent-team/`](E:/GoOut/_dsh_ref/packages/experimental/agent-team/) 整包
- [`docs/subsystems/agent-team.zh.md`](E:/GoOut/_dsh_ref/docs/subsystems/agent-team.zh.md) — roster/mailbox/task-board/waitForChange 四件套
- [`agent-team/src/roster.ts:79-115`](E:/GoOut/_dsh_ref/packages/experimental/agent-team/src/roster.ts#L79) 三态（provisioning/active/failed）+ 状态派生
- [`agent-team/src/mailbox.ts:55-98`](E:/GoOut/_dsh_ref/packages/experimental/agent-team/src/mailbox.ts#L55) queued-minus-delivered 对账
- [`agent-team/src/task-board.ts:48-130`](E:/GoOut/_dsh_ref/packages/experimental/agent-team/src/task-board.ts#L48) revision CAS + blockedBy 校验

**Tutti 痛点**：
- 跨轮上下文散落
- 评审单一回合没有持续辩论 /议程推进
- 子任务失败经验难沉淀

**改动范围**：
- 新建 `app/core/team.py`（roster + team 编排，约 200 行）
- 新建 `app/core/mailbox.py`（per-teammate 消息队列，约 100 行）
- 新建 `app/core/task_board.py`（任务 DAG + CAS，约 200 行）
- `app/core/jobs.py` 接入 task board
- `app/core/runner.py` 注册 team-member 作为新 vendor（可选：每个 teammate 是一个 sub-runner）

**代码骨架**：
```python
# app/core/team.py
from dataclasses import dataclass, field
from typing import List, Dict, Optional
import threading, time, uuid

@dataclass
class Teammate:
    name: str                    # 永不重用
    role: str                    # "writer" | "reviewer" | "researcher" | ...
    persona: str = ""
    member_phase: str = "provisioning"  # provisioning|active|failed
    runtime_phase: str = "inactive"     # running|idle|inactive 派生
    created_at: float = field(default_factory=time.time)

class TeamRoster:
    """同 root run 一份 roster。teammate name 不可重用（即使 failed）。"""
    def __init__(self, root_run_id: str, store_path: str):
        self.root_run_id = root_run_id
        self.store_path = store_path
        self._teammates: Dict[str, Teammate] = {}
        self._lock = threading.Lock()
        self._load()

    def add(self, *, name: str, role: str, persona: str = "") -> Teammate:
        with self._lock:
            if name in self._teammates:
                raise ValueError(f"teammate {name} already exists (and is permanent)")
            tm = Teammate(name=name, role=role, persona=persona, member_phase="active")
            self._teammates[name] = tm
            self._persist()
            return tm

    def get(self, name: str) -> Optional[Teammate]:
        return self._teammates.get(name)

    def all(self) -> List[Teammate]:
        return list(self._teammates.values())

    def _persist(self):
        # 写 team/roster.json
        pass

# app/core/mailbox.py
@dataclass
class MailMessage:
    msg_id: str
    from_name: str
    to_name: str
    body: str
    queued_at: float = field(default_factory=time.time)
    delivered_at: Optional[float] = None

class TeamMailbox:
    """Lead 落库 + target inbox 真正接受才算送达；恢复时 queued-minus-delivered 重投递。"""
    def __init__(self, store_path: str):
        self.store_path = store_path
        self._queues: Dict[str, List[MailMessage]] = {}  # to_name -> queued
        self._delivered: Dict[str, List[MailMessage]] = {}  # to_name -> delivered
        self._lock = threading.Lock()
        self._load()

    def send(self, from_name: str, to_name: str, body: str) -> MailMessage:
        with self._lock:
            msg = MailMessage(msg_id=str(uuid.uuid4()), from_name=from_name,
                              to_name=to_name, body=body)
            self._queues.setdefault(to_name, []).append(msg)
            self._persist_queue(to_name)
            return msg

    def deliver(self, to_name: str) -> Optional[MailMessage]:
        """target inbox 拉取并标记 delivered。返回下一条未读。"""
        with self._lock:
            queue = self._queues.get(to_name, [])
            if not queue:
                return None
            msg = queue.pop(0)
            msg.delivered_at = time.time()
            self._delivered.setdefault(to_name, []).append(msg)
            self._persist_queue(to_name)
            self._persist_delivered(to_name)
            return msg

    def pending_count(self, to_name: str) -> int:
        with self._lock:
            return len(self._queues.get(to_name, []))

    def reconcile(self, to_name: str):
        """恢复时：把 delivered 但 queued 没记录的重新入队（防止硬中断丢消息）。"""
        with self._lock:
            queued_ids = {m.msg_id for m in self._queues.get(to_name, [])}
            for msg in self._delivered.get(to_name, []):
                if msg.msg_id not in queued_ids:
                    self._queues.setdefault(to_name, []).append(msg)

# app/core/task_board.py
@dataclass
class TeamTask:
    task_id: str
    subject: str
    description: str
    status: str = "pending"           # pending|in_progress|completed|failed|blocked
    owner: Optional[str] = None       # teammate name
    blocked_by: List[str] = field(default_factory=list)  # task_ids
    revision: int = 0
    write_scopes: List[str] = field(default_factory=list)  # 提示性，不强制
    created_at: float = field(default_factory=time.time)
    updated_at: float = field(default_factory=time.time)

class StaleTaskRevisionError(Exception):
    pass

class TeamTaskBoard:
    def __init__(self, store_path: str):
        self.store_path = store_path
        self._tasks: Dict[str, TeamTask] = {}
        self._lock = threading.Lock()
        self._load()

    def create(self, *, subject: str, description: str, blocked_by=None,
               owner=None, write_scopes=None) -> TeamTask:
        with self._lock:
            self._validate_no_cycle(subject, blocked_by or [])
            task = TeamTask(
                task_id=str(uuid.uuid4()),
                subject=subject, description=description,
                blocked_by=list(blocked_by or []),
                owner=owner, write_scopes=list(write_scopes or []),
            )
            self._tasks[task.task_id] = task
            self._persist(task.task_id)
            return task

    def claim(self, task_id: str, teammate_name: str, *, expected_revision: int) -> TeamTask:
        return self.update(task_id, expected_revision=expected_revision,
                           owner=teammate_name, status="in_progress")

    def complete(self, task_id: str, *, expected_revision: int, result: dict) -> TeamTask:
        return self.update(task_id, expected_revision=expected_revision,
                           status="completed", result=result)

    def update(self, task_id: str, *, expected_revision: int, **changes) -> TeamTask:
        with self._lock:
            task = self._tasks[task_id]
            if task.revision != expected_revision:
                raise StaleTaskRevisionError(
                    f"task {task_id} expected rev {expected_revision}, got {task.revision}")
            for k, v in changes.items():
                setattr(task, k, v)
            task.revision += 1
            task.updated_at = time.time()
            self._persist(task_id)
            return task

    def ready_for(self, teammate_name: str) -> List[TeamTask]:
        """返回 owner=teammate_name 且 status=pending 且 blocked_by 全 completed 的任务。"""
        with self._lock:
            completed = {tid for tid, t in self._tasks.items() if t.status == "completed"}
            return [
                t for t in self._tasks.values()
                if t.owner == teammate_name
                and t.status == "pending"
                and all(b in completed for b in t.blocked_by)
            ]

    def wait_for_change(self, *, timeout=30.0) -> bool:
        """bounded event wait（10s-1h），避免轮询。
        返回 True 表示有变化，False 表示超时。"""
        # 用 threading.Event + timeout 实现
        ...

    def _validate_no_cycle(self, subject: str, blocked_by: List[str]):
        # ... 检测 blocked_by 链有环、自指、引用不存在 task
        pass
```

**集成点**（`jobs.py`）：
```python
# Team 内每个 teammate 是一个 sub-runner
team = TeamRoster(run_id, store_path)
roster = team.add(name="writer-1", role="writer", persona="...")
reviewer = team.add(name="reviewer-1", role="reviewer")
mailbox = TeamMailbox(store_path)
board = TeamTaskBoard(store_path)

# Lead 创建任务
task = board.create(
    subject="实现功能 A",
    description="...",
    blocked_by=[],
    owner="writer-1",
)
# writer-1 通过 wait_for_change 监听
def writer_loop():
    while True:
        ready = board.ready_for("writer-1")
        if ready:
            t = ready[0]
            # 调用 LLM 完成
            result = chat(...)
            board.complete(t.task_id, expected_revision=t.revision, result=result)
            # 通知 reviewer
            mailbox.send("writer-1", "reviewer-1", f"task {t.subject} done")
        if not board.wait_for_change(timeout=60):
            continue  # idle 心跳
```

**测试**（`tests/test_agent_team.py`）：
- roster.add 重复 name → ValueError
- teammate failed 后仍占名
- mailbox.send + deliver → queued=0, delivered=1
- mailbox.reconcile 后 queued 恢复
- task blocked_by 链有环 → ValueError
- task claim CAS 失败 → StaleTaskRevisionError
- wait_for_change timeout 返回 False
- ready_for 返回所有 blocked_by 已 completed 的 pending

**风险**：
- **高**。完整 subagent + mailbox + task board 三件套实现量大。
- **建议观望**：先看 dsh experimental 包正式化进度，再决定 Tutti 投入。
- **简化版**（如果观望）：只做 task board（2C 已覆盖 80%）+ mailbox，跳过 roster。

**回退**：完全独立模块，不启用时 jobs.py 走原路径。

**依赖**：2A（goal 外置）、2C（CAS 概念）、1A（surface 日志可选）。

---

## 6B. 评审议程化（带 expectedRevision）

**目标**：cross-vendor review 改为"在 task board 上创建评审任务，被评审方 claim，完成后写 revision；Lead 等待 `waitForChange`"。一次评审 = 一次 task transition，而不是字符串拼接。

**dsh 参考**：[`agent-team/README.zh.md:24-32`](E:/GoOut/_dsh_ref/packages/experimental/agent-team/README.zh.md#L24) subagent vs agent-team 边界。

**Tutti 痛点**：评审无多轮辩论 / 议程推进；一次评审 = 一次单回合调 LLM。

**改动范围**：
- `app/core/pipeline.py` 的 review 步骤改为走 task board
- 新增 `app/core/review_session.py`（约 100 行）— 评审议程状态机

**代码骨架**：
```python
# app/core/review_session.py
@dataclass
class ReviewAgenda:
    review_id: str
    subject: str
    rounds: List[dict] = field(default_factory=list)  # [{"round": 1, "reviewer": "claude", "verdict": "approve|reject", "comments": "..."}]
    revision: int = 0

class ReviewSession:
    """评审议程：可以多轮，每轮一个 reviewer。"""
    def __init__(self, board: TeamTaskBoard, review_id: str, expected_revision: int = 0):
        self.board = board
        self.review_id = review_id
        self.expected_revision = expected_revision

    def add_round(self, reviewer: str, verdict: str, comments: str) -> ReviewAgenda:
        """添加一轮评审结果。"""
        # 通过 task board 的 update 做 CAS
        ...

    def decide(self) -> str:
        """综合所有 rounds 决定最终 verdict。"""
        # 简单 majority，或按 reviewer weight
        ...

    def wait_for_next_round(self, *, timeout=300):
        """等待 reviewer 完成（bounded）。"""
        return self.board.wait_for_change(timeout=timeout)
```

**集成点**（`pipeline.py`）：
```python
# 替换现有 review 逻辑
review_task = board.create(
    subject=f"评审 {task.title}",
    description="...",
    blocked_by=[implement_task.task_id],
    owner=f"reviewer-{vendor_name}",
)
# 等评审完成
while not review_completed:
    if review_session.wait_for_next_round(timeout=300):
        review_completed = review_session.decide()
```

**测试**（`tests/test_review_session.py`）：
- 单轮评审 → decide 返回该 verdict
- 多轮评审（3 reviewer）→ majority 决定
- CAS 失败 → StaleTaskRevisionError
- 评审超时（无 reviewer 完成）→ 默认 reject

**风险**：中。复用 task board 已有 80% 逻辑。

**回退**：保留旧 review 单回合逻辑。

**依赖**：6A（task board 必须先实现）。

---

## Phase 6 实施清单

按依赖顺序：

1. **6A Roster + Mailbox + TaskBoard**（观望，可只做 task board + 简化 mailbox）
2. **6B 评审议程化**（依赖 6A）

---

## Phase 6 风险与回退

- **观望策略**：
  - 等 dsh experimental/agent-team 进入 stable 包名（不再是 experimental.）再开始
  - 或先用 2C（job CAS）+ 简化 mailbox 跑通核心场景，不做完整 Agent Team
- **回退**：完全独立模块，关闭 `data/orchestration.json: team_enabled = false` 即可

---

## 完整迁移总览

| 阶段 | 项数 | 主要文件 | 估计工时 | 立即收益 |
|---|---|---|---|---|
| Phase 1（地基） | 8 | runner.py + diagnostics.py + sessions.py | 1-2 天 | 防御模式 + 错误归一 |
| Phase 2（上下文） | 4 | session_log.py + compaction.py + token_meter.py + pipeline_v2.py | 1-2 周 | 长任务不撑爆 |
| Phase 3（状态外置） | 4 | goal_service.py + goal_round_driver.py + job_board.py + credentials.py | 1 周 | Goal + 任务板 + 热改密钥 |
| Phase 4（技能） | 2 | skills.py（重构）+ skill_providers/* | 1 周 | 渐进加载 |
| Phase 5（模型） | 5 | modelhub.py（重构）+ settings_schema.py + UI | 2 周 | seam 化 |
| Phase 6（Agent Team） | 2 | team.py + mailbox.py + task_board.py + review_session.py | 2-3 周（观望） | 持续接力 |

**累计估计**：Phase 1-5 共 5-7 周，Phase 6 观望 + 2-3 周。

每个 Phase 完成都需：
- 灰度开关在 `data/orchestration.json`
- 1 周生产观察
- 切默认 → 旧路径移除

dsh 仓库保留在 `E:/GoOut/_dsh_ref` 供随时查阅。