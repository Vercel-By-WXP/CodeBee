# 2026-10-07 12 时班调研底稿（current-round，新一轮第 1/4 步·全类型竞品调研）

> 开工实录：12:38（UTC+8，时区 Asia/Shanghai，hour=12，12%7=5 → 轮换批5「检索/知识/浏览器」）、
> 分支 main（678baa4，v0.1.91 已发版）、工作区干净（v0.1.91 发版补录已入库）。
> 通道：gh api 认证可用（主扫串行 sleep 4s、repos 端点复查 sleep 0.4-0.5s）、
> WebSearch 串行 1 发、trendshift curl 直抓可达（330KB）。证据底稿即本文件。

## 主扫描与批次判定

- **主扫**：`scripts/borrow_scan_nightly.py` 无参全量（自动选批 B5）——A 常驻 89
  （含内置 B1 11）+ B5 轮换 10 + A1u/A1p2 双轮 16 = **115 查询 524 行 458 唯一仓，
  exit 0、PROGRESS DONE 115 实证，零 403 零 FAIL**。
- **主扫面过筛**（全名正则对 docs/borrow-log/ 全历史）：229 已录 / 229 首见——首见
  绝大多数系**全名/简称形态差异**（firecrawl/dify/langchain/ragflow/crewAI/LibreChat/
  khoj/composio/mastra/planning-with-files/agent-os/OpenSandbox/gsd-2 等正文已录族），
  逐一简称反查（invisible_playwright/BrowserSkill/Agent-Reach/dexter/career-ops/
  sparrow/startup-skill/obscura/agency-agents）**全部多份历史在录**——
  **主扫真新面孔仅 trendshift 通道 3 件**（见新面孔节）。

## B5 轮换域逐词头部（10 词，本班域）

| 查询词 | 头部命中（top3） | 判定 |
|---|---|---|
| agent+rag | dify 157,979 · langchain 147,511 · awesome-llm-apps 140,889 | 已录族 |
| deep+research+agent | khoj 37,584 · gpt-researcher 29,930 · dexter 27,640 | 已录族 |
| browser+use+agent | invisible_playwright_mcp 31,780 · BrowserSkill 8,219 · oxylabs-ai-studio-py 3,397 | 已录族 |
| web+scraping+agent | firecrawl 189,256 · invisible_playwright_mcp 31,780 · obscura 28,610 | 已录族 |
| search/retrieval agent | Agent-Reach 92,722 · career-ops 73,651 · LibreChat 45,350 | 已录族 |
| document+understanding | MDocAgent 358 · -L- 161 · aarambh-studio 110 | 微型域 |
| data+extraction+agent | sparrow 5,224 ·（COVID 序列仓噪声）· quant-mind 3,047 | 已录/噪声 |
| competitive+intelligence | startup-skill 1,172 · agents 586 · competitive-intelligence-multi-agent 48 | 判据级 |
| citation+verification | 全 0-4★ 微型 | 微型域 |
| knowledge+base+quality/rag eval | prod-evals-cookbook 59 · LLM-Wiki 24 | 微型域 |

**批5 域结论：稳定期延续**（与批2/3/6 域轮过均稳同型），零机制级新差量。

## WebSearch 串行交叉验证（1 发，批5 域定向）

- 查询：deep research agent / RAG knowledge base / browser use 开源面。
- 捞出 5 名全部 repos 二次实证且**全已录族**：gpt-researcher 29,930 /
  langchain-ai/open_deep_research 12,683 / infiniflow/ragflow 91,746 /
  browser-use 117,305 / langchain-ai/deepagents 29,975——
  **双通道结论一致（批5 域稳定期），无单通道盲区**；无新仓名需勘定。
- 新闻面线索「tier-adaptive 16-step deep research pipeline for Claude Code」
  （openapps.pro）无仓名不可 repos 实证，如实记待后续班词组验证。

## trendshift 根页 30 仓（curl 330KB 可达，候选逐件 repos 实证）

- **cursor/plugins 10,136★**（created 2026-01-23，pushed 10-06）——Cursor 官方
  plugin 规范+官方插件集；组织矩阵顺扫（orgs/cursor/repos 零搜索配额）再掘
  **cursor/community-plugins 4,001★**（社区插件集）+cookbook 4,119/sdk-bridge 83/
  plugin-template 96/mcp-servers 227。见新面孔节定性。
- **Raja0sama/vibex 252★**（created 09-19 新锐）——代码库→架构图（ERD/C4/API/
  lifecycle）+可核验文档。同族第二信号（diagram-design 43,915 之后两周内第 2 件
  「代码→架构图」）。
- 已录复认放量：tigerless-labs/autoharness、morluto/rea、openai/math、
  alchaincyf/huashu-art-motion、lexmount/moli 11,425、robbietilton/Compositor 9,721、
  storytold 纯净室全家桶 6 件、chengyi-ai/native-subtitle-quote-image、
  anthropics/knowledge-work-plugins、Ebony-Vinyl/dsh-our-free-model。
