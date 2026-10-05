# 2026-10-06 01 时班调研底稿（current-round，第 1/4 步·全类型竞品调研）

> 开工实录：01:01（UTC+8，时区 Asia/Shanghai，hour=1，1%7=1 → 轮换批1「代码质量与评审」）、
> 分支 main（b6d2269）。工作区在制品 = 20 时班沉淀 + 21 时班节 + 22 时落地班（均未提交），
> 本班增量记录、逐字不动。通道：gh api 认证可用，串行 sleep 4s 纪律。

## 主扫描与雷达 C 覆盖清单

- **主扫**：`scripts/borrow_scan_nightly.py` 无参全量（自动按小时选批 B1）——A 常驻 89
  （含内置 B1 11）+ B1 轮换 11 + A1u/A1p2 双轮 16 = **116 查询 525 行 426 唯一仓，
  零失败零限流**（DONE 116 / errors: 0 实证；唯一仓数 426 系 02 时复核班按
  _scan_uniq.txt 实测勘正，初稿误记 425）。
- **WebSearch 串行交叉验证**（keywords.md 规则：距 21 时班已 4 班，到 3-4 班窗口）：批1 域
  2 发——`AI code review agent GitHub open source 2026` + `open source PR review bot
  self-hosted agent October 2026`。捞出名：PR-Agent / Tabby / CodeRabbit / Kodus /
  AI Gitea Bot——**repos 端点二次实证**：Kodus 正主勘定 **kodustech/kodus-ai 1,449★**
  （10-05 push；`kodus/kodus` 404，search in:name 一次勘定）；**PR-Agent 仓名迁移勘定
  qodo-ai/pr-agent → The-PR-Agent/pr-agent 13,273★**（qodo-ai 属主 404，10-05 push 活跃
  ——「新闻面 ≠ 开源仓在」规则第 4 例，本例为仓名迁移形态）。CodeRabbit 商业闭源、
  Tabby 补全域已录、AI Gitea Bot 微型不入。
- **雷达 C 全过**：awesome 清单 12 源 repos 实测全 alive（ai-boost/harness-engineering
  4,710 / punkpeye/awesome-mcp-servers 95,837 / VoltAgent 35,228 / hesreallyhim
  awesome-claude-code 55,096 / awesome-llm-apps 140,768 / e2b-dev 30,268 / RUC-NLPIR
  1,062 / TeleAI-UAGI 658 / TsinghuaC3I 665 / vijaythecoder 4,388 / buildwithclaude
  3,589；awesome-claude-skills 正主=**ComposioHQ 76,539** 复认在档）+ topic 8 页
  （sort=updated）+ npm 两查（agent orchestrator / claude code orchestrator——已录族
  为主，@nathapp/nax 复认）+ **trendshift 本班可达**（http 200，29 仓，新面仅 inkloom
  域外备注）+ 禅道周边含于批1/B1 主扫。
- **pypi**：省配额未复试（历班六种拦截形态在案，如实记）。

## 批1 域结果（代码质量与评审——已录为主，稳定期延续第 21 班）

- B1 域命中（code review agent / pr review bot / github action ai review / bug
  detection / test generation / refactor agent 等词组）全部落已录族：open-code-review
  43,833 / anthropics/claude-code-security-review 6,304 / pr-af 646 / agentic_security
  2,017 / BugTraceAI-CLI 184 / mira 356 等——零机制级新差量。
- **WebSearch 交叉验证补差 2 件**（主扫盲区实证——Kodus 与 PR-Agent 迁移主扫词组均未
  命中，WebSearch 跨通道价值再证）+ **topic:mcp 捞出 agnix**：见下节。

## 本班新面孔定性（逐条：项目 | 亮点 | 对比 | 结论 | 日期）

- **chunxiaoxx/nautilus-compass**（1,134★，10-05 push，Python，主扫 A13 域捞出）新入库 |
  多智能体可靠性层：「keep agents coordinating without an orchestrator」跨对话契约+漂移
  检测+黑盒记忆（no LLM extraction） | 漂移检测与我们 _scope_note 越范围提醒同域但更广
  （协调不靠编排者）；黑盒记忆与 agentmemory/hippo-memory 同域；我们已有编排者在位，
  「无编排者协调」不同轨 | 雷达 | 2026-10-06
- **kodustech/kodus-ai**（1,449★，10-05 push，WebSearch 捞出+repos 实证）新入库 |
  self-hosted AI code review（AGPLv3、Docker Compose、BYO-model） | 批1 域竞品；我们
  diff 评审链已满配（深读指引/越范围提醒/半成品收工三件 16 时班落地）；AGPLv3 不适合
  接入 | 雷达（批1 域） | 2026-10-06
- **The-PR-Agent/pr-agent**（13,273★，10-05 push）新入库（仓名迁移勘定） | 老牌开源 PR
  reviewer（PR 摘要/逐行评论/自定义指令），qodo-ai → The-PR-Agent 属主迁移 | 机制面对应
  物已满配（评审 JSON findings 契约+pr-af 借鉴在档）；正主迁移形态入库（对照表
  The-PR-Agent/pr-agent） | 雷达 | 2026-10-06
- **agent-sh/agnix**（440★，10-05 push，Rust，topic:mcp 捞出）新入库 | AI 助手配置的
  linter+LSP：校验 CLAUDE.md/AGENTS.md/SKILL.md/hooks/MCP，带 autofix | 我们 skill_scan
  装前扫描系安全向，agnix 系配置语法/规范向——skill 包质量巡检（B 专项）未来可借鉴的
  校验思路；当前不接 | 雷达（B 专项备注） | 2026-10-06
- **ZASENJC/dsh-plugins-store**（68★，10-05 push）新入库 | DSH 社区插件自动分类/收录/
  验证商店 | E 域生态扩散第 2 信号（继 dsh-our-free-model 外部属主后，第三方商店形态
  出现）；微型 | 雷达（E 域） | 2026-10-06
- 微型批（不过三门槛，如实记）：almogdepaz/wolfpack 46★（self-hosted 浏览器终端面板）/
  parallax-labs/context-harness 42★（本地上下文摄取检索）/ amirfish1/
  claude-command-center 177★（Claude Code 命令中枢）/ aarondpn/redmine-cli 51★
  （Redmine CLI，项目管理域微型）/ Inkloom-art/inkloom 1,368★（logo 设计 AI 管线，
  trendshift 捞出，域外）| 判据 | 2026-10-06

