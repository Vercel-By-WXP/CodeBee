# 2026-10-08 06 时班（批6：框架/平台/SDK 生态——新一轮计划第 1/4 步·全类型竞品调研+七专项巡检）

> 开工实录：06:40（UTC+8，hour=6，6%7=6 → 轮换批6）、分支 main（f16e4d1，
> v0.1.95 已发版）、工作区干净零在制品。通道：gh api 认证可用（search 30/分、
> sleep 4s 纪律内）、trendshift/pypi/npm/topic 页 curl 全可达、WebSearch 串行
> 1 发通过。证据底稿即本文件（本班新建，同日 00-05 时班已走完上一轮 1-4 步
> 并发版 v0.1.94/v0.1.95——本班重新起算新一轮）。本报告系计划锚定文件
> full-type-review.md，最终归档为执行当日（2026-10-08）报告。

## 主扫描与雷达 C 覆盖清单

- **主扫**：`scripts/borrow_scan_nightly.py` 无参全量（自动选批 B6）——A 常驻 89
  （含内置 B1 11）+ **B6 轮换 10** + A1u/A1p2 双轮 16 = **115 查询 454 行
  410 唯一仓，零失败零限流**（PROGRESS DONE 115 实证；后台时限按 10-09 报告
  教训给足 1800s，全程未被杀）。
- **主扫面过筛**（全名+简称双通道对 docs/borrow-log/ 全历史）：**288 已录 /
  122 首见**，首见以域外噪声与停更旧件为主（apache OptaPlanner 迁移通知仓
  3,515★/Coursera 课件库/FTC 机器人 SDK/2018-2025 停更教程仓等）；
  **首见判据件 4**：agno-agi/agno 42,603★（WebSearch 交叉验证捞出，见新面孔
  表）、screenpipe/screenpipe 21,853★（topic:mcp 页，见新面孔表）、
  Sri-Krishna-V/awesome-adk-agents 348★（ADK agents 清单，awesome 域微型
  判据，08-18 两个月未更——跟踪即可）、Nachx639/context-canary 0★（Claude
  Code 行为指示器玩具，微型判据）。
- **WebSearch 串行 1 发**（B6 域定向「open source AI agent framework platform
  SDK 2026 langgraph crewai autogen alternative new release」）：
  Microsoft Agent Framework（MAF，Semantic Kernel+AutoGen 合并后继，2026-04
  v1.0）——**repos 端点二次实证在位 13,997★** 且全历史已录（复认增量）；
  LangGraph/CrewAI/AutoGen/OpenAI Agents SDK/Google ADK/PydanticAI/Mastra
  全已录族复认；**新 claim 一件：Agno 42,603★（repos 坐实，见新面孔表）**。
- **雷达 C 全过**：
  - **awesome 20 源 repos 实测 20/20 alive 零 archived**（含 buildwithclaude
    本班 in:name 勘定正主=davepoon/buildwithclaude 3,604★ 10-06 在更，与
    03 时班持平）；头部增量（vs 03 时班，间隔约 3h）：ComposioHQ
    awesome-claude-skills 76,669（+7）/punkpeye awesome-mcp-servers 95,908
    （+2）/hesreallyhim awesome-claude-code 55,207（+3）/VoltAgent-skills
    35,335（+7）/awesome-llm-apps 140,937（+6）/awesome-ai-agents 30,294
    （+2）/ai-boost 4,743（+1）/bradAGI 1,322（+1）/papers 图 1,827（+1）；
    长程/记忆/spec/evolution 五域图与 caramaschiHG 1,928、vijaythecoder
    4,389 全在位；对标件 StaffDeck 1,969（±2 缓存抖动常态）。
  - **trendshift HTTP 200 344KB 49 仓**（flight payload `full_name` 提取法
    第 3 班复用，href 双通道 29 仓交叉）：已录复认 storytold 家族 7 兄弟/
    openai/math/morluto-rea/Compositor/dsh-our-free-model/autoharness/
    huashu-art-motion/mattpocock-skills/HowToLiveBetter 50,196（域旁生活
    指南）/knowledge-work-plugins/diagram-design/native-subtitle-quote-image；
    **判据级新面孔零**（最大增量=已录 addyosmani/agent-skills 102,753★，
    vs 10-06 入库 101,666 **+1,087 放量续**）；域旁排除 AnyPS5 10,308
    （PS5 移植）/openGym 6,825（健身 tracker）/jumper 1,393（机器人）等。
  - **topic 8 页全扫（updated 排序，每页 20 仓）**：160 仓过筛——常客复认
    oh-my-openagent 69,875（+3）/strands-agents harness-sdk 8,727（+5）/
    pacifio/atlas **9,269（+383 快涨续，09-30 入库 8,431 以来一月 +838）**/
    omnigent 10,653（已录复认）/nimbalyst 1,849 持平/mixpeek amux 521 持平；
    首见 screenpipe 21,853★（见新面孔表）；本机在案多 CLI 控制面同域常客
    Dicklesworthstone/ntm 454 复认（caam 同作者）。
  - **npm 三查**（agent orchestrator / claude code / zentao ai）：
    agent orchestrator 头部全已录族（@nathapp/nax 0.83.5/@polderlabs/bizar
    10.33.0/opencode-oceanus/coleo——bdb-agent-orchestrator 系已录
    Untrivial-ai 上游 fork，不另计）；claude code 查官方二进制包零判据；
    **禅道 npm 通道第 32 例复认零新**（@jw-king/dsh-plugin-zentao 0.1.17+
    zentao-cli 0.3.1 均历班已录，GitHub 侧零自动修复集成竞品维持独占）。
  - **pypi**：agent-orchestrator JSON API HTTP 200 在架（第 17 班）。
  - GitHub Trending 直抓未行（trendshift 补位，历班惯例）。

## B6 批域逐词头部（10 词，本班主扫域：框架/平台/SDK 生态）

| 查询词 | 头部命中（top1-3） | 判定 |
|---|---|---|
| langgraph+platform+OR+deploy | NirDiamant/agents-towards-production 21,533 · aegra 1,242（10-03）· agentscope-runtime 876 | 已录族+停更旧件 |
| crewai+studio+OR+platform | CrewAI-Studio 1,357 · chirpz-ai/pandaprobe 785（10-04，traces/evals 平台已录）· evo-ai 613（25-06 停更） | 已录族 |
| autogen+platform+OR+studio | autogenstudio-skills 315（25-01 停更）· autogen-studio 83 | 停更旧件域 |
| openai+agents+sdk | （主扫 5 行全已录族） | 已录族 |
| google+adk+agent | Sri-Krishna-V/awesome-adk-agents 348（08-18 未更）· Azure labs 87 | 首见微型判据+课程件 |
| mastra+agent | （头部全已录族） | 已录族 |
| pydanticai+agent | PydanticAI-Research-Agent 144（25-11 停更）· pydantic-ai-tutorial 143 | 教程/停更域 |
| semantic+kernel+agent | （头部全已录族） | 已录族 |
| agent+interop+protocol+OR+a2a | （A2A 生态已录族；a2a 正主 google/A2A 主扫 per_page 剪切线外、历班在录） | 已录族 |
| agent+framework+benchmark+OR+comparison | （横评类已录族） | 已录族 |

**批6 域结论**：稳定期延续，零机制级新差量；本班最大信号 Agno 系 WebSearch
通道捞出而非主扫（42.6k★ 大仓 147 组词从未命中——头部查询 per_page=5 剪切
线+词形盲区又一例，与 StaffDeck/magic/openworker 同型，系第 4 例）。

## 全类型雷达对账（任务书逐类型：查询词/调研时间/增量/适用结论）

调研时间：2026-10-08 06:40-07:25（UTC+8）；来源=主扫 A 常驻 89+B6 10+双轮
16（gh api search，JSONL 410 仓）+雷达 C（awesome/trendshift/topic/npm/pypi）
+WebSearch 串行 1 发。逐类型增量均为「竞品有、本站没有、确实有用」判据：

| 预置类型（任务书列举） | 对应查询组与头部 | 增量与结论 |
|---|---|---|
| 直接执行 | （无独立流程组；direct 系快档引擎） | 零新差量；快档定位与历班一致 |
| 代码 | B1 内置 11 组：SkillSpector 19,514（装前扫描源头，已录放量族）/costrict 4,446/planning-with-files 27,323（已录） | 代码域头部全已录族，零新差量 |
| 小说 | A7 novel/creative writing：alfredxw/denova 876（已录复认）/NovelClaw 379（已录） | 已录族复认，零新 |
| 连载（serial_novel） | A7 long-form：zenstory-ai/oh-story-claudecode 7,346（已录，借鉴包在档）/neuro-book 724（已录） | 已录族复认；oh-story 借鉴件已落地清单在案 |
| 自媒体文章（article） | A7 article：ai-collab-playbook 452/OmniWriter 180（首见微型，域旁教程） | 判据件零（教程域） |
| 调研报告（research） | A10 deep research：gpt-researcher 29,940/deep-research 19,767/hyperresearch 3,800（均已录） | 已录族复认，零新 |
| 短视频脚本（video_script） | A10 short video：huobao-drama 15,824（已录）/genpark skill 9（微型） | 已录族复认；00 时班 native-subtitle-quote-image 借鉴方向维持雷达 |
| 技术方案（tech_proposal） | 随 A10/research 域覆盖；B3 spec 域常驻 | 零新差量 |
| 翻译（translation） | A7/A10 translation：OpenCreator 12,616/inkos 10,155（均已录放量族） | 已录族复认，零新 |
| 演讲稿（speech） | A7 speech/presentation script 组（头部已录族） | 零新差量 |
| 工作汇报（weekly_report） | A10 weekly report：GitPulse 16/lazyweek 5（微型首见，域旁） | 判据件零 |
| 商务邮件（email） | A10 email：AI-Based-Email-Generator 53（微型） | 判据件零 |
| 扫榜选材（rank_scan） | A9 webnovel author tools 组（头部已录族） | 零新差量 |
| 禅道工单（非独立流程） | F 专项+npm 禅道通道三查 | 第 32 例复认零新；自动修复集成面维持独占 |
| 【补充】对话（chatbot） | A10 chatbot memory：nanobot 48,844/py-gpt 1,982/hope-agent 1,769/LightMem 1,185（均已录） | 已录族复认，零新 |
| 【补充】文档（doc） | A10 doc gen：beagle 83（微型首见域旁） | 判据件零 |
| 【补充】知识库（RAG） | A5/A10 deep research+DocsGPT 18,314（已录） | 已录族复认，零新 |