- 排除件（域外噪声如实记）：crimera/piko（TG/IG 补丁）/ KingKongRobotics/jumper
  （机器人）/ DuarteSantos8/openGym（健身）/ DictionLabs/Diction（语音键盘）/
  boykopovar/AnyPS5 + Supermedo/bloodborne_pc（游戏移植）/ scholay/rimes（输入法，
  09 时班已排除复见）/ 34306/vphone-aio、mars-tw/anti-gambling-trader-tw、
  joshuaswarren/omarchy-apple-dev、eternity4719/HowToLiveBetter（09 时班同批排除复见）。
- 新微型判据：kaixinit/Codex-Pets 0★（当日建，桌面宠物趣味件——我方 pet.py 形态
  同域趣味对照）/ rbrus/agent-redteam-benchmark 1★（7 件红队工具集清单，A6 评测域旁）。

## 本班新面孔定性（三门槛：重合度/可直读性/用户会搜吗）

| 仓 | stars/pushed | 来源 | 机制亮点 | 对比 | 结论 | 日期 |
|---|---|---|---|---|---|---|
| cursor/plugins（+community-plugins） | 10,136+4,001 / 10-06 | trendshift+orgs 顺扫 | Cursor 官方 plugin 规范+官方/社区插件集（spec 化定义插件清单/安装/权限） | B 专项（插件市场）域旁大件：竞对官方插件生态规范 vs 我方六源市场+白名单闸门+SSRF 防护；规范层面（plugin spec 如何声明权限/入口）可对标 | **跟踪不接入**（非六源清单、依赖 Cursor 宿主，三问②③不过）；spec 规范面对标价值记录，交第 2 步 B 专项核对 | 2026-10-07 |
| Raja0sama/vibex | 252 / 10-06 | trendshift+repos | 代码库静态分析→ERD/C4/API/lifecycle 架构图+可核验文档 | tech_proposal/doc/presentation 域：与 diagram-design（43,915，10 时班录「借鉴方向」）同族第二信号——「代码→架构图」两周两件，方向信号增强；我方无图表生成能力系增量面 | 判据（同族第二信号，跟踪；借鉴判断与 diagram-design 合并拍板） | 2026-10-07 |
| rbrus/agent-redteam-benchmark | 1 | trendshift+repos | 7 件 AI 红队工具集清单（garak/promptfoo/DeepTeam/PyRIT 等） | A6 评测域旁微型判据 | 判据（微型） | 2026-10-07 |
| kaixinit/Codex-Pets | 0 | trendshift+repos | Codex 桌面双宠物（离火心焰/橘猫）当日建 | 我方 pet.py 宠物形态同域趣味对照，0★ 微型 | 判据（微型） | 2026-10-07 |

## 存量复查（repos 端点 34 件批量，13:0x-13:1x，全 alive 零 archived；vs 09 时班间隔约 3.5 小时）

- **头部增量**：orca 86,553→**86,624（+71 续领跑）**/ superpowers 296,028→296,064 /
  claude-mem 97,200→97,257 / hermes-agent 251,714→251,749 / ponytail 156,868→156,954 /
  opencode 212,054→212,074（**属主迁移勘定：sst→anomalyco/opencode**，gh api 301 跟随
  实证——「仓名/属主迁移勘定」第 N 例入库）。
- **放量族**：**rea 9,577→10,132（+555 放量加速，当日第二波）**/ **openai/math
  2,250→3,905（+1,655 爆量——created 10-06 新仓 24h 近翻倍，OpenAI 官方 org）**/
  autoharness 8,972→9,156（+184 放量续，「使用轨迹→SKILL 蒸馏」同族观察信号续强）/
  Strata 15,802→15,989（+187）/ mattpocock/skills 278,184→278,328 / anthropics/skills
  179,754→179,943（+189）/ knowledge-work-plugins 26,468→26,552（+84）。
- **写作/翻译域**：yomiyasu 1,621→1,648（+27 翻译腔标的续）/ webnovel-writer
  7,335→7,336 / ainovel-cli 2,110→2,111 / huobao-drama 15,790→15,794 / drama-skills
  持平族。
- **持平族**：DeepSeek-Reasonix（esengine）35,742（E 候选首位维持）/ letta 25,057
  pushed 09-10 停更近月维持 / mem0 66,701→66,719（+18 在动）/ hippo-memory 773 /
  bernstein 1,416 / heym 1,404 / nautilus-compass 1,255 / RSIAgent 466 / eve 5,475→5,477 /
  bifrost 8,588→8,591 / beads 27,687→27,695 / gascity 1,331→1,332 / agentmemory
  29,185→29,188 / OpenSpec 71,163→71,186 / diagram-design 44,062→44,155 /
  OpenMontage 64,702→64,747 / openrig 5,524→5,546。

## 雷达 C 全过（逐源证据）

