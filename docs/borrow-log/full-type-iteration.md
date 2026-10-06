# 全类型迭代工作底稿（2026-10-06 19 时班·新一轮计划第 1/4 步）

> 本文件系 19 时班全类型调研底稿（实际执行日 2026-10-06，19:01 开工），最终归入执行日日报。
> 元信息：hour=19，19%7=5 → 轮换批5（检索/知识/浏览器）；分支 main（ac15ee1，v0.1.88 已发版）；
> 工作区在制品=15/16 时班 docs 四件+16 时巡检班提案 1/2 落地件（pipeline.py +59 行、
> tests/test_full_type_round.py +71 行、knowledge.md 增量等，均未提交）——本班未触碰。
> 通道：gh api 认证可用，串行 sleep 4s 纪律，全程零 403 零 FAIL（DONE 115 / errors 0 实证）。
>
> **并行班补记**：18 时班（批4，18:38 开工）系与本班并行的调研班，其 knowledge.md
> 节在会话期间落盘（knowledge.md 4449→4562 行）；其底稿自称见本文件「18 时班」节，
> 实际该节未落盘（本文件只含 19 时班底稿全文，18 时班细目以 knowledge.md 4451-4562
> 行节为准）。本班记录已对其去重对齐：dsh 生态信号编号顺延、oh-story/禅道/pypi/
> lessons 增量改复认口径、存量复查基准改 vs 18 时班。

## 一、主扫（脚本 borrow_scan_nightly.py 无参全量）

115 查询 = A 常驻 89（A1-A13 共 78 组，含内置 B1 11 组）+ B5 轮换 10 + A1u/A1p2 双轮 16。
产出 524 行、463 唯一仓；过筛（全名+简称双通道对 docs/borrow-log/ 全历史）：
**313 已录 / 150 首见**（首见以域外噪声与微型件为主，见 §四）。

### A 常驻组逐组头部（stars，本班实测；已录状态除非注明首见）

| 组 | 头部命中（top3） | 判定 |
|---|---|---|
| A1 核心编排（8+双轮16） | obra/superpowers 295,807 · mattpocock/skills 277,430 · affaan-m/ECC 273,919；A1u 新锐轮捞 pipelock 921/cezar 502（首见，见 §四） | 已录族稳定 |
| A2 扩展形态（7） | superpowers/ECC/opencode 211,948 | 已录族稳定 |
| A3 token 节约（4） | img2threejs 17,570 · FlashML-org/FreeToken 14,218 · TokenRhythm/opensquilla 7,087；**首见 rtk 82,496 走 trendshift 通道**（见 §四） | A3 域有新面孔 |
| A4 新 CLI（3） | anthropics/claude-code 149,565 · openai/codex 128,003 · earendil-works/pi 112,846 | 已录族 |
| A5 舰队/形态（4） | stablyai/orca 86,183 · trycua/cua 28,324 · OpenSandbox 15,690 | 已录族 |
| A6 评测/观测/协作（6） | tldraw 50,777 · claude-code-router 37,566 · ChatDev 34,451 | 已录族 |
| A7 写作场景（8） | funNLP 83,709 · Some-Many-Books 24,355 · **zenstory-ai/oh-story-claudecode 7,289（18 时班已勘定改名真身，本班独立复证+顺掘端口扩散家族，见 §五）** | 写作域稳定+复证 1 件 |
| A8 prompt/网关/质量（5） | aklivity/zilla 1,711 · one-api-pro 1,000 · anythingmcp 942 | 域平稳 |
| A9 产品配套（6） | crewAI 59,390 · CowAgent 47,241 · planning-with-files 27,303 | 已录族 |
| A10 全类型写作/对话/代码（10） | nanobot 48,815 · gpt-researcher 29,927 · planning-with-files 27,303 | 已录族（deep research 面 B5 重复验证一致） |
| A11 项目记忆与档案（5） | segmentio/myth 4,285 · DevoxxGenieIDEAPlugin 684（首见微型）· semver.org 570 | 域平稳 |
| A12 发布与平台（5） | bony-agent 16（微型）——域整体沉寂 | 域平稳 |
| A13 面板/用量/守卫（7） | live-panel-skill 610 · claude-code-monitor 310 · tlive 214 | 域平稳 |

### B5 轮换批逐词（10 词，本班主扫域）