**任务书「13 种/14 名称」与注册表差异核清（如实解释，未删任何列举类型）**：
任务书言「13 种」但列举 14 个名称；实际注册表 `flows.py:36 BUILTIN_FLOWS=18`
（import 实数）。14 个名称中 **13 个系独立预置流程全部在册**（direct/code/
novel/serial_novel/article/research/video_script/tech_proposal/translation/
speech/weekly_report/email/rank_scan）；第 14 个「禅道工单」**非独立预置流程**
——禅道激活 Bug 由 `zentao.py` 定时扫描自动转成 code 修复任务
（README:242「禅道工单是自动转成」口径），归 F 专项巡检面；另有 5 种任务书
未列但在册：doc（文档）/defect_retro（缺陷复盘）/presentation（演示文稿）/
resume（简历）/bid_doc（标书编制）。13+5=18 对账吻合，README:135「18 种」
口径准确。**三种口径（任务书 14 名称/「13 种」计数/实际 18 流程）本班全部
对清，零删零改。**

## 本班新面孔定性（三门槛：重合度/可直读性/用户会搜吗）

| 仓 | stars/pushed | 来源 | 机制亮点 | 对比 | 结论 | 日期 |
|---|---|---|---|---|---|---|
| agno-agi/agno | **42,603** / 10-07 | WebSearch 交叉验证+repos 坐实 | 全栈 agent 平台（原 phidata）：「Build, run, and manage agent platforms」——agentic RAG/记忆/工作流/评估/多租户 runtime 一体，Python 生态 | **B6 框架域重磅对标件**（42.6k★ 超 microsoft/agent-framework 3 倍）：其系 SDK+runtime 消费层框架、CodeBee 系跨 CLI 桌面编排台——形态不同但「agent 平台全栈」愿景同域；RAG/记忆/评测面 CodeBee 均有对应物（知识库/经验库/rubric 评审）；147 组词从未命中系剪切线+词形盲区第 4 例 | **B6 对标入库（雷达/对标跟踪），交第 3/4 步评审** | 2026-10-08 |
| screenpipe/screenpipe | 21,853 / 10-07 | topic:mcp updated 头部 | YC S26「Open Computer History」：连续录屏+索引+检索（24h/d 本机工作录屏，SQL/插件生态） | A5 computer use 域旁（录屏记忆非 agent 控制）；与编排台不重合；「本机工作史可检索」与任务档案方向弱相关 | 参考（域旁雷达，跟踪不立项） | 2026-10-08 |
| Sri-Krishna-V/awesome-adk-agents | 348 / 08-18 | 主扫 B6 首见 | Google ADK agents 策展清单（模板/最佳实践） | awesome 域判据（ADK 垂直）；两个月未更 | 微型判据（awesome 雷达） | 2026-10-08 |
| Nachx639/context-canary | 0 / 10-07 | trendshift | Claude Code 行为指示器：不守指令就「死」并自动 compact 的像素金丝雀 | 微型玩具；「上下文压力可视化」词形与 A3 同向但 0★ 无机制面 | 微型判据（雷达） | 2026-10-08 |

## 七专项巡检结论（源码/数据实测，本班独立实读）

- **A. token 节约**：八机制锚点在位零漂移——pipeline.py:613
  `_budget_max_tokens`/:648 `_cost_gate_block`（:677 花费闸先于 token 闸）/
  :2538 `_shrink_context_block` 四层降级、step_runner.py:28
  `_PRECHECK_RATIO=0.9` 事前门、skills.py:676 `block_for`（stable_order 保
  前缀缓存）。四对标方向「三有一不适用」维持（diff-only 评审满配/cascade
  opt-in 满配/前缀缓存设计面已友好/语义缓存拍板件维持）。| 巡检·A
- **B. 插件市场**：六源在位实测（market_remote.py:50 `SOURCES`：zcode/
  anthropic/anthropic-skills/claude-skills/clawhub/cocoloop）。本班候选走
  接入三问：agno=SDK 框架非六源包（不接，对标跟踪）；screenpipe=录屏索引
  域旁（不接）；awesome-adk-agents=清单非包（不接）。**零新接入**。| 巡检·B
- **C. 任务类型**：`BUILTIN_FLOWS=18` import 实测（flows.py:36）；18
  name+18 note+18 goal_hint 共 54 串 i18n.js 全部有键（本班脚本实测
  missing=0，键=中文串本身 t() 直译形态）；recommendTaskType 在位
  （app.js:837，657dc13 补词后 video_script 规则含「B站/视频号」）；README
  「18 种」口径准确。任务书 14 名称差异核清见上节（零删零改）。对话记忆/
  知识库两补充方向随 A10 主扫照跑（头部全已录族零新差量）。| 巡检·C
- **D. 经验库**：data/skills.json lessons=**70** 本班独立统计（流程规范 30
  =43%/节奏爽点 16/情节逻辑 10/人物塑造 7/一致性 4/文笔风格 3；scope 分布
  serial_novel 41/code 12/*/12/direct 4/article 1）——与 03 时班勘正基线
  零漂移。本班调研产出（Agno 对标/词形盲区第 4 例）属 borrow-log 面不入
  lessons（knowledge/knowledge.py 头注分工边界）。| 巡检·D
- **E. 新 CLI 接入**：DEFAULT_CATALOG=**14 条目**实测（catalog.py）；本机
  which 按探测名实测 **13/14 在装**（仅 openclaw 缺，与历班一致）；候选
  DeepSeek-Reasonix（esengine 35,749★，+2）/FuXi/Gitlawb/zero/Empryo which
  全 MISSING——维持不盲接防死链，Reasonix 首位待本机实装后再评。| 巡检·E
- **F. 禅道集成**：data/zentao.json 实测 `poll_enabled=False`、claims={}
  零积压、last_error 空、last_scan 停 2026-09-21 20:43:44（**18 天未扫**，
  关闭态非故障）、product_profiles 1 条在位、auto_resolve/auto_merge/
  triage_ai 全 True。**维持交拍板（是否重开轮询），不擅动用户配置**；
  竞品面第 32 例复认零新（禅道 npm 通道），自动修复集成面维持独占。
  | 巡检·F（交拍板 1 件维持）
- **G. 产品巡检**：「13 种」「单源/双源」类过时文案 grep README+app/ui
  零命中；README:135/242「18 种」与实际一致；657dc13 落地件（verify_command
  消毒分流+video_script 补词）在位实读确认。**零新毛病**。| 巡检·G

## 复查记录（06 时班，基准=03 时班，间隔约 3h）

- 存量头部 410 仓主扫 stars 全 alive 零 archived：superpowers 296,375（+35）/
  mattpocock-skills 279,503（+152 续放量）/ECC 274,904（+94）/hermes-agent
  251,926（+28）/deepseek-harness 245,153（+65）/opencode 212,200（+10）/
  dify 158,039（+6）/ponytail 157,546（+70）/pi 113,178（+9）/open-design
  99,860（+8）；**orca 87,093（+47 续领跑）**；crewAI 59,427/oh-my-openagent
  69,875（+3）；E 候选 **DeepSeek-Reasonix 35,749（+2 首位维持）**；
  对标件 StaffDeck 1,969（±2 缓存抖动）/nimbalyst 1,849 持平/story-skills
  277 持平（本班 in:name 勘定正主=danjdewhurst）/magic-context 2,276（+1，
  勘定正主=cortexkit）/amux 521 持平/strands-agents 8,727（+5）/autoharness
  9,179（+2）/cost-xray 3,834 持平/agent-memory 2,380 持平。
  | 复查
- 放量信号：addyosmani/agent-skills 101,666→**102,753（+1,087/两日）**；
  pacifio/atlas 8,886→**9,269（+383 快涨续）**；Compositor 11,034 续
  （trendshift 复认）。| 复查·放量

## 本班结论

1. **Agno（42.6k★）系本班最大信号**：WebSearch 交叉验证捞出、repos 坐实，
   系 147 组词从未命中的 42.6k★ 大仓——「头部查询 per_page=5 剪切线+词形
   盲区」第 4 例（StaffDeck/magic/openworker 同型），印证 10-04 补纪律
   （单通道结论须 WebSearch 串行交叉验证）持续有产出。B6 对标入库交评审。
2. 批6 域（框架/平台/SDK）稳定期延续：MAF v1.0/Aegra/pandaprobe 等全已录
   复认，零机制级新差量。
3. 全类型 14 名称对账核清（13 独立流程在册+禅道工单转 code+5 种补充在册
   =18），三种口径零删零改。
4. 七专项零新代码缺口：A 八锚点零漂移、B 零新接入、C i18n 54 串
   missing=0、D 基线 70 零漂移、E 13/14+候选零盲接、F 关闭态维持交拍板、
   G 零新毛病——「巡检无产出」稳定态延续。

## 待深挖/风险/未验证项（如实记录）

- 待深挖队列 11 项维持零新队列项（Agno 对标件机制面全有对应不入深挖）。
- 交拍板 4 件维持：①禅道 poll_enabled=false 是否重开；②cost-xray 观测
  粒度差量；③release_gate 判定面（Ran/OK 汇总校验）；④npm 包体 files
  白名单收紧。
- 未验证项：禅道积压/路由/回写链路（poll 关闭态不触发真实扫描，无实例
  权限——按纪律不动用户配置）；pypi 仅在架验证（未做包内容级）；trendshift
  flight payload 提取法第 3 班（49 仓全量成功，置信度再升高）。
- 词形盲区第 4 例（Agno）提示：42.6k★ 量级仓在「agno」品牌词与「agent
  platform」功能词上均与现有 147 组词正交——暂不新增词组（WebSearch 交叉
  验证通道已能兜住，防词库膨胀），如实记档。

---

*本报告系新一轮（v0.1.95 后）计划第 1/4 步实录：主扫 115 查询（批6）+
雷达 C 全过+WebSearch 交叉验证+全类型对账+七专项巡检。第 2/4 步（深化
实证+落地提案）承接本底稿。*

---

# 第 2/4 步（2026-10-08 续班）：七专项深化实证 + 落地件

> 承接上节底稿做深化实读（非重复扫描）：每项落到真实文件行号与数据实测；
> 本轮证据选出落地件 1 件（rank_scan/defect_retro 推荐面补齐），变更清单
> 先行、代码后落、五道关验证证据随附。

## A. token 节约（八机制锚点实读 + 四对标方向判定，零当新功能）

- 预算闸 `pipeline.py:613` `_budget_max_tokens`（ENV 优先）+ `:687-701`
  命中返回 ENV_BLOCK **只拦下一步**；花费闸 `:648` `_cost_gate_block`
  （`pipeline.py:677` 花费闸先于 token 闸），日/月硬顶读失败放行不锁死业务。
- 分层上下文降级 `pipeline.py:2538` `_shrink_context_block`：经验库→4K →
  模块库按「## 」边界 → 圣经按二级标题边界；连载重试封装 `:2581`
  `_serial_shrunk_block`（知识库块与大纲/前情永不动，降级说明进 step note）。
- 事前预检 `step_runner.py:28` `_PRECHECK_RATIO=0.9`；口径分离锚点
  `token_meter.py:129` `last_context`（最近一次上下文锚点）≠ `used()` 窗口累计
  ——响应式预算闸与事前拒答预判不混用（freebuff 借鉴在位）。
- 三段压缩 `compaction.py:2`（工具结果剪枝→LLM 摘要→surface replace），
  灰度开关 `pipeline.py:231` `_compaction_enabled`，撑爆→压缩→守门重试
  `:703-720`；usage 累进 token_meter（§1A 不变量：模型可见即已记录）。
- 经验召回 `skills.py:676` `block_for`（stable_order 保前缀缓存）；
  会话复用 `sessions.py`（codex/claude/opencode/qwen/mimo 五源扫描）；
  diff 评审 `pipeline.py:921`（diff 为主要依据+环境允许只读核对，v0.1.86 件）。
- **四对标方向判定（全部是既有能力，不是本轮新功能）**：prompt 缓存=
  stable_order 前缀友好排序已在位；语义缓存=历班拍板件维持不扩（纪律：
  不另扩缓存）；diff-only 评审=v0.1.86 满配；**廉价模型分流=
  `dispatch.py:20` TYPE_DIMENSIONS（18 型→writing/coding/reasoning/vision）
  + `_KIND_AFFINITY` + `_TIER_SCORE`（easy/default/hard ×budget/standard/
  premium 价位分）按难度分层选模已在位**。| 巡检·A

## B. 插件市场（六源+三道闸实读；候选三问过筛，零新接入）

- 六源 `market_remote.py:50-76` 实读在位（zcode/anthropic/anthropic-skills/
  claude-skills/clawhub/cocoloop）；SSRF 网关 `:113` `assert_public_url`
  （https-only+环回/私网/保留/链路本地拒绝+**重定向逐跳复检** `:156-181`）；
  体量五上限 `:93-98`；纯技能白名单 `:568` `inspect_tree`（脚本/钩子/MCP
  组件一律拒绝）+显式 skills 白名单收敛 `:941-952`。
- 安装通道 `market.py:267` `install_files`：装前静态安检 skill_scan（:281）/
  内容突变对账（:289）/typosquatting 近似名（:294）/装后冒烟（:341）全在位。
- 候选三问过筛（缓存 808 项，10-03 快照）：anthropic 官方目录 315 项多为
  MCP/API 集成件（需外部凭据，可直读性不过）；claude-skills/marketing-skills
  带 62 个 Python 工具（折在纯技能白名单，且营销域与 13 型正交）；agno=SDK
  框架非六源包。**三问全不过者雷达跟踪，零新接入**；方法论（API 集成件
  与纯技能件的甄别线=白名单而非人工挑拣）已由 inspect_tree 结构化，无需
  蒸馏入库。| 巡检·B

## C. 任务类型（18 型逐项核对；发现 1 处辅助信息缺口→本轮落地件）

- `flows.py:36-134` BUILTIN_FLOWS=18 实读；`rank_scan` note「四平台」=
  `paihang.py:97` `_SOURCES` 四源（七猫/番茄/起点/纵横）实读一致；
  `defect_retro` note「三视角复盘」管线在位。
- 调度维度 `dispatch.py` TYPE_DIMENSIONS 18/18、耗时基线 `usage.py:873`
  `_DURATION_BASELINES` 18/18 实读（v0.1.87 对账件维持零漂移）。
- i18n：18 name+18 note+18 goal_hint 词条在位（06 时班脚本实测 missing=0
  维持）。
- **缺口（本轮发现并落地）**：推荐规则表 `app.js:839` 此前 16 条——
  rank_scan/defect_retro 两型无规则，direct 档输入「扫榜看热门题材」「复盘
  缺陷」类目标不弹切换建议，**两型的专用数据链路（四平台抓榜/CSV 三视角
  管线）被整个旁路**，模型只能凭空答榜单。同型先例：v0.1.93 补四类型、
  v0.1.95 video_script 补词（goal_hint 引导词与规则词对齐）。| 巡检·C

## D. 经验库（基线零漂移；偏科维持记录不批量改）

- `data/skills.json` lessons=**70**（流程规范 30=43%/节奏爽点 16/情节逻辑 10/
  人物塑造 7/一致性 4/文笔风格 3；scope serial_novel 41/code 12/*/12/
  direct 4/article 1）——与 06 时班基线零漂移。
