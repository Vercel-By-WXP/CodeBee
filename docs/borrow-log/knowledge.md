# 竞品模式库（knowledge.md）

> 每轮调研深挖的项目在此沉淀：项目 | 亮点机制 | 与 CodeBee 对比 | 结论 | 上次调研日期。
> **已沉淀项目每轮仍需复查**：老项目会更新——看近期 commits/releases/CHANGELOG 相对
> 上次调研日的增量，有新机制就吸收更新；确实没变化写一行「YYYY-MM-DD 复查无增量」。
> 知识库的作用是知道查过什么、从哪续查，不是免查金牌。新条目追加在文件尾部。
> 结论取值：借鉴(进路线图) / 已落地 / 已覆盖 / 不适用 / 参考。

## token 节约机制专项（每轮必查维度）

目标：帮 CodeBee 用户省 token。已有机制（对照基准）：
- 三段上下文压缩（compaction.py：工具结果剪枝→LLM 摘要→surface replace）
- token_meter 压力表 + 换将超窗预检（step_runner 0.9 事前门）
- 单次运行预算熔断（budget.max_tokens_per_run）
- cascade 级联路由（easy 任务按 tier 升序走廉价模型）
- 经验召回免重读（skills.relevance_top）
- CLI 会话复用（免重发前缀）+ 连载前情提要

待调研方向：prompt 缓存显式利用（cache 断点/热缓存不压缩）、语义结果缓存、
diff-only 评审、工具结果去重、精简输出协议、更细粒度廉价模型分流。

## 2026-09-18 第三轮（调研日 2026-09-18）

## 2026-09-18 第三轮

- **omnigent**（omnigent-ai/omnigent，10.1k★）| 编排多 CLI 的元壳：每步前按「本步将用模型」容量重估并压缩、跨壳任务交接、聚合看板、pre-run 成本预估 | 换将超窗预检与成本预估已抄；跨壳交接≈我们的换将链；看板≈蜂巢 | 已落地/已覆盖
- **agent-orchestrator**（Untrivial-ai，12.1k★）| planning→merge 全程监督、.spec/PROMPT.md 任务规格文件化、计划评审闸 | spec 文件化未抄（路线图）；计划闸≈编排者+待裁决 | 部分借鉴
- **oh-my-claudecode**（39.2k★）| 团队化编排、安全围栏、自学习沉淀、PR 工作流、doctor 健康诊断 | 大多有对应物（经验库=自学习、diagnostics=doctor、评审闸=围栏） | 已覆盖
- **munder-difflin**（7.5k★）| 同任务 N 克隆并行+评审择优+每任务 token 上限；LanceDB 向量经验检索 | 赛马已抄（连载+单稿）；预算熔断已有；向量检索未抄（重依赖，暂缓） | 已落地
- **freebuff**（CodebuffAI/freebuff，12.3k★）| 每步按本步模型容量重估、缓存感知压缩、suggest_followups、best-of-n 多策略+败者精华回收、专职子 agent 分工（thinker/researcher-web/file-explorer 家族）| 预检/追问卡/赛马精华已抄；专职子 agent 分工未抄（路线图候选）；缓存感知按设计不需要 | 已落地/部分
- **emdash**（5.8k★）| 并行编码 agent + worktree 隔离 + 外部集成面 | 隔离链已有；外部集成抄了 Webhook 思路 | 已覆盖
- **edict**（cft0808，16.9k★）| 三省六部制分角色治理 + 实时看板 + 多模型 | 治理隐喻可参考；看板=蜂巢 | 参考
- **grill-me-skill**（RobMitt，610★）| 需求拷问：一次一问、每题多选弹窗、能自答绝不问用户、沿决策树逐分支到达共识、收尾汇总决策 | 三问向导是固定题序，此为自适应树状追问升级；追问芯片机制现成可复用 | 借鉴（路线图：需求拷问模式）
- **grill-for-unknowns**（nicobailon，219★）| 先找未知项、再拷问计划、达成实现前共识 | 与 grill-me 合并借鉴 | 借鉴（同上）

## 前两轮（详见当日报告）

- **Baton / Codeband / CodeCrew** | 隔离链、待裁决徽章、跨族评审硬规则、章节实时预览、bee.py CLI、README 小队叙事 | 全部已落地（第一轮清单①-⑥）
- **第二轮 GitHub 全景** | 采访式向导（三问一确认）等 | 全部已落地

## 检索经验（方法论沉淀）

- 只搜 2-3 组关键词会漏大量同类项目（2026-09-18 用户指正）：必须 ≥12 组关键词（含 token 节约专项组）+ sort=stars/updated 双轮 + created:>近半年新锐轮 + GitHub topic 页 + awesome 清单顺藤摸瓜 + Trending
- skill 生态（topic:claude-skills、skills marketplace）是独立借鉴源：好 SKILL 的方法论蒸馏进经验库/内置技能，不限于代码功能
- 已沉淀项目不等于免查：每轮复查增量，老项目的新版本常带新机制

## 2026-09-18 傍晚试跑补充（调研日 2026-09-18）

- **orca**（stablyai，71.5k★）| 并行 agent 舰队的 ADE | 待深挖（本轮最大遗漏，规模超过此前所有已调研项目）| 待深挖
- **Whale**（usewhale，928★）| DeepSeek 终端 agent，~98% prompt 缓存命中 | 缓存利用路线的标杆案例 | 借鉴方向（token 专项）
- **LLMLingua**（microsoft，6.7k★）| prompt 压缩（剪枝/替换降 token）| 可用于长上下文注入前的压缩；重依赖慎接 | 参考
- **prompt-cache**（messkan，409★）| 语义缓存，降本 80% | 语义缓存=同义请求直接回缓存 | 借鉴方向（token 专项）
- **claude-code-cache-fix**（433★）| 缓存回归致 20x 成本的真实案例 | 印证「缓存破坏=真金白银」；我们压缩时机设计已避开 | 已覆盖（设计层面）
- **token-goat**（121★）| token 燃烧抑制器 | 思路参考 | 参考
- **sandcastle**（mattpocock，8k★）| TS 沙箱编排 coding agents | 沙箱隔离与我们 CLI 沙箱思路互补 | 参考
- **lazycodex**（3.5k★）| 复杂代码库的项目记忆+规划 | 项目记忆≈经验库+圣经，规划≈编排者 | 待深挖

### 复查记录
- 2026-09-18 傍晚：freebuff/omnigent/agent-orchestrator/oh-my-claudecode/munder-difflin 全部活跃（push 09-17/18，star 微增），未做逐 commit 增量——下轮补
- 2026-09-18 傍晚：grill-me-skill 无变化（push 2026-04-11）

## 2026-09-18 夜·扩组第二轮（调研日 2026-09-18）

- **orca**（stablyai，71.5k★）已深挖 | 桌面 ADE：手机伴侣 app（跑完通知+随时追话）、并行 worktree（一个 prompt 扇出 5 agent 各自 worktree，比完合并胜者）、Ghostty 级终端 | 手机伴侣≈我们 Tailscale 远程访问+通知；worktree 择优=Best-of-N 工程版（我们的赛马在内容引擎，代码引擎可借鉴 worktree 版）| 借鉴方向：代码任务 worktree 版 Best-of-N + 移动端体验
- **gsd-pi**（open-gsd，1.2k★，gsd-2 7.8k★ 的继任）已深挖 | spec-driven：milestones→slices→tasks 三层结构、.gsd/ 本地项目记忆（需求/决策/计划/验证证据）、AUTO 状态条显示当前派发模型、hermes 停滞会话通知、消息保留模型路由溯源 | 三层结构≈编排者拆解；项目记忆≈经验库+圣经；停滞通知已有看门狗；**已抄：步骤卡模型路由溯源徽章**；差量：验证证据持久化（.gsd 的 validation evidence）| 已落地（溯源徽章）+ 借鉴方向（验证证据）
- **trycua/cua**（23.1k★）| computer-use 2.0：开源驱动+跨 OS 舰队+基准 | 我们无 GUI 操控能力；建书资料抓取是 CDP 定制实现 | 不适用（暂）
- **agent-os**（buildermethods，5.4k★）| 注入代码库标准+写更好 spec | spec 路线参考 | 待深挖
- **conductor**（gemini-cli-extensions，3.7k★）| spec-driven 开发插件 | 轻量 spec 工作流参考 | 待深挖
- **moai-adk**（modu-ai，1.2k★）| SPEC plan/run/sync + TRUST 分层 | spec+信任分层参考 | 待深挖
- **OpenSandbox**（opensandbox-group，15.4k★）| 安全快速可扩展的 agent 沙箱运行时 | 我们的沙箱=CLI 自带；浏览器沙箱场景不同 | 参考
- **helix**（helixml，809★）| 私有 agent 舰队+spec coding，每 agent 独享 GPU | 私有化部署路线 | 参考
- **SWE-AF**（Agent-Field，1k★）| 自主软件工程舰队产生产级 PR | 类似 agent-orchestrator | 待深挖

### 复查记录（逐 commit）
- 2026-09-18 夜：orca 近期=工程化收尾（macOS 差分客户端加固/移动端 UI 重做/多 PR 行折行/UNSTABLE PR 可合并策略）——移动端与 PR 流成熟中；freebuff=私仓高频快照（每天 10+ 次 sync，无公开语义增量）；agent-orchestrator 当日 7 commit 均工程修复

## 2026-09-18 深夜·翻页扩查第三轮（调研日 2026-09-18）

翻页纪律（核心组 page=2）+ evals/observability/MCP/handoff/router/中文 六组新词的收获：
- **paseo**（getpaseo，17.6k★）| 桌面+手机编排多个编码 agent | 与 orca 同赛道（桌面+移动），移动端思路二证 | 待深挖
- **superset**（superset-sh，14.4k★）| agentic IDE 编排 100+ coding agent 并行 | 超大舰队编排的 UI/调度思路 | 待深挖
- **agentmemory**（rohitg00，28.6k★）| 面向编码 agent 的持久记忆（真实基准第一）| 我们经验库=教训型记忆；差量=结构化项目记忆（决策/事实分层）| 借鉴方向（已进路线图）
- **swarms**（kyegomez，7.2k★）| 企业级多智能体编排框架 | 框架路线，与我们的产品路线定位不同 | 参考
- **nexent**（ModelEngine-Group，5.9k★）| 零代码平台自动生成生产级 agent | 无代码方向参考 | 参考
- **solace-agent-mesh**（SolaceLabs，4.9k★）| 事件驱动的多 agent 编排 | 事件驱动 vs 我们的流水线驱动，调度模型参考 | 待深挖
- **Vibe-Skills**（3336★）| 智能 Skill 路由与工作流编排（+21.12pp 基准）| **Skill 路由思路可借鉴我们经验召回**：按任务特征选技能而非全量注入 | 借鉴方向
- **Tracely-ai**（1.4k★）| trace 原生 CI/CD：生产失败变回归测试 | 失败→回归测试的闭环思路（我们错误台账可加「一键变测试」）| 借鉴方向
- **workshop**（raindrop-ai，1.1k★）| 让编码 agent 自己写并跑 agent evals | 自评式质量闭环 | 待深挖
- **axonhub**（5.2k★）| 开源 AI 网关：100+ LLM、内置故障转移 | 与我们绑定链换将同构，跨语言网关参考 | 参考
- **smallcode**（2k★）| 为小模型优化的编码 agent（4B 活跃参数 87% 基准）| 小模型分流路线佐证（cascade 已有）| 已覆盖
- **sia**（2.2k★）| 自我改进 AI 框架 | 与我们经验库自学习同向 | 参考
- **oh-my-opencode-slim**（8.9k★）| 精调 OpenCode 多智能体套件·混模·自动委派 | opencode 生态编排参考 | 待深挖

