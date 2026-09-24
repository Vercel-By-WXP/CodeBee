# CodeBee 开源借鉴矩阵

> 本文记录 2026-09-24 对公开仓库的检索结果和实际落地边界。只借鉴可验证的设计机制；没有复制第三方代码。许可证信息以各项目仓库根目录的 LICENSE/NOTICE 为准，升级依赖或复制代码前必须逐文件复核。

## 1. 检索范围与关键词

检索覆盖：`agent orchestration`、`multi-agent workflow`、`durable execution`、`workflow DAG`、`checkpoint resume replay`、`LLM evaluation`、`eval registry`、`prompt testing`、`LLM observability`、`OpenTelemetry`、`OpenInference`、`trace span`、`model routing`、`cost routing`、`fallback retry`、`circuit breaker`、`task queue`、`idempotency outbox`、`CAS content addressed storage`、`revision provenance`、`asset lineage`、`browser automation`、`BrowserGym`、`Playwright trace`、`publishing workflow`、`SSRF`、`security policy`、`secret scanning`、`supply chain attestation`、`MCP`、`A2A`、`agent sandbox`。

## 2. 项目与可借鉴机制

| 领域 | 项目（许可证） | 借鉴机制 | CodeBee 对应位置/结论 |
|---|---|---|---|
| 状态编排 | [LangGraph](https://github.com/langchain-ai/langgraph)（MIT） | checkpoint、interrupt/resume、状态图、replay | `core/store.py` 的 run/step 快照；追踪视图已落地，完整 checkpoint 仍为 P1 |
| 工作流 | [Microsoft Agent Framework](https://github.com/microsoft/agent-framework)（MIT） | workflow、middleware、human-in-loop | `core/pipeline.py`、钩子与人工确认；参考实现，不复制代码 |
| 多智能体 | [CrewAI](https://github.com/crewAIInc/crewAI)（MIT） | Flow、guardrail、事件回调 | `core/flows.py`、验收矩阵；补齐阈值门为 P1 |
| DAG | [Haystack](https://github.com/deepset-ai/haystack)（Apache-2.0） | 可序列化 DAG、breakpoint、tracing | 任务流程摘要与运行步骤；可序列化流程编辑为 P2 |
| 类型契约 | [PydanticAI](https://github.com/pydantic/pydantic-ai)（MIT） | 结构化输出、工具校验、重试、durable activity | `core/runner.py`/`core/error_codes.py`；统一错误签名为 P0 |
| 事件回放 | [OpenHands](https://github.com/All-Hands-AI/OpenHands)（MIT） | 事件流、replay、sandbox | `core/dispatch_log.py`；本轮新增 trace/event 关联 |
| 工具边界 | [smolagents](https://github.com/huggingface/smolagents)（Apache-2.0） | 统一 Tool schema、sandbox/权限边界 | `core/capability.py`、发布/文件守卫；继续加强外部连接策略 |
| Prompt 实验 | [DSPy](https://github.com/stanfordnlp/dspy)（MIT） | Signature/Module、可重复优化实验 | `core/evaluation.py`；P2 引入可复跑实验注册 |
| 工作台 | [Dify](https://github.com/langgenius/dify)（前后端分层许可证，需逐目录核对） | workflow/provider registry/版本发布 | `core/registry.py`、模型目录；仅借架构 |
| 评测注册 | [OpenAI Evals](https://github.com/openai/evals)（MIT） | eval registry、grader、可复跑结果 | `docs/execution-standard.md`；P1 增加 case/seed/阈值字段 |
| Prompt 测试 | [Promptfoo](https://github.com/promptfoo/promptfoo)（MIT） | YAML/JSON matrix、assertion、provider matrix、red team | 供应商/模型评测；P1 接入 matrix，不引入运行时依赖 |
| LLM 指标 | [DeepEval](https://github.com/confident-ai/deepeval)（Apache-2.0） | metric contract、threshold gate、trace-to-test | `core/acceptance.py`；P0 保证 measured 不冒充 passed |
| RAG 质量 | [Ragas](https://github.com/explodinggradients/ragas)（Apache-2.0） | faithfulness、context precision/recall | 知识库任务的质量维度；P2 |
| Trace 反馈 | [TruLens](https://github.com/truera/trulens)（MIT） | 逐 trace feedback、groundedness/consistency | 本轮 trace 读模型为基础，反馈指标 P1 |
| ML lineage | [MLflow](https://github.com/mlflow/mlflow)（Apache-2.0） | run/dataset/model lineage | `core/revisions.py`、`core/assets.py`；P1 扩展 source/provenance |
| 评测执行 | [Inspect AI](https://github.com/UKGovernmentBEIS/inspect_ai)（MIT） | Task/Solver/Scorer、sandbox、并行样本 | 评测标准三层模型；P2 |
| Durable workflow | [Temporal](https://github.com/temporalio/temporal)（MIT） | event history、replay、Activity retry/heartbeat/timeout/signal | `core/jobs.py`、操作台账；P0 统一 retry/unknown 语义 |
| 状态机/队列 | [Prefect](https://github.com/PrefectHQ/prefect)（Apache-2.0） | state machine、cache key、backoff、并发限制 | `core/repeat_guard.py`、运行状态；P1 |
| 资产血缘 | [Dagster](https://github.com/dagster-io/dagster)（Apache-2.0） | asset lineage、materialization、sensor | `core/assets.py`；P1 |
| Job queue | [Celery](https://github.com/celery/celery)（BSD-3-Clause） / [Dramatiq](https://github.com/Bogdanp/dramatiq)（MIT） | ack/requeue、visibility timeout、retry、dead letter | 当前本地进程执行；P2 再评估外部 broker |
| 轻量 DAG | [Dagu](https://github.com/dagu-org/dagu)（AGPL-3.0） | 本地 YAML DAG | 仅借可视化思路，避免引入 AGPL 代码 |
| CAS/修订 | [DVC](https://github.com/iterative/dvc)（Apache-2.0）、[lakeFS](https://github.com/treeverse/lakeFS)（Apache-2.0） | CAS、stage graph、branch/commit/merge | `core/revisions.py`、内容指纹；P1 |
| Provenance | [Pachyderm](https://github.com/pachyderm/pachyderm)（Apache-2.0） | commit DAG、provenance | 成品/发布闭包；P1 |
| Artifact | [ORAS](https://github.com/oras-project/oras-go)（Apache-2.0）、[OCI Distribution](https://github.com/opencontainers/distribution-spec)（Apache-2.0） | digest、manifest、registry/blob | 资产引用已有内容哈希；OCI 适配 P2 |
| 供应链 | [in-toto](https://github.com/in-toto/in-toto)（Apache-2.0）、[Sigstore/cosign](https://github.com/sigstore/cosign)（Apache-2.0） | step attestation、材料/产物关联、digest 签名 | 发布证据链 P1；不复制签名服务代码 |
| 模型网关 | [LiteLLM](https://github.com/BerriAI/litellm)（MIT，enterprise 目录另审） | provider adapter、fallback/retry、预算、callbacks | `core/modelhub.py`、`core/router.py`；P0 统一 attempt 账本 |
| 路由 | [Portkey Gateway](https://github.com/Portkey-AI/gateway)（MIT） / [RouteLLM](https://github.com/lm-sys/RouteLLM)（Apache-2.0） | 负载均衡、fallback、熔断、质量/成本路由 | `core/router.py`；P1 接入 provider health/cost 信号 |
| 本地模型 | [vLLM](https://github.com/vllm-project/vllm)（Apache-2.0） / [Ollama](https://github.com/ollama/ollama)（MIT） | 吞吐、prefix cache、版本/健康 API | 仅参考运行指标与健康探针 |
| 浏览器证据 | [Playwright](https://github.com/microsoft/playwright)（Apache-2.0） / [BrowserGym](https://github.com/ServiceNow/BrowserGym)（Apache-2.0） | BrowserContext 隔离、auto-wait、trace、轨迹 benchmark | `core/publish` 与浏览器链；P1 分层证据 |
| 浏览器工具 | [playwright-mcp](https://github.com/microsoft/playwright-mcp)（Apache-2.0） / [Stagehand](https://github.com/browserbase/stagehand)（MIT） | 结构化 observe/act、动作缓存 | 仅借接口契约，避免引入浏览器运行时 |
| 网络可靠性 | [httpx](https://github.com/encode/httpx)（BSD-3-Clause）、[urllib3](https://github.com/urllib3/urllib3)（MIT） | timeout、连接池、代理、retry/backoff | P0 统一 request policy，含 IPv6/redirect 重校验 |
| 策略安全 | [OPA](https://github.com/open-policy-agent/opa)（Apache-2.0）、[Bandit](https://github.com/PyCQA/bandit)（Apache-2.0） | policy-as-code、AST 安全扫描 | 发布/外部操作守卫；P1 |
| 依赖/密钥 | [pip-audit](https://github.com/pypa/pip-audit)（Apache-2.0）、[gitleaks](https://github.com/gitleaks/gitleaks)（MIT） | 漏洞与 secret scanning | 提交流程 P1；不把扫描结果写入运行正文 |
| 可观测性 | [OpenTelemetry Python](https://github.com/open-telemetry/opentelemetry-python)（Apache-2.0）、[OpenInference](https://github.com/Arize-ai/openinference)（Apache-2.0） | span/metric/log/context、LLM span 语义 | 本轮新增无依赖 trace schema；未来可导出 OTel |
| LLM 观测 | [Langfuse](https://github.com/langfuse/langfuse)（核心 MIT，ee 目录另审） / [OpenLLMetry](https://github.com/traceloop/openllmetry)（Apache-2.0） | trace、prompt version、score、instrumentation | P1；本地模式先脱敏 |
| Agent 协议 | [MCP Python SDK](https://github.com/modelcontextprotocol/python-sdk)（MIT）、[A2A](https://github.com/a2aproject/A2A)（Apache-2.0） | tool/resource/prompt 契约、agent card、task/artifact 状态 | P2 外部 agent 能力接入 |
| 角色协作 | [AG2](https://github.com/ag2ai/ag2)（Apache-2.0）、[CAMEL](https://github.com/camel-ai/camel)（Apache-2.0）、[TaskWeaver](https://github.com/microsoft/TaskWeaver)（MIT） | conversation/graph、消息协议、插件 schema、代码优先 planner | 借契约与状态传递，不替换现有执行器 |

## 3. 优先级与落地状态

| 优先级 | 事项 | 来源机制 | 状态 |
|---|---|---|---|
| P0 | run → step → dispatch event 统一 trace/span/event_id，并提供只读回放 API | OpenTelemetry/OpenInference、OpenHands、Langfuse | **本次已落地**：`app/core/tracing.py`、`core/dispatch_log.py`、`/api/runs/{id}/trace` |
| P0 | provider attempt 独立 usage 账本、统一 error fingerprint/retry/circuit breaker | LiteLLM、Temporal、Prefect | 待落地；现有最终汇总不能冒充逐尝试成本 |
| P0 | 外部操作区分本地确认与远端确认，unknown 不自动重放 | Temporal activity/outbox、Debezium outbox | 已有 `pending/confirmed/failed/unknown`；`remote_confirmed` 语义待补 |
| P0 | SSRF 统一 timeout、禁代理、DNS/IP denylist、IPv6 与 redirect 重校验 | httpx、urllib3、OPA | 部分已有，统一 request policy 待补 |
| P1 | acceptance threshold gate：measured/not_evaluated 与 passed 分离 | DeepEval、Promptfoo、Inspect AI | 文档已规定；代码阈值收口待补 |
| P1 | 发布旁路按“连接→能力→提交→回读”分层，每层独立证据 | Playwright、BrowserGym、Promptfoo | 现有发布闸分散，待统一矩阵 |
| P1 | 资产 digest/重复冲突扫描、source commit/worktree/provenance 输出 | DVC、lakeFS、in-toto、MLflow | 内容指纹已有，发布闭包待增强 |
| P1 | TTFT/tokens_per_sec 与 provider health 进入路由/验收 | OpenInference、OpenLLMetry、RouteLLM | 待落地；样本不足时保持 `not_evaluated` |
| P2 | MCP/A2A 外部 agent 能力契约与 OCI artifact 传递 | MCP、A2A、ORAS | 方向储备，需先稳定 P0/P1 |
| P2 | 外部 broker/durable workflow、可视化 DAG、浏览器轨迹基准 | Temporal、Celery、Dagu、BrowserGym | 不在当前轻量本地执行范围内 |

## 4. 许可证与复制边界

1. 本轮只吸收公开设计，不复制第三方实现；提交中没有新增第三方代码或依赖。
2. MIT/Apache/BSD 也需要保留版权与 NOTICE；AGPL 项目（例如 Dagu）只做机制参考，不能未经评估嵌入服务端代码。
3. AutoGen 主仓、Dify 的 enterprise 目录、Langfuse 的 ee 目录、LiteLLM 的 enterprise/部分文件可能采用不同条款，不能按仓库首页标签推断所有文件许可。
4. 若未来引入 SDK，先锁定版本、记录 SPDX/NOTICE、执行依赖扫描和许可证审计，再进入运行时依赖。

## 5. 本次修改标记

- **[新增]** 建立开源项目、关键词、许可证和 CodeBee 模块映射表。
- **[新增]** 明确 P0/P1/P2 的落地状态、未采用原因和后续入口。
- **[新增]** 落地无依赖的 run/step/dispatch trace 读模型，敏感正文不进入追踪结果。
- **[新增]** 调度事件加入 `event_id` 与可选 trace/span 关联，旧调用保持兼容。
- **[新增]** 提供 `/api/runs/{id}/trace` 只读接口，供 UI、诊断包和后续 OTel exporter 使用。
