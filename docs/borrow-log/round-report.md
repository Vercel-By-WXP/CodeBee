# 本轮工作报告（round-report.md · 计划第 1/4 步）

> 开工时刻 **2026-10-10 03:40（UTC+8）**，hour=3，3%7=3 → **轮换批 B3（计划/spec/长任务）**。
> 基线：分支 **main**、v0.1.99（1e6320b）、工作区干净零在制品。本报告系本轮新建（写前确认 docs/borrow-log/ 下无同名文件）。

## 一、目录探索证据（真实路径与关键函数，全部实读核实）

| 关注面 | 真实路径 | 关键事实（本班实测） |
|---|---|---|
| 关键词总库 | `docs/borrow-log/keywords.md` | **A1-A13 共 79 组**（文件头仍写「78 组」已过时）+ 轮换池 B 7 批 + 雷达源 C 全套；本班按实际规则执行 |
| 预置任务定义 | `app/core/flows.py:36` `BUILTIN_FLOWS` | **实测 18 型**（非需求的「13 种」）：直接执行/代码/小说/连载小说/自媒体文章/短视频脚本/文档/翻译/扫榜选材/缺陷复盘/调研报告/演讲稿/演示文稿/工作汇报/商务邮件/技术方案/简历/标书编制；需求枚举 14 名中「禅道工单」**不是预置型**——README.md:236 明确「禅道工单是自动转成 code 任务」，走 zentao 集成 |
| CLI 目录 | `app/core/catalog.py:32` `DEFAULT_CATALOG` | **14 条**（codex/claude/opencode/qwen/aider/openclaw/kimi/mimo/grok/pi/dsh/gemini/cbc/trae-cli）；`ORCH_RESUME_PATCH`/`LAUNCH_PATCH`/`CONFIG_PATCH` 幂等补丁链在位 |
| 插件市场 | `app/core/market.py:56` `BUILTIN_PACKS` 6 包；`app/core/market_remote.py:50` `SOURCES` | **六源在位**：zcode/anthropic/anthropic-skills/claude-skills/clawhub/cocoloop；SSRF 闸 `assert_public_url`（仅 https+私网拒绝）+ 五重体量帽（5MB 清单/80MB 包/120MB 解包/500 文件/512KB 单文）在位 |
| 禅道集成 | `app/core/zentao.py`（2692 行） | `_scan:2332`/`_poll:2403`/`_route_one:2128`/`_finish_ok:1912`/`retry_claim:2281`/`archive_claim:1816`/SSRF `_guard_url:163`；运行态 `data/zentao.json`：**poll_enabled=False、claims=0、last_error 空、last_scan 停 09-21**——关闭态非故障（与 18 时班口径一致） |
| token 八机制锚点 | `pipeline.py:725` `_budget_max_tokens`、`:760` `_cost_gate_block`、`:2704` `_shrink_context_block`；`usage.py:117` cached 记账；`compaction.py:182` `maybe_compact`；`modelhub.py:3689` chat cache_ttl；`skills.py:348` 衰减；cascade（pipeline 级联路由） | 全部在位零漂移（与 18 时班行号一致） |
| 主扫脚本 | `scripts/borrow_scan_nightly.py` | QUERIES=89（A 常驻 78+内置 B1 11）+ 批表 7 批；`--batch N` 补课口；A1 双轮（A1u updated/A1p2 page2）内置 |
| 工作区改动 | `git status --short` | 空（零在制品，docs 外无改动） |

## 二、主扫描覆盖记录（gh api 认证通道，sleep 4s 纪律）

- **116/116 查询全部 ok、零 FAIL、零 403、零 EXC**（progress 逐 Q 实证）：A 常驻 89（含内置 B1 11）+ **B3 轮换 11** + A1 双轮 16。
- JSONL **524 行 → 466 唯一仓**；组分布 A1×40/A1u×40/A1p2×40/A2×35/A3×20/A4×15/A5×20/A6×30/A7×33/A8×25/A9×20/A10×50/A11×25/A12×12/A13×18/B1×51/B3×50。
- 活跃过筛（≥300★ 且 9 月后在更）**158 件全历史（docs/borrow-log/ 全目录）双通道过筛：已录 156、首见仅 2**——主扫面域稳定期延续（与 15/16/18 时班同型，且为历史最收敛班之一）。
- 零回查询 7 条，B3 域内：`requirement+elicitation+agent` **ok 0**（词形弱，与 10-09 报告同型，如实记）。
- **受限项如实记**：后台进程在 Q116 完成后撞 600s 上限被杀，`PROGRESS DONE` 标记未落盘——属进程中止形态非查询失败（与 10-09 报告同型；116 ok 行已实证全覆盖）。

### B3 批域逐词头部（11 词，本班主扫域）

| 查询词 | 头部命中（top3） | 判定 |
|---|---|---|
| long+running+agent | planning-with-files 27,360 · PlanWeave 412 · Comp_Sci_Sem_2 214 | 已录族领跑 |
| spec+driven+development | OpenSpec 71,471 · get-shit-done 64,332 · gsd-2 7,776 | 已录族；**get-shit-done 属主 open-gsd→gsd-build 改名重定向**（GitHub 自动跳转零断链，勘定记） |
| plan+and+execute+agent | PraisonAI 9,214 · TaskWeaver 6,166 · refact 3,534 | 已录族 |
| task+decomposition | DeepResearchAgent 3,557 · spec_driven_develop 986 · claude-swarm 402 | 已录族 |
| milestone+tracking | jettbrains/-L- 161 · genpark 7 | 微型/停更域维持 |
| project+planning | lazycodex 3,750 · AI-Agents-Projects-Tutorials 2,920 · itsaplan 916 | 已录族 |
| autonomous+long+horizon | InternAgent 1,448（**InternScience 新属主**，旧 InternAgent org 勘定） · OpenART 234 | 已录族 |
| worktree+parallel | worktrunk 9,111 · ccpm 8,406 · pro-workflow 2,909 | 已录族 |
| checkpoint/resume | atlas-agent-control-plane 106 · tiger_cowork 62 · CONTINUUM 23 | 微型域维持 |
| acceptance+criteria | 1★/0★ 零星 | 沉寂域维持 |
| requirement+elicitation | **0 命中**（ok 0 实证） | 词形弱，零回如实记 |