| 源 | 通道 | 结果 |
|---|---|---|
| awesome 清单 19 源 | gh api repos 逐个（0.4s） | **19/19 alive 零 archived**（vivy-yi 79 / ComposioHQ 76,613 / harness-engineering 4,734 / awesome-mcp-servers 95,885 / bradAGI 1,319 / e2b-dev 30,285 / awesome-llm-apps 140,888 / hesreallyhim 55,173 / VoltAgent skills 35,287 + papers 1,824 / buildwithclaude 3,601 / RUC-NLPIR 1,064 / TeleAI 658 / caramaschiHG 1,923 / vijaythecoder 4,388 / TsinghuaC3I 665 / Engineering4AI 288 / EvoMap 234 / IAAR 1,258——数值微增在动） |
| Trending | trendshift.io curl 直抓 330KB | 30 仓全解析（见上节；WebFetch 通道未试走 curl 成功） |
| topic 页 8 | gh api search topic: pushed:>2026-10-01 sort=updated | **全微型零新大件**（desplega-ai/agent-swarm 861 复认 / omnigent 10,634 微增复认 / Misaka-Agent 125 / adhdev 103 / manim-skill 165 / iphone-use 82 等微型复见） |
| 发行渠道 npm | npm search 两查 | 已录族复见（nax/agent-orchestrator-mcp-server/gm-orchestrator/@extropolis/claudia），零新大件 |
| 发行渠道 pypi | curl 直抓 | **第 11 班复认受阻**：HTTP 200 3,038 字节 CSP 挑战壳页零 snippet——通道持续不可用如实记 |
| 框架周边搜 | 重合裁定 | langgraph/crewai/autogen 平台词与批6 全重合（06 时班跑过）——按「重合跳余页省配额」不重跑 |
| 自家 CLI 周边 3 查 | gh api search | **affaan-m/ECC 274,393 属主勘定**（历班悬置件）/ cockpit-tools 18,679 / skills-manager 5,654 / multica 族复认；DSH 生态：deepseek-harness 244,723 / awesome-dsh-plugin 17,936 / dsh-web 8,442 复认在录 |
| 禅道周边 2 查 | gh api search | 头部大盘噪声+clickup-ai-bug-triage 0★ 复见——**第 25 例零新禅道 AI 竞品**（09 时班第 24 例顺延） |
| 新锐轮 | 主扫内置 A1u 8 组 | 首见以微型/形态差异为主，真新面孔集中于 trendshift 通道（本班特点） |

## flows.py 预置类型核实（任务口径 13 种 vs 枚举 14 项的差异说明）

- **flows.py:36-128 `BUILTIN_FLOWS` 实读 = 18 种**：direct/code/novel/serial_novel/
  article/video_script/doc/translation/rank_scan/defect_retro/research/speech/
  presentation/weekly_report/email/tech_proposal/resume/bid_doc（逐条 name+engine+
  rubric 实读；与 10 时巡检班实测一致，零漂移）。
- **差异说明**：任务总述称「13 种」、枚举 14 项；枚举中「禅道工单」在 flows.py 无
  对应预置流程（禅道系 F 专项集成：zentao.py 定时扫描激活 Bug→自动建 code 修复任务，
  defect_retro 缺陷复盘流程接受禅道导出数据但本身是复盘报告流程）；flows.py 实际
  比枚举多出 **doc 文档/presentation 演示文稿/defect_retro 缺陷复盘/resume 简历/
  bid_doc 标书编制** 5 种。本报告按实际 18 种做覆盖矩阵，不自行删项。

## 全类型覆盖矩阵（实际 18 类型 × 本班证据通道）

| 类型 | 注册 id | 本班证据通道 |
|---|---|---|
| 直接执行 | direct | A1/A2 编排域主扫+detent/reevesagents 已录族复认 |
| 代码 | code | B1 内置 11 组（costrict 4,446 已录复认） |
| 小说 | novel | A7 novel 组（webnovel-writer 7,336/ainovel-cli 2,111 持平复测） |
| 连载小说 | serial_novel | A7 consistency 组+A12 chapter hook（域沉寂维持） |
| 自媒体文章 | article | A7 article 组（域平稳）+native-subtitle-quote-image 配图 skill 复认 |
| 文档 | doc | vibex 新面孔（代码→可核验文档）+knowledge-work-plugins 域旁 |
| 翻译 | translation | A7/A10 translation 组（yomiyasu 1,648 放量续） |
| 扫榜选材 | rank_scan | trendshift 30 仓+topic 8 页（扫榜通道即产出源） |
| 缺陷复盘 | defect_retro | A13 defect retro 组+禅道周边 2 查（第 25 例零新竞品） |
| 调研报告 | research | A10/B5 deep research 组（gpt-researcher/open_deep_research/ragflow 全已录复测） |
| 演讲稿 | speech | A7 speech 组（域平稳） |
| 演示文稿 | presentation | A10 presentation 组+diagram-design/vibex 图表域信号 |
| 工作汇报 | weekly_report | A10 weekly report 组（微型为主） |
| 商务邮件 | email | A10 email 组（域长期微型维持） |
| 技术方案 | tech_proposal | A5 spec-driven 组+vibex 架构图新面孔 |
| 简历 | resume | career-ops 73,651（求职 agent 已录族复测，域旁） |
| 标书编制 | bid_doc | A13 组域沉寂维持（历班同） |
| 对话/知识库（补充） | — | B5 全域+WebSearch 交叉验证（batch5 域结论稳定期） |