- 去重/并类通道在位（v0.1.89 并类 8→3、v0.1.91 蒸馏守卫、v0.1.94 幂等
  重放）；本轮零新蒸馏（「词形盲区」属调研方法论，按 knowledge.py 分工
  边界留 borrow-log 面）。流程规范偏科 43% 维持记录，不验证不批量改。
  | 巡检·D

## E. 新 CLI 接入（14 条目+本机复测；候选零盲接）

- `catalog.py:32-215` DEFAULT_CATALOG=**14** 条目实读（06 时班计数维持）；
  本机 which 复测 **13/14 在装**（openclaw 缺，与历班一致）。
- 候选 DeepSeek-Reasonix/FuXi/Gitlawb/zero/Empryo which 全 MISSING
  （本班重跑探测）→ 待验证队列，不盲目录接入防死链；Reasonix 首位待实装。
  | 巡检·E

## F. 禅道集成（状态/档案/回写链路实读；未触发生产动作）

- `data/zentao.json` 实读：poll_enabled=false（用户配置不擅动）、claims={}
  **零积压**、last_error 空、last_scan 停 2026-09-21 20:43:44（关闭态非
  故障，next_scan 同步停摆）、auto_resolve/auto_merge/triage_ai=true、
  product_profiles 1 条（product 96，backend→E:\GitLab\cbc\mo-so，
  module_routes 空=AI 兜底路由在位）。
- 回写链路锚点实读：`zentao.py:1708` `_ensure_resolved`（幂等 resolve+
  operations 回执）、`:1729` `_transfer`（空 target 先 GET 带回指派人，
  防 #27783 清指派）；legacy interval_hours→interval_minutes 迁移
  `:269-279` 在位（本机数据恰走迁移路径，属设计非漂移）。
- 按纪律未触发真实扫描、未 resolve 任何工单。**交拍板维持**：是否重开
  轮询由人决定。| 巡检·F

## G. 产品巡检（文案/链接/流程一致性；1 件落地+其余零新毛病）

- 过时文案守卫在位（tests `_STALE_COPY`：13 种/单源模式等零命中）；
  「设置→编排设置→预算」路径实链（index.html:259 → app.js:12813
  NS_LABEL schema 驱动渲染 budget 三字段，预算闸消费端 pipeline:613/648
  闭环）；「禅道工单自动转成」README:242 口径与 zentao 实现一致。
- **落地件（见下节）**；其余零新毛病。| 巡检·G

## 落地件：rank_scan/defect_retro 推荐面补齐 + 建议文案按引擎分流

**变更清单（写码前定稿）**：
1. `app/ui/app.js` `recommendTaskType`（:839 rules 表）：新增
   `defect_retro` 规则（置于 code 之前——专属词前置同 resume/presentation/
   bid_doc 惯例；要求「复盘」与 bug/缺陷 共现或命中「漏测」，纯 debug
   仍归 code）+ `rank_scan` 规则（末位兜底，宽词不被前置规则截胡；词形
   对齐其 goal_hint「热门题材」）。
2. `app/ui/app.js` 类型建议弹窗（:3117）：文案按建议目标 engine 分流——
   direct 引擎目标（rank_scan/defect_retro）用「专用数据链路与产出模板」
   （如实，无门禁不虚标），review/code 目标维持「规划与质量门禁」。
3. `app/ui/i18n.js`：新文案键补 1 条英文词条（t() 字面量对账契约要求）。
4. `tests/test_recommend_rules.py`：`_RULELESS` 收敛为 `{"direct"}`（原
   「快档三件豁免」中仅 direct 的豁免理由成立——推荐逻辑只在 direct 档
   触发无需自荐；两型豁免会使专用链路永久旁路）；补命中样例 4 条+
   防截胡回归 1 条（「分析这个bug的原因」仍归 code）。

**改动前后行为**：前——direct 档输入「扫榜看看热门题材」「复盘这些bug」
无任何建议，快档直答（榜单/根因无数据支撑）；后——弹「更适合 XX 流程」
确认框（快档目标如实标注数据链路卖点），同意即切换到抓榜/CSV 专用管线，
拒绝维持 direct。gated 流程的建议路径零变化。

**覆盖类型**：direct 之外 17/17 预置流程推荐面全覆盖；直接受益
rank_scan（扫榜选材）与 defect_retro（缺陷复盘）两型，其余 15 型靠
新增回归样例锁序防漂移。

**验证方法与结果**：node --check app.js/i18n.js OK；
test_recommend_rules 3/3 绿；邻接守卫（test_borrow_iteration/
test_i18n_key_coverage/test_borrow_round/test_full_type_round）33/33 绿；
全量 `python -m unittest discover` **OK**（exit 0）。

**风险（如实）**：①本件修订了 v0.1.93 刚定的「快档三件豁免」口径——
不认可可整件 revert（4 文件独立，无数据迁移）；②「排行榜」宽词在末位，
对「把数据做成排行榜图表」类罕见目标会弹一次建议（可拒绝，会话内仅
一次 typeSuggestionDone）；③defect_retro 与 code 的 bug 词存在顺序依赖
（测试锚定防回退）。无并发/权限/迁移/公共 API 面。

## 交人确认 / 未验证项

- 交拍板 4 件维持（06 时班口径）：禅道轮询重开/cost-xray 观测粒度/
  release_gate 判定面/npm 包体 files 白名单。
- 未验证：六源市场缓存为 10-03 快照（刷新属用户「拉取更新」动作，巡检
  不代触网）；B 专项候选均为目录元数据过筛（未做包内容级下载检查——
  零候选过三问，无需下载）。

---

*第 2/4 步完：A-G 深化实证全落行号级证据；落地件 1 件（4 文件，全量
269 模块 discover 绿）。第 3/4 步（五道关+提交推送+发版）承接。*

---

# 第 3/4 步（2026-10-08 续班）：落地件验证收口 + 本轮专属行为测试

> 承接第 2/4 步在制品，本步不重做已落改动：收敛变更清单、补本轮专属测试
> 文件、以仓内逐模块纪律复跑全量绿。五道关+提交推送+发版留待第 4/4 步。

## 变更清单收敛（实际动到的文件）

- 第 2/4 步已落、本步未再动：app/ui/app.js（规则表两型+文案按引擎分流）、
  app/ui/i18n.js（新文案键 1 条 EN）、tests/test_recommend_rules.py
  （_RULELESS 收敛 +5 样例）、docs/borrow-log/knowledge.md（沉淀）。