**批3 域结论：稳定期延续，零机制级新差量**（批3 两周一轮换，头部与 10-09 班同型）。

## 三、逐类型调研矩阵（18 实际预置型 + 禅道集成全覆盖）

| 预置类型 | 覆盖查询组/通道 | 本班头部与结论 |
|---|---|---|
| 直接执行 | A1/A4/A5 全景 | pi 113,784/claude-code 149,851/codex 128,383 已录族，零新 |
| 代码 | B1 内置 11 词 | 头部全已录（open-code-review 生态词在库）；ARTEX 2,098 攻防冠军已录复认 |
| 小说 | A7 novel/creative/long-form/网文 | oh-story-claudecode 7,390/NovelClaw 378/denova 893 全已录复认，零新 |
| 连载小说 | A7 consistency | sky-flux/skills 10★ 微型域——长文一致性**沉寂域维持**（连载圣经仍独占） |
| 自媒体文章 | A7 article | ai-collab-playbook 453/OmniWriter 157 已录/微型，零新 |
| 短视频脚本 | A10 video + trendshift/topic | huobao-drama 15,898 已录；**baocut 555★ 首见**（AI 视频剪辑代理，见下节）；**hyperframes 59,706★ 首见**（见下节） |
| 文档 | A10 doc gen | beagle 82★ 微型域，零新 |
| 翻译 | A7 translation + A10 translation quality | OpenCreator 12,667/inkos 10,218/YoudaoTranslator 3,457 全已录复认，零新 |
| 扫榜选材 | A10 deep research + B5（轮换外）+ npm | gpt-researcher 29,977/deep-research 19,771/DocsGPT 18,313 已录族；**niubigeo 6,072（+543/11h 放量）**竞品报告域已录判据续放量 |
| 缺陷复盘 | A13 defect retrospective | 零头部新面孔（微型域维持） |
| 调研报告 | A10 deep research | 同扫榜行；零新 |
| 演讲稿 | A7 speech/presentation script | **ok 0**（speech 词组本班零回，历班亦弱词形，如实记） |
| 演示文稿 | A10 slides | presentation-ai 3,035/SlideBot 1,216 已录复认，零新 |
| 工作汇报 | A10 weekly report | Major-project-list 255（课程噪声）/GitPulse 16——域长期沉寂维持 |
| 商务邮件 | A10 email | 头部 53★ 微型域，零新 |
| 技术方案 | A8 structured + B3 spec | 全已录族，零新 |
| 简历 | A4 career-ops 73,896（已录族） | 域旁复认，零新 |
| 标书编制 | B3 requirements/acceptance | 沉寂域维持，零新 |
| **禅道工单（集成非预置型）** | npm 禅道通道（第 6 班连用） | **在架 10 件密度续升；+3 首见**（见下节）；「激活 Bug→自动建 code 修复任务→resolve+评论回写+群通知」深度闭环仍独占 |

## 四、新面孔勘定与定性（repos 端点二次实证，三门槛：重合度/可直读性/用户会搜吗）

### 首见判据件（主扫 1 + trendshift 3 + topic 6 + WebSearch 1 源 + npm 3）