### 复查记录
- 2026-09-18 深夜：paseo/superset/agentmemory/Vibe-Skills/Tracely 进待深挖队列；swarms/nexent/axonhub/smallcode/sia 定性完毕（参考/已覆盖）

## 2026-09-18 深夜·新维度第四轮（调研日 2026-09-18）

新维度词（评审/自我改进/长任务/人机协同/框架生态/协议/成本）收获：
- **planning-with-files**（OthmanAdi，27k★）| AI 编码 agent 的持久化文件计划+长任务 | **直击 .spec 路线图**：计划落文件、跨会话续跑、断点可见 | 待深挖（高优）
- **prime-agent**（PrimeIntellect-ai，21k★）| 自我改进 RLM agent+长自主任务 | 自我改进≈经验库自学习；长任务=连载断点续跑同向 | 待深挖
- **cc-sdd**（gotalab，3.7k★）| 批准的 spec→长时自主实现 | spec 驱动+自主实现的衔接设计 | 待深挖
- **pi-plans**（110★）| Pi CLI 的人机协同规划扩展（rough changes→计划→批准→执行）| **我们已接 pi**；人机协同规划=需求拷问模式的同路人 | 借鉴方向
- **Yuxi**（xerrors，7.1k★）| 可私有部署多租户知识智能体平台（中文）：统一 RAG/知识图谱/多智能体/MCP/Skills/沙箱权限 | 中文生态最大发现（靠 q=智能体+平台 挖到）；私有部署+知识图谱方向参考 | 待深挖
- **agent-client-protocol**（4.3k★）| 编辑器连接任意 agent 的协议 | 协议生态位参考（我们的编排面是产品不是协议）| 参考
- **agentscope-java**（5.7k★）| 分布式生产级长运行 agent | 长任务工程化参考 | 参考
- **aegra**（1.2k★）| LangGraph Platform 开源替代（自托管）| 框架平台自托管参考 | 参考
- **argo**（827★）| Local Manus 桌面 | 通用 agent 桌面参考 | 参考
- **mira**（303★）| 自托管 AI 代码评审：索引化 PR 评审/走查/漏洞 | 跨厂商评审可借鉴其索引化评审（增量而非全量）| 待深挖
- **pr-af**（632★）| Code-Review-Bench 第一的开源评审器 | 评审基准化思路 | 参考
- **pandaprobe**（787★）| agent 工程：traces/evals/metrics | 观测参考 | 参考
- **Aegis**（370★）| agent 运行时策略执行+密码学审计链+HITL 审批 | 全权沙箱的治理增强方向 | 参考
- **CrewAI-Studio**（1.4k★）| CrewAI 多平台 GUI | 框架 GUI 参考 | 参考

### 复查记录
- 2026-09-18 深夜：新维度词验证了扩词价值（planning-with-files 27k★ 此前完全没出现在任何词组里）；轮换组 A/B 机制与全部新词已固化进夜间自动化（48 组总库）

## 2026-09-18 深夜·写作场景专项（A7 首跑，调研日 2026-09-18）

**A7 组首跑即命中核心场景直系竞品——验证关键词扩到最大的价值**：
- **oh-story-claudecode**（zenstory-ai，7k★）已深挖 | 网文写作 skill 包（13 skills+7 agents+8 hooks+100+ 方法论），装进 Claude Code/ZCode/OpenCode/Codex/Reasonix 等 7 种 CLI：**扫榜选材、拆解爆款、大纲门禁、去AI味、封面图、分层上下文**；「文件系统当记忆」几百章不靠对话记忆；面向起点/番茄/晋江/七猫/知乎盐言 | **正面撞车我们核心场景**。可借鉴：①**去AI味检查**（平台审核硬需求，我们评审维度没有）②**扫榜选材/拆解爆款**（选题洞察入口，我们只有官方类目抓取）③**剧情模块库**（素材模块化跨章复用——经验库增强方向）④分层上下文 vs 我们的圣经+前情（对比精修）⑤封面图生成（挂作品信息）| 借鉴（多项进路线图）
- **denova**（766★）| AI 小说+RPG 创作平台 | 平台形态参考 | 待深挖
- **neuro-book**（674★）| 长篇小说 AI IDE（软件工程化写作）| IDE 形态参考 | 待深挖
- **NovelClaw**（372★）| 动态记忆优先的长篇协作框架 | 动态记忆 vs 我们前情提要 | 待深挖
- **Lanerra/saga**（110★）| 自主 agentic 故事写作+存储情绪线 | 情绪线管理参考 | 参考
- **NovelFlow**（金字塔上下文分段生成）| 与我们前情提要同思路 | 参考
- **wzxsph/Novel-Claude**（12★，中文工业级网文框架）| 中文同赛道小体量 | 参考
- **A8 组**：pezzo（3.3k★ LLMOps prompt 平台）、promptMinder（中文提示词管理）、proofhound、awesome-hallucination-detection（1.1k★ 论文库）——prompt 管理与幻觉检测是独立赛道，登记备查 | 参考

### 路线图新增（oh-story 借鉴包）
- [ ] **去AI味检查**（评审维度+确定性检测脚本）
- [ ] **扫榜选材/拆解爆款**（选题洞察）
- [ ] **剧情模块库**（素材模块化跨章复用）
- [ ] 封面图生成（挂作品信息一键生成）

### 复查记录
- 2026-09-18 深夜：A7/A8 组首跑；keywords.md 总库（97 组+雷达）建立

## 封面图生成（设计就绪，待安全策略放行——2026-09-18 深夜）

**设计定稿**（四轮迭代被 Mimosa 策略拦截，架构本身可行）：挂 bookmeta.py（同款 generate_async 状态机写任务 cover_gen 字段）→ 调编排者供应商 OpenAI 兼容 `/images/generations`（模型候选 cogview-3-flash → cogview-4，env CODEBEE_IMAGE_MODEL 优先；竖版 768x1344 失败回落 1024x1024）→ 产物落 runs/<run_id>/cover.png（与 report 同款受管路径）→ UI 走 run 文件通道预览。SSRF 边界：仅 https+解析 IP 拒私网/环回/链路本地+禁重定向。
**被拦原因**：扫描器策略性拒绝「网络下载→写盘」组合（无法证明下载字节无害）。**放行条件**：用户调整 Mimosa 策略或确认接受下载落盘风险后，按本设计重实现（预计 30 分钟）。路由图先挂「封面图生成（待安全放行）」。

## 2026-09-19 夜间第一班（调研日 2026-09-19，周六·轮换批7）

- **planning-with-files** 已深挖（26,981★）| 三文件 task_plan/findings/progress 分离、每轮 hook 注入计划头（goal+next+active phase）、SHA-256 防篡改、Stop gate 防提前收工、KV-cache 稳定注入（289ms/次）、盲测 3/3 胜、恢复 13.3 轮→5.0 轮 | **已抄两件**：task_plan.md 落盘（与 spec/evidence 同居 .codebee/）+ 子任务进度注入（多子任务时提示词带「第 i/N 项+已完成」防漂移）；findings.md（中间发现）与 Stop gate 暂不抄（连载章级进度已有对应物） | 已落地
- **cognee**（topoteretes，30.8k★）| 开源 AI 记忆平台（知识图谱式）| agentmemory 同赛道更大体量；我们经验库=教训型，知识图谱路线暂缓（重依赖） | 参考
- **agency-orchestrator**（jnMetaCode，2.3k★）| 一句话→一人公司 AI 专家→交付物（中文）| 与我们定位同向，中文产品化参考 | 待深挖
- **deep-eye**（2.3k★）多供应商编排、**LocalAGI**（2k 本地自托管）| 网关/本地路线参考 | 参考
- 复查：Yuxi 7094★（+）活跃；planning-with-files/prime-agent/pi-plans 均 09-18 活跃；cc-sdd 停更（05-20）——降级出待深挖队列
- 新 CLI 本机探测（reasonix/fuxi/zero/empryo/goose/crush/herdr）：均未装，无新增接入

## 2026-09-19 傍晚续班（调研日 2026-09-19）

- **agency-orchestrator** 已深挖（2.3k★，中文）| 一句话→自动组队（276 专家）→流水线交付；**结果群推送**（--notify：钉钉/飞书/企微按域名自动适配，"配合 cron 就是定点交活"）、可分享自包含报告页（ao report）、验收标准 acceptance 字段、社区工作流模板、Claude 服务商安全切换+急救 | **已抄：群推送 notify.py**（域名自动适配+curl POST+收尾异步推，设置 notify_webhook）；分享报告页/验收标准进待深挖 | 已落地
- **cognee**（30.8k★）| 开源 AI 记忆平台（知识图谱式记忆）| 经验库=教训型；知识图谱重依赖暂缓 | 参考
- **七猫榜单数据源（扫榜选材可行性）**：www.qimao.com/paihang/ **公开可抓**（200/106KB，书名+分类在 a 标签，页面含简介块）——扫榜选材的数据源落点确定：七猫派先行，番茄网页 DNS 不通+API 需 SecuritySign 签名（难，缓）
- 插件市场六源连通：zcode 26/anthropic 310/anthropic-skills 5/claude-skills 99 OK；clawhub 网络不通；cocoloop 0 项——市场巡检纳入夜班常态

## 2026-09-19 傍晚二班（调研日 2026-09-19）

