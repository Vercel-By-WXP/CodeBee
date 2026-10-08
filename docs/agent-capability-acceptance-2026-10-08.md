# Agent 六项能力验收（2026-10-08）

## 验证范围

代码基线为 `bcf9c992e07ea78a3345b60075dd8c4e97535030`，已包含前轮实现与安全修复（包括 `4be9c7f` 的工具结果引用验证）。本次在基线上叠加 `app/tests/test_competitive_features.py` 的两个验收改动：明确模拟缺少网络隔离后端；补充记忆归档、恢复、版本和过期回归。

共享工作区同时有其他任务正在修改 3D 场景和发布流程。验收使用 Git 基线导出的独立快照，叠加本轮测试文件；未覆盖、纳入或提交并行任务的未完成改动。共享工作区的测试结果不能替代该快照的交付证据。

## 逐项证据

| 目标 | 实现入口 | 行为验证 |
|---|---|---|
| 执行级工具权限 | `app/core/policy.py`、`builtin_agent.py`、`mcp_client.py` | 工具禁用同时从供应商工具清单移除并在执行入口拒绝；MCP 发现与调用继承禁用策略；子任务继承策略并可收窄目录。对应 `test_disabled_tools_are_rejected_at_execution_boundary`、`test_mcp_dispatch_enforces_task_disabled_tools_at_execution_boundary` 和 `test_builtin_task_tool.py`。 |
| 可恢复文件检查点 | `app/core/checkpoints.py`、`pipeline.py`、`main.py` | 捕获文件原始内容，预览差异，恢复修改/新建/删除/重命名；拒绝覆盖人工修改；恢复前重查冲突；POSIX 目录句柄防止父目录被替换后越界写入。对应 `test_builtin_file_write_is_rewindable_end_to_end`、Git 检查点测试及父目录替换测试。 |
| 真实沙箱执行边界 | `app/core/builtin_agent.py`、`mcp_client.py`、`pipeline.py` | 文件工具和枚举遵守允许目录；shell/MCP 使用 Linux bubblewrap；缺少可验证后端时拒绝启动；未验证隔离能力的外部 CLI 拒绝执行。真实运行测试见 `app/tests/test_sandbox_runtime.py`。 |
| 子任务与长上下文 | `app/core/builtin_agent.py`、`tool_outputs.py`、`compaction.py` | 子任务仅接收显式背景；保留最近工具调用与结果配对，压缩早期轮次；大输出按 run/call 保存并可读取；重复压缩不丢引用；伪引用和复制其他调用的真实引用均不能绕过限制。对应 `test_builtin_task_tool.py` 与 direct-context/tool-output 系列回归。 |
| 长期记忆治理 | `app/core/project_memory.py`、`pipeline.py`、`main.py` | 自动生成内容处于 pending；批准后才注入；内容哈希与版本校验；归档停止注入；正确版本可恢复；过期条目拒绝恢复并停止注入。新增 `test_project_memory_archive_restore_checks_version_and_expiry` 实际执行该完整生命周期。 |
| AGENTS.md 信任范围 | `app/core/agent_context.py` | Git 仓库之外的父目录不被信任；无 Git 时只读取选定工作目录；拒绝不属于工作目录祖先的显式边界；指令内容脱敏。对应 agent-context 系列回归。 |

## 本次检查

- Windows / Python 3.8：`python -m unittest discover -s tests -q`，159 项，结果 `OK (skipped=5)`。跳过项涉及 Windows 下不可用的符号链接、POSIX 目录句柄及 Linux bubblewrap 测试类。
- Linux / WSL 临时 Alpine 3.22.1、Python 3.12.15、bubblewrap 0.12.0：`CODEBEE_REQUIRE_BWRAP=1 python3 -m unittest discover -s tests -p test_sandbox_runtime.py -v`，3 项全部通过，没有跳过。实际通过 MCP initialize/tools-call 启动 Python 服务，核对工作目录、工作区外文件不可见且原文件未改变；shell 与子进程读写边界、禁网 namespace 均验证通过。
- 同一 Linux 环境：`CODEBEE_REQUIRE_BWRAP=1 python3 -m unittest discover -s tests -v`，162 项全部通过，没有跳过；Windows 跳过的符号链接和 POSIX 目录句柄恢复测试也已实际执行。
- `python -m compileall -q core tests`：通过。
- `node --check ui/app.js`、`node --check ui/hive3d.js`：独立快照通过。
- 本轮差异 `git diff --check`：通过。
- Python 与通用代码独立审查：无阻塞问题；新测试兼容 Python 3.8 语法。采用 python-testing 的行为回归方式，source-command-python-review 要求的独立审查已完成。
- ruff、mypy、pyright、black、bandit 未安装，没有将这些检查标记为通过；未测量全仓覆盖率。

Linux 环境在任务专用 256 MiB tmpfs 内创建，不向 WSL 系统根目录安装软件。完成后卸载临时 proc/dev/tmpfs 挂载并删除目录。较早一次验证因 WSL 根分区空间不足中断，已清理；上面的通过结果来自空间问题解决后的完整重新执行。

共享工作区同时复跑全量测试：160 项，1 项失败、5 项跳过。失败为另一个尚未完成任务新增的 `test_3d_office_projects_live_step_info_onto_clickable_monitors`，当时缺少 `screenMeta`。本轮没有修改或提交该任务的测试/实现，因此不宣称共享工作区全量已通过。

## 使用边界

文件检查点只恢复已捕获文件。shell、MCP、外部服务写入等副作用不保证可回滚。

Linux bubblewrap 挂载允许工作目录及只读系统运行时目录，并按策略隔离网络；这不是只暴露业务文件的无运行时环境。Windows/macOS 的内置 shell/MCP 未接入等价 OS 隔离后端时会拒绝执行。外部 CLI 也必须有可验证的隔离能力，不能用完整访问模式绕过任务策略。

通过仅表示上述交付快照及已执行检查通过，不包含共享工作区中另一个任务仍在修改的 UI/发布文件。