## 存量复查（repos 端点 24 仓，01:1x-01:2x，全 alive 零 archived）

相对 21 时班快照（21:4x-22:0x）增量：**orca 85,660（+179 续领跑）**/ superpowers
295,558（+79 无 push 维持）/ **mattpocock/skills 276,863（+157，对 superpowers 差
18.7k 续逼近）**/ ECC 273,439（+113）/ **ponytail 155,778（+182）**/ pi 112,658（+46）/
hermes-agent 251,379（+50）/ opencode 211,843（+18）/ anthropics/skills 179,754（+27）/
open-code-review 43,833（+25）/ herdr 42,456（+44）/ **claude-mem 96,493（+91 放量
持续）**/ agentmemory 29,145（+9）/ SkillSpector 19,438（+14）/ **DeepSeek-Reasonix
35,735（10-05 push，E 候选首位维持）**；写作域 webnovel-writer 7,324（+3）/ ainovel-cli
2,096（10-05 push 活跃）/ **yomiyasu 1,495（+16 在动加速，翻译腔标的持续演进）**/
drama-skills 2,525（+8）/ hippo-memory 772 持平 / huobao-drama 15,750（+24）/ cc-haha
14,867（+5）；治理域 beads 27,646（+3）/ gascity 1,330（+1）。
**trendshift 同源放量**：**morluto/rea 4,253→4,879（+626 当日放量，trendshift 在榜）**；
answer-me-with-html 1,359 持平回落榜外。

## 覆盖矩阵（14 指令项 vs 注册表实数，不静默删项）

用户指令列 14 项一一映射注册类型（flows.py BUILTIN_FLOWS **import 实数 18**，01:0x
实测）：直接执行→direct / 代码→code / 小说→novel / 连载→serial_novel / 自媒体文章→
article / 调研报告→research / 短视频脚本→video_script / 技术方案→tech_proposal /
翻译→translation / 演讲稿→speech / 工作汇报→weekly_report / 商务邮件→email /
扫榜选材→rank_scan / **禅道工单→defect_retro**（18 时班联调勘定口径）；另 4 独有
doc/presentation/resume/bid_doc = **18**。**用户所称「13 种」与源码实数差异如实记录：
13 系任务指令面口径（+禅道工单映射=14 面），注册表 18=指令 14+自研 4——以源码实数为准，
不遗漏任何列举项**。补充雷达三面均已覆盖：对话（A10 chatbot memory 词组+hippo-memory/
nautilus-compass 黑盒记忆）、知识库（批5 词组+Yuxi 在档）、文档（A10 doc gen 词组+
univer 在档）。

## 七专项巡检（本班独立实证，01:0x-01:3x）

- **A token**：锚点全实证在位（_ensure_budget :61 / _budget_max_tokens :613 /
  _budget_cost_caps :632 / cascade :1526-1529 opt-in / _shrink_context_block :2538 /
  _serial_shrunk_block :2581 / stable_order :3141-3142+:3595-3598 / `cache_control`
  全 app/core grep 零命中维持）；四方向判定维持（prompt 缓存供应商侧+语义缓存待拍板
  +diff-only 已有+廉价分流 cascade 已满配）；本班新见 context-harness/summarization
  pydantic-ai 均微型雷达级不改结论 | 已覆盖
- **B 市场**：六源 SOURCES :50-76 在位；缓存 **810** 逐源实测（zcode 26/anthropic 315/
  anthropic-skills 5/claude-skills 99/clawhub 215/cocoloop 150，fetched_at 2026-10-03，
  与 21 时补录班口径零漂移）；本班新见（agnix 校验器/dsh-plugins-store 商店）过三问
  均不过——agnix 系独立 CLI 工具不可直装六源、dsh 系属 DSH 生态不属六源；零接入维持
  | 已覆盖
- **C 类型**：BUILTIN_FLOWS import 实数 **18**（direct/code/novel/serial_novel/article/
  video_script/doc/translation/rank_scan/defect_retro/research/speech/presentation/
  weekly_report/email/tech_proposal/resume/bid_doc 逐项在档）；14 指令项覆盖矩阵全对上
  （见上节） | 巡检
- **D 经验**：data/skills.json lessons=**72** 零漂移（流程规范 26/节奏爽点 21/情节逻辑
  10/人物塑造 7/一致性 4/文笔风格 4，流程规范 36.1% 口径维持）；packs=3；本班零新蒸馏
  （WebSearch 迁移勘定方法论入 knowledge 对照表层） | 数据卫生
- **E 新 CLI**：catalog DEFAULT_CATALOG :32 在位（**14 条目**）；本机 which 按探测名实测
  **13/14 在装**（codex/claude/opencode/qwen/aider/kimi/mimo/grok/pi/dsh/gemini/cbc/
  trae-cli；codebuddy 条目探测名为 cbc——「14 在装」系 codebuddy 与 cbc 同条目双计，
  勘误承 00 时班，本班 import 实测 codebuddy→detect.cli=cbc 复证）仅 openclaw 未装；
  六候选（deepseek-reasonix/reasonix/fuxi/gitlawb/zero/
  empryo）which 全空——零接入防死链维持，Reasonix 35,735★ 候选首位 | 巡检+勘误承00时班
- **F 禅道**：data/zentao.json poll_enabled=False/claims=**0 零积压**/profiles=1/
  last_error 空——定时扫描未启系部署配置缺位非代码缺陷（多班同口径）；链路锚点 _poll
  :2403 / poll_enabled 闸 :2425 / _profile_for :334 全在位；周边零新禅道 AI 竞品
  （第 13 例持续为零；redmine-cli 51★ 系项目管理域微型非 AI 竞品） | 巡检
- **G 产品**：过时文案 grep（13 种/14 种/15 种/17 种/11 个预置/单源模式）app/ui+app/core+
  README 零命中；本班零新毛病 | 巡检

## 落地件选定结论（第 3/4 步交接）

