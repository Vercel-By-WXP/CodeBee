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
2026-09-14 | 5F | app/core/diagnostics.py:new | invariant 注册表：source/name 分组断言 + fail_fast + 默认 checks（review_no_all_fail_zero / step_count_consistency）；tests/test_diagnostics.py 15 绿
2026-09-14 | 5G | app/core/runner.py:421 (_check_approval) | approval NEVER 一线：catalog orch.sensitive=True + TUTTI_APPROVAL_POLICY=never → 早期拒绝（ENV_BLOCK），避免无人值守静默放行；tests/test_approval.py 6 绿
2026-09-14 | (test-infra) | tests/base.py | 修复 pre-existing pipeline._agents monkey-patch 未恢复导致 test_pipeline 污染 test_quality_gates 的问题
```

Phase 1 全部 8 项 + 1 个 test-infra 修复完成；合计新增 77 测试全绿。

## Phase 2（上下文压缩）

```
2026-09-14 | 1C | app/core/token_meter.py:new | 压力估算：每 run 累计窗口（input+output+reasoning，cached 不计压力），容量表 data/model_capacity.json 热改生效；tests/test_token_meter.py 14 绿
2026-09-14 | 1A | app/core/session_log.py:new | surface 会话日志：只追加 SessionEvent（seq 单调）+ derive_messages 折叠派生 + surface_op replace（replace_generation 计数）+ JSONL 落盘坏行跳过；tests/test_session_log.py 11 绿
2026-09-14 | 1B | app/core/compaction.py:new | 三段式压缩：select_range（跳 system 头/尾预算/不切 tool 配对/区域上限）→ 工具结果剪枝 → LLM 摘要 → surface replace；失败仍写 compaction_end；tests/test_compaction.py 16 绿
2026-09-14 | 1D | app/core/step_runner.py:new | 重试守门：撑爆（MAX_TOKENS/CONTEXT_OVERFLOW）→ maybe_compact 仅当 replace_generation 前进才复用同一 prompt 重试一次；tests/test_step_runner.py 6 绿
2026-09-14 | (接线) | app/main.py:649 | recover_orphaned_runs 接入启动链（jobs.resume_interrupted 之前，防止僵尸 run 被当成正常中断续跑）
2026-09-14 | (接线) | app/core/pipeline.py:_run_step | Phase 2 灰度接入：TUTTI_COMPACTION=1 时真实步骤走 execute_step 路径——「模型可见即已记录」（user/assistant 消息先落 session.jsonl）+ usage 累进 token_meter + 撑爆压缩守门重试；resume 会话跳过（CLI 自己管上下文）；默认关；tests/test_pipeline_compaction.py 3 绿
```

Phase 2 完成：4 模块 + pipeline 灰度接线，50 测试绿；全套回归 151 测试绿。
开启方式：`set TUTTI_COMPACTION=1` 后重启服务；关闭即回原路径，session.jsonl 仅追加不影响旧逻辑。

## Phase 3（状态外置）

```
2026-09-14 | 2A | app/core/goal_service.py:new | Goal 状态外置：单一当前目标 + phase（active/paused/complete/abandoned）+ revision CAS + 轮次计数 + 原子落盘（goals.json）；tests/test_goal_service.py 13 绿
2026-09-14 | 2C(最小化) | app/core/store.py:update_run | expected_status CAS：陈旧执行方（被取消 worker/崩溃恢复前线程）不再覆盖新状态；不传参数保持原行为；tests/test_run_cas.py 8 绿（含 recover 重放安全）
2026-09-14 | 4B | — | 取消：复核 modelhub.py providers()/chat() 本就每次现读 models.json，per-call 热生效已成立（设计稿已记录原因）
2026-09-14 | 2B | — | 缓期：Goal Round Driver 与 v5 自驱闭环（resume_interrupted/_maybe_auto_resume）重叠，避免双通道续行（设计稿已记录）
```

Phase 3 完成：2A + 2C 落地 21 测试绿；4B 取消、2B 缓期（均记录于设计稿，非静默跳过）。

## Phase 4（技能 seam）

```
2026-09-14 | 3A | app/core/skills.py（增量） | 用户自建经验包 provider：data/skillpacks/*.md（frontmatter: name/scopes/persona/note），目录+文件 mtime 热缓存（零依赖替代 watchdog），list_packs/pack_op/block_for 全接入；按 Tutti 实际修正：模型在 CLI 内无法按需调 skill 工具，「按需正文加载」不适用（设计稿已记录）；tests/test_user_packs.py 17 绿
2026-09-14 | 3B | app/core/skills.py（增量） | Skill Persona：包 frontmatter persona 字段注入为独立「角色设定」块（先于规范正文）；tests/test_user_packs.py 覆盖
```

Phase 4 完成：34 测试绿（17 新 + 17 存量 skills 回归）。

## Phase 5（模型 seam）

```
2026-09-14 | 4C | app/core/settings_schema.py:new | schema 化设置：namespace 注册 + FieldDef（类型/choices/clamp）+ revision CAS + describe(redact_secrets) 脱敏 + 默认 orchestrator namespace（compaction.*/max_goal_rounds）；零触碰 settings.py；tests/test_settings_schema.py 18 绿
2026-09-14 | 4D | app/core/capability.py:new | 能力维度：classify_task_type（显式声明>关键词>coding 兜底）+ pick_by_strength（供应商声明可选 strengths 字段，强项优先）+ resolve_binding_by_task（维度绑定回落 default）；零触碰 modelhub.py；tests/test_capability.py 13 绿
2026-09-14 | 4A | — | 取消：modelhub.chat() 已是统一 adapter seam（三协议分发 + 现读 + 原子写），无第二实现可挂，重构无净收益（设计稿已记录）
2026-09-14 | 4E | — | 取消：providers_op(disable) 即 dormant 语义（停用不清配置随时可启）（设计稿已记录）
```

Phase 5 完成：2 项落地 31 测试绿；2 项有据取消。

## Phase 1 收尾接线（5C/5F 从「模块就绪」到「运行时生效」）

```
2026-09-14 | 5C(接线) | app/core/pipeline.py:_run_step + repeat_guard.py:guard 单例 | 重复调用守门进主流程：spawn 前检查（指纹取原始 prompt，提醒只注入 spawn 副本不污染计数链），超阈值停止并记 ENV_BLOCK 失败；_run_step 拆分为 _spawn_step + _finish_step_result；tests/test_pipeline_guards.py 8 绿
2026-09-14 | 5F(接线) | app/core/pipeline.py:_finish_step_result + diagnostics.py | step 级运行时断言进主流程：每步收尾跑 step source 断言（新增 ok_but_error_code：成功但带错误码的降级输出告警），只告警不阻断；tests/test_pipeline_guards.py 覆盖
```

---

## 总计

Phase 1-5 全部收口：**6 项取消/缓期均有设计稿记录**（4A/4B/4E 复核发现已存在，2B 与 v5 重叠，2C 按实际架构最小化，3A 按注入式架构修正），
**新增模块 11 个、测试 20 个文件、219 测试全绿**，长跑回归（pipeline+质量闸门）无回归。
Phase 6（Agent Team）按设计稿维持观望。

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