| 查询词 | 头部命中 | 判定 |
|---|---|---|
| agent rag | firecrawl 189,045 · ragflow 91,724 | 已录族 |
| deep research agent | gpt-researcher 29,927 · Alibaba-NLP/DeepResearch 20,012 · dzhng/deep-research 19,761 | 已录族 |
| browser use agent | invisible_playwright_mcp 31,777 · BrowserSkill 8,191 · moli 10,251（trendshift 放量续） | 已录族 |
| web scraping agent | firecrawl · oxylabs-ai-studio-py 3,398（**已录 oxylabs 同族商业抓取客户端新仓**，合规边界不变） | 同族增量 |
| search/retrieval agent | Agent-Reach 92,322（放量续）· dify 157,945 · langchain 147,492 | 已录族 |
| document understanding agent | landing-ai/ade-cli 2,418 · sparrow 5,224 | 已录族 |
| data extraction agent | sparrow · quant-mind 3,043 | 已录族 |
| competitive intelligence agent | 域内无大星新面孔（trendshift 候选 bcefghj/competitiv… 系 16 时班已录微型） | 域平稳 |
| citation verification agent | agentic-rag-verified-citations 1★ · Rag-Agent-with-Citation-Grounding 0★ | 微型域 |
| knowledge base quality / rag evaluation | 无大星新面孔 | 域平稳 |

**批5 域结论：稳定期延续，零机制级新差量**（与 16/18 时班批2/批4 域结论同型——各域轮过均稳）。

### A2 词形补查（手工 1 发，脚本缺口本班收口前最后复跑）

digital+employee+ai+agent：StaffDeck 1,967 居首复认（18 时班同证）；18 时班新入库
zeenie-ai/OpenCompany 983★ 复认；微型新生件 bytefolk/digital-employee 21★/
ai-openclaw-skeletons 74★/lemonclaw 54★——零新大件。脚本 A2 词形同步见 §八。

## 二、雷达源 C 逐源

| 源 | 结果 | 状态 |
|---|---|---|
| awesome 清单 18 源 | 17 alive 零 archived：vivy-yi 77 / **ComposioHQ/awesome-claude-skills 76,575** / ai-boost 4,721（10-06 push）/ punkpeye 95,864 / bradAGI 1,317 / Shubhamsaboo 140,843 / hesreallyhim 55,137 / VoltAgent agent-skills 35,261 / davepoon 3,593 / RUC-NLPIR 1,062 / TeleAI-UAGI 658 / caramaschiHG 1,922（停更观察维持）/ vijaythecoder 4,389（停更维持）/ TsinghuaC3I 665 / Engineering4AI 288 / VoltAgent papers 1,821 / mattpocock/skills 277,439 | 全过 |
| 属主勘定 | **wshobson/awesome-claude-skills 404 → 正主 ComposioHQ/awesome-claude-skills**（16 时班「ComposioHQ 76,571」系此仓，+4）；**awesome-ai-agents 正主勘定 = e2b-dev/awesome-ai-agents 30,276★**（in:name 一次命中，此前知识库未录属主） | 勘定 2 件 |
| Trending | 直抓未行（历班拦截在案）；**trendshift root 200 可达、/repositories 路径 404（改版），根页仍捞 30 仓**；候选 25 件 repos 端点逐一实证（见 §四） | 补位过 |
| topic 8 页 | multi-agent-orchestration / ai-agents / claude-code / agent-framework / claude-skills（一次 TLS 抖动补跑成功）/ llm-agents / ai-coding-assistant / mcp；候选 agent-swarm 860（852→860）/ atlas 9,193（放量）/ cli-agent-orchestrator 1,389 / ui-ux-pro-max 133,471 / Understand-Anything 85,397 / oh-my-openagent 69,839 / archify 78,407 等**全已录** | 全过 |
| npm 两查 | agent orchestrator：bdb-agent-orchestrator/bizar/nax/agentcraft/garda 等已录族+微型新生件；claude code：官方包+claude-code-router 已录族 | 全过 |
| pypi | 复试：HTTP 200 但返回 3KB CSP 挑战壳页零结果（服务端不渲染）——18 时班已记第七形态，本班独立复试同形复认 | 受阻（复认） |
| 框架周边 | 经 A6/B6 组与 topic agent-framework 覆盖（haystack 26,681/pydantic-ai 20,428 已录族） | 过 |
| 自家 CLI 周边 | DSH 生态：**nmaych/dsh-mobile-connect 0★ 首见**（第 4 扩散信号，见 §四）+ koocmitwho/video-operation-review（适配 Codex 与 DSH 的微型 skill） | 过 |
| 禅道/项目管理周边 | 2 查（zentaophp+OR+zentao+ai / bug+triage+agent）：头部全无关大星件+0★ 微型，**第 21 例零新禅道 AI 竞品**（18 时班记第 20 例，顺延） | 过 |

