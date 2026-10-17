# 2026-10-17 竞品能力：只读模型调用的流中断恢复

## 官方来源

- [Cline v4.1.25 release](https://github.com/cline/cline/releases/tag/v4.1.25)，2026-10-16：供应商错误导致响应流中断时，在本轮失败前最多重试三次；同版也修复本地模型长时间处理请求时的 BodyTimeoutError。
- [Zed 1.24.2 release](https://zed.dev/releases)，2026-10-14：Agent Panel 改善长线程的 CPU/内存使用，并允许将终端标签拖入 Agent Panel。
- [OpenHands 1.27.0 release](https://github.com/All-Hands-AI/OpenHands/releases/tag/1.27.0)，2026-10-12：自动化表单草稿、agent-assisted 自动化设置，以及 OpenCode/Pi 本地 ACP agent。

本轮选择 Cline 的中断恢复。CodeBee 现有 runner 已能将 429、瞬态错误交给后备模型，但同一模型请求在读取响应时断流会直接结束该候选；而只读评审、规划调用重复执行没有文件写入副作用。

## 改动

- 对 Codex/Claude 的只读调用识别明确的流传输中断信号，最多在当前模型上重试两次，短暂等待并尊重取消及该候选的截止时间。
- 将重试范围限定为只读调用；具有写权限的编码步骤不会自动重放，避免断流后重复工具副作用。
- 在结果中返回 `stream_retries`，并将每次重试记录在步骤日志。既有 Claude 空响应和 OpenCode 横幅秒退逻辑保持原有行为。
- 新增 `tests/test_stream_retry.py`，覆盖中断后恢复、重试上限和写入步骤不重放。

## 验证

- `PYTHONPATH=app python -m unittest discover -s tests -p 'test_stream_retry.py' -v`
- `PYTHONPATH=app python -m unittest discover -s tests -p 'test_execution_guards.py' -v`
- `PYTHONPATH=app python -m unittest discover -s tests -p 'test_opencode_banner_retry.py' -v`
- `python -m py_compile app/core/runner.py tests/test_stream_retry.py`
- `git diff --check`

所有上述检查通过。测试不连接生产服务 `8765`，不发起真实供应商调用。

## 边界与后续

自动重试只重放只读 Codex/Claude 调用，不含 ACP 和通用 CLI。只读文件沙箱不能证明 MCP 或网络工具没有外部副作用；因此该能力只应在只读/评审任务中使用，调用方需避免把有副作用的外部操作交给这类任务。重试仍消耗时间与可能的供应商费用，且若服务端已完成推理但客户端只收到断流，调用方无法确认该次是否计费。可在获得更多真实 CLI 样本后再评估扩展协议范围或重试预算。