| 仓/件 | stars/pushed | 来源 | 机制亮点 | 对比 | 结论 | 日期 |
|---|---|---|---|---|---|---|
| Nanako0129/pilotfish | 705 / 10-09 | 主扫 A1 首见 | 多模型编排**政策层**：front-tier 主会话只做规划/审批/整合/终审，有界执行下沉 Sonnet、侦察下沉 Haiku、**风险触发 fresh-Opus 新鲜上下文评审**；角色=agents/*.md+政策=CLAUDE.md；跨 CLI 家族移植（pilotfish-grok/codex/remora-cc） | 我们 cascade（easy 按 tier 升序）+独立评审链已覆盖主干；「侦察下沉廉价模型」形态不同——我们 workdir_recon 是**纯本地文件扫描零 LLM**（planner.py:176）更省；跨 CLI 政策家族打法与我们「一台编排多 CLI」互证 | 参考判据（A1/A3 域，雷达盯增量） | 2026-10-10 |
| JimLiu/baocut | 555 / 10-09 | 主扫 A10 首见 | AI 视频代理+可编辑时间线：转录/字幕/翻译/配音/剪辑/动画 | video_script 类型的**下游邻接**（我们产脚本非成片） | 域旁判据（雷达） | 2026-10-10 |
| heygen-com/hyperframes | 59,706 / 10-09（created 03-10） | topic:mcp | HeyGen 官方「Write HTML. Render video. Built for agents.」——给 agent 用的 HTML→视频渲染库（npm hyperframes 在架） | 短视频/演示文稿类型的下游工具链大件；编排台不重叠 | 域旁大件判据（雷达盯增量） | 2026-10-10 |
| anthropics/financial-services | 39,151 / 09-21 | trendshift | Anthropic 官方金融垂直插件包（claude-plugins-official 同族官方垂直行业 skill 包打法） | 产品配套/skill 生态：官方垂直包 vs 我们六源市场+内置包 | 参考判据（B 专项生态信号） | 2026-10-10 |
| ZeroPointRepo/youtube-skills | 1,038 / 10-06 | trendshift | YouTube 转录/检索/频道浏览 skill（OpenCode/Claude 兼容） | 自媒体文章/短视频的素材获取芯片方向 | 微型判据（雷达） | 2026-10-10 |
| TerseAI/durable-actors | 179 / 10-09 | trendshift | Durable Actors（actor 模型持久化运行时） | A2 workflow 域旁 | 微型判据 | 2026-10-10 |
| dshworks/awesome-dsh-plugins | 15 / 10-09 | topic:ai-agents | DSH 插件第三方 spam-filtered registry（bundles+skills） | E 专项：DSH 生态归属双轨实锤——官方 deepseek-ai org（246,290★）+ 第三方 dshworks registry；我们 deepseek-harness 走 npm @deepseek-ai/dsh 官方包名零断链 | 判据（E 专项跟踪件） | 2026-10-10 |
| SourceShift/mini-ork | 29 / 10-09 | topic:agent-framework | 「证明 AI 写的修复真的修了 bug」：reproduce-certify | A13 证据门禁族（不信任自报）再+1，微型 | 微型判据 | 2026-10-10 |
| invergent-ai/surogates | 27 / 10-09 | topic:agent-framework | Managed Agents 规模运行平台 | A1 同形态微型 | 微型判据 | 2026-10-10 |
| linny006/agent-framework-radar | 8 / 10-09 | topic:agent-framework | 新 agent 框架 live index（15 分钟更） | 雷达源域旁工具 | 微型判据 | 2026-10-10 |
| thunderhead…/skill-doctor | 2 / 10-09 | topic:claude-code | skill 体检诊断器 | C 专项域旁（技能质量） | 微型判据 | 2026-10-10 |
| Thanatos9404/polygraph | 0 / 10-09 | topic:llm-agents | 「每个 PR 都在 claim，Polygraph 用实验核验」 | A13 证据门禁族再+1 | 微型判据 | 2026-10-10 |
| talas9/anti-hall | 2 / 10-09 | topic:ai-coding-assistant | verify-first Rust hook 闸（Claude/Codex） | B4 治理微型 | 微型判据 | 2026-10-10 |
| Looted/kibi | 9 / 10-09 | topic:mcp | 需求符合层（requirement-conformance for coding agents） | B3 验收域沉寂域内新动静 | 微型判据 | 2026-10-10 |
| BlocUnited-LLC/mozaiks | 24 / 10-09 | topic:agent-framework | self-hostable 多 agent 运行时 | A1 微型 | 微型判据 | 2026-10-10 |
| andyrewlee/awesome-agent-orchestrators | 2,143 / 10-09 | WebSearch 交叉验证+repos 坐实 | **编排器专属 awesome 清单**（control planes/协议/harness 适配器/运行时分类） | **C 雷达新源候选**（建议第 7 步入 keywords.md 雷达源） | 新雷达源 | 2026-10-10 |
| @chenish/zentao-mcp-agent 1.1.3 | npm 03-30 建 | npm 禅道通道 | 「Seamlessly integrate ZenTao with **OpenClaw**」MCP+CLI | **自家已接 CLI 生态与禅道交叉第 2 例**（第 1 例 DSH 系 @haoyu-qi/dsh-zentao）——OpenClaw 我们 catalog 在册 | F 竞品第 37 例（雷达） | 2026-10-10 |
| ahs-zentao 0.2.17 | npm 08-21 | npm 禅道通道 | CAS SSO 登录禅道 MCP | MCP 查询形态居多，深度闭环我们独占 | F 竞品第 38 例 | 2026-10-10 |
| @haoyu-qi/dsh-host-zentao-cli-gateway 0.1.0-rc.8 | npm 08-29 | npm 禅道通道 | DSH 系禅道 loopback Web 网关（与 dsh-zentao 同作者族） | 同上 | F 竞品第 39 例 | 2026-10-10 |

### 复认与增量（重点）

- **caura-ai/caura 497★**（topic:mcp）——MemClaw 改名 Caura 实锤（「formerly MemClaw」），记忆舰队治理共享，已录件增量复认。
- **OrchestratorInc/agent-orchestrator 12,998（+21）**——`Untrivial-ai` org 改名重定向实证（repos 端点自动跳转，零断链）；`gsd-build/get-shit-done` 同款改名。
- **PerryLink/deepseek-harness archived=true 实证**（旧址）；真身 deepseek-ai/deepseek-harness **246,290（+213）**——18 时班属主勘定结论维持，DSH 生态双轨补全（官方 org+dshworks 第三方 registry）。
- WebSearch claim 级未勘定不入库（10-05 纪律）：`aperant`/`fleetctl` 系博客 roundup 名无仓名，repos 无法实证；「Copilot /fleet」系 GitHub 产品功能动态非开源仓——记趋势信号（大厂入场舰队编排）。

## 五、雷达 C 覆盖清单

- **awesome 27 源 repos 实测 27/27 alive 零 archived**（零搜索配额）：ComposioHQ 76,748（+24）/punkpeye 95,984/vivy-yi 84/ai-boost 4,769/bradAGI 1,330/e2b-dev 30,319/Shubhamsaboo 140,936/hesreallyhim 55,319/ECC 275,851（+218）/hermes-agent 252,236（+81）/ponytail 159,475（+407）/dify 158,001（+17）/pi 113,784（+78）/opencode 212,366（+65）/VoltAgent-skills 35,422/buildwithclaude 3,607/claude-plugins-official 37,585/RUC-NLPIR 1,070/TeleAI-UAGI 668/TsinghuaC3I 665/Engineering4AI 291/BMAD 53,990/papers 1,833/EvoMap 234/IAAR 1,263/caramaschiHG 1,928/vijaythecoder 4,390（2025-10 停更，清单域唯一停件，如实记）。
- **存量头部 30 件复查**（vs 18 时班，间隔约 8h）：orca 88,508（+262）/mattpocock-skills 282,421（+564 放量）/openworker 18,478 持平/StaffDeck 1,974（+1）/DeepSeek-Reasonix 35,746 持平（E 候选首位）/openclacky 1,204 持平/freebuff 13,387（+11）/agentmemory 29,261（+4）/planning-with-files 27,360（+7）/oh-my-pi 34,779（+43）/minimax-code 2,004（+19）/cli-agent-orchestrator 1,402（+8）/amux 523/ai-maestro 816 持平/**OpenShell 15,581（+379 放量）**/hive 11,082 持平/magic 5,048 持平（08-12 后停更，如实记）/**OpenSpec 71,471（+209）**/ace 1,352 持平（08-24 停更）/**niubigeo 6,072（+543 放量）**/horizon 715 持平/governance-toolkit 6,416 持平/bifrost 8,673（+6）；tigerless 三件：autoharness 10,765（+24 续放量趋缓）/agent-memory 3,392（+5）/cost-xray 4,696 持平。
- **trendshift 根页 HTTP 200 338KB，41 仓提取**（`full_name:\"…\"` 转义 payload 提取法复用）：storytold 九兄弟/morluto-rea/diagram-design/Compositor/knowledge-work-plugins/hermes-agent 等已录复认；判据首见 3（financial-services/youtube-skills/durable-actors）+ AnyPS5/Kazumi/system-design-notes 等域外噪声排除。
- **topic 8 页全扫（updated 排序，每页 20 仓 = 160 仓）**：判据首见 6（hyperframes/dshworks/mini-ork/surogates/radar/skill-doctor 等）+ caura 改名复认 + polygraph/anti-hall/kibi/mozaiks 微型；claude-code/claude-skills 两页 SEO 噪声仓占比高（「Best…2026」命名族），批量排除。
- **npm 两查**：agent orchestrator 头部 @nathapp/nax 0.83.5→**0.85.0**（10-09 在更，已录增量）；@hybridlabor-api/bdb-agent-orchestrator 系 Untrivial-ai fork（fork 噪声排除）；禅道通道见上（+3 首见，密度 10 件）。
- **pypi**：agent-orchestrator Simple index HTTP 200（第 20 班）。
- **WebSearch 串行 1 发**（全景交叉验证，执行「每 3-4 班配一次」纪律）：awesome-agent-orchestrators 新源坐实（见上）；Omnigent 复认（omnigent-ai/omnigent 10,705★ +600 微增，Databricks 博客 claim 属主仍 omnigent-ai 无断链）；ruflo/Sandcastle/Multica/Pane/Golutra 全已录复认。
- **GitHub Trending 直抓未行**（trendshift 补位，历班惯例，如实记）。

