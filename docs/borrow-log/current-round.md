# 2026-10-08 10 时班调研底稿（current-round，新一轮（v0.1.95 后）计划第 1/4 步·全类型竞品调研·批3）

> 开工实录：10:00（UTC+8，hour=10，10%7=3 → 轮换批3「计划/spec/长任务」）、
> 分支 main（HEAD f16e4d1，v0.1.95 已发版）。工作区**非干净**：遗留 08 时班（上一轮
> 第 2/4 步）落地件未提交（app/ui/* 规则表补词+tests/test_recommend_rules.py+
> tests/test_full_type_improvements.py+full-type-review.md 证据+knowledge.md 节），
> 本班只做调研与文档，不碰代码、不掩盖遗留。通道：gh api 认证可用（主扫串行
> sleep 4s）、WebSearch 串行 1 发、trendshift/topic/npm curl 全可达。

## 主扫描与批次判定

- **主扫**：`scripts/borrow_scan_nightly.py` 无参全量（自动选批 B3）——A 常驻 89
  （含内置 B1 11）+ B3 轮换 11 + A1u/A1p2 双轮 16 = **116 查询 524 行 466 唯一仓，
  exit 0、PROGRESS DONE 116 实证，零 403 零 FAIL**（后台跑满约 9 分钟，未触时限）。
- **主扫面过筛**（全名+简称双通道对 docs/borrow-log/ 全历史）：**335 已录 / 131 首见**，
  首见以噪声/课程库/2022-2024 停更旧件为主（与 03 时班 136 首见全噪声同型）；
  **判据级新面孔 5 件**（见下表，全部 repos 端点二次实证）。

## B3 轮换域逐词头部（11 词，本班域）

| 查询词 | 头部命中（top3） | 判定 |
|---|---|---|
| long-running/persistent planning | PraisonAI 9,199 · PlanWeave 412 · plandeck 67 | 已录族 |
| spec+driven+development | OpenSpec 71,276 · spec-workflow-mcp 4,302 · zhu1090093659/spec_driven 983 | 已录族 |
| plan-and-execute / task decomposition | TaskWeaver 6,168 · refact 3,536 · OpenMOSS 1,334 | 已录族 |
| milestone/project planning | PlanWeave 412 · Taoidle/plan-cascade 133 | 已录/微型 |
| autonomous long horizon | DeepResearchAgent 3,555 · claude-elixir-phoenix 565 | 已录族 |
| worktree parallel agent | （主扫 A5 域覆盖，已录族） | 已录族 |
| checkpoint/resume、acceptance、requirement elicitation | 全已录/微型 | 稳定期 |

**批3 域结论：稳定期延续**（vs 03 时班同域，间隔 7h——OpenSpec 71,262→71,276（+14）、
planning-with-files 27,321→27,323（+2）、PlanWeave 411→412（+1 平稳），零机制级新差量）。

## 新面孔 5 件（repos 二次实证全过，判据线以上）

| 仓 | 星数 | 实证 | 域 | 定性 |
|---|---|---|---|---|
| docker/docker-agent | 3,748★ | pushed 10-07，created 25-09 | A1 编排 | **Docker 官方 AI Agent Builder+Runtime**——大厂官方编排件，runtime 面与桌面编排部分重合，需盯增量 |
| tingly-dev/tingly-box | 351★ | pushed 10-08（当日） | A1 编排 | 「Every builder. Every team. Every agent」聚合编排新锐，判据级跟踪 |
| openqodex/openqodex | 276★ | created 10-02（5 天新锐）、pushed 10-07 | B1 评审 | push 前 CC/Codex 代码评审+SAST/secrets 依赖扫描；我们评审闸已有（diff-only+_review_depth），「push 前门禁」形态判据级 |
| rrahimi-uci/guarded-agentic-compaction | 1★ | pushed 10-08 | A3 压缩 | 研究库+论文「Compile the routine, refuse the uncertain」——与我们三段压缩同域学术前沿信号，方法论参考 |
| Azure-Samples/aspire-semantic-kernel-creative-writer | 66★ | pushed 09-10 | A7 写作 | 微软官方 creative writing multi-agent sample，微型判据 |

- PlanWeave（GaosCode）412★ 复认已录（10-03 首见 406→本班 412），非新面孔。
- Tencent-Hunyuan/Hy-MT2（trendshift）系腾讯混元模型仓，域旁排除；
  storytold/cadcraft 系创作工具族第 8 兄弟（域旁放量复认）；
  feder-cr/invisible_puppeteer 系 invisible_playwright 同作者爬虫域旁排除。

## WebSearch 串行交叉验证（1 发，B3 域定向）

- 查询「spec-driven development long-running coding agent plan file 开源 2026-10」：
  **零新 claim**——GitHub Spec Kit（/specify→/plan→/tasks→/implement 四相，已录族）、
  Kilo Code SDD 工作流（Constitution→Specify→Plan→Tasks→Implement，方法论文章）、
  DeepLearning.AI SDD 课件仓，全已录/方法论面。
- 一条参考判据：truefoundry 2026-06-15「Governing Specs」——spec 在舰队规模需要
  **版本、属主、门禁**三件套，与我们 .codebee/{spec.md,task_plan.md} 档案同向，
  归档型参考（不进路线图，档案已有勾选 stopgate+归档戳）。
- 来源：github.blog（Spec Kit）、path.kilo.ai（Kilo SDD）、truefoundry.com（Governing Specs）、
  thebcms.com（SDD 2026 Guide）、augmentcode.com（SDD Complete Guide）。

## 雷达 C 全过清单

- **awesome 20 源 repos 实测 20/20 alive 零 archived**（本班属主勘定 2 件）：
  - `buildwithclaude` 正主 = **davepoon/buildwithclaude 3,604★**（pushed 10-06，
    in:name 勘定——旧注缺属主，keywords.md 已补）；
  - `BMAD-METHOD` 正主 = **bmad-code-org/BMAD-METHOD 53,909★**（pushed 10-07，
    in:name 勘定——裸名 404 属属主失配，keywords.md 已补）。
  - 其余 18 源星数快照：ComposioHQ/awesome-claude-skills 76,675 / punkpeye/
    awesome-mcp-servers 95,911 / Shubhamsaboo/awesome-llm-apps 140,947 / hesreallyhim/
    awesome-claude-code 55,215 / VoltAgent-skills 35,341（+20）/ ai-boost 4,746 /
    vijaythecoder 4,389 / e2b-dev 30,296 / bradAGI 1,323 / RUC-NLPIR 1,065 /
    IAAR-Shanghai 1,261 / VoltAgent-papers 1,827 / caramaschiHG 1,929 /
    TsinghuaC3I 665 / TeleAI-UAGI 659（pushed 10-08 当日）/ Engineering4AI 289 /
    EvoMap 234 / vivy-yi 80（pushed 03-05 停更维持）。
- **trendshift 根页 29 仓**（curl 322KB）：已录复认 mattpocock-skills/morluto-rea/
  Compositor/openai/math/jumper/huashu-art-motion/storytold 七兄弟；
  新面孔 docker/docker-agent（见上表）+storytold 第 8 兄弟 cadcraft（域旁）；
  已录件 maximhq/bifrost、yetone/magpie、farion1231/cc-switch、GetBusbar/busbar 复认。
- **topic 8 页（updated 排序 curl 直抓）**：multi-agent-orchestration 页两次抓取均空
  （间歇反爬，如实记）；ai-coding-assistant 页与 llm-agents 页返回内容相同（size
  545,256 一致，系上游同缓存，词级粒度损失如实记）；agent-framework 页见
  **esengine/DeepSeek-Reasonix updated 复见**（E 候选首位增量信号第 3 班连见）；
  ai-agents/claude-skills/llm-agents/mcp 页均为微型新锐件群（zompinc/
  agent-conventions、danjdewhurst/story-skills〔已录 277★〕、QwenLM/qwen-code
  〔已录〕、vitali87/code-graph-rag 等），判据线以上零新。
- **npm 两查**：agent orchestrator 头部（@nathapp/nax、@polderlabs/bizar 10.33.0、
  opencode-oceanus、coleo、@hybridlabor-api/bdb-agent-orchestrator fork 件）**全已录
  零新**；**禅道 npm 通道第 33 例零新**（@jw-king/dsh-plugin-zentao 0.1.17、
  zentao-cli 0.3.1、zentao-api 0.7.2 全复认，自动修复集成面独占维持）。
- pypi 通道本班未跑（03 时班同域刚跑零新、间隔 7h，下一批5 轮换班补——如实记不虚报）。

## 复查记录（10 时班，基准=08 时班，间隔约 2h）

- 存量头部仓 repos 实测全 alive 零 archived：**orca 87,111→87,177（+66 续领跑）**/
  superpowers 296,387→296,408（+21）/mattpocock-skills 279,561→279,702（+141 续放量）/
  ponytail 157,578→157,654（+76）/oh-my-claudecode 39,660（主扫实测）/planning-with-files
  +2/OpenSpec +14/StaffDeck 1,969→1,970/amux 521 持平/**DeepSeek-Reasonix 35,749→35,748
  （±1 持平，E 候选首位维持）**。| 复查 | 2026-10-08

## 14+4 类型覆盖表（需求 14 项 vs flows.py 实读 18 条，零漏项）

flows.py BUILTIN_FLOWS=18 条实读确认（flows.py:25-140）：direct/code/novel/
serial_novel/article/video_script/**doc**/translation/rank_scan/**defect_retro**（=禅道工单
复盘）/research/speech/**presentation**/weekly_report/email/tech_proposal/**resume**/**bid_doc**
——需求 14 项全在；粗体 4 条系需求未列但预置在库（文档/演示文稿/简历/标书）。
对话场景=A10 chatbot+memory 词组（每轮必跑）、知识库=批5 knowledge base quality
词组（轮换覆盖）+A10 deep research。本班 A 组 89 查询全跑 → 18 类型调研面全覆盖。