- 本步新增：**tests/test_full_type_improvements.py**（本轮专属行为测试
  4 件，只测本轮行为；复用 test_recommend_rules._rules，不重复邻接守卫
  职责——t() 字面量↔i18n 对账在 test_borrow_iteration、18 型矩阵在
  test_full_type_round，分工边界写入 docstring）。
- 计划候选 flows.py/pipeline.py/style.css **未动**：落地件不涉及后端流程
  与样式，无验证价值的候选不强行落地。

## 本步新增测试（正常/边界/回归）

| 测试 | 面 | 断言 |
|---|---|---|
| test_new_types_are_direct_engine | 正常 | rank_scan/defect_retro 注册表 engine=direct（「专用数据链路」文案只弹给真无门禁类型） |
| test_suggestion_copy_splits_by_engine | 正常+回归 | app.js 建议块 flow.engine 三元分流接线在位（direct=数据链路口径；review/code 门禁旧口径不丢） |
| test_direct_engine_rules_exactly_new_two | 边界 | direct 引擎带规则的类型恰为本轮两型（文案可达面锁定；后续扩须 consciously 更新） |
| test_rank_scan_broad_word_known_tradeoff | 边界 | 「排行榜」宽词兜底取舍锚定（「把数据做成排行榜图表」→rank_scan，收窄词形时 consciously 翻转） |

## 验证结果（本步实测）

- 关①：py_compile（两个测试文件）+ node --check（app.js/i18n.js）OK。
- 新件 test_full_type_improvements **4/4 绿**；邻接守卫
  test_recommend_rules 3 + test_borrow_iteration 9 + test_full_type_round 8
  + test_i18n_key_coverage 3 + test_i18n_dups 3 = **26 绿**。
- 全量：单进程 `discover -s tests` 本机**静默中途退出**（无 Ran/OK 汇总行、
  os 层 exit 0，进度点 1218 < 装载 2233）——第 2/4 步「全量 discover OK
  (exit 0)」实为同一静退签名（管道下 exit 0 不可作绿凭据）。按 f16e4d1
  逐模块纪律复跑：**270/270 模块全绿**（269 基线 + 本步新件 1）。

## 新发现（交拍板候选 +1）

- **单进程 discover 静退根因未查**：疑某测试路径触发 os._exit(0)（点数
  戛然而止、零 traceback 零汇总）。建议后续班全量绿一律以逐模块计数为准，
  单进程 exit 0 不作凭据；根因排查入待深挖队列。

## 未实施风险（维持第 2/4 步三条，零新增代码风险）

①「快档三件豁免」口径修订可整件 revert（4 文件独立无迁移）；②「排行榜」
宽词对罕见目标会弹一次建议（可拒绝，会话内仅一次，已锚定测试）；③
defect_retro 与 code 的 bug 词顺序依赖（测试锚定防回退）。交拍板 4 件
维持（禅道轮询重开/cost-xray/release_gate/npm files 白名单）+ 本步静退
根因 1 件。

---

*第 3/4 步完：落地件验证收口（新测试 4/4 + 邻接 26 绿 + 逐模块 270/270）；
单进程 discover 静退如实记档并交拍板。第 4/4 步（五道关+提交推送+发版+
当日归档）承接。*

---

# 2026-10-08 08 时班（新一轮计划第 1/4 步·批1：代码质量与评审——全类型竞品调研+七专项巡检）

> 开工实录：08:01（UTC+8，hour=8，8%7=1 → 轮换批1，当日未跑过）、分支
> main（f16e4d1）。**已有改动归属核清**：工作区 4 改 2 新（app.js/i18n.js/
> test_recommend_rules.py/knowledge.md 改 + full-type-review.md/
> test_full_type_improvements.py 新）全部系同日 06 时班第 1-3/4 步在制品
> （报告自身有实录，落地件 rank_scan/defect_retro 推荐面在位实读确认，
> node --check×2 + py_compile×2 本班复验过）——本班零踩踏，承接追加。
> 本班改动面仅 docs/borrow-log/ 两锚定文件；提交推送发版留第 4/4 步收口人。

## 主扫描实录（stars 与 updated 双轮 + B1 轮换批）

- **主扫**：`scripts/borrow_scan_nightly.py` 无参全量（自动选批 B1）——
  A 常驻 89（含内置 B1 11）+ **B1 轮换 11** + A1u/A1p2 双轮 16 =
  **116 查询零失败零限流**（PROGRESS DONE 116 实证；后台 1800s 未被杀）；
  另补 **B1u 新锐轮 11 词**（sort=updated，双轮排序纪律对 B1 批全覆盖）。
  合计 127 查询 576 行 **450 唯一仓**：**295 已录 / 155 首见**（首见以域外
  噪声为主：apache OptaPlanner 迁移通知仓 3,515★/Coursera 课件/FTC SDK/
  WPGulp/Unity 特效等与 06 时班同源噪声族）。
- **判据级新面孔 1 件**：**kenryu42/cc-safety-net 1,582★**（2025-12-25 建、
  2026-10-08 仍 push、MIT、中英日三语文档）——**AI coding agent 预执行
  守卫**：工具调用运行**前**拦截破坏性 git/文件系统命令+敏感文件访问，支持
  Amp/Antigravity/Claude Code 等多 CLI（A1u 新锐轮捞出——**updated 排序
  纪律直接产出**，stars 轮 5/页剪切线从未命中）。详见新面孔表。
- **雷达 C 全过**：
  - **awesome 19 源 repos 实测 19/19 alive 零 archived**（vivy-yi 80/
    ComposioHQ 76,670/ai-boost 4,746/punkpeye 95,910/bradAGI 1,323/
    hesreallyhim 55,210/VoltAgent-skills 35,337/buildwithclaude 3,604/
    e2b 30,295/llm-apps 140,939/RUC-NLPIR 1,065/TeleAI 659/caramaschiHG
    1,928/vijaythecoder 4,389/TsinghuaC3I 665/Engineering4AI 289/papers
    1,827/EvoMap 234/IAAR 1,259）；对标件 StaffDeck 1,969 持平。
  - **trendshift HTTP 200 338KB**：**flight payload 提取法本班缺席**
    （full_name 零命中，如实记）；href 双通道法 **29 仓**全量成功——已录
    复认 storytold 家族 7 兄弟/openai/math/morluto-rea/Compositor/
    dsh-our-free-model/autoharness 9,181（+2）/mattpocock-skills/
    knowledge-work-plugins/diagram-design/native-subtitle-quote-image/
    addyosmani/agent-skills **102,795（+42 vs 06 时班，放量放缓）**；
    **在库复认 2 件**：maximhq/bifrost 8,613（10-05 入库 8,558 → **+55**，
    企业 AI 网关域参考维持）/GetBusbar/busbar 175（10-05 入库 171 → +4，
    治理控制面微型维持）；域旁排除 omarchy-apple-dev/shaders/
    system-design-notes/onedump/gosnakego/AnyPS5/openGym/jumper 等。
  - **topic 8 页全扫（updated 每页 8 仓）**：常客复认 oh-my-openagent
    69,878（+3）/omnigent 10,653/amux 521/ai-maestro 814（+2）/
    DeepSeek-Reasonix 35,749 持平；首见 4 件见新面孔表（ODS/macro/
    opencorvus/agentos）；其余微型（human-review 19★/rimz 32★/LoopTroop
    159★ 已录）。
  - **npm 三查**（agent orchestrator/claude code/zentao ai）：头部全已录
    （nax 0.83.5/bizar 10.33.0/oceanus/coleo；官方二进制包零判据）；
    **禅道 npm 通道第 33 例复认零新**（dsh-plugin-zentao 0.1.17+zentao-cli
    0.3.1+zentao-api 0.7.2），GitHub 侧自动修复集成竞品维持独占。
  - **pypi**：agent-orchestrator JSON API HTTP 200 在架。
  - GitHub Trending 直抓未行（trendshift 补位，历班惯例）。
- **WebSearch 串行 2 发**（B1 域定向）：均零新仓（Bing 分词失效返词典
  噪声 1 发+消费级结果 1 发；DDG 域名被拦）——**通道噪声高如实记录，
  不宣称交叉验证捞出**；06 时班 WebSearch 已捞出 Agno 坐实，10-04 纪律
  覆盖延续，下轮继续串行配发。

## B1 批域逐词头部（11 词 stars 轮+updated 轮，本班主扫域：代码质量与评审）

| 查询词 | 头部命中（top1-3） | 判定 |
|---|---|---|
| code+review+agent+OR+ai+code+reviewer | open-code-review 族/SkillSpector 19,645（updated 轮）、anthropics/claude-code-security-review 6,318 | 已录族 |
| pr+review+bot+github | qodo/pr-bot 族（已录）/mgreiler/code-review-checklist 1,088 | 已录族 |
| github+action+ai+review | anthropics/claude-code-security-review 6,318/CoderGPT 族 | 已录族 |
| bug+detection+agent | BugTraceAI（已录）/微型 | 已录族 |
| vulnerability+scanner+agent | future-architect/vuls 12,281/msoedov/agentic_security 2,019 | 已录族 |
| test+generation+agent+OR+ai+testing+agent | pr-test-guard 139（已录微型）/学生件 | 已录族 |
| regression+test+generation+ai | 学生件域 | 空赛道维持 |
| refactor+agent+OR+tech+debt+agent | sentrux 3,314（已录）/学生件 | 已录族 |
| secure+code+review+agent+OR+security+review+bot | Tencent/AI-Infra-Guard 6,772/NVIDIA SkillSpector 19,645 | 已录族（+29/2h 放量续） |
| api+test+generation+agent+OR+integration+test+agent | 微型/教程域 | 空赛道维持 |
| ast+based+code+editing+agent+OR+symbol+level+code+edit | （头部已录族） | 已录族 |

**批1 域结论**：代码质量与评审域头部全已录族（SkillSpector 19,645 放量续/
vuls/AI-Infra-Guard/cc-security-review/mira 358/pr-af 647 均在库），
零机制级新差量；唯一增量信号系域外侧翼捞入的 cc-safety-net（预执行守卫，
见新面孔表——借 runner 层方向非评审域本身）。

## 全类型雷达对账（任务书逐类型：查询词/调研时间/增量/适用结论）

调研时间：2026-10-08 08:01-09:10（UTC+8）；来源=主扫 A 常驻 89+B1 11+双轮
16+B1u 11（gh api search，JSONL 576 行）+雷达 C（awesome/trendshift/topic/
npm/pypi）+WebSearch 串行 2 发。逐类型增量均为「竞品有、本站没有、确实有用」
判据：