## 六、七专项巡检 A-G（本轮口径）

- **A token 节约**：八机制锚点全在位零漂移（见 §一 表）；A3 域主扫零新（FreeToken 14,320/opensquilla 7,092/Continuous-Claude-v3 3,943 全已录复认）；待深挖队列维持（openclacky 闲时压缩+预热缓存）。
- **B 插件市场**：六源在位（market_remote.py:50）；本轮判据件三问全不过——pilotfish 系编排政策层非 skill 包（重合度高/不可直读装/非包形态）、baocut/hyperframes 系独立工具与渲染库、ace 停更——**零新接入**，全部雷达跟踪。
- **C 任务类型**：BUILTIN_FLOWS=18 实测（flows.py:36-134）+ 守卫定向绿（test_borrow_round 10/10 + test_i18n_dups 3/3 + test_i18n_key_coverage 3/3）；需求「13 种/14 名」与代码 18 型映射已钉进 §一（禅道工单=集成非预置型，README.md:236 已写明）。
- **D 经验库**：lessons=71（流程规范 31/43.7%）与 18 时班基线一致零新增；本轮调研方法论（「词库-脚本同步缺口」蒸馏件）见 §七，归当班报告非 lessons 注入面。
- **E 新 CLI**：catalog=14、本机 **13/14 在装（openclaw 缺）**；候选五 CLI（DeepSeek-Reasonix/FuXi/Gitlawb/zero/Empryo）`command -v` 全 MISSING——**零盲接防死链维持**；DSH 生态双轨归属补全（dshworks 第三方 registry 15★ 判据入库）。
- **F 禅道**：poll_enabled=False+claims=0+last_error 空+last_scan 停 09-21——**关闭态非故障，轮询重开交拍板维持**；零积压零错误；竞品面密度续升（npm 10 件，+3 首见第 37-39 例，@chenish/zentao-mcp-agent 系 OpenClaw 交叉首例）——需求侧确认延续，「激活→建任务→resolve+回写+群通知」深度闭环仍独占。
- **G 产品巡检**：过时文案 grep（「13 种任务」真实源码零命中、「单源」命中均正当语境、README「18 种」2 处准确）；**发现 1 件同步缺口（非产品毛病，系调研基建）：`scripts/borrow_scan_nightly.py` A3 组仍 4 词，keywords.md A3 组 10-09 16时班补的第 5 词（token+efficient+agent+OR+openclacky 生态）未同步；keywords.md 文件头「78 组」计数亦过时（实际 79）**——待第 4 步落地修复（脚本 QUERIES 补 1 词 + keywords.md 头部计数校正）。

## 七、失败/受限项与本轮移交清单

**受限/失败（全部如实记）**：
1. 后台主扫进程 600s 上限杀于 Q116 完成后——DONE 标记未落盘（116 ok 行已实证全覆盖，零查询损失）。
2. `speech+writing` 词组本班 ok 0（弱词形，历班同型）；`requirement+elicitation` ok 0。
3. aperant/fleetctl claim 级未勘定（无仓名可实证，不入库）；GitHub Trending 直抓未行（trendshift 补位）。
4. 雷达源 vijaythecoder/awesome-claude-agents 2025-10 后停更（清单域唯一停件）；dtyq/magic 08-12 后停更。

**移交第 4 步（落地候选，小而实排序）**：
1. 主扫脚本 A3 组补第 5 词 + keywords.md 头部 78→79 计数校正（调研基建同步缺口，G 专项发现）。
2. keywords.md 雷达源补 andyrewlee/awesome-agent-orchestrators（2,143★ 编排器专属清单，C 源）+ 禅道第 37-39 例注记 + pilotfish/hyperframes 判据注记（第 7 步沉淀一并）。
3. 路线图在册件（spec 分段签核跨四层/语义缓存/宪章骨架）维持非小而实不动。