## 七专项快照（第 1/4 步班次，A-G 引 08 时班续行号级实证+本班零代码改动确认）

- A：八锚点零漂移维持（08 时班续刚复验 pipeline:61/:613/:648/:677/:703/:948/:1019/
  :2538/:2581 等，本班零代码改动）；四对标方向全既有能力零新差量。
- B：六源市场在位；本班新面孔 5 件按接入三问全判「不接、雷达跟踪」（docker-agent/
  tingly-box/openqodex 系平台级整仓非包；compaction 研究库系论文非装件；aspire
  sample 系微型 demo）——方法论面：compaction 论文方向蒸馏进 D 专项参考。
- C：BUILTIN_FLOWS=18 本班实读再确认+goal_hint 18 条在位；08 时班落地件（推荐
  规则补词）在工作区待上一轮收口，本班不重叠。
- D：lessons=70 基线维持（流程规范 43% 偏科维持）；本班方法论蒸馏：trendshift
  Next.js flight payload 的 full_name 提取法两班连用稳定；topic 页 curl 直抓
  `href="/owner/repo"` 模式修正（旧 /stargazers 后缀模式已失效——10-08 10 时班实测）。
- E：catalog=14、本机 13/14 在装、候选五 CLI 全 MISSING 维持零盲接（防死链纪律）。
- F：禅道 poll_enabled=false + claims={} 零积压维持（交拍板件）；npm 通道第 33 例
  零新；产品档案路由 18 流程无变化。