## 七专项轻量实测（本班第 1/4 步只读面）

- **E 新 CLI**：command -v 实测 dsh 在位（catalog 已接）；reasonix/deepseek-reasonix/
  fuxi/gitlawb/zero/empryo/openclaw **全 MISSING**——零接入防死链维持（与 10 时班一致）。
- **B 市场**：cursor/plugins+community-plugins 系 Cursor 宿主插件非六源清单，
  三问②③不过——跟踪不接入零绕闸（定性见新面孔节）。
- **F 禅道**：周边竞品第 25 例零新（本班 2 查）；定时扫描与积压状态归第 2/4 步巡检班
  实测（10 时班 triage_ai=true/claims=0 快照在档）。
- **A token/D 经验/G 产品**：归第 2/4 步巡检班；本班零代码改动。

## 失败请求与未完成项（如实记）

- pypi 通道第 11 班受阻（CSP 壳页）——非本班可解，维持历班口径。
- WebSearch 线索「16-step deep research pipeline」无仓名未勘定（无 repos 可实证）。
- 框架周边词按重合纪律跳跑（批6 06 时班刚跑）；topic 页取 pushed:>2026-10-01
  近周更新向 per_page=4（配额让路主扫），非历班 sort=stars 全量口径——零新大件
  结论与历班一致，风险低。
- ECC/pi 两件历班悬置属主：ECC 本班勘定（affaan-m）；pi 仍未勘定（grok/pi?
  候选属主均 404），留后续班 in:name 勘定。
- 主扫 JSONL 原始件存 /tmp/scan_12h.jsonl（临时区，不入库）。

---

# 2026-10-07 12 时班第 2/4 步（七专项巡检，13:2x 开工）

> 元信息：分支 main（678baa4）；工作区在制品 = 本轮第 1 步 docs 两件（current-round.md
> / knowledge.md）；pipeline.py/zentao.py/market*.py 均为 HEAD 净态，行号即 HEAD 口径。
> 取证方式：pipeline.py（5721 行）与 zentao.py（2692 行）+market*.py 由两个并行只读
> 代理逐项实证（file:line 证据），flows/catalog/skills/knowledge/compaction/token_meter/
> settings_schema 与 UI 三件由主线程直读；data/skills.json、data/knowledge.json、
> data/zentao.json、data/market.json 实测统计；CLI 用 command -v 逐个探测。

## A · token 节约（调用链全实证；八件机制全在位，两件灰度默认关）

- **三段压缩**：pipeline 不直调 maybe_compact——`pipeline.py:703-706` 灰度开关
  （`_compaction_enabled()` = env TUTTI_COMPACTION 或 orchestrator.compaction.enabled，
  **默认 False**，settings_schema.py:226）开启后经 `step_runner.execute_step` 生效：
  事前预检 `step_runner.py:78-87`（last_context > capacity×0.9 先压再发）+ 事后
  溢出重试 `:92-103`（CONTEXT_OVERFLOW/MAX_TOKENS → maybe_compact，surface 前进
  才重试一次）；重试真正换上下文 `:104-128`（derive_messages 截尾 24k 弃 resume）。
  llm_caller 复用当前 step 的 agent（pipeline.py:260-267，readonly+timeout 300）。
- **token_meter**：accumulate 两路兜底（pipeline.py:722-724 压缩路径 / :740-744 直通
  resume 路径，防 used 恒 0）；pressure_ratio/last_context 消费方在 compaction.py:193
  与 step_runner.py:78；容量表可热改（data/model_capacity.json，token_meter.py:37-50）。
- **预算熔断**：token 闸 pipeline.py:685-701（budget.max_tokens_per_run，默认 0=不限）
  + 花费闸 :648-669（daily/monthly_cost_yuan 按 ¥ 折算）先于 token 闸命中，均返
  ENV_BLOCK 只拦下一步不杀当前步；同因连撞由 repeat-guard 止损。默认全 0（不限）。
- **cascade（廉价模型分流）**：唯一接点 pipeline.py:1520-1536——difficulty==easy 且
  `cascade.enabled`（**默认 False**，settings_schema.py:262-267）时 capability.py:100-125
  按 tier(budget/standard/premium) 稳定升序重排 call_chain（FrugalGPT 式）；无显式
  升级二跳，质量闸不过走既有 repair/换将等效兜底。
- **经验召回**：pipeline.py:3657（起草）/3191（连载评审）注入 skills.block_for；
  预算 MAX_INJECT_CHARS=9000、MAX_LESSONS_INJECT=8、通配包单包 2400，教训保底
  完整注入（skills.py:740 budget=max(600, 9000−教训长)）；降级另受 4K 硬截
  （pipeline.py:2551-2554）。知识块独立预算 3000/条数 6（knowledge.py:37-38）。