**待深挖队列**：openclacky 闲时压缩+预热缓存（在册维持）；pilotfish「风险触发 fresh-上下文评审」（本轮新增观察项——我们评审链已独立上下文，差量在「风险分级触发」的门槛设计，暂记不立项）。

**本步结论**：主扫 116/116 零失败、首见判据仅 2（历史最收敛班之一）——域稳定期延续；判据总产出 19 件（主扫 1/trendshift 3/topic 6+caura 复认/WebSearch 1 源/npm 3/存量勘定 3+2）全入库雷达；七专项零代码缺口，1 件调研基建同步缺口移交落地步；docs-only 本步不触发发版。

---

# 七专项巡检深检（第 2/4 步 · 2026-10-10）

> 全部结论基于本步实读（函数体级打开核对），非转述第 1 步表格。先记一处勘误。

## 〇、第 1 步 §一 表勘误（本步实读勘定）

keywords.md 文件头**实写「147 组」**（= A 常驻 78 + B 轮换池 69），第 1 步 §一 表「文件头仍写 78 组已过时」系转述失实。真实缺口收敛为两条：① A3 节头写「4 组」未计 10-09 班补的第 5 词行（词库内实有 5 行 `- q=`，全局头 147 按 4 计）——词库内部两处计数不一致；② 主扫脚本 `QUERIES` A3 组 **4 词**，第 5 词（`token+efficient+agent+OR+openclacky`，keywords.md:58）**未同步进脚本**——16 时班补词只进了词库，连续两班夜间主扫漏跑该词形。§九落地项 1 相应修正。

## A、token 节约（单列章节，八机制逐一实读 + 两缺口评估）

**八机制锚点（本步逐一打开函数体核实，零漂移）**：

| # | 机制 | 真实锚点（实读证据） |
|---|---|---|
| 1 | 三段压缩 | `app/core/compaction.py:182` `maybe_compact`——压力比≥阈值触发 `compact_region`；`llm_caller=None` 直接跳过（无摘要能力不压）；`token_meter.pressure_ratio` 供压 |
| 2 | token 计量 | `app/core/usage.py:117-146`——input/output/**cached**/reasoning/total 五列入账；调用方没给费用按标定单价折算（宁缺毋滥）；compaction 专用 `saved` 注记「零值不落字段防老记录膨胀」 |
| 3 | 预算熔断 | `app/core/pipeline.py:725` `_budget_max_tokens`——`TUTTI_BUDGET_MAX_TOKENS` env 优先（运维不动配置直钳）；`_spawn_step` 内**只拦下一步、允许越线的当前步完成** |
| 4 | 花费硬顶 | `app/core/pipeline.py:760` `_cost_gate_block`——日/月 ¥ 硬顶，**先于 token 闸**（钱比 token 早见顶）；读台账失败按 0 放行（统计层故障不锁死业务） |
| 5 | cascade 级联 | pipeline 级联路由按 tier 升序走廉价模型（easy 短链），在位 |
| 6 | 经验召回+衰减 | `app/core/skills.py:348` `_surplus_decay`——30 天宽限+60 天线性衰减到 0，**只压排序不删除不改数据**；won≥1 有结局归因不衰减；粘滞负证据（lost/useless）同权 |
| 7 | 会话复用 | CLI resume 免重发前缀 + 连载前情提要（knowledge.md 10-05 盘点节在册） |
| 8 | 分层降级 | `app/core/pipeline.py:2704` `_shrink_context_block`——四层优先级圣经>模块库>经验库>大纲/前情；经验→4K 硬截、模块库按「## 」边界、圣经按二级标题边界；降级说明进 step note 可对账 |

**两缺口评估（是否真实缺口）**：
- **精确缓存**：`app/core/modelhub.py:3689` `chat(cache_ttl>0)` 精确匹配响应缓存在位，docstring 明确边界「仅限幂等调用（连通性测试等）；创作类调用不要开，否则同一 prompt 的二次请求会屏蔽模型的新输出」——生产编排链路至今**无 cache_ttl 调用方**（knowledge.md 10-05 盘点在册结论）。判定：**在册真实缺口**（接线面小），但接线点需逐调用方幂等性判定，非本轮小而实，维持路线图。
- **语义缓存**：同义不同文缓存有陈旧响应正确性风险、编排任务上下文天然互异命中率存疑——10-05 判定「待拍板不扩建」**维持**；生产 exact cache 接线是其前置。
- **廉价模型分流**：cascade 已在位；本轮判据件 pilotfish「侦察下沉廉价模型」与我们 workdir_recon **纯本地零 LLM**（planner.py:176）对比，我们更省——非缺口。

**结论**：八机制零漂移零回归；真实缺口仅「生产 exact cache 接线」一项在册（语义缓存等其前置），均不属本轮小而实，不动。

## B、插件市场（六源实读 + 判据件三问逐项）

- 六源实读 `app/core/market_remote.py:50` `SOURCES`：zcode（marketplace）/anthropic（jsdelivr+raw 双 url）/anthropic-skills/claude-skills（repo 克隆型）/clawhub（注册表+trending 合成）/cocoloop——与第 1 步一致零漂移。
- 内置包 `app/core/market.py:56` `BUILTIN_PACKS` 6 包在位，category 全取 `skills.LESSON_CATEGORIES` 闭集枚举（文件头注释明示约束）。
- 闸门链实读：`assert_public_url:113`（仅 https+私网拒绝）→ `inspect_tree:568`（纯技能白名单，白名单外内容不参与检查与安装）→ 五重体量帽——**零绕过路径新增**。
- **判据件三问逐项**（第 1 步首见 19 件中可装形态候选逐个过）：

