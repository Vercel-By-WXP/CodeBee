# CodeBee 每日竞品能力优化：步骤间批量暂停

检查日期：2026-10-12，Asia/Shanghai。自动化：`codebee-2`，首次运行。

本轮在主目录 `E:/GoOut/MultiAgentOrchestration` 的 `main` 开发。只实现一个改进：运行记录页的「全部暂停 / 全部放行」，复用既有步骤暂停闸门。专项验证通过，但执行链与架构检查尚未全绿；不能将其表述为完整发布验收通过。

## 官方资料与现状对照

下列页面均在本轮实际读取。只有带明确发布日期的项目被称为近期发布；未注明日期的文档只说明当前公开能力。失败或身份不明的 URL 不作为证据，也未安装竞品或调用付费模型。

| 产品 | 本轮核验的能力与来源 | CodeBee 对照 / 本轮决策 |
| --- | --- | --- |
| Codex | [官方 changelog](https://developers.openai.com/codex/changelog)：2026-09-01 Priority 视图优先展示运行、未读和待回复任务；排队提示跨主机同步。2026-09-08 支持工作进行中回答问题。 | 已有侧栏活动、消息信箱、运行控制；多任务整体控制有缺口。 |
| ZCode | [Z.ai 官方插件清单](https://cdn-zcode.z.ai/zcode/official-plugin/marketplace.json)：CloudBase 技能/MCP、Mimosa 1.0.4 写入前 hooks/收尾审查/Git 门禁、GitHub 工作流插件。未给出发布日期。 | 已接入该官方市场，已有技能安装安全闸，本轮不重复扩展。曾探测的 zcode.ai 是无关体育站点，已排除。 |
| Cursor | [官方 changelog](https://www.cursor.com/changelog)：2026-10-06 Local Agents Remote Control，可在 iOS 查看并回复本机代理；代理继续在电脑执行。2026-09-23 Rollouts 与 Security Review。 | CodeBee 已有手机连接、控制权和 SSE；本轮改善同一用户对多任务的可逆控制，不添加新的远程基础设施。 |
| Zed | [AI Overview](https://zed.dev/docs/ai/overview)：Threads Sidebar 支持多个代理和 Terminal Threads，ACP 外部代理与 Parallel Agents。 | CodeBee 已有跨 CLI 多任务编排、ACP 和任务侧栏；不新增代理协议或工作树。 |
| Windsurf / Cascade | [官方 Cascade 文档](https://docs.windsurf.com/windsurf/cascade/cascade)，当前标题为 Devin Desktop：排队消息、可撤回、命名检查点、自动继续与调用上限。 | 已有消息信箱、撤回和检查点；控制任务节奏的缺口更小且可本地验证。品牌状态以读取页面为准，不推断迁移日期。 |
| Cline | [Checkpoints](https://docs.cline.bot/features/checkpoints)：独立 shadow Git，回退文件并保留对话。当前文档也列出桌面、Agent Teams、Subagents。 | 已有文件快照与外部改动冲突保护；检查点正在其他聊天修改，本轮避开。 |
| Roo Code | [Checkpoints](https://docs.roocode.com/features/checkpoints)：文件差异、Restore Files Only / Files & Task；文件修改前自动快照，命令执行前不自动快照。 | 回退的边界需要明确，但本轮不动检查点实现。 |
| Claude Code | [Checkpointing](https://code.claude.com/docs/en/checkpointing)：按轮保存快照，恢复代码/对话或局部摘要，恢复会话后仍可 rewind。 | 已有检查点与上下文压缩；本轮复用既有暂停而不重新设计恢复。 |
| GitHub Copilot Workspace / 当前 Copilot | [官方 agent 页面](https://docs.github.com/en/copilot/concepts/agents/coding-agent/about-coding-agent)：GitHub 上的规划、代理执行、评审与定时/事件自动化。此 URL 当前标题为 Copilot on GitHub.com。 | CodeBee 已有自动化、任务链与评审。本轮没有核实旧 Workspace 独立产品的现行可用状态，不作断言。 |
| Goose | [官方仓库 README](https://raw.githubusercontent.com/block/goose/main/README.md)：本地原生桌面/CLI/API、多供应商、ACP、MCP 扩展。旧 session-management 文档路径 404，改用官方源码资料。 | CodeBee 的本地、多供应商架构已有相同方向，本轮无需新增依赖。 |
| OpenHands | [官方仓库 README](https://raw.githubusercontent.com/OpenHands/OpenHands/main/README.md)：Agent Canvas，自托管跨本地/远程/云后端，支持 ACP 代理与自动化。 | CodeBee 已有后端配置、ACP、自动化；后端扩展范围大，不适合此轮。 |
| Devin | [When to Use Devin](https://docs.devin.ai/essential-guidelines/when-to-use-devin)：多会话并行、Managed Devins、自动评审/修复、浏览器自测、Schedule 自动化。 | 已有跨厂商评审/修复和自动化；全局步骤暂停对本地多人/多任务共享环境更有直接价值。 |
| Replit Agent | [官方 Agent 文档](https://docs.replit.com/replitai/agent)：Plan mode、多产物、模型路由与后台任务。 | 已有规划、产物预览、类型和模型路由；本轮不扩展付费模式/云部署。 |

以上共同体现并行工作、可接管、可恢复的产品方向；没有证据表明所有竞品均提供「批量步骤暂停」。此功能是针对 CodeBee 的设计推导。

## 选择理由与行为

CodeBee 已支持单次运行暂停和全部取消。用户要临时检查多个并行任务时，逐条暂停成本高，全部取消则会终止当前执行。新增批量暂停复用 `_wait_gate`，保留任务、消息、上下文与历史，范围只涉及存储/状态投影/路由/按钮，无新依赖。

- 运行记录页有活动编排任务时显示按钮；混合暂停态显示「全部暂停」，全部已暂停显示「全部放行」。
- 后端全量扫描 `kind=orchestration` 且 `status=queued/running` 的运行；管理/安装操作没有步骤暂停闸门，排除在外。
- `/api/state.pause_control` 返回全量 `active/paused` 计数，不受最近 40 条运行窗口限制。
- `POST /api/runs/pause_all` 仅接受明确的布尔 `paused`，复用既有鉴权和单设备控制权闸；返回实际改变的数量。
- 使用既有存储锁串行化状态与落盘写入；重复请求幂等，终态记录不变。
- 按钮在确认与请求期间禁用，重复点击不会发送重复请求；取消确认恢复按钮。新增中英文文案。
- 暂停发生在下一步骤开始前；正在执行的步骤会继续。新建任务不受影响；总时限、单步骤内工具循环行为沿用原有设计。它不承诺立即冻结进程或冻结时间预算。

## 本轮文件

| 文件 | 变更 |
| --- | --- |
| [store.py](/E:/GoOut/MultiAgentOrchestration/app/core/store.py) | `pause_control` 全量计数与 `set_paused_all` 批量持久化。 |
| [main.py](/E:/GoOut/MultiAgentOrchestration/app/main.py) | POST 路由、布尔校验和状态投影。 |
| [openapi.py](/E:/GoOut/MultiAgentOrchestration/app/core/openapi.py) | 请求/响应与 400/401/423 文档。 |
| [app.js](/E:/GoOut/MultiAgentOrchestration/app/ui/app.js) | 按钮状态、确认、请求、防重复、反馈和刷新。 |
| [index.html](/E:/GoOut/MultiAgentOrchestration/app/ui/index.html) | 运行记录头部入口。 |
| [i18n.js](/E:/GoOut/MultiAgentOrchestration/app/ui/i18n.js) | 新 UI 文案的英文词条。 |
| [test_direct_steering.py](/E:/GoOut/MultiAgentOrchestration/tests/test_direct_steering.py) | 独立 `TestSwarmPause`；终态/管理过滤、幂等、落盘、窗口外任务、消息保留。原 `TestPauseGate.runTest` 保留。 |
| [test_http_contract_routes.py](/E:/GoOut/MultiAgentOrchestration/app/tests/test_http_contract_routes.py) | HTTP 响应、错误输入、控制权、全量投影与 UI 入口契约。 |
| [ui_direct_steering.mjs](/E:/GoOut/MultiAgentOrchestration/tests/ui_direct_steering.mjs) | 浏览器验证按钮、窗口外计数、请求方向、防双击/取消；旧 SSE 断言从正文改为真实摘要字段 `message_count`。 |
| [本报告](/E:/GoOut/MultiAgentOrchestration/docs/competitive-codebee-2026-10-12.md) | 来源、对照、实施和验证记录。 |

## 验证结果

测试先补，再落实现有代码。默认 Python 为 3.8 32 位，没有 pytest，采用 unittest。第一次运行因导入入口不正确失败；最终命令如下：

```powershell
$env:PYTHONPATH='E:\GoOut\MultiAgentOrchestration\tests'
python -m unittest test_direct_steering.TestSwarmPause -v
# 2/2 通过
$env:PYTHONPATH='E:\GoOut\MultiAgentOrchestration\app'
python app/tests/test_http_contract_routes.py -v
# 18/18 通过
python -m unittest discover -s tests -p 'test_state_payload.py' -v
# 14/14 通过
python -m unittest discover -s tests -p 'test_i18n_key_coverage.py' -v
# 3/3 通过
node tests/ui_direct_steering.mjs
# Edge headless + 隔离临时服务：39/39 通过，不使用用户服务/数据
node --check app/ui/app.js
node --check app/ui/i18n.js
node --check tests/ui_direct_steering.mjs
python -m py_compile app/core/store.py app/main.py app/core/openapi.py app/tests/test_http_contract_routes.py tests/test_direct_steering.py
git diff --check
# 语法与 diff 检查通过
```

更广回归 `python -m unittest test_direct_steering -v`：10 项中 8 通过，`TestDirectiveInjection`、`TestPauseGate.runTest` 在 `_run_step` 返回 `ok=False` 处失败。将 HEAD 的原始测试内容在内存加载后复测这两项，也失败。未回退文件或修改执行链；这不是完整干净 HEAD 的全量基线验证，失败根因尚未查明。

架构 `python scripts/architecture_check.py --changed`：4 项失败（两条依赖环、store/main 行数）。只读替换本轮 Python 文件为 HEAD 内容再运行，仍报相同类别：store 2404 > 历史 2347，main 3952 > 历史 3754。本轮最终为 2434 / 3960 行；没有新增依赖边，但加重了文件行数问题。未更新基线或放宽策略。下一轮应以拆分模块与修复执行链测试为先。

已逐文件复核本轮 diff、输入/权限闸、锁、幂等、UI 状态与多语言。没有提交、暂存、推送、部署、安装插件、调用真实模型或删除用户数据。

## 共享工作与未解决风险

开始时已有 `app/tests/hive3d_webgl_smoke.mjs`、`app/ui/hive3d.js` 的暂存改动，及 style.css/移动探针改动。均未触碰；其他聊天在运行中自行改变 HEAD/工作区状态。本轮后续观察到 checkpoints.py、桌面 Windows 兼容打包文件、检查点新测试、借鉴日志等变化，也均排除在本轮范围。

当前整体质量检查未全绿；不能直接作为发布结论。批量持久化沿用逐文件 JSON 保存，磁盘写入错误可能导致部分任务已暂停，接口也会报错；它不是磁盘层面的多文件事务。暂停是本轮请求时的活动任务集合，不是阻止未来任务进入的全局调度锁。没有验证真 CLI/付费模型的长期运行，也没有验证 Windows/macOS 安装包内的显示。

下一轮：先调查两项执行链失败并把运行控制从巨型 store/main 模块拆出；架构闸恢复后，再考虑展示「等待当前步骤完成」与「已到达暂停闸门」的不同状态，避免用户误以为正在执行的工具已被立即停止。