在册代码级「小而实」积压**核对为零**（22 时落地班已清账：邮件契约/标记无用/时间衰减/
outcome 加权/装后冒烟全在位，复选框 4 项已落地锁定回归）；队列活项均为攒批（第 1/4 项
管线级）/拍板（第 2/3 项语义缓存·追读力度量）/远期（第 11 项 gascity 常驻舰队）——
按「拍板件不擅动」纪律不开代码件。本班实落地=调研沉淀通道：本报告 + knowledge.md
本班节（新条目 5+微型批+对照表 PR-Agent 迁移 1 仓）。零 app/ 实现、零 JS/CSS 改动。

## 发版判定（预记，供第 4/4 步）

本班改动仅 docs/borrow-log 调研沉淀——零用户可感知变更，按 05 时观察班/06 时班/22 时
班先例不发版。

## 待深挖队列（01 时快照）

21 时快照 11 项全部维持（第 3/10 项保持划掉）；本班新面孔均雷达级**零新队列项**
（nautilus-compass 漂移检测随 A13 越范围域备注；agnix 随 B 专项 skill 包质量校验
备注；kodus-ai/pr-agent 系已满配评审域旁证）。风险在档维持（交人拍板）：
data/zentao.json 明文密码；portscan 真实杀进程用例挂死；32 位全量 discover 静默退出。

## 未验证项（如实记录）

- pypi 未复试（历班六种拦截形态在案，省配额——非本班新增拦截）。
- GitHub Trending 直抓未行（trendshift 本班可达补位，21 时班同口径）。
- 禅道定时扫描未触发真实工单验证（不为验证制造真实 Bug，配置缺位非代码缺陷）。

## 02 时独立复核班附记（第 1/4 步交接前复核，01:25-01:35）

上一执行（本班初稿 01:01-01:23 + knowledge.md 节 01:24）产物逐项独立实证，全过：

- **主扫证据**：_scan_tmp.progress `DONE 116`、_scan_tmp.jsonl 525 行、
  _scan_uniq.txt **426** 行（初稿 425 已勘正）；topic/trend 产物 mtime 01:19/01:21。
- **源码锚点复测**：BUILTIN_FLOWS=18（import 实数，14 指令项 id 逐项对上）；
  pipeline.py _ensure_budget :61/_budget_max_tokens :613/_budget_cost_caps :632/
  cascade :1526/_shrink_context_block :2538；market_remote.py SOURCES :50。
- **数据复测**：market 缓存 810（26/315/5/99/215/150，fetched_at 2026-10-03）逐源
  吻合；skills.json lessons=72（26/21/10/7/4/4）packs=3；zentao config
  poll_enabled=False/product_profiles=1/claims=0/last_error 空。
- **本机探测复测**：14 二进制在装（codebuddy+cbc 同条目双名），openclaw 未装，
  六候选（deepseek-reasonix/reasonix/fuxi/gitlawb/zero/empryo）which 全空。
- **repos 抽验 5 仓**（02 时班 repos 端点，零搜索配额）：pr-agent 13,273 /
  kodus-ai 1,449 / nautilus-compass 1,134 与初稿分毫不差；orca 85,666、
  mattpocock/skills 276,873 为初稿写入后自然增量（+6/+10）；全 alive 零 archived。
- **复查时间窗勘正**：初稿「01:3x-01:4x」系误标，实际 01:1x-01:2x（产物 mtime 链
  01:14-01:23），knowledge.md 同步勘正。
- 结论：本班调研成果真实有效，第 1/4 步可交棒第 2/4 步（七专项深化与落地件选定）。

## 03 时巡检班（第 2/4 步·七专项深化实证，基于第 1/4 步定位逐项复核）

> 分支 main；只读巡检+文档沉淀，零 app/ 实现改动。所有锚点为本班独立复测行号，
> 与 01 时班口径互证。禅道链路全程只读（未触发真实 resolve/评论/群通知）。

### A · token 节约（锚点全实证，四方向判定维持）

- **预算熔断**：_ensure_budget :61（:412/:531/:990/:1463/:3201/:4812/:5439 七处闸口）；
  _budget_max_tokens :613 / _budget_cost_caps :632 → 日/月成本帽 :655。
- **token_meter**：:690-691 used 查询 + :723/:743 accumulate 累进（Phase 2 1C/1D 注释
  在位，usage 逐次累进 session_run_id 维度）。
- **撑爆→压缩→守门重试**：:704-742 注释在位（Phase 2 1D 三段式）。
- **三段压缩与分层降级**：_shrink_context_block :2538（budget=12000）→
  _serial_shrunk_block :2581（经验库/圣经按各自身份过 shrink，连载起草重试 :3676 与
  非赛马 :3800 两处调用；:3596 注释「三块分开留底」佐证）。
- **cascade 廉价分流**：:1526-1529 `ss_get("cascade","enabled")` opt-in + 
  capability.cascade_reorder——已满配。
- **会话复用**：session_run_id 贯穿 :487/:672/:705（_get_session 复用同会话）。
- **经验召回**：skills.block_for(task, stable_order=True) :3142/:3598——同一任务
  技能块字节级一致（§07 T1.2' 前缀缓存注释在位）。
- **diff 评审**：_git_diff :948（diff HEAD + 未跟踪新文件拼合）+ CODE_REVIEW_PROMPT
  :919（「diff 为主要依据」）+ _review_depth_note :1019（评审深度随 diff 规模分级，
  借鉴 pr-af）——diff-only 评审已满配。
- **四方向对比判定**：prompt 缓存=供应商侧（stable_order 字节级一致已在做我们能做的
  最大化）；语义缓存=待拍板（交人决定，不擅动）；diff-only=已有；廉价分流=cascade
  opt-in 已满配——**零重复建设**。

### B · 插件市场（六源+双闸门实证，零接入维持）

- **六源 SOURCES :50-76**：zcode/anthropic（双 URL 主备）/anthropic-skills/
  claude-skills/clawhub/cocoloop，SOURCES_BY_ID :89；clawhub 翻页 4×50、cocoloop 
  3×50（:102-109）。
- **SSRF 防护 :114-134**：主机名非空+端口域校验 → getaddrinfo 逐 IP 拒绝
  loopback/private/link_local/reserved/multicast/unspecified——解析级防护非字符串
  黑名单，在位。
