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
- **持久化层**：`store.py` 负责任务、运行、步骤、消息和报告；`paths.py` 集中管理数据目录；运行日志与产物放在每次 run 的目录中。具体文件布局和迁移规则若需对外稳定化仍【需确认】。
- **Web UI**：`app/main.py` 使用 Python 标准库 `ThreadingHTTPServer` 提供 HTTP API 和静态资源，前端位于 `app/ui/`。默认绑定回环地址；远程访问必须显式配置。

## 关键数据流

1. HTTP 请求创建任务后，`store.create_task` 写入任务记录；后台 worker 创建 run 并把步骤写入运行目录。
2. 规划结果交给 `router.route_plan`，实现者、验证者和评审者的选择随 run 保存，便于审计和重放。
3. `runner.run_agent` 接收 argv、工作目录和受控环境，采集 stdout/stderr、退出码、模型/供应商信息，并把摘要回传 pipeline。
4. 验证和评审结果写回步骤与 run verdict；失败或低于阈值时，pipeline 生成修复提示并在配置的轮次内重新执行。
5. 终态报告、日志和产物由 `store.write_report` 及 run 目录持久化，Web UI 通过只读 API 展示。

## 扩展点

### 添加 CLI 适配器

在 `app/core/registry.py`/`catalog.py` 的 CLI 描述表中登记可执行文件、能力和模型字段；在 `runner.py` 增加必要的 argv/输出解析分支；通过 `manager.py` 接入检测、绑定和健康状态，并为成功、超时、鉴权失败和降级补充 unittest。稳定的适配器接口名称目前【需确认】，应以现有注册项和测试约定为准。

### 自定义路由策略

当前默认选择逻辑位于 `router.py`（`pick`、`pick_reviewer`、`route_plan`），会综合角色、任务类型、绑定、在线状态、历史统计和候选排除列表。要替换策略，建议实现 `docs/routing_strategy.md` 定义的接口，并在配置层选择策略；配置键名与运行时加载入口尚未固定，标记为【需确认】。

## 运行边界

CodeBee 运行时坚持 Python 标准库；外部 CLI 是独立进程，凭据由各 CLI 或绑定配置提供。CodeBee 不应把密钥写入任务提示词、日志或报告，具体脱敏规则由 `redact.py`、`env_scrub.py` 和相关调用点共同维护。
