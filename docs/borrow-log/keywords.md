# 关键词总库（全类型覆盖版 · 130 组 + 雷达源）

> 夜间自动化检索的完整词库。规则：每晚跑 **常驻组全部 + 轮换池按星期选 1 批 + 雷达源全部**；
> 双轮排序（sort=stars 与 sort=updated 或 created:>2026-03-01 新锐轮）；核心组翻页 page=2
>（满页才翻，最多 page=3）；GitHub API 未认证限流 10 次/分，请求间 sleep 6-8 秒；
> 结果高度重合即跳余页省配额。新增关键词直接编辑本文件并在行尾标注（谁/何时/为何）。

## A. 常驻组（每晚全跑，41 组）

### A1 核心编排（8 组，翻页）
- q=multi-agent+orchestration
- q=agent+orchestration
- q=meta-harness+OR+agent+harness
- q=claude+code+orchestrator+OR+codex+orchestrator
- q=agent+swarm+OR+crew+agents
- q=coding+agent+supervisor+OR+agent+manager
- q=claude+skills+OR+agent+skills+marketplace
- q=多智能体+编排

### A2 扩展形态（7 组）
- q=autonomous+agent+framework
- q=agent+workflow+engine+OR+agent+pipeline
- q=agentic+coding
- q=agent+memory+OR+agent+evals
- q=context+engineering
- q=ai+employee+OR+digital+worker
- q=LLM+workflow+builder

### A3 token 节约（4 组，每轮必查——帮 CodeBee 用户省 token）
- q=prompt+caching+OR+llm+semantic+cache
- q=token+optimization+OR+token+efficient
- q=context+window+management
- q=cheap+model+routing+OR+model+cascade

### A4 新 CLI（3 组）
- q=ai+coding+agent+cli
- q=terminal+coding+agent
- q=headless+agent+cli

### A5 舰队/形态（4 组）
- q=agent+team+OR+agent+fleet（可加 stars:>500）
- q=computer+use+OR+computer+control+agent+cli
- q=spec-driven+development+agent
- q=ai+agent+sandbox+runtime

### A6 评测/观测/协作（6 组）
- q=ai+agent+evals+OR+agent+benchmark
- q=agent+observability+OR+agent+tracing
- q=mcp+orchestration+OR+mcp+manager
- q=agent+handoff+OR+multi-agent+collaboration
- q=model+router+OR+llm+router
- q=智能体+编排

### A7 写作场景（8 组，CodeBee 核心场景——2026-09-18 深夜新增，此前从未覆盖）
- q=novel+writing+ai+OR+story+generation+ai
- q=creative+writing+agent
- q=long+form+writing+ai+OR+book+writing+agent
- q=网文+AI+写作+OR+小说+生成
- q=article+writing+ai+OR+blog+writing+agent（自媒体文章——对应我们的自媒体文章流程）
- q=speech+writing+ai+OR+presentation+script+generator（演讲稿/口播脚本）
- q=translation+agent+OR+ai+translation+workflow（翻译流程）
- q=story+consistency+check+OR+long+document+consistency（长文一致性——连载圣经/全局评审）

### A9 产品配套场景（6 组，覆盖 CodeBee 自有能力面——2026-09-18 深夜新增）
- q=agent+scheduler+OR+cron+ai+tasks（定时自动化）
- q=self+update+cli+OR+auto+update+mechanism（自更新）
- q=rate+limit+backoff+llm+OR+429+retry+agent（限流退避）
- q=onboarding+wizard+cli+OR+first+run+experience（首启向导）
- q=webnovel+author+tools+OR+小说+作者+工具（平台作者工具——番茄/七猫建书）
- q=ai+cover+image+generator+OR+book+cover+generation（封面图）

### A8 prompt/网关/质量（5 组，2026-09-18 深夜新增）
- q=prompt+management+platform+OR+prompt+registry
- q=prompt+versioning+OR+prompt+ab+testing
- q=one-api+alternative+OR+llm+api+gateway
- q=hallucination+detection+OR+llm+output+validation
- q=structured+output+agent+OR+schema+guard+llm