| 预置类型（任务书列举） | 对应查询组与头部 | 增量与结论 |
|---|---|---|
| 直接执行 | （无独立流程组；direct 系快档引擎） | 零新差量；06 时班推荐面落地件在位实读 |
| 代码 | B1 11 组（SkillSpector 19,645/vuls 12,281/AI-Infra-Guard 6,772）+A10 codegen 组（planning-with-files 27,324/costrict 4,446） | 头部全已录族零新；cc-safety-net 系 runner 层安全域借入候选（非评审流程） |
| 小说 | A7 novel/creative：denova 876/NovelClaw 379/chinese-novelist-skill 3,314（+2 复认） | 已录族复认，零新 |
| 连载（serial_novel） | A7 long-form：oh-story 7,346 持平/neuro-book 724 持平 | 已录族复认；借鉴包落地清单在档 |
| 自媒体文章（article） | A7 article：ai-collab-playbook 452/OmniWriter 180 | 判据件零（教程域） |
| 调研报告（research） | A10 deep research：gpt-researcher 29,940/deep-research 19,767/DocsGPT 18,314/hyperresearch 3,802 | 已录族复认，零新 |
| 短视频脚本（video_script） | A10 short video：huobao-drama 15,824 持平/genpark skill 9 | 已录族复认，零新 |
| 技术方案（tech_proposal） | 随 A10/research 域覆盖；B3 spec 域常驻 | 零新差量 |
| 翻译（translation） | A7/A10 translation：OpenCreator 12,617/inkos 10,156/skillkit 1,546 | 已录族复认，零新 |
| 演讲稿（speech） | A7 speech/presentation script 组（头部已录族） | 零新差量 |
| 工作汇报（weekly_report） | A10 weekly report：GitPulse 16/lazyweek 5 | 判据件零（微型域） |
| 商务邮件（email） | A10 email：AI-Based-Email-Generator 53 | 判据件零 |
| 扫榜选材（rank_scan） | A9 webnovel author tools 组（头部已录族） | 零新差量；06 时班推荐规则补齐件在位 |
| 禅道工单（非独立流程） | F 专项+npm 禅道通道三查 | 第 33 例复认零新；自动修复集成面维持独占 |
| 【补充】对话（chatbot） | A10 chatbot memory：nanobot 48,844/py-gpt 1,982/hope-agent 1,769/LightMem 1,185 | 已录族复认，零新 |
| 【补充】文档（doc） | A10 doc gen：beagle 83（微型域旁） | 判据件零 |
| 【补充】知识库（RAG） | A10/A5 deep research+DocsGPT 域 | 已录族复认，零新 |
| 【补充】演示文稿（presentation） | A10 slides：presentation-ai 3,033/SlideBot 1,216/ppt-agent-skill 155 | 已录族复认，零新 |

**任务书「13 种/14 名称」与注册表差异核清（本班 import 实测复核，零删零改
维持 06 时班口径）**：`from app.core.flows import BUILTIN_FLOWS` 实数
**18**：direct/code/novel/serial_novel/article/video_script/doc/translation/
rank_scan/defect_retro/research/speech/presentation/weekly_report/email/
tech_proposal/resume/bid_doc。任务书 14 名称中 **13 个系独立预置流程全部
在册**；第 14 个「禅道工单」非独立预置流程（zentao.py 定时扫描激活 Bug
自动转 code 修复任务，README:242 口径）；另有 5 种任务书未列但在册（doc/
defect_retro/presentation/resume/bid_doc）。13+5=18 对账吻合，README:135
「18 种任务类型」口径准确。

## 本班新面孔定性（三门槛：重合度/可直读性/用户会搜吗）

| 仓 | stars/pushed | 来源 | 机制亮点 | 对比 | 结论 | 日期 |
|---|---|---|---|---|---|---|
| kenryu42/cc-safety-net | **1,582** / 10-08 | A1u 新锐轮（updated 排序捞出） | AI coding agent **预执行守卫**：工具调用运行前拦截破坏性 git/文件系统命令+敏感文件访问；多 CLI 适配（Amp/Antigravity/Claude Code 等）；MIT+CI+codecov+三语文档 | **runner 层安全差量**：我们编排 CLI 有审批闸（auto_submit=false）+沙箱，但无「CLI 内部工具调用级」破坏性命令拦截网——其 hook 层模式可嫁接我们的 CLI 启动配置（permission-deny 形态） | **借鉴方向（待深挖，交第 2/4 步评审）**；非 skill 包不进市场 | 2026-10-08 |
| alamops/agetor | 88 / 10-08 | 主扫 A1 首见 | 「harness orchestrator」local-first 看板跑 Claude Code/Codex 多 CLI | A1 同形态（多 CLI 编排看板）微型新锐，与 CodeBee/ntm/caam 同赛道 | 微型判据（雷达跟踪） | 2026-10-08 |
| Osmantic/ODS | 7,125 / 10-08 | topic:ai-agents | 「把 PC/Mac/Linux 变私有 AI 服务器」V3 预发布 | 本地自托管基础设施（批7 域旁），平台不可直装、编排重合度低 | 对标参考（雷达） | 2026-10-08 |
| macro-inc/macro | 4,589 / 10-08 | topic:mcp | 团队统一工作台：email/chat/docs/tasks/agents/CRM + 共享 AI 记忆 | 企业协作域（非创作编排）；「共享 AI 记忆」词形与 A11 同向但产品形态不同 | 参考（雷达） | 2026-10-08 |
| yangheng95/opencorvus | 312 / 10-08 | topic:agent-framework | 「DIY 长程 agent 团队 harness」 | A5/A13 同向微型；用户会搜度低 | 微型判据（雷达） | 2026-10-08 |
| framerslab/agentos | 677 / 10-08 | topic:ai-agents | TS agent 框架（认知记忆/工具锻造/11 供应商） | B6 框架域微型，与 MAF/Agno 同域小份额 | 参考（雷达） | 2026-10-08 |

**B 专项三问过筛**：cc-safety-net=CLI 守卫工具非六源 skill 包（机制面走
runner 借鉴）；ageto/ODS/macro/opencorvus/agentos=平台或框架非包。**零新
接入**。

## 七专项巡检结论（源码/数据实测，本班独立复验）

- **A. token 节约**：八机制锚点行号级复验零漂移——pipeline.py:613
  `_budget_max_tokens`/:648 `_cost_gate_block`（:677 花费闸先于 token 闸）/
  :2538 `_shrink_context_block`、step_runner.py:28 `_PRECHECK_RATIO=0.9`、
  skills.py:676 `block_for`。四对标方向判定维持 06 时班（全既有能力：
  stable_order 前缀缓存友好/语义缓存拍板件/diff-only v0.1.86 满配/
  廉价分流=dispatch TYPE_DIMENSIONS 分层）。| 巡检·A
- **B. 插件市场**：market_remote.py:50 `SOURCES` 六源在位实测；本班候选
  全部非 skill 包（三问不过）**零新接入**；禅道 npm 第 33 例零新。
  | 巡检·B
- **C. 任务类型**：BUILTIN_FLOWS=**18** import 实测（本班独立跑）；18
  name+18 note+18 goal_hint=**54 串 i18n 全有键**（本班脚本 missing=0）；
  README:135/242「18 种」口径一致；06 时班落地件（rank_scan/defect_retro
  推荐规则 app.js:847/:864+文案按引擎分流 :3128）在位实读。
  | 巡检·C
- **D. 经验库**：data/skills.json lessons=**70** 本班独立统计——与 06 时班
  基线零漂移（流程规范 30=43% 偏科维持记录）；本班调研产出属 borrow-log
  面不入 lessons（分工边界维持）。| 巡检·D
- **E. 新 CLI 接入**：DEFAULT_CATALOG=**14** 条目实测（本班独立跑）；
  本机 which 复测核心 CLI 在位（dsh/claude/codex/opencode/qwen/mimo 等
  **13/14**，openclaw 缺）；候选 reasonix/fuxi/gitlawb/zero/empryo 全
  MISSING——维持不盲接防死链。| 巡检·E
- **F. 禅道集成**：data/zentao.json 实读 poll_enabled=**False**（config
  层，auto_resolve/auto_merge/triage_ai 全 True、interval_hours=2 走运行期
  迁移路径）、claims={} **零积压**、last_error 空、last_scan 停 2026-09-21
  20:43:44（关闭态非故障）、product_profiles 1 条。**交拍板维持**（是否
  重开轮询由人定），本班未触发生产动作。| 巡检·F
- **G. 产品巡检**：「13 种」「单源模式」过时文案 grep README+app/ui 零
  命中；657dc13+06 时班落地件均在位。**零新毛病**。| 巡检·G

## 复查记录（08 时班，基准=06 时班，间隔约 2h）

- 存量头部全 alive 零 archived（主扫 450 仓+repos 通道）：superpowers
  296,387（+12）/mattpocock-skills 279,561（+58）/ECC 274,928（+24）/
  hermes-agent 251,943（+17）/deepseek-harness 245,167（+14）/opencode
  212,205（+5）/dify 158,040（+1）/ponytail 157,578（+32）/pi 113,185
  （+7）/open-design 99,869（+9）；**orca 87,111（+18）**；E 候选
  DeepSeek-Reasonix 35,749 持平；对标件 StaffDeck 1,969 持平/nimbalyst
  持平/amux 521 持平/strands-agents 持平；OrchestratorInc/
  agent-orchestrator 12,875（+18）；**SkillSpector 19,616→19,645（+29
  放量续）**；bifrost 8,558→8,613（+55/3 天）；busbar 171→175。
  | 复查
- 放量信号：addyosmani/agent-skills 102,753→102,795（+42/2h，较前两日
  +1,087 明显放缓）；pacifio/atlas 9,269 持平；chinese-novelist-skill
  3,312→3,314（写作域缓增）。| 复查·放量

## 本班结论

1. **cc-safety-net（1,582★）系本班唯一判据级新面孔**：A1u 新锐轮
  （updated 排序）捞出而 stars 轮 5/页剪切线从未命中——双轮排序纪律的
  直接产出实证。runner 层预执行守卫方向交第 2/4 步评审（借鉴候选，
  非市场接入件）。
2. 批1 域（代码质量与评审）稳定期延续：SkillSpector 放量续（+29/2h）、
  头部全已录族复认，零机制级新差量。
3. 全类型 14 名称对账本班 import 实测复核维持（18 注册表/13 独立在册/
  禅道转 code/5 补充），零删零改。
4. 七专项零新代码缺口：六锚点行号零漂移、lessons=70、catalog=14、
  本机 13/14、禅道关闭态零积压、过时文案零命中——「巡检无产出」稳定态
  延续；06 时班在制品（4 改 2 新）完好零踩踏。

## 待深挖/风险/未验证项（如实记录）