- **会话复用**：跨 run 声明式 task.resume（pipeline.py:206-228 校验+workdir 约束，
  :270-286 sid 解析，codex/claude/opencode/qwen 原生、generic 靠 catalog resume 模板）；
  run 内 sid 复用链完整（impl_sid :1407/:1738 fix 轮、critic_sids :3618、draft_sid
  :3850→:4112 revise）——前缀缓存热的字节稳定设计在位（stable_order 注释链）。
- **diff 评审**：CODE_REVIEW_PROMPT（pipeline.py:919-921）「diff 为主要依据」+允许
  只读翻文件核对；_git_diff :948-953（git diff HEAD+未跟踪新文件）；深度分级
  :1019-1033（<40 行快评、≥600 行概览+高风险区）——**非纯 diff-only 但已 diff 为主**。
- **_shrink_context_block 分层降级**：pipeline.py:2538-2578 四层优先级（圣经>模块库>
  经验库>大纲/前情永不动）；仅起草重试且 prompt>12000 才触发（:3730-3737、赛马
  :3858/:3888）；降级说明入 step note 供对账；与压缩灰度互斥（:3698-3699、:4934）。
- **缓存缺口评估**（报告单列）：
  1. **prompt 缓存**：pipeline 侧零响应缓存；modelhub.chat 精确匹配缓存已在
     （modelhub.py:3655-3695，data/chat_cache/，key=供应商+模型+prompt+max_tokens+图
     签名），但生产调用方仅 planner.py:680/:829/:972/:1003（ttl 3600，attempt==1）与
     main.py:1553 连通测试（86400）——pipeline 的压缩摘要/知识提炼系一次性调用，
     接缓存收益趋零。供应商前缀缓存的字节稳定设计（sid 复用+stable_order）是
     已落地的「免费 prompt 缓存」，无新增缺口。
  2. **语义缓存**：全 core 零命中（grep cache_ttl/semantic/prompt_cache 实证）——
     深挖队列第 2 项拍板件，维持攒批不擅动。
  3. **廉价模型分流**：cascade 机制已建默认关（灰度件），非缺口；tier 声明的
     供应商覆盖面是放量前提，交人拍板。
- **A 结论**：八件机制（压缩/计量/双熔断/级联/经验召回/会话复用/diff 评审/分层降级）
  证据齐全、互相咬合；本班零新增机制零代码改动。真缺口仅语义缓存一项且系在册
  拍板件；压缩与级联的灰度默认关是既有部署决策非缺陷。

## B · 插件市场（六源+双层闸门实证；本班新面孔三问不过，跟踪不接入）

- **六源实定**：market_remote.py:50-76 SOURCES（zcode/anthropic/anthropic-skills/
  claude-skills/clawhub/cocoloop），目录缓存 data/market_remote/ 6 文件全在。
- **闸门双层**：(a) 内容白名单=剥离式检查（inspect_tree :568-604：scripts/hooks/
  commands/agents/.mcp.json/可执行扩展一律剥）+条目 skills 清单收敛（build_files
  :941-952）；(b) SSRF=assert_public_url :113-133（仅 https+全 IP 解析拒私网/环回/
  保留段）+每跳重定向重校验 :158-159+上限 3 跳；装前 preview :1030-1084（token
  TTL 600s）+skill_scan 危险模式+typosquatting 近名对账；装后冒烟回写台账。
  与 zentao 的 _guard_url 策略相反（外部严格、用户自配内网宽松）——方向正确。
- **存量实况**：data/market.json installed=20（本地 6+远程 14：zcode9/cocoloop3/
  clawhub2；anthropic 三源 0 装）；落盘 42 个 market-*.md 经 skills 同构解析注入。
- **本班新面孔三问**（cursor/plugins 10,136★+community-plugins 4,001★，第 1 步定性
  交本班核对）：①重合度——高：插件分发/装前审查机制我方六源+双层闸门已覆盖；
  ②可直读性——不过：Cursor 宿主 plugin spec 依赖 Cursor runtime，装进 CodeBee
  跑不了；③用户会搜吗——不过：CodeBee 用户搜的是 claude/codex/opencode 生态。
  **三问②③不过→不接入，雷达跟踪**；spec 层面（权限/入口声明）留对标素材。
- **B 结论**：零绕闸零接入；本班无可装新件。

## C · 任务类型（18 型逐一对照 UI/i18n/README，零漂移）

- **flows.py:36-134 实读 18 型**（16 review+2 direct），参数复核：threshold 全 7.0
  仅 bid_doc 7.5（刻意从严）；serial 钳位 2-20 章/500-8000 字（:164-165）；_EDITABLE
  白名单 12 字段（:140-142）不含 icon（预置图标跟版本走）。
- **UI 对照**：app.js flowDesc :434-441 按引擎分型描述+rank_scan 短描述「抓四平台榜」
  与 paihang.py:97-101 四源（七猫/番茄/起点/纵横）一致；i18n.js 18 个类型名+goal_hint
  抽样全有英文键（脚本核验 NONE missing）；index.html 流程编辑器字段（fl-note 等
  :10942-10964）与 _EDITABLE 对齐；连载章节输入 f-chapters（index.html:511）钳位同
  后端。