- **白名单闸门**：inspect_tree(root, whitelist) :568 + 安装时 :941-952（白名单技能
  与包内容不匹配即拒装、多余技能过滤）；落点收容 :636-855（resolve 逐级校验防逃逸）。
- **缓存实测**：data/market_remote/*.json 六文件 catalog.plugins 键 = 26/315/5/99/
  215/150 = **810**（fetched_at 2026-10-03，与 01 时班零漂移）。
- **三问复核**（本班新见候选）：agnix——重合度低（配置校验器非技能包）、不可直装
  （Rust 独立 CLI）、低频搜索 → **三问不过仅雷达**；dsh-plugins-store——DSH 生态
  不属六源、微型 68★ → 仅雷达。零接入维持，方法论（装前内容质量+白名单+SSRF 三闸）
  已是既有通道本身，无需再蒸馏。

### C · 任务类型（18 类型逐项实证，覆盖矩阵全对上）

- BUILTIN_FLOWS :36 import 实数 **18**；14 指令项 id 逐项对上（direct/code/novel/
  serial_novel/article/video_script/doc/translation/rank_scan/defect_retro/research/
  speech/presentation/weekly_report/email/tech_proposal + 自研 resume/bid_doc）。
- **描述-参数一致性抽查全过**：review 类 14 型统一 rubric 4-5 维/threshold 7.0/
  rounds 2；serial_novel 独有 serial{chapters:8,words_per_chapter:2500}；rank_scan 
  note 与「四平台+A-E 证据分级」辅助信息相符；defect_retro note 与禅道/Jira CSV 
  附带口令相符；presentation note 明示「不生成 PPT 二进制」与实际行为相符——
  **零文案-行为漂移**。

### D · 经验库（零漂移，零新蒸馏）

- data/skills.json lessons=**72**（流程规范 26/节奏爽点 21/情节逻辑 10/人物塑造 7/
  一致性 4/文笔风格 4，流程规范 36.1% 口径维持）、packs=3——与 01 时班零漂移。
- 本轮零新蒸馏：WebSearch 仓名迁移勘定方法论已入 knowledge 对照表层（01 时班）；
  「未装不盲接」打法已在 knowledge 接入打法节——按去重纪律不重复入库。

### E · 新 CLI（14 条目实数，六候选全空零接入）

- DEFAULT_CATALOG :32 **14 条目**（codex-cli/claude-code/opencode/qwencode/aider/
  openclaw/kimi-code/mimo-code/grok-build/pi/deepseek-harness/gemini-cli/codebuddy/
  trae-agent），探测 CLI 名 14 个。
- 本机 which 实测：**13/14 在装**（codex/claude/opencode/qwen/aider/kimi/mimo/grok/
  pi/dsh/gemini/cbc/trae-cli；openclaw 未装；codebuddy 条目探测名 cbc 已在装）。
- 六候选（deepseek-reasonix/reasonix/fuxi/gitlawb/zero/empryo）which **全空**——
  未装无法实测，标记待验证，**零接入防死链**（Reasonix 35,735★ 候选首位在档）。

### F · 禅道集成（只读核查，配置缺位非代码缺陷）

- data/zentao.json：config.poll_enabled=**False**/interval 未设/product_profiles=1/
  claims=**0 零积压**/last_error 空/next_scan 陈旧（2026-09-21，poll 从未启动实证）
  ——定时扫描未启系**部署配置缺位**非代码缺陷（多班同口径）。
- 链路锚点全在位：_poll :2403 / _poll_unlocked :2414 / poll_enabled 闸 :2425/:2474/
  :2502 / _profile_for :334（产品档案路由）。
- 前端禅道子页在位：app.js :11570（/api/zentao 拉取脱敏 config+claims）/:11829
  （modules）/:11849（products/users 并行）/:1012-1017（六操作 verb 映射）。
- 本班全程只读：未触发真实 Bug resolve/评论回写/群通知（不为验证制造真实 Bug）。
- 周边零新禅道 AI 竞品（第 13 例持续为零）。

### G · 产品巡检（零新毛病）

- 过时文案 grep（13 种/14 种/15 种/17 种/11 个预置/单源）app/ui 三文件+app/core+
  README 零命中；禅道前端与后端端点对齐（六操作全有 verb 映射）——**零新毛病**。

### 落地件选定结论（第 3/4 步交接）

在册代码级「小而实」积压**核对为零**（承 01 时班核对：22 时落地班已清账+回归锁定）；
队列活项均为攒批（第 1/4 项管线级）/拍板（语义缓存·追读力度量，交人不擅动）/远期
（gascity 常驻舰队）。风险备注维持交人决定：data/zentao.json 明文密码；语义缓存
拍板件。本班实落地=文档沉淀通道：current-round.md 本节 + knowledge.md 本班节。
零 app/ 实现、零 JS/CSS 改动——**docs-only 不发版**（05 时观察班/06 时班先例）。

### 本班独立实证差异点（相对 01 时班节的增量）

- A 节补齐 token_meter accumulate/used 精确行号与七处 _ensure_budget 闸口清单；
  diff 评审链（_git_diff+CODE_REVIEW_PROMPT+_review_depth_note）首次完整成串。
- B 节首次实证 SSRF 解析级防护细节（getaddrinfo 逐 IP 六类拒绝）与白名单-包内容
  匹配校验拒装路径。
- F 节新增 next_scan 陈旧值实证（2026-09-21）坐实「poll 从未启动」；前端禅道子页
  端点链首次成档。

## 第 3/4 步落地班（01:4x，承 03 时班选定结论执行）

> 分支 main；选定结论=**零代码件**（在册积压清账、队列活项均攒批/拍板/远期），
> 本班尊重选定不强行加功能；实落地=回归锚定件+文档沉淀。零 app/ 实现、零 JS/CSS
> 改动，六候选边界文件中仅测试件新增内容。

### 选定结论独立复核（本班再实证，非照抄上步）

- **flows.py 注册表实测**：BUILTIN_FLOWS import 实数 **18**（=指令 14+自研 4）；
  review 型 **14** 个（rubric 4-5 维/threshold≥7.0/rounds≥2 全过，bid_doc 7.5 系
  刻意从严例外）+ 非 review 4 个（direct/code/rank_scan/defect_retro 均不带评审
  参数）——03 时班「描述-参数一致性抽查全过」结论复证。
