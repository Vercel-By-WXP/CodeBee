# 关键词总库（全类型覆盖版 · 147 组 + 雷达源）

> 夜间自动化检索的完整词库。规则：每轮跑 **常驻组全部 + 轮换池按当前小时数对 7 取模选 1 批（余 0=批7）+ 雷达源全部**；
> 双轮排序（sort=stars 与 sort=updated 或 created:>2026-03-01 新锐轮）；核心组翻页 page=2
>（满页才翻，最多 page=3）；GitHub API 限流：**认证调用（gh api）搜索接口是 30 次/分**——
> 2026-09-22 实测无间隔连打必然一批 403（本轮 85 组首轮撞限、补跑才拿到），
> **一律间隔 sleep 4 秒起**；未认证脚本调用仍是 10 次/分、间隔 6-8 秒；
> 2026-09-22 补：**WebSearch 通道同样限流——并行多查必 429，串行单发可过**；被限即顺延下一词组、勿原地重试；
> gh api 不可用时（如本机 Bash 被劫持/WebFetch 域名校验全拦）降级走 WebSearch 串行，翻页与双轮排序纪律不适用、报告如实记录；
> 2026-10-04 补：**单通道「无新竞品」结论性判定须每 3-4 班配一次 WebSearch 串行交叉验证**——
> gh api 连续八班报「写作域无新竞品」后，WebSearch 仍捞出 webnovel-writer（7.3k★）/ainovel-cli（2.1k★）两件大仓（20 时复核班实证），防单通道盲区（谁/何时/为何：20 时独立复核班）；
> 2026-10-05 补：**WebSearch 捞出的仓/平台 claim 一律 repos 端点二次实证后才能定性入库**——
> 新闻面 ≠ 开源仓在（04 时班实证：NVIDIA「Open Agent Safety Platform」新闻真但同名仓 404、
> covenant 被 WebSearch 放大实仓仅 5★、WSO2 Agent Manager 仓名未勘定）（谁/何时/为何：04 时班首例）。
> 2026-10-05 再补：**新面孔入库时顺带 gh api orgs/<org>/repos 扫其组织矩阵**（repos 端点零搜索配额）——
> beads 27.6k★ 一年仓被 A2「agent+memory」头部恒星压出 per_page=5 剪切线、靠 gascity 新锐轮顺组织
> 才掘出（谁/何时/为何：15 时班首例，头部查询剪不动中腰部巨仓）。
> 2026-10-06 补：**复查遇属主失配/404 时用 `q=<name>+in:name` 搜索勘定真属主**——
> 00 时班 11 件失配一批勘清（ponytail→DietrichGebert/OpenSpec→Fission-AI/claude-mem→thedotmack
> 等三条全名新补），盲猜 owner 逐个试错浪费配额（谁/何时/为何：00 时班首例批量落地）。
> 中文/多语查询过 gh api 前必须 URL 编码（urllib.parse.quote）——00 时班 11 条中文查询未编码
> 全数 HTTP 400、补跑才拿到（谁/何时/为何：00 时班）。
> 2026-10-07 补：**小时不可测时（shell 劫持+时间站域名全拦）批次改按「当日未跑批」覆盖**——
> 从当日已跑记录反推剩余批，优先轮转位最近的未跑批、候选批全过，报告如实记小时缺失不伪造时刻
> （谁/何时/为何：10-07 午后班 Bash 三测全废、worldtimeapi/timeanddate 双拦，当日已跑
> B3/B6/B2/B5/B6 后改覆盖 B1+B7+B4）。
> 2026-10-07 再补：**WebSearch 为唯一通道时按族合并查询（A1-A13 每族 1-3 发+B 候选批+C 可用面），
> 产出对 borrow-log 全历史 Grep 过筛防重复入库**——逐词单查必超配额，合并查询词级粒度损失如实记
> （谁/何时/为何：10-07 午后班串行 20 发实证可行、七件复见全数过筛零重复）。
> 结果高度重合即跳余页省配额。新增关键词直接编辑本文件并在行尾标注（谁/何时/为何）。