**重大盲区补漏**（agentic coding 组 sort=stars 首页一击命中 4 个巨型项目——此前十轮调研从未命中，说明「sort=stars 首页」这手牌此前低估）：
- **superpowers**（obra，288,620★！）| 完整软件开发方法论 skill 框架：对话中 teased spec→**分段短块给用户逐段消化签核**→实现计划要「清晰到热情但没品味没判断力没项目上下文且讨厌测试的初级工程师也能照做」（**初级工程师测试**）→subagent 驱动开发（自主跑几小时不离计划）；17 种 harness 全覆盖 | **借鉴①：spec 分段签核**嫁接进需求拷问（问完→分段预览 spec→用户逐段确认→落 spec.md）；**借鉴②：计划清晰度「初级工程师测试」**写入编排者规划提示词；subagent 开发=我们实现链已有 | 借鉴（两项小件进路线图）
- **ECC**（affaan-m，262,331★）| agent harness OS：skills 为主工作面+commands 兼容垫片；**「我想…→用这个面→哪个 agent」三列表**（I want to... / Use this surface / Agent used）是极好的任务类型组织范式 | 借鉴方向：类型菜单加「我想做什么」引导列 | 参考+待深挖
- **ponytail**（142k★）|「让 AI 像最懒的资深开发一样思考」——最少代码解决问题哲学 | 与 YAGNI 同向 | 参考
- **cc-switch**（133k★）| Claude Code/Codex/OpenCode 桌面 All-in-One 助手 | 与 agency-orchestrator 切换器同赛道 | 参考
- **orca 复查**：72.2k★（+0.7k）持续领跑；paseo 17.7k/superset 14.4k（当日 push）均活跃
- superset 消化：100+ agent 并行 worktree+live diff——与我们代码赛马同向，无新差量；paseo 消化：桌面+移动编排，移动端借鉴已记 orca 条

### 2026-09-19 晚三班补录
- **awesome-claude-code**（hesreallyhim，54,298★）与 **VoltAgent/awesome-agent-skills**（34,587★，1000+ 官方+社区 skills 精选）——两个 skills 生态巨型雷达源此前未入库；已纳入 keywords.md 雷达源清单（awesome 清单节）与夜班雷达
- **buildwithclaude**（3.5k★）：Skills/Agents/Commands/Hooks/Plugins/Marketplace 单一入口枢纽——插件市场巡检的补充源
- **vsync**（61★）：MCP/Skills/Agents/Commands 跨 CLI 同步——多 CLI 配置同步方向参考
- 并行代理同期完成**封面真机 E2E 闭环**（ece1a02：三真案修复，cogview-3-flash 768x1344 竖版 182KB 实出图）——封面图功能全通

## 2026-09-19 晚·每2小时第一班（批2+全类型）

**短剧赛道爆发**（A10 短视频脚本组首跑即中）：
- **drama-skills**（zenstory-ai，2k★，oh-story 同门新品线）| 11 skills 短剧/漫剧全流程：原著分析→分集剧本→视觉设定→图片提示词→分镜→视频提示词→生产→剪辑成片→审查。**三个机制极可借鉴**：①五份 Markdown 即创作事实（剧本/视觉/分镜/图提/视提——无数据库，改文件=改决定，与我们任务档案/圣经思路同源）②**连续性锁**（跨镜造型写成可直接贴提示词的短语+每镜说明依据+检查脚本指出遗漏）③**先预览确认再生产**（图片/视频/配音先在文件里看到准确内容参数确认后才调外部接口花钱）| 路线图：连续性锁→圣经人物卡增强；预览确认→封面生成前预览 | 借鉴方向
- **Toonflow**（15.8k★）/ **火宝短剧**（15.3k★）/ **Jellyfish**（6.5k★）| 一站式短剧平台（小说→动画短剧/一句话→短剧成片）| 平台化路线，CodeBee 可作「小说→短剧剧本」上游供给 | 参考+待深挖
- **codegraph**（colbymchenry，71.5k★）| 预索引代码知识图谱，代码变更自动同步，Claude Code 等 CLI 直接查询 | 代码任务方向的重大发现——代码任务可加「代码库知识图谱」能力（重依赖，先记路线图） | 待深挖
- **graphiti**（getzep，31k★）| 实时知识图谱 for AI agents | 与 cognee 同赛道 | 参考
- 批2：Agent_Memory_Techniques（1k★，30 本 notebook 记忆技术）、agent-apprenticeship（1.6k★，任务循环+技能习得生态）、mengram（人类式三层记忆）、KIP（知识交互协议）、RLCF（社区反馈强化学习）——均参考级
- A10 全类型其他组：邮件/汇报/对话记忆组无新可借鉴标的（低星学生项目为主）——**短剧组是大鱼**

## 2026-09-20 00:00 第二班（周日批1：代码质量与评审）

- **pr-af**（Agent-Field，634★）已深挖 | Martian Code-Review-Bench 38 PR 上 **#1 开源评审器（golden recall 0.706/42 工具对比）**：任务定制评审计划→spawn 专职评审 agent→**findings 锚定代码证据**→**challenge 质疑结果**（评审的评审）→便宜模型榨出更多有效评审智能；**模型分级**（常规 PR 用 DeepSeek 级/深度用 GLM-5.2/重大用 Opus 级）成本 10× 低于闭源 | **借鉴方向（代码任务）**：①评审 findings 要求「锚定代码行证据」（我们评审输出可加 evidence 行号要求）②challenge 二次质疑（跨族评审已有——可加「对已发现问题质疑复核」环节）③按 PR 重要性分级模型（难度分级路由已有 difficulty——可加「评审深度随 diff 规模分级」） | 借鉴方向（三项小件）
- **mira**（305★）自托管 AI 代码评审（索引化 PR 审查/走查/漏洞）——此前已入待深挖，保持
- **pr-lint-action**（72★）PR 标题规范 GitHub Action | 轻参考
- **gsd-browser**（265★，gsd 家族的浏览器自动化 CLI——为 agent 从零构建）| 我们 CDP 通道的替代思路参考 | 参考
- refactor/tech-debt 组：无成熟标的（学生项目为主）——技术债治理赛道尚空
- 复查：code-review-checklist 1084★ 稳定；ai-codereviewer 停更（2024-08）

## 2026-09-20 02:00 第三班（批3：计划/spec/长任务）

**spec-driven 两大头部全漏补齐**：
- **spec-kit**（github 官方，137,935★）已深挖 | 三独立入口：①SDD（constitution 项目宪章一次定→specify→plan→tasks→implement→**converge 收敛循环**直到 Converged）②Bug fixing（**assess→fix→test 三段分离**——诊断/修复/验证各司其职，verdict=verified/partial/failed，缺验证≠成功修复）③Idea assessment（intake→research→define→shape→decuce——投资前证据决策 go/clarify/stop）。**Constitution 机制**：每项目一次定代码质量/测试/可维护性原则，所有后续特性共用 | **借鉴方向**：①**任务宪章**（constitution→工作目录 .codebee/constitution.md，作者定质量原则，每次 run 注入——比故事圣经更通用，代码/文章全类型可用）②converge 收敛判定（我们的评审循环已有类似；差量=显式「Converged」结论）③bug 三段分离（我们的 fix 轮已有 verify；差量=assess 独立段） | 借鉴（任务宪章先行）
- **OpenSpec**（Fission-AI，69,546★）| SDD for AI coding assistants（另一大流派）| 待深挖
- **get-shit-done**（gsd 家族原型，64,498★）| gsd-2/gsd-pi 的根仓库 | 参考
- **PraisonAI**（9.1k★）24/7 AI Workforce；**TaskWeaver**（微软 6.2k★ code-first 计划执行）；**DeepResearchAgent**（SkyworkAI 3.5k★ 分层多 agent 调研——调研报告任务对标）| 均参考/待深挖
- 复查：orca 72.5k★/superpowers 288.8k★/ECC 262.7k★ 全活跃（09-19 push）

## 2026-09-20 04:00 第四班（批5：检索/知识/浏览器）

- **BrowserSkill**（Tencent，5.7k★）已深挖 | **让 agent 用真实登录态浏览器不打扰用户**：①复用已登录状态（无需测试账号）②任务跑在独立可见 Agent Window（用户浏览器不受扰）③**「借标签页-还标签页」显式协议**（要用哪个 tab 明说，用完归还，其余不碰）④内置 human-in-loop（验证码/登录/确认弹窗时请人接管后继续）；bsk CLI 任何能调 shell 的 agent 可用；沙箱 agent 有 BSK_HOME 持久 daemon 方案 | **与我们 CDP 通道对比**：我们接管整个 Edge（用户不能同时用）；BrowserSkill 的「独立 Agent Window+借还标签页」体验更好——**借鉴方向：发布通道的浏览器会话改用独立窗口实例**（短期）+「借还标签页」语义（长期） | 借鉴方向
- **Agent-Reach**（Panniantong，83,412★）| 给 agent 一键装上互联网能力（替你选好/装好/体检好接入方式，换代不用操心）| **「接入方式会换代你不用操心」的抽象层思路**=我们绑定页的协议适配 wire_caps 同向；本体是工具聚合器 | 参考
- **12-factor-agents**（humanlayer，26.3k★）| 构建 LLM 软件的 12 条原则 | 工程原则参考（值得单独深挖提炼） | 待深挖
- 深研赛道（调研报告任务对标）：khoj（37.4k）/gpt-researcher（29.5k）/dexter（27.6k 金融深研）/阿里通义 DeepResearch（20k）——**我们的调研报告任务可对标 gpt-researcher 的迭代深研**（多轮搜索-阅读-综合循环） | 待深挖
- 复查：langchain 146.7k/langgraph 42k/eliza 19.4k 均活跃

### 2026-09-20 06:00 第五班（批7：中文网关/本地/小说生成）
- **unsloth**（76.4k★）本地训练/运行 LLM 与扩散模型（GGUF/MLX）| 本地路线工具参考 | 参考
- **anything-llm**（66.2k★）自托管全栈 AI 工作站 | 本地优先参考 | 参考
- **openhuman**（39.9k★）本地优先记忆+agent 编排 harness | 待深挖
- 中文网关组无新发现（one-api 替代品多为小项目）；中文小说平台组空结果——**词组太窄，改为「小说 AI 平台」方向已由 A7 覆盖**

## 2026-09-20 08:00 第六班（批2：学习记忆+技能库）

- **OpenSpec**（Fission-AI，69.5k★）已深挖 | 哲学「fluid not rigid / iterative not waterfall / brownfield not just greenfield」；**四段工作流**：/opsx:explore（对话探索技术路径——先看代码库再给最干净方案让用户拍板）→ propose（proposal.md+specs+design.md+tasks.md 四件套）→ apply（逐任务执行勾选）→ **archive（归档到 changes/archive/日期-名称/，specs 更新为正式需求）**；**SHALL 场景化 spec**：`## Requirement: X / The app SHALL... / #### Scenario: WHEN...THEN...` 纯 Markdown 可读可审 | **借鉴方向**：①explore 探索段（我们需求拷问是问需求，缺「先看代码库再给方案」的探索轮——适合代码任务编排者前置）②archive 归档语义（任务档案三件套缺「归档/规格升格」概念——.codebee/ 完成后可升格为正式 spec） | 借鉴方向（两项）
- **scientific-agent-skills**（K-Dense-AI，45.6k★）| #1 科学 agent skills 库 | 市场雷达新源 | 参考
- **text-to-cad**（16.1k★）/ **AI-Research-SKILLs**（12.9k★）/ **stitch-skills**（8.3k★）/ **anbeime/skill**（7k★ 中文技能商店——收录最全更新最快）/ **GPT-Image2-Skill**（5.5k★）| skill 生态大库五连——插件市场 B 专项的潜在接入源（anbeime 中文尤其贴合） | 待深挖
- 批2 复查：Agent_Memory_Techniques/agent-apprenticeship 无增量