- **疑点排查一：manuscript.md 双用**（novel 与 serial_novel 同名）——核
  pipeline._ms_io :115-119 与 store.create_task：manuscript 落盘按**任务自身
  workdir** 隔离（任务创建时用户指定或回落默认工作区），单任务单类型不存在
  跨类型互踩；同名系「同书续写复用同目录」的有意口径（第 2/4 步 C 专项已核
  一致），**判非差距不改**。
- **疑点排查二：「单源」grep 命中** pipeline.py :2350/:2360——系调研证据链
  「多源交叉验证、单源降权」语义，非旧市场「单源模式」残留；G 专项精确口径
  （13 种/14 种/15 种/17 种/11 个预置/单源模式）六模式本班逐一 grep **全零**。

### 本班实际改动

1. **tests/test_borrow_round.py 追加** `AllTypesRegistryContractTests`（4 测试，
   不动既有 branches 件）：18 型实数+14 指令项映射 / 14 review 型共享参数不变量
   （含 bid_doc 7.5、serial{8,2500} 边界）/ 非 review 四型无评审参数 / 过时文案
   六模式零残留（扫描域：UI 三件+flows.py+pipeline.py+README）——C/G 专项结论
   机械化，漂移先红倒逼同步台账。
2. **docs/borrow-log/current-round.md** 本节（改动、测试命令、效果证据）。
3. **零前端改动** → 按指令条件句不新建 tests/test_borrow_round_ui.mjs（仅前端
   改动才另建）。
4. 零插件安装（新见候选 agnix/dsh-plugins-store 三问不过，承 B 专项雷达口径）；
   零经验蒸馏新条目（去重纪律，承 D 专项）。

### 自检与效果证据

- `python -m py_compile tests/test_borrow_round.py` 通过；
  `python -m unittest discover -s tests -p "test_borrow_round.py" -v` **10 全绿**
  （既有 6+新增 4）；`test_borrow_round_regressions.py` **6 全绿**（未受影响）。
- 测试即证据：新增 4 测试当前全过=注册表 18 实数/14 指令项/参数不变量/文案零
  残留四项结论在本班时点真实成立；日后任一漂移（加型漏参/文案回潮）此件先红。

### 发版判定（预记，供第 4/4 步）

本班 app/ 零改动，唯一代码件为测试锚定（无用户可感知变更）：按「docs-only 不发
版」先例倾向不发；按「当天有代码入库才发」字面则测试件算代码——两口径如实记录，
**交第 4/4 步按仓库惯例拍板**。

## 04 时调研班（第 1/4 步·批3：计划/spec/长任务，03:38 开工）

> 分支 main（b6d2269）；只读调研+文档沉淀，零 app/ 实现、零 JS/CSS 改动。
> gh api 认证搜索 30/分满额开局，主扫串行 sleep 4s 零限流；repos 端点（core 5000）
> 承担 35 仓复查+13 清单实测+7 新面孔定性，零搜索配额消耗。

### 通道与证据链（可追溯）

- **主扫**：`python scripts/borrow_scan_nightly.py`（无参，自动 hour%7=3 → B3）→
  产物 `$TEMP/_scan_1006_04.jsonl`（524 行）+ `.progress`（`DONE 116 queries`，
  errors 0）；唯一仓 467（python 去重实测）。窗口 03:41-04:00。
- **WebSearch 串行 2 发**：①`spec-driven development AI agent open source GitHub
  new 2026 spec planning toolkit` → 捞出 BMAD-METHOD/Augment Cosmos/Kiro（后两者
  商业闭源不入库）+Engineering4AI 清单；②`long-running autonomous AI agent planning
  checkpoint resume open source framework GitHub October 2026` → 捞出 AgentField
  （LangGraph/Claude Agent SDK/Temporal 系已录或框架路线）。
- **repos 二次实证**（「新闻面 ≠ 开源仓在」规则）：BMAD-METHOD 53,809 pushed 10-05
  alive；Agent-Field/agentfield 2,605 pushed 10-05 alive（in:name 五仓辨正主）；
  awesome-spec-driven-development 288 pushed 10-03 alive；davepoon/buildwithclaude
  3,589 pushed 10-04 alive。
- **BMAD 盲区机理**：主扫 spec/plan 词组按 stars 排序 top5 剪切——spec-kit 140k/
  OpenSpec 71k/get-shit-done 64k 恒占前席，BMAD 53.8k 恒第 6+；且「bmad」无
  spec/plan 词根，词组 AND 匹配不命中——**双盲区叠加，WebSearch 单通道唯一入口**。
- **全量查重**：467 唯一仓逐一比对 borrow-log 全历史（1.15MB 合并文本）→ 163 件
  零收录，逐一过筛均课程/书单/词根错配噪声，零机制级漏网。

### 批3 域与全类型扫描面（零机制级新差量，稳定期延续）

- 批3 域 11 词组命中逐仓已在档（清单见 knowledge.md 本班节）；主扫 A1-A13 全组
  过筛同结论；trendshift 29 仓/ topic 8 页/npm 两查全在档或微型新生件。
- 新入库 2+1：BMAD-METHOD（参考·形态观察）/AgentField（雷达）/awesome-spec-driven
  （keywords.md C 补源）——全部 WebSearch 通道捞出，主扫通道贡献为零，**跨通道
  互补结构性再证**（本夜 01 时班 Kodus/PR-Agent 后连续第 2 班）。

### 存量复查 35 仓明细（04:0x，全 alive 零 archived）

批3 域：spec-kit 140,239(10-05)/OpenSpec 71,087(10-05)/planning-with-files 27,297
(10-01)/agentmemory 29,148(10-05)/worktrunk 8,844(10-05)/gsd-pi 1,290(10-05)/jean
1,309(10-05)/itsaplan 891(10-05)/lazycodex 3,731(10-05)；头部：orca 85,746/superpowers
295,611/mattpocock-skills 276,962/ECC 273,532/ponytail 155,876/claude-mem 96,548/pi
112,685/hermes-agent 251,407/opencode 211,868/anthropics-skills 179,774；写作域：
webnovel-writer 7,324/ainovel-cli 2,096/yomiyasu 1,498/drama-skills 2,526/huobao-drama
15,759/oh-story-claudecode 7,269；E/治理域：DeepSeek-Reasonix 35,737/SkillSpector
19,447/hippo-memory 772/context-mode 25,467/Strata 13,533/rea 5,164/beads 27,649/
gascity 1,330/open-code-review 43,843/herdr 42,477/bernstein 1,404。
全名对照表本班勘定 3 条：davepoon/buildwithclaude、code-yeongyu/lazycodex、
spec-kitty/spec-kitty。

