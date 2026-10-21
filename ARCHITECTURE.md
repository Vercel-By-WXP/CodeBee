# CodeBee 架构

## 整体流程

```text
用户目标
  -> Web UI / CLI 请求
  -> task_request.py、planner.py（拆解与任务规格）
  -> pipeline.py + router.py（编排与 CLI 路由）
  -> runner.py / builtin_agent.py（子进程或内置执行）
  -> 验证与质量闸
  -> 跨模型族评审
  -> 修复轮次（必要时重新路由）
  -> store.py（任务、步骤、日志、报告落盘）
  -> Web UI 结果与可回放时间线
```

## 核心模块

- **编排引擎**：`app/core/pipeline.py` 负责规划后的步骤执行、取消/暂停、预算与修复轮次；`jobs.py` 提供后台任务队列和 worker 生命周期。
- **CLI 适配器层**：`registry.py`、`catalog.py` 和 `manager.py` 保存 CLI 能力/绑定信息；`router.py` 根据角色、任务类型、历史统计和健康状态选择实现者或评审者；`runner.py` 以参数列表启动外部进程并解析输出。
- **评审模块**：`pipeline.py` 的评审阶段调用 `quality_gate.py`、`evaluation.py` 和评审路由，规范化维度评分、共识与通过条件。人工评审流转由 `review_requests.py` 记录。
- **持久化层**：`store.py` 负责任务、运行、步骤、消息和报告；`paths.py` 集中管理数据目录；运行日志与产物放在每次 run 的目录中。JSON 状态（包括自动化、资产、presence、模板和项目权限登记）统一经 `atomic.py` 的唯一临时文件 + 原子替换写入，并在 POSIX 收紧为进程私有权限；评审历史由 `review_storage.py` 使用标准库 SQLite 保存，WAL sidecar 同样收紧权限。具体文件布局和迁移规则若需对外稳定化仍【需确认】。
- **Web UI**：`app/main.py` 使用 Python 标准库 `ThreadingHTTPServer` 提供 HTTP API 和静态资源，前端位于 `app/ui/`。默认绑定回环地址；远程访问必须显式配置。

## 关键数据流

1. HTTP 请求创建任务后，`store.create_task` 写入任务记录；后台 worker 创建 run 并把步骤写入运行目录。
2. 规划结果交给 `router.route_plan`，实现者、验证者和评审者的选择随 run 保存，便于审计和重放。
3. `runner.run_agent` 接收 argv、工作目录和受控环境，采集 stdout/stderr、退出码、模型/供应商信息，并把摘要回传 pipeline。
4. 验证和评审结果写回步骤与 run verdict；失败或低于阈值时，pipeline 生成修复提示并在配置的轮次内重新执行。
5. 终态报告、日志和产物由 `store.write_report` 及 run 目录持久化，Web UI 通过只读 API 展示。

## 扩展点

### 添加 CLI 适配器

CLI 元数据仍由 `catalog.py`/`registry.py` 管理，启动和能力检测由 `manager.py` 协调，执行兼容分支仍集中在 `runner.py`。已拆出的 `cli_commands.py`、`cli_parsers.py`、`cli_text.py` 分别处理命令发现、供应商输出解析和文本清洗；它们保留 `runner.py` 的旧入口以兼容现有调用。当前尚无覆盖所有供应商的统一运行时 adapter protocol：`adapter_scaffold.py` 生成的是接入起点和可发现的测试样例，不会自动替代 runner 分支。

可从仓库根目录运行 `python app/main.py scaffold-adapter --name "Example CLI" --executable example-cli` 生成文件，再按目标 CLI 补齐参数、输出和错误码处理。接入时保留现有 catalog ID、调用模板和结果字典形状；先增加 fake CLI 契约测试，再逐步把对应分支迁入统一 adapter protocol，避免要求用户同时升级所有已有 CLI 配置。

### 自定义路由策略

当前默认选择逻辑位于 `router.py`（`pick`、`pick_reviewer`、`route_plan`），会综合角色、任务类型、绑定、在线状态、历史统计和候选排除列表。`routing_strategy.py` 提供进程内显式注册表；嵌入式调用方可调用 `register()`/`set_default()`，或在启动时用 `CODEBEE_ROUTING_STRATEGY` 选择已注册名称。未知策略和策略异常回落到内置打分。任务数据不能提供 Python 导入路径。

## 运行边界

CodeBee 运行时坚持 Python 标准库；外部 CLI 是独立进程，凭据由各 CLI 或绑定配置提供。CodeBee 不应把密钥写入任务提示词、日志或报告，具体脱敏规则由 `redact.py`、`env_scrub.py` 和相关调用点共同维护。

## 确定性工具裁定

模型输出的工具调用只是一项提议，不能直接获得执行权。`app/core/action_policy.py`
维护内置工具的封闭能力目录，并在 `_exec_tool` 的第一步执行纯函数裁定：

```text
能力登记 → 任务白名单/禁用项 → 动态红线 → 运行时审批 → 工具实现
```

默认保持历史兼容行为；任务沙箱可以提供 `allowed_tools` 建立闭合白名单，提供
`require_approval=true` 与 `approved_tools` 开启黄灯工具的运行时批准。`fs_manage`
的 `delete` 动作始终是红线，即使任务已批准也不会执行。未知工具在进入文件、命令或
MCP 分发前直接拒绝。

MCP 的工具发现和执行也分开：只有发现结果中的工具名才允许调用；任务白名单存在时，
服务器配置本身不能扩大工具权限。`app/core/audit.py` 对新增审计记录写入
`prev_hash/hash/seq`，并提供 `verify(event, month)` 检查链是否被篡改。

工具裁定还保留一份短期运行态审批：审批必须绑定 `run_id + task`，默认十分钟过期，
只存进程内，不进入任务快照或配置文件。`POST /api/tool-approvals` 仅登记这类模型工具
审批；任务契约审批和命令授权仍由各自模块负责。所有建书、直发、批量和定时发布入口
都会经过任务契约审批门禁，`unattended` 只表示执行方式，不会替代审批。每次工具调用都会追加 `tool_decision`
哈希链事件，只保存参数/结果摘要哈希，不把文件内容、命令输出或凭据写入审计日志。
MCP 还必须命中本次运行的发现清单；未知工具即使配置了服务器也会拒绝。

文件写入经 `workspace_boundary.py` 原子替换，支持字节上限、内部目录只读和格式锁；
`zone_policy.py` 提供动态区域解析器，解析失败按红灯关闭。效能统计只能影响建议，不能
绕过黄灯审批或红线。
