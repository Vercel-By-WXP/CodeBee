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
