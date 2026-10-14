# Claude 超时、OpenCode 换将失败的诊断与修复

## 真实现场

- 对应运行：`r-20261014-143908-6431`，任务 `t-20260930-123242-6471`；运行数据位于 `C:/Users/HP/AppData/Roaming/CodeBee/runs/`，此次只读，未改写历史状态。
- 运行记录：14:39:09 开始，15:32:49 失败（UTC+8）。用户通知中的“4/4”是四条执行记录，不是已完成计划第四项：实际只有 plan、Claude implement-1/4、OpenCode implement-1/4、OpenCode implement-2/4。
- Claude 第一步运行 2761.3 秒，状态 timeout、退出码 15；审计日志有两次模型调用（14:40:55、15:03:55），最后停在 npm/pypi 发行渠道扫描的工具心跳。不能把 53 分钟全部归给 OpenCode，也不能断言是 API 配额故障。
- OpenCode 接手第一项运行 25.8 秒、退出码 0，但日志明确记录读取 `E:/AGENTS.md` 被 `external_directory` 自动拒绝。已有配置只放行 edit/bash/webfetch，漏掉独立的目录外权限。
- OpenCode 第二项运行 322.1 秒、退出码 1。前三次 Glob 均报 `Error: ripgrep execution failed`，随后模型已改用 bash 并继续阅读源码。最后停在下一轮模型调用；OpenCode 原生日志和只读会话数据库均未留下最终异常，Application 事件窗口也未找到对应崩溃证据。因此 **ripgrep 是已观察到的工具故障，不足以证明它导致最终退出码 1**。本机随 OpenCode 安装的 rg.exe 目前直接扫描同一路径成功，不能据此认定需要重装。
- 步骤期间主仓库 HEAD 曾被其他会话更新，检查点提示无法安全对账；该提示出现于进程结束后的检查点处理，不能反推它导致 CLI 退出。

## 修改

- `app/core/runner.py`：stdout/stderr 独立提取诊断，保留错误串开头和真实退出状态；timeout/cancelled 标志优先于输出关键词，避免被供应商错误分类覆盖。探针仅识别结构化错误事件，忽略思考、工具结果、截断 JSON、命令回显及带行号的源码（真实输出有 `429: url = ...`，不能当 HTTP 429）。日志任务启动转为可读描述，工具心跳不再原样污染摘要。
- `app/core/manager.py`：沿用已有无人值守授权策略，补充 OpenCode `external_directory=allow`；JSON 和 JSONC 同步路径均覆盖。
- `tests/test_cli_failure_diagnostics.py`：新增 9 项回归，覆盖长 stdout 遮住 stderr、状态优先级、UUID/思考/源码行号误判、真实错误提取及可读上下文。
- `tests/test_launch.py`：JSON/JSONC 配置测试增加目录外权限断言。

## 验证与边界

9 个相关测试模块共 **107 项通过**：`test_cli_failure_diagnostics` 9、`test_implement_switch` 16、`test_launch` 33、`test_error_codes` 22、`test_opencode_banner_retry` 7、`test_runtime_config_sync` 6、`test_guard_tmpdata` 6、`test_log_pretty` 6、`test_stream_log` 2。执行方式为 `python -m unittest discover -s tests -p <模块名>.py`。修改的 Python 文件 py_compile 通过，所拥有文件的 `git diff --check` 通过；逐项审查本次差异。

只读重放原始日志：Claude 诊断保留“超时”及“npm/pypi 发行渠道扫描”，OpenCode 诊断提取 `Error: ripgrep execution failed`，不会再用 CSS 文件列表或源码第 429 行冒充错误。

本次在主 checkout 的 main 修改；保留其他会话的 `app/core/publish/manager.py` 修改及原有未跟踪报告、stackdump。初次修复完成时尚未提交或推送；用户后续明确授权提交并推送，本次提交仅包含上述两个实现文件、两个测试文件及本记录，推送前先 fetch 核对远端。没有发布、重启共享服务或重新执行原调研任务；修复尚需加载新代码的服务使用。原任务未完成，最终无错误文本退出的原因仍待新一次运行提供证据。

经验：运行记录序号与计划序号要分开核对；结构化状态是超时/取消的依据；stdout 的工具输出和源码行号不能当供应商错误；无头 CLI 的独立权限域必须逐项检查。