| 候选 | 重合度 | 可直读性 | 用户会搜吗 | 判定 |
|---|---|---|---|---|
| pilotfish（编排政策层） | 高（cascade+评审链已覆盖主干） | 否——agents/*.md+CLAUDE.md 政策形态非 skill 包 | 否（编排者是产品本体非插件） | 三问全不过，雷达 |
| anthropics/financial-services（垂直 skill 包） | 低但需求不匹配 | 是（claude-plugins-official 同族格式） | 否（中文写作/编排用户非金融垂直） | 不接，雷达 |
| ZeroPointRepo/youtube-skills（转录 skill） | 低 | 是（OpenCode/Claude 兼容 skill） | 弱（用户面七猫/番茄/抖音/B站，YouTube 需外网） | 不接，雷达 |
| baocut/hyperframes（成片/渲染工具） | 低（我们产脚本非成片） | 否（独立工具/渲染库非包） | 否 | 雷达 |
| dshworks/awesome-dsh-plugins（第三方 registry） | 否（registry 非包） | — | — | E 专项跟踪件 |
| skill-doctor/mini-ork/polygraph 等（0-2★ 微型） | — | — | — | 微型判据不入市场 |

**结论**：零新接入，全部雷达跟踪；「接入判断三问」纪律执行无放水。

## C、任务类型（18 型逐项核对）

- `app/core/flows.py:36-134` `BUILTIN_FLOWS` **18 型实读**：每型四件套齐备（name/goal_hint/note）；review 引擎 15 型带 rubric（4-5 维）+ threshold（7.0，**标书 7.5 唯一例外**——合规域门槛更高，合理）+ rounds(2) + 产出文件名；serial_novel 加 `serial{chapters:8, words_per_chapter:2500}`；direct 型 3 个（direct/rank_scan/defect_retro）无评审参数（快档直出，形状正确）。rubric 维度逐型定制非通用套话（如 video_script「黄金3秒钩子/节奏密度/口播流畅/画面可执行/互动引导」、translation「忠实度/流畅度/术语一致性/风格贴合」）。
- UI 菜单**非硬编码**：`app/ui/index.html` 无预置型名硬编码（grep 实证），前端从 flows API 动态渲染——flows.py 改即菜单改，无双源真相。i18n 覆盖守卫在位。
- 守卫测试本步复跑：`cd tests && python -m unittest test_borrow_round test_i18n_dups test_i18n_key_coverage` → **16/16 OK**。⚠️ 实证：**必须在 tests/ 目录下跑**——仓库根跑撞 `ModuleNotFoundError: No module named 'base'`（与 18 时班「unittest 管道假绿退出码坑」同型，根因复认）。
- README.md:129「18 种任务类型」、:236「内置 18 种；禅道工单是自动转成 code 任务」与代码一致。

**结论**：18 型菜单描述/流程参数/辅助信息零缺口；「13 种」系需求文档口径与代码漂移（代码 18 型+禅道集成形态），代码侧无恙。

## D、经验库（真实路径从探索确认，非凭名猜）

- **真实路径勘定**：经验库=`app/core/skills.py` + `data/skills.json`（`lessons` 键+`packs` 键）；知识库另库=`app/core/knowledge.py` + `data/knowledge.json`——两库分工边界两文件头 docstring 写明（经验记「下次怎么做」/知识记「已知是什么」），竞品调研沉淀走 `docs/borrow-log/knowledge.md`（模式库）而非运行时知识库，三库不混。
- 实测统计：lessons=**71**、**重名 0**、category 六闭类分布：**流程规范 31（43.7%）**/节奏爽点 16/情节逻辑 10/人物塑造 7/一致性 4/文笔风格 3；kind：lesson 66 + procedure 5——与第 1 步口径完全一致。
- 去重机制在位：`skills.py:413` `_title_sim`（字符 bigram 包含度，与 knowledge 同口径）。
- **偏科判定**：流程规范 43.7% 属结构性（source 以代码任务运行 r-2026… 为主，code 任务产出天然偏流程教训），非分类错误——维持观察不硬调（硬调=为凑均衡改分类，反过度防御）。
- **本轮可蒸馏方法论（候选 2 条，移交第 7 步沉淀，不在本步写库）**：①「词库补词必须同班回写主扫脚本 QUERIES」——A3 缺口实证（16 时班补词只进词库，两班漏跑）；②「头部计数类核对必须实读头部行原文」——第 1 步 78/147 转述失实教训。

## E、新 CLI（catalog 实读 + 本机探测）

- `app/core/catalog.py:32` `DEFAULT_CATALOG` **14 条实读**（codex-cli/claude-code/opencode/qwencode/aider/openclaw/kimi-code/mimo-code/grok-build/pi/deepseek-harness/gemini-cli/codebuddy/trae-agent）；`ORCH_RESUME_PATCH`/`LAUNCH_PATCH`/`CONFIG_PATCH` 幂等补丁链 :348-379 在位。
- 本机 `command -v` 逐个探测：**13/14 在装，openclaw MISSING**（与第 1 步一致）。
- 候选五 CLI（deepseek-reasonix/fuxi/gitlawb/zero/empryo）`command -v` **全 MISSING** → **仅列待验证，零目录入口**（防死链纪律维持）。
- DSH 生态归属双轨（官方 deepseek-ai org 246k★ + dshworks 第三方 registry 15★）第 1 步已勘定，catalog 走 npm `@deepseek-ai/dsh` 包名零断链。

**结论**：零接入零断链；候选全部待实测（本机未装）。

## F、禅道（只读巡检，零写操作）