- G：本班零 UI 改动，08 时班续 grep 零命中维持。

## 待深挖队列（10 时快照）

- 待深挖 12 项维持零新队列项；交拍板 6 件维持（禅道轮询重开/cost-xray/release_gate
  判定面/npm files 白名单/单进程 discover 静退根因/cc-safety-net denylist 嫁接）。
- keywords.md 本班调整 1 处：C 源 buildwithclaude/BMAD-METHOD 属主勘定补注
  （davepoon 3,604★/bmad-code-org 53,909★，in:name 一次勘定，零多余配额）。

## 10:3x 续班补强（真实性抽查+两缺口翻案）

- **底稿真实性抽查 3/3 过**（repos 端点独立复核）：docker/docker-agent
  3,748→**3,752**（自然增量）/openqodex 276→**277**/tingly-box 351 持平——
  底稿星数与实证同源可信，非虚报。
- **multi-agent-orchestration topic 页翻案**：底稿两次抓空系间歇反爬，本班隔约
  25 分钟重抓**成功 20 件**——过筛 borrow-log 全历史 **6 已录复认**（5dive/
  Theepankumargandhi-Multi-Agent-Orchestration/desplega-agent-swarm/looptroop-
  LoopTroop/synapse-ai-hub-Forge/synapseorch-synapse-ai）+ **14 首见**；repos
  端点逐一星数核查后判据线以上微型件 4 件（下表），其余 0-9★ 判据线下
  （agentbeacon 6★/flow-crew 9★/loop-troop-gatekeeper、doperpowers、
  agentmesh-link、clad-labs、SAGAR、Genet、ai-skills 全 0-1★ 噪声）。