## A. 常驻组（每轮全跑，78 组）

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
- q=ai+employee+OR+digital+worker+OR+digital+employee（2026-10-05 21 时班补：StaffDeck 1,967★〔OpenBMB 数字员工平台〕系 WebSearch 交叉验证捞出、147 组词各班从未命中——GitHub 搜索按词 AND 匹配，「digital employee」与「digital worker」系不同词形，谁/何时/为何：21 时班首例）
- q=LLM+workflow+builder

### A3 token 节约（4 组，每轮必查——帮 CodeBee 用户省 token）
- q=prompt+caching+OR+llm+semantic+cache
- q=token+optimization+OR+token+efficient
- q=context+window+management
- q=cheap+model+routing+OR+model+cascade
- q=token+efficient+agent+OR+openclacky 生态（2026-10-09 16 时班补：clacky-ai/openclacky 1,204★ 系 trendshift 根页捞出——「The most Token-efficient open-source AI Agent」描述本身含 token-efficient 词形但主扫 A3 组 per_page=5 被头部剪切未现，系**剪切线盲区**非词形盲区；其 Insert-then-Compress 保前缀缓存+16 工具元工具收敛+闲时压缩预热三机制系 A3 高价值对标件，需盯增量，谁/何时/为何：16 时班首见）

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

### A11 项目记忆与档案（5 组，2026-09-20 新增——任务档案/结构化记忆方向对标 agentmemory/OpenSpec）
- q=persistent+memory+coding+agent+OR+agent+facts+store（持久事实记忆）
- q=ai+decision+log+OR+architecture+decision+records（决策记录 ADR）
- q=findings+file+OR+discovery+log+agent（发现记录 PWF）
- q=spec+archive+OR+specification+versioning（spec 归档/版本化）
- q=project+constitution+OR+coding+standards+auto（项目宪章/规范注入）

### A12 发布与平台（5 组，2026-09-20 新增——发布上架全流程对标）
- q=web+novel+publish+automation（网文自动发布）
- q=story+to+video+pipeline+OR+novel+adaptation（小说→短剧/漫画改编）
- q=multi+platform+content+publishing+agent（多平台内容分发）
- q=reader+feedback+analysis+ai（读者反馈分析）
- q=chapter+hook+optimization+OR+serial+pacing（章节钩子/节奏优化）

### A13 多 CLI 面板 / 用量指标 / 运行守卫（7 组，2026-09-22 新增——本轮实测命中一整簇同形态竞品与「不信任自报」护栏族）
- q=agent+dashboard+OR+multi+agent+cli+panel（多 CLI 统一面板/看板——OmniTerm/adhdev/CPA-Manager-Plus 一簇）
- q=claude+code+web+ui+OR+codex+web+terminal（Web 形态的多 CLI 界面）
- q=llm+usage+metrics+OR+token+throughput+dashboard（用量/速度/缓存命中指标——opencode-metrics 方向）
- q=agent+credential+vault+OR+secret+management+agent（凭据保险库——sandbase-harness 方向）
- q=agent+out+of+scope+edit+OR+scope+creep+agent+OR+half+finished+agent（越范围/半成品守卫——agent-delegate/scopebond 族）
- q=proof+gated+completion+OR+agent+self+report+trust（证据门禁/不信任自报——2026 下半年共识信号）
- q=defect+retrospective+ai+OR+bug+postmortem+agent（缺陷复盘——禅道生态 test-defect-retrospective 方向）

## B. 轮换池（69 组，按当前小时选批：小时 % 7，余 1=批1 … 余 6=批6，余 0=批7；如 08 点→批1、10 点→批3）