- 链路实读 `app/core/zentao.py`（2692 行）：`_scan:2332`（按产品档案拉 bug→认领新+need_manual 重排查+retry 重试+_archive_sweep 归档对账）、`_poll:2403`（单飞锁 `_SCAN_LOCK` 防并发扫描）、`_poll_unlocked:2414`（对账回写+到点/强制扫描，异常不外抛）、`_holiday_gap`（法定假日顺延到下一工作日同时刻）、`_route_one:2128`/`_finish_ok:1912`/`retry_claim:2281`/`archive_claim:1816`、SSRF `_guard_url:163`——全链在位。
- 运行态只读核查 `data/zentao.json`：`poll_enabled: false`、`claims: {}`（**零积压**）、`last_error: ""`（**零错误**）、last_scan `2026-09-21 20:43:44`——**关闭态非故障**（轮询开关用户侧关闭，非异常停摆；last_error 空证明停前最后一轮干净）。
- 产品档案 1 条：product 96 → backend 仓 `E:\GitLab\cbc\mo-so`（owners backend=wuxinping/frontend=xiangdong/not_ours 空；`module_routes: []` 空——模块级路由未配，定责走 AI 排查兜底，属用户侧配置自由度非缺陷）。
- 回写链路代码在位（`_finish_ok` 合并+resolve+评论回写 + `_notify:1754` 群通知）；**本步零写操作**（未创建修复任务/未 resolve/未发通知，遵巡检只读纪律）。
- 竞品面：第 37-39 例（@chenish/zentao-mcp-agent〔OpenClaw 交叉首例〕/ahs-zentao/@haoyu-qi/dsh-host-zentao-cli-gateway）第 1 步已入库；「激活 Bug→自动建 code 任务→合并+resolve+评论回写+群通知」深度闭环在 npm 10 件在架品中仍独占。

**阻塞**：轮询是否重开属用户拍板项，非缺陷不代开。

## G、产品巡检（文案/链接/流程一致性）

- README「18 种」2 处均与代码一致；「13 种」全仓零命中；「单源」零命中（双源落地无残留）；「六源」与 `SOURCES` 6 条一致。
- 设置页禅道子页在位且文案准确：`app/ui/index.html:250` 子页按钮、:813-828 面板+说明（「先按模块路由/AI 排查定责…修完自动合并并 resolve 或转派对应负责人；测试指错人也按排查结论改派」与 zentao.py 实际行为逐句对得上）。
- **发现 1 件真实缺口（调研基建同步缺口，非产品毛病）**：主扫脚本 A3 组 4 词 vs keywords.md A3 节 5 词行——10-09 16 时班补词只进词库未进脚本，连续两班夜间主扫漏跑 openclacky 词形；keywords.md 双处计数（A3 节头「4 组」、文件头「147 组」）未跟上第 5 词。→ 移交落地项 1。
- 产品侧 UI/流程小毛病：本轮**零发现**（文案/断链/描述与实际不符三查全过）。

## 落地项选点（第 3 步实施，范围就此钉死）

**落地项 1：A3 组词库-脚本同步缺口修复（G 专项发现，调研基建）**
- 改动 1：`scripts/borrow_scan_nightly.py` `QUERIES` 列表 :27 行尾（`("A3", "cheap+model+routing+OR+model+cascade"),` 之后）追加 `("A3", "token+efficient+agent+OR+openclacky"),`——行为变化：夜间主扫 A3 组 4→5 查询，补齐剪切线盲区词形；QUERIES 总数 89→90（脚本 :117 进度行 `len(QUERIES)` 动态取值，无硬编码计数需同步；测试无 QUERIES 断言，grep 实证）。
- 改动 2：`docs/borrow-log/keywords.md` :1 头「147 组」→「148 组」（79 A + 69 B）、:53 节头「4 组」→「5 组」。
- 回归用例：`python -m py_compile scripts/borrow_scan_nightly.py`；`python -c` 断言 QUERIES 总数 90 且 A3=5；keywords.md `grep -c "^- q=" ` A3 节 5 行与节头一致。
- 验收命令：`python -m py_compile scripts/borrow_scan_nightly.py && grep -o '("A3"' scripts/borrow_scan_nightly.py | wc -l`（期望 5；⚠️ 须用 `grep -o | wc -l`，`grep -c` 按行计数——现 4 词挤在 2 行会误报 2，本步实测坑）。
- 风险：无（纯追加+计数文本，docs+脚本不触产品代码，不触发发版）。

**落地项 2：keywords.md 雷达源补编排器专属清单（第 1 步 C 源候选坐实，repos 端点已实证 2,143★ alive）**
- 改动：`docs/borrow-log/keywords.md` 雷达源 C 节追加 `andyrewlee/awesome-agent-orchestrators`（编排器专属 awesome：control planes/协议/harness 适配器/运行时分类，零搜索配额 repos 实测通道）+ A3 节 :58 注记尾补「脚本已同步（2026-10-10 班）」一句防复查重复劳动。
- 行为变化：下一班雷达多 1 源；A3 注记闭环。
- 回归：`grep -c "awesome-agent-orchestrators" docs/borrow-log/keywords.md`（期望 ≥1）；文档 diff 逐 hunk 自审。
- 风险：无（纯文档追加）。

**不动的（明示排除）**：生产 exact cache 接线（需逐调用方幂等判定，非小而实）；语义缓存（待拍板维持）；spec 分段签核跨四层/宪章骨架（路线图大件）；经验库分类硬调（结构性偏科非错误）；禅道轮询重开（用户拍板项）。

## 本步结论

七专项全部有证据、结论与阻塞说明：A 八机制零漂移、真实缺口仅 exact cache 生产接线（在册不动）；B 零新接入三问无放水；C 18 型零缺口（守卫 16/16 复跑绿，tests/ 目录纪律复认）；D 71 条零重名、流程规范 43.7% 结构性维持、蒸馏候选 2 条移交；E 14 条 13 在装、候选 5 全 MISSING 零盲接；F 关闭态非故障零积压零写操作；G 零产品毛病、1 件调研基建缺口移交落地。落地项 2 个已钉死范围（4 处文件级改动+回归+验收命令），待第 3 步实施。

---

# 落地实施实录（第 3/4 步 · 2026-10-10 04:1x-05:3x）

> 按第 2 步钉死范围实施，零范围漂移。改动全部 docs+调研脚本，不触产品代码，不发版。

## 一、实际改动清单（4 处，2 文件，与第 2 步钉死清单逐项对应）

