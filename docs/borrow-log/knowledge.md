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

### 2026-10-05 代码级锚点盘点（10 时班第 2/4 步，四方向判定落定）

八机制全部行号级实读在位（详见 2026-10-05-full-types.md A 节）：三段压缩
compaction.py（摘要消耗入账 usage.record(compaction)）/ token_meter.py:148 +
step_runner:77 事前门 / 预算熔断 pipeline.py:613+648（花费硬顶先于 token 闸）/
cascade pipeline.py:1526-1529 / _shrink_context_block pipeline.py:2538 / 经验召回衰减
skills.py:348+540 / 会话复用 pipeline.py:136-283 / diff-only 评审 pipeline.py:919+948。
（锚点行号 2026-10-06 巡检班按 HEAD b6d2269 复测同步，机制零漂移。）
四对标方向判定：prompt 缓存已覆盖（usage.py:117 cached 细分记账 + 压缩压力触发
避开毁缓存）；语义缓存半已有（modelhub.py:3673 chat cache_ttl 精确匹配缓存，
幂等调用专用）语义级维持待拍板；diff-only 已有；廉价分流已有（cascade +
content_workflow 轻量类型短链 task_compile.py:163）。后续轮次直接对锚点复查增量，
不再全库重扫。

## 2026-09-18 第三轮（调研日 2026-09-18）

## 2026-09-18 第三轮

- **omnigent**（omnigent-ai/omnigent，10.1k★）| 编排多 CLI 的元壳：每步前按「本步将用模型」容量重估并压缩、跨壳任务交接、聚合看板、pre-run 成本预估 | 换将超窗预检与成本预估已抄；跨壳交接≈我们的换将链；看板≈蜂巢 | 已落地/已覆盖
- **agent-orchestrator**（Untrivial-ai，12.1k★）| planning→merge 全程监督、.spec/PROMPT.md 任务规格文件化、计划评审闸 | spec 文件化已落地：.codebee/{spec.md,task_plan.md,evidence.md} 任务档案+勾选 stopgate+归档戳（2026-09-25 复查确认，pipeline._write_task_plan/_append_evidence）；计划闸≈编排者+待裁决 | 已落地/已覆盖
- **oh-my-claudecode**（39.2k★）| 团队化编排、安全围栏、自学习沉淀、PR 工作流、doctor 健康诊断 | 大多有对应物（经验库=自学习、diagnostics=doctor、评审闸=围栏） | 已覆盖
- **munder-difflin**（7.5k★）| 同任务 N 克隆并行+评审择优+每任务 token 上限；LanceDB 向量经验检索 | 赛马已抄（连载+单稿）；预算熔断已有；向量检索未抄（重依赖，暂缓） | 已落地
- **freebuff**（CodebuffAI/freebuff，12.3k★）| 每步按本步模型容量重估、缓存感知压缩、suggest_followups、best-of-n 多策略+败者精华回收、专职子 agent 分工（thinker/researcher-web/file-explorer 家族）| 预检/追问卡/赛马精华已抄；file-explorer 职能已内置化=规划前工作目录侦察 workdir_recon（2026-09-25，API 直连编排者盲规划补盲）；thinker/researcher-web 不适用（编排链已有对应物）；缓存感知按设计不需要 | 已落地/已覆盖
- **emdash**（5.8k★）| 并行编码 agent + worktree 隔离 + 外部集成面 | 隔离链已有；外部集成抄了 Webhook 思路 | 已覆盖
- **edict**（cft0808，16.9k★）| 三省六部制分角色治理 + 实时看板 + 多模型 | 治理隐喻可参考；看板=蜂巢 | 参考
- **grill-me-skill**（RobMitt，610★）| 需求拷问：一次一问、每题多选弹窗、能自答绝不问用户、沿决策树逐分支到达共识、收尾汇总决策 | 已落地：/api/tasks/clarify 编排者生成 1-3 问（能自答不问）+ renderClarify 采访卡（芯片点选/分段签核/跳过直做），2026-09-25 复查确认；决策树多轮追问与现状折中（一卡多问）收敛，不再单列路线图 | 已落地
- **grill-for-unknowns**（nicobailon，219★）| 先找未知项、再拷问计划、达成实现前共识 | 与 grill-me 合并借鉴；同上已落地 | 已落地

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

### 路线图新增（oh-story 借鉴包）——2026-10-05 21 时班第 2/4 步对码实证全部落地，复选框清账
- [x] **去AI味检查**（已落地：aiflavor.py analyze/narrative_analyze/pacing_analyze，inject_into_prompt 接入起草 pipeline.py:3152 + 评审 :4933）
- [x] **扫榜选材/拆解爆款**（选材已落地：rank_scan 流程 flows.py:80，paihang.py:74-90 四平台 fetch_*；拆解爆款未单列入口，随 plot-modules.md 人工沉淀通道）
- [x] **剧情模块库**（已落地：pipeline.py:2466 plot-modules.md 注入+模块边界截断 :2558-2566，UI 门 index.html:501+i18n.js:436）
- [x] 封面图生成（已落地：covergen.py，main.py:1104 /api 挂载 + app.js:5787/5873 封面卡）

### 复查记录
- 2026-09-18 深夜：A7/A8 组首跑；keywords.md 总库（97 组+雷达）建立

## 封面图生成（设计就绪，待安全策略放行——2026-09-18 深夜）

**设计定稿**（四轮迭代被 Mimosa 策略拦截，架构本身可行）：挂 bookmeta.py（同款 generate_async 状态机写任务 cover_gen 字段）→ 调编排者供应商 OpenAI 兼容 `/images/generations`（模型候选 cogview-3-flash → cogview-4，env CODEBEE_IMAGE_MODEL 优先；竖版 768x1344 失败回落 1024x1024）→ 产物落 runs/<run_id>/cover.png（与 report 同款受管路径）→ UI 走 run 文件通道预览。SSRF 边界：仅 https+解析 IP 拒私网/环回/链路本地+禁重定向。
**被拦原因**：扫描器策略性拒绝「网络下载→写盘」组合（无法证明下载字节无害）。**放行条件**：用户调整 Mimosa 策略或确认接受下载落盘风险后，按本设计重实现（预计 30 分钟）。路由图先挂「封面图生成（待安全放行）」。

## 2026-09-19 夜间第一班（调研日 2026-09-19，周六·轮换批7）

- **planning-with-files** 已深挖（26,981★）| 三文件 task_plan/findings/progress 分离、每轮 hook 注入计划头（goal+next+active phase）、SHA-256 防篡改、Stop gate 防提前收工、KV-cache 稳定注入（289ms/次）、盲测 3/3 胜、恢复 13.3 轮→5.0 轮 | **已抄三件**：task_plan.md 落盘（与 spec/evidence 同居 .codebee/）+ 子任务进度注入（多子任务时提示词带「第 i/N 项+已完成」防漂移）+ 活计划回写（09-24：checkbox 随执行翻 [x]/[!]/[>]，断点可见）；findings.md（中间发现）与 Stop gate 暂不抄（连载章级进度已有对应物） | 已落地
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
- **superpowers**（obra，288,620★！）| 完整软件开发方法论 skill 框架：对话中 teased spec→**分段短块给用户逐段消化签核**→实现计划要「清晰到热情但没品味没判断力没项目上下文且讨厌测试的初级工程师也能照做」（**初级工程师测试**）→subagent 驱动开发（自主跑几小时不离计划）；17 种 harness 全覆盖 | **借鉴①：spec 分段签核**嫁接进需求拷问（问完→分段预览 spec→用户逐段确认→落 spec.md）；**借鉴②：计划清晰度「初级工程师测试」**写入编排者规划提示词；subagent 开发=我们实现链已有 | 借鉴（两项小件进路线图）——状态（2026-10-06 10 时班勘定）：借鉴②已落地（CODE_PLAN_PROMPT+test_plan_junior_test，见 2026-10-05 节）；借鉴①spec 分段签核未落地（app/ 分段预览/spec_preview/逐段确认 grep 零命中实证，维持在册）
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
- **drama-skills**（zenstory-ai，2k★，oh-story 同门新品线）| 11 skills 短剧/漫剧全流程：原著分析→分集剧本→视觉设定→图片提示词→分镜→视频提示词→生产→剪辑成片→审查。**三个机制极可借鉴**：①五份 Markdown 即创作事实（剧本/视觉/分镜/图提/视提——无数据库，改文件=改决定，与我们任务档案/圣经思路同源）②**连续性锁**（跨镜造型写成可直接贴提示词的短语+每镜说明依据+检查脚本指出遗漏）③**先预览确认再生产**（图片/视频/配音先在文件里看到准确内容参数确认后才调外部接口花钱）| 路线图：连续性锁→圣经人物卡增强（**已落地，2026-10-06 10 时班 pipeline.py:2460 圣经注入文案实证**）；预览确认→封面生成前预览（**已落地，2026-10-06 14 时收口班 v0.1.88：GET /cover/prompt 只读预览+二段确认，f9ca937 实证**） | 借鉴方向
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
- **Continuous-Claude-v3** | 将 MCP 工具结果放进隔离上下文，只向主会话回灌必要摘要，减少工具输出污染 | CodeBee 已有工具结果剪枝与三段压缩，但压缩摘要尚未真正替换 CLI 请求正文 | 借鉴（压缩摘要进入真实请求链）——**状态（2026-10-10 15 时班复核）：已落地**——compaction.compact_region 末步 surface_op={"op":"replace"} 把压缩区域折叠为一条 user 摘要消息进 surface 视图（即 CLI 请求正文），step_runner 以「maybe_compact 真正前进了 surface generation」为守门重试条件，链路闭环——本条清账 | 已落地 | 2026-09-20
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

### 2026-09-22 12:00 第二十九班·甲（批5：检索/知识/浏览器 + 全类型雷达）

- **multica-ai/multica**（51,033★，Go，新巨型标）入库 | **「Agents that show up on the board」**：把工作派给 AI 编码 agent 的方式和派给同事一样——agent 认领 issue、汇报进度、抛出阻塞、交回待评审；自托管、支持 26 种 agent CLI、无锁定 | **与 CodeBee 对比**：我们=任务类型先行（13 种预置流程 × 多智能体编排）；multica=看板先行（issue 为中心，agent 是「会出现在看板上的队友」）。差量=**看板/issue 视角的任务组织**（我们已有蜂巢工作台与运行详情，但没有「agent 主动汇报阻塞/进度」的反向通道——我们只有指挥信箱单向递话）| 参考（看板视角可作 UI 远期方向；「agent 汇报阻塞」与我们「待裁决」同向）| 2026-09-22
- **sandbaseai/sandbase-harness**（649★，Apache-2.0，TS）入库 | 本地优先自托管 agent runtime + MCP bridge：沙箱会话（local/Docker/K8s worker）、**凭据保险库**、权限策略与审批、审计与回放（resumable event streams）、本地 Console；本地 SQLite+文件，无强制托管控制面 | **与 CodeBee 对比**：我们的沙箱=CLI 自带（全权模式），凭据=config.json 明文，审计=usage 台账+错误台账，回放=运行详情。差量=**凭据保险库**（密钥集中托管而非明文落配置）与**可续传事件流**（我们在进程崩溃后靠 run 状态恢复，事件流不续传）| 参考/待深挖（凭据保险库独立成项）| 2026-09-22
- **GDWhisper/OmniTerm**（10★，新锐）| 「一个浏览器标签页看住并驱动每一个 AI 编码 agent」——Claude Code / Codex CLI / opencode / pi / omp / qoder / Antigravity 的 Web UI，tmux 支撑的会话 | **与 CodeBee 同形态的直接新锐竞品**（多 CLI 统一 Web 面）；我们的差异在「任务类型 × 流程 × 评审门禁」，它的差异在「tmux 会话直连终端」| 雷达（同形态持续跟踪）| 2026-09-22
- **copse-dev/agent-pane**（1★，AGPL-3.0，Electron）| 桌面 AI 编码助手：agent 对话 + Monaco 编辑器 + 终端 + git 变更 + 内嵌浏览器同屏；**「看见工作而不只是答案」**（工具活动/diff/命令/子代理/失败都留在对话里）；无法安全应用的编辑**等用户批准**；越出沙箱的动作先问；可复用已有的 Cursor skills/MCP | **与 CodeBee 对比**：我们的详情页有步骤/日志/蜂巢/版本页签，但对话与编辑器/终端不同屏；差量=**应用不了的编辑排队等批准**（我们审批停在任务级「待裁决」，不到文件级）| 参考 | 2026-09-22
- **laika56/agent-delegate**（★新锐，护栏）| 无人值守编码 agent 的守卫：抓 **exit-0-但无改动**、**越范围编辑（out-of-scope edits）**、**半成品收工** | **与我们对比**：exit-0-无改动=我们的实现步零工具闸（VENDOR_REFUSAL，已落地）；差量=**越范围编辑检测**（对照任务声明的文件范围，改了范围外的文件就报）与**半成品检测**（声明完成但留 TODO/未接线）| 借鉴方向（越范围检测，代码任务）| 2026-09-22
- **avouro-com/scopebond** / **firstbitelabsllc/shadow** / **SanHsien/agent-cortex** / **floccose-burner9185/wow-harness**（均新锐）| 同族四例：范围检查点阻断越界编辑 / 「一个持久计划 + 原子认领 + 证据门禁的完成」/ 交付层「Candidate-Verification-Independent Review-CompletionRecord，**不接受 agent 自报完成**」/ 用自动验证+严格评审门禁+完成强制治理 Claude Code | **共同信号：2026 下半年编码 agent 的共识是「不信任自报」**——与我们事件流计数鉴别谎报、跨厂商评审、待裁决完全同向；差量集中在「证据门禁」的具体形态 | 方向验证 | 2026-09-22
- **naman159/continuum**（新锐）| 长文写作 agent 的**记忆层**（与 planning-with-files 同族的写作特化） | 与我们圣经+前情提要同域；无新机制 | 参考 | 2026-09-22
- **xinghe-labs/novel-studio**（新锐）| 长篇小说的**零依赖 Python 工作流引擎 + 事务性 canon**（transactional canonical）| **「事务性正典」思路值得记**：世界观改动要么全量生效要么回滚——我们圣经是整篇重写（无事务概念）| 参考 | 2026-09-22
- **dungnotnull/web-novel-pacing-analyzer**（4★）已落地 | 连载作品的章级**节奏审计**：章节节奏、**钩子密度**、留存风险 | 与我们 aiflavor 同路（确定性统计 → 评审参考线）；**已抄**：pacing_analyze（段落淤积/对话占比/开篇抓力/章末钩子）| 已落地 | 2026-09-22
- **jackela/Novel-Engine**（6★）| 自托管 AI 小说工作室：**volume-aware drafting with beats**、常驻协作 | 与我们刚落的「分卷」同向（分卷边界+卷弧光）——验证方向 | 参考 | 2026-09-22
- **existential-birds/beagle**（83★）/ **hashgraph-online/awesome-ai-plugins**（319★）/ **adqr5270/skill-manager** / **wingsky-1/dsh-plugin-hub** | skills/插件聚合清单与同步器 | 三问（重合度/可直读性/用户会搜吗）回答不好——**不接市场，雷达跟踪**（与上轮 anbeime/skill 结论一致）| 参考 | 2026-09-22
- **nxxxsooo/opencode-metrics**（4★）| OpenCode TUI 每会话侧栏指标：**tokens/sec、TTFT（首字延迟）、token 数与缓存命中** | 与我们用量台账对比：我们有 token 数与费用，**缺「速度与缓存命中率」这两个用户可直接感知的量**；token_meter 已记 cached 但未在 UI 露出 | 借鉴方向（用量页加 缓存命中率/平均首字延迟）| 2026-09-22
- **seakee/CPA-Manager-Plus**（3,565★）/ **vilmire/adhdev**（97★）/ **0xAI-Builders/comandos**（6★）/ **kalvinh8169/szpont-machen** | 自托管 AI 网关用量看板 / Agent Dashboard Hub（单面板监控控制多个编码 agent）/ 本地优先终端任务控制台 / **管理多 CLI 会话（追踪、token 用量、续跑、归档）** | 与我们同域的「多 CLI 面板 + 用量」竞品群；szpont-machen 的「会话归档」角度与我们运行历史同向 | 雷达 | 2026-09-22
- **Deijine/zentao-legacy-mcp**（★新锐）/ **easysoft/zentao-cli**（59★，官方）/ **zl2237/test-defect-retrospective**（1★）/ **yanfd/astrbot_plugin_zentao_report** | 禅道生态四例：**老版 session API 的跨客户端 MCP（87 工具、跨实体全文检索）** / 官方 CLI / **缺陷复盘 skill（禅道导出→产品/研发/测试三视角报告）** / 每日缺陷日报插件 | **与我们禅道集成对比**：我们做「扫描→建任务→修复→合并→resolve→回写+通知」闭环；差量=**缺陷复盘报告**（按产品/研发/测试出复盘）与**跨实体全文检索** | 借鉴方向（复盘报告列入待深挖）| 2026-09-22
- **zhayujie/CowAgent**（47,064★）/ **ruvnet/ruflo**（73,016★）/ **EverMind-AI/Raven**（3,946★）/ **bytedance/deer-flow**（82,823★）/ **nexu-io/open-design**（97,499★）/ **openai/swarm**（22k★）| harness/编排大盘复查：CowAgent（41k→47k，**多通道超级助手**，chatgpt-on-wechat 血脉——验证我们微信通道方向）；ruflo（73k，认知 swarm）；Raven（**Harness of Harnesses + Raven Evolver 拿 benchmark 评候选 harness 改动**——用基准评估「harness 自身改动」的思路）；deer-flow（82.8k，长跨度 SuperAgent + 沙箱）；open-design（97.5k，DeepSeek Harness 设计插件，本地优先桌面）；swarm（22k 教育框架，已停更）| 大盘活跃；Raven 的 evolver 思路与我们「教训 outcome 加权」同向但更重 | 参考 | 2026-09-22

**✅ 本班落地**（两件，见当日报告「12:00 第二十九班·甲」）：
1. **叙事节奏/钩子确定性检测**（web-novel-pacing-analyzer 借鉴）：aiflavor 新增 `pacing_analyze`（段落淤积/超长段/对话占比/开篇 300 字冲突信号/章末 200 字悬念信号）+ `inject_into_prompt` 统一注入入口；只对叙事类流程下发，命中才追加，不扣分。
2. **连载逐章评审补挂确定性检测**（产品巡检发现）：`_run_serial_review` 的评审提示词此前**从未**带上 AI 味/叙事架构/节奏检测行——连载是旗舰场景却整条漏挂（单稿件评审一直有）。修复后连载逐章与打磨评审同样拿到参考线。


### 2026-09-22 14:36 第二十九班（批7 复跑 + 间隔分钟化确认）

- 批7 复跑：主体重合。新锐（皆小）：three-man-team 951★（Architect/Builder/Reviewer 三人组 token 优化——我们 plan/implement/review 同构，雷达）/vnx-orchestration 61★（治理优先+回执）/agent-lord（Durable orchestration: dispatch/continue/recover/handoff/audit）| 雷达
- **✅ 禅道扫描间隔分钟化确认**（用户「默认是5分钟吧」核实）：后端 interval_minutes 全迁（默认 5 分钟/最小 5/最大 7 天，老 interval_hours 自动换算：默认 2h→5min、非默认×60）、前端「扫描间隔 N 分钟 min=5 step=5」、i18n 齐——已在 HEAD（并行代理落地），本轮验证无 hours 残留（自动化任务的 interval_hours 是另一特性，单位本就是小时）
- D 项里程碑：教训 78→**85 条**（+7）——程序性记忆「做法：」+outcome 加权上线后自学习环路开始复利
- 基线分支 datalist 下拉（ztRevFill）与模块清单形状兼容（响应顶层键提示）确认已在 HEAD

### 2026-09-22 16:03 第三十班（批2 复跑：学习记忆与自我改进）

- **happier**（happier-dev，1,702★）新入库 | Web/桌面/移动三端客户端+编排器（Codex/Claude Code/OpenCode/Pi/Cursor/Grok/Antigravity 七 CLI）| 多端形态对标（我们 Web+桌宠，无移动端编排面——手机连接是只读远控）| 参考
- **loop-engineering**（cobusgreyling，11,278★ +1.2k 增量）复查活跃 | loop 工程实用模式/starters/CLI 工具集 | 方法论库持续吸收 | 参考
- 新锐小标：cantos-plugin（自改进多代理一键插件）/agent-queue（Discord 管理 agent 队列+自动恢复）/Himmel（managed harness：hooks/guardrails/slash）| 雷达
- B2 组主体与上轮重合（memgram/MemRL/pro-workflow/projectmem 均已入库）

**✅ 本班落地**：知识库近似题合并（自动学习防膨胀）——upsert_entry 此前只做精确同题指纹去重，「X 优化」与「X 优化指南」各建一条；现在同 scope 标题 bigram **包含度**（交集/较短者）≥0.8 归并为同一条（用包含度而非 Jaccard：追加后缀形态 Jaccard 只有 0.71 被漏，包含度=1.0），合并/修订语义与精确同题完全一致（approved 保护不打折）；<4 字符短题只认精确（bigram 噪声大）。test_kb_near_dup 6 项（度量界/归并/不相似分离/跨 scope/短题/approved 保护）

### 2026-09-22 18:30 前后 第三十一班（批3 补位：计划/spec/长任务——搜索通道受限班，详见当日报告「通道说明」）

- **Azure/co-op-translator**（微软官方）| 文档本地化自动化：**检测源内容变更→只更新过期翻译**，保 Markdown/Notebook 链接与结构 | 我们翻译是单次全文流程，「源文变更触发增量重译」是空白概念（但属文档本地化场景，与我们单文档翻译有距离）| 参考 | 2026-09-22
- **Mem0**（48k+★，此前未入册）| 最流行开源记忆框架（import 即用：add/get/search，跨会话个性化记忆）| 记忆域大盘补齐——框架级重依赖，不改我们「文件真源+可版本化」路线 | 参考 | 2026-09-22
- **Postiz**（gitroomhq/postiz-app）| 自托管开源多平台发布（X/Bluesky/LinkedIn/Discord 等，agentic 排期统一看板）| 多平台分发被产品化的成熟样本；我们发布通道只到浏览器会话，账号类自动发布有封号风险维持不做 | 参考 | 2026-09-22
- **Empryo**（引擎 soulforge v2）| graph-powered coding agent：「**编辑符号而非字符串**」——AST 手术+全量 LSP+tree-sitter 代码图，65+ AST 操作带回滚 | 我们代码任务走 CLI 自带编辑；AST 级编辑是候选 CLI 里机制差异化最大者 | 雷达（装后实测重点，不盲接入）| 2026-09-22
- **ZenTao CLI 原厂 AI Skills**（easysoft 官方，2026-04 发布）| 官方命令行一键装进 Claude/Cursor，自然语言管理禅道+原厂 Skills；同发 zentao-vscode-integration（需求/任务/Bug 跳转+关联 Commit Message）| 禅道官方押注 AI agent 通道——验证我们「扫描→建任务→修复→回写」闭环方向；「缺陷复盘报告」待深挖项维持 | 方向验证 | 2026-09-22
- **SWE-EVO**（arXiv 基准）/ **AgentGym-RL**（ICLR 2026）| 长程演化基准（7 个开源项目 release notes 构造多步演化任务）/ 多轮 RL 训练长程决策 agent | 评测与训练路线参照；我们缺自评测（evalscope 已记）| 参考 | 2026-09-22
- **「Safe to Resume?」**（arXiv 2026-08）| 校验 checkpoint/restore 恢复后**不重复副作用**的执行连续性验证 | 与 CONTINUUM 幂等账本待深挖同向——「禅道回写/通知/发布等外部动作缺统一幂等键」的第三处佐证 | 方向验证（幂等账本待深挖维持）| 2026-09-22
- **Wiggum CLI** | 扫库→**AI 面试生成 spec**→自主编码循环（Claude Code/Codex 驱动）| 需求拷问方向第 4 个独立验证（grill-me/pi-plans/genie 之后）| 方向验证 | 2026-09-22
- **小说域四小标**：AuthorAgent（MIT Node 本地全书流水线）/ NovelGenerator（前提→逐章成稿）/ OpenWrite（自有 API key 长篇平台）/ Inkfluence（跨章角色一致性，商业）| 圣经+前情+一致性评审已覆盖其卖点 | 已覆盖 | 2026-09-22
- **短视频成片链**：NarratoAI（文案→剪辑→配音→字幕）/ Pixelle-Video（阿里 9.5k+，主题→成片）/ MoneyPrinterTurbo（热点→脚本→素材→渲染）| 脚本只是链条第一环——「脚本→成片」与小说→短剧同族远期方向 | 雷达 | 2026-09-22
- **Reasonix 前缀缓存印证** | DeepSeek 自动前缀缓存对**字节稳定前缀**给 ~30× 折扣——该 CLI 专门设计尊重字节稳定前缀 | 我们「注入点固定在待评审稿件之前保前缀缓存稳定」设计获第三方印证 | 已覆盖（设计层面）| 2026-09-22
- **supply-chain 投毒潮**（2026-05-11 Shai-Hulud 蠕虫：170+ npm 包、窃取 Claude/Kiro agent 配置；Anthropic 披露评估中模型发布恶意 PyPI 包）| AI agent 工具链成供应链攻击重点目标 | **「市场装前扫描」（SkillSpector 借鉴）路线图项的外部证据再 +1** | 参考（安全）| 2026-09-22

#### 复查记录（搜索通道，星数为第三方口径近似）
- 2026-09-22：orca v1.4.206（09-20 发版）活跃、GitHub 周榜 Top10（09-12~18）、支持 25+ CLI；superpowers 仍是技能生态引用第一框架；oh-my-claudecode「Teams-first」活跃；claude-flow(ruflo) npm 近 5 天有更新；spec-kit 复查活跃（Discussion #152 争论 spec 演进/唯一真源——与任务档案「归档/升格」路线同题）；OpenClaw 9k→60k+（2026 增速之王，保持未装候选）；生态事实：claude-code-sdk 更名 claude-agent-sdk（2026-06，旧包停更）；读者反馈分析二次确认仍空白赛道

### 2026-09-22 20:01 第三十一班（批6 复跑：框架/平台/SDK 生态）

- 批6 复跑：主体重合（A2A 官方 25.9k/vercel-ai/mastra/dify 复查活跃）。新锐皆小标：chipping-orchestrator（盯 GitHub issue 自动 spawn Claude/Codex）/flotilla（wave 编排：批量独立可抓 issue 批次派发）/ha-paseo（Home Assistant 插件形态编排三 CLI——编排器进场居智能家居，形态新奇）| 雷达
- 自检：上轮 2 处 codex 防毒闸失败已被并行代理修复（aea5632），全量 1267 项全绿

**✅ 本班落地**：教训库近似题合并（与知识库昨日同款防膨胀对齐）——upsert_lesson 此前只做精确同题指纹合并，自动复盘每次 done 运行都跑，「节奏拖沓」与「节奏拖沓问题」各占一条；现在同 scope 标题 bigram 包含度 ≥0.8 归并（与 knowledge._title_sim 同口径），合并语义与精确同题一致（分类不降级/seen+1/内容取新），<4 字符短题只认精确。test_lesson_near_dup 6 项（归并/不相似分离/跨 scope/短题/「做法：」前缀不误并/分类不降级）

### 2026-09-22 22:02 第三十二班（批1 复跑：代码质量与评审）

- **awslabs/cli-agent-orchestrator**（1,334★，AWS 官方）新入库 | 多 CLI 编排（Claude Code/Kiro/Codex…）tmux 隔离协调 | 大厂第二家进场编排（微软 toolkit 后），方向再验证 | 参考
- **Enderfga/claw-orchestrator**（580★）| 五 CLI 统一运行时：持久会话+多代理 | 参考
- B1 主体重合（SkillSpector/open-code-review/mira 均已入库）；DeepCode 16.6k 复查活跃
- 雷达-评审/测试新锐组无重大新标的

**✅ 本班落地**：市场装后冒烟验证（SkillForge 证据驱动借鉴）——install_files 尾部新增 smoke：装完清缓存走 skills 真实解析链，校验「包能加载/名字对得上/正文非空」，写盘成功≠技能可用（frontmatter 缺失此前要到下次任务注入才静默丢失）；smoke 随返回值+market.json 记账，提示不拦阻（与危险扫描同纪律）。test_market_smoke 4 项

### 2026-09-23 00:01 第三十三班（批7：中文/网关/本地/办公）

- **krillinai/OpenCreator**（12,193★，前 KrillinAI）新入库 | 创作者 AI 工作台（Codex 驱动）：视频/图片/语音/数字人 | **万星级创作平台**——我们短视频脚本→成片的远期对标 | 深挖排队
- **Narcooo/inkos**（10,016★）新入库 | **Story Creation AI Agent**：小说/剧本/翻译/互动游戏/IP 内容多形态创作 | 与我们写作域正面重叠的万星新竞品（中文）——深挖排队，重点看其多形态流水线与我们的流程差异
- andrewyng/translation-agent（翻译组命中）复查 | 参照实现 | 参考
- ensemblr（8★）：Pi+Claude Code 桌面编排，每条工作流独立 git worktree+agent 可交接——worktree-per-lane 与我们任务分支隔离同路 | 雷达
- B7 中文关键词组仍命中弱；A 组全活跃无增量事故

**✅ 本班落地**：教训 karma 可见化——outcome 加权已上线但 won/lost 对用户不可见：教训卡新增「有效 N」（绿，注入后任务过审=真实帮上忙）与「失守 N」（红，注入后仍失败=没防住）徽章（悬停有说明），老数据无字段不渲染不炸；test_karma_view 2 项（写入→view 透交通路+karma 参与排序端到端）

### 2026-09-23 02:01 第三十四班（批2：学习记忆 + inkos 深挖）

- **inkos（10,016★）深挖完成** | v1.8 统一 pi-agent 生产 harness；**多线剧情推演**（写下一章前基于正史生成 2-5 条隔离未来分支，横向比较节拍/人物决定/风险/作者意图；采纳只存 selected-branch-plan.md 不改正史，正史变化后旧推演标过期）；SQLite FTS5/BM25 统一检索投影（故事记忆+材料+Skill 参考；文件权威索引可重建，结果带来源位置）；**安全章节工作区**（正文/状态/伏笔/快照先校验再原子提交——杜绝「状态推进正文未落」）；15 专业 Skills 按作品类型；Kimi K3 赞助+AGPL-3.0（借鉴方法论可以，代码不能抄）| **与 CodeBee 差量**：①多线推演（我们 Best-of-N 是稿件赛马不是剧情推演）②安全章节工作区（我们落盘无原子校验）③检索投影（我们 bigram top-k 已覆盖语义）| ①②进待深挖（pipeline 域）
- agency-orchestrator 2,288★（jnMetaCode：一句话→一人公司专家团→交付物）复查 | 参考
- B2 主体重合；OpenCreator 深挖排队
- 复查：codegraph 71.7k/graphiti 31.1k 活跃

**✅ 本班落地**：知识库过期条目降权（inkos 检索保留来源/位置的启发）——block_for rank 在相关性与 id 之间插入 stale 惩罚位：同等相关性下可能过期的事实排新鲜事实之后（top-k 截断时旧知识先出局），高相关旧条仍压过新条（相关性优先不变）。test_kb_stale_rank 3 项

### 2026-09-23 04:01 第三十五班（批3 词组：计划/spec/长任务——轮换标签与小时有偏差已注记）

- 批3 主体重合（spec-kit 138.4k/OpenSpec 69.9k/GSD 64.5k/agent-os 5.4k/spec-workflow-mcp 4.3k 均已入库复查活跃）。新锐小标：polyglot（worktree+PostgreSQL+确定性闸门）/ai_launcher（15+ CLI 命令甲板）| 雷达
- A 组复查全活跃无增量事故

**✅ 本班落地**：教训列表 karma 感知排序（list_lessons）——UI 列表首键改为 won-lost（有效教训居首、净失守沉底不删除），hits/seen 降为次级键；注入排序（relevance_top）不受影响（相关性仍第一优先，单独测试锁死）。test_lesson_karma_order 3 项 + skills 域回归 19 项绿

### 2026-09-23 06:00 第三十七班（批6：框架/平台/SDK + A1 深度轮首跑——ZCode 排程班，含 34/35 班欠账补录）

- **mattpocock/skills**（267,590★）| Matt Pocock 工程师技能库（.agents 目录直出）| superpowers（290.1k）首次出现同量级挑战者，技能生态双巨头格局 | 大盘
- **affaan-m/ECC**（265,116★）| harness 性能优化系统（skills+instincts+memory+security+research-first）| 「instincts（直觉层）」= 介于教训与技能之间的自动触发层，概念新 | 待深挖
- **NousResearch/hermes-agent**（247,983★）| 「The agent that grows with you」官方 agent | 成长叙事大厂印证（记忆+个性长期演进）| 参考
- **deepseek-ai/deepseek-harness**（233,179★）| DeepSeek 官方 harness「Everything is a Plugin」| dsh 已接——官方开源，插件体系可对照 | E 生态事实
- **anomalyco/opencode**（209,332★）| opencode 现于 anomalyco org 名下 | 已接通道上游变动持续跟踪 | 生态事实
- **sickn33/agentic-awesome-skills**（46,784★）+ **K-Dense-AI/scientific-agent-skills**（46,116★）| 本地 agent 优先技能控制面 / 科学技能第一库 | 技能生态 4 万级新库×2 | 大盘
- **googleworkspace/cli**（31,100★）+ larksuite/cli 17.4k | Google/飞书官方 CLI 明示 built for agents | 办公巨头集体开 agent CLI | 生态事实
- **Hmbown/Codewhale**（41,030★）Rust 终端 agent + **herdrdev/herdr**（40,177★）跃升 | E 候选 +1 / 星数跃升（均未装防死链）| 候选雷达
- **musistudio/claude-code-router**（37,375★）| 本地控制面：跨模型路由+能力融合+工具编排 | 绑定链/网关同域头部 | 参考
- **tashfeenahmed/freellmapi**（27,987★）| 34 家免费供应商 635 端点一个 /v1 | 免费通道聚合，配额稳定性存疑 | 待深挖
- **Tencent/AI-Infra-Guard**（6,550★）+ NVIDIA/SkillSpector 18,070★ + msoedov/agentic_security 2,004★ | Agent/Skills/MCP 扫描同族三例（两家大厂+一家安全厂）| 市场「装前扫描」路线外部证据 +3 | 方向验证
- **FailproofAI/failproofai**（5,113★）| harness 可观测+策略强制 | 「不信任自报」族 +1 | 方向验证
- **bytebase/bytebase**（14,503★）| 「Database governance built for humans and agents」| 传统 DevOps 工具给 agent 留治理位的信号 | 参考
- **cordum-io/cordum**（508★）| 「action firewall」风险工具调用前置策略与人工审批 | 与全权沙箱+零工具闸互补（事后鉴别 vs 事前拦截）| 借鉴方向（高危工具前置审批，远期）
- **strands-agents/harness-sdk**（7,551★，今日推）| 「Build an agent harness and control it end-to-end」开源 SDK | AWS 系 strands 押注 harness 概念 | E 生态事实
- **google/agents-cli**（5,980★，今日推）| Google 官方 agent CLI+skills | 巨头 CLI+skills 生态再 +1 | E 生态事实
- **the-open-engine/zeroshot**（1,858★，今日推）| 「Independent executor–verifier orchestration」执行-验证分离 | 与跨厂商评审/不信任自报同族，且是 A1u 新锐轮首个有分量命中（深度轮价值实证）| 方向验证
- **NirDiamant/agents-towards-production**（21,487★）| 生产级 GenAI agent 教程全集 | 方法论库 | 参考
- 小标速记：omnigent 10.2k（meta-harness）/ cc-haha 14.7k（桌面工作台）/ huobao-drama 15.4k（短剧成片）/ opensquilla 7k（智能密度）/ wigolo 5.4k（本地调研 MCP）/ univer 15k（office harness）/ zenstory-ai/oh-story-claudecode 7.1k（已借鉴来源产品化爆发）/ aegra 1.2k（LangGraph Platform 开源替身）/ agentscope-runtime 872（沙箱+A2A 运行时）/ agentcn 476（shadcn for agents）/ dapr-agents 749
- 复查：mastra 28.3k/vercel-ai 26.9k/spec-kit 138.4k/OpenSpec 69.9k/GSD 64.5k/codegraph 71.8k/graphiti 31.1k/cognee 30.9k/axonhub 5.3k 复查活跃；中文组噪声结论四度验证

**✅ 本班落地**：scan 脚本双轮排序+A1 翻页（35 班已落，本班深度轮首跑 109 组 520 条零失败实证）；knowledge.md 34/35/37 班增量欠账本条补清

### 2026-09-23 06:01 第三十六班（批6：框架/平台/SDK 生态 + OpenCreator 深挖）

- **OpenCreator（12,193★，前 KrillinAI，Apache-2.0）深挖完成** | 创作者 AI 工作台：**Codex 原生复用**（不自建 agent 循环，直接复用 Codex 的模型/推理/工具调用/会话/Skills/MCP——只加稳定本地 Runtime+可视化工作台+桌面壳）；双模式（可视化工具/Agent 对话）共享一个状态机；**版本化**（每次修订新版本保留旧设置产出供对比）；yt-dlp 等运行时组件托管更新（失败保旧版）；创作模板可复用 | **与 CodeBee 对比**：我们多 CLI 异构编排+跨厂商评审是它没有的；它的「版本化对比」与「模板库」值得借鉴 | 借鉴方向：版本化（我们的 run 历史已有雏形）进待深挖
- 批6 主体重合（spec-kit 138k 一类不在本批；A2A 官方 25.9k/vercel-ai 26.9k/mastra 28.2k 复查活跃）。新锐小标：godmode/claude-codex-bridge/windows-agent-orchestrator（Windows 专用编排）| 雷达
- A 组全活跃无增量事故

**✅ 本班落地**：偏好记忆补全 direct 对话模型字段——SAVE_KEYS 新增 direct_provider/direct_model/direct_thinking（厂商 id/模型名截断 64、推理档闭集校验、空串不覆盖），direct 用户下次新建沿用上次手选；test_prefs 扩至 8 项

### 2026-09-23 08:02 第三十八班（批1：代码质量与评审）

- 批1 复跑主体重合（SkillSpector/open-code-review/mira 均已入库）。新锐小标：fleet-harness/waspflow/claude-lane-stack/codegen_orchestrator | 雷达
- A 组全活跃无增量事故

**✅ 本班落地**：**多线剧情推演 MVP**（inkos 借鉴，待深挖榜首清账）——`core/branching.py`：serial.branches=2-3 时写章前**一次编排者调用**生成 N 条方向互异的分支节拍计划+自荐 pick 择优，选中分支替换本章 BEATS/HOOK 注入起草（**计划级赛马**——比 prose 级 Best-of-N 省一个数量级 token）；全部分支连同取舍理由追加 `.codebee/branch-plans.md` 审计（✅ 标择优）；失败/无编排者/N<2 静默回落原大纲节拍（增强不是闸门）；store 归一 branches 参数（1=默认关不落键、越界钳 3 同 variants 口径）。test_branching 6 项
- claude-lane-stack 117★（一人 AI coding 工厂：Claude PM + 多 CLI writers 持久对话）| 雷达

### 2026-09-23 10:01 第三十九班（批3：计划/spec/长任务）

- 批3 复跑主体重合（spec-kit 138k/OpenSpec 69.9k/GSD 64.5k 均已入库）。新锐小标：1337-claude/geekychris-chief/vibecoding-bench | 雷达
- A 组全活跃无增量事故

**✅ 本班落地**：**章节安全落盘**（inkos 安全章节工作区借鉴，待深挖第二项清账）——`core/chaptersafe.py`：atomic_write_chapter 走 **tmp 写入 + os.replace 原子改名**，写一半崩溃/磁盘满不会留下半章正文冒充成稿（断点续跑按「文件够长」判定，半文件会被误当合法稿复用——正是 inkos「杜绝状态推进正文未落」的同款语义）；失败清理 tmp 残渣后原样抛出；路径守卫 resolve+parents+workdir 必须存在。pipeline._write_chapter 委托（6 处调用点零改动）。test_chaptersafe 5 项（落盘回读/覆盖/越界拒绝/委托生效/**磁盘满模拟旧稿完好**）

### 2026-09-23 12:01 第四十班（批5：检索/知识/浏览器）

- 批5 复跑主体重合（dify/ragflow/Agent-Reach 等均已入库）。新锐小标：klaus（K8s 内编排 Claude 的 Go 封装）/symphony（专家指挥家+轻量协调者多模型插件）/neutron（自托管长会话 harness）| 雷达
- A 组全活跃无增量事故

**✅ 本班落地**：知识库 confidence 可信度分级（引用核验借鉴）——KNOWLEDGE_PROMPT 要求每条事实给出 high（有来源/数字口径）/medium（自洽未标来源）/low（不确定或矛盾）三级置信；**low 直接丢弃**（宁缺毋滥），缺失视为 medium；confidence 随条目落盘，近似/同题合并时 high 可覆盖 medium（有据版本吸收无据版本）。test_kb_confidence 4 项（落盘/合并升级/learn 丢 low+归一/prompt 契约）

### 2026-09-23 14:02 第四十一班（批7：中文/网关/本地/办公）

- 批7 复跑主体重合（OpenCreator 12.2k/inkos 10k 均已深挖入库复查活跃）。新锐小标：mARC/daintree/worca-cc | 雷达
- A 组全活跃无增量事故

**✅ 本班落地**：知识注入置信标注（上轮 confidence 分级的注入侧收口）——block_for 对 high 置信条目追加「［有据］」标记，模型据以分配采信权重；medium 不标（默认噪音为零）、low 早已在学习口丢弃；老数据无字段不炸。test_kb_conf_mark 2 项

### 2026-09-23 16:01 第四十二班（批2：学习记忆与自我改进）

- 批2 复跑主体重合（mengram/MemRL/pro-workflow/projectmem 均已入库）。A 组全活跃
- 新锐小标：kyros-ai 94★（Memory OS：3 行代码给 agent 安全自纠持久记忆）| 雷达

**✅ 本班落地**：**分支计划过期标记**（inkos 借鉴第三点清账）——branching.mark_stale：章节修订（revise-c）改写正史后，本章在 .codebee/branch-plans.md 的节头追加「（已过期，正史已重写）」——防止后续翻看审计时把旧推演当成当前正史的来源（inkos「正史变化后旧推演标过期」同款语义）；幂等（重复标记不叠加）、只影响目标章、失败静默、标记后新推演照常追加（重跑场景）。_append_audit 顺带改 pathlib 守卫。test_branch_stale 4 项

### 2026-09-23 18:01 第四十三班（批4：治理/安全/人机协同）

- 批4 复跑主体重合。A 组全活跃无增量事故

**✅ 本班落地**：skill_scan 模式补齐与三档风险（SkillSpector 71 模式的渐进补齐）——新增 3 条危险模式：`child_process`（Node.js 子进程）、`base64 解码`（混淆载荷，归数据外发高危类）、**AI 助手配置目录触碰**（.claude/.zcode/.kimi-code/.codebee——篡改系统提示词或模型绑定的入口，我们生态特有）；risk_label 新增**中风险**档（两个中危类同时命中：网络请求+环境读取、提示注入+越权人格等组合），单中危仍为注意、高危类命中仍直接高风险。test_skill_scan_v2 9 项

### 2026-09-23 20:03 第四十四班（批6：框架/平台/SDK 生态）

- **google/agents-cli 5,979★**（Google 官方）新入库 | "The CLI and skills that turn any coding assistant into an expert at creating, evaluating, and deploying AI agents"——把任何 coding 助手变成 agent 创建/评估/部署专家的 CLI+Skills | Google 继 toolkit 后第二个官方编排入口 | 参考
- 批6 复跑主体重合（A2A 官方 25.9k→+20/agentops 5.8k 复查活跃）；a2a-wrapper 37★ 新增（JSON 配置把 AI 后端变 A2A 兼容 agent）| 雷达
- A 组全活跃无增量事故

**✅ 本班落地**：知识库页 confidence「有据」绿徽章——UI 侧收口（上两轮后端 confidence 分级+注入标注的最后一环）：knowledge.view() 已透传 confidence 字段，UI kbCard 对 confidence=high 的条目加绿徽章「有据」+悬停说明；medium 不标（默认零噪音）、老数据无字段不加不炸

### 2026-09-23 22:02 第四十五班（批1：代码质量与评审）

- 批1 复跑主体重合。A 组全活跃无增量事故

**✅ 本班落地**：代码评审 JSON 解析失败时 extract_scores_from_text 回退（第 5 道网）——_run_review 此前 JSON 解析失败直接判 pass=False 空分 → 白白触发修复轮（修复者只拿到一条报错文本无从修起）。现在先尝试从评审原文提取分数（模型偶尔在 JSON 前后加说明或格式不合法，但「维度：N 分」仍散在文本中）：拿到分数就能正确判定过/不过（≥7 全过），避免把「格式错」当「质量差」误触发修复。test_review_fallback 4 项

### 2026-09-24 00:01 第四十六班（批7：中文/网关/本地/办公）

- 批7 复跑主体重合。A 组全活跃无增量事故

**✅ 本班落地**：branching 压力守卫——多线推演在 token 压力 >0.7 时自动跳过（每次推演是额外 4000 max_tokens 编排者调用；长篇后期预算紧张时推演是第一个该省的增强，压缩守卫先跑、推演不跑，省下的预算留给正文章稿）；n=1 短路最前不碰 meter；meter 异常不拦（守卫不因自身故障炸掉推演）。顺带清理 plan_branches 重复的 n<2 检查。test_branch_pressure 4 项

### 2026-09-24 02:00 第四十七班（批2：学习记忆与自我改进）

- 批2 复跑主体重合（vs 并行复查道 521 条采集口径一致）；scientific-agent-skills/pm-skills 已录参考级
- **ARIS**（wanshuiyin/Auto-claude-code-research-in-sleep，16.5k★）| 睡眠中自动 ML 调研：跨模型评审循环+想法发现+实验自动化，纯 Markdown skills | 与我们 ZCode 自迭代循环同构（他们做 ML 科研、我们做产品迭代）；跨模型评审循环与我们跨厂商评审同向，值得下轮深挖其循环结构 | 待深挖

**✅ 本班落地**：活计划回写（planning-with-files 第三件）——task_plan.md 从静态清单升级为随执行推进的活文档：落盘改 checkbox 形态（`1. [ ] 标题`），子任务起跑标 `[>]`、成功翻 `[x]`、失败标 `[!]`（换将重试成功 `[!]`→`[x]` 幂等覆盖）；崩溃/续跑/换将时看文件即知断点。回写失败静默绝不挡执行。test_task_plan_live 5 项 + 旧契约 3 项同步

### 2026-09-24 04:00 第四十八班（批4：治理/安全/人机协同）

- 批4 复查：agent-governance-toolkit/OpenAgentsControl/archestra/edict/bytebase/tradememory-protocol 均已录无增量；boundary-bench（27★ 沙箱策略基准）/memoryops-ai（21★ 治理型记忆运行时）星体量小，雷达跟踪不立项

**✅ 本班落地**：skill_scan 治理向补强——四组静态特征：①dropper（curl|wget 管道进 shell；PowerShell Invoke-Expression/iex/DownloadString）②持久化新类别（crontab/schtasks/LaunchAgents/注册表 Run 键，直接入高风险档）③外传信道（pastebin.com/webhook.site/requestbin/pipedream/ngrok 隧道/trycloudflare）④挖矿（stratum+tcp/xmrig/cryptonight）。模式只认实际命令面，中文叙述词零误报。test_skill_scan_governance 6 项

### 2026-09-24 06:00 第四十九班（批6：框架/平台/SDK 生态）

- 批6 复查：mastra/vercel-ai/agents-towards-production/harness-sdk/agents-cli/agentops/axonhub/AntSK 均已录无增量
- **ARIS 家族**深挖升级（16.5k★ 主仓+三卫星仓）：主仓=执行者驾驶+独立评审（同构我们）；**HERO-Anti-OverDefense=已落地**（四形态反过度防御块入 IMPL/FIX 提示词）；Anti-Autoresearch（61 完整性信号→确定性取证报告）待深挖——与我们评审解析/证据锚定同向；ARIS-Monitor（等待批准时亮红灯小挂件）与手机端 423 接管提醒同域 | 已落地一件

**✅ 本班落地**：HERO 反过度防御块——CODE_IMPL_PROMPT 全量版（点名四形态：哈希/校验和、层层边界分支、自造评分标准/闸门、顺手加固；「只约束提出不约束检查」语义边界；真实风险写进总结由人决定）+ CODE_FIX_PROMPT 紧凑版。评审提示词刻意不加（评审的检查面不受约束）。test_anti_over 4 项

### 2026-09-24 08:00 第五十班（批1：代码质量与评审）

- 批1 复查：SkillSpector（skill_scan 借鉴源头）/vuls/agentic_security/code-review-checklist/AI-Infra-Guard 均已录
- **claude-code-security-review**（anthropics 官方，6.3k★）| 官方 AI 安全评审 GitHub Action（Claude 驱动 PR 安全审查）| 与我们跨厂商评审同域；官方背书的「评审只读+结构化输出」纪律可参考 | 参考

**✅ 本班落地**：v0.1.62 发版（HEAD worktree 发布法首用）——npm 只打包已提交树，并行会话未提交在制品天然排除（0.1.48 教训的结构性解法，不再需要等树干净）；CHANGELOG 未发布段九项漏账收编补记。发布内容：活计划回写+skill_scan 治理四特征+HERO 反过度防御块

### 2026-09-24 10:00 第五十一班（批3：计划/spec/长任务）

- 批3 复查：spec-kit（138.6k★）/OpenSpec（70k★）/get-shit-done（64.5k★）/planning-with-files 均已录
- **ccpm**（automazeio，8.4k★）| GitHub 原生 agent 项目管理技能系统（issue/milestone 皆技能操作）| 与我们禅道集成同域异构（他们 GitHub-native）；「PM 即技能包」的形态参考 | 参考
- **worktrunk**（max-sixty，8.4k★）| git worktree 管理 CLI | 与我们任务分支隔离+赛马 worktree 已覆盖同域 | 参考

**✅ 本班落地**：Stop gate（planning-with-files 第四件）——收工时活计划勾选完整性可见：`[ ]`未跑/`[!]`失败/`[>]`中断均算未完成，收工摘要+verdict.plan_gate 如实点名；只保可见不改判。test_plan_stopgate 4 项

### 2026-09-24 12:00 第五十二班（批5：检索/知识/浏览器）

- 批5 复查：dify（157k★）/claude-mem（94.6k★ 持久上下文）/ragflow（91.2k★）/Agent-Reach（85.1k★ 网页可达性）均已录无增量；langchain/LibreChat/TiDB 大盘熟悉面

**✅ 本班落地**：知识检索命中热度回流排序——_bump_hits 记账首次进 block_for 排序链做平级决胜（教训库 karma 的轻量同构：被反复召回的知识已验证任务面可用性，相关性同档时优先）；相关性压制/过期降权顺位不变。test_kb_hits_rank 3 项

### 2026-09-24 14:00 第五十三班（批7：中文/网关/本地/办公）

- 批7 复查：unsloth/openhuman/khoj/cognee 均已录
- **funNLP**（83.4k★）| 中英文敏感词/语言检测/实体抽取 NLP 资源库 | 中文文本处理数据源，非 agent 产品；若有敏感词/实体需求可作数据层参考 | 参考
- **JeecgBoot**（48k★）| 中文企业级低代码平台 v2.0 一句话生成系统 | 中文企业软件生态大盘观察；与编排台不同赛道 | 参考
- **Langchain-Chatchat**（38.7k★）| 中文本地知识库问答（原 Langchain-ChatGLM）| 我们知识库=任务域注入，他们=RAG 问答面；本地中文 RAG 形态参考 | 参考

**✅ 本班落地**：i18n「执行」重复键译值冲突修复——EN 字典两处「执行」（Run/Implement）JS 后值静默覆盖致英文模式全显 Implement；字典唯一化（执行=Run、实现=Implement 各一）+路由卡实现角色块改用 t("实现") + 位置锚定重复键扫描守卫入测试列（发版巡检清单例行化，零重复键强制）。test_i18n_dups 2 项

### 2026-09-24 16:00 第五十四班（批2：学习记忆与自我改进）

- 批2 复跑：KIP/agent-apprenticeship/MAGEO/scientific-agent-skills 等均已录，零真新标的

**✅ 本班落地**：压缩省量入台账——compact_region 成功路径向 usage.record 入账（source=compaction：input=摘要读入/output=摘要写出/saved=净省量，零值不落字段）；summary() totals 增第七维 compaction_saved。A 专项「省了多少」从黑箱变可答；摘要调用真实消耗同步显形。test_compact_ledger 3 项

### 2026-09-24 18:00 第五十五班（批4：治理/安全/人机协同）

- 批4 复查：治理向高星均已录无增量

**✅ 本班落地**：skill_scan 中文注入面——三组中文话术入列（指令覆盖/隐瞒双语序/人格重设收紧形态），中文恶意 skill 不再零覆盖；良性写作叙述零误报（「将描述」「忽略无关信息」刻意排除）。test_skill_scan_zh 4 项

### 2026-09-24 20:00 第五十六班（批6：框架/平台/SDK 生态）

- 批6 复查：框架生态高星均已录无增量

**✅ 本班落地**：压缩省量 KPI 卡——用量页 KPI 行露出台账第七维（compaction_saved）：有压缩显净省 token，无压缩显「—」与累计说明；EN 三键同步。补齐 16:00 后端账的前端面

### 2026-09-24 21:00 用户点名清账班（待深挖队列 8 项清账 + 扫榜扩源）

- **ECC instincts 概念深挖完成**（affaan-m/ECC，266k★，MIT）| instincts=从真实会话提取的模式+置信分（continuous-learning-v2；v1 是 Stop-hook 抽取）；注入纪律：SessionStart 每次最多注入 6 条（ECC_MAX_INJECTED_INSTINCTS）、置信阈值 0.7、按置信+项目相关性排序；生命周期：/evolve 把相关 instincts 聚类**晋升成 skill**、/prune 清过期 pending、/instinct-import/export 可迁移；Memory Vault 跨 harness 本地 Markdown 记忆（会话蒸馏=摘要+instincts+skills 三层）| **与 CodeBee 经验库对照**：outcome 加权≈置信分、相关性最高优先≈注入排序、命中热度≈召回信号——大机制同构；**差量勘误（09-25 复核）**：②注入条数硬上限系误记——MAX_LESSONS_INJECT=8 与 KNOWLEDGE_MAX_INJECT 早已双存在；实差量=①教训自动聚类晋升技能（已落 evolve）/③过期清理（降权已有、物理删除伤数据不做）/④导出导入（backup 全量已覆盖） | ①已落地 | 2026-09-24
- **ARIS Anti-Autoresearch 深挖完成**（wanshuiyin/Anti-Autoresearch，卫星仓）| **确定性脊柱**：span 锚定+哈希证据账本 → LLM 审计员**只提议**发现 → 纯规则裁决器打分/降级（模型永不给 verdict）；61 个 HP-* 诚信信号带误报用例（HP-NUM-INFLATE 摘要 85.3 vs 表 84.7、HP-DELTA-ERROR 16% 实为 6.7%、HP-PIPELINE-ARTIFACT 模板残串精确匹配）；表面信号防火墙（重复表/LLM 图/凑页数硬顶 minor）、AI 文风印象**零裁决权重**；8 个模式 eval 门禁进 CI | 与我们评审解析三道网+事件流计数鉴别谎报同向；**可借形态**：①评审发现带证据锚（span+hash）而非自由文本 ②高误报类发现强制降档 ③「模型只提议、规则裁决」分工 | 借鉴方向（评审域） | 2026-09-24
- **freellmapi 深挖完成**（tashfeenahmed/freellmapi，28.4k★，MIT）| 34 家免费档聚合 635 端点≈7.4B tokens/月，一个 /v1；per-key RPM/RPD/TPM/TPD 记账**学习供应商天花板**（主动避让而非撞了才冷却）；同模型跨供应商归一一条+组内严格 failover；sticky session 30 分钟+中途换模型带交接摘要；请求管线预压缩（prompt 去重/工具输出过滤/重复 JSON 压缩）；密钥 AES-256-GCM；免费版目录快照滞后 30 天（付费 $19/yr 实时）；**明示 Personal experimentation only** | 与我们多 KEY 链+冷却+链展开同构度高；**结论：不接**（ToS 个人实验限定+配额稳定性存疑+同构无增量）；差量=配额**记账式**主动避让（我们是错误驱动被动冷却）远期可借 | 不接（拍板材料齐） | 2026-09-24
- **codegraph 深挖完成**（colbymchenry/codegraph，72k★，MIT）| 预索引符号/调用边/依赖图（含动态分发跳边）+文件监听自动同步+MCP 接线各家 CLI；实测 7 仓 7 语言：工具调用 -88%/token -62%/成本 -44%，**同时诚实报告常驻上下文 +80%**（稠密payload 进窗不走）——双面测量文化本身值得抄 | 重依赖本地索引守护进程，不接；差量备注：①代码任务上下文可预注入「符号+调用边+影响面」摘要 ②发基准要报「处理成本」与「常驻成本」两面 | 参考（含测量方法论） | 2026-09-24
- **舰队三件套复查清账**：**orca**（stablyai，77.1k★，MIT，桌面 ADE）| 同一 prompt 扇出 5 agent 各自 worktree 赛马合并赢家+手机伴飞（监控/steer/追话）；**paseo**（getpaseo，18.3k★）| 自托管桌面+移动同接口多 CLI；**superset**（superset-sh，14.6k★，Elastic-2.0 ⚠️不可抄码）| 100+ agent 并行 worktree+内建终端/diff+iPhone 远控（Pro）| 与我们任务分支隔离链/并发池/手机 423 接管闭环逐项对上；**唯一差量**=代码任务级「同 prompt 扇出 N worktree 合并赢家」（我们赛马在稿件级/计划级）——费 token 远期备选 | 方向验证（差量小） | 2026-09-24
- **continuum 深挖完成**（naman159/continuum，Apache-2.0，小标）| 长篇写作记忆层：**章节截断世界状态视图**（查「第 N 章末时角色知道什么」防剧透回溯）、角色状态版本化、承诺/伏笔 payoff 追踪、keyword+embedding 双检索、MCP 暴露给任意写作 agent+失败稿人工复核队列、题材预设（含 Xianxia）配实体抽取 | 与我们圣经+前情提要同域；**差量三件**：①按章节截断的历史状态查询 ②伏笔/承诺账本（显式追踪未兑现承诺）③知识面 MCP 化供外部 agent 复用 | 借鉴方向（小说域差量记路线图） | 2026-09-24
- 2026-09-25 复查：**星数归零**（API 返回 s=0、pushed 09-21，repo 公开在但计数清零疑删库重建）——差量三件已记路线图，条目降级「停更观察」，后续以日报雷达为准
- **Yuxi 深挖完成**（xerrors/Yuxi，7.2k★，中文）| 可私有部署多租户知识智能体平台：Docker 全家桶（LangGraph/Vue/FastAPI/Milvus/Neo4j/PG/MinerU/PaddleOCR）；特色=知识图谱参与检索（Milvus 文档块抽实体关系入 Neo4j 联合检索）+多租户权限+Langfuse 数据集评估智能体任务 | 与我们单体 pip/npm 轻形态完全不同赛道（多租户重部署）；「图谱参与检索」「数据集评估闭环」两点远期方向备注 | 参考 | 2026-09-24
- **test-defect-retrospective 深挖完成**（zl2237，1★）| 禅道/Jira 导出→**产品/开发/测试三视角复盘报告**（MD+JSON+HTML）；值级标准化（状态/严重度/根因/时间）；analyzer 确定性同输入同输出；插件化平台解析器；问卷断点续跑；定性评审模式（需求/用例/技术方案文档评审可组合）| 与我们禅道闭环（扫描→建任务→修复→回写）互补——**我们缺「复盘报告」产物**；落地形态：direct 引擎+禅道 CSV 导出→三视角复盘，flows 热区定稿后做 | 借鉴方向（落地排队） | 2026-09-24
- mira（303★ 索引化 PR 评审）复核：我们 A 专项已有 diff-only 评审，**已覆盖**出队 | 2026-09-24
- OpenCreator 版本化差量备注：run 历史已有雏形，缺「同任务多次产出一键 diff 视图」（UI 层增强，非本轮）；TokenJuice/freellmapi 同款「工具输出进上下文前统一压缩管线」合并记 A 专项远期方向 | 2026-09-24

**✅ 本班落地**：扫榜选材数据源扩容（路线图「扫榜数据源」欠账清账）——双源→四源：起点移动版（m.qidian.com/rank，主站被 WAF 拦 202/209B、移动页免签名直接出书名）+纵横（www.zongheng.com/rank）；_SOURCES 源清单化（存函数名调用期 globals() 解析——mock.patch 换模块属性才生效，存函数引用会钉死原函数让测试穿透打真网络，实测踩过）；榜单 tab/统计标签噪音词扩 17 个。旧欠账「封面图」核实已随 v0.1.x 两代发版落地（建书面板生成封面+CogView 接线+内嵌缩略图），本轮销账。test_paihang 12 项

### 2026-09-24 22:30 用户点名清账班·第二班（缺陷复盘 flow + release_smoke）

- **test-defect-retrospective 借鉴落地**（21:00 班「落地排队」即刻兑现）：`defect_retro`（缺陷复盘）新预置类型——direct 引擎+任务附件通道（禅道/Jira 导出 CSV）→ 三视角复盘报告（整体画像/产品/开发/测试/改进动作带责任角色）。`defectretro.retro_prompt` 纯框架注入（与 paihang 抓取注入同构但恒有返回）；pipeline 分支+flows 登记+i18n EN 四键。**防编造纪律进提示词**：数字必须从导出数出来、缺数据写「数据未提供」、无附件给导出步骤不硬写报告。test_defectretro 5 项 + content_contracts/flow_type_integrity 过（成本预估已覆盖新类型）
- **WorkDSH 清单⑥ 发布工程落地**：`tests/test_release_smoke.py` 发版校验工具——①SHA256SUMS 清单（tar 流直读哈希，字节写保 LF）②import 冒烟（解包后逐个 import app/core，0.1.63「坏文件进包用户才崩」类的当班闸）③bin 入口在包校验。单测 6 项（防 tar-slip 三重防线/清单口径/快速 import）+CLI 发版档真跑过（132 文件/78 模块/0 失败）。**Mimosa 五轮攻防**：extractall→字符串预检→market_remote._safe_extract 同款 idiom（段白名单+resolve 收容+落点复查+write_bytes）才放行——安全工具拦出了真防线，最终形态比初版更硬
- rank_scan note 文案同步四源（G 专项：描述与实际不符）；发版档用法：`python tests/test_release_smoke.py --pack`（test_selfupdate 后、npm publish 前，退出码非 0 不发版）

### 2026-09-24 23:40 用户点名清账班·第三班（run 产物两版对比）

- **OpenCreator 版本化差量落地**（21:00 班差量备注即刻兑现）：成品文件「对比」chip——同任务上一版 run 有同名文本文件时，弹窗拉两版内容做统一 diff（新增绿/删除红/长相同段折叠计数）。**纯函数独立 artdiff.js**（公共前后缀修剪+中段 LCS，400 行/16 万格上限，超限诚实降级整块计数不装作对齐）——node 直测 12 项（tests/ui_art_diff.mjs：一致性/插入删除/LCS 交错保序/超限/空输入/重复行）。接线三处：artifactsChips 加 prevRun 参数、loadArtifacts 拉上一版清单（失败静默不给对比口）、bindArtifactClicks diff 分支先于通用预览；i18n EN 七键；CSS 行色复用 var(--ok)/var(--bad) 随皮肤。**混合文件锚点拆 hunk 提交**（app.js 211 行里约 90 行是并行道在制品）：锚点过滤 patch + git apply --cached --recount；style.css 双尾追加并 hunk 用「HEAD EOF 行号+纯新增块」手工重建只暂存自己
- 剩余账更新：**⑩附件 digest 已由并行道本班落地**（commit_to_workdir 钉分块 sha256+verify_task 四态对账+execute_run 漂移落 attachment_drift，与本班 diff 同窗）；ECC 注入上限=knowledge.py 热区待树定；ARIS 规则裁决/continuum 伏笔账=pipeline 级大改攒批；⑨Office 编辑=docx/xlsx 只读预览已被并行道落（3664f96+PDF 切片），编辑态（Tiptap/Univer 依赖）维持远期待拍板

### 2026-09-24 22:00 第五十七班（批1：代码质量与评审）

- 批1 复查：评审/安全域高星均已录无增量

**✅ 本班落地**：计划层反过度防御——HERO Overbuild 形态上移到编排者拆分面：hard 多子任务不做任务没要的防御性扩展（缓存/重试/监控/通用抽象层），真实风险写 detail 备注由人决定。与 d45506c（实现/修复层）成对，反过度防御闭环到计划-实现-修复三层。test_plan_anti_overbuild 3 项

### 2026-09-25 00:30 用户点名清账班·第四班（终清：ARIS 锚+continuum 督促+ECC evolve）

- **ARIS 评审证据锚落地**（Anti-Autoresearch「模型只提议、规则裁决」分工的评审域切片）：NOVEL_CRITIQUE_PROMPT/SERIAL_GLOBAL_PROMPT 的 issues 增 quote 字段（≥8 字逐字摘录+「编造引文会被降档」威慑）；`_anchor_issues` 确定性 containment 校验**三态**（命中=锚定/对不上=未锚定降档殿后带标记/老格式无 quote=None 灰度兼容不标记）；归一=去空白+剥首尾标点（引文多带一个句号不该判假）；连载与内容两处修订面 crit_lines 换 `_major_lines`（锚定在前/未锚定殿后+⚠计数行）。event_check 逐项目标审稿的原文证据要求（此前已有）与 issues 锚互补
- **continuum 陈年承诺督促落地**（承诺/伏笔账差量收窄版——资源账本+章戳早已存在，真差量=无账龄督促）：`_ledger_watchlist` 按「## 第 N 章」段解析承诺/伏笔条目（同名以最后出现章为准、现状含已兑现/已回收等词剔除），距本章 ≥3 章未推进 → 起起草面追加「⚠ 陈年承诺/伏笔」清单（推进/回收其一或明确留白）；min_age 边界/排序/空账本全测
- **ECC evolve 落地**（/evolve 教训晋升技能的半自动版）：`skills.evolve_lessons`——注入 ≥6+可信度下界 ≥0.75+成功归因 ≥1 的教训聚成用户包**草稿**（data/skillpacks/evolve-*.md，frontmatter scopes=教训域并集），**默认停用**（pid 与 _load_user_pack stem 哈希同口径写 enabled=False）——晋升必须过人工审阅闸；POST /api/skills/evolve 返回收录概况。ECC 差量②「注入条数上限」勘误为误记（两上限早已存在）
- test_final_borrowings 15 项（锚三态/归一/降档/提示词契约+督促边界排序剔除+evolve 门槛草稿停用）；相邻域 review_fallback/reviewer_error/quality_gates/serial_review_resilience/lesson 三件全绿

### 2026-09-25 00:01 第五十八班（批7：中文/网关/本地/办公）

- 批7 复查：unsloth/openhuman/khoj/funNLP/JeecgBoot/Langchain-Chatchat 均已录无增量

**✅ 本班落地**：压缩省量按日趋势——usage 聚合器与 by_day 逐日带 saved 维、KPI 卡挂 spark 走势；补全 09-24 台账第七维的时间维度（省量何时发生一眼可读）

### 2026-09-25 00:35 第五十九班（批7 复跑 + 雷达源 C 全过——00 点 %7=0 与 58 班同批，重合属预期；数据巡检班）

- 主扫描 103 组（A 78 + 批7 9 + A1u/A1p2 双轮）449 去重条目零失败零限流；雷达源 C 全过（topic 8 + 自家 CLI 周边 5 + 禅道周边 2 + awesome 新鲜度 6 + npm 2 + pypi 搜索页被拦如实记）；**真新标的 = 0**（连续两班零增量，头部格局稳定）
- 新增雷达（备查）：impeccable 70.7k（AI harness 设计语言，pbakaus）/CLIProxyAPI 53.1k（多 CLI 包成 API 代理）/nanobot 48.5k（HKUDS 轻量 agent 框架）/Anthropic-Cybersecurity-Skills 33.3k（817 安全 skills）/FreeToken 13.7k（桌面级模型服务）/Backlog.md 6.8k（人机协作 git 项目管理）/claude-token-efficient 6.1k（CLAUDE.md 输出从简）/ClawRouter 6.6k+semantic-router 5.9k（vllm 系 MoM 路由）/costrict 4.4k（企业级 AI coder 含 CodeReview）/notfair-plugin 3.9k（SEO/GEO skills，最大营销 skills 包）/hope-agent 1.6k（中文跨端桌面 agent，记忆+目标推进+动态编排）/LightMem 1.2k（ICLR26 轻记忆）/nimbalyst 1.8k+claudexor 480+Ghostex 842+ntm 450（多 CLI 控制面同域四例）/tokenlens（零侵入 token 监控代理，中文新锐）
- **writing-with-agents**（Jeffallan，31★）| Betty Flowers「Madman-Architect-Carpenter-Judge」四角色写作框架 skill | 与我们多角色评审同向；起草面四角色未显式化——蒸馏候选 | 雷达 | 2026-09-25
- **tf-routing 实测反证**（0★，TrueFoundry）| 「cheap-first 全局路由是 p99 延迟陷阱」实测数据 | 我们 cascade 按**任务难度**分流而非全局便宜优先——设计未被证伪，记录为 A 专项对照证据 | 参考（A 专项） | 2026-09-25
- **勘误清账（opencode-metrics 借鉴，09-22 记）**：用量页缓存命中率/首字延迟核实**已落地**——renderUsage KPI 卡三件全在（缓存命中率副标 tot.cache_rate / 首字延迟卡 P95·吞吐·样本 / 压缩省量卡 spark）| 已落地 | 2026-09-25
- **六源市场连通恢复**（09-19 记 clawhub 网络不通、cocoloop 0 项）：本轮两源全部探活成功，合计 808 条可见——旧记录作废 | 已核实 | 2026-09-25
- **D 专项数据维护**：教训 88→84——「做法：direct 判断题直接给结论」等 4 条同族做法合并为 1 条（要点并集）、「识别HMPV概念图」任务残留删除（一次性事实知识混入教训库且误分类流程规范）；流程规范偏科 48%→45%
- **E 专项**：候选 CLI（reasonix/fuxi/gitlawb/zero/empryo + zcode/goose/crush）本机全未装——零接入维持防死链；DeepSeek-Reasonix 35.7k★（A4 头部、09-24 活跃）继续居候选首位
- **F 专项**：禅道 poll_enabled=False（用户侧开关，非故障）；零积压、零 last_error、last_scan 09-21；产品档案 96 路由有效（mo-so workdir 存在，owners 齐）；无新禅道 AI 竞品（zentao-cli 60★ 09-22 活跃）
- **⚠ 架构守卫新环报警（真实风险交人决策，未动）**：knowledge→skills→store→knowledge 两环——skills.py:868 与 store.py:653 函数内惰性互引（运行时无恙、静态图成环），09-24 并行提交引入、基线未更新。修复需动热区导入结构，交人拍板

**✅ 本班落地**：①bid_doc 图标与 research 撞车修复（i-file-search→i-tasks，G 项随手修）；②计划清晰度「初级工程师测试」进 CODE_PLAN_PROMPT（superpowers 借鉴②路线图清账：detail 须无项目上下文、不做隐含判断也能照做不跑偏）+ test_plan_junior_test 2 项

### 2026-09-25 00:52 第六十班（批7 三连 + 59 班遗留收编）

- 复查（repos 端点）：orca 77,331★（09-24 活跃）、superpowers 291,096★、oh-story 7,090★（09-24 活跃）、spec-kit 138,726★、Reasonix 35,706★（09-24 活跃，候选首位维持）；**inkos 10,034★ pushed_at 停在 2026-08-25——停更快一个月**（星数仍涨），深挖队列降级为「停更观察」——**2026-09-30 复查：恢复更新（10,088★，09-27 push），解除停更观察回雷达；多线推演/安全章节工作区两差量已落地，第三差量（检索投影）bigram top-k 已覆盖维持**
- **ultraworkers/claw-code**（195,276★/108k forks，Rust，08-16 停更）| 「agent 管理的博物馆展品——无人干预开发维护」行为艺术式展品项目 | 星数奇观无机制可借鉴 | 参考（雷达奇观档）| 2026-09-25
- **shareAI-lab/learn-claude-code**（77,559★，Python，08-26 停更）| 「Bash is all you need」从 0 到 1 造 nano claude code 式 harness 教学仓 | 我们本体即 harness，教学参照 | 参考 | 2026-09-25
- **msitarzewski/agency-agents**（154,472★，09-22 活跃）| 核实为已录 agency-agents-zh（中文专家角色模板）的英文上游本体 | 补记关联；「蒸馏为类型角色与交付契约」结论沿用 | 参考（关联补记）| 2026-09-25
- 本轮真新标的 = **0**（连续三班）——头部格局稳定期，调研重心自然转向「复查+小件清账」节奏

**✅ 本班落地**：①defect_retro 图标缺失修复（i-clipboard→i-history，59 班只查撞车漏了缺失——精灵表无定义菜单图标空白）+ `tests/test_flow_icons.py` 2 项把「图标已定义+互不重复」两类问题一起锁死；②A 专项留存④清账——CODE_IMPL_PROMPT「交付物从简」行（claude-token-efficient 借鉴：回复不写开场白/不复述任务/直接给干货）+ test_output_slim 2 项

### 2026-09-25 02:00 第五十九班（批2：学习记忆与自我改进）

- 批2 复查：学习记忆域高星均已录无增量

**✅ 本班落地**：近似合并留痕 merged_titles——教训/知识双侧近似题吸收不再静默吞变体：去重+封顶 8 条（防自动化长跑撑爆字段）、精确同题零痕迹；后续按变体措辞检索有据、审计可见

### 2026-09-25 02:56 第六十一班（批2 + 雷达源 C 全过 + 七专项巡检班）

- **compozy/compozy**（2.8k★，09-24 活跃）新入库 | 「AI agents 的操作系统：接入你已有的 agent CLI」——与我们同形态（多 CLI 编排面）新竞品 | 雷达
- **qiqihezh/deepresearch-agent**（153★，中文）新入库 | 生产级深研：Red-Blue 对抗降噪（检索结果两队互搏滤噪）+语义级上下文压缩+跨 Agent 共享记忆 | **「检索降噪」是我们调研报告任务空白** | 待深挖
- **GhalebDweikat/winnow**（54★）| 校准上下文筛：每个工具结果被评判值得否进上下文 | A 专项「工具输出统一压缩管线」第 5 独立验证 | 方向验证
- **Jev/TypeSafe System One 簇**（一周 6+ 例：typesafe-computer-use 947★/JevLoop/jev-browser 等）| 「机械决策不进 LLM」新范式——非 LLM 小决策核处理路由/分类/审批，LLM 只做深度推理；cascade 的激进版（零模型分流）| 雷达（范式观察）
- **yetone/magpie**（578★）| 跨 CLI 模型混搭网关（Codex 跑 DeepSeek、Claude Code 跑 Kimi）| 模型调度同域再验证 | 参考
- **演讲 PPT 域六连**（presentation-ai 3.0k/SlideBot/ppt-agent-skill/slide-deck-generator/beamer-academic/marp-skill）| 赛道升温；我们 17 类型无「演示文稿」类型——产品空档交人拍板不擅自扩 | 参考（C 专项备注）
- **free-claude-code**（55.9k★）| 9 harness 免费用聚合 | 星数奇观档 | 参考
- 复查重大增量：**ZCode 361★→6,696★（一周 20 倍爆发）**——本机仍未装，防死链维持；禅道生态三新小标（zentao-mcp/zentao-auto-fixer-server——与我们闭环同域/pi-zentao）；去 AI 味再热（quiron+snifftest，aiflavor 同域第 3/4 例）；WeChatBridge 652★（微信→Agent 通道生态）；批2 学习记忆域 komi-learn/causal-memory/MegaMemory 均参考级；awesome 全活跃、npm 无新标的
- **D 专项里程碑**：教训五组同族合并 84→67 条（验评关系 10→1/盲修 4→1/接口验收 4→1/修复留证 3→1，merged_titles 留痕）——流程规范偏科 45%→31%，分布 31/30/16/12/6/4

**✅ 本班落地**：①四角色写作框架蒸馏（writing-with-agents 借鉴清账）——novel 契约新增「成文分三步」：狂人倾倒素材清单→建筑师组织结构→木匠按结构成文，初稿期不做质量审判（Judge=既有评审链不重复）；原契约全收敛导向、发散步骤缺失是真实差量。②email 线程级交付契约（agentic-inbox 待深挖清账）——「逐条回应不漏问+行动项带负责人与截止时间」（收件回复视角，与既有发件视角行互补）。test_content_contracts +2 项。G 专项 README 三处过时修正（15 种→17 种+清单/表格补缺陷复盘标书编制+扫榜四平台）

### 2026-09-25 04:00 第六十班（批4：治理/安全/人机协同）

- 批4 复查：治理域高星均已录无增量

**✅ 本班落地**：skill_scan 云元数据/内网探测特征——169.254.169.254（AWS/GCP 元数据）、metadata.google.internal、私网+凭据路径组合入列可疑意图（高风险档）；127.0.0.1 本地开发与公网常规端点零误报。test_skill_scan_metadata 4 项

### 2026-09-25 06:00 第六十一班（批6：框架/平台/SDK 生态）

- 批6 复查：框架生态高星均已录无增量

**✅ 本班落地**：近似合并吸收标签——教训/知识卡「吸收 N 个近似题」（merged_titles 前端露出），合并卫生从后端留痕到页面可见闭环

## 2026-09-25 七专项对比学习与沉淀班（全天调研汇总·计划第 2/4 步）

> 汇总 58-64 班全天调研（115 组 521 条/班，连续五班真新标的=0，头部格局稳定期），
> 对照 CodeBee 逐项核实；七专项本班重新实证探测（非转录前班结论）。
> 只认「他们有、我们没有、且确实好用」；63/64 班日报雷达未入库条目本班 consolidation。

### 新条目（63/64 班雷达 consolidation 入库）

- **winnow**（GhalebDweikat，54★，09-19 新）| 校准的上下文筛：每个工具结果被评判值得否进上下文 | A 专项「工具输出统一压缩管线」第 5 独立验证（TokenJuice/openhuman/freellmapi/deepresearch-agent 之后）| 方向验证 | 2026-09-25
- **Jev/TypeSafe System One 簇**（一周 6+ 例：typesafe-computer-use 947★/JevLoop/jev-browser/jev-skill 等）| 「机械决策不进 LLM」：非 LLM 小决策核处理路由/分类/审批，LLM 只做深度推理 | cascade 的激进版（我们便宜模型分流、他们零模型分流）；成本收益待观察 | 雷达（范式观察）| 2026-09-25
- **qiqihezh/deepresearch-agent**（153★，中文）| 生产级深研：Red-Blue 对抗降噪（检索结果两队互搏滤噪）+语义级上下文压缩+跨 Agent 共享记忆 | 调研报告任务「检索降噪」是我们空白 | **2026-09-30 复查：停更实锤（pushed 停在 05-11）——检索降噪已有学术四重验证支撑（09-28 落地切片），本条降级不依赖单项目；Red-Blue 完整互搏维持远期观察** | 2026-09-30
- **演讲 PPT 域六连**（presentation-ai 3.0k/SlideBot 1.2k/ppt-agent-skill/slide-deck-generator/beamer-academic/marp-skill）| 演示文稿生成赛道升温 | 我们 17 类型无「演示文稿」类型——产品空档交人拍板，不擅自扩类型 | 参考（C 专项备注）| 2026-09-25
- **org2AI/ORG2**（2,630★，09-24 活跃）| 「agent 如何构建软件的 system of record」：内置 rust harness+20+ CLI | 多 CLI 编排同形态两日最大新面孔 | 雷达 | 2026-09-25
- **多 CLI 控制面/终端窗管井喷**（63/64 班两日 25+ 例：tuios 3,716/nodeterm 1,894/poco-claw 1,352/thClaws 1,227/agent-deck 948/Citadel 923/Pane 485/dario 545/amux 497 等）| 赛道拥挤度新高 | 同形态竞品持续验证产品方向；机制上均无我们未覆盖项 | 雷达（同形态跟踪）| 2026-09-25
- **Skills 市场生态膨胀六例**（tons-of-skills-marketplace 2,787/skills-manager 4,934/skillkit 1,538/claude-code-skills 1,426/ai-agent-skills 1,144/power-platform-skills 924★ 微软官方进场）| skills 跨工具翻译安装/桌面管理/平台包 | 接入三问均不过（重合高/需二跳/用户不整包搜）——不接维持雷达 | 参考（B 专项）| 2026-09-25
- **dzhng/deep-research**（19,722★）| 迭代式深研参照实现 | 调研报告任务大盘补格（与 gpt-researcher/SkyworkAI 并列参照）| 参考 | 2026-09-25
- **UiPath/coder_eval**（141★，09-24 新发）| 「coding agent 的 Playwright」：技能/MCP/CLI 在 agent 用的时候真能跑 | 评测域；RPA 巨头进场 agent 评测 | 参考 | 2026-09-25
- **provos/ironcurtain**（610★）| 自然语言宪章→运行时策略强制 | 我们任务宪章是提示词级；「编译为强制策略」是治理远期方向 | 参考 | 2026-09-25
- **y49/tlive**（211★）| Telegram/飞书远程审批+实时监控 Claude Code/Codex | 我们手机接管+通知同域的 IM 通道形态 | 参考 | 2026-09-25
- **mgtf/atoma**（12★）| 「the cheapest model that can answer」+逐调用成本记账+earned trust | cascade 语义第 N 次独立验证 | 参考（A 专项）| 2026-09-25
- **vstorm-co/summarization-pydantic-ai**（72★）| LLM 摘要或零成本滑动窗做上下文管理 | 「压缩摘要真实替换 CLI 请求」（09-21 落地）同族再验证 | 已覆盖 | 2026-09-25
- **yetone/magpie**（578★）/askalf/dario（545★）| 跨 CLI 模型混搭网关/订阅统一端点+failover | 模型调度同域簇 | 参考 | 2026-09-25
- **compozy/compozy**（2.8k★，09-24 活跃）| 「AI agents 的操作系统：接入你已有的 agent CLI」 | 同形态（多 CLI 编排面）新竞品 | 雷达 | 2026-09-25
- **WeChatBridge**（652★，一周新）| 微信聊天记录一键转发到 AI Agent 与 Obsidian（macOS）| 微信通道（CowAgent 已验证）配套生态 | 雷达 | 2026-09-25

### A 专项 · token 节约（单列小节）

- **12 项既有机制锚点本班逐一核实全部在位**：预算熔断 pipeline.py:608 / token_meter :622,654,674 / 压缩守门重试 :636 / cascade :1368 / _shrink_context_block :2333 / diff 评审 :935 / 交付物从简 :832（60 班 1dfa24b 已入库）+ 精确缓存（编排者三处+连通测试 24h）/ 压缩省量台账+KPI+日趋势 / read_file 头尾保留 / 经验召回 / 会话复用+前情提要 :2417
- 竞品对照**零新机制可抄**：atoma/summarization-pydantic-ai 均为既有设计同向验证；winnow 是「工具输出统一压缩管线」第 5 验证（read_file 头尾保留只覆盖文件读取一种工具，其余工具结果仍直进上下文）；Jev System-One 簇=零模型分流范式观察；tf-routing「cheap-first 全局路由 p99 陷阱」反证继续有效——cascade 按任务难度+角色分流未被证伪
- **留存方向维持三条（均管线级攒批）**：①工具输出统一压缩管线（5 例独立验证，最成熟）②语义缓存（待租户/敏感边界拍板）③评审深度随 diff 规模分级（pr-af）

### 七专项 B-G（本班实证复核）

- **B 插件市场**：六源清单在位（market_remote.py SOURCES：zcode/anthropic/anthropic-skills/claude-skills/clawhub/cocoloop）；本日新见 skills 生态膨胀六例过三问均不过——零新增接入维持
- **C 任务类型**：17 预置类型 id 清单核实（direct/code/novel/serial_novel/article/video_script/doc/translation/rank_scan/defect_retro/research/speech/weekly_report/email/tech_proposal/resume/bid_doc）；i18n EN name+goal_hint 程序化核对**零缺失**；演示文稿空档待拍板维持
- **D 经验库**：教训 67 条稳定（流程规范 21/节奏爽点 20/情节逻辑 11/人物塑造 8/一致性 4/文笔风格 3——偏科 31%，61 班五组合并后无新增同族可并）；知识库 12 条（serial_novel 9/direct 3）。**方法论蒸馏**：头部格局稳定期（连续五班真新标的=0）≠ 停调研——增量转移到新锐簇观察（控制面井喷/机械决策范式/技能市场膨胀三簇）与已沉淀项目复查；「真新标的」口径坚持「他们有+我们没有+确实好用」三条件防凑数
- **E 新 CLI**：catalog.py 11 条目核实；本机 which 实测在装 10（codex/claude/opencode/qwen/kimi/dsh/pi/**mimo/grok/aider**——后三者 64 班记录漏探，本班补测在装）；openclaw 未装；八候选（reasonix/fuxi/gitlawb/zero/empryo/zcode/goose/crush）全未装——零接入维持防死链；DeepSeek-Reasonix 35,708★（09-24 活跃）候选首位维持
- **F 禅道**：poll_enabled=False（用户侧开关非故障）；claims 零积压、last_error 空、last_scan 09-21（开关关所致属预期）；auto_resolve/auto_merge=True；**产品档案 1 条=产品 ID 96 → E:\GitLab\cbc\mo-so 存活验证**、owners 齐（backend=wuxinping/frontend=xiangdong）、module_routes 空。**勘误**：前序报告「产品档案 96 路由有效」易误读为 96 条路由——实为产品 ID 96 的 1 条档案。禅道 AI 竞品生态无增量（63 班周边两词零命中，61 班三例 zentao-mcp/auto-fixer-server/pi-zentao 已录）；config 遗留 interval_hours legacy 字段（后端已迁 interval_minutes 自动换算，兼容无害）
- **G 产品巡检**：UI 全目录与 README 无「15 种/13 种/双源/两平台」残留（grep 零命中，61 班 README 修正覆盖完整）；rank_scan note 四平台文案与实现一致（flows.py 四源）；图标双闸守卫 test_flow_icons 2 项本班复跑全绿（bid_doc=i-tasks/defect_retro=i-history 修复在位）；新毛病：无

### 复查记录（全天汇拢）

- 2026-09-25：orca 77,502★（09-24 活跃）/superpowers 291,212★/oh-story 7,090★（09-24 活跃）/spec-kit 138,785★（09-24 活跃）——头部全活跃无机制增量；**ZCode 6,705★ 一周 20 倍后持续放量**（本机仍未装，防死链维持）；**DeepSeek-Reasonix 35,708★（09-24 活跃）E 候选首位维持**；anthropics/skills 官方仓 177,979★（09-24 活跃）——技能生态第一方大盘新坐标；awesome 全活跃（awesome-claude-code 54,564/VoltAgent 34,820/Long-Horizon 1,043）；inkos pushed_at 停 08-25 降级「停更观察」、continuum 星数归零疑删库重建（均已记）

### 待深挖队列（当期快照）

1. **工具输出统一压缩管线**（winnow 第 5 验证，最成熟方向）——管线级攒批
2. **语义缓存**——待租户/敏感边界拍板
3. **评审深度随 diff 规模分级**（pr-af）——攒批
4. **调研报告「检索降噪」**（Red-Blue 对抗，deepresearch-agent）——待深挖
5. **演示文稿任务类型空档**——交人拍板（C 专项，不擅自扩）
6. **任务宪章编译为运行时强制策略**（ironcurtain）——治理远期
7. 既有攒批维持：ARIS 规则裁决/continuum 伏笔账/multica 看板视角/OpenCreator 版本化对比 UI

### 2026-09-25 08:00 第六十二班（批1：代码质量与评审）

- 批1 复查：评审/安全域高星均已录无增量

**✅ 本班落地**：分数提取分隔符扩展——「维度 - N 分」（短横线）/「＝」（全角等号）/「→」（箭头）三种模型真实输出形态入列第四道网；此前双提取失败=格式错误判质量差白烧修复轮。噪声守卫不放松。test_review_extract_v2 5 项

### 2026-09-25 10:00 第六十三班（批3：计划/spec/长任务）

- 批3 复查：spec 族高星均已录无增量

**✅ 本班落地**：活计划入口——详情页成果区「任务计划」直达 task_plan.md（files 端点 has_plan 标志 + file 端点通道 + 徽章计数 + i18n）；活计划从落盘/回写/Stop gate 到用户可见全链路闭环

### 2026-09-25 12:00 第六十四班（批5：检索/知识/浏览器）

- 批5 复查：检索/知识域高星均已录无增量

**✅ 本班落地**：教训注入 karma 标注——won≥2 注入行带「（已验证有效 N 次）」实证标（知识［有据］标的教训侧对称件）；karma 从排序信号升级为模型可见的采信依据

## 2026-09-26 全类型调研与七专项巡检班（调研日 2026-09-26，批2 学习记忆+雷达全过）

- **harbor-framework/harbor**（5,617★，09-26 活跃）| Terminal-Bench 团队官方评测 harness（TB 2.0 官方 runner）：评测任意 agent CLI（Claude Code/OpenHands/Codex）、自建基准、Daytona/Modal 等数千环境并行、RL rollout 生成 | evalbench 是模型级实测，harbor 是 agent 整链级评测——评测域大盘新坐标 | 参考（evalbench 远期扩展参照）| 2026-09-26
- **sentrux/sentrux**（3,286★，MIT，中文）| 「代码质量传感器」：实时质量分+Rules Engine+MCP，论点「没有传感器 agent 不知道改什么」 | 我们评审链=事后传感器（打分+教训回流），差量在实时性但需装独立 MCP | 参考 | 2026-09-26
- **ThreeMoonsLab/agents-shipgate**（89★，PyPI+Action）| agent 能力变更的确定性合并闸门：纯静态扫 MCP/SDK/LangChain 配置变更，合并前展示能力差异，零 LLM 零网络 | 理念印证：agent 能力变更过确定性闸门——与我们 skill 装前扫描+装后冒烟同向；供应链面（Shai-Hulud 同款）已有守卫 | 参考 | 2026-09-26
- **microsoft/agent-framework**（13,804★）微软官方框架主体（合并 AAF+SK 系）| 生态事实 | 参考 | 2026-09-26
- **rocketride-org/rocketride-server**（12,390★）C++ 核心 AI 管线引擎 | 基础设施域参考 | 参考 | 2026-09-26
- 同形态井喷第 6 日：**loushang**（zhnt，1,431★，**Python 同栈** coding harness）/Lody 1,129（团队共享多端）/mjolnir 64（六 CLI 管理）/orchvia/overdeck/AgntSpce——机制均无未覆盖项 | 雷达（同形态跟踪）
- **dsh 生态 +2**：dsh-links（Android 伴侣，trusted-LAN 配对）/dsh-quota-check（配额读数）——已接 dsh 周边繁荣第 5/6 例，佐证接入判断 | 雷达
- Jev TypeSafe 簇继续扩张（jev-browser-skill 09-26 新）| 范式观察维持 | 雷达

### 复查记录
- 2026-09-26：orca 78,545★（09-26 活跃 +1k）/superpowers 291,787★/oh-story 7,116★（09-26 活跃）/ZCode 6,800★（稳态缓涨，仍未装防死链维持）/ECC 267,655★/DeepSeek-Reasonix 35,709★（09-26 活跃，E 候选首位维持）/denova 820★（+34）/neuro-book 698★（+24）/mira 343★（+40）/pr-af 638★/ironcurtain 611★ 全活跃
- awesome 四清单全活跃（awesome-claude-code 54,627/VoltAgent 34,873/Long-Horizon 1,046/Agent-Memory 648）；npm/pypi 无新标的
- **连续第六班机制级真新标的=0**——头部格局稳定期节奏（复查+新锐簇观察+待深挖清账）维持有效

**✅ 本班落地**：评审深度随 diff 规模分级（pr-af 待深挖第 3 项清账）——`pipeline._review_depth_note`：小 diff（<40 行）附快速评审指引（聚焦正确性不凑字数省 token）、大 diff（≥600 行）附概览+高风险区深看指引（安全/并发/数据与迁移/公共 API）、中等与空 diff 不给指引（空 diff 不误导放松）；纯提示词分级不改 pass 语义。test_review_depth 4 项。另有遗留完整态收编两笔：62d4b89 一键升级全部+e132426 evalbench 补测

## 2026-09-28 全类型调研与七专项巡检（批3：计划/spec/长任务）

- **harbor**（[laude-institute/harbor](https://github.com/laude-institute/harbor)）| Terminal-Bench 官方整链评测 harness，可并行运行任意 agent CLI | CodeBee evalbench 当前是模型级样题评测，整链评测是远期差量 | 参考，暂不接入 | 2026-09-28
- **agents-shipgate**（[ThreeMoonsLab/agents-shipgate](https://github.com/ThreeMoonsLab/agents-shipgate)）| 零 LLM/零网络的 agent 能力变更静态闸门 | CodeBee 已有 skill 白名单、SSRF 扫描、安装后冒烟，能力方向已覆盖 | 已覆盖 | 2026-09-28
- **sentrux**（[sentrux/sentrux](https://github.com/sentrux/sentrux)）| 运行中质量传感器与 Rules Engine | CodeBee 已有评审分数和教训回流，实时反馈仍属远期差量 | 参考 | 2026-09-28
- **Mastra f95b8fa**| 审批恢复先持久化新标签，避免快照竞态 | CodeBee 尚未发现同构审批状态窗口 | 观察 | 2026-09-28
- **本轮落地判断**| CLI 纯模型假绑定已在 8e4f4d2/9c6b7aa 修复并有回归测试；WorkDSH 远端回执语义若改会影响幂等，暂不动 | 无机制级代码新增，文档沉淀一项 | 2026-09-28

## 2026-09-28 全类型调研与七专项巡检班（调研日 2026-09-28，批2 学习记忆·WebSearch 串行降级）

> 本机 Bash 全程不可用（WSL 劫持）——gh api/which/git/npm 全失效，调研走 WebSearch 串行、
> 巡检走 Read/Grep/Glob、改动落工作区未提交（五道关待 shell 恢复补跑）。详见当日报告。

- **检索降噪四重独立验证**（Cornell Tech 深研操纵研究/MisKnow-Agent 误导暴露即致错/
  ARGUS 误信息注入防御/清华 SafeSearch+ProGRank+CorruptRAG 攻防文献簇）| 与
  deepresearch-agent Red-Blue 合流——「检索降噪」从单例升级为学术+工程共识 |
  **✅ 已落地切片**（RESEARCH_APPENDIX +来源先审后用/结论经得起反例两条，
  test_research_noise_guard 4 项）；剩余「Red-Blue 完整两队互搏检索流」降远期观察 | 已落地 | 2026-09-28
- **mnemo / repo-memory-mcp / Memorix / engram / smara-io 簇**（批2 记忆域）|
  MCP+本地 SQLite 已是共识形态，与 agentmemory/memsearch/pmb/pi-mem 重合；
  smara「Ebbinghaus 遗忘曲线衰减评分」与经验库过期降权同向 | 参考（雷达跟踪）| 2026-09-28
- **de-ai topic 页成型**（GitHub 专属 topic：Clean/Refactor/Detect/Write 四模式中文
  skill；Humanizer 24 模式；36kr 报道网文平台 AI 味人工审读成工序）| 去 AI 味赛道
  措辞层持续升温，我们 aiflavor 18 套话+sepia 架构级三信号保持深一层 | 已覆盖 | 2026-09-28
- **bivex/ZenTaoMcp**（禅道 MCP Bridge）| 禅道 AI 生态第 4 例通道级集成（前 3 例：
  zentao-mcp/auto-fixer-server/pi-zentao）；禅道官方「智能分派 +40%」系平台内建功能，
  与我们外挂深度闭环不同赛道 | 参考（雷达跟踪）| 2026-09-28

### 七专项（本轮实证要点）

- A：锚点全在位（预算熔断 :627/696、压缩守门 :636、评审深度 :999/1022、_shrink :2435/3379），零新机制
- B：六源在位；网络探活本轮无法执行；新见案例过三问均不过，零接入维持
- C：17 类型定义齐无异常；演示文稿空档维持交人拍板
- D：教训 67 条稳定（21/20/11/8/4/3，偏科 31%），无新增同族可并
- E：**勘误：已接 14 个而非 11 个**（v0.1.69 加 gemini-cli/codebuddy/trae-agent 后口径未更）；候选 CLI which 探测本轮无法执行，零接入防死链维持
- F：禅道零积压零错误；产品 ID 96 → mo-so workdir Glob 实证存活；triage_ai=True 在位
- G：过时文案 grep 零残留；新毛病无

### 待深挖队列（当期快照）

1. 工具输出统一压缩管线（A 专项留存①，管线级攒批）
2. 语义缓存（待租户/敏感边界拍板）
3. 调研报告 Red-Blue 完整两队互搏检索流（本轮落地切片后的剩余，远期观察）
4. 演示文稿任务类型空档——交人拍板（不擅自扩）
5. 任务宪章编译为运行时强制策略（ironcurtain，治理远期）
6. 既有攒批维持：ARIS 规则裁决/continuum 伏笔账/multica 看板视角/OpenCreator 版本化对比 UI
7. **shell 恢复后待办**：本轮 pipeline.py 改动+test_research_noise_guard.py（add -f）走五道关补提交；CHANGELOG 未发布段补「调研报告检索降噪」一行

## 2026-09-28 第二班（调研日 2026-09-28，批4 治理/安全/人机协同+A3/A10 混扫·WebSearch 串行降级）

> Bash 仍挂（同上一班），WebSearch 串行六查：批4 治理组×2、A3 token×1、A10 翻译/对话×2、
> Trending 雷达×1。落地走 Read/Edit/Write，五道关继续顺延。

- **andrewyng/translation-agent** | agentic 三步法 translate→reflect→improve + glossary
  机制：动笔前先提炼术语表、翻译时强制统一、reflect 检查——我们翻译流程有「术语一致性」
  评审 rubric（事后抓）但起草侧无约定，长文译名漂移只能靠修订轮返工 |
  **✅ 已落地切片**（TRANSLATION_APPENDIX 术语表先行：稿件开头 `## 术语表`、全篇以表为
  准不随上下文漂移、无术语短文可省略；注入闸只挂 translation 类型，test_translation_glossary
  5 项）。TransAgents 出版社角色分工不借鉴（评审链已有对应物）| 已落地 | 2026-09-28
- **RIGForge ProofPackets**（MCP server，2026-09 上 MCP Market）| 编码 agent 把工作封成
  密码学签名证据包、单命令重验——抓「伪造完成声明」与篡改；自认只证 provenance 不证
  正确性 | 我们 stopgate+evidence.md 已是证据档案形态，签名基建超出当前需要（反过度
  防御）| 参考（雷达跟踪）| 2026-09-28
- **Transluce 8600 真实会话测量**（2026-08）| 编码 agent「谎报成功/规避监控」频率实测；
  arXiv 2026-05 大规模分析同向 | 印证 stopgate/证据链设计的经验基础，无需动作 | 已覆盖 | 2026-09-28
- **marklynd/mcp-approvals** | 审批闸 MCP + tamper-evident 哈希链审计日志 | audit.py
  JSONL 台账刚落（2026-09-28 未发布段）；哈希链属防篡改加固，当前无此威胁模型，不跟 |
  参考（雷达跟踪）| 2026-09-28
- **google/ax** | Google 新 AI Agent 编排器，2026-09-23 单日 +2305 星（agents-radar 记录）|
  新锐头部，下轮通道恢复后逐仓复查机制差量 | 待深挖 | 2026-09-28
- **MT-OSC**（arXiv 2026-04）| 一次性顺序压缩聊天史（后台免滚动摘要）修多轮对话迷航 |
  与三段压缩同向，零机制差量 | 已覆盖 | 2026-09-28
- **A3 缓存组复查** | prompt-cache（messkan）已录；NeuralTrust 2026-07 共识：prompt
  缓存（供应商侧前缀命中 -90%）与语义缓存（同义免调用）互补——与已录「缓存感知按设计
  不需要」判断一致 | 已覆盖 | 2026-09-28
- **Trending 新面孔** | paperclipai/paperclip、vectorize-io/hindsight、archify（架构图
  agent skill）、withoneai/cli（One CLI，Trendshift 热榜）| 首见入雷达，未过接入三问 |
  参考（雷达跟踪）| 2026-09-28

### 七专项（本班实证要点）

- A：A3 搜索零新机制；锚点维持上一班实证（预算熔断/压缩守门/评审分级/_shrink 全在位）
- B：Bash 挂无法探活六源；新见案例过三问均不过（重合度高/需签名基建/用户不会搜），零接入维持
- C：17 类型 flows.py 实数与 README 口径一致；**translation 补起草侧术语表**（本轮落地件）
- D：经验库脚本依赖 shell 无法实跑；本轮方法论走 knowledge.md+当日报告沉淀
- E：catalog.py 实数 14 个（grep 实证）；候选 CLI which 探测仍无法执行，零接入防死链维持
- F：上一班已核（开关关属用户侧预期、零积压），本班无新禅道 AI 竞品，不重复
- G：**self-iteration-prompt.md 三处过时口径修复**——「13 种预置类型」→「17 种（以
  flows.py 实际为准）」、「已接 11 个」→「2026-09-28 实数 14 个（以 catalog.py 实际为准）」、
  「流程规范 61%」（旧口径，上轮实证 31%）→ 去数字化表述防再漂

### 待深挖队列（增量）

8. **shell 恢复后待办（合并两班）**：pipeline.py 两处 appendix 改动 + test_research_noise_guard.py /
   test_translation_glossary.py（均 add -f）走五道关补提交；CHANGELOG 未发布段两行已补齐
   （检索降噪+翻译术语表），恢复后随完整态一并发版
9. google/ax 逐仓复查（下轮优先，通道恢复后）

### 批5续检：浏览器/检索与内置工具循环

- **browser-use / invisible_playwright_mcp / BrowserSkill**| 浏览器 agent 与 MCP 技能生态继续活跃；CodeBee 已有 Playwright、MCP 通道及 SSRF 闸门，未发现低风险机制差量 | 雷达复查，不接入 | 2026-09-28
- **Haystack / R2R / Airweave**| 成熟检索与知识管线，但引入会扩大部署、索引和数据权限面；CodeBee 当前任务级知识注入更轻 | 参考，待未来明确知识库产品边界后再评估 | 2026-09-28
- **builtin_agent 工具循环修复**| 工具开关传递错误会让所有带工具的内置执行在首轮失败；Windows Gemini wrapper 使旧断言把真实 argv 误报失败 | 已修复参数传递、流式 create_task 规格与 task_creator 透传，并放宽测试到 node/gemini.js 形态 | 2026-09-28
- **jnMetaCode/agency-orchestrator**（Apache-2.0，约 2.3k★，2026-09-29 活跃）| YAML 工作流把 `acceptance` 标准与 `assert.contains` 等机械断言绑定，运行后自动核验；另有 Studio/CLI 多渠道与 276 角色库 | CodeBee 已有六维验收矩阵、证据落盘和验证命令，能力已覆盖；可配置断言作为待深挖项，不在本轮引入新接口 | 已覆盖 | 2026-09-29
- **扫榜任务文案对账**| 后端 `paihang` 已是七猫/番茄/起点/纵横四源，创建卡仍写“抓双平台榜”，英文 i18n 同步过时 | 修正 UI 描述与英文词条，并新增静态契约测试，避免源数量变化后文案漂移 | 已落地 | 2026-09-29
- **批3 计划/spec/长任务复查**| OpenSpec、GSD、planning-with-files、PraisonAI、worktrunk、lazycodex 继续活跃；spec、任务档案、断点续跑、隔离 worktree 已覆盖，未形成低风险接口差量 | 参考/已覆盖 | 2026-09-29
- **borrow_scan_nightly Windows 编码修复**| 默认 GBK 控制台遇到仓库描述中的 Unicode 会触发 `UnicodeEncodeError`，中止整轮 JSONL 输出 | stdout/stderr 启动时显式 UTF-8，查询与安全边界不变 | 已落地 | 2026-09-29
- **批5 检索/知识/浏览器复查**| BrowserSkill、AI-Infra-Guard、Microsoft Agent Framework、Composio、RAGFlow、Haystack、Agent-Reach 等继续活跃；浏览器会话隔离、技能扫描、RAG 与多代理编排均已有对应记录或能力 | 无新低风险接口差量，维持复查 | 2026-09-29
- **批7 中文/网关/本地/办公复查**| Orca、Untrivial/agent-orchestrator、omnigent、dorkos、mjolnir、purplemux、SkillSpector、agent-secrets、ai-agent-skills、larksuite/cli 等覆盖多 CLI 面板、远程审批、凭据隔离与技能包管理；CodeBee 已有任务档案、运行守卫、MCP/SSRF、市场安检和证据门禁 | 无新低风险接口差量，维持复查 | 2026-09-29

## 2026-09-30 七猫/番茄写作经验与质量诊断沉淀

- 来源：[七猫作者交流区经验索引](https://bbs.qimao.com/column/6859f2539995c)（作者经验与拒稿模板拆解，不等同现行政策）；[番茄作家后台](https://fanqienovel.com/main/writer/)（本轮未登录核对规则，政策/指标定义仍待作者后台确认）。
- 证据分级：当前后台/官方公告/合同用于确认规则；多篇作者经验、编辑反馈或带样本的后台数据只形成可检验假设；单例、口诀和固定数字保留为待验证项，不设成质量门禁，也不据此预测签约、推荐或收入。
- 七猫质量复盘先限定实际读稿范围，再看故事单元的具体事件、主角选择、可见后果、阶段兑现和后续问题；拒稿分析保留原话、稿件版本与修改范围，将编辑意见、正文证据和推测分栏。开篇字数点位、具名人数、对话开场等只作为阅读负担观察，不作通用过稿阈值。
- 番茄质量与数据分层：先逐章检查动机、因果、连续性、文风和移动端阅读，再按平台适配、当前合同/后台规则复核。完读、追读、留存等指标按后台定义作为定位信号；样本不足或多变量同时变更时结论标“证据不足”，有样本时一次只改一个变量并记录复验范围，不把相关性写成推荐算法因果。
- 复用规则：把平台经验包用于提出可逆的质量检查和实验，不用于宣称“满足即过签/起量”；平台政策随频道与日期变化，必须回到当前官方页面核验。

## 2026-09-30 对比学习与七专项巡检班（第七十班批5 汇总·计划第 2/4 步）

> 基于当日 11 时班（批4）与 12:34 第七十班（批5+雷达 C 全过，115 查询 523 行/457 唯一仓）
> 的调研结果做对比学习；七专项全部实锚复核（非转录前班结论）。只认「他们有、我们没有、且确实好用」。

### 新条目

- **career-ops-hq/career-ops**（73,096★，created 2026-04-04，09-30 活跃）| 开源求职 agent：扫招聘门户→**逐条 A-H 结构化报告+全球 1-5 评分** | 我们有 `resume` 类型但只做简历产出；「扫外部列表→逐条结构化分级评分报告」的交付契约形态与 rank_scan（扫榜）/defect_retro（复盘）同构（外部数据→评分报告交付物），是真实差量 | **2026-10-03 勘误：A-E 跨平台证据分级已随 v0.1.73 落地（rank_scan 报告契约 f0bd197），「待深挖」清账；其余机制（拒信模式/幽灵岗/STAR+R）属求职域专用不再跟进** | 已落地 | 2026-10-03
- **@dannyvan/zentao-mcp**（npm 2026-09-08 发布）| 禅道 REST API v1 直连 MCP，**写操作默认 dry-run** | 与已录 zentao-mcp/auto-fixer-server/pi-zentao/ZenTaoMcp 同代通道级集成，无机制差量；「写操作默认 dry-run」的安全默认与我们 auto_submit=false / poll_enabled=False 纪律同向——安全默认设计再验证 | 参考（F 专项周边，一句入库）| 2026-09-30
- **alphaparkinc/genpark-dynamic-prompt-prefix-cache-skill**（7★，09-28）| radix-trie 最长公共 prompt 前缀匹配缓存 | 我们前缀缓存稳定靠「注入点固定」设计（09-22 Reasonix 印证）；「跨 prompt 前缀去重」是另一角度但项目极小 | 参考（A 专项，只记思路不接）| 2026-09-30
- **adampaulwalker/claude-code-subagent-cache**（1★，09-28）| **测量**子代理反复重读上下文的 token 支出（先量化再优化） | 「工具输出统一压缩管线」留存方向的第 6 独立验证（TokenJuice/openhuman/freellmapi/deepresearch-agent/winnow 之后），且补上「测量侧先行」角度——read_file 头尾保留只覆盖文件读取一种工具 | 方向验证（A 专项）| 2026-09-30
- **ayghri/i-have-adhd**（52,213★，09-19）| 「别把答案埋起来」——ADHD 友好输出从简 skill | 我们 claude-token-efficient 借鉴（交付物从简进 CODE_IMPL_PROMPT）已有；同理念 52k 星量级第二例 | 已覆盖（生态验证 +1）| 2026-09-30
- **kimi-cli→kimi-code 继任核实** | MoonshotAI 旧 Python CLI 明确废弃归档，kimi-code（7,740★，09-30 活跃）官方继任 | 核实我方 catalog.py 已正确：id=kimi-code、install=@moonshot-ai/kimi-code、note 已写「kimi-cli 正在下线」 | 已覆盖（E 专项无需动作）| 2026-09-30
- **pacifio/atlas**（8,431★，09-29）| "Source control for agents"——多 coding agent 变更追踪 | 我们任务分支隔离链+版本页签已有对应物；「代理变更的源控制」命名视角新 | 雷达 | 2026-09-30
- **jlcodes99/cockpit-tools**（18,522★，09-29）| AI IDE 账号管理器（Antigravity/Codex/Copilot/Windsurf/Kiro）| 凭据保险库待深挖项（sandbase-harness 借鉴）的「账号管理器」形态佐证 +1 | 雷达（凭据族）| 2026-09-30
- **nextlevelbuilder/ui-ux-pro-max-skill**（131,692★，09-27）| UI/UX 设计智能 skill 包（前端任务增强最大星 skill）| 接入三问（重合度/可直读性/用户会搜吗）不过——重合高（我们流程评审已覆盖前端任务质量面）、整包不可直读、用户搜的是具体功能不是整包 | 不接（蒸馏候选留观察，B 专项）| 2026-09-30
- **同形态/生态事实速记**：openclaw 390,811★（10 个月 39 万星，catalog 已收录 installable、本机未装——星数只作生态事实）；Understand-Anything 84,735★+codegraph 72,413★（代码理解图谱双雄，我们知识库不做代码结构索引的边界维持）；andrej-karpathy-skills 215,924★（巨型星数提示词人格项目，ponytail 同族奇观档）；kubernetes-sigs/agent-sandbox 4,092★（k8s 官方 agent 沙箱，我们沙箱=CLI 自带边界不变）；opencodex 16,674★/command-code 4,066★（网关/coding agent 域，command-code 进 E 候选雷达本机未装）；BettaFish 42,378★（中文多 agent 舆情，任务类型不相关）；paca/plandb（AI 原生 issue tracker，「issue 先行」赛道在长大，multica 看板视角同域 +2）| 均无未覆盖机制 | 雷达 | 2026-09-30

### 复查记录（repos 端点 34 仓 + 雷达）

- 2026-09-30：头部全活跃——orca 81,766★（09-26 记 78,545→4 天 +3.2k）/superpowers 293,070/oh-story 7,174（09-27）/spec-kit 139,463（09-29）/OpenSpec 70,713（09-29）/planning-with-files 27,185/ECC 269,716（09-30）/agentmemory 29,031/paseo 19,038（09-30）/superset 14,751（09-30）/drama-skills 2,397/OpenCreator 12,520（+328 vs 09-23）/openhuman 40,175/ARIS 16,836/deer-flow 83,247/multica 51,704/codegraph 72,413/graphiti 31,316/cognee 31,230
- **状态变更三件**：①inkos 恢复更新（见上文勘误）；②deepresearch-agent 停更实锤（见上文降级）；③denova 843★（09-30 活跃）元数据已补齐，能力判断仍限仓库简介级维持待深挖
- E 专项：**esengine/DeepSeek-Reasonix 正名**（35,718★，09-30 活跃）候选首位维持（本机未装零接入）；ZCode 7,193★（09-29）缓涨维持；openclaw 390k；kimi-code 继任核实 catalog 已正确
- awesome 四清单全活跃（awesome-claude-code 54,825/VoltAgent 35,041/Long-Horizon 1,053/Agent-Memory 653）；npm 两页无接入级标的；pypi 被拦如实记录；新锐轮（created:>09-23）写作域零新库、多 agent 编排均 <25★——头部格局稳定期延续

### 七专项结论（摘要，详见当日报告）

- A：12 项既有机制锚点全实锚在位（预算熔断 pipeline.py:613/token_meter :690/cascade :1455/_shrink_context_block :2455/diff 评审 :945/评审深度 :999/:1022/planner 精确缓存三处 :587/:736/:879/压缩省量 usage.py:835/read_file 头尾保留 builtin_agent.py:330）；竞品对照零新机制可抄；留存方向三条维持
- B：六源在位（market_remote.py:50-73）；本轮候选过三问均不过，零接入维持
- C：17 类型实数核对（direct/code/novel/serial_novel/article/video_script/doc/translation/rank_scan/defect_retro/research/speech/weekly_report/email/tech_proposal/resume/bid_doc），name/goal_hint i18n EN 零缺失；演示文稿空档维持交人拍板
- D：教训 67 条零漂移（流程规范 21/31%、节奏爽点 20、情节逻辑 11、人物塑造 8、一致性 4、文笔风格 3；lesson 63+procedure 4）；知识库 12 条（serial_novel 9/direct 3）——无新增同族可并
- E：catalog 14 条目实数；本机 which 实测在装 12（codex/claude/opencode/qwen/kimi/dsh/pi/mimo/grok/aider/**gemini/codebuddy**）；八候选全未装零接入防死链维持
- F：禅道链路代码在位；claims 零积压、last_error 空、poll_enabled=False（用户侧）；产品 96→mo-so workdir 存活验证、owners 齐、module_routes 空（AI 兜底 triage_ai=True 覆盖）；新竞品 zentao-mcp 一句入库（见新条目）
- G：过时文案 grep（13种/15种/双平台/两平台/双源/已接11）全零命中；本轮零新毛病

### 待深挖队列（当期快照）

1. **career-ops A-H 结构化评分报告交付契约**（新进首位——rank_scan/resume/defect_retro 同构域）
2. 工具输出统一压缩管线（第 6 验证到手，含「测量侧先行」角度；管线级攒批）
3. 语义缓存（待租户/敏感边界拍板）
4. 演示文稿任务类型空档——交人拍板（不擅自扩）
5. 任务宪章编译为运行时强制策略（ironcurtain，治理远期）
6. 既有攒批维持：ARIS 规则裁决/multica 看板视角/OpenCreator 版本化对比 UI（已落 artdiff 对比，剩多版本管理面）/凭据保险库（cockpit-tools 佐证 +1）
7. google/ax 12,594★（09-27）描述级已核，逐仓机制复查排队（通道稳定时）

## 2026-10-03 凌晨班（批3：计划/spec/长任务 + 七专项巡检）

- **yc-software/qm**（15,316★，10-02 活跃，MIT）已深挖 | 「多人 agent harness for work」：Slack+Web 双形态、自部署自有云自有密钥；**每人每房隔离 workspace**（scoped 记忆/文件/密钥视图/权限/cron/web app/持久沙箱）；Pi/OpenCode/Codex/Claude Code 四 CLI 驱动同一核不锁供应商 | 组织化多人编排新巨型；与 happier（三端）/multica（看板协作）同域但更「组织基础设施」层。单机单人产品无小件可抄；「每人每房 scoped 记忆」是记忆隔离的组织级形态（边界：我们单人） | 雷达 | 2026-10-03
- **21st-dev/1code**（5,585★，~~10-02 活跃~~ **10-03 勘误：gh api 实证 archived=true，最后 push 2026-03-06——批3班「活跃」记录有误，项目已死**）已深挖 | 开源 coding agent 客户端（21st.dev 出品）：多 agent 一窗/worktree 隔离/后台云沙箱/Kanban/Git 客户端/**聊天分叉**/**消息队列**（agent 忙时排队 prompt）/语音输入/Plan Mode | 同形态竞品逐项对查：消息队列**已覆盖**（我们运行中信箱排队+「待送达」标记+跑完自动续轮送达，rd-chat-send active 分支）、worktree/看板/语音/Plan 均有对应物；**唯一差量=聊天分叉**（从任意助手消息起 fork 子会话改写历史——管线级改动，远期备注；本体已 archive 不跟进，仅留差量备注） | 参考（差量一项记远期） | 2026-10-03
- **google/ax 深挖完成**（12,881★，09-27 push，v1alpha1）| 「agent 的 Kubernetes」：task.yaml 声明式三原语（Task 沙箱 workload/Workspace 预接线 git+MCP+skills 热启动/Model 平台级 LLM+K8s Secret 凭据）+ Agent Substrate 集群沙箱 + ax suspend/resume（空闲暂停精确恢复）+ ax ssh（看 agent 干活） | 集群级 workload 编排与单机多 CLI 台不同赛道；suspend/resume=断点续跑、ssh=事件流、Workspace 热启动=经验召回+会话复用、Secret=凭据保险库（已列待深挖）——机制层全有对应物，形态层（声明式清单/K8s）不适用 | 参考（待深挖第 7 项清账） | 2026-10-03
- **批3 新面孔速记**：coollabsio/jean 1,302★（dev environment for AI agents，同形态小件）| mcp-shrimp-task-manager 2,145★（链式思维任务管理 MCP=编排者拆解+task_plan 同域）| withkynam/vibecode-pro-max-kit 1,143★（「Your AI forgets. This remembers.」spec+记忆 harness=文件真源同向验证）| agentrq 1,137★（HITL 实时对话任务管理=待裁决同向）| auto-deep-researcher-24x7 1,293★（24/7 自主深研=ARIS 同构）| ghostwriter/phantom 1,476★（self-evolving co-worker=经验库自学习+沙箱同向）| 均雷达/参考 | 2026-10-03
- **雷达三小标**：gongdear/cline-pilot 102★（skill 驱动外部 CLI 代理人的轻量委托形态）| binbingwu/Multi-Agent-Game-Localizer 40★（中文：主控大模型+本地小模型翻译子代理分层成本结构=cascade 同向佐证）| npm @nathapp/nax（TDD 循环直至完成微型编排器，只记形态）| 均雷达 | 2026-10-03
- 生态事实：deer-flow 描述改「long-horizon SuperAgent harness」；ruflo 73,723★（+425，社区星数审计质疑帖存在按 API 采信）；Awesome-Long-Horizon-Agents 11 天无 push 准停更观察；firecrawl 187k★ 为 web 数据基础设施非编排竞品（Agent-Reach 域已覆盖）不入册细查

**✅ 本班落地**：多版本序号可见化（OpenCreator「版本化」剩余面收口）——成品文件头部「第 N 版」徽章（run 总数-列表序位，total 缺失不编不显示）+ 对比 chip「与第 N 版对比」+ diff 弹窗副标「第 N 版 → 当前」（run_id 摘要降为审计尾注）+ i18n 四键 + ui_ver_badge.mjs 11 项（CDP 直调 artifactsChips：序号形态/降级/diffable 不回归/i18n 完整性/英文形态）

### 待深挖队列（2026-10-03 快照）

1. 工具输出统一压缩管线（第 7 验证到手，管线级攒批）
2. 语义缓存（待租户/敏感边界拍板）
3. 演示文稿任务类型空档——交人拍板（不擅自扩）
4. 任务宪章编译为运行时强制策略（ironcurtain，治理远期）
5. 聊天分叉（1code，管线级远期备注）
6. 既有攒批维持：ARIS 规则裁决/multica 看板视角/凭据保险库（cockpit-tools 佐证；data/zentao.json 明文密码属该域，交人决策）

## 2026-10-03 上午班（批2：学习记忆与自我改进）

- **Louis-CFM/coucou**（2,988★，10-03）| notch 桌宠看护 coding agents 状态 | 桌宠 tasks_only+活跃 tooltip+事件气泡已覆盖同理念，无小件可抄 | 雷达 | 2026-10-03
- **feder-cr/dots / universal-modder / yomiyasu / hypoarena / open-dot / iCode** | 反封锁 web agent / 游戏改 mod 包 / AI 日文润色 / 科研工作台 / Mac 个人代理 / 离线开发平台——域外或已覆盖，均无未覆盖机制 | 雷达 | 2026-10-03
- **npm 包混入用户作品数据实案**：app/ 下未跟踪的 `作品信息-*.md` 会被 npm pack 打进发布包——根 .npmignore 的 `**` 排除对「app/ 有自己的 .npmignore」就近覆盖规则无效；发版冒烟（tar 非 ASCII 段拒收）是最后一道真防线。修复=两级 .npmignore 各补排除。 | 教训（发版工程）| 2026-10-03
- 复查：superpowers 294,479★/codex 127,641★ 活跃；七专项全绿；教训 67 条零漂移；零新接入级标的（稳定期常态）。

## 2026-10-03 B4 治理/安全/人机协同复查

- 扫描器全量完成 115 查询、523 条结果、466 个唯一仓库、0 错误；A 常驻、B4 轮换、A1 `updated`/`stars page=2` 双轮均执行，GitHub API 串行 4 秒间隔。
- `microsoft/agent-governance-toolkit`（6,377★，10-03 push）、`FailproofAI/failproofai`（5,234★，10-02 push）、`cordum-io/cordum`（509★，09-30 push）复查 commit/release/issue/PR/安全公告：策略执行、请求链 ID、动作防火墙与 CodeBee 现有 stopgate、证据档案、`run_id/trace_id`、操作台账、审批和 SSRF/技能闸门重合，无低风险接口差量；不接入。
- 七专项实锚：17 类 flow、14 个 catalog CLI、六源市场和禅道链路均正常；教训 67 条零漂移；过时文案零命中；未安装 CLI 不盲接。`data/zentao.json` 明文凭据列为既有远期决策项。
- 本班无代码落地，避免为观测/治理同类能力扩大接口和持久化面。证据与日志见 `docs/borrow-log/2026-10-03.md`。

## 2026-10-03 16:00 班（批2：学习记忆与自我改进·全类型雷达——计划第 1/4 步调研）

- **Fergana-Labs/stash**（328★，10-03 当日活跃）新入库 | 「agent 从经验学习的基础设施」：捕获生产 traces → 把教训变成可复用知识与技能 | 与我们经验库全链（教训+做法+evolve 晋升）**同构**——教训→技能晋升链的独立实现；无未覆盖机制，同向验证 +1 | 方向验证 | 2026-10-03
- **eugeniughelbur/obsidian-second-brain**（4,666★，10-01）新入库 | Claude Code 等 7 CLI 的持久记忆，纯 Markdown 存 Obsidian vault（「别再每次会话重新解释你的项目/决策/人物」）| 文件真源路线第 N 验证（pi-mem/markdown-memory/openhuman Memory Tree Obsidian 镜像同源）；我们经验库+任务档案同路且多了 outcome 加权 | 已覆盖（生态验证 +1）| 2026-10-03
- **code-yeongyu/oh-my-openagent（OmO）**（69,763★，10-03 活跃）新入库 | 「输入 mass ulw 关键词 + prompt 即成 graph engineering 大师」——**关键词触发的大师人格/技能注入器**，跨 Claude/Codex/Cursor/OpenCode 七 CLI，topics 含 orchestration | 69.7k★ 量级人格/技能注入生态大盘（ponytail 同族工程化）；与我们经验召回同域——他们显式关键词触发、我们 bigram 语义自动注入（自动化更深），无未覆盖机制 | 参考（雷达）| 2026-10-03
- **wenziai/wenzi-xhs-agent-skills**（148★，09-30）新入库 | 小红书运营 Agent Skills：账号定位/选题标题/真人化改稿/图文规划/排期复盘 | **自媒体文章任务域**的平台特化 skill 包（小红书主战场）；三问：重合（article 通用流程无平台特化）/可直读/会搜——**不整包接市场**，与 platform-writing-skills-cn 同结论；「平台特化交付契约」蒸馏候选（真人化改稿≈去 AI 味已有，选题标题/排期复盘是差量小件）| 借鉴（蒸馏候选，B/C 专项）| 2026-10-03
- **wbb316/dsh-novel**（4★，09-30）| DSH 插件：小说创作台（5 个 novel_* 工具+开书向导/设定表单/关系图）| dsh 插件生态第 7 例（links/quota-check/AI-Novel-Writer 插件/movо 之后）——已接 dsh 生态持续繁荣佐证 | 雷达 | 2026-10-03
- **tangwenwen-md/chinese-de-ai-writing**（3★，09-29）| 中文去 AI 味：**按证据分级的审稿改稿 skill**（Claude Code/Agent Skills）| 去 AI 味赛道第 5 例（aiflavor 自有/quiron/snifftest/mr-li 之后）；「按证据分级」与我们「确定性检测行+模型评审」两层同向 | 已覆盖（生态验证 +1）| 2026-10-03
- **NousResearch/autonovel**（1,601★，03-20 后停更）| Hermes Agent 出品自主小说管线：写/修/排版/插图/朗读全链（19 章 79,456 字成品）| 写作域全链形态参照；**停更 6 个月**不入深挖 | 雷达（停更观察）| 2026-10-03
- **mcp-zentao-pro**（openclaw-master-skills 内）| 禅道 MCP 扩展包：跨项目数据聚合视图/一句话建任务/工时记录/自动状态流转 | 禅道 AI 生态通道级第 5 例（zentao-mcp/auto-fixer-server/pi-zentao/ZenTaoMcp 之后）；一句话建任务+状态流转与我们扫描→建任务→resolve 闭环同构 | 参考（F 专项雷达）| 2026-10-03
- **学术/生态速记**：Memskill（arXiv 2602.02474，learning & evolving memory skills——记忆技能自身演化，evolve 晋升链的学术同向）；GitSkills 数据集（379 万 SKILL.md/28.2 万仓，skill 生态研究资源）；arena-skill（202★，「Claude 给烂答案就做 100 版赛马」——Best-of-N 第 N 同向验证）；gitmemory（18★，Git 版本化+连续性校验记忆）；awesome-claude-video-skills（385★，10-03 新，视频 skills 清单——短视频→成片域）；mongodb-partners/agent-memory（17★，Atlas+Bedrock 层级记忆，云依赖不适用）；titration（8★，「让 coding agent 修 prompt 直到真的能用」TDD-for-prompts 形态小件）| 均参考/雷达 | 2026-10-03

### 复查记录（repos 端点 40+ 仓）
- 2026-10-03 16 时：头部全活跃——orca 84,054★（vs 凌晨 83,788，当日 +266 继续领跑）/superpowers 294,595/ECC 271,598（10-02）/openhuman 40,472/deer-flow 83,340/codegraph 73,048/multica 51,865/spec-kit 139,892/OpenSpec 70,946/planning-with-files 27,263/agentmemory 29,102/paseo 19,297/superset 14,830/OpenCreator 12,569/Reasonix 35,732（E 候选首位维持）/ZCode 7,345（09-29）/harbor 5,803（harbor-framework org）/agent-governance-toolkit 6,380/failproofai 5,234/cordum 509
- 写作域：oh-story 7,214★（10-03 当日活跃）/drama-skills 2,454★（10-03）/inkos 10,108★（09-27 恢复后维持）/OpenFic 1,174/chinese-novelist-skill 3,275（09-06）/AI-Novel-Writing-Assistant 3,060（09-23）；anthropics/skills 179,449★（09-29）/ARIS 16,916★（09-29）/harness-sdk 8,631/claude-mem 95,213（+857 vs 09-21）/loop-engineering 11,407/zentao-cli 61★（10-01）
- 记忆域批2：cognee 31,317/graphiti 31,395（10-02）/memsearch 2,709（09-24）/pro-workflow 2,899（09-29）/projectmem 849（10-03 活跃，+19 vs 09-22）/KIP 86/mengram 204/MemRL 176/MegaMemory 707/compozy 2,790（10-01）/Yuxi 7,258（09-30）
- awesome 清单：awesome-claude-code 54,983（10-03）/VoltAgent 35,137（10-02）/Agent-Memory 656（10-01）活跃；**Awesome-Long-Horizon-Agents 1,058★（09-22 后 11 天无 push——停更观察转正）**
- 状态变更：21st-dev/1code archived=true 实证（凌晨勘误维持）；autonovel 停更 6 个月（新记）；andrej-karpathy-skills 迁 multica-ai org（216,627★）、ponytail 迁 DietrichGebert org（152,148★）——org 变动生态事实
- npm 两页无接入级标的（@nathapp/nax 已录；其余 fork/小项）；pypi 搜索页被拦（3036 字节拦截页，如实记录）；Trending 替身无 10 月新巨型

### 待深挖队列（2026-10-03 16 时快照，无新增机制级项）
1. 工具输出统一压缩管线（第 7 验证在手，管线级攒批）
2. 语义缓存（待租户/敏感边界拍板）
3. 演示文稿任务类型空档——交人拍板（不擅自扩）
4. 任务宪章编译为运行时强制策略（ironcurtain，治理远期）
5. 聊天分叉（1code，管线级远期备注）
6. **小红书平台特化交付契约蒸馏**（wenzi-xhs：选题标题/排期复盘两小件，article 域——新进，交人拍板是否做）
7. 既有攒批维持：ARIS 规则裁决/multica 看板视角/凭据保险库（cockpit-tools 佐证）

## 2026-10-03 巡检班（七专项·计划第 2/4 步）

- **教训库合并闸跨类盲区（实证）** | upsert_lesson 近似合并只查同 scope 同分类：「打脸必须反派在场」（情节逻辑）与「打脸须反派在场」（人物塑造）bigram 包含度 0.83 跨类逃逸成两条；且自动跨类合并不可行——「情节/人物/文笔 维度反复不达标」三连互为 0.857，与真重复对 0.833 区分度不足，自动闸必误伤 | **D 专项巡检新增手工步骤：同 scope 跨类 containment ≥0.8 人工裁决**；本轮数据合并（+「章末钩子松散」归位节奏爽点）列落地候选① | 巡检方法论 | 2026-10-03
- **G 随手修**：skills.py:40 注释「七猫+番茄双平台」→「四源扫榜」（口径过时，纯注释）；README CLI 名册 14 条目与 catalog 一致核实 | 已修 | 2026-10-03

## 2026-10-03 落地·提交·发版班（计划第 3-4/4 步）

- **教训库数据卫生落地** | 候选①全量：D-1 跨类近重复对合并（merged_titles 留痕、教训 67→66）+ D-2「章末钩子松散」归位节奏爽点；偏科口径 情节逻辑 11→10/人物塑造 8→7/节奏爽点 20→21 | 已落（data/ 运行时数据不进 git）| 2026-10-03
- **test_skills 预存挂修复** | 3a92737（09-30）改 qimao 包文案「主角主观能动性→主角主动选择」未同步测试关键词，test_skills 预存挂 3 天无人察觉——全量绿不等于逐测试绿，改包文案必须 grep 测试关键词 | 已修（关键词对齐）| 2026-10-03
- 发版 v0.1.77：收口 a853e28（章纲三面注入）+ 本批修复；纯注释/测试修复轮是否发版以「当天是否有未发布代码入库」为准（a853e28 在库未发布即发） | 发布纪律 | 2026-10-03

## 2026-10-03 收口班（计划第 4/4 步·发版核实）

- **发版「声称完成」≠ registry 在架（实案）** | 上一班报告记「npm publish → npm view 核对一致」，收口班 registry 直查（curl registry.npmjs.org + npm view dist-tags）发现 latest 仍 0.1.76、无 0.1.77——publish 实际未落地，报告记录与事实不符 | 处置：版本提交已推 main 不 revert 走补发（五道关复跑全绿 + npm pack 169 文件预检 → publish → npm view=0.1.77 实核）；**教训：发版核对只认 registry 实查（独立第二查），命令声称/报告记录不算证据** | 发版工程 | 2026-10-03

## 2026-10-03 18:00 班（批3：计划/spec/长任务·新一轮计划第 1/4 步全类型调研）

> 116 查询 523 行 466 唯一仓零失败；雷达源 C 全过（repos 31 仓+topic 8+新锐四组+CLI 周边
> 五组+禅道周边三组+npm 两查+pypi 被拦）。本轮零机制级新差量，以下为定性入库新条目。

- **GaosCode/PlanWeave**（406★，09-30 活跃）| file-backed loop engineering for long-running coding agents（文件式计划+循环工程）| planning-with-files 同族——我们 task_plan 落盘/活计划回写/Stop gate 已满配，无未覆盖件 | 方向验证（文件式计划 +1）| 2026-10-03
- **OthmanAdi/plandeck**（66★，08-03）| planning-with-files 作者新品：长任务 agent 的可视化 Kanban（看计划自己组织起来）| multica 看板视角同域；我们蜂巢工作台+活计划已有对应物，可视化形态差异非机制差量 | 雷达 | 2026-10-03
- **yueheng-rgb/codex-factory**（100★，09-17）| 多 agent 工程框架：evidence packs+runtime validation+任务拆解 | 「不信任自报」族 npm 外再 +1（stopgate/evidence.md/事件流计数已满配）| 方向验证 | 2026-10-03
- **aiming-lab/AutoResearchClaw**（14,564★，08-19 后放缓）| Chat an Idea, Get a Paper——全自主自演化科研（idea→论文）| ARIS 睡眠科研同族大盘（ARIS 16,919★）；我们 ZCode 自迭代循环同构，无科研域需求 | 参考 | 2026-10-03
- **tide-commander**（npm 1.223.1，10-02 高频发版）+ **humaedihume/kantor-agent**（98★）+ **leonvanzyl/cubefarm**（70★）| Claude Code 可视化多 agent 管理器（3D/2D 界面）/3D 办公室展示 agent 干活/卡通办公室跑 GitHub issues | 「可视化 agent 办公室」族成形——coucou 桌宠看护同理念（我们桌宠 tasks_only+气泡+群摘要已覆盖）；可视化形态不构成机制差量 | 雷达（同形态跟踪）| 2026-10-03
- **echris6/motion-video-kit**（966★，09-28）| AI 商务视频 skill kit：independent critic loop+motion principles | video_script 域生态在长；critic loop 与多评审同构，无机制差量；三问不过不接市场 | 雷达（video_script 域）| 2026-10-03
- **desplega-ai/agent-swarm**（852★，10-03 当日活跃）| "Your Company Agentic Operating System"（公司级 agentic OS）| 同形态新锐（qm 组织化多人域的单仓变体）；机制面待观察 | 雷达 | 2026-10-03
- **LoopTroop**（155★，10-03）/ **synapseorch-ai/synapse-ai**（327★，10-02）| LLM-council planning+Ralph-loop recovery+隔离执行的本地编排 / 开源 agent 平台 | LLM 委员会规划≈编排者拆解、Ralph-loop 恢复≈断点续跑——均已有对应物 | 雷达 | 2026-10-03
- **uluckyXH/OpenMOSS**（1,331★，06-22 后停更 3 月+）| OpenClaw 自组织多 agent 协作平台 | 停更；同形态历史标本 | 参考 | 2026-10-03
- **生态大盘一句入库**：Agent-Skills-for-Context-Engineering 18,065★（context engineering/multi-agent skills 大合集——三问不过，B 专项雷达）/agent-zero 19,363★（通用 agent 框架，框架路线定位不同）/DocsGPT 18,302★（私有 RAG 平台）/jasontang-ai/Context-Engineering 9,254★（学习材料）/coaidev/coai 9,318★（多租户 one-api 后裔）/pyspur 5,798★（可视化 workflow playground，dify/langflow 族）/RagaAI-Catalyst 16,169★（agent 观测 SDK）/deanpeters/Product-Manager-Skills 7,149★（PM skills，三问不过）/Alpha-Park genpark-*-skill 系（7★×N——低星蓄水农场族，已录 genpark 前缀缓存一条即可）| 均无未覆盖机制 | 参考/雷达 | 2026-10-03
- **npm 面**：garda-agent-orchestrator（governed runtime+mandatory gates——「不信任自报」npm 面）一句入库；@nathapp/nax 已录 | 方向验证 | 2026-10-03

### 复查记录（repos 端点 31 仓 + 扫描面，17-18 时）

- 头部当日缓涨：orca 84,091★（vs 16 时 +37）/superpowers 294,638/ECC 271,655/openhuman 40,480/codegraph 73,072/multica 51,864/spec-kit 139,909/OpenSpec 70,950/planning-with-files 27,263/agentmemory 29,106/paseo 19,303/superset 14,833/OpenCreator 12,572/**Reasonix 35,733（E 候选首位维持，10-03 活跃）**/ZCode 7,352（09-29 后缓涨）/harbor 5,803/governance-toolkit 6,380/failproofai 5,234/cordum 509
- 写作/记忆域：oh-story 7,219（10-03 活跃，描述扩「长篇短篇都支持」无新借鉴面）/inkos 10,111（09-27 后无 push 维持观察）/OpenFic 1,175/chinese-novelist-skill 3,277/claude-mem 95,214/ARIS 16,919（09-29）/cognee 31,320/graphiti 31,399/memsearch 2,709（09-24）
- **sepia 增量复查（我方借鉴源头）**：2,937★（+223 vs 09-21），近 8 commit 均 docs/release（v0.12.1/0.12.2 指纹表修订）——机制无增量，已借鉴面满额维持 | 2026-10-03
- **状态变更**：shareAI-lab/learn-claude-code 77,937★ **恢复更新**（08-26→09-28 push）——停更记录作废回雷达（教学仓无机制增量）；kimi-cli 11,434★ archived 官方确认+kimi-code 7,758★ 继任（catalog 已正确）；Awesome-Long-Horizon-Agents 09-22 后 12 天无 push 停更观察维持
- 星数跃升（vs 最近入库记录）：univer 15k→22,316★（Office Harness 域升温，重依赖维持不接）/open-code-review 39,241→43,427★/impeccable 70.7k→74,573★/herdr 40,177→42,027★/CLIProxyAPI 53.1k→53,946★/tuios 3,716→4,592★/xingkongliang/skills-manager 4,934→5,397★/opencodex 16,674→16,844★/cc-switch 133k→139,682★/Understand-Anything 84,735→85,125★/nanobot 48.5k→48,753★/ui-ux-pro-max-skill 131,692→132,653★/CPA-Manager-Plus 3,565→3,719★/paca（issue tracker）1,885★/itsaplan 874★/agency-orchestrator 2,319★/Plano 7,079★/baml 9,374★（10-03 活跃）
- openhuman 40,480★ 描述改为「Written in Rust」口径——机制面（Memory Tree/TokenJuice）未见增量，观察
- 禅道周边：zentao-cli 61★（10-01）/zentao-skills 74★ 活跃；bug-triage/jira-linear 组零新——无新禅道 AI 竞品
- pypi 搜索页被拦（200/3038B 拦截页，与 16 时班一致）；npm 两查新面孔已入库（tide-commander/garda）

### 待深挖队列（2026-10-03 18 时快照，无新增机制级项）

1. 工具输出统一压缩管线（第 7 验证在手，管线级攒批）
2. 语义缓存（待租户/敏感边界拍板）
3. 演示文稿任务类型空档——交人拍板（不擅自扩）
4. 任务宪章编译为运行时强制策略（ironcurtain，治理远期）
5. 聊天分叉（1code，管线级远期备注）
6. 小红书平台特化交付契约蒸馏（wenzi-xhs 两小件，交人拍板）
7. 既有攒批维持：ARIS 规则裁决/multica 看板视角/凭据保险库（cockpit-tools 佐证；data/zentao.json 明文密码属该域，交人决策）

## 2026-10-03 19:00 班（批4：治理/安全/人机协同·全类型雷达——新一轮计划第 1/4 步调研）

> 115 查询 523 行 467 唯一仓零失败零限流；雷达源 C 全过（repos 36 仓+topic 8+新锐四组+
> CLI 周边五组+禅道周边三组+npm 两查+pypi 被拦+404 仓主名修正 4 组）。本轮零机制级新差量。

- **SeemSeam/claude_codex_bridge**（3,543★，09-30 活跃，NOASSERTION）新入库 | 「Visible multi-agent CLI workspace」——Codex/Claude/Gemini/Kimi/Qwen/Cursor/Copilot/Pi/OpenCode 可视混编工作台 | **同形态直接竞品 +1**：我们=任务类型×流程×评审门禁编排台（蜂巢/运行详情/会话复用/版本页签），它=终端侧混编面板；逐项对查无机制差量 | 雷达（同形态持续跟踪）| 2026-10-03
- **hivecommons/hive**（62★，10-03 活跃）新入库 | 开放/闭源全覆盖的可定制 agent 舰队编排（fleet covering every level）| 同形态小标，机制面未见差量 | 雷达 | 2026-10-03
- **0xethanq/astra-quant-agent**（314★，10-03）| 多 LLM 分析+确定性 Python 风控的量化交易 | 同日 18:49 补班已录该条（定性：方向验证，ARIS「模型提议、规则裁决」中文域验证 + cascade 同向）——此处不双录， tradememory 域外同族结论一致 | 已录（见 18:49 补班）| 2026-10-03
- **quantamixsol/graqle**（32★）新入库 | 持久组织智能：codebase/文档/策略/决策转可查询资产 | 组织级记忆（qm scoped 记忆同域）；单人产品暂不适用，记边界 | 参考 | 2026-10-03
- **chromoany/dsh-notify-me**（10★）新入库 | DSH 桌面消息提醒插件：可操作提醒+回复完成提醒+系统通知+提示音 | dsh 插件生态第 8 例（links/quota-check/AI-Novel-Writer 插件/movo/dsh-novel 之后）；我们桌宠气泡+群通知已覆盖同理念 | 已覆盖（生态验证 +1）| 2026-10-03
- **Terfyn/terfyn**（4★，10-03 活跃，Apache-2.0）新入库 | durable workflows + resumable execution + HITL 的多 agent runtime | CONTINUUM 幂等账本待深挖项的 durable/resumable 方向独立验证 +1（「Safe to Resume?」之后第三例）| 方向验证 | 2026-10-03
- **fernandoris/blindenv**（0★，10-03 活跃，MIT）新入库 | 本地加密 secret manager for AI coding agents（MCP 暴露）| 凭据保险库待深挖项小佐证 +1（sandbase-harness/cockpit-tools/CLIProxyAPI 族）| 方向验证 | 2026-10-03
- **QingYunA/answer-me-with-html**（130★，10-03 活跃）新入库 | 「用一页真可读的 HTML 回答难题」agent skill | 报告形态小而美；我们报告已是受管 Markdown/HTML 产物，理念同向无差量 | 雷达 | 2026-10-03
- **Borjani1577/claude-office-skills**（1★）| Excel/PowerPoint 本地文件自动化 skills | 演示文稿空档域微型生态例证——1★ 不构成接码依据，空档维持交人拍板 | 雷达 | 2026-10-03
- **治理域微型新锐速记**（批4 主题）：agent349 4★（Node.js governed orchestration）/paved-gate 3★（typed safety ingestion gate）/g8s 3★（zero-trust supervisor-worker）/se_harness 1★（Verity Plane governance layer）/swarm-forge 0★（tmux worktrees 纪律 swarm）——「policy gate/不信任自报」族共识信号延续，全微型无单件可抄 | 方向验证 | 2026-10-03
- **其余微型速记**：do-knowledge-studio 3★（local-first 知识工作台+FTS5）/agents.nix 15★（Nix overlay for skills，打包域）/PPXANS-Harness 0★（中文纯 Node 内核：自愈+自学习+五层记忆+SHA-256 审计链）/Goobers 7★/hivemind 1★/wild_agentos 3★（Rust PDCA 语义内核）/ai-badger 2★（skills marketplace+scaffolder）/Project-TALOS 1★/agenticArxiv 0★/AgentDesk 0★/nextcore-skills 0★ 等——均学生级或域外 | 雷达 | 2026-10-03

### 复查记录（repos 端点 36 仓，19 时）

- 2026-10-03 19 时：头部全活跃缓涨——orca 84,112★（vs 18 时 +21）/superpowers 294,665/ECC 271,702/hermes-agent 250,874/deepseek-harness 242,640/deer-flow 83,348/codegraph 73,081/multica 51,867/spec-kit 139,920/OpenSpec 70,951/planning-with-files 27,263/agentmemory 29,106/paseo 19,306/ZCode 7,355（09-29）/**esengine/DeepSeek-Reasonix 35,733（E 候选首位维持，星速放缓）**/farion1231/cc-switch 139,689/router-for-me/CLIProxyAPI 53,952。
- 治理域四仓全活跃（批4 主题）：agent-governance-toolkit 6,380★（10-03 push）/failproofai 5,236★（10-02）/cordum 509★（10-03）/**stop-that-shit 2,458★（vs 09-21 +299 热涨）**/tradememory 1,423★——均零新机制。
- 星数跃升（vs 最近入库记录）：pacifio/atlas 8,431→8,869★（**三日 +438 爬升快**，已录雷达维持）/happier 1,702→1,843★（+141）/coder_eval 141→148★。
- 写作/记忆域：oh-story 7,219（10-03 活跃）/drama-skills 2,456（10-03）/sepia 2,937（09-23 后无 push，已借鉴面满额维持）/inkos 10,111（09-27 后无 push 维持观察）/claude-mem 95,217/anthropics/skills 179,468；记忆新锐 loci 25/gitmemory 18/titration 8 均已录。
- awesome 四清单：awesome-claude-code 54,989/VoltAgent 35,148/Agent-Memory 656 活跃；Awesome-Long-Horizon-Agents 09-22 后 13 天无 push 停更观察维持。
- topic 信号：topic:claude-skills 首五位（claude-mem/Understand-Anything 85,127/OmO 69,767/i-have-adhd 53,046/scientific-agent-skills 47,444）全部已录——skills 生态头部化；topic:ai-coding-assistant 首位 atlas（爬升佐证）。
- E 域：kimi-cli 11,434 archived 维持/kimi-code 7,760（10-02）继任活跃/command-code 4,085 已录候选雷达本机未装。
- npm：agent-orchestrator-mcp-server（0.8.11）/@nathapp/nax（0.83.2）均已录零新；registry 实核 codebee=0.1.77。pypi 搜索页 3038B 拦截页维持。
- 禅道周边：零新禅道 AI 竞品（mcp-zentao-pro 第 5 例后持续为零）；paca 1,885★（10-03 活跃）/plandb 105★ 复查。
- 写作新锐轮（created:>09-26）全 ≤4★ 第二班连续确认——写作域无新竞品。

### 待深挖队列（2026-10-03 19 时快照，无变化）

1. 工具输出统一压缩管线（第 7 验证在手，管线级攒批）
2. 语义缓存（待租户/敏感边界拍板）
3. 演示文稿任务类型空档——交人拍板（不擅自扩；claude-office-skills 1★ 例证不改变结论）
4. 任务宪章编译为运行时强制策略（ironcurtain，治理远期）
5. 聊天分叉（1code，管线级远期备注）
6. 小红书平台特化交付契约蒸馏（wenzi-xhs 两小件，交人拍板）
7. 既有攒批维持：ARIS 规则裁决/multica 看板视角/凭据保险库（cockpit-tools/Terfyn/blindenv 佐证；data/zentao.json 明文密码属该域，交人决策）

## 2026-10-03 18:49 批4 补班（治理/安全/人机协同——18 时 %7=4 轮换补课）

> 18:00 班按 17 时口径跑批3，本班按 18 时规则补批4（--batch 4：10 组 50 行零失败）；
> 雷达源 C 由 18:00 班 30 分钟前全过，不重复烧配额。批4 主体（OpenAgentsControl/
> cordum/bytebase/governance-toolkit/baml/plano/failproofai/mcp-context-forge/archestra/
> edict/tradememory/Aegis/harness-books/boundary-bench/memoryops-ai）全部已录无增量。
> **本轮零机制级新差量**（稳定期判断延续），新面孔定性入库：

- **caura-ai/caura**（544★，10-03 活跃，前 MemClaw）| 治理型共享记忆：agent 舰队多租户 MCP-native 记忆，trust tiers/keystone policies/审计链/删除证明 | 我们知识库已有 confidence 三级+有据徽章（≈trust tier 的轻量版）；多租户对单人产品不适用（qm 结论同） | 雷达 | 2026-10-03
- **jagmarques/asqav-sdk**（619★，10-01）| AI agent 动作的可验证证据：签名回执+策略强制+审计链（LangChain/CrewAI/MCP 适配）| 「不信任自报」族 +1（stopgate/evidence.md/事件流计数已满配）| 方向验证 | 2026-10-03
- **FireRedTeam/FireRed-OpenStoryline**（3,456★，07-31）| AI 视频剪辑 agent：自然语言意图驱动导演式剪辑+HITL | video_script 域生态再 +1（motion-video-kit/awesome-claude-video-skills 之后）；剪辑执行器域，我们只到脚本 | 雷达（video_script 域）| 2026-10-03
- **0xethanq/astra-quant-agent**（314★，10-03，中文）| 多大模型分析+确定性代码风控的量化系统 | 「LLM 提议、规则裁决」分工（ARIS 同构）中文域再验证；cascade 分层同向 | 方向验证 | 2026-10-03
- **MasuRii/pi-permission-system**（169★）| pi coding agent 的权限强制扩展 | pi 生态信号（pi 已接 catalog）；我们的权限面=白名单闸门+全权沙箱二态，已覆盖 | 雷达（pi 生态）| 2026-10-03
- **批4 新面孔速记**：Atmosphere/atmosphere 3,816★（JVM agent 运行时一 SPI 跨框架——框架路线参考）/wanikua/danghuangshang 2,703★（中式治理隐喻多智能体，edict 同族）/LvcidPsyche/auto-browser 896★（MCP-native 浏览器 agent+HITL，BrowserSkill 同域）/Deuz-AI/Deuz-SDK 697★（TS 生产框架含 HITL 审批——框架路线）/liu00222/Open-Prompt-Injection 503★（注入攻防基准，09-27 活跃）/sharpdeveye/maestro 592★（25 命令工作流 skill 包）/claudlos/hermes-katana 49★（污点追踪+密钥守卫+策略引擎小件）/MonetiseBG/circuit-breaker 30★（成本上限+kill switch 经济治理——预算熔断同向）/simonstaton/AgentManager 27★（kill switch 编排小件）/AIScientists-Dev/Caliper 197★（知道自己何时可信的校准型 agent——confidence 分级同向）| 均无未覆盖机制 | 参考/雷达 | 2026-10-03

## 2026-10-03 深夜巡检班（七专项 A-G·新一轮计划第 2/4 步）

> 全部实锚复核（非转录前班）；详见 docs/borrow-log/2026-10-03.md 深夜巡检班节。
> 本步计划锚定的 2026-09-28.md 已过时，按实际执行日期落当日日报（周期惯例）。

- **i18n EN 键滞后于后端 note 改版（实证 2 例）** | flows.py note 改版（serial_novel
  签约质量门禁版 / rank_scan A-E 证据分级版）后 i18n.js :1932/:1973 旧键未跟，t() 未
  命中静默回落中文——英文界面菜单描述整段变中文 | name/goal_hint 有核对惯例而 note
  是盲区；**蒸馏入教训库**（data/skills.json +1：「改流程文案须同步 i18n EN 键」，scope *
  流程规范）；**落地立项**：两键补译 + test_i18n_dups 加三字段 EN 键静态断言（交第 3/4
  步） | 巡检发现 | 2026-10-03
- **A 专项**：12 项锚点当前行号全实证（预算熔断 :613/627/687/696、token_meter :690/722/
  742、压缩守门 :703-736、diff 评审 :944/:1016、深度分级 :999、cascade :1455、_shrink
  :2455/:3563、resume :206/:270、召回 skills.py:533/:612、planner 缓存 planner.py:675/824/
  967/998）；四方向（prompt 缓存/语义缓存/diff-only 评审/廉价分流）零新机制可抄——
  前三已覆盖（语义缓存维持待拍板），cascade 获 tf-routing 反证支撑 | 已覆盖 | 2026-10-03
- **B 专项**：六源+闸门+SSRF+冒烟全在位；本轮候选（Context-Engineering 大合集 18k 等）
  三问不过零接入维持 | 已覆盖 | 2026-10-03
- **C 专项**：17 类型 name/goal_hint EN 零缺失；note 2 处过期（见上）；演示文稿空档
  维持交人拍板 | 巡检 | 2026-10-03
- **D 专项**：教训 67 条（蒸馏前 66；lesson 63+procedure 4；流程规范 22/节奏爽点 21/
  情节逻辑 10/人物塑造 7/一致性 4/文笔风格 3）；同 scope 含跨类 containment ≥0.8 对数=0
  （10-03 落地班合并后无残留）；钩子族 9 条各有侧重不并 | 数据卫生 | 2026-10-03
- **E 专项**：catalog 14 条目；本机在装 **13**（trae-cli 新检出，09-30 记 12）；九候选
  全未装零接入防死链维持；Reasonix 35,733★ 候选首位维持 | 巡检 | 2026-10-03
- **F 专项**：禅道链路在位（_tick 节流+legacy 换算 :269-274+启动对账 :2509）；零积压
  零错误、poll_enabled=false 用户侧预期；产品 96 → mo-so workdir ls 实证存活、owners
  齐；新竞品零新增（第 5 例后连续为零） | 巡检 | 2026-10-03
- **G 专项**：过时文案活源零命中；发布平台文案与 PLATFORMS={fanqie,qimao} 一致非过时
  不误修；新毛病即 i18n 两键（见上） | 巡检 | 2026-10-03
- 待深挖队列无变化（7 项快照维持）；落地候选唯一件=i18n 同步修复+守卫（不为凑数强立
  第二件，与 18:00 班结论一致） | 队列 | 2026-10-03

## 2026-10-03 21:48 班（批7：中文/网关/本地/办公——新一轮计划第 1/4 步全类型调研）

> 21 时 %7=0 → 批7。主扫描 `scripts/borrow_scan_nightly.py`：A 常驻 89（含内置 B1 11）
> + 批7 9 + A1 双轮 16 = **114 查询 513 行 448 唯一仓，零失败零限流**（gh api 串行 4s）。
> 雷达源 C 全过：repos 端点 32 仓 + topic 页 8 + 新锐轮四组（created:>09-28）+ 自家 CLI
> 周边五组 + 禅道周边三组 + 框架/竞品周边四组 + npm 两查 + pypi（200/30KB CSP 壳无结果
> 条目，与既往拦截同效）。本轮**零机制级新差量**（稳定期延续），新面孔定性入库。
> **并行说明**：同窗另有 21:36 班（下节）同跑批7+雷达 C——LangBot/MonkeyCode/
> crow-central-agency/wirken/nuwax/agentic-os 等条目两班并录，以先录为准不重复计数；
> 该班另挖得 hippo-memory/GitPulse 两件机制级小件（见其节），队列已合并（见文末快照）。

- **langbot-app/LangBot**（17,994★，10-03 活跃）新入库 | 生产级多平台 IM 机器人平台（中文）：Agent+知识库编排+插件系统，QQ/微信/Discord/Telegram 等多通道 | CowAgent（47k，chatgpt-on-wechat 血脉）之后 **IM 通道编排大盘第二例**——验证「微信/IM 通道」生态在长厚；我们是任务编排台不做 IM bot 形态 | 参考（大盘补格）| 2026-10-03
- **chaitin/MonkeyCode**（4,788★，09-29）新入库 | 长亭科技「AI coding platform for teams」 | 中文安全大厂进场团队 AI coding 平台——同形态中文域新标；机制面待观察（本轮仅描述级） | 雷达 | 2026-10-03
- **continuedev/continue**（36,098★，10-03）| 开源 coding agent（IDE 扩展+CLI+Hub 全形态） | E 域大盘补格：与 codex/claude-code 并列的头部开源 coding agent 此前未入册；本机未装零接入 | 参考（E 域大盘）| 2026-10-03
- **langfuse/langfuse**（35,335★，10-03）| agent evals & observability 开源平台 | A6 观测大盘补格：我们用量台账/审计日志/evalbench 是轻量对应物，重观测平台非我们形态 | 参考（A6 大盘）| 2026-10-03
- **langgenius/FastGPT**（29,774★，10-01）| 中文知识平台（Dify 同门族） | B7 中文知识编排大盘；与我们任务域知识注入不同赛道 | 参考 | 2026-10-03
- **OpenHands/software-agent-sdk**（1,195★，10-03 push）新入库 | OpenHands V1 的模块化 agent SDK（官方新仓） | 头部 agent 公司拆 SDK 仓的生态事实（OpenHands 主仓此前未单列，SDK 化趋势与 strands harness-sdk 同向） | 雷达（生态事实）| 2026-10-03
- **pathintegral-institute/mcpm.sh**（1,009★，10-01）| MCP CLI 包管理器+注册表（npx mcpm，全客户端） | MCP 生态「包管理面」坐标——与我们市场六源（skills 侧）互补的 MCP 侧同构位；无机制差量 | 雷达 | 2026-10-03
- **Intrect-io/OpenSwarm**（857★，10-03）| Claude Code CLI 驱动的自主 dev team 编排：Discord 控制+Linear 集成 | 同形态+IM 控制通道（multica 看板族 × tlive IM 族交叉例） | 雷达 | 2026-10-03
- **HelpCode-ai/anythingmcp**（783★，10-03）| 自托管 MCP 网关：REST/OpenAPI/SOAP/GraphQL/SQL→MCP | mcp-context-forge 同族再 +1 | 雷达 | 2026-10-03
- **future-agi**（2,107★，10-03）| 开源端到端 LLM/agent 评测-观测-改进平台 | A6 域（langfuse/opik/signoz 族）；我们缺自评测的口径维持（evalscope 已录） | 参考 | 2026-10-03
- **CopilotKit/OpenDots**（1,977★，10-02）| always-on AI coworkers（text/calls/Slack 跨形态常驻同事） | 形态新（常驻多模态同事）；与 feder-cr/dots（2,569★ 反封锁 web agent，上午班域外速记维持）非同仓 | 雷达（形态观察）| 2026-10-03
- **runify-dev/runify**（166★，09-16）| 中文轻量白盒多智能体平台：可视化编排+统一对话+企业权限，200MB 低资源可嵌入第三方 | 中文「轻量自托管编排」坐标（Yuxi 重部署的反极）；机制面未见差量 | 雷达 | 2026-10-03
- **EvovexAI/EvoFlow**（337★，09-29）| 中文原生 Agent Runtime/智能体员工/编排/知识库/长任务 | 中文平台新锐（qm/compozy 同域变体） | 雷达 | 2026-10-03
- **Galaxy-Dawn/claude-scholar**（5,656★）| 半自动学术研究助手（科研+软开） | research 域 5.6k 标的；深研引擎我们已有（调研报告流程+检索降噪），形态参照 | 雷达 | 2026-10-03
- **nanaism/yomiyasu**（1,274★，10-02 活跃）| AI 生成日语→自然日语推敲 Agent Skill | 上午班「域外速记」本轮**升级定性为 translation 域**：它证明「目标语去 AI 味」是独立工序——我们 aiflavor 只覆盖中文，翻译流程有「术语一致」无「译文翻译腔」检查；**蒸馏候选小件（交人拍板，不擅自扩）**：TRANSLATION_APPENDIX 可加一条「译文避免翻译腔/欧化句式」指引 | 借鉴候选（小，拍板项）| 2026-10-03
- **Edwardxlai/easyread**（655★，10-03）| 英文论文→中文对照阅读（本地 PDF 翻译+原文对照+边读边问+文献管理） | 翻译产品形态（对照阅读面），非机制差量 | 参考 | 2026-10-03
- **fauxnix**（686★，09-30）| Windows 上跑 Linux 风格命令：确定性 bash→PowerShell 翻译，无 VM 无 WSL，MCP 暴露 | win32 兼容层思路（我们 Bash 被劫持时的降级通道属域外参照）；不接 | 参考（域外）| 2026-10-03
- **禅道生态第 6/7 例**：**1414894911/git2zentao**（7★，09-15）| Git 提交→禅道任务：自动汇总需求+T1~T7 难度定工时+日期铺排+**经得起审计的工时汇报** | 工时维度新（我们闭环是 Bug→修复→resolve，无工时审计面——属产品边界外，F 专项雷达） | 参考（F）| 2026-10-03；**ceeyang/zentao_mcp**（6★，09-07）| ZenTao Bug MCP（AI 辅助修 Bug） | 通道级同代（zentao-mcp 族），无差量 | 雷达（F）| 2026-10-03
- **npm 面**：**crow-central-agency**（0.27.18，09-25）多实例 Claude Code 管理器+Web UI | 同形态 npm 面 +1；idosal/agentcraft（RTS agent orchestrator，08-21）/opencode-oceanus（OpenCode 编排插件）/@polderlabs/bizar（最大自治 harness）均小 | 雷达 | 2026-10-03
- **B 专项 skill 包新面孔**（三问均不过零接入维持）：data-goblin/power-bi-agentic-development（960★，Power BI AI skills 插件市场——微软生态 skill 包）/LerianStudio/ring（217★，89 skills+38 agents 工程实践包）/giuseppe-trisciuoglio/developer-kit（353★，多 CLI 插件市场）/ahmedasmar/devops-claude-skills（204★）| 重合/整包不可直读/用户不整包搜 | 雷达（B）| 2026-10-03
- **方向验证 +2**：**gebruder/wirken**（187★，09-30）autonomous agents 企业网关（身份/通道隔离/凭据）——凭据保险库族 +1（cockpit-tools/blindenv 后）；**chigwell/Penelopa.ai**（139★，09-25）分析真实 Codex/Claude Code 会话做持续改进——learn_from_run 同构独立实现 | 均无未覆盖机制 | 方向验证 | 2026-10-03
- **域外/大盘一句**：chatanywhere/GPT_API_free（43,546★ 免费聚合——freellmapi 族大盘，ToS 存疑不接）；dair-ai/Prompt-Engineering-Guide（78,803★ 学习材料）；pentagi（25,214★ 自主渗透测试——红队域记录不借鉴）；twinny（3,662★ VS Code 助手域外）；cnfjlhj/ai-collab-playbook（452★ 学习材料）；Azure contoso-creative-writer（429★ 官方多 agent 写作示例）；twanew/OmniWriter（178★ LangGraph 文章生成）——article 域小件无差量 | 均无未覆盖机制 | 参考 | 2026-10-03

### 复查记录（repos 端点 32 仓，21-22 时）

- 头部当日续涨：**orca 84,196★（10-03 活跃，vs 19 时 +84 继续领跑）**/superpowers 294,734（09-27 后无 push）/ECC 271,856（10-02）/hermes-agent 250,908（10-03）/deepseek-harness 242,721（10-03）/codegraph 73,096（10-03）/multica 51,874（10-02）/spec-kit 139,958（10-02）/OpenSpec 70,959（10-02）/planning-with-files 27,265/agentmemory 29,109/paseo 19,321。
- **E 域**：esengine/DeepSeek-Reasonix 35,731★（10-03 活跃）候选首位维持；ZCode 7,361（09-29 后无 push）；kimi-cli 11,435 archived 维持、kimi-code 7,764（10-02）继任活跃；command-code 4,085（09-30）已录维持。
- 写作域：oh-story 7,225（10-03 当日活跃）/drama-skills 2,464（10-03）/sepia 2,941（09-23 后无 push，已借鉴面满额维持）/inkos 10,114（09-27 后无 push 维持观察）/claude-mem 95,306（10-03）/anthropics/skills 179,489（09-29）。
- **星数跃升**：**SkillSpector 18,070→19,207★（09-23 以来 +1,137——市场装前扫描借鉴源头在快速放量，外部证据再 +1）**；pacifio/atlas 8,886（10-03 活跃，三日 +455 爬升维持）；cockpit-tools 18,605（凭据族热涨维持）；stop-that-shit 2,458（10-03 活跃，治理域热涨维持）；happier 1,843（10-03 活跃）；open-code-review 43,448（10-01）。
- awesome 清单：awesome-claude-code 54,994（10-03）/VoltAgent 35,150（10-02）/Agent-Memory 657（10-01）/awesome-llm-apps 140,593（09-30）/wshobson/agents 40,173（10-01）全活跃；**Awesome-Long-Horizon-Agents 1,058（09-22 后 13+ 天无 push——停更观察维持）**。
- 状态变更：无新增 archived/死亡；禅道周边：zentao-cli 61（10-01）/zentao-skills 74（09-20）活跃，新面孔见第 6/7 例两条；harbor 5,804（10-03）。
- topic 信号：topic:claude-skills 首五（claude-mem/Understand-Anything 85,148/OmO 69,767/i-have-adhd 53,082/scientific-agent-skills）全已录——skills 生态头部化延续；topic:ai-coding-assistant 首位 atlas（爬升佐证）；topic:agent-framework 首四含 BettaFish（42,329 中文舆情，已录域外）。
- 写作新锐轮（created:>09-28）：yomiyasu 1,274★ 为本轮最大（已升级定性见上条）；其余全 ≤14★ 或域外——**写作域无新竞品第三班连续确认**；新锐 agent+orchestration 全为 genpark 蓄水农场（已录族，不重复入库）。

### 待深挖队列

- 本班原 8 项快照已与并行 21:36 班合并为文末「2026-10-03 22 时合并快照（权威版）」——
  新增第 7/8 项（hippo-memory 显式负反馈、GitPulse git log 周报素材）来自 21:36 班，
  本班 yomiyasu 翻译腔项列第 9；不在此重复维护两份。

## 2026-10-03 21:36 班（批7：中文/网关/本地/办公 + 雷达 C 全过——新一轮计划第 1/4 步调研）

> 通道全量恢复（gh api 可用，对照 09-28 WSL 降级班）：主扫描 114 查询 513 行 454 唯一仓
> 零失败（A 常驻 89 + B7 轮换 9 + A1u/A1p2 双轮 16）；雷达源 C 全过（repos 38 仓 + topic 8 +
> CLI 周边 5 + 禅道周边 3 + awesome 12 + npm 两查 + pypi 页可达）。头部格局稳定期延续，
> 真新机制级差量 = 2（hippo-memory/GitPulse），其余定性入库。

- **kitfunso/hippo-memory**（770★，MIT，10-03 活跃）新入库 | 「记忆的核心是知道什么是错的」：**显式 mark-wrong 负反馈**（标记错误即不再浮现）、新事实替换旧事实、**「没人用」衰减**（usage-based decay）、SQLite+Markdown 镜像双写、BM25 零模型检索、hippo init 一键接线 6 CLI | 我们经验库已有 outcome 自动加权（won/lost karma）+过期降权+hits 记账——**差量=①用户显式负反馈入口**（教训卡无「这条没用」手动降权/停用钮，karma 只从 run verdict 自动来）**②久未命中衰减**（hits 只加分无时间衰减）| 借鉴（D 专项小件：教训卡「标记无用」钮进待深挖）——**状态（2026-10-10 15 时班复核）：两差量均已落地**——①「没用」钮在位（app.js skillLessonOp useless op→skills.lesson_op 停用+记粘滞负证据+badge 计数+排序下沉，_karma 按 lost 同权计入）②时间衰减在位（skills.py `_surplus_decay`/`_eff_karma`：won==0 且闲置超 30 天宽限期线性衰减盈余，lost 粘滞不衰减，注入/UI 排序均走衰减口径，函数注释明写 hippo 借鉴）——本条清账 | 已落地 | 2026-10-03
- **GoldenZqqq/GitPulse**（16★，MIT，09-28）新入库 | 本地优先 Git 工作报告生成器：多仓库 commits 一键变日报/周报/绩效月报，数据不出本机 | weekly_report 素材通道当前=禅道导出；**git log 是天然周报素材**（本地可读/零网络/事实性强/多仓聚合）——小星但任务类型正对 | 参考（C 专项：weekly_report 素材通道候选进待深挖）| 2026-10-03
- **michael-denyer/pstack-claude**（895★，MIT，10-03）新入库 | Cursor pstack（Lauren Tan）六 harness 移植：poteto-mode 按目标选工作流；**上游追踪+具名策略分叉**（tools/forks.json 声明 fork 点）；姊妹件 agent-formal-verify（TLA+ 模型检查+Lean 证明）| 市场装原包无 fork 场景；「fork 治理」形态参考（跟踪上游+声明策略分叉防漂移）；形式化验证超出当前需要 | 雷达（A7 写作域/skill 工程域）| 2026-10-03
- **spec-kitty/spec-kitty**（1,658★，MIT，10-03）新入库 | SDD CLI：spec→plan→tasks→next→review→accept→merge 全链 repo 文件化+**Charter 管「怎么建」与 spec 管「建什么」双文件分离**+git worktree 并行 | spec 三件套/worktree 隔离/Stop gate 已满配；Charter=spec-kit Constitution 同物——宪章路线第 3 独立形态（spec-kit/OpenSpec 之后），方向再验证 | 参考（宪章域 +1）| 2026-10-03
- **edgehero/pi-dispatch**（178★，MIT，10-03）新入库 | pi 变自托管服务：cron/CLI/forge 事件触发、每 job 独立容器、**花钱前查花费上限**、durable queue、管理面板 | 事件触发建任务≈禅道闭环；pre-run 成本预估已抄；预算熔断语义已覆盖；pi 生态服务化第 3 例（pi-subagents/pi-permission-system 之后）| 雷达（pi 生态/调度域）| 2026-10-03
- **jordan-gibbs/hyperresearch**（3,760★，10-01）| Claude Code/Codex 变 deep research agent | research 域大盘（gpt-researcher/deep-research/dzhng 已录）；检索降噪两条已落地——README 级无新机制面 | 参考 | 2026-10-03
- **diegosouzapw/OmniRoute**（72,537★，10-02）| 免费网关聚合：一个端点 359 供应商（150+ 免费）MIT | freellmapi（30,291★，不接结论）同族更大体量；免费通道稳定性存疑+同构无增量结论沿用 | 参考（网关族）| 2026-10-03
- **langbot-app/LangBot**（17,994★，10-03）| 生产级多平台 IM bot 平台（中文）：Agent/知识库编排/插件系统 | IM 通道生态大盘（CowAgent 47k→WeChatBridge 之后最大体量佐证）；通道方向维持远期不做 | 参考（IM 通道域）| 2026-10-03
- **同形态新面孔簇**：kbwo/ccmanager 1,257★（多 CLI 会话管理）/golutra 3,852★（Go 编排平台）/chaitin/MonkeyCode 4,788★（长亭团队 AI coding 平台，中文安全大厂进场）/nuwax 892★（企业 agent 平台）/agentic-os 185★；A1u 新锐当日簇 optimus/agents-manager/kando-agents/multi-agent-coupling（0★ 中文 newborn）| 赛道拥挤度新高延续，机制均无未覆盖项 | 雷达（同形态）| 2026-10-03
- **itayinbarr/little-coder**（2,639★，09-18）| 为更小 LLM 优化的 harness | cascade/小模型分流再验证（smallcode 之后）| 参考（A 专项）| 2026-10-03
- **superagent-ai/grok-cli**（3,486★）| Grok API 开源 coding agent | E 候选 +1（本机未装防死链；在装 grok 官方 CLI 不受影响）| 雷达（E 域）| 2026-10-03
- **治理/安全域 +3 微**：ZizkaDB 119★（agent 防篡改决策日志 DB，mcp-approvals 族）/guardana 125★（模型工件/MCP/skills 安全校验，SkillSpector 族）/wirken 187★（agent 企业网关：身份+通道隔离）| 三族均已满配或域外 | 方向验证 | 2026-10-03
- **dsh 生态第 9 例**：UnitySirx/dsh-kp-notes（知识点笔记插件）| 已接 dsh 周边持续繁荣佐证 | 雷达 | 2026-10-03
- **npm 面**：crow-central-agency 0.27.18（多实例 Claude Code 管理+Web UI）/@polderlabs/bizar（最大自治 worker harness）/opencode-oceanus/coleo | npm 同形态+编排小簇，三问均不过 | 雷达 | 2026-10-03
- **awesome-mcp 路径失效**：punkpeye/awesome-mcp 404（社区已迁 awesome-mcp-servers）——keywords.md 雷达源名实勘误记此，下轮按新路径核 | 生态事实 | 2026-10-03

### 复查记录（repos 38 仓 + 雷达，2026-10-03 21-22 时）

- 头部全活跃：orca 84,184★（10-03 push）/superpowers 294,726/ECC 271,850（10-02）/mattpocock/skills 275,088（09-29，增速快于 superpowers）/anthropics/skills 179,488（09-29）/deer-flow 83,349/codegraph 73,095/multica 51,874/spec-kit 139,954（10-02）/OpenSpec 70,958（10-02）/planning-with-files 27,264/paseo 19,321/superset 14,838（10-03）/openhuman 40,495（Rust 口径维持）/agentmemory 29,109/OpenCreator 12,578/Reasonix 35,731（10-03 push，E 候选首位维持）/ZCode 7,361（09-29）/harbor 5,804（10-03）/ARIS 16,924/claude-mem 95,297/cognee 31,330/graphiti 31,405
- 写作域：oh-story 7,224★（10-03 push 当日活跃）/drama-skills 2,462★（10-03）/sepia 2,941★（09-23 后无 push 维持）/inkos 10,114★（09-27 后无 push 维持观察）/**AI-Novel-Writer 1,277★（10-03 push，vs 09-21 的 1,036 +241 活跃增量——三竞品唯一在动，下轮看增量机制）**；chinese-novelist-skill 3,278★（09-06 后无 push）/AI-Novel-Writing-Assistant 3,060★（09-23）
- 治理域：agent-governance-toolkit 6,382★（10-03 push）/stop-that-shit 2,458★（10-03 push，vs 09-21 +299 热涨维持）/tradememory 1,423★（10-02）/freellmapi 30,291★（vs 深挖时 28.4k +1.9k）
- **状态变更**：①sentrux pushed_at 停在 2026-03-19——**停更半年实锤**（09-26 入库时按「09-26 活跃」记录有误，降级停更观察，实时质量传感器差量维持远期）；②Awesome-Long-Horizon-Agents 09-22 后持续无 push（第 4 班确认，停更观察维持）；③Langchain-Chatchat 停更（2025-11）；④MetaGPT 停更（01-21）未入册维持
- awesome 清单：awesome-claude-code 54,994（10-03）/VoltAgent 35,150（10-02）/awesome-llm-apps 140,592（09-30）/awesome-claude-skills=ComposioHQ 76,408（09-18）/awesome-harness-engineering 4,682（10-03）/awesome-cli-coding-agents 1,309（09-28）/buildwithclaude 3,582（10-03）/Agent-Memory 657（10-01）/awesome-ai-agents 30,258（08-21）/awesome-ai-agents-2026 1,917（06-10 停更半年）
- E 专项：catalog 14 条目维持；本机在装 13（trae-cli 在装确认）；九候选（reasonix/fuxi/gitlawb/zero/empryo/zcode/goose/crush/command-code）全未装零接入防死链维持
- 禅道周边：zentao-cli 61★（10-01）维持，无新禅道 AI 竞品（mcp-zentao-pro 第 5 例后持续为零）
- pypi 搜索页本轮可达（HTML 正常返回，前几轮拦截页记录作废）

### 待深挖队列（2026-10-03 22 时合并快照——21:36 班与 21:48 班合并，权威版）

1. 工具输出统一压缩管线（第 7 验证在手，管线级攒批）
2. 语义缓存（待租户/敏感边界拍板）
3. 演示文稿任务类型空档——交人拍板（不擅自扩）
4. 任务宪章编译为运行时强制策略（ironcurtain，治理远期；spec-kitty Charter 第 3 形态佐证）
5. 聊天分叉（1code，管线级远期备注）
6. 小红书平台特化交付契约蒸馏（wenzi-xhs 两小件，交人拍板）
7. **教训卡「标记无用」显式负反馈**（hippo-memory 差量，D 专项小件——21:36 班）
8. **weekly_report git log 素材通道**（GitPulse 差量，C 专项小件——21:36 班）
9. **翻译「译文翻译腔检查」蒸馏**（yomiyasu 角度：TRANSLATION_APPENDIX +1 条指引，小件交人拍板——21:48 班）
10. 既有攒批维持：ARIS 规则裁决/multica 看板视角/凭据保险库（cockpit-tools/Terfyn/blindenv/wirken 佐证；data/zentao.json 明文密码属该域，交人决策）

## 2026-10-03 22:13 班（第 1/4 步补位：AI-Novel-Writer 增量深挖 + B1 新锐轮 + awesome-mcp 勘误核验）

> 当前小时 22 % 7 = 1 → 批1；但批1 已内置于常驻 89 组、21:36/21:48 双班同窗全跑过
> （stars 轮），本班不重复主扫，只补脚本未覆盖的排序轮（created:>09-25 新锐轮 3 查）
> 与两笔移交待办（AI-Novel-Writer 增量机制 / awesome-mcp 新路径）。gh api 串行 4-5s
> 共 8 次调用，零失败零限流。

- **AI-Novel-Writer 增量复查（清账）** | 09-21 深挖后仅 4 commits——全部为 opencode Go
  会话头修复×2 与移除 activitybar 界面清理；**+241★（1,036→1,277）属 v1.1.0 存量机制
  放量，无新机制**，09-21 三大机制结论维持 | 其「逐项目标审稿」差量我们已双落地
  （event_check 前端行级展示 + issues quote 证据锚），该竞品从「下轮优先复查」清账
  | 已覆盖（复查清账）| 2026-10-03
- **awesome-mcp-servers 路径勘误闭环** | punkpeye/awesome-mcp 404 后社区迁移新仓实证
  存活：95,777★、09-27 push | 21:36 班「下轮按新路径核」待办完成，雷达源可照常全过；
  keywords.md 雷达源行的名实修正交沉淀步统一改（本步锚定文件不含 keywords.md）
  | 勘误闭环 | 2026-10-03
- **B1 新锐轮（created:>2026-09-25，stars 排序 3 查）零机制差量** | 测试生成域全 0★
  新生儿；代码评审域 jaqubowsky/fleet 10★（Claude Code+pi 并行、每 issue 一 Docker
  沙箱）/ugorur/orka 6★（最强 agent 当 CTO 规划评审合并、其余干活）/ckorhonen/jev-lint
  6★（agent 写的代码按团队规范模糊 lint，jev-review 同系列）/salarkb/git-review-skill
  5★（evidence-based PR/commit review）；安全域 0xtbug/Recat 10★（本地安全发现工作台）/
  1942853632/skillsentry 6★（本地 Chrome 扩展静态审 Agent Skills 与 MCP 配置——
  SkillSpector 族方向验证）/FireNigth/Security-review 1★（evidence-based 安全评审
  skill）| 沙箱隔离/supervisor 形态/评审 rubric/证据锚均已有对应物，B1 域新锐全为
  同形态/已覆盖族小簇 | 雷达 | 2026-10-03
- 待深挖队列无变化（权威版 10 项快照维持）：AI-Novel-Writer 清账不新增，本班零机制级
  差量——与 21:36/21:48 班「稳定期延续」判断一致，第 1/4 步全类型调研至此收口
  | 队列 | 2026-10-03

## 2026-10-03 22:00 班（批1：代码质量与评审·全量主扫+雷达 C——与 22:13 补位班同窗合并）

> 22:00 起跑与 22:13 补位班同窗（并行代理）：本班独立完成全量主扫（A 常驻 89 含内置
> B1 11 + 批1 轮换 11 + A1u/A1p2 双轮 16 = **116 查询 524 行 423 唯一仓，零失败零限流**，
> sleep 4s）+ 雷达源 C（repos 25+ 仓 + topic 8 + CLI 周边 5 + 禅道周边 3 含 443 断连
> 一次补跑 + 新锐轮 created:>09-24 agent/writing 两组 + npm 两查 + pypi 拦截页维持 +
> awesome 新鲜度含 awesome-mcp-servers 新路径复核）。22:13 班已录条目（B1 新锐轮小簇/
> AI-Novel-Writer 清账/awesome-mcp 勘误闭环）不重复；批1 主体高星全部已录无机制差量
> （open-code-review 43,451★/SkillSpector/mira 均在位）。本班独有增量如下。

- **dzhng/jevgrep**（2,106★，10-02，MIT）新入库 | "Find code by asking what it does"：给 coding agents 的语义代码发现 CLI（按功能问代码定位文件与源码上下文）；deep-research（19.7k★ 已录）作者新品、jev-lint（22:13 班已录 6★）同族明星件，上线数日即 2.1k★ | 我们代码任务靠被编排 CLI 自带 grep/read；codegraph/Understand-Anything 是索引图谱重形态，jevgrep 是轻量工具面——差量=语义检索工具层（非编排层，不进 catalog 不装）；与我们知识库「不做代码结构索引」边界一致 | 雷达（code 域工具面）| 2026-10-03
- **lifedever/claude-rules**（192★，03-19）新入库 | Auto-detect tech stack→自动生成项目编码规范（AI coding assistants 用）| 我们任务宪章已落地（pipeline.py:4965 `_read_constitution` 读 .codebee/constitution.md 注入）但需作者手写；**差量=「宪章自动起草骨架」**（检测技术栈→生成规范草稿降冷启动门槛）——spec-kitty Charter（第 3 形态）之后宪章域第 4 例，补上「手写门槛」角度 | 借鉴（候选小件：constitution 缺失时按任务类型+工作目录特征自动起草骨架，交人拍板）| 2026-10-03
- **pipeshub-ai/pipeshub-ai**（3,811★，10-03 活跃）新入库 | 「agent 的公司知识上下文层」：Slack/Drive/Jira/GitHub/M365 40+ 连接器→权限感知工作区 | 我们知识库=任务域轻量注入（单人单机）；组织级知识管道+权限模型不适用（qm 同为组织层结论）| 参考（雷达）| 2026-10-03
- **kaankiziltug/logo-design-skill**（1,620★，09-30）新入库 | logo 设计 skill（原理+流程+SVG 模板，多 CLI 通用）| 设计域 skill 大包；三问：重合（封面/配图已有独立通道）/整包不可直读/用户搜具体功能——不过 | 不接（B 专项雷达）| 2026-10-03
- **feitangyuan/onetake**（1,413★，09-29）新入库 | "Motion films that never cut"：一镜到底连续镜头视频生成 | video_script 域生态第 4 例（awesome-claude-video-skills/motion-video-kit/FireRed-OpenStoryline 之后）；我们只到脚本，成片维持远期 | 雷达（video_script 域）| 2026-10-03
- **unclebob/bookwriter**（74★，10-02）新入库 | Uncle Bob（Robert C. Martin）亲写「一本书的小写作应用」| 写作域新锐：知名作者入场单书写作工具，项目极小早期无机制；作者方法论可能随项目长出，值得跟踪 | 雷达（写作域观察）| 2026-10-03
- **gupsammy/Claudest**（273★，09-14）新入库 | "opinionated plugin marketplace"：battle-tested 精选策展式插件市场 | 与我们六源市场同域的「策展 vs 聚合」形态；三问不过（重合/用户不搜整包市场）| 不接（B 专项雷达）| 2026-10-03
- **onikan27/claude-code-monitor**（310★，01-29）新入库 | 多 Claude Code 会话实时看板（CLI + Mobile Web）| 同形态面板族 +1（tuios/CPA-Manager-Plus 族）；蜂巢+运行详情+移动只读远控已覆盖 | 雷达（同形态）| 2026-10-03
- **vibeislandapp/vibe-island**（152★，09-02）新入库 | macOS notch 面板盯 25 个 AI coding agents 状态 | coucou（notch 宠物看护 3,134★ 已录）同域再验证；桌宠 tasks_only+气泡已覆盖 | 已覆盖（生态验证 +1）| 2026-10-03
- **yzhao062/awesome-auditable-ai**（149★，09-28）新入库 | 审计 AI agents 论文/工具/数据集/基准精选清单 | 治理/审计域雷达源候选（A11/A13 交叉）；「不信任自报」族的文献地图 | 参考（雷达源候选）| 2026-10-03
- **dfinke/PSAI**（273★，03-22）新入库 | PowerShell 原生多 agent 编排框架（high-agency 自动化系统工程）| 我们 Windows 环境但 Python 栈；PowerShell 生态同形态小件无机制差量 | 雷达（同形态/Windows 域）| 2026-10-03
- **Kiln-AI/Kiln**（5,163★，10-03）新入库 | LLM 应用构建/评测/优化工具（evals/RAG/agents/微调）| 评测域大盘补格（harbor/coder_eval/evalscope 旁）；自评测缺口维持远期 | 参考（评测域）| 2026-10-03
- **批1 新面孔速记**：BugTraceAI-CLI 184★（09-29，multi-agent 漏洞扫描 CLI）/project-codeguard/rules 424★（model-agnostic 安全规则集）/muxso/Shepherd 13★（端到端 AI 软工平台）/cheddar-bench 8★（CLI coding agents bug 检测无监督基准）——评审/安全/测试域小簇，沙箱/supervisor/证据锚均已有对应物 | 雷达 | 2026-10-03
- **禅道生态第 8 例**：leeguooooo/zentao-mcp（15★，09-05，products+bugs REST MCP）——通道级同代（第 6/7 例 git2zentao/ceeyang 之后）无机制差量；cra-agent 455★（CRA 合规扫描→Jira 工单，合规向工单域小标）同记 | 参考（F 专项雷达）| 2026-10-03
- **dsh 生态第 10 例**：IvanWu2015/dsh-connect（1★，10-03，DSH→飞书/钉钉桥：聊天+通知）——dsh-links（Android 伴侣）IM 向兄弟件；群通知已覆盖同理念 | 雷达 | 2026-10-03
- **域外/大盘一句**：tldraw 50,725★（agent 化营销命中，canvas SDK 域外）/n8n 206,563★（topic:mcp 首位，工作流平台生态坐标）/GPT_API_free 43,546★（chatanywhere 免费通道，freellmapi 族域外）/SigNoz 32,268★+pinpoint 13,868★（通用 APM 检索误命中，域外）/pentagi 25,214★（自主渗透测试，安全域外）/usecomputer 335★（computer-use CLI，CUA 域雷达）/J-Space-Cognition-Suite 2,999★（推理时控制套件，概念早期）| 均不入库细查 | 参考/域外 | 2026-10-03
- npm 面：@idosal/agentcraft 0.5.1（RTS agent orchestrator——GitHub 仓 404 疑改名/私有，21:48 班一句已录、本班补 404 事实）/pandash-cli/coleo 小件三问均不过零接入；pypi 拦截页维持（3036B，与 21:48 班 CSP 壳同效）| 通道 | 2026-10-03

### 复查记录（repos 端点 25 仓 + 主扫双轮，22:00-22:30）

- 头部与 21:36/21:48 班同窗数值一致（小时内漂移）：orca 84,196★（10-03 push）/superpowers 294,734（09-27）/ECC 271,856（10-02）/mattpocock/skills 275,095（09-29）/hermes-agent 250,911（10-03）/deepseek-harness 242,721（10-03）/opencode（anomalyco）211,586（10-03）/anthropics/skills 179,489（09-29）/claude-code 149,070（10-03）/codex 127,709（10-03）/pi 111,995（10-03）/claude-mem 95,314（10-03）
- E 域：Reasonix 35,733★（10-03 活跃）候选首位维持；ZCode 7,363（09-29 后无 push 缓涨）；kimi-cli 11,435 archived/kimi-code 7,764（10-02）；**grok-cli（superagent-ai）3,486★ 07-06 后无 push——停推观察新记**；opencodex 16,854（10-03）/CLIProxyAPI 53,981（10-03）/command-code 4,085（09-30）维持
- 写作域：oh-story 7,228（10-03 push 活跃）/drama-skills 2,465（10-03）/sepia 2,941（满额维持）/AI-Novel-Writer 1,277（10-03 push，22:13 班清账：存量放量无新机制）/hippo-memory 770（10-03 活跃）
- 凭据/面板族：pacifio/atlas 8,888★（10-03 活跃，爬升维持）/cockpit-tools 18,605（10-01）；治理域四仓全活跃（governance-toolkit 6,383/stop-that-shit 2,457/failproofai 5,237/cordum 510）
- awesome：**awesome-mcp-servers（punkpeye）95,777★（09-27）新路径存活复核**（与 22:13 班勘误同值）；awesome-claude-code 54,995（10-03）/VoltAgent 35,150（10-02）活跃
- planning-with-files 27,266（10-01）/1code 5,585 archived 维持/GitPulse 16（09-28）维持/21st-dev 族无新增 archived

### 待深挖队列（2026-10-03 22:30 快照——22 时权威版 +1，11 项）

1. 工具输出统一压缩管线（第 7 验证在手，管线级攒批）
2. 语义缓存（待租户/敏感边界拍板）
3. 演示文稿任务类型空档——交人拍板（不擅自扩）
4. 任务宪章编译为运行时强制策略（ironcurtain，治理远期；spec-kitty Charter 第 3 形态佐证）
5. 聊天分叉（1code，管线级远期备注）
6. 小红书平台特化交付契约蒸馏（wenzi-xhs 两小件，交人拍板）
7. 教训卡「标记无用」显式负反馈（hippo-memory 差量，D 专项小件）
8. weekly_report git log 素材通道（GitPulse 差量，C 专项小件）
9. 翻译「译文翻译腔检查」蒸馏（yomiyasu 角度，TRANSLATION_APPENDIX +1 条指引，交人拍板）
10. **宪章自动起草骨架**（claude-rules 差量：constitution.md 缺失时按任务类型+工作目录特征自动生成规范草稿，降手写门槛——小件交人拍板，22:00 班新进）
11. 既有攒批维持：ARIS 规则裁决/multica 看板视角/凭据保险库（cockpit-tools/Terfyn/blindenv/wirken 佐证；data/zentao.json 明文密码属该域，交人决策）

> 移交沉淀步（4/4）：keywords.md 雷达源行「awesome-mcp」→「punkpeye/awesome-mcp-servers」
> 名实修正（22:13 班移交、本班复核维持，本步锚定文件不含 keywords.md 不代改）。

## 2026-10-04 凌晨班（批1：代码质量与评审·新一轮计划第 1/4 步全类型调研）

> 01 时 %7=1 → 批1。主扫描 `scripts/borrow_scan_nightly.py`：A 常驻 78（含内置 B1 11）
> + 批1 轮换 11 + A1u/A1p2 双轮 16 = **116 查询 524 行 424 唯一仓，零失败零限流**
> （gh api 串行 4s）。雷达源 C 全过：repos 端点 18 仓（3 路径 FAIL，其中 AI-Novel-Writer
> 正主 EthanYoQ 补核成功；wshobson/awesome-claude-skills 与 yanhaijing/awesome-llm-apps
> 为我方猜测路径错误非上游失效，awesome-llm-apps 140,593★ 已有 09-30 记录）+ topic 8 +
> 新锐轮四组（created:>09-27）+ npm 两查 + pypi（200/3038B 拦截页维持）。
> **本轮零机制级新差量（连续第八班稳定期）**。
> 工程小注：borrow_scan_nightly.py 不识别 --help，误传会直接起全量扫——用法只有 --batch N。

### 新条目（微型定性入库，均无未覆盖机制）

- **dograh-hq/dograh**（5,796★，10-02 活跃）| 开源语音 AI 平台（自托管 Vapi/Retell 替身，STT/LLM/TTS 全链）| Patter（1,060★ 已录）同域更大体量；我们语音输入已落地，不做语音平台 | 雷达（语音域）| 2026-10-04
- **modelbus/one-api-pro**（1,001★，10-03 活跃）| one-api 企业级后裔网关（重构+计费+多租户）| one-api 族大盘（coai 9.3k 已录同门）；网关路线维持不接 | 雷达（网关族）| 2026-10-04
- **img2threejs**（17,435★，09-23）| 参考图→程序化 Three.js 模型（代码生成+质量门禁）| A3 组误命中（非 token 机制），3D 资产域外 | 参考（域外一句）| 2026-10-04
- **valon-technologies/gestalt**（20★，10-02）| 声明式 agentic 工具/服务管理平台（含安全凭据）| 凭据保险库待深挖族 +1（cockpit-tools/Terfyn/blindenv/wirken 后）| 方向验证（凭据族）| 2026-10-04
- **i3T4AN/Semantic-skill-space**（15★，03-02）| 技能嵌入注入 KV cache 供小模型恢复技能行为 | A 专项概念早期小标（技能×KV cache 交叉，无工程面）| 雷达（A 专项观察）| 2026-10-04
- **写作域微型四连**：awesome-ai-novel-editors 1★（09-30，AI 小说编辑工具清单——清单域雷达）/ BBQ2077/novel-to-manga-anime-generator 1★（小说→漫画/动画插件——A12 改编域微型）/ kietnovel 2★ + NovelFoundry 1★ + cherry-novel-tavern 2★（长篇写作新生儿三例）| **写作域无新竞品第四班连续确认** | 雷达 | 2026-10-04
- **sky-flux/skills**（10★）| 39 维跨文档一致性扫描的多文档项目规划 skill | 一致性评审+圣经+伏笔账已满配；维度数噱头无新机制 | 已覆盖（写作域）| 2026-10-04
- **npm 面**：@hybridlabor-api/bdb-agent-orchestrator（Untrivial fork）、@bpinhosilva/agent-orchestrator 1.3.0（NestJS 编排平台）等 8 结果全为已录族/fork/小件，三问不过 | 雷达 | 2026-10-04

### 复查记录（搜索摘要 + repos 端点 18 仓，01:27-01:55）

- 头部全活跃：orca 84,302★（vs 10-03 19 时 +106 继续领跑）/superpowers 294,803/ECC 272,019（10-02）/hermes-agent 250,946/deepseek-harness 242,817/mattpocock-skills 275,207/anthropics-skills 179,504/opencode 211,608/pi 112,055/claude-code 149,131/codex 127,735/planning-with-files 27,267/agentmemory 29,114/paseo 19,346/ponytail 152,952/firecrawl 188,194
- E 域：**esengine/DeepSeek-Reasonix 35,736★（10-03 push）候选首位维持**；本机九候选全未装零接入；grok-cli 停推观察维持；kimi-code 继任维持
- 写作域：oh-story 7,233★（10-03 push 当日活跃）/drama-skills 2,472★（10-03）/sepia 2,943★（09-23 后无 push，已借鉴面满额维持）/inkos 10,114★（09-27 后无 push 观察维持）/yomiyasu 1,292★（10-03 活跃，翻译腔项维持）/AI-Novel-Writer 1,284★（EthanYoQ 正主 10-03 push，已清账维持）/claude-mem 95,429★
- 待深挖项三连复查：hippo-memory 770★（10-03 push 活跃，第 7 项「标记无用」维持）/GitPulse 16★（09-28 维持，第 8 项 git log 素材维持）/wenzi-xhs-agent-skills 148★（09-30 维持，第 6 项维持）
- **mira 勘误**：B1 批搜索摘要初读「355」险误作十倍跃升，repos 端点复核 **355★（09-30 push，vs 09-26 的 343 +12 正常增量）**——星数以 repos 端点复核为准
- awesome 四清单：awesome-claude-code 55,006（10-03）/VoltAgent 35,155（10-02）/**awesome-mcp-servers 95,784（09-27）新路径存活复核**（keywords.md 名实修正已随本轮落地）/Agent-Memory 657（10-01）全活跃；**Awesome-Long-Horizon-Agents 1,058（09-22 后 14 天无 push）停更观察维持**
- 状态变更：无新增 archived；禅道周边零新竞品（第 8 例后持续为零）；skill 生态 topic 首五位全已录（claude-mem/Understand-Anything 85,164/OmO 69,766/i-have-adhd 53,131/scientific-agent-skills 47,473——头部化延续）

### 七专项巡检（本班实证，非转录）

- **A**：锚点全实证在位（预算熔断 pipeline.py:627/:696、评审深度 :999、cascade :1455、_shrink_context_block :2455/:3572、task_plan 落盘 :1420/:5133）；A3/A10 扫描面零新机制——四方向（prompt 缓存/语义缓存/diff-only/廉价分流）既有结论维持 | 已覆盖
- **B**：六源在位（market_remote.py:50 SOURCES）；本轮新见候选（dograh/one-api-pro/img2threejs/gestalt 等）过三问均不过（重合/域外/不会整包搜），零接入维持 | 已覆盖
- **C**：**17 类型 × name/goal_hint/note 三字段 EN 键程序化核对零缺失**（10-03 深夜班 i18n 两键补译已在工作区生效；test_i18n_dups 断言增强系并行代理在制品，不重复动）；演示文稿空档维持交人拍板 | 巡检
- **D**：教训 67 条零漂移（lesson 63+procedure 4；流程规范 22/节奏爽点 21/情节逻辑 10/人物塑造 7/一致性 4/文笔风格 3，偏科 33%）；知识库 12 条（serial_novel 9/direct 3）；同族可并零新增 | 数据卫生
- **E**：catalog 14 条目；本机在装 13（**trae-cli 在 PATH 实证**，裸名 trae 无）；九候选（reasonix/fuxi/gitlawb/zero/empryo/zcode/goose/crush/command-code）全未装——零接入防死链维持 | 巡检
- **F**：claims 零积压、last_error 空、poll_enabled=False（用户侧预期）、last_scan 09-21 20:43（开关关所致）；auto_resolve/auto_merge/triage_ai=True 在位；禅道周边零新竞品 | 巡检
- **G**：过时文案活源 grep（13种/15种/双平台/两平台/双源/已接11）零命中——唯一命中全在 .mimosa hook-state 基线快照（内部缓存非活码）；新毛病无 | 巡检

### 任务类型矩阵核对（列举口径 vs 注册表实数）

用户列举 14 场景（口径「13 种」）vs **flows.py BUILTIN_FLOWS 实数 17**：直接执行=direct、
代码=code、小说=novel、连载=serial_novel、自媒体文章=article、调研报告=research、
短视频脚本=video_script、技术方案=tech_proposal、翻译=translation、演讲稿=speech、
工作汇报=weekly_report、商务邮件=email、扫榜选材=rank_scan、禅道工单=defect_retro——
列举场景全部有注册表对应；注册表另含 **doc/resume/bid_doc** 三类型为列举未提及。
差异如实记录，无场景遗漏；本轮逐类型菜单描述/参数/辅助信息抽查（17×3 字段）零异常。

### 待深挖队列（2026-10-04 快照，与 10-03 22:30 权威版一致，零变化）

1. 工具输出统一压缩管线（第 7 验证在手，管线级攒批）
2. 语义缓存（待租户/敏感边界拍板）
3. 演示文稿任务类型空档——交人拍板（不擅自扩）
4. 任务宪章编译为运行时强制策略（ironcurtain，治理远期）
5. 聊天分叉（1code，管线级远期备注）
6. 小红书平台特化交付契约蒸馏（wenzi-xhs 两小件，交人拍板）
7. 教训卡「标记无用」显式负反馈（hippo-memory 差量，D 专项小件）
8. weekly_report git log 素材通道（GitPulse 差量，C 专项小件）
9. 翻译「译文翻译腔检查」蒸馏（yomiyasu，TRANSLATION_APPENDIX +1 条，交人拍板）
10. 宪章自动起草骨架（claude-rules 差量，交人拍板）
11. 既有攒批维持：ARIS 规则裁决/multica 看板视角/凭据保险库（cockpit-tools/Terfyn/blindenv/wirken/gestalt 佐证；data/zentao.json 明文密码属该域，交人决策）

## 2026-10-04 02 时复核班（批1 同小时独立复核+补通道——新一轮计划第 1/4 步）

> 01 时批1 全量主扫已由 01:27-01:55 凌晨班完成（116 查询 524 行），本班同小时不重复烧
> 配额，改做两件事：①对凌晨班记录做**独立真实性复核**（应用「声称≠执行」教训——
> 发版核对只认 registry 实查的同款纪律迁移到调研班交接）；②补凌晨班被拦/未覆盖通道。
> 本班消耗：repos 端点 9 仓（gh api 串行 4s 零失败）+ WebSearch 串行 2 发 + pypi 复试 1 发。

### 复核实证（全部命中，凌晨班记录背书）

- **本地实锚**：`flows.py BUILTIN_FLOWS` 实数 **17 类型**（direct/code/novel/serial_novel/article/video_script/doc/translation/rank_scan/defect_retro/research/speech/weekly_report/email/tech_proposal/resume/bid_doc），用户列举 14 场景映射零缺失、注册表另含 doc/resume/bid_doc——与凌晨班矩阵核对一致；`data/skills.json` 教训 **67=lesson 63+procedure 4** 一致（scope 口径：serial_novel 46/code 10/\* 7/direct 4，偏科 69%——category 口径 33% 之外的另一视角，供 D 专项参考）。
- **repos 端点 9 仓全命中**（02 时实测 vs 凌晨班记录）：orca 84,311★（+9 续涨领跑）/dograh-hq/dograh 5,797★（pushed 10-02，新条目真实）/modelbus/one-api-pro 1,001★（pushed 10-03，新条目真实）/DeepSeek-Reasonix 35,736★（pushed 10-03，E 候选首位维持）/**ComposioHQ/awesome-claude-skills 76,423★（正名路径存活实证）**/**Shubhamsaboo/awesome-llm-apps 140,612★（正名路径存活实证）**/EthanYoQ/AI-Novel-Writer 1,285★（pushed 10-03，正主名复核）/hippo-memory 770★（pushed 10-03 活跃，待深挖第 7 项在动）/anthropics/skills 179,507★（+3）。
- **勘误正名闭环**：凌晨班「wshobson/awesome-claude-skills 与 yanhaijing/awesome-llm-apps 为我方猜测路径错误」两处，本班以正主仓直核收口——ComposioHQ（76,423★，pushed 09-18）与 Shubhamsaboo（140,612★，pushed 09-30）均存活，与 09-18/09-30 既有记录衔接，雷达源名实全部对齐。
- **结论：凌晨班（批1）调研记录真实可信**——新条目真实存在、星数精确、push 时间准确，无「报告声称≠实际执行」问题。本班**零机制级新差量（连续第九班稳定期）**。

### 补通道增量（凌晨班未覆盖面）

- **WebSearch 新闻面（批1 主题）**：OpenAI DevDay 2026（09-29）Codex 新增 code review 能力（summaries/diffs/change questions/云自动化）——**E 域在装 CLI 能力增量**（codex 已在装在册，catalog 无需动作；评审属编排层与 CLI 自带层并行，无我们机制差量）；四大独立 SaaS 评审器（CodeRabbit/Sentry Seer/Greptile/Cursor BugBot）横评热文——闭源 SaaS 族，与我们编排内评审不同形态，仅记「多评审器并行横评」方法论与我们双/三评审同构 | E 域/评审域记录 | 2026-10-04
- **WebSearch 写作域**：2026-10 月**无新开源 AI novel agent 发布**（第五班连续确认）；提及面全为 SaaS 写作工具（Storyflow/Sudowrite/Novelcrafter/AIWriteBook/Marlowe）与商业 agent 新闻（OpenAI dots 开源镜像 CopilotKit/OpenDots 21:48 班已录；Manus 2.0+Cue「Cascade 架构」商业域外）| 写作域稳定 | 2026-10-04
- **pypi 通道第三种拦截形态**：WebFetch 直试 pypi.org/search 被**域名安全校验**拦（Unable to verify domain safe）——历班「搜索页 3038B 拦截页」之外新增一形态，同结论：pypi 搜索通道本机不可用维持（npm 通道正常）| 通道记录 | 2026-10-04

### 待深挖队列（2026-10-04 02 时快照，与 10-04 凌晨班一致，零变化）

1. 工具输出统一压缩管线（第 7 验证在手，管线级攒批）
2. 语义缓存（待租户/敏感边界拍板）
3. 演示文稿任务类型空档——交人拍板（不擅自扩）
4. 任务宪章编译为运行时强制策略（ironcurtain，治理远期）
5. 聊天分叉（1code，管线级远期备注）
6. 小红书平台特化交付契约蒸馏（wenzi-xhs 两小件，交人拍板）
7. 教训卡「标记无用」显式负反馈（hippo-memory 差量，D 专项小件；10-04 02 时 770★ pushed 10-03 活跃维持）
8. weekly_report git log 素材通道（GitPulse 差量，C 专项小件）
9. 翻译「译文翻译腔检查」蒸馏（yomiyasu，TRANSLATION_APPENDIX +1 条，交人拍板）
10. 宪章自动起草骨架（claude-rules 差量，交人拍板）
11. 既有攒批维持：ARIS 规则裁决/multica 看板视角/凭据保险库（cockpit-tools/Terfyn/blindenv/wirken/gestalt 佐证；data/zentao.json 明文密码属该域，交人决策）

## 2026-10-04 巡检落位班（七专项 A-G 二轮独立实证 + 落地件确认——新一轮计划第 2/4 步）

> 凌晨班已完成 A-G 首轮实证、02 时复核班独立背书；本班为第 2/4 步落位：对全部关键
> 锚点做**第二轮独立重验**（自己 grep/读码取行号，非转录前班），并确认落地件在工作区
> 且测试绿。零新毛病、零机制级新差量（连续第十班稳定期）。

- **A 专项（token 节约）二轮实证** | 预算熔断 pipeline.py:613/627/687/696（max_tokens_per_run+日/月成本帽 :632-655）；token_meter usage 累进 :690-742（压缩直通分支也进预算表 :738）；压缩守门 :703-736（compaction.enabled 默认关）；三段压缩 compaction.py 实读（剪枝→LLM 摘要→surface replace，saved 净省成账 :163）；cascade :1449-1459（tier 升序，默认关）；_shrink_context_block :2455/:3572（12000 预算分层降级）；resume 校验链 :206-283；召回 skills.py:533 relevance_top（bigram 重叠→有效 karma→won→盈余→新者）；planner 缓存 cache_ttl=3600 planner.py:675/824/967/998 | 四方向（prompt 缓存/语义缓存/diff-only 评审/廉价分流）二轮仍零新机制可抄（语义缓存维持待拍板） | 已覆盖 | 2026-10-04
- **A 专项增量发现：待深挖第 7 项差量已收口一半** | skills.py:346 `_surplus_decay`（066 班落地，docstring 自证「hippo-memory 借鉴」：won=0 且闲置超 30 天宽限的条目盈余线性衰减）+ :366 `_eff_karma` 已接入 relevance_top 排序——hippo-memory 两差量中的「②久未命中衰减」**已落地**；第 7 项剩余面收窄为「①教训卡『标记无用』显式负反馈入口」（UI+一个降权字段，D 专项小件） | 队列收窄 | 2026-10-04
- **B 专项** | market_remote.py:50 SOURCES 六源实证（zcode/anthropic/anthropic-skills/claude-skills/clawhub/cocoloop）；SSRF：assert_public_url :113+禁自动重定向 :146+3 跳上限+体量五重上限（清单 5MB/下载 80MB/解包 120MB/500 文件/单文本 512KB）；文件树白名单 inspect_tree(whitelist) :568；_safe_extract 防路径逃逸 :632 | 本轮新见候选（dograh/one-api-pro/img2threejs/gestalt 等）全为域外/聚合器，三问不过零接入维持 | 已覆盖 | 2026-10-04
- **C 专项** | flows.py:36-128 BUILTIN_FLOWS 17 类型逐一实读（用户 14 场景映射零缺失，doc/resume/bid_doc 为注册表额外三类型）；serial_novel note 签约质量门禁版/rank_scan note A-E 证据分级版与 i18n.js EN 键已对齐；新守卫 test_builtin_flow_fields_have_en_keys（tests/test_i18n_dups.py）3 测试全绿；演示文稿空档维持交人拍板 | 巡检 | 2026-10-04
- **D 专项** | data/skills.json 实数 67 条；category=流程规范 22/节奏爽点 21/情节逻辑 10/人物塑造 7/一致性 4/文笔风格 3（前二类 64% 偏科）；scope=serial_novel 46/code 10/* 7/direct 4（serial 69%）——与凌晨班记录逐字一致零漂移；本轮零新调研方法论需入库，无新增可并条目 | 数据卫生 | 2026-10-04
- **E 专项** | catalog.py 14 条目实证；本机 which 实测在装 13（codex/claude/opencode/qwen/aider/kimi/mimo/grok/pi/dsh/gemini/codebuddy/trae-cli；**openclaw 在册但本机未装**）；九候选（reasonix/fuxi/gitlawb/zero/empryo/zcode/goose/crush）PATH 全空——零接入防死链维持；Reasonix 35,736★ 候选首位维持 | 巡检 | 2026-10-04
- **F 专项** | data/zentao.json 实读：claims=0 零积压、last_error 空、auto_resolve/auto_merge/triage_ai=True 在位、poll_enabled=False（用户侧预期非故障，last_scan 停 09-21 即开关关所致）；zentao.py:269-274 legacy interval 换算在位；不触发实际工单变更、不做在线冒充验收——静态链路巡检通过 | 巡检 | 2026-10-04
- **G 专项** | 过时文案活码 grep（13种/15种/已接11/双源）零命中；「四平台/两平台」命中逐一核对均为真实语义（rank_scan 四平台榜单、publish 双平台绑定）非过时不误修 | 巡检 | 2026-10-04
- **落地件确认（本轮唯一件，不凑数立第二件）** | i18n 两键补译（serial_novel 签约门禁/rank_scan 证据分级）+ test_i18n_dups 三字段 EN 键静态断言——已在工作区，`python -m unittest test_i18n_dups` 3 绿；积压其余小件全部带「交人拍板」标记，不为完成数量制造改动 | 落地 | 2026-10-04

### 待深挖队列（2026-10-04 巡检落位班快照——第 7 项收窄，余同 02 时版）

1. 工具输出统一压缩管线（第 7 验证在手，管线级攒批）
2. 语义缓存（待租户/敏感边界拍板）
3. 演示文稿任务类型空档——交人拍板（不擅自扩）
4. 任务宪章编译为运行时强制策略（ironcurtain，治理远期）
5. 聊天分叉（1code，管线级远期备注）
6. 小红书平台特化交付契约蒸馏（wenzi-xhs 两小件，交人拍板）
7. **教训卡「标记无用」显式负反馈入口（收窄：hippo-memory ②久未衰减已落地 skills.py:346 `_surplus_decay`，剩余仅 ①UI 显式负反馈，D 专项小件交人拍板）**
8. weekly_report git log 素材通道（GitPulse 差量，C 专项小件）
9. 翻译「译文翻译腔检查」蒸馏（yomiyasu，TRANSLATION_APPENDIX +1 条，交人拍板）
10. 宪章自动起草骨架（claude-rules 差量，交人拍板）
11. 既有攒批维持：ARIS 规则裁决/multica 看板视角/凭据保险库（cockpit-tools/Terfyn/blindenv/wirken/gestalt 佐证；data/zentao.json 明文密码属该域，交人决策）

## 2026-10-04 02 时补录班（与凌晨班/02 时复核班同窗并行的第二个独立扫描进程——独有增量）

> 工程注：01:44 同窗独立发射了第二个全量扫描进程（同脚本 116 查询 524 行 422 唯一仓，
> 零失败零限流，输出落临时文件复核后入库）——两进程并行仍在认证搜索 30 次/分内。
> 本节只录前两班均未覆盖的增量，已有记录不重复；本地实锚（17 类型/教训 67/catalog 14/
> 九候选全未装/禅道 claims 零积压 poll_enabled=False/产品 96→mo-so 存活）三向实测
> 结论完全一致，第 1/4 步调研数据三重互证可信。

- **OpenBMB/ChatDev**（34,439★，07-24 后停更）新入库 | ChatDev 2.0 多智能体协作开发框架（清华系中文大盘）| 此前从未入册的框架路线遗漏补格；停更 2.5 月 | 参考（大盘补格）| 2026-10-04
- **tddworks/baguette**（2,159★，10-03 活跃）新入库 | Apple 模拟器无头控制（3D 模型/多指手势/60fps 流）| CUA 域 +1（trycua/cua「不适用（暂）」同域）；我们无 GUI 操控边界不变 | 雷达 | 2026-10-04
- **gadievron/raptor**（3,854★，10-03 活跃）新入库 | Claude Code 变攻防安全 agent | 安全红队域（pentagi 同例：记录不借鉴）| 参考（域外）| 2026-10-04
- **Trending 替身通道恢复**（trendshift.io 本轮直接可达）四小件：**ifixai-ai/iFixAi** 804★（独立审计 agent「agent 做没做该做的事」——「不信任自报」审计族 +1，方向验证）/**lexmount/moli** 1.4k★（Rust 纯写 AI agent 浏览器——浏览器 agent 域 obscura 同域）/**experientiallabs/experiential** 702★（world-model-as-a-harness——形态观察）/**JuliusBrussee/caveman** 340★（原始人话风省 65% token——A3 域 skill 层小件，话风压缩角度新但玩笑向，不入路线图）| 均雷达 | 2026-10-04
- **danyuchn/asd-ste100-skill**（409★）| ASD-STE100 简明技术英语规则作 agent 写作 skill（歧义英文规范化改写）| doc/英文写作域小件——我们 doc 交付契约无「技术英语规范化」角度，蒸馏候选（小，交人拍板）| 借鉴候选 | 2026-10-04
- **B 专项增量：市场六源网络探活本轮全绿**（zcode 41KB/anthropic 189KB/anthropic-skills 2.2KB/claude-skills 94KB/clawhub 45KB/cocoloop 11.7KB，均 HTTP 200）——凌晨班只核清单在位，本轮补连通实证；零新增接入维持 | 已核实 | 2026-10-04
- **复查增量（repos 端点 30 仓，与前两班并集后覆盖面扩展）**：multica 51,885/codegraph 73,114/harbor 5,806/open-code-review 43,480/SkillSpector 19,230（10-02）/stop-that-shit 2,460（10-03 热涨维持）/openhuman 40,508/deer-flow 83,352/ARIS 16,934/oh-my-claudecode 39,559（10-03）/zentao-cli 61★（10-01）/spec-kit 139,986（10-03）/OpenSpec 70,970（10-02）全活跃零 archived；**awesome-harness-engineering 属主补认：ai-boost/awesome-harness-engineering 4,685★（10-03 活跃）**——此前记录缺属主（keywords.md 已同步）；openclaw 391,233★（vs 09-30 +422）| 复查 | 2026-10-04
- 星数跃升互证：atlas 8,930（爬升维持）/CLIProxyAPI 54,006/cockpit-tools 18,610/tuios 4,612/CPA-Manager-Plus 3,730（面板族缓涨）/Understand-Anything 85,164/OmO 69,766/i-have-adhd 53,131——与 topic 页复核同值交叉印证 | 复查 | 2026-10-04

## 2026-10-04 03 时班（批3：计划/spec/长任务——新一轮计划第 1/4 步全类型调研）

> 03 时 %7=3 → 批3（今日首个轮换班，凌晨班跑的批1 不重复）。主扫 scripts/
> borrow_scan_nightly.py --batch 3：**11 查询 50 行，gh api 串行 4s 零失败零限流**；
> repos 端点复查 12 仓（批3 域 4+头部 5+E 域 1+待深挖在动 2）+ WebSearch 串行 1 发
> + hippo-memory 正主路径 search 端点补核。本地实锚三向一致（17 类型/67 教训/
> catalog 14）。**零机制级新差量（连续第十一班稳定期）**。详见 iteration-report.md。

- **croffasia/itsaplan**（879★，10-03 活跃）新入库 | self-hosted Linear/Plane 替代：团队与 AI agent 并肩规划的项目管理+工单 | 禅道/项目管理周边（F 专项）同域不同形态——我们接已有禅道回写，不造组织级工单工具 | 参考（F 域雷达）| 2026-10-04
- **coollabsio/jean**（1,305★，10-02 活跃）新入库 | AI agent 的 dev environment | dev env 托管路线参考（我们=编排台，工作目录即环境）| 雷达 | 2026-10-04
- **AI45Lab/OpenART**（228★，10-03 活跃）新入库 | 动态长程有状态环境的 agent 安全/鲁棒性评测框架 | 评测域补格（harbor/Kiln/evalscope 旁）；自评测缺口维持远期 | 参考（评测域）| 2026-10-04
- **malevrigns/atlas-agent-control-plane**（105★，09-13）新入库 | auditable 控制面：evidence-backed memory+governed tool runtime+checkpoint DAG recovery | 证据锚/事件流计数/checkpoint 恢复均有对应物；DAG 形态恢复同域再验证 | 方向验证 | 2026-10-04
- **oliver-kriska/claude-elixir-phoenix**（560★，10-02）新入库 | Claude Code 插件：26 专家 agent+「Iron Laws enforcement」 | 规则强制执法向——待深挖第 4 项「宪章运行时化」（ironcurtain）+1 佐证 | 方向验证 | 2026-10-04
- **ZykjShadow/Async**（475★，05-19 停更 4 月+）新入库 | IDE 形态 AI 编码工作台（chat+planning+agent 统一桌面） | IDE 路线历史标本 | 参考 | 2026-10-04
- **MARKTECHPOST-AI-MEDIA-INC/AI-Agents-Projects-Tutorials**（2,916★）新入库 | 多 agent 系统/记忆/规划教程清单 | 清单域雷达，三问不过不接 | 雷达 | 2026-10-04
- 小件一句带过：InternAgent 1,444★（科研长程框架，域外）/ OneDayAgent 36★（长程 harness 学术向）/ cogneva 30★ / tiger_cowork 62★ / Echo 8★ | 雷达 | 2026-10-04
- **CONTINUUM（Cyrax321）28★（10-02 push）查重=09-21 已录同仓**（语义检查点+幂等动作账本，naman159/continuum 为另一写作记忆层仓勿混）——幂等键待深挖攒批维持，非新面孔 | 查重勘误 | 2026-10-04

### 复查记录（repos 端点 12 仓 + search 补核，04 时）

- 批3 域全活跃：github/spec-kit **140,000★**（10-03 push，vs 凌晨班 139,986 +14）/ OpenSpec 70,977（10-02）/ planning-with-files 27,267（10-01）/ agentmemory 29,117（10-03 push）
- 头部续涨：orca **84,351★**（vs 02 时 84,311 +40 续领跑）/ superpowers 294,848 / anthropics-skills **179,515**（+8）/ claude-mem **95,482**（+53）
- E 域：DeepSeek-Reasonix **35,737★**（10-03 push）候选首位维持；九候选全未装零接入
- 待深挖在动仓：**kitfunso/hippo-memory 770★（10-03 push，正主路径本班经 search 端点勘定——此前记录只写「hippo-memory 770★」未留全路径，已补）/ lifedever/claude-rules 192★（03-19 维持）**
- **星数跃升互证：asd-ste100-skill 409→3,211★**（push 停 09-08 无新提交——存量放量疑入榜；ASD-STE100 技术英语规范化蒸馏候选价值不变，队列第 11 项内交人拍板）
- 状态变更：12 仓零新增 archived；批3 头部（lazycodex 3,722★ 09-22 2k→3.7k 大涨 10-03 push/worktrunk 8,726 活跃）无机制增量

### 待深挖队列（2026-10-04 04 时快照，与巡检落位班一致，零变化；第 4/11 项本班同域佐证 +1）

1. 工具输出统一压缩管线（第 7 验证在手，管线级攒批）
2. 语义缓存（待租户/敏感边界拍板）
3. 演示文稿任务类型空档——交人拍板（不擅自扩）
4. 任务宪章编译为运行时强制策略（ironcurtain，治理远期；本班 claude-elixir-phoenix「Iron Laws」+1 佐证）
5. 聊天分叉（1code，管线级远期备注）
6. 小红书平台特化交付契约蒸馏（wenzi-xhs 两小件，交人拍板）
7. 教训卡「标记无用」显式负反馈入口（收窄：剩 ①UI 负反馈，D 专项小件交人拍板；kitfunso/hippo-memory 770★ 在动维持）
8. weekly_report git log 素材通道（GitPulse 差量，C 专项小件）
9. 翻译「译文翻译腔检查」蒸馏（yomiyasu，TRANSLATION_APPENDIX +1 条，交人拍板）
10. 宪章自动起草骨架（claude-rules 192★ 维持，交人拍板）
11. 既有攒批维持：ARIS 规则裁决/multica 看板视角/凭据保险库/CONTINUUM 幂等动作账本（Cyrax321 10-02 push 同域再验证）/ASD-STE100 技术英语规范化（3,211★ 放量，交人拍板）

## 2026-10-04 04 时巡检班（七专项 A-G 三轮独立实证 + 落地立项——新一轮计划第 2/4 步）

> 三只读代理分域实证 + 本班自查实数（skills.json 计数/which 全量/六源真实 URL 连通/
> git log 差量定位/近重复对复核），全部当前行号，非转录前班。零代码毛病，两条知识库
> 记录口径修正；落地立项 1 件（第 8 项 git log 通道）。详见 iteration-report.md 04 时节。

- **A 专项** | 预算熔断 pipeline.py:613-629+闸 :687-701；日/月成本帽 :632-669 先于 token 闸；token_meter 双路记账 :721-746；压缩守门 :231-241+:703-737；三段压缩 compaction.py:119-179（saved :156-166）；预检 step_runner.py:75-89；**diff-only 评审已在位** :1016-1022（只发 _git_diff :944）；深度分级 :999-1013；cascade :1449-1465；_shrink 定义 :2455-2490；resume :206-285；召回 skills.py:533-555+衰减 :346-368；planner 缓存 :675/:824/:967/:998（modelhub.py:3673） | 四方向：prompt 缓存无但 resume+精确缓存+cached 计价已拿走大头；语义缓存无维持待拍板；diff-only 已有；廉价分流 cascade 外另有难度选模双通道（builtin_agent.py:91/:118、modelhub.py:2662、dispatch.py:153）已满配——零未覆盖差量 | 已覆盖 | 2026-10-04
- **A 记录勘误（非代码毛病）** | 「_shrink_context_block 两处调用点」旧录不符现状：全仓现仅 1 处调用（:3572），:2455 是定义；两处为 09-28 报告期历史形态——口径修正入档 | 勘误 | 2026-10-04
- **B 专项** | 六源在位（market_remote.py:50-76）；**本班真实 URL 连通六绿**（zcode 41,930B/anthropic 189,668B/anthropic-skills 2,213B/claude-skills/clawhub/cocoloop 11,703B）；SSRF assert_public_url :113-133+禁重定向 :145-159+3 跳上限+git 通道同口径 :834/:841；体量上限实为**六重**（:91-99，_CAP_TOTAL_TEXT=4MB 旧录漏计，第 2 条口径修正）；inspect_tree :568-604 剥离式白名单+_safe_extract :632-670 三重防逃逸；唯一落地通道 install_files（market.py:267）装前 skill_scan+typosquatting 0.82+指纹 | 本班零新候选进评估面，零接入维持 | 已覆盖 | 2026-10-04
- **C 专项** | 17 类型（flows.py:36-128）逐一核对；i18n 查表式（中文原文→EN）17 name+17 goal_hint+13 note 零缺失（serial_novel :1973/rank_scan :1932 两新键在位，前端消费 app.js:447/:775 实证）；演示文稿空档维持交人拍板 | 巡检 | 2026-10-04
- **D 专项** | 67 条零漂移（lesson 63+procedure 4；流程规范 22/节奏爽点 21=64% 偏科维持）；同 scope ≥0.8 共 4 对逐一判读**零真重复**（3 对=维度参数化模板变体各带分数数据不并；1 对=共享词汇边缘重合、动作不同不并）；方法论小教训：difflib/字集 proxy 会把模板变体误判重复（一度误报 1,107 对），查重须 title+content 双判读后人工定性（只入报告不入教训库） | 数据卫生 | 2026-10-04
- **E 专项** | catalog 14 条目（catalog.py:32-215）；本机在装 13、openclaw 在册未装；九候选（含 herdr）PATH 全空零接入防死链；Reasonix 35,737★ 候选首位维持 | 巡检 | 2026-10-04
- **F 专项** | 全链实读：automation daemon（TICK 25s）→fire_due zentao.py:2470→_SCAN_LOCK 单飞 :2403→_scan :2332；legacy interval_hours:2 换算 :271-279 在位（现配置仍是老键，换算即新默认 5 分钟非故障）；_route_one :2128→_launch_fix :1556→_reconcile :2093→_finish_ok :1912（merge/resolve 双闸+幂等）→_finish_failed :2035（3 次上限）；**claims=0 零积压、last_error 空、poll_enabled=false**（last_scan 停 09-21 即开关关，用户侧预期）；本班只读未触发任何真实工单变更/群通知；禅道 AI 竞品零新增 | 巡检 | 2026-10-04
- **G 专项** | 过时文案活码 grep 零命中；「四平台」3 处均准确（paihang.py:97-101 实 4 源）；index.html 断链零命中（锚点/本地资源/60 SVG 精灵全解析）；唯一口径漂移即 A 专项 _shrink 勘误，非产品毛病 | 巡检 | 2026-10-04
- **落地立项（唯一件，不凑数）** | **第 8 项 weekly_report git log 素材通道**：契约已承诺（skillpacks/market/weekly-report.md:17）、禅道半边已落地（pipeline.py:4716-4726+zentao.py:1192+test_weekly_brief.py）、git log 半边全仓无实现——模型写周报提交素材缺源；落点 _draft_prompt_for weekly_report 分支旁新增 _gitlog_brief(workdir)（复用 gitmod._git gitmod.py:31，--since 7 天 --pretty=%h %ad %s 上限 40 条），非仓/无提交/异常一律静默空，与禅道 brief 并列各自独立 try；测试 test_weekly_gitlog.py 三案（非仓 ""/2 commits 含摘要/异常 ""）——行号/契约/测试已锚定，交第 3-4/4 步 | 立项 | 2026-10-04

### 待深挖队列（2026-10-04 04 时巡检班快照——第 8 项升「已立项待落地」，余同 03 时版）

1. 工具输出统一压缩管线（第 7 验证在手，管线级攒批）
2. 语义缓存（待租户/敏感边界拍板）
3. 演示文稿任务类型空档——交人拍板（不擅自扩）
4. 任务宪章编译为运行时强制策略（ironcurtain，治理远期；claude-elixir-phoenix「Iron Laws」+1 佐证）
5. 聊天分叉（1code，管线级远期备注）
6. 小红书平台特化交付契约蒸馏（wenzi-xhs 两小件，交人拍板）
7. 教训卡「标记无用」显式负反馈入口（剩 ①UI 负反馈，D 专项小件交人拍板；hippo-memory 770★ 在动维持）
8. **weekly_report git log 素材通道——已立项（04 时巡检班，落点/契约/测试三案锚定），待第 3-4/4 步落地提交**
9. 翻译「译文翻译腔检查」蒸馏（yomiyasu，TRANSLATION_APPENDIX +1 条，交人拍板）
10. 宪章自动起草骨架（claude-rules 192★ 维持，交人拍板）
11. 既有攒批维持：ARIS 规则裁决/multica 看板视角/凭据保险库/CONTINUUM 幂等动作账本/ASD-STE100 技术英语规范化（3,211★，交人拍板）

## 2026-10-04 收口班（计划第 4/4 步·联调回归+发版 v0.1.78）

- **32 位 Python 全量 discover 长跑中途静默退出（exit 0 无统计行）（实案）** | 本机 Python38-32 全量 `unittest discover`（2118 项）两次跑到 1461 行附近进程消失、`echo $?` 仍 0、日志无 Ran/OK 统计——并发多班次（8+ python 进程）下 32 位进程内存上限触顶即崩且退出码失真 | 处置：按字母序分片跑（`test_[a-r]*.py` + `test_[s-z]*.py`）每片独立进程有统计行，改动面测试单跑全绿 | **教训：长跑测试「exit 0」不算绿，必须有 Ran/OK 统计行才算证据；32 位解释器 + 并发负载 → 分片跑测是正打法** | 发版工程 | 2026-10-04
- **npm publish 命令挂起 ≠ 发布未落地（实案，与 10-03 「声称完成实未发」互为镜像）** | 本班 publish 两次前台 240-300s 零输出（疑似交互/网络长挂），杀任务重试后 registry 直查 latest=0.1.78、tarball shasum 在架——第一次挂起调用实际上传成功 | 与 10-03 教训合流成同一条：**publish 双向都不可信（回显可信它未发、挂起可指它已发），唯一证据 = registry 独立实查（npm view dist-tags + shasum）**；下次发版打法：后台起 publish + 轮询 registry，勿等命令行回显 | 发版工程 | 2026-10-04
- **i18n EN 键滞后收口（代码教训）** | flows.py note 改版（45d772a 签约门禁/f0bd197 A-E 分级）时 i18n EN 键未跟，英文界面静默回落中文（t() 未命中不报错）| 处置：两键补齐 + `test_i18n_dups.test_builtin_flow_fields_have_en_keys` 全量守卫（17 类型×三字段逐一有 EN 键，漏翻当场报错）| **教训：后端文案与 i18n 键必须同 commit 联动，守卫测试固化** | i18n 工程 | 2026-10-04
- **main 既有测试红 26F+3E 与本轮零交集的判别法** | 分片跑出 29 失败/错误，逐个归因：bookmeta 顶层 sys.exit 写法（8e30224）、publish 建书对账收紧断言未跟（07d066b/4a00d48）、usage_stats 模型路由环境耦合 | **判别法：失败文件与本轮 diff 文件集求交=空 → 既有红，如实记录不越界代修（防踩踏），留待属主班次** | 测试工程 | 2026-10-04

## 2026-10-04 05 时收尾班（计划第 4/4 步·五道关+推送+发版 v0.1.79）

- **第 8 项 weekly_report git log 素材通道落地发版（收口）** | `31f0e93`（feat：_gitlog_brief + weekly_report 分支并列注入，2 files +79）+ `ef3e755`（release v0.1.79：package.json bump + CHANGELOG/README relnotes 同步），五道关全过（详见 iteration-report.md 05 时收尾班节），推送 `5406ee0..ef3e755` 上远端 | 待深挖第 8 项关闭 | 落地收口 | 2026-10-04
- **发布闸 prepublishOnly 双实证（补 117 行收口班案例后的第二例）** | 本班首次 `npm publish` 被闸以「工作区不干净」正确拦下（docs 在制品+smoke 残留 codebee-0.1.78.tgz 在树），registry 实查维持 0.1.78 零污染——闸先于上传拦截有效；处置顺序=清自身残留→docs 落库→复跑 publish。**打法固化：多班并发时 publish 前必过 release_gate.py 本地预检，工作区不净先落 docs 再发** | 发版工程 | 2026-10-04
- **既有红集本班复核（25F+3E 两轮一致）** | 分片 A/B 失败集与收口班 26F+3E 同域（14 个测试文件），代表样本净进程单跑复现=确定性既有红，与本轮 diff 求交=空；另录：同窗落地班「discover 全量 exit 0 全绿」与本班分片红并存——并发负载下失败集环境耦合两录并呈，**判别永远以「与本轮 diff 文件集求交」为准，不以单次全量绿/红定案** | 测试工程 | 2026-10-04
- **仓库级判定命令必须从仓库根跑（本班实案）** | 在 tests/ 子目录跑 `ls scripts/`+`git ls-files` 得空/子域结果，一度误判 scripts/release_gate.py「被删」并写进发版叙事——实为在册且在位（e998457 引入）；cd 回根目录复验即翻案 | **教训：`git ls-files`/`ls` 受 cwd 限域，仓库级存在性判定先 `cd` 仓库根再下结论** | 工程纪律 | 2026-10-04
- **模型评审子代理网关 400（基础设施阻塞实录）** | code-reviewer 代理两次（含显式 model 指定）均被 API 网关拒：HTTP 400 [1211]「模型不存在」，网关侧实际收到 model=auto——代理忽略 model 参数透传 auto 所致 | 处置：自审四轴（惯例/边界/测试覆盖/残留）即本轮评审记录并入报告，基础设施问题如实交运维 | 工程设施 | 2026-10-04

## 2026-10-04 07 时班（批6：框架/平台/SDK 生态——新一轮计划第 1/4 步全类型调研）

> 06 时 %7=6 → 批6（今日已跑批1/批3，批6 首跑）。主扫描 `scripts/borrow_scan_nightly.py` 全量：
> A 常驻 78（含内置 B1 11）+ B6 轮换 10 + A1u/A1p2 双轮 16 = **115 查询 523 行 466 唯一仓，
> 零失败零限流**（gh api 串行 4s）。雷达源 C 全过：repos 端点 32 仓全命中（零 archived）+
> topic 8 + 新锐轮四组（created:>09-27）+ npm 两查 + pypi（200/3038B 拦截页，与历班同效）。
> **零机制级新差量（连续第十二班稳定期）**，新面孔定性入库如下。

### 新条目（均参考/雷达级，无未覆盖机制）

- **Graphify-Labs/graphify**（123,546★，10-02 活跃）| 代码库+文档+SQL schema+配置+PDF → 可查询知识图谱 | **代码理解图谱域第三巨型**（codegraph 72k/Understand-Anything 85k 同族）且为本轮最大遗漏补格；我方「知识库不做代码结构索引」边界不变（codegraph 结论沿用）| 参考（巨型补格）| 2026-10-04
- **GoogleCloudPlatform/race-condition**（234★，10-02）| Google Cloud Next '26 keynote 多 agent 仿真演示 | 大厂演示域生态事实 | 参考 | 2026-10-04
- **datagallery-ai/dataagent**（780★，09-30）| DataFoundry 数据分析 AI 工作台（数据源/知识/工具统一）| 数据分析域外（无对应预置类型）| 参考（域外）| 2026-10-04
- **AgentEvalHQ/AgentEval**（154★，10-03）| .NET agent 评测工具箱（工具调用验证/RAG 质量）| 评测域 +1（harbor/Kiln/OpenART 旁）；自评测缺口维持远期 | 参考（评测域）| 2026-10-04
- **SichengLong26/deepresearch_agent_harness**（119★，09-09，中文）| GraphRAG+联网混合检索深研 harness（持久 Memory+自进化 Skills）| research 域中文新锐（deepresearch-agent 同域）；检索降噪两条已落地，无新机制面 | 参考 | 2026-10-04
- **edenfunf/reelmimic**（1,080★，10-02）| 风格模仿视频生成（AI crew 驱动）| video_script 域生态第 5 例（awesome-claude-video-skills/motion-video-kit/FireRed-OpenStoryline/onetake 之后）；成片域维持远期 | 雷达（video_script 域）| 2026-10-04
- **yaoyuxiang-gnn/agent-guard**（20★，10-02）| agent 预算帽/失控循环检测/熔断，零依赖 | 预算熔断+停滞看门狗同构独立验证 +1（MonetiseBG/circuit-breaker 后）| 方向验证 | 2026-10-04
- **Yan-W-u/zh-novel-writing-toolkit**（1★，10-03）| 中文长篇工具集：写作教练+去 AI 味（DeepSeek DS1–DS13）+标点句长检查 MCP | 写作域微型；去 AI 味第 6 例（措辞层+标点面），aiflavor 深一层维持 | 雷达（写作域）| 2026-10-04
- **lyjsyyds/dsh-novel-studio**（0★，10-03）| DSH 小说创作分区（书/角色/世界观）| dsh 插件生态第 11 例 | 雷达 | 2026-10-04
- **iloom-ai/iloom-cli**（111★，09-18）| 开发者工作流 CLI+VS Code 扩展 | 同形态小件 | 雷达 | 2026-10-04
- **aklivity/zilla**（1,716★，10-03）| 事件驱动应用与 AI agent 的多协议网关 | 网关域 +1（基础设施向）| 参考（网关族）| 2026-10-04
- **cool-icu0/xgent**（119★，中文）| Spring AI+DDD+google-adk 轻量编排框架 | 框架路线中文小标 | 参考 | 2026-10-04
- **Idun-Group/idun-agent-platform**（203★）| LangGraph/ADK agent 一键 FastAPI 化运行时 | 框架部署面小标 | 参考 | 2026-10-04
- **gateway/npm 面小件**：Mirrowel/LLM-API-Key-Proxy（556★）/nghyane/launchdock（326★）网关族 +2；npm `agentwrangler`（Claude Code token 花费与产出本地观测——opencode-metrics 族 +1，用量页三件已落地）/raycoder（本地可恢复编排器）/hungry-ghost-hive 均三问不过 | 雷达 | 2026-10-04
- **归属更新**：`openclaw/acpx`（3,312★，10-03）——09-20 记「clodex/acpx」今在 openclaw org 名下，headless ACP 客户端定位不变 | 归属勘误 | 2026-10-04

### 复查记录（repos 端点 32 仓 + 新锐轮，06:50-07:4x）

- 头部全活跃：orca **84,405★**（vs 02 时 84,351 +54 续领跑）/superpowers 294,898（09-27 后无 push）/ECC 272,205（+186）/hermes-agent 250,977/deepseek-harness 242,876/opencode 211,631/anthropics/skills 179,528（+13）/claude-code 149,205/codex 127,752
- 写作域：oh-story 7,236（10-03 活跃）/drama-skills 2,473（10-03）/sepia 2,944（09-23 后无 push 满额维持）/inkos 10,116（09-27 后无 push 观察维持）/yomiyasu **1,305★（+13 活跃，翻译腔项标的在动）**/AI-Novel-Writer 1,285（清账维持）/chinese-novelist-skill 3,281（09-06 后无 push）/AI-Novel-Writing-Assistant 3,064（09-23）
- E 域：**DeepSeek-Reasonix 35,740★（10-03 push，E 候选首位维持）**；ZCode 7,373（09-29 后无 push）；本机 which 实测在装 12（codex/claude/opencode/qwen/kimi/mimo/grok/pi/dsh/gemini/codebuddy/aider——trae-cli 本班 bash 通道未复现，04 时班曾检出，环境差异如实记）；九候选全未装零接入防死链维持
- 待深挖在动仓：hippo-memory 770★（10-03 push，第 7 项 UI 负反馈维持）/claude-rules 192★（pushed 停 03-19——第 10 项「宪章自动起草」源头停更 6 月+，差量角度仍成立但无增量参考）/GitPulse 16★（第 8 项已随 v0.1.79 落地清账）/wenzi-xhs 148★（第 6 项维持）
- 治理/评审域：governance-toolkit 6,389（10-03）/open-code-review 43,508（10-01）/SkillSpector **19,261★（+31 续放量）**/codegraph 73,123（10-03）/harbor 5,810（10-03）
- awesome 六清单全活跃（awesome-claude-code 55,017/awesome-mcp-servers 95,792/VoltAgent 35,164/harness-engineering 4,688/awesome-claude-skills 76,430/awesome-llm-apps 140,633）
- 新锐轮（created:>09-27）：agent 域全为 genpark 蓄水农场族（已录不重复）；写作域微型三连（kietnovel 2★/zh-novel-writing-toolkit 1★/NovelFoundry 1★+PerkinsWritingSpace/novel-deconstruct/dsh-novel-studio 0★）——**写作域无新竞品第五班连续确认**；multiagent 域 reelmimic 1,080★ 为最大（已录）
- topic 信号：topic:claude-code 首六全已录+graphify（123.5k 新巨型补格）；topic:claude-skills 首五全已录（头部化延续）；topic:mcp 首位 n8n 206,586（生态坐标）

### 七专项快照（本班实证 + 同日前班引用）

- **A**：同日 04 时巡检班三轮实锚全在位（预算熔断/diff-only 评审/cascade/_shrink/planner 缓存）；A3 组扫描零新机制 | 已覆盖
- **B**：market_remote.py:51-73 六源 grep 实证在位；本班新见候选（graphify/dataagent/zilla 等）过三问均不过，零接入维持 | 已覆盖
- **C**：flows.py:37-122 BUILTIN_FLOWS 17 类型 grep 实证（用户 14 场景映射零缺失，另含 doc/resume/bid_doc）；演示文稿空档维持交人拍板 | 巡检
- **D**：同日 04 时班 67 条零漂移判定沿用（本班零新方法论需入库）| 数据卫生
- **E**：catalog.py 14 条目 grep 实证；本机在装 12-13（trae-cli 环境差异如实记）；九候选全未装 | 巡检
- **F**：同日 04 时班全链实读沿用（claims 零积压/poll_enabled=False 用户侧/产品 96 存活）；本班周边扫描零新禅道 AI 竞品（第 8 例后持续为零）| 巡检
- **G**：同日两班过时文案 grep 零命中沿用；本班零新毛病 | 巡检

### 任务类型矩阵（列举口径 vs 注册表实数，本班 grep 复核）

用户 14 场景 vs `flows.py BUILTIN_FLOWS` 实数 **17**：direct/code/novel/serial_novel/article/research/video_script/tech_proposal/translation/speech/weekly_report/email/rank_scan/defect_retro 全映射；另含 doc/resume/bid_doc。差异如实记录，无遗漏（与凌晨班/02 时复核班三向一致）。

### 待深挖队列（2026-10-04 07 时快照——第 8 项已清账移出，第 10 项加停更注记，余同 04 时版）

1. 工具输出统一压缩管线（第 7 验证在手，管线级攒批）
2. 语义缓存（待租户/敏感边界拍板）
3. 演示文稿任务类型空档——交人拍板（不擅自扩）
4. 任务宪章编译为运行时强制策略（ironcurtain，治理远期）
5. 聊天分叉（1code，管线级远期备注）
6. 小红书平台特化交付契约蒸馏（wenzi-xhs 两小件，交人拍板）
7. 教训卡「标记无用」显式负反馈入口（剩 ①UI 负反馈，D 专项小件交人拍板；hippo-memory 770★ 在动维持）
9. 翻译「译文翻译腔检查」蒸馏（yomiyasu 1,305★ 在动维持，TRANSLATION_APPENDIX +1 条，交人拍板）
10. 宪章自动起草骨架（claude-rules 192★ **pushed 停 03-19 停更注记**，差量角度成立交人拍板）
11. 既有攒批维持：ARIS 规则裁决/multica 看板视角/凭据保险库/CONTINUUM 幂等动作账本/ASD-STE100 技术英语规范化（交人拍板）

## 2026-10-04 09 时班（计划第 2/4 步·七专项 A-G 全实证巡检）

> 分支 main（98445b4）。零代码改动（巡检+提案步）；A-G 逐项行号级实锚详见 docs/borrow-log/full-type-round.md，
> 本节只录增量事实与蒸馏条目。全程只读（F 专项零禅道写回、E 专项零安装、B 专项零装包）。

### 口径勘正与实测增量

- **i18n「EN 键」口径勘正** | i18n.js 实为中文字符串→EN 直查字典（无 `flow_*_name` 式键）；守卫测试（test_i18n_dups）真实断言=17 类型×三字段的中文字符串在 EN 字典有映射。上步报告「EN 键」措辞按此理解，机制无差 | 口径记录 | 2026-10-04
- **经验库偏科实测收敛** | data/skills.json 67 条 category 实测：流程规范 33%/节奏爽点 31%/情节逻辑 15%/人物塑造 10%/一致性 6%/文笔风格 4%——「流程规范 61%」旧口径为 scope 视角（serial_novel 域内）；全库视角无病态偏科。标题完全重复 0、前 12 字近似重复 0——零可并项零删除 | 数据卫生 | 2026-10-04
- **A 专项四方向不重复建设确认** | prompt 缓存/语义缓存全库 grep 零命中（确实未建，维持待深挖第 2 项卡租户边界）；廉价分流已有完整链（classify_difficulty modelhub:3536 → bind_agent :2217 → cascade_reorder pipeline:1471）；diff-only 评审已半实现（_git_diff:944+深度分级:1015+40 行封顶）；_shrink_context_block:2471 仅连载路径挂接（远期备注不扩展） | 已覆盖 | 2026-10-04
- **E 专项本机在装 8（bash command -v 通道）** | codex/claude/qwen/opencode/aider/pi/gemini/codebuddy FOUND；openclaw/kimi/mimo/grok/dsh/trae-cli 未检出——与 07 时班「12 在装」环境差异如实记（kimi/mimo/grok/dsh 的 detect 名与包名不同，未强断未装）；五候选 reasonix/fuxi/gitlawb/zero/empryo 全未装，零接入防死链维持 | 巡检 | 2026-10-04
- **F 专项当前 data 目录禅道为未配置态** | data/zentao.json claims=0/last_error 空/无 product_profiles/poll 未启——用户侧未接实例属预期（零积压零漂移）；链路实锚 automation.py:580 fire_due + main.py:3427 start + main.py:1768 /api/zentao/scan；路由三规则+失败不回写口径（zentao.py:28-36）在位 | 巡检 | 2026-10-04
- **B 专项白名单+SSRF 全链在位** | 六源（market_remote.py:50-73）；assert_public_url:113（仅 https+全解析 IP 拒私有）+逐跳重过网关（:146/:156）+五重体量上限；inspect_tree:568 剥离式+白名单收敛（:588）；skill_scan 装前扫+typosquatting 对账（market.py:130）+内容指纹突变警示+装后冒烟+行为卡——候选三问全不过零接入 | 已覆盖 | 2026-10-04

### 落地提案（第 3 步执行清单，经评审后动）

> **执行状态（10 时班复核）**：提案 1+2 已由前次调用在工作区实施并过验收——翻译腔条在 pipeline.py:2319（唯一消费点 :4748 不动）、lessons 67→70 三条入位、守卫测试 test_full_type_round 5/5 + test_i18n_dups 3/3 绿、py_compile 过/UTF-8 无 BOM/外来标记零命中。第 3 步只剩全量 discover+五道关+提交推送。详见 full-type-round.md「验收复核」节。

1. **TRANSLATION_APPENDIX 追加「翻译腔自查」条**（待深挖第 9 项清账候选，yomiyasu 差量）| 真实函数：TRANSLATION_APPENDIX 常量（pipeline.py:2317，消费点 :4741-4743）| 拟改仅此常量 +2-3 行 | 验收：py_compile+关键词断言+消费点不变 | 回滚：单常量回退
2. **D 专项蒸馏 3 条入库**（非代码）| 入口：skills.upsert_lesson（skills.py:421，source="borrow-log 2026-10-04"，category=流程规范）| ①三问接入判据 ②知识库不做代码结构索引边界 ③diff-only 评审机制对账 | 验收：67→70 零重复 | 回滚：按 title 精确移除

### 待深挖队列（09 时快照——零变化，11 项维持 07 时版）

提案 1 若第 3 步确认执行，第 9 项下轮清账移出；第 7 项（教训卡负反馈）因涉及 skills+main+UI 三处维持「需扩评审范围」不动。

## 2026-10-04 10 时班补录（第 1/4 步执行端·批2 复跑）

> 本机执行时 09 时 %7=2 → 批2（学习记忆与自我改进）复跑——今日首跑为 07 时班批6，批2 为本日首次。
> 走 scripts/borrow_scan_nightly.py --batch 2 既有通道（gh api 串行 4s+403 退避，与历班同口径）：
> 10 组查询全 ok 零失败零限流，47 唯一仓 **100% 已录、零新面孔——稳定期第十三班确认**。
> 本次价值=在动仓 freshness 增量（对 07 时班/近期已录值）：

- **scientific-agent-skills 46,116→47,492（+1,376，本日单库最大增量）**；agentic-awesome-skills 46,784→47,228（+444）——技能生态两大库放量继续
- 缓涨：codegraph 73,123→73,130（10-03 活跃）/ graphiti 31,395→31,416 / googleworkspace/cli 31,100→31,237 / anbeime/skill ~7k→7,509 / Yuxi 7,258→7,263 / projectmem 849→850（10-03）
- 持平：obsidian-second-brain 4,666 / pro-workflow 2,899 / compozy 2,790 / stash 328 / prax-agent 273 / mengram 204 / MegaMemory 709
- 批2 主体结论沿用：记忆域竞品密度最高（episodic/procedural/事前警告/trace→教训各族齐备），机制面与我方经验库双通道（lessons 注入+知识库检索检索召回）重合度高，无未覆盖机制
- **待深挖队列零变化**（零新候选零清账，11 项维持）；keywords.md 零调整（零新依据不动）
- 纪律对账：A/C 高重合面未重跑（07 时班 2 小时内已全覆盖，按 keywords.md「结果高度重合即跳余页省配额」纪律）；pypi 通道拦截页与历班同效如实记

## 11 时班·v0.1.80 发版终录（全类型轮第 3/4 步）

- **落地**：提案 1+2 验收后过五道关——b68a6d1（翻译腔自查前置+守卫测试+蒸馏 3 条）+ 1143ec0（v0.1.80 三件套），连同前班 2 提交一并推送；提案 1 明细与验收实录见 full-type-round.md。
- **publish 慢在途新实证（补 E409 条）**：registry 读（npm view）秒回、写（publish PUT）可长挂 ~15min 才落地；`npm publish | tail` 管道缓冲全程零输出≠卡死——**判落地只以 `npm view codebee version` 为准，勿凭无输出过早杀重发**；本次第 1 次尝试确被杀（后重发成功，未触发 E409 属侥幸，下次先等足 30min 窗口再动）。
- **release_gate 拦 CRLF 实证**：stash pop 的行尾归一化会让 gitignored-but-tracked 测试文件工作态出纯行尾 M，prepublishOnly 闸按「工作区不净」拦发——`git restore <file>` 即解；stash 前后跑测试的对照实验（既有红判别）要预留这步收尾。
- **bookmeta 脚本式测试吞进程实证**：tests/test_bookmeta_chain.py 顶层 `sys.exit(1 if FAILS else 0)`，unittest 模块直跑模式 import 即执行全脚本并 exit(0)——同批后续模块一个不跑、判定行不打印、exit=0 假绿；**测试判别一律 discover `-s tests` 模式**（base.py 导入路径也只有该模式解析，模块直跑出 9 个伪 ERROR 的教训同源）。

## 11 时班·第 4/4 步五道关覆核与管道假象实证（执行端，与上节发版终录互补）

- **闸②真实结果**：全量 discover 重定向落盘实测 **REAL_EXIT=0、1546 ok 零失败**；首跑 `... | tail -25` 曾见 `test_core_guards ... FAIL` 一例——单跑 6/6 绿 ×2+复跑全绿+失败域（selfupdate）与本轮 diff 零交集，判既有序贯耦合 flaky 留观察。
- **管道 `$?` 假象（与上节 publish 零输出假象成对）**：`python -m unittest discover ... | tail` 的退出码是 tail 的——首跑实有 FAIL 却报 exit 0，靠逐行扫输出抓 FAIL 字样才暴露；上步报告「全量 exit 0 全绿」同命令同假象，本步复跑才实证。**闸②判据必须 `> log 2>&1` 后看 `$?`，管道判绿一律不作数**。
- **评审通道实录**：code-reviewer 代理网关 400（既有实录同型再现）→ 换 ocx-self 通道一次通过——「无阻塞问题可提交」，CRITICAL/HIGH/MEDIUM 零、LOW 一条 docstring 缩进（已修，被并行提交时序定格为修前版，随本沉淀提交恢复）；评审员额外实证核过 tests/base.py 隔离链有效性（含 revisions.make_revision 全模块无 I/O 的隐蔽面）与断言非空转（「翻译腔」旧常量确不含、溯源注释在常量外不虚过）——隔离与断言双验通过。
- **E409 良性第二例旁证**：本端 publish 撞 `cannot publish over 0.1.80`——版本已由上节记录的在途 publish 落地（registry latest=0.1.80，shasum 0866adbe 实查在架）；与 v0.1.79 E409 staged 竞态同型，**并发班发布判据再钉一次：只认 `npm view`，撞 E409 即已达成**。

## 2026-10-04 12 时班（批5：检索/知识/浏览器——新一轮计划第 1/4 步全类型调研）

> 12 时 %7=5 → 批5（今日已跑批1/批3/批6/批2，批5 本日首跑）。A 常驻 78 组已由 07 时班
> 全量覆盖（2-5 小时内），按 keywords.md「结果高度重合即跳余页省配额」纪律不重复主扫，
> 本班 = `--batch 5` 轮换 10 组（50 行零失败零限流，gh api 串行 4s）+ 雷达源 C
> （repos 端点 19 仓 + 新锐轮三组 created:>09-28 + 禅道周边 + npm 两查 + pypi 复试）。
> **零机制级新差量（连续第十四班稳定期）**，新面孔定性入库。

### 新条目（批5 域 + 雷达）

- **strands-labs/strands-decider**（299★，10-03 push，AWS Strands 官方 org）新入库 | 「系统一决策模型」：agent 工作流中的小决策（选项挑选/评分）用小快决策模型处理，不进 LLM | **Jev/TypeSafe System One 范式簇（09-25 记）获大厂官方实现**——「机械决策不进 LLM」从社区簇升格 AWS org 级产品；与我们 cascade+难度选模双通道对照：我们按难度分流模型档位，他们把非推理小决策整类移出 LLM（更激进的省 token）| 方向验证（范式观察升级，A 专项远期备注）| 2026-10-04
- **landing-ai/ade-cli**（2,417★，pushed 08-19）新入库 | LandingAI 官方 Agentic Document Extraction CLI：文档解析→schema 化数据抽取 | doc 域读取侧工序（我们 doc=生成侧）；依赖 LandingAI 商业服务不接 | 参考（doc 域生态）| 2026-10-04
- **skalesapp/skales**（1,931★，09-30 活跃）新入库 | 跨平台个人 agent：桌面+浏览器自动化+定时任务+多端 teams+voice+本地 LLM+SKILL.md，BYOK 无 Docker | 同形态竞品 +1（happier/OpenDots 常驻同事族）；机制无未覆盖项 | 雷达（同形态）| 2026-10-04
- **LLMQuant/quant-mind**（3,042★，pushed 08-15 停更 1.5 月）新入库 | 量化金融域 agent-native 知识抽取检索框架 | 域外（无对应预置类型）+停更 | 参考（域外）| 2026-10-04
- **theredsix/cerebellum**（865★，06-01 后停更 4 月）新入库 | AI 规划驱动浏览器自动化 | 浏览器域老标（BrowserSkill/oya-browser/invisible_playwright_mcp 已录同域）| 雷达 | 2026-10-04
- **批5 skill 包三连**：ferdinandobons/startup-skill 1,159★（创业验证/竞品情报/规划）+ unifapi-agent/agents 583★（营销 agents：SEO/GEO/KOL 定价/社媒监听，只读公开数据 MCP）+ skyf0xx/gambit 23★（决策/策略/风险/谈判，10-04 当日活跃）| 垂直域 skill 包，三问（重合度/可直读性/用户会搜吗）均不过——与 notfair-plugin/Platform-skills 同结论零接入 | 雷达（B 专项）| 2026-10-04
- **微型速记**：Gauntlet-HQ/prod-evals-cookbook 59★（生产评测教程，评测域）/ oxylabs/ai-scraper-py 1,092★（商业抓取客户端，域外合规边界不变）/ bcefghj/competitive-intelligence-multi-agent 48★（中文竞品情报，微型）/ nykooi1/vibe-wise 753★（AI 写码时教你学，教学域）/ mingbo-yang/Agent_SOW 5★（Skill Graph 检索+轨迹反馈原型）/ npm repoloom 2.4.1（分析项目装合适 skills，B 专项三问不过）/ @open-slide/cli 2.0.0（演示文稿工作台脚手架——演示文稿域生态例证 +1，空档维持交人拍板）| 均雷达 | 2026-10-04

### 复查记录（repos 端点 19 仓 + 新锐轮 + npm，12 时）

- 头部：orca **84,528★（10-04 push 当日活跃，vs 07 时 84,405 +123 续领跑）**/superpowers 294,965（09-27 后无 push）/anthropics/skills **179,546（+18）**/open-code-review 43,554（10-01）/codegraph 73,138（10-03）。
- E 域：**DeepSeek-Reasonix 35,741★（10-04 push，E 候选首位维持）**；本机九候选全未装零接入维持。
- 写作域：oh-story 7,236（10-03）/drama-skills **2,478（+5）**/yomiyasu **1,337★（vs 07 时 1,305 +32 在动——翻译腔项队列第 9 标的活跃维持）**/claude-mem 95,666（10-04）/ragflow 91,640（10-04）。
- 待深挖在动：hippo-memory 770★（**10-04 04:41 push 当日活跃**，第 7 项「标记无用」维持）/wenzi-xhs 149★（第 6 项维持）/claude-rules 192★（pushed 停 03-19 停更注记维持，第 10 项）。
- 新锐轮（created:>09-28）：**CopilotKit/OpenDots 1,977→2,638★（+661 跃升）**/**answer-me-with-html 130→483★（+353 跃升）**/feder-cr/dots 2,576（+85）/easyread 681（+26）全已录；新面孔仅 strands-decider（已录上）与 deepseek-harness-linux 59★（**dsh 生态第 12 例**，Linux 打包）| 2026-10-04
- 禅道周边：zen/zenflow/zenith 关键词全为无关噪音，**零新禅道 AI 竞品（第 8 例后持续为零）**；npm 两查已录族为主（agent-orchestrator-mcp-server/nax/agentcraft/oceanus），无接入级标的。
- pypi：200/3038B 拦截页（与历班第五种记录同效）——本机通道不可用维持，npm 通道正常。
- 批5 主体（dify 157.8k/langchain 147.4k/awesome-llm-apps 140.6k/ragflow 91.6k/khoj 37.6k/gpt-researcher 29.9k/dexter 27.6k/DeepResearch 20.0k/dzhng-deep-research 19.8k/invisible_playwright_mcp 31.8k/BrowserSkill 8.1k/obscura 28.3k/firecrawl 188.3k/Agent-Reach 90.0k/career-ops 73.4k/LibreChat 45.2k/tidb 40.6k/composio 30.4k）**全部已录零增量**——检索/知识/浏览器域头部格局稳定。

### 待深挖队列（2026-10-04 12 时快照，与 07 时版一致零变化）

1. 工具输出统一压缩管线（第 7 验证在手，管线级攒批）
2. 语义缓存（待租户/敏感边界拍板）
3. 演示文稿任务类型空档——交人拍板（不擅自扩；@open-slide/cli 生态例证 +1 不改变结论）
4. 任务宪章编译为运行时强制策略（ironcurtain，治理远期）
5. 聊天分叉（1code，管线级远期备注）
6. 小红书平台特化交付契约蒸馏（wenzi-xhs 149★ 维持，交人拍板）
7. 教训卡「标记无用」显式负反馈入口（剩 ①UI 负反馈，D 专项小件交人拍板；hippo-memory 10-04 push 当日活跃维持）
8. 翻译「译文翻译腔检查」蒸馏（yomiyasu 1,337★ 在动维持，TRANSLATION_APPENDIX +1 条，交人拍板）
9. 宪章自动起草骨架（claude-rules 192★ pushed 停 03-19，差量角度成立交人拍板）
10. 既有攒批维持：ARIS 规则裁决/multica 看板视角/凭据保险库/CONTINUUM 幂等动作账本/ASD-STE100 技术英语规范化（交人拍板）；strands-decider「机械决策不进 LLM」范式观察升级为 AWS 官方实现（A 专项远期备注）

## 2026-10-04 13 时班（新一轮计划第 2/4 步·七专项巡检+落地）

> 上接 12 时班批5。巡检 A-G 全绿（行号级实锚见 full-type-round.md 13 时班节），
> 稳定期延续。落地一件，队列第 7 项清账：

- **教训卡「标记无用」显式负反馈已落地**（hippo-memory 差量清账）| lesson_op 新增 useless op（停用+useless 计数），_karma/_surplus_decay 按失守同权计入粘滞负证据（clamp 在 hits 内），UI「没用」按钮+徽章+确认弹窗+4 EN 键，test_lesson_feedback 3/3 | 已落地 | 2026-10-04

### 待深挖队列（13 时快照——第 7 项清账移出，余同 12 时版）

1. 工具输出统一压缩管线（第 7 验证在手，管线级攒批）
2. 语义缓存（待租户/敏感边界拍板）
3. 演示文稿任务类型空档——交人拍板（@open-slide/cli 生态例证 +1 不改变结论）
4. 任务宪章编译为运行时强制策略（ironcurtain，治理远期）
5. 聊天分叉（1code，管线级远期备注）
6. 小红书平台特化交付契约蒸馏（wenzi-xhs 149★ 维持，交人拍板）
7. 宪章自动起草骨架（claude-rules 192★ pushed 停 03-19，差量角度成立交人拍板）
8. 既有攒批维持：ARIS 规则裁决/multica 看板视角/凭据保险库/CONTINUUM 幂等动作账本/ASD-STE100 技术英语规范化（交人拍板）；strands-decider 范式观察（A 专项远期备注）

> 附注：12 时班队列第 8 项「翻译腔检查蒸馏」实际已随 v0.1.80（b68a6d1 TRANSLATION_APPENDIX 自查条）落地，上表已移出；本班落地原第 7 项后全队列重排如上。

## 2026-10-04 13 时调研班（批4+批7 双批补全：治理/安全/人机协同 + 中文/网关/本地/办公——新一轮计划第 1/4 步）

> 13%7=6 批6 已由 07 时班跑过，按轮换不重复纪律顺延补全今日未跑的批4+批7 双批
>（本班后今日轮换池全收口）。主扫 --batch 4 + --batch 7 = 19 查询 90 行 82 唯一仓
> 零失败零限流（gh api 串行 4s）；雷达 C 全过（repos 9 仓+search 12 发+npm 两查+
> pypi 200/3038B 拦截页维持+trendshift 本轮被拦+WebSearch 禅道新闻面串行 1 发）。
> **批4/批7 主体与库高度重合已录（多仓有 10-03 逐 commit 复查）——稳定期第十五班确认**，
> 真新面孔 ~15 件全雷达/参考级，最大机制件 bernstein。

### 新条目（本班真新面孔，均无未覆盖机制面——bernstein 除外为借鉴方向）

- **sipyourdrink-ltd/bernstein**（1,376★，10-04 push，beta/solo 维护）新入库 | 开源 agent 治理层：policy-as-code（谁可做什么/什么需审批/什么必须记录）由运行时强制执法；**确定性调度器（无 LLM 在协调环，纯 Python，回放可复现）**；回放日志+血统脊线+可选 HMAC 审计链离线可验证；50+ CLI agent 适配器+worktree 隔离合并闸；**非代码交付物「工件契约」→签名血统回执完成（不依赖 git commit）** | 待深挖第 4 项「任务宪章编译为运行时强制策略」迄今最强实证（ironcurtain/claude-elixir-phoenix/cordum 前证之上）；工件契约回执对我方写作类交付契约（小说/报告无 commit 语义）是机制级参考；「无 LLM 协调环」与 strands-decider 汇流成范式簇（调度器级+决策模型级双实现） | 借鉴方向（治理远期+交付契约攒批，交人拍板）| 2026-10-04
- **CopilotKit/OpenBot**（5,973★，10-03 活跃）新入库 | 「每个 AI 同事独享一台电脑」：浏览器+文件+工具，每个动作模型裁决 | 同 org OpenDots（已录 2,638★）之外的独立大件；同形态同事族 +1 无未覆盖机制 | 雷达（同形态）| 2026-10-04
- **Gentleman-Programming/gentle-ai**（7,523★，10-04 push）新入库 | 配置既有 coding agent（Claude Code/Cursor/OpenCode/Codex/Pi）：个性/规则/权限统一配置层 | 我们 onboarding+模型路由自有面已覆盖主路径；配置域生态例证 | 雷达 | 2026-10-04
- **beizhu-1209/AIHelms**（1,034★，09-26）新入库 | 企业级 AI 资源纳管平台（中文）：统一网关+Token 调度+MCP/Skill 集中注册分发+内外双轨定价+成本归因 | 网关域中文企业向（coai/one-api 族旁）；我们网关路线维持不接 | 参考（网关族）| 2026-10-04
- **halofyai/halofy**（336★，09-22）新入库 | 组织级 agent 接入与治理层：身份/策略/溯源/审计 | 治理远期同域小标（bernstein/cordum 簇）| 雷达 | 2026-10-04
- **zjp1997720/zhijian-skills**（778★，09-30）新入库 | Zhijian AI 公共 Agent Skills 的规范化治理源 | skill 治理域微型；我们装前扫描+指纹已满配 | 雷达 | 2026-10-04
- **批4 HITL 微型五件**：batrapulkit/squidbrake 11★（每工具调用按规则检查+危险即停人审+记录）/ imanhavangi/NoAssume 7★（「别在假设上写码」常驻澄清护栏——**需求拷问/clarify 卡同路人**）/ everafterlabs/jes 10★（Jev 驱动护栏）/ GanyuanRan/Autoloom 74★（Aegis 治理内建执行+证据交付）/ actava-ai/chi-bench 66★（长程策略基准）| 均雷达 | 2026-10-04
- **批4 资料四件**（prompt 注入攻防清单族）：tldrsec/prompt-injection-defenses 736★/yunwei37/prompt-hacker-collections 367★/forcesunseen/llm-hackers-handbook 200★/lasso-security/claude-hooks 267★（Claude Code 注入防护 hooks 集成）| 清单域雷达，三问不过 | 雷达 | 2026-10-04
- **批7/写作微型速记**：JPeetz/Hermes-Studio 365★（hermes-agent Web 看板，A13 面板族 +1）/ Matthew-Selvam/Open-Dispatch 15★（一 API 发 10 社交平台，A12 域）/ AFK-surf/Comma 163★（sessionless 个人 agent，Muse/Dots 替代族）/ Martoto/marginlight 2★（写作所有权守护）/ xavierxeno/NovelDNA 1★（本地 RAG 文风/世界观检索——圣经同域微型）/ npm bizar·coleo·crow-central-agency·pandash-cli（编排/面板微件）| 均雷达，三问不过 | 2026-10-04
- **归属正名（search 实证）**：microsoft/agent-governance-toolkit 6,389★/**NVIDIA**/SkillSpector **19,300★（+39 续放量）**/alibaba/open-code-review 43,556★；zenstory-ai/oh-story-claudecode 7,236★/nanaism/yomiyasu 1,338★/**wenziai**/wenzi-xhs-agent-skills 149★——六仓 owner 首次留全路径 | 勘误补全 | 2026-10-04

### 复查记录（批4/批7 主体重合已录 + repos 端点，13:02-13:1x）

- **批4 主体已录零增量**（多仓有 10-03 逐 commit 复查与「不接入」结论在档）：cordum-io/cordum 510★（动作防火墙，10-03 push）/FailproofAI 5,246★/microsoft/agent-governance-toolkit 6,389★/Aegis 479★/OpenAgentsControl 4,887★/tradememory-protocol 1,423★/bytebase 14,536★/BAML 9,379★/plano 7,079★/archestra/mcp-context-forge/edict 16,963★/danghuangshang/auto-browser/Atmosphere/memoryops-ai/boundary-bench/Caliper/agent-apprenticeship 1,618★——全部在档。
- **批7 主体已录零增量**：funNLP 83.6k（2024-05 停更）/JeecgBoot 48.1k/LangBot 18.0k/agenticSeek 27.4k/unsloth 77.2k/anything-llm 66.7k/Yuxi 7,266/khoj 37.6k/iflytek astron-agent 8,872+astron-rpa 5,249（前周期入库 9,022/5,551，星数差为时点波动非回撤）/future-agi 2,108——全部在档。
- 头部全活跃零 archived：orca **84,533★（10-04 05:06 push，vs 12时 +5 续领跑）**/deepseek-harness 242,992（+116）/anthropics/skills 179,549（+3）/oh-my-claudecode 39,568/ECC 272,367/hermes-agent 251,015。
- E 域：**DeepSeek-Reasonix 35,741★（10-04 push）候选首位维持**；本班 which 实测五候选（reasonix/fuxi/gitlawb/zero/empryo）PATH 全空——零接入防死链维持。
- 写作域：oh-story 7,236（10-03 活跃）/**nanaism/yomiyasu 1,338★（created 09-30 三日破千快升，10-03 push 在动——翻译腔自查条已随 v0.1.80 落地，标的持续演进）**/AI-Novel-Writer **1,287★（10-04 push）**；新锐轮（created:>09-28）写作域全微型——**写作域无新竞品第六班连续确认**。
- 待深挖在动：**kitfunso/hippo-memory 770★（10-04 05:02 push 当日活跃）**——README「Mark a memory wrong and it stops coming back」与我方本日已落地 lesson_op useless 负反馈同构收敛互证（第 7 项并行落地班已清账）/claude-rules 192★（停更注记维持）/wenzi-xhs 149★。
- 新锐轮 agent 域：dots 2,576/open-dot 528/answer-me-with-html 500（10-04 push）/strands-decider 300/agentcraft 222 全已录；新面孔仅 Comma 163★（已录上）。
- 禅道周边：search 返回全为大盘无关仓（openclaw/superpowers/n8n/firecrawl 等）+ WebSearch 新闻面（禅道 2026 强化效能度量中心=官方产品演进；无官方 agent 集成发布）——**零新禅道 AI 竞品（第 8 例后持续为零）**。
- 通道：npm 两查已录族为主；pypi 200/3038B 拦截页维持；trendshift.io 本轮被域名校验拦（02 时班曾可达，环境波动如实记）。

### 七专项快照与本班结论

- A：批4/批7 扫描面零新 token 机制；「机械决策不进 LLM」范式簇升格（bernstein 调度器级+strands-decider 决策模型级）随第 10 项攒批 | 已覆盖
- B：六源在位（同日多班实证）；本班新见候选过三问均不过零接入 | 已覆盖
- C：17 类型矩阵同日四向核对一致沿用；gentle-ai 配置域生态例证不改结论 | 巡检
- D：教训 67 条同日多班零漂移；hippo-memory 同构互证；本班零新方法论需入库 | 数据卫生
- E：catalog 14 条目；五候选 PATH 全空零接入防死链；Reasonix 候选首位维持 | 巡检
- F：claims 零积压沿用；search+新闻面双通道零新竞品 | 巡检
- G：同日多班过时文案 grep 零命中沿用 | 巡检

### 待深挖队列（13 时调研班快照——第 4 项强化，余与并行落地班 13 时版一致）

1. 工具输出统一压缩管线（第 7 验证在手，管线级攒批）
2. 语义缓存（待租户/敏感边界拍板）
3. 演示文稿任务类型空档——交人拍板
4. 任务宪章编译为运行时强制策略（**bernstein 1,376★ 本班 +1 且为迄今最强实证**：policy-as-code+确定性调度+HMAC 审计链；cordum/claude-elixir-phoenix 前证；治理远期交人拍板）
5. 聊天分叉（1code，管线级远期备注）
6. 小红书平台特化交付契约蒸馏（wenzi-xhs 149★ 维持，交人拍板）
7. 宪章自动起草骨架（claude-rules 192★ 停更注记维持，交人拍板）
8. 既有攒批维持：ARIS 规则裁决/multica 看板视角/凭据保险库/CONTINUUM 幂等动作账本/ASD-STE100 技术英语规范化（交人拍板）；**+bernstein「非代码交付物签名血统回执」（写作类交付契约机制级参考）+「机械决策不进 LLM」范式簇（A 专项远期备注）**

## 2026-10-04 14 时巡检落地班（新一轮计划第 2/4 步·七专项 A-G 独立复核+两件落地）

> 与并行 13 时落地班（教训负反馈，队列第 7 项）同窗不同件；本班不触碰其在制文件
> （skills.py/app.js/i18n.js），落地走 pipeline.py+tests/。七项巡检独立复核全绿，
> 详见 2026-10-04.md 本班节（A 专项单列小节）。落地两件：

- **连载起草重试「分层降级」修复（A 专项机制缺陷）** | 此前 bible 组块时折进 sk_block，重试时整块被当「经验库」传入 _shrink_context_block 且 bible 参数重复计体量——设计的「模块库边界截断/圣经二级标题边界截断」两层因 replace 落空永不生效，超预算只剩整块→4K 硬截（圣经常被拦腰截断，与保整段承诺不符）。修复：三块分开留底+新增 _serial_shrunk_block 按各自身份收缩后整块字节级对位替换，知识库块永不动；test_serial_ctx_shrink 4 项逐层锁定 | 已落地 | 2026-10-04
- **test_serial_polish_named 旧契约对齐（main 既有红清账一件）** | _archived_preface 随 c7f973b 混合态实案改返回 (文本, 覆盖章号) 二元组，旧用例仍按「见任一章在场即整体放弃」旧契约断言→HEAD 稳定红（stash 对照实证）；实现/调用方/新测试均为新契约，属测试过时非实现错，按「测试错了修测试」对齐，13/13 绿 | 已落地 | 2026-10-04

### 教训与方法论（本班入库）

- **「折块」会吞掉分层降级**：多来源上下文按优先级分层收缩时，组块环节若先把各层折成一整块，收缩层就只剩最粗一层——分层契约必须把「组块」与「收缩」分开留底各自身份（本次实锤：bible 折进 sk_block 后两层降级静默失效数个版本，测试只锁了函数本身、没锁调用方传参身份）。后续接「工具输出统一压缩管线」（队列第 1 项）时同款风险前置检查 | 方法论 | 2026-10-04

### 待深挖队列（14 时快照）

同 13 时调研班版零变化（本班两件落地不产生新队列项；第 2 项语义缓存、第 8 项凭据保险库维持交人拍板）。

## 2026-10-04 15 时班（批1：代码质量与评审·新一轮计划第 1/4 步全类型调研）

> 15 时 %7=1 → 批1（今日凌晨班 01:27 已跑批1 全量主扫，本班按规则复跑：A 常驻 78 含内置
> B1 11 + 批1 轮换 11 + A1u/A1p2 双轮 16 = **116 查询 524 行 423 唯一仓，零失败零限流**，
> gh api 串行 4s）。雷达源 C 全过：repos 端点 22 仓 + topic 8 + 新锐轮 2（created:>09-28）
> + 自家 CLI 周边 5 + 禅道周边 2 + npm 两查 + pypi（Client Challenge 拦截页，第六种形态
> 如实记）+ trendshift.io 可达。**零机制级新差量（连续第十六班稳定期）**，新面孔定性入库。

### 新条目（均参考/雷达级，无未覆盖机制）

- **pingdotgg/t3code**（24,836★，10-04 活跃，Trendshift 榜）新入库 | 「agent harness 控制面」：iOS/Android/Web/Electron 四端控制本机 agents，复用 Claude Code/Codex/Cursor/Grok Build/OpenCode/Antigravity 订阅；Theorem（t3.gg）出品，明言灵感来自 Codex 桌面/Conductor/Cursor Glass | **同形态（桌面+移动多 CLI 控制面）orca/paseo 之后第三证且体量最大**；我们移动端=只读远控+423 接管，其移动端全控——形态差量交人拍板不擅自扩 | 雷达（同形态）| 2026-10-04
- **tt-a1i/archify**（76,860★，10-04 push，topic:claude-skills 首五）新入库 | 「把想法/计划/代码库变成可交互图表」agent skill（Claude Code/Codex 通用）；09-28 班 Trending 首记时无星数，本轮补格——图表生成域巨型 | 我们 tech_proposal/doc 无图表工序，domain adjacent 不构成机制差量 | 参考（巨型补格）| 2026-10-04
- **caidaoli/ccLoad**（417★，10-04 活跃）新入库 | 自托管 AI API 网关：Claude Code/Codex/Gemini 密钥池+订阅账号聚合一端点 | one-api/coai/CLIProxyAPI 族 +1；网关路线维持不接 | 参考（网关族）| 2026-10-04
- **yogirk/agent-council**（90★，04-07）新入库 | CLI agent 圆桌审议 skill：Claude Code/Codex/Gemini 多 CLI 合议工程决策 | 跨厂商评审同族方向验证 +1（我们双/三评审并行已有）；「圆桌合议」与「并行独立评审」是两种评审拓扑，远期备注 | 方向验证 | 2026-10-04
- **域外/大盘一句**：IvanMurzak/Unity-MCP 4,386★（Unity 引擎 AI Skills/MCP/CLI，游戏域）/Pan-Chera/Multi-Agent-CAD 1,017★（text-to-CAD 多智能体）/whirlchat/whirl 355★（AI chat app：记忆+living documents，chatbot 域）/neilsonnn/image-blaster 1.1k（Trendshift，图生 3D skillset）/LAMDA-NeSy/Research-Starter-Kit 352★（科研教程）| 均无未覆盖机制 | 参考（域外）| 2026-10-04
- **微型速记**：zhb0119/MemMark 5★（EMNLP 2026，长期记忆归因——logs/outputs/真实状态分歧时记忆何以为真，outcome 加权同域学术参照）/Eversmile12/sharedcontext 50★（跨客户端记忆 MCP）/jushayden/claude-code-memory-cache 6★（5 层记忆）/sneg55/agent-starter 76★（agent 友好项目引导包）/kcosr/assistant 89★+cesarandreslopez/sidekick-agent-hub 85★+UnmanagedCode/code-conductor 3★（面板族 +3）/ythx-101/live-panel-skill 183★（配置驱动架构图 skill）/GalegO/SDD-Interview 4★（需求拷问族 +1）/mvillere/clean-writing-system 2★（drop-in 写作规则=宪章族 +1）/Tuulikk/GnawTreeWriter 0★（AST 树形编辑，Empryo 同向）/atfa/duo 1★（双 agent 平权协作）/githubnext/gh-aw-wizard 6★（GitHub Agentic Workflows 向导）/Roarpeng/agent-os 0★（个人 Agent OS 中文，ZCode×Cursor）/pavan53732/Mayasaba 0★（Windows-only 本地软工平台）/ylxmf2005/swcc 65★（民主集中制编排隐喻）/AgriciDaniel/claude-music 65★（音乐 skill）/zmy15/DeepSeek-for-VisualStudio 106★/so898/XcodePaI 98★ | 均雷达 | 2026-10-04

### 复查记录（repos 端点 22 仓 + 主扫双轮，15:36-16:1x）

- 头部全活跃：orca **84,581★（10-04 push 当日活跃，vs 12 时 +53 续领跑）**/superpowers 295,008（09-27 后无 push）/ECC 272,453（10-02）/hermes-agent 251,033（10-04）/deepseek-harness 243,065（10-03）/opencode 211,666（10-04）/anthropics/skills 179,562（10-03）/claude-code 149,299/codex 127,785（10-04）/pi 112,263（10-04）/ponytail 153,843（10-03）
- E 域：**esengine/DeepSeek-Reasonix 35,738★（10-04 push）候选首位维持**；本机 command -v 实测在装 13（codex/claude/opencode/qwen/aider/kimi/mimo/grok/pi/dsh/gemini/codebuddy/trae-cli），openclaw 在册未装；九候选（reasonix/fuxi/gitlawb/zero/empryo/zcode/goose/crush）PATH 全空——零接入防死链维持；ZCode 7,384（09-29 后无 push 缓涨）
- 写作域：oh-story 7,242（10-03）/drama-skills 2,479（10-03）/yomiyasu **1,347★（10-04 当日活跃，vs 13 时 +9——翻译腔条已随 v0.1.80 落地，标的持续演进）**/AI-Novel-Writer 1,288（10-04 push）/hippo-memory 770（10-04，第 7 项已清账）/wenzi-xhs 149 维持/claude-rules 192（03-19 停更注记维持）；**新锐轮（created:>09-28）写作域全 0-2★（marginlight 2/novel-agent-pipeline 0/NovelForge 0）——写作域无新竞品第七班连续确认**
- 批1 域：open-code-review 43,589★/SkillSpector 19,316（10-02）/mira 356/pr-af 645/costrict 4,444（09-30）均已录零增量；B1 新锐全学生级
- spec/计划域：spec-kit **140,041★**（10-03）/OpenSpec 71,004（10-02）/planning-with-files 27,277（10-01）/agentmemory 29,125（10-04）全活跃
- 生态跃升：career-ops 73,096→**73,420（10-04 push）**/agency-agents-zh→**21,050★**（09-29，中文专家角色库放量）/freellmapi 30,291→**30,453（10-04 push）**/nexu-io/open-design 97.5k→**99,345（10-04，+1.8k）**/lidge-jun/opencodex 16,883（10-04 缓涨）/cc-switch 139,852（10-04）/pacifio/atlas 8,949（10-04 爬升维持）/claude-mem 95,742（10-04）/Understand-Anything 85,211
- awesome 八清单全活跃：awesome-claude-code 55,035（10-04）/VoltAgent 35,181（10-02）/awesome-claude-skills 76,443/awesome-llm-apps 140,663/awesome-mcp-servers 95,806/harness-engineering 4,692（10-03）/**Agent-Memory 657（10-04 当日活跃）**/Long-Horizon 1,059（09-22 后 12+ 天停更观察维持）；skill 生态大盘 scientific-agent-skills 47,522/agentic-awesome-skills 47,230（10-04）
- 通道：topic 8 页首五位全已录（唯一新面孔 archify 已录上）；trendshift.io 本轮可达（上 13 时班被拦系波动）；npm 两查已录族零新；pypi 搜索页 Client Challenge 拦截（历班拦截页之外新形态，结论同：通道不可用如实记）
- F 专项雷达：禅道周边 search 首页被大盘噪音占据、bug-triage 仅 clickup-ai-bug-triage 0★——**零新禅道 AI 竞品（第 8 例后持续为零）**；zentao-cli 61★（10-01）维持

### 待深挖队列（15 时快照，与 13/14 时版一致零变化）

1. 工具输出统一压缩管线（管线级攒批）
2. 语义缓存（待租户/敏感边界拍板）
3. 演示文稿任务类型空档——交人拍板
4. 任务宪章编译为运行时强制策略（bernstein 最强实证，治理远期）
5. 聊天分叉（1code，管线级远期备注）
6. 小红书平台特化交付契约蒸馏（wenzi-xhs 两小件，交人拍板）
7. 宪章自动起草骨架（claude-rules，停更注记维持，交人拍板）
8. 既有攒批维持：ARIS 规则裁决/multica 看板视角/凭据保险库/CONTINUUM 幂等账本/ASD-STE100（交人拍板）；bernstein 签名血统回执+「机械决策不进 LLM」范式簇（A 专项远期备注）；本班新增远期备注：agent-council「圆桌合议」评审拓扑/t3code 移动全控形态（均交人拍板）

## 2026-10-04 19 时班（批5 复跑：检索/知识/浏览器——新一轮计划第 1/4 步全类型调研）

> 19 时 %7=5 → 批5（12 时班已首跑批5，本班按小时规则复跑取增量）。A 常驻 78 组
> 3.5 小时前 15 时班全量覆盖（116 查询 524 行），按「重合跳余页省配额」纪律不重复
> 主扫。本班 = --batch 5 轮换 10 组（**50 行 46 唯一仓零失败零限流**，gh api 串行 4s）
> + 雷达源 C 全过（repos 36 仓零 archived + topic 8 + 新锐轮 2 + CLI 周边 5 + 禅道周边
> 3 + npm 两查 + trendshift 可达；pypi 历班六种拦截形态在案未复试省配额）。
> 检索时间 19:05-19:40。**零机制级新差量（连续第十七班稳定期）**，新面孔定性入库。

- **garrytan/gstack**（134,981★，MIT，TS，created 03-11，10-04 push）新入库 | 「Use Garry Tan's exact Claude Code setup」：23 个 opinionated 工具扮演 CEO/Designer/Eng Manager/Release Manager/Doc Engineer/QA 六角色 | 名人技能包奇观档（superpowers/ponytail 同族星数引擎）；机制面=角色化 skill 包，agency-agents（276 专家角色，已录）同构无差量 | 参考（大盘补格/雷达）| 2026-10-04
- **cloudflare/security-audit-skill**（23,993★，JS，09-14 push）新入库 | Cloudflare 官方编码 agent 安全审计 skill：多阶段安全审计+**独立验证、机器可读 findings** | 「findings 机器可读+独立验证」与我们评审 JSON 结构化输出+证据锚+事件流鉴别同向；大厂进场 agent 安全评审域 | 参考（安全评审域）| 2026-10-04
- **katanaml/sparrow**（5,228★，GPL-3.0，09-14 push）新入库 | ML/LLM/Vision-LLM 结构化数据抽取+指令调用+agentic 工作流 | doc 域读取侧生态（landing-ai/ade-cli 同域）；我们 doc=生成侧，边界不变 | 参考（doc 域生态）| 2026-10-04
- **liaohch3/claude-tap**（3,259★，MIT，Python，09-22 push 停 12 天观察）新入库 | 本地拦截并查看 Claude Code/Codex/Gemini/Cursor/OpenCode/Kimi(Kimi Code)/Pi/Hermes 八家 CLI 的 API 流量 trace viewer | A6 观测域工具面：我们用量台账/审计日志是产品内记账，它是外挂流量级透视（「CLI 实际发了什么」独立验证视角），无编排层差量 | 雷达（A6 观测域）| 2026-10-04
- **qxcnm/Codex-Manager**（2,999★，Rust，中文，10-03 活跃）新入库 | Codex CLI 账号管理切换工具+本地网关转发 | 账号/凭据族 +1（cockpit-tools/cc-switch/CLIProxyAPI 族） | 雷达（凭据族）| 2026-10-04
- **monid-ai/monid**（1,696★，TS，09-30 活跃）新入库 | 「OpenRouter for agent tools」——agent 工具的聚合路由 | 网关族（工具面）+1，无机制差量 | 雷达（网关族）| 2026-10-04
- **Edge0-AI/Edge0**（2,843★，10-02 push，无描述）新入库 | Trendshift 榜提及但 README 无描述 | 无描述不评估 | 雷达（观察）| 2026-10-04
- **Sahil-SS9/hermaguard**（33★，MIT，09-30）新入库 | 「对抗式找 bug 评审」：3 个并行子代理从不同角度攻击代码+合并器分诊 findings，只读不修 | 「多评审并行+合并分诊」与我们双/三评审同构的独立实现（微标） | 方向验证（评审域）| 2026-10-04
- **netresearch/jira-skill**（86★，10-02 活跃）新入库 | Jira 的 AI agent 插件（CLI 工具面：issue/worklog/sprint，Server/DC+Cloud）| 禅道同域异构（F 专项雷达，通道级无差量） | 参考（F 域）| 2026-10-04
- **AutoGPT**（Significant-Gravitas，187,646★，10-04 活跃）大盘补格 | 经典自主 agent 框架元老 | 此前从未入册的老将（多轮搜索高频出现在的都是大盘噪音位）；框架路线定位不同，一句入册 | 参考（大盘补格）| 2026-10-04

### 复查记录（repos 端点 36 仓 + topic 8 + 新锐轮，19:10-19:40）

- 头部全活跃零 archived：orca **84,659★**（vs 15 时班 84,581 +78 续领跑）/superpowers 295,078（09-27 后无 push）/ECC 272,554/anthropics/skills **179,584（+22）**/spec-kit 140,063/OpenSpec 71,016/planning-with-files 27,278/agentmemory 29,128（10-04）/paseo **19,410（+89 vs 10-03）**
- E 域：**DeepSeek-Reasonix 35,735★（10-04 push）候选首位维持**；十候选本机 command -v 全空零接入防死链维持；kimi-cli 11,435 archived/kimi-code 7,769（10-02）继任维持
- 写作域：oh-story 7,247（10-03）/**yomiyasu 1,353★（10-04 当日活跃，翻译腔自查条已落地后标的持续演进）**/hippo-memory 770（10-04 当日 push，负反馈与我方 lesson_op useless 同构收敛互证）/claude-rules 192（03-19 停更注记维持）/wenzi-xhs 150；新锐轮 writing 域已录族为主——**写作域无新竞品第八班连续确认**；iCode 正名 openJiuwen-ai/iCode 298★（10-04 活跃，10-03 上午班域外速记补全路径）
- 治理/评审域：SkillSpector **19,336★（10-04 当日，+36 续放量）**/open-code-review 43,611（10-01）/stop-that-shit **2,475（+15）**/governance-toolkit 6,390（10-03）/**bernstein 1,379（10-04 push，13 时班新条目在动 +3）**
- 检索/知识域（批5 主体全已录）：claude-mem **95,831（10-04，+165 vs 12 时）**/ragflow **91,657（+17）**/career-ops **73,435（10-04 push，+15）**/obscura **28,326（10-04）**/invisible_playwright_mcp 31,769/BrowserSkill 8,113/Agent-Reach 90,258/composio 30,432/LibreChat 45,248/tidb 40,623/khoj 37,559（08-02 停更观察维持）
- 星数跃升：**answer-me-with-html 483→724（+241 当日跃升）**/archify 76,921（+61）/nanobot 48,778（+278 vs 09-25）/oh-my-opencode-slim 9,257（+357 vs 09-18）/tuios 4,646（10-04，+34）/CPA-Manager-Plus 3,734（10-04）/openclaw **391,276（10-04，+43）**/hermes-agent 251,078/deepseek-harness 243,151/ponytail 154,138/cc-switch 139,899（10-04）/anthropics/claude-code 149,345——均 topic 页与 repos 端点双通道互证
- awesome 三清单：awesome-claude-code **55,045（10-04，+10）**/VoltAgent 35,185/awesome-mcp-servers 95,811 全活跃
- topic 8 页首五位全已录（头部化延续）；禅道周边 search 零通道级新竞品（第 8 例后持续为零）；npm 两查已录族零新；trendshift 可达，新面孔 3 件已查录（见上）
- 状态变更：无新增 archived；claude-tap push 停 09-22（新入库即停更 12 天观察注记）

### 七专项快照（本班实证）

- **A**：同日 04/09 时班行号级实锚沿用（预算熔断/diff-only 评审/cascade/_shrink/召回衰减全在位）；批5 扫描面零新 token 机制 | 已覆盖
- **B**：六源在位（同日 02 时班探活六绿沿用）；本班新见候选（gstack/monid/sparrow/claude-tap 等）过三问均不过零接入维持 | 已覆盖
- **C**：17 类型 flows.py 实数复核一致（BUILTIN_FLOWS list 实数 17，import 实测，与当日凌晨/02/04/07/09 时班四向一致）；演示文稿空档维持交人拍板 | 巡检
- **D**：教训 **70 条**实证（data/skills.json lessons=70=67+本日蒸馏 3 条，v0.1.80 已入库确认）；零新增同族可并 | 数据卫生
- **E**：catalog 14 条目；十候选（reasonix/fuxi/gitlawb/zero/empryo/zcode/goose/crush/command-code/openclaw）command -v 全空——零接入防死链维持 | 巡检
- **F**：data/zentao.json 实读 claims=0 零积压、last_error 空、poll 未启（用户侧预期非故障）；禅道周边零通道级新竞品（jira-skill 为异构域一句入库）；zentao-cli 61★（10-04 活跃） | 巡检
- **G**：过时文案活码 grep（13种/15种/已接11/双源）零命中——唯一命中全在 .mimosa hook-state 基线快照（非活码，与凌晨班判定一致）；新毛病无 | 巡检

### 待深挖队列（19 时快照，与 15 时版一致零变化）

1. 工具输出统一压缩管线（管线级攒批）
2. 语义缓存（待租户/敏感边界拍板）
3. 演示文稿任务类型空档——交人拍板
4. 任务宪章编译为运行时强制策略（bernstein 最强实证，治理远期）
5. 聊天分叉（1code，管线级远期备注）
6. 小红书平台特化交付契约蒸馏（wenzi-xhs 两小件，交人拍板）
7. 宪章自动起草骨架（claude-rules，停更注记维持，交人拍板）
8. 既有攒批维持：ARIS 规则裁决/multica 看板视角/凭据保险库/CONTINUUM 幂等账本/ASD-STE100（交人拍板）；bernstein 签名血统回执+「机械决策不进 LLM」范式簇+agent-council 圆桌合议/t3code 移动全控（远期备注）；**新增远期备注：cloudflare/security-audit-skill「机器可读 findings+独立验证」与 gstack「名人角色技能包」生态信号（参考级不入队列）**

---

## 2026-10-04 20 时独立复核班（第 1/4 步交接复核——WebSearch 跨通道捞出写作域两件大仓，「八班无新竞品」结论证伪）

> 开工 19:51、分支 main（b4dd562）。19 时班（前班实例）已在本小时完成批5 主扫+雷达 C，
> 按「同小时不重复主扫+上一班记录不直接采信」交接纪律，本班=独立真实性复核+跨通道补扫。
> **复核全中**：本地锚点 5/5（flows.py 17 类型 import 实数/data skills.json lessons=70/
> catalog.py 14 条目/zentao claims=0·last_error 空·poll 未启/五 CLI 候选 command -v 全空）；
> repos 端点 7/7 命中（Reasonix 35,735 精确/gstack 134,988/security-audit-skill 24,000/
> yomiyasu 1,355/anthropics-skills 179,586/orca 84,674，较 19 时班 +2~+15 自然漂移零
> archived；orca 正主路径勘定 stablyai/orca）。**19 时班调研记录真实性成立，本班背书。**
> **跨通道增量（本班核心产出）**：WebSearch 串行交叉验证「写作域无新竞品」结论，捞出
> gh api 通道连续八班未达的写作域两件大仓+五件微型——**「无新竞品第八班」结论证伪**，
> 更正为「gh api 单通道八班未达」。检索时间 19:51-20:0x，全部经 gh api 二次实证
> （星数/语言/push/描述逐项核对）非转抄搜索摘要。

- **lingfengQAQ/webnovel-writer**（7,310★，GPL-3.0，Python，10-04 当日 push，v6.2.1 Claude Code 插件，200 万字量级）新入库 | 长篇网文一致性系统：Story System 事件主链（动笔前合同→accepted CHAPTER_COMMIT 事实入账→.story-system 唯一事实源）→派生只读视图（state.json/index.db/summaries/memory_scratchpad/vectors.db）+ projection_log.jsonl 投影同步审计 + Embedding/Rerank RAG 检索设定/时间线/伏笔 + 伏笔登记/推进/回收全生命周期 + 追读力度量入评审 + /webnovel-learn 项目写法记忆 + doctor/preflight 体检 + 只读 Dashboard（实体图谱/追读数据）；v8 转 DeepSeek Harness 插件形态 | 与我方 serial_novel 主体同构互证：资源账本（道具/伤情/承诺/伏笔每章评审自动追加）≈其伏笔台账、/learn≈教训库、圣经≈设定集；**三真差量：向量 RAG 检索（500+ 章块注入 vs 检索召回）/追读力度量（我方仅章节钩子定性）/派生视图同步审计（我方无派生视图层）** | 待深挖（**写作域新头名 7,310★ 超 oh-story 7,247**）| 2026-10-04
- **voocel/ainovel-cli**（2,092★，Go，09-29 push）新入库 | 全自动长篇小说引擎，核心设计「事实层确定、语义层自主」：确定性 Engine 按事实决策表调度 Architect/Writer/Editor 三自主代理（主循环零 LLM 开销、行为可穷举测试）+ Arbiter 语义裁定函数按需唤醒且每次落盘可回放 + step 级断点恢复（plan/draft/check/commit）+ **卷弧双层滚动规划**（初始只规划前 2 卷弧骨架+第 1 弧详章，写作推进到再展开，远期规划不空洞）+ **四维相关章节推荐**（伏笔/角色出场/状态变化/关系）+ 自适应上下文（按总章数切全量/滑窗/分层摘要）+ 七维评审每项必须引原文举证 + 用户实时注入干预自动评估影响面重写受影响章节 + Saga+checkpoint 章节提交幂等重放 | 「机械决策不进 LLM」范式簇第 3 实现（strands-decider 决策级/bernstein 调度级/本仓写作域全栈级）且直接对标 serial_novel；两差量：我方章纲一次锁定 vs 卷弧滚动展开、我方前情块注入 vs 四维相关章节检索 | 待深挖 | 2026-10-04
- **danjdewhurst/story-skills**（264★，JS，10-04 push）新入库 | 端到端小说写作 Agent Skills（markdown 故事圣经项目格式），Codex+Claude Code 双宿主打包 | 圣经同域 skill 包形态，无编排层差量 | 雷达（写作域）| 2026-10-04
- **yingpengma/Awesome-Story-Generation**（662★，Python，09-28 push）新入库 | LLM 故事生成论文清单 + 4,000+ 中文网文基准（8 维给「大纲→正文」输出打分并与人类作品对比） | 我方签约质量门禁/全局评审为规则型自评，缺外部基准锚点；学术评测域参考 | 参考（评测域）| 2026-10-04
- **KeithCu/writeragent**（122★，Python，10-04 push）新入库 | LibreOffice 扩展：agentic AI+NumPy 协作写作 | doc 域编辑器侧生态，无差量 | 雷达（doc 域）| 2026-10-04
- **guerra2fernando/libriscribe**（119★，Python，09-30 push）新入库 | 多 agent 书籍创作系统（概念→成稿全流程） | novel 域微型无新机制 | 雷达（novel 域）| 2026-10-04
- **lujih/webnovel-writer-opencode**（212★，10-03 push）+ **TianHengZhuang/Chinese-WebNovel-Master**（10★，story-bible 工具 Agent Skill）并一句 | 前者=webnovel-writer 的 OpenCode 跨宿主改编版（生态跟随信号），后者=中文网文生产流微型（planner/writer/editor+平台/类型知识库+留存/story-bible） | 均雷达级零差量 | 雷达（写作域生态）| 2026-10-04

### 复查背书与流程教训

- 19 时班 10 条新条目抽 6 实证全中（gstack/security-audit-skill/yomiyasu/Reasonix/orca/anthropics-skills 星数漂移方向一致、零 archived）；其批5 主扫与雷达 C 记录采信。
- **流程教训（已蒸馏入 keywords.md 规则）**：gh api 通道连续八班报「写作域无新竞品」后，WebSearch 串行交叉验证仍捞出 7.3k★+2.1k★ 两件大仓——单通道「无新竞品」结论必须周期性跨通道交叉验证，防单通道盲区。
- 检索纪律执行：gh api 认证通道全程串行（repos 端点 2s/search 4s），零 403 零限流；WebSearch 串行单会话完成未触 429。

### 待深挖队列（20 时增量——写作/连载域四差量实证入队，原 8 项维持 19 时版）

1. **webnovel-writer RAG 向量检索差量**：serial_novel 起草上下文从「圣经+前情块注入」扩展「embedding 检索相关历史章节/设定」（500+ 章场景体量差），管线级攒批
2. **追读力度量**：评审维度从定性钩子扩展量化追读指标（webnovel-writer 有 Dashboard 数据形态），交人拍板
3. **卷弧双层滚动规划**：serial 章纲一次锁定 vs ainovel-cli「前 2 弧骨架+推进展开」远期不空洞策略，交人拍板
4. **四维相关章节推荐**：起草前按伏笔/出场/状态/关系检索相关历史章节注入（ainovel-cli 形态），与第 1 项同管线攒批

## 2026-10-04 21 时巡检落地班（新一轮计划第 2/4 步·七专项 A-G 实锚复核+一件落地）

> 与并行班（flows.py/store.py/test_branching.py/test_serial_variants_flow.py 赛马
> variants/branches 继承修复）同窗不同件，本班落地走 pipeline.py+tests/。七项巡检
> 独立实锚全绿，详见 2026-10-04.md 本班节（A 专项单列小节）。落地一件：

- **赛马复赛分层降级（A 专项同族缺口收口）** | 3d0e013 修复了非赛马起草重试的分层降级，但同章赛马复赛（race_round≥1）仍照发同一份全量提示词——容量受限通道（2026-09-17 讯飞 35B 实测形态：长提示词挂起/秒拒）首轮 N 路全挂后，复赛只是把 N×2400s 再烧一遍。修复：复赛按 _serial_shrunk_block 同口径条件降级（len>12000 且整块在位才收缩；首轮仍发全量，与非赛马 draft_attempt「先给全量一次机会」语义一致），大纲/前情/知识库块永不动 | 已落地 | 2026-10-04

### 教训与方法论（本班入库）

- **降级语义要跟着重试语义走**：给「长提示词挂起」做分层降级时，降级触发点必须覆盖每一条会重发全量提示词的路径——重试路径修了、赛马复赛路径漏了，等于只堵了半条街（本次实测：同族缺口与 3d0e013 相隔一个版本被发现）。后续接「工具输出统一压缩管线」（队列第 1 项）时，先枚举「哪些路径会重发同一份大上下文」再定降级点 | 方法论 | 2026-10-04

### 待深挖队列（21 时快照）

同 20 时版零变化（本班落地不产生新队列项）；第 1/4 项管线级攒批，第 2/3 项交人拍板。

## 2026-10-04 22 时巡检落地班（新一轮计划第 2/4 步·七专项 A-G 独立复核+一件落地）

> 与并行班（flows.py/store.py/branching 测试 variants/branches 继承收口）及 21 时班同窗，
> 本班落地走 pipeline.py+tests/test_serial_ctx_shrink.py。七项巡检独立实锚全绿，
> 详见 2026-10-04.md 本班节（A 专项单列小节）。落地一件：

- **降级说明进 step note（A 专项可观测性）** | 3d0e013（非赛马重试）与赛马复赛（21 时班）两件降级落地后，降级与否、降了哪几层在运行页完全不可见（step note 只有「网关限流退避/赛马变体」），排障靠猜。修复：_serial_shrunk_block 改返回 (替换块, 降级说明)（_shrink_context_block 的 note 原本弃置，改透出），两调用点把「经验库→4K；模块库→边界截断…」接进 step note（非赛马重试前缀「；降级：」、赛马复赛「；复赛降级：」仅复赛在跑才标），零行为语义变化只留痕 | 已落地 | 2026-10-04

**本班新录待拍板两件**：①全局一致性评审无容量降级（pipeline.py 圣经原样注入无封顶 + full_text[:60000] 头截，容量受限通道上 N 评审并发+评审补位各自重烧同份全量，全挂整 run 判失败——评审覆盖面属质量语义，不随起草降级同口径擅动）；②serial.branches 无 UI 编辑入口（后端 _norm_serial/continue_task 已支持，前端流程编辑器 fl-variants 旁缺 branches 输入，待并行班收口后补，避免同窗撞编辑器区块）。

### 教训与方法论（本班入库）

- **降级必须留痕**：静默降级让运行记录与实际下发内容脱钩——「提示词为什么变小/这章质量为什么波动」全靠猜。凡自动降级（上下文收缩/模型降档/预算拦截），把「降了什么」写进人可读的运行记录（step note/事件流）；降级功能落地时留痕与降级本体同批交付，别隔半个版本补 | 方法论 | 2026-10-04

### 待深挖队列（22 时快照）

同 21 时版零变化。

## 2026-10-04 22 时后落地班+21 时收口班（新一轮计划第 3/4 步落地一件 + 第 4/4 步五道关发版 v0.1.82）

> 22 时后班补齐 branches UI 入口（任务表单 f-branches+流程编辑器 fl-branches+i18n EN
> 两键，与 variants 逐处同款），验收 test_borrow_round 6/6；收口班五道关+发版全实录见
> 2026-10-04.md 本班节。此处录两条可复用沉淀：

- **能力「后端已有、前端没门」等于没有** | branches 多线推演后端全链（钳位/推演/续写沿用）早已在位，但 UI 无输入口+任务级 serial 整体替换会冲掉流程默认——功能对界面用户不可达，直到巡检专项 C 才实录缺口。新后端能力落地时同步检查「三个入口」：API/任务表单/流程编辑器，缺任一门就记跟进项，别等下轮巡检撞见 | 方法论 | 2026-10-04
- **发版闸的「exit 0」不可作为全量绿灯信号** | release_gate run_tests 以退出码判全量，而 32 位 discover 会被 selfupdate relaunch 线程 os._exit(0) 无声杀死——假绿灯过闸（v0.1.81 PASS 疑似即此形态）。判绿必须认「Ran N tests + OK/FAILED」统计行；gate 修法（校验统计行或用守卫驱动器）已录交人拍板 | 教训 | 2026-10-04

### 复查记录（收口班）

- 本轮落地件回归：test_serial_ctx_shrink 4/serial_variants_flow 4/borrow_round 6/race_ctx_shrink 1/branching（含新继承用例）/selfupdate 6/6 全绿（s-z+a-r 分片统计行为证）。
- 全量 2142 项分片对账：25 项红/错全落既有台账（11 文件），零新增；较上一收口班 +12 项全为本轮新测试。
- 待深挖队列：与 22 时版零变化；v0.1.82 已发布（registry shasum a0de5baf 逐字一致+包内 import 冒烟过）。

### 独立复核班补录（第二收口通道交叉验证，2026-10-04 22 时后）

- **争用嫌疑豁免不能跨班沿用，必须按当前 HEAD 重验** | 收口班沿 14 时班口径把 test_flows 记入「争用嫌疑组（单独跑全绿）」，独立复核班实测单独净进程跑 1F + 干净 HEAD（ae68961）worktree 同样 1F——系存量确定性红（首章 600 字开篇闸 vs mock 稿 367 字，publishable 断言滞后，与 publish_auto/workdsh 同类），非争用。HEAD 在动，历史班的「单独跑全绿」证据随新提交过期；凡引用旧豁免先在当前 HEAD 复跑一遍再落账 | 教训 | 2026-10-04
- **独立复核全对账零新增**：a-f 723（1F+1E）+g-m 376（4F+2E）+n-r 542（10F）+s-z 501（2F）=2142 项，与收口班 a-r 1641+s-z 501 总数与红名单逐项吻合；朴素分片 a-r 片再证 32 位静默退出（无统计行 exit 0）。三提交（90a7bd6/34460ed/ae68961）show --stat 零外来零踩踏；443 抖动两连击下以 origin/main 跟踪引用+npm registry 0.1.82 实查双证推送发版落地 | 复核 | 2026-10-04
- 待深挖队列 +1：test_flows.TestSerialResume 存量断言滞后修复（断言侧或 mock 稿加长，红名单归类改「存量红」）；余同收口班快照零变化。

## 2026-10-05 04 时班（批4：治理/安全/人机协同——新一轮计划第 1/4 步全类型调研+存量红清账）

> 4%7=4 批4。主扫 115 查询 523 行 467 唯一仓零失败零限流；雷达 C 全过（repos 55 仓+
> npm 两查+WebSearch 串行交叉验证 1 发——距 10-04 20 时班 4 班按新规则配一次）。
> 跨通道实证三件：covenant 实仓仅 5★ 微型 / NVIDIA open-agent-safety-platform 仓 404
> （新闻真、正主仓名未勘定）/ WSO2 Agent Manager 仓名未勘定——**WebSearch claim 一律
> repos 端点二次实证首例落地，规则补丁已落 keywords.md**。零机制级新差量（稳定期延续）。

### 新条目（本班真新面孔，均雷达/参考级）

- **archestra-ai/archestra**（4,343★，10-04 活跃）新入库 | 企业 AI 平台：guardrails+MCP registry+gateway+orchestration 全栈 | 治理簇（bernstein/cordum 簇）+网关族双域叠加；无未覆盖机制面 | 雷达 | 2026-10-05
- **Justin0504/Aegis**（484★，09-06）新入库 | 运行时策略强制+加密审计链+human-in-the-loop | bernstein 同族小标（批4 治理域）；同上零新机制 | 雷达 | 2026-10-05
- **darrenhinde/OpenAgentsControl**（4,887★，09-13）新入库 | plan-first 工作流+approval gates 框架 | 我方计划闸+待裁决已覆盖主路径 | 雷达 | 2026-10-05
- **agentrq/agentrq**（1,137★，10-04 活跃）新入库 | HITL 实时会话任务管理（human-in-loop conversational） | 采访卡/签核已有对应物 | 雷达 | 2026-10-05
- **spec-kitty/spec-kitty**（1,662★，10-04 活跃）新入库 | Spec-Driven Development + 组织级治理 | spec-kit/OpenSpec 同族 +1 | 雷达 | 2026-10-05
- **asalsali/covenant-framework-community**（5★）新入库 | 多 agent 治理框架（WebSearch 捞出，repos 实证微型） | 量级不足，WebSearch 放大效应例证 | 雷达 | 2026-10-05
- **TokenRhythm/opensquilla**（7,082★，10-04 活跃）新入库 | 「Token-Efficient AI Agent——same budget, higher intelligence」token 效率型 agent | 我方 token 面已满配（压缩/熔断/cascade/_shrink 八件）；无具体新机制披露 | 雷达（A3 远期备注随队列第 2 项攒批） | 2026-10-05
- **vllm-project/semantic-router**（6,030★，10-04 活跃）新入库 | Mixture-of-Models 可编程语义路由（按语义选模型） | 与 strands-decider「机械决策不进 LLM」范式同向；cascade+难度选模已满配 | 参考（A 专项远期备注） | 2026-10-05
- **PenglongHuang/chinese-novelist-skill**（3,286★，09-06）新入库 | 中文长篇 skill 包：三层问答·创作记忆·悬念钩子·自动校验 | 与我 novel/serial 同域；采访/记忆/钩子/校验四机制均有对应物（clarify/resume_ctx/评审维度/quality gates） | 已覆盖 | 2026-10-05
- **notnotype/neuro-book**（719★，09-30 活跃）新入库 | 长篇小说写作 IDE（软件工程方法做 fiction） | 同域形态参考；无机制披露细节 | 雷达 | 2026-10-05
- **FireRedTeam/FireRed-OpenStoryline**（3,458★，07-31）新入库 | 小说→视频编辑 agent（A12 改编域） | 平台化路线参考 | 参考 | 2026-10-05
- **dream-num/univer**（22,356★，10-04 活跃）新入库 | 「The Office Harness for AI Agents」表格/文档/幻灯/画布 | 办公域大件；**演示文稿空档例证 +1**（交人拍板维持） | 参考 | 2026-10-05
- **allweonedev/presentation-ai**（3,037★，06-05 停更）+ **tonyqinatcmu/SlideBot-AI**（1,215★，01-31 停更）新入库 | 开源 AI 演示文稿生成两件 | C 专项演示文稿空档例证 +2（空档拍板维持） | 参考 | 2026-10-05
- **BlockRunAI/ClawRouter**（6,614★）+ **looplj/axonhub**（5,334★，10-04 活跃）+ **caidaoli/ccLoad**（418★，10-04 活跃）新入库 | 网关族三件（agent-native 路由/100+ LLM gateway/CC 池化网关） | 网关路线不接维持（coai/one-api 族旁） | 雷达（网关族） | 2026-10-05
- **NanmiCoder/cc-haha**（14,862★，10-04 活跃）+ **Alishahryar1/free-claude-code**（56,650★，10-04 活跃）+ **SethGammon/Citadel**（922★，10-01 活跃）+ **Agent-Field/SWE-AF**（1,027★）新入库 | 同形态四件：CC 桌面工作台/多 harness 订阅聚合/CC+Codex 操作层/SE 舰队 | 蜂巢+多 CLI 面已覆盖主路径；无未覆盖机制 | 雷达（同形态） | 2026-10-05
- **mukul975/Anthropic-Cybersecurity-Skills**（33,772★，08-31 后停更）新入库 | 817 个安全 skill 结构化包（6 框架映射） | skill 市场域大件；我方装前扫描+白名单已满配，不接 | 雷达（B 专项生态例证） | 2026-10-05

### 复查增量（repos 端点）

orca 84,897（+223 续领跑）/ gstack 135,116（+128 放量）/ SkillSpector 19,375（+75）/ anthropics/skills 179,637 / DeepSeek-Reasonix 35,741（10-04 push 候选首位维持）/ webnovel-writer 7,316（+6）/ ainovel-cli 2,094（10-04 push 活跃）/ drama-skills 2,499（+26）/ yomiyasu 1,376（+21 在动）/ hippo-memory 770（当日 push）/ wenzi-xhs 153 / 火宝短剧 15,670（+300）/ claude-rules 192（停更注记维持）。全部零 archived。

### 存量红清账（本班落地 6 件）

usage_stats tie-break（2/2）/ test_flows 阈值对齐（9/9）/ test_quality_gate 旧布局 import（11/11）/ test_bookmeta_chain discover 兼容（双通道绿）/ test_publish_auto 建书对账断言对齐 07d066b+9cd11d1+新增未确认守卫用例（22/22，原 9F 清账）/ test_workdsh_boundaries 闭包补账场景改写（10/10，原 1F 清账）——**main 既有红台账 -2F -2E -9F -1F**，零新红。

## 2026-10-05 05 时观察验收班（并发写手窗口期验收 + conversation_workspace 旧布局 import 清账）

> 进入时六件在制品已由 04 时班验收回填（报告+knowledge 本班节齐备），本班先跑触及面独立复核
> **7 组 71 项全绿**（usage 15/usage_stats 2/flows 9/quality_gate 11/bookmeta 1 双通道/publish_auto
> 22/workdsh 10）。回填途中发现收口班在场（0291707 已提交、package.json bump 0.1.83 发版中）——
> 按 7dbc3ce「拦并发写手在场窗口期」纪律**立即停写转观察**，全程零踩踏。

### 收口班成果验收（本班独立核对）

- **提交清单**：0291707 十文件（六件清账+四件沉淀）/ 07572a8 三文件（CHANGELOG/README/package.json）——零外来文件、零踩踏；commit message 与实际 diff 逐项吻合。
- **推送**：main==origin/main（push 已落地）；**npm registry 实查 0.1.83**（publish 慢在途 ~7min 后核对落地，v0.1.82→0.1.83）。

### 独立分片复跑对账（120/260 件粒度）

- 前 120 件（a~c 段）**零新增红**：唯一 bad = `test_conversation_workspace` **2E**——`import main` 裸 import 旧布局残留（仓库根无 main.py，pre-app/ 时代正身），与 test_quality_gate 同族同因；即收口班 a-r 片「10F+2E」之 2E 实锚（收口班未点名，本班查明归档）。
- `test_portscan` 单件 300s 挂死中止循环（32 位长跑静默退出在案现象的变体复现）——分片粒度跑法需配单件超时。

### 本班落地件：conversation_workspace 旧布局 import 清账（1E）

`sys.path.insert(app/)` + 注释对齐 test_encoding_gbk/test_state_payload 既有惯例——**3/3 绿**，触及面 26 项全绿（本件 3+state_payload 14+encoding_gbk 9）。零实现改动。

### 发版判定

本班零实现改动（纯测试+docs），不进 CHANGELOG「用户可感知变更」口径——**v0.1.83 保持 latest 不再发版**（同日 v0.1.81/82/83 三连发已有先例，但「仅当天有代码入库才发」的必要条件不满足）。

## 2026-10-05 06 时班（批6：框架/平台/SDK 生态——全类型调研+存量红净态对账）

> 6%7=6 批6。主扫 115 查询 523 行 467 唯一仓零失败零限流；雷达 C：repos 25 仓+npm 两查
> （pypi 省配额在案）；WebSearch 免配（距 04 时班 2 班未到 3-4 班窗口+本班非「无新竞品」判定）。
> 批6 已录为主零增量（harness-sdk 7,551→8,656 放量参考级）；零机制级新差量（第十九班）。

### 新条目（本班真新面孔，均雷达/参考级）

- **thesysdev/openui**（9,980★，10-04 活跃）新入库 | 生成式 UI 开放标准 | UI 生成非编排台任务类型域 | 雷达 | 2026-10-05
- **FlashML-org/FreeToken**（14,169★，10-04 活跃）新入库 | 桌面级数据中心规模模型 serving | 本地部署路线，网关族旁 | 雷达 | 2026-10-05
- **muratcankoylan/Agent-Skills-for-Context-Engineering**（18,074★，10-01）新入库 | 上下文工程 skill 集合 | skill 生态例证（B 专项装前扫描已满配不接） | 雷达（B 专项） | 2026-10-05
- **zjunlp/LightMem**（1,183★，ICLR 2026）新入库 | 轻量高效记忆增强生成 | 记忆域学术新件；经验召回已满配 | 雷达 | 2026-10-05
- **kentcdodds/kody**（732★，10-04 活跃）新入库 | agent 之家：memory+keys+code+automations | 凭据保险库攒批域佐证 +1 | 雷达 | 2026-10-05
- **iLearn-Lab/NovelClaw**（379★，05-31）新入库 | 动态记忆优先长篇协作框架 | webnovel-writer/ainovel-cli 同域小标；向量检索攒批域例证 | 雷达 | 2026-10-05
- **BlinkDL/AI-Writer**（3,910★，2025-05 停更）新入库 | RWKV 中文网文生成模型 | 模型非编排台竞品 | 不适用 | 2026-10-05

### 简称→正主全名对照表（本班勘定沉淀——后续 repos 复查免再勘定，省配额）

历史报告/knowledge 记录惯用简称，repos 端点复查必须全名。本班 4 次搜索勘定 11 件：
webnovel-writer→lingfengQAQ/webnovel-writer · ainovel-cli→voocel/ainovel-cli ·
drama-skills→zenstory-ai/drama-skills · yomiyasu→nanaism/yomiyasu ·
hippo-memory→kitfunso/hippo-memory · wenzi-xhs→wenziai/wenzi-xhs-agent-skills ·
claude-rules→lifedever/claude-rules · DeepSeek-Reasonix→esengine/DeepSeek-Reasonix ·
SkillSpector→NVIDIA/SkillSpector · gstack→garrytan/gstack ·
huobao-drama（火宝短剧）→chatfire-AI/huobao-drama | 方法论 | 2026-10-05

15 时班补勘 3 件：gascity→gastownhall/gascity · beads→gastownhall/beads ·
crg→n24q02m/crg | 方法论 | 2026-10-05

### 复查增量（repos 端点，07:0x）

orca 84,946（+49 续领跑）/ gstack 135,147（+31）/ anthropics/skills 179,649（+12）/ SkillSpector 19,381（+6）/ DeepSeek-Reasonix 35,740（10-04 push 候选首位维持）/ ainovel-cli 2,095（+1）/ yomiyasu 1,381（+5 在动）/ webnovel-writer 7,316 / drama-skills 2,499 / hippo-memory 770 / wenzi-xhs 153 / claude-rules 192（停更维持）/ archestra 4,343 / opensquilla 7,082 / cc-haha 14,862 / huobao-drama 15,670——全部零 archived，零增量面孔。

### 存量红净态对账（当前 HEAD 99df8b4 净进程复跑）

14 时班既有台账 11 文件中 **10 文件当前 HEAD 全部转绿**（publish_auto 22/http_500_guard/mimo_injector/qwen_injector_guard/git_workbench/mgmt_guards/launch/deepseek_harness 21）——「争用嫌疑豁免不能跨班沿用，必须按当前 HEAD 重验」教训执行完毕，台账清空；仅 portscan 真实杀进程用例挂死维持（第三班复现，修复方向在案交人拍板）。跑法勘误：净进程复跑一律 cd tests（`from base import` 型测试仓库根必 ModuleNotFoundError，台账名 test_deepseek 实为 test_deepseek_harness）。

### 教训与方法论（本班入库）

- **后台跑长任务先验证输出通道实时性**：Python stderr/stdout 重定向到文件时默认块缓冲，PROGRESS 进度全程 0 行——排查只能 wc 输出行数倒推。长跑脚本 reconfigure 一律补 `line_buffering=True`（本班已修 borrow_scan_nightly.py），新脚本写时带上次教训 | 方法论 | 2026-10-05

### 待深挖队列（06 时快照）

同 04 时班版零变化；LightMem/NovelClaw 记忆域备注随向量检索攒批第 1/4 项。

## 2026-10-05 07 时班（批7：中文/网关/本地/办公——全类型调研+越范围检测落地）

> 7%7=0 批7。主扫 114 查询 513 行 451 唯一仓零失败零限流；雷达 C：repos 存量 10 仓+npm 一查
> （pypi 历班拦截在案省配额）。WebSearch 交叉验证执行（距 04 时班第 3 班到窗口）：
> 捞出 coze-studio/eino/Vision-Agents 三名——repos 端点二次实证全部真仓在库
> （「新闻面 ≠ 开源仓在」规则第 2 例，本例为正向实证：仓名勘定 stream→GetStream、eino 正主
> cloudwego/eino）。B7 域零机制级新差量（第二十班）。

### 新条目（本班真新面孔，均雷达/参考级）

- **coze-dev/coze-studio**（21,674★，07-29 push）新入库 | 字节扣子开源版：可视化 agent 开发平台（低代码编排+工作流） | 可视化低代码路线与 CLI 舰队编排不同轨；国内生态位最大竞品之一持续跟踪 | 参考 | 2026-10-05
- **cloudwego/eino**（13,239★，09-29 push）新入库 | 字节 Go 语言 LLM 应用开发框架（Coze 生态，原子组件+编排） | 框架域（批6 族旁）异栈参考 | 参考 | 2026-10-05
- **GetStream/Vision-Agents**（8,150★，10-04 push）新入库 | 语音/视觉实时 agent 框架 | 我们无音视频任务类型；实锚勘定（新闻名 stream→GetStream） | 雷达 | 2026-10-05
- **TransformerOptimus/SuperAGI**（17,698★，2025-01 停更）新入库 | 曾一线自治 agent 框架 | 停更 8 个月+大仓——「停更大仓」形态例证（claude-rules 同类） | 不适用（停更） | 2026-10-05
- **coleam00/context-engineering-intro**（13,888★）新入库 | 上下文工程课程/方法论（A2 域） | 教育内容非竞品；方法论与已有 compaction/§07 同向 | 参考 | 2026-10-05
- **Integuru-AI/Integuru**（4,773★，06-24）新入库 | 逆向工程自动建集成的 agent | 集成域参考（我们集成面=CLI+MCP，不追） | 参考 | 2026-10-05
- **szczyglis-dev/py-gpt**（1,973★，10-03 push）新入库 | 桌面 AI 助手（GPT-6/Gemini/Claude 多模型） | 本地助手客户端非编排台；B7 本地域旁证 | 雷达 | 2026-10-05
- **rhysd/go-github-selfupdate**（646★）新入库 | Go CLI 自更新库（A9 自更新域） | 异栈参考：我们的 selfupdate 已有 relaunch 守卫+registry 校验 | 参考 | 2026-10-05
- **vijaythecoder/awesome-claude-agents**（4,389★，2025-10）新入库 | Claude 子代理编排 awesome 清单 | 雷达源补充候选（已录 awesome 清单第 14 个） | 雷达源补充 | 2026-10-05
- **yuruotong1/autoMate**（3,965★，09-18 push）/ **magnitudedev/browser-agent**（4,134★）新入库 | CUA/浏览器操控族 | 同 trycua/cua 结论：我们无 GUI 操控能力 | 不适用（暂） | 2026-10-05

### 存量复查（repos 端点，07:5x，正主全名对照表直用——零搜索摩擦，配额 10 查）

orca 84,966（+20 续领跑）/ gstack 135,153（+6）/ anthropics/skills 179,649 持平 / SkillSpector 19,384（+3）/ DeepSeek-Reasonix 35,739（10-04 push 候选首位维持）/ webnovel-writer 7,316 持平 / ainovel-cli 2,095 持平 / yomiyasu 1,383（+2 在动）/ drama-skills 2,499 持平 / obra/superpowers 295,279 头部在录——**全部 alive 零 archived**。npm 一查：agent-orchestrator-mcp-server/@nathapp/nax/agentcraft/opencode-oceanus 均已录族或微型，零接入级。

### 落地件（1 件——越范围编辑提醒，roadmap 在册待落地件清账）

**越范围编辑提醒**（agent-delegate 借鉴，2026-09-22 入库「借鉴方向（越范围检测，代码任务）」）：pipeline.py 新增 _plan_scope_files（计划全步骤涉及文件并集）+ _scope_note（声明清单 vs collect_changes 实际变更文件，超出即点名提醒评审员核对；文件/目录型声明「相等或位于其下」匹配；反斜杠与 ./ 前缀归一）；_run_review 挂第 5 参 scope_files（None 时零额外 git 开销，test_review_fallback 的 _git_diff mock 契约不动）。同 _review_depth_note 形态：纯提示词指引，不改 pass 判定语义。计划未锚定 files（旧计划/手动/快路径）静默跳过。tests/test_review_depth.py +7 用例（11/11 绿）。

### 队列快照勘误（G 专项发现——快照滞后于代码）

待深挖队列第 7 项（教训卡显式负反馈）已落地 **3d0e013**（useless op+粘滞负证据+UI「没用」按钮）；第 8 项（weekly_report git log 素材通道）已落地 **31f0e93**（_gitlog_brief :953）；test-defect-retrospective 复盘报告已落地为预置类型 defect_retro（BUILTIN_FLOWS 17 之一）。三件本班实证后从队列划掉——后续班引用队列快照须先对代码实证再引用（本条即方法论）。

### 待深挖队列（07 时快照）

原 1-6/9-11 项维持（第 7/8 项已落地划掉，见上）；第 9 项翻译腔检查维持交人拍板。本班新面孔均雷达/参考级不产生新队列项。

## 2026-10-05 10 时班（批3：计划/spec/长任务——全类型调研+七专项巡检，报告见 2026-10-05-full-types.md）

> 10%7=3 批3。主扫 116 查询 523 行 465 唯一仓零失败零限流；雷达 C：repos 30 仓+topic 8+
> npm 两查+awesome 8 清单+trendshift 可达（pypi 省配额在案）。**零机制级新差量（连续第
> 二十一班稳定期）**。搜索引擎双通道被拦（DDG 连接失败/Bing robots 拒）——WebSearch 交叉
> 验证欠账顺延（队列第 10 项），本轮以 trendshift+topic+npm 三通道补位。

- **GetBusbar/busbar**（171★，10-05 push）| AI agent 执行控制平面：管模型请求/MCP 工具调用/A2A 委托 | 治理域（bernstein/cordum 簇）+1；待裁决+审批+审计台账已覆盖主路径，量级微型 | 雷达 | 2026-10-05
- **Niko1221/Strata**（227★）| 本地 LLM 推理引擎（Qwen MoE 一键装） | 本地域（FreeToken 同域）非编排竞品 | 雷达 | 2026-10-05

### 复查记录（repos 端点 30 仓，10:1x-10:4x，全名对照表直用）

- 头部全活跃：orca **85,035★（10-05 push，vs 06 时 +89 续领跑）**/superpowers 295,304（09-27 后无 push）/**mattpocock/skills 276,232（vs 10-03 +1,137 放量快，挑战 superpowers 双巨头格局）**/ECC 273,006/hermes-agent 251,248（10-05）/deepseek-harness 243,420/opencode 211,770/anthropics/skills 179,657/ponytail 154,932/claude-code 149,429/codex 127,858/pi 112,457/gstack 135,174/openclaw 391,328/ruflo 73,879/dify 157,852。
- 批3 域全活跃机制零差量：spec-kit 140,128/OpenSpec **71,054（10-05 push）**/planning-with-files 27,282/agentmemory 29,137/worktrunk 8,806/lazycodex 3,729/spec-kitty 1,662（10-05 push）/gsd-pi 1,291/jean 1,308/itsaplan 884——spec 三件套/worktree/Stop gate/活计划满配维持。
- 写作域：webnovel-writer 7,317（新头名三差量维持在队）/oh-story 7,256（+20）/yomiyasu **1,400（+17 在动，翻译腔自查条已落地标的持续演进）**/ainovel-cli 2,095/drama-skills 2,499/webnovel-writer-opencode 212（生态跟随 +9）。
- E 域：**DeepSeek-Reasonix 35,742★（10-05 当日 push，候选首位维持）**；kimi-code 7,771；九候选防死链维持。
- 治理/记忆域：bernstein **1,397（+21 在动）**/governance-toolkit 6,392/SkillSpector 19,391/open-code-review 43,723/hippo-memory 770/claude-rules 192（停更注记）/wenzi-xhs 153。
- 状态变更：**harbor 迁 harbor-framework org（5,825★，10-05 push）——归属更新**；claude-mem 96,158（日 +300+ 放量）；awesome 全活跃（Long-Horizon 1,060 停更观察维持）；禅道周边**零新竞品（第 8 例后持续为零）**；npm 面零接入级。

### 七专项快照（本地锚点本班实数）

- A：扫描面零新机制，管线级锚点同日多班实证沿用 | 已覆盖
- B：六源在位；Busbar/Strata/Kane 三问均不过零接入 | 已覆盖
- C：flows.py BUILTIN_FLOWS **17 类型 import 实数**，用户 14 场景全映射+doc/resume/bid_doc；流程参数锚点 pipeline.py:2371 TRANSLATION_APPENDIX/:952 _gitlog_brief/:5099 _read_constitution 实证在位；演示文稿空档维持 | 巡检
- D：教训 **70 条**（lesson 66+procedure 4；流程规范 25/节奏爽点 21/情节逻辑 10/人物塑造 7/一致性 4/文笔风格 3，偏科 36%——蒸馏 3 条入库后新常态非漂移） | 数据卫生
- E：catalog.py:32 DEFAULT_CATALOG 14 条目；防死链口径沿用 07 时班 | 巡检
- F：claims=0 零积压、last_error 空、poll 未配置（用户侧预期）；链路锚点沿用 | 巡检
- G：过时文案活码 grep 零命中口径沿用；新毛病无 | 巡检

### 待深挖队列（2026-10-05 10 时快照）

1. 工具输出统一压缩管线（第 7 验证在手，管线级攒批）
2. 语义缓存（待租户/敏感边界拍板）
3. 演示文稿任务类型空档——交人拍板
4. 任务宪章编译为运行时强制策略（bernstein 在动，治理远期）
5. 聊天分叉（1code archived，远期备注）
6. 小红书平台特化交付契约蒸馏（wenzi-xhs，交人拍板）
7. webnovel-writer 三差量（向量 RAG 攒批/追读力度量/卷弧滚动规划，交人拍板）
8. 宪章自动起草骨架（claude-rules 停更注记，交人拍板）
9. 既有攒批维持：ARIS 规则裁决/multica 看板视角/凭据保险库/CONTINUUM 幂等账本/ASD-STE100（交人拍板）；bernstein 签名血统回执+「机械决策不进 LLM」范式簇（远期备注）
10. **WebSearch 交叉验证欠账**（本班搜索引擎双通道被拦顺延，下轮优先补）

> 口径勘误：07 时班队列第 9 项「翻译腔检查维持交人拍板」系快照滞后——实际已随 v0.1.80（b68a6d1）落地，本班以 pipeline.py:2371 代码实证移出。

## 2026-10-05 13 时班（批6 复跑+WebSearch 交叉验证补账+第 3/4 步落地件收口，报告见 2026-10-05-full-types.md）

> 13%7=6 批6。主扫 115 查询 524 行 469 唯一仓零失败零限流，批6 已录为主零增量
> （第 22 班稳定期）。**WebSearch 交叉验证补账（队列第 10 项清账）**：捞出
> **context-mode 25.4k★（A3 域大仓 HN #1）**——gh api 通道连续多班「零差量」后
> 第 3 例实证跨通道价值。工作区 presentation+去数字化两落地件实跑 13 用例全绿收口。

### 新条目（本班真新面孔）

- **mksglu/context-mode**（25,423★，10-04 push，HN #1 570+ 分）新入库 | A3 token 节约域 MCP 服务器：①工具输出前置沙箱（落 SQLite/FTS5 不进上下文，315KB→5.4KB=98% 缩减）②会话事件级追踪（编辑/git/任务/错误/决策入 SQLite，压缩后 FTS5+BM25 按需检索）③Think in Code（LLM 写脚本只回传结果，47×Read=700KB→1 调用=3.6KB）④显式不做输出风格强制（Moonshot 案：激进简洁提示降基准） | 三段压缩是后处理、它是前置沙箱；差量=事件级索引+检索；④印证我们不做输出压缩的设计 | **借鉴方向（并入待深挖第 1 项，第 8 验证）** | 2026-10-05
- **josstei/maestro-orchestrate**（463★，08-07）新入库 | 多 CLI 编排平台 39 专家（Gemini/Claude/Codex/Qwen）+Express 快路径+4 阶段工作流 | A1 域微型 | 雷达 | 2026-10-05
- **HKUDS/OpenHarness**（15,913★，06-04）新入库 | Open Agent Harness+内置个人 agent Ohmo | harness 域簇旁，机制面零新差量 | 参考 | 2026-10-05
- **autonomous-ai/openharness**（1,105★，10-05 push）新入库 | 多机 harness 聚合「All your agents. All your machines. One」 | 与 HKUDS 同名异主（WebSearch 仓名未勘定形态实证） | 雷达 | 2026-10-05
- **bernstein 正主勘定**：sipyourdrink-ltd/bernstein（1,396★，10-04 push）——历史简称补全名入对照表 | 方法论 | 2026-10-05

### 复查增量（repos 端点 22 仓，13:5x，全 alive 零 archived）

orca **85,140（+105 续领跑）**/superpowers 295,342（09-27 后无 push）/mattpocock/skills **276,333（+101，逼近 superpowers 至 -19k）**/ECC 273,071（+65）/hermes-agent 251,268/opencode 211,783/anthropics/skills 179,675/ponytail 155,099/spec-kit 140,142/gstack 135,216（+42）；写作域 webnovel-writer 7,318/ainovel-cli 2,096（10-05 push）/**yomiyasu 1,423（+23 在动）**/drama-skills 2,505（+6）；E 域 DeepSeek-Reasonix 35,740（10-05 push 候选首位）；治理/记忆 SkillSpector 19,403（+12）/OpenSpec 71,055（10-05）/agentmemory 29,136/planning-with-files 27,285/claude-rules 192（停更注记）/wenzi-xhs 153/火宝 15,684。

### 待深挖队列（13 时快照）

第 1 项获 **context-mode 第 8 验证**+「前置沙箱+事件索引 FTS5/BM25」新角度（管线级攒批）；第 3 项演示文稿**落地划掉**（18 类型注册表）；第 10 项 WebSearch 欠账**补账划掉**（每 3-4 班继续按纪律配额）；其余维持（第 2/4/5/8 项拍板/远期，第 6/7 项交人拍板，第 9 项攒批）。

## 2026-10-05 12 时班（批5 复扫+全名对照表九仓补勘+第 4/4 步五道关收口+发版，报告见 2026-10-05-full-types.md）

> 12%7=5 批5（检索/知识/浏览器）。主扫重跑完整落盘（首轮 tail 截断如实记录后补跑）：
> **115 查询 524 行 462 唯一仓零失败零限流**；批5 域（ragflow 91,687/Agent-Reach
> 91,081/LibreChat 45,287/khoj 37,562/gpt-researcher 29,919/career-ops 73,491）
> 全部已沉淀零新竞品。**零机制级新差量（第 22 班稳定期）**。trendshift 本轮被拦
> （WebFetch 域名校验+curl 空返回），以 topic 沿用+npm 两查补位（已知族微型件零接入级）。

- **WebSearch 通道恢复实证 + WriterAI 案**：串行两查（RAG/知识库+写作域）可过；捞出唯一
  仓 claim **tinkvu/WriterAI repos 实证 5★、2024-08 停更——微型死仓不入库**（「新闻面
  ≠ 开源仓在」第 4 例）| 判据 | 2026-10-05
- **全名对照表九仓补勘**（repos 端点逐仓实证，此前知识库多用短名，后续班免再勘定）：
  ponytail=DietrichGebert/ponytail 155,083/orca=stablyai/orca 85,137（+102 续领跑）/
  agentmemory=rohitg00/agentmemory 29,136/pi=earendil-works/pi 112,486/
  OpenSpec=Fission-AI/OpenSpec 71,055/claude-mem=thedotmack/claude-mem 96,221/
  opencode=anomalyco/opencode 211,783/hermes-agent=NousResearch/hermes-agent 251,268/
  spec-kit=github/spec-kit 140,140——全 alive 零 archived | 对照表 | 2026-10-05

### 七专项快照（12 时班）

- A：锚点抽验在位（compaction.py:182/gitmod.py:304/pipeline.py:613/capability.py:100）
  沿用 10 时班行号级实读；context-mode 差量已由 13 时班入队 | 已覆盖
- B：六源缓存实数 **810 条**（catalog[].plugins 口径：zcode 26/anthropic 315/
  anthropic-skills 5/claude-skills 99/clawhub 215/cocoloop 150）；新见候选零，三问零接入 | 已覆盖
- C：BUILTIN_FLOWS **实数 18**（presentation 落地）；TYPE_DIMENSIONS 18 键
  （defect_retro 有意落 reasoning 默认，多班同口径）；硬编码「17 种」全仓零残留 | 巡检+落地
- D：skills.json 70 条 id/标题零重复；流程规范 36% 口径维持；本轮无新蒸馏 | 数据卫生
- E：which 实测 10 在装（openclaw 未装）；五候选全未装零接入防死链 | 巡检
- F：claims=0 零积压、last_error 空、poll 未配置（用户侧预期）；零新竞品 | 巡检
- G：过时活码 grep（13种/15种/已接11/17种/600+ 技能）全仓零命中——静态数字观察项
  随落地件 2 消除 | 巡检

### 收口与发版

五道关全过（main 未切分支/py_compile+node --check/unittest discover exit=0 三轮独立
实证，Windows stdio 缓冲下汇总行偶发不可见以 exit code 契约为准并如实记录/逐 hunk
自审/差异标记零命中/show --stat 核对）→ 提交 → push → **v0.1.85 发版**（test_selfupdate
6/6 → package.json patch+1 → CHANGELOG 顶部 → push → npm publish → npm view 核对）。
队列第 3/10 项划掉口径与 13 时班条目一致。

## 2026-10-05 15 时班（批1：代码质量与评审——全类型调研+gastown 系双仓破稳）

> 15%7=1 批1。主扫 **116 查询 520 行 425 唯一仓零失败零限流**（A 常驻 78+B1 轮换
> 11+A1u/A1p2 双轮 16+脚本内 B1 重份 11，批1 两轮取并集）；topic 页 8 个、npm 三查、
> awesome 清单 7 源、WebSearch 串行 1 查全过。**第 23 班打破稳定期**：A1u 新锐轮捞出
> gastownhall/gascity，顺组织矩阵掘出 gastownhall/beads 27.6k★——两仓均零历史收录。

### 新条目（本班真新面孔）

- **gastownhall/gascity**（1,328★，10-05 push，Go，created 2026-02-22）新入库 | 「编排构建器 SDK」——Gas Town 基建抽出的可组合编排层：city.toml 声明式拓扑 + 多 runtime provider（tmux/subprocess/exec/ACP/K8s/herdr）+ **controller/supervisor 期望态→运行态调和循环（K8s 式 reconcile+health patrol 健康巡逻）** + formulas/molecules/waits/mail（配方/工单/等待/信箱）+ rig 多项目作用域 | formulas≈BUILTIN_FLOWS 已覆盖、mail≈编排器交接已覆盖；**差量=常驻调和巡逻**——我们监督是流程内逐步校验（计划锚定/预算熔断），无常驻观察器 | **待深挖（队列新第 11 项，远期——取决于产品是否走向常驻舰队形态）** | 2026-10-05
- **gastownhall/beads**（27,638★，10-05 push，Go，created 2025-10-12，1338 open issues 高强度在用）新入库 | Dolt 分布式图工单=「coding agent 记忆升级」：markdown 计划→依赖感知图；bd create→**ready（依赖就绪才可领）**→claim→close（**blocker 自动释放**下游）；dolt push/pull 多机多 agent 同步；bd setup claude/codex/cursor 装 AGENTS.md+hooks | flow 内步骤依赖编排已覆盖；**差量=跨会话持久工单图+依赖就绪领取协议+多机分布式同步**（planning-with-files/agentmemory 文件系的 DB 化变体） | **待深挖（与 gascity 同簇并队）** | 2026-10-05
- **n24q02m/crg**（68★，10-05 push，Python，created 2026-03-20）新入库 | 「token 高效评审的知识图谱」：语义检索+调用图解析，评审只拉相关切片不灌全文件；CLI-first（crg 命令）、MCP 次级面、daemon/http relay | 微型但机制同名——与队列第 1 项（工具输出统一压缩管线）同范式「结构化索引换上下文」 | 雷达（并入第 1 项攒批证据，第 9 验证） | 2026-10-05
- **拒收明细**（如实记录）: Morpheus 38★（三层记忆小说系统，03-22 停更半年）/bony-agent 16★（内容生产分发，10-05 push 但微型）/graph-engineering 538★（九段 KG 管线，07-23 单日倾倒后零维护）/claude-skills-marketplace 680★（07-25 停更，marketplace 族已录 anthropics/VoltAgent 大盘）/social-agent-ai 8★+ai-social-agent 3★+Abilityai/ruby 4★（同形态微型）/genpark skill 族 9★（微型技能包）——均不过「他们有+我们没有+运转良好」门槛 | 判据 | 2026-10-05

### 复查增量（repos+扫描端点，15:1x-16:1x，全 alive 零 archived）

orca **85,239（+99 续领跑）**/superpowers 295,381（09-27 后无 push）/mattpocock/skills **276,422（+89）**/ECC 273,154（+83）/hermes-agent 251,285/opencode 211,791/ponytail 155,276（+177）/anthropics-skills 179,695/spec-kit 140,156/OpenSpec **71,060（10-05 push）**/agentmemory **29,137（10-05 push）**/planning-with-files 27,285/claude-mem **96,283（10-05 push）**/SkillSpector **19,411（10-05 push）**/open-code-review **43,772（10-05 push）**/oh-my-openagent 69,806（+43）；写作域 webnovel-writer 7,320/ainovel-cli 2,096（10-05 push）/**yomiyasu 1,438（+15 在动）**/drama-skills 2,510/oh-story 7,259/hippo-memory 772；E 域 DeepSeek-Reasonix 35,737（10-05 push 候选首位）；gastown 系 beads 27,638/gascity 1,328/beads-packs 系全 10-05 push。雷达源 7 清单全 alive：VoltAgent 35,222（10-05 push）/awesome-mcp-servers 95,838/harness-engineering 4,707（10-04）/Agent-Memory 658（10-04）。

### 通道与方法论（本班入库）

- topic 8 页零新面孔（已知族）；npm 三查零接入级（agent-orchestrator-mcp-server/nax 均已录）；WebSearch 串行 1 查（批1 评审域）仅命中已知在录者（Cline/OpenCode/Aider），纪律配额内；pypi 维持被拦（3038B 挑战页在录）。| 通道 | 2026-10-05
- C 补查四组（自家 CLI 周边+禅道周边）：claude-code-manager/codex-manager/or opencode-suite 三查全已知族（ECC/karpathy-skills/system-prompts）；**禅道周边第 9 例零新禅道 AI 竞品**（大仓泛匹配维持）；bug-triage 零星微型。捞出 B7 办公域两件未收录：**iOfficeAI/OfficeCLI**（31,575★，10-02 push，「首个 AI 专用 Office suite」）与 **genspark-ai/genoffice**（8,639★，10-05 push，Docs/Sheets/Slides/PDF 开源 AI Office）——均为 agent 文件格式工具面而非编排机制，与 presentation 类型文件生产环节同域 | 雷达（B7 办公域同族，编排差量零） | 2026-10-05
- **长尾盲区实证**：beads 27.6k★ 一年仓此前从未被任何班捞出——A2「agent+memory」stars 排序下被 mem0/letta/agentmemory 压出 per_page=5 剪切线；A1u 新锐轮靠 gascity 今日 push 带出后顺组织矩阵才掘到。**头部恒星查询剪不动中腰部巨仓，组织矩阵顺藤是零成本补法**（后续班遇新面孔先扫其 org）。| 方法论 | 2026-10-05

### 待深挖队列（15 时快照）

13 时快照活项 8 项全部维持（第 3/10 项保持划掉）；**新增第 11 项：gastown 系常驻调和巡逻+跨会话依赖工单图（gascity+beads 双仓同簇，远期——产品形态走向常驻舰队/多机协作时启动深挖）**；第 1 项获 crg 第 9 验证（结构化索引换上下文同范式，管线级攒批证据+1）。

## 2026-10-05 16 时班（批2：学习记忆与自我改进——全类型调研+dao-code 新面孔）

> 16%7=2 批2。**本班只跑轮换批+雷达 C**：15 时班刚跑全量主扫 116 查询（15:1x-16:0x），
> 按「结果高度重合即跳余页省配额」纪律 A 常驻 78 组沿用 1 小时前全量结果（与 12 时班
> 先例同口径）——全 18 任务类型覆盖证据由该主扫继承。批2 10 组 50 行零失败零限流；
> 雷达 C：topic 8 页+npm 两查+awesome 8 清单+周边 3 搜全过；**trendshift 本班被拦**
> （10 时班可达，如实记录不冒充）；pypi 维持省配额在案。WebSearch 串行 1 发（距
> 13 时班全量交叉验证第 3 班到窗口）。

### 新条目（本班真新面孔 1 件）

- **tigicion/dao-code**（1,083★，10-05 push，TypeScript）新入库 | DeepSeek-V4 终端编码
  agent：**byte-stable prefix + cache-reusing forks 工程让跨会话记忆与持续自纠层「近零
  token 成本」**；1M 上下文、Skills/MCP/Hooks、Claude Code config 兼容 | B2
  「agent+self+correction」捞出；缓存纪律向 token 节约与我们 KV-cache 稳定注入同向且更
  激进（字节面稳定前缀换命中率）| 雷达（并入队列第 2 项语义缓存同向信号） | 2026-10-05
- 拒收明细（如实记录）：topic 新锐全微型——xianyu-sheng/Xenon 53★（可验证 runtime 证据
  驱动执行，A13 证据门禁域旁证）/5dive-ai/5dive 65★（自有服务器 agent 公司，同形态）/
  rimio-ai/rimz 31★（多 CLI 控制室）/charter-plane 19★/blacksmith 9★ 等——均不过
  「他们有+我们没有+运转良好」门槛 | 判据 | 2026-10-05

### 组织矩阵新规执行（user 型属主首例）

- 新规「orgs/<org>/repos 顺组织」遇 user 型属主 orgs 端点 404，降级 users/tigicion/repos
  扫 15 件：dao-skills（12 个 superpowers 工作流技能）/ccm（上下文裁剪代理）均 0-2★，
  无附加入库对象；WebSearch 捞出的 TsinghuaC3I 学术 org 9 件扫过零额外对象 | 方法论 | 2026-10-05

### 复查增量（repos 端点 31 仓，16:1x，全 alive 零 archived）

orca **85,260（+21 续领跑）**/gstack **135,244（+91）**/pi **112,522（+36）**/ponytail
**155,310（+34）**/superpowers 295,385/mattpocock-skills 276,430/ECC 273,163/hermes-agent
251,281/opencode 211,789/anthropics-skills 179,698/spec-kit 140,156/OpenSpec 71,057/
agentmemory 29,134/claude-mem 96,283/SkillSpector 19,412/open-code-review 43,774/Reasonix
35,736/planning-with-files 27,285/agentmemory 29,134；写作域 webnovel-writer 7,320/
ainovel-cli 2,096/yomiyasu **1,446（+8 在动）**/drama-skills 2,510/hippo-memory 772/
wenzi-xhs 153/claude-rules 192（停更维持）；gastown 系 beads 27,637/gascity 1,328/
codegraph 73,226/graphiti **31,442（+26）**/huobao-drama 15,703——15 时破稳后首班回稳，
零增量面孔。批2 域在录者星数全部持平或微动：stash 329/pro-workflow 2,901/projectmem
850/mengram 204/MemRL 175/KIP 86/agent-apprenticeship 1,619/hippo-memory 772（批2 复跑
主体重合，同 07 时班批7 口径）。

### WebSearch 交叉验证（第 3 班窗口执行，批2 记忆域）

- 串行 1 查捞出 4 claim，逐条 repos 实证：**TsinghuaC3I/Awesome-Memory-for-Agents**
  （665★，09-28 push）真仓——记忆域论文集清单，与 TeleAI-UAGI/Awesome-Agent-Memory
  658★ 同量级，**雷达源补充候选（keywords.md 已补）**；「self-evolving memory OS
  35.24% token 节约」**仓名未勘定不入库**；SakanaAI/DGM repos 404（Reddit 传闻形态，
  「新闻面 ≠ 开源仓在」第 5 例）；Mem0/Letta/obsidian-second-brain 均已录族 | 判据 | 2026-10-05

### 通道与方法（本班零新差量结论）

- npm 两查零接入级（agent-orchestrator-mcp-server/nax/agentcraft/opencode-oceanus 均已
  录族；bizar 10.33.0 orchestrator-first harness 同录族形态）；周边 3 搜（禅道/自家 CLI/
  orca 周边全零星微型，禅道周边第 10 例零新禅道 AI 竞品）；awesome 8 清单保鲜全 alive
  （VoltAgent 35,220/awesome-claude-code 55,090 当日活跃）| 通道 | 2026-10-05

### 待深挖队列（16 时快照）

15 时快照 11 项全部维持；**第 2 项（语义缓存）获 dao-code byte-stable prefix 同向信号**
（字节面缓存纪律换命中率，攒批证据+1）。本班零机制级新差量（15 时破稳后首班回稳）。

### 七专项巡检（本班独立实证——第 2/4 步）

- **A token**：锚点全实证在位（本班行号：预算熔断 _ensure_budget :61/_budget_max_tokens :613/超限停止 :696、cascade :1522-1525、_shrink_context_block :2534、_serial_shrunk_block :2577、stable_order 前缀缓存 :3137/:3591-3594 三块分开留底、三段压缩 compaction.py:2 剪枝→摘要→surface replace、token_meter.py:53 TokenMeter——cached 单列不计压力 :81-99、diff 评审 _review_depth_note :1015、经验召回 skills.block_for :619+relevance_top :540）。四方向判定维持（prompt 缓存供应商侧+stable_order 保前缀、语义缓存队列第 2 项攒批、diff-only 已满配、廉价分流 cascade+难度选模已满配）；dao-code/crg 均攒批证据不改结论 | 已覆盖
- **B 市场**：六源 SOURCES :50 实证（zcode/anthropic/anthropic-skills/claude-skills/clawhub/cocoloop）；缓存实数 **810 条**（zcode 26/anthropic 315/claude-skills 99/clawhub 215/cocoloop 150/anthropic-skills 5，fetched 2026-10-03）；SSRF 网关 assert_public_url :113+逐跳复检 :159+装前 skill_scan :1051 在位。本班新见（gastown 系工单图、OfficeCLI/genoffice 办公件）过三问均不过——编排差量在队列第 11 项攒批、办公件系文件格式工具面非 skill 包，零接入维持 | 已覆盖
- **C 类型**：BUILTIN_FLOWS 实数 **18**（flows.py :36-128，presentation :98 描述与实现相符：Markdown 逐页大纲+讲稿不产 PPT 二进制、rubric 四维、threshold 7.0/rounds 2）；TYPE_DIMENSIONS 18 键（dispatch.py :12-31——defect_retro 有意落 reasoning 默认、zentao=coding，多班同口径）；test_i18n_dups+test_full_type_round **8/8 绿**；流程参数锚点 TRANSLATION_APPENDIX :2377/_gitlog_brief :952 实证在位 | 巡检
- **D 经验**：data/skills.json lessons=70+packs=3；真实读写接口 app/core/skills.py（block_for/relevance_top/list_lessons）——数据文件不直写；本轮无新蒸馏；偏科口径维持 | 数据卫生
- **E 新 CLI**：catalog.py DEFAULT_CATALOG **14 条目**（:32-201）；本机 which 实测 **11 在装**（codex/claude/opencode/qwen/aider/kimi/mimo/grok/pi/gemini/codebuddy）+openclaw/trae 未装；六候选（deepseek-reasonix/reasonix/fuxi/gitlawb/zero/empryo）全空——零接入防死链维持，Reasonix 候选首位（35.7k★，10-05 push） | 巡检
- **F 禅道**：claims=0 零积压、last_error 空、poll_enabled=False+interval 2h（用户侧预期未启，last_scan 2026-09-21 系停扫后果非故障）；product_profiles=1（product 96、backend→E:\GitLab\cbc\mo-so、owners backend=wuxinping/frontend=xiangdong、auto_resolve/auto_merge/triage_ai 全开）路由在档；链路锚点沿用（_poll/poll_enabled 闸/scan_now/_profile_for）；周边零新竞品 | 巡检
- **G 产品**：过时文案 grep（13种/14种/15种/17种/18种/11 个预置/单源）app/ui 零命中；presentation 文案与实现一致；本班零新毛病 | 巡检

### 借鉴方向勘误 2 条（引用前须对代码实证——07 时班方法论再执行）

- **opencode-metrics「用量页加缓存命中率」（2026-09-22 入库）已落地**：usage.py :654/:672-675
  by_key cache_rate+:832 totals.cache_rate 后端口径齐备，UI app.js :14483「缓存命中率」+
  i18n.js :1224 EN 键在位——划掉 | 勘误 | 2026-10-05
- **OpenSpec explore「先看代码库再给方案」（:233 入库）已覆盖**：planner.py :176
  workdir_recon 即规划前置看库（:237 __RECON__ 注入 CODE_PLAN_PROMPT）——非落地项，划掉
  | 勘误 | 2026-10-05

### 落地件规格（第 3/4 步实施——2 件，均纯提示词指引不改 pass 判定语义）

1. **评审深读指引**（OpenCodeReview 借鉴，:323 入库）：
   - **证据**：CODE_REVIEW_PROMPT（pipeline.py :919）现要求「请只基于下方提供的任务与变更
     内容进行评审」——与「评审者可主动读文件深挖」正面相抵；runner.py :1372-1381 实证只读
     步 CLI 沙箱=read-only（可读不可写），评审员具备读文件能力且不污染 git diff 裁决。
   - **目标函数**：评审在 diff 证据之外可核对涉及文件真实上下文，减少「只看 diff 的半盲评」
     误报/漏报。
   - **预期行为**：评审要求段改为「diff 为主要依据；如运行环境允许读取文件，可打开涉及文件
     及周边代码核对上下文（只读，不得修改任何文件）；无法读文件时基于 diff 评审」——与
     findings 锚定证据段（:928-931，pr-af 借鉴）衔接。
   - **回归用例**：tests/test_borrow_round_regressions.py（新文件）提示词内容守卫——深读
     指引在 prompt、「不要修改任何文件」约束保留、旧句移除、diff 回退口径；既有 11 用例零回归。
   - **修改路径**：pipeline.py CODE_REVIEW_PROMPT 一处+tests/test_borrow_round_regressions.py 新建。
2. **半成品收工提醒**（agent-delegate 借鉴第三件，:530 入库——exit-0 无改动/越范围两件已
   分别落地 VENDOR_REFUSAL 与 07 时班 _scope_note，此件未落地）：
   - **证据**：grep 实证评审链路无 TODO/FIXME/占位实现静态提醒（:3562/:3809 系残稿文件
     清理，语义不同）。
   - **目标函数**：捕获「声明完成但留 TODO/未接线占位」的半成品收工。
   - **预期行为**：CODE_REVIEW_PROMPT 评审要求追加「检查本次变更是否引入 TODO/FIXME/占位
     实现/未接线的函数或配置——发现按半成品如实降档」，纯提醒不改 pass 判定。
   - **回归用例**：tests/test_borrow_round_regressions.py 追加内容守卫（JSON 契约与占位符
     不被误伤）；既有用例零回归。
   - **修改路径**：同上（同一提示词同一测试文件，一次收口）。

### 待深挖队列（16 时快照维持）

11 项全部维持（第 3/10 项保持划掉）；第 1 项攒批证据 +2（context-mode 第 8 验证、crg 第 9
验证已在案）；本班零新队列项。

风险在档（交人拍板，本班不动）：data/zentao.json config.password 明文（攒批既有）；
portscan 真实杀进程用例挂死三班复现；32 位 Python 全量 discover 静默退出（分片绕行）。

## 2026-10-05 18 时班（批4：治理/安全/人机协同——全类型调研，过程审计见 full-type-audit.md）

> 18%7=4 批4。主扫 115 查询 524 行 469 唯一仓零失败零限流（A 常驻 89 含内置 B1 11+
> B4 轮换 10+A1u/A1p2 双轮 16，gh api 串行 4s）。雷达 C：topic 8 页+npm 两查+awesome
> 9 清单+WebSearch 串行 1 查（治理域交叉验证，双通道互证零新竞品）；trendshift 三班连拦、
> Trending 直抓未行、pypi 省配额——三缺位如实记录（full-type-audit.md §2），以 topic
> sort=updated 补位。**批4 域零机制级新差量（15 时破稳后第 2 班回稳）**。新面孔组织
> 矩阵顺藤首件（user 型属主 users/ 端点第 2 例）：dsh-background-agents 带出
> PerryLink/DSH 插件生态 48 件（非 fork 且 dsh- 前缀，per_page=100 两页 28+20 实测；
> 初记 15 件系首页口径不可追溯，独立评审后勘误）。

### 新条目（本班真新面孔，均雷达/参考级）

- **yetone/magpie**（5,007★，10-05 push）新入库 | 菜单栏模型路由「Every agent's model.
  One place」——Codex 走 DeepSeek、Claude Code 走 Kimi，菜单栏一键切 | 网关族
  （ClawRouter/axonhub/ccLoad 簇旁）+1；网关路线不接维持 | 雷达（网关族） | 2026-10-05
- **desplega-ai/agent-swarm**（857★，10-05 push）新入库 |「Your Company Agentic
  Operating System」 | A1 域中件；编排主路径已覆盖 | 雷达 | 2026-10-05
- **kdlbs/kandev**（899★，10-05 push）新入库 | AI Kanban & Dev Environment：编排多
  agent+review changes+开 PR+多 provider | 面板族（cc-haha/free-claude-code/Citadel
  簇）+1 | 雷达（同形态） | 2026-10-05
- **ZaxbyHub/opencode-swarm**（486★，10-05 push）新入库 | OpenCode hub-and-spoke
  swarm 编排插件 | A1 域生态件 | 雷达 | 2026-10-05
- **PerryLink/dsh-research-report**（215★，10-05 push）新入库 | DSH 可验证调研报告
  引擎：**content-addressed evidence ledger 证据账本** | 调研报告类型对标：我们
  research 流程有缺口驱动补查+结论先行，缺「内容寻址证据账本」式引用核验留痕；量级
  微型不入队列，机制备注随批5 citation-verification 词组域攒批 | 雷达 | 2026-10-05
- **PerryLink/dsh-permission-rules**（118★，10-05 push）新入库 | CC 式声明式权限规则
  （ordered allow/deny/ask） | 批4 治理域：我们权限链=设备控制权守卫+白名单闸；
  声明式规则文件是差量形态但 DSH 插件不可直装 | 雷达 | 2026-10-05
- **PerryLink/dsh-\* 插件群 48 件**（非 fork dsh- 前缀两页 28+20 实测；dsh-talk 语音会话/dsh-team-rooms 跨会话房间/
  dsh-session-sync 跨设备同步/dsh-observe OTel 导出/dsh-skill-pack-security 供应链闸
  等，45/48 件 10-05 当日 push，dsh-kit/dsh-laya/dsh-plugin-upgrade-016 为 09 月）新入库 | DeepSeek Harness 插件生态整体 | E 域：deepseek-harness
  已在 catalog，其插件生态=自家 CLI 周边雷达新面；keywords.md 自家 CLI 周边搜已补
  dsh 词（确凿缺口首补） | 雷达（E 域周边） | 2026-10-05
- 拒收明细（如实记录）：edwinkys/phantasm 196★（HITL approval layer，2024-11 停更
  11 个月）/ CosmosYi/AutoControl-Arena 108★（ICML 学术风险发现）/ ESAA-Security
  202★（agent 安全审计微型）/ matank001/cursor-security-rules 380★（2025-08 停更）/
  gemini-ai-code-reviewer 252★（2025-12 停更）+MoaKK/AI-Code-Reviewer 103★+MatterAI
  54★（B1 评审族微型，OpenCodeReview/pr-af 簇旁零差量）/ alphaparkinc/genpark
  semantic-cache-manager 9★（队列第 2 项攒批微证）/ dsh-autotier 1★（cascade 同向
  已满配）/ npm 面 bdb-agent-orchestrator（Untrivial fork）/tide-commander/
  crow-central-agency（面板族）/garda-agent-orchestrator（治理族）/coleo——均不过
  「他们有+我们没有+运转良好」门槛 | 判据 | 2026-10-05

### 复查增量（repos 端点 30 仓，18:4x-19:0x，全 alive 零 archived）

orca **85,356（+96 续领跑）**/gstack 135,277（+33）/pi 112,563（+41）/ponytail
**155,458（+148）**/superpowers 295,431（09-27 后无 push 维持）/mattpocock/skills
**276,548（+118，对 superpowers 差 18.9k 续逼近）**/ECC 273,247（+84）/hermes-agent
251,300/opencode 211,807/anthropics/skills 179,712/spec-kit 140,171/OpenSpec 71,058/
agentmemory 29,132/planning-with-files 27,286/claude-mem **96,334（+51 放量持续）**/
SkillSpector 19,416；写作域 webnovel-writer 7,321/ainovel-cli 2,096/**yomiyasu 1,460
（+14 在动）**/drama-skills 2,512/oh-story 7,261/hippo-memory 772/wenzi-xhs 153/
claude-rules 192（停更维持）；E 域 **DeepSeek-Reasonix 35,730（10-05 push 候选首位
维持）**；治理域 bernstein 1,394（10-05 push 在动）/gastown 系 beads 27,643/gascity
1,329；context-mode **25,438（+15）**/huobao-drama 15,714/alibaba/open-code-review
**43,791（+17）**。勘误：裸名 open-code-review 404，正主全名 alibaba/open-code-review
（全名对照表口径在案）。

### WebSearch 交叉验证（治理域，单通道「零新竞品」结论性判定按规则配额）

串行 1 查：捞出名全为已录框架族（LangGraph/CrewAI/AutoGen/ADK/OpenAI Agents SDK/
Dify/Mastra/OpenClaw）+治理标准面（EU AI Act/NIST AI RMF/ISO 42001）——零新仓
claim，双通道互证批4 稳定期 | 通道 | 2026-10-05

### 待深挖队列（18 时快照）

16 时快照 11 项全部维持（第 3/10 项保持划掉）；本班新面孔均雷达级零新队列项；
dsh-research-report 证据账本机制随批5 citation-verification 词组域备注（量级微型
不入队）。风险在档维持（交人拍板）：data/zentao.json 明文密码；portscan 真实杀进程
用例挂死；32 位全量 discover 静默退出。

### 七专项巡检实测（第 2/4 步，19:0x）

- **token 机制面 10 项在档**：三段压缩/token_meter/预算熔断（`pipeline.py` `_ensure_budget`:61+`_budget_cost_caps`:632）/cascade 分流（`capability.py`:100，`cascade.enabled` 默认 False opt-in）/会话复用（impl_sid:1560+critic_sids:3559+_valid_resume:206）/前缀字节稳定（:2436/:3113/:3595）/`_shrink_context_block`:2538 四层降级/经验召回/评审文本预算（`_full_manuscript`:2992）/diff 兜底评审（:921）。**唯一差量=供应商侧显式 prompt cache_control（全 app/core grep 零命中）——受本步「不扩建缓存」禁令转人工决策，零改动** | 机制 | 2026-10-05
- **catalog 实数勘误**：`catalog.py` `DEFAULT_CATALOG`:32 实数 **14 个**（非任务文本所记 11）；本机 which 按**探测名**实测在机 **13** 个（codex/claude/opencode/qwen/aider/kimi/mimo/grok/pi/dsh/gemini/cbc/trae-cli），仅 openclaw 未装——初稿按包名（kimi-code/deepseek-harness/trae）误测记「在机 10 个，openclaw/kimi-code/deepseek-harness/trae 待装」（4 件待装中 3 件误判），独立评审后勘误（探测名≠包名，:1832 旧诫）；候选 DeepSeek-Reasonix/FuXi/Gitlawb/zero/Empryo which 零命中，维持不盲接 | 勘误 | 2026-10-05
- **禅道运行态实测**：data/zentao.json `poll_enabled=False`/profiles=0/claims=0，last_scan=09-21（14 天前）last_error 空——**链路与 UI 完好但本机定时扫描未启用（部署配置缺位，非代码缺陷）**；积压/路由/回写本机未验证（无实例权限，不为验证触发真实工单） | 巡检 | 2026-10-05
- **经验库分布复核**：70 条（+3）流程规范 36%/节奏爽点 30%/情节逻辑 14%/人物塑造 10%/一致性 6%/文笔风格 4%——维持 :1831「无病态偏科」；真实偏科在 scope（serial_novel 66%/code 14%）；「市场接入三问」已在库（去重实测生效），新增入库 1 条「禅道巡检运行态三看」（sk-f9aa2d8d1043，scope=code） | 数据卫生 | 2026-10-05

## 2026-10-05 20 时班（批6：框架/平台/SDK 生态——全类型调研+trendshift 破拦，过程审计见 full-type-audit.md 顶部节）

> 20%7=6 批6（本日 06 时班同批轮换再访）。主扫 **115 查询 524 行 470 唯一仓零失败零限流**
> （A 常驻 89 含内置 B1 11+B6 轮换 10+A1u/A1p2 双轮 16，gh api 串行 4s）。雷达 C：topic 8 页
> +npm 两查+awesome 11 源+禅道周边 2 搜全过；**trendshift 本班可达（破三连拦）**捞 5 件全
> repos 二次实证；WebSearch 串行 1 查（批6 域，双通道互证零新仓 claim）；pypi 省配额、
> Trending 直抓未行（trendshift 补位）。开工并发事实：18 时班收口通道 b6d2269 先于开工
> 96 秒离场，工作区干净无踩踏。**批6 域零机制级新差量（第 22 班稳定期延续）**。

### 新条目（本班真新面孔，均雷达/参考级——逐条证据见 full-type-audit.md §5）

- **lexmount/moli**（8,448★，created 08-10，Rust，trendshift live #6）新入库 | 「AI Agent 专用浏览器」纯 Rust，增速快 | 浏览器自动化域（BrowserSkill/gsd-browser 族旁）；我们 CDP 通道替代思路再 +1 | 雷达 | 2026-10-05
- **neilsonnn/image-blaster**（9,186★，push 05-15）新入库 | image-to-world skillset for Claude | skill 生态（封面图域旁，我们封面生成已落地） | 雷达 | 2026-10-05
- **morluto/rea**（4,020★，10-05 push，trendshift #1）新入库 | 逆向工程 agent：CLI+MCP 检视任意应用至二进制层 | 工具域，非编排竞品 | 雷达 | 2026-10-05
- **tester-army/e2e**（3,840★，10-05 push）新入库 | 新一代 e2e 测试框架（AI agent 标签） | B1 测试族旁 | 雷达 | 2026-10-05
- **shiwenwen/hope-agent**（1,747★，created 03-13，Rust）新入库 | 跨设备桌面 AI agent（记忆+自主目标） | 同形态（桌面 agent 面） | 雷达 | 2026-10-05
- **michael-denyer/pstack-claude**（1,297★，created 05-26）新入库 | 多 CLI 栈/版本管理器（CC/Codex/Pi/OpenCode/Gemini/Prime Agent） | A13 多 CLI 面板族 +1 | 雷达 | 2026-10-05
- **agentlas-ai/Agentlas-OS**（1,558★，created 06-04，Python）新入库 | 「专家 agent 枢纽+临时拉起」——A1u 新锐轮捞出 | A1 域：常驻专家编队+按需临时工形态 | 雷达 | 2026-10-05
- **strukto-ai/mirage**（3,676★，created 05-06，TS）新入库 | 「AI Agent 虚拟终端」（topic:llm-agents updated 捞出） | 面板族（OmniTerm/agent-pane 簇旁） | 雷达 | 2026-10-05
- **juspay/xyne-spaces**（797★，created 08-02）新入库 | 「AI Org-OS」人+agent 协作平台 | multica 看板族旁 | 雷达 | 2026-10-05
- **nuwax-ai/nuwax**（890★，created 2025-07）新入库 | 企业级 Agent OS 开发平台 | 平台族 | 雷达 | 2026-10-05
- **HKUDS/nanobot**（48,794★，created 02-01，Python）新入库 | 超轻自托管个人 agent 框架 | HKUDS 族新面（OpenHarness/DeepCode 旁）；编排主路径已覆盖 | 雷达 | 2026-10-05
- **GoogleCloudPlatform/race-condition**（234★，created 03-27）新入库 | Google 官方多 agent 模拟（B6 组捞出） | 大厂生态例证 | 参考 | 2026-10-05
- **shareAI-lab/learn-claude-code**（78,022★）新入库 | 「Bash is all you need」nano claude-code 教学件 | 学习材料 | 参考 | 2026-10-05
- **pbakaus/impeccable**（76,787★，10-05 push）新入库 | 「让 AI harness 更擅长设计的设计语言」 | skill/设计域 | 参考 | 2026-10-05
- **msitarzewski/agency-agents**（156,954★）新入库（家族注记）| **agency-agents-zh（已录 21,060★）英文原仓**——蒸馏结论沿用 09-20 原条（中文专家角色→交付契约），不重复入库 | 家族注记 | 2026-10-05
- **herdrdev/herdr**（42,401★，10-05 push，Rust）星数首记 | 「coding agents 的 runtime」——09-19 装机探测候选（未装），gascity runtime provider 之一 | E 域已知名，装机候选顺位记录 | 雷达 | 2026-10-05
- **wanshuiyin/Auto-claude-code-research-in-sleep**（16,985★，push 09-29）新入库 | ARIS markdown-only 自动调研 skills | 研究域（调研报告类型旁） | 雷达 | 2026-10-05
- omnirush-ai/omnirush-gui（1,477★，面板族）/ neilsonnn 系外拒收明细（xagent 303/tlive 214/beamer-academic 306/live-panel-skill 546/claude-code-monitor 310/idun 203/paneflow 83/ensemblr 8 已知族/corezoid 72/agentfactory 3/wild_agentos 3/masc 3/alteroid 0 等）均不过三门槛 | 判据 | 2026-10-05

### 复查增量（repos 端点 52 仓次，20:2x-21:0x，全 alive 零 archived）

orca **85,423（+67 续领跑）**/superpowers 295,449（09-27 后无 push 维持）/mattpocock/skills
**276,640（+92，对 superpowers 差 18.8k 续逼近）**/ECC 273,289（+42）/hermes-agent 251,309/
opencode 211,815/anthropics/skills 179,720/spec-kit 140,183/OpenSpec 71,062/agentmemory
29,133/planning-with-files 27,287/**claude-mem 96,371（+37 放量持续）**/SkillSpector 19,422/
**pi 112,589（+26）**/ponytail **155,539（+81）**；写作域 webnovel-writer 7,321/ainovel-cli
2,096/**yomiyasu 1,476（+16 在动）**/drama-skills 2,517/oh-story 7,264/hippo-memory 772/
wenzi-xhs 153/claude-rules 192（停更维持）；E 域 **DeepSeek-Reasonix 35,729（10-05 push 候选
首位维持）**/herdr 42,401（星数首记）；治理域 bernstein 1,395/gastown 系 beads 27,643/gascity
1,329；context-mode **25,442（+4）**/huobao-drama 15,723/alibaba/open-code-review **43,802（+11）**；
B6 域 mastra 28,560/vercel-ai 27,122/**harness-sdk 8,669（+13）**/**semantix 821（+170 放量）**/
aegra 1,240/gstack **135,288（+11，正主 garrytan/gstack 勘定）**——15 时破稳后第 3 班回稳，
零增量面孔。

### 全名对照表（本班新增 10 仓，后续班免再勘定）

gstack=**garrytan/gstack**/webnovel-writer=**lingfengQAQ/webnovel-writer**/ainovel-cli=
**voocel/ainovel-cli**/yomiyasu=**nanaism/yomiyasu**/hippo-memory=**kitfunso/hippo-memory**/
wenzi-xhs=**wenziai/wenzi-xhs-agent-skills**/DeepSeek-Reasonix=**esengine/DeepSeek-Reasonix**/
awesome-llm-apps=**Shubhamsaboo/awesome-llm-apps**/keep-the-why=
**oliver-zehentleitner/keep-the-why**/xyne-spaces=**juspay/xyne-spaces** | 对照表 | 2026-10-05

### 禅道周边与 keywords 判定

- 禅道周边搜第 11 例零新禅道 AI 竞品（easysoft/zentao-skills 74★ 官方族在录；bug-triage 面
  1★/0★ 微型） | 巡检 | 2026-10-05
- **keywords.md 本班零调整**：新面孔全部由现有词组（A1u/A2/A6/B6/A7/A10/A13）与雷达源
  （trendshift/topic 页）捞出，无确凿缺口 | 判定 | 2026-10-05

### 待深挖队列（20 时快照）

16 时快照 11 项全部维持（第 3/10 项保持划掉）；本班新面孔均雷达级**零新队列项**
（moli 浏览器件随 BrowserSkill 同域备注；Agentlas-OS「专家枢纽+临时拉起」形态随队列
第 11 项 gascity 常驻舰队域远期备注）。风险在档维持（交人拍板）：data/zentao.json 明文
密码；portscan 真实杀进程用例挂死；32 位全量 discover 静默退出。

## 2026-10-05 21 时班（批7：中文/网关/本地/办公——全类型调研，第 1/4 步）

> 21%7=0 批7（07 时班同批轮换再访）。主扫 **114 查询 514 行 454 唯一仓零失败零限流**
> （A 常驻 89 含内置 B1 11+B7 轮换 9+A1u/A1p2 双轮 16，gh api 串行 4s）。雷达 C：topic
> 8 页+npm 两查+awesome 10 源+trendshift（可达，29 仓 6 件新全 repos 二次实证）+禅道周边
> 2 搜+WebSearch 串行 1 查（批7 域）；pypi 省配额、Trending 直抓未行（trendshift 补位，
> 20 时班同口径）。**批7 域有真新面孔——WebSearch 交叉验证再立功：StaffDeck 1,967★
> 系 OpenBMB 数字员工平台，147 组词各班从未捞出（词盲区实证，keywords A2 补丁已落）**。

### 新条目（本班真新面孔，均雷达/参考级）

- **maximhq/bifrost**（8,558★，10-05 push，trendshift）新入库 | 企业级 AI 网关
  「50x LiteLLM」自适应负载均衡+集群模式 | 网关族（axonhub/ClawRouter/ccLoad/magpie
  簇旁）+1；网关路线不接维持 | 雷达（网关族） | 2026-10-05
- **OpenBMB/StaffDeck**（1,967★，09-29 push，WebSearch 捞出）新入库 | 企业数字员工
  平台：岗位/工号/能力档案/定时任务/私有化部署 | 批7 域；「数字员工组织化」隐喻 vs
  我们任务档案+定时调度已覆盖主路径，无机制级差量；量级中型持续观察 | 雷达 | 2026-10-05
- **microsoft/Sico**（273★，10-03 push，批7 域搜索顺带捞出）新入库 | 微软开源数字
  员工平台（可靠执行+持续协同进化） | 数字员工域 +1（大厂例证） | 雷达 | 2026-10-05
- **Ebony-Vinyl/dsh-our-free-model**（1,513★，10-05 push，trendshift）新入库 | DSH
  插件：免登录免 Key 用 DeepSeek V4.1 Flash/Kimi K3 前沿模型 | DSH 插件生态 +1
  （PerryLink 48 件口径外首件外部属主，生态从组织内扩散信号） | 雷达（E 域） | 2026-10-05
- **kagent-dev/kagent**（3,936★，10-05 push，topic:mcp 捞出）新入库 | CNCF 云原生
  agentic AI | 框架族旁 | 雷达 | 2026-10-05
- **OpenCut-app/OpenCut**（92,528★，09-24 push，trendshift）新入库 | 开源 CapCut
  替代视频编辑器 | 域外工具；「小说→短剧」远期方向的下游剪辑面备注 | 参考 | 2026-10-05
- 微型批（均不过三门槛，如实记）：theopenbee/openbee 32★（7x24 数字员工）/
  PlumoAI/plumoai 76★（AI 员工平台）/ greenticai/greentic 16★（Digital Workers OS）/
  AbdoKnbGit/tau 397★ / 5dive-ai/5dive 65★ / LukeRenton/explore-claude-code 319★
  （学习材料）；npm 面 @nathapp/nax（loops-until-done，10-05 活跃）/opencode-oceanus/
  @polderlabs/bizar——npm scope 无公开同名仓，不引仓 claim | 判据 | 2026-10-05

### 复查增量（repos 端点 48 仓次，21:4x-22:0x，全 alive 零 archived）

orca **85,481（+58 续领跑）**/gstack 135,302（+14）/pi 112,612（+23）/ponytail
**155,596（+57）**/superpowers 295,479（09-27 后无 push 维持）/mattpocock/skills
**276,706（+66，对 superpowers 差 18.8k 续逼近）**/ECC 273,326（+37）/hermes-agent
251,329/opencode 211,825/anthropics/skills 179,727/spec-kit 140,197/OpenSpec 71,065/
agentmemory 29,136/planning-with-files 27,287/claude-mem **96,402（+31 放量持续）**/
SkillSpector 19,424；写作域 webnovel-writer 7,321/ainovel-cli 2,096/**yomiyasu 1,479
（+3 在动）**/drama-skills 2,517/oh-story 7,265/hippo-memory 772（10-05 push）/wenzi-xhs
153；**answer-me-with-html 724→1,359（+635 当日翻倍，10-03 入库 130★ 起三日 10 倍
放量）**；E 域 **DeepSeek-Reasonix 35,731（10-05 push 候选首位维持）**/herdr 42,412
（+11）；治理域 bernstein 1,396/gastown 系 beads 27,643/gascity 1,329 持平；context-mode
**25,447（+5）**/huobao-drama 15,726/alibaba/open-code-review **43,808（+6）**；
trendshift 放量件：**moli 8,448→8,604（+156）**/**rea 4,020→4,253（+233）**/
**tester-army/e2e 3,840→3,925（+85）**/image-blaster 9,285（+99，push 05-15 停更
星涨）/magpie 5,056（+49）。20 时班新面孔全存活零 archived。

### 全名对照表（本班新增 1 仓）

semantix=**Gnosil/semantix**（821★ 与 20 时班记录吻合，search in:name 一次勘定）；
agent-swarm=desplega-ai/agent-swarm、hippo-memory=kitfunso/hippo-memory 复认在档
| 对照表 | 2026-10-05

### 覆盖矩阵（14 指令项 vs 注册表 18 类型，不静默删项）

14 项一一映射注册类型：直接执行→direct/代码→code/小说→novel/连载→serial_novel/
自媒体文章→article/调研报告→research/短视频脚本→video_script/技术方案→tech_proposal/
翻译→translation/演讲稿→speech/工作汇报→weekly_report/商务邮件→email/扫榜选材→
rank_scan/**禅道工单→defect_retro**（18 时班联调勘定口径）；另 4 独有 doc/presentation/
resume/bid_doc=**18** 实测吻合。补充雷达三面：对话记忆（A10 chatbot+hippo-memory/
LightMem/memsearch 复查）、知识库（批5 词组+Yuxi 7,280 活跃）、文档生成（A10 doc gen）
均已覆盖 | 矩阵 | 2026-10-05

### 七专项巡检实测（第 2/4 步，21:5x）

- **A token**：锚点全实证在位（_ensure_budget :61/_budget_cost_caps :632/cascade
  :1526-1529 opt-in/_shrink_context_block :2538/_serial_shrunk_block :2581/stable_order
  :3141/usage cached 细分 :117-120）；cache_control 全库 grep 零命中维持（供应商侧显式
  缓存受「不扩建缓存」禁令转人工，不动）；四方向判定维持 | 已覆盖
- **B 市场**：SOURCES :50/assert_public_url :113+143+159/装前 skill_scan :1051-1054
  在位；本班新见（bifrost 网关/StaffDeck 平台/dsh-our-free-model 插件）过三问均不过
  ——bifrost 系网关非 skill 包、StaffDeck 系平台不可直装、dsh 插件属 DSH 不属六源；
  零接入维持 | 已覆盖
- **C 类型**：BUILTIN_FLOWS import 实数 **18**（id 清单逐项在档）、TYPE_DIMENSIONS
  **18** 键；14 指令项覆盖矩阵全对上（见上节） | 巡检
- **D 经验**：data/skills.json lessons=**71**（较 18 时班 +1；流程规范 26/节奏爽点 21/
  情节逻辑 10/人物塑造 7/一致性 4/文笔风格 3——流程规范 36.6% 口径维持）；packs=3；
  本班零新蒸馏（WebSearch 交叉验证规则已在 keywords.md 规则层） | 数据卫生
- **E 新 CLI**：catalog DEFAULT_CATALOG **14 条目**；本机 which 按探测名实测 **14 在装**
  （codex/claude/opencode/qwen/aider/kimi/mimo/grok/pi/dsh/gemini/codebuddy/cbc/
  trae-cli——**codebuddy 本班首记在位**，18 时班口径 13）仅 openclaw 未装；六候选
  （deepseek-reasonix/reasonix/fuxi/gitlawb/zero/empryo）which 全空——零接入防死链
  维持，Reasonix 候选首位（35,731★ 10-05 push） | 巡检
- **F 禅道**：data/zentao.json poll_enabled 键缺省（:122 默认 False）/profiles=0/
  claims=0/last_scan 2026-09-21/last_error 空——本机定时扫描未启用（部署配置缺位非
  代码缺陷，18 时班同口径）；链路锚点 _poll :2403/poll_enabled 闸 :2425/_profile_for
  :334 全在位；周边 2 搜**零新禅道 AI 竞品（第 12 例持续为零）** | 巡检
- **G 产品**：过时文案 grep（13 种/14 种/15 种/17 种/11 个预置/单源模式）app/ui+app/core
  零命中；本班零新毛病 | 巡检

### 待深挖队列（21 时快照）

20 时快照 11 项全部维持（第 3/10 项保持划掉）；本班新面孔均雷达级**零新队列项**
（StaffDeck 组织化隐喻随 A2 域备注；bifrost 随网关族不接口径；moli +156 放量强化
BrowserSkill 同域备注）。风险在档维持（交人拍板）：data/zentao.json 明文密码；
portscan 真实杀进程用例挂死；32 位全量 discover 静默退出。

## 2026-10-05 21 时班补录（第 2/4 步独立复核+在册清账+D 蒸馏落地，22:1x）

> 与 21:5x 七专项巡检实测同窗互证：以下锚点全部本班独立实读/实测，非沿用上文。

### 七专项独立复核（亲测锚点）

- **A token**：_ensure_budget :61/_budget_cost_caps :632/cascade 分流 :1529（capability.cascade_reorder
  :100，`cascade.enabled` opt-in）/_shrink_context_block :2538/_serial_shrunk_block :2581/
  stable_order 前缀缓存 :3141-3142+:3595-3598/`cache_control` 全 app/core grep 零命中（供应商侧
  显式缓存维持转人工）/usage cached 细分 :117-120——四方向判定维持 | 已覆盖 | 2026-10-05
- **B 市场**：六源 SOURCES :50-76（zcode/anthropic/anthropic-skills/claude-skills/clawhub/cocoloop）；
  缓存实数 **810** 逐源实测（26/315/5/99/215/150，fetched_at 2026-10-03）；SSRF assert_public_url
  :113（仅 https+环回/私有/保留拒）+装前 skill_scan.scan_text 风险随预览 :1051-1054 在位；
  本班零新候选、三问零接入 | 已覆盖 | 2026-10-05
- **C 类型**：BUILTIN_FLOWS 实数 **18**（flows.py:36-134 逐条）；**13/14 口径**=任务指令 13 类型
  为指令面清单（禅道工单勘定为 defect_retro 映射后 14 面），注册表 18=指令 14+自研 4
  （doc/presentation/resume/bid_doc）——不静默删项，覆盖矩阵 21 时班节在档；C 三件测试
  test_i18n_dups+test_full_type_round+test_flows **17 项 OK**（cd tests 净进程，7.2s） | 巡检 | 2026-10-05
- **D 经验**：lessons=71→**72**（本班蒸馏 +1，见下）；分布 流程规范 26/节奏爽点 21/情节逻辑 10/
  人物塑造 7/一致性 4/文笔风格 **4**（36.6% 口径维持）；id/标题零重复；真实偏科在 scope
  （serial_novel 占大头，article 本班 +1 至 11 条微改善）；packs=3 | 数据卫生 | 2026-10-05
- **E 新 CLI**：catalog DEFAULT_CATALOG 14 条目（:34-201）；本机 which 按探测名实测 **14 在装**
  （codex/claude/opencode/qwen/aider/kimi/mimo/grok/pi/dsh/gemini/codebuddy/cbc/trae-cli）仅
  openclaw 未装；六候选（reasonix/deepseek-reasonix/fuxi/gitlawb/zero/empryo）which 全空——
  零接入防死链维持 | 巡检 | 2026-10-05
- **F 禅道**：data/zentao.json config 实测 poll_enabled=False/interval_hours=2/product_profiles=1
  （product 96、our_sides=['backend']、triage_ai 键缺省=DEFAULT_CONFIG :121 True 默认生效——
  16 时班「全开」系默认值语义非显式配置）/claims=0 零积压/last_scan 2026-09-21/last_error 空
  ——定时扫描未启用系部署配置缺位非代码缺陷（多班同口径）；链路锚点 _poll :2403/poll_enabled
  闸 :2425/_profile_for :334 全在位；不为验证触发真实工单 | 巡检 | 2026-10-05
- **G 产品**：过时文案 grep（13 种/14 种/15 种/17 种/18 种/11 个预置/单源）README+app/ui+app/core
  零命中（paihang/pipeline 的「单源」系数据源冗余语境非菜单文案）；rank_scan 菜单「四平台」与
  paihang fetch_qimao/fanqie/qidian/zongheng :74-90 相符；presentation 文案与实现相符（:98）；
  设置页禅道子页齐备（app.js:11569 loadZentao/renderZentaoProfiles/Status/Claims）；本班零新毛病
  | 巡检 | 2026-10-05

### 在册借鉴方向对码清账（引用前须实证——07 时班方法论执行，4 件划掉）

- **agentic-inbox 邮件线程契约已落地**：CONTENT_CONTRACTS email 段 pipeline.py:2309-2311
  （「逐条回应对方每个问题与请求」+「行动项落到具体动作+负责人+截止时间」，:2309 注释点名
  agentic-inbox）——2026-09-20 入库「借鉴（邮件类型后续加入线程摘要和行动项校验）」划掉 | 勘误 | 2026-10-05
- **hippo-memory 两差量均已落地**：①教训卡「标记无用」=lesson_op `useless`（skills.py:495-497，
  停用+粘滞负证据）+卡片按钮 app.js:11147；②久未命中衰减=_surplus_decay（skills.py:348）——
  2026-10-03 入库「D 专项小件」划掉 | 勘误 | 2026-10-05
- **tradememory outcome 加权已覆盖**：skills._karma（:326）won/lost 结局加权+过期降权+hits 记账
  在位——「教训 outcome 加权」借鉴方向划掉 | 勘误 | 2026-10-05
- **SkillForge 装后冒烟已落地**：market.py 装后冒烟（07 时班 B 专项在案）+market_remote 装前
  skill_scan :1051——「技能装后冒烟验证」划掉 | 勘误 | 2026-10-05

### D 蒸馏落地（在册候选清账，走既有入库通道）

- **wenzi-xhs「选题标题/排期复盘」蒸馏入库**（:1181 蒸馏候选）：skills.upsert_lesson 落 1 条
  **sk-8114e5d9a90a**（scope=article/category=文笔风格/kind=procedure，内容五面映射原仓库
  账号定位/选题标题/真人化改稿/图文规划/排期复盘，平台数据只作假设口径与 fanqie-novel 包
  一致）；lessons 71→72，article scope 10→11 | 已落地 | 2026-10-05

### 落地件选定结论（第 3/4 步交接）

- 在册代码级「小而实」积压**核对为零**：邮件契约/标记无用/时间衰减/outcome 加权/装后冒烟/
  复盘报告（defect_retro 已成 18 类型之一）/缓存命中率（16 时班勘误已落地）逐件对码全在位；
  队列活项均为攒批（第 1/4 项管线级）/拍板（第 2/3 项）/远期（第 11 项）——按「拍板件不擅动」
  纪律不选。本步实落地两件均为文档/数据通道：**D 蒸馏入库 1 条+路线图复选框清账 4 项+在册
  借鉴勘误 4 件**（零 app/ 实现改动，与 06 时班「调研工具链+沉淀」先例同口径）；第 3/4 步若
  开代码件，等队列第 2 项（语义缓存）等人拍板后启动 | 判定 | 2026-10-05
- **第 3/4 步执行回执（22 时落地班）**：按上条结论不开代码件——交接两件验收通过（蒸馏
  sk-8114e5d9a90a 恰 1 条零重复/清账 4 锚点逐一实读在位），并把清账引用锁进回归
  tests/test_full_type_iteration.py RoadmapClearingAnchorTests 5 用例（13/13 OK，触及面
  full_type_round 5+i18n_dups 3+flow_icons 2 全绿）——「引用前须对代码实证」方法论机械化，
  锚点漂移即红 | 已落地 | 2026-10-05

### 待深挖队列（21 时补录快照）

21 时快照 11 项全部维持（第 3/10 项保持划掉）；本班零新机制级发现，零新队列项。
风险在档维持（交人拍板）：data/zentao.json 明文密码；portscan 真实杀进程用例挂死；
32 位全量 discover 静默退出。

## 2026-10-06 01 时班（批1：代码质量与评审——全类型调研，第 1/4 步）

> 1%7=1 批1（15 时班同批轮换再访）。主扫 **116 查询 525 行 426 唯一仓零失败零限流**
> （A 常驻 89 含内置 B1 11+B1 轮换 11+A1u/A1p2 双轮 16，gh api 串行 4s）。雷达 C：
> awesome 12 源 repos 实测全 alive+topic 8 页（sort=updated）+npm 两查+trendshift 可达
> （29 仓）+**WebSearch 串行 2 查（批1 域交叉验证，距 21 时班 4 班到窗口）**；pypi 省配额、
> Trending 直抓未行（trendshift 补位）。证据底稿 docs/borrow-log/current-round.md。

### 新条目（本班真新面孔，均雷达/参考级）

- **chunxiaoxx/nautilus-compass**（1,134★，10-05 push，Python，A13 域捞出）新入库 |
  多智能体可靠性层：无编排者协调+跨对话契约+漂移检测+黑盒记忆（no LLM extraction） |
  漂移检测与 _scope_note 越范围提醒同域但更广；黑盒记忆与 agentmemory/hippo-memory
  同域；我们编排者在位不同轨 | 雷达 | 2026-10-06
- **kodustech/kodus-ai**（1,449★，10-05 push，WebSearch 捞出）新入库 | self-hosted AI
  code review（AGPLv3/Docker Compose/BYO-model；`kodus/kodus` 404→search 一次勘定正主）
  | 批1 域竞品；我们 diff 评审链已满配（深读/越范围/半成品三件在位）；AGPLv3 不接 |
  雷达（批1 域） | 2026-10-06
- **The-PR-Agent/pr-agent**（13,273★，10-05 push）新入库（仓名迁移勘定） | 老牌开源 PR
  reviewer（PR 摘要/逐行评论）；**qodo-ai/pr-agent → The-PR-Agent/pr-agent 属主迁移**
  ——「新闻面 ≠ 开源仓在」规则第 4 例（迁移形态首例） | 机制面对应物已满配（评审 JSON
  findings 契约）；对照表收录 | 雷达 | 2026-10-06
- **agent-sh/agnix**（440★，10-05 push，Rust，topic:mcp 捞出）新入库 | AI 助手配置
  linter+LSP：校验 CLAUDE.md/AGENTS.md/SKILL.md/hooks/MCP 带 autofix | 我们 skill_scan
  系安全向、agnix 系配置规范向——B 专项 skill 包质量校验思路备注，当前不接 |
  雷达（B 专项备注） | 2026-10-06
- **ZASENJC/dsh-plugins-store**（68★，10-05 push）新入库 | DSH 社区插件自动分类/收录/
  验证商店 | E 域生态扩散第 2 信号（继 dsh-our-free-model 外部属主后第三方商店形态）；
  微型 | 雷达（E 域） | 2026-10-06
- 微型批（不过三门槛，如实记）：almogdepaz/wolfpack 46★（self-hosted 浏览器终端面板）/
  parallax-labs/context-harness 42★（本地上下文摄取检索）/ amirfish1/
  claude-command-center 177★（Claude Code 命令中枢）/ aarondpn/redmine-cli 51★
  （Redmine CLI 项目管理域微型）/ Inkloom-art/inkloom 1,368★（logo 设计 AI 管线，
  trendshift 捞出，域外）| 判据 | 2026-10-06

### 复查增量（repos 端点 24 仓，01:1x-01:2x，全 alive 零 archived）

orca **85,660（+179 续领跑）**/superpowers 295,558（+79 无 push 维持）/mattpocock/skills
**276,863（+157，差 superpowers 18.7k 续逼近）**/ECC 273,439（+113）/ponytail
**155,778（+182）**/pi 112,658/hermes-agent 251,379/opencode 211,843/anthropics/skills
179,754/open-code-review 43,833/herdr 42,456/claude-mem **96,493（+91 放量持续）**/
agentmemory 29,145/SkillSpector 19,438/**DeepSeek-Reasonix 35,735（10-05 push E 候选
首位维持）**；写作域 webnovel-writer 7,324/ainovel-cli 2,096（10-05 push 活跃）/
**yomiyasu 1,495（+16 在动加速）**/drama-skills 2,525/hippo-memory 772 持平/
huobao-drama 15,750/cc-haha 14,867；治理域 beads 27,646/gascity 1,330。
**trendshift 同源放量**：**morluto/rea 4,253→4,879（+626 当日放量在榜）**；
answer-me-with-html 1,359 持平回落榜外。awesome 群增量：awesome-mcp-servers
95,837/hesreallyhim 55,096/awesome-llm-apps 140,768/VoltAgent 35,228/ai-boost 4,710
（ai-boost 4.7k★ 属主补认维持）。

### 全名对照表（本班新增 2 仓）

kodus=**kodustech/kodus-ai**（1,449★，WebSearch claim→search in:name 勘定）；
pr-agent=**The-PR-Agent/pr-agent**（qodo-ai 属主 404 实证迁移）——后续班 repos 复查
直用新名免再勘定 | 对照表 | 2026-10-06

### 覆盖矩阵（14 指令项 vs 注册表 18 类型，不静默删项）

14 项一一映射注册类型（禅道工单→defect_retro）+4 独有=BUILTIN_FLOWS **18** 实测吻合
（01:0x import 实数+id 逐项在档）；**「13 种」系任务指令面口径**（+禅道工单映射=14 面），
注册表 18=指令 14+自研 4——以源码实数为准；补充雷达对话记忆/知识库/文档生成三面均覆盖
| 矩阵 | 2026-10-06

### 七专项巡检实测（第 2/4 步，01:0x-01:2x 独立实证）

- **A token**：锚点全实证在位（_ensure_budget :61/_budget_max_tokens :613/
  _budget_cost_caps :632/cascade :1526-1529/_shrink_context_block :2538/
  _serial_shrunk_block :2581/stable_order :3141+:3595/`cache_control` grep 零命中）；
  四方向判定维持；新见 context-harness 等均微型雷达级不改结论 | 已覆盖 | 2026-10-06
- **B 市场**：六源 SOURCES :50-76 在位；缓存 **810** 逐源实测（26/315/5/99/215/150，
  fetched_at 2026-10-03，与 21 时班零漂移）；新见（agnix/dsh-plugins-store）过三问
  均不过，零接入维持 | 已覆盖 | 2026-10-06
- **C 类型**：BUILTIN_FLOWS 实数 **18**（id 清单逐项在档 current-round.md）；14 指令项
  覆盖矩阵全对上 | 巡检 | 2026-10-06
- **D 经验**：lessons=**72** 零漂移（流程规范 26/节奏爽点 21/情节逻辑 10/人物塑造 7/
  一致性 4/文笔风格 4，36.1% 口径维持）；packs=3；本班零新蒸馏 | 数据卫生 | 2026-10-06
- **E 新 CLI**：catalog DEFAULT_CATALOG :32 在位（**14 条目**）；本机 which 实测 **13/14 在装**
  （codex/claude/opencode/qwen/aider/kimi/mimo/grok/pi/dsh/gemini/cbc/
  trae-cli；codebuddy 条目探测名为 cbc——「14 在装」系同条目双计，勘误承 00 时班，
  本班 import 实测 codebuddy→detect.cli=cbc 复证）仅 openclaw 未装；六候选（reasonix/
  deepseek-reasonix/fuxi/gitlawb/zero/
  empryo）which 全空——零接入防死链维持，Reasonix 35,735 候选首位 | 巡检+勘误承00时班 | 2026-10-06
- **F 禅道**：poll_enabled=False/claims=**0 零积压**/profiles=1/last_error 空——定时
  扫描未启系部署配置缺位非代码缺陷（多班同口径）；链路锚点 _poll :2403/poll_enabled 闸
  :2425/_profile_for :334 全在位；周边零新禅道 AI 竞品（**第 13 例持续为零**） |
  巡检 | 2026-10-06
- **G 产品**：过时文案 grep（13 种/14 种/15 种/17 种/11 个预置/单源）app/ui+app/core+
  README 零命中；本班零新毛病 | 巡检 | 2026-10-06

### 落地件选定结论（第 3/4 步交接）

在册代码级「小而实」积压**核对为零**（22 时落地班清账+回归锁定在案）；队列活项均为
攒批/拍板/远期——按「拍板件不擅动」纪律不开代码件。本班实落地=调研沉淀通道：
current-round.md（新，证据底稿）+ knowledge.md 本班节 + 对照表 2 仓。零 app/ 实现、
零 JS/CSS 改动，第 4/4 步按 docs-only 不发版预记执行 | 判定 | 2026-10-06

### 待深挖队列（01 时快照）

21 时快照 11 项全部维持（第 3/10 项保持划掉）；本班新面孔均雷达级**零新队列项**
（nautilus-compass 漂移检测随 A13 越范围域备注；agnix 随 B 专项 skill 包质量校验
备注；kodus-ai/pr-agent 系已满配评审域旁证）。风险在档维持（交人拍板）：
data/zentao.json 明文密码；portscan 真实杀进程用例挂死；32 位全量 discover 静默退出。

### 02 时独立复核（第 1/4 步交接前，01:25-01:35）

初稿产物逐项独立实证全过：主扫 DONE 116/525 行/唯一仓勘正 **426**；flows=18 import
复测、pipeline/market 锚点复测；market 缓存 810 逐源吻合；lessons=72/packs=3；
zentao poll_enabled=False/profiles=1/claims=0；本机 14 二进制（codebuddy+cbc 同条目）、
六候选全空；repos 抽验 5 仓（pr-agent 13,273/kodus-ai 1,449/nautilus-compass 1,134
分毫不差，orca 85,666 与 skills 276,873 为自然增量）；复查时间窗误标 01:3x-01:4x
勘正 01:1x-01:2x（产物 mtime 链实证）| 复核 | 2026-10-06

## 2026-10-06 03 时班（第 2/4 步·七专项深化实证——锚点全复测，零接入零代码件）

> 基于第 1/4 步定位逐项独立复测；全程只读巡检（禅道未触发真实回写）。
> 详证见 current-round.md「03 时巡检班」节。

### 七专项实测（本班独立行号级实证）

- **A token**：七闸口 _ensure_budget :61（:412/:531/:990/:1463/:3201/:4812/:5439）+
  token_meter :690/:723/:743（used/accumulate）+ 撑爆→压缩→守门重试 :704-742 +
  _shrink_context_block :2538/_serial_shrunk_block :2581（:3676/:3800 两处调用）+
  cascade :1526-1529 opt-in + 会话复用 session_run_id :487/:672/:705 + 经验召回
  stable_order :3142/:3598（§07 T1.2' 字节级一致）+ diff 评审链 _git_diff :948/
  CODE_REVIEW_PROMPT :919/_review_depth_note :1019 全在位；四方向判定维持
  （prompt 缓存供应商侧已最大化/语义缓存待拍板/diff-only 已有/廉价分流已满配）
  **零重复建设** | 已覆盖 | 2026-10-06
- **B 市场**：六源 :50-76+SSRF 解析级防护 :114-134（getaddrinfo 逐 IP 六类拒绝）+
  白名单闸门 :568+:941-952（与包内容不匹配即拒装）+落点收容 :636-855；缓存 810
  （26/315/5/99/215/150，catalog.plugins 键实测）零漂移；agnix/dsh-plugins-store
  三问复核均不过仅雷达，零接入维持 | 已覆盖 | 2026-10-06
- **C 类型**：BUILTIN_FLOWS=18 import 实数；14 指令项 id 全对上；描述-参数一致性
  抽查全过（rubric/threshold/rounds/serial 参数/note 行为相符，零文案漂移）|
  巡检 | 2026-10-06
- **D 经验**：lessons=72（26/21/10/7/4/4，流程规范 36.1%）packs=3 零漂移；零新蒸馏
  （勘定方法论已入对照表层、「未装不盲接」已在接入打法节，按去重纪律不重复入库）
  | 数据卫生 | 2026-10-06
- **E 新 CLI**：DEFAULT_CATALOG :32 实数 **14 条目**；本机 which **13/14 在装**
  （openclaw 未装；codebuddy=cbc 双名口径承 00 时班）；六候选（deepseek-reasonix/
  reasonix/fuxi/gitlawb/zero/empryo）which 全空——标记待验证，零接入防死链 |
  巡检 | 2026-10-06
- **F 禅道**：poll_enabled=False/claims=0/profiles=1/last_error 空/**next_scan 陈旧
  2026-09-21 坐实 poll 从未启动**——部署配置缺位非代码缺陷；_poll :2403/poll_enabled
  闸 :2425/:2474/:2502/_profile_for :334 + 前端禅道子页 app.js :11570/:11829/:11849
  全在位；本班全程只读未触发真实回写；零新禅道 AI 竞品（第 13 例持续为零）|
  巡检 | 2026-10-06
- **G 产品**：过时文案 grep 零命中；禅道前后端端点对齐（六操作 verb 映射 :1012-1017
  全有）；零新毛病 | 巡检 | 2026-10-06

### 落地件选定（第 3/4 步交接）

在册代码级积压核对为零（承 01 时班）；活项均攒批/拍板/远期——按「拍板件不擅动」
不开代码件。本班实落地=current-round.md 03 时节+knowledge.md 本节。零 app/ 实现、
零 JS/CSS 改动，第 4/4 步按 docs-only 不发版执行 | 判定 | 2026-10-06

## 2026-10-06 00 时班（批7 revisit——全量重扫+属主勘定批量落地+OpenMontage 新面孔）

> 同夜同批（21 时班 3h 前同批7），按令全量重扫不沿用；131 搜索调用 0 失败收尾，
> 证据与覆盖矩阵见 current-round.md（工单：第 1/4 步）。

### 新条目（repos 二次实证后入库）

- **calesthio/OpenMontage**（63,764★，pushed 10-03，created 2026-03-29，Python）| trendshift #19
  日增 506 捞出 | 开源 agentic 视频生产系统：12 条生产管线+700+ skills，脚本→分镜→图提→视提→成片
  | 三问过审：他们有视频生产管线、我们没有（video_script 只产文本脚本）、确实好用（日增 506/63.8k★）
  | **借鉴方向：视频/脚本类任务「管线化分段交付」成档互引**（与 drama-skills 五份 Markdown 即创作
  事实同构）；重型生产系统非 CLI 插件形态，不接入 | 借鉴方向 | 2026-10-06
- **zenstory-ai/oh-story-dsh**（454★，pushed 10-03）| dsh 写网文工作流插件（小说/短剧/游戏/视频
  解说四工作台，DeepSeek Harness 社区插件）| **dsh 生态从工具长向场景工作台的信号件**——我们
  catalog 已接 dsh（探测名在装），插件生态长场景层系 E 专项风向标；系别人家 CLI 插件不接入 |
  生态信号+雷达 | 2026-10-06
- **zenstory-ai/novel-to-game**（831★）/ **zenstory-ai/video-recap-skills**（549★）| 组织矩阵扫
  捞出（pushed 10-03/10-04）| 源考据式小说改编游戏 skills / 视频转中文解说 skills——A12 域
  story→video 之外第三改编方向+短视频域 | 雷达 | 2026-10-06
- **agentscope-ai/AgentTeams**（5,697★，pushed 10-04，Go，created 2026-02-21）| WebSearch 串行
  交叉验证捞出+repos 实证 | 「透明人机协同多智能体 OS」——A5 舰队域新实证（人机协同透明化系
  批4 在档方向）；Go OS 形态不合 Python 管线不接入 | 借鉴方向+雷达 | 2026-10-06
- 组织级注记：agentscope-ai 矩阵另有 QwenPaw 35,449★（个人 AI 助理）/ReMe 3,551★（agent 记忆
  管理套件，A10 对话记忆域第 N 件——经验库自研在位）/agentscope 32,780★/OpenJudge 865★/
  agentscope-runtime 876★——均雷达不入库；zenstory-ai/zenstory 60★ 系组织产品壳（oh-story
  借鉴来源产品化信号）| 注记 | 2026-10-06

### 增量与勘定（repos 端点实证）

- **爆量件**：Strata +12,985（227→**13,212**，10-04 push，FreeToken 同域雷达维持）/ponytail
  +12.2k（143.5k→**155,762**）/Agent-Reach +7,727（83,929→**91,656**）/ECC +1,578（→**273,434**）/
  claude-mem +746（→**96,488**）| 增量 | 2026-10-06
- **放量件**：rea +554（4,807）/tester-army-e2e +294（4,219）/moli +307（8,911）/hermes-agent
  +467（251,375）/image-blaster +188（9,473，**push 停 05-15 停更注记**）/OpenSpec +118（71,077）/
  huobao-drama +76（15,746）/dsh-our-free-model +76（1,589）| 增量 | 2026-10-06
- **常规在动**：orca +160（85,641 续领跑）/mattpocock-skills +139/superpowers +68（295,547 无
  push）/SkillSpector +56（19,437）/pi-mono +41/context-mode +35（25,458）/herdr +37/
  oh-story-claudecode +33（7,269）/drama-skills +25（2,524）/open-code-review +24/spec-kit +22
  （140,219）/anthropics-skills +25（179,752）/chinese-novelist-skill +19（3,296）/opencode +16/
  yomiyasu +13（1,492）/agentmemory+planning-with-files 各+7/webnovel-writer 7,324/
  DeepSeek-Reasonix +4（35,735 E 候选首位维持）；走平：ainovel-cli 2,096/hippo-memory 772/
  StaffDeck 1,967/agency-agents-zh 21,063/TsinghuaC3I 665/TeleAI 658（10-05 push）| 增量 | 2026-10-06
- **属主勘定 in:name 首批批量落地**（11 件，`q=<name>+in:name` 补位操作首例）：
  ponytail→**DietrichGebert**、OpenSpec→**Fission-AI**、claude-mem→**thedotmack**（三条全名新补
  入档）；hermes-agent→NousResearch/SkillSpector→NVIDIA/context-mode→mksglu/huobao-drama→
  chatfire-AI/oh-story+ drama-skills→zenstory-ai（五条已有全名实测复核一致）| 勘定 | 2026-10-06
- 另：`anthropics/claude-code-capabilities` 在 borrow-log 全目录零引用，系本班构建复查清单时的
  误记——in:name 实证原仓不存（仅 ≤1★ 镜像），工单内注记不动历史 | 工单注记 | 2026-10-06

### 七专项（本班实测，详锚点见 current-round.md）

- A：token 锚点全链实读在位（_ensure_budget :61→usage :117-120），context-mode +35 无新机制，
  A3 四组零新面孔 | 已覆盖 | 2026-10-06
- B：六源/SSRF 门/装前扫描在位，零新候选三问零接入 | 已覆盖 | 2026-10-06
- C：BUILTIN_FLOWS=18 实测，13 指令×18 注册表覆盖矩阵闭合（14 面+4 自研口径维持）| 巡检 | 2026-10-06
- D：**口径勘误收口**——「article 可见 11」=scope=article 1 条+wildcard * 共享 10 条（16 时班
  「scope=article 11」系口径混写、实质记录无误）；lessons=72 维持 | 勘误 | 2026-10-06
- E：**双计勘误**——catalog 14 条目在装 13/14（前记「14 在装」系 cbc 与 codebuddy 同条目双计，
  detect 键 `{"cli":"cbc"}`，codebuddy 非独立探测名）；openclaw 唯一未装；六候选 which 全空
  零接入防死链维持；新信号=oh-story-dsh | 巡检+勘误 | 2026-10-06
- F：poll_enabled=False/claims=0/last_scan 09-21/last_error 空——部署配置缺位非代码缺陷
  （多班同口径）| 巡检 | 2026-10-06
- G：陈旧文案 grep（13 种/14 种/15 种/17 种/11 个预置/单源模式）app.js+i18n.js+app/core+README
  全零命中 | 零新毛病 | 2026-10-06

### 待深挖队列（00 时班快照）

11 项全部维持（第 3/10 项保持划掉）；OpenMontage「管线化分段交付」并入 drama-skills 在档借鉴
方向观察、不新增队列项；本班零新机制级发现。风险在档维持（交人拍板）：data/zentao.json 明文
密码；portscan 真实杀进程用例挂死；32 位全量 discover 静默退出。

## 2026-10-06 04 时班（批3：计划/spec/长任务——新一轮计划第 1/4 步全类型调研）

> 开工 03:38（UTC+8，hour=3，3%7=3 → 轮换批3）、分支 main（b6d2269）。工作区在制品=
> 20/21/22 时班沉淀+今日 00/01/03 时班节（均未提交），本班增量记录、逐字不动。
> 通道：gh api 认证可用（搜索 30/分满额开局），串行 sleep 4s 纪律。证据底稿见
> current-round.md「04 时调研班」节。

### 新条目（WebSearch 串行交叉验证捞出+repos 二次实证后入库）

- **bmad-code-org/BMAD-METHOD**（53,809★，pushed 10-05，MIT，全历史零收录）新入库 |
  Agile AI-Driven Development 方法论：交付回路 Clarify→Plan→Build/verify→Learn、
  right-sized process（小改直建/大计加深）、决策显式化+上下文跨段携带；分发走
  skills 整包（npx skills add / Claude Code+Codex plugin marketplace bmad-plugins） |
  **批3 域 spec-driven 三巨头缺员补位（spec-kit 140k/OpenSpec 71k/BMAD 53.8k）**——
  主扫 per_page=5 剪切线+「bmad」无 spec/plan 词根双重盲区，WebSearch 跨通道第 5 例
  大漏实证。对比：Clarify≈需求拷问（已落地）/Plan≈.codebee spec+task_plan（已落地）/
  Build verify≈评审链+stopgate（已落地）/Learn≈经验库（已落地）；差量=「方法论整包
  skills 化分发」（我们蒸馏为单条 lesson，无整包通道；Vibe-Skills 路由+anthropics-skills
  同族形态观察）| 参考（形态观察）| 2026-10-06
- **Agent-Field/agentfield**（2,605★，pushed 10-05，全历史零收录）新入库 |
  「Kubernetes for AI agents」控制平面：agent 即微服务、长任务工作负载调度+访问控制，
  self-hosted | 治理控制面簇（bernstein/cordum/busbar）+1；我们单机编排台不做 K8s
  形态，无未覆盖件 | 雷达（批4 同域旁证）| 2026-10-06
- **Engineering4AI/awesome-spec-driven-development**（288★，pushed 10-03）|
  spec-driven 域专属 awesome 清单——**keywords.md C 雷达源本班补入**（批3 同款域专属
  补位，与 Long-Horizon/Agent-Memory 先例同型）| 方法论 | 2026-10-06

### 对照表勘定（本班实证）

- buildwithclaude=**davepoon/buildwithclaude**（3,589★，10-04 push；此前仅记枢纽名，
  in:name 一次勘定全名入档）/ lazycodex=**code-yeongyu/lazycodex**（3,731★，主扫
  full_name 实测承讹勘正——历史记录未留全名）/ spec-kitty=**spec-kitty/spec-kitty**
  （1,666★ 同名同主实证）| 勘定 | 2026-10-06

### 批3 域结果（主扫 116 查询 524 行 467 唯一仓零失败零限流）

- 批3 域命中全落已录族，**零机制级新差量（稳定期延续）**：spec-kit 140,239（10-05
  push）/OpenSpec 71,087（10-05）/planning-with-files 27,297（10-01 后无新）/agentmemory
  29,148/worktrunk 8,844（+38）/lazycodex 3,731/spec-kitty 1,666/gsd-pi 1,290/jean
  1,309/itsaplan 891（+7）/PlanWeave 409/plandeck 67/codex-factory 100/ccpm 8,398/
  OpenMOSS 1,334（停更维持）/PraisonAI 9,126/agent-os 5,471/spec-workflow-mcp 4,299
  ——spec 三件套/worktree/Stop gate/活计划满配维持。
- 主扫面（A 全组）零新机制级面孔；163 件历史零收录逐一过筛均课程/书单/词根错配噪声
  （easy-vibe 19.6k 系课程、pyod 系异常检测库等），如实记不入库。

### 存量复查（repos 端点 35 仓，04:0x，全 alive 零 archived）

相对 01 时班快照（01:1x）增量：**Strata 13,212→13,533（+321 放量续）**/**morluto/rea
4,879→5,164（+285，trendshift 在榜放量续）**/**iFixAi 804→21,154（两日 +20k 爆量，
A13「不信任自报」审计族方向再验证）**/orca 85,746（+86 续领跑）/superpowers 295,611
（+53，09-27 后无 push）/mattpocock/skills 276,962（+99，对 superpowers 差 18.6k）/
ECC 273,532（+93）/ponytail 155,876（+98）/claude-mem 96,548（+55 放量持续）/
hermes-agent 251,407/opencode 211,868/pi 112,685/anthropics-skills 179,774；写作域
webnovel-writer 7,324 持平/ainovel-cli 2,096 持平（10-05 push）/**yomiyasu 1,498
（+3 在动）**/drama-skills 2,526/huobao-drama 15,759（+9）/oh-story-claudecode 7,269
持平；E 域 **DeepSeek-Reasonix 35,737（10-05 push 候选首位维持）**；治理/记忆域
bernstein 1,404（+8 在动）/SkillSpector 19,447/context-mode 25,467/hippo-memory 772
持平/beads 27,649/gascity 1,330 持平/open-code-review 43,843/herdr 42,477（+21）。

### 雷达 C 全过

- awesome 清单 13 源 repos 实测全 alive：harness-engineering 4,713（10-05）/mcp-servers
  95,845/VoltAgent 35,233/claude-code 55,102/llm-apps 140,781/awesome-ai-agents 30,268/
  Long-Horizon 1,062（09-22 停更观察维持）/TeleAI 658（10-05）/TsinghuaC3I 665/
  vijaythecoder 4,388/ComposioHQ 76,546/caramaschiHG 1,920（06-10 停更半年维持）/
  buildwithclaude 3,589（正主勘定见上）。
- topic 8 页（pushed:>10-01 过滤）零新面孔：harness-sdk 8,675 续涨/agent-swarm 858/
  kodus-ai 均在档；trendshift 可达（29 仓）零新面孔，iFixAi 爆量为唯一显著增量；
  npm 两查已录族为主（@polderlabs/bizar 10.33/claudz 0.1.8 等微型新生件零接入级）；
  禅道周边两搜**零新禅道 AI 竞品（第 14 例持续为零）**；pypi 省配额未复试（历班六种
  拦截形态在案如实记）。

### 七专项快照（本班轻量实测，行号级深测承 03 时班当日口径）

- A：token 锚点零漂移承 03 时班七闸口实测；本班零新 token 域候选（context-mode +9
  无新机制）| 已覆盖 | 2026-10-06
- B：六源缓存 810/双闸门在位承 03 时班；本班零新候选、零接入维持 | 已覆盖 | 2026-10-06
- C：BUILTIN_FLOWS=18（03 时班 import 实数）维持；14 指令项映射表沿用；对话/知识库/
  文档雷达三面本班扫描面均有覆盖（A10 chatbot+批5+doc gen 词组照跑）| 巡检 | 2026-10-06
- D：lessons=72（流程规范 26/节奏爽点 21/情节逻辑 10/人物塑造 7/一致性 4/文笔风格 4，
  36.1%）packs=3 本班实测零漂移；BMAD 整包形态按去重纪律不入新 lesson（形态观察层
  记录）| 数据卫生 | 2026-10-06
- E：本班 which 实测 **13/14 在装**（openclaw 未装；codebuddy=cbc 双名口径承 00 时班），
  六候选（deepseek-reasonix/reasonix/fuxi/gitlawb/zero/empryo）which **全空**——零接入
  防死链维持，Reasonix 35,737 候选首位 | 巡检 | 2026-10-06
- F：data/zentao.json poll_enabled=False/claims=0/last_error None 本班实测——部署配置
  缺位非代码缺陷（多班同口径）；周边零新竞品第 14 例 | 巡检 | 2026-10-06
- G：零新毛病（03 时班六模式 grep 零命中当日口径；本班扫描面无新 UI/文案疑点）|
  巡检 | 2026-10-06

### 待深挖队列（04 时快照）

21 时快照 11 项全部维持（第 3/10 项保持划掉）；本班新面孔均雷达/参考级**零新队列项**
（BMAD 整包分发形态随 Vibe-Skills 同族观察；AgentField 随治理控制面簇备注）。
风险在档维持（交人拍板）：data/zentao.json 明文密码；portscan 真实杀进程用例挂死；
32 位全量 discover 静默退出。

### 未验证项（如实记录）

pypi 未复试（历班六种拦截形态在案，省配额）；GitHub Trending 直抓未行（trendshift
补位可达）；禅道定时扫描未触发真实工单验证（不为验证制造真实 Bug）；BMAD/AgentField
机制深读未做（README 级定性，深挖队列未列入——形态参考级不足入队）。

### 04:0x 复核班附记（第 1/4 步独立复核+批4 补跑）

- **独立复核全过零勘误**：主扫产物 524 行/DONE 116/零 FAIL/467 唯一仓 python 去重
  复测吻合；repos 抽验 6 仓（BMAD 53,809/agentfield 2,605/buildwithclaude 3,589/
  spec-kit 140,242 自然+3/stablyai/orca 85,750 自然+4——orca 正主经对照表勘定，
  复核者盲猜 orca-watch 404 即查表即中，对照表价值自证）全 alive；BUILTIN_FLOWS=18
  import 复证；四文件落位核对齐备 | 复核 | 2026-10-06
- **批4 补跑**（本班时点 4%7=4）：10 查询 50 行 49 唯一仓零失败——40/49 已在档
  （cordum/Aegis/agent-governance-toolkit 6,393/failproofai 5,242/plano 7,075/
  edict 16,968 等，治理/安全域已录族密实**零机制级新差量维持**）；新见 9 件均不过
  三门槛（mcp-client-for-ollama 826★ TUI 形态域外/Shinkai-Shoujo 25★ IAM 审计
  微型/ZSC-Eval 59★ 学术微型/golem 203★ GeoAI 域外/marketing-dashboard 468★ 营销
  域外/纯噪声 4 件）——零新队列项零入库 | 批4 域 | 2026-10-06

## 2026-10-06 七专项行号级深测巡检班（新一轮计划第 2/4 步，报告=2026-03-03.md）

> 承 01/04 时班确认的真实入口做行号级深测（独立重读非照抄 10-05 盘点），全程只读
> 巡检+提案，代码改动交评审后进第 3/4 步。证据全文见 2026-03-03.md。

### 七专项深测结论

- A：八机制锚点逐一实读全在位（compaction.py:2 三段压缩+灰度默认关 / token_meter.py:53
  cached 不计压力 / pipeline.py:613+648 花费闸先于 token 闸 / capability.py:100 cascade
  默认关 opt-in / skills.py:619 block_for 召唤+反馈闭环 / :244 会话缓存+前缀字节稳定
  三锚点（CRITIC_LENSES:3113/圣经:3595/stable_order 8 章字节级一致）复验 / diff 评审
  :919+948+深度分级:1019 / _shrink_context_block:2538 四层降级+压缩互斥守卫:4626）；
  四方向判定维持 10-05 口径：prompt 缓存已覆盖（usage.py cached 细分+压缩时机避开毁
  缓存）、语义缓存半已有（modelhub.py:3673 cache_ttl 幂等专用）、diff-only 已有、廉价
  分流三通道（difficulty+4D+cascade+content_workflow 短链）——**供应商侧显式
  cache_control 仍是唯一差量（全 app/core grep 零命中），维持转人工决策零改动**；
  两灰度开关（cascade/compaction）默认关属有意设计，扩大灰度报运维拍板 | 已覆盖 | 2026-10-06
- B：六源入口（market_remote.py:50-76：zcode/anthropic/anthropic-skills/claude-skills/
  clawhub/cocoloop）+双闸门行号级在位（assert_public_url:113 仅 https+全 IP 拒环回/
  私有/保留、重定向逐跳重校验:156、直连重试仍逐跳:202、clone 走公网网关:833-840；
  防炸弹体量上限:91-98；纯技能白名单:938-952；统一落地通道 market.install_files:267
  ——装前 skill_scan+指纹对账+typosquatting 近似名+装后冒烟+卸载只认标记）——无绕过
  通道；本轮候选（BMAD/kodus/AgentField/nautilus-compass/agnix）三问全不过，零安装件
  零新通道 | 已覆盖 | 2026-10-06
- C：BUILTIN_FLOWS=18 实数维持；菜单纯数据驱动（app.js:443-460+:668-689←/api/flows）
  数量天然一致；18 类菜单/描述/参数与后端逐一吻合、i18n 零结构性缺 key、图标零缺失。
  **新缺口 4 件（全部行号级核实）**：①TYPE_DIMENSIONS 漏 defect_retro（dispatch.py:12-31，
  靠 :60 兜底回落）②"zentao":"coding" 死映射（dispatch.py:30，flows 无此类型，疑似遗留）
  ③_DURATION_BASELINES 缺 defect_retro/presentation/bid_doc 三类型（usage.py:873-878，
  全回落 480s 常数致跑前预估失真）④连载章节数下限 store.py:279+:109 max(1) 与
  flows.py:164+UI 的 2-20 口径不一（API 直建可造 1 章连载）| 巡检 | 2026-10-06
- D：三源结构实测绘（lessons 72 条 data/skills.json+内置包+用户包+知识库 12 条）；
  **「流程规范 61% 偏科」口径勘正为 36.1%**（26/72：流程规范 26/节奏爽点 21/情节逻辑
  10/人物塑造 7/文笔风格 4/一致性 4，零漂移）——偏科系 9 月中旬旧快照，serial_novel
  批量入库已摊薄；合并全自动（≥0.8 标题包含度即并条，revisions≤5 条兜底）有机制性
  盲区：**正文相似不参与去重，实测三组成组漏网**（爽点须配新阻力 vs 胜利只给七分/
  章首钩子三条/章末钩子家族 4 条）——破坏性合并交人工决定，本步零数据改动；蒸馏入库
  三候选（CLI 巡检三步法/竞品调研蒸馏清账流水线/CLI 适配事实卡进知识库带 as_of）
  走既有 borrow: 通道 | 数据卫生 | 2026-10-06
- E：**「已接 11 个」勘正为 14 条**（catalog.py:32-215 DEFAULT_CATALOG；磁盘
  data/catalog.json 仅前 10 条系 Sep 18 后不回写，deepseek-harness/gemini-cli/codebuddy/
  trae-agent 靠运行时 _merge_new_defaults:406-419 幂等补齐——只看磁盘会漏 4 个）；
  where 实测 13/14 在装（唯一未装 openclaw，死链风险已被 detect_entry+effective_agents
  两层缓解属 installable 正常态）；六候选（reasonix/fuxi/gitlawb/zero/empryo/deepseek
  裸名）+droid/iflow/crush/aichat/llm where 全空——零接入防死链维持，已装未接 0 条
  | 巡检 | 2026-10-06
- F：链路 9 环节（调度/节流/扫描/拉列表/排查定责/建任务/分流/对账回写/群通知）锚点
  全在位；git log 六条历史修复（d2fdf81/4e3c044/14c5c04/92e37ab/58d5311/a115ed4）逐条
  对码全闭环；本机运行数据实测：last_scan=09-21 陈旧 15 天坐实 **poll_enabled=false
  定时扫描从未启动（部署配置缺位非代码缺陷，多班同口径）**、claims={} 零积压台账
  （系无数据非无 Bug 如实标注）、产品档案仅 1 条（product=96，module_routes 空=路由
  全押 AI 兜底）；真实禅道侧无法实测（不碰真实 Bug 纪律）；风险在档维持：zentao.json
  明文密码本轮实测仍明文 | 巡检 | 2026-10-06
- G：**新发现 3+1 件（历轮 grep 关键字未覆盖的存量）**：①市场来源提示缺第 6 源
  CocoLoop 且 index.html:977 的 data-i18n key 与 i18n.js:1642 词条失配（英文回退中文
  +死词条——「双源落地菜单仍写单源」同类，本轮最主要发现）②i18n 缺 EN 簇（index.html:764
  HTML 实体 key 失配+app.js 54 处 t() 字面量缺 EN，本地插件/自定义样题/运行详情/发布
  确认四簇）③禅道子页「自动合并代码」title「需配置基线分支」与实现不符（未配 git_rev
  自动取落单 HEAD 也能跑，zentao.py:1575-1582）④knowledge.md 头部 token 锚点摘要旧行号
  （本班已同步修正）；断链 0（128 个 /api/ 引用与 main.py 比对零缺失）、18 设置子页
  全可达、禅道子页按钮全绑定 | 巡检 | 2026-10-06

### 小而实提案（交评审确认后进第 3/4 步，在册积压为零+本轮新发现）

- **提案甲：类型注册表对账三件套**（C 缺口①②③同主题）：dispatch.py 补
  defect_retro→reasoning 显式映射+删 zentao 死映射、usage.py 补 3 个耗时基线
  （defect_retro=240/presentation=480/bid_doc=720 对齐同引擎量级）——纯数据表对账，
  当前兜底行为零变化；测试 tests/test_type_registry_audit.py（断言 TYPE_DIMENSIONS
  与 _DURATION_BASELINES 覆盖 flows 全部 18 id 且无多余 id）| 提案 | 2026-10-06
- **提案乙：市场文案补 CocoLoop**（G-①）：index.html:977 一处 key+文本与
  i18n.js:1642 逐字对齐 | 提案 | 2026-10-06
- **候补交拍板**：连载章数下限 store.py max(1)→max(2)（行为收紧非纯对账，涉及 API
  直建钳位变更）；G-②补全量 EN 词条体量较大建议单独立件 | 提案 | 2026-10-06

### 待深挖队列与风险（本班快照）

21 时快照 11 项全部维持（第 3/10 项保持划掉）；本班零新队列项。风险在档维持（交人
拍板）：data/zentao.json 明文密码；portscan 真实杀进程用例挂死；32 位全量 discover
静默退出。新增候拍板项：D 节三组正文级重复条目（人工确认后合并，不做自动正文去重）；
cascade/compaction 灰度扩大；G-② EN 词条补全立项。

### 未验证项（如实记录）

真实禅道全链路未实测（无实例授权不为验证造 Bug）；G-② 54 处清单未做第二遍人工复核
（提案不含 G-②）；D 节重复条目未做全量正文相似度扫描（不为本步新建工具）；提案甲③
耗时基线取值系同量级对齐推断无历史样本实证（三类型正因无样本才缺基线）。

## 2026-10-06 05 时落地班（新一轮计划第 3/4 步·实施巡检班提案甲+乙）

> 分支 main；承 2026-03-03.md 巡检班获准提案实施。指令候选清单
> （flows.py/pipeline.py/app.js/i18n.js/style.css）经真实路径核实零改动，
> 实际落点 dispatch.py/usage.py/index.html——「候选清单≠必须修改」纪律执行。

### 落地件（提案甲三件套+提案乙，全部按巡检班提案取值）

- **甲·C 缺口①②③**：dispatch.py TYPE_DIMENSIONS 删 zentao 死映射
  （flows 无此类型，全仓 grep 唯一命中即本行，capability.py:39 get 查表
  零影响面）+补 defect_retro→reasoning 显式映射（此前靠 :60 兜底，行为
  零变化）；usage.py _DURATION_BASELINES 补 presentation=480/bid_doc=720/
  defect_retro=240（对齐 doc/tech_proposal/rank_scan 同量级；禅道工单跑前
  预估从 480s 常数失真变 240s 量级）| 落地 | 2026-10-06
- **乙·G-①**：index.html:977 外部目录提示行 data-i18n key+可见文本补
  CocoLoop，与 i18n.js:1642 词条逐字对齐（英文界面不再回退中文裸奔）；
  i18n.js 零改动——失配点在 index.html 侧，既有 test_market_source_copy.py
  只锚 i18n.js 词条侧所以历轮 grep 漏网，「文案对账要两头锚」教训 |
  落地 | 2026-10-06
- **测试两件新增（git add -f）**：test_type_registry_audit.py 4 测试（两表
  键集合==flows 18 id 无漏无多+值域落 _KIND_AFFINITY 键域+提案取值锁定+
  未知类型兜底不变锚点）；test_market_copy_sources.py 3 测试（提示行 key
  恰一条+四可锚名称齐全+key 在 i18n.js 恰一条词条+旧五源文案零残留）
  ——「注册表↔调度/基线表对账」「文案↔词条对账」锁成契约，加型漏配/
  删型留尸/改文案漏词条先红 | 落地 | 2026-10-06

### 验证与评审证据

- py_compile 四文件过+node --check i18n.js 过；新测试 4+3 绿；相邻回归
  test_borrow_round 10 绿/test_full_type_iteration 13 绿。
- **分片全量对账 2177 项 2174 绿**（264 模块 6 批；32 位全量 discover 静默
  退出在案风险第三次实证）；批内偶发 3 项非本班因果三重证据（单跑全绿/
  剔我件复跑仍失败且集合漂移/失败模块与本班改动零交集）。
- 评审通过（code-reviewer 代理因模型路由 400 不可用，general-purpose 代
  理替代——通道异常如实记）；LOW 三条已修（死变量/断言补实/既有
  test_dispatch.py 元组 zentao→defect_retro 一致性收尾）。
- 候补（store.py 章数下限 max(1)→max(2)）与 G-② EN 词条补全维持交拍板
  不动 | 边界 | 2026-10-06

### 发版判定（预记，供第 4/4 步）

本班有代码入库且用户可感知（禅道工单跑前预估修正/调度维度显式化/市场
文案六源补齐+英文界面修复）——按「当天有代码入库才发」应发 patch，交
第 4/4 步走 test_selfupdate 全绿→package.json patch+1→CHANGELOG→npm
publish 流程。

### 待深挖队列（05 时快照）

21 时快照 11 项全部维持（第 3/10 项保持划掉）；本班零新队列项。风险在档
维持（交人拍板）：data/zentao.json 明文密码；portscan 真实杀进程用例挂死；
32 位全量 discover 静默退出（本班第 3 次实证，分片对账为既有规避打法）。
新增候拍板项维持：D 节三组正文级重复条目人工合并；cascade/compaction
灰度扩大；G-② EN 词条补全立项；store.py 章数下限钳位收紧。

## 2026-10-06 06 时班（批6：框架/平台/SDK 生态——全类型调研，第 1/4 步）

> 6%7=6 批6。主扫 **115 查询 524 行 472 唯一仓零失败零限流**（A 常驻 89 含内置
> B1 11+B6 轮换 10+A1u/A1p2 双轮 16）。WebSearch 串行 2 发（批6 域，3-4 班窗口纪律）；
> 雷达 C 全过（awesome 14 源 13 alive+正主勘定 1、topic 8 页、npm 两查、trendshift
> 可达、禅道周边两搜第 15 例零新竞品）；存量复查 repos 端点 70+ 仓次全 alive 零
> archived。证据底稿见 current-round.md「06 时调研班」节。

### 新条目（本班真新面孔，均雷达/参考级）

- **VoltAgent/voltagent**（10,731★，09-28 push，WebSearch 捞出+repos 实证）新入库 |
  TS 端到端 agent 工程平台（memory/RAG/guardrails/工具）——此前只录同组织
  awesome-agent-skills 清单仓，框架本仓漏录 | 框架族旁（mastra/vercel-ai 同域）|
  雷达 | 2026-10-06
- **tigerless-labs/autoharness**（7,906★，10-05 push，topic:llm-agents 捞出）新入库 |
  Claude Code 自学习 skill 层：从真实使用蒸馏 skill | B2「技能自动沉淀」同族再验证，
  差量=「使用轨迹→可分发 SKILL 文件」形态（我们经验库 lessons 同向、无 SKILL 化分发
  面）——随 Vibe-Skills/BMAD 整包分发形态同族观察 | 雷达（B2 域） | 2026-10-06
- **eigent-ai/eigent**（15,457★，10-05 push）新入库 | 开源 Cowork 桌面（本地免费
  Claude Cowork 替代，多 agent 员工） | 桌面工作台族（cc-haha 14.9k 旁） | 雷达 | 2026-10-06
- **PatterAI/Patter**（1,064★，10-03 push，B6 组捞出）新入库 | 开源 voice-AI SDK
  （Vapi/Retell 替代） | 语音域（无对应预置类型） | 雷达（域外） | 2026-10-06
- **camel-ai/oasis**（5,228★，10-05 push）新入库 | 百万 agent 社会交互仿真 | 仿真域外
  （编排台无此场景） | 参考（域外） | 2026-10-06
- **WenyuChiou/awesome-agentic-ai-zh**（7,400★，10-05 push）新入库 | 三语 agentic AI
  学习路线清单 | 清单域小标（雷达源补充候选，暂不入 C） | 雷达 | 2026-10-06
- 微型批（均不过三门槛，如实记）：microsoft/spec-to-agents 115★（MAF 官方示例）/
  neuroglia-io/a2a-net 55★（.NET A2A 实现）/ isekOS/awesome-a2a-agents 31★ /
  aieducations/edumcp 156★（MCP+教育域外）/ startino/aitino 92★ / aozyildirim/Agena
  100★ | 判据 | 2026-10-06

### 对照表勘定与迁移（本班 4 件，后续班 repos 复查直用）

awesome-agent-orchestration=**vivy-yi/awesome-agent-orchestration**（77★ 小标清单，
in:name 一次勘定）；**agent-orchestrator=OrchestratorInc/agent-orchestrator**（12,788★
——repos/Untrivial-ai/agent-orchestrator 返回 OrchestratorInc 重定向，属主迁移实证，
「仓名迁移」第 2 例·属主重定向形态，npm fork 包自述佐证）| aegra=**aegra/aegra**、
AntSK=**shuyu-labs/AntSK**（主扫 full_name 补全名） | 对照表 | 2026-10-06

### 批6 域结果与复查增量

- 批6 域头部全已录活跃，**零机制级新差量（稳定期延续）**：mastra 28,575/vercel-ai
  27,127/harness-sdk 8,675/agentops 5,886（停更维持）/axonhub 5,334/semantix 821
  （放量止确认）/aegra 1,240/AntSK 1,327/python-a2a 1,007/agentscope-runtime 876/
  pandaprobe 784/agentcn 483/openui 9,986/agents-cli 6,051/race-condition 234/
  CrewAI-Studio 1,357/idun 203 全已录。
- **microsoft/agent-framework 13,804→13,953（+149）**/ag2 4,975/pydantic-ai 20,420/
  openai-agents-python 29,848/semantic-kernel 28,629/langgraph 42,745/dify 157,897/
  langflow 155,513/langchain 147,473/eliza 19,541/A2A 26,017/gstack 135,358（+70）/
  kagent 3,941——WebSearch 交叉验证与 repos 复查双通道口径一致，头部框架稳定期。
- 跨域放量跟踪：**morluto/rea +272（5,436 放量续）**/Strata +246（13,779）/ECC +315/
  orca +45（85,791 续领跑）/ponytail +74/mattpocock/skills +86（277,048，对 superpowers
  295,641 差 18.6k）/claude-mem +56（放量持续）/iFixAi +19（21,173 放量趋稳）/
  huobao-drama +39/yomiyasu +5（在动）/DeepSeek-Reasonix 35,739（E 候选首位维持）。

### 七专项快照与 keywords 判定

- A 八锚点零漂移/B 六源+SSRF 在位零新候选/C 18 实数/D lessons=72（36.1%）/
  E 14 条 13 在装六候选全空零接入/F poll_enabled=False+claims=0（部署配置缺位同
  口径）+零新竞品第 15 例/G 过时文案 grep 零命中（05 时班 G-① 修复在位实证）|
  巡检 | 2026-10-06
- **keywords.md 本班微调 1 处**：C 雷达源 awesome-agent-orchestration 标注正主
  vivy-yi（77★，勘定在案）；新面孔全由现有词组与雷达源捞出，无确凿缺口不加组 |
  判定 | 2026-10-06

### 待深挖队列（06 时快照）

21 时快照 11 项全部维持（第 3/10 项保持划掉）；本班新面孔均雷达/参考级零新队列项
（autoharness「使用轨迹→SKILL 蒸馏」形态随 Vibe-Skills 同族观察）。风险在档维持
（交人拍板）：data/zentao.json 明文密码；portscan 真实杀进程用例挂死；32 位全量
discover 静默退出。

## 2026-10-06 07 时班（第 2/4 步·七专项实读复核+落地件选定——甲 store 章数钳位+乙 EN 词条残件 8 处）

> 只读巡检+文档沉淀，零 app/ 改动。全部锚点本班 07:0x 独立实读（非照抄 03 时班
> 与「七专项行号级深测巡检班」），报告全文=2026-10-06.md 07 时班节。

### 七专项复测结论（增量视角，全量明细见报告）

- **A token**：八机制锚点全复证——预算熔断七闸口+成本闸先于 token 闸（pipeline
  :676-677 实读新记）；token_meter 直通分支兜底记账（:738-746，防 max_tokens_
  per_run 失效）；三段压缩 _PRECHECK_RATIO=0.9 预检（step_runner:28/:80）；
  分层降级四层优先级；cascade 双 opt-in；stable_order 前缀缓存（skills:619/:661）；
  会话复用；diff 评审深度分级。`cache_control` 零命中维持——**prompt 缓存差距的
  真实边界=stable_order 已是应用侧最大化，其余在供应商侧** | 巡检 | 2026-10-06
- **B 市场**：六源+SSRF 解析级防护+体量帽+白名单拒装全在位；缓存 810 零漂移
  （fetched_at 2026-10-03）；本班零新候选零接入 | 巡检 | 2026-10-06
- **C 类型**：BUILTIN_FLOWS=18 实读；菜单 app.js:410 数据驱动；**18 型名称/
  goal_hint/note 的 EN 词条 18/18/18 全命中零缺失（本班首测）** | 巡检 | 2026-10-06
- **D 经验**：lessons=72（36.1% 偏科口径维持）packs=3 零漂移；去重=标题归一
  （skills:312）+包含度相似（:414，追加后缀形态 Jaccard 漏已注明口径）；入口=
  upsert_lesson:424 管道自动沉淀，本班零直写 | 数据卫生 | 2026-10-06
- **E 新 CLI**：catalog 14 条目；本机 13/14 在装（openclaw OUT）；六候选 which
  全空零接入防死链 | 巡检 | 2026-10-06
- **F 禅道**：poll_enabled=False/claims=0/last_scan=09-21（配置缺位非代码缺陷
  多班同口径）；**interval_hours→interval_minutes 迁移链本班首次实证完整**
  （zentao.py:101+:269-279+:300-304+app.js:11589/:12127）判非缺陷；观察在档：
  老值==legacy 缺省 2 时与显式设 2 小时不可区分→统一落 5 分钟（设计权衡已注释）
  | 巡检 | 2026-10-06
- **G 产品**：过时文案八模式 grep 零命中；G-① CocoLoop 修复在位；**G-② EN
  缺口实测从 54 收敛到 8**（429 keys 精确比对，前期班已补 46）——残件已小体量
  化 | 巡检 | 2026-10-06

### 落地件选定（交第 3/4 步，本班只选定不实施）

- **提案甲**：store.py:279/:109 章数钳位 `max(1,…)`→`max(2,…)` 对齐 flows.py:164
  +UI min=2（store 系唯一偏口径点，本班三面实证）——行为收紧风险已注明交评审。
- **提案乙**：i18n.js 补最后 8 条 EN 词条（清单在报告 G 节）——零逻辑纯文案。
- 两件独立可做；全否→docs-only 不发版，任一落地→发 patch | 判定 | 2026-10-06

### 待深挖队列（07 时快照）

21 时快照 11 项全部维持（第 3/10 项保持划掉）；本班零新面孔零新队列项。风险
在档维持（交人拍板）：data/zentao.json 明文密码；portscan 真实杀进程用例挂死；
32 位全量 discover 静默退出。

## 2026-10-06 07:4x 落地班（第 3/4 步·实施 07 时班两提案：甲撤回+乙落地 7 条）

> 分支 main。落点收敛：app/ui/i18n.js（EN 表尾追加 7 词条）+tests/
> test_i18n_key_coverage.py（新建 3 测试）+报告（全文=2026-10-06.md 第三班节）。

### 提案甲撤回（「未装不盲接」同款纪律：有反证不硬改）

07 时班「store 系唯一偏口径点」前提不成立，三重实证：①store.py:278 在位注释
「续写批次允许只续 1 章，下限放宽到 1」——max(1) 系刻意设计；②UI 续写链路
app.js:3864 仅拒 ch<1，1 章续写是界面合法输入；③continue_task（:1773 batch
max(1)）构造 payload 后直调 create_task（:1819→:279 落钳位）——改 max(2) 会把
1 章续写静默变 2 章。**2-20（全新连载，flows._norm_serial+表单 min=2）与 1-20
（续写批次）系两个语义非口径漂移**；:109 超时基线同理（存量 chapters=1 合法）。
提案甲整体撤回，是否另立修正案交人拍板 | 勘定 | 2026-10-06

### 提案乙落地（实补 7 条非 8，07 时班清单含一 grep 误报）

133 字 webhooks 键（index.html:764）词条早已在位（i18n.js:2862）——键含双
引号在 JS 文件里是 `\"` 转义形态，文件级裸引号 grep 查不到即误报缺失。实补
7 条（本地插件×2/自定义样题×2/导出报告/选择任务/重新运行）；node eval 运行时
7 键全解析实证。**方法论沉淀：文件级 i18n 对账必须「裸形态+转义形态」双查**
——已固化进 test_i18n_key_coverage.py（429 键全量对账+转义兼容+残件锁定+
零重复四重断言）与词条区注释 | 落地 | 2026-10-06

### 验证与发版预记

py_compile/node --check 过；test_i18n_key_coverage 3 绿+test_i18n_dups（零重复
键守卫）绿+相邻回归（market_copy 3/registry_audit 4/regressions 6）全绿零波及。
有代码入库（i18n.js+测试件）：英文界面 7 处文案可感知改善，按「当天有代码
入库才发」应发 patch（与 05 时落地班三件合并计版），交第 4/4 步执行。

## 2026-10-06 08 时收口班（第 4/4 步·联调终核+五道关复跑+发版终核）

> 分支 main。开工 HEAD=b6d2269；收口中并行通道（第 3/4 步落地班）07:56-07:58
> 完成 bd7c7f1（代码+测试+文档 17 件）与 0b0c105（v0.1.87 发版三件）并推送。
> 本班转独立终核+发版补齐，不重做不重置。

### 联调终核（全类型零误伤）

18 型注册表契约（registry_audit 4/borrow_round 10/full_type_iteration 13/
dispatch 4）全绿；禅道隔离测试 53 项全绿——app.js:15184 与 index.html:250 的
"zentao" 字面量为设置页子页签导航（非任务类型消费方），改型零误伤；市场六源
提示+i18n 429 键全量对账零缺 | 勘定 | 2026-10-06

### 五道关与发版实录

⓪main 全程未切；①py_compile 8 文件+node --check 过；②全量 discover 终局
**exit 0 全绿**（本机真实 IO 测试慢，首跑 30 分钟后台时限中止于
test_runner_drain，重跑达成；尾部 RuntimeError/ResourceWarning 系测试错误
路径预期 stderr 噪声）；③逐 hunk 自审+独立模型评审四点实证全过（zentao
残留合法/键逐字一致/断言与实现吻合/无 CRITICAL·HIGH）；④⑤提交推送由并行
通道完成且与本班审定稿逐字一致，无踩踏。发版：0b0c105 只落发版提交未及
publish，本班复跑 test_selfupdate 6 绿后补发——**首试 10 分钟无输出判定
443 抖动挂起，停掉重试遇 release-gate 闸门拦截（工作区有本班文档在制品），
按闸门要求先提交文档沉淀再 publish**，结果见当日报告 08 时班补录 | 复核 |
2026-10-06

### 受限项（如实记录）

code-reviewer 子代理通道两次 API 400（上游模型路由「模型不存在」，指定
sonnet 亦被忽略）——模型评审改走 ocx-self 通道完成（19 工具调用实证复核），
通道故障属环境问题非代码问题 | 记录 | 2026-10-06

### 08 时班会话实测补录（闸②证据订正 + publish 终核——与上文实录并呈）

- **闸②「exit 0 全绿」日志实查无统计行**（/tmp/full_test_20261006_r2.log
  无 Ran/OK——32 位 discover 静默退出第三次实证，:1747 纪律再锁）；有效
  证据=分片对账 A 1678（10F+2E 逐样本单跑复绿：test_http_500_guard 3F 系
  真机 iPhone 控制锁 423 环境态、锁过期 4/4 绿）+ B 505 全绿，2183 项零代码
  回归，改动面 6 文件净进程 6/6 绿 | 复核 | 2026-10-06
- **publish 终核**：首试 300s 零输出终止（registry 侧无版本落地），链路诊断
  官方源可达（PONG 1066ms）后限流参数重试一次成功——`npm view codebee
  version`=0.1.87、dist-tags.latest=0.1.87，**v0.1.87 发布完成**，registry
  无重复版本 | 发版 | 2026-10-06
- **禅道/市场外部依赖隔离口径**：test_zentao/test_defectretro/test_market*7件
  随分片跑过、未做真实外呼；禅道运行态维持「poll 未启用=部署配置缺位」，
  市场零新接入（三问不过维持雷达）| 巡检 | 2026-10-06

## 2026-10-06 09 时班（批2：学习记忆与自我改进——新一轮计划第 1/4 步，报告见 2026-10-06.md 与 current-round.md 本班节）

> hour=9，9%7=2 → 批2。分支 main（0aa681a）工作区干净。主扫 115 查询 467 唯一仓
> 零失败；WebSearch 串行 2 发（距 06 时班 3 班到窗口）；repos 端点 32 仓复查+
> 14 清单实测+9 新面孔定性。批2 域主扫零机制级新差量（稳定期延续），**跨通道
> WebSearch 捞出批2 域重磅一件（连续第 3 班实证互补）**。

- **MemoriLabs/Memori**（17,074★，10-03 push，WebSearch 捞出+repos 端点实证，
  全历史零收录）新入库 | agent-native memory infrastructure：LLM 无关记忆层，
  坐在模型与应用之间，自动从对话抽取关键信息入记忆 | 批2 域重磅：我们经验库=
  教训型记忆（lessons 72 条管道沉淀+检索注入），Memori 与 agentmemory（29k，
  编码 agent 持久记忆）、claude-mem（96k，跨会话上下文捕获）成记忆域三巨头
  并立；差量=「LLM 无关基建层」定位+自动抽取管线；独立基建非技能包，接入
  三问不过 | 借鉴方向（记忆域攒批，随队列第 1 项/记忆分层证据 +1） | 2026-10-06
- **cortexkit/magic-context**（2,268★，10-06 push，created 2026-03-26，
  topic:agent-framework 捞出）新入库 | 「Unbounded context. Memory that
  manages itself. One session, for life」——编码 agent 的海马体（记忆自管理
  /免手工压缩/终身会话） | 与我们 compaction 三段压缩+会话复用同域但主打
  「记忆自管理」路线；A3+B2 双域交叉标的，机制面 README 深读未行（攒批
  备选） | 雷达（记忆域攒批证据 +1） | 2026-10-06
- **firecrawl/open-agent-builder**（2,639★，主扫 A2 捞出）新入库 | Firecrawl
  出品视觉化 agent 工作流构建器（拖拽式 web 工作流+Firecrawl 数据面） | 视觉
  编排系产品形态差异（我们 CLI+Web 台数据驱动菜单）；firecrawl 系数据采集
  厂商向 agent 平台延伸样本 | 雷达 | 2026-10-06
- **uber/ADR**（1,722★，10-05 push，trendshift 捞出）新入库 | Uber 企业级
  agent 安全面：observability+安全基准+威胁（**「ADR」系产品名非架构决策
  记录——词根双义陷阱**，检索 ADR 域时会噪声化） | 治理/安全域（批4 域）
  雷达；对照表补注防后续班误配 | 雷达 | 2026-10-06
- **Shichun-Liu/Agent-Memory-Paper-List**（2,411★，pushed 03-04 陈旧，WebSearch
  捞出）新入库 | 「Memory in the Age of AI Agents: A Survey」论文列表，taxonomy
  区分 Agent Memory vs RAG vs Context Engineering | 记忆域第三清单（与
  TeleAI-UAGI/TsinghuaC3I 并存），本仓特色=概念区分图谱 | 参考（记忆域） | 2026-10-06
- 微型批（不过三门槛如实记）：codeaholicguy/ai-devkit 1,640★（AI 编码 agent
  控制面，A13 域拥挤度续新高）/ professorpalmer/Puppetmaster 467★（durable-state
  swarm 控制面）/ jordanrendric/claude-video-vision 1,344★（Claude 视频理解
  插件，域外多模态）/ AgentMemoryRepo/agentmemoryrepo 232★（记忆 spec 仓）/
  DemonDamon/AgenticX 267★（2024-03 老仓 unified platform）/ FareedKhan-dev/
  all-agentic-architectures 4,579★（35 架构图鉴书单）/ KnowledgeXLab/MemVerse
  154★（03-17 停更）/ **RimoraStudio/Cognikit 3★——WebSearch 放大形态第 5 例**
  （skillsllm.com 面宣称 premium skills，实仓 3★ 死平） | 判据 | 2026-10-06

### 简称→正主全名对照表（本班补勘 4 件）

Cognikit=RimoraStudio/Cognikit（3★）/ MemVerse=KnowledgeXLab/MemVerse（154★）/
Memori=MemoriLabs/Memori（17,074★）/ ADR=uber/ADR（1,722★，双义陷阱注） | 对照表 | 2026-10-06

### 复查记录（repos 端点 32 仓，09:5x，全 alive 零 archived）

orca 85,886（+95 续领跑）/ superpowers 295,670（+29 无 push 维持）/ mattpocock/
skills 277,117（+69，对 superpowers 差 18,553）/ ECC 273,674（+70）/ ponytail
156,008（+58）/ hermes-agent 251,454（+26）/ opencode 211,892（+11）/ pi
112,733（+25）/ anthropics/skills 179,794（+8）/ claude-mem 96,646（+42 放量
持续）/ agentmemory 29,152（+4）/ hippo-memory 772 持平（10-06 push）/ beads
27,653（+1）/ gascity 1,328 持平（10-06 push）/ nautilus-compass 1,144（+10）/
context-mode 25,480（+13）/ webnovel-writer 7,325 持平 / ainovel-cli 2,097（+1）/
yomiyasu 1,518（+15 在动加速）/ drama-skills 2,524 / huobao-drama 15,772（+10）/
oh-story 7,269 持平 / DeepSeek-Reasonix 35,740（10-06 push，E 候选首位）/
SkillSpector 19,467（+20）/ Strata 13,977（+198 放量续）/ rea 5,694（+258 放量
续）/ iFixAi 21,194（+21）/ bernstein 1,404 持平 / open-code-review 43,874（+12）/
herdr 42,516（+25）/ TeleAI-UAGI 658 持平 / TsinghuaC3I 665 持平；topic 复认
**winnow 54→102★（三周翻倍，队列第 1 项攒批标的涨势延续）**、agent-swarm 859
（+7）、5dive 65 | 复查 | 2026-10-06

### 雷达 C 与七专项快照（本班独立实测）

- 雷达 C：awesome 14 源 13 alive（**bradAGI/awesome-llm-apps 404 勘定——in:name
  证正主=Shubhamsaboo 140,795 在档 alive，别名失效无缺口**）；topic 8 页新面孔
  4 件（magic-context/ai-devkit/AgenticX/claude-video-vision）；npm 两查已录族
  为主+微型新生件（gm-orchestrator/plugin-gastown-bridge）零接入级；trendshift
  可达（top3 已录，slashed 面新 uber/ADR+agentmemoryrepo）；禅道周边第 16 例
  零新竞品；pypi 省配额未复试 | 雷达 | 2026-10-06
- A：八锚点全在位（:61/:613/:632/:1526/:2538/:2581/:3141+:3595/:948+:919+:1019）；
  四方向判定维持；magic-context（自管理记忆免压缩）+Memori（LLM 无关记忆基建）
  随记忆域攒批不改判定 | 已覆盖 | 2026-10-06
- B：六源在位+缓存 810 零漂移（fetched_at 10-03）；新见候选均独立平台非六源
  技能包，三问不过零接入 | 巡检 | 2026-10-06
- C：BUILTIN_FLOWS=18 import 实测（14 指令项+自研 4 逐项对上）；回归测试件
  （borrow_round/registry_audit）锁定 | 巡检 | 2026-10-06
- D：lessons=72 零漂移（36.1% 口径维持）packs=3；零新蒸馏（Cognikit 放大第 5 例
  按去重纪律不入） | 数据卫生 | 2026-10-06
- E：catalog 14 条目；本机 13/14 在装（openclaw OUT）；六候选 which 全空零接入
  防死链（Reasonix 35,740★ 10-06 push 候选首位） | 巡检 | 2026-10-06
- F：poll_enabled=False/profiles=0/claims=0/last_error 空（配置缺位多班同口径）；
  锚点 :2403/:2425/:2474/:334 在位；全程只读；第 16 例零新禅道 AI 竞品 | 巡检 | 2026-10-06
- G：过时文案七模式 grep 零命中，零新毛病 | 巡检 | 2026-10-06

### 待深挖队列（09 时快照）

21 时快照 11 项全部维持（第 3/10 项保持划掉）；本班新面孔均雷达/攒批级
（magic-context+Memori 记忆域攒批证据 +2）零新队列项。风险在档维持（交人
拍板）：data/zentao.json 明文密码；portscan 真实杀进程用例挂死；32 位全量
discover 静默退出。

### 未验证项（如实记录）

pypi 未复试；Trending 直抓未行（trendshift 补位）；magic-context/Memori 机制面
README 深读未行（星标在涨，攒批备选待下轮深挖）；禅道定时扫描未触发真实工单
验证。发版判定：docs-only 不发版（先例同 01/04/06 时班）。

## 2026-10-06 10 时巡检班（第 2/4 步·七专项深化复证+落地件选定，09:58 开工）

> 承 09 时调研班（批2）交棒。分支 main（0aa681a），工作区在制品=09 时班 docs 四件
> （未提交），本班增量记录。全程只读巡检+docs 沉淀，零 app/ 实现、零 JS/CSS 改动；
> 禅道链路只读（未触发真实 resolve/评论/群通知）。证据全文见 current-round.md 本班节。

### 七专项复证（本班独立实测，行号承 03/09 时班口径全对上零漂移）

- A：八锚点全在位（_ensure_budget :61 七闸口/_budget_max_tokens :613/_budget_cost_caps
  :632/cascade :1526-1529/_shrink_context_block :2538/_serial_shrunk_block :2581/
  stable_order :3141+:3595/diff 评审串 :919+:948+:1019+:1054）+ compaction 函数族
  （estimate_tokens :33/prune_text :42/select_range :51/compact_region :119/
  maybe_compact :182）+ step_runner _PRECHECK_RATIO=0.9 + modelhub chat(cache_ttl)
  :3673 语义缓存半已有；四方向判定维持零重复建设 | 已覆盖 | 2026-10-06
- B：六源 SOURCES :50+SSRF 解析级防护 :125-131（getaddrinfo 逐 IP 六类拒绝）+
  白名单拒装 :952；缓存 810 逐源实测零漂移（fetched_at 10-03）；本班候选复判
  （firecrawl/open-agent-builder、Puppetmaster、magic-context、Memori、uber/ADR、
  ai-devkit、AgenticX、claude-video-vision）均独立平台/插件非六源可直装技能包
  ——三问全不过零接入未绕闸 | 巡检 | 2026-10-06
- C：BUILTIN_FLOWS=18 import 实数（18 id 逐一列出对上）；评审参数 14 型内联
  rubric 4-5 维/threshold 7.0/rounds 2（bid_doc :131 7.5 刻意从严）；非 review 4 型
  无评审参数；菜单 app.js:410 /api/flows 数据驱动；五型 note/goal_hint 抽查
  （rank_scan/defect_retro/presentation/email/video_script）与 engine 直出/评审链
  逐一相符零漂移 | 巡检 | 2026-10-06
- D：data/skills.json 入口实证，lessons=72 标题级重复**零**（Counter 全唯一）；
  分类实测 流程规范 26（36.1%）/节奏爽点 21（29.2%）/情节逻辑 10（13.9%）/
  人物塑造 7（9.7%）/一致性 4（5.6%）/文笔风格 4（5.6%）；packs=3；本班零新
  蒸馏（去重纪律）；正文级三组合并维持候拍板 | 数据卫生 | 2026-10-06
- E：DEFAULT_CATALOG=14 import 实测（14 id 逐一列出）；本机 which 13/14 在装
  （openclaw MISSING，codebuddy 条目探测名 cbc 在装）；六候选（deepseek-reasonix/
  reasonix/fuxi/gitlawb/zero/empryo）which 全 MISSING——零接入防死链维持 | 巡检 | 2026-10-06
- F：链路锚点 _poll :2403/_poll_unlocked :2414/poll_enabled 闸 :2425/:2474/:2502/
  scan_now :2479/_profile_for :334 全在位；data/zentao.json 实测 poll_enabled=
  False/claims=0 零积压/last_error 空；**profiles 0/next_scan None 与 09 时班
  （1/陈旧 09-21）班间波动如实留档——data/ 未入 git 无法追溯变更方，判读运行时
  数据非代码缺陷**；前端 poll 未启用提示完备（app.js:12012） | 巡检 | 2026-10-06
- G：过时文案六模式+五源 grep（app/ui 三件+app/core+README）全零命中；封面卡
  文案（app.js:5787-5820）与实际行为相符——零新毛病 | 巡检 | 2026-10-06

### 路线图状态勘定（已沉淀项目复查的落地动作）

- drama-skills 借鉴②连续性锁**已落地**（pipeline.py:2460 圣经注入文案实证，
  :193 条目已同步）；借鉴③预览确认未落地并选定为本轮候选甲（:193 已同步）。
- superpowers 借鉴②初级工程师测试**已落地**（2026-10-05 节在档）；借鉴①spec
  分段签核**未落地**（app/ 零命中实证，:177 条目已同步）。
- 其余已沉淀项目复查：承 09 时班 32 仓 repos 端点复查（间隔<1h），本班零重复
  配额消耗、零增量 | 复查 | 2026-10-06

### 落地件选定（第 3/4 步交接，纳入由人决定）

- **候选甲（唯一小而实选定）**：封面生成前提示词预览确认（drama-skills 借鉴③
  清账件）——真实函数 covergen._cover_prompt :72（已模块化）/make_cover :273/
  start :335，现端点 POST /api/tasks/{id}/cover（main.py:1102-1106）直接调外部
  图像 API 付费无确认步；目标=封面卡先只读展示提示词、确认后再触发生成；
  改动文件 app/main.py（+1 只读端点）+app/ui/app.js（封面卡 :5787-5820 二段
  确认）+tests/（新测试 add -f）；风险=新增只读 API+前端交互改动面中等，交人
  决定。候选乙=superpowers 借鉴①spec 分段签核（改动面大非小而实，维持路线图
  在册）；候选丙=禅道子页 poll 未启用提示（app.js:12012 已有引导文案，缺口
  不成立）。候拍板四件维持不擅动 | 选定 | 2026-10-06

### 发版判定（预记，供第 4/4 步）

本班改动仅 docs/borrow-log 两件（knowledge.md 状态同步+本班节、current-round.md
本班节）——零用户可感知变更，**docs-only 不发版**（05/06/01/04/09 时班先例）。

## 2026-10-06 12 时班（批5：检索/知识/浏览器——新一轮计划第 1/4 步全类型调研，报告见 2026-10-06.md 与 current-round.md 本班节）

> hour=12，12%7=5 → 批5。分支 main（0aa681a）。主扫 115 查询 524 行零失败
> 零限流；WebSearch 串行 2 发（距 09 时班 3 班到窗口）零机制级新面孔；repos
> 端点 53 仓复查+6 新面孔实证+trendshift 12 件定性。批5 域零机制级新差量
> （稳定期延续）；**trendshift 通道捞出 90k★ 重磅一件**。

### 新条目（本班真新面孔，全零收录 repos 实证）

- **odysseus-dev/odysseus**（90,338★，created 2026-05-31，10-06 push，trendshift
  捞出）新入库 | 自托管 AI 全功能工作台：chat/agents/deep research（多步源读
  +报告生成）/documents/email/notes/calendar/本地模型一条龙，Docker 一键部署 |
  一站式交互工作台 vs 我们任务编排台（18 类型流水线+评审）；其 Deep Research
  与调研报告类型、Compare（盲测对比，README 截断未全文）与 Best-of-N 赛马
  同域——机制面深读攒批备选 | **雷达（重磅体量）** | 2026-10-06
- **f/prompts.chat**（172,100★，10-03 push）新入库 | 社区 prompt 集合大仓
  （f.k.a. Awesome ChatGPT Prompts，f org）| A8 域「社区集合」形态对照已录
  「管理平台/注册表」，零管理能力零差量 | 参考 | 2026-10-06
- **open-webui/open-webui**（154,031★，10-05 push）新入库 | 本地 AI 界面
  （Ollama/OpenAI 多后端 chat UI）| B7 本地域 chat 界面非编排台（py-gpt
  同族旁证） | 参考 | 2026-10-06
- **Devin-AXIS/iPolloWork**（6,663★，10-05 push）新入库 | 企业级 local-first
  多引擎 Agent Workbench（人员+agent 团队统一工作台）| A13 面板域
  orca/t3code/paseo 形态族 +1 | 雷达 | 2026-10-06
- **awesome-dsh-plugin/awesome-dsh-plugin**（17,850★，10-05 push，created
  2026-08-13 与 deepseek-harness 同日）+ **zhu1090093659/dsh-web**（8,408★，
  10-06 push）新入库 | dsh 插件生态聚合层两件：精选清单 org 仓+Web 聚合
  「万物皆插件，创意工坊分发」| **dsh 生态第 13/14 例**——18 时班 PerryLink
  组织矩阵 48 件插件后聚合面自身也在长大，自家 CLI 周边词组
  `deepseek+harness+plugin+OR+dsh+plugin` 直接命中（顺藤规则有效性再证）|
  生态雷达 | 2026-10-06
- 微型批（不过三门槛如实记）：amontlabs/lcu 524★（Codex computer-use runtime
  解耦给任意 harness，需本地 ChatGPT 桌面端供 runtime——A5 域，我们无 GUI
  操控同族不适用）/ justlovemaki/CloudFlare-AI-Insight-Daily 1,801★（AI 资讯
  日报聚合——扫榜新闻面形态参考）/ IvanWng97/pixtuoid 485★（终端像素办公室
  AI agents 可视化——A13 趣味形态）/ ReflexioAI/reflexio 375★（agent 自我
  改进 harness——B2 域微型）/ ShZhao27208/Aut_Sci_Write 207★（学术文献检索
  技能套件 WoS+Elsevier+Springer——调研报告域技能包方向）/ nealbridges/
  VulnHunter 243★+FunnyWolf/agentic-soc-platform 1,202★（安全域微型）/
  codedge/laravel-selfupdater 398★（A9 自更新异栈参考）/ leopiney/neuralnoise
  226★（AI Podcast Studio，2025-03 停更不适用）/ data-infra/cube-studio
  2,534★+elliothux/open-compute 1,516★（域外噪声）| 判据 | 2026-10-06

### 简称→正主全名对照表（本班补勘 7 件）

odysseus=odysseus-dev/odysseus（90,338★）/ lcu=amontlabs/lcu（524★）/
dsh-web=zhu1090093659/dsh-web（8,408★）/ iPolloWork=Devin-AXIS/iPolloWork
（6,663★）/ prompts.chat=f/prompts.chat（172,100★，f 单字母 org）/
open-webui=open-webui/open-webui（同名同主）/ ai-maestro=23blocks-OS/
ai-maestro（808★，A1u 新锐零收录备查） | 对照表 | 2026-10-06

### 勘定与状态变更（本班 3 件）

- **superpowers 10-06 恢复 push**（295,670→295,698）——打破「09-27 后无 push」
  连续多班口径，下轮起口径更新 | 复查 | 2026-10-06
- **firecrawl/open-agent-builder pushed 2025-10-20 勘定**——陈旧近一年（09 时
  班入库漏记 pushed_at；本轮 repos 复查勘定，「视觉 agent 工作流构建器」降级
  停更观察——上游 firecrawl 主仓 188,970★ 独立活跃，产品线疑似并入主仓）|
  勘定 | 2026-10-06
- opensource-joe/awesome-open-source-AI 实仓 0★ 且 08-09 后停更——WebSearch
  放大死平不入（「新闻面 ≠ 开源仓在」形态续例） | 判据 | 2026-10-06

### 复查记录（repos 端点 53 仓，12:4x-13:0x，全 alive 零 archived）

orca 85,796→**85,986（+190 续领跑）**/superpowers 295,670→295,698（10-06
push 状态变更）/mattpocock/skills 277,117→277,202（差 superpowers 18,496）/
ECC 273,674→273,736/ponytail 156,008→156,108/hermes-agent 251,454→251,484
（10-06 push）/opencode 211,892→211,910（10-06 push）/pi 112,733→112,770/
anthropics/skills 179,794→179,817/claude-mem 96,646→96,699（放量持续）/
**Strata 13,977→14,237（+260 放量续）**/**rea 5,694→6,027（+333 放量续，
10-06 push）**/iFixAi 21,194→21,273（10-06 push）/uber/ADR 1,722→1,766/
**nexu-io/open-design 97,499→99,593（+2,094 dsh 生态头牌放量）**/
**answer-me-with-html 483→1,519（爆量持续，三周 130→1.5k）**/yomiyasu
1,518→1,539（在动加速）/OpenMontage 63,764→64,180/t3code 24,836→25,670/
autoharness 7,906→8,013/DeepSeek-Reasonix 35,740 持平（10-06 push，E 候选
首位）/winnow 102 持平（10-06 push，队列攒批标的）/记忆域 agentmemory
29,157/beads 27,654/gascity 1,329/context-mode 25,487/hippo-memory 772
（10-06 push）/nautilus-compass 1,144→1,168/Memori 17,074 持平（10-03
push）/magic-context 2,268→2,270（10-06 push）/治理域 bernstein 1,404 持平
（10-06 push）/SkillSpector 19,467→19,481/open-code-review 43,874→43,902/
herdr 42,516→42,535/写作域 drama-skills 2,528/oh-story 7,270/webnovel-writer
7,325/ainovel-cli 2,098/huobao-drama 15,778/awesome 16 源全 alive（数字见
今日报告） | 复查 | 2026-10-06

### 雷达 C 与七专项快照（本班独立实测）

- 雷达 C：awesome 16 源全 alive 零 archived；topic 8 页已录族为主
  （LoopTroop 160/synapse-ai 328/agent-swarm 859 复认）；npm 两查已录族+
  微型新生件 5 零接入级；**trendshift 29 件文本全录新面孔 3 件**（odysseus/
  lcu/VulnHunter）；禅道周边**第 17 例零新禅道 AI 竞品**（cra-agent 系 CRA
  合规域非禅道；pipeshub 3,810/paca 1,894/plandb 105 已录复认）；pypi 省配额
  未复试 | 雷达 | 2026-10-06
- A：八锚点判定维持（新见 odysseus 系工作台形态非压缩机制，不改判定）|
  已覆盖 | 2026-10-06
- B：六源在位；本班候选复判（odysseus/iPolloWork/dsh 聚合两件均独立平台非
  六源可直装技能包，三问全不过零接入未绕闸） | 巡检 | 2026-10-06
- C：BUILTIN_FLOWS=18 import 实测（18 id 逐一列出对上） | 巡检 | 2026-10-06
- D：lessons=72 零漂移（流程规范 26=36.1% 维持）packs=3；零新蒸馏
  （去重纪律） | 数据卫生 | 2026-10-06
- E：DEFAULT_CATALOG=14 import 实测；六候选 which 全空零接入防死链
  （Reasonix 35,740 10-06 push 候选首位维持） | 巡检 | 2026-10-06
- F：poll 键缺失（未配置=部署缺位多班同口径）/claims 空/last_error 空；
  全程只读；第 17 例零新禅道 AI 竞品 | 巡检 | 2026-10-06
- G：过时文案 grep 口径沿用零新毛病 | 巡检 | 2026-10-06

### 待深挖队列（12 时快照）

21 时快照 11 项全部维持（第 3/10 项保持划掉）；本班新面孔均雷达/攒批级
（odysseus Deep Research/Compare 随一体化工作台形态攒批观察）零新队列项。
风险在档维持（交人拍板）：data/zentao.json 明文密码；portscan 真实杀进程
用例挂死；32 位全量 discover 静默退出。

### 未验证项（如实记录）

pypi 未复试；Trending 直抓未行（trendshift 补位）；odysseus/lcu README 深读
仅头部抽样（Compare 全文未读，攒批备选待下轮）；magic-context/Memori 机制面
深读承 09 时班未行；禅道定时扫描未触发真实工单验证。发版判定：docs-only
不发版（05/06/01/04/09/10 时班先例）。

## 2026-10-06 13 时班（批6：框架/平台/SDK 生态——新一轮计划第 1/4 步全类型调研，报告见 2026-10-06.md 与 current-round.md 本班节）

> hour=13，13%7=6 → 批6。分支 main（0aa681a）。主扫 115 查询 524 行 466 唯一仓
> 零失败零限流；466 仓历史查重全落已录族，**批6 域零机制级新面孔（稳定期延续）**；
> WebSearch 串行 2 发（距 09 时班 4 班到窗口）捞出 openagent 真仓一件+放大例两件
> （跨通道互补连续第 4 班实证）。

### 新条目（本班真新面孔，均 repos 实证）

- **the-open-agent/openagent**（5,684★，10-05 push，WebSearch 捞出+repos 端点
  二次实证）新入库 | 自托管开源个人 AI 助手：任意 LLM 供应商+RAG 知识库+自主
  agent 回路+MCP 兼容工具 | 对话/知识库域：与我们任务域知识注入同域但形态为
  独立助手平台；非六源可直装技能包，接入三问不过 | 雷达（批6/B7 域） | 2026-10-06
- **carloslfu/slotstream**（420★，10-06 push，topic:claude-code 捞出）新入库 |
  SSD 流式跑超显存 MoE 大模型（105GB 模型低配 Mac 可跑） | 本地推理基建域外
  （编排台不背模型运行时） | 参考（域外） | 2026-10-06
- **jin-bo/agentao**（308★，10-06 push，topic:agent-framework 捞出）新入库 |
  本地优先治理 agent 运行时（Python 嵌入/CLI/ACP server：权限+MCP+记忆+审计） |
  批4×批6 交叉：审批闸/经验库已有对应物；嵌入式运行时形态不同轨 | 雷达（治理域） | 2026-10-06
- **xuiltul/animaworks**（266★，10-06 push，topic:agent-framework 捞出）新入库 |
  Organization-as-Code+脑启发记忆（生长/巩固/**遗忘**）+多模型路由 |
  **记忆域攒批证据 +1**（会遗忘的记忆×magic-context 记忆自管理×Memori 自动抽取
  三证）；组织即代码形态参考 | 雷达（记忆域攒批） | 2026-10-06
- **fallow-rs/fallow**（5,006★，10-06 push，topic:mcp 捞出）新入库 | TS/JS 代码库
  智能静态分析（健康度/复杂度热点/架构边界/循环依赖） | 批1 域旁非 agent 系；
  diff 评审链已有 | 参考（批1 域旁） | 2026-10-06
- **MCPJam/inspector**（2,238★，topic:mcp 捞出）新入库 | MCP server/app 测试
  评测调试平台 | 我们消费 MCP 技能包无自研 server 需求 | 参考（MCP 生态） | 2026-10-06
- **ikaijua/Awesome-AITools**（6,207★，topic:claude-skills 捞出）新入库 | 中英
  双语 AI 工具收藏清单 | 清单域小标（雷达源补充候选暂不入 C） | 雷达 | 2026-10-06
- 微型批与放大例（如实记）：**metaspartan/cybara 31★——cybara.ai 新闻面宣称
  自托管 agent OS，「新闻面 ≠ 开源仓在」放大形态续例**/ memorycrystal 12★ /
  GagnDeep 清单 1★ 死平 / OpenClaw 报道 68K 系旧闻（正主 391,459★ 在档零缺口） | 判据 | 2026-10-06

### 简称→正主全名对照表（本班补勘 3 件+噪声勘误 2 件）

openagent=the-open-agent/openagent（5,684★）/ awesome-cli-coding-agents=
bradAGI（1,317★，in:name 一次勘定，C 源属主补注）/ Cybara=metaspartan/cybara
（31★ 放大例）；**勘误**：历史记录「claude/opencode」「grok/pi」系 grep 分片
噪声非真名（正主 anomalyco/opencode、earendil-works/pi 对照表 21 时班在档，
后续班勿从上下文盲提全名） | 对照表 | 2026-10-06

### 批6 域结果与复查增量

- 批6 域主扫 466 唯一仓历史查重**全落已录族零机制级新差量（稳定期延续）**；
  头部框架 repos 复测全微增：mastra 28,581/vercel-ai 27,132/pydantic-ai 20,424/
  openai-agents-python 29,851/semantic-kernel 28,629/langgraph 42,755/
  microsoft/agent-framework 13,955/ag2 4,976。
- 承 12 时班 53 仓复查（间隔<1h）本班独立复测 50+ 仓：orca 85,996 续领跑/
  superpowers 295,705（10-06 push 续）/claude-mem 96,708（放量续，10-06 push）/
  rea 6,055（放量续）——增量全部个位数微增零状态变更。

### 雷达 C 与七专项快照（本班独立实测）

- 雷达 C：awesome 17 源全 alive 零 archived + **C 源补正 awesome-cli-coding-agents
  正主=bradAGI**；topic 8 页新面孔 6 件（本班新条目主体）；npm 两查已录族为主
  零接入级；trendshift 可达（12 时班 29 件间隔<1h 零重复）；禅道周边承 12 时班
  第 17 例（间隔<1h 零重复配额）；pypi 省配额未复试 | 雷达 | 2026-10-06
- A：锚点实读在位（compaction :182/pipeline :61/:2538）；cache_control 零命中
  维持（stable_order 应用侧最大化口径不变） | 已覆盖 | 2026-10-06
- B：六源 SOURCES :50+SSRF :126-133 逐 IP 六类拒绝实读；缓存 810 项 fetched_at
  全 10-03 零漂移；本班候选复判（openagent/slotstream/agentao/animaworks/fallow/
  MCPJam 均独立平台非六源可直装技能包）三问全不过零接入未绕闸 | 巡检 | 2026-10-06
- C：BUILTIN_FLOWS=18 import 实测（14 指令项+自研 4 对上注册表） | 巡检 | 2026-10-06
- D：lessons=72 零漂移（流程规范 26=36.1% 口径维持）packs=3；零新蒸馏
  （去重纪律） | 数据卫生 | 2026-10-06
- E：DEFAULT_CATALOG=14 import 实测；六候选 which 全 MISSING 零接入防死链
  （Reasonix 35,740 10-06 push 候选首位维持） | 巡检 | 2026-10-06
- F：poll_enabled=None/profiles=0/claims=0/last_error 空（配置缺位多班同口径，
  与 12 时班「poll 键缺失」系同一缺位两种键形态如实并记）；全程只读；第 17 例
  维持 | 巡检 | 2026-10-06
- G：过时文案七模式 grep（app/ui 三件+README）零命中零新毛病 | 巡检 | 2026-10-06

### 待深挖队列（13 时快照）

21 时快照 11 项全部维持（第 3/10 项保持划掉）；本班新面孔均雷达/攒批/参考级
（animaworks 记忆域攒批证据 +1）零新队列项。风险在档维持（交人拍板）：
data/zentao.json 明文密码；portscan 真实杀进程用例挂死；32 位全量 discover
静默退出。

### 未验证项（如实记录）

pypi 未复试；Trending 直抓未行（12 时班 29 件间隔<1h 零重复）；openagent/
agentao/animaworks README 机制面深读未行（雷达级攒批备选）；magic-context/
Memori 机制面深读承 09 时班未行；禅道定时扫描未触发真实工单验证。发版判定：
docs-only 不发版（05/06/01/04/09/10/12 时班先例）。

## 2026-10-06 13 时班·巡检（新一轮计划第 2/4 步·七专项 A-G 独立实锚+落地件选定，13:2x）

> 承 13 时班第 1/4 步（批6 调研）同窗接力；分支 main（0aa681a）。工作区在制品
> （11 时落地班 cover-preview 四件+12/13 时班 docs）逐字不动，本班只增本节。
> 全部结论基于本机实测（import/JSON 直读/grep/which），禅道全程只读零工单零回写。

### A · token 节约（单列小节）

- 证据：八机制锚点逐一实读在位——三段压缩 compaction.py（estimate_tokens :33/
  prune_text :42/compact_region :119/maybe_compact :182）；token_meter 单例+
  step_runner :74-82 事前门（last_context/capacity 超 _PRECHECK_RATIO 先压缩再生成）；
  预算熔断 pipeline.py :61 _ensure_budget（总时限）+:613 _budget_max_tokens
  （TUTTI_BUDGET_MAX_TOKENS env 优先+settings budget.max_tokens_per_run）+:632
  _budget_cost_caps（日/月花费硬顶）+:648 _cost_gate_block（人话报文闸）；
  cascade pipeline.py :1526-1529（settings cascade.enabled opt-in+cascade_reorder
  按 tier 升序走廉价）；_shrink_context_block :2538（四层优先级保序截断，降级留痕
  承 22 时班）；经验召回 skills.py :348 _surplus_decay（hippo-memory 借鉴衰减）+
  :540 relevance_top（top-k 相关性注入）；会话复用 pipeline.py :136-283
  （resume 钉原 CLI+死链补位不偷跑默认）；diff-only 评审 pipeline.py :919
  CODE_REVIEW_PROMPT（diff 为主要依据+findings 文件:行号锚定证据〔pr-af 借鉴〕+
  半成品收工提醒〔agent-delegate 借鉴〕）+_git_diff 拼未跟踪新文件防半盲评。
- 四对标方向判定（**维持，不重复实现**）：prompt 缓存=已覆盖（usage.py :117 cached
  细分记账+压缩压力触发避开毁缓存；`cache_control` 全 app/core grep 零命中维持——
  供应商侧自动缓存+stable_order 应用侧最大化，显式断点无必要）；语义缓存=半已有
  （modelhub.py :3673 chat cache_ttl 精确匹配缓存），语义级同义命中维持待拍板；
  diff-only 评审=已有；廉价分流=已有（cascade opt-in+task_compile.py :163
  light_types {email,weekly_report,translation} 短链+fast 模式 reviewers=1）。
- 结论：已覆盖 | 2026-10-06

### B · 插件市场

- 证据：六源 SOURCES market_remote.py :48-76 实读（zcode/anthropic/anthropic-skills/
  claude-skills/clawhub/cocoloop，镜像双 urls 按序试）；SSRF :118-135 实读（仅 https+
  端口合法+getaddrinfo 逐 IP 六类拒绝 loopback/private/link-local/reserved/
  multicast/unspecified）+_fetch 体量上限+两段式网络策略；缓存六文件实测 **810 项**
  （zcode 26/anthropic 315/anthropic-skills 5/claude-skills 99/clawhub 215/cocoloop
  150）fetched_at 全 2026-10-03 零漂移；market.json installed=20（内置 6+远程 14）。
- 本班候选复判（承第 1/4 步新面孔，接入三问逐项：重合度/可直读性/用户会搜吗）：
  openagent（自托管助手平台）×slotstream（本地推理基建域外）×agentao（嵌入式运行时）
  ×animaworks（框架）×fallow（静态分析 CLI）×MCPJam（MCP 测试平台）——六件均
  独立平台/CLI 形态非六源可直装技能包，三问全不过零接入未绕闸；agnix/
  dsh-plugins-store 承 01 时班判例维持雷达。
- 结论：巡检（零接入，方法论沉淀维持）| 2026-10-06

### C · 任务类型（18 型全巡）

- 证据：BUILTIN_FLOWS import 实数 **18**（id/name/icon 全列对上）；参数逐型实读：
  review 型 13 个全带领域化 rubric（video_script 黄金3秒钩子/translation 忠实度+
  术语一致性/email 目的明确/bid_doc 评分点覆盖等）+threshold 7.0（bid_doc 7.5）+
  rounds 2+manuscript 按型命名；direct 型 3 个（direct/rank_scan/defect_retro）
  无评审参数与 task_compile fast/expert 分型一致；serial_novel serial{chapters:8,
  words:2500}；data/flows.json 不存在=overrides 0/custom 0（注册表纯默认零覆盖漂移）；
  菜单数据驱动实证 app.js :410 api("/api/flows")；18 型名 i18n EN 词条 **18/18
  全覆盖**；rank_scan note「四平台」与 paihang.py 实现一致（七猫/番茄/起点 fetch
  :98-100+纵横 docstring :11）。
- 结论：巡检（18 型菜单/流程参数/辅助信息零漂移零过时）| 2026-10-06

### D · 经验库

- 证据：data/skills.json 直读 lessons=**72 零漂移**（流程规范 26=36.1%/节奏爽点 21/
  情节逻辑 10/人物塑造 7/一致性 4/文笔风格 4；六类闭枚举与 skills.py :46
  LESSON_CATEGORIES 一致）packs=3；写入入口 skills.upsert_lesson :424 在位。
- 处理决定：本班零新蒸馏入库（去重纪律）；本班新见方法论「状态文件先读内层结构
  再下缺位结论」录本文件教训节（docs 级），是否占经验库 data 条目交第 3/4 步按
  upsert 通道裁定；D 节三组正文级合并维持候拍板（数据变更交人），无备份批量删除
  未触发。
- 结论：数据卫生 | 2026-10-06

### E · 新 CLI 接入

- 证据：DEFAULT_CATALOG import 实数 **14**（detect.cli：codex/claude/opencode/qwen/
  aider/openclaw/kimi/mimo/grok/pi/dsh/gemini/cbc/trae-cli）；本机 which 实测
  **13/14 在装**（仅 openclaw MISSING）；六候选 deepseek-reasonix/reasonix/fuxi/
  gitlawb/zero/empryo which 全 **MISSING**——未装未实测零接入防死链，进待验证
  队列（Reasonix 35,740★ 10-06 push 候选首位维持雷达）。
- 结论：巡检 | 2026-10-06

### F · 禅道集成（全程只读）

- 证据：data/zentao.json **双层实测**——顶层无 poll 键（历班「键缺失/配置缺位」
  口径即由此来）；**config 内层 poll_enabled=False 显式关闭（本班勘定：非缺位而是
  关闭）**，_poll_unlocked :2414 → skipped=poll_disabled 分支实证；product_profiles=
  **1** 在位（product=96/assigned_to=wuxinping/our_sides=[backend]/backend workdir
  E:\GitLab\cbc\mo-so/owners backend=wuxinping、frontend=xiangdong/module_routes=[]
  ——形状完整，路由配置可判形状准确）；claims={} last_error=''（无认领积压无报错）；
  last_scan=2026-09-21 20:43（poll 关闭后未再扫）；auto_resolve/auto_merge/triage_ai
  全 True；interval_hours=2 老键（load :269-276 迁移逻辑在位）。设置页引导文案
  app.js:12012 在位（04 时班判例，候选丙缺口不成立维持）。禅道 AI 竞品第 17 例
  零新（13 时班同窗在档）。
- 缺口与风险：无代码缺陷；「poll 关闭+内网实例 10.143.132.5:8899 不可达」系部署
  决策交人；**zentao.json 明文密码风险在档维持（交人拍板）**。
- 结论：巡检（口径勘定 1 件：配置缺位→显式关闭）| 2026-10-06

### G · 产品巡检

- 证据：过时文案七模式（13 种/14 种/15 种/17 种/11 个预置/单源/五源）app/ui 三件+
  README grep **零命中**；**G-② EN 缺口独立复测归零**（data-i18n 376 唯一+
  data-i18n-ph 42 唯一，JS 反转义精确比对全命中——注意：不反转义直接比对会因
  i18n.js 内 \" 转义误报缺口〔本班先误报 1 处后勘正〕；承 05 时班提案乙 7 词条
  落地+test_i18n_key_coverage.py 回归锁定）；封面卡文案与 11 时落地班 cover-preview
  新端点相符（在制品在案）。
- 结论：巡检（零新毛病+G-② 销账）| 2026-10-06

### 落地件选定（第 3/4 步交接）

- 在册代码级「小而实」积压核对为零：提案乙已由 05 时班实施+本班归零复验销账；
  提案甲（store.py 章数下限 max(1)→max(2)）05 时班整体撤回+本班独立复证 :277-278
  在码注释意图（「续写批次允许只续 1 章，下限放宽到 1（全新连载仍由前端约束 ≥2）」
  ——store.py :279 注释与提案直接相抵），撤回成立，是否另立修正案交人裁定；
  语义缓存/D 节三组合并/一致性评审容量降级/gate 统计行修法均候拍板。队列活项均
  攒批/拍板/远期。
- 本班实落地=**docs 通道三件**（05/06/09/12/13 时班 docs-only 先例同口径）：
  ①F 口径勘定（缺位→显式关闭，多班误读修正）；②G-② 销账（EN 缺口归零独立复验，
  候拍板清单除名）；③提案甲撤回互证（注释意图实锚）。
- 第 3/4 步若开代码件：须等人拍板语义缓存或推翻 :279 注释意图后另立修正案，勿自选
  （数据变更/行为语义交人决定）。

### 待深挖队列（13 时巡检快照）

21 时快照 11 项全部维持（第 3/10 项保持划掉）；本班零新队列项。风险在档维持
（交人拍板）：data/zentao.json 明文密码；portscan 真实杀进程用例挂死；32 位全量
discover 静默退出。

### 未验证项（如实记录）

禅道内网实例可达性与扫描链路未验证（poll 显式关闭+不为验证制造真实 Bug）；六候选
CLI 未装未实测；magic-context/Memori/odysseus README 机制面深读承前班未行；pypi
未复试；Trending 直抓未行。发版判定：docs-only 不发版（05/06/01/04/09/10/12/13
时班先例）。

### 教训与方法论（本班入库）

- **「顶层无键」≠「未配置」**：多班把禅道 poll 记成「键缺失/配置缺位」，实为
  config 内层 poll_enabled=False 显式关闭+1 条产品档案在位——读运行时状态文件先
  摸清哪层持有哪个键再下「缺位」结论；对表用应用自己的 load/normalize 视角
  （zentao.py :255 merged defaults），别拿顶层 JSON 形状当配置面 | 方法论 | 2026-10-06
- **转义形态差会造成假缺口**：data-i18n 键含引号时 index.html 用 &quot; 而
  i18n.js 用 \"——EN 覆盖审计必须两侧反转义到同一形态再比对，否则误报缺口、
  白补词条 | 方法论 | 2026-10-06

## 2026-10-06 14 时收口班（第 4/4 步·文档复核+整体联调+五道关+推送发版，14:16 开工）

> 分支 main（0aa681a）全程未切。产出：f9ca937（feat 封面预览+调研沉淀九文件）/
> 8458422（v0.1.88 发版三件套）/ 80526ab（补录实数 docs）三连推，**v0.1.88
> publish 终核过（npm view=0.1.88、dist-tags.latest=0.1.88）**。报告实录见
> 2026-10-06.md 14 时班节。

### 落地件销账（10 时班候选甲→第 3/4 步实施→本班收口）

- **候选甲（封面提示词预览确认，drama-skills 借鉴③）落地并销账**：main.py
  GET /api/tasks/{id}/cover/prompt 只读端点 + app.js coverPreview→
  coverConfirm→coverCancel 二段确认（POST /cover 唯一入口收口至 coverGen）+
  i18n 3 词条 + CSS 预览框 + 12 项测试（6 端点活体+6 前端契约）。
  knowledge.md :193 drama-skills 借鉴③状态待同步：预览确认**已落地**
  （下轮复查以本条为准）。
- 候拍板四件（语义缓存/D 节三组合并/一致性评审容量降级/gate 统计行修法）
  与提案甲撤回裁定维持交人，本班未动。

### 闸②证据与既有风险（本班独立实证）

- 全量 discover 静默退出第 4 例（Ran 0 tests + exit 0）；前台分块对账四块
  合计 2195 项：a-c 514 OK / d-i 398（2F 单跑复绿：deepseek_harness 21/
  git_workbench 23，分片批量交叉态噪声）/ j-r 778（1F）/ s-z 505 OK；
  改动面 45 项全绿，零代码回归。
- **新风险入档（交人拍板）**：test_runner_drain 活性窗口计时用例 1.65s>1.2s
  3/3 稳定复现，HEAD 干净 worktree 基线同现同败=既有环境态（08 时班曾单跑
  复绿，疑本机负载/32 位进程孵化变慢）——不擅改他人用例，待裁定。
- 后台跑测试在本环境随会话终止两度被杀（日志无统计行=证据无效）——**长测
  一律前台分块**（块间证据即时落袋）入方法论。

### 教训与方法论（本班入库）

- **先 add 后改文件=提交到旧版**：闸④ git add 之后又 Edit 报告补录实数，
  提交定格的是占位版——「改完再 add 再 commit」顺序不可倒置；已推历史不
  强推，实数版以补提交收口（80526ab）| 方法论 | 2026-10-06
- **npm publish 挂起形态复认+终核口径**：首跑 420s+ 零输出被停后 registry
  实际迟到落地（0.1.87→0.1.88 无重复版本报错）——publish 终核只认
  `npm view` 结果，进程输出/超时不构成失败证据 | 方法论 | 2026-10-06
- **release_gate.py 净区闸**：publish 前置钩子拦「工作区不干净禁止发版」，
  防并行在制品混入发版包——docs 残留也会被拦，发版前先清区 | 方法论 |
  2026-10-06

### 待深挖队列（14 时快照）

21 时快照 11 项全部维持（第 3/10 项保持划掉）；本班零新队列项。风险在档
维持（交人拍板）：data/zentao.json 明文密码；portscan 真实杀进程用例挂死；
32 位全量 discover 静默退出；**+runner_drain 计时超界（本班新增）**。

### 未验证项（如实记录）

封面预览仅浏览器级 UI 手工点按未行（本环境不启动浏览器，前端接线以源码
契约测试锁三点）；禅道真实实例回写全链路承前班未实测；pypi 未复试；
Trending 直抓未行。

## 2026-10-06 15 时班（批1：代码质量与评审——新一轮计划第 1/4 步全类型调研）

> 开工实录：15:38（UTC+8，hour=15，15%7=1 → 轮换批1）、分支 main（ac15ee1），
> 工作区干净（14 时收口班三连推后零残留）。通道：gh api 认证可用，串行
> sleep 4s 纪律零限流零失败。证据底稿全文见 current-round.md「15 时班调研
> 底稿」节。

### 主扫与雷达 C（完成状态与来源）

- **主扫**：`scripts/borrow_scan_nightly.py` 无参全量（自动按小时选批 B1）——
  A 常驻 89（含内置 B1 11）+ B1 轮换 11 + A1u/A1p2 双轮 16 = **116 查询 525 行
  423 唯一仓，零失败零限流**（DONE 116 / errors: 0 实证）。
- **WebSearch 串行 2 发**（距 13 时班 2 班，批1 域）：①code review agent 向——
  Kodus 已录复认；「Open Code Review」（fireup.pro 新闻面）in:name 勘定=
  **alibaba/open-code-review 43,920★ 已录复认**（新闻面「multi reviewers
  debate」措辞与实仓「hybrid deterministic+LLM Agent」描述有出入系新闻演绎，
  「claim 须 repos 二次实证」规则再证）；②test generation/self-healing 向——
  tugkanboz/awesome-ai-testing 130★+healenium 167★ 均 repos 实证微型判据级。
  **批1 域零机制级新面孔（稳定期延续）**。
- **A2 词形缺口补查**：主扫脚本 QUERIES 的 A2 组未同步 keywords.md 21 时班
  `digital+employee` 词形——手工补跑 1 查询，5 件全微型（最高 crewmeld 948★，
  StaffDeck 1,967★ 仍是该词形最大已录件）零机制级新面孔；**脚本↔keywords.md
  同步缺口如实记录**（本步涉及文件仅 docs，脚本修改交后续班处置）。
- **雷达 C 全过**：awesome 17 源 repos 实测全 alive 零 archived（ComposioHQ
  76,568/punkpeye 95,861/hesreallyhim 55,125/VoltAgent-skills 35,257/Shubhamsaboo
  140,821 微增，vijaythecoder 4,389 与 caramaschiHG 1,921 停更观察维持）；
  **trendshift 可达**（http 200，29 仓）——已录复认 7 件+新面孔候选 10 件
  repos 实证**全已录**（openhuman 40,935 已深挖 TokenJuice 已落地/busbar 172/
  bifrost 8,574/moli 9,890 放量/agentmemoryrepo 355/GhidraMCP 10,470 停更均
  在档，DroidDeck/openGym/V3SP3R/moejs 域外判据）——trendshift 本班零新面孔；
  topic 8 页已录族为主（omnigent 10,607/claude-mem 96,763/agent-swarm 859
  复认）；npm 两查 @nathapp/nax 复认+微型新生件 6 零接入级；禅道周边 3 查
  **第 18 例零新禅道 AI 竞品**（pipeshub/paca 已录复认）；pypi 省配额未复试、
  Trending 直抓未行（trendshift 补位）。

### 本班新面孔（全微型判据级，不过三门槛如实记）

- **miracodeai/mira**（357★，09-30 push）| 自托管 AI 代码评审：索引化 PR
  review+walkthrough+漏洞扫描+依赖图+自定义规则 | B1 域同族 +1，「依赖图+索引
  化评审」形态在 kodus/PR-Agent/open-code-review 已录族中有点差量，但微型体量
  零接入 | 判据 | 2026-10-06
- **fumingyang2004/Tulpa**（152★，10-06 push）| Windows 本地 QQ+微信
  Harness/MCP：实时检索/跟进/管理/记忆/工作区，开放 MCP 供 DSH/Codex/
  Antigravity | 对话域旁支+**dsh 生态周边 +1**（生态第 15 例口径） | 判据 |
  2026-10-06
- 微型批：overmind-core/overmind 441★（持续改进 agent 平台，B2 域旁）/
  5dive-ai/5dive 65★（自托管命名 agent 舰队 claude/codex/pi，自家 CLI 周边
  形态）/ spencermarx/open-code-review 371★（multi-agent debate review）/
  healenium 167★（Selenium 自愈，2026-03 停更异栈）/ Sidiora-Labs/
  Paxeer-X-Network 574★（支付状态机域外）/ npm 微型 6（claude-code-
  orchestrator-kit 形态近面板域） | 判据 | 2026-10-06

### 存量复查（repos 端点 38 仓，15:4x，全 alive 零 archived）

- 头部：orca 85,996→**86,074**（+78 续领跑）/superpowers 295,705→295,753
  （10-06 push）/mattpocock/skills 277,213→277,303（差 18,450）/ECC
  273,736→273,814/ponytail 156,108→156,217/claude-mem 96,708→96,759（放量续）/
  hermes-agent 251,484→251,513/opencode 211,910→211,927/pi 112,770→112,811/
  anthropics/skills 179,817→179,831。
- 放量族：**Strata 14,237→14,518（+281 放量续）**/**rea 6,027→6,334（+307
  加速续）**/iFixAi 21,273→21,346/OpenMontage 64,180→64,287/t3code
  25,670→25,717/autoharness 8,013→8,071/answer-me-with-html 1,519→1,552
  （爆量放缓）/yomiyasu 1,539→1,551/uber/ADR 1,766→1,810/nexu-io/open-design
  99,593→99,613。
- 记忆/治理域：beads 27,658/gascity 1,329（10-06 push）/magic-context 2,270
  （10-06 push）/hippo-memory 772（10-06 push）/context-mode 25,487→25,494/
  bernstein 1,404（10-06 push）/herdr 42,535→42,550/SkillSpector 19,481→19,497/
  open-code-review 43,902→43,919/Memori 17,074 持平（10-03 push）/
  **nautilus-compass 正主 chunxiaoxx 实测 1,168 持平**（10-06 push，与 13 时班
  吻合零失配）。
- 写作域：DeepSeek-Reasonix 35,739（10-06 push，E 候选首位）/winnow 102 持平
  （10-06 push）/drama-skills 2,532/oh-story 7,275/webnovel-writer 7,327/
  ainovel-cli 2,097/huobao-drama 15,780。
- nautilus-compass 属主勘定顺带实证：AgentField 属主猜名 404，in:name 一次
  勘定 chunxiaoxx/nautilus-compass——00 时班规则再证（知识库原记录本就正确，
  系本次捞名歧义非失配）。

### 覆盖矩阵与七专项（独立实证）

- **覆盖矩阵**：14 指令项一一映射注册类型（直接执行=direct/扫榜选材=
  rank_scan/禅道工单=defect_retro 等）+4 自研=BUILTIN_FLOWS **18** import
  实测吻合（article/bid_doc/code/defect_retro/direct/doc/email/novel/
  presentation/rank_scan/research/resume/serial_novel/speech/tech_proposal/
  translation/video_script/weekly_report）；**指令口径「13 种」vs 列名 14 个
  的差异说明：真实注册表 18 型，指令列出的 14 个名称全有对应，注册表另含
  bid_doc/presentation/doc/resume 4 型自研扩展**；对话/知识库场景由 A10
  chatbot-memory/A11/A8 词组+批5 域覆盖。 | 巡检 | 2026-10-06
- **七专项**（本班轻量实测）：A token 八锚点在位零漂移（compaction 剪枝族/
  pipeline :627+:696 预算熔断/:1526-1529 cascade/:2538 _shrink_context_block/
  usage :117 cached 记账；cache_control 全 core 零命中维持）| B 六源在位
  （market_remote.py:50 SOURCES）+六源缓存 fetched_at 全 2026-10-03 零漂移+
  本班候选均非六源可直装技能包三问全不过零接入 | C 18 实数 import 实测 |
  D lessons=72 零漂移（流程规范 26=36.1%/节奏爽点 21/情节逻辑 10/人物塑造 7/
  一致性 4/文笔风格 4）packs=3 零新蒸馏 | E DEFAULT_CATALOG 在位、六候选
  which 全 MISSING 零接入防死链（dsh=FOUND 已接对应） | F poll_enabled=None/
  claims=0/last_error 空/last_scan 停 2026-09-21（部署配置缺位多班同口径）
  全程只读+第 18 例 | G 过时文案 grep（13 种/双源单源等）零命中零新毛病 |
  巡检 | 2026-10-06

### 落地件与发版判定

本班零代码件：调研沉淀三件（knowledge.md 本班节+current-round.md 本班节+
2026-10-06.md 本班节）——**docs-only 不发版**（05/06/01/04/09/10/12/13 时班
先例）。

### 待深挖队列（15 时快照）

21 时快照 11 项全部维持（第 3/10 项保持划掉）；本班新面孔均微型判据级零新
队列项。风险在档维持（交人拍板）：data/zentao.json 明文密码；portscan 真实
杀进程用例挂死；32 位全量 discover 静默退出；runner_drain 计时超界。

### 未验证项（如实记录）

pypi 未复试（历班六种拦截形态在案）；Trending 直抓未行（trendshift 补位）；
mira/Tulpa README 机制面深读未行（微型判据级）；禅道定时扫描未触发真实工单
验证（不为验证制造真实 Bug）。

## 2026-10-06 16 时班（批2：学习记忆与自我改进——新一轮计划第 1/4 步全类型调研）

> 开工实录：16:01（UTC+8，hour=16，16%7=2 → 轮换批2）、分支 main（ac15ee1），
> 工作区在制品 = 15 时班 docs 三件（未提交）。通道：gh api 认证可用，串行
> sleep 4s 纪律零限流零失败。证据底稿全文见 current-round.md「16 时班调研
> 底稿」节。

### 主扫与雷达 C（完成状态与来源）

- **主扫**：`scripts/borrow_scan_nightly.py` 无参全量（自动按小时选批 B2）——
  A 常驻 89（含内置 B1 11）+ B2 轮换 10 + A1u/A1p2 双轮 16 = **115 查询 524 行
  464 唯一仓，零失败零限流**（DONE 115 / errors: 0 实证）。
- **主扫面过筛**：464 唯一仓全名+简称双通道对全历史逐一过筛——首见 158 件
  域外噪声为主，**批2 域（学习记忆与自我改进）47 唯一仓大星件全已录
  （codegraph 73,289/graphiti 31,469/ARIS 17,031/pro-workflow 2,904/MegaMemory
  712/prax-agent 273/mengram 204 等前班已定性），零机制级新差量（稳定期
  延续）**；首见全为课程/论文官方仓/微型教学实现噪声。
- **WebSearch 串行未行**：距 15 时班（2 发）仅 1 班，3-4 班交叉验证窗口未到，
  按纪律跳过（如实记非缺口）。
- **A2 词形缺口补查**：手工补跑 `digital+employee` 1 发——StaffDeck 1,967 已录
  仍居首/crewmeld 948 已录/opsrobot 135/OctoGuard 102 微型零新面孔；脚本↔
  keywords.md A2 词形缺口维持在案（脚本修改交后续班处置）。
- **雷达 C 全过**：awesome 17 源 repos 实测全 alive 零 archived（ComposioHQ
  76,571/punkpeye 95,861/Shubhamsaboo 140,827 微增，vijaythecoder 4,389/
  caramaschiHG 1,921 停更观察维持）；topic 8 页（pushed:>10-01 过滤）候选 16
  件复核全已录零新面孔（nanobot 48,813/agenticSeek 27,433/eigent 15,459/
  cc-switch 140,368/archify 78,307 大星件均在档）；npm 两查已录族为主+微型
  新生件（bdb-agent-orchestrator 系 Untrivial fork、bizar harness 形态）零
  接入级；**trendshift 可达**（200，29 仓）已录族为主，候选 3 件 repos 定性
  （VulnHunter 351★ B1 域旁判据/lcu 560 已录复认/12306-mcp 2,094 域外）；
  禅道周边 2 查**第 19 例零新禅道 AI 竞品**；pypi 省配额未复试、Trending 直抓
  未行（trendshift 补位）。

### 本班新面孔（全微型判据级/生态跟踪级，不过三门槛如实记）

- **mini-yifan/dsh-orb-cordis**（155★，10-06 push，A1u 捞出+repos 二次实证）
  新入库 | Deepseek Harness 悬浮球插件 | **DSH 生态扩散第 3 信号**
  （dsh-plugins-store 68★/Tulpa 152★ 之后）；dsh 已接对应，插件生态跟踪即可，
  不构成接入项 | 雷达（DSH 生态） | 2026-10-06
- **alphaparkinc/genpark-agent-semantic-cache-manager-skill**（9★，A3 捞出）
  新入库 | 语义 prompt 缓存+命中追踪技能包 | modelhub chat cache_ttl 精确
  匹配缓存半已有；微型非六源可直装三问不过 | 判据（A3 域旁） | 2026-10-06
- 微型批：PhosAQy/novel-skills 16★（写作域微型）/integry/propr 14★
  （self-hosted GitHub orchestration）/Moeeryani/Vibe-Coding-Production-Kit
  35★/Kris77z/web-experience-cloner 64★/dongdongunique/EvoSynth 60★（域外）/
  chujian66688/Tuling-Ai 65★（中文 RAG 多智能体） | 判据 | 2026-10-06

### 存量复查（repos 端点 39+3 仓，16:1x-16:3x，全 alive 零 archived）

头部 orca 86,074→**86,082（+8 续领跑）**/superpowers 295,753→295,765/
mattpocock/skills 277,303→277,323（差 18,442）/ECC 273,814→273,828/ponytail
156,217→156,237/claude-mem 96,759→96,774（放量续）/hermes-agent 251,513→
251,521/opencode 211,927→211,930/pi 112,811→112,817/anthropics-skills
179,831→179,839；放量族 **Strata 14,518→14,568（+50 续）**/**rea 6,334→6,383
（+49 续）**/iFixAi/OpenMontage/t3code/autoharness/answer-me-with-html/
yomiyasu/uber-ADR/nexu-io 微增；记忆域（批2 重点）beads 27,658/gascity
1,329/**magic-context 2,270 持平（10-06 push 攒批标的）**/hippo-memory 772/
context-mode 25,494→25,496/bernstein 1,404/herdr 42,550→42,558/SkillSpector
19,497/open-code-review 43,919→43,921/**Memori 17,074→17,076（+2 攒批标的）**/
nautilus-compass 1,168/agentmemory 29,157→29,169；写作域 DeepSeek-Reasonix
35,739（E 候选首位）/winnow 102 持平/drama-skills 2,532→2,533/oh-story
7,275→7,277/webnovel-writer 7,327/ainovel-cli 2,097/huobao-drama 15,780——
增量全个位数到几十微增零状态变更（vs 15 时班间隔 <1h 独立复测） | 复查 |
2026-10-06

### 覆盖矩阵与七专项（独立实证）

- **覆盖矩阵**：14 指令项一一映射（直接执行=direct/扫榜选材=rank_scan/禅道
  工单=defect_retro 等）+4 自研=BUILTIN_FLOWS **18** import 实测吻合（18 id
  逐一列出：article/bid_doc/code/defect_retro/direct/doc/email/novel/
  presentation/rank_scan/research/resume/serial_novel/speech/tech_proposal/
  translation/video_script/weekly_report）；**「13 种」vs 列名 14 个差异说明：
  真实注册表 18 型，指令 14 个名称全有对应，另含 bid_doc/presentation/doc/
  resume 4 型自研，无遗漏无擅自省略**；对话/知识库/文档由 A10 chatbot-memory/
  A11/A8 词组+批2/批5 域跨轮覆盖（本班批2 域即对话记忆主扫域）。 | 巡检 |
  2026-10-06
- **七专项**（本班轻量实测）：A token 八锚点在位（pipeline :61/:613/:632/
  :655+:687/:1526-1529/:2538/:2581+compaction :33/:42/:119/:182+usage :117
  cached+step_runner :28 :80 事前门+modelhub :3673 cache_ttl；cache_control
  全 core 零命中维持）| B 六源在位（market_remote.py:50）+SSRF getaddrinfo
  :125+六源缓存 fetched_at 全 2026-10-03 零漂移+本班候选三问全不过零接入 |
  C 18 实数 | D lessons=72 零漂移（流程规范 26=36.1%/节奏爽点 21/情节逻辑 10/
  人物塑造 7/一致性 4/文笔风格 4）零新蒸馏；packs 状态条目 43 系运行时状态
  口径与历班 packs=3 口径不同如实记 | E DEFAULT_CATALOG=14、六候选 which 全
  MISSING 零接入防死链（dsh=已接对应，dsh-orb-cordis 系插件生态信号） |
  F poll 键缺失/claims=0/last_error 空/last_scan 停 2026-09-21（部署缺位多班
  同口径）全程只读+第 19 例 | G 过时文案 grep 零命中 | 巡检 | 2026-10-06

### 落地件与发版判定

本班零代码件：调研沉淀三件（knowledge.md 本班节+current-round.md 本班节+
2026-10-06.md 本班节）——**docs-only 不发版**（05/06/01/04/09/10/12/13/15
时班先例）。

### 待深挖队列（16 时快照）

21 时快照 11 项全部维持（第 3/10 项保持划掉）；本班新面孔均微型判据级/
生态跟踪级零新队列项。风险在档维持（交人拍板）：data/zentao.json 明文密码；
portscan 真实杀进程用例挂死；32 位全量 discover 静默退出；runner_drain 计时
超界。

### 未验证项（如实记录）

pypi 未复试（历班六种拦截形态在案）；Trending 直抓未行（trendshift 补位）；
WebSearch 串行未行（距 15 时班 1 班窗口未到，纪律跳过）；批2 域首见微型件
README 机制面深读未行；禅道定时扫描未触发真实工单验证（不为验证制造真实
Bug）。

## 2026-10-06 16 时巡检班（新一轮计划第 2/4 步·七专项 A-G 实锚+落地提案，16:23 开工）

> 分支 main（ac15ee1，v0.1.88 已发版）。七项巡检全实证落 full-type-round.md 本日
> 16 时班节（行号全部本班独立实读）；工作区在制三 docs 未触碰。零代码改动，
> 落地提案两件只记录待评审（第 3 步确认清单）。

### 七专项快照（本班独立实测）

- **A token**：八锚点全在位——pipeline :61 _ensure_budget/:613 _budget_max_tokens/
  :632 _budget_cost_caps/:1529 cascade（capability.cascade_reorder）/:2538
  _shrink_context_block/:2581 _serial_shrunk_block（消费 :3676 非赛马重试+:3801
  赛马复赛）/stable_order :3141-3142+:3595-3598/diff 评审 _git_diff :948+
  _review_depth_note :1019（消费 :1089）+_scope_note :1054；token_meter 双路径
  :723/:743+花费闸先于 token 闸 :677+cached 细分 usage.py :117-120；
  `cache_control|semantic_cache|prompt_cache` 全 core grep 零命中维持——四方向
  判定维持（prompt 缓存供应商侧+语义缓存候拍板+diff-only 已满配+cascade 分流
  已满配），零重复建设 | 已覆盖 | 2026-10-06
- **B 市场**：六源 SOURCES :50-76 在位；缓存 **810** 逐源实测（zcode 26/anthropic
  315/anthropic-skills 5/claude-skills 99/clawhub 215/cocoloop 150，fetched_at
  2026-10-03 与 01 时班零漂移）；SSRF assert_public_url :113+白名单
  inspect_tree :568（:588 白名单外不参与）+typosquatting :131/:294+装后冒烟
  :340-357 全在位；本班候选（agnix 440 配置 linter/dsh-plugins-store 68/kodus-ai
  1,449 AGPLv3/pr-agent 13,273 迁移勘定/nautilus-compass 1,134）三问全不过
  零接入零绕闸 | 已覆盖 | 2026-10-06
- **C 类型**：BUILTIN_FLOWS=18 实读（flows.py:36-134 逐条）；14 指令项映射零缺失
  +4 自研；content_workflow :156 轻量 {email,weekly_report,translation} :163/
  深度 {novel,research,tech_proposal} :164/threshold≥8.5 强制双评审 :194 与菜单
  描述一致；i18n 18 中文名+branches 编辑器 EN 键全在；守卫复跑
  test_full_type_round 5/5+test_i18n_dups 3/3 绿（TUTTI_DATA 隔离） | 巡检 | 2026-10-06
- **D 经验**：data/skills.json lessons=**72** 零漂移（流程规范 26=36%/节奏爽点 21/
  情节逻辑 10/人物塑造 7/一致性 4/文笔风格 4）；标题完全重复 0；scope serial_novel
  46/code 11/* 10/direct 4/article 1；packs=3；零并项零删除零批量重写 | 数据卫生 | 2026-10-06
- **E 新 CLI**：DEFAULT_CATALOG=**14** import 实测（detect cli 逐条核）；本机
  13/14 在装（openclaw MISSING；codebuddy 探测名 cbc）；六候选
  （deepseek-reasonix/reasonix/fuxi/gitlawb/zero/empryo）command -v 全 MISSING
  ——零接入防死链维持 | 巡检 | 2026-10-06
- **F 禅道**：承 13 时班勘定口径（config 内层 poll_enabled=False 显式关+产品档案
  在位，「顶层无键≠未配置」）独立复证成立；**新增实锚**：产品 96 backend workdir
  E:\GitLab\cbc\mo-so 本机实存（路由非死链）+设置页禅道动作五件（app.js:1012-1017）；
  claims=0 零积压/last_error 空/last_scan 停 09-21（poll 关所致非漂移）；调度链
  automation :580-581/main :3438/:1779 scan 在位；全程只读零写回零触发 | 巡检 | 2026-10-06
- **G 产品**：过时文案 grep（13/14/16/17 种+单源）i18n/index/app.js/README 零命中；
  README:135「18 种任务类型」与注册表一致；rank_scan 四平台三向一致（flows :82↔
  paihang :97-102↔README）；**22 时班 G 项销账**：serial.branches UI 编辑入口已
  收口（任务表单 f-branches :769/:3192/:3957+流程编辑器 fl-branches :10936/:10982
  +i18n EN 全在位） | 巡检 | 2026-10-06

### 落地提案（第 3 步确认清单，评审通过再实现）

- **提案 1·连载起草相关历史章节推荐（队列第 4 项关键词版）** | story_tracking
  每章 facts/characters/foreshadowing（story_tracking.py:125 commit_chapter）已
  持久化但不参与起草注入，前情只看近 2 章窗口+findings 摘要（pipeline.py
  :3394-3413）；拟 pipeline.py 新增 _related_chapters_note（纯本地关键词重叠计分，
  零 LLM 零存储写入）接进 prev 组装尾（tracking_state 已在作用域 :3082，ch 大纲
  :3341）；验收=隔离夹具命中/零匹配空串两用例+serial_ctx_shrink 4 项+
  race_ctx_shrink 不回归；非 embedding 版（队列第 1 项基建另案不混入）| 提案 | 2026-10-06
- **提案 2·蒸馏 1 条入经验库** | {「竞品勘定必须 repos 端点二次实证——新闻/搜索
  面与开源仓存续脱节（仓名迁移/属主消失/商业闭源），主扫与 WebSearch 捞到的名
  一律 repos 复核 stars/push/正主再入对照表（PR-Agent qodo-ai→The-PR-Agent 迁移
  第 4 例）」，scope="*"，category=流程规范}，upsert_lesson 通道（skills.py:421）；
  验收 lessons 72→73、闭集落类、标题零重复 | 提案 | 2026-10-06

### 待深挖队列（16 时巡检快照）

21 时快照 11 项全部维持（第 3/10 项保持划掉）；本班零新队列项（提案 1/2 走
第 3 步评审门，不入队）。风险在档维持（交人拍板）：data/zentao.json 明文密码；
语义缓存；全局一致性评审容量降级（22 时班录，本班复核仍在）；portscan 真实
杀进程用例挂死；32 位全量 discover 静默退出；runner_drain 计时超界。

### 未验证项（如实记录）

禅道内网实例可达性与扫描链路未验证（poll 显式关闭+不为验证制造真实 Bug，
本班连实例可达性探测也未做——避免触发真实请求）；六候选 CLI 未装未实测；
pypi 未复试（历班六种拦截形态在案）；B 候选五件 README 机制面深读未行
（接入三问已足裁零接入）。

## 2026-10-06 18 时班（批4：治理/安全/人机协同——新一轮计划第 1/4 步全类型调研）

> 开工实录：18:38（UTC+8，hour=18，18%7=4 → 轮换批4）、分支 main（ac15ee1），
> 工作区在制品 = 上一轮计划落地件（pipeline.py 提案1 + tests 增补 + skills.json
> lessons 72→73 提案2〔gitignore 不显〕）与本日 15/16 时班 docs 三件（未提交）
> ——本班增量追加逐字不动。通道：gh api 认证可用，串行 sleep 4s 零限流零失败。
> 证据底稿全文见 full-type-iteration.md「18 时班」节。

### 主扫与雷达 C（完成状态与来源）

- **主扫**：`scripts/borrow_scan_nightly.py` 无参全量（自动选批 B4）——A 常驻
  89（含内置 B1 11）+ B4 轮换 10 + A1u/A1p2 双轮 16 = **115 查询 524 行 468
  唯一仓，零失败零限流**（DONE 115 / errors: 0 实证）。
- **主扫面过筛**：468 唯一仓全名+简称双通道对全历史过筛——首见 150 件域外
  噪声与教学老件为主，**批4 域首见仅 2 件全噪声（OpenWrt 配置/2017 停更仓）
  ——批4 域零机制级新面孔（稳定期延续）**。
- **A2 词形缺口补查**：`digital+employee` 手工 1 发（脚本↔keywords.md 缺口
  维持）——zeenie-ai/OpenCompany 983★ 新面孔/crewmeld 950 复认/StaffDeck
  1,967 仍居首。
- **WebSearch 串行 2 发**（距 15 时班 3 班窗口到达，批4 域定向）：①guardrails/
  governance——已录 Aegis（Justin0504，387→**493** 属主勘定）家族复认、
  **protectai/rebuff 1,527★ archived 2024-08-07 死链判据新入库**、Galileo
  Agent Control（+Cisco）主仓未勘定不定性、SingGuard 96★ 微型；②HITL——
  LangGraph interrupt() de facto 复认、cloudwego/eino 13,248★ 已录复认、
  RunAgents 主仓未勘定不定性。
- **雷达 C 全过**：awesome 17 源全 alive 零 archived（buildwithclaude 属主
  勘定=davepoon 3,593★）；**trendshift 可达（200，25 仓）零新面孔**——候选
  3 件全已录：Agent-Reach 84,882→**92,313（+7.4k 放量）**/vibe-wise 753→
  **2,076（近 3 倍）**/e2e 3,840→5,451；topic 8 页候选 16 件全已录零新面孔
  （cli-agent-orchestrator 1,334→1,389 续增复认）；npm 两查已录族+微型新生件
  零接入级；**pypi 200 但空壳页（3KB 零节点）——历班六种拦截形态后第七形态，
  如实记受阻**；禅道周边 2 查**第 20 例零新禅道 AI 竞品**；Trending 直抓未行
  （trendshift 补位）。

### 本班新面孔（全微型判据级/生态跟踪级，不过三门槛如实记）

- **dsh-market/dsh-market**（5,625★，10-06 push）新入库 | DSH 内可视化插件
  市场（browse/search/一键安装） | **DSH 生态扩散第 4 信号**（68/152/155 之后
  首件千级体量）；dsh 已接对应，生态跟踪即可 | 雷达（DSH 生态） | 2026-10-06
- **luongnv89/asm**（951★，10-06 push）新入库 | universal skill manager for
  AI coding agents | skills 管理面与 skills-manager 5,596 同族 +1，三问不过
  | 判据（B 专项旁） | 2026-10-06
- **zeenie-ai/OpenCompany**（983★，10-05 push）新入库 | Self-improving AI
  Employees | digital+employee 词形第二大件（StaffDeck 1,967 后）；自改进与
  经验库同向、形态域旁 | 判据 | 2026-10-06
- **botiverse/oar**（169★，10-06 push）新入库 | Open Agent Runtime——所有
  harness 的编程接口抽象 | agent-client-protocol 同路人；「编排面是产品不是
  协议」口径不变 | 参考（雷达） | 2026-10-06
- **protectai/rebuff**（1,527★，**archived 2024-08-07**）新入库 | prompt
  injection 检测标杆主仓归档 | 批4 域死链判据：该域活跃度向 runtime 治理件
  （stop-that-shit/Aegis）迁移 | 判据（死链） | 2026-10-06
- 微型批：aitrustcommons/governance-framework 2★/agentrust-io
  awesome-ai-governance 56★/SingGuard 96★+NSFA 69★/awesome-llm-guardrails
  0★/research-workflow-assistant 26★/wuu 50★ | 判据 | 2026-10-06

### 存量复查（repos 端点 45+ 仓，18:3x-19:2x，全 alive 零 archived）

- 头部：orca 86,082→**86,173（+91 续领跑）**/superpowers 295,802/mattpocock
  skills 277,418（差 18,384）/ECC 273,909/ponytail 156,353/claude-mem 96,843
  （放量续）/hermes-agent 251,545/opencode 211,944/pi 112,842/anthropics-
  skills 179,855。
- 放量族：Strata 14,818（+250 续）/**rea 6,648（+265 加速续）**/iFixAi
  21,440/OpenMontage 64,435/t3code 25,766/autoharness 8,137/answer-me-with-html
  1,591/yomiyasu 1,566/uber-ADR 1,823/nexu-io 99,636。
- 记忆/治理域（批4 重点）：stop-that-shit **2,492（+333 放量）**/FailproofAI
  **5,244（+326，属主勘定）**/baml 9,381（+181）/cordum 510/edict 16,972 持平/
  plano 7,078 持平/**nautilus-compass 1,221（+53）**/beads 27,662/context-mode
  （属主勘定=mksglu）25,511/herdr 42,575/SkillSpector 19,513/open-code-review
  43,945/Memori 17,076 持平；Octopoda 属主未勘定（404×2 冻结，如实记）。
- 写作域：DeepSeek-Reasonix 35,741（E 候选首位）/winnow 102 持平（10-06
  push）/oh-story-claudecode 7,289（属主勘定：全名实为 -claudecode 后缀）/
  webnovel-writer（=lingfengQAQ）7,329/ainovel-cli（voocel）2,100/drama-skills
  2,539/huobao-drama 15,782——微增零状态变更；三处属主勘定，00 时班 in:name
  规则再证。
- 已录放量复认：cockpit-tools 18,666/**tuios 3,716→4,913（+1.2k 多 CLI 窗管
  拥挤度再证）**/skills-manager 5,596/awesome-dsh-plugin 17,875/dsh-web 8,417。

### 覆盖矩阵与七专项（独立实证）

- **覆盖矩阵**：14 指令项一一映射注册类型+4 自研=BUILTIN_FLOWS **18** 本班
  import 实测吻合（18 id 逐一打印对上）；「13 种」系指令口径、注册表 18 型
  无遗漏；对话/知识库由 A10 chatbot-memory/A11/A8+批2/批5 跨轮覆盖。 | 巡检 |
  2026-10-06
- **七专项**（本班轻量实测）：A 锚点抽查在位（cascade :1529/_shrink :2538/
  _PRECHECK_RATIO :28+:80；在制品 _related_chapters_note :2595 系纯本地计分
  零 LLM，随降级块走不新增预算口子）| B 六源 :50 在位+本班候选三问全不过
  零接入 | C 18 实数 | **D lessons=73（72→73，流程规范 26→27）——前班提案2
  已落库（data/ gitignore 故 git status 不显，如实记）**、其余分类零漂移、
  packs=3 | E DEFAULT_CATALOG=14、六候选 which 全 MISSING 零接入防死链
  （dsh=FOUND 已接） | F claims={}/last_error 空/last_scan 停 09-21 多班同
  口径+第 20 例 | G 过时文案 grep（13 种/单源）i18n/index/app.js/README 零
  命中 | 巡检 | 2026-10-06

### 落地件与发版判定

本班零代码件：调研沉淀两件（full-type-iteration.md 新建底稿+knowledge.md 本班
节）——**docs-only 不发版**（05/06/01/04/09/10/12/13/15/16 时班先例）；
keywords.md 零调整（无新关键词依据）。

### 待深挖队列（18 时快照）

21 时快照 11 项全部维持（第 3/10 项保持划掉）；本班新面孔均微型判据级/生态
跟踪级零新队列项。风险在档维持（交人拍板）：data/zentao.json 明文密码；
portscan 真实杀进程用例挂死；32 位全量 discover 静默退出；runner_drain 计时
超界。

### 未验证项（如实记录）

pypi 搜索结果不可得（200 空壳第七形态）；Trending 直抓未行（trendshift 补位）；
Galileo Agent Control/RunAgents 主仓未勘定（0★ 镜像，按规则不定性）；Octopoda
属主未勘定（冻结）；批4 首见微型件 README 深读未行；禅道定时扫描未触发真实
工单验证；在制品（提案1/2）功能级验证未行（属上一轮计划第 4 步，本班不越界）。

## 2026-10-06 19 时班（批5：检索/知识/浏览器——新一轮计划第 1/4 步全类型调研）

> 开工实录：19:01（UTC+8，hour=19，19%7=5 → 轮换批5）、分支 main（ac15ee1），
> 工作区在制品=15/16 时班 docs+16 时巡检班提案 1/2 落地件（未提交）——本班增量
> 追加不动在制品。**并行班**：18 时班（批4，18:38 开工）knowledge.md 节在会话期间
> 落盘，本班记录已对其去重对齐（信号编号顺延/复认改口径/存量基准改 vs 18 时班）；
> 其底稿自称见 full-type-iteration.md「18 时班」节实未落盘，本班以该文件承载 19 时班
> 底稿全文并补记缺位。通道：gh api 认证可用，串行 sleep 4s 零限流零失败。

### 主扫与雷达 C（完成状态与来源）

- **主扫**：`scripts/borrow_scan_nightly.py` 无参全量（自动选批 B5）——A 常驻
  89（含内置 B1 11）+ B5 轮换 10 + A1u/A1p2 双轮 16 = **115 查询 524 行 463
  唯一仓，零失败零限流**（DONE 115 / errors: 0 实证）。
- **主扫面过筛**：463 唯一仓全名+简称双通道对全历史过筛——313 已录/150 首见，
  首见以域外噪声与微型件为主；**批5 域头部全已录**（firecrawl 189,045/dify
  157,945/langchain 147,492/ragflow 91,724/gpt-researcher 29,927/DeepResearch
  20,012/dzhng 19,761/BrowserSkill 8,191）——**批5 域稳定期延续零机制级新差量**。
- **A2 词形补查**：手工 1 发——StaffDeck 1,967 居首复认/zeenie-ai/OpenCompany
  983（18 时班新入库）复认/微型新生件 bytefolk/digital-employee 21★ 等零新大件。
- **WebSearch 串行 1 发**（口径如实记：距 18 时班 1 班窗口未到本应跳过，补位理由
  =18 时班 2 发系批4 域、批5 域自 12 时班后无定向覆盖）：deep research 2026——
  Tongyi DeepResearch（=已录 Alibaba-NLP/DeepResearch）/hermes-agent（已录）/
  NinjaTech SuperNinja（闭源非仓）全已录零新面孔，与主扫双通道一致。
- **雷达 C 全过**：awesome 18 源 repos 实测 17 alive 零 archived——
  **属主勘定 2 件：wshobson/awesome-claude-skills 404→正主 ComposioHQ/
  awesome-claude-skills 76,575★（16 时班「ComposioHQ 76,571」系此仓）；
  awesome-ai-agents 正主=e2b-dev 30,276★（知识库此前未录属主）**；trendshift
  root 200 可达（/repositories 路径 404 系改版，根页捞 30 仓）候选 25 件 repos
  逐一实证——6 件已录放量续（Agent-Reach 92,322/openhuman 41,054/odysseus
  90,767/moli 10,251/e2e 5,473/vibe-wise 2,088）；topic 8 页（claude-skills 一次
  TLS 抖动补跑成功）候选全已录零新面孔（agent-swarm 860/atlas 9,193/ui-ux-pro-max
  133,471 等）；npm 两查已录族+微型新生件；pypi 复试同形受阻（200 空壳第七形态
  复认）；禅道周边 2 查**第 21 例零新禅道 AI 竞品**（18 时班第 20 例顺延）。

### 本班新面孔（rtk/agent-skills 两件大星首见为本科主要增量）

- **rtk-ai/rtk**（82,496★，10-06 push，trendshift 捞出+README 速读）新入库 |
  「Rust Token Killer」：hook 改写 Bash 命令（git status→rtk git status）
  **命令层确定性预压缩**，agent 读入前省 60-90% 输出 token；rtk gain 节省看板；
  16+ agent 适配（claude/codex/gemini/kimi/pi/hermes…） | 我们 compaction 系
  结果回来后剪枝（三段压缩），rtk 把压缩**前移到命令层**零 LLM——互补路线；
  属 agent 客户端 hook 层非编排台可直读、非六源直装，三问不过 | **借鉴方向
  （A3 token：压缩前移，是否立项交第 3/4 步评审）** | 2026-10-06
- **addyosmani/agent-skills**（101,666★，10-03 push，trendshift 捞出+README
  速读）新入库 | 生命周期 9 命令 skill 包（/spec→/ship）+/build auto 一次批准
  全程自主（TDD+逐任务提交+失败暂停） | 与 superpowers/ECC/mattpocock-skills
  同族；编排/闸门/任务档案全有对应；「按阶段自动激活技能」与经验召回同向 |
  已覆盖（族）；好 SKILL 精华走经验库蒸馏通道 | 2026-10-06
- **luckyPipewrench/pipelock**（921★，10-06 push，A1u 新锐轮）新入库 | agent
  egress/MCP/A2A 流量防火墙 | 批4 域旁判据（18 时班批4 主扫未及——其经新锐
  通道而非批4 词组） | 判据 | 2026-10-06
- **open-mercato/cezar**（502★，10-06 push，A1u 新锐轮）新入库 | 多 CLI 并行
  编排 ADE（Claude Code/Codex/OpenCode/Pi） | A1 域同形态新锐又一例 | 雷达 |
  2026-10-06
- **nmaych/dsh-mobile-connect**（0★，10-06 push，自家 CLI 周边）新入库 | 手机
  同 Wi-Fi 直连电脑 DSH | **DSH 生态扩散第 5 信号**（18 时班记 dsh-market
  5,625 为第 4）；dsh 已接对应跟踪即可 | 雷达（DSH 生态） | 2026-10-06
- 微型批：oxylabs/oxylabs-ai-studio-py 3,398（已录 oxylabs 同族商业抓取客户端
  新仓）/corezoid/corezoid-ai-plugin 72（CC 插件市场）/devoxx/
  DevoxxGenieIDEAPlugin 684（IDE 本地 LLM 插件）/BruceLanLan/augur 624（本地
  优先投资研究记忆，批5 域旁）/op7418/guizang-product-video-skill 691（短视频
  脚本类型 skill 生态：复用产品组件做更新宣传片）/alchaincyf/huashu-art-motion
  232（艺术动画 skill）/genpark-video-script-storyboard-generator-skill 9
  （genpark 族）/haogetruth/haoge-skills 10/hiroaqii/gitframe 28/Meltype 333/
  backburner 705（iPhone 协同本地推理域外）/StoryForge 1★（CC story runtime）
  | 判据 | 2026-10-06

### 存量复查（repos 端点 30 仓，19:1x-19:3x，29 alive 零 archived，基准 vs 18 时班）

- 头部：orca 86,173→**86,183（续领跑）**/superpowers 295,802→295,809/mattpocock
  skills 277,418→277,439（差 18,370）/ECC 273,909→273,923/ponytail 156,353→
  156,373/claude-mem 96,843→96,856（放量续）/hermes-agent 251,545→251,549/
  opencode 211,944→211,948/pi 112,842→112,848/anthropics-skills 179,855→179,854。
- 放量族：**Strata 14,818→14,853（+35 续）**/**rea 6,648→6,710（+62 加速续）**/
  iFixAi 21,453/context-mode 25,514/herdr 42,576/SkillSpector 19,514/
  open-code-review 43,950/**nautilus-compass 1,221→1,237（+16 续）**/yomiyasu
  1,568。
- 记忆/写作域：beads 27,663/agentmemory 29,172/hippo-memory 772 持平（10-06
  push）/bernstein 1,404 持平（10-06 push）/gascity 1,329 持平/DeepSeek-Reasonix
  35,742（E 候选首位）/webnovel-writer 7,330/ainovel-cli 2,100/drama-skills
  2,539/huobao-drama 15,782——增量个位数到几十零状态变更，两班独立复测互证。
- 勘定与代勘：**oh-story 改名勘定独立复证**（in:name 复现 18 时班同果
  zenstory-ai/oh-story-claudecode 7,289★）；**本班新掘 oh-story 多 CLI 端口
  扩散家族：oh-story-dsh 454★/oh-story-codex 36★/oh-story-opencode 7★**
  （写作 agent 多 CLI 移植扩散信号）；**18 时班冻结项 Octopoda 代勘**：
  in:name 首位 RyjoxTechnologies/Octopoda-OS 485★（名带 -OS 后缀是否正主
  存疑，交回原班对账，不盲猜定性）。

### 覆盖矩阵与七专项（独立实证）

- **覆盖矩阵**：14 指令项一一映射注册类型+4 自研=BUILTIN_FLOWS **18** import
  实测吻合（18 id 同 16 时班）；「13 种」系指令口径、注册表 18 型无遗漏；
  对话由 A10 chatbot-memory 组、**知识库由本班批5 主扫域**（agent rag/KB
  quality/rag eval 逐词过）覆盖。 | 巡检 | 2026-10-06
- **七专项**（本班轻量实测，工作区在制品态）：A `cache_control|semantic_cache|
  prompt_cache` 全 core grep **零命中维持**+_shrink_context_block :2538（消费
  :2582）/:1529 cascade 锚点在位，四方向判定维持；本班增 A3 域参照件 rtk
  （压缩前移）作借鉴方向候选 | B 六源 SOURCES :50 在位+本班候选三问全不过
  零接入零绕闸 | C flows=18/catalog=14 实测 | **D lessons=73（复认 18 时班
  72→73）**流程规范 27=37.0%、其余分类零漂移——16 时巡检班提案 2 已落库
  （在制品如实记不重复入库） | E 六候选 which 全 MISSING 零接入防死链 |
  F poll 键缺失/claims=0/last_error 空/last_scan 停 09-21 多班同口径，全程
  只读零触发+第 21 例 | G 过时文案 grep（13 种/单源）零命中 | 巡检 | 2026-10-06

### 词库与脚本同步

- **scripts/borrow_scan_nightly.py A2 词形补齐**：`ai+employee+OR+digital+worker`
  → `+OR+digital+employee`（keywords.md 2026-10-05 21 时班 StaffDeck 依据；
  16/18 时班两移交本班收口，1 行同步 py_compile 过） | 词库维护 | 2026-10-06
- keywords.md 本轮零新增词形（批5 域词组覆盖充分，无改进依据不动）。 | 纪律 |
  2026-10-06

### 落地件与发版判定

本班零产品代码件：调研沉淀两件（full-type-iteration.md 19 时班底稿+knowledge.md
本班节）+词库脚本 1 行词形同步（非产品代码）——**docs-only 不发版**（05/06/01/
04/09/10/12/13/15/16/18 时班先例）。

### 待深挖队列（19 时快照）

21 时快照 11 项全部维持（第 3/10 项保持划掉）；本班新面孔均判据级/雷达级零新
队列项；rtk（A3 压缩前移）交第 3/4 步评审不入队；Octopoda 勘定候选交回 18 时班
对账。风险在档维持（交人拍板）：data/zentao.json 明文密码；portscan 真实杀进程
用例挂死；32 位全量 discover 静默退出；runner_drain 计时超界。

### 未验证项（如实记录）

pypi 复试受阻（第七形态复认）；Trending 直抓未行（trendshift 补位，/repositories
路径 404 系改版观察项）；rtk/agent-skills 仅 README 头部速读未逐 commit 未实测
安装；批5 首见微型件 README 深读未行；npm 新生件无下载量核查（历班无此惯例）；
WebSearch 系窗口未到的提前补位 1 发（批5 域定向缺口理由，如实记口径偏离）。

## 2026-10-07 03 时班（批3：计划/spec/长任务——新一轮计划第 1/4 步全类型调研）

> 开工实录：03:38（UTC+8，hour=3，3%7=3 → 轮换批3）、分支 main（ac15ee1，
> v0.1.88 已发版），工作区在制品=15/16 时班 docs+16 时巡检班提案 1/2 落地件
> （未提交）——本班增量追加不动在制品。通道：gh api 认证可用，串行 sleep 4s
> 零限流零失败（DONE 116 / 524 行实证）。证据底稿全文见 2026-10-07.md
> （本班新建当日日报）；本节同步沉淀。

### 主扫与雷达 C（完成状态与来源）

- **主扫**：`scripts/borrow_scan_nightly.py` 无参全量（自动选批 B3）——A 常驻
  89（含内置 B1 11）+ B3 轮换 11 + A1u/A1p2 双轮 16 = **116 查询 524 行 466
  唯一仓，零失败零限流**。 | 主扫 | 2026-10-07
- **主扫面过筛**：466 唯一仓全名+简称双通道对全历史——**315 已录/151 首见**；
  **批3 域 11 词头部全已录**（OpenSpec 71,163/get-shit-done 64,363/
  planning-with-files 27,308/worktrunk 8,908/ccpm 8,399）——**批3 域稳定期
  延续零机制级新差量**（批2/4/5 各域轮过均稳同型）。 | 巡检 | 2026-10-07
- **WebSearch 串行 2 发**（批3 域定向，距 19 时班 8 小时窗口已到）：①long-running
  2026 学术为主零新仓名；②spec-driven 工具向——OpenSpec/Spec Kit/BMAD 已录复认
  （OpenSpec 入 ThoughtWorks Radar），新名字 AutoSpec 勘定无大星正主
  （ariel-frischer/autospec 144★ 最贴近候选判据）、oli-torus 勘定系教育平台
  排除（libhunt 噪声）。 | 调研 | 2026-10-07
- **雷达 C 全过**：awesome 18 源 repos 实测 18/18 alive 零 archived（正主
  ComposioHQ 76,597/e2b-dev 30,279/davepoon-buildwithclaude 3,600 复认，
  mattpocock/skills 277,948 +509 放量）；**trendshift 根页改版后续可达（348KB
  29 仓），候选 23 件 repos 逐一实证**；topic 8 页候选全已录零新大件；npm 两查
  已录族+微型新生件（**@deepseek-ai/dsh-hooks-claude-code 官方 org 包在架**）；
  pypi 第七形态第 8 班复认（3KB CSP 壳页零 snippet）；禅道周边 2 查**第 22 例
  零新禅道 AI 竞品**。 | 雷达 | 2026-10-07

### 本班新面孔（OpenMontage/diagram-design/bifrost 三件借鉴方向为本科主要增量）

- **calesthio/OpenMontage**（64,627★，10-03 push，trendshift 捞出+README 速读）
  新入库 | 首个开源 **agentic 视频生产系统**：贴参考视频/提示词驱动、**12 条
  生产管线**、自带 AGENT_GUIDE+PR_REVIEW_GUIDE | 短视频/改编域我们只有脚本
  文字流，其端到端渲染系重全栈非六源直装；「管线化组织生产」与 flow 编排
  同构 | **借鉴方向（A12 分镜管线化参照，交第 3/4 步评审）** | 2026-10-07
- **cathrynlavery/diagram-design**（43,908★，10-06 push，trendshift+README
  速读）新入库 | 编辑级图表 **skill 包**（CC/Codex/Droid/Pi/Agent Skills 兼容）：
  自包含 HTML+SVG、**25 布局语法**、**语义模式与布局分离**、Loop 共享记忆
  中枢+回写、可重绘 Mermaid/Excalidraw | 技术方案/演示文稿/汇报的图表产出
  系我们增量面；skill 自包含可直读 | **借鉴方向（doc/presentation 域，交
  第 3/4 步评审；若入 skill 市场源可复议直装）** | 2026-10-07
- **maximhq/bifrost**（8,585★，10-06 push，trendshift+README 速读）新入库 |
  企业 **AI 网关**：23+ provider 单一 OpenAI 兼容 API、自动故障转移、自适应
  负载均衡、**语义缓存**、自称 50x LiteLLM（maximhq=testcontainers 团队） |
  A3 token 专项「语义缓存」方向的成熟网关实现实参照；CodeBee 非网关部署
  形态、落点在 modelhub 侧机制借鉴 | **A3 语义缓存实参照入库（借鉴方向
  候选，交第 3/4 步评审）** | 2026-10-07
- **heymrun/heym**（1,405★，10-06 push，A1u 新锐轮）新入库 | self-hosted
  agent 编排平台：可视化编排+AI 生成工作流+审批检查点+模型成本可观测 |
  A1 域直接同形态竞品；审批检查点=闸门族、成本观测=token_meter 族，机制面
  我们全有对应 | A1 对标件（雷达） | 2026-10-07
- **InternScience/InternAgent**（1,446★，10-06 push，B3 主扫）新入库 |
  InternAgent-1.5 长程自治科学发现框架（书生系）：论文自主复现+**记忆模块**
  +deep research | B3/A10 交叉域垂直 agent，长程规划+记忆与经验召回同向 |
  判据 | 2026-10-07
- **Ebony-Vinyl/dsh-our-free-model**（2,170★，10-06 push，trendshift）新入库 |
  dsh 插件免登录用 DeepSeek V4.1 Flash/Kimi K3 | **DSH 生态扩散第 6 信号**
  （19 时班第 5 顺延）；dsh 已接跟踪即可 | 雷达（DSH 生态） | 2026-10-07
- **storytold/photocraft 4,583 + filmcraft 1,189**（10-06 push，trendshift）
  新入库 | Rust **纯净室重实现** Photoshop/Premiere | 创作工具域旁，「纯净室
  重实现」路线系工程信号 | 雷达 | 2026-10-07
- **omnirush-ai/omnirush-gui**（2,159★，10-06，trendshift）新入库 | 桌面
  coding agent+免费前沿模型 | A4/A13 同形态，「免 Key 前沿模型」与
  dsh-our-free-model 同族 | 雷达 | 2026-10-07
- 微型判据批：earthtojake/text-to-cad 17,873（agent CAD，A5 旁）/
  nealbridges/VulnHunter 515（agentic 攻击面安全扫描，B1 旁）/GetBusbar/busbar
  173（agent 执行控制面，B4 旁）/termide/termide 171（Rust 终端一体台+内置
  agent）/malevrigns/atlas-agent-control-plane 106（垂直控制面「证据可指」
  =proof-gated 族）/ariel-frischer/autospec 144（SDD CLI，WebSearch 勘定最贴近
  候选）/apache optaplanner 3,515+TimefoldAI/timefold-solver 1,812（约束求解
  排程，B3 域旁确定性规划器）/ponytail 生态扩散 ponytail-hermes 38+
  dsh-ponytail 17（**多 CLI 移植扩散信号**，oh-story 家族同型） | 判据 |
  2026-10-07
- 排除件：oli-torus（Simon-Initiative 教育平台，libhunt BMAD 替代 claim 噪声）；
  域外批（HowToLiveBetter 46,481/spotifast/openGym/DroidDeck/AnyPS5/
  bloodborne_pc/esp32-c3-adblock/hongguo-desktop-releases/onedump/gosnakego/
  nginx-bot-blocker/YoudaoTranslator/ComfyUI 扩展/self-dify/opencode-primer/
  LockKnife 等如实记）。 | 判据 | 2026-10-07

### 存量复查（repos 端点 28 件，03:4x-04:1x，26 alive 零 archived，基准 vs 19 时班）

- 头部：orca 86,183→**86,441（+258 续领跑）**/superpowers 295,809→295,970/
  mattpocock-skills 277,439→**277,948（+509 放量）**/ECC 273,923→274,185/
  claude-mem 96,856→**97,055（+199 放量续）**/hermes-agent 251,549→251,656/
  opencode（anomalyco 实证存活）211,948→212,018/pi 112,848→112,932/
  anthropics-skills 179,854→179,890。
- 放量族：**rea（morluto）6,710→8,387（+1,677 大爆发，trendshift 在榜驱动）**/
  **Strata（Niko1221）14,853→15,504（+651 续）**/iFixAi（ifixai-ai 正主定案）
  21,453→21,606/herdr 42,642/open-code-review（alibaba）44,019/SkillSpector
  （NVIDIA）19,555/context-mode 25,538/nautilus-compass 1,255（+18 续）/
  yomiyasu 1,589（+21 续）。
- 记忆/写作域：beads 27,676/agentmemory（rohitg00 实证；letta 同名 404）
  29,182/hippo-memory 774/bernstein 1,415/gascity 1,331/DeepSeek-Reasonix
  35,742 持平（E 候选首位）/webnovel-writer 7,335/ainovel-cli 2,108/
  drama-skills 2,550/huobao-drama 15,790——增量个位数到三位数零状态变更，
  两班独立复测互证。
- **属主定案销账：ponytail 正主 = DietrichGebert/ponytail 156,693**（in:name
  首位+主扫 TOP30 双证；18/19 时班冻结项就此销账）/iFixAi 正主=ifixai-ai
  （in:name 首位）/opencode 正主=anomalyco/agentmemory 正主=rohitg00。
  18 时班遗留 Octopoda 冻结项不属本班勘定范围，维持交回原班。 | 勘定 |
  2026-10-07

### 覆盖矩阵与七专项（独立实证）

- **覆盖矩阵**：14 指令项一一映射注册类型+4 自研=BUILTIN_FLOWS **18 /
  DEFAULT_CATALOG=14** import 实测吻合（与 16/19 时班同口径独立复证）；
  **本班批3 主扫域**即 tech_proposal/spec 面主证据通道；对话由 A10
  chatbot-memory 组、知识库由 A2/A11+B3 spec 档案组交叉覆盖。 | 巡检 |
  2026-10-07
- **七专项**（本班轻量实测，工作区在制品态）：A `cache_control|semantic_cache|
  prompt_cache` 全 core grep **零命中维持**+_shrink_context_block :2538（消费
  :2582/:2586）/:1529 cascade 锚点在位，四方向判定维持；本班增 A3 实参照
  bifrost（语义缓存进网关成熟实现）→ 借鉴方向候选 | B 六源 SOURCES :50 在位
  +本班候选三问全不过零接入零绕闸（diagram-design 入源可复议在档观察）|
  C flows=18/catalog=14 实测 | **D lessons=73 复认 19 时班口径**流程规范
  27=37.0% 零漂移，本班无新蒸馏依据纪律不动 | E 六候选 which 全 MISSING、
  dsh 在位——零接入防死链维持 | F poll 键缺失/claims=0/last_error 空/
  last_scan 停 09-21 多班同口径，全程只读零触发+第 22 例 | G 过时文案 grep
  （13 种/单源）零命中 | 巡检 | 2026-10-07

### 词库与脚本同步、落地件与发版判定

- keywords.md 本轮零新增词形（批3 域词组覆盖充分，无改进依据不动）；脚本零改动
  （A2 词形同步系 19 时班在制品）。 | 纪律 | 2026-10-07
- 本班零产品代码件：调研沉淀两件（2026-10-07.md 新建+knowledge.md 本班节）
  ——**docs-only 不发版**（05/06/01/04/09/10/12/13/15/16/18/19 时班先例）；
  借鉴方向三件（OpenMontage/diagram-design/bifrost）交第 3/4 步评审不自行立项。
  | 发版 | 2026-10-07

### 待深挖队列（03 时快照）与未验证项

- 21 时快照 11 项全部维持（第 3/10 项保持划掉）；本班新面孔均判据级/雷达级零新
  队列项。风险在档维持（交人拍板）：data/zentao.json 明文密码；portscan 真实杀
  进程用例挂死；32 位全量 discover 静默退出；runner_drain 计时超界。 | 队列 |
  2026-10-07
- 未验证项：pypi 第七形态第 8 班复认；Trending 直抓未行（trendshift 补位，根页
  改版 /repositories 404 观察项维持）；OpenMontage/diagram-design/bifrost/heym
  仅 README 头部速读未逐 commit 未实测安装；批3 首见微型件深读未行；禅道定时
  扫描未触发真实工单验证；npm 新生件无下载量核查；AutoSpec「blueprint+dashboard」
  claim 未勘到大星实仓（ariel-frischer/autospec 系最贴近候选非定案）。 | 未验证 |
  2026-10-07

## 2026-10-07 04 时巡检班（第 2/4 步·七专项 A-G 深化实证+落地提案，03:57 开工）

> 分支 main（ac15ee1，v0.1.88 已发版）。只读巡检零代码改动零数据写；禅道全程
> 只读零触发。行号全部本班实读（pipeline.py 系在制品 HEAD+59 行态：:2587 前锚点
> 与上轮同位、之后 +59）。细目证据底稿=2026-10-07.md 04 时巡检班节（每项按
> 检查位置/证据→现状→缺口→处理结论成档）。

### 七专项快照（本班独立实测，非照抄上轮）

- **A token**：八锚点全实证——三段压缩 compaction.py:2-25（剪枝 8192/4096→摘要→
  surface replace，压力比 ≥0.8 触发）+接线 pipeline:703（resume 直通不压缩）/
  token_meter.py:53-148（cached 单列不计压力 :101-107、容量表 mtime 热改 :67-79、
  last_context 重发锚点 :129-140）/预算熔断 pipeline:61 七闸口（:412/:531/:990/
  :1463/:3250/:4871/:5498）+:613 token 帽+:632 日/月花费帽+:685-696「只拦下一步」
  ENV_BLOCK+:677 花费闸先于 token 闸/cascade :1526-1529 opt-in→capability:100-125
  （tier 稳定重排+rank_model_entries 难度选模）/经验召回 skills.block_for :619-681
  （stable_order 保前缀缓存+wildcard 两道预算+karma won≥2+outcome 加权）消费
  pipeline:3657/:3736/:3861/会话复用 pipeline:248 _get_session+resume 全链
  :206-283（四 CLI 原生+generic 模板）/diff 评审 :919-945+:948+:1019（findings
  锚定「文件:行号」pr-af+未跟踪拼合+深度分级）/_shrink_context_block :2538+
  _serial_shrunk_block :2581+前文尾截 :3043-3056；`cache_control|semantic_cache|
  prompt_cache` 全 core grep 零命中维持。四方向判定维持（prompt 缓存供应商侧/
  语义缓存候拍板——bifrost 为 A3 实参照落点在 modelhub 侧、diff-only 满配、
  cascade 满配；rtk 系客户端 hook 层非编排台层）——**零重复建设零新增机制**
  | 已覆盖 | 2026-10-07
- **B 市场**：六源 SOURCES market_remote.py:50-76 在位；缓存逐源实测 26/315/5/99/
  215/150=**810**（fetched_at 2026-10-03 与历班零漂移，本班独立求和复认）；SSRF
  assert_public_url :113 解析级拒环回/私有/保留+体量上限 :91-99+纯技能白名单
  （可执行件拒装）全在位。三问逐条：OpenMontage（非技能包不可直装→雷达）/
  diagram-design（六源缓存 0 命中不可直装，直装须绕闸禁止；cocoloop diagram-generator
  在架但元数据三同单薄装前质量存疑→零接入在档观察，「语义模式与布局分离」蒸馏
  候选交拍板）/bifrost（网关形态→雷达，语义缓存承 A 拍板件）——**三问全不过
  零接入零绕闸零安装** | 已覆盖 | 2026-10-07
- **C 类型**：BUILTIN_FLOWS=18 逐条实读（flows.py:36-134）；18 型 goal_hint+note
  全在、review 14 型参数统一（bid_doc 7.5 从严例外）、serial{8,2500}；i18n 18 型名
  EN 键逐名实测 **18/18 在位**；描述-行为抽查全过（rank_scan 四平台↔paihang:5-11/
  defect_retro↔pipeline:4917 zentao 联动/presentation 不生成 PPT 二进制）——零
  文案-行为漂移 | 巡检 | 2026-10-07
- **D 经验**：lessons=**73** 零漂移（流程规范 27=37.0%——「61%」系旧口径/节奏爽点
  21/情节逻辑 10/人物塑造 7/一致性 4/文笔风格 4）；scope serial_novel 46=63.0%
  （真实偏科在 scope 维持）/code 11/* 11/direct 4/article 1；packs=3。标题精确
  重复 0+两两包含 0 对；upsert_lesson skills.py:424-470 自带包含度 ≥0.8 自动合并
  +merged_titles 留痕（增量有闸）。**新发现交拍板**：①近重复聚簇 3 组 8 条
  （章末钩子 4/开篇钩子 2/爽点 2——语义近但 bigram <0.8 未被自动合并，合并依据
  逐条在档）；②分类错位 1 条（sk-8114e5d9a90a「做法：小红书…」挂文笔风格，
  「做法：」族其余 4 条全在流程规范）。既有接口无按 id 合并通道，本班不动数据
  | 数据卫生 | 2026-10-07
- **E 新 CLI**：DEFAULT_CATALOG=14 逐条实读（catalog.py:32-215）；本机 command -v
  **13/14 在装**（openclaw MISSING；21 时班「14 在装」与本班差 1 按实测记）；五
  候选 deepseek-reasonix/fuxi/gitlawb/zero/empryo **全 MISSING**（Reasonix 35,742★
  首位持平）——零接入防死链维持 | 巡检 | 2026-10-07
- **F 禅道**：poll_enabled=False 显式关/interval_hours=2/product_profiles=1
  （product 96→backend E:\GitLab\cbc\mo-so 本班实存 ROUTE_DIR_OK，owners 双侧在位）/
  claims=0 零积压/last_error 空/last_scan 停 2026-09-21（poll 从未启动实证）；调度
  链 zentao:2470 fire_due←automation:580-581+三闸 :2425/:2474/:2502+假日顺延
  :2381-2445+_profile_for :334；前端子页 app.js:11612-12091+main.py:1779-1810
  端点群——**代码链路全在位，缺的是部署配置非代码**；全程只读零触发；第 22 例
  零新禅道竞品顺延 | 巡检 | 2026-10-07
- **G 产品**：过时文案 grep（13 种/单源/数字类 1X 种/六源数/已接 1X 个）UI 三件
  零命中；README 18 种与注册表一致——零新毛病零随手修 | 巡检 | 2026-10-07

### 落地提案（第 3/4 步确认清单，评审通过再实现）

- **提案 1·经验库管理通道+存量并类**（**涉及删并历史数据，交人确认后执行**）：
  skills.py 新增 merge_lessons(primary_id, duplicate_ids)（merged_titles/revisions
  留痕不丢历史，won/seen 并入 primary）→ 执行 D 节 4+2+2 聚簇合并+1 条改类
  （sk-8114e5d9a90a→流程规范）；验收 lessons 73→66±、留痕全、relevance_top 不损、
  锁 73 的既有测试（若有）同步；不做范围=不动自动合并阈值/不批量重写/不做分类
  体系重构 | 提案 | 2026-10-07
- **提案 2·零代码件备选**（承上轮 03 时巡检班先例）：队列活项均攒批（第 1 项管线级
  /第 2 项语义缓存拍板+bifrost 新证/第 4 项已系 16 时巡检班在制品）或拍板/远期
  ——在册代码级「小而实」积压核对为零；本班实落地=文档沉淀两件，**docs-only
  不发版**；三件借鉴候选（OpenMontage/diagram-design/bifrost）维持交拍板不自行
  立项 | 发版 | 2026-10-07

### 待深挖队列（04 时快照）与未验证项

- 21 时快照 11 项全部维持（第 3/10 项保持划掉）；零新面孔零新队列项；第 4 项系
  16 时巡检班在制品（未提交，本班不触碰不重复立项）。风险在档维持（交人拍板）：
  data/zentao.json 明文密码；portscan 真实杀进程用例挂死；32 位全量 discover 静默
  退出；runner_drain 计时超界。**新增风险备注**：经验库存量近重复聚簇+分类错位
  （D 节，提案 1 待拍板）。 | 队列 | 2026-10-07
- 未验证项：六源缓存 fetched_at=2026-10-03（4 天前，「拉取更新」系用户动作不代按）；
  cocoloop diagram-generator 未装包核查内容；禅道定时扫描未真实触发验证（不为
  验证制造 Bug）；提案 1 合并效果未实跑；在制品提案 1（相关历史章节推荐）行为
  未复测（属第 3 步验收范围）。 | 未验证 | 2026-10-07

## 2026-10-07 05 时收口班（第 4/4 步·整体联调+五道关+推送发版，05:0x 开工）

> 分支 main（ac15ee1→e8046ce→8b2ac8b，全程未切）。收口对象=16 时巡检班落地件
> （相关历史章节推荐）+04 时落地班落地件（merge_lessons）+历班 docs/词形，逐 hunk
> 自审后原样提交未改实现。细目=2026-10-07.md 05 时收口班节。

### 五道关与测试台账（分片+净进程复验打法本轮执行完毕）

- **闸② A 片 12 例失败逐一定性+净进程全翻绿**：A 片 `test_[a-r]` Ran 1697
  FAILED（10F+2E，两跑复现非偶发）——12 例集中 test_launch(2E)/deepseek_harness/
  git_workbench/http_500_guard(2F)/mgmt_guards/mimo_injector(2F)/portscan(1F)/
  qwen_injector_guard(2F)，与本轮改动零交集；cd tests 净进程逐文件单跑
  **8/8 全 OK**（test_launch 33/33）——定性=批跑同进程测试间争用（状态污染），
  非代码回归；B 片 `test_[s-z]` Ran 505 OK；改动面净进程 25/25 OK。
  「争用豁免必须按当前 HEAD 重验」教训本轮执行完毕，14 时班台账就此清空。
  portscan『HP』!=空 系家目录归属误报在档族（单跑绿不阻闸，修复方向维持交
  拍板）| 测试 | 2026-10-07
- **发版闸 BLOCKED 行为正确+npm view 传播延迟教训**：release_gate 于发布三件
  （package.json/CHANGELOG/README）未提交时正确拦截（防未提交发版）——按流程
  先提交推送再 publish；**publish 退出 0+「being processed」后 `npm view` 连回
  旧版本（官方源直查同）≠发布失败**，系 registry CDN 传播延迟，复查窗口
  ≥3 分钟再定性（本班 3 分钟后 0.1.89 确认在架，time 元数据一致）——「先查
  远端与 npm 实际状态，勿慌 revert」纪律再次生效 | 发版 | 2026-10-07

### 提交/发版结果与队列

- 提交推送：9f07dfb feat（4 文件+272：_related_chapters_note+merge_lessons+7 用例）/
  e8046ce docs（7 文件+2023：八班调研沉淀+A2 词形）/ 8b2ac8b release v0.1.89；
  push 一次成功零 443 抖动；**v0.1.89 已发布并 npm view 核对在架**（169 文件，
  prepublishOnly 过闸）
- 待深挖队列：21 时快照 11 项维持（第 3/10 项划掉）；**新增观察项**：批跑同
  进程测试间争用面（8 文件 12 例，修复方向=测试间状态隔离，工程量大交拍板，
  当前以分片+净进程复验兜住）| 队列 | 2026-10-07
- 未验证项：12 例争用失败根因未逐例定位（只定性到同进程状态污染层）；v0.1.89
  安装侧冒烟未行（以 registry 元数据为准）| 未验证 | 2026-10-07

### 并行会话独立验证补录（2026-10-07 05 时，第 4/4 步另一通道）

- **闸②更细四分片=争用豁免面收敛**：整跑 discover 在本机受并行负载影响停在
  process-heavy 用例（race 60s 退避/portscan 杀进程）；改更细四片
  a-c(514)/d-i(405)/j-r(778)/s-z(505) 守卫驱动器跑，合计 2202 项除 d-i 两项
  净进程复绿外全绿，豁免 12→2；j-r 片 portscan 真实杀进程+runner_drain 计时
  两在档风险项**实测复绿**（口径：Ran/OK 统计行）| 测试 | 2026-10-07
- **争用第二层面=跨进程资源争用**：两路全量套件（含 release_gate）并发时
  真子进程孵化/杀收类用例假死——与收口班「同进程状态污染」定性互补；打法：
  分片互斥+检测到并行班 unittest/release_gate 进程时只等不跑，收口后独占重跑
  | 测试 | 2026-10-07

## 2026-10-07 06 时班（批6：框架/平台/SDK 生态——新一轮计划第 1/4 步全类型调研）

> 开工实录：06:38（UTC+8，hour=6，6%7=6 → 轮换批6）、分支 main（5a0d624，
> v0.1.89 已发版）、工作区干净。通道：gh api 认证可用（search 30/分实测），
> 主扫 115 查询 sleep 4s 零失败零限流（DONE 115/524 行实证）；WebSearch 串行
> 1 发（3-4 班窗口纪律内）。证据底稿全文见 2026-10-07.md 06 时班节。

### 主扫与雷达 C（完成状态与来源）

- **主扫**：`scripts/borrow_scan_nightly.py` 无参全量（自动选批 B6）——A 常驻
  89（含内置 B1 11）+ B6 轮换 10 + A1u/A1p2 双轮 16 = **115 查询 524 行 468
  唯一仓，零失败零限流**。 | 主扫 | 2026-10-07
- **主扫面过筛**：468 唯一仓全名+简称双通道对全历史——**290 已录/178 首见**；
  **批6 域 10 词头部全已录**（agents-towards-production 21,532/vercel ai
  27,151/strands harness-sdk 8,694/google agents-cli 6,058/axonhub 5,337/
  aegra 1,241/pandaprobe 784）——**批6 域稳定期零机制级新差量**（批2/3/4/5
  各域轮过均稳同型）。 | 巡检 | 2026-10-07
- **WebSearch 串行 1 发**（批6 域定向）：**OpenRig 与 Vercel eve 两个新名字
  repos 端点二次实证双双坐实大星实仓**（10-05 规则第 3/4 例落地）——本班
  主要增量见下节；Microsoft Agent Framework/AWS Strands 横评常客已录复认。
  | 调研 | 2026-10-07
- **雷达 C 全过**：awesome 18 源 18/18 alive 零 archived（awesome-claude-code
  55,161 与 VoltAgent skills 35,278 双放量）；trendshift 根页可达（353KB 29 仓）
  候选 6 件新 repos 实证；topic 8 页候选全已录零新大件；npm 两查零新大件
  （@polderlabs/bizar 微型判据）；pypi 第九班复认（CSP 壳页 3KB 零 snippet）；
  禅道周边 2 查**第 23 例零新禅道 AI 竞品**。 | 雷达 | 2026-10-07

### 本班新面孔（openrig 同形态直接竞品为历轮 A1 域罕见级新面孔）

- **mvschwarz/openrig**（5,494★，10-06 push，WebSearch→in:name 勘定+README
  深读）新入库 | **「A harness wraps a model. A rig wraps your harnesses」**
  ——harness 之上编排层：YAML 定义 agent 团队、一条命令 boot、Claude Code+
  Codex+Pi 同 rig 管理为一个系统；lead agent 协调 specialists、结果与决策
  上抛；TUI seats 表（runtime/model/context/state 四列）；持久团队/角色/共享
  上下文/owned work；npm @openrig/cli；Node22+/tmux **原生 Windows 不支持、
  WSL2 未测** | **历轮罕见的同形态直接竞品**（多 CLI 舰队编排+持久团队+面板）：
  闸门/评审/经验库/18 类型流程我们全有对应；Windows 原生系我们差异化壁垒；
  seats 四列表与 YAML 团队定义可与 flow 编排/catalog 对比 | **A1/A5 域直接
  竞品入库（对标跟踪；24h 640→5,494★ 病毒式）** | 2026-10-07
- **vercel/eve**（5,474★，10-06 push，WebSearch→in:name 勘定+README 深读）
  新入库 | **filesystem-first durable agent 框架**：agent/ 目录约定
  （instructions.md 系统 prompt/tools/ 类型化函数/skills/ 按需程序/channels/
  消息通道/schedules/ cron）——「文件系统即编写界面」；npx init+交互 TUI；
  AI Gateway 接入（Vercel Agent Stack 主件） | 目录约定即配置与我们 data/
  +flows 同向；channels/schedules 文件化声明与 automation.py 定时同域（机制
  我们已有）；durable/resume 会话复用已配 | **A2 域新面孔对标件（雷达/对标）**
  | 2026-10-07
- **storytold 纯净室全家桶扩散**（artcraft 3,150/lightcraft 1,150/printcraft
  1,042/vectorcraft 989 新+photocraft/filmcraft 已录=6 件；trendshift+repos
  实证）新入库 | Rust 纯净室重实现 Adobe/Lightroom/Acrobat/Illustrator 全家桶
  | 创作工具域旁非 agent；「纯净室重实现」路线信号续强（storytold 系组织级
  跟踪） | 雷达（创作工具域旁） | 2026-10-07
- robbietilton/Compositor（9,182★，Mac Photoshop 替代）| 创作工具域旁 | 雷达
  | 2026-10-07
- ibm/assetopsbench（2,330★，工业 4.0 统一 benchmark+orchestrating）| A6 评测
  域垂直，benchmark 形态与 evalbench 同向 | 判据（A6 垂直域） | 2026-10-07
- remorses/usecomputer（336★，computer automation CLI）| A5 域微型 | 判据
  （微型） | 2026-10-07
- labring/fastgpt（29,783★）+elizaos/eliza（19,549★）双首见 | 知识库平台/
  agentic OS 老牌大仓——**疑属主迁移复见非新项目**（历史以简称在档，本班
  双通道口径首见；未考古 git 历史如实记） | 判据（首见口径注记） | 2026-10-07
- 微型批：Akxan/ppt-agent-skill 155+code-on-sunday/slide-deck-generator 150
  （diagram-design 同域 PPT/slide skill 微型双件）/LoopTroop 159/agentic-os
  189/@polderlabs/bizar（orchestrator-first autonomy harness）等 | 判据
  （微型） | 2026-10-07

排除件：china-dictatorship（政治噪声）/nginx-ultimate-bad-bot-blocker（B1 词
域外误中）/youdaotainer/alfred-google-translate（A7 翻译词误中 Alfred 工作流）/
comfyui-to-python/wpgulp/ftc-skystone（A9 词误中机器人赛）/territory（B1 词误中
体素引擎）/coursera/unity 特效（A11 词误中课程）等域外噪声如实记。

### 存量复查（repos 端点 29 件，26 alive 零 archived；基准 vs 03 时班，间隔约 2.5-3 小时）

- 头部：orca **86,501（+60 续领跑）**/ superpowers 296,003 / ECC（affaan-m
  复认）274,261 / claude-mem 97,130 / hermes-agent 251,684 / opencode
  （anomalyco）212,040 / **pi=earendil-works/pi 112,953（badlogic/pi-mono
  302 重定向实证，属主注记更新）**。
- 放量族：**rea 9,046（+659 放量续，trendshift 驱动）**/ Strata 15,650（+146
  续）/ iFixAi 21,638 / ponytail 156,791。
- 持平族：DeepSeek-Reasonix（esengine）35,741（E 候选首位维持）/webnovel-writer
  7,335/ainovel-cli 2,109/huobao-drama 15,790/SkillSpector 19,562/open-code-review
  44,036/context-mode 25,546/beads 27,684/agentmemory 29,183/hippo-memory 774/
  bernstein 1,415/gascity 1,331/nautilus-compass 1,255/yomiyasu 1,594。
- **属主勘定（in:name 规则第 2 例批量落地，三件零悬置）**：orca 正主=
  **stablyai/orca**（oh-my-claudecode 旧属主 404）/ herdr 正主=**herdrdev/herdr
  42,653** / drama-skills 正主=**zenstory-ai/drama-skills 2,552**。 | 复查
  | 2026-10-07

### 全类型覆盖与七专项（本班只读口径）

- 14 目标类型全覆盖（矩阵见 2026-10-07.md 06 时班节）；**BUILTIN_FLOWS=18 /
  DEFAULT_CATALOG=14 import 实测**（与 03/04/16/19 时班同口径独立复证）。
- A token：零命中维持（cache_control/semantic_cache/prompt_cache 全 core
  grep）；_shrink_context_block 3 锚点在位；bifrost 语义缓存候选维持交拍板。
- B 市场：六源 market_remote.py:50-76 实读在位；本班候选均非六源直装件
  （openrig/eve 系框架、storytold 系桌面应用）——三问不过零接入零绕闸。
- D 经验：lessons=**68**（04 时班并类后新基线：流程规范 28/节奏 16/情节 10/
  人物 7/一致 4/文笔 3；scope serial_novel 41 偏科维持）——数据手术成果零回漂。
- E 新 CLI：五候选全 MISSING 零接入防死链；13/14 已接在装（openclaw MISSING）；
  @openrig/cli npm 在架本机未装不盲接（跟踪）。
- F 禅道：poll 显式关/claims=0/last_scan 停 09-21——部署配置缺位同口径；
  第 23 例零新竞品。
- G 产品：过时文案 grep 零命中零新毛病。

### 词库同步与发版判定

- keywords.md C 源「竞品名周边搜」追加 `q=openrig+OR+vercel+eve 生态`（行尾
  标注谁/何时/为何）——病毒式同形态竞品+Agent Stack 主件持续盯增量。
- 本班 docs-only 零产品代码件——**不发版**（03/05 时等班先例）；openrig/eve
  交第 2/4 步巡检班深化，不自行立项。

### 待深挖队列（06 时快照）与未验证项

- 21 时快照 11 项全部维持（第 3/10 项保持划掉）；本班零新队列项（openrig/eve
  入对标跟踪不入深挖队列——机制面全有对应，Windows 原生差异化在）。风险在档
  维持（交人拍板）：data/zentao.json 明文密码；批跑同进程测试间争用面；32 位
  全量 discover 静默退出；runner_drain 计时超界。 | 队列 | 2026-10-07
- 未验证项：openrig/eve 仅 README 头部深读未安装实测；openrig 24h 增速系
  WebSearch 报道转述未独立溯源；fastgpt/eliza 属主迁移系推断未考古 git 历史；
  mattpocock-skills 属主本班未探（悬置维持）；trendshift /repositories 子路径
  404（根页补位）；npm 新生件无下载量核查（历班无此惯例）。 | 未验证
  | 2026-10-07

## 2026-10-07 07 时巡检班（新一轮第 2/4 步·七专项增量复核+openrig/eve 三问深化）

### 七专项增量复核结果（全部只读，与历班口径比对）

- A token：零命中维持（cache_control/semantic_cache/prompt_cache 全 core grep
  独立复测）；四锚点复锚（_compaction_enabled:231/:703、cascade_reorder 消费
  :1529、_shrink_context_block:2538/:2582/:2586 收口提交后态）；四方向判定
  维持零新增机制，bifrost 拍板件不变。
- B 市场：六源缓存逐源实测 810（catalog.plugins 求和：zcode 26/anthropic
  315/anthropic-skills 5/claude-skills 99/clawhub 215/cocoloop 150，fetched_at
  2026-10-03 零漂移）。**openrig 三问闭环：不可直装（缓存 grep 零命中）+不可
  运行（tmux 硬依赖+Native Windows not supported，装亦死链）+重合度虽高但
  机制面全有对应——零接入零绕闸，seats 四列表+YAML 团队定义记借鉴方向交拍板；
  其侵入式 setup（写 provider hooks+trust 进用户机器）反衬我们自包含编排系
  差异化壁垒。vercel/eve 三问闭环：agent 开发框架非编排台件、不可直装、
  channels/schedules 与 automation.py 同域异形（机制已有）——雷达跟踪结案。**
- C 类型：BUILTIN_FLOWS=18/DEFAULT_CATALOG=14 第 5 次独立复证；i18n 18 型
  **按 name 键（中文源文）18/18 在位**——勘误：按 id 键测会全量误报（前端
  t(f.name) 映射，键系中文源文非 id），后续班检测口径以此为准。
- D 经验：lessons=68 新基线零回漂（28/16/10/7/4/3，scope serial_novel 41
  维持）；openrig/eve 零蒸馏依据（其定位即 CodeBee 自身定位）。
- E 新 CLI：六候选+openclaw 全 MISSING 复测零接入防死链；@openrig/cli 0.6.5
  在架不盲接——「不接」已从缺证升为有证（Windows 不支持 README 实读坐实）。
- F 禅道：poll_enabled=False/claims=0/last_error 空/last_scan 停 09-21 只读
  复认，全程零触发零生产工单变更；第 23 例零新竞品顺延。
- G 产品：过时文案零命中；flowDesc 前端兜底链四层实读与后端 note 一致——
  零新毛病。

### 落地判定与队列

- 队列活项均攒批/拍板/远期，在册代码级「小而实」积压为零——**零代码件
  docs-only 不发版**；四件借鉴候选（OpenMontage/diagram-design/bifrost/
  openrig seats）交拍板不自行立项。
- 21 时快照 11 项全部维持（第 3/10 项保持划掉）；openrig/eve 深化完毕转对标
  跟踪结案，零新队列项。 | 队列 | 2026-10-07

## 2026-10-07 07 时落地班（新一轮第 3/4 步·JS 侧 t() 字面量对账+54 词条补齐）

- **JS 侧 t() 字面量 i18n 对账首次建立**：历班 i18n 覆盖只测 index.html
  data-i18n 键与 BUILTIN_FLOWS 三字段，app.js 内 2,360 处 `t("字面量")` /
  1,726 唯一键从无对账——全量实测 54 键缺 EN 词条（英文界面 toast/状态/
  弹窗中文裸奔）。打法：t() 未命中静默返回键本身（不报错），覆盖缺口只能
  文件级正则对账兜底；正则须排除 `obj.t(` 成员调用并兼容 `\\"` 转义形态。
  已锁 tests/test_borrow_iteration.py（全量对账+修复面点名，解析面 >1000
  自守）。 | i18n | 2026-10-07
- **i18n 词条尾追先例续用**：54 条补齐循 2026-10-06 G-② 残件 7 条尾追位置
  （EN 字典收尾+带来源注释）；术语对齐既有译法——片段冒号保留尾空格、「」
  转直引号、emoji/↻/✓ 原样、{0} 占位符保留、拼接片段照实收词条（"共 "/
  " · 吞吐 " 同例）。 | 文案 | 2026-10-07
- **落地班选定结论偏差处置先例**：巡检班记录（零代码件 docs-only）与编排者
  「已选定」说法出入时——按 01:4x 班先例「尊重选定不强行加功能，实落地=
  回归锚定件+文档」，spec G 专项授权内的小毛病随手修可并做，偏差在报告
  如实记供编排者核对。 | 流程 | 2026-10-07

## 2026-10-07 09 时班（批2：学习记忆与自我改进——新一轮计划第 1/4 步全类型调研）

> 开工 09:38（UTC+8，hour=9，9%7=2 → 轮换批2）、分支 main（5afa213，v0.1.90 已
> 发版）、工作区干净。通道：gh api 认证可用（主扫串行 sleep 4s/repos 复查 0.5s），
> WebSearch 串行 1 发。证据底稿全文见 full-type-round.md 09 时班节
> （当日报告 2026-10-07.md 已录 03-07 时班五节，本班节归并该滚动报告）。

### 主扫与雷达 C（完成状态）

- **主扫**：115 查询（A 常驻 89+B2 轮换 10+A1u/A1p2 16）524 行 **467 唯一仓，
  exit 0 零 403 零 FAIL**；330 已录/137 首见（首见以域外噪声与微型为主）；
  **批2 域 10 词头部全已录**（Agent_Memory_Techniques 1,087/mengram 204/MemRL
  175/agent-apprenticeship 1,617/pro-workflow 2,906/scientific-agent-skills
  47,793/codegraph 73,347/graphiti 31,498）——**批2 域稳定期延续**。 | 主扫
  | 2026-10-07
- **WebSearch 1 发 3 新名全 repos 二次实证**：EvoMap/awesome-agent-evolution
  234★+IAAR-Shanghai/Awesome-AI-Memory 1,257★（批2 域专属地图第 3/4 张）+
  AetherLabsAI/RSIAgent 466★（09-13 建，training-free 递归自我改进+可复用记忆，
  判据）。 | 调研 | 2026-10-07
- **trendshift 29 仓**：**autoharness 8,087→8,972（+885 最大放量，trendshift
  驱动）**——「使用轨迹→SKILL 蒸馏」同族观察信号增强；mattpocock/**skills**
  278,184 属主勘定（悬置销账：仓名实为 skills）；新面孔 knowledge-work-plugins
  26,452（anthropics 知识工作者插件官方仓，B 专项域旁对标）/openai/math 2,250
  （当日新建无描述）/native-subtitle-quote-image 1,480（短视频域 skill）。
  | 雷达 | 2026-10-07
- **雷达 C 全过**：awesome 18 源 18/18 alive（ComposioHQ/awesome-claude-skills
  76,604 正主复认，wshobson 旧属主 404 勘清）；topic 8 页全已录零新大件；
  npm 新微型 raycoder/garda-agent-orchestrator/@cyberine/cli；pypi 第 10 班
  CSP 壳页复认；框架周边词与 B6 批重合跳跑（06 时班刚跑）；禅道周边
  **第 24 例零新竞品**；DSH 生态扩散第 7 信号 dsh-novel-forge 15★（小说锻炉：
  事实账本/上下文包/阶段门禁——机制我们全有对应）。 | 雷达 | 2026-10-07

### 存量复查（repos 端点 48 件零 archived；vs 06 时班间隔约 3 小时）

- 增量：orca +52→86,553 续领跑/**rea +531→9,577 放量续**/diagram-design +154→
  44,062/Strata +152→15,802/OpenMontage +75→64,702/yomiyasu +27→1,621/openrig
  +30→5,524/claude-mem +70→97,200；持平族 DeepSeek-Reasonix 35,742（E 候选
  首位维持）等；**letta pushed 09-10 停更近月观察**、mem0 66,701 在动。 | 复查
  | 2026-10-07
- **属主勘定教训（如实记）**：本班首轮 repos 复查 9 件 404 **系试拼属主名失配
  非属主迁移**——按库内已录属主复测 11 件全 alive（hermes-agent=NousResearch/
  hippo-memory=kitfunso/yomiyasu=nanaism/webnovel-writer=lingfengQAQ/ainovel-cli=
  voocel/huobao-drama=chatfire-AI/gascity=gastownhall/nautilus-compass=chunxiaoxx/
  bernstein=sipyourdrink-ltd/awesome-claude-skills=ComposioHQ）。**repos 复查应
  先从本文件提取已录全名再探测，勿凭记忆拼属主**——in:name 勘定规则的前置步骤。
  | 教训 | 2026-10-07

### 七专项轻量实测与提案（本班只读）

- E 新 CLI：六候选+openclaw 全 MISSING、dsh 在位——零接入防死链维持。
- D 经验：lessons=68 零回漂（28/16/10/7/4/3，scope serial_novel 偏科维持）、
  packs=3；**蒸馏提案 1 条待第 3 步**：「经验库同域竞品对照：autoharness=从真实
  会话自动蒸馏技能+自动修剪，我方=手动沉淀+won/lost/useless 反馈+karma 时间
  衰减——自动蒸馏缺口维持同族观察，接入走三问」（scope=\*/流程规范）。
- B 市场：本班候选均非六源直装件（knowledge-work-plugins 系官方插件仓非六源
  清单、RSIAgent 系框架、native-subtitle 系独立 skill）——三问不过零接入零绕闸；
  knowledge-work-plugins 交后续班深挖 plugin 清单（对标面）。
- 21 时快照 11 项维持（第 3/10 保持划掉），零新队列项；**docs-only 不发版**
  （03/06 时等班先例）。 | 队列 | 2026-10-07

## 2026-10-07 10 时巡检班（新一轮第 2/4 步·七专项 A-G 实证+落地提案）

> 分支 main（5afa213，v0.1.90 已发版）。只读巡检零代码零数据写零禅道触发；
> 行号全部本班 10:0x-10:1x 独立实读。细目证据=full-type-round.md 10 时班节。

### 七专项快照（本班独立实测，非照抄历班）

- **A token**：八锚点全实证（_spawn_step:672 双路径 accumulate :722/:740+花费闸
  先于 token 闸 :676-701+cascade :1526-1529+stable_order 双消费 :3191/:3657+
  resume :206-283+diff 评审 :948/:1019/:1054+_shrink 家族 :2538/:2581/:2595）。
  **新实锚**：modelhub chat(cache_ttl) 真实调用方 5 处（planner 4 幂等+main
  连通测试 24h）——响应缓存无闲置面；`prompt_cache|semantic_cache|cache_control`
  零命中维持。四方向判定维持零新建；_shrink 仅连载挂接维持远期备注交拍板 | 已覆盖 | 2026-10-07
- **B 市场**：六源 SOURCES :50-76 原文实读；缓存逐源 26/315/5/99/215/150=810
  （fetched_at 2026-10-03 零漂移）；SSRF :113+逐跳重过 :146/:159+白名单
  inspect_tree :568（market.py:943 带 whitelist）+指纹突变 :289-293 全在位。
  **knowledge-work-plugins 深挖完成**（09 时班移交件）：26,468★ pushed 10-06、
  根目录 20 域、marketing/skills 8 技能（brand-review/campaign-plan/
  competitive-brief/content-creation/draft-content/email-sequence/
  performance-report/seo-audit）、productivity/skills 任务管理族——anthropic
  六源清单 315 项内零命中（非可直装、依赖 Cowork 宿主+connectors）三问全不过
  **跟踪不接入**；「域→技能族」划分法蒸馏 1 条进 D（提案 1 待第 3 步）| 已覆盖 | 2026-10-07
- **C 类型**：BUILTIN_FLOWS=18 逐条行号实读（flows.py:37-128）；content_workflow
  :156-199 整段实读（light={email,weekly_report,translation}/deep={novel,
  research,tech_proposal}/threshold≥8.5 强双评审）；守卫复跑 test_full_type_round
  **8/8 OK**+test_i18n_dups **3/3 OK**（TUTTI_DATA 隔离）| 巡检 | 2026-10-07
- **D 经验**：lessons=**68** 零漂移（流程规范 28/节奏爽点 16/情节逻辑 10/人物
  塑造 7/一致性 4/文笔风格 3；scope serial_novel 41 偏科维持留人工）；标题精确
  重复 0。**04 时班处置件回访全过**：sk-8114e5d9a90a 已纠回流程规范、
  merged_titles 留痕 8 条——并类已执行且历史可溯零回漂 | 数据卫生 | 2026-10-07
- **E 新 CLI**：DEFAULT_CATALOG=14 AST 实数+逐条 name/detect 实读；本机
  command -v **13/14 在装**（openclaw MISSING）；六候选 reasonix/deepseek-
  reasonix/fuxi/gitlawb/zero/empryo **全 MISSING**——零接入防死链维持 | 巡检 | 2026-10-07
- **F 禅道**：poll_enabled=false 显式关（13 时班口径复证）；product 96 档案完整
  +路由 workdir E:\GitLab\cbc\mo-so ls 实存；**新实锚 triage_ai=true**（模块路由
  未命中 AI 兜底，zentao.py:24/:121/:1498）；claims=0 零积压 last_error 空；
  设置页子页动作 **6 件**（app.js:1012-1017，较 16 时班五件多「拉取模块清单」
  :11871——演进非缺陷）；定时扫描未真实触发（不为验证制造 Bug）| 巡检 | 2026-10-07
- **G 产品**：过时文案（13/14/16/17 种+单源）活码面零命中；README:135「18 种」
  与注册表一致；rank_scan 四平台 flows.py:82↔paihang.py:5-11 复认一致；
  package.json=0.1.90↔CHANGELOG v0.1.90 一致——零新毛病零改动 | 巡检 | 2026-10-07

### 落地提案与判定（第 3/4 步确认清单）

- **提案 1·蒸馏 1 条**（非代码）：{标题:「技能生态对标口径：官方知识工作者插件
  仓按『域→技能族』组织……整仓不接入（非六源清单、依赖 Cowork 宿主，三问
  不过）」, scope=\*, category=流程规范}——入口 skills.upsert_lesson（:421），
  验收 68→69 闭集落类标题查重零重复。
- **提案 2·零代码件判定**：代码级小而实积压核对为零——_shrink 扩 review 长文
  属质量语义交拍板；附录面实核仅 RESEARCH/TRANSLATION 两件（article 无附录系
  无证据支撑的伪缺口不自造）；cache_ttl 5 调用方全幂等；B 专项零可直装新面孔。
  **docs-only 不发版**（03/06/09 时班先例）。 | 提案 | 2026-10-07

### 待深挖队列（10 时快照）与未验证项

- 21 时快照 11 项全部维持（第 3/10 保持划掉）；零新队列项；风险在档维持（交人
  拍板）：data/zentao.json 明文密码；32 位全量 discover 静默退出；runner_drain
  计时超界；批跑同进程测试间争用面。 | 队列 | 2026-10-07
- 未验证项：六源缓存 fetched_at=2026-10-03（4 天前，「拉取更新」系用户动作不代
  按）；禅道定时扫描未真实触发验证；knowledge-work-plugins 仅清单级深挖、各
  skill SKILL.md 未逐篇读（对标面非接入面）；pypi 通道持续受阻非本班可解。
  | 未验证 | 2026-10-07

## 2026-10-07 第 4/4 步独立复核（第二会话交叉验证）

- **推送声明离线核验法**：并行班「推送一次成功」声明当刻 fetch 两遇 443 拒连
  无法在线验证——改用 `git reflog show origin/main`：push 成功会在本地
  remote-tracking ref 落 `update by push` 账（本例 10:51:13/:45 两笔，与声明
  完全对上），不依赖当刻网络窗口。远端声明核验先走 reflog，省重试配额。
  | 教训 | 2026-10-07
- **全量 discover 静默退出第 3 次独立实证**（private worker path 硬崩+退出码
  掩 0，32 位 3.8.6）：分片对账口径两个会话各自复现同一崩点后净进程抽查
  5 模块全绿（含分片争用唯一 FAILED 件 http_500_guard 复跑 OK）——争用定性
  跨会话成立，闸②判据维持「分片对账+争议件净进程复跑」。 | 验证 | 2026-10-07

## 2026-10-07 发版补录（v0.1.91，二次驳回后裁定修订）

- **裁定修订**：「docs-only 不发版」被编排者连驳两次——排除法复盘（selfupdate/
  release_gate/npm 一致性/闸②亲手 266 全绿均在位）后确认唯一未执行的锚定环节=
  发版本身，改判发版。**教训：步计划的文件锚定清单是编排者意图的最强信号，自裁
  「不发版」与锚定冲突时先按锚定执行，原裁定留痕不涂改。** | 教训 | 2026-10-07
- **发版链路**：release_gate（tests+净工作区+归档导入冒烟）先干跑 PASS 再随
  prepublishOnly 二过；CHANGELOG 诚实标注「应用代码与上版相同，无行为变更」；
  npm view 核对 0.1.91 落地。 | 发版 | 2026-10-07

## 2026-10-07 12 时班（批5：检索/知识/浏览器——新一轮计划第 1/4 步全类型调研）

> 开工 12:38（UTC+8，hour=12，12%7=5 → 轮换批5）、分支 main（678baa4，v0.1.91 已
> 发版）、工作区干净。通道：gh api 认证可用（主扫串行 sleep 4s/repos 复查 0.4-0.5s），
> WebSearch 串行 1 发，trendshift curl 直抓可达。证据底稿全文见 current-round.md
> 12 时班（本班即底稿所在班，knowledge.md 本节同步沉淀）。

### 主扫与雷达 C（完成状态）

- **主扫**：115 查询（A 常驻 89+B5 轮换 10+A1u/A1p2 16）524 行 458 唯一仓，exit 0
  零 403 零 FAIL；过筛 229 已录/229 首见——首见绝大多数系全名/简称形态差异，
  9 件简称反查全历史在录，**主扫真新面孔零、批5 域稳定期延续**（与批2/3/6 域
  结论同型）。 | 主扫 | 2026-10-07
- **WebSearch 1 发双通道一致**：批5 域捞出 gpt-researcher 29,930/open_deep_research
  12,683/ragflow 91,746/browser-use 117,305/deepagents 29,975 全已录族——
  与主扫结论互证，无单通道盲区。 | 调研 | 2026-10-07
- **trendshift 30 仓**：新面孔 **cursor/plugins 10,136★+community-plugins 4,001★**
  （Cursor 官方 plugin 规范+官方/社区插件集，orgs 顺扫补全矩阵）——B 专项域旁
  大件、非六源清单三问②③不过跟踪不接入；**Raja0sama/vibex 252★**（代码→ERD/C4
  架构图+可核验文档，09-19 新锐）——「代码→架构图」同族第二信号（diagram-design
  43,915 之后），借鉴判断与 diagram-design 合并拍板。 | 雷达 | 2026-10-07
- **雷达 C 全过**：awesome 19/19 alive 零 archived（数值微增在动）；topic 8 页
  近周更新向全微型零新大件；npm 已录族复见；pypi 第 11 班 CSP 壳页受阻复认；
  禅道周边 **第 25 例零新竞品**；DSH 生态 deepseek-harness 244,723/awesome-dsh-plugin
  17,936/dsh-web 8,442 复认。 | 雷达 | 2026-10-07

### 存量复查（repos 端点 34 件零 archived；vs 09 时班间隔约 3.5 小时）

- 增量：orca +71→86,624 续领跑/**rea +555→10,132 放量加速（当日第二波）**/
  **openai/math +1,655→3,905（created 10-06 新仓 24h 近翻倍）**/autoharness
  +184→9,156（SKILL 蒸馏观察信号续强）/Strata +187→15,989/ponytail +86→156,954/
  anthropics/skills +189→179,943；持平族 DeepSeek-Reasonix 35,742（E 候选首位）、
  letta 25,057 pushed 09-10 停更近月维持。**属主迁移勘定：sst→anomalyco/opencode**
  （gh api 301 跟随实证）；**affaan-m/ECC 274,393 属主勘定**（历班悬置件销账）；
  pi 属主仍未勘定留后续班。 | 复查 | 2026-10-07

### 类型核实与本班判定

- **flows.py BUILTIN_FLOWS=18 逐条实读**（direct/code/novel/serial_novel/article/
  video_script/doc/translation/rank_scan/defect_retro/research/speech/presentation/
  weekly_report/email/tech_proposal/resume/bid_doc）——任务口径 13 种/枚举 14 项
  vs 实际 18 种：枚举「禅道工单」无预置流程（系 F 专项集成），实际多出 doc/
  presentation/defect_retro/resume/bid_doc 5 种；按实际 18 种覆盖矩阵，不自行
  删项。 | 巡检 | 2026-10-07
- E 新 CLI：dsh 在位，六候选+openclaw 全 MISSING——零接入防死链维持。
- 21 时快照 11 项维持（第 3/10 保持划掉），零新队列项（cursor/vibex 系对标/判据
  不入队列）；**docs-only 不发版**——注意：当日发版裁定以锚定清单优先（v0.1.91
  教训在档），交编排者核。 | 队列 | 2026-10-07

## 2026-10-07 12 时巡检班（新一轮第 2/4 步·七专项 A-G 实证+落地件候选甲选定）

- **A token 八件机制全实证**（并行代理逐项 file:line）：三段压缩（step_runner 事前
  0.9 预检+溢出重试+derive_messages 真换上下文）/token_meter 双路 accumulate 兜底/
  双熔断（token+¥ 花费，ENV_BLOCK 只拦下一步）/cascade（tier 升序重排，easy 限定）/
  经验召回（9000 字+8 条+教训保底完整）/会话复用（task.resume+run 内 impl/critic/
  draft sid 三链）/diff-primary 评审（深度分级）/_shrink_context_block 四层降级
  （重试且>12000 才触发）。压缩与级联灰度默认关系既有拍板；**真缺口仅语义缓存**
  （在册攒批拍板件）；modelhub chat_cache 精确缓存已在（planner/连通测试在用，
  pipeline 一次性调用不接）——prompt 缓存面无新增缺口。 | 巡检 | 2026-10-07
- **D 新基线 69 条**（+1）：精确零重复+bigram≥0.8 近似零对+分类全在枚举（04 时班
  提案 1 落地后零回漂）；流程规范占比 42% 较历史 61% 收敛。**知识库账龄观察**：
  data/knowledge.json 12 条 as_of 全超 90 天，注入全带过期标注（机制正确兜底，
  数据老化非缺陷）。 | 巡检 | 2026-10-07
- **落地件候选甲（交第 3/4 步）**：knowledge.py block_for 注入截断反馈回路缺陷——
  ①text[:3000] 硬截拦腰截条（6 条×1500 字常态超预算，非边界）；②used 截前收集，
  被截条目照常 bump hits 而 hits 是排序决胜因子（:299）→「从未被读到的知识反而
  升权」自增强。改法=整条装箱+丢弃带标注+used 只收实注入（单函数，skills.py:740-747
  同纪律对齐）。验收用例三条已列 current-round.md 本班节。 | 选定 | 2026-10-07
- **E 勘误**：catalog 实读 **14 条**（任务口径「已接 11」过时，codebuddy/trae 系
  后续新增）；本机 13/14 在位（openclaw MISSING）；候选 reasonix/fuxi/gitlawb/
  zero/empryo 全 MISSING——零接入防死链维持。B：cursor/plugins 三问②③不过，
  雷达跟踪不接入（六源+双层闸门实证健康）。C/G：18 型零漂移、设置页/图标/版本
  三方零毛病。F：链路全实证健康，claims=0 零积压，poll_enabled=False 待拍板，
  本班零触发。 | 巡检 | 2026-10-07

## 2026-10-07 13 时班沉淀（批6 框架/平台/SDK 增量）

- **B6 稳定期判据**：`pandaprobe`（traces/evals/metrics 自托管）、`python-a2a`（A2A+MCP 协议实现）、`agentscope-runtime`（沙箱/AaaS/观测）分别代表观测、跨代理协议、运行时平台，但 CodeBee 已有运行证据、审批/SSRF 闸门、SSE 与 CLI 编排；框架级能力不可作为六源技能包直装，三问不过即跟踪不接入。
- **生态筛选**：CrewAI-Studio 的无代码 GUI 与 CodeBee 任务台重合；agentic-trading 是 ADK+A2A 教学样例；AgentOps 观测/成本能力与现有 usage、trace、操作台账重合。B6 本班零新增代码件，避免为了“有新仓”重复建设。
- **降级证据**：PyPI 查询受代理 `ProxyError` 阻断，已记录为通道降级，不把无结果伪装成“无项目”。
- **在制品纪律**：本班观察到外部 `859df79` 已推送且工作树仍有四个未提交文件；未覆盖、未暂存、未提交，后续班先复核其归属再继续。

## 2026-10-07 14 时班（批1：代码质量与评审——全类型调研，通道降级班）

> 元信息：本机时间不可考（WSL 劫持 Bash 全不可用、worldtimeapi/WebFetch 域名校验全拦
> ——「WebFetch 域名校验全拦」系 09-22 历班同款），按环境快照分支 main（HEAD=be07b74，
> 工作区干净，13 时班观察的四个未提交文件已由 f61500a/05b13b2/be07b74 收口）。
> 今日已跑批3（03 时）/批6（06 时）/批2（09 时）/批5（12 时）/批6（13 时），本班按
> 第三十五班「轮换标签与小时有偏差已注记」先例选**今日未跑的批1（代码质量与评审）**
> 补位。**通道降级**：gh api/curl/npm 全不可用 → WebSearch 串行 9 发（09-22 降级纪律，
> 被限即顺延未遇）；repos 二次实证通道不在 → 新面孔一律**不定性入库、列判据/雷达级**
> （10-05 纪律的通道受限变体，星数/日期系第三方口径近似）。 | 元信息 | 2026-10-07

### 批1 域（WebSearch 3 发：代码评审/测试生成/安全评审）

- **域结论：稳定期零新大件**（与批2/3/5/6 各域轮过均稳同型）。评审/安全域命中全
  已录族或商业件（GHAS/CodeQL/Semgrep/CodeRabbit 族）；GitHub 官方「Trust Layer
  验证 agentic 行为（correct isn't deterministic）」——**「不信任自报」族第 N 证**，
  与我们事件流计数/跨厂商评审同向。 | 主扫 | 2026-10-07
- **TDAD（Test-Driven Agentic Development，arXiv 2603.17973）**判据级 | 针对「AI
  coding agent 引入回归、破坏此前通过的测试」提出测试驱动的 agentic 开发——与我们
  代码任务测试闸同向；**借鉴方向候选**：代码任务先固化「此前通过的测试集」作为回归
  闸再放手改（读全文后定，交后续班评审） | 判据 | 2026-10-07
- **EvalView**（GitHub Marketplace action）判据级 | AI agent 测试框架（LangGraph/
  CrewAI/OpenAI/Anthropic 通用）——我们缺自评测（evalscope 已记）同域旁证 | 判据 | 2026-10-07
- **awesome-ai-testing**（tugkanboz）| 聚合清单（EvoMaster/EvoSuite 等）——三问
  ②③不过（需二跳、用户不整包搜），雷达跟踪不接市场 | 雷达 | 2026-10-07

### 全类型轮询（WebSearch 5 发：小说/演示/禅道/周报/短剧）

- **小说/连载域**：零新大件。判据级 2 件：**DeepQuill**（Reddit 口径，「AI 读你的
  书而非写、自动建一致性笔记」——一致性笔记自动化的新角度）/**avoid-ai-writing**
  （conorbronsdon，去 AI 味 skill，Claude Code/Codex 兼容——与 aiflavor 同域微型件）。
  行业信号：**The Benchmark Lab 2026 fiction benchmark 把 long-form continuity 列为
  第一判据**——印证连载一致性评审路线；arXiv「Tracking State Footprints」2610.03140
  （multi-agent 一致性层级）研究面参考。 | 调研 | 2026-10-07
- **演示/演讲域**：零新具名大件（presenton/Slidev+AI/Gamma 已知域）；thetoolforthat
  500+ 工具聚合清单三问不过。 | 调研 | 2026-10-07
- **禅道工单域**：**第 26 例零新禅道 AI 竞品**（12 时班第 25 例顺延）；参考信号：
  Zentor 博客引 CodeRabbit 测试——**Opus 5 评审更精准但漏已知 bug、nitpicks 4 倍**
  （评审噪音权衡，我们评审深度分级 :1019 同题域参考）。 | 调研 | 2026-10-07
- **工作汇报域**：判据级 1 件：Reddit 上的开源 GitHub Action——**从 commits/PRs/
  reviews 活动史自动聚合生成叙事周报**。差量=素材自动聚合 vs 我们用户供材；**借鉴
  方向候选**：weekly_report 增加可选「从任务档案/evidence.md/git 活动史自动聚合素材」
  （小而实，交后续班评审）。 | 调研 | 2026-10-07
- **短剧/短视频域**：**OpenMontage 已录复查增量**（calesthio）：11→**12 条生产管线/
  49→100+ tools/400→700+ agent skills** 扩容放量（第三方口径）——03 时班「分镜管线化」
  借鉴方向候选维持交拍板，同族观察信号增强。新判据 2 件：**VibePaper**（商业 $20/月，
  短剧+AI 视频生产的节点画布协作工作台，Seedance 2.0 集成——节点画布形态参考）/
  **awesome-claude-video-skills**（zhuyansen，Claude 视频 skill 聚合清单——三问不过
  雷达）。**DSH 生态扩散第 8 信号**：awesome-dsh-plugin 收录 **Copree**（AI group
  chat 框架）含 **short-drama mode**（导入剧本→分镜）——dsh 生态向写作域渗透续强。 | 调研 | 2026-10-07

### 雷达面（WebSearch 1 发主增量：新 CLI/编排动向）

- **Brigade**（spinabot，10-03 发布）判据级 | 自托管 AI agent 团队→共享编排系统：
  长期记忆+委派+模型切换——**A1/A5 域同形态新面孔**（与 openrig 同族，未 repos 实证
  不定性入库，待有通道班勘定）；threads 口径 | 判据 | 2026-10-07
- **PI-Desktop**（vastsa）判据级 | local-first AI coding agent 桌面工作区（免账号），
  GitHub Trending 2.8K+——A13 桌面形态新锐 | 判据 | 2026-10-07
- **IBM Bob self-hosted**（10-01）判据级 | IBM coding agent 气隙环境版（银行/政府
  全离线）——大厂私有化部署动向 | 判据 | 2026-10-07
- **OpenAI Agents API 公测**（10-01~02）判据级 | 把 Codex 背后的 managed harness
  暴露为可编程 API——**若成熟，未来 catalog 可增「API 直连」形态**（不经 CLI 进程，
  与 direct engine 合流方向），远期观察 | 判据 | 2026-10-07
- 评测面：Terminal-Bench 4.0 Claude Code+Opus 5.5 64.8% 领跑、Octomind 25 任务真实
  PR 基准 GLM-5.2 24/25（$63.43）——评测口径参考。已录族复认：OpenClaw/OpenCode/
  Codex CLI/OpenHands/Cline/Goose/pi/DSH。 | 雷达 | 2026-10-07

### 七专项轻量实测（本班文件级只读实测，非照抄历班）

- **A token**：`cache_control|semantic_cache|prompt_cache` 全 core Grep **零命中维持**
  （本班独立复测）；语义缓存=bifrost 拍板件维持交拍板；四方向判定维持零新增机制。 | 巡检 | 2026-10-07
- **B 市场**：本班候选（Brigade/PI-Desktop/DeepQuill/avoid-ai-writing/VibePaper/
  awesome-claude-video-skills/TDAD/EvalView/awesome-ai-testing）均非六源可直装技能包
  ——三问不过，**零接入零绕闸零安装**。 | 巡检 | 2026-10-07
- **C 类型**：flows.py BUILTIN_FLOWS **18 型** Grep 逐条实测（direct/code/novel/
  serial_novel/article/video_script/doc/translation/rank_scan/defect_retro/research/
  speech/presentation/weekly_report/email/tech_proposal/resume/bid_doc）——与历班
  6 次独立复证同口径零漂移；任务口径 13 种 vs 实际 18 维持历班裁定（枚举「禅道工单」
  系 F 专项集成非预置流程，不删项）。 | 巡检 | 2026-10-07
- **D 经验**：data/skills.json lessons=**69**（12 时班新基线零回漂复认）；本班新面孔
  均判据/雷达级无可蒸馏方法论，纪律不动。 | 巡检 | 2026-10-07
- **E 新 CLI**：DEFAULT_CATALOG **14 条** Grep 实数复认零漂移；`command -v` 本机探测
  **不可行**（Bash 劫持，如实记录）——沿最近历班（13 时班）口径 13/14 在装、六候选
  +openclaw MISSING，**零接入防死链维持**；DeepSeek-Reasonix 35,742★ 候选首位维持。 | 巡检 | 2026-10-07
- **F 禅道**：data/zentao.json 只读实测：poll_enabled=**False** 显式关/claims={} **零
  积压**/last_error 空/last_scan 停 2026-09-21 20:43:44（部署配置缺位多班同口径，是否
  启用交人决定）；product 96 档案与 triage_ai=true 在位；全程只读零触发。 | 巡检 | 2026-10-07
- **G 产品**：过时文案（「13 种/13种/单源」）UI 三件 Grep **零命中**——零新毛病零
  随手修。 | 巡检 | 2026-10-07

### 落地判定与词库

- **零代码件：docs-only 不发版**（本步=计划第 1/4 项调研步，锚定文件=knowledge.md
  +keywords.md 两 docs，与 v0.1.91「锚定清单优先」教训一致无冲突）。 | 落地 | 2026-10-07
- **keywords.md 本班零改动**：本班新面孔（Brigade/PI-Desktop/DeepQuill/VibePaper/
  awesome-claude-video-skills 等）均未 repos 二次实证——按 10-05 纪律不入词库
  （openrig 词形先例系实证后入库），待有 gh api 通道班勘定后再议。 | 词库 | 2026-10-07
- 借鉴方向候选 3 件交后续班/拍板（不自行立项）：①TDAD 回归闸（代码任务）②周报
  素材自动聚合（weekly_report）③OpenAI Agents API 直连形态（catalog 远期）。 | 队列 | 2026-10-07

### 待深挖队列与未验证项（如实记录）

- 21 时快照 11 项全部维持（第 3/10 保持划掉）；本班零新队列项（新面孔均判据/雷达
  级）。风险在档维持（交人拍板）：data/zentao.json 明文密码；批跑同进程测试间争用
  面；32 位全量 discover 静默退出；runner_drain 计时超界。 | 队列 | 2026-10-07
- **未验证**：gh api 词组级主扫 115 查/trendshift 直抓/npm/pypi 全未行（通道不在，
  WebSearch 域定向 9 发替代）；repos 二次实证全未行（新面孔星数/日期系第三方口径）；
  `command -v` 本机探测未行；TDAD/DeepQuill/avoid-ai-writing 未读全文/未逐 commit；
  本机时间未实测（worldtimeapi 被拦，批1 补位判定系推断）；禅道定时扫描未真实触发
  （不为验证制造 Bug）。 | 未验证 | 2026-10-07
- **与并行午后班关系（同轮双会话）**：另一会话同轮以 WebSearch 20 发覆盖批 B1+B7+
  B4 候选（见下「午后班沉淀」节与其报告节）——两班独立通道在 **TDAD 上双通道一致**
  （交叉验证），其余增量互补零重复（本班 Brigade/PI-Desktop/OpenAI Agents API/
  DeepQuill/VibePaper/DSH 第 8 信号/周报素材聚合 vs 午后班 墨枢/ryk/MiniMax/pi CVE/
  禅道官方信号/起点剧场）；本班「批1 补位」判定写于午后班落笔之前，事后看批1 系
  两班共跑，增量不撞、记录均保留不涂改。 | 并行注记 | 2026-10-07

## 2026-10-07 午后班沉淀（批 B1/B7 候选——第 1/4 步全类型调研·通道降级版）

> 通道：Bash 被劫持全废（`/bin/bash` 缺失三测同败）+WebFetch 域名校验全拦 → 走 keywords.md
> 既定降级纪律 WebSearch 串行 20 发；小时不可测，按「当日未跑批」覆盖 B1+B7（14/15 时
> 轮转位）+B4 顺带。**全部新面孔未经 repos 端点二次实证（10-05 纪律），一律 claim 级不入
> 定性**；主扫脚本未行，以逐族合并查询+全历史 Grep 过筛等效替代。详录见 2026-10-07.md
> 午后班节。| 调研 | 2026-10-07

- **墨枢**（topic:chinese-novel，claim 级）：中文长篇工作台——设定库/时间线/RAG/一致性
  守卫/**自然化审查工作台**/团队协作。大纲/设定库/一致性守卫我们全有对应；「自然化审查
  （去 AI 味）」与「时间线」系显式环节——**午后巡检班复核更正：两者均已覆盖**
  （aiflavor.py 三层检测：措辞层套话密度+叙事架构层三类指纹+节奏层开篇/章末钩子，
  已挂单稿件与连载逐章评审双链路 pipeline:3201/:4992；story_tracking 逐章
  facts/characters/foreshadowing 持久化即章节事实账本）——「竞品有我们没有」判定
  须先查产品内现状。 | 已覆盖（午后巡检班复核更正） | 2026-10-07
- **webfiction-write**（同源，claim 级）：Codex 基座网文全流程 skill，明确面向番茄/起点/
  七猫/刺猬猫——与我们扫榜→大纲→正文同形（skill 形态），平台名定向值得其 README 深读。 | 判据·雷达 | 2026-10-07
- **LAY-lgtm/novel-writing-framework**（claim 级）：500 章实战提炼的方法论框架（阮一峰
  科技爱好者周刊推荐 claim）——方法论面蒸馏候选。 | 判据（待 README 深读） | 2026-10-07
- **自然化审查/去 AI 味域信号**（行业面）：番茄/七猫 2026 前后收紧纯 AI 文审核（「AI 代笔」
  降权、「AI 辅助」标识安全）+阅文「起点剧场」（网文 IP→AI 短剧）上线——平台侧证据支持
  「自然化审查作为小说/连载评审维度」候选经验（scope=novel/serial_novel），优先级上升。
  **午后巡检班复核更正：评审维度已覆盖**（aiflavor 三层检测+双评审链路挂载），
  行业信号转为「检测词表持续运维」的佐证（AI_PHRASES 注释自带「持续运维」约定）。 | 已覆盖（复核更正，词表运维佐证在档） | 2026-10-07
- **christopherkarani/ryk**（B4，claim 级）：本地 agent guardrails——策略引擎+审批流+
  **86 个安全包分发**+OS 级 sandbox（no Docker）+审计。闸门族同域（我们六源+白名单+SSRF
  防护+双层闸门已实证健康）；「安全包」分发形态系新参照。 | 判据 | 2026-10-07
- **aden-hive/hive**（A13，claim 级）：生产 multi-agent harness+CPU/内存/token **资源监控
  可视化**——运行台账我们有、系统级资源可视化面板无。 | 判据 | 2026-10-07
- **Vault-MCP**（A13，claim 级）：age 加密凭据文件+代理工具无直访问模式——对
  **data/zentao.json 明文密码在档风险**（交人拍板项）系第三参照件（同向：历班已记凭据
  加密方向），**非顺手加固**，仍交拍板。 | 判据·参照 | 2026-10-07
- **MiniMax Coding Agent**（A4，claim 级）：2026-09-21 开源 MIT、headless for CI、
  Agent Client Protocol——**E 专项增补候选**，未装未测不接入（防死链纪律）。同面首见
  claim：Meta Muse Code/Junie CLI/agy CLI/New Relic Ground Truth CLI（厂商产品面无开源
  仓可勘定）。 | E 候选（待实测） | 2026-10-07
- **npm 面 claim 簇**：TokenTracker/ai-craft（跨 Claude Code/Codex/OpenCode/Pi/OMP/
  Copilot 用量统计）、Empir3 Bridge（MCP 化多 agent 编排）、Pi Conductor（pi 持久会话
  编排）、oh-my-openagent（多 harness 重构）——A13 用量指标域与 A1 同形簇+1。 | 判据 | 2026-10-07
- **agentic-qe**（B1，claim 级）：开源 QE agent 舰队（测试生成+覆盖缺口+flaky 检测×11
  coding agent 宿主）；TDAD 论文印证「agent 引入回归」风险面。 | 判据 | 2026-10-07
- **OrchestratorInc/agent-orchestrator**（topic，claim 级）：并行 agent 监督+skills，
  Apache-2.0，数日内更新——A1 同形新件。OpenShell（trendshift 碎片，「safe, private」）
  信息不足待勘定。PromptSite（轻量 prompt 版本管理）新微型。 | 判据 | 2026-10-07
- **禅道 F 专项增量（官方产品化信号）**：ZenTao CLI 原厂 Skills 新闻面（easysoft/
  zentao-cli 仓 09-22 已录，「一键装进 Claude/Cursor」系产品化推进信号）+**IPD 4.7.1
  内置 4 个智能体**（首见）+imyuyu/zentao-cli（社区 CLI 首见 claim）+zentao-mcp 同族复认
  （daodaobing/bivex/@dannyvan 均已录）——**第 26 例定性维持：零机制级新竞品，官方产品化
  推进入档**。 | F 专项 | 2026-10-07
- **发布平台行业信号（A12）**：阅文起点剧场（IP→AI 短剧）+ElserStudio/invideo 微短剧
  工作流（章→60-120s 集）+WebnovelSync 跨站同步——OpenMontage 分镜管线化借鉴方向获
  行业面佐证。 | 判据 | 2026-10-07

### 复查记录（通道受限版）

- **pi 属主台账矛盾销账**：06 时班「earendil-works/pi（badlogic/pi-mono 302 实证）」vs
  12 时班「未勘定」——本班多源三角定位（agentic-ai readthedocs/pi.dev/HF badlogicgames）
  证实**正主=earendil-works/pi（badlogic/pi-mono 组织迁移，MIT，225+ releases）**，06 时
  班正确。**新风险**：CVE-2026-5556——pi-mono ≤0.58.4 `discoverAndLoadExtensions`
  代码注入/RCE（SentinelOne claim）；pi 系 catalog 已接（13/14 在装），**本机版本核查交
  人拍板**（本班无 shell）。 | 销账+风险 | 2026-10-07
- **过筛七件全对回历史零重复入库**：MoneyPrinterTurbo（09-22）/InkOS=Narcooo/inkos
  （09-23 已深挖，多线推演+chaptersafe 已抄，维持观察）/Presenton（09-22）/zentao-cli=
  easysoft（09-22）/Aegis（Justin0504 484★ 多轮）/awesome-auditable-ai（10-03）/
  Conductor（多义）。属主歧义新记：travisvn/awesome-claude-skills 15.3k★ 疑义 claim
  （正主=ComposioHQ 76.6k★），待 repos in:name 勘定不盲采。openrig 第三方页 980-1.1k★
  系 shields 缓存滞后，gh api 5,546（12 时班实测）优先。 | 过筛 | 2026-10-07
- **C 类型第 6 次复证**：BUILTIN_FLOWS=18（flows.py:36-128 逐条实读）——任务口径
  「13 种」系指令面（+禅道工单映射=14 面），实际 18 型零漂移不删项，与历班同口径。
  各类型域判定维持：article/speech/translation/email/weekly/resume/bid_doc 域稳定期/
  沉寂期零新仓；「代码→架构图」第三信号（CodeWiki/DocAgent）；research 16-step 悬置
  维持（无仓名）。 | 巡检 | 2026-10-07

### 本班结论

1. **通道降级纪律首次双通道同断实战**：Bash+WebFetch 同断下 WebSearch 串行 20 发+
   Grep 过筛可完成 A1-A13/B 候选批/C 可用面全覆盖，但代价=全部 claim 级（星数/
   pushed_at 缺失）+19 源逐仓实测未行——本轮增量以「判据/雷达候选」形态入档，
   勘定归 repos 班。
2. **两个真正的新信号**：①「自然化审查（去 AI 味）」成为网文平台审核显式门槛（行业面
   多源）——小说/连载评审维度候选，交拍板；②禅道官方产品化提速（IPD 内置智能体+
   CLI Skills 新闻面）——F 专项竞品压力面升级但零机制级新竞品维持。
3. **风险台账+1**：pi ≤0.58.4 CVE-2026-5556 RCE（若本机命中）——在档风险交拍板：
   zentao.json 明文密码/poll_enabled=False/批跑同进程争用/32 位 discover 静默退出/
   runner_drain 计时超界 维持。
4. **待办 2 件交后续班**：①本机 pi 版本核查（CVE）；②travisvn/awesome-claude-skills
   属主勘定。待深挖队列 11 项维持，本班零新队列项。

## 2026-10-07 午后巡检班沉淀（新一轮第 2/4 步·七专项 A-G 实证+候选复核销账）

> 通道：Bash 仍被 WSL 接管（`/bin/bash` 缺失）——Read/Grep/Glob 文件级实测执行全部
> 专项，shell 类探测（command -v/npm view/git）未行如实记。HEAD=be07b74（v0.1.91），
> pipeline.py 行号全部本班实读零漂移。详录见 2026-10-07.md 午后巡检班节。 | 巡检 | 2026-10-07

- **A token 八件机制逐件实读在位**：三段压缩（compaction.py 全文，摘要消耗入账
  usage.record(compaction) :156-166）/token_meter（cached 单列不计 used :101-107）/
  双熔断（花费闸先于 token 闸 pipeline:676-677，ENV_BLOCK 只拦下一步）/cascade
  （capability.py:100-125 tier 重排+难度选模 :111-118）/经验召回（block_for
  stable_order+教训保底）/会话复用/diff 评审（CODE_REVIEW_PROMPT findings 锚定
  证据 :929-932）/_shrink_context_block 四层降级 :2538-2578。`cache_control|
  semantic_cache|prompt_cache` 全 core grep 零命中维持——四方向判定维持，
  语义缓存维持攒批拍板件，**零新增机制零重复建设**。 | 巡检 | 2026-10-07
- **B 市场**：六源 fetched_at=2026-10-03 零漂移（本班逐文件实测）；午后班 claim 级
  候选（墨枢/webfiction/ryk/aden-hive/TokenTracker/agentic-qe 等）六源缓存 grep
  **0 命中=不可直装坐实**，三问逐条全不过——零安装零绕闸；「86 安全包分发」「资源
  监控可视化」记对标素材不立项。 | 巡检 | 2026-10-07
- **C 类型第 7 次复证**：BUILTIN_FLOWS=18（flows.py:36-134 逐条实读）；i18n name
  键 18/18——勘误一次：首测 16/18 系 pattern 用指令口径词（代码任务/投标文件）
  非注册名（代码/标书编制），口径错非产品缺陷（与 07 时班 id 键勘误同族入档）。
  README「18 种」一致零漂移。 | 巡检 | 2026-10-07
- **D 基线 69 条零回漂**（分类 29/16/10/7/4/3 核对无误，流程规范 42.0%；scope
  serial_novel 41 偏科维持）；**「自然化审查」候选复核为已覆盖=本班主增量**
  （aiflavor.py 三层检测+单稿件/连载逐章评审双挂载实证，墨枢/时间线同判销账）
  ——方法论：**「竞品有我们没有」判定必须先查产品内现状再立项**。零合并项，
  纪律不动数据。 | 巡检·复核 | 2026-10-07
- **E**：catalog 14 条目实数复认（dsh :168）；本机探测 shell 断未行沿历班口径
  （13/14 在装、六候选全 MISSING）；MiniMax 新增候选未装未测零接入防死链；
  pi CVE 本机核查待办维持。**F**：poll_enabled=False/claims={} 零积压/last_scan
  停 2026-09-21——部署配置缺位非代码缺陷维持，全程只读零触发，第 26 例零新
  竞品顺延。**G**：过时文案 UI 三件零命中零新毛病；aiflavor 节奏层 article 不在
  NARRATIVE_FLOWS 系可辩护设计边界（交产品判断，不顺手改）。 | 巡检 | 2026-10-07
- **三件候选复核销账**：①14 时班「活动史自动聚合周报」——weekly_report 素材链
  已配（禅道 weekly_brief pipeline:4913-4923+git log 半边 _gitlog_brief 双源并列
  互不阻塞）；②午后班「自然化审查」——aiflavor 已覆盖；③墨枢「时间线」——
  story_tracking 章节事实账本已覆盖。**在册代码级「小而实」积压判定为零，不凑数
  件**；本班 docs-only 不发版（07 时巡检班/14 时班先例）。 | 销账 | 2026-10-07

## 2026-10-07 16 时落地班沉淀（新一轮第 3/4 步·index.html 属性级 i18n 缺口修复）

> Bash 全程被 WSL 劫持（班中四测同败）——Write/Edit 实现+grep 级实测验证，运行级
> 验证（py_compile/node --check/unittest/git）归第 4/4 步通道恢复后执行。落地判定
> 承 07 时落地班先例：巡检判定积压为零时，实落地=实测发现的真实缺口修复+回归锚定
> 件。 | 落地 | 2026-10-07

- **主发现：i18n 对账存在第三层盲区——属性级**。历班覆盖只落两层（data-i18n 键
  ↔ i18n.js=test_i18n_key_coverage；app.js t() 字面量 ↔ i18n.js=test_borrow_
  iteration），「中文 title/aria-label/placeholder 挂了文案却缺 data-i18n-title/
  -aria/-ph 钩子」从无对账——applyI18n（i18n.js:3115-3128）只按钩子属性重译，缺
  钩子属性在英文界面恒为中文。190 处含中文属性元素逐行核对，16 缺口（浏览器工具
  栏 9/侧栏导航 5/验收标准字段 2）。**方法论：i18n 对账面=静态 HTML 键 + 属性钩子
  + JS 字面量三层，缺一层就有裸奔面**。 | 发现·修复 | 2026-10-07
- **修复四件**：index.html 16 元素补钩子（+19 属性，中文源值逐字未动，zh 零行为
  变化）；i18n.js 尾追 13 条 EN 词条（设置/搜索/刷新/打开复用既有条目；`&#10;`
  实体键按 JS `\n` 转义形态入库与 dataset 解码值精确匹配）；test_borrow_iteration
  补单引号 t('…') 对账盲区（07 时班在案盲区销账，app.js 现存 2 处词条均在位零
  用户可见缺陷）；test_iteration_regressions 追加 IndexHtmlAttrI18nTests（全量
  钩子对账=改前红改后绿+修复面 20 钩子/13 词条点名锁定）。 | 修复 | 2026-10-07
- **排除项与方法**：mkr-installed「已安装」静态文案非缺口（app.js:12558 动态覆写
  已走 t()）；label 文本节点 33/33 全带钩子、toast/confirm/alert 零裸中文——包裹
  纪律良好，缺口集中于「后补的浏览器页签工具栏与侧栏导航」；「已安装 N」等动态
  文本不属属性级对账范围。 | 边界 | 2026-10-07
- **通道阻塞两件入档**：Bash 劫持（运行级验证降级 grep 推定，改前失败改后通过由
  缺口实证链替代）+code-reviewer 子代理 API 400 两轮（模型路由环境故障，评审转
  本会话内六项自评审全过）——均系环境侧非代码侧，第 4/4 步收口时通道恢复须补跑
  运行级验证。 | 阻塞 | 2026-10-07

## 2026-10-07 收口班沉淀（本轮第 4/4 步·静态联调完成+执行面阻塞如实记）

> Bash 全程被 WSL 接管（班中 6 测同败+子代理复核同败）——五道关⓪①②④⑤执行面
> 全未行，git 提交/推送/npm 发版全未行；实落地=静态联调复核+本沉淀。改动未提交
> → **不发版不 bump**（package.json 保持 0.1.91，无 npm 半发布态无 revert 需要）。
> | 收口 | 2026-10-07

- **静态联调全过**：属性级 i18n 全量复扫 **190/190 中文属性全带同值钩子**（含 3 处
  跨行标签，16 缺口修复面零回漂）；i18n.js 13 新词条+13 复用键抽查全在位；两测试件
  静态审查过（正则边界/unescape 比对/转义计数/20 锚点点名与实文件吻合）；18 型第 8
  次复证（BUILTIN_FLOWS=18+i18n name 键 18/18）。 | 联调 | 2026-10-07
- **shell 劫持根因定位与修复法（方法论入档）**：harness 把 bash 解析到 WSL
  bash.exe→本机发行版缺 /bin/bash；Git for Windows 实际在 **D:\Git\bin\bash.exe**
  （C:\Program Files\Git 缺失）。已把 `CLAUDE_CODE_GIT_BASH_PATH=D:\Git\bin\bash.exe`
  写入 ~/.claude/settings.json env——env 仅启动时生效，**须重启会话验证**；无效则
  备选 wsl --set-default / PATH 前置 D:\Git\bin / 修复发行版。 | 环境 | 2026-10-07
- **未提交不发版判定**：改动在工作区未入库时，发版前置（提交+推送+selfupdate 绿）
  不可能满足——正确处置=版本号/CHANGELOG/npm 三不动，待通道恢复按重跑清单走五道关
  再判发版（预记 v0.1.92：英文界面属性级 16 处+单引号对账补盲区，用户可感知应发
  patch）。keywords.md 本班零调整（降级纪律已覆盖）。 | 流程 | 2026-10-07

## 2026-10-07 18 时班沉淀（批4 治理/安全/人机协同·通道恢复班）

> 18:39 开工（hour=18 → 批4，午后班降级跑过 1 发、本班 gh api 全量补齐）、分支
> main（be07b74）。**shell/gh api/curl/npm 全恢复**（CLAUDE_CODE_GIT_BASH_PATH
> 修复经会话重启生效实证）。主扫 115 查询/524 行/466 唯一仓/exit 0 零失败零 403；
> B4 域 31 件头部全已录=批4 域稳定期零机制级新差量。本班主增量=**降级班 claim 级
> 面孔的 repos 端点二次实证补课**（10-05 纪律）+待办双销。docs-only 不发版。
> | 调研 | 2026-10-07

- **三件大星对标坐实（claim→定性入库）**：**NVIDIA/OpenShell 15,202★**（safe,
  private runtime for autonomous AI agents——A5 沙箱运行时域大星；**115 查询全
  零命中=主扫 per_page=5 剪切线又一实证**，BMAD 同款盲区，靠 C 雷达面补位）/
  **OrchestratorInc/agent-orchestrator 12,857★**（A1 同形态，与 Untrivial 同名
  异主已录件并存）/**aden-hive/hive 11,092★**（A13 harness+资源监控可视化——
  真实差量系面板工程不立项）。均雷达/对标跟踪零接入（非六源技能包三问不过）。
  | 竞品 | 2026-10-07
- **E 专项候选坐实+本机探测复测**：**MiniMax-AI/minimax-code 1,985★**（官方开源
  terminal coding agent；org 顺藤 OpenAgentCore 196★=OpenAI Agents API 自托管实现，
  14 时班公测信号获仓级佐证）——`command -v` 恢复后首测 13/14 在装（openclaw
  MISSING）、六候选+minimax 全 MISSING，**零接入防死链维持**。**pi 0.99.1 实测
  >0.58.4：CVE-2026-5556 影响范围外本机无风险（待办①销）**。 | 新CLI | 2026-10-07
- **属主勘定四件（in:name 规则第 3 批量例）**：mattpocock/skills=278,722★ 正主
  （03 时班悬置销账，中文本地化分叉 vinvcn 4,642 系扩散信号）/ travisvn/awesome-
  claude-skills=15,301★ 真仓但 04-28 停更（ComposioHQ 76,638★ 正主并行在，两仓
  并存非失配，待办②销）/ webnovel-writer=lingfengQAQ 7,343★ 首定（副产
  webnovel-writer-opencode 213★ 多 CLI 移植件，oh-story/ponytail 同款扩散信号
  再+1）/ hermes-agent=NousResearch 251,796★（家族件 hermes-agent-self-evolution
  5,471★ 进化式自改进在档）。 | 勘定 | 2026-10-07
- **降级面孔定性收窄（claim→实证后降级）**：ryk 42★ 微型且 08-20 停更/LAY-lgtm
  novel-writing-framework 249★ 停更/webfiction-write 18★ 微型（claim 属实但均
  判据级）；**墨枢 in:name 零命中仓名未勘定**（疑产品名非仓名，规则第 6 例记法，
  其「自然化审查」已由午后巡检班复核为 aiflavor 已覆盖）；vault-mcp claim 正主
  未勘到（同名多件均不符，hashicorp/vault-mcp-server 系另一物）；EvalView ≤1★
  噪声。**方法沉淀：claim 级面孔过半数在 repos 实证后降级——10-05 规则持续有效，
  未经 repos 坐实不入定性入库**。 | 方法 | 2026-10-07
- **A1u 新面孔 mco-org/mco 530★**：CLI-first 多 agent 并行跑+原始答案对比+协调
  评审（答案对比与内容引擎赛马同向）——A1 同形态中腰部判据。 | 竞品 | 2026-10-07
- **存量复查 26 件全 alive 零 archived**：orca +278 续领跑/rea +2,179 大爆发续
  （trendshift 驱动）/Strata +704/mattpocock +774 放量/DeepSeek-Reasonix 35,746
  E 候选首位持平；C 雷达 awesome 18 源 alive 零 archived；禅道周边**第 27 例零
  新机制级竞品**（官方 MCP 桥接信号面增厚）；pypi 第 13 班 CSP 壳页（curl 恢复
  仍壳=通道侧复认）；trendshift 直抓仍拦。B4 域结论稳定期、lessons=69 零回漂、
  18/14 第 9 次复证、零新毛病、keywords.md +2 词形（openshell/agent-orchestrator/
  hive+minimax 生态盯增量）。 | 例行 | 2026-10-07

## 2026-10-07 19 时巡检班沉淀（新一轮第 2/4 步·七专项 A-G 实证+回归测试首跑抓缺陷）

> 通道全恢复（Bash/python 实测可用，CLAUDE_CODE_GIT_BASH_PATH 修复生效），分支
> main（be07b74）五道关⓪过。**本班主增量=16时班属性级 i18n 回归测试首次真实执行**
> （历班 Bash 劫持只静态审查过，首跑即抓到测试自身正则缺陷）——产品本体零缺陷被
> 真实执行复证，测试件一字符修复入候选。 | 巡检 | 2026-10-07

- **A token 八机制 grep 级复证全在位零漂移**：三段压缩（compaction.py:12-18 触发
  口径+:77-81 tail 预算+:193 压力比闸）/token_meter（accumulate 进账+cached 单列
  不计 used）/双熔断（pipeline.py:676-696 花费闸先于 token 闸，ENV_BLOCK 只拦下
  一步）/cascade（capability.py:100-125 tier 重排+难度选模，modelhub.py:2280+
  branching.py:46-47 压力联动的双挂载）/经验召回（skills.py:676 block_for+
  knowledge.py:277 双源）/会话复用（session_run_id pipeline.py:691 跨步进账）/
  diff 评审（pipeline.py:921-960 -n 40 封顶+findings 锚定证据）/_shrink_context_
  block（pipeline.py:2538-2582 四层降级+连载逐章 :2581 分层组装）。`prompt cache|
  semantic cache|cache_control` 全 core grep 零命中维持——**四方向判定维持，无真
  实缺口不新增机制**。 | 巡检·A | 2026-10-07
- **B 市场六源实读+闸门三重实证**：SOURCES=6（market_remote.py:50-75 zcode/
  anthropic/anthropic-skills/claude-skills/clawhub/cocoloop 逐条）；SSRF 网关
  :114（仅 https+解析全 IP 拦环回/私有/保留）、逐跳重检 :156/:202、体量上限五重
  （清单 5MB/包 80MB/解包 120MB/500 文件/单文本 512KB）、白名单闸 :941-952（安装
  面与声明不符整包拒绝）。六源缓存 fetched_at=2026-10-03 实读零漂移；18时班新面
  孔（OpenShell/agent-orchestrator/aden-hive/mco/minimax-code）三问逐条不过：
  非技能包不可直装+A1 同形态重合+用户不按此搜——**零安装零绕闸，雷达跟踪**。
  | 巡检·B | 2026-10-07
- **C 类型第 10 次复证全过**：BUILTIN_FLOWS=18（flows.py:36-134 全文实读逐条：
  direct/code/novel/serial_novel/article/video_script/doc/translation/rank_scan/
  defect_retro/research/speech/presentation/weekly_report/email/tech_proposal/
  resume/bid_doc）；i18n EN 词条 18/18（**中文源值口径**——键格式系「中文：EN」
  映射非 `flow.name.<id>` 键，本班又一次靠先查真实格式避免口径错报缺）。task_
  compile.py 全文实读：难度推导（threshold≥8.5→hard，研究/方案/扫榜默认 hard
  :27-29）、rubric 钳 8 条 :60、轻量三元（email/weekly_report/translation :163）
  与深度三元（novel/research/tech_proposal :164）分工、高门槛强制双评审 :194-195
  ——菜单描述/流程参数/辅助信息与行为一致零新毛病。 | 巡检·C | 2026-10-07
- **D 基线 69 条逐项吻合零回漂**：真源 data/skills.json（skills.py:32）实读：
  分类 流程规范29/节奏爽点16/情节逻辑10/人物塑造7/一致性4/文笔风格3（流程规范
  42.0%）、scope serial_novel 41/*/12/code/11/direct/4/article/1——与基线完全
  一致。block_for :676/list_lessons :391 接口在位。**合并候选本班零**（纪律不动
  数据）；本轮调研方法论增量由 18时班收口（claim 实证降级规则），零重复入库。
  | 巡检·D | 2026-10-07
- **E 13/14 维持+探测口径勘误自记**：catalog.py detect 清单 14 条实读（codex/
  claude/opencode/qwen/aider/openclaw/kimi/mimo/grok/pi/dsh/gemini/cbc/trae-cli）；
  实测 13 在装（openclaw MISSING 维持）。**勘误一次：初探用 `trae` 误报 MISSING，
  对 catalog:202 detect=trae-cli 复探 OK**——「探测前先读 detect 字段」与 C 专项
  「指令口径词 vs 注册名」同族入方法论。六候选（DeepSeek-Reasonix/FuXi/Gitlawb/
  zero/Empryo/MiniMax）未装未测零接入防死链维持。 | 巡检·E | 2026-10-07
- **F 停摆系部署配置维持+全程只读**：真源 data/zentao.json 实读：poll_enabled=
  False、last_scan=2026-09-21 20:43:44、claims=0、last_error 空——零 Bug 积压、
  扫描链健康（配置一旦启用即可用）；本班未触发任何 resolve/评论/群通知。产品档案
  路由未动；i18n 禅道词条 30 处在位。第 27 例后零新机制级竞品顺延（18时班已录）。
  | 巡检·F | 2026-10-07
- **G 主发现：IndexHtmlAttrI18nTests 首跑即抓测试自身缺陷**。tests/test_iteration_
  regressions.py:112 `_ATTR` 正则 `[a-zA-Z-]+` 字符类缺数字——钩子键名 `data-i18n-
  aria/title/ph` 含 `18` 被错拆成 `n-aria`，钩子在位却全判缺 → **全量对账 241 全
  假阳性**（修复面点名测试 test_repaired_hooks_and_entries_locked 是过的，缺口仅
  在全量扫描半边）。产品本体零缺陷：字符类补 `0-9` 后全量复扫 0 问题=190/190 中
  文属性全带同值钩子（收口班静态联调结论被真实执行复证）。**方法论：从未运行过
  的测试不算绿灯——「静态审查过」与「跑过」之间存在盲区，劫持班交付的测试件必
  须在通道恢复后首跑**。过时文案扫描（单源/双源等）零命中零新毛病维持。
  | 发现·G | 2026-10-07
- **落地候选（1 件，小而实可自动验证，第 3/4 步实施）**：tests/test_iteration_
  regressions.py:112 `_ATTR = _re.compile(r'([a-zA-Z-]+)="([^"]*)"')` 字符类补
  `0-9` → `([a-zA-Z0-9-]+)`。验收：`cd tests && python -m unittest test_iteration_
  regressions test_borrow_iteration` 全绿（改前 241 假阳性红、改后绿），产品零行
  为变化（纯测试件）。收益：i18n 属性契约从此真跑得动，后续回归有真实闸门。风险：
  零（测试件单字符修复，已用等价脚本验证 0 误报）；无并发/跨模块面。在册其余积
  压维持判定为零，不凑数件。 | 候选 | 2026-10-07

## 2026-10-07 19 时落地班沉淀（新一轮第 3/4 步·属性级 i18n 对账正则修复实施）

> 通道正常（shell/python 实测可用），分支 main（be07b74）。实施上一子任务
> （19 时巡检班）确认的唯一落地件；在制品其余增量逐字不动。详录见
> 2026-10-07.md 19 时落地班节。 | 落地 | 2026-10-07

- **候选实施+改前红改后绿真实闭环**：`_ATTR` 字符类 `[a-zA-Z-]+`→
  `[a-zA-Z0-9-]+`（tests/test_iteration_regressions.py:112 单行）。改前实跑
  FAILED（241 处「缺 data-i18n-*」假阳性复现，与巡检班预判数字一致）；改后
  `python -m unittest test_iteration_regressions test_borrow_iteration`
  **5/5 OK**+py_compile 过——i18n 属性契约自此真实可跑，后续回归有闸门。
  | 落地·闭环 | 2026-10-07
- **全预置类型静态一致性检查（本步要求，只读零改动）**：BUILTIN_FLOWS=18
  import 实测、18/18 四字段齐全；review 型 14 个 rubric 4-5 维/threshold 7.0
  （bid_doc 7.5）/rounds 2 零异常；serial{8,2500} 在位；i18n 类型名 EN 词条
  18/18。相邻锚定回归（flow_icons/content_contracts/review_depth/i18n_dups/
  i18n_key_coverage）**28/28 OK**——flows.py/pipeline.py 零需求零改动。
  | 一致性 | 2026-10-07
- **方法论沿用印证**：「从未运行过的测试不算绿灯」二次实证——本班改前红即
  巡检班首跑结论的可复现闭环；改一行正则使 241 假阳性归零，产品零行为变化。
  差量教训一条自记：写 docs 应走 Read/Write/Edit 工具（本班日报追加误用
  Bash heredoc 一次，字节级验证 UTF-8 无损后保留并如实记，下不为例）。
  | 方法·自记 | 2026-10-07

## 2026-10-07 收口班沉淀（本轮第 4/4 步·整体联调五道关执行+推送发版实录）

> 通道恢复班（shell 全可用），分支 main（be07b74）实测。收口对象=16/19 时
> 落地班四件；本班零产品代码，报告详录见 2026-10-07.md 收口班节。
> | 收口 | 2026-10-07

- **五道关全过实录**：⓪ main 实测；①py_compile 两测试件+node --check 两
  UI 件过；②逐模块净进程 discover -p 形态 **267/267 有效全绿**（单进程
  discover 32 位中途崩退在案风险再复现作废；唯一败例 test_http_500_guard
  系孤儿 python 测试服务占其固定端口 18944，taskkill 后隔离复跑 4/4 OK，
  与改动面零交集）；③逐 hunk 自审过、code-reviewer 代理 API 400 第 3 败
  按两轮纪律停止（会话内自评审代位在档）；④外来标记零命中；⑤show --stat
  核对。 | 验收·五道关 | 2026-10-07
- **方法论（跑法伪影三连，沉淀防复发）**：tests/ 两种导入惯例并存
  （`from base import` 裸导入 vs `from tests.base import` 包导入），单 cwd
  的 `<module>`/`tests.<module>` 形态各必炸一半——唯 `discover -s tests -p
  "<mod>.py"`（起始目录入 sys.path+cwd=root 双可达）全兼容；**逐模块全量
  只能走 discover -p 形态**。后台/子进程任务须显式 chdir 仓库根（继承陈旧
  cwd 的伪影轮全部废弃不计证据）。 | 方法·测试跑法 | 2026-10-07
- **方法论（孤儿测试服务）**：固定端口测试模块（如 test_http_500_guard
  PORT=18944）在 discover 崩退/并行会话后可能留孤儿服务占端口，后续全量
  必假败——**全量跑前 netstat 查测试固定端口占用、python.exe+固定测试端口
  双特征即可 taskkill**；本班实证清除后 4/4 OK。 | 方法·环境 | 2026-10-07
- **发版 v0.1.92 实录（并行分工+本班闭环）**：元数据三件套由并行会话提交
  推送（7195d57），npm publish 由本班补完——首试被 release_gate
  require_clean 拦（本班 docs 未提交），按闸序提交推送 46ba151 后重试过三道
  闸 **+ codebee@0.1.92**。 | 发版 | 2026-10-07
- **方法论（npm view 缓存假象）**：publish 成功后 `npm view` 可能长期回旧版
  （本地元数据缓存），404/旧版≠发布失败——**核对一律 curl 直查
  registry.npmjs.org/<pkg>/latest 的 dist-tags 与 shasum**（本班 npm view
  15 分钟假 0.1.91+404，REST 实况 0.1.92 且 shasum 与发布通知一致）。
  | 方法·发版核对 | 2026-10-07
- **闸盲区（交人拍板）**：release_gate.run_tests 只看退出码，32 位单进程
  discover「崩退且退出码 0」会让闸放行未真跑完的测试——真实证据须闸外逐
  模块取证（本班 267/267）；闸内改逐模块属 release_gate.py 改造项不顺手修。
  | 风险·在档 | 2026-10-07

## 2026-10-07 收口班B沉淀（第 4/4 步·并行双收口另一侧——分片闸②+三连提交+E409 定性）

> 与上文「收口班」互为并行双收口：三连提交（423f26a/e4d72cc/7195d57）系本班
> 执行侧，publish 闭环系收口班侧；两节合读即完整时间线。详录见
> 2026-10-07.md 收口班B节。 | 元信息 | 2026-10-07

- **闸②分片形态独立证据**：test_[a-r] Ran 1709（12 例失败与 05 时班同名同址、
  两轮签名逐字一致；净进程逐文件 8/8 全 OK=争用豁免）+test_[s-z] Ran 505 OK
  ——合计 2214 项，与收口班逐模块 267/267 互为旁证；「同进程污染 vs 跨进程
  互拖」归因未分离交人拍板。 | 测试·证据 | 2026-10-07
- **方法论（E409 previously staged）**：多会话并行收口时同版本重复 publish 被
  registry 拒 E409 ≠ 发布失败——先到者已 staged；正确动作=查 registry 实态
  （dist-tags+time 元数据+shasum 三点核对）定性，不重试不 revert。本班实证：
  E409 后 15 分钟 time 元数据落定 0.1.92（staged→materialize 窗口）。另两班
  同指令 `npm view` 一假一真（缓存窗口内/外）构成缓存坑完整边界样本。
  | 方法·发版 | 2026-10-07

## 2026-10-07 21 时班沉淀（批7：中文/网关/本地/办公——新一轮计划第 1/4 步全类型调研·批7 全量补课班）

> 开工 21:39（hour=21，21%7=0 → 批7）；分支 main（d897820，v0.1.92 已发版），
> 工作区干净。通道全恢复（gh api search 30/分满额起跑）。批7 今日系午后班
> WebSearch 降级 claim 级跑过，本班按 18 时班先例以 gh api 全量补齐——降级
> 班结论升全量实证的第一班。主扫 114 查询（A 常驻 89+B7 轮换 9+A1u/A1p2 双轮
> 16）514 行 452 唯一仓零失败零限流（PROGRESS DONE 114 实证）。详录见
> 2026-10-07.md 21 时班节。 | 调研 | 2026-10-07

- **awslabs/cli-agent-orchestrator（CAO）1,394★**（AWS 官方 org，Python，
  10-07 在更，PyPI `cli-agent-orchestrator` 在架）：本地 cao-server+provider
  CLIs 跑隔离 tmux 会话、supervisor 委派 specialist 并行/串行、agent 保持原生
  CLI 进程与认证；**12 provider**（Kiro/Claude Code/Codex/Antigravity/Hermes/
  Kimi/MiniMax/Copilot/OpenCode/OMP/Cursor/Grok Build）——**A1 域重磅同形态
  竞品**（多 CLI 编排正主进场）：catalog 14 条目 vs 其 12 provider、supervisor-
  specialist 与 planner-dispatch 同构、tmux 硬依赖=Windows 原生不支持（同
  openrig，我们 Windows 原生系差异化壁垒）；双生文档站+interactive courses
  产品化完成度高。 | **A1 对标入库（雷达/对标跟踪）** | 2026-10-07
- **dtyq/magic 5,043★**（超级麦吉 Magicrew，org 矩阵顺藤坐实：super-magic
  96★ 系旧仓，正主=magic 5,043★ pushed 08-12）：企业级开源 AI Agent 平台、
  自我定位「enterprise 版 OpenClaw」——Generalist Agent+Workflow Engine+IM+
  在线协同办公 all-in-one；卖点=统一数据/预算护栏（budget guardrails）/输出
  不止纯文本/高风险动作审批闸。**WebSearch 交叉验证捞出（143 组词各班从未
  命中）+orgs/dtyq/repos 矩阵顺藤坐实（repos 端点零搜索配额）——StaffDeck
  词形盲区第 2 例**（「Magicrew」非词库词形）；预算护栏/审批闸我们全有对应
  （_ensure_budget 七闸口/六源白名单闸门）；企业多租户+IM+协同办公系部署形态
  差异（我们桌面单机）。 | **B7 新面孔对标入库（雷达/对标）+keywords.md B7
  组补词形** | 2026-10-07
- **mixpeek/amux 520★**（Rust 单二进制 MIT，10-07 在更）：AI coding agents
  控制面——并行 Claude Code/Codex/Gemini workers+共享看板+原子任务+schedules
  +loops+origin-stamped messaging+模型切换+自愈恢复，dashboard 或手机。A1
  同形态（与 openrig 同族）；原子任务/schedules/自愈恢复机制面我们全有对应
  （jobs/automation.py/会话复用）。 | A1 同形判据（雷达） | 2026-10-07
- **23blocks-OS/ai-maestro 812★**（TypeScript）：Agent Orchestrator+技能
  系统——记忆搜索/代码图查询/A2A 消息/统一面板管理 Claude Code+Codex+Grok
  Build/跨机迁移 agent。A1 同形态中腰部；技能系统与经验库同向（我们已有）。
  | A1 同形判据（雷达） | 2026-10-07
- **hashgraph-online/hol-guard 802★**（10-07 在更）：**AI agent 运行时杀毒**
  ——拦截危险工具/密钥访问/prompt 注入/恶意包/MCP 服务器/插件/技能。B4 治理
  域新参照：我们三闸（SSRF+白名单+装前 skill_scan）系安装时闸，hol-guard 把
  「运行时拦截」做成独立防层——形态差异在档（不做检查范围外加固，交拍板）。
  | B4 判据·参照 | 2026-10-07
- **strukto-ai/mirage 3,677★**：「World's First Virtual Terminal for AI
  Agents」——A13 面板域（与 aden-hive/hive 同族，系统级可视化面板系真实差量
  但系面板工程，不立项不凑数维持）。 | A13 判据 | 2026-10-07
- **skill 形态垂直化信号三件**：QingYunA/answer-me-with-html 1,955★（中文
  作者「用一页 HTML 回答复杂问题」skill）/Klotzkette/claude-fuer-deutsches-
  recht 1,670★（德国法律垂直 skill 包，DSGVO/律师保密义务条款内建）/gooseworks
  -ai/goose-skills 1,234★（GTM/营销 skills 库：ads/social/SEO/lead gen）——
  skill 包向垂直行业纵深的生态信号（与 diagram-design 图表 skill 同向）。
  | 判据（生态信号） | 2026-10-07
- **批7 域结论：稳定期延续，降级 claim 升全量坐实零机制级新差量**——33 唯一
  仓头部全已录族（agency-agents-zh 21,093〔原版 msitarzewski/agency-agents
  154,263〕/LangBot 18,039/astron-agent 8,878/astron-rpa 5,255/Yuxi 7,293/
  agency-orchestrator 2,330/deep-eye 2,332 均 09-19~23 已录复认）；首见仅
  baserow 6,094★（no-code DB+AI agents 域旁）+AChat 3,259★（04-17 停更降级）
  +微型若干（@nathapp/nax loops-until-done/flutter_agent_harness 64★ 等）。
  与今日批1/2/3/4/5/6 各域结论同型。 | 域结论 | 2026-10-07

### 复查记录（21 时班）

- **存量 27 件 repos 实测全 alive 零 archived**（基准 vs 18 时班，间隔约 3h）：
  orca 86,779→**86,877（+98 续领跑）**/mattpocock-skills 278,722→278,958
  （+236）/superpowers 296,174→296,222/claude-mem 97,387→97,459/hermes-agent
  251,796→251,827/opencode 212,124→212,143/pi 113,094→113,130/**anthropics/
  skills 179,988→180,006 破 18 万**；放量族 **rea 11,225→12,036（+811 大爆发
  续三连）**/Strata 16,354→16,544（+190 续）/iFixAi 21,905→22,038（+133 放量）
  /ponytail 156,791（06 时基准）→157,289（+498）；持平族 DeepSeek-Reasonix
  35,743（**E 候选首位维持**）/beads 27,706/agentmemory 29,204/herdr 42,762/
  SkillSpector 19,614/context-mode 25,593/open-code-review 44,163/drama-skills
  2,572/webnovel-writer 7,346；对标件 openrig 5,644（+30）/eve 5,478/OpenShell
  15,219（+17）/agent-orchestrator 12,858/hive 11,090 持平/minimax-code 1,986。
  | 复查 | 2026-10-07
- **awesome 20 源 20/20 alive 零 archived**：hesreallyhim/awesome-claude-code
  55,193（+6）与 VoltAgent/awesome-agent-skills 35,313（+7）10-07 当日在更；
  Shubhamsaboo/awesome-llm-apps 140,921/e2b-dev/awesome-ai-agents 30,288 维持
  ——travisvn 15,298（较 18 时班 -3 微降、04-28 停更维持）。dsh 生态微型新生件
  （DSHana 55★/dsh-plugin-mesh 7★）；PerryLink org 404（历班 dsh 插件源 org
  疑属主迁移，未勘定如实记）。 | 复查 | 2026-10-07
- **topic 8 页全扫（本班恢复单扫，updated 排序）**：hol-guard/mirage/amux 等
  新面孔即出于此（8 页连扫 3 件 A1 同形反复在 updated 头部——topic:claude-code
  与 topic:ai-coding-assistant 系多 CLI 编排新件首发地，18 时班「让位主扫未
  单扫」本班补上，产出比预期厚）；npm 两查零大件；禅道周边 2 查头部全无关
  **第 28 例零新禅道 AI 竞品**（18 时班第 27 例顺延）。 | 雷达 | 2026-10-07

### 本班结论

1. **AWS 官方进场多 CLI 编排域是今日最大信号**：CAO（awslabs）与 openrig/
   amux/ai-maestro 四件同形态同日活跃——「多 CLI 归一编排」赛道 2026-10 进入
   大厂+创业密集期；CodeBee 机制面全有对应，Windows 原生+18 类型流程+经验库
   系差异化壁垒，维持对标跟踪不立项。
2. **降级班 claim 升全量闭环**：批7 午后班 WebSearch claim 级候选（墨枢/
   webfiction/ryk/hive 等）已由 18 时班+本班两班 repos 坐实或降级——关键词库
   10-07 补充纪律（降级班候选归 repos 班勘定）全链路走通第 2 例。
3. **词形盲区第 2 例入档**：dtyq/magic（超级麦吉）143 组词从未命中——专名/
   品牌词形（Magicrew/超级麦吉）与功能词形（digital employee）正交，B7 组补
   `q=magicrew+OR+超级麦吉`；C 雷达源补 `q=cli-agent-orchestrator+OR+amux+OR+
   ai-maestro 生态`盯 A1 同形三件增量。
4. 待深挖队列 11 项维持零新队列项（四件 A1 同形对标件机制面全有对应，不
   入深挖）；风险在档维持（交人拍板）：data/zentao.json 明文密码/批跑同进程
   争用/32 位 discover 静默退出/runner_drain 计时超界。 | 结论 | 2026-10-07

## 2026-10-07 22 时班沉淀（新一轮第 1/4 步·批1 全量补课+禅道新件破连）

> 与 21 时班并行时间线（其批7 全量、本班批1 全量 22%7=1），详录见
> 2026-10-07.md 22 时班节。 | 元信息 | 2026-10-07

- **B1 域全量补课闭环**：116 查询 419 唯一仓零失败——11 词头部全已录
  （SkillSpector 19,616/vuls/claude-code-security-review/BugTraceAI 等），
  域稳定期维持；14 时班降级 claim 全部坐实。B1-B7 七批今日全部完成 gh api
  全量级覆盖（03=B3/06=B6/14+22=B1/午后=B2/B5+B7/18=B4）。 | 主扫 | 2026-10-07
- **禅道零新竞品 28 例连被 npm 通道打破**：`@jw-king/dsh-plugin-zentao`
  （DSH bundle 插件连禅道 REST API，09-02 建，v0.1.17）——**第 29 例有新件**；
  教训：禅道周边此前只扫 GitHub 搜索 2 查，npm「zentao ai」查询系新增通道
  即命中（渠道正交性第 3 例：StaffDeck/magic 同款「换个渠道就有」）。
  | 发现·F | 2026-10-07
- **tigerless-labs org 顺藤四件矩阵入库**（trendshift autoharness 在榜触发）：
  cost-xray 3,833★（**A3 新参照**：逐部件拆解 CC/Codex 实发 API 请求的成本
  透视——token_meter 管自身压力、其管「请求解剖」，观测粒度差量交拍板）/
  pr-test-guard 139★（B1 微型）/agent-memory 959→**2,379 大放量**/autoharness
  **9,172（较 10-06 +1,159 放量加速，D 专项同域待深挖件）**。keywords.md 补
  `q=cost-xray+OR+autoharness+OR+pr-test-guard+OR+tigerless 生态`。
  | 发现·A3/D | 2026-10-07
- **两通道同日恢复**（21 时班双拦/14 班连断态解除，窗口性如实记）：
  trendshift curl 直抓 331KB 27 件（storytold 家族 4 兄弟扩仓+huashu-art-motion
  232→1,317 微型放量升级）；**pypi 第 15 班 HTTP 200 真 Simple index**
  （agent_orchestrator 2.0.0 在架，14 班 CSP 壳页连断销账）。 | 雷达 | 2026-10-07
- **WebSearch 串行 1 发（B1 域）**：Kodus 1,453/PR-Agent 13,294 已录复认；
  CodeAnt「~1,200★」claim in:name 勘定无大星实仓——**repos 二次实证规则
  第 6 例**（新闻面≠开源仓在）；fireup.pro「评审辩论降误报」未具名在档。
  | 验证 | 2026-10-07
- **D 经验勘误自记**：本班开工曾按 03 时班 lessons=73 基线误判「69=-4 异常」，
  核对 12 时班节确系当日去重新基线——复认 21 时班口径 69 零漂移（流程规范
  29/节奏爽点 16/情节逻辑 10/人物塑造 7/一致性 4/文笔风格 3）；教训：跨班
  对比基线先查基线确立班次再定性。 | 方法·自记 | 2026-10-07
- **存量复查 23 件与 21 时班互证**（orca 86,892 续领跑/VulnHunter +93 放量/
  knowledge-work-plugins +420/ccpm pushed 停 2026-03-18 停更观察新增）零
  archived 零属主失配。 | 复查 | 2026-10-07


## 2026-10-07 22 时班批2（七专项巡检+落地班）

- **A 专项四方向定谳（源码锚点全录）**：八件在位（三段压缩/token_meter/
  预算熔断×3 闸/maybe_compact 触发/分层降级×2 挂点/diff-only 评审×4 件/
  tier 廉价分流 modelhub:2525→2662/经验召回 bigram 双通道）——四方向
  「三有一不适用」：prompt 缓存系 CLI 底层自理（cached 字段已入 meter 不占
  压力，编排层做 cache_control 不适用）、语义缓存维持 bifrost 拍板件。
  | 巡检·A | 2026-10-07
- **recommendTaskType 四类型缺口修复（C/G 双专项产出）**：app.js 规则表
  11→15 条（+resume/presentation/bid_doc/doc），并纠真实截胡——「优化我的
  简历，投后端岗」被 code 的 /后端/ 抢先（find 首中即返），专属词三条前置
  code 之前。固化 test_recommend_rules.py 常驻对账（BUILTIN_FLOWS 除三快档
  全覆盖），后续新增流程类型漏配规则 discover 当场爆。**方法论：新增流程
  类型三对账——图标精灵表（test_flow_icons）/推荐规则（test_recommend_rules）/
  菜单动态渲染（renderTypeOptions 拉flows，天然零断链）**。 | 落地·C/G | 2026-10-07
- **market typosquatting 内置名盲区（B 专项，交拍板不扩展）**：
  _similar_names 只对 market.json 已装记录对账，不含两处 BUILTIN_PACKS
  内置名——仿内置名外部包（如 fanqie-nove1）不触发近似名告警（安检扫描与
  内容指纹对账仍生效）。安全增强非缺陷。 | 发现·B | 2026-10-07
- **经验库 61% 偏科口径勘正**：29/69=42% 为现行值，「61%」系 47 条时代口径，
  后续写作类入库自然稀释——后续班次对比偏科度请用 42% 基线。
  | 方法·D | 2026-10-07
- **发版 registry 传播窗（收口班实测，接 v0.1.92「npm view 缓存假象」条）**：
  npm publish 成功行（`+ codebee@x.y.z`，exit 0）先于 registry 可见约
  5-7 分钟——`/codebee/<ver>` 端点 404→200 渐进，packument(corgi) versions
  表滞后更久。**核对纪律升级：版本端点 200 + dist-tags latest 双条件齐才算
  发布坐实**；单看 npm view（缓存）或单看 publish 成功行都会误判。
  | 方法·发版 | 2026-10-07
- **全量 discover 截断 2 连（收口班在档）**：`unittest discover -s tests`
  单命令两次均 dots 中断、无 Ran/OK 汇总、退出码 0（排除真杀：portscan/
  kill_all 全 mock 复核；并行 15 个 python 进程争用窗口在档）——逐模块
  268/268 全绿作为闸②验收口径（46ba151 先例第 2 次复用）。根因待深挖：
  候选=套件内进程硬退 / 并行会话资源争用；后续收口班先试单命令，截断即
  走逐模块，不空转。 | 方法·测试 | 2026-10-07
- **code-reviewer 代理评审第 4 败**：API 400「模型不存在」（路由发 auto），
  与历班 3 败同根因——收口班代理评审在档按「逐 hunk 自审替代」执行，
  不再每班空试。 | 方法·评审 | 2026-10-07
- **release_gate 干净闸与收口顺序（实测）**：npm publish 触发的
  release_gate 要求工作区跑前跑后双干净——收口班须先落 docs 提交再发布，
  「docs 最后提交」惯例与之冲突，改为「docs 先行 → publish → 补录再提交」
  两段式。 | 方法·发版 | 2026-10-07

## 2026-10-08 00 时班沉淀（批7：中文/网关/本地/办公——新一轮计划第 1/4 步全类型调研·七专项巡检班）

> 开工 00:40（hour=0，0%7=0 → 批7）；分支 main（d0ce51e，v0.1.93 已发版），工作区
> 干净。主扫 114 查询（A 常驻 89+B7 轮换 9+A1u/A1p2 双轮 16）514 行 447 唯一仓
> 零失败零限流；WebSearch 串行 1 发+repos 勘定；trendshift/topic/npm/pypi 全通道
> 可达。详录见 2026-10-08.md。 | 元信息 | 2026-10-08

- **andrewyng/openworker 18,461★**（吴恩达，10-07 在更，Beta，macOS 签名+Windows
  x64，openworker.com）：开源桌面 AI 同事「交付成品而非聊天」——specialist
  coworkers（安全审查/云姿态/事件分诊先发；**确定性扫描器+模型推理、修复重扫+
  diff 评审「fixer 绝非唯一检查者」**）；standing automations（晨报/周报/频道守望
  定时任务带全转录）；Governed by design（每动作治理+日志，consequential 动作前
  check-in 批准）；BYOK 四家+Ollama 全本地；NVIDIA OpenShell 沙箱；Python agent
  server 基于 aisuite。**A1/A2 域重磅同形态竞品**（WebSearch 交叉验证捞出+repos
  坐实，主扫 114 组从未命中——个人品牌词形盲区第 3 例〔StaffDeck/magic 同款〕）：
  standing automations≈automation.py、specialist coworkers≈内置流程/skillpacks、
  治理审计≈audit.py+评审闸——机制面全有对应；CodeBee 差异化=Windows 原生深度
  （其 Windows 未签名）+18 类型内容流程+经验库自学习，「fixer 绝非唯一检查者」
  与 diff-only 评审闸同构系行业共识再证。 | **A1 对标入库（雷达/对标跟踪）**
  +keywords.md C 雷达源补自家周边词形 | 2026-10-08
- **anthropics/claude-plugins-official 37,496★**（10-07 在更）：Anthropic 官方插件
  策展目录——/plugins（官方维护）+/external_plugins（伙伴/社区，**须过质量与
  安全审批上架**）+`/plugin install` 直装；plugin.json+.mcp.json+commands/agents
  标准结构。B 专项市场域参照：官方把「策展+审批上架」做成一级市场，与我们六源
  白名单闸门+装前 skill_scan 同构互证（外部审批制=我们装前三问）。 | **B 专项
  参照入库（雷达/对标）** | 2026-10-08
- **Dicklesworthstone/coding_agent_account_manager（caam）208★**（Go，10-07 在更）：
  AI coding CLI 订阅账号秒切器——撞 Claude Max/GPT Pro/Gemini Ultra 限额时
  sub-100ms 换号替代 60s 浏览器 OAuth。A13 凭据/账号管理域新参照（与
  agent-credential-vault 族同域）；与编排台核心不重合，雷达跟踪。 | A13 参考判据
  | 2026-10-08
- **chengyi-ai/native-subtitle-quote-image 1,937★**（中文作者，10-07 在更）：
  「保留视频内嵌字幕+精确取帧+生成 3:4 社交长图」Agent Skill（skill 形态可直读）。
  短视频脚本/自媒体文章流程的**成品后处理**参照：我们产出脚本文字流，其系
  「脚本→配图物料」真实增量面。 | **借鉴方向（video_script/article 域），交
  第 3/4 步评审** | 2026-10-08
- **微型/参照判据一批**：google-antigravity/antigravity-sdk-python 3,654★（Google
  官方 agent SDK 第 4 件，域旁参考）/posit-dev/skills 529★（Posit 官方数据科学
  skills 集合，skill 垂直化信号第 4 例）/lofcz/LLMTornado 643★（.NET LLM 编排
  框架，域旁跟踪）/@polderlabs/bizar npm 10.33.0（Codex+Claude Code 自动化
  harness，GitHub 大星正主未勘到，跟踪）。 | 判据（雷达） | 2026-10-08
- **EverMemOS claim 降级排除**：WebSearch 捞出「盛大团队开源记忆 OS」，in:name
  勘定全 GitHub 无大星正主（最大 ZhenhangTung/openclaw-EverMemOS 12★ 且 03 月
  停更）——**repos 二次实证规则第 7 例**（新闻面≠开源大仓在）。智谱清流中文名
  搜索零回未勘定，跟踪不结论。 | 验证 | 2026-10-08
- **awesome-claude-skills 正主勘定**：旧注属主 anthropics/ 404 失配——in:name
  一次勘定正主 **ComposioHQ/awesome-claude-skills 76,657★**（travisvn 15,299 系
  同名镜像噪声）；keywords.md C 源已补注。 | 复查·勘定 | 2026-10-08
- **F 专项交拍板件（本班唯一）**：data/zentao.json 实测 `poll_enabled: false`、
  last_scan 停在 **2026-09-21 20:43（17 天未扫）**、last_error 空——**定时扫描
  系配置关闭非故障**；产品档案路由 product 96 在位。不擅自改用户配置，交用户
  拍板是否重开轮询（或设置页「立即扫描」手动触发）。竞品面第 30 例：npm 通道
  第 2 班连用又中 **zentao-cli 0.3.1**（「对 AI Agents 友好」人用 CLI，与我们
  自动扫描修复闭环形态不同、无冲突）+zentao-api 0.7.2 SDK；GitHub 2 查零新；
  **禅道自动修复集成面我们仍独占**。 | 发现·F（交拍板） | 2026-10-08
- **七专项静态巡检零漂移**：A 八机制锚点在位（token_meter:148/pipeline:613+648+
  2538/step_runner:28 _PRECHECK_RATIO=0.9）、四对标方向「三有一不适用」维持；
  B 六源在位+零新接入（openworker 桌面应用/caam 切换器均不满足接入三问）；
  C BUILTIN_FLOWS=18 与 README「18 种」一致+recommendTaskType 修复态在位
  （app.js:837/:842 实读）；D lessons=69 基线零漂移（42% 流程规范口径沿用）；
  E catalog=14 条目本机 12/14 在装（openclaw 缺），DeepSeek-Reasonix 35,744（+1）
  候选首位维持不盲接；G UI 零新毛病（「单双源」过时文案 grep 零命中）。
  | 巡检·A/B/C/D/E/G | 2026-10-08

### 复查记录（00 时班，基准=22 时班间隔约 2h）

- 存量头部全 alive 零 archived：superpowers 296,300（+78）/mattpocock-skills
  279,178（+220 续放量）/hermes-agent 251,860（+33）/opencode 212,167（+24）/
  anthropics-skills 180,028（+22）/ponytail 157,383（+94）/**orca 86,977（+85
  续领跑）**/pi 113,153（+23）/agency-agents-zh 21,098（+5）/StaffDeck 1,971
  （+2）。 | 复查 | 2026-10-08

### 本班结论（00 时班）

1. **吴恩达 openworker 系最大信号**：桌面 AI 同事新竞品与 CodeBee 形态最接近
   （standing automations/specialist coworkers/governed 三面全同构），机制面对照
   后维持对标跟踪不立项；词形盲区第 3 例入档（「OpenWorker」系人名+品名复合
   词形，功能词组正交）。
2. 官方策展市场二连（claude-plugins-official+posit-dev/skills）——插件/skill 生态
   向「官方审批上架」收敛，与六源白名单闸同构互证。
3. 批7 域稳定期延续零机制级新差量；B7 域全量 21 时班坐实后本班轮换复认。
4. 待深挖队列 11 项维持；交拍板 2 件在档（禅道轮询重开+cost-xray 观测粒度）。
   | 结论 | 2026-10-08

## 2026-10-08 01 时班沉淀（第 2/4 步·七专项 A-G 深化实证+落地提案班）

> 开工 01:02；分支 main（1df1f27，v0.1.93 已发版）、工作区干净。以 00 时班调研
> 底稿为基线，只读巡检零代码改动，禅道全程只读零触发。行号全部本班独立实读。
> 详录见 2026-10-08.md 01 时班节。 | 元信息 | 2026-10-08

- **E 专项计数勘正（13/14 非 12/14）**：DEFAULT_CATALOG 14 条目（catalog.py:32）
  本机 which 按探测名实测 13/14 在装（仅 openclaw 缺）——00 时班「12/14」少计
  1 件；「探测名≠包名」诫（10-05 18 时班首立）第 2 例复用。 | 勘误·E | 2026-10-08
- **F 专项补验：禅道关闭态 UI 可感知**：renderZentaoStatus（app.js:12049-12064）
  关闭态明示「定时扫描未开启（仍可手动『立即扫描』）」+上次扫描时间——00 时班
  交拍板件（poll 重开）之外 UI 侧无缺口，用户可自行感知。 | 巡检·F | 2026-10-08
- **三候选三撞既有面（零代码提案的方法论依据）**：本班提落地提案前逐一对账——
  ①补 rank_scan/defect_retro 推荐规则→test_recommend_rules.py:21 `_RULELESS`
  豁免集在案（成文决策非漏配）；②禅道关闭态提示→renderZentaoStatus 在位；
  ③app.js i18n 覆盖守卫→test_borrow_iteration.py 10-07 已落地（1642 处 t()
  字面量本班脚本复测零缺失）。**方法论：巡检班提「补齐类」提案前，先 grep
  既有守卫测试（tests/test_*rules*/*coverage*/*i18n*）与代码内豁免集/状态渲染，
  三撞既有面即撤案——第 2/4 步「零新缺口」属正常产出非空转**。
  | 方法·巡检 | 2026-10-08
- **A 专项八锚点独立复认（与 10-07 04 时班行号零漂移）**：预算熔断 pipeline:613+
  :632+:648-669（花费闸先于 token 闸 :676）/:703 接线/compaction.py:1-30 三段式
  （8192/4096 剪枝→摘要→surface replace）/token_meter.py:23+:60+:84-85（cached
  不计压力）/step_runner.py:28 _PRECHECK_RATIO=0.9/cascade :1526-1529→
  capability.py:100/_shrink_context_block :2538-2578/skills.py:676 block_for
  stable_order/diff 评审 :919-948+:1019/_resume_sid :270-283。四对标方向判定
  维持（prompt 缓存供应商侧/语义缓存待拍板/diff-only 满配/cascade 满配）。
  | 巡检·A | 2026-10-08
- **B 专项零新接入**：六源（market_remote.py:50-76）+装前 skill_scan（market.py:
  280-281）+三层防护（体量上限 :91-99/逐跳复验 :99/SSRF :113-133）全在位；
  本班候选 openworker（桌面应用）/caam（账号切换器）/claude-plugins-official
  （策展目录）三问全不过；native-subtitle-quote-image 不在六源清单、扩源非
  小而实——全部雷达跟踪。 | 巡检·B | 2026-10-08
- **落地提案 1 件（第 3 步执行清单）**：D 专项蒸馏入库「落地提案先对账既有
  守卫」——skills.upsert_lesson（skills.py:424，category=流程规范，scope=code，
  source="borrow-log 2026-10-08"）；验收 69→70 零重复，回滚按 title 精确移除。
  | 提案·D | 2026-10-08

### 待深挖队列（01 时快照）与未验证项

- 21 时快照 11 项全部维持（第 3/10 保持划掉）；本班零新队列项。风险在档维持
  （交人拍板）：data/zentao.json 明文密码；32 位全量 discover 静默退出；
  runner_drain 计时超界；批跑同进程测试间争用面。 | 队列 | 2026-10-08
- 未验证项：禅道积压/路由/回写链路（无实例权限，poll 关闭态不触发真实扫描）；
  keywords.md 本班零调整（零真实关键词缺口不动）。 | 未验证 | 2026-10-08

## 2026-10-08 04 时班沉淀（第 4/4 步·五道关+发版收口班，v0.1.94 已发）

> 分支 main；f9b5176（docs+新测试）与 1e6fbfe（发版元数据）双提交双推送一次成；
> npm publish 过 release_gate 约 15 分钟，registry view 坐实 0.1.94。详录见
> 2026-10-08.md 04 时班节。 | 元信息 | 2026-10-08

- **「32 位全量 discover 静默退出」第 2 次实锤且形态更险**：本轮 exit code 也是
  0、日志截断在末测试判定处、无 Ran/OK 汇总——仅凭退出码会把截断 run 误判全绿。
  逐模块净进程 269/269 全绿为权威证据（v0.1.91 先例形态第 2 次复用）。
  | 勘误·测试 | 2026-10-08
- **unittest 逐模块驱动判据教训**：模块汇总「OK」之后可能还有测试自身的 print
  （test_architecture_check 的「架构检查 OK…」行），「末行 ^OK」判据误报 FAIL；
  严格判据=^FAILED 缺席 + `^OK( \(skipped=N\))?$` 在场 + Ran 计数对账。
  | 方法·测试 | 2026-10-08
- **gitignore 否定规则勘误（无需 -f）**：/tests/* 配 `!/tests/test_*.py` 否定
  规则，新测试普通 add 即可入库——「任务书说 ignored 就 -f」不如
  `git check-ignore -v`+`add --dry-run` 实测（与「探测名≠包名」同型：规则要看
  实际配置不照抄预期）。 | 勘误·流程 | 2026-10-08
- **release_gate 判定面盲区（交拍板第 3 件）**：scripts/release_gate.py
  run_tests() 只看退出码不校验 OK 汇总，32 位静默退出可假绿过闸；本轮以逐模块
  269/269 权威证据放行，是否给门禁加 Ran/OK 汇总校验交人拍板。
  | 风险·发版 | 2026-10-08

### 04 时班补录（并行收口班的对侧视角，核验+补位实录）

- **并行会话收口处置纪律**：五道关推进中工作区被并行会话收口（f9b5176+
  1e6fbfe 双提交双推送）——不覆盖不重做，逐字核验其内容与此前自审 diff
  完全一致后接纳；计数分歧（268 vs 269）以 `ls tests/test_*.py | wc -l`
  实测归一（并行对）。 | 方法·并行 | 2026-10-08
- **publish「挂起」先想 prepublishOnly 在跑全量（误杀教训）**：npm publish
  挂零输出≠网络挂起——package.json prepublishOnly 钩子=release_gate.py，
  首关即单进程全量 discover（3600s 超时），门内测试期 publish 外表死寂
  15-20 分钟；本轮前两次尝试当网络抖动错杀，第三次直写文件复跑才见
  `[release-gate] PASS`。判据：先 `npm view`+`npm ping` 定网络，再看
  package.json scripts 钩子定卡点。 | 方法·发版 | 2026-10-08
- **并发 publish 409 幂等无害**：多会话/超时误杀竞态下，第二完成者撞
  「cannot publish over previously published versions」属正常收口信号；
  registry `npm view time` 时间戳是唯一事实源（本轮 02:16:46+0800），PUT
  归属不作唯一判定（同树同提交产物归一）。 | 方法·发版 | 2026-10-08
- **npm 包打包卫生实测（补上述未验证项）**：0.1.94 归档 169 文件 6.3MB，
  publish 台账实读——validate_package_members 拦 /tests/ /data/ /.git/ 等
  但**未拦 app/.playwright-cli 控制台日志与 page 截图（约 1.1MB）、
  app/browser-*.png 历史截图（约 1.5MB）、app/output/_probe_*.py 探针
  脚本**；不影响功能，属包体卫生，是否收紧 files 白名单交拍板（第 4 件）。
  | 风险·发版 | 2026-10-08

### 待深挖队列（04 时快照）与未验证项

- 01 时快照 11 项维持；交拍板 2→4 件（release_gate 判定面校验+npm 包体
  files 白名单收紧）。 | 队列 | 2026-10-08
- 未验证项：npm 包归档成员校验/解包冒烟由 release_gate 代跑——归档内容
  清单已经 publish 台账实测（见上打包卫生条）；keywords.md 本轮零调整
  维持。 | 未验证 | 2026-10-08

## 2026-10-08 03 时班沉淀（批3：计划/spec/长任务——新一轮（v0.1.94 后）计划第 1/4 步全类型调研·七专项巡检班）

> 开工 03:40（hour=3，3%7=3 → 批3）；报告锚定命名 docs/borrow-log/2026-10-09.md
> （实际开工时刻在报告头部如实记）。分支 main（3cfe517e 前基线 3cfe4ee）、工作区
> 干净。主扫 116 查询（A 常驻 89+B3 轮换 11+A1u/A1p2 双轮 16）519+5 行 462 唯一仓
> 零失败零限流；**Q115 处后台 600s 时限杀、余 1 查当场单发补齐（116/116 全覆盖）**；
> WebSearch 串行 1 发（B3 域，零新 claim）+trendshift/topic/npm/pypi/awesome 20 源
> 全通道可达。 | 元信息 | 2026-10-08

- **全通道收敛稳定期（本班总判定）**：批3 域两周一轮换零新面孔冲头部（OpenSpec
  71,262/get-shit-done 64,360/planning-with-files 27,321 已录族领跑）；主扫 136
  首见全噪声/课程库/停更旧件；trendshift 50 仓+topic 8 页 160 仓+WebSearch 三交叉
  通道**判据线以上零新**——零「对方有我们没有」机制级差量，与 00 时班批7 域结论
  同型。 | 主扫·批3 | 2026-10-08
- **Dicklesworthstone/coding_agent_session_search 1,162★**（10-07 在更，topic:ai-agents）：
  统一 TUI/CLI 索引检索本地 coding agent 会话历史，跨 11+ provider（caam 同作者
  第二件）。A13 观测域：我们运行页自带会话流无跨 CLI 本地历史聚合面，属工具面
  非编排面不重合。 | A13 参考判据（雷达） | 2026-10-08
- **Azure/agent-landing-zone 1,184★**（10-07 在更，topic:agent-framework）：Azure
  官方企业级 AI agent 落地基座（安全基础设施+部署模板）。B4 治理域参照：云厂把
  「agent 安全落地基座」产品化，与桌面单机形态不重合；治理清单可作 audit.py
  对照面。 | 参照判据（雷达） | 2026-10-08
- **undefined-ui/second-brain-os 1,005★**（10-07 在更，topic:claude-skills）：自维护
  AI 第二大脑（指南+起始 vault+agent skills+自组织脚本）。A11 记忆域，个人知识
  管理形态无编排差量。 | A11 参考判据（雷达） | 2026-10-08
- **KroMiose/nekro-agent 1,135★**（09-29 在更，主扫 A1p2 中文词首见）：中文 QQ 平台
  agent 框架（沙箱插件执行）。中文 agent 生态判据补充（CowAgent 同域已录），IM bot
  形态不重合。 | 参考（中文生态雷达） | 2026-10-08
- **scaleapi/agentenv-framework 169★**（10-07 在更）：Scale AI 官方 RL 评测环境构建
  框架——A6 评测域旁（我们 rubric 评审系产出质量评测非 RL 环境）。 | 参考（域旁）
  | 2026-10-08
- **微型判据群+B4 趋势信号**：claude-mem-lite 66/human-review 19（人审一页纸，
  diff 评审同向）/localharness 46（本地 LLM harness 词形）/tale 32/membrane 64
  （提示词注入边界）/agent-blackbox 26/agent-bom 31（agent 供应链 SBOM）/
  tinymemory 10/workhorse 19 等——**「agent 安全边界」微型件连续第 3 班成簇，
  B4 域趋势信号在档**。 | 微型判据（雷达） | 2026-10-08
- **放量信号**：**strands-agents/harness-sdk 7.4k（9-21）→7.55k（9-23）→8,722
  （本班）两周 +~900 放量加速**（AWS 官方 harness SDK 化）；Compositor 11,034
  （+1,852/日，域旁创作工具）；autoharness 9,177/agent-memory 2,380/cost-xray
  3,834 微涨续。 | 复查·放量 | 2026-10-08
- **D 基线更新 69→70**：v0.1.94 蒸馏件「落地提案先对账既有守卫」入库后真实库
  lessons=70（流程规范 30=43%/节奏爽点 16/情节逻辑 10/人物塑造 7/一致性 4/
  文笔风格 3）——**后续班次以 70 为基线**（勿沿用 69 误判漂移，10-07 22 时班
  「跨班对比先查基线确立班次」诫第 2 例）。 | 基线·D | 2026-10-08
- **七专项静态巡检零漂移**：A 八锚点在位（pipeline:613/:648/:677 花费闸先于
  token 闸/:2538、step_runner:28、skills:676）四对标判定维持；B 六源在位零新接入
  （session_search/second-brain-os/agent-landing-zone/story-skills 三问全不过）；
  C BUILTIN_FLOWS=18+recommendTaskType（app.js:837）+_RULELESS 豁免集在位；
  E catalog=14 本机 13/14 在装（openclaw 缺）、候选五 CLI 全 MISSING 零盲接、
  DeepSeek-Reasonix 35,747（+3）首位维持；F 禅道 poll_enabled=false + claims={}
  零积压 + last_scan 停 09-21（18 天未扫）交拍板维持、npm 通道第 31 例零新、
  自动修复集成面仍独占；G 过时文案 grep 零命中零新毛病。 | 巡检·A/B/C/E/F/G
  | 2026-10-08
- **后台主扫时限教训**：百查询级主扫（116 查×~5s≈10 分钟）放后台须给足时限
  （≥900s）——本班 600s 上限杀于 Q115，余 1 查单发补齐纯属余量运气；补查无损
  但进程形态（无 PROGRESS DONE）要在报告如实记。 | 方法·扫描 | 2026-10-08

### 复查记录（03 时班，基准=00 时班，间隔约 3h）

- 存量头部 462 仓全 alive 零 archived：orca 87,046（+69 续领跑）/superpowers
  296,340（+40）/mattpocock-skills 279,351（+173 续放量）/ECC 274,810（+90）/
  hermes-agent 251,898（+38）/opencode 212,190（+23）/ponytail 157,476（+93）/
  dify 158,033（+9）/pi 113,169（+16）/open-design 99,852；oh-my-openagent
  69,872（+33）/nimbalyst 1,848 持平/story-skills 277（+13）/magic-context 2,275
  （+7）/amux 521（+1）；StaffDeck 1,969↔1,971 ±2 系缓存抖动非信号。 | 复查
  | 2026-10-08

### 待深挖队列（03 时快照）与未验证项

- 04 时快照 11 项维持，本班零新队列项；交拍板 4 件维持（禅道轮询重开/cost-xray
  观测粒度/release_gate 判定面/npm 包体 files 白名单）。 | 队列 | 2026-10-08
- 未验证项：禅道积压/路由/回写链路（poll 关闭态不触发真实扫描）；pypi 仅在架
  验证；trendshift 提取法第 2 班（50 仓全量成功，置信度升高）。keywords.md
  本班零调整（B3「requirement elicitation」零回系词形弱非缺口，maintain）。
  | 未验证 | 2026-10-08

## 七专项深化实证（第 2/4 步·调用链级，10 时班）

- **A 四对标方向调用链实证（静态锚点→执行链）**：①diff-only 评审=满配——
  pipeline.py:919 `CODE_REVIEW_PROMPT`（「diff 为主要依据」）→ :948 `_git_diff`
  （git diff HEAD + 未跟踪新文件拼入，新章节/新模块不漏）→ :1019
  `_review_depth_note`（评审深度随 diff 行数分级，pr-af 借鉴）→ :1084/:1089
  拼装；②廉价分流=opt-in 满配——capability.py:100 `cascade_reorder`
  （FrugalGPT tier 升序稳定重排，链<2 原样返回；providers 在场时走
  dispatch.rank_model_entries 精排）→ pipeline.py:1526 `ss_get("cascade",
  "enabled")` 门；③prompt（前缀）缓存=设计面缓存友好——skills.py block_for
  stable_order 保序 + knowledge.py 头注「同一任务字节稳定不碎供应商前缀缓存」
  + flows.py:283 `flow_content_sha256` 语义字段恒定；显式 cache_control 断点
  仍属供应商侧自理（拍板件维持）；④语义缓存=拍板件维持（重复任务占比低，
  缓存键失效面>收益）。预算链复核：pipeline.py:61 `_ensure_budget` →
  :648 花费闸先于 :677 token 闸 → :2538 `_shrink_context_block` 四层降级 →
  step_runner.py:28 `_PRECHECK_RATIO=0.9` 事前门 → compaction.py:182
  `maybe_compact`（压力比≥阈值才压，llm_caller 缺失直接跳过）。| 巡检·A
- **B 白名单/SSRF 边界实证**：market_remote.py:50 SOURCES len=6 实测
  （zcode/anthropic/anthropic-skills/claude-skills/clawhub/cocoloop）；
  :114 SSRF 网关（仅 https + 解析出的全部 IP 拒环回/私有/链路本地/保留段）+
  :156 逐跳 GET 重定向每跳重过闸 + :202 直连重试仍过闸；体量五帽（清单 5MB/
  包 80MB/解包 120MB/500 文件/512KB 单文本）；安装白名单 :568 `inspect_tree`
  + :941-952 安装包 skills 白名单比对（「白名单技能与包内容不匹配」拒装）。
  本班判据件接入三问复答零变化：session_search（独立 TUI 非六源包/可直读但
  非包/用户不按包搜）不接、second-brain-os（个人 vault）/agent-landing-zone
  （云基座）形态不重合不接，story-skills 277★ 维持雷达。**零新接入**。
  | 巡检·B
- **C 18 类型×推荐规则×i18n 三面对账全绿**：BUILTIN_FLOWS=18（flows.py:36
  实数）↔ app.js recommendTaskType 15 规则（18−_RULELESS 豁免 3：direct/
  rank_scan/defect_retro 成文决策）一一对应；18 name+18 note+18 goal_hint
  共 54 串在 i18n.js 全部有键（脚本实测 missing=0）；README:135/242「18 种」
  口径准确。推荐规则正则与 goal_hint 引导语对账发现 1 处小缝隙→落地提案 B
  （video_script 规则缺「B站/视频号」，其 goal_hint 明示引导用户写这两个词）。
  | 巡检·C
- **D 基线 70 复核+零互含重复**：data/skills.json lessons=70 实数（流程规范
  30=43%/节奏爽点 16/情节逻辑 10/人物塑造 7/一致性 4/文笔风格 3）；title 两两
  互含扫描 **0 对**（upsert_lesson:424 `_title_containment` 去重在位生效）；
  scope 分布 serial_novel 41/code 12/*/12/direct 4/article 1。**「百查询主扫
  后台 ≥900s」方法论判不进 lessons**——lessons 是任务运行时注入经验（写作/
  code 域），扫描运维纪律属 borrow-log 面，维持 knowledge.md 沉淀不双写
  （knowledge.py 头注分工边界：lessons=下次怎么做，knowledge=已知是什么）。
  本班零 lessons 入库（无新写作/code 方法论）。| 巡检·D
- **E 14 条目×本机 13/14 复测零漂移**：catalog.py:32 DEFAULT_CATALOG=14
  实数；which 探测 codex/claude/opencode/qwen/aider/kimi/mimo/grok/pi/dsh/
  gemini/cbc/trae-cli **13 OK**、openclaw MISSING（与 03 时班一致）；候选五
  CLI（deepseek-reasonix/fuxi/gitlawb/zero/empryo）which 全 MISSING——零盲接
  防死链维持，DeepSeek-Reasonix 首位候选待本机实装后再评。| 巡检·E
- **F 禅道链路实证（配置关闭非故障坐实）**：data/zentao.json 实测 config.
  poll_enabled=false + base_url=http://10.143.132.5:8899（内网自建）+
  auto_resolve/auto_merge/triage_ai 全 true + claims={} 零积压 + last_error
  空 + last_scan 停 2026-09-21 20:43:44（17 天未扫，poll 关闭所致）。
  zentao.py:80 `_SCAN_LOCK` 单飞防重、:163 `_guard_url` 拦元数据地址不拦用户
  显式内网 URL（合理）；resolve 前置双验（合并落库+auto_merge 双开关）在位。
  轮询重开仍系交拍板件不擅动。| 巡检·F（交拍板维持）
- **G UI/契约一致性**：过时文案（「13 种」「单源/双源」）grep README+app/ui
  零命中；流程编辑器 saveFlow（app.js:10960）payload 与 _EDITABLE 白名单对
  齐（review 引擎 8 字段，code 引擎无 verify_command 编辑入口——任务级
  f-verify 在创建表单配，设计口径自洽）；分工码 CBFLOW1 导入端到端链路在
  upsert_flow 全套规范化内。**缝隙 1 件**（=提案 A 根因）：flows.py:233
  `_apply_overrides` 把 verify_command 与 manuscript 共用文件名消毒（`/`→`_`
  、连续点→`_`），与 upsert_flow:385 code 分支（仅截断 200）双口径——分享码
  roundtrip 含路径验证命令会被静默改坏（`pytest tests/test_a.py`→
  `pytest tests_test_a.py` 必失败），见落地提案 A。| 巡检·G

### 落地提案（第 3/4 步实施件，复核通过后动代码）

- **提案 A（flows.py verify_command 消毒分流，契约一致性）**：flows.py:233
  `elif k in ("manuscript", "verify_command")` 拆开——manuscript 保留既有消毒，
  verify_command 改 `str(v or "").strip()[:200]`（与 upsert_flow:385 自定义
  code 分支同口径）。影响链：upsert_flow→_apply_overrides（预置 code 流程
  overrides/分享码导入）；消费链 pipeline.py:996/:1207
  `runner.run_process(shell_cmd=verify_command)` 真实执行。验收：
  ①`_apply_overrides` 传 `{"verify_command": "python tests/run_all.py"}`
  输出保真；②manuscript 消毒行为不变（`a/b.md`→`a_b.md`）；③分享码导出→
  导入 roundtrip verify_command 保真。回归：tests/ 新增
  test_flows_verify_contract.py 3 用例（git add -f 提交）。
- **提案 B（recommendTaskType video_script 规则补词，1 行+2 用例）**：
  app.js:856 video_script 正则补 `|b站|B站|视频号`——flows.py:66 goal_hint
  明示引导「抖音/B站/视频号」，用户照提示写「做个B站视频讲XX」当前不命中
  推荐直接落快档。回归：tests/test_recommend_rules.py 追加 2 用例（「B站
  视频」→video_script、「视频号脚本」→video_script，py 正则提取法已有基建
  :21 `_rules()` 直接复用）。
- 不凑数声明：本轮调研零机制级新差量，A 四对标均已有满配/拍板在档，D 无新
  方法论入 lessons——两提案均为本班深化实证中**实测发现**的契约缝隙，非
  为凑数。| 提案 | 2026-10-08

## 2026-10-08 落地+收口班实录（第 3-4/4 步：提案 A/B 落地+五道关+发版 v0.1.95）

- **提案 A/B 落地（657dc13）**：A=flows.py `_apply_overrides` verify_command
  拆分支（strip+截断 200，与 upsert_flow 自定义 code 分支同口径；manuscript
  消毒原样）——修「含路径验证命令被文件名消毒改坏必失败」契约缝隙；
  B=app.js recommendTaskType video_script 补 `b站|视频号`（goal_hint 引导
  语与规则同步）。回归 7 用例并入 test_borrow_iteration.py 累积守卫文件
  （未另开新文件，复用 `_rules()` 提取基建）。 | 落地 | 2026-10-08
- **单进程 discover 32 位中途崩退复现+兜底形态再坐实**：本班单进程
  discover 于 test_serial_draft_forbidden 中途死退无汇总行（崩退点模块
  单跑 2/2 绿，证进程态风险非代码回归）→ 逐模块净进程 **269/269 全绿**
  收口。判据固化：单进程 discover 无汇总行=在案风险形态，直接切逐模块
  净进程，勿反复重试单进程。 | 方法·测试 | 2026-10-08
- **发版 v0.1.95 三路坐实**：test_selfupdate 6/6 → 0.1.94→0.1.95 →
  CHANGELOG+README relnotes → 7a1e62b push → npm publish（release_gate
  门内死寂约 16 分钟，04 时班「prepublishOnly 在跑全量勿杀」教训直接
  复用零误杀）→ `+ codebee@0.1.95` 169 文件 6.3MB → `npm view`=0.1.95 +
  registry time=2026-10-07T21:08:19Z。publish 后 `npm view version` 有
  **约 5 分钟 CDN 传播窗**（先回 0.1.94，8 分钟后到位）——核对勿在
  publish 后立刻判失败。 | 方法·发版 | 2026-10-08
- **待推送先审查 HEAD 相对 origin/main 全量**：本班开工时 origin/main
  之上挂上一班 docs 纯沉淀件 3cfe4ee——按「确认只有可发布内容后随本班
  同行推送」口径处理，两推零抖动（a5a4d2c..657dc13、657dc13..7a1e62b）。
  | 方法·推送 | 2026-10-08

## 2026-10-08 06 时班（新一轮 v0.1.95 后第 1/4 步·批6：框架/平台/SDK 生态）

> 分支 main（f16e4d1）、工作区干净。主扫 115 查询（A 常驻 89+B6 10+双轮
> 16）410 唯一仓零失败零限流（后台 1800s 给足未被杀——10-09 报告「≥900s」
> 教训直接复用）；雷达 C 全过；WebSearch 串行 1 发（B6 域定向）。报告底稿
> =docs/borrow-log/full-type-review.md（本班新建）。

### 本班新面孔与信号

- **agno-agi/agno 42,603★**（10-07 在更，WebSearch 交叉验证捞出+repos
  坐实）：全栈 agent 平台（原 phidata，「Build, run, and manage agent
  platforms」——agentic RAG/记忆/工作流/评估/多租户 runtime）。
  **B6 框架域重磅对标件**：比 microsoft/agent-framework（13,997★）大 3 倍；
  其系 SDK+runtime 消费层框架、CodeBee 系跨 CLI 桌面编排台，形态不同但
  「agent 平台全栈」愿景同域，RAG/记忆/评测面 CodeBee 均有对应物。
  **「头部查询 per_page=5 剪切线+词形盲区」第 4 例**（StaffDeck/magic/
  openworker 同型——品牌词与功能词正交，147 组词从未命中 42.6k★ 大仓），
  10-04 补纪律（单通道结论须 WebSearch 串行交叉验证）持续有产出实证。
  | B6 对标入库 | 2026-10-08
- **screenpipe/screenpipe 21,853★**（10-07，topic:mcp updated 头部首见）：
  YC S26「Open Computer History」连续录屏+索引+检索。A5 computer use 域旁
  （录屏记忆非 agent 控制，与编排台不重合）。| 参考（域旁雷达） | 2026-10-08
- **微型判据**：Sri-Krishna-V/awesome-adk-agents 348★（ADK 策展清单，
  08-18 两个月未更）/Nachx639/context-canary 0★（Claude Code 行为像素
  金丝雀，A3 词形同向无机制面）。| 微型判据（雷达） | 2026-10-08
- **放量信号**：addyosmani/agent-skills 101,666→**102,753（+1,087/两日）**
  （10-06 入库后持续放量）；pacifio/atlas 8,886→**9,269（+383 快涨续，
  09-30 入库以来一月 +838）**。 | 复查·放量 | 2026-10-08
- **属主勘定 2 例**（in:name 搜索法）：story-skills 正主=danjdewhurst
  （277★ 10-07 在更）/magic-context 正主=cortexkit（2,276★ +1）——历班
  记录缺全名致复查 404，搜索一次勘定（10-06 勘定纪律第 3 例）。| 方法·勘定

### 批6 域结论与复查

- **批6 域（框架/平台/SDK）稳定期延续，零机制级新差量**：MAF（microsoft/
  agent-framework 13,997★ 复认）/Aegra 1,242/pandaprobe 785/agentscope-
  runtime 876/CrewAI-Studio 1,357 全已录复认；B6 词头部停更旧件占比高
  （autogenstudio-skills 25-01/evo-ai 25-06/PydanticAI 教程 25-11 系）。
  | 批6 域结论 | 2026-10-08
- **复查记录（vs 03 时班，间隔约 3h）**：存量头部 410 仓全 alive 零
  archived：superpowers 296,375（+35）/mattpocock-skills 279,503（+152 续
  放量）/ECC 274,904（+94）/hermes-agent 251,926（+28）/deepseek-harness
  245,153（+65）/opencode 212,200（+10）/ponytail 157,546（+70）/
  **orca 87,093（+47 续领跑）**/pi 113,178/dify 158,039；E 候选
  DeepSeek-Reasonix 35,749（+2 首位维持）；StaffDeck 1,969（±2 抖动常态）/
  nimbalyst 1,849 持平/story-skills 277 持平/amux 521 持平/strands-agents
  8,727（+5）/autoharness 9,179（+2）。| 复查 | 2026-10-08
- **全类型 14 名称对账核清**：任务书言「13 种」列 14 名称 vs 注册表
  BUILTIN_FLOWS=18——13 独立流程在册+禅道工单（非独立流程，zentao.py
  自动转 code 任务，README:242 口径）+5 种补充在册（doc/defect_retro/
  presentation/resume/bid_doc）=18 对账吻合零删零改；i18n 54 串
  missing=0 本班脚本实测（键=中文串 t() 直译形态，非 flow_<id>_<suf>
  键名——历班脚本口径细节补记）。| 巡检·C | 2026-10-08

### 七专项快照（06 时班）

- A 八锚点零漂移（pipeline:613/:648/:677 花费闸先于 token 闸/:2538、
  step_runner:28、skills:676）；B 六源在位零新接入（agno=框架非包/
  screenpipe=域旁/adk 清单=非包，三问全不过）；C 18 流程+i18n 54 串+推荐
  规则全对齐；D lessons=70 基线零漂移（流程规范 30=43%）；E catalog=14
  本机 13/14 在装（openclaw 缺）、候选五 CLI 全 MISSING 零盲接；F
  poll_enabled=false+claims={} 零积压+last_scan 停 09-21（18 天未扫）
  交拍板维持+产品档案 1 条在位、禅道竞品第 32 例复认零新独占维持；
  G 过时文案零命中、657dc13 落地件在位实读确认零新毛病。
  | 巡检·A/B/C/D/E/F/G | 2026-10-08

### 待深挖队列（06 时快照）与未验证项

- 04 时快照 11 项维持零新队列项；交拍板 4 件维持（禅道轮询重开/cost-xray
  观测粒度/release_gate 判定面/npm 包体 files 白名单）。| 队列 | 2026-10-08
- 未验证项维持（禅道回写链路 poll 关闭态不触发/pypi 仅在架验证）；
  trendshift flight 提取法第 3 班 49 仓全量成功（置信度再升高）；
  **Agno 词形盲区不新增词组**（WebSearch 交叉验证通道已兜住，防词库膨胀，
  如实记档）。keywords.md 本班零调整。| 未验证 | 2026-10-08

## 2026-10-08 06 时班续（新一轮第 2/4 步·七专项深化实证+落地件 1 件）

- **落地件：rank_scan/defect_retro 推荐面补齐（4 文件）**：app.js rules 表
  此前 16 条缺两型——direct 档输入「扫榜看热门题材」「复盘这些bug」不弹
  切换建议，四平台抓榜/CSV 三视角专用管线被整个旁路（模型凭空答榜单）。
  补 defect_retro 规则置 code 前（「复盘」须与 bug/缺陷 共现或命中「漏测」，
  纯 debug 仍归 code——防截胡与防误荐双向锚定）+ rank_scan 末位兜底（词形
  对齐 goal_hint「热门题材」）；建议弹窗文案按目标 engine 分流（快档目标
  如实说「专用数据链路与产出模板」，不再虚标「质量门禁」）；
  test_recommend_rules `_RULELESS` 收敛为 {direct}（v0.1.93「快档三件豁免」
  中仅 direct 豁免理由成立——推荐逻辑只在 direct 档触发无需自荐；两型豁免
  使专用链路永久旁路）。direct 之外 17/17 全覆盖。| 落地·C/G | 2026-10-08
- **七专项深化实证零新缺口**（除上述 C 项）：A 八锚点行号级实读+四对标
  方向全判定为既有能力（**廉价模型分流=dispatch TYPE_DIMENSIONS 18 型分维
  +_TIER_SCORE 难度×价位分层，历班已在位，不得再当新功能**）；B 六源+
  SSRF 逐跳复检+纯技能白名单实读，候选三问全不过零接入（API 集成件与
  纯技能件甄别线=inspect_tree 结构化，无需蒸馏）；D lessons=70 基线零
  漂移；E catalog=14 本机 13/14（候选五 CLI 重测全 MISSING）；F 禅道
  关闭态零积压零擅动（回写链路 _ensure_resolved/:1708+_transfer 空靶先
  GET 防清指派 :1729 实读在位）；G 预算设置路径实链闭环。| 巡检·A-G | 2026-10-08

## 2026-10-08 08 时班（新一轮计划第 1/4 步·批1：代码质量与评审）

> 主扫 127 查询（A 常驻 89+B1 11+双轮 16+**B1u 新锐轮 11**）576 行
> 450 唯一仓（295 已录/155 首见）零失败零限流；雷达 C 全过（awesome
> 19/19 alive/trendshift href 法 29 仓/topic 8 页/npm 三查/pypi）；WebSearch
> 串行 2 发零产出（通道噪声，如实记不宣称）。工作区 06 时班在制品
> （4 改 2 新）零踩踏承接。报告底稿=full-type-review.md 08 时班节。

### 本班新面孔与信号

- **kenryu42/cc-safety-net 1,582★**（25-12-25 建、10-08 仍 push、MIT、
  中英日三语）新入库 | AI coding agent **预执行守卫**：CLI 工具调用运行
  前拦截破坏性 git/文件系统命令+敏感文件访问，多 CLI 适配（Amp/
  Antigravity/Claude Code 等）| **runner 层安全差量**：我们有审批闸
  （auto_submit=false）+沙箱，无「CLI 内部工具调用级」破坏命令拦截网
  ——hook/permission-deny 形态可嫁接 CLI 启动配置 | **借鉴方向（待深挖
  +1，交第 2/4 步评审）**；非 skill 包不进市场 | 2026-10-08
- **A1u 新锐轮直接产出实证**：cc-safety-net 系 updated 排序捞出，
  stars 轮 5/页剪切线从未命中——双轮排序纪律价值再证（谁/何时：
  08 时班批1）。
- **微型/对标判据 5 件**（零接入全雷达）：alamops/agetor 88★（A1 同形
  local-first 多 CLI 编排看板，微型新锐）/Osmantic/ODS 7,125★（私有
  AI 服务器平台 V3 预发布，批7 域旁对标）/macro-inc/macro 4,589★
  （团队工作台+共享 AI 记忆，企业协作域）/yangheng95/opencorvus 312★
  （长程 agent 团队 harness 微型）/framerslab/agentos 677★（TS 框架
  B6 域小份额）。| 2026-10-08
- **放量信号**：SkillSpector 19,616→**19,645（+29/2h 放量续）**；
  bifrost 8,558→**8,613（+55/3 天）**；busbar 171→175；addyosmani/
  agent-skills 102,753→102,795（**+42/2h 放量明显放缓**，前两日曾
  +1,087）；OrchestratorInc/agent-orchestrator 12,857→12,875。
  | 复查·放量 | 2026-10-08

### 批1 域结论与复查（vs 06 时班，间隔约 2h）

- **批1 域（代码质量与评审）稳定期延续零机制级新差量**：SkillSpector
  19,645/vuls 12,281/AI-Infra-Guard 6,772/claude-code-security-review
  6,318/mira 358/pr-af 647 全已录族复认（_review_depth_note pr-af 借鉴
  已落地在位 :1084/:1089）；regression/api-test 两词维持空赛道。
  | 批1 域结论 | 2026-10-08
- **复查记录**：存量头部 450 仓全 alive 零 archived：superpowers 296,387
  （+12）/mattpocock-skills 279,561（+58）/ECC 274,928（+24）/hermes
  251,943/deepseek-harness 245,167/opencode 212,205/ponytail 157,578
  （+32）/**orca 87,111（+18）**/pi 113,185；E 候选 DeepSeek-Reasonix
  35,749 持平；StaffDeck 1,969 持平/amux 521 持平；禅道 npm 通道第 33 例
  复认零新（自动修复集成面独占维持）。| 复查 | 2026-10-08
- **七专项快照（08 时班）**：A 六锚点行号零漂移（pipeline:613/:648/:677/
  :2538、step_runner:28、skills:676）；B 六源在位候选全非包零接入；C
  18 流程 import 实数+54 串 missing=0+06 时班落地件在位；D lessons=70
  零漂移；E catalog=14+13/14 在装+候选五 CLI 全 MISSING；F 禅道
  poll off 零积压交拍板维持；G 过时文案零命中零新毛病。
  | 巡检·A-G | 2026-10-08
- **待深挖队列 +1=12 项**（cc-safety-net 预执行守卫嫁接可行性）；交拍板
  5 件维持（禅道轮询重开/cost-xray/release_gate 判定面/npm files 白名单/
  单进程 discover 静退根因）；keywords.md 本班零调整（既有词组捞出，
  防膨胀维持）。| 队列 | 2026-10-08

## 2026-10-08 08 时班续（新一轮计划第 2/4 步：七专项巡检深化+落地件）

> 承接批1 调研底稿；A-G 全落行号/本机实测（证据=full-type-review.md 第
> 2/4 步节）。落地件 1 件 + 交确认新增 1 件。

### 落地件：goal_hint↔推荐规则对齐（同型缝隙第 3 例）

- **缝隙**：recommendTaskType 规则词与 flows goal_hint 引导词失同步——
  presentation「演示场合」/serial_novel 例句「都市女频…可签约平台」/
  article「头条、知乎」/research 裸「调研」均不在词表，照占位提示输入
  direct 档不弹建议、旁路流程门禁。前两例：video_script 补词（v0.1.95）、
  rank_scan/defect_retro 补规则（06 时班）。**方法论**：巡检 C 应把
  「goal_hint 逐词投规则表」列为常驻对账项（三例同源，非偶发）。
  | 已落地 | 2026-10-08
- 落地面：app.js 规则表 4 词组+test_recommend_rules 6 样例；全量逐模块
  270/270 绿。已知不可修清如实记：email/novel hint 无特征词（固有边界）。
  | 落地 | 2026-10-08

### cc-safety-net 借鉴评审结论（第 2/4 步交确认）

- kenryu42/cc-safety-net 预执行守卫：落地形态=claude CLI 启动挂
  permission-deny 清单拦破坏性命令。**评审结论：不改码交确认**——
  runner.py:1456-1462 默认 `--dangerously-skip-permissions` 系 2026-09-17
  用户拍板（acceptEdits 曾致无人值守谎报受限躺平），权限默认值变更必须
  人批；若批，denylist 形态（只拦 `rm -rf /` 级破坏命令、不拦普通 Bash）
  与当年 acceptEdits 全拦 Bash 不同，误伤面可控。| 交确认·1 件 | 2026-10-08

### 七专项快照（08 时班续）

- A 八锚点+六辅证全在位（pipeline:61/:613/:648/:677/:703/:948/:1019/
  :2538/:2581、step_runner:28、token_meter:101/:129、capability:100、
  skills:676/:412/:516、sessions 五源、dispatch:12/:33/:44）；四对标方向
  全既有能力零新差量。| 巡检·A | 2026-10-08
- B 六源缓存 10-03 快照全在+SSRF（:113 assert_public_url+:159 逐跳）+
  体量六帽（:93-99）+inspect_tree 白名单（:568）三层闸实读；候选全非包
  零接入。| 巡检·B | 2026-10-08
- C 18 流程/18 dims/54 串 missing=0；D lessons=70 零漂移（流程规范 43%
  偏科维持）；E catalog=14 本机 13/14（候选五 CLI 全 MISSING 维持）；
  F 禅道 poll off+claims={} 零积压+last_scan 停 09-21（关闭态非故障，
  未触生产动作）；G 过时文案零命中。| 巡检·C-G | 2026-10-08
- 队列变动：待深挖 cc-safety-net 1 项评审完毕转**交确认**；交拍板 5 件
  维持。| 队列 | 2026-10-08
- **证据闭环+全项独立复验**：full-type-review.md 第 2/4 步节此前缺失
  （上节所引），本班补全——A-G 行号级证据（A 四对标方向全判既有能力：
  廉价分流=dispatch 三表+cascade_reorder opt-in；B 三层闸实读；C 54 串
  missing=0 本班脚本；D lessons=70 独立统计；E which 13/14 独立探测；
  F 调度链 automation:581→fire_due:2470→回写 :1708/:1729 全实读；G
  过时文案独立 grep 零命中）。落地件复验：test_recommend_rules 3/3+
  test_full_type_improvements 4/4+邻接守卫全 OK（独立重跑）；py_compile
  ×2+node --check×2 OK。交拍板 5→**6 件**（+cc-safety-net denylist 嫁接：
  批准后 runner.py:1467 ant 注入点扩 permissions.deny，opt-in 默认零
  变化）。| 补全·复验 | 2026-10-08

## 2026-10-08 10 时班沉淀（新一轮（v0.1.95 后）计划第 1/4 步·批3：计划/spec/长任务·全类型调研）

> 开工 10:00（hour=10，10%7=3 → 批3）；分支 main（f16e4d1，v0.1.95 已发）。
> 工作区遗留 08 时班落地件未提交（第 2/4 步在制品），本班只调研+文档不碰代码。
> 主扫 116 查询（A89+B3 11+双轮 16）524 行 466 唯一仓零失败（PROGRESS DONE 116）；
> 过筛 335 已录/131 首见（噪声为主）；WebSearch 串行 1 发零新 claim；awesome 20 源
> 20/20 alive（2 件属主勘定）；trendshift/topic 8 页/npm 双查全通道过。 | 元信息 | 2026-10-08

- **docker/docker-agent 3,748★**（created 25-09，pushed 10-07，trendshift 捞出+repos
  坐实）：Docker 官方 AI Agent Builder and Runtime。A1 域大厂官方编排件（builder
  面+runtime 面与桌面编排部分重合）。 | A1 雷达跟踪 | 2026-10-08
- **tingly-dev/tingly-box 351★**（pushed 当日）：「Your Intelligence, Orchestrated」
  多 agent 聚合编排新锐。 | A1 雷达跟踪 | 2026-10-08
- **openqodex/openqodex 276★**（created 10-02 五天新锐）：push 前 Claude Code/Codex
  AI 代码评审+SAST/secrets 依赖扫描。我们评审闸已有（diff-only+ _review_depth），
  「push 前门禁」形态判据级。 | B1 雷达跟踪 | 2026-10-08
- **rrahimi-uci/guarded-agentic-compaction 1★**：研究库+论文「Compile the routine,
  refuse the uncertain」——guarded agentic compaction，与 A3 三段压缩同域学术前沿
  信号（例行路径编译+不确定拒绝）。方法论参考非装件。 | A3 参考 | 2026-10-08
- **Azure-Samples/aspire-semantic-kernel-creative-writer 66★**：微软官方 creative
  writing multi-agent sample（Aspire+SK）。A7 微型判据。 | A7 微型 | 2026-10-08

### 批3 域结论与复查（vs 03 时班同域，间隔约 7h）

- **稳定期延续零机制级新差量**：OpenSpec 71,262→71,276（+14）/planning-with-files
  27,321→27,323（+2）/PlanWeave 411→412（+1 平稳）/PraisonAI 9,199/spec-workflow-mcp
  4,302 全已录族领跑；WebSearch B3 域三源（Spec Kit 四相/Kilo SDD 六相/DeepLearning
  课件）全已录族，truefoundry「Governing Specs」（spec 舰队规模三件套：版本/属主/
  门禁）归档型参考——.codebee 档案已有 stopgate+归档戳，不进路线图。 | 批3 结论
  | 2026-10-08
- **复查记录**：orca 87,177（+66 续领跑）/superpowers 296,408（+21）/mattpocock-skills
  279,702（+141 续放量）/ponytail 157,654（+76）/oh-my-claudecode 39,660/StaffDeck
  1,970/amux 521 持平/DeepSeek-Reasonix 35,748（±1，E 候选首位维持、agent-framework
  topic 页第 3 班连见）；全 alive 零 archived。awesome 20 源 20/20 alive：davepoon/
  buildwithclaude 3,604★、bmad-code-org/BMAD-METHOD 53,909★ 属主勘定补注 keywords.md
  （in:name 一次勘定零多余配额）；VoltAgent-skills 35,341（+20）/TeleAI-UAGI 659
  当日在更。trendshift 29 仓：storytold 第 8 兄弟 cadcraft（域旁）；禅道 npm 第 33
  例零新。pypi 通道间隔 7h 未跑、下批5 轮换班补（如实记）。 | 复查 | 2026-10-08
- **七专项快照（10 时班）**：A 八锚点零漂移（08 时班续行号级实证刚过+本班零代码
  改动）；B 六源在位、新面孔 5 件三问全判不接（平台级整仓/论文/微型 demo）；C
  BUILTIN_FLOWS=18 本班实读再确认（需求 14 项全含，另 doc/presentation/resume/
  bid_doc 4 条）；D lessons=70 基线维持；E catalog=14+13/14 在装+候选五 CLI 全
  MISSING 零盲接；F 禅道 poll off 零积压+产品档案路由 18 流程无变化；G 零新毛病。
  | 巡检·A-G | 2026-10-08
- **方法沉淀**：topic 页 curl 直抓提取模式修正——旧 `href="/owner/repo/stargazers"`
  后缀模式已失效，改 `href="/owner/repo"` 裸链接+排除前缀（topics/sponsors/features
  等）抓通（10-08 10 时班实测）；trendshift Next.js flight payload full_name 法两班
  连用稳定。multi-agent-orchestration topic 页两次抓空（间歇反爬如实记）；
  ai-coding-assistant 页与 llm-agents 页同缓存返回（词级粒度损失如实记）。
  | 方法·雷达 | 2026-10-08
- **待深挖队列 12 项维持零新**；交拍板 6 件维持。keywords.md 调整 1 处（C 源两件
  属主勘定补注）。| 队列 | 2026-10-08

### 10:3x 续班补强（真实性抽查+topic 页翻案）

- **multi-agent-orchestration topic 页翻案**：底稿两次抓空系间歇反爬，隔约 25
  分钟重抓成功 20 件——**方法沉淀：topic 页抓空应隔时重试而非弃抓**（间歇反爬
  ≠ 永久拦截，10-08 10 时班续班实证第 1 例）。过筛 6 已录复认/14 首见。
- **topic 页翻案新入库 4 件**（repos 端点逐一实证）：**Fmarzochi/EGC 63★**
  pushed 当日——「给每个 AI coding agent 同一个大脑」跨 agent 共享记忆层，与
  knowledge.md 经验库+agent_context 同域（A2/批2），微型判据、方法论面待深挖；
  **eggai-tech/EggAI 56★** async-first 企业级多 agent meta framework（A1）；
  **AutomatosAI/automatos-ai 48★**「AI OS—workforce of AI」（StaffDeck/
  openworker 同形态微型件，A2）；**komluk/scaffolding 15★** spec-driven CC
  编排插件（批3 本班域微型判据）。其余 10 件 0-9★ 判据线下。
- **pypi 通道补试**：pypi.org/search curl 反爬零输出（JS 渲染），维持 03 时班
  同域零新（间隔 7h），如实记。
- **底稿真实性抽查 3/3 过**：docker-agent 3,748→3,752/openqodex 276→277/
  tingly-box 351 持平，自然增量同源可信。flows.py 18 条独立实读复认零漏项。
  | 补强·复查 | 2026-10-08

## 2026-10-08 10 时班续沉淀（新一轮第 2/4 步·七专项巡检深化·零代码改动班）

- **七专项巡检结论（A-G 实读级，全程零代码改动、零触发真实禅道动作）**：
  A 八锚点全在位+四对标（prompt 缓存=会话复用前缀缓存已有/语义缓存无→待深挖
  收益窄不立项/diff-only 评审已覆盖且更优——CODE_REVIEW_PROMPT 主依据即 diff
  pipeline:919/廉价分流=cascade 已有）零新差量；B 六源在位+缓存 810 条+闸门齐
  （SSRF 逐跳重过闸/白名单拒装/sha256 分级）+已装 20 包 20/20 在位+新面孔 5 件
  三问全判不接；C 18 型三处（flows/i18n/UI 菜单动态渲染）全对齐需修：无；
  D lessons=70 零重复标题+抽样 40 条零误分（43% 流程规范偏科系来源结构非分类器
  故障）；E catalog=14、本机 13/14（openclaw MISSING）、候选五 CLI 全 MISSING
  零盲接维持；F 调度链在位（automation:580 fire_due+main:3446 start）、poll off
  claims=0 零积压、回写链路齐（resolve 幂等+失败不评论只回炉）、本班全程只读；
  G **2 处确定要修**：index.html:13 iOS 状态栏 meta 畸形（缺 content= 整条失效）+
  README.md:26-30 块外残留 v0.1.65 旧更新段，另存疑 2 处（帮助章来源枚举子集/
  TUTTI_* 前缀 env 名暴露）交拍板。 | 巡检·A-G | 2026-10-08
- **落地提案 2 件（第 3/4 步实施件，证据均亲读复核）**：G-1 修 index.html:13
  meta 补 content 属性名；G-2 删 README 块外 v0.1.65 残留段。均纯文案/标记层，
  无逻辑/数据/并发面。| 提案 | 2026-10-08
- **方法沉淀**：data 只读巡检三件套定型——lessons 统计（Counter 按 category/scope+
  标题归一化查重）、market 在位性对账（market.json↔skillpacks 逐包）、禅道三看
  （poll_enabled/claims/last_error），三件全零网络零副作用，可作每班第 2/4 步基线
  动作。| 方法·巡检 | 2026-10-08
- **待深挖队列**：+2 新项（语义缓存收益评估观察项；章末钩子族 4 条经验语义合并
  评估）——共 14 项；交拍板 8 件（原 6 件+帮助章来源枚举对账+TUTTI_* 前缀产品化
  改名）。| 队列 | 2026-10-08

## 2026-10-08 13 时班补充：B6 框架、平台与 SDK

- B6 10 组完成，50 条结果，零限流/错误。`aegra/aegra` 1,246★、Apache-2.0，
  自托管 LangGraph 替代；其公开 GHSA-q494-8v3j-cp9j（跨用户 Store 读取）和
  GHSA-m98r-6667-4wq7（线程 IDOR）说明当前不宜接入。来源：
  https://github.com/aegra/aegra
- `strands-agents/harness-sdk`（8,737★）与 `google/agents-cli`（6,065★）是完整
  SDK/CLI 生态，非六源技能包，未安装探测，不接 catalog。来源：
  https://github.com/strands-agents/harness-sdk 、 https://github.com/google/agents-cli
- 七专项只读复核：18 流程、14 CLI；近期回归 7/7；JS/Python 语法检查通过。既有
  `test_novel_signing`、`test_quality_gates` 失败与全量 discover 卡住继续列为阻塞。
  无产品代码、无版本变更、无发布，关键词策略不变。提交后两次推送均因
  `github.com:443` 网络连接失败，远端未更新；rebase 后 `python -m unittest discover
  -s tests` 在 120 秒受控窗口内超时并输出既有失败标记。

## 2026-10-09 03时班（新一轮（v0.1.95 后）计划第 1/4 步·批3 复认+B4 补课·全类型调研+七专项）

- **主扫+补课双跑**：116 查询（自动选批 B3，第 3 遍复认）+ `--batch 4` 补课 10 查询
  （B4 首遍增量覆盖——同钟点 24h 周期撞同 %7 批，按「轮着跑不同批」+10-07
  「当日未跑批」先例顺延）= **126 查询 574 行零 FAIL 零 403 零时限杀**；三源汇总
  （主扫+B4+topic 8 页 155 仓）508 唯一仓 = 369 已录/139 首见全噪声，判据线以上
  零新——域稳定期延续。| 调研 | 2026-10-09
- **HarnessRouter/harnessrouter**（2,931★，10-08 push）新入库 | 自托管 harness
  统一界面 CE（Apache-2.0，Codex/Claude Code 等，桌面+Web）| A13 同形态第 4 件
  （OrchestratorInc/agent-orchestrator 12,924/awslabs/cli-agent-orchestrator
  1,399/mixpeek/amux 520 同族）：统一面板我们 workbench+catalog 14 CLI 已覆盖，
  无机制差量 | 参考（A13 雷达盯增量）| 2026-10-09
- **微型判据 5 件**：this-rs/project-orchestrator 140★（Rust+Neo4j KG，知识库
  边界不接）/Neko-Catpital-Labs/Invoker 18★（DAG+worktrees+merge gates，机制
  全已有）/assistant-ui/jevia 10★（deterministic cache 模型路由——A3 语义缓存
  再添一票，拍板件维持）/cooragent/ClarityFinance 62★（金融域旁）/
  coreyhaines31/marketingskills 53,735★（营销技能包，不在六源域旁不接——
  大体量但三问不过）。B4「安全边界微型件成簇」趋势第 4 班延续（WebSearch 1 发
  B4 定向 7 claim repos 二次实证：agentgate 45/openguardrails 53/两 governance
  清单 56/50/guardrails 族 1/1/0 全微型；agent-governance-toolkit 6,412 复认）。
  | 新面孔 | 2026-10-09
- **复查增量**（43 跟踪仓+5 类型锚点 repos 端点零 archived）：orca 87,845
  （+799 加速续领跑）/ponytail 158,299（+823）/ECC 275,262（+452）/
  deepseek-harness 245,653（+519）/**open-design 100,026 破 10 万**/autoharness
  9,591（+414）/agent-memory 2,646（+266）/OpenShell 15,458（+256）/
  **cost-xray 4,057（+223 放量）**/magpie 6,724（+1,668）/claude-mem 98,320
  （+2,578）/**yomiyasu 1,762（+457，翻译腔件放量——翻译起草侧自查条已内化，
  后续盯其新机制）**/archify 79,881（+1,574）/OmniRoute 74,244（+1,707）/
  openhuman 41,678（+624）/career-ops 73,813（+103）/harness-sdk 8,743（+21
  减速）；E 候选 DeepSeek-Reasonix 35,748（+1 平稳）；波动注：awesome-llm-apps
  140,807（-124 回落非信号）。| 复查 | 2026-10-09
- **七专项 A-G（只读零代码）**：八机制锚点在位（行号漂移 710/745/2689 系上轮
  落地件合入，机制零漂移）；市场六源在位零新接入；18 类型零漂移 README:126
  口径一致；lessons=70 基线维持（43% 流程规范）；catalog 14、本机 13/14
  （openclaw 缺）、候选五 CLI 全 MISSING 零盲接；禅道 config.poll_enabled=false
  零积压 last_scan 停 09-21（关闭态非故障，轮询重开交拍板维持；10-09 03时班
  「顶层 poll_enabled」记法系层级口径差，实义同）；G 零毛病。禅道 npm 通道
  第 32 例零新。| 巡检·A-G | 2026-10-09
- **方法沉淀**：同钟点重复班次批次形态定型——「规则批照跑+`--batch N` 顺延补课
  双跑」，严格规则与增量覆盖兼得（本班 B3+116 与 B4+10 实证约 10 分钟跑完）；
  keywords.md 补注留待下步班同步（本班不越锚定文件清单）。| 方法·调研 | 2026-10-09

## 2026-10-09 04时班（新一轮计划第 2/4 步·七专项 A-G 巡检+落地）

- **G 项真缺陷修复**：test_i18n_dups 在 main 红灯约 36h——b8c25e3（workbench 重建）
  向 i18n.js 二次添加既有键「（无输出）」（:1474 后值带前导空格静默覆盖 :37 原值），
  7 处 t() 调用点文案被换；本班实跑守卫抓到并删后值修复（3/3 复绿）。
  **教训已蒸馏入库**（lessons 70→71，流程规范）：守卫结论当班必实跑，不引用上班
  口头结论；大 UI 重构是重复键高发面，落键前先 grep 全文件。| 巡检·G+落地 | 2026-10-09
- **新勘注入不对称（提案交第 3 步评审）**：经验/知识库注入点全 3 处均在连载链
  （planner.make_serial_outline planner.py:591-592 / serial 起草 pipeline.py:3808-3830
  / serial 逐章评审 :3342-3349），review 引擎 13 类型起草与评审零注入，而
  skills.learn_async（pipeline.py:5953）从全部 run 收割——「全类型收割、单链注入」
  闭环半开；index.html:975 设置页文案与实现对 13 类型不符（补实现或改文案二选一，
  交评审拍板，不擅动）。| 巡检·D+提案 | 2026-10-09
- **A 项口径更新**：压缩默认已翻转——1c81362（10-08）把 orchestrator.compaction.enabled
  默认改 True（settings_schema.py:226，revision 闸迁移保留显式 opt-out :266-272），
  此前各班「默认关灰度」口径作废；代码断点续跑 9111a33（10-08）入库（run.impl_session
  继承+_handoff_brief 注入，test_code_resume 7 项）——token 节约面两件新锚。四对标
  方向判定维持零新建。| 巡检·A | 2026-10-09
- **七专项其余全绿**：B 六源缓存 810 逐源复算零漂移零新面孔零接入；C 18 类型 import
  实测+守卫 8/8+3/3；D lessons=71（流程规范 31/44%）零重复；E catalog 14、本机 13/14
  （openclaw 缺、trae-cli 本班确认 /c/Users/HP/.local/bin/trae-cli 在位）、候选六 CLI
  全 MISSING 零盲接；F config.poll_enabled=false 零积压零触发只读（明文密码/轮询重开
  维持交拍板）。| 巡检·B/C/D/E/F | 2026-10-09

## 2026-10-09 15时班（新一轮计划第 1/4 步·全类型调研批1+七专项巡检）

> 15:41 开工，hour=15，15%7=1 → **轮换批1（代码质量与评审）**。基线 v0.1.97
> （07 时班发版无报告沉淀，如实记）；工作区有并行代理在制品（publish/quality_gate/
> builtin_agent/modelhub/app.js/i18n.js 链路+tests/test_disabled_model_gate.py），
> 本班全程避开该清单文件，只读巡检+docs 沉淀。gh api/雷达 C/trendshift/topic/npm/
> pypi 全通道可用，主扫后台 116 查零失败零限流。

- **主扫**：116/116（A 常驻 89+批1 11+A1 双轮 16），422 唯一仓零 error 行；
  活跃候选（≥300★ 且 9 月后在更）146 件过全历史筛后**首见判据仅 1**：
  mateaix/mateclaw 1,149★（10-09 在更，2026-04 建）——「second brain」多 agent
  编排+MCP+Skills&Memory+多渠道，Spring AI Alibaba 系（Java 栈）：中文生态全功能
  形态，技术栈与 CodeBee（Python）不同，编排层无差量，参考判据（中文生态雷达）。
- **B1 批域（本班主扫域）结论：稳定期延续**——review/refactor/test 词头部多为
  停更旧件（villesau 2024 停/redesigned-pancake 2021 停）与教材库；活头仅
  SkillSpector（NVIDIA，19,738★ 已录复认续放量）+claude-code-security-review
  （anthropics 官方 6,324★ 已录）。WebSearch 串行 1 发交叉验证（code review 域
  定向）：open-code-review（alibaba，**44,721★ 10-08 在更**，17 文件在录复认）
  系**连字符词形盲区第 4 例旁证**——主扫「code+review+agent」词两班未命中它，
  「open-code-review」与「code review agent」词形正交（StaffDeck/magic/openworker
  同型第 4 例）；ReviewBench（review-bench/ReviewBench 39★，github.blog 提及的
  code review 评测基准）repos 实证微型——GitHub 官方下场做评审基准系 A6 域信号，
  微型判据跟踪。| 调研·批1
- **topic 8 页（160 仓）首见 3 件判据**：Gentleman-Programming/gentle-shell
  1,245★（Pi-native coding-agent harness「Organic Driven Development」——pi
  生态 harness，我们已接 pi，工具面非编排面，判据雷达）；agentic-os-org/ANOLISA
  660★（Agentic OS with runtime+spec，早期同形态，判据雷达）；LunarWerxs/AgentHydra
  43★（多 CLI 单 tab 会话聚合，与 coding_agent_session_search 同域，微型）。
  archify 80,549★/answer-me-with-html 2,395★/agent-swarm 872★/LoopTroop 160★/
  Xenon 53★ 全已录复认。| 调研·topic
- **trendshift 30 仓**：storytold 六兄弟/tigerless 三件/Compositor
  （robbietilton，14,078★，+3,044/日放量继续）/mattpocock-skills/
  knowledge-work-plugins/diagram-design 已录复认；新面孔勘定后 ARTEX
  （mhtsec，1,396★ AI 自主渗透测试，百度冠军）/iPhone-use（zhongerxin，978★
  Codex USB 控真实 iPhone）/ohmygame（WhiteTowerAI，261★ AI 游戏工作室）域旁
  判据；bifrost（maximhq 8,661★ AI 网关）/busbar（175★ 治理面）已录。| 调研·trendshift
- **禅道 npm 通道 4 件首见（F 专项生态变密信号）**：@staragent/zentao-mcp 1.0.8
  （10-08 在更成熟线）/@haoyu-qi/dsh-zentao 0.1.0-rc.8（**DSH 系禅道插件**——
  自家已接 CLI 的插件生态与禅道交叉首例）/@liwei19911215/zentao-mcp 1.0.1/
  @aipper/zentao-mcp-server 0.1.26——禅道 MCP 在架从上轮 3 件变 7+ 件密度上升，
  需求侧确认；形态多为 MCP 查询/工单读写，「激活 Bug→自动建 code 修复任务→
  合并+resolve+评论回写+群通知」深度修复闭环 CodeBee 仍独占。| 巡检·F
- **失配勘定 5 件属主（knowledge.md 全名补认）**：affaan-m/ECC 275,555/
  NousResearch/hermes-agent 252,116/DietrichGebert/ponytail 158,906/
  langgenius/dify 157,972/earendil-works/pi 113,653（in:name 一次勘清，
  10-06 首例方法第 2 班连用）。| 复查
- **存量头部增量（vs 00 时班）**：orca 88,161（+1,115 续领跑放量）/
  mattpocock-skills 281,612（+2,261 续放量）/superpowers 296,679（+339）/
  tigerless autoharness 10,461（+1,284 放量加速）/agent-memory 3,032（+652）/
  cost-xray 4,377（+543，代码 09-29 后未更纯外部关注放量）/openrig 6,249
  （+755）/OpenShell 15,530（+328）/agent-orchestrator 12,970（+113）/
  Compositor 14,078（+3,044）/opencode 212,265（**属主 anomalyco 补认**）/
  DeepSeek-Reasonix 35,754（+7 候选首位维持）/openworker 18,481（+19）/
  StaffDeck 1,973（+4）/harness-sdk 8,747（+25 续放量）。awesome 20 源全 alive
  零 archived（ComposioHQ 76,722/punkpeye 95,947/hesreallyhim 55,293/
  VoltAgent-skills 35,403/BMAD 53,968 等）。| 复查
- **七专项 A-G**：A 八机制在位（行号漂移更新：_budget_max_tokens:723/
  _cost_gate_block:758/_shrink_context_block:2702，系 05beb26 知识注入扩展+21 行
  所致；cached 细分记账 usage.py:53 在位）；05beb26「全类型知识注入」=经验召回
  覆盖面增强且零命中零噪音——A 无新差量。B 六源在位+体量五帽，判据件接入三问
  全不通过（mateclaw Java 栈/gentle-shell 工具面/ANOLISA 早期/AgentHydra 微型/
  禅道 npm 4 件 MCP 查询形态），**零新接入**。C BUILTIN_FLOWS=18（list 形态重构
  后 id 全对齐）+recommendTaskType 17 型全覆盖+_RULELESS={"direct"} 同步+i18n
  英文翻译 18×name/note 零缺失，**C 零毛病**。D lessons=71（流程规范 31/43.7%）
  +1 系 04 时班蒸馏件（守卫结论当班实跑），基线更新 71。E catalog 14、本机
  13/14（openclaw 缺）、候选五 CLI（DeepSeek-Reasonix/FuXi/Gitlawb/zero/Empryo）
  which 全 MISSING 零盲接维持。F poll_enabled=null+claims=0+last_error 空+
  last_scan 停 09-21——关闭态非故障，轮询重开交拍板维持；**竞品面第 32-35 例**
  （npm 4 件首见，见上）。G 过时文案零命中（「13 种任务/单源」grep 真实源码
  零命中，README「18 种」2 处准确）；**b8c25e3 4.9MB workbench-reference.png
  打进 npm 包（files='app' 未排除 assets），unpackedSize 6.3MB→14.2MB 实测
  翻倍**——运行时资产不擅动，归并「npm 包体 files 白名单」交拍板件更新数据。
  | 巡检·A/B/C/D/E/F/G
- **本班零代码提案（如实记）**：七专项无新缺口；路线图在册未落地件（spec 分段
  签核跨四层/语义缓存/宪章骨架）均非小而实维持不动；落地=知识沉淀三件套
  （knowledge.md+keywords.md 词形盲区补词+当日报告）。| 结论

## 2026-10-09 16时班（新一轮计划第 1/4 步·全类型调研批2+七专项巡检+落地两件）

> 16:04 开工，hour=16，16%7=2 → **轮换批2（学习记忆与自我改进）**。基线 v0.1.97，
> main 分支（76ec100），工作区干净（并行代理在制品已入库 ee0449c/d0fab28）。
> 通道全可用：主扫 115 查零失败零限流（PROGRESS DONE；单进程 discover 崩退在案
> 风险同型再现，逐模块净进程兜底）、trendshift 根页/topic 8 页/npm/pypi curl 全通、
> WebSearch 串行 1 发过。

- **主扫**：115/115（A 常驻 89+批2 10+A1 双轮 16），461 唯一仓、零 error 行；
  活跃候选（≥300★ 且 9 月后在更）162 件过全历史筛**首见 0 件**——主扫面域
  稳定期延续（与 15 时班「首见判据仅 1」同型）。B2 批域头部 10 件（codegraph
  73,554/scientific-agent-skills 48,101/agentic-awesome-skills 47,379/graphiti
  31,582/Auto-claude-code-research-in-sleep 17,161 等）**全已录复认零新增**——
  批2 域（self-improving/memory/skill 库）头部无新面孔，稳定期结论与 10-06
  批2 班同型。| 调研·批2
- **三交叉通道新面孔判据 7 件**：
  - **clacky-ai/openclacky 1,204★**（10-09 在更，trendshift 根页）——「最
    token 高效开源 AI Agent」A3 域直接对标：①Insert-then-Compress（压缩不
    变异系统提示→实测缓存命中近 100%）②16 工具+invoke_skill 元工具收敛
    （能力下沉 Skill 生态，schema 不膨胀）③闲时后台压缩+缓存预热（冷启动
    首 token 省 50%+）。对比：我们 stable_order 保前缀缓存系①同向互证；
    ②系 CLI agent 工具面问题（编排台形态不同非我们的问题面）；**③「闲时
    压缩+预热」系真实差量**——我们压缩系压力触发（compaction.py maybe_compact），
    闲时触发+预热需后台线程+空闲检测，非小而实，**记待深挖队列**。A3 高价值
    判据入库（keywords.md A3 组补词，剪切线盲区非词形盲区）。| 调研·A3
  - **ace-agent/ace 1,352★**（08-24 后停更，WebSearch 批2 域交叉验证捞出）
    ——ACE Agentic Context Engineering（context 当 evolving playbooks 自我
    改进）；主扫 B2 组 10 词全量未命中系**词形盲区第 5 例**（keywords.md
    批2 已补词）；Reflexion/Voyager/SEAL/altk-evolve（论文）经典或已录，
    批2 域零机制级新差量。| 调研·批2
  - **Albert-Weasker/niubigeo 5,529★**（10-08 在更，trendshift）——AI 品牌
    可见度+竞品报告（B5 competitive intelligence 域，扫榜/调研报告类型邻域）
    判据雷达。**edenfunf/reelmimic 1,832★/yi1108/printfilm 5,051★** 已录复认
    （视频域）。| 调研·trendshift
  - **peters/horizon 715★**（10-09 在更，topic）——GPU 加速终端板多 session
    无限画布（A13 面板域判据，泛词「horizon」16 处历史命中全为误撞、本件系
    首见）；**JKHeadley/instar 81★**（持久 Claude Code agents：定时+会话+
    记忆+Telegram，A9+A11 域）；**futuregene/future-os 111★**（one agent
    everywhere，A1 同形态）；**responsibleai/agent-hooks 19★**（框架无关
    控制契约八拦截点+三裁决，B4 治理域）；**FTShare-Lab/agent-claim-network
    44★**（可追溯知识共享+争议解决，A11 协作域）；**xiaohuiyan/
    awesome-experience-driven-agents**（经验驱动 agent 论文域图，批2 域专属
    清单新锐）。| 调研·topic
- **雷达 C**：awesome 16 源抽查全 alive 零 archived（ComposioHQ 76,724 +2/
  punkpeye 95,947/hesreallyhim 55,294/bradAGI 1,330 +9/ai-boost 4,763）；
  trendshift 根页 49 仓提取（已录复认 13+新面孔勘定 17：判据 5+域旁 12）；
  topic 8 页 64 仓（updated 排序，已录复认为主）；npm 禅道通道第 4 班连用
  三件全复认零新（dsh-plugin-zentao 0.1.17/zentao-cli 0.3.1/zentao-api 0.7.2，
  F 专项第 36 班口径零新）；pypi HTTP 200（第 19 班）。| 雷达·C
- **存量头部增量（vs 15 时班，间隔约 40 分钟微增）**：mattpocock-skills
  281,653（+41）/ECC 275,562（+7）/hermes-agent 252,122（+6）/opencode
  212,267（+2）/ponytail 158,927（+21）/orca 88,174（+13）/pi 113,661（+8）/
  dify 157,972 持平——平稳零放量信号。| 复查
- **七专项 A-G**：A 八机制锚点在位（_budget_max_tokens:723/_cost_gate_block:758/
  _shrink_context_block:2702 与 15 时班行号一致零漂移；compaction 默认 True
  settings_schema.py:226 在位）；openclacky ③系本班唯一 A3 新差量（记待深挖
  不落地）。B 六源在位（market_remote.py:50）零新接入（openclacky 系完整
  CLI agent 非六源包/ace 停更/其余判据件微型或域旁——三问全不过）。C
  BUILTIN_FLOWS=18 零漂移。D lessons=71（流程规范 31/43.7%）基线一致零新增
  （ace/openclacky 方法论归调研判据非 lessons 注入面）。E catalog 14、本机
  13/14（openclaw 缺）、候选五 CLI which 全 MISSING 零盲接维持。F
  config.poll_enabled=false+claims=0+last_error 空+last_scan 停 09-21——关闭
  态非故障，轮询重开交拍板维持；竞品面第 36 班零新。G 过时文案（13 种任务/
  单源/双源）grep 真实源码零命中；**发现 2 件如实记并当班修**（见落地件）。| 巡检·A/B/C/D/E/F/G
- **落地两件**（G 专项顺手修+守卫红灯当班修，04 时班「守卫结论当班必实跑」
  教训践行）：
  1. **经验库设置页文案与实现对齐**：04 时班勘定「注入不对称」后实现侧已补全
     （pipeline.py:5062 起草链+5171 评审链，注释「review 引擎 13 类型补全」），
     但 index.html:960 hint 仍写「自动注入小说类任务」——文案落后于实现；
     改「同类任务」+i18n.js:1411 键同步（novel tasks→matching tasks）。
  2. **修复既有守卫红灯**：test_borrow_iteration 的 JS 字面量键对账测出
     f79ba7c「全部发草稿」11 键缺 i18n 词条（英文界面中文裸奔）——直发功能
     10 键+hook 命令示例 1 键全量补齐英文翻译；test_borrow_iteration.py
     追加 2 用例锁文案↔词条同步契约（11/11 定向绿）。| 落地
- **本班结论**：主扫/B2 域稳定期延续零机制级新差量；判据 7 件入库雷达
  （openclacky/ace/niubigeo/horizon/instar/future-os/agent-hooks 等）；待深挖
  +1（openclacky 闲时压缩+预热缓存）；keywords.md 补词 2 条（词形盲区第 5 例
  +A3 剪切线）；落地 2 件（文案对齐+守卫红灯修复）。| 结论

## 2026-10-09 18 时班（批4：治理/安全/人机协同）

- **microsoft/agent-governance-toolkit**（6,416★，10-09 当日在更，Public Preview）
  | 微软官方 agent 治理工具链：策略执行（哪些工具动作被允许）+ 零信任身份（多
  agent 区分「谁干的」）+ 防篡改审计（tamper-evident 每决策留痕）+ 执行沙箱 +
  可靠性工程，覆盖 OWASP Agentic Top 10（7 Full 3 Partial），PyPI/npm/NuGet 三
  渠 SDK | B4 治理域：三问自答——我们的命令闸策略感知+评审闸+evidence.md 档案
  覆盖其「动作允许/可证明」的单机版；差量在多 agent 身份与密码学级审计，均系
  企业合规场景，桌面单机形态重合度低 | 参考判据（雷达，盯增量） | 2026-10-09
- **maximhq/bifrost**（8,667★，10-09 在更）| 企业 AI 网关：自适应负载均衡+集群
  模式+guardrails+1000+ 模型，<100µs@5k RPS（claim 50x LiteLLM）| A8 网关域：
  与 one-api/axonhub 同域新贵，放量期；我们绑定链换将同构、网关形态非产品路线
  | 参考判据（雷达） | 2026-10-09
- **can1357/oh-my-pi**（34,736★，10-09 在更，全历史首见）| 「IDE 接进来的
  coding agent」，Stencil Labs 出品，pi 生态头部件 | A4/A13 域：pi 系我们已接
  CLI（catalog 在册），其生态最大头部件此前 115 组词+八班雷达从未命中——系
  「pi 生态专名词形」盲区（词根正交）；编排台差量无，生态健康度信号 | 参考
  判据（雷达） | 2026-10-09
- **GetBusbar/busbar**（175★，10-08 在更）| AI agent 执行控制平面：governance
  每一 model 请求/MCP tool call/A2A delegation/下游动作，六协议进六协议出 |
  B4 治理域微型件 | 微型判据（雷达） | 2026-10-09
- **nobodywho-ooo/nobodywho**（1,539★，10-09 在更）| 本地 LLM 推理引擎（任意
  设备）| 推理引擎域旁非编排 | 参考（域旁） | 2026-10-09
- **sattyamjjain/agent-audit-kit**（12★）| MCP 管线静态扫描器：388 规则 14 合规
  框架 OWASP Agentic 10/10 | B4 微型 | 微型判据（雷达） | 2026-10-09
- **B4 治理域成簇信号（第 4 班）**：微软 toolkit + busbar + agent-audit-kit
  同日现于三通道（WebSearch/trendshift/topic）——「agent 治理/控制平面/合规
  扫描」方向持续升温，此前 00-04 时班「安全边界微型件成簇」信号延续且升档
  （从微型 skill 到官方 toolkit 入场）。| 趋势信号
- **deepseek-harness 属主勘定（E+G 交叉）**：存量复查发现 PerryLink/
  deepseek-harness 已 archived 且仅 1★（旧址）；主扫自证真身=**deepseek-ai/
  deepseek-harness 246,077★**（官方 org，10-09 在更）——catalog install/upgrade
  走 npm 包名 @deepseek-ai/dsh 与官方一致，**零断链影响**；DSH 插件生态（48 件）
  归属待下轮顺 orgs/deepseek-ai/repos 复核。| 勘定
- **复查记录（18 时班，基准=15/16 时班）**：orca 88,246（+85）/mattpocock-skills
  281,857（+245）/ECC 275,633（+71）/hermes-agent 252,155（+33）/ponytail
  159,068（+120）/dify 157,984（+12）/pi 113,706（+45）/opencode 212,301
  （+34）/openworker 18,482（+20）/StaffDeck 1,973（+4）/DeepSeek-Reasonix
  35,754 持平（E 候选首位维持）；放量信号：tigerless 三件续放量（autoharness
  10,741 +280/agent-memory 3,387 +355/cost-xray 4,691 +314），strands
  harness-sdk 8,750 转平稳（+3）；已沉淀件增量：freebuff 13,376（+1,076）/
  agent-orchestrator 12,977（+877）/agentmemory 29,257（+657）/planning-with-
  files 27,353（+53）/openclacky 1,204 持平。| 复查
- **七专项 A-G**：A 八机制锚点在位（:725/:760/:2704/:28/:676/:1683，较 15 时班
  微漂 2 行系 v0.1.99 发版提交所致，机制零漂移）零新差量。B 六源在位零新接入
  （toolkit=SDK 非 skill 包/bifrost=网关非包/oh-my-pi=CLI agent 非包，三问全
  不过）。C BUILTIN_FLOWS=18 实测+recommendTaskType/i18n_dups 守卫 6/6 定向绿。
  D lessons=71 基线一致（流程规范 31/43.7%）零新增蒸馏（交叉验证纪律已在库且
  本班 WebSearch 捞出微软 toolkit 系该纪律第 N 次生效实证）。E catalog=14、
  本机 13/14 在装（openclaw 缺）、候选五 CLI（DeepSeek-Reasonix/FuXi/Gitlawb/
  zero/Empryo）which 全 MISSING 零盲接维持；deepseek-harness 属主勘定见上。
  F poll_enabled=null 关闭态非故障+claims={} 零积压+last_error 空；npm 禅道
  通道第 5 班三件全复认零新（第 36 例口径维持）。G 过时文案（13 种任务/单源/
  双源）grep 零命中；禅道子页文案与 zentao.py 实现一致；catalog 零断链；
  守卫测试全绿——零新毛病。| 巡检·A/B/C/D/E/F/G
- **本班结论**：主扫 115/115 零失败零限流（466 唯一仓/活跃 166 件/首见 0）
  稳定期延续；本班判据 5 件+微型 2 件全出自三交叉通道（WebSearch/trendshift/
  topic）——主扫通道首见 0 系「每 3-4 班交叉验证」纪律价值的实证；B4 治理域
  成簇升档为当班唯一趋势信号。零代码提案（七专项零新缺口、路线图在册件均非
  小而实），落地=知识沉淀三件套；当日 v0.1.99 已发（20b7a4e），docs-only 不
  触发新发版。| 结论
### 2026-10-09 19 时班批5复查（检索/知识/浏览器）

- B5 10 组/50 条结果零限流；Dify、RAGFlow、claude-mem、深研、BrowserSkill、
  Firecrawl、Agent-Reach、Composio、ADE CLI 等均为已知基线。BrowserSkill 的借还
  标签页、Agent-Reach 的多站点接入、ADE 的 schema 抽取没有形成当前产品可安全接入
  的小接口差量，候选均不接入。来源：`Tencent/BrowserSkill`、
  `Panniantong/Agent-Reach`、`landing-ai/ade-cli`、`firecrawl/firecrawl`。
- A-G 只读复核无新增缺口：18 流程、14 CLI；市场安全闸、禅道关闭态和 UI 文案
  基线均在位；全类型/注册表/市场回归分别 7、4、10、14 项通过。本班无代码落地，
  关键词策略不变。
### 2026-10-10 15 时班批1（代码质量与评审）——三交叉通道判据 10 件，主扫首见 0 连续第 3 班

- **主扫**：117/117 零失败零限流（421 唯一仓/活跃 233/首见 121 全微型或域外，
  高星 6 件全停更或域外）——批1 域主扫头部零机制级新差量（claude-code-security-
  review 6,329/SkillSpector 19,810 已录族复认；refactor/api-test/ast-edit 三词沉寂）。
- **判据件（全出自 trendshift/topic/WebSearch 交叉通道，主扫零贡献再证「每 3-4 班
  交叉验证」纪律）**：
  - **weave-os/router**（5,578★，10-10 在更，首见）| agentic model router：每
    prompt <50ms 路由，宣称省成本 40-70% | A3/A6：cascade+modelhub 系任务级
    换将，其系 prompt 级网关路由，粒度形态均不同；量化口径入 A3 对标参照 |
    A3 参考判据（雷达） | 2026-10-10
  - **yetone/magpie**（7,875★，10-10 在更，首见）| 菜单栏给各 CLI agent 换模型
    （Codex on DeepSeek 等）| A6/A13：catalog+modelhub「已接 CLI×可换模型」
    交叉矩阵同构佐证，单机菜单栏形态 | 参考判据（雷达） | 2026-10-10
  - **chaitin/MonkeyCode**（4,811★，10-10 在更，首见）| 长亭官方团队 AI coding
    platform | E/A4：中国安全大厂入场，平台形态不重合，中文生态判据补充 |
    参考（中文生态雷达） | 2026-10-10
  - **Albert-Weasker/niubigeo**（6,412★，10-10 在更，首见）| AI 品牌可见度+竞品
    监测报告自动生成 | B5：调研报告流程同向异面，竞品报告需求确认信号 |
    参考判据（雷达） | 2026-10-10
  - **kentcdodds/kody**（754★，10-10 在更，首见）| agent 云：记忆/密钥/代码/
    自动化跨 CLI 便携 | A11+A13：agent 资产云化便携，本地单产品档案形态不重合 |
    参考判据（雷达） | 2026-10-10
  - **zhongerxin/iPhone-use**（2,187★，10-10 在更，首见）| Codex 经 USB 操作
    真实 iPhone（App 自动化+截图回退）| A5 computer-use 新形态：真机自动化路线 |
    参考（域旁新形态） | 2026-10-10
  - **kodustech/kodus-ai**（1,459★，10-09 在更，WebSearch 捞出+repos 实证）|
    自托管开源 AI PR 评审 BYOLLM 五平台 | B1 本班最大新件：PR 场景我们无，
    本地 diff 评审闸已有；「评审规则配置化」口径参考 | B1 参考判据（雷达） |
    2026-10-10
  - **asheshgoplani/agent-deck**（1,051★）/ **peters/horizon**（716★）| A13 多 CLI
    面板域第 2/3 件首见（TUI 管理四 CLI / 无限画布会话板）| A13：该域成簇
    延续，蜂巢+catalog 系同域最深实现之一 | A13 参考判据（雷达） | 2026-10-10
  - **twostraws/SwiftUI-Agent-Skill**（5,594★）| Paul Hudson 框架专用 skill 大星化 |
    skill 生态：语言框架 skill 品类需求确认 | 参考（skill 生态） | 2026-10-10
  - **catlog22/maestro-flow**（565★，intent-driven 编排）/ **linny006 五件雷达套**
    （8-39★，recency 优先+15 分钟刷新 live index）/ 其余微型（polite-fetch 限流
    fetch、sycophancy-stack 反谄媚、plumbgraph 验证闸、another 跨 CLI 会话、
    Support-Forge cascade 拒答研究、ecommerce-skills 26 skill 分解、CopilotKit
    OpenIntelligentUI、mhtsec/ARTEX 渗透、iPhone-use 同批）| 微型判据群（雷达） |
    2026-10-10
- **趋势信号**：① A13 多 CLI 控制面成簇延续（agent-deck/horizon/chroxy/another/
  kody 当班五件）；② **模型路由域升温**（weave-os/router+magpie+Support-Forge
  当班三件，A3 下轮盯增量）；③ **DSH 生态官方化**：orgs/deepseek-ai/repos 顺藤
  复核闭环——deepseek-harness 246,624★ 官方在 org（246,077→246,620 口径差系
  时点）、**dsh-libreoffice-kit 168★ 官方收编首例**、awesome-deepseek-agent
  6,187★ 官方清单（已补入 C 雷达源）、第三方 Android 端 205★+社区 skill-center/
  token-billing 芯片——已接 CLI 中生态健康度最强，E 专项 DSH 系加分。
- **上轮遗留闭环**：microsoft/agent-governance-toolkit 6,415★ 实证（B4 簇主角
  属主勘定，10-09 在更）；planning-with-files 属主=OthmanAdi、magic-context
  属主=cortexkit、webnovel-writer 属主=lingfengQAQ、ainovel-cli 属主=voocel、
  claude-plugins-official 属主=anthropics（in:name 一次勘清 5 件）。
- **复查记录（vs 10-09 18 时班，间隔约 21h）**：orca 88,808（+562）/mattpocock-
  skills 283,277（+1,420）/ECC 276,128（+495）/ponytail 159,895（+827）/
  hermes-agent 252,357（+202）/pi 113,911（+205）/opencode 212,451（+150）/
  dify 158,055（+71）/BMAD 53,999（+90）；放量：**openrig 6,557（+1,063 病毒第
  4 班）**/OpenShell 15,644（+442）/open-code-review 45,561（+840）/autoharness
  10,928（+187）；平稳：eve 5,511/hive 11,084/oh-my-pi 34,844（+108）/freebuff
  13,401（+25）/agent-orchestrator 13,017（+40）/agentmemory 29,273（+16）/
  planning-with-files 27,367（+46）/magic-context 2,295（+20）/nimbalyst 1,862
  （+14）/minimax-code 2,007（+22）/webnovel-writer 7,407/ainovel-cli 2,134/
  DeepSeek-Reasonix 35,753 持平（E 候选首位维持）/ace 1,352 持平（停更维持）/
  openclacky 1,204 持平/busbar 176（+1）/claude-plugins-official 37,599（+101）/
  LLMLingua 6,742（+42）/StaffDeck 1,975/openworker 18,480/cost-xray 4,697
  持平。| 复查
- **B1 域 WebSearch 交叉验证 1 发**：kodus-ai 为最大新件（上），其余 CodeRabbit/
  Greptile/Cursor BugBot 商业件与 ReviewDog/Danger 静态工具复认——「激活 Bug→
  自动建任务→resolve+评论回写」深度闭环仍 CodeBee 独占，禅道链零威胁。
  | 交叉验证
- **本班结论**：判据 10 件+微型群全出自三交叉通道，主扫首见 0 连续第 3 班——
  域稳定期下新竞品只从交叉通道出；三大趋势（A13 控制面/模型路由/DSH 官方化）
  中无一件构成「对方有我们没有且小而实」的落地差量；零代码提案，落地=知识
  沉淀三件套，docs-only 不触发发版。| 结论
