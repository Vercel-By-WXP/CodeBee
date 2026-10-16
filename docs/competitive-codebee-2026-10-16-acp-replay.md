# CodeBee 竞品能力优化：ACP 压缩事件后端回放（2026-10-16）

## 依据

- [Zed v1.25.0-pre 发布说明](https://github.com/zed-industries/zed/releases/tag/v1.25.0-pre)：external-agent context compaction 与 session notices。
- [ACP Session Compaction RFD](https://agentclientprotocol.com/rfds/session-compaction.md)：一次压缩可以由状态更新和多个 `compaction_summary_chunk` 组成。

## 问题

CodeBee 原先把最多 200 条 ACP 原始事件直接落盘，详情页只展示最后 8 条。长压缩流会让摘要早期分块退出可见窗口；此外，部分 ACP 实现把分块内容作为纯字符串发送，而记录器只接受文本 `ContentBlock`，造成摘要丢失。

## 改动

- `app/core/acp_client.py` 接受纯字符串或文本 ContentBlock 两种分块形态。
- 在成功结果写入运行记录前，按 `compactionId` 合并压缩状态更新和摘要分块，保留首个时间线位置、最新状态/摘要和有序文本。
- 对同一 `compactionId` 的后续更新在采集时原位合并；即使 200 条活动窗口已满，也允许既有压缩继续追加分块，新的活动仍受总量上限约束。
- 合并文本限制为 2400 字符；非压缩事件保持顺序，输入事件不会被原地修改。
- ACP 失败、超时和取消结果也携带收集到的受限活动，走既有 pipeline/store/API 路径，步骤仍保留原来的失败状态。
- `app/tests/test_agent_governance.py` 增加真实 stdio 假进程回放、字符串分块、状态合并、窗口满后续传、失败回放、边界和脱敏回归覆盖。

## 验证

- `PYTHONPATH=app python -m unittest app.tests.test_agent_governance`：16/16。
- `node tests/acp_event_ui_runtime.mjs`：通过。
- `node tests/check_ui_syntax.mjs`：9 个 UI 文件通过。
- `python -m py_compile app/core/acp_client.py`：通过。
- `git diff --check`：通过。

## 风险与后续

富媒体摘要仍被忽略，非压缩活动总事件窗口仍为 200 条。后续可用真实 ACP CLI 回放样本补充端到端持久化测试，并验证 UI 中失败步骤确实能展开 ACP 活动。