### 本班实际改动（零代码件）

1. docs/borrow-log/keywords.md：C 雷达源 awesome 补充行 +Engineering4AI/
   awesome-spec-driven-development（行尾标注谁/何时/为何）。
2. docs/borrow-log/knowledge.md：本班节（3 新条目+对照表勘定+批3 域+复查+雷达 C+
   七专项快照+队列+未验证项）。
3. docs/borrow-log/2026-10-06.md + current-round.md 本节。
4. 零插件安装、零经验蒸馏新条目（BMAD 形态观察级，去重纪律）；零 app/ 实现。

### 发版判定（预记，供第 4/4 步）

本班改动仅 docs/borrow-log 调研沉淀——零用户可感知变更，**docs-only 不发版**
（05 时观察班/06 时班/01 时班先例）。

## 04:0x 独立复核班附记（第 1/4 步复核+批4 补跑，04:01 开工）

> 上一次执行（03:38 开工调研班）文件阶段已收口（最后写入 04:03:47），本班认定其
> 成果在制品、**不重跑 116 查询主扫**（不重做已完成），转独立复核+按本班时点
> hour=4 规则补跑批4。全程只读+docs 增量，零 app/ 改动。

### 独立复核清单（全部实测，非照抄自报）

- **主扫产物复测**：`$TEMP/_scan_1006_04.jsonl` 524 行、`.progress` `DONE 116
  queries` 零 FAIL 零 error；python 去重复测 **524 行 467 唯一仓**——与该班自报
  分毫不差。
- **repos 端点抽验 6 仓**（core 配额零搜索消耗）：BMAD-METHOD 53,809（10-05）/
  Agent-Field/agentfield 2,605（10-05）/davepoon/buildwithclaude 3,589（10-04）/
  github/spec-kit 140,242（自报 140,239 后自然 +3）/stablyai/orca 85,750（自报
  85,746 后自然 +4；本班先猜 orca-watch 404，查 knowledge 对照表勘定正主
  stablyai/orca 再验通过——对照表价值自证）——全 alive 零 archived。
- **注册表复测**：`from app.core.flows import BUILTIN_FLOWS` import 实数 **18**
  复证（14 指令项映射口径维持）。
- **四文件落位核对**：keywords.md（Engineering4AI 雷达源行尾标注在位）/
  knowledge.md 04 时班节（新条目 3+对照表 3+批3 域+复查 35+雷达 C+七专项快照+
  队列+未验证项全齐）/2026-10-06.md 04 时班节/current-round.md 04 时调研班节
  ——**零勘误需求，零失配**。

### 批4 补跑（治理/安全/人机协同，本班时点 4%7=4 规则项）

- `python scripts/borrow_scan_nightly.py --batch 4`：**10 查询 50 行 49 唯一仓，
  DONE 10 零失败零限流**（04:1x）。与 borrow-log 全历史比对：**40/49 已在档**
  （cordum 510/Aegis 487/agent-governance-toolkit 6,393/failproofai 5,242/plano
  7,075/archestra 4,345/mcp-context-forge 4,572/edict 16,968/caura 542/
  memoryops-ai 22 等——治理/安全域已录族密实，**零机制级新差量维持**）。
- 新见 9 件逐一过筛均不过三门槛：mcp-client-for-ollama 826★（本地 LLM TUI MCP
  客户端，形态域外微型）/Shinkai-Shoujo 25★（IAM 审计微型）/ZSC-Eval 59★（学术
  评测微型）/MEKXH/golem 203★（GeoAI 域外）/marketing-dashboard 468★（营销域外）
  + 纯词根错配噪声 4 件（OpenWrt .config/课程作业/W3C 报告/2017 条款页）——
  **零新队列项，零入库**（微型批如实记）。

### 复核结论（验收对照）

本步验收四项全过：**检索覆盖矩阵**（116 查询=A 78+B3 11+内置 B1 11+A1 双轮 16，
四文件落位可溯）/ **增量复查记录**（35 仓 repos 端点带时点）/ **来源证据**
（主扫产物+repos 抽验+WebSearch 2 发留痕）/ **未完成项**（pypi 省配额、Trending
trendshift 补位、禅道真实工单未触发——该班已如实记录）。第 1/4 步可交棒第 2/4 步。
本班实改动=本附记+knowledge.md 复核附记小节；**docs-only 不发版维持**。

## 第 3/4 步落地班·第二班（04:4x，实施第 2/4 步巡检班提案甲+乙）

> 分支 main；承 2026-03-03.md 巡检班获准提案，只实施甲（类型注册表对账
> 三件套）+乙（市场文案补 CocoLoop）两项；候补（store.py 章数下限）与
> G-② EN 词条补全维持交拍板不动。指令候选清单（flows.py/pipeline.py/
> app.js/i18n.js/style.css）经真实路径核实后零改动——实际落点为
> dispatch.py/usage.py/index.html（改动清单已按指令更新并记录偏差）。

### 改动与证据

- **甲**：dispatch.py TYPE_DIMENSIONS 删 zentao 死映射+补 defect_retro 显式
  映射（reasoning，行为零变化——此前靠 :60 兜底）；usage.py
  _DURATION_BASELINES 补 presentation=480/bid_doc=720/defect_retro=240
  （跑前预估不再回落 480s 常数）。引用面核对：capability.py 仅 get 查表、
  usage.py 仅兜底取值，影响面=预估显示不触执行。
- **乙**：index.html:977 提示行 key+文本补 CocoLoop，与 i18n.js:1642 词条
  逐字对齐（英文界面不再回退中文）；i18n.js 零改动（词条早已在位，失配
  在 index.html 侧）。