- **pypi 通道补试**：pypi.org/search curl 直抓反爬零输出（搜索页 JS 渲染），
  维持 03 时班同域零新结论（间隔 7h），如实记不虚报；npm 双查底稿已过维持。

### topic 页翻案新入库 4 件（repos 端点实证）

| 仓 | 星数 | 实证 | 域 | 定性 |
|---|---|---|---|---|
| Fmarzochi/EGC | 63★ | pushed 10-08（当日） | A2/批2 记忆 | 「给每个 AI coding agent 同一个大脑」**跨 agent 共享记忆层**——与我们 knowledge.md 经验库+agent_context 同域，微型判据，方法论面待深挖 |
| eggai-tech/EggAI | 56★ | pushed 10-06 | A1 编排 | async-first 企业级多 agent meta framework，判据级跟踪 |
| AutomatosAI/automatos-ai | 48★ | pushed 10-07 | A2 数字员工 | 「AI Operating System—workforce of AI」，StaffDeck/openworker 同形态微型件 |
| komluk/scaffolding | 15★ | pushed 10-07 | B3（本班域） | spec-driven multi-agent orchestration plugin for Claude Code，微型判据 |

- **flows.py BUILTIN_FLOWS=18 条独立实读复认**（flows.py:37-128 逐条核对）：
  需求 14 项→direct/code/novel/serial_novel/article/research/video_script/
  tech_proposal/translation/speech/weekly_report/email/rank_scan/defect_retro
  全对上（禅道工单=defect_retro+zentao.py 自动修复链）；另 doc/presentation/
  resume/bid_doc 4 条预置在库；对话=direct 引擎+A10 chatbot memory 词组、
  知识库=批5 knowledge base quality+A10 deep research 覆盖。零漏项。

## 10 时班续·第 2/4 步七专项巡检（实读级证据，本班零代码改动）

