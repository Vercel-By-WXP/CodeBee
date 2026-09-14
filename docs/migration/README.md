# DeepSeek Harness → Tutti 迁移设计稿

> 本目录是把 DeepSeek Harness（dsh）源码里"有意义的内容"学习过来、落地到 Tutti 的完整设计稿。
> 每项含 **目标 / Tutti 痛点 / dsh 参考 / 改动范围 / 代码骨架 / 测试用例 / 风险评估 / 依赖**。

## 阅读顺序

按实施阶段读，每阶段内部按依赖顺序：

| 阶段 | 文件 | 项数 | 估计投入 | 立即收益 |
|---|---|---|---|---|
| Phase 1（地基） | [01-defense-patterns.md](01-defense-patterns.md)（5A-5F, 1E, 2D） | 8 | 1-2 天 | 8 条不变量 / 防御模式落地，CLI 启动更安全，错误归一 |
| Phase 2（上下文） | [02-context-compaction.md](02-context-compaction.md)（1C, 1A, 1B, 1D） | 4 | 1-2 周 | 长任务不撑爆，重试不重复组装 |
| Phase 3（状态外置） | [03-state-externalization.md](03-state-externalization.md)（2A, 2B, 2C, 4B） | 4 | 1 周 | Goal/任务板/CAS；凭据热改立即生效 |
| Phase 4（技能 seam） | [04-skills-seam.md](04-skills-seam.md)（3A, 3B） | 2 | 1 周 | 渐进加载、按需正文 |
| Phase 5（模型 seam） | [05-model-seams.md](05-model-seams.md)（4A, 4C, 4D, 4E, 5E） | 5 | 2 周 | modelhub 双写变单写；能力维度选模型 |
| Phase 6（Agent Team，可观望） | [06-agent-team.md](06-agent-team.md)（6A, 6B） | 2 | 2-3 周 | 持续接力、议程化评审 |

## 共通约定

每项设计稿里：
- **改动点**：Tutti 文件路径:行号（带 vscode 链接） 或 "新增 `path/to.py`"
- **dsh 参考点**：`E:/GoOut/_dsh_ref/...` 路径:行号 或子系统文档
- **代码骨架**：只写关键签名/函数/类骨架，不写完整实现（避免与并行 agent 冲突 + 留实现灵活度）
- **测试**：列测试函数名 + 断言要点，不写完整测试代码
- **风险**：失败模式 + 回退方案
- **依赖**：与其它项的前置关系

## 优先级速查矩阵

| 项 | 主题 | 投入 | 收益 | 阶段 |
|---|---|---|---|---|
| 5A env 净化 | 防御 | 极小 | 防密钥泄漏 | 1 |
| 5B kill-tree 排空 | 防御 | 极小 | 不再有 CLI 僵尸 | 1 |
| 5C 重复 CLI 检测 | 防御 | 小 | 防死循环 | 1 |
| 5D failure 归一 | 防御 | 小 | 错误信息归一 | 1 |
| 1E crash-recovery | 上下文 | 极小 | worker 崩溃后状态可恢复 | 1 |
| 2D 错误码体系 | 防御 | 小 | 区分 fatal vs item | 1 |
| 5E 每 CLI 独立超时 | 防御 | 中 | 编排者不再过短超时 | 1（延后做） |
| 5F invariant 注册表 | 防御 | 中 | 质量闸门可告警 | 1（延后做） |
| 1C token-meter | 上下文 | 小 | 给 1B 触发条件 | 2 |
| 1A surface 派生 | 上下文 | 大 | 跨轮上下文语义正确 | 2 |
| 1B 三段式压缩 | 上下文 | 大 | 长任务不撑爆 | 2 |
| 1D 重试复用 prompt | 上下文 | 小 | 不再重复组装 | 2 |
| 2A Goal 外置 | 状态 | 中 | 持续接力基础 | 3 |
| 2C revision CAS | 状态 | 中 | 任务板 CAS | 3 |
| 4B CredentialRef | 模型 | 中 | 凭据热改生效 | 3 |
| 2B Goal Round Driver | 状态 | 大 | 自驱打磨 | 3（延后做） |
| 3A SkillProvider 分层 | 技能 | 大 | 渐进加载 | 4 |
| 3B Skill persona | 技能 | 极小 | skill 自带角色 | 4 |
| 4A LlmProvider 抽象 | 模型 | 大 | 双写变单写 | 5 |
| 4C Settings schema | 模型 | 中 | schema-driven 配置 | 5 |
| 4D 能力维度 | 模型 | 中 | 按能力选模型 | 5 |
| 4E dormant provider | 模型 | 小 | UI 显示但未启用 | 5 |
| 5G approval seam | 防御 | 大 | 无人值守不静默降级 | 5 |
| 5H 审计事件 | 防御 | 大 | resume 时状态可重建 | 5 |
| 6A Roster/Mailbox/TaskBoard | 团队 | 大 | 完整 Agent Team | 6 |
| 6B 评审议程化 | 团队 | 中 | 复用 6A | 6 |

## 不建议照搬

- **完整 Typert RPC**（双面 wire、装饰器生成）— Tutti 是 Python+stdlib SSE，引入等于造 RPC DSL。
- **Dynamic Cordis 沙箱**（模型 runtime 编译 TS 插件）— Python 包强类型生成不在 Tutti 控制范围内。
- **`user-questions` / `ask_user_question` 工具** — Tutti 模型是外部 CLI，模型无法在 harness 侧暂停。
- **完整 `runtime-diagnostics` 包族**（包级 invariant 配套入口）— 用单文件 `app/core/diagnostics.py` + pytest 钩替代更轻。
- **完整 hooks 桥接**（Claude Code / Codex hook 协议）— Tutti 调外部 CLI 而非 Cordis 内部 waterfall；克隆协议成本不抵收益。
- **continuable Activation 双层状态机**（`subagent.zh.md:128-146`）— 依赖 dsh Agent/Session 强解耦与冷恢复基础设施，Tutti 无等价 ctx。

## dsh 仓库保留位置

`E:/GoOut/_dsh_ref`（已浅克隆）。每个设计稿都引用了它。

## 实施纪律

- 每完成一项：在 docs/migration/CHANGELOG.md 写一行（日期、项号、影响文件）。
- 每完成一项：在 Tutti git commit 信息里引用 `dsh-migration: <项号>`。
- 每完成一项：在 README 优先级表里把该项标 ✅。
- 实施过程中如发现设计稿假设不成立（例如 Tutti 某函数签名已变），先改设计稿，再动代码。