- **测试两件新增**：test_type_registry_audit.py 4 测试（两表键集合==flows
  18 id 无漏无多+值域合法+取值锁定+既有兜底锚点）/ test_market_copy_sources.py
  3 测试（key 恰一条+六源齐全+词条恰一条+旧五源文案零残留）。
- **验证**：py_compile 四文件过、node --check i18n.js 过；新测试 4+3 全绿；
  相邻回归 test_borrow_round.py 10 绿、test_full_type_iteration.py 13 绿；
  全量 discover 结果另行补记。残留 grep：zentao 死映射/旧五源文案全零。
- 实施结果全文见 docs/borrow-log/2026-03-03.md「第 3/4 步落地班实施结果」节。

### 发版判定（预记，供第 4/4 步）

本班有代码入库（dispatch.py/usage.py/index.html+测试两件）：defect_retro
跑前预估从 480s 常数变为 240s 量级、禅道工单调度维度显式化、市场文案六源
补齐——用户可感知（预估显示+英文界面文案），按「当天有代码入库才发」应
发 patch 版；交第 4/4 步按五道关+发版流程执行。

### 分片全量对账补记（本班时点实测）

- 32 位全量 discover 本机再次静默退出（在案风险第三次实证，exit 0 无总结
  行）——按 18 时班先例改**分片对账**：264 模块 6 批（PYTHONPATH=tests +
  python -m unittest 显式列模块），**2177 项 2174 绿**（批次明细：369 OK /
  372 OK / 309 中 3 偶发 / 368 OK skipped=2 / 329 OK / 430 OK）。
- 批内偶发 3 项（test_launch 2E+test_git_workbench 1F）**非本班因果三重
  证据**：①涉事件单跑全绿（test_launch 33/test_git_workbench 23）；②剔除
  本班两新测试件复跑同批失败依旧且集合漂移（4F+2E）；③失败模块与本班
  改动零交集。
- 评审（general-purpose 代理，code-reviewer 通道因模型路由 400 不可用
  如实记）**通过**，LOW 三条已修：新测试死变量删除/文案断言补实/既有
  test_dispatch.py 元组 zentao→defect_retro（死映射清除一致性收尾）。
- 重名复核补充：新建 test_market_copy_sources.py 与既有
  test_market_source_copy.py 近名——实核既有件只锚 i18n.js 词条侧，未覆盖
  本次失配点（index.html key↔词条一致），两件互补不重复；类名已错开防
  跨模块同名混淆。

### 独立复核班附记（05:5x，承评审班收口后增量验证，详见 2026-03-03.md 同名节）

- 逐 hunk 复核：三项代码改动与获准提案甲/乙逐一吻合，外来标记零命中；
  评审班两测试件更新（锚点名补 Anthropic/test_dispatch 元组收尾）重读后
  重验全绿（4+3+4，另 borrow_round 10/full_type_iteration 13/
  regressions 6/selfupdate 6）。
- 全量整串跑与分片对账互证：静默退出元凶首次定位=test_serial_draft_forbidden
  全量串上下文硬崩（单跑 2/2 绿）；13 处 FAIL/ERROR 归因=10 处单跑绿
  （隔离性污染）+http_500_guard 3 处既有路由-守卫漂移（fakeA 404）+
  portscan 台账在案挂死；10 涉事模块对本轮改动符号零引用=非本班因果。
  http_500_guard/portscan 两件既有问题按纪律不修，交人拍板。
- 发版判定承落地班预记：有代码入库应发 patch 版，交第 4/4 步执行。

## 2026-10-06 06 时调研班（第 1/4 步·批6：框架/平台/SDK 生态，06:38 开工）

> hour=6，6%7=6 → 轮换批6。分支 main（b6d2269），工作区在制品=20/21/22 时班沉淀+
> 今日 00/01/03/05 时班节（未提交），本班增量记录、逐字不动。gh api 认证可用，
> 串行 sleep 4s 纪律。

### 主扫描（borrow_scan_nightly.py 无参全量）

- **115 查询 524 行 472 唯一仓，零失败零限流**（DONE 115 / errors: 0 实证；
  A 常驻 89 含内置 B1 11 + B6 轮换 10 + A1u/A1p2 双轮 16）。分组唯一仓数：
  A1 35/A2 35/A3 20/A4 13/A5 20/A6 30/A7 31/A8 25/A9 20/A10 50/A11 25/A12 12/
  A13 18/B1 48/B6 50/A1u 39/A1p2 40。
- 批6 域命中全落已录族：mastra 28,575/vercel-ai 27,127/harness-sdk 8,675/agentops
  5,886（06-25 停更维持）/axonhub 5,334（looplj）/CrewAI-Studio 1,357/AntSK 1,327
  （shuyu-labs，勘定入对照表）/aegra 1,240（aegra/aegra 同名同主）/semantix 821/
  python-a2a 1,007/agentscope-runtime 876/pandaprobe 784/race-condition 234/
  idun 203/agentcn 483/openui 9,986/agents-cli 6,051（google）——**批6 域零机制级
  新差量（稳定期延续）**。
- 主扫面高星（>=300）零收录逐一过筛：新面孔仅 7 件微型/雷达级（Patter 1,064/
  spec-to-agents 115/a2a-net 55/awesome-a2a-agents 31/edumcp 156/aitino 92/
  Agena 100）；其余候选（hermes-agent/deer-flow/claude-code-router/gpt-researcher/
  12-factor-agents/agent-zero/oh-my-claudecode/ARIS/agency-agents-zh/cc-haha/
  easy-vibe/Some-Many-Books 书单噪声等）逐个精确查重全已录，零漏判。

### WebSearch 串行交叉验证（距 04 时班 2 班，3-4 班窗口内）

- 2 发：批6 域框架横评面 1 发+新发布面 1 发。捞出 claim 逐一 repos 端点二次实证：
  **microsoft/agent-framework 13,953★（+149 vs 9-26 入库 13,804，已录复查增量）**/
  ag2ai/ag2 4,975/OpenClaw·Pydantic AI 2.0·Google ADK·Mastra 全已录——**头部框架
  稳定期 WebSearch 亦零新面孔**；**VoltAgent/voltagent 10,731★ 系 WebSearch 捞出
  （repos 实证 09-28 push）——knowledge 此前只录 VoltAgent/awesome-agent-skills
  清单仓，框架本仓未录，新入库**。