### 2026-09-20 08:00 第六班 B 专项结论
- anbeime/skill 评估：元聚合器（聚合 VoltAgent/awesome 上游），SKILL_SOURCES.json 无描述需二跳解析，与我们已有雷达重合——**不入市场源，降级雷达参考**
- VoltAgent/awesome-agent-skills 34.6k 逐类扫描：anthropics 官方 17 skills 与市场已有源重合（docx/pptx/pdf 已可装）；社区部分垂直小众（SEO/营销/CFO/材料模拟）；**结论：按需挑装而非批量接入**——市场六源+官方源已覆盖主流量，新装留给用户按需触发
- B 专项沉淀教训入经验库：市场源接入判断三问（①与我们已有源重合度②有描述可直读吗③用户会主动搜吗）——回答不好就不接，雷达跟踪即可

### 2026-09-20 10:00 第七班（批4：治理/安全/HITL）
- **BAML**（BoundaryML，9.2k★）|「agent 的编程语言」——结构化输出 typed prompt 工程 | 参考+待深挖（结构化输出 schema 与我们 JSON 解析网互补）
- **Plano**（katanemo，7.1k★）| AI 原生代理服务器/数据平面：智能 LLM 路由 | 网关参考
- **mcp-context-forge**（IBM，4.5k★）| AI 网关+注册表+代理（MCP/A2A/REST 前置）| MCP 网关参考
- **OpenAgentsControl**（4.9k★）| plan-first 工作流+approval 执行 | HITL 参考
- **failproofai**（3.8k★）| agent harness 观测+强制（capture every run and rule）| 观测参考
- **cordum**（508★）|「AI agent 的动作防火墙」——危险操作前策略+人审 | 与我们 auto_submit=false 纪律同向 | 参考
- kill-switch 组：avakill/state-harness/halt 小而美——token 螺旋检测/注定失败任务早杀与我们预算熔断+停滞看门狗同向，无新差量
- 复查：archestra 4.3k 活跃（09-20 push）

### 2026-09-20 12:00 第八班（批6：框架/平台/SDK）
- **agents-cli**（google 官方，5.9k★）已深挖 | 「把你的编码助手变成 agent 构建专家」——CLI+skills 让 Claude Code/Codex/Antigravity 获得企业级 agent 构建/部署/治理能力（Gemini Enterprise Agent Platform 上的 skills 下发）；`npx skills add google/agents-cli` | **与我们关系**：同是「给 CLI 下发 skills 增能」路线的官方实现——验证了我们 skills 市场方向；其「平台 skills 包」概念可借鉴 | 参考
- **Mastra**（28.2k★）| TS 现代框架 | 框架参考
- **agentcn**（shadcn-labs，473★）|「shadcn/ui 但给 agent 用」——组件化 agent 构建 | 参考
- **aegra**（1.2k★）LangGraph 平台开源替代 | 复查活跃
- 批6 其他：voice SDK/数据工作台等垂直，无直接差量

## 2026-09-20 14:00 第九班（批1：代码质量与评审）

- **SkillSpector**（NVIDIA，17,861★）已深挖 | **AI agent skills 安全扫描器**：装前答「这个 skill 安全吗」——71 漏洞模式×17 类（提示注入/数据外泄/提权/供应链/过度自主/工具滥用/memory 投毒/反拒答/触发滥用…）；两段式（静态+LLM 语义）；OSV.dev 实时 CVE；0-100 风险分+建议；基线误报抑制。**研究数据触目：31,132 skill 中 26.1% 含漏洞、5.2% 疑似恶意**。NVIDIA Verified Skills 流水线（扫描→评估→签名→目录）| **与我们市场安全对比**：我们有白名单闸门+SSRF 防护+剥离式检查——**差量=模式库细度与风险评分**。**借鉴方向：市场装前扫描增强**（安装前静态扫危险模式：eval/exec/反连 URL/env 读取外发，给风险提示行——纯本地静态规则，够小够实） | 借鉴方向（装前扫描）
- **Tencent/AI-Infra-Guard**（6.5k★）AI 红队平台（Agent Scan/Skill Scan）| 同赛道参考
- **vuls**（12.3k★）无 agent 漏扫（Linux/容器/WordPress）| 基础设施侧，非我们域
- 复查：superpowers 288.9k/oh-story 7.0k/drama-skills 2.1k 活跃
- pr-review 组新锐无新标的（学生项目/模板为主）

### 2026-09-20 16:00 第十班（批3：计划/spec/长任务·GitHub push 443 持续抖动，API 通道可用）
- **paseo** 深挖完毕（17,675★）| daemon+多客户端架构（桌面/iOS/Android/web/CLI 连同一 daemon）；**Settings→Pair Device 手机配对**（E2E 加密 relay，可拒走 TCP/Tailscale 直连）；**语音控制**（口述任务/语音讨论 hands-free）；插件 TypeScript 生态（npm/Git/本地目录） | **对比我们**：Tailscale+令牌≈其直连模式；差量=①语音输入（手机远程页可加 Web Speech API 口述——小而实）②设备配对 UX（我们手动带 token URL，它扫码级体验）| 借鉴方向：语音输入进对话页

### 2026-09-20 16:00 写作场景新锐轮（第11班前哨）
- **mr-li-writer-skill**（5★，新）| 去 AI 味中文长文写作 skill（先评估后写作）| 与我们 aiflavor 检测同向；其「先评估再写」两段式可借鉴进我们的去AI味流程 | 参考
- **platform-writing-skills-cn**（新）| 中文多平台内容工作流 agent skills | 与我们 13 种预置类型中的文章/汇报同域 | 参考
- 写作场景新锐轮持续有中文写作 skill 产出——A7 组保留高优

## 2026-09-20 19:00 全类型巡检（批5）

- **adaptive-llm-gateway** | 语义缓存、请求回放、PII/提示注入防护集成在统一网关 | CodeBee 只有显式 `cache_ttl` 精确缓存，且生产调用尚未启用；语义缓存和回放均缺 | 借鉴（先做生产精确缓存接线，再评估语义缓存；项目新，暂不入市场） | 2026-09-20
- **Continuous-Claude-v3** | 将 MCP 工具结果放进隔离上下文，只向主会话回灌必要摘要，减少工具输出污染 | CodeBee 已有工具结果剪枝与三段压缩，但压缩摘要尚未真正替换 CLI 请求正文 | 借鉴（压缩摘要进入真实请求链） | 2026-09-20
- **agentic-inbox** | 保留邮件线程上下文，并从往来邮件提取责任人、待办和截止时间 | 商务邮件目前有专属评审维度，但没有线程级交付契约 | 借鉴（邮件类型后续加入线程摘要和行动项校验） | 2026-09-20
- **ai-passage-creator** | 文章以研究、写作、审校多角色协作，并按内容语义选择配图方式 | 自媒体文章已有多评审，缺少配图决策；本轮先补平台化成品契约 | 参考（配图工作流列入待深挖） | 2026-09-20
- **clodex / acpx** | 用稳定的终端/headless 协议跨机器驱动编码 agent | CodeBee 已有本机 11 CLI 目录与远程访问，但没有通用跨机 agent 传输协议 | 参考（协议成熟度不足，雷达跟踪） | 2026-09-20
- **agency-agents-zh** | 中文专家角色模板按职业交付物拆分 | 与内置流程 rubric 有部分重合；可直读但用户不会把整包当插件搜 | 借鉴（蒸馏为类型角色与交付契约，不整包接市场） | 2026-09-20
- **Orca / oh-my-claudecode / ECC / OpenSandbox** | 2026-09-20 复查仍活跃 | 编排、隔离、经验和安全能力与现有实现重合，本轮无新增可抄机制 | 已覆盖/复查无增量 | 2026-09-20

### 本轮三问与待深挖

- 市场接入三问：六源现有覆盖高；新增候选多为聚合清单，描述需二跳；用户主动搜索概率低。因此本轮不新增市场源，只跟踪。
- 待深挖队列：①让压缩摘要进入真实 CLI 请求；②39 个通用 skill 约 36.7 万字改为 top-k/独立配额；③市场下载重定向与非 GitHub `git clone` 的 SSRF 旁路；④生产精确缓存与语义缓存；⑤邮件线程行动项和文章配图决策。

## 2026-09-20 默认推荐调度与显式覆盖

- **默认自动推荐、指定才绑定** | 竞品网关普遍把路由策略与人工 pin 分开；CodeBee 旧界面把“智能体管理”和“CLI 绑定”并列成必经步骤，空绑定实际只回落 CLI 默认 | 改为未指定时运行期临时推荐：协议/启停/密钥冷却/健康为硬约束，任务类型、角色、难度、档位、价格、优先级为软评分；不落盘 | **已落地** | 2026-09-20
- **本机智能体与模型调度职责拆分** | 本机 CLI 生命周期与运行时模型选择是两个层次 | 设置入口改名为“本机智能体”与“模型调度（可选）”；前者管安装/版本/启停/冒烟/CLI 默认模型，后者只管显式覆盖 | **已落地** | 2026-09-20
- **显式覆盖优先且可只锁供应商** | 有些用户要固定账户/网关，但仍希望模型按供应商默认或难度选择 | `provider_id + 空 chain` 作为合法显式状态；完整链继续支持跨厂商降级；供应商变化只修复已有显式死链，不把空链自动落盘 | **已落地** | 2026-09-20
- **自动链必须复用多 KEY 容灾** | 自动推荐若只取 `api_key` 镜像，会丢失首 KEY 欠费后的备用切换和冷却记账 | 推荐条目按 `_chain_keys` 展开并保留 `provider_id/key_id`，与显式链共用 runner 尝试和冷却机制 | **已落地** | 2026-09-20
- **绑定别名必须白名单化** | 旧二元表达式把所有非 codex 标识都回落到 `claude-code`，可令 opencode/qwen 等误继承 Claude 绑定并触发死链 | 只允许 `codex↔codex-cli`、`claude↔claude-code`，其他 CLI 只读自身绑定 | **已落地** | 2026-09-20
- **安全下载逐跳复验** | 初始 URL 通过 SSRF 校验不代表重定向目标仍安全 | HTTP 每跳禁自动重定向、重新校验公网地址且限制 3 跳；git clone 仅公网 HTTPS 且禁跟随重定向 | **已落地** | 2026-09-20

待复查：①将调度决策理由展示到运行详情；②基于真实成功率/延迟/成本做在线权重校准；③语义缓存需先完成租户/任务隔离与敏感内容边界；④自动推荐链运行失败后是否允许追加 CLI 登录态兜底，需单独产品开关，不能对显式绑定静默降级。

## 2026-09-20 直接执行与无队列语义

