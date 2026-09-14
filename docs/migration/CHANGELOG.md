# DeepSeek Harness → Tutti 迁移 CHANGELOG

> 每完成一项在此加一行：`日期 | 项号 | 影响文件 | 备注`

## Phase 1（防御模式与错误归一）

```
2026-09-14 | 5A | app/core/env_scrub.py:new + app/core/runner.py:118 | env 净化：deny KEY/SECRET/TOKEN/PASSWORD 等；白名单前缀保留 vendor 变量；stub/off 模式可调；tests/test_env_scrub.py 6 绿
2026-09-14 | 5B | app/core/runner.py:67 (_drain_streams) | kill-tree 后排空流：cancel 后 10s、timeout 后 5s 等管道线程读完剩余字节；tests/test_runner_drain.py 5 绿
2026-09-14 | 5D+2D | app/core/error_codes.py:new + app/core/runner.py:325 (_classify_failure) | 错误码体系：ErrorCode 枚举 + FATAL_CODES + is_fatal/is_retryable；run_agent 返回 dict 加 error_code 字段；tests/test_error_codes.py 14 绿
2026-09-14 | 5C | app/core/repeat_guard.py:new | 重复 CLI 调用检测：(run_id, role) 分桶，prompt SHA-256 指纹，阈值 5/10/15 提醒+强制停止；tests/test_repeat_guard.py 18 绿
2026-09-14 | 1E | app/core/store.py:recover_orphaned_runs | crash-recovery：启动时把 status=running 的 run 标记为 failed；tests/test_run_recovery.py 8 绿
2026-09-14 | 5E | app/core/runner.py:432 | per-vendor timeout：catalog orch.timeout_ms 优先于 caller timeout；tests/test_per_vendor_timeout.py 5 绿
2026-09-14 | (test-infra) | tests/base.py | 修复 pre-existing pipeline._agents monkey-patch 未恢复导致 test_pipeline 污染 test_quality_gates 的问题
```

## Phase 2（上下文压缩）

<!-- 1C | token_meter.py | 压力估算 -->
<!-- 1A | session_log.py + session_projection.py + pipeline_v2.py | surface 日志 -->
<!-- 1B | compaction.py | 三段式压缩 -->
<!-- 1D | pipeline_v2.py | 重试复用 prompt -->

## Phase 3（状态外置）

<!-- 4B | credentials.py | CredentialRef + resolve per-call -->
<!-- 2A | goal_service.py + planner.py | Goal 外置 -->
<!-- 2C | jobs.py | Job revision + CAS -->
<!-- 2B | goal_round_driver.py | 自驱打磨 -->

## Phase 4（技能 seam）

<!-- 3A | skills.py(重构) + skill_providers/* | SkillProvider 分层 -->
<!-- 3B | skill_providers/* + pipeline.py | Skill Persona -->

## Phase 5（模型 seam）

<!-- 4A | modelhub.py(重构) | LlmProvider 抽象 -->
<!-- 4C | settings_schema.py + settings.py | Settings schema + revision -->
<!-- 4D | modelhub.py | 能力维度 -->
<!-- 4E | modelhub.py + main.py + UI | dormant provider -->
<!-- 5E(已纳入) | catalog.py | per-vendor timeout -->

## Phase 6（Agent Team，观望）

<!-- 6A | team.py + mailbox.py + task_board.py | Roster + Mailbox + TaskBoard -->
<!-- 6B | review_session.py | 评审议程化 -->

---

## 已知遗留问题（pre-existing / 并行 agent 引入，与本次迁移无关）

- `tests/test_paths.py`（并行 agent 添加）：测试用例引用旧 paths.py 私有函数 `_user_data_dir`/`default_data_dir`/`_DATA_SENTINELS`，这些已被并行 agent 简化删除 → 5 个 AttributeError
- `tests/test_auto.py::TestPlannerFallback`（parallel agent 回归）：mock 检测逻辑被破坏，调用真实 LLM 而非 fallback to template
- 修复方向：与并行 agent 协商合并

## 实施记录格式

每项提交示例：

```
2026-09-14 | 5A | runner.py:100 + env_scrub.py | env 净化：deny 列表 + 白名单前缀；tests/test_env_scrub.py 通过；灰度默认开
```

附在 commit message：

```
dsh-migration: 5A env scrub

参考 dsh defensive-patterns.zh.md#L31
新增 app/core/env_scrub.py
runner.py:run_process 接入
tests/test_env_scrub.py
```