- **守卫链**：test_borrow_round.py:127 / test_borrow_round_regressions.py:172/:304
  均断言 len==18；test_i18n_dups.py:70 动态遍历 BUILTIN_FLOWS 三字段——后端加型
  漏翻会在测试报错。README.md:136/:243「18 种」与实际一致。
- **C 结论**：菜单描述/流程参数/辅助信息三面零漂移零过时，本班零改动。

## D · 经验库（69 条新基线：精确零重复+近似零对，无新增合并项）

- **实统计**（data/skills.json）：69 条全 enabled（+1 vs 07 时班 68 基线）；精确同题
  零重复；bigram 包含度 ≥0.8 近似对**零对**（全 scope 两两扫）——04 时班提案 1 落地
  （管理通道）后库貌清洁，无新增合并建议。
- **分类**：节奏爽点 16/流程规范 29/情节逻辑 10/人物塑造 7/一致性 4/文笔风格 3——
  全部落在 LESSON_CATEGORIES 六枚举内（零未分类零错位回漂）；**流程规范占比
  42%（29/69），较任务口径的历史 61% 已收敛**，偏科放缓但仍是最大类，观察维持。
- **scope 分布**：serial_novel 41/*/12/code 11/direct 4/article 1——其他 15 型零
  沉淀，系使用频度自然分布非缺陷（learn_from_run 按任务 type 落 scope，:467）。
- **知识库账龄观察**（同门 knowledge.py 侧）：data/knowledge.json 12 条 as_of 全部
  早于 2026-07-09——**全量超 STALE_DAYS=90 天**，注入时将全部带「可能过期」标注并
  排序降权（knowledge.py:296-301）；影响面仅 serial_novel/direct 两 scope。属数据
  老化非代码缺陷，过期标注机制已正确兜底，不擅改。
- **D 结论**：零重复零错位零合并项；本轮调研无新方法论需蒸馏入库（批5 域稳定期，
  与第 1 步结论一致）。

## E · 新 CLI（13/14 在位；候选全 MISSING 零接入维持）

- **catalog.py 实读 = 14 条**（DEFAULT_CATALOG :32-215：codex/claude/opencode/qwen/
  aider/openclaw/kimi/mimo/grok/pi/dsh/gemini/codebuddy/trae）——任务口径「已接
  11 个」**已过时**（codebuddy/trae-agent 系后续新增），勘误记录。
- **command -v 逐个实测**：13/14 在位（codex/claude/opencode/qwen/aider/kimi/mimo/
  grok/pi/dsh/gemini/cbc/trae-cli 全有路径）；**openclaw MISSING**（installable 态
  正确，未装不参与编排）。
- **候选实测**：reasonix/deepseek-reasonix/fuxi/gitlawb/zero/empryo **全 MISSING**
  ——与 07/10 时班同口径，**零接入防死链维持**（未安装未实测不进目录）。
- **E 结论**：零接入；DeepSeek-Reasonix（esengine，35,7★ 保持 E 候选首位）待本机
  实测后再议。

## F · 禅道集成（结构可验证运行；poll 显式关系部署决策，本班零触发）

- **链路全实证**（zentao.py）：automation.py:580-581 每 tick(25s) 调 zentao.fire_due()
  → _poll_unlocked :2414（先 _reconcile 对账 :2093，到点才 _scan :2332）→ list_bugs
  :1164（REST 下推 active+assignedTo 过滤）→ _claimable :1151 → _route_one :2128 →
  _triage :1484（module_routes > AI 判端 :1386 > unknown 留人工）→ _launch_fix :1556
  （create_task(code)+create_run+enqueue :1588-1592）；回写链 _finish_ok :1912（合并
  :1924-1936→转派 :1952-1988→resolve+评论 :1989-2011）+群通知 :1754-1759；失败重试
  :2252/:2281。
- **配置实况**（data/zentao.json）：凭据三项齐（base_url=http://10.143.132.5:8899
  内网自建，_guard_url :163-188 按设计放行私网）；产品档案 1 个（product=96/
  our_sides=[backend]/assigned_to=wuxinping，module_routes=0 → 定责全靠 AI 兜底，
  triage_ai 默认 True 需 modelhub 可用模型）；配置系老键 interval_hours（load() :271-279
  幂等迁移，不阻塞）。
- **运行状态**：last_scan=2026-09-21 20:43:44 成功、last_error 空、**claims=0 零
  积压**；**poll_enabled=False**——定时扫描显式关闭近半月，与 04/06/07/09/10 时班
  「部署配置缺位非代码缺陷」同口径，交人拍板维持；手动通道（/api/zentao/scan
  main.py:1779+UI 立即扫描钮）可用。
- **本班纪律**：不触发真实扫描/工单修改（避免对真实禅道实例产生副作用）；竞品面
  第 25 例零新（第 1 步已录）。风险在档维持：data/zentao.json 明文密码（交人拍板）。
- **F 结论**：代码链路健康零改动；可验证路径=先 /api/zentao/test 探连通再手动扫描，
  待 poll 启用拍板后一并做。

## G · 产品巡检（零新毛病，零随手修）

- **设置子页**：18 个 data-sub 全有归属（__guide/__phone 系弹框特例，app.js:15190-15191
  显式分流，非断链）；图标 sprite 引用零断链（脚本全量比对 href="#i-*" vs id 定义）。
- **版本三方一致**：package.json 0.1.91 = npm registry 0.1.91 = CHANGELOG 未发布节
  正常置顶。
- **文案与实际**：README「18 种任务类型」= flows 18；paihang 四源与 rank_scan 文案
  （「四平台」）一致——历史「单源/双源」过时文案零残留；「已接 11 个 CLI」仅任务
  口径过时（E 节勘误），产品内文案无此说法。
- **G 结论**：与 07 时班同口径零新毛病。

## 落地件选定（交第 3/4 步实施，最小修改清单）

- **候选甲（唯一小而实，D/A 交叉产物）**：**knowledge.py block_for 注入截断的反馈
  回路缺陷**——两处真实问题同源：
  1. **拦腰截断**：knowledge.py:319-321 `text[:KNOWLEDGE_BUDGET]` 硬截可把最后一条
     知识拦腰截成半句（单条最大 = 标题 80+标注+body 1500 ≈ 1600 字，6 条上限
     KNOWLEDGE_MAX_INJECT=6 理论可达 ~9600 字 >> 预算 3000——截断是常态路径不是
     边界），半条知识是模型噪音且浪费预算。
  2. **hits 虚高自增强**：used 列表在截断**前**收集全部条目 id（:317-318），被截掉
     内容的条目照常 _bump_hits（:322-323）；而 hits 是排序决胜因子（:299
     `-(int(x.get("hits") or 0))`）——**从未被模型真正读到的知识反而升权**，下次
     更靠前、继续被截、继续涨热度，挤压真正可读条目。对照 skills.py 同职能代码
     教训「永远完整注入」+包区按预算截断的既有纪律（skills.py:740-747），
     knowledge 侧缺同款保护。
  - **改法**（单函数，无新抽象）：block_for 组装循环改为按序整条装箱——当前条
    完整放得下才收入，放不下即停，末尾加「…（N 条超出预算未注入）」标注；used
    只收实际注入条目（截断丢弃的不 bump hits）。单条必装得下（body ≤1500 <
    预算 3000−header），无「第一条就超预算」边界。
  - **文件与函数**：app/core/knowledge.py `block_for`（:277-324，仅尾部组装段
    :305-324 动刀）；新测试 tests/test_knowledge_block_budget.py（git add -f）。
  - **行为前后**：前=超预算时 text[:3000] 硬截+全 6 条 bump；后=整条装箱（≤3000）、
    丢弃条目带数量标注+不 bump。
  - **验收用例**：①3 条 body 各 1400 字的 approved 条目 → 输出含前 2 条整条、
    不含第 3 条任何半句、尾部有未注入标注、第 3 条 hits 不变前 2 条 +1；②总长
    不超预算 → 输出与现状逐字节一致、hits 照常 +1；③全量 unittest 不回归。
  - **风险**：低（单函数、有既有测试文件模式可循、无接口变更）。
- **候选乙（备选，不并做）**：无——A/B/C/E/F/G 五面本班均零缺口零毛病，凑数件
  不做；语义缓存/工具输出压缩系在册攒批拍板项，本班纪律不碰。

## 本班结论与交接

- 七专项全部有实证有结论：A 八件在位（缓存缺口=在册拍板件）、B 零绕闸、C 零漂移、
  D 零重复（新基线 69）、E 零接入、F 链路健康待拍板启用、G 零毛病。
- 落地件候选甲已写清文件/函数/行为前后/验收用例，交第 3/4 步评审实施；
- docs-only 班不发版（05/06/01/04/09 时班先例；当日已有 04 时落地班代码入库，
  发版判定归第 4/4 步按锚定清单裁定）。

---

# 2026-10-07 12 时班第 3/4 步（落地实施，13:5x 开工）

> 元信息：分支 main（678baa4）；实施前 knowledge.py 实读核对（`block_for` :277-324、
> used 截断前收满 :317-318、`text[:KNOWLEDGE_BUDGET]` 拦腰截 :320-321、hits 排序决胜
> :299）——第 2 步计划锚点全部对上，零漂移；`…（已截断）` 标记无任何活代码/测试
> 依赖（grep 仅 Mimosa 基线快照）；`block_for` 四个消费方（planner.py:588/
> bookmeta.py:731/pipeline.py:3196/:3658）只拼接返回串，无长度契约。

## 准确修改清单（锚定清单外落点，按纪律先更新本清单再实施）

- **`app/core/knowledge.py`**（锚定清单外，系第 2 步评审通过的候选甲必要落点）：
  仅动 `block_for` 尾部组装段（:307-324）——整条装箱替代 `text[:KNOWLEDGE_BUDGET]`
  拦腰硬截；被预算丢弃的条目以「…（N 条超出预算未注入）」标注块尾（替代原
  「…（已截断）」）；`used` 只收实际注入条目，被丢弃条目不再 `_bump_hits`。
  其余部分（排序/stale/置信标注/`_bump_hits` 本体）零改动。
- **`tests/test_iteration_regressions.py`**（新建，git add -f）：仅覆盖选中行为
  三组用例——①正常：预算内输出与现状逐字节一致 + hits 照常 +1；②边界：恰在
  3000 预算线整条装箱 + 第 3 条整条丢弃带标注 + hits 反馈正确；③回归：重复调用
  下被丢弃条目 hits 恒 0（切断「从未被读到反而升权」自增强回路）。
- **命名勘误**：第 2 步计划写「tests/test_knowledge_block_budget.py」，本步锚定
  清单指定「tests/test_iteration_regressions.py」——按本步锚定清单命名（同为一
  件新测试文件，内容不变），如实记录。
- **明确不触碰**：锚定六文件中 flows.py/pipeline.py/app.js/i18n.js/index.html/
  style.css 本件零需求零改动（后端单函数修复，无 UI 面）；test_knowledge.py 等
  存量测试零改动。

## 行为前后与已知边界

- **前**：超预算时整块 `text[:3000]` 硬截（最后一条可被拦腰截成半句）+ 被截条目
  照常 bump hits → 热度虚高自增强，挤压真正可读条目。
- **后**：按序整条装箱（≤3000）；放不下即停，块尾带「…（N 条超出预算未注入）」；
  丢弃条目不 bump。
- **边界核实**：body 全部写入路径（upsert_entry :133 / entry_op edit :244）均
  截 1500，单条整行（≤1616）+ 表头 22 < 预算 3000，「第一条就超预算」不可达，
  不加额外分支；若未来出现绕过写入路径的手改数据（>1500 body），首条仍整条
  注入（略超预算但不拦腰截）——数据兼容风险如实记，交人决定是否加防线。
- **风险**：低（单函数、无接口变更、`…（已截断）` 标记无消费方）；安全/权限/
  并发面无新增（`_bump_hits` 仍走既有 _LOCK）。

## 实施记录与清单修订

- **knowledge.py 已实施**（diff 自审过）：仅 `block_for` 尾部组装段——整条装箱、
  `skipped` 计数带「…（N 条超出预算未注入）」标注、`used` 只收实际注入条目；
  排序/stale/置信标注/_bump_hits 本体零改动。
- **清单修订 1（测试命名）**：新测试按本步锚定清单命名
  `tests/test_iteration_regressions.py`（第 2 步计划原名
  test_knowledge_block_budget.py，同物异名，内容覆盖计划验收用例①②③）。
- **清单修订 2（存量测试同步，计划外必要落点）**：`tests/test_knowledge.py`
  :140-150 第 6 例两行断言同步——旧断言 `b.endswith("…（已截断）")` 与
  `len(b) ≤ 80+标注长` 锁死的正是本次修掉的拦腰截缺陷标记；按新契约改为
  `endswith("超出预算未注入）")` + `assertNotIn("已截断", b)`，其余断言零动。
  不改则存量套件必红（首轮实测 FAILED 复现），属「测试锁死缺陷」的必要同步。
- **无 goal 路径核实**：`skills._text_bigrams(None)` 安全返回空集
  （skills.py:590-593 `str(text or "")`），无 goal 时走按 id 排序的确定性装箱，
  测试据此做稳定断言。
- **明确未触碰**：flows.py/pipeline.py/app.js/i18n.js/index.html/style.css 零
  改动（本件后端单函数修复无 UI 面）；knowledge.md 系第 1/2 步在制品，本步
  未动；current-round.md 大额 diff 系每轮重写惯例（开工前已存在的第 1/2 步
  重写），非本步造成。

## 验证命令与真实结果（本步执行）

```
> python -m py_compile app/core/knowledge.py
PY_COMPILE_OK
> python -m unittest discover -s tests -p "test_iteration_regressions.py" -v
runTest (test_iteration_regressions.TestKnowledgeBlockBudget) ... ok
Ran 1 test in 0.135s  OK
> python -m unittest discover -s tests -p "test_knowledge.py" -v
（首轮）test 失败：test_knowledge.py:145 旧断言锁死「…（已截断）」→ 按上节
  清单修订 2 同步两行断言后重跑：
Ran 4 tests in 0.485s  OK
> python -m py_compile tests/test_knowledge.py tests/test_iteration_regressions.py
COMPILE_OK
> python -m unittest discover -s tests -p "test_iteration_regressions.py"
Ran 1 test in 0.131s  OK
```

- JS 面：本步零 JS 改动，node --check 不适用。
- 工作区核对：改动仅 knowledge.py / test_knowledge.py / 新增
  test_iteration_regressions.py / current-round.md（+第 1/2 步在制品
  knowledge.md），无锚定 UI 文件与外来文件改动（git status --short 实证）。
- 交接：全量 unittest discover、逐 hunk 终审、add/-f、commit、push、发版判定
  归第 4/4 步。