> 巡检方式：A/B/D/E/F 主文件直接实读+data/*.json 只读统计；C/G 派并行只读代理
> 全量扫描后关键件独立复核。全程未触发 scan_now/真实修复/群通知/安装动作。

### A. token 节约（单列小节）——八锚点全在位，四对标零新差量

| # | 锚点 | 实证（当前工作区行号） |
|---|---|---|
| 1 | 三段压缩（撑爆→压缩→守门重试） | compaction.py:33-182（estimate_tokens/prune_text/select_range/compact_region/maybe_compact）+ pipeline.py:231 `_compaction_enabled` + :703 灰度路径 + step_runner.execute_step |
| 2 | token_meter | token_meter.py:53-142 TokenMeter（accumulate/used/cached/pressure_ratio/last_context）；pipeline:690-691 预算闸读数、:722-724 累加 |
| 3 | 预算熔断（双闸） | pipeline:613 `_budget_max_tokens`（TUTTI_BUDGET_MAX_TOKENS env 优先）+ :648 `_cost_gate_block` 花费硬顶闸（usage.cost_snapshot 日/月 ¥ 顶，:677 先于 token 闸执行） |
| 4 | cascade 廉价模型分流 | capability.py:100 `cascade_reorder` + :128 make_tier_lookup + pipeline:1526 ss_get("cascade","enabled") 撑爆换将重排 |
| 5 | 经验召回 | knowledge.py:277 `block_for`（KNOWLEDGE_BUDGET=3000/MAX_INJECT=6 独立预算，knowledge.py:37-38）+ pipeline:3196 注入 |
| 6 | 会话复用 | sessions.py:298 scan（codex/claude/qwen/opencode/mimo 五家）+ pipeline:143 resume 钉原 CLI + :271 续会话可用性探测 + :1560「会话内前缀走缓存读计价」 |
| 7 | diff 评审 | pipeline:919 CODE_REVIEW_PROMPT「diff 为主要依据」+ :948 `_git_diff`（拼未跟踪新文件防半盲评）+ :1019 `_review_depth_note` 按行数分级（<40 快评 / ≥600 先概览后深看） |
| 8 | _shrink_context_block 分层降级 | pipeline:2538（经验库→4K→模块库按模块边界→圣经按二级标题边界）+ :2581 `_serial_shrunk_block` 连载起草重试复用 |

四对标方向（不重复造已有功能）：
- **prompt 缓存**：已有——会话复用即前缀缓存（pipeline:1560 读计价注释），token_meter.cached()（token_meter.py:109）已计量入账。无需新建。
- **语义缓存**：无既有实现（pipeline.py 全文零命中）→ 列待深挖不立项：与 repeat_guard 定位不同（repeat 拦同因失败重试，语义缓存复用成功响应），但本产品任务以写作/长文为主、prompt 几乎不复用，收益窄。
- **diff-only 评审**：已覆盖且更优——主依据已是 diff（:919），读文件是可选核对加分项（:920）。无需改。
- **廉价模型分流**：cascade 已有（锚点 4），tier 分层+预算换将齐备。无需改。

### B. 插件市场——六源在位、闸门齐、20 包零孤儿，本班零安装

- 六源实读：market_remote.py:50-75 SOURCES（zcode/anthropic/anthropic-skills/claude-skills/clawhub/cocoloop）；缓存盘点 810 条（anthropic 315/clawhub 215/cocoloop 150/claude-skills 99/zcode 26/anthropic-skills 5）。
- 闸门全在位：SSRF `assert_public_url`（:113-134 仅 https+环回/私有/保留段全拒，:146-156 重定向逐跳重过闸）；纯技能白名单 `inspect_tree`（:568-588 白名单外内容不参与检查与安装）+ `build_files`（:941-952 白名单与包内容不匹配拒装）；zip+sha256 分级标注 verified/unverified（:980-990）；安全解包段字符白名单（:632-635）。
- 已装 20 包文件在位性 20/20（data/market.json ↔ data/skillpacks 逐包核对，零孤儿零缺失）。
- 本班新面孔 5 件按接入三问逐一判定：docker/docker-agent（平台级 runtime 整仓）、tingly-box（平台级聚合编排）、openqodex（push 前门禁整套）、guarded-agentic-compaction（论文研究库）、aspire-creative-writer（微型 demo）——重合度/可直读性/用户会搜吗三问全不过 → **不接、雷达跟踪**；compaction 论文方向（「编译例程、拒绝不确定」）归 A 专项学术参考。零安装动作。

### C. 任务类型——18 型三处（flows/i18n/UI）全对齐，需修：无

- BUILTIN_FLOWS=18（flows.py:37-133）；菜单 `#f-type`/`#type-menu` 为占位、由 /api/flows 动态渲染（app.js:443-460/:668-689）——结构性保证菜单覆盖=后端清单，无写死类型。
- 逐型核对：18 型 i18n 名/goal_hint/note 全齐（direct i18n.js:1358、code :1272、novel :1918…bid_doc :1930）；54 个评审维度翻译在位（:1933-2042）。
- 参数钳位一致：f-chapters min2/max20=flows.py:164、f-words-per-ch 500-8000=:165、f-variants/f-branches 1-3=:180-186；bid_doc 阈值 7.5 预填正确（app.js:781）。
- 三处「看似缺口」核实为设计：快捷 chips 有意 5 枚（app.js:878-882 注释）；推荐规则 17 型+direct 兜底（:837-871，注释明示）；翻译目标语言/演示页数/演讲时长走 goal_hint 占位引导（app.js:775），非缺陷。

### D. 经验库——零重复、抽查零误分，43% 偏科系来源结构

- 70 条实读（data/skills.json）：流程规范 30（43%）/节奏爽点 16/情节逻辑 10/人物塑造 7/一致性 4/文笔风格 3；scope 全 `*`；字段 11 个闭集在位。
- 去重：标题归一化（去空白/标点+lower）后重复组=0。
- 误分类抽查：抽样 40 条逐条比对——节奏爽点 16 条全钩子/爽点/节奏主题，流程规范抽样全流程纪律，零明显误分。
- 偏科结论：43% 系来源结构（教训产出主体为自我迭代代码任务，天然产流程规范），非分类器故障；不批量迁移（合并前保留来源与现有数据纪律）。knowledge.json 知识库 12 条全 approved。
- 待深挖：章末钩子族 4 条（必须留强钩子/松散/防自答/类型轮换）语义近邻但侧面不同，不强合。

### E. 新 CLI——catalog=14、本机 13/14，候选五 CLI 全 MISSING 零盲接

- catalog.py:32-215 DEFAULT_CATALOG=14 条实读（codex/claude/opencode/qwen/aider/openclaw/kimi/mimo/grok/pi/dsh/gemini/cbc/trae-cli）。
- 本机 `command -v` 探测：13/14 INSTALLED（aider/trae-cli 走 ~/.local/bin，其余 nodejs 目录）；openclaw MISSING。
- 候选 DeepSeek-Reasonix/fuxi/gitlawb/zero/empryo 全 MISSING → 维持零盲接（防死链），只列待实测，不添加目录。

### F. 禅道——调度链在位、poll off 零积压，本班全程只读

- 调度链：automation.py:580-583 `_tick`→`zentao.fire_due`（内部自节流，未启用零开销）+ main.py:3446 `zentao.start()` 启动加载，挂点与定时发布同模式。
- 运行态只读三看（data/zentao.json）：poll_enabled=False、claims=0、last_error=空 → **零积压**；last_scan=2026-09-21 20:43:44（停用期不扫属预期）。poll 重开维持交拍板件，本班不动。
- 产品档案路由：`_profiles`/`_profile_for`（zentao.py:307-342）数据驱动路由在位；回写链路齐：resolve 幂等 `_ensure_resolved`（:1708，已 resolved/closed 视为成功）、resolve 前未提交改动拦截（:43-44）、转派评论（:1672）、失败不评论只回炉 retry（:35-37）、群通知。
- 本班未触发 scan_now/真实修复/resolve/群通知；禅道 AI 集成竞品 npm 通道第 33 例零新（第 1/4 步已录）。

### G. 产品巡检——2 处确定要修（已独立复核），2 处存疑，10 项核查通过

确定要修（两处均本班亲读复核）：
1. **app/ui/index.html:13 iOS 状态栏 meta 畸形**：`<meta name="apple-mobile-web-app-status-bar-style"="black-translucent">` 缺 `content=` 属性名，整条标签被浏览器忽略，iOS PWA 状态栏样式失效。全文件仅此一处畸形。
2. **README.md:26-30 relnotes 块外残留 v0.1.65 旧更新段**：与标记块内 v0.1.95（README.md:36）并存，npm/GitHub 渲染出现「最新版是 v0.1.65」误导段。

存疑待拍板（本班不动）：帮助章市场来源枚举子集写法（app.js:13509 带「等」字，v0.1.87 曾修同型漏改，可纳入提示行同一条对账）；i18n.js:2580/:2584 用户可见文案暴露 TUTTI_* 旧代号 env 名（与实现一致，改名有兼容成本，产品层决定）。

核查通过 10 项：本地引用零断链（style/manifest/js/qrcode/artdiff/icons 7 个/assets 3 张全在）、页内锚点 52 个 `#i-*` 差集为空、无硬编码版本号（关于页 API 动态）、无单源/双源残留、无占位文案、GitHub/QQ群/打赏码外链两处一致、README 图片齐全、data-i18n 551 键差集为空、onclick 无死按钮、manifest.json 一致。

### 候选路线图（第 3/4 步落地件，均有证据可独立验证）

| 提案 | 修改文件 | 实际位置 | 预期行为 | 验收用例 |
|---|---|---|---|---|
| 一（G-1） | app/ui/index.html | :13 meta 行 | 补 `content="black-translucent"` 属性名，iOS 状态栏样式标签生效 | 改后该行含 `content="black-translucent"` 且全文件无 `"=" ` 畸形模式；五道关照走 |
| 二（G-2） | README.md | :26-30（含尾随空行） | 删除标记块外 v0.1.65 旧更新段，「最新版更新内容」标题全文件仅存 relnotes 块内 1 处 | grep「最新版更新内容」README 命中 1 次（v0.1.95）；`<!-- relnotes:start/end -->` 标记对完好 |

风险与边界：两处均纯文案/标记层——无逻辑改动、无数据变更、无并发/权限面；安全风险为零（不改任何输入处理与网络代码）。存疑 2 处与禅道 poll 重开交拍板，本班不实施，不为凑数量制造改动。