- **待深挖队列 +1**：cc-safety-net 预执行守卫（hook/permission-deny 形态
  嫁接 CLI 启动配置的可行性——交第 2/4 步实证评审）。其余 11 项维持。
- 交拍板 5 件维持：①禅道 poll_enabled=false 是否重开；②cost-xray 观测
  粒度差量；③release_gate 判定面（Ran/OK 汇总校验）；④npm 包体 files
  白名单收紧；⑤单进程 discover 静退根因。
- 未验证项：WebSearch 通道本班 2 发零产出（通道噪声，非「无新竞品」结论
  ——下轮继续串行配发防单通道盲区）；trendshift flight payload 法本班
  缺席（href 法 29 仓全量补位，两法并用惯例维持）；六源市场缓存未刷新
  （刷新属用户「拉取更新」动作）；禅道回写链路 poll 关闭态不触发。
- keywords.md 本班零调整（cc-safety-net 系既有 A1u 词组捞出，无需新词；
  防词库膨胀纪律维持）。

---

*本节系新一轮计划第 1/4 步（08 时班·批1）实录：主扫 127 查询（含 B1u
新锐轮）+雷达 C 全过+WebSearch 串行 2 发（零产出如实记）+全类型对账+
七专项复验。第 2/4 步（深化实证+落地提案）承接本底稿。*

---

# 第 2/4 步（2026-10-08 08 时班续）：七专项深化实证 + 落地件收口实录

> 开局对账：工作区在制品=06 时班第 1-3/4 步件（app.js/i18n.js/
> test_recommend_rules.py/knowledge.md 改 + full-type-review.md/
> test_full_type_improvements.py 新，含 06 时班落地件与本周转节 goal_hint
> 对齐件——同属推荐面一条线，diff 注释已分轮署名）零踩踏承接。本班动作：
> A-G 全项独立复验（行号/本机/数据实测，不引用上班结论）+ cc-safety-net
> 嫁接可行性实证评审（第 1/4 步交办待深挖项收口）+ 本证据节补全
> （knowledge.md 6572 行所引「full-type-review.md 第 2/4 步节」此前缺失，
> 本节即闭环）。五道关+提交推送+发版留第 3/4、4/4 步收口人。

## A. token 节约（八锚点+六辅证全文实读；四对标方向全判既有能力，零当新功能）

- 预算与花费闸：`pipeline.py:613` `_budget_max_tokens`（ENV
  TUTTI_BUDGET_MAX_TOKENS 优先，0=不限）+ `:632` `_budget_cost_caps`（日/月
  硬顶，读台账失败按 0 放行不锁死业务）+ `:648` `_cost_gate_block`（命中
  返 ENV_BLOCK 人话报文，只拦「下一步」）；`_spawn_step` 内 `:677`
  **花费闸先于 token 闸**（钱比 token 更早见顶）→ `:687-701` token 闸。
- usage 累进双路兜底 `:702-746`：压缩灰度路径 `_call` 内 `:721-727` 累进 +
  直通分支 `:740-746` 兜底累进（「默认关闭压缩和 resume 也必须入账，否则
  max_tokens_per_run 是无效设置」注释在位）——§1A 不变量（模型可见即已
  记录）两路都成立。
- 压缩三段式 `compaction.py` 全文：select_range `:51-109`（跳 system 头/
  尾部保留预算/不切断 tool 配对/区域上限）→ compact_region `:119-179`
  （工具结果剪枝→LLM 摘要→surface replace 四件事件，失败仍写 end；
  **台账入账** `:153-166` raw/saved 进 usage 统计，压缩消耗不再隐形）；
  触发 `:182-201` maybe_compact（压力比 ≥ 阈值）。灰度开关
  `pipeline.py:231` `_compaction_enabled`（ENV/设置页任一，默认关）；
  微调 `step_runner.py:31` `_v2_compaction_tuning`（None=未配置与 0=不保留
  语义分离）。撑爆→压缩→守门重试 `step_runner.py:90-` execute_step。
- 事前预检 `step_runner.py:28` `_PRECHECK_RATIO=0.9` + `:75-89`（最近一次
  上下文锚点 vs 本步模型容量，超阈先压再发）；口径分离锚点
  `token_meter.py:129` `last_context`（单次真实上下文体量，预判用）≠
  `:101` `used()`（窗口累计，响应式闸用）——freebuff 借鉴在位；
  `:84` cached 单独记录不入 used（缓存读不占压力）。
- 分层上下文降级 `pipeline.py:2538` `_shrink_context_block`（经验库→4K →
  模块库按「## 」边界 → 圣经按二级标题边界；大纲/前情永不动）+ `:2581`
  `_serial_shrunk_block`（连载重试封装，降级说明进 step note 可对账）。
- 经验召回 `skills.py:676` `block_for`：stable_order `:681-683`（按 id 排序
  保前缀缓存，hits 中途变化不打碎字节稳定性）+ 预算纪律 `:685-688`（
  wildcard 单包限额+教训保底）+ karma 标注 `:727-729`（won≥2 才标，run 内
  稳定）+ 精确归因 `:761` note_outcome（None 三态不回写，防整批误伤）。
- 会话复用 `sessions.py:1-18` 五源扫描（codex/claude/qwen + opencode/mimo
  SQLite 只读 mode=ro）——「在已有会话上继续」选择面。
- diff 评审 `pipeline.py:919-945` CODE_REVIEW_PROMPT（diff 为主要依据+允许
  只读核对+**findings 锚定文件：行号证据**，pr-af 借鉴在位）+ `:948`
  `_git_diff`（拼未跟踪新文件防半盲评）；半成品收工提醒 `:934-935`。
- **四对标方向判定（全部既有能力）**：①prompt 缓存=stable_order 前缀
  友好排序已在位（skills:681）；②语义缓存=历班拍板件维持不扩（纪律：
  不另扩缓存）；③diff-only 评审=v0.1.86 满配（:919-921）；④廉价模型
  分流=`dispatch.py:14` TYPE_DIMENSIONS（18 型分维）+`:33` _KIND_AFFINITY
  +`:44` _TIER_SCORE（easy/default/hard ×budget/standard/premium 价位分）
  +`capability.py:100` cascade_reorder（FrugalGPT 便宜优先，opt-in，消费端
  `modelhub.py:2280` cascade_enabled 接线）已在位。| 巡检·A

## B. 插件市场（六源+三层闸实读；候选三问过筛，零新接入）

- 六源 `market_remote.py:50-76` 实读在位（zcode/anthropic/anthropic-skills/
  claude-skills/clawhub/cocoloop；镜像优先 urls 按序试）。
- SSRF 网关 `:113` `assert_public_url`（https-only+getaddrinfo 全部解析 IP
  逐一拒绝环回/私网/链路本地/保留/组播/未指定）+ **逐跳重定向复检**
  `:156-181`（每跳发起前重过闸，上限 3 跳）——本班实读确认。
- 体量帽 `:93-98`（清单 5MB/下载 80MB/解包 120MB/500 文件/单文本 512KB/
  总文本 4MB）；剥离式安检 `:568` `inspect_tree`（可执行/脚本/钩子/命令/
  agents/.mcp.json **剔除非拒绝**，纯文本图片保留；白名单 `:587` 收敛
  repo 来源防同仓他插件混入）。
- 安装通道 `market.py:267` `install_files`：装前静态扫描 skill_scan（:281，
  SkillSpector 借鉴）+ 内容突变对账（:289-293，卸载重装也骗不过）+
  typosquatting 近似名（:294）+ 装后冒烟（:341，重走 skills 解析链证明
  可用）全在位。
- 候选三问（重合度/可直读性/用户会搜吗）：cc-safety-net=CLI 守卫工具
  非 skill 包（不接，机制面走 A/runner 借鉴评审，见下节）；08 时班批1
  其余新面孔（agetor/ODS/macro/opencorvus/agentos）全为平台/框架非包。
  **零新接入**；缓存仍 10-03 快照（刷新属用户「拉取更新」动作，巡检不
  代触网）。| 巡检·B

## C. 任务类型（18 型三面独立复验；goal_hint↔规则对齐件在制品复验）

- `flows.py:36-134` BUILTIN_FLOWS=**18** import 实数（本班独立跑）；
  18 name+18 note+18 goal_hint=**54 串 i18n.js 全有键**（本班 node 脚本
  实测 missing=0）。流程参数逐项核对：threshold 通用 7.0、bid_doc 7.5、
  serial_novel 8 章×2500 字（`_norm_serial` 钳位 2-20 章/500-8000 字）；
  rubric 上限 8 维（:195）。rank_scan note「四平台」↔ `paihang.py:97`
  _SOURCES（七猫/番茄/起点/纵横）一致；defect_retro「三视角」管线在位。
- 调度维度 `dispatch.py` TYPE_DIMENSIONS 18/18、`usage.py:873`
  _DURATION_BASELINES 18/18 维持（历班对账件）。
- **goal_hint↔推荐规则对齐件（本轮落地，在制品）复验**：app.js
  recommendTaskType 规则表现 18 条——06 时班补 rank_scan/defect_retro
  两型+本节对齐 4 词组（presentation「演示场合」/serial_novel
  「女频|男频|签约平台」/article「头条|知乎」/research 裸「调研」），
  照占位提示输入即命中（test_recommend_rules 样例 4 条锚定）。
  **固有边界如实记**：email/novel 的 hint 无特征词（「给谁写、要达成
  什么」「小说|故事」已部分覆盖），不硬凑词防误荐。
- 三面结论：菜单名/流程参数/辅助信息 18/18 零缺口（对齐件落地后）；
  direct 之外 17/17 推荐规则全覆盖（_RULELESS={direct} 锁定）。| 巡检·C

## D. 经验库（70 条独立统计；偏科维持记录不批量改）