### A10 全类型写作/对话/代码（10 组，2026-09-19 新增——覆盖全部预置任务类型）
- q=ai+email+writing+OR+business+email+generator（商务邮件）
- q=weekly+report+ai+OR+work+report+generator（工作汇报/月报）
- q=short+video+script+ai+OR+tiktok+script+generator（短视频脚本）
- q=ai+translation+quality+OR+translation+agent（翻译质量）
- q=chatbot+memory+OR+conversational+agent+memory（对话记忆/追问芯片）
- q=ai+code+generation+OR+code+completion+agent（代码生成/补全）
- q=ai+code+refactoring+OR+code+improvement+agent（代码重构/优化）
- q=ai+documentation+generator+OR+doc+writing+agent（文档生成）
- q=ai+presentation+slides+generator（演示文稿/汇报 PPT）
- q=ai+search+agent+OR+deep+research+agent（调研报告/deep research）

## B. 轮换池（56 组，按星期选批：周日=批1 … 周六=批7）

### 批1（周日）代码质量与评审
- q=code+review+agent+OR+ai+code+reviewer
- q=pr+review+bot+github
- q=github+action+ai+review
- q=bug+detection+agent
- q=vulnerability+scanner+agent
- q=test+generation+agent+OR+ai+testing+agent
- q=regression+test+generation+ai
- q=refactor+agent+OR+tech+debt+agent

### 批2（周一）学习记忆与自我改进
- q=self+improving+agent+OR+agent+reflexion
- q=agent+episodic+memory
- q=project+memory+coding+agent
- q=knowledge+graph+agent
- q=agent+learning+from+feedback
- q=experience+reuse+agent
- q=agent+self+correction
- q=skill+library+agent

### 批3（周二）计划/spec/长任务
- q=long+running+agent+OR+persistent+planning+agent（可 stars:>200）
- q=spec+driven+development
- q=plan+and+execute+agent
- q=task+decomposition+agent
- q=milestone+tracking+agent
- q=project+planning+ai+agent
- q=autonomous+long+horizon+agent
- q=worktree+parallel+agent

### 批4（周三）治理/安全/人机协同
- q=human+in+the+loop+ai+agent
- q=agent+approval+workflow
- q=agent+governance
- q=agent+guardrails
- q=agent+permission+policy
- q=agent+audit+trail
- q=agent+kill+switch
- q=agent+risk+control

### 批5（周四）检索/知识/浏览器
- q=agent+rag
- q=deep+research+agent
- q=browser+use+agent+OR+browser+automation+ai
- q=web+scraping+agent
- q=search+agent+OR+retrieval+agent
- q=document+understanding+agent
- q=data+extraction+agent
- q=competitive+intelligence+agent

### 批6（周五）框架/平台/SDK 生态
- q=langgraph+platform+OR+langgraph+deploy
- q=crewai+studio+OR+crewai+platform
- q=autogen+platform+OR+autogen+studio
- q=openai+agents+sdk
- q=google+adk+agent
- q=mastra+agent
- q=pydanticai+agent
- q=semantic+kernel+agent

### 批7（周六）中文/网关/本地/办公
- q=数字员工+OR+大模型+编排
- q=one-api+alternative
- q=模型中转+OR+api+网关+大模型
- q=ollama+orchestrator
- q=local+llm+agent
- q=self+hosted+agent+platform
- q=rpa+ai+agent
- q=小说生成+ai+OR+ai+写作+平台

## C. 雷达源（每轮全过）

- awesome 清单：awesome-agent-orchestration、awesome-claude-skills、awesome-harness-engineering、awesome-mcp、awesome-cli-coding-agents、awesome-ai-agents、awesome-llm-apps、awesome-claude-code（54k★）、VoltAgent/awesome-agent-skills（34.6k★ 1000+ skills）、buildwithclaude（3.5k★ 枢纽）
- GitHub Trending（weekly，ai/agent 类）
- topic 页：multi-agent-orchestration、ai-agents、claude-code、agent-framework、claude-skills、llm-agents、ai-coding-assistant、mcp
- 发行渠道：npm search（agent orchestrator / claude code）、pypi（agent orchestrator）各扫一页
- 框架周边搜：q=langgraph+platform / crewai+studio / autogen+studio 类；竞品名周边：q=orca+alternative、q=claude+flow+OR+ruflo 生态
- 自家 CLI 名周边搜：q=codex+manager、q=claude+code+manager+OR+wrapper、q=opencode+suite、q=kimi+cli、q=grok+cli