### 批1：代码质量与评审
- q=code+review+agent+OR+ai+code+reviewer
- q=pr+review+bot+github
- q=github+action+ai+review
- q=bug+detection+agent
- q=vulnerability+scanner+agent
- q=test+generation+agent+OR+ai+testing+agent
- q=regression+test+generation+ai
- q=refactor+agent+OR+tech+debt+agent
- q=secure+code+review+agent+OR+security+review+bot（2026-09-20 补：代码安全评审）
- q=api+test+generation+agent+OR+integration+test+agent（2026-09-20 补：接口/集成测试）
- q=ast+based+code+editing+agent+OR+symbol+level+code+edit（2026-09-22 补：Empryo「编辑符号而非字符串」AST 手术方向）
- q=open-code-review+alternative+OR+codeai+review 生态（2026-10-09 15 时班补：alibaba/open-code-review 44,721★ 系 WebSearch 交叉验证复认〔17 文件在录非首见〕、主扫 B1 词「code+review+agent」两班未命中它——「open-code-review」连字符词形与「code review agent」词形正交系词形盲区第 4 例〔StaffDeck/magic/openworker 同型〕，补竞品名周边词防同类新竞品漏捞，谁/何时/为何：15 时班批1）

### 批2：学习记忆与自我改进
- q=self+improving+agent+OR+agent+reflexion
- q=agentic+context+engineering+OR+evolving+playbook（2026-10-09 16 时班补：ace-agent/ace 1,352★ 系 WebSearch 批2 域交叉验证捞出+repos 实证〔08-24 后停更，evolving playbooks 自我改进〕、主扫 B2 组 10 词全量未命中——「agentic context engineering」与「self improving agent」词形正交系**词形盲区第 5 例**〔StaffDeck/magic/openworker/open-code-review 同型〕，谁/何时/为何：16 时班批2）
- q=agent+episodic+memory
- q=project+memory+coding+agent
- q=knowledge+graph+agent
- q=agent+learning+from+feedback
- q=experience+reuse+agent
- q=agent+self+correction
- q=skill+library+agent
- q=conversation+memory+compression+OR+memory+summarization+agent（2026-09-20 补：对话记忆压缩）
- q=agent+skill+learning+OR+automatic+skill+discovery（2026-09-20 补：技能自动沉淀）

### 批3：计划/spec/长任务
- q=long+running+agent+OR+persistent+planning+agent（可 stars:>200）
- q=spec+driven+development
- q=plan+and+execute+agent
- q=task+decomposition+agent
- q=milestone+tracking+agent
- q=project+planning+ai+agent
- q=autonomous+long+horizon+agent
- q=worktree+parallel+agent
- q=agent+checkpoint+resume+OR+workflow+recovery+agent（2026-09-20 补：长任务断点恢复）
- q=acceptance+criteria+agent+OR+requirements+validation+agent（2026-09-20 补：验收标准与需求核验）
- q=requirement+elicitation+agent+OR+spec+interview+ai（2026-09-22 补：Wiggum「AI 面试生成 spec」——需求拷问族第 4 验证）

### 批4：治理/安全/人机协同
- q=human+in+the+loop+ai+agent
- q=agent+approval+workflow
- q=agent+governance
- q=agent+guardrails
- q=agent+permission+policy
- q=agent+audit+trail
- q=agent+kill+switch
- q=agent+risk+control
- q=prompt+injection+defense+agent+OR+indirect+prompt+injection（2026-09-20 补：工具输入安全）
- q=agent+policy+evaluation+OR+guardrail+benchmark（2026-09-20 补：治理规则评测）

### 批5：检索/知识/浏览器
- q=agent+rag
- q=deep+research+agent
- q=browser+use+agent+OR+browser+automation+ai
- q=web+scraping+agent
- q=search+agent+OR+retrieval+agent
- q=document+understanding+agent
- q=data+extraction+agent
- q=competitive+intelligence+agent
- q=citation+verification+agent+OR+source+grounding+agent（2026-09-20 补：调研引用核验）
- q=knowledge+base+quality+OR+rag+evaluation+agent（2026-09-20 补：知识库质量）

