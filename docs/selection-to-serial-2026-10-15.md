# 扫榜选材审核与连载交接

扫榜选材原先使用 direct 引擎，一次输出报告即结束；没有选题评审/打磨，也没有确定的连载输入。新版改用 review 引擎：榜单分析和候选立项 → 多维评审 → 定向打磨 → 复审 → 连载输入门禁。默认 3 轮评审，最多两次返修；快速模式仍保留至少两轮评审预算，合格可提前结束。

## 怎么用

1. 新建「扫榜选材」任务，写目标平台、读者、题材、希望改进的拒稿问题，可附编辑反馈和自己读过的对标正文。方向也可留空。
2. 等评审结束。在详情页点「从选材新开连载」。旧版直出报告须先重跑任务，进入新流程。
3. 选择审核后的候选，查看书名、主线和新目录路径，设置试写章数与每章字数，点击「新建目录并开始连载」。
4. 系统在 **选材任务工作目录/书名/** 下建立独立文件夹，导入标准方案与故事圣经，并打开新连载运行。默认试写 8 章×2500 字，后续使用现有「继续连载」。

目录以审核后的书名命名。书名含非法路径字符/Windows 保留设备名时须先修改并重新审核。同名目录存在则拒绝覆盖。相同来源、同一候选重复提交会返回原任务；启动失败保留已创建的新书任务，在侧栏打开后重试即可。

## 审核到底检查什么

默认六维：证据可靠、机制差异、开篇因果、人物选择、长篇可写性、交接完整。内部阈值默认 8 分；任一维不达标、评审缺失、JSON 不完整或存在未解决 major，均不能进入开书入口。问题驱动返修后重新评审，不靠提高均分掩盖严重问题。

必须写清首个事件、主角选择及代价、读者回报、前三章因果链、长篇的变化与回收机制。参考拒稿反馈分析，不把改名、换职业、加怪声或措辞润色当作机制创新。评分是内部流程判据，不预测平台签约结果。

榜单页面当前主要提取书名/分类，保留跨平台重合用于 A-E 分级。同一本书跨平台不重复计算机制饱和证据。缺乏正文时，机制饱和/平台适配只能列待核实与验证办法，不能宣称已精读或使用平台真实标准。资料抓取失败时明确标缺口，仍可提出带风险说明的试写方案。

## 标准输出

`rank-scan.md` 为报告，末尾一个 JSON 代码块：`schema_version: 1`、1–3 个 `candidates`。

每个候选包含：`id`、`title`、`platform`、`audience`、`genre`、`premise`、`mechanism`、`differentiation`、`protagonist`、`opening_event`、`first_three_chapters`（3项）、`milestones`（20000/30000）、`long_term_engine`、`story_bible`、`risks`。

审核通过后冻结到运行目录的 `selection-package.json` 和 `selection-report.md`；来源包括任务 ID、运行 ID、正文 SHA-256。启动时对照原始快照复核，后续改动工作目录的报告不会改变已审核方案。最新运行未通过时不会自动拿旧方案开书。

新书目录保存 `selection-package.json`（仅所选候选）、`selection-brief.md`、`story-bible.md`、`.selection-origin.json`（来源/重复提交记录）。方案完整注入连载上下文；连载采用专家模式、3轮评审、8分内部阈值，继承网络/工具/执行策略，并把允许目录收窄到新书目录。

2万/3万字作为内部试写评估和首单元闭环检查点，不能当成各平台通用投稿规定。若实际试写未达到节点，不得宣布完成对应检查；平台要求须按频道和当前材料核实。

## API

- `GET /api/tasks/{task_id}/selection`：读取最新一次审核通过的候选和来源 run_id，旧报告或未通过返回400。
- `POST /api/tasks/{task_id}/selection/serial`：`{run_id, candidate_id, chapters?, words_per_chapter?}`；章节数2–20，每章500–8000字。成功返回新/复用的 task_id、run_id、workdir、source；创建和入队沿用普通任务通路。需要既有认证和写控制权。

## 验证与边界

确定性测试覆盖：高分 major 触发打磨、缺包不能通过、快档保留审核、快照防串稿/篡改、旧版报告拒绝开书、书名路径校验、同名目录保护、重复提交复用、失败保留任务及最新未通过不能回退旧方案。

浏览器测试 `node tests/ui_selection_handoff.mjs` 使用隔离数据和本机 Edge 无头模式，覆盖选方案、目录预览、章数校验、重复点击单次提交、新连载运行打开和按钮状态清理；不会调用真实模型或创建用户作品。

本次验证不包含真实平台签约、当前平台政策实抓或真实模型选题质量对比，不能据此声称提高了过签率。

## 本次实施与验收记录

实施文件：`app/core/selection.py`（交付契约、结构校验和审核快照）、`selection_launch.py`（选材读取、目录保护、幂等开书）、`pipeline.py`（评审返修与旧任务运行时升级）、`flows.py`/`task_compile.py`（新默认流程）、`paihang.py`（来源归属、采集时间和保守证据判断）、`store.py`（纳秒级创建时间，避免同秒随机ID颠倒新旧审核）、`usage.py`（选材耗时估算）、`flow_graph.py`/`openapi.py`（流程和接口描述）、`app/main.py`（读写路由与失败回传任务ID）、UI三件（入口、选书弹窗、英译）。同时更新受流程变更影响的既有测试、架构模块分层和UI测试忽略例外。未创建分支、未提交或推送共享工作区。

验证结果：

- `python -m unittest discover -s tests -p test_selection_handoff.py -q`：12项通过，覆盖审核/返修、越界评分阻断、旧任务升级、快照、同名目录、幂等与失败保留。
- `PYTHONPATH=app python -m unittest discover -s app/tests -p test_selection_routes.py -v`：4项通过，包含认证与写控制权。
- `PYTHONPATH=app python -m unittest discover -s app/tests -p test_task_contracts.py -q`：12项通过。
- 相关流程、交付契约、证据分级、注册表、国际化和架构检查器用例合跑111项通过；之后增加的旧任务重跑、越界评分两项另跑通过。Python编译、JavaScript语法和`git diff --check`通过。
- `node tests/ui_selection_handoff.mjs`：真实Edge无头浏览器通过，DPR=1，视口1416×858。
- 全部`app/tests`：277项，2失败、6跳过；失败为`test_plugins.PluginTests.test_enabled_plugin_mcp_is_namespaced_in_client_settings`、`test_install_toggle_uninstall_and_mcp_declaration`，均提示插件缺少显式Write能力授权，与本次选材实现无关。日志：`%TEMP%/codebee-selection-app-tests.log`。
- 全部`tests`尝试：进程返回0但日志含失败进度且缺少`Ran … / OK / FAILED`收尾，**不能算全量通过**。保留`%TEMP%/codebee-selection-full-tests.log`供后续整体测试基础设施排查。
- 全仓架构检查仍未通过，报告37项，包含现有/并行模块依赖、环和大文件行数超基线；本次新增两模块已显式分层，未新增其循环/反向依赖。日志：`%TEMP%/codebee-selection-architecture.log`。

可复用经验：市场证据按平台保留归属，正文机制判断与榜单线索分开；质量门禁需要结构校验、完整评审和严重问题阻断；跨任务交接使用审核快照；新书目录独占创建并记录来源；不要把随机后缀ID排序当作同秒创建顺序；全量测试缺少结束汇总时不能只凭退出码0报绿。