### 雷达 C 覆盖

- awesome 14 源 repos 实测：13 alive 零 archived；**awesome-agent-orchestration
  正主勘定=vivy-yi/awesome-agent-orchestration（77★ 小标清单，in:name 一次勘定，
  kyegomez 盲猜 404 在案）**——keywords.md C 源标注本班同步。ComposioHQ/awesome-
  claude-skills 76,552/punkpeye/awesome-mcp-servers 95,846/VoltAgent 清单 35,237/
  Shubhamsaboo 140,789/hesreallyhim/awesome-claude-code 55,106/e2b-dev/awesome-
  ai-agents 30,270/bradAGI 1,317/ai-boost 4,715/davepoon/buildwithclaude 3,589
  等全复认。
- topic 8 页（pushed:>10-01 过滤）：**新面孔 4 件**（autoharness 7,906/eigent
  15,457/oasis 5,228/awesome-agentic-ai-zh 7,400，repos 端点逐一实证）+其余已录
  （nanobot 48,805/archify 78,090/oh-my-openagent 69,828/cc-switch 140,266/
  ui-ux-pro-max 133,328/agenticSeek 27,433/atlas 9,130 等全已录）。
- npm 两查：已录族为主（musistudio/claude-code-router 3.1.1 等），微型新生件
  （nax/bizar/oceanus/garda 等编排器包）零接入级；**@hybridlabor-api/bdb-agent-
  orchestrator 自述「Upstream fork of Untrivial-ai」佐证属主迁移**。
- trendshift 可达（HTTP 200），首页明细 3 件（Busbar/Bifrost/Kane CLI）全已录
  零新面孔；禅道周边两搜第 15 例零新禅道 AI 竞品（微型 clickup-ai-bug-triage 0★
  噪声级）；pypi 省配额未复试（历班六种拦截形态在案）、Trending 直抓未行
  （trendshift 补位）。

### 存量复查（repos 端点 70+ 仓次，06:4x-07:1x，全 alive 零 archived）

orca **85,791（+45 续领跑）**/ponytail 155,950（+74）/mattpocock/skills 277,048
（+86，对 superpowers 295,641 差 18.6k）/ECC 273,604（+315）/claude-mem 96,604
（+56 放量持续）/**morluto/rea 5,436（+272 放量续）**/iFixAi 21,173（+19 放量趋稳）/
**Niko1221/Strata 13,779（+246 放量续）**/DeepSeek-Reasonix 35,739（E 候选首位维持）/
spec-kit +17/OpenSpec +2/agentmemory +2/BMAD +3/spec-kitty 1,666 持平/gsd-pi 1,290
持平/oh-story 7,269 持平（10-03 push）；写作域 yomiyasu 1,503（+5 在动）/webnovel-
writer 7,325（+4）/ainovel-cli 2,096 持平/huobao-drama 15,762（+39）/drama-skills
2,525；B6 域 mastra +15/vercel-ai +5/harness-sdk +6/semantix 821 持平（放量止确认）/
aegra 1,240 持平/gstack 135,358（+70）/a2aproject/A2A 26,017（+~100）/kagent 3,941/
AntSK 1,327 持平（09-20 push）/xgent 119 持平（05-25 停更）/pydantic-ai 20,420/
openai-agents-python 29,848/semantic-kernel 28,629/langgraph 42,745/dify 157,897/
langflow 155,513/langchain 147,473/eliza 19,541；E 域 herdr 42,491（+90）/pi 112,708
（+119）/beads 27,652/gascity 1,328；anthropics/skills 179,786（+66）/hermes-agent
251,428（+119）/opencode 211,881（+66）/alibaba/open-code-review 43,862（+60）。

### 对照表勘定与迁移（本班 3 件）

- **awesome-agent-orchestration=vivy-yi/awesome-agent-orchestration**（77★，
  in:name 勘定）
- **agent-orchestrator=OrchestratorInc/agent-orchestrator**（12,788★——gh api
  repos/Untrivial-ai/agent-orchestrator 返回 OrchestratorInc 重定向，属主迁移实证；
  「仓名迁移」第 2 例·属主重定向形态，npm fork 包描述佐证）
- **aegra=aegra/aegra**（1,240★ 同名同主）/ **AntSK=shuyu-labs/AntSK**（1,327★，
  主扫 full_name 实测）——历史简称补全名

### 七专项快照（06 时班独立轻量实测）

- A：八锚点全在位（usage.py:117 cached 记账/pipeline.py:627+696 预算熔断/
  pipeline.py:1526-1529 cascade/pipeline.py:2538 _shrink_context_block/
  step_runner.py:28 _PRECHECK_RATIO 0.9/compaction.py 在位）；A3 4 组 20 行全已录
  零新 token 域候选
- B：market_remote.py 六源在位+SSRF 防护（仅 https+拒绝环回/私有地址）；npm 面
  微型件零接入级，本班零新候选零接入
- C：BUILTIN_FLOWS=18 实测吻合；菜单 /api/flows 数据驱动数量天然一致
- D：lessons=72 零漂移（流程规范 26=36.1% 口径维持）
- E：DEFAULT_CATALOG=14 import 复证；本机 13/14 在装（openclaw OUT）；六候选
  （reasonix/fuxi/gitlawb/zero/empryo/deepseek 裸名）which 全空零接入防死链
- F：poll_enabled=False/claims=0/last_scan=09-21 陈旧 15 天（部署配置缺位非代码
  缺陷多班同口径）；禅道周边第 15 例零新竞品
- G：过时文案 grep（五源/单源/死词条模式）零命中——05 时落地班 G-① 修复在位
  （index.html CocoLoop+dispatch.py:30 defect_retro+usage.py:878 三基线）；
  G-②③ 候拍板维持

### 未验证项（如实记录）

pypi 未复试；Trending 直抓未行（trendshift 补位）；worktrunk 全名未勘定
（worktrunk/worktrunk 盲猜 404，B3 域承 04 时班 +38 口径不重测）；trendshift
仅首页明细三件（分页深抓未行）；禅道定时扫描未触发真实工单验证。