### 批6：框架/平台/SDK 生态
- q=langgraph+platform+OR+langgraph+deploy
- q=crewai+studio+OR+crewai+platform
- q=autogen+platform+OR+autogen+studio
- q=openai+agents+sdk
- q=google+adk+agent
- q=mastra+agent
- q=pydanticai+agent
- q=semantic+kernel+agent
- q=agent+interoperability+protocol+OR+agent+to+agent+protocol（2026-09-20 补：跨代理协议）
- q=agent+framework+benchmark+OR+multi-agent+framework+comparison（2026-09-20 补：框架横评）

### 批7：中文/网关/本地/办公
- q=数字员工+OR+大模型+编排
- q=one-api+alternative
- q=模型中转+OR+api+网关+大模型
- q=ollama+orchestrator
- q=local+llm+agent
- q=self+hosted+agent+platform
- q=rpa+ai+agent
- q=小说生成+ai+OR+ai+写作+平台
- q=office+document+agent+OR+办公+智能体+工作流（2026-09-20 补：办公文档自动化）
- q=magicrew+OR+%E8%B6%85%E7%BA%A7%E9%BA%A6%E5%90%89 生态（2026-10-07 21 时班补：超级麦吉 Magicrew 系 WebSearch 交叉验证捞出、143 组词各班从未命中的词形盲区第 2 例〔StaffDeck 同款——专名/品牌词形与功能词形正交〕，in:name 勘定 dtyq/super-magic 96★ 系旧仓后 orgs/dtyq/repos 矩阵顺藤坐实正主=dtyq/magic 5,043★ 企业级 all-in-one 平台，需盯增量；中文查询已 URL 编码，谁/何时/为何：21 时班批7 全量补课班首例）

## C. 雷达源（每轮全过）

