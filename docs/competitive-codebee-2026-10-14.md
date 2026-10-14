# CodeBee 每日竞品巡检：暂停状态可观测性

日期：2026-10-14（Asia/Shanghai），自动化 `codebee-2`。在主目录 main 执行。结论：本轮完成研究和本地诊断，仅新增本报告；不修改产品代码、测试文件或架构基线，不提交、推送、部署或删除用户数据。

## 官方来源与 CodeBee 对照

下列来源本轮均实际读取。明确日期只用于来源标注日期的发布；未标日期的文档和源码不称为今日新增。版本列表不是安装验证，产品可用性不作超出来源的推断。与上轮记忆对照后，不重复实施批量暂停和自动化草稿恢复。

| 产品 | 核验来源与能力 | CodeBee 对照与取舍 |
| --- | --- | --- |
| Codex | [官方 changelog](https://developers.openai.com/codex/changelog)：10-07 长线程流式性能、审批选择更清晰、状态更新不再显示为待回答问题；10-02 取消排队提示词可恢复文本和附件。 | 已有信箱、撤回、审批和长会话。应继续让运行状态表达真实执行阶段；不把版本号列表视为新功能证据。 |
| ZCode | [Z.ai 官方插件市场](https://cdn-zcode.z.ai/zcode/official-plugin/marketplace.json)：CloudBase 技能/MCP、Mimosa 1.0.4 写入前 hooks、收尾复核、Git 门禁。未标发布日期。 | 已接入市场与安全门禁，本轮不重复扩展。 |
| Cursor | [官方 changelog](https://cursor.com/changelog)：10-13 Origin App Marketplace，包含部署、CI、评审、安全、监控和项目管理；10-06 本机代理 iOS 远程控制。 | 市场集成范围较大，远程连接已有；优先完善本地接管反馈。 |
| Zed | [Preview releases](https://zed.dev/releases/preview)：1.24.2（10-12）检查点使用遥测为 opt-in；1.24.1（10-07）长线程 CPU/内存优化、终端拖入 Agent Panel。 | CodeBee 检查点与聊天工具活动正在共享工作区改进，避开重叠。 |
| Windsurf / Devin Desktop | [官方 changelog](https://windsurf.com/changelog)：当前页面标题为 Devin Desktop；09-29 噪声终端输出内存修复、未聚焦时会话轮询退避；09-08 页面明确记载 Cascade 移除。 | 本轮纠正旧名称的现行状态；输出和会话渲染也是当前在途工作，不重复修改。 |
| Cline | [官方 releases](https://github.com/cline/cline/releases)：v4.1.24（10-13）修复过期模型 404 被误报为登录问题、错误详情丢失、图片误算文本 token；SDK v0.0.93 的插件状态显式区分 loading/ready/degraded/failed/disabled。 | 故障与状态应如实表达，与暂停请求/到达闸门的区分方向一致；具体状态设计由 CodeBee 架构决定。 |
| Roo Code | [Checkpoints](https://docs.roocode.com/features/checkpoints)：独立 shadow Git、文件修改前快照、差异和恢复；命令执行前不自动快照。无日期。 | 已有检查点，边界不可混同，当前不扩展恢复功能。 |
| Claude Code | [官方 CHANGELOG](https://raw.githubusercontent.com/anthropics/claude-code/main/CHANGELOG.md)：2.1.299 列出空闲压缩、日志内存修复、headless 运行停止边界修复；该源码条目没有发布日期。[Checkpointing](https://code.claude.com/docs/en/checkpointing) 说明每轮快照、最近 100 个检查点和恢复/摘要选择。 | 已有压缩、时限和检查点；不把源码版本推断为今日发布，不盲目扩大生命周期功能。 |
| GitHub Copilot Workspace / 当前 Copilot | [GitHub 官方 agent 页面](https://docs.github.com/en/copilot/concepts/agents/coding-agent/about-coding-agent)：当前标题为 Copilot on GitHub.com，包含规划、云端执行、代码评审、定时/事件自动化。无日期。 | 现有编排与自动化覆盖主要方向；此页不能确认旧 Workspace 独立产品的可用状态。 |
| Goose | [官方 releases](https://github.com/block/goose/releases)：链接当前转向 aaif-goose/goose；v1.54.0（10-08）交互式 HTML 会话导出、扩展操作 lease、前台子代理状态机；1.53.0（10-02）引用会话文字到输入框。 | 会话导出是候选，但与在途聊天呈现相邻；本轮不增加第二项建议实施。 |
| OpenHands | [官方 releases](https://github.com/OpenHands/OpenHands/releases)：1.27.1（10-12）Docker 模块和主题警告修复；1.27.0 自动化草稿、ACP 模型列表来自代理、导航前刷新聊天草稿。 | 草稿已由上一轮实现；不重复。 |
| Devin | [Recent Updates](https://docs.devin.ai/release-notes/overview)：个人记忆与夜间整理、细分通知偏好、自动化失败通知在第 1/3/9…次退避，成功后恢复；页面顶部条目未显示日期。 | CodeBee 已有知识/经验和候选链失败通知；自动化级退避需要另行核对，不在本轮顺带实现。 |
| Replit Agent | [官方 Updates](https://docs.replit.com/updates)：10-09 Windows 本地构建私有预览，MXC/OpenShell 隔离；从一段聊天跨项目读取和派发后台任务，不自动发布。 | 本地 Windows 与可接管方向相近；全新沙箱和跨项目调度超出本轮可控范围。 |

GitHub API 的 releases 抓取被 robots 检查拒绝，随后改读官方 GitHub HTML 发布页成功；没有绕过拒绝或把 API 错误当成无更新。未安装竞品、插件、调用付费模型或访问用户服务。

## 本轮唯一优先建议：区分暂停请求与真正挂起

CodeBee `store.set_paused/set_paused_all` 立即保存 `run.paused`；`pipeline._wait_gate` 只在下一步骤入口阻塞。`pause_control.paused` 统计的是请求标志，不是停止执行的数量。当前步骤仍可能调用 CLI 或写文件，但 `app/ui/app.js` 的消息提示使用“已暂停：指令入箱，放行后随下一步送达”，toast 使用“已暂停：当前步骤跑完后挂起”。

建议把用户可见阶段明确为“暂停请求已接收，当前步骤继续完成”和“已到达暂停点，等待放行”。这项工作复用现有闸门，用户能明确判断何时可以检查工作目录；无需新增协议、云服务、暂停进程或恢复功能。它是基于竞品状态透明度和 CodeBee 实现推导的建议，不宣称竞品均有相同字段。

拟定验收边界：

1. 当前步骤执行中请求暂停，界面显示等待步骤结束，不能声称 CLI 已停止。
2. 到达下一步骤闸门后显示真正挂起，恢复后清除等待态。
3. 暂停中取消能及时退出；终态、服务重启和异常收尾不得遗留假挂起状态。
4. 同一运行若有并行步骤，以每个闸门/步骤的状态汇总，不能用单个布尔值错误宣称全部停止。
5. 混合 queued/running/终态、批量控制、窗口外运行与 SSE/重连都覆盖；总时限沿用现行行为，不能静默冻结预算。

本轮不实施：目标涉及 `app/core/store.py`、`app/main.py`、`app/ui/app.js`、`app/ui/i18n.js`，开始时均有其他聊天的未提交工作。运行中新增 `review_requests.py` 改动，app.js 的 diff 也变化，说明共享工作尚未收口。已有架构闸失败；在此时加入跨层状态字段不适合安全验证。仅新增独立报告，不覆盖现有文件。

## 前两轮执行链失败的根因核验

原测试命令：`python -m unittest discover -s tests -p 'test_direct_steering.py' -v`，10 项中 8 通过、2 失败。

`TestDirectiveInjection` 和 `TestPauseGate` 只调用 `create_run`，没有关联任务。当前 `_run_step` 在非 mock 模式读取关联任务的沙箱策略；任务缺失时返回“无法读取任务沙箱策略，外部智能体执行已拒绝”。两例的失败发生在启动伪造 CLI 前。这不能证明指令/暂停实现失效，更不能为使旧测试通过而移除保护。

只在本次 Python 进程中包装 `store.create_run`，为这两个用例通过 `store.create_task(type='code', goal=title, workdir=case.workdir)` 创建临时任务，再关联其 id。未修改任何源码或测试文件，也未 mock 掉沙箱校验。两项分别通过（1/1 + 1/1），指令注入、消费、防重复、等待放行和取消断言均执行。原测试仍为失败状态，不能称正式修复或全量通过。

所有数据沿用 `BaseTest` 的临时目录；真实 CLI 进程入口由原测试 mock。此对照没有调用真实模型或变更用户任务。

## 验证结果

| 检查 | 结果 |
| --- | --- |
| `test_direct_steering.py` 原样 discover | 8/10；两项缺关联任务被沙箱闸拒绝 |
| 内存中补齐真实任务夹具后运行上述两项 | 2/2；仅诊断对照，不是持久化修复 |
| `python -m unittest discover -s tests -p 'test_external_sandbox_gate.py' -v` | 4/4，默认策略、收窄拒绝、Codex 与 mock 语义通过 |
| `node tests/automation_draft_runtime.mjs` | 通过，上一轮草稿运行回归保持可用 |
| `node tests/check_ui_syntax.mjs` | 8 个 UI JavaScript 文件全部通过 |
| `git diff --check` | 通过；有既存 LF/CRLF 提示 |
| `python scripts/architecture_check.py --changed` | 失败：7 项；未修改基线 |

架构闸当前报：两条依赖环；review_requests(L99)→paths(L0) 的层序违规；builtin_agent 2192、publish/manager 1422、store 2654、main 4166 行超基线。该命令检查共享工作区，不能把所有失败都归给任意单一聊天或宣称完整干净 HEAD 基线失败。两条环与前轮记录一致，其余当前状态在案；未回退、stash 或覆盖文件做实验。

## 实际文件、风险和下一轮

本轮仓库写入仅 `docs/competitive-codebee-2026-10-14.md`；另更新自动化记忆并调用项目会话归档。没有产品行为变化，没有 Git 写操作。

风险仍在：原执行链两例的夹具缺失尚未正式修正；暂停标志依旧表示下一步停止请求；共享工作区架构检查不全绿；真实模型、安装包和完整 UI 没有本轮端到端验收。未标日期的竞品能力不能称为昨晚以来新增。

下一轮先复查共享修改是否收口，正式为两项旧回归补齐任务夹具并保留缺失任务拒绝的负例；随后在可独立验证的窗口实施上述唯一产品建议。避免顺带新增市场、通知或沙箱体系，也不通过放宽保护和更新架构基线制造绿色结果。