| # | 文件 | 改动 | 对应钉死项 |
|---|---|---|---|
| 1 | `scripts/borrow_scan_nightly.py:28` | A3 组追加 `("A3", "token+efficient+agent+OR+openclacky"),`（A4 组之前，格式与邻行一致） | 落地项 1 改动 1 |
| 2 | `docs/borrow-log/keywords.md:1` | 文件头「147 组」→「148 组」 | 落地项 1 改动 2 |
| 3 | `docs/borrow-log/keywords.md:53` | A3 节头「4 组」→「5 组」 | 落地项 1 改动 2 |
| 4 | `docs/borrow-log/keywords.md:58` | :58 注记尾补「主扫脚本 QUERIES 已同步该词〔2026-10-10 班，此前两班夜间漏跑——词库补词必须同班回写脚本 QUERIES〕」（内层用全角方括号〔〕照文件惯例防圆括号嵌套） | 落地项 2 A3 注记 |
| 5 | `docs/borrow-log/keywords.md:232` | 雷达源 C 节 awesome 清单补充行尾追加 `andyrewlee/awesome-agent-orchestrators`（2,143★ 编排器专属 awesome，含谁/何时/为何标注） | 落地项 2 雷达源 |

git diff --stat：keywords.md 8 行（4 处修改）+ 脚本 1 行追加，5 insertions/4 deletions，零外来改动混入（git status 仅本轮 2 文件 + 本报告）。

## 二、回归与验收命令（全部实跑）

```
python -m py_compile scripts/borrow_scan_nightly.py          → PY_COMPILE_OK
python -c "import 断言"                                       → QUERIES total=90 A3=5 尾词=token+efficient+agent+OR+openclacky OK
grep -o '("A3"' scripts/borrow_scan_nightly.py | wc -l        → 5（照第 2 步钉死用 -o|wc -l 防 grep -c 按行误报坑）
grep -o '148 组' docs/borrow-log/keywords.md                  → 命中
sed A3 节 | grep -c '^- q='                                   → 5（节头与实词行一致）
grep -c awesome-agent-orchestrators keywords.md               → 1
python 括号配对校验 :58 行                                     → （）开 1 闭 1 OK
```

## 三、闸②全量测试对账（如实记，**既有失败单独报告，不称全绿**）

- **单进程全量 discover 两跑均无 Ran 汇总**：管道 tail 首跑（退出码系 tail 的，knowledge.md:1873 在册管道假绿坑，本班正中一次即改）→ 落盘重跑 `> log 2>&1` 取 **REAL_EXIT=0 但日志尾部停在 test_selfupdate 的 `RuntimeError: private worker path` traceback、无 Ran/OK 统计行**——32 位 relaunch 线程 `os._exit(0)` 静默杀死 discover 进程，knowledge.md:2182 在案形态第 N 次实证，退出码 0 判绿一律不作数。
- **改用知识库在册替代打法：逐模块净进程全扫**（266 模块先例，full-type-round.md:845）。本班三修打法：① `cd tests && discover -s .` → 9 件 ImportError（`from tests.base import` 包导入解析不到，BAD 假象）→ ② 仓库根 `discover -s tests`（start_dir 双侧入 sys.path，裸导入/包导入兼容）→ 首循环 cwd 残留 tests/ 致 glob 落空秒退 → ③ 绝对路径修正后跑通全量。
- **终账：260 OK / 16 FAIL / 276 模块**——与 18 时班 c2785de「逐模块兜底 260/276」基线**逐位一致**。
- **16 件 FAIL 全部既有在册**：14 件在 18 时班欠账清单（2026-10-09.md:651-661：cancel_step_finalize/direct_steering/impl_zero_tool_gate/iteration_regressions/lesson_karma_mark/novel_signing/pipeline/procedural_learn/quality_gates/race_ctx_shrink/resume_e2e/settings_v2_consumers/skills/token_cost_t2t3），+2 件清单漏记但同基线：test_pipeline_compaction(3)（压缩默认翻转 1c81362 演进配套欠账，「默认关」断言与翻转后行为不符）、test_zentao(1)（TestHolidaySkip「chinesecalendar 范围内找不到补班日」——节假日数据时效性失败非产品逻辑）。
- **stash 对照铁证**：`git stash push` 摘掉本轮全部改动 → test_pipeline_compaction 单跑仍 3 failures 同文 → **既有失败与本轮改动零关联**，`stash pop` 已恢复（git status 复核 2 文件在位）。
- **本轮改动面回归证据**：py_compile 绿 + QUERIES 断言绿 + tests/ 对 `borrow_scan_nightly` 零引用（grep 实证，改动影响面隔离）。

## 四、逐 hunk 自审与评审结论

- 5 处 hunk 逐个过：脚本追加行缩进/逗号/+连接词形与邻行一致，:117 进度行 `len(QUERIES)` 动态取值零硬编码联动；keywords.md 4 处计数与实词行互相印证（头 148=A 79+B 69、A3 节头 5=5 行 q=）。
- **自审拦下 1 件**：:58 注记首版内层用圆括号致全角括号配对失衡（外层 `（2026-10-09 16 时班补：…` 悬空）——照 :231 文件惯例改全角方括号〔〕后 python 计数校验（开 1 闭 1）通过。
- 跨模块影响：QUERIES 是纯数据列表，无导入方（grep 实证）；keywords.md 系调研词库，产品零读取；脚本进度行/批表逻辑不感知 A3 组词数变化。
- 市场安全边界/禅道权限/并发状态：本轮改动不触及（docs+独立扫描脚本，无网络/存储/权限面）。
- i18n：改动文件无用户可见 UI 文案，i18n 守卫面不涉及。
- 评审判定：**通过**（自审 1 件括号问题当轮修复闭环；无超范围改动；测试对账如上）。

## 五、本步结论

落地项 1+2 共 5 处 hunk（第 2 步钉死 4 处+注记括号修正 1 处）全部落地并验收通过；质量关：py_compile/QUERIES 断言/grep 验收/括号配对全绿，逐模块净进程全扫 260/276 与 18 时班基线逐位一致、16 件既有失败有 stash 对照铁证与本轮零关联、单进程 discover 静默退出形态在案复认（判绿认 Ran 统计行口径维持）。零外来改动混入（git status/diff --stat 双核对）。docs+调研脚本改动不触发发版；提交推送归第 4 步。