## 三、WebSearch 串行交叉验证（口径如实记：18 时班已用 2 发重置窗口，本班属提前补位）

1 发 B5 域定向（open source deep research agent 2026）。窗口纪律口径：距 18 时班仅 1 班、
本应跳过；补位理由=18 时班 2 发系批4 域（guardrails/HITL），批5 域自 12 时班后无近期
WebSearch 覆盖存在定向盲区。结果：捞出 Tongyi DeepResearch（=已录
Alibaba-NLP/DeepResearch）/ NousResearch hermes-agent（已录 251,549）/ NinjaTech SuperNinja
（闭源商业产品非开源仓）/ Towards AI 榜文（无新仓名）——**与主扫双通道一致零新面孔**；
「repos 二次实证」规则无需触发新入库。来源：tongyi-agent.github.io、opc.community、
ninjatech.ai、pub.towardsai.net。

## 四、本班新面孔定性（三门槛：重合度/可直读性/用户会搜吗）

| 仓 | stars/pushed | 来源 | 机制亮点 | 对比 | 结论 | 日期 |
|---|---|---|---|---|---|---|
| rtk-ai/rtk | 82,496 / 10-06 | trendshift+README 速读 | 「Rust Token Killer」：hook 改写 Bash 命令（git status→rtk git status）**命令层确定性预压缩**，agent 读入前省 60-90% 输出 token；`rtk gain` 节省看板；16+ agent 适配（claude/codex/gemini/kimi/pi/hermes…） | 我们 compaction 系「结果回来后」剪枝（compaction.py 三段），rtk 把压缩**前移到命令层**、零 LLM 确定性执行；两者互补。非六源直装；CodeBee 编排台侧无可直读落点（属 agent 客户端 hook 层） | **借鉴方向（A3 token：压缩前移）**，是否立项交第 3/4 步评审 | 2026-10-06 |
| addyosmani/agent-skills | 101,666 / 10-03 | trendshift+README 速读 | 生命周期 9 命令 skill 包（/spec→/plan→/build→/test→/review→/ship）；**/build auto 一次批准全程自主**（TDD+逐任务提交+失败暂停）| 与 superpowers/ECC/mattpocock-skills 同族；编排/闸门/任务档案 CodeBee 全有；「按阶段自动激活技能」与经验召回同向 | 已覆盖（族）；好 SKILL 精华可走经验库蒸馏通道 | 2026-10-06 |
| luckyPipewrench/pipelock | 921 / 10-06 | A1u 新锐轮 | agent egress/MCP/A2A 流量防火墙 | B4 治理域旁（批4 轮次再看） | 判据 | 2026-10-06 |
| open-mercato/cezar | 502 / 10-06 | A1u 新锐轮 | 多 CLI 并行编排 ADE（Claude Code/Codex/OpenCode/Pi） | A1 域同形态新锐（63 班以来又一例，面板/编排井喷延续） | 雷达 | 2026-10-06 |
| nmaych/dsh-mobile-connect | 0★ / 10-06 | 自家 CLI 周边 | 手机同 Wi-Fi 直连电脑 DeepSeek Harness | DSH 生态扩散**第 5 信号**（18 时班已记 dsh-market 5,625★ 为第 4；此前 68/152/155 三件）；dsh 已接对应 | 雷达（DSH 生态跟踪） | 2026-10-06 |
| 微型批 | — | 主扫/雷达 | oxylabs-ai-studio-py 3,398（oxylabs 同族）/ corezoid-ai-plugin 72（CC 插件市场）/ DevoxxGenieIDEAPlugin 684（IDE 本地 LLM 插件）/ BruceLanLan/augur 624（本地优先投资研究记忆，B5 域旁）/ guizang-product-video-skill 691（**短视频脚本类型 skill 生态**：复用产品组件做更新宣传片）/ huashu-art-motion 232（艺术动画 skill）/ genpark-video-script-storyboard-generator-skill 9（genpark 族）/ haogetruth/haoge-skills 10 / hiroaqii/gitframe 28 / Meltype 333 / backburner 705（iPhone 协同本地推理，域外）/ StoryForge 1★（Claude Code story runtime 微型） | 微型/域外/同族为主 | 判据 | 2026-10-06 |

