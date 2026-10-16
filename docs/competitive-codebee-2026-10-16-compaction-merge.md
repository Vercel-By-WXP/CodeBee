# CodeBee 竞品能力优化跟进：ACP 压缩事件合并（2026-10-16）

## 依据

- [Zed v1.25.0-pre](https://github.com/zed-industries/zed/releases/tag/v1.25.0-pre)
  将 external-agent context compaction 与 session notices 作为 ACP 能力发布。
- [ACP Session Compaction RFD](https://agentclientprotocol.com/rfds/session-compaction.md)
  允许一次压缩通过 `compaction_update` 和多个 `compaction_summary_chunk` 逐步更新。

## 本轮选择

CodeBee 已能保存并展示 ACP 事件，但详情只取最近 8 条原始事件。同一 `compactionId` 的多个摘要分块会占满这个窗口，导致状态更新或摘要内容被分散。此次只改详情展示层：同一 ID 的压缩更新与分块合并成一项，保留最新状态/摘要，并把文本分块按顺序拼接且限制为 2400 字符；其他 ACP 事件顺序和 HTML 转义保持不变。

## 验证

- `node tests/acp_event_ui_runtime.mjs`：通过，覆盖多分块、最终状态合并、摘要显示和 HTML 转义。
- `node --check app/ui/app.js`：通过。

## 风险与下一步

展示层仍只处理后端已保留的文本事件；超过最近 8 条原始事件窗口的旧分块不会被恢复。后续可在真实 ACP CLI 样本稳定后，把合并逻辑下沉到后端持久化层并增加按 `compactionId` 的端到端回放测试。
