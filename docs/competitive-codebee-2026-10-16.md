# CodeBee 竞品能力优化记录（2026-10-16）

## 查到的能力

- [Zed v1.25.0-pre release notes](https://github.com/zed-industries/zed/releases/tag/v1.25.0-pre)
  于 2026-10-14 发布，明确写入「context compaction for external agents」和
  「session notices for external agents using ACP」。
- [ACP Session Compaction RFD](https://agentclientprotocol.com/rfds/session-compaction.md)
  定义 `compaction_update` 与 `compaction_summary_chunk`：客户端应在时间线中显示压缩开始/结束，
  并可展开保留的用户可见摘要。
- [ACP Session Notices RFD](https://agentclientprotocol.com/rfds/session-notices.md)
  定义带 `severity`、`title`、`description` 的 `notice`，用于展示降级、可选集成不可用等运行信息，
  且不把它们伪装成对话正文。

## 为什么适合 CodeBee

CodeBee 已在 ACP 客户端声明 v1 `session.compaction`/`session.notices` 能力，并将受限事件写入运行步骤的
`acp_events`。但详情对话的「ACP 活动」折叠区此前只显示事件类型和少量状态，压缩保留摘要、通知详情会被
丢在用户视野之外。补齐展示层可以直接利用已有持久化契约，不改执行、权限或沙箱边界，也适用于任何实现
这些 ACP 更新的本地 CLI。

## 本轮改动

- `app/ui/app.js`：将 `compaction_update`、`compaction_summary_chunk`、`notice` 映射为可读标题；显示
  `compactionId`、状态/严重度、通知标题与描述、压缩摘要或分块文本；所有文本继续经过 `esc()`，计划事件
  保持原有展示。
- `app/ui/style.css`：为 ACP 事件增加分隔、标题、详情和可滚动摘要样式，摘要有高度上限，避免长文本撑开对话。
- `app/ui/i18n.js`：补充「上下文压缩」「压缩摘要」「通知」英文词条。
- `tests/acp_event_ui_runtime.mjs`：用真实 UI 函数体回归验证压缩、通知、计划事件的展示和 HTML 转义。

## 验证

- `node tests/acp_event_ui_runtime.mjs`：通过。
- `node tests/check_ui_syntax.mjs`：9 个 UI JavaScript 文件全部通过。
- `PYTHONPATH=app python -m unittest discover -s app/tests -p 'test_agent_governance.py'`：ACP/治理测试通过（当前
  环境用 `python -m unittest app.tests.test_agent_governance` 等价运行 11 项）。
- `git diff --check`：通过。
- 全量 i18n 对账仍有仓库既有 4 个缺口（`新建项目`、`项目概览`、`成员权限`、`移动适配`），本轮没有修改这些
  无关词条。

## 尚未解决的风险

- ACP 客户端只保留受限的文本摘要，不渲染图片或其他 ContentBlock；这是有意的安全边界，避免把外部内容
  当作 HTML 执行。
- 事件最多保留最近 200 条、详情最多显示最近 8 条；重连/历史回放仍依赖上游运行记录契约。
- 现有并行工作区仍有未提交的任务完成通知和临时文件，本轮提交只包含下列 ACP hunk 与新增文档/测试。

## 下一轮建议

继续观察真实 ACP CLI 的压缩事件是否包含可展示摘要，并为 `compactionId` 的多次更新做前端合并；若协议
实现仍不稳定，先保留状态事件而不要扩大到富媒体渲染。
