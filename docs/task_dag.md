# 任务 DAG 与并行调度

规划器子任务可带稳定 `id` 和 `depends_on` 列表。执行层先调用
`app.core.task_graph.topological_order`，对未知节点和循环依赖直接报错；无依赖
的节点进入同一批次，由 `run_dag` 使用标准库线程池并行执行。每个节点结果写入
检查点后才会解锁下游节点。默认并发数为 4，CLI 级限制由 `CliConcurrency` 控制。

示例：

```json
{"subtasks": [
  {"id": "analyze", "title": "分析", "detail": "..."},
  {"id": "implement", "title": "实现", "depends_on": ["analyze"]}
]}
```

失败节点不会自动满足依赖；恢复运行时仅跳过状态为 `done` 的节点。
