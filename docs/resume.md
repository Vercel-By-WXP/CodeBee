# 断点续跑与崩溃恢复

每个执行步骤由 `checkpoints.start`/`finish` 追加到
`data/checkpoints/checkpoints-YYYYMM.jsonl`。启动或重试时通过
`checkpoints.replay_preview(run_id)` 读取每一步最新状态：只有明确的 `done`
步骤可跳过，`running`、`unknown`、`failed` 都需要重新提交，避免把未确认的
外部副作用误当作完成。文件级 Git 快照仍由现有检查点模块负责。

任务报告应展示未完成任务数量；用户可以选择恢复或放弃，放弃操作将 run 标为
`cancelled`，不会删除历史检查点。