- `data/skills.json` lessons=**70** 本班独立统计：流程规范 30（**43%**）/
  节奏爽点 16/情节逻辑 10/人物塑造 7/一致性 4/文笔风格 3；scope
  serial_novel 41/code 12/*/12/direct 4/article 1——与 06/08 时班基线
  零漂移。
- 去重/并类通道在位（v0.1.89 并类 8→3、v0.1.91 蒸馏守卫、v0.1.94 幂等
  重放）；周整理自动任务 `automation.py:809` weekly-experience-cleanup
  在位（合并重复教训/按主题归纳/标记过时）。本班零新蒸馏（「词形盲区/
  denylist 甄别线」属调研方法论，按 knowledge.py:4 分工边界留 borrow-log
  面）。流程规范偏科 43% 维持记录，未验证不批量改。| 巡检·D

## E. 新 CLI 接入（14 条目独立探测；候选零盲接）

- `catalog.py:32-215` DEFAULT_CATALOG=**14** import 实数；本机 which 独立
  探测 14 个 detect 名：**13/14 在装**（codex/claude/opencode/qwen/aider/
  kimi/mimo/grok/pi/dsh/gemini/cbc/trae-cli 全 OK；openclaw MISSING——
  与历班一致零漂移）。
- 候选 DeepSeek-Reasonix/FuXi/Gitlawb/zero/Empryo which 全 MISSING（本班
  重跑探测）→ 待验证队列维持，不盲目录接入防死链；Reasonix 首位待本机
  实装后再评。| 巡检·E

## F. 禅道集成（config/链路/回写全实读；未触生产动作）

- `data/zentao.json` 实读（config 层）：poll_enabled=**False**、
  interval_hours=2（`load()` :269-279 legacy 迁移路径，属设计非漂移）、
  auto_resolve/auto_merge/triage_ai 全 True、product_profiles 1 条
  （product 96，backend→E:\GitLab\cbc\mo-so，module_routes 空=AI 兜底
  路由在位）、claims={} **零积压**、last_error 空、last_scan 停
  2026-09-21 20:43:44+next_scan 同步停摆（关闭态非故障，18 天）。
- 调度链全实读：`automation.py:581` _tick→`zentao.py:2470` fire_due
  （自节流，poll off 零开销返回）→ `:2425` _poll（poll_disabled 早退/
  not_due 节流/`:2439` 法定节假日顺延闸）；手动闸 `main.py:1781`
  /api/zentao/scan→`:2479` scan_now（绕过轮询闸）；启动补对账 `:2484`
  _boot_reconcile（3s Timer，防死窗漏对账 #27697）。
- 回写链路锚点：`:1708` _ensure_resolved（幂等 resolve+operations 台账
  begin/confirm/finish_exception 全闭合）、`:1729` _transfer（空 target
  先 GET 带回指派人，防 #27783 PUT 清指派——宁不评论不盲写）、`:1754`
  _notify 群通知失败静默不拖业务。
- 纪律遵守：本班未调 scan_now、未 resolve 任何工单；poll 重开与否维持
  交拍板（用户配置不擅动）。竞品面第 33 例复认零新（禅道 npm 通道），
  自动修复集成面维持独占。| 巡检·F（交拍板 1 件维持）

## G. 产品巡检（文案/链接/流程一致性独立复验；零新毛病）

- 「13 种/13种/单源」grep README+app/ui 三件（app.js/i18n.js/index.html）
  **零命中**（本班独立跑，历班多轮修正覆盖维持）。
- README:135「18 种任务类型」清单与 BUILTIN_FLOWS 18 逐一吻合；README:242
  「禅道工单是自动转成 code 修复任务的集成入口，不另占菜单类型」与
  zentao.py 实现（F 节链路）一致。
- 落地件在位实读：app.js 规则表（:840-871，含 06 时班两型+本节 4 词组）+
  建议弹窗文案按 engine 分流（:3128-3138，快档目标如实说「专用数据链路
  与产出模板」不虚标门禁）；预算设置路径（index.html→NS_LABEL schema
  驱动渲染→pipeline:613/648 消费端）闭环维持。零新毛病。| 巡检·G

## cc-safety-net 嫁接可行性实证评审（第 1/4 步交办待深挖项收口）

- **现状锚点（实读）**：`runner.py:1443-1477` claude 无头分支——
  readonly=true 显式禁工具（评审/规划零工具面，无拦截需求）；readonly=
  false 默认 `--dangerously-skip-permissions`（2026-09-17 用户拍板：
  acceptEdits 曾致无人值守 Bash 一律拒绝→模型谎报受限躺平；env
  TUTTI_CLAUDE_PERMS=acceptEdits 可收回）；`:1463-1475` 已有临时
  `--settings` 注入通道（现只注 env 段）。
- **差量判定**：真实——写路径（code 任务实现步）工具调用级零拦截，
  cc-safety-net 针对的正是这个面（运行前拦破坏性 git/文件系统命令）；
  落地通道已在位（--settings 可扩 `permissions.deny` 规则）；denylist
  形态（只拦 `rm -rf /`、`git push --force` 级破坏命令，不拦普通 Bash）
  与当年 acceptEdits 全拦 Bash 语义不同，误伤面可控。
- **评审结论：不改码交确认**——权限默认值变更必须人批（任务纪律：安全/
  权限风险写入候选交人确认；且 2026-09-17 已有用户拍板在先，不得擅翻）。
  待深挖 1 项收口转**交确认 +1**。| 评审·cc-safety-net

## 落地件与本班验证

- **落地件（在制品，本班复验）**：goal_hint↔推荐规则对齐（app.js 规则表
  4 词组+test_recommend_rules 样例 6 条）+ 06 时班件（rank_scan/defect_retro
  规则+文案按引擎分流+_RULELESS 收敛）。变更清单与前后行为见 06 时班第
  2/4 步节与 knowledge.md 08 时班续节（已归档），本班不重复展开。
- **已知取舍（如实）**：①research 宽词「调研」对非报告类目标（「调研一下
  同事背景」）会弹一次建议（可拒绝，会话内 typeSuggestionDone 仅一次）；
  ②「排行榜」宽词末位兜底同型取舍（测试锚定）；③defect_retro 与 code 的
  bug 词顺序依赖（测试锚定防回退）。无并发/权限/迁移/公共 API 面。
- **本班实测**：py_compile（两测试文件）+ node --check（app.js/i18n.js）
  OK；test_recommend_rules **3/3** 绿、test_full_type_improvements **4/4**
  绿、邻接守卫 test_full_type_round+test_borrow_iteration+
  test_i18n_key_coverage 全 OK（独立重跑）。逐模块全量绿留第 3/4 步
  （惯例：单进程 discover exit 0 不作凭据，按 f16e4d1 逐模块纪律）。

## 交确认/未验证项（本班口径）

- 交拍板 **6 件**：①禅道 poll_enabled=false 是否重开；②cost-xray 观测
  粒度；③release_gate 判定面（Ran/OK 汇总校验）；④npm 包体 files 白名单
  收紧；⑤单进程 discover 静退根因；⑥**cc-safety-net denylist 嫁接**
  （新增，本节收口转确认——批准后落地形态：runner.py:1467 ant 注入点扩
  permissions.deny，opt-in 开关+默认行为零变化）。
- 未验证项维持：六源市场缓存未刷新（用户动作）；禅道回写链路 poll 关闭
  态不触发真实链路（无实例权限）；B 专项候选零包内容级下载检查（零候选
  过三问，无需下载）。

---

*第 2/4 步完（08 时班续）：A-G 独立复验全落行号/本机/数据级证据；落地件
在制品复验绿；cc-safety-net 待深挖项收口转交确认。第 3/4 步（逐模块全量
+五道关+提交推送）与第 4/4 步（发版+归档）承接。*

---

# 第 2/4 步（2026-10-08 08 时班续）：七专项巡检深化实证 + 落地件

> 承接 08 时班第 1/4 步底稿（批1 调研），本步为计划第 2/4 项：A-G 逐项落到
> 真实文件行号与本机实测；本轮证据选出落地件 1 件（C 专项：goal_hint 引导词
> 与推荐规则对齐——同型缝隙第 3 例），变更清单先行、代码后落、验证随附。
> 工作区 06 时班在制品（4 改 2 新）零踩踏，本次改动全部为增量追加。

## A. token 节约（八机制锚点复验 + 四对标方向判定，零当新功能）

- 锚点全数在位本班独立 grep 实测：`pipeline.py:61 _ensure_budget`/
  `:613 _budget_max_tokens`（ENV 优先）/`:648 _cost_gate_block`（:677
  花费闸先于 token 闸）/`:703 _compaction_enabled` 撑爆→压缩→守门重试/
  `:948 _git_diff`+`:1019 _review_depth_note`（diff 量级分级评审投入）/
  `:2538 _shrink_context_block` 四层降级+`:2581 _serial_shrunk_block`
  （知识库块与大纲/前情永不动）/`:231 _compaction_enabled`；
  `step_runner.py:28 _PRECHECK_RATIO=0.9` 事前门（:80 判定 + :86 压缩提示）；
  `token_meter.py:101 used()` 窗口累计 ≠ `:129 last_context` 响应式口径分离。
- 六机制辅证：cascade=`capability.py:100 cascade_reorder`（tier 升序稳定
  重排+`dispatch.py:175 rank_model_entries` 精排，pipeline:1526 opt-in 门）；
  经验召回=`skills.py:676 block_for`（:718 stable_order 保前缀缓存，:412
  `_title_containment` 去重、:516 merge_lessons 并类）；会话复用=`sessions.py`
  五源扫描（:68 codex/:124 claude/:183 opencode/:241 qwen/mimo，:298 scan
  单入口）；廉价模型分流=`dispatch.py:12 TYPE_DIMENSIONS`（18/18 实数）+
  `:33 _KIND_AFFINITY`+`:44 _TIER_SCORE`（easy/default/hard ×budget/
  standard/premium）+`:60` 维度回退。
- **四对标方向判定（全部既有能力，非本轮新功能）**：prompt 缓存=注入
  stable_order 前缀友好+usage cached 细分记账在位；语义缓存=历班拍板件
  维持不扩（纪律：不另扩缓存）；diff-only 评审=v0.1.86 满配（:919/:948/
  :1019 三锚点实读）；廉价分流=TYPE_DIMENSIONS+cascade 满配。**零新差量，
  不再加缓存/重试/监控**。| 巡检·A

## B. 插件市场（六源+闸门实读；候选三问过筛零新接入）

- 六源 `market_remote.py:50-76` 实读在位（zcode/anthropic/anthropic-skills/
  claude-skills/clawhub/cocoloop），`data/market_remote/` 六缓存文件全在
  （10-03 快照，刷新属用户「拉取更新」动作不代触网）；分页参数 :80-87。
- 闸门三层实读：SSRF 网关 `:113 assert_public_url`（https-only+全 IP 解析
  拒环回/私网/保留/链路本地/组播，`:159` 重定向逐跳复检、`:839` git 克隆
  重定向风险注释在位）；体量六帽 `:93-99`（清单 5MB/下载 80MB/解包 120MB/
  文件 500/单文本 512KB/文本总量 4MB+重定向 ≤3）；纯技能白名单 `:568
  inspect_tree`（脚本/钩子/MCP 剥离、可执行/未知类型剥离、白名单外目录
  不参与）。安装通道 `market.py:267 install_files` 在位。
- **候选三问过筛**：cc-safety-net=CLI 守卫工具非六源 skill 包（机制面走
  runner 借鉴，见交确认）；08 时班其余新面孔（agetor/ODS/macro/opencorvus/
  agentos）全为平台/框架非包。三问全不过，**零新接入、零蒸馏新条**
  （甄别线已由 inspect_tree 结构化）。| 巡检·B

## C. 任务类型（18 型对账 + 发现 goal_hint↔规则对齐缝隙第 3 例→本轮落地件）

- `flows.py` BUILTIN_FLOWS=**18** import 实测，18/18 engine 齐备（direct/
  code 系直连与动态编排，其余 16 型 review 门禁链），note 与实现一致
  （rank_scan note「四平台」=paihang `_SOURCES` 七猫/番茄/起点/纵横；
  presentation note「不生成 PPT 二进制」如实）。
- `dispatch.py TYPE_DIMENSIONS` **18/18** dims_missing=[] 实测（调度对账
  零漂移）；i18n **54 串**（18 name+18 note+18 goal_hint）脚本实测
  **missing=0**。
- **缝隙（同型第 3 例，本轮落地）**：recommendTaskType 规则词与 goal_hint
  引导词失同步——presentation hint「演示场合」/serial_novel hint 例句
  「都市女频…可签约平台」/article hint「公众号/头条/知乎」（头条、知乎
  不在词表）/research hint「调研什么问题」（裸「调研」不在词表）。
  照占位提示输入 direct 档不弹类型建议，旁路流程门禁/专用链路。前两例：
  video_script 补词（v0.1.95）、rank_scan/defect_retro 补规则（06 时班）。
  已知不可修清（如实记录不硬凑）：email hint 无特征词、novel hint
  「题材/篇幅/风格」与 novel/serial_novel 边界固有模糊——维持现状。
  | 巡检·C

## D. 经验库（lessons=70 零漂移；偏科维持记录不批量改）

- `data/skills.json` 本班独立统计 lessons=**70**：流程规范 30（43%）/
  节奏爽点 16/情节逻辑 10/人物塑造 7/一致性 4/文笔风格 3；scope
  serial_novel 41/code 12/* 12/direct 4/article 1——与 06/08 时班基线
  零漂移。六类闭集枚举 `skills.py:46 LESSON_CATEGORIES`；去重 `:412
  _title_containment`+`:448` 近重阈值；并类 `:516 merge_lessons`；蒸馏
  守卫与分类回退 `:427`（非法分类退关键词映射）。本轮调研产出属
  borrow-log 面不入 lessons（knowledge.py 分工边界维持），**零改运行
  数据**。| 巡检·D

## E. 新 CLI 接入（catalog=14 + 本机 13/14；候选零盲接）

- `catalog.py:32 DEFAULT_CATALOG=**14** 条目`（codex-cli/claude-code/
  opencode/qwencode/aider/openclaw/kimi-code/mimo-code/grok-build/pi/
  deepseek-harness/gemini-cli/codebuddy/trae-agent）。
- 本机 which 实测 **13/14 在装**（openclaw 缺，历班一致）；候选
  reasonix/fuxi/gitlawb/zero/empryo **全部 MISSING**——维持不盲接防死链，
  待验证队列原样。| 巡检·E

## F. 禅道集成（关闭态零积压；回写链路锚点在位；未触发生产动作）

- `data/zentao.json` 实读（config 嵌套结构）：`poll_enabled=false`
  （用户配置不擅动）、`claims={}` **零积压**、`last_error` 空、
  `last_scan` 停 2026-09-21 20:43:44（17 天未扫，关闭态非故障）、
  auto_resolve/auto_merge/triage_ai 全 true、product_profiles 1 条
  （product 96，backend→E:\GitLab\cbc\mo-so，module_routes 空=AI 兜底
  路由）、interval_hours=2 走运行期迁移路径。
- 链路锚点实读：`zentao.py:80 _SCAN_LOCK` 扫描单飞、`:223 _migrate_legacy`
  +`:269-279` interval_hours→interval_minutes 迁移、`:1708 _ensure_resolved`
  （幂等 resolve）、`:1729 _transfer`（空 target 先 GET 带回指派人防清指派）。
- **未触发真实扫描/评论/resolve/群通知**（无实例权限+纪律）。交拍板维持
  （是否重开轮询由人定）。| 巡检·F

## G. 产品巡检（文案/链接零新毛病；落地件见下）

- 过时文案 grep（README+index.html+app.js+i18n.js）：「13 种」「单源」
  **零命中**；README:135「18 种任务类型」/:242「禅道工单是自动转成」与
  实现一致；06 时班落地件（rank_scan/defect_retro 规则+按引擎分流文案
  app.js:847/:864/:3128）在位实读。| 巡检·G

## 落地件：goal_hint 引导词与推荐规则对齐（4 词组 + 6 测试样例）

**变更清单（写码前定稿，已照单落地）**：
1. `app/ui/app.js` recommendTaskType 规则表 4 行：presentation 补
   「演示场合」（窄词防「演示一下函数」误荐）；research 收敛为
   `/(调研|深度研究)/i`（调研报告/竞品调研/市场调研均为「调研」超集，
   行为零缩窄）；serial_novel 补「女频|男频|签约平台」（网文域专属词，
   规则序在 novel 前不截胡短篇）；article 补「头条|知乎」（hint 明示
   平台词；知乎类调研问句由前置 research 的「调研」先接住）。
2. `tests/test_recommend_rules.py`：正向样例 4（演示场合/女频签约/
   头条/调研）+防误荐 2（「调研这个bug」bug 词先于 research 仍归 code；
   「演示一下函数」不荐 presentation）；docstring 记第 3 例缝隙。

**改动前后行为**：前——照 goal_hint 引导词输入（如「演示场合：发布会」
「都市女频 20 万字可签约平台」「写篇头条文章」「调研一下 X」）在
direct 档零建议直接快档直答，16 型 review 门禁与专用链路被旁路；后——
弹类型建议确认框，一键切换走门禁链/专用管线，拒绝维持 direct。
gated 流程既有建议路径零变化，i18n 零新串（纯正则改动）。

**覆盖类型**：presentation（演示文稿）/serial_novel（连载）/article
（自媒体文章）/research（调研报告）四型的 hint 引导路径；其余 14 型
靠既有样例+防误荐回归锁序防漂移。

**验证方法与结果**：node --check app.js OK；py_compile 过；
test_recommend_rules 3/3 绿；邻接守卫（test_borrow_iteration 9/
test_full_type_improvements 4/test_full_type_round/test_i18n_key_coverage/
test_i18n_dups）全绿；全量逐模块 discover **270/270 全绿**。

**风险（如实）**：①「知乎/头条」宽词使「写篇知乎体回答」类边缘目标
弹一次建议（可拒绝，会话内仅一次 typeSuggestionDone）；②research 规则
收敛改写（行为超集非缩窄，已有测试锚定）；③「签约平台」依赖用户照
hint 输入，自发措辞（如「投稿起点」）仍不荐——词形边界如实记录不追。
无并发/权限/迁移/公共 API 面。

## 交人确认 / 未验证项（新增 1 件，其余维持）

- **交确认（本轮新增第 6 件）**：cc-safety-net 预执行守卫借鉴——落地
  形态=claude CLI 启动参数挂 permission-deny 清单（拦 `rm -rf /` 类破坏
  命令），但 runner.py:1456-1462 默认 `--dangerously-skip-permissions`
  系 2026-09-17 用户拍板（acceptEdits 曾致模型谎报受限躺平）；改权限
  默认值属权限面变更，**交人确认后再动**，本轮只入队列不动码。
- 交拍板 5 件维持：禅道轮询重开/cost-xray 观测粒度/release_gate 判定面/
  npm 包体 files 白名单/单进程 discover 静退根因。
- 未验证项维持：六源市场缓存 10-03 快照未刷新；禅道回写链路 poll 关闭
  态不触发；pypi 仅在架验证；email/novel 两型 hint 无特征词不可荐属
  已知边界。

---

*第 2/4 步完：A-G 深化实证全落行号/实测证据；落地件 1 件（2 文件，
270/270 全绿）；交确认 +1（cc-safety-net 权限面）。第 3/4 步（五道关+
提交推送+发版）承接。*

---

# 第 3/4 步（2026-10-08 08 时班续）：落地件验证收口 + 全类型兼容性实测

> 承接上节在制品，本步不重做已落改动：核实落地件完整在位、复跑受影响
> 测试、以全部预置类型做配置与流程兼容性验证、收敛变更清单。五道关+
> 提交推送+发版留第 4/4 步收口人。

## 变更清单收敛（实际动到的文件，工作区实核）

- 第 2/4 步已落、本步零再动：app/ui/app.js（规则表 4 词组 ：843/:856/:860/:862
  + 建议文案按 engine 分流 ：3133-3135）、app/ui/i18n.js（新文案键 1 条 EN
  :150）、tests/test_recommend_rules.py（样例 6+防误荐 2）、
  tests/test_full_type_improvements.py（本轮专属 7 件：06 时班 4+对齐 3）、
  docs/borrow-log/knowledge.md（沉淀）。
- 计划候选 flows.py/pipeline.py/style.css **未动**：落地件系纯正则+文案
  分流，不涉及后端流程与样式，无验证价值的候选不强行落地。

## 本步实测（逐项）

- 关①：py_compile（两测试文件）+ node --check（app.js/i18n.js）OK。
- 新件 test_full_type_improvements **7/7 绿**（hint↔规则同源对账/
  research 改写超集回归/对齐词首中顺序防截胡/两型 direct 引擎锁定/
  分流文案接线/宽词取舍锚定）。
- 邻接守卫：test_recommend_rules 3 + test_borrow_iteration 9 +
  test_full_type_round 8 + test_i18n_key_coverage 3 + test_i18n_dups 3
  = **26 绿**。
- **全部预置类型兼容性验证（本步独立跑）**：`BUILTIN_FLOWS` import 实数
  **18**；`TYPE_DIMENSIONS` 18/18 dims_missing=[]；engine 三值
  （direct/code/review）齐备；i18n 18 name+18 note+18 goal_hint=**54 串
  missing=0**——13+5 全类型菜单/参数/辅助信息零缺口，推荐面
  direct 之外 17/17 全覆盖（_RULELESS={direct}）。
- 逐模块全量 **270/270 实跑全绿**（本步复跑，含扩充后新件；单进程
  discover 静退根因仍待拍板，逐模块计数为准）。

## 未实施风险（维持上节三条，零新增代码风险）

①「知乎/头条」宽词使边缘目标弹一次建议（可拒绝，会话内仅一次）；②
research 规则收敛改写（行为超集，测试锚定）；③「签约平台」依赖用户照
hint 输入，自发措辞不荐属词形边界。交拍板 6 件维持。

---

*第 3/4 步完：落地件验证收口（新件 7/7 + 邻接 26 绿 + 全类型兼容性
18 型/i18n 54 串零缺失）；变更清单收敛为实际 5 文件。第 4/4 步（五道关+
提交推送+发版+当日归档）承接。*