- **默认不排队** | 固定 worker 池即使默认并发较高，只要容量被占满仍会产生用户不可控的 `queued` 等待；内存队列还会在重启、线程启动失败时制造僵尸状态 | 改为“原子占执行位→run 切 running→启动独立线程”；达到保护上限直接失败并要求稍后重试，绝不接受后静默等待 | **已落地** | 2026-09-20
- **并发计数必须在线程启动前预占** | 旧 `_resize` 在 `Thread.start()` 返回到 worker 增加 `_alive` 之间可重复判断不足，瞬间多开线程 | 调度线程创建前在锁内递增 `_alive`，启动失败释放；结束通过 condition 统一归还 | **已落地** | 2026-09-20
- **等待必须带业务原因和截止时间** | 容量排队与自动重试退避都显示 queued，用户无法判断是卡死还是策略等待 | 容量等待取消；仅保留 `resume_enqueue_at` 明确的自动续跑退避，等待不占并发位 | **已落地** | 2026-09-20
- **取消是状态机写入而非单一事件** | 取消可能落在执行方读状态与收尾写状态之间，旧线程会把 `cancelled` 复活成 `failed` 或 `running` | 所有流水线终态写入使用 `expected_status=running` CAS；起跑确认按 `queued/running` 当前态 CAS；管理、禅道、自升级入口同步收口 | **已落地** | 2026-09-21

### 2026-09-20 20:00 第十三班（批7：中文网关/本地/小说生成）
- **model-hotel**（53★，新）| 多供应商网关：模型自动发现+无个人日志 | 网关赛道小项 | 参考
- **poster-v2**（新）| 中文网文封面海报生成流水线（小说事实抽取→海报）| 与我们封面图同域的中文实现——**novel fact extraction 思路可借鉴封面提示词**（从章节事实自动生成视觉元素而非仅题材） | 参考
- 网关/小说组无重大新标的（既有 one-api/new-api/LibreChat 已覆盖）
- 工具聚合组：无新（Agent-Reach 83k 仍是标杆）

### 2026-09-20 22:00 第十四班（批2+A11/A12 新词首跑）
- **pmb**（277★）已深挖 | 本地优先持久记忆：SQLite 实体图谱（3800+ 实体/41000+ 连接自动捕获）；MCP 供给多 agent；**「量化记忆真实帮助」而非宣传 +X%**；读路径无 LLM 调用 | 对比：我们 findings/spec 是文件式记忆——**文件式胜在版本化与可读，图谱胜在关联查询**；两者互补，暂不引 SQLite（轻量优先） | 参考（量化记忆效果思路可借鉴进 evidence.md 加「引用计数」）
- **pi-mem**（77★）Plain-Markdown 持久记忆（长期事实+日记）| 与我们 findings.md 同路的极简实现——验证文件式路线 | 参考
- **ai-memory-comparison**（164★）源码背书的记忆系统对比表 | 挑选记忆方案时的参考源 | 参考
- A12 发布/反馈组：无新标的（空结果/学生项目）——**读者反馈分析是空白赛道**

## 2026-09-21 00:00 第十五班（批1：代码质量与评审）

- **OpenCodeReview**（alibaba，38,241★）已深挖 | 阿里内部 2 年数万开发者验证的 AI 评审 CLI：**agent 带工具读全文件/搜代码库/看其他变更文件**——不是表面 diff 评审而是深上下文评审；**`ocr scan` 全文件审查**（审计陌生代码库/无 diff 场景）；行级精度结构化意见；多 agent 支持（Claude Code/Codex/Cursor/Kimi）| **对比我们**：我们评审只看 diff——**差量=评审者可主动读文件**（CLI 智能体本身有工具；内置智能体也有。评审提示词可加「可读源文件深挖」指引）；`scan` 全审模式对**连载全局评审**有启发（不只看新增章） | 借鉴方向（评审深读指引）
- **opcode**（winfunc，22.4k★）| Claude Code 的 GUI 工具集：自定义 agent/管理工具 | 参考
- **agent-skills**（tech-leads-club，6.5k★）| 安全验证的专业 skills 注册表 | 与我们市场安全层同向 | 参考
- **mcp-gateway-registry**（934★）企业级 MCP 网关+注册表 | 参考
- 复查：LibreChat 44.5k 活跃

### 2026-09-21 02:00 第十六班（批3：计划/spec/长任务）
- **genie**（automagik-dev，338★）已深挖 | 「Wishes in, PRs out」——**interviews→plans→parallel dispatch→acceptance review→merge-ready**；轻量三面（签名二进制+skills+可选 Orca 插件）；cosign 签名+SLSA 溯源；per-repo SQLite 单文件；**interviews 用户进 plan**（=我们的需求拷问+grill-me 同路，验证方向）| 参考+方向验证
- **markdown-memory**（25★）| 跨平台文件式持久记忆桥 | pi-mem 同路再验证 | 参考
- **CCteam-creator**（306★）| Claude Code 多 agent 团队编排 skill | 参考
- SDD 新锐全为学生/练习项目——spec-kit(138k)+OpenSpec(69.5k) 已覆盖
- 复查：planning-with-files 27,021★（+36）活跃

### 2026-09-21 04:00 第十七班（批5：检索/知识/浏览器）
- **evalscope**（modelscope，3.5k★）| 阿里模型评测框架（LLM/VLM 高效评测）| A 专项相关：我们缺自评测 | 参考
- **EnterpriseRAG-Bench**（562★）| 企业内部文档 RAG 基准 | 调研报告任务的质量标尺参考 | 参考
- **oya-browser**（202★）| 浏览器控制平面：一个 API 跨 Oya Cloud/Browserbase/自托管 | BrowserSkill 同域再验证 | 参考
- **lexicon**（新）| voice-to-agents 个人词典（YAML 一个文件）| 语音输入的词表纠偏思路（人名/术语定制）——**可借鉴语音输入加自定义词表** | 借鉴方向（小）
- 浏览器/本地知识库组：学生项目为主，无重大标的

### 2026-09-21 06:00 第十八班（批7：中文网关/本地/小说生成）
- **OpenFic**（syrizelink，1,116★）已深挖 | 「AI Native 一站式 Vibe Writing 工具」：构建设定/设计角色/定制工作流——**「让 Agent 适应你的写作流程，而非反之」**；明确反对「一句提示词一键生成整本」的不切实际定位（与我们圣经+大纲+逐章评审的渐进创作理念完全一致）；长期维护世界观/角色/伏笔/章节；自定义 Prompt/Agent/工作流；本地数据+上下文管理 | **定位共鸣**：这是首个明确打出「人机协作渐进创作」旗号的中文竞品——CodeBee 的连载流水线（圣经+赛马+评审+门禁）在同一理念下更自动化 | 参考（产品定位文案可借鉴）
- **denova** 复查 786★（+20）活跃 | 小说+RPG 创作平台 | 参考
- **北斗 beidou**（新）| AI 网文创作工作台全栈 | 学生级，雷达跟踪
- 本地推理组：llama.cpp 自托管站系列（gputier）——本地权重跑 Claude Code | 参考
- 路由组：无新标的（既有 model-hotel/one-api 覆盖）

## 2026-09-21 04:30 用户点名调研：leftopen

- **leftopen**（SonghaiFan，38★，macOS 菜单栏+CLI）已深挖 | **"See what your tools left running on localhost, and gently close them"**：检测本机被工具遗留的监听端口→识别所属项目→温和关闭。三个核心机制：①**项目归属推断**（从进程工作目录向上走到 .git/package.json/pyproject.toml/Cargo.toml/go.mod 或 .app——全局 npm/python -m/独立服务也识别，极少看到裸 node/python）②**本地 vs LAN 区分**（127.0.0.1 vs 0.0.0.0/LAN 地址的安全边界）③**温和关闭**（关闭前显示进程其他端口，关闭瞬间重验 PID+启动时间，只发 SIGTERM 绝不 SIGKILL 绝不杀系统进程）；CLI 与 GUI 同引擎同推理同安全规则；零 daemon 零 Dock 图标零 telemetry

**与 CodeBee 关系**：
- 我们 **_kill_tree 已由并行代理重写**（TerminateProcess 优先+taskkill 短等待兜底），但**缺项目归属推断**——孤儿进程清理按 PID/进程名杀，不知道该进程属于哪个项目/用户
- **借鉴方向（待实施）**：①孤儿清理加**项目归属探测**（读 /proc/<pid>/cwd 或 Windows 等效，向上走找到项目根，避免误杀用户 dev server）②**端口归属可见化**（扫描器报「端口 3000 属于 project-A 的 dev server」而非裸 node）③**安全关闭协议**（SIGTERM only + PID 重验——与并行代理的 _kill_tree 重写方向一致但更保守）

**落地路径**：不改 runner.py（并行代理域）——新增 `app/core/portscan.py` 独立模块，供「启动收尸」和「诊断面板」调用

**✅ 已落地（2026-09-20）**：
- `app/core/portscan.py`：跨平台端口→PID→进程→**项目归属推断**（CWD 向上走标志文件；家目录及以上算环境噪音不算项目——真机实测 Temp 进程会误报归属到家目录，已加护栏）；本地 vs LAN 区分（local_only）；温和关闭（SIGTERM/taskkill 无 /F，关前重验 PID，系统进程/自身服务拒关）
- `app/main.py`：①端口绑定失败自动指认占用者（PID/进程/项目，替代手跑 netstat+tasklist 两连）②GET /api/ports（只读诊断，?port=N 过滤，self 标记）③POST /api/ports/close（设备控制权守卫内，越界 400）
- `app/ui/`：设置页「帮助改进 CodeBee」面板新增「端口占用」区——扫描表格（端口/进程/项目/仅本机/本服务徽章），非自身进程给「关闭」钮，confirm 后温和关闭并重扫
- 测试：test_portscan.py 8 项（归属/解析/去重/守卫/真机冒烟）+ ui_ports.mjs 12 项（随机高位端口+随机 CDP 口防并行双绑；自身关闭 409 人话 toast 全链路）

### 2026-09-21 08:33 第十九班（批1：代码质量与评审 + 全类型雷达）

