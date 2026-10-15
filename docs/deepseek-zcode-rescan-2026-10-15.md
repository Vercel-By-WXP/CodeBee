# DeepSeek / ZCODE 二次扫描与性能验收落地

日期：2026-10-15。以主 checkout 当前代码核对缺口，旧路线图未勾选不等于尚未实现。

## 来源与核验范围

- DeepSeek：[npm 发布元数据](https://registry.npmjs.org/@deepseek-ai/dsh/latest) 返回 `@deepseek-ai/dsh` `0.2.0-rc.2`、MIT，以及 GitHub 仓库声明。只在内存中读取发布包的 README 和文件清单，没有安装或执行包内代码。[GitHub README](https://raw.githubusercontent.com/deepseek-ai/deepseek-harness/main/README.md) 本轮返回 404，因此不能宣称扫描了最新 GitHub 全量源码。
- 发布包 README 明确描述：bundle → profile → home → CLI patch 的配置覆盖顺序、配置与 schema dump、插件 peer 版本校验、禁用状态保留、共享写锁和串行 reload。上述内容可用于借鉴设计；README 对内部源文件的引用不是本轮已读取源码的证据。
- ZCODE：[官方插件清单](https://cdn-zcode.z.ai/zcode/official-plugin/marketplace.json) 本轮可读。其中 Mimosa `1.0.4` 声明写入前 Hook、收尾复查、Git 门禁；清单还提供固定版本和 SHA-256。这里只核验清单声明，未安装插件，也未验证其内部实现。

## 值得继续落地的项

| 优先级 | 项目 | 本仓库当前情况与可交付范围 |
| --- | --- | --- |
| P0，本轮完成 | 性能验收阈值与运行快照 | `acceptance.py` 原来有耗时或用量就通过。现改为明确阈值、实测比较、缺测不通过，并接通 API、持久化、终态证据与契约回执。属于本仓库实际缺口，不声称移植了竞品性能验收代码。 |
| P1 | 最终配置解释 | 借鉴 DeepSeek 配置 dump。已有 backend profiles、运行快照与厂商绑定信息；下一步只需输出脱敏的最终值、来源层和覆盖原因，帮助定位“为什么用了这个模型/CLI”。需要与模型绑定在途修改协调。 |
| P1 | 插件版本兼容预检 | 借鉴 DeepSeek peer 校验。当前 `plugins.py` 已有 manifest 验证、权限、启停和技能安装；可增最低宿主版本约束、安装前错误与升级后禁用状态保留测试，不引入 Cordis 内核。 |
| P1 | Hook 失败可诊断 | 借鉴 ZCODE 收尾检查的可观测方向。已有三个生命周期事件和超时边界，但失败主要静默跳过；可补脱敏的耗时、退出码、超时和最近错误记录。必须区分旁路 Hook 与真正阻断执行的门禁，不能把现有 best-effort Hook 当作安全拦截器。 |

已存在、不重复建设：`.codebee/spec.md`、需求澄清接口、session/lease、attempt ledger，以及 runner 的 attempt usage/耗时/TTFT/TPS 写入。本轮没有把这些历史建议再次列为新功能。完整 TypeScript 插件内核、Typert RPC、语义缓存、外部 broker 和完整 Agent Team 仍不适合当前范围。

## 已落地接口

创建任务 `POST /api/tasks` 可传：

```json
{
  "type": "code",
  "goal": "生成报告并完成验证",
  "acceptance_criteria": ["acceptance:performance"],
  "acceptance_thresholds": {
    "max_duration_s": 120,
    "max_cost_usd": 0.5
  }
}
```

空闲任务可通过 `POST /api/tasks/{id}/params` 替换阈值，支持既有 `expected_rev` CAS；传空对象清除阈值，不传保持原值。运行中的任务拒绝修改。阈值对象仅允许 `max_duration_s`、`max_cost_usd`、`max_first_token_ms`、`min_tokens_per_sec`；值须为有限非负 JSON 数字，布尔、字符串、未知字段及非法对象返回 400。校验发生在参数修改之前。

每次创建 run 都保存独立的 `acceptance_thresholds`，包括空对象。评估优先读取 run 快照，旧 run 没有该字段时才兼容读取 task。重试新建的 run 使用当时的任务阈值，连载续写沿用阈值。历史结果不会被后续 task 编辑重新分级。

性能结果保持既有六维验收结构，补充 `evidence[].measured/thresholds/checks`：全部已声明指标达标才 `passed`；任一实测超标为 `failed`；其余缺测为 `not_evaluated`。同时出现超标与缺测时保留全部检查、以失败为准。未声明阈值保留测量，但不判通过。终态写入继续生成契约证据和完成回执，不改变主运行状态。

## 测量边界

- 耗时优先使用有效 `duration_s`，否则用完整 `started_at/ended_at` 相减，支持跨天；不累加并行 step 耗时。
- 费用使用当前 run 报告的正费用。默认 `cost_usd=0` 无法证明免费，本轮按缺测处理；这也不是完整供应商账单或所有失败 attempt 成本的证明。
- TTFT/TPS 只有 run 显式提供有效测量才参与比较；当前 attempt ledger 的指标不自动推导成任务级指标。正常执行路径缺少这些 run 字段时保持未评估。
- 本轮提供 API 与持久化支持，没有新增 UI 阈值编辑器或真实模型端到端测试。

## 验证与实际文件

环境 `PYTHONPATH` 包含仓库根、`app` 和 `tests`，使用标准库 unittest，无新增依赖。

- `tests/test_acceptance_performance_gate.py`：16/16，覆盖 API 建立/更新、非法输入无落盘、CAS、运行中拒绝、快照隔离与落盘回读、跨天耗时、零值/相等边界、缺测、非法历史值、契约证据和终态回执。
- `app/tests/test_task_contracts.py`：12/12；`app/tests/test_http_contract_routes.py`：22/22。
- `tests/test_task_timeout_budget.py`：4/4；`tests/test_task_spec.py`：6/6。合计 60 项通过。
- 另将隔离暂存区导出到临时验证目录，仅含 HEAD 与本任务五个文件增量，不含其他聊天未提交代码；同组测试 58/58 通过（HEAD 的 HTTP 集为 20 项，共享工作区在途版本为 22 项）。这确认本次提交不依赖他人未提交实现。
- 修改文件 `compileall` 和定向 `git diff --check` 通过。
- `scripts/architecture_check.py --changed`：未通过，当前共享工作区报 9 项：两条依赖环、runner→paths 层序，以及 manager/modelhub/pipeline/runner/store/main 行数。此次 store 也增加了少量参数接线；未修改架构基线，不宣称全仓门禁绿色。
- 上一轮 `test_workdsh_boundaries.py` 有发布 book_id 用例失败，与本次文件没有交集，尚未验证其干净 HEAD 基线；本轮没有代修或宣称全量测试通过。

本任务文件：`app/core/acceptance.py`、`app/core/store.py` 中阈值相关增量、`app/core/openapi.py` 中阈值 schema、`tests/test_acceptance_performance_gate.py` 和本报告。共享工作区其他修改不属于本次提交。