## 五、存量复查（repos 端点 30 仓，19:1x-19:3x，29 alive 零 archived；基准 vs 18 时班 18:3x-19:2x 实测，间隔约 10-30 分钟）

| 域 | 增量（vs 18 时班） |
|---|---|
| 头部 | orca 86,173→**86,183（续领跑）**/ superpowers 295,802→295,809 / mattpocock-skills 277,418→277,439（差 18,370）/ ECC 273,909→273,923 / ponytail 156,353→156,373 / claude-mem 96,843→96,856（放量续）/ hermes-agent 251,545→251,549 / opencode 211,944→211,948 / pi 112,842→112,848 / anthropics-skills 179,855→179,854（±0 微抖） |
| 放量族 | **Strata 14,818→14,853（+35 续）** / **rea 6,648→6,710（+62 加速续）** / iFixAi 21,440→21,453 / context-mode 25,511→25,514 / herdr 42,575→42,576 / SkillSpector 19,513→19,514 / open-code-review 43,945→43,950 / nautilus-compass 1,221→1,237（+16 续）/ yomiyasu 1,566→1,568 |
| 记忆/写作域 | beads 27,662→27,663 / agentmemory 29,172 / hippo-memory 772 持平（10-06 push）/ bernstein 1,404 持平（10-06 push）/ gascity 1,329 持平 / DeepSeek-Reasonix 35,741→35,742（E 候选首位）/ webnovel-writer 7,329→7,330 / ainovel-cli 2,100 持平 / drama-skills 2,539 持平 / huobao-drama 15,782 持平 |
| 勘定 | **oh-story 改名勘定独立复证**（18 时班已勘定 zenstory-ai/oh-story-claudecode 7,289★；本班 in:name 独立复现同果）；**本班新掘 oh-story 多 CLI 端口扩散家族**：oh-story-dsh 454★ / oh-story-codex 36★ / oh-story-opencode 7★（写作 agent 多 CLI 移植扩散信号，18 时班未录） |
| 代勘 | **18 时班冻结项 Octopoda 勘定候选**：in:name 首位 RyjoxTechnologies/Octopoda-OS（485★，push 2026-07，名带 -OS 后缀是否正主存疑待其原上下文核对）；piaodazhu/Octopoda 22★ 2024 停更非候选——按「不盲猜定性」纪律记候选不定案 |

增量全部个位数到几十（rea/Strata 双位数续），零状态变更零 archived——**两班独立复测互证**。

## 六、全类型覆盖矩阵（14 目标类型 × 本班证据通道）

| 类型 | 注册 id | 本班证据通道 |
|---|---|---|
| 直接执行 | direct | A1/A2 编排域（全类型底座） |
| 代码任务 | code | B1 内置 11 组（ SkillSpector 19,514/vuls 12,278 已录族）+ addyosmani agent-skills 定性 |
| 小说 | novel | A7 novel 组（oh-story-claudecode 7,289 勘定续算） |
| 连载 | serial_novel | A7 consistency 组+A12 chapter hook（域沉寂零新面孔） |
| 自媒体文章 | article | A7 article 组（域平稳） |
| 调研报告 | research | A10 deep research 组+**B5 主扫域全 10 词**+WebSearch 交叉验证 |
| 短视频脚本 | video_script | A10 video script 组+guizang-product-video-skill/genpark-storyboard skill 生态 |
| 技术方案 | tech_proposal | A11 spec 档案组（myth 4,285 已录族） |
| 翻译 | translation | A7/A10 translation 组（域平稳） |
| 演讲稿 | speech | A7 speech 组（域平稳） |
| 工作汇报 | weekly_report | A10 weekly report 组（域平稳） |
| 商务邮件 | email | A10 email 组（微型新生件为主） |
| 扫榜选材 | rank_scan | trendshift 30 仓+topic 8 页（本班扫榜通道即产出源） |
| 禅道工单 | defect_retro | A13 defect retro 组+禅道周边 2 查（第 20 例零新竞品） |
| 对话（补充） | — | A10 chatbot memory 组（域平稳） |
| 知识库（补充） | — | **本班批5 主扫域**（agent rag/KB quality/rag eval 逐词过） |