- awesome 清单：awesome-agent-orchestration（正主 vivy-yi 77★，2026-10-06 in:name 勘定）、awesome-claude-skills（正主 ComposioHQ 76,657★，2026-10-08 00 时班 in:name 勘定——旧注 anthropics/ 已 404 失配，travisvn 同名 15,299★ 系镜像噪声，谁/何时/为何：00 时班 awesome 20 源实测）、ai-boost/awesome-harness-engineering（4.7k★，2026-10-04 属主补认）、awesome-mcp-servers（punkpeye，2026-10-04 名实修正：旧 punkpeye/awesome-mcp 已 404，社区迁此仓 95.8k★ 两轮实证存活）、awesome-cli-coding-agents（正主 bradAGI 1,317★，2026-10-06 13 时班 in:name 一次勘定；ishandutta2007 同名 4★ 系镜像噪声，谁/何时/为何：13 时班 C 源属主补注）、awesome-ai-agents（正主 e2b-dev 30,314★，2026-10-09 15 时班属主补注）、awesome-llm-apps、awesome-claude-code（正主 hesreallyhim 55,293★，2026-10-09 15 时班 repos 实测属主补注；上批「54k★」裸名无属主易失联）；另存量头部失配勘定 6 件全名补认（2026-10-09 15 时班 in:name 一次勘清）：affaan-m/ECC、NousResearch/hermes-agent、DietrichGebert/ponytail、langgenius/dify、earendil-works/pi、anomalyco/opencode）、VoltAgent/awesome-agent-skills（34.6k★ 1000+ skills）、buildwithclaude（正主 davepoon 3,604★，2026-10-08 10 时班 in:name 勘定——旧注缺属主裸名 404，谁/何时/为何：10 时班 awesome 20 源实测）
- awesome 清单补充（2026-09-22）：RUC-NLPIR/Awesome-Long-Horizon-Agents（长程 agent 路线图）、TeleAI-UAGI/Awesome-Agent-Memory（记忆域地图）、caramaschiHG/awesome-ai-agents-2026（300+ 资源月更）、vijaythecoder/awesome-claude-agents（4.4k★ Claude 子代理编排，2026-10-05 07 时班批7 捞出补入）、TsinghuaC3I/Awesome-Memory-for-Agents（665★ 记忆域论文集，WebSearch 交叉验证捞出+repos 二次实证，2026-10-05 16 时班批2 补入——批2 域专属地图与 TeleAI 互补）、Engineering4AI/awesome-spec-driven-development（288★ spec-driven 域专属清单——批3 同款域专属补位；BMAD-METHOD 正主=bmad-code-org 53,909★〔in:name 勘定，2026-10-08 10 时班 repos 实测〕大漏同轮 WebSearch 捞出坐实「主扫 per_page=5 剪切线+词根错配」盲区，谁/何时/为何：2026-10-06 04 时班批3 首补、10-08 10 时班属主勘定）、VoltAgent/awesome-ai-agent-papers（1,821★ 2026 agent 工程论文清单，覆盖记忆/评测/工作流——批2 域 WebSearch 交叉验证捞出+repos 二次实证，谁/何时/为何：2026-10-06 09 时班批2 补入）、EvoMap/awesome-agent-evolution（234★ agent 进化/记忆/自我改进专属清单）、IAAR-Shanghai/Awesome-AI-Memory（1,257★ 记忆域知识库）——批2 域专属地图第 3/4 张与 TeleAI/TsinghuaC3I 互补，WebSearch 交叉验证捞出+repos 二次实证双双坐实（谁/何时/为何：2026-10-07 09 时班批2 补入）
- GitHub Trending（weekly，ai/agent 类）；直抓被拦时的替身（2026-09-22 补）：ossinsight.io、trendshift.io
- topic 页：multi-agent-orchestration、ai-agents、claude-code、agent-framework、claude-skills、llm-agents、ai-coding-assistant、mcp
- 发行渠道：npm search（agent orchestrator / claude code）、pypi（agent orchestrator）各扫一页
- 框架周边搜：q=langgraph+platform / crewai+studio / autogen+studio 类；竞品名周边：q=orca+alternative、q=claude+flow+OR+ruflo 生态、q=openrig+OR+vercel+eve 生态（2026-10-07 06 时班补：openrig 系 WebSearch 交叉验证捞出的多 CLI 舰队编排同形态直接竞品〔mvschwarz/openrig 24h 640→5,494★ 病毒式〕、eve 系 Vercel Agent Stack 主件〔vercel/eve 5,474★〕，双双 repos 实证后入库跟踪，谁/何时/为何：06 时班批6 WebSearch 首捞）、q=openshell+OR+agent-orchestrator+OR+hive 生态（2026-10-07 18 时班补：NVIDIA/OpenShell 15,202★ 沙箱运行时/OrchestratorInc/agent-orchestrator 12,857★/aden-hive/hive 11,092★ 三件降级班 claim 级 repos 端点坐实大星且主扫 115 查全零命中系剪切线盲区，需盯增量，谁/何时/为何：18 时班批4 补课班）、q=cli-agent-orchestrator+OR+amux+OR+ai-maestro 生态（2026-10-07 21 时班补：awslabs/cli-agent-orchestrator 1,394★〔AWS 官方多 CLI 编排，12 provider，PyPI 在架〕/mixpeek/amux 520★〔Rust 控制面〕/23blocks-OS/ai-maestro 812★ 三件 A1 同形态出自 topic 页连扫 updated 头部、A1 常驻组当班未命中系新锐增速期，需盯增量，谁/何时/为何：21 时班批7 全量补课班）、q=openworker+OR+ai+coworker+OR+digital+coworker 生态（2026-10-08 00 时班补：andrewyng/openworker 18,461★〔吴恩达桌面 AI 同事，standing automations/specialist coworkers/governed 三面与 CodeBee 同构〕系 WebSearch 交叉验证捞出+repos 坐实、主扫 114 组从未命中系人名+品名复合词形盲区第 3 例〔StaffDeck/magic 同款——个人品牌词形与功能词形正交〕，需盯增量，谁/何时/为何：00 时班批7）
- 自家 CLI 名周边搜：q=codex+manager、q=claude+code+manager+OR+wrapper、q=opencode+suite、q=kimi+cli、q=grok+cli、q=deepseek+harness+plugin+OR+dsh+plugin（2026-10-05 18 时班补：catalog 已接 deepseek-harness〔探测名 dsh，本机在〕，顺 PerryLink 组织矩阵掘出 DSH 插件生态 48 件（非 fork 且 dsh- 前缀两页 28+20 实测）——dsh-research-report 证据账本/dsh-permission-rules 声明式权限；自家 CLI 周边词此前缺 deepseek-harness 系，顺藤规则比新词组更先命中，谁/何时/为何：18 时班首补）、q=oh-my-pi+OR+pi+ecosystem 生态（2026-10-09 18 时班补：can1357/oh-my-pi 34,736★〔pi 生态头部件「IDE 接进来的 coding agent」〕系 topic:claude-code 页捞出、115 组词+八班雷达全历史零命中——「pi 生态专名」词根与功能词形正交系词形盲区第 6 例〔StaffDeck/magic/openworker/open-code-review/ace 同型〕，pi 系已接 CLI 其生态头部件需雷达跟踪，谁/何时/为何：18 时班批4）；deepseek-harness 属主勘定（2026-10-09 18 时班）：PerryLink/deepseek-harness 已 archived 且仅 1★（旧址），真身=**deepseek-ai/deepseek-harness 246,077★** 官方 org（主扫头部自证），catalog install/upgrade 走 npm 包名 @deepseek-ai/dsh 零断链，DSH 插件生态归属待下轮顺 orgs/deepseek-ai/repos 复核）、q=minimax+code+OR+minimax+cli 生态（2026-10-07 18 时班补：MiniMax-AI/minimax-code 1,985★ 官方开源 terminal coding agent，org 矩阵顺藤坐实〔OpenAgentCore 196★=OpenAI Agents API 自托管实现〕，E 专项候选本机未装待实测零接入防死链，谁/何时/为何：18 时班批4 补课班）、q=cost-xray+OR+autoharness+OR+pr-test-guard+OR+tigerless 生态（2026-10-07 22 时班补：tigerless-labs org 顺藤四件矩阵——autoharness 9,172★ D 专项同域待深挖件放量加速、cost-xray 3,833★ A3 逐部件请求成本透视新参照、agent-memory 959→2,379 大放量、pr-test-guard 139★ B1 微型；org 顺藤规则第 2 班连用，需盯增量，谁/何时/为何：22 时班批1 补课班 trendshift→org）
- 禅道/项目管理/工单周边搜：q=zentaophp+OR+zentao+ai、q=bug+triage+agent+OR+issue+auto+assign、q=jira+ai+agent+OR+linear+ai+agent、npm search「zentao ai」（2026-10-07 22 时班补第 4 通道：@jw-king/dsh-plugin-zentao〔DSH bundle 插件连禅道 REST API，09-02 建 v0.1.17〕系 npm 查询捞出、GitHub 2 查 28 例从未命中——渠道正交性第 3 例，禅道零新竞品连就此破，谁/何时/为何：22 时班批1 补课班）（竞品：禅道集成 7951ca5 已落地，持续盯增量；2026-10-09 15 时班第 4 通道 4 件首见：@staragent/zentao-mcp 1.0.8〔10-08 在更成熟线〕/@haoyu-qi/dsh-zentao 0.1.0-rc.8〔DSH 系禅道插件首例——自家已接 CLI 插件生态与禅道交叉〕/@liwei19911215/zentao-mcp 1.0.1/@aipper/zentao-mcp-server 0.1.26——禅道 MCP 在架 3→7+ 件密度上升系需求侧确认，MCP 查询形态居多，「激活 Bug→自动建 code 修复任务→resolve+评论回写+群通知」深度闭环 CodeBee 仍独占，第 32-35 例）