- **sepia**（Nanako0129，2,714★）已深挖 | **De-AI writing at the layer that actually gives AI away**：基于 StoryScope 研究（61,608 篇小说，2026）——**AI 小说 93.2% 靠叙事架构特征检出，人工改写措辞后检出率仅 95.5%→93.9%**（架构级指纹改不掉）；三遍协议：叙事架构→语篇流→表面措辞（市面 humanizer 全在第三层）；**架构级 tells：主题由叙述者说破/单线因果整洁/情绪只写身体感觉/无真实世界指涉/线性时间/成长式收束**；30 特征诊断 rubric+分模型指纹（Claude/GPT/Gemini/DeepSeek/Kimi 两层）；**职场文体分场合规则**（PR 回复：先答再引 file:line 不 reflex praise 篇幅∝利害；postmortem：对人宽容对机制无情+时间戳+死胡同+归属行动项；技术文章：从问题开场+一个真实死胡同+一个明确观点+带条件的数字）| **与我们 aiflavor 对比**：我们只有措辞层（18 套话模式）——**架构层是真空**| **✅ 已落地**：aiflavor.py 新增 `narrative_analyze()` 三类可确定性检测的架构信号（顿悟说教/情绪身体化/成长式收束[只扫尾 600 字]），报告行随评审下发带情节结构追问；职场文体规则进待深挖（报告/邮件评审 prompt 增强）
- **pi-subagents**（tintinweb，1,196★）已深挖 | pi 的 Claude Code 风格子代理编排：隔离会话/后台并发/中途 steering/会话恢复/自定义 agent 类型(.pi/agents/*.md)/嵌套子代理(allowlist 特权边界)/**@mention 子代理一等公民**/`SubagentWorkflow` 确定性 JS 编排（agent()/parallel()/pipeline() 无栅栏流水线，vm 沙箱禁 Date.now/random/eval）/`gate:"npm test"` **用命令验证子代理而非再问模型**/优雅回合上限（硬中止前先 wrap-up 警告出干净部分结果）/git worktree 隔离+完成自动提交分支/事件总线+跨扩展 RPC/定时子代理（cron，PID 锁持久化）| **dsh 有社区适配器**（#258：pi host API→原生 DSH agents）| **与我们对比**：我们=多 CLI 编排台（pi 是被编排对象之一），子代理语义在 CLI 内部；**可借鉴**：①确定性 gate 验证（跑测试代替模型复查——省 token 且更硬）②wrap-up 警告代替直接强杀（与用户「取消=强杀」拍板冲突，只记录不实施）| 借鉴方向（gate 验证进待深挖）
- **hermes-conductor**（forcewake，75★）已深挖 | **"Zero trust in self-reports"**：18 生产看板/367 卡片/566 次派发的实战蒸馏——**编排者只路由不干活**（route-only profile）+ 每个 CLI 独立 worktree 泳道 + **diff/tests/commits 由控制者验证，绝不信任代理自己的汇报** + canonical 集成分支+证据 | **与我们对比**：我们已有事件流计数鉴别谎报（零工具判 VENDOR_REFUSAL）——方向一致；差量=worktree 泳道级并行（我们任务分支隔离链已有，跨厂商并行评审已有）| 方向验证
- **tale**（tale-project，29★，Elixir OTP）| AI 聊天+项目+知识+自动化一体工作台；**Arena 模式**（同题双模型并排对比+投票）| 我们 Best-of-N 已有赛马，UI 并排对比可参考 | 参考
- **alibaba/open-code-review**（38,383★）| 阿里规模化实战：**确定性规则+AI 混合架构**代码评审 | 验证我们「确定性检测行+模型评审」两层路线 | 方向验证
- 短剧赛道（A12 组）：**huobao-drama 15,368★**（一句话→成片全自动）、Toonflow 15,824★ 复查（+9k 增量活跃）、drama-skills 2,119★ 复查、wind-comic 581★（新，单行文本→成品短剧多代理管线）| 短剧改编是 CodeBee 连载→视频的远期方向，雷达跟踪
- A11 记忆组新锐：**memsearch**（zilliztech，2,626★）Markdown+向量统一记忆层（Claude Code/Codex/DSH）、**archgate/cli**（68★）**ADR 当可执行规则**（与人同守+与 AI 同守——我们宪章是提示词级，可执行规则是差量）、tigerless-labs/agent-memory 959★（Markdown 真源+本地排序检索）| 参考/待深挖
- A3 缓存组：vCache 79★（语义提示缓存系统）、adaptive-llm-gateway 12★（包月订阅→网关复用）、weave-os/router 4,546★（"<50ms 路由省 40-70%"）| prompt 缓存路线图既定，无新机制
- oh-my-claudecode 39,273★ 复查（+2k 活跃）、orca 73,593★ 复查（活跃）、omnigent 10,119★ 复查、edict 16,903★ 复查、HKUDS/DeepCode 16,602★（新入雷达：agent harness&loop 工程）、novel 域 narralume 114★（开源中文长篇写作工作室，理念与我们近似）/vellium 134★（本地优先桌面工作台）

## 2026-09-21 10:30 用户点名调研：zai-org/ZCode 开源

- **ZCode**（zai-org，361★首日，Apache-2.0，TypeScript monorepo）已深挖 | Z.ai 官方 coding agent harness 开源：Electron 桌面 + Web + TUI + Agent CLI（`zcode` 命令三态分流：无参进 TUI / --web 进 Web / 其余给 Agent CLI）；apps/zcode-cli 是普通目录非 submodule；`ZCODE_DATA_BASE_DIR` 数据基目录；远程 SSH/WSL 经 SFTP 上传资源；打包出 tar.gz+sha256+latest.json+install.sh 走自建下载根
- **真金在治理体系而非功能**：
  - **architecture-policy.yaml + scripts/architecture-check.mjs + .architecture-baseline.json**：机器可执行架构守护——模块 id/roots/managed 标记/publicEntrypoints/owner；全局 maxFileLines 400/maxContractLines 300/maxPublicMethods 12/forbidCycles/forbidDeepImports；**基线哲学=存量 violations 快照豁免、增量零容忍**；`architecture:check --changed` 只查变更文件
  - **AGENTS.md 宪章**：「新增或修改行为前先更新对应 spec」「未明确要求修改代码就先调查原因」「报告真实结果，不将已有失败写成通过」「修复 bug 用中文注释说明原因和修复依据」「发现设计缺陷先与用户对齐，不不断增加兜底分支」——与我们五道关/教训库理念同源，验证方向
  - **architecture-governance SKILL**：先跑架构检查→再读目标模块受控上下文（`architecture:context <module-id>` 模块阅读包）
- **与 CodeBee 关系**：zcode CLI 本机无独立可执行（桌面版宿主，未进 PATH）——按「先实测再入目录」纪律不接 catalog，入雷达待装后实测
- **直接会话适配复查**：源码入口支持 `zcode -p <prompt> --output-format json|stream-json --resume <id> --cwd <dir>`，可映射 CodeBee 的 direct 首轮/续轮/工作目录/结构化输出；但 CLI package 仍是 `private: true`，本机无 `zcode`，公共可复现安装链未成立。接入三问结论为「重合高、整仓不可轻量直读、用户会搜但当前会死链」，所以不进 catalog；待官方二进制发行后只接 CLI 协议，不复制 Electron/Web/TUI/会话库。Apache-2.0 复用时必须保留 LICENSE、NOTICE/归属及修改声明
- **可蒸馏方法论**：会话状态机（running/compacting/goalVerifying/completed）用形式化候选动作验证；工具执行按 schema 校验→PreToolUse→权限→执行→PostToolUse 收口；SessionStart/UserPromptSubmit/Stop hooks 有次数上限；项目记忆与会话记录分层。CodeBee 已落地架构基线，本轮将状态机/权限链列入 direct 会话后续路线图，避免用整仓复制换来双运行时
- **✅ 已落地（2026-09-21）**：架构基线守护 Python 版——`architecture-policy.json`（四层单向依赖：L0 基座/L1 领域/L2 编排/L3 入口）+ `scripts/architecture_check.py`（AST 建图/三色 DFS 找环/层序单向/行数历史最高基线/`--changed` 模式/`--update-baseline`）+ `.architecture-baseline.json`（首检 136 条存量：5 条真实 import 环 + 123 层序 + 8 千行文件）+ test_architecture_check.py 7 项（新环必抓/存量豁免/行数增长必抓/基线更新幂等/changed 全图环检测/真仓冒烟）。此后每轮自动化提交前跑 `--changed`，架构漂移机器把关

## 2026-09-21 在线路由反馈闭环

- **真实运行指标参与推荐** | 成功率、P95 延迟、平均成本按任务类型/角色/CLI/provider/model 聚合，精确样本不足逐层回退；Beta(3,1) 平滑避免新候选被一次失败永久压低 | CodeBee 原有静态能力、配额和历史胜负，缺少可解释的在线校准 | **已落地；只作软评分，硬约束与显式绑定优先** | 2026-09-21
- **脱敏调度回放** | 记录候选、分数、选择理由、降级链、验证/评审结果，跨运行复盘路由是否有效 | 旧 route_plan 只保存在单个 run 中，难做长期比较 | **已落地 `/api/dispatch/replay`；禁止保存 prompt、正文、密钥与文件内容** | 2026-09-21
- **升级后自动生效** | npm 升级成功后仅在版本变化且无其他任务运行时自动重启；端口未知或系统忙则明确回落手动重启 | 旧流程安装完成但进程仍跑旧代码，容易出现“升级了却没生效” | **已落地** | 2026-09-21
- **统一操作状态中心** | 顶栏会话级列表收口用户触发的写请求，记录 running/done/failed、时间与错误；后台 `busy:false`、澄清探测和 GET 轮询默认静默，完成项可清除、失败项可追溯 | 旧界面只有单按钮忙碌态，请求慢或失败时用户无法判断是否仍在执行 | **已落地；前端 `api()` 统一埋点，覆盖创建/删除/绑定/插件等操作** | 2026-09-21
- **启动端口清场边界** | 只对命令行精确匹配本包 `app/main.py` 的旧 CodeBee 杀树；其他占用者只报告 PID，不发送信号 | 自动关闭任意占用端口的服务会误伤用户开发进程 | **已落地** | 2026-09-21
- **附件消费闭环** | 上传落盘后，文本/代码与 Office 伴生文本按总计 1 万字符、单文件 6000 字符预读进 prompt；图片走原生视觉输入；PDF/旧 Office 等不可预读格式显式标状态；修复/修订轮再次携带附件上下文 | 旧实现只给 `_attachments/` 路径并期待模型主动读，快档、换将、无会话修订会直接忽略；GBK 还可能被误判 UTF-16 | **已落地；附件正文按不可信资料隔离，不能覆盖系统规则** | 2026-09-21

### 2026-09-21 12:10 第二十班（批5：检索/知识/浏览器 + 全类型雷达）

- **chinese-novelist-skill**（PenglongHuang，3,139★，v2.0）已深挖 | Claude Code Skill 完整中文小说生成：**三层递进式问答**（可快速跳过/随机生成）+ **偏好记忆跨会话学习用户喜好** + 中断续写自动检测断点 + 三种写作模式（串行/子Agent并行/Agent Teams）+ 自动校验（字数+连贯性不合格自动重写）+ 每章必爽（开头高潮结尾悬念）| **与我们对比**：问答向导/断点续跑/逐章评审/AI味检测全有——**差量=偏好记忆**（跨会话学习「这个用户喜欢什么」）| 借鉴方向（偏好记忆进经验库或任务档案）待深挖
- **AI-Novel-Writing-Assistant / Biz Novel Studio**（ExplosiveCoderflome，2,966★，Trendshift 榜）已深挖 | 一句灵感→方向/世界/角色/**卷战略**/章节任务自动规划；章节生成-审核-修复-**状态回灌**生产链可暂停可恢复；拆书/知识库/写法引擎/**角色资源账本**/世界手册=可召回长期资产；漫画/短剧衍生工坊 | React+Express+LangGraph+Qdrant | **差量=角色资源账本**（物品/伤因/转交追踪）+卷战略层 | 参考
- **AI-Novel-Writer**（EthanYoQ，1,036★，桌面版+DSH插件）已深挖 | v1.1.0 三大机制：**来源分层的连续性材料**（作者填写/模型提炼/旧项目未知来源分层，写作优先带来源定稿原文）；**分层章节材料**（本章任务/未发生计划/定稿历史/候选稿分列，相邻段落承接伤因/否定/物品转交）；**逐项目标审稿**（本章关键事件逐项显示已完成/未完成/待核实+正文证据，不把准备当完成）| **差量=逐项目标审稿**（我们是打分制，他们加了 per-event 完成度核对）| 借鉴方向（连载评审加事件清单核对）待深挖
- **claude-mem**（thedotmack，94,355★，Vercel OSS）新入雷达 | 会话持久上下文：捕获 agent 会话全程→压缩成可召回记忆，跨会话注入 | 与我们经验库/项目记忆同路，规模最大 | 参考
- **Agent-Reach**（Panniantong，83,929★）复查 | AI agent 的眼睛：读搜 Twitter/Reddit/YouTube/GitHub/Bilibili | 检索面广度标杆 | 参考
- **Tencent/BrowserSkill**（6,114★）复查活跃 | 真实登录态浏览器复用（CLI+扩展不打断用户）| 与我们 CDP 发布通道同思路 | 参考
- **movo**（himovo，113★，新）| **把 DeepSeek Harness 变成自托管企业 Agent 平台**（知识/深研/内容生成）——dsh 生态第三例（pi-subagents 适配器、AI-Novel-Writer 插件之后）| dsh 作为底座的生态在长大 | 雷达
- 检索/抓取组：AIHawk 31.6k★（反检测浏览+抓取 MCP）/obscura 27.5k★（agent 专用无头浏览器）——灰色域只记录不借鉴；loci 98★（vector+BM25 混合检索二脑）小而美参考；引用核验组全为学生级，citegate「cite-or-refuse 引擎级强制」理念可记
- 短剧赛道增量：YoLuster-shorts 289★（新，釉光影短剧工作台）| 雷达
- 复查：oh-my-claudecode 39,277★（+4 活跃）、orca 73,786★（活跃）、edict 16,902★（活跃）、OpenFic 1,116★（+26）、oh-story 7,014★ 稳定、agentmemory 28,652★、memsearch 2,627★、archgate 68★ 活跃

**✅ 本班落地**：sepia 职场文体分场合规则进 CONTENT_DELIVERY_CONTRACTS——weekly_report（结论先行/不指名甩锅/行动项带 owner）、email（先答再铺陈/请求具体到动作/篇幅∝利害）、tech_proposal（问题开场/真实死胡同/明确观点/带条件数字）、doc（标题=结果/验收可测试/链接不重复）四类契约各 +2 条体裁硬规则，起草与修订全链路注入；test_content_contracts 新增 test_venue_rules_from_sepia

## 2026-09-21 动态工作流与可预期等待

- **运行级模式与思考程度分离** | 编排模式决定规划、实现、评审、修复和换将数量；思考程度决定每一步模型推理投入 | 旧固定深链让短邮件、翻译、小改动也付出多轮规划和评审成本 | **已落地：快速/自动/专家/手动 + 自动/快速/标准/深度；OpenAI wire 和支持参数的 CLI 实传** | 2026-09-21
- **全类型先编译再执行** | 根据任务类型、难度、验证能力和高风险词生成实际工作流 | 代码、内容、连载不再共用固定轮次；安全/权限/迁移/并发/架构/公共 API 保留评审底线 | **已落地；轻内容单评审，深内容保留大纲与多轮，代码短链最多一次针对修复** | 2026-09-21
- **会话级显式模型不可静默降级** | 用户指定厂商或模型是本次对话的硬约束；不可用时应解释失败 | 全局绑定与单次会话混用会让用户以为选中模型实际被替换 | **已落地；选择随任务保存，不改全局 CLI/模型绑定** | 2026-09-21
- **等待必须可估算** | 创建前显示同类历史/流程基线 ETA 与 P90，运行中显示剩余时间及超出常规/保守区间状态 | 单一“执行中”无法区分正常长任务和卡死 | **已落地；ETA 固化到 run，刷新页面仍可见** | 2026-09-21
- **桌宠复用窗口的识别标记必须稳定** | 窗口按浏览器进程 + `CodeBee [端口]` 精确匹配；所有标题更新路径都必须保留端口 | 初始化后语言切换覆盖标题会使窗口查找失效，表面修复仍重复开窗 | **已落地并补 UI 回归** | 2026-09-21

## 2026-09-21 17:52 计划/长任务增量

- **CONTINUUM**（28★）| 语义检查点 + 幂等动作账本，恢复时拒绝重复副作用 | CodeBee 已有任务恢复、CAS 状态和文件档案，但禅道回写、通知、发布等外部动作缺统一幂等键 | **借鉴：下一阶段增加 run/action/idempotency_key 账本** | 2026-09-21
- **maestro-flow**（556★）| 按意图自适应生命周期、自强化知识图谱 | 动态流程已落地；经验库采用可读、可版本化文件真源，图谱收益暂不足以抵消复杂度 | 跟踪，不引入 | 2026-09-21
- **keep-the-why**（158★）| 把被否决方案和原因作为 Git 版本化 Markdown 记忆 | findings/evidence 已记录发现与证据，缺显式 rejected decision | 借鉴：任务档案增加否决理由段 | 2026-09-21
- **confdiff**（41★）| JSON/YAML/TOML 等结构化语义 diff | 当前 diff-only 评审仍以文本 diff 为主 | 借鉴：配置类任务减少格式噪声 | 2026-09-21
- **wechat-article-pipeline-skill**（21★）| 优化、配图、排版和多平台草稿一体 | 文章写作评审完整，但发布适配缺失且涉及第三方账号 | 蒸馏发布清单，不直接接市场 | 2026-09-21

### 2026-09-21 18:13 第二十一班（批4：治理/安全/人机协同 + 全类型雷达）

- **microsoft/agent-governance-toolkit**（6,302★，微软官方）新入雷达 | Agent 治理工具包：策略强制+零信任身份+执行沙箱+可靠性评测 | 治理域大厂背书，方向验证 | 参考
- **stop-that-shit**（lennney，2,159★）新入雷达 | Hook+Skill 护栏拦截 AI coding agent **无需求的哈希/校验和伪造与任务范围膨胀**（Codex/GPT 多平台）| 与我们 require_tools/事件流计数鉴别谎报同路——他们做「事前拦截」我们做「事后鉴别」，互补 | 借鉴方向（hook 预防）待深挖
- **tradememory-protocol**（mnemox-ai，1,421★）已深挖 | AI 交易代理的**决策审计链+持久记忆**：SHA-256 防篡改、**按结局加权召回**（outcome-weighted recall——好决策的记忆权重更高）| **与我们经验库对比**：relevance_top 只看 bigram 重叠+hits，不看「这条教训后来有没有真帮上忙」——差量=outcome 加权（教训命中后任务成功率变化反馈进排序）| 借鉴方向（教训 outcome 加权）进待深挖
- **OpenAgentsControl**（4,868★）| 计划先行+**审批制执行**（危险操作先批后跑）| 与我们合并「待裁决」门禁同路，他们推到工具调用级 | 参考
- **FailproofAI**（4,918★）/cordum（508★，action firewall）/Aegis（387★，加密审计链+熔断）| harness 可观测+策略强制/工具调用防火墙/运行时策略 | 治理三件套参考 | 参考
- **harness-books**（wquguru，3,127★）| 两本 harness 工程书：Claude Code & Codex 的设计哲学（约束/查询循环…）| 学习材料 | 参考
- 注入防御组：RepoGuardBench（70★，**仓库内提示注入基准**——本地 coding agent 场景，与我们知识库/skill 注入面相关）/MELON（ICML'25 可证防御）/prompt-guard | 参考
- 治理/审计组复现 edict 16,903★（审计类搜索第一名，三章六部多代理+实时仪表盘）——治理与编排边界在模糊
- A3 组新锐：fable5-opus5-orchestrator（72★，**token 节俭编排**——保 Fable5 在座全天不爆配额）小而切题 | 雷达
- 复查：oh-my-claudecode 39,284★/orca 74,133★/omnigent 10,130★/claude-code-router 37,358★/freellmapi 27,732★ 全活跃；chinese-novelist-skill 等小说三竞品无增量

**✅ 本班落地**：连载「章节评审卡」前端展示（renderChapterScores）——详情页步骤区新增卡片：每章均分徽标（低分标红）/达标计数/未达标红框/字数轮数，**逐项目标审稿结果（event_check）行级红绿灰展示**（已完成=绿/未完成=红/待核实=灰）；ui_chapter_card.mjs 10 项（含英文词条）；第二十班落的 event_check 数据从此用户可见

### 2026-09-21 20:01 第二十二班（批6：框架/平台/SDK 生态 + 全类型雷达）

- **strands-agents/harness-sdk**（7,386★，AWS Strands 系）新入雷达 | 「Build an agent harness and control it end-to-end」——harness SDK 化（Python/TS），端到端控制 agent harness | 我们=编排台不是 SDK，但「harness 可编程化」方向值得关注 | 参考
- **aegra**（1,213★）| LangGraph Platform 开源替代：自托管 agent 后端 | 自托管路线参考 | 参考
- **agents-towards-production**（NirDiamant，21,483★）复查 | 原型→企业级 agent 教程库 | 学习材料 | 参考
- **CrewAI-Studio**（1,359★）/pandaprobe（786★，agent 工程平台：traces/evals/metrics）| GUI/可观测配套 | 参考
- **agentcn**（shadcn-labs，473★）| 「shadcn/ui, but for building agents」——agent UI 组件库 | 前端参考 | 参考
- **Patter**（1,060★）开源语音 AI SDK（Vapi/Retell 替身）| 语音方向雷达（我们语音输入已落地）| 雷达
- **AntSK**（1,327★，.Net9+SK 知识库问答）/semantix（651★，自进化 semantic kernel）| SK 生态中文标的 | 参考
- **A2A 协议族**：python-a2a 1,009★（Google A2A Python 库）/a2a-rust（v1.0.0 spec 类型安全）| 跨代理协议成熟中——我们单机编排暂无跨进程协商需求 | 雷达
- 生态大盘：vercel/ai 26.9k/mastra 28.2k/dify 156.7k/langflow 155.1k 活跃；system-prompts-and-models-of-ai-tools 143.8k（各家系统提示词全集，我们提示词工程的参照库）；ponytail 143.5k（「最懒高级工程师思维」人格——巨型星数的提示词项目，人格/风格域奇观）
- 复查：oh-my-claudecode/orca/omnigent/claude-code-router 活跃；小说三竞品无增量

**✅ 本班落地**：教训 outcome 加权（tradememory「按结局加权召回」借鉴）——block_for 支持 run_id 登记注入教训；learn_from_run 收尾按 verdict.pass 回写 won/lost（过审在场教训 won+1=真帮上忙，失败 lost+1=没防住）；relevance_top 同相关性下 karma 优先于 hits——好教训在排序中胜出、坏教训被挤出 top-k。test_skill_outcome 5 项（登记/幂等/lost/真实链路/排序/有界）

### 2026-09-21 22:00 第二十三班（批1：代码质量与评审 + 全类型雷达）

- **SkillForge**（tripleyak，897★）新入库 | **证据驱动的技能创建**：给 Claude Code/Codex 造 skill 时先建 baseline、装完自动验证「技能真的有效」——skill 不是写完就算，要证明其工作 | **与我们市场对比**：装前有 SkillSpector 危险扫描，但没有「装后有效性验证」——差量明确，进待深挖 | 借鉴方向（技能装后冒烟验证）
- **gsd-browser**（gsd-build，265★）| 为 AI agent 从零造的原生浏览器自动化 CLI（Chrome DevTools Protocol）| 与我们 CDP 发布通道同协议族 | 雷达
- **py-lintro**（新）| AI 评审引擎+15 linter 统一编排（CLI/Action/MCP 三态）| 确定性+AI 混合又一致方向 | 参考
- video-debug（skill）：从录屏抽关键帧调试 UI bug——与我们截图诊断思路可交叉 | 雷达
- 复查：SkillSpector 17.9k/alibaba open-code-review 39k（+600）/mira/pr-af 稳定；B1 组无重大新标的
- A 组复查：oh-my-claudecode 39.3k/orca 74.2k/omnigent 10.2k/claude-code-router 37.4k/freellmapi 27.8k 全活跃

**✅ 本班落地**：压缩摘要真正替换 CLI 请求上下文（最老待深挖项清账）——实锤 derive_messages() 零消费方：压缩折叠 session surface 后重试仍发原 prompt+续旧 resume 会话，摘要只进审计日志、CLI 上下文一点没小（重试必再爆）。修复：溢出重试改用 session 派生转录（折叠摘要+近尾消息，24k 上限）+ 本步指令缺失显式补尾 + **弃 resume**（旧会话本体仍是膨胀态）；test_step_runner 断言升级+新增 drops_resume 用例，压缩域 29 项回归绿

### 2026-09-22 00:00 第二十四班（批7：中文/网关/本地/办公 + 全类型雷达）

- **openhuman**（tinyhumansai，40,002★，新巨型标）入库 | 「开源 agent harness：local-first 记忆+编排+工作流」——本地优先的完整 harness 形态，与 unsloth/anything-llm 同列本地三巨头 | 深挖排队
- **iflytek/astron-agent 9,022★ + astron-rpa 5,551★**（讯飞开源）入库 | 企业级 agentic workflow 平台 + Agent-ready RPA 套件（商用友好协议）| 国内大厂 agent 化 RPA 的标杆参照 | 参考
- **Yuxi**（xerrors，7,158★）入库 | 可私有部署多租户知识智能体平台（统一 RAG/知识图谱/多智能体/MCP+Skills/沙盒权限）| 中文自托管全家桶对标 | 参考
- **cognee**（30,886★）复查 | 开源 AI 记忆平台（跨会话持久长期记忆）| 记忆域大盘 | 参考
- **BidCraft 标书匠**（18★，新）| 对话式标书编制：招标文件解析→标段选择→标书编写→知识库提炼→对话式改稿（LangGraph）| **新任务类型灵感：标书/投标文件**——我们的文档族没覆盖，进 keywords 备选 | 雷达
- comfy-agent（25★）：本地优先 ComfyUI 短剧工坊（10MB 零依赖 exe）| 短剧赛道补充 | 雷达
- 网关新锐：1Panel-Gateway（71★，统一接入/智能路由/合规审计——企业 AI 落地管控链）/FailoverAI（图/视频/LLM 可靠性网关）/CosyRedactGateway（脱敏网关）| 参考
- 本地大盘复查：unsloth 76.5k/anything-llm 66.3k/agenticSeek 27.3k/khoj 37.5k 全活跃
- B7 中文搜索组（数字员工/中转/小说平台/办公/中文编排）API 返回空——中文关键词命中弱，靠雷达-中文新锐补位

**✅ 本班落地**：T2.2 幂等调用精确缓存补全接线（§07 token-cost 收尾）——连通测试此前已接 24h；本轮补编排者三处：连载大纲（尝试 1 TTL 1h、**尝试 2 故意旁路**保「换样本」语义）/代码计划 1h/评审大纲 1h——断点续跑与重跑同任务时规划调用直接命中，省一次全量编排者调用。test_planner_cache_wiring 3 项

### 2026-09-22 02:01 第二十五班（批2：学习记忆与自我改进 + 全类型雷达）

- **codegraph**（colbymchenry，71,711★ 巨型标）入库 | **预索引代码知识图谱**：代码变更自动同步，给 Claude Code/Codex/Cursor/OpenCode 供给仓库级结构认知 | 与我们知识库互补（我们存经验教训/竞品知识，不做代码结构索引）| 参考/边界清晰
- **graphiti 31.1k / cognee 30.9k** 复查 | 实时知识图谱/持久记忆平台双雄 | 记忆域大盘稳定
- **Agent_Memory_Techniques**（NirDiamant，1,069★）| 30 个可跑的 agent 记忆 Jupyter notebook（buffer/向量库/知识图谱）| 学习材料 | 参考
- **MemRL**（172★，论文）| **运行时强化学习作用于情景记忆**——agent 自进化新路线（与我们的 outcome 加权同向但走 RL）| 参考
- **mengram**（201★）| 人类式三段记忆：semantic/episodic/**procedural（经验驱动的程序性知识，从做中学）**| 程序性记忆是我们空白 | 雷达
- **pro-workflow**（rohitg00，2,875★）| Claude Code 从你的纠正中学习：**自纠错记忆跨 50+ 会话复利** | 与我们教训库同路，规模参照 | 参考
- **projectmem**（830★）| 记录 issues/attempts/fixes/decisions，**在 agent 重蹈覆辙前警告它** | 「事前警告」角度与我们的注入式教训互补 | 雷达
- **prax-agent**（273★）| 自改进运行时：test-verify-fix 循环+纠正检测+跨会话 | 参考
- **KIP**（82★）| Knowledge Interaction Protocol——持久记忆与学习的开放协议 | 雷达
- A 组复查：全活跃无增量事故

**✅ 本班落地**：新增「标书编制」任务类型（BidCraft 标书匠灵感清账）——bid_doc 流程（5 评审维度：应答完整性/合规符合度/方案针对性/评分点覆盖/商务清晰度，门槛 7.5 上调）；CONTENT_DELIVERY_CONTRACTS「投标经理」契约（逐条对齐评分标准+资质不虚构标注待补+应答先结论+**废标风险项自查**——sepia 投标文体）；dispatch 归 writing 维度；中英文案齐。test_bid_flow 3 项

### 2026-09-22 04:01 第二十六班（批4 复跑：治理/安全/人机协同）

- 批4 轮换周期重跑：标的与上轮高度重合（微软治理工具包 6.3k→+1k、edict 16.9k、stop-that-shit 2.2k、tradememory 1.4k、Aegis/cordum/Octopoda 全部已入库），无新重大标的；baml 9.2k（agent 的编程语言）/plano 7.1k（AI 原生代理数据面）复查活跃
- A 组复查全活跃无增量事故

**✅ 本班落地**：程序性记忆 MVP（mengram「从做中学」借鉴）——LEARN_PROMPT 从「只提炼规避性教训」扩展为两类：①需规避的教训（来自问题）②**已验证有效的做法**（一次通过且高分时把「这次做对了什么」提炼成可复用步骤，title 以「做法：」开头）+「一次通过的高分运行优先提炼做法」指引；一次通过的运行本就进学习链（verdict 在场即学），此前 prompt 只问问题导致 clean-pass 学不到东西。test_procedural_learn 3 项（prompt 契约/pass 运行入库「做法：」/upsert 幂等）

### 2026-09-22 06:00 第二十七班（批6 复跑 + openhuman 深挖）

- **openhuman**（40k★）深挖完成 | 三支柱：🧠 记忆（**Memory Tree**：SQLite 评分 Markdown 树 + Obsidian 镜像可编辑，拒绝「向量汤黑盒」；100+ OAuth/5000+ MCP/90000+ Skills；**TokenJuice 工具输出压缩 80%**）/🕸️ 编排（工作流画布提案-人工审-保存；**分裂脑**：快反射 agent 分流 + 深推理核心派工舰队）/🔬 深研执行（15 消息通道+原生邮件 IMAP IDLE；每 run 可回放带真实 per-call 成本）| **与 CodeBee 对比**：我们有压缩（compaction）/预算/回放（usage 台账）/审批（待裁决）——差量=Memory Tree 的「评分树+可编辑镜像」与 TokenJuice 的「工具输出预压缩」（我们只压会话不压工具输出）| 借鉴方向（工具输出预压缩）进待深挖
- **a2aproject/A2A**（Google 官方，25,882★ 新巨型标）入库 | Agent2Agent 开放协议正式仓库——跨 agent 互操作协议从库实现升到官方主体 | 雷达
- **sia**（hexo-ai，2,159★）入库 | **Self Improving AI 框架**：自主改进任意 AI 系统的性能（模型/agent）| 与我们自学习闭环同向，方法待深挖 | 待深挖
- **llm-as-a-verifier**（3,253★）| 通用细粒度反馈框架（无需求文档也行）| 评审域参考 | 参考
- 复查：agentops 5.8k/AssetOpsBench 2.3k 活跃；批6 其余标的与上轮重合

**✅ 本班落地**：经验库 kind 标记落地（openhuman 记忆可视化借鉴）——持久层稳定 token（procedure/lesson，并行代理同域撞车统一收编：upsert 写 token、view() 翻译「做法/教训」+老数据派生兜底）；UI 教训卡「做法」绿徽章+悬停说明。test_lesson_kind 2 项

### 2026-09-22 08:01 第二十八班（批1 复跑：代码质量与评审）

- 批1 轮换复跑：主体标的与上轮重合（mira 327★/pr-af 633★ 复查活跃）。新锐：jev-review 196★（本地优先 MCP 持续代码质量评审）/reslop 118★（**专门评审 AI 生成的代码**——AI 代码的评审与人工代码不同域，角度新）/mr-agent 99★（GitLab MR 多代理评审+CI 自愈——禅道同域第三个标的）| 雷达
- A 组复查全活跃无增量事故

**✅ 本班落地**：read_file 头尾保留中段省略（**TokenJuice 差量清账**，openhuman 借鉴）——超 64KB 文本文件从「纯截头」改为头 44k+尾 16k+中段省略标注：日志/代码的报错与结论常在文件尾部，纯截头把最关键的信息丢了；尾段多字节残缺剥头防乱码；工具描述同步。test_read_elide 4 项 + 既有截断断言升级