「13 种」口径 vs 注册表实数：BUILTIN_FLOWS=**18** import 实测（direct/code/novel/
serial_novel/article/research/video_script/tech_proposal/translation/speech/
weekly_report/email/rank_scan/defect_retro + 4 自研 bid_doc/presentation/doc/resume），
指令 14 个名称全有对应、无遗漏无擅自省略。

## 七、七专项轻量实测（本班只读，工作区在制品态）

- **A token**：`cache_control|semantic_cache|prompt_cache` 全 core grep **零命中维持**；
  _shrink_context_block pipeline :2538（消费 :2582 连载分层降级）/ cascade
  capability.cascade_reorder :1529 锚点在位（行号系在制品 HEAD+59 行态）。四方向判定
  维持：prompt 缓存供应商侧已覆盖 / 语义缓存候拍板 / diff-only 已满配 / 廉价分流已满配。
  本班新增 A3 域参照件 rtk（压缩前移命令层）→ 借鉴方向候选（见 §四）。
- **B 市场**：六源 SOURCES market_remote.py:50 在位（zcode/anthropic/anthropic-skills/
  claude-skills/clawhub/cocoloop 六源名实读命中）；本班候选均非六源可直装技能包，
  三问全不过零接入零绕闸。
- **C 类型**：BUILTIN_FLOWS=18 实测（见 §六）；DEFAULT_CATALOG=14 实测。
- **D 经验**：data/skills.json lessons=**73（复认 18 时班口径 72→73）**，分布：流程规范 27
  （=37.0%）/节奏爽点 21/情节逻辑 10/人物塑造 7/一致性 4/文笔风格 4——16 时巡检班
  提案 2（「竞品勘定必须 repos 二次实证」蒸馏）已入库（18 时班首记，本班独立复测同数）；
  该落地件系工作区在制品（未提交），本班不重复入库。
- **E 新 CLI**：六候选 deepseek-reasonix/fuxi/gitlawb/zero/empryo `command -v` 全
  MISSING——零接入防死链维持（dsh 已接对应，dsh-mobile-connect 系其生态信号）。
- **F 禅道**：data/zentao.json 顶层 poll 键缺失（13 时班勘定口径：config 内层
  poll_enabled=False 显式关）/claims=0/last_error 空/last_scan 停 2026-09-21——
  部署配置缺位多班同口径；产品档案路由在制（16 时巡检班 workdir 实锚）；全程只读
  零触发；禅道周边第 20 例零新竞品。
- **G 产品**：过时文案 grep（「13 种/13种/单源」）i18n.js/index.html/app.js/README
  零命中零新毛病。

## 八、词库与脚本同步

- **scripts/borrow_scan_nightly.py A2 词形补齐**：`ai+employee+OR+digital+worker` →
  `ai+employee+OR+digital+worker+OR+digital+employee`（keywords.md 2026-10-05 21 时班
  已录 StaffDeck 1,967★ 依据；脚本滞后缺口系 16 时班移交本班处置，1 行同步）。
- **keywords.md 本轮零新增词形**：批5 域词组覆盖充分、无改进依据不动（纪律）。

## 九、落地件与发版判定

本班零产品代码件（脚本 1 行词形同步属词库维护非产品代码）：调研沉淀两件
（knowledge.md 19 时班节+本底稿）——**docs-only 不发版**（05/06/01/04/09/10/12/13/
15/16 时班先例）。工作区在制品（16 时巡检班提案 1/2 落地件）非本班产出，不提交不触碰。

## 十、待深挖队列（19 时快照）

21 时快照 11 项全部维持（第 3/10 项保持划掉）。本班新面孔均判据级/雷达级零新队列项；
rtk（A3 压缩前移）作借鉴方向候选交第 3/4 步评审，不入队；18 时班冻结项 Octopoda 本班
给出勘定候选（Octopoda-OS，存疑待核）交回原班对账。风险在档维持（交人拍板）：
data/zentao.json 明文密码；portscan 真实杀进程用例挂死；32 位全量 discover 静默退出；
runner_drain 计时超界。

## 十一、未验证项（如实记录）

pypi 复试受阻（CSP 挑战壳页第七形态）；Trending 直抓未行（trendshift 补位，且
/repositories 路径 404 系改版观察项）；B5 首见微型件 README 机制面深读未行；rtk 与
agent-skills 仅 README 头部速读（未逐 commit/未实测安装）；禅道定时扫描未触发真实
工单验证（不为验证制造真实 Bug）；npm 新生件无下载量核查（历班无此惯例）。
