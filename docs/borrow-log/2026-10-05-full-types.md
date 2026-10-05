# 2026-10-05 全类型调研与七专项巡检（10 时班·批3：计划/spec/长任务——新一轮计划第 1/4 步）

> 开工 10:02、分支 main（bb88049）、工作区干净。10 % 7 = 3 → 批3。
> 主扫 `scripts/borrow_scan_nightly.py`（无参全量模式自动按小时选批）：
> **116 查询 523 行 465 唯一仓，零失败零限流**（A 常驻 89 含内置 B1 11 + B3 轮换 11 +
> A1u/A1p2 双轮 16，gh api 串行 4s）。雷达源 C 全过（见下）。
> **本轮零机制级新差量（连续第 21 班稳定期）**；搜索引擎双通道被拦如实记录。

## 通道实录

- **主扫**：116 查询全 ok。stars 轮 + A1 updated 新锐轮 + A1 stars page=2 翻页三面齐跑。
- **repos 端点复查 30 仓**（core 限流池串行 2s，全名对照表直用零勘定摩擦）。
- **topic 页 8 个**：multi-agent-orchestration / ai-agents / claude-code / agent-framework /
  claude-skills / llm-agents / ai-coding-assistant / mcp——首五位全部已录，零新面孔。
- **npm 两查**（agent orchestrator / claude code manager wrapper）：全为 0-2★ 新生或已录族
  （ruflo 73,879 已录），零接入级标的。**pypi 未复试**（历班六种拦截形态在案，省配额）。
- **awesome 清单 8 个新鲜度**：awesome-claude-code 55,085（10-05）/VoltAgent 35,207（10-02）/
  awesome-mcp-servers 95,837（09-27）/harness-engineering 4,702（10-04）/Agent-Memory 658
  （10-04）/awesome-claude-code 系全活跃；**Awesome-Long-Horizon-Agents 1,060（09-22 后无 push
  停更观察维持）**。
- **trendshift.io 可达**（Trending 替身）：morluto/rea 294（逆向工程 agent，域外）/
  Niko1221/Strata 227（本地推理引擎，雷达）/ neilsonnn/image-blaster 196（已录）；
  featured 两件 repos 二次实证：**GetBusbar/busbar 171★（10-05 push）**「execution control
  plane for AI agents：govern model requests / MCP tool calls / A2A delegation」——治理域
  （bernstein/cordum 簇）+1，我们待裁决+审批+审计台账已覆盖主路径，雷达级；Kane CLI
  正主 search 未勘定（仅 hackathon 微仓，不入库）。
- **搜索引擎交叉验证未完成（如实记录）**：DuckDuckGo 连接失败、Bing robots.txt 拒抓——
  本机 WebSearch 通道本轮不可用，跨通道验证顺延下一窗口（距 10-05 04 时班已 6 班，
  本应配一次；本轮以 trendshift 替身 + topic 页 + npm 三通道补位）。
- **禅道/自家 CLI 周边**：zentao-cli 61★（10-04）/test-defect-retrospective 无变化——
  **零新禅道 AI 竞品（第 8 例后持续为零）**；codex manager 搜出 ntm 452★（已录）+ 微型。

## 新条目（本班真新面孔，均雷达/参考级）

- **GetBusbar/busbar**（171★，10-05 push）| AI agent 执行控制平面：管每个模型请求/MCP 工具
  调用/A2A 委托 | 治理域（bernstein 1,397/cordum 簇）+1；我们待裁决+审批流+审计台账已覆盖
  主路径，量级微型 | 雷达 | 2026-10-05
- **Niko1221/Strata**（227★）| 本地 LLM 推理引擎（Qwen MoE 一键装，OpenAI/Anthropic API
  localhost）| 本地部署域（FreeToken 14.2k 已录同域）；非编排竞品 | 雷达 | 2026-10-05

## 已沉淀项目复查增量（repos 端点 30 仓，全部 alive 零 archived）

- **头部**：orca **85,035★**（10-05 push，vs 06 时班 +89 续领跑）/superpowers 295,304
  （09-27 后无 push）/mattpocock/skills **276,232（vs 10-03 +1,137 放量快，威胁 superpowers
  双巨头格局）**/ECC 273,006（10-02）/hermes-agent 251,248（10-05）/deepseek-harness 243,420
  /opencode 211,770（10-05）/anthropics/skills 179,657/ponytail 154,932/claude-code 149,429
  /codex 127,858/pi 112,457/gstack 135,174/openclaw 391,328（10-05）/open-design 99,434
  /ruflo 73,879/free-claude-code 56,671/dify 157,852（10-05）。
- **批3 域（本班轮换主题）全活跃**：spec-kit **140,128**（10-03）/OpenSpec **71,054
  （10-05 当日 push）**/planning-with-files 27,282（10-01）/agentmemory 29,137（10-04）/
  worktrunk 8,806/lazycodex 3,729（10-04）/PraisonAI 9,130/spec-kitty **1,662（10-05 push）**
  /gsd-pi 1,291/itsaplan 884/jean 1,308/CONTINUUM 28——SDD 双雄+计划文件化+worktree 全线
  活跃，机制面零新差量（spec 三件套/worktree/Stop gate/活计划均满配）。
- **写作域**：webnovel-writer 7,317（10-04）/ainovel-cli 2,095 持平/oh-story **7,256（+20）**
  /drama-skills 2,499/yomiyasu **1,400（+17 在动，翻译腔自查条已随 v0.1.80 落地，标的持续
  演进）**/denova 864/neuro-book 719/Awesome-Story-Generation 662/webnovel-writer-opencode
  212（+9 生态跟随）——写作域新头名（webnovel-writer）向量 RAG/追读力度量/卷弧滚动三差量
  维持在队（管线级攒批/交人拍板）。
- **E 域**：**DeepSeek-Reasonix 35,742★（10-05 当日 push，E 候选首位维持）**；kimi-code
  7,771（10-02）；本机在装与九候选防死链口径沿用 07 时班（本轮未重复 which 全量）。
- **治理域在动**：bernstein **1,397（+21，治理远期最强实证持续演进）**/governance-toolkit
  6,392/SkillSpector **19,391（+7）**/open-code-review 43,723/caura 族无增量。
- **记忆/待深挖在动**：hippo-memory 770（10-04，负反馈已同构落地互证）/claude-rules 192
  （03-19 停更注记维持）/wenzi-xhs 153 持平。
- **其他**：claude-mem **96,158（10-05，日 +300+ 放量）**/harbor **5,825（已迁
  harbor-framework org，10-05 push——org 归属更新）**/火宝 15,673/univer 22,361（10-04，
  演示文稿空档例证维持）/FreeToken 14,175/agent-zero 19,374/eliza 19,541。

## 七专项快照（本班实证锚点）

- **A（token 节约）**：A3 扫描面零新机制；管线级锚点（预算熔断/压缩守门/diff-only 评审/
  cascade/_shrink 分层降级/planner 精确缓存/召回衰减）同日多班行号级实证沿用；
  待深挖第 1 项（工具输出统一压缩管线，第 7 验证在手）管线级攒批维持 | 已覆盖
- **B（插件市场）**：六源在位口径沿用（market_remote.py:50 SOURCES，10-04 真实 URL 探活
  六绿）；本轮新见候选（Busbar/Strata/Kane CLI）三问（重合度/可直读性/用户会搜吗）均不过，
  零接入维持 | 已覆盖
- **C（任务类型）**：`flows.py BUILTIN_FLOWS` **17 类型** import 实数（direct/code/novel/
  serial_novel/article/video_script/doc/translation/rank_scan/defect_retro/research/speech/
  weekly_report/email/tech_proposal/resume/bid_doc）——用户列举 14 场景全映射，另含
  doc/resume/bid_doc；用户 13+ 预置类型口径全部有注册表对应；演示文稿空档维持交人拍板。
  流程参数锚点：pipeline.py:2371 TRANSLATION_APPENDIX（翻译术语表+翻译腔自查，v0.1.80）、
  :952 _gitlog_brief（weekly_report git log 素材，v0.1.79）、:5099 _read_constitution
  （任务宪章 .codebee/constitution.md）；菜单描述 i18n 三字段守卫（test_i18n_dups）在位
  | 巡检
- **D（经验库）**：data/skills.json **教训 70 条**（lesson 66 + procedure 4；流程规范 25/
  节奏爽点 21/情节逻辑 10/人物塑造 7/一致性 4/文笔风格 3——偏科 36%，为 10-04 蒸馏 3 条
  入库后的新常态口径）；知识库/同族可并零新增判定沿用 | 数据卫生
- **E（新 CLI）**：catalog.py:32 DEFAULT_CATALOG **14 条目**；候选防死链口径沿用 07 时班
  （reasonix/fuxi/gitlawb/zero/empryo 等九候选全未装零接入）；Reasonix 35,742★ 候选首位
  维持 | 巡检
- **F（禅道）**：data/zentao.json 实读 claims=0 零积压、last_error 空、poll 未配置
  （用户侧预期非故障）；链路代码锚点沿用（zentao.py _scan/_route_one/_reconcile）；
  **零新禅道 AI 竞品（第 8 例后持续为零）** | 巡检
- **G（产品巡检）**：过时文案活码 grep（13种/15种/已接11/双源）零命中口径沿用；
  rank_scan 四源/发布双平台文案与实现一致；新毛病无 | 巡检

## 全类型覆盖对账（本步指令点名场景 vs 注册表）

直接执行=direct ✓ / 代码=code ✓ / 小说=novel ✓ / 连载=serial_novel ✓ / 自媒体文章=article ✓ /
调研报告=research ✓ / 短视频脚本=video_script ✓ / 技术方案=tech_proposal ✓ / 翻译=translation ✓ /
演讲稿=speech ✓ / 工作汇报=weekly_report ✓ / 商务邮件=email ✓ / 扫榜选材=rank_scan ✓ /
禅道工单=defect_retro ✓；注册表另含 doc/resume/bid_doc 三类型。额外关注面：对话记忆（A10
chatbot memory 组）/知识库（批5 域）/文档生成（A10 doc 组）本轮扫描均覆盖，零未覆盖机制。

## 待深挖队列（2026-10-05 10 时快照，与 07 时版对齐后重排）

1. 工具输出统一压缩管线（第 7 验证在手，管线级攒批）
2. 语义缓存（待租户/敏感边界拍板）
3. 演示文稿任务类型空档——交人拍板（univer 22.4k 例证维持）
4. 任务宪章编译为运行时强制策略（bernstein 1,397★ 在动，治理远期）
5. 聊天分叉（1code archived，管线级远期备注）
6. 小红书平台特化交付契约蒸馏（wenzi-xhs 153★，交人拍板）
7. webnovel-writer 三差量（向量 RAG 检索管线级攒批/追读力度量/ainovel-cli 卷弧滚动规划，
   交人拍板）
8. 宪章自动起草骨架（claude-rules 停更注记，差量角度成立交人拍板）
9. 既有攒批维持：ARIS 规则裁决/multica 看板视角/凭据保险库/CONTINUUM 幂等账本/
   ASD-STE100（交人拍板）；bernstein 签名血统回执+「机械决策不进 LLM」范式簇（远期备注）
10. **跨通道验证欠账**：本轮搜索引擎双通道被拦，WebSearch 交叉验证顺延下一窗口
    （距 04 时班 6 班未配成，下轮优先补）

## 口径勘误与移交

- **队列第 9 项（翻译腔检查）确认已落地**（v0.1.80 b68a6d1 TRANSLATION_APPENDIX 自查条，
  pipeline.py:2371）——07 时班快照误留「维持交人拍板」口径，本班以代码实证修正移出。
- 教训偏科口径更新：流程规范 25/70=36%（10-04 09 时班 33% 口径为 67 条时代；蒸馏 3 条
  均入流程规范类所致，属自学习正常增长非漂移）。

---

# 第 2/4 步：七专项代码级实证巡检（10 时班补注，2026-10-05）

> 口径：本轮不做网络扫描，七专项全部以「读代码 + 本机 which 探测 + 数据文件实读」为准，
> 逐条锚到 file:line；落地项只出评审稿不写码，评审通过后进第 3 步实现。
> keywords.md 本轮零调整（稳定期无新关键词需求，纪律：必要才改）。

## A. token 节约（已覆盖，四方向零重复建设）

实读锚点（全部当日行号级验证）：
- **三段压缩**：compaction.py（prune_text:42 头尾剪枝 → LLM 摘要 → surface replace；
  compact_region:119 压缩事务四件事件，摘要调用自身消耗经 usage.record(source="compaction")
  入账，净省量非黑箱）；触发闸 maybe_compact:182（压力比 ≥ 阈值才动手——天然避开
  claude-code-cache-fix 案的「压缩毁缓存」坑）
- **token_meter**：token_meter.py:148 单例压力表；branching.py:47 压力预检；
  step_runner.py:77-79 换将前按目标模型容量预检（0.9 事前门）
- **预算熔断**：pipeline.py:613 _budget_max_tokens（TUTTI_BUDGET_MAX_TOKENS env 优先，
  运维免改配置）+ :632 _budget_cost_caps 日/月花费硬顶 + :648 _cost_gate_block
  （先于 token 闸：钱比 token 早见顶）+ usage.py:184 _budget_alert_check 告警落盘
- **cascade**：pipeline.py:1519-1534（easy 任务按 tier 升序重排走廉价模型，
  capability.cascade_reorder:100，默认关）
- **_shrink_context_block**：pipeline.py:2528 四层降级（经验库→4K → 模块库按「## 」
  边界 → 圣经按二级标题边界）+ _serial_shrunk_block:2571 连载重试同待遇
- **经验召回**：skills.py:540 relevance_top（相关性→有效可信度→won 归因→衰减盈余）
  + :348 _surplus_decay 闲置衰减（hippo-memory 同构互证）
- **会话复用**：pipeline.py:136 _dead_binding_substitute（resume 会话跟人走不补位）/
  :206 _valid_resume / :270 _resume_sid（codex/claude/opencode/qwen 原生 resume，
  generic 靠 catalog resume_argv_template）；换将丢弃旧会话（pipeline.py:1508 附近）
- **diff-only 评审**：pipeline.py:945 评审变更集 = git diff HEAD + 未跟踪新文件
  （gitmod.collect_changes），评审官只看变更不看全量

四对标方向判定（不重复建设）：
- **prompt 缓存**：已有——usage.py:53/117 cached 细分按供应商回填记账，命中率可查；
  压缩时机设计已避开缓存破坏 | 已覆盖
- **语义缓存**：半已有——modelhub.py:3673 chat(cache_ttl>0) 精确匹配响应缓存已在
  （幂等调用专用，创作类刻意不开防屏蔽新输出）；语义级（同义改写命中）= 待深挖
  第 2 项，租户/敏感边界未拍板不建 | 维持
- **diff-only 评审**：已有（上） | 已覆盖
- **廉价模型分流**：cascade 已有 + task_compile.py:163/:164 轻量类型短链
  （email/weekly_report/translation 免大纲单评审）已是结构性分流 | 已覆盖

## B. 插件市场（六源通道实证，零接入维持）

- **六源在位**：market_remote.py:50 SOURCES（zcode/anthropic/anthropic-skills/
  claude-skills/clawhub/cocoloop）；本机缓存 data/market_remote/ 六文件全在
  （10-03 拉取，合计 **810 条**：zcode 26 / anthropic 315 / anthropic-skills 5 /
  claude-skills 99 / clawhub 215 / cocoloop 150）
- **安装通道**：preview_remote:1030 先看后装（token 一次性 + TTL 10 分钟 + 最多 8 份）
  → install_remote:1087 复用 market.install_files（避让/记账/卸载全沿用）
- **白名单闸**：inspect_tree:568 剥离式检查——scripts/hooks/commands/agents 目录与
  .mcp.json 剔除（:542/:592）、可执行扩展名 _EXEC_EXT:544 全拒、repo 来源按条目
  skills 白名单收敛（:587）；build_files:935 SKILL.md→skillpack frontmatter 重写
- **SSRF 防护**：assert_public_url:113（仅 https + 解析全部 IP 拒环回/私有/保留）；
  _fetch:136 逐跳重定向复检（_MAX_REDIRECTS=3）；体量五重上限（清单 5MB/包 80MB/
  解包 120MB/500 文件/单文本 512KB）；_safe_extract:632 收容三重防线；
  git clone 前 assert_public_url + http.followRedirects=false（:834-845）
- **装前内容检查**：preview 阶段 skill_scan.scan_text 危险模式扫描（风险红字确认）+
  供应链对账（digest 变更检测/近名仿冒检测 :1057-1065）
- **候选三问裁决**：本轮新见 Busbar（治理平面，编排重合度高）/ Strata（本地推理引擎，
  非技能包）/ Kane CLI（无正主仓）——重合度/可直读性/用户会搜吗均不过，
  雷达跟踪不接入。本轮无蒸馏级新方法论（三问判据已在经验库
  sk-71d4cce9be4e / sk-69d2bf6ecf2d，近重不重复入库）。

## C. 任务类型（实数 17，用户列举 14 项全覆盖，无删项凑数）

- **注册表**：flows.py:36 BUILTIN_FLOWS 实数 **17**（direct/code/novel/serial_novel/
  article/video_script/doc/translation/rank_scan/defect_retro/research/speech/
  weekly_report/email/tech_proposal/resume/bid_doc）。用户口径「13 种」与列举 14 场景
  均以注册表为准：列举 14 项全映射，另含 doc/resume/bid_doc 三类型（README:134 同口径）
- **维度映射**：dispatch.py:12 TYPE_DIMENSIONS 18 键（17 类型 + zentao 集成维度），
  无漏网落 reasoning 默认
- **编译参数**：task_compile.py:44 compile_task（difficulty 三档判定 :13 含研究/方案类
  默认 hard；rubric ≤8；thinking 枚举收敛）+ :93 code_workflow（easy 低风险短链，
  高风险词表 :104 强制评审，验证命令可替代模型评审）+ :156 content_workflow
  （light=email/weekly_report/translation 免大纲；deep=novel/research/tech_proposal
  双评审；threshold≥8.5 强制双评审 :194）
- **菜单**：任务表单类型下拉直接渲染 /api/flows（flows.list_flows:253，overrides
  已套用），name/goal_hint/note 单一真源；i18n 三字段守卫（test_i18n_dups）在位
- **辅助信息专项锚点**：translation→pipeline.py:2371 TRANSLATION_APPENDIX（术语表
  先行+翻译腔自查，v0.1.80）；weekly_report→:952 _gitlog_brief（git log 素材，
  -n 40 封顶）；全类型→:5099 _read_constitution（任务宪章注入块最前）；research
  缺口驱动补查+结论先行（:2360 附近）；rank_scan 四平台文案（flows.py:82）与
  paihang.py:23-26 实源一致；defect_retro 禅道/Jira CSV 附件口径与 zentao 集成注记
  （README:241「不占菜单类型」）一致
- 结论：全部注册项「菜单/参数/辅助信息」三面对账一致，无缺漏无过时。

## D. 经验库（数据卫生复核，本轮无动作对象）

- **真实存储**：data/skills.json（lessons 70 + packs 3）；写入既有入口
  skills.py:424 upsert_lesson（id=scope+标题哈希 :318，分类闭集
  LESSON_CATEGORIES:46 六类，:75 归一化兜底）
- **实测统计**（脚本实读）：流程规范 25（35.7%）/节奏爽点 21/情节逻辑 10/
  人物塑造 7/一致性 4/文笔风格 3；kind=lesson 66 + procedure 4；
  **id 零重复、标题零重复**（含标题包含去重前基线实测）
- **偏科判定**：36% 流程规范属自学习正常增长（工程复盘/调研判据类条目天然入此类），
  非分类漂移，不做强制搬类；去重/合并无对象
- **入库裁决**：本轮零新机制 → 无新蒸馏方法论；调研三问判据已在库
  （sk-71d4cce9be4e / sk-69d2bf6ecf2d 近重），不重复入库

## E. 新 CLI（防死链口径维持）

- **catalog.py:32 DEFAULT_CATALOG 实数 14 条目**（codex-cli/claude-code/opencode/
  qwencode/aider/openclaw/kimi-code/mimo-code/grok-build/pi/deepseek-harness/
  gemini-cli/codebuddy/trae-agent）
- **本机 which 实测**：13/14 在装（**openclaw 未装**，installable 状态如实）；
  五候选 **reasonix/fuxi/gitlawb/zero/empryo 全部未装**——只记候选不接入（防死链），
  Reasonix（35,742★）候选首位维持，接入打法见 knowledge.md 既有条目

## F. 禅道（只读巡检，无写操作）

- **链路代码在位**：zentao.py _scan → _route_one:2128（模块路由/AI 排查定责）→
  _reconcile:2093 → _ensure_resolved:1708（幂等 resolve，resolved/closed 视为成功，
  未提交改动拦截谎报）→ _transfer:1729（转派+评论）→ _notify:1754（群通知）；
  调度三件套：fire_due:2470（automation._tick:580 每拍调用+内部节流）/
  scan_now:2479（手动绕闸）/ _boot_reconcile:2484（重启补对账，#27697 案）
- **数据实读** data/zentao.json：claims=0（**零积压**）、archived=0、last_error 空、
  last_scan=2026-09-21 20:43:44、poll 未配置（poll_enabled 缺省 false——用户侧预期
  非故障；启用后 fire_due 才生效）
- **产品档案路由**：_norm_profile:2517（产品 ID 整数必填/双端 repos 绝对路径/
  owners 三向/severity_cap 钳位）在位，无积压可路由故本轮无路由实测对象
- **竞品**：零新禅道 AI 集成（第 8 例后持续为零）
- **限制如实记录**：线上禅道写操作（resolve/转派/评论）未在明确授权范围，
  本轮全部只读

## G. 产品巡检（文案对账，随手修观察项 1 处交评审）

- README:134/:241「17 种任务类型」与注册表实数一致；「禅道工单自动转 code 修复任务、
  不另占菜单」注记与实现一致
- 过时活码 grep（13种/15种/已接11/单源/四源）零命中
- **唯一观察项（不动，记录在案）**：app.js:13360/:13402 与 i18n.js:681/:2622 的
  「600+ 技能」静态数字——六源缓存实数 810 且随上游浮动，写死更大数字会再过期；
  已列入落地候选 2 交评审（去数字化）

## 落地候选（评审稿——通过后才实现，本步不写码）

### 候选 1：新增「演示文稿」任务类型（presentation）

- **真实文件与函数**：app/core/flows.py `BUILTIN_FLOWS`（:36，追加 1 条目）+
  app/core/dispatch.py `TYPE_DIMENSIONS`（:12，加 `"presentation": "writing"`）+
  tests/ 新测试（仿 tests/test_flow_icons.py 断言注册表字段与维度映射）
- **现状**：待深挖队列第 3 项（多班「交人拍板」，univer 22.4k★ 例证）；17 类型无
  演示文稿，用户用 doc 将就，评审维度（视觉呈现/页面信息密度）错配
- **目标行为**：id=presentation、name=演示文稿、icon=`i-preview`（复用精灵表现成
  图标，不加新 sprite）、engine=review、manuscript=presentation.md、
  rubric=[结构逻辑, 内容密度, 视觉呈现, 演讲适配]、threshold 7.0、rounds 2；
  note 如实写「产出逐页大纲与讲稿（Markdown），不生成 PPT 二进制」防预期错位；
  task_compile content_workflow 默认走标准单评审短链（不进 light/deep 集合，
  零额外改动）
- **验收方法**：py_compile 两文件；tests/ 新测试（get_flow('presentation') 字段
  断言 + TYPE_DIMENSIONS 含 presentation + BUILTIN_FLOWS 全量 digest 稳定不触旧类型）；
  unittest discover 全量全绿；UI 冒烟类型下拉出现「演示文稿」
- **风险**：低——纯注册表追加，新 id 不影响既有任务 flow_revision 对账；
  无 sprite 手术（复用 i-preview）即无 style.css/index.html 改动

### 候选 2：帮助页「600+ 技能」静态数字去数字

- **真实文件与函数**：app/ui/app.js:13360（`t("600+ 技能，一键安装")`）与 :13402
  （`t("设置 → 插件市场：600+ 技能一键安装，…")`）+ app/ui/i18n.js:681/:2622 对应
  英文条目
- **现状**：六源缓存实数 810 且随上游浮动，静态下限必然持续过期（G 项唯一观察）
- **目标行为**：中英文案去「600+」（如「技能一键安装」/「skills install in one
  click」），来源描述保留，四条字符串同步
- **验收方法**：node --check 两 js；grep 全仓无「600+ 技能」残留（tests/_out 生成物
  除外）；unittest discover 全量全绿
- **风险**：极低——纯文案，无测试断言旧串（tests/_out 为生成报告非断言源）

## 移交第 3 步

- 两个候选待评审拍板；通过后按「改前 Read（并行代理防冲突）/style.css 追加尾/
  Write/Edit 直写/新测试 tests/ + git add -f」纪律实现，再走五道关。
- 待深挖队列其余项口径不变（第 1/2/7 项管线级攒批，第 4/5/8/9 项远期，
  第 6 项交人拍板，第 10 项跨通道验证下轮优先补）。

---

# 第 4/4 步：批5 复扫 + 雷达复查 + 落地核验 + 五道关收口（12 时班，2026-10-05）

> 12 % 7 = 5 → 批5（检索/知识/浏览器）。分支 main（bb88049）。工作区已有第 3/4 步
> 落地件（两候选实现 + tests/test_full_type_iteration.py + knowledge.md 10 时班条目），
> 逐 hunk 核对属本轮评审稿范围后一并收口（候选 1 的 icon 有一处合理偏差，见下）。

## 调研实录（主扫 + 雷达 C + 跨通道清账）

- **主扫完整落盘**（首轮输出被 tail 管道截断只留尾 60 行，如实记录后重跑）：
  **115 查询 524 行 462 唯一仓，零失败零限流**（A 常驻 15 组 + A1u/A1p2 双轮 + B1
  内置 51 + B5 轮换 50；gh api 串行 4s）。**批5 域零新竞品**：ragflow 91,687/
  Agent-Reach 91,081/LibreChat 45,287/khoj 37,562/gpt-researcher 29,919/career-ops
  73,491 全部已沉淀。**本轮零机制级新差量（连续第 22 班稳定期）**。
- **WebSearch 交叉验证欠账清账**（队列第 10 项）：通道本轮恢复（10 时班 DDG/Bing
  双拦后补上），串行两查（RAG/知识库 + 写作域）——唯一仓 claim tinkvu/WriterAI 经
  repos 实证 **5★、2024-08 停更**，微型死仓不入库（「新闻面 ≠ 开源仓在」再证）。
- **全名对照表补勘**（repos 端点逐仓实证 27 仓，此前知识库多用短名）：
  **ponytail=DietrichGebert/ponytail 155,083（10-05）**/orca=stablyai/orca **85,137
  （10-05，+102 续领跑）**/agentmemory=rohitg00/agentmemory 29,136/pi=
  earendil-works/pi 112,486/OpenSpec=Fission-AI/OpenSpec 71,055（10-05 当日 push）/
  claude-mem=thedotmack/claude-mem 96,221（+63）/opencode=anomalyco/opencode 211,783/
  hermes-agent=NousResearch/hermes-agent 251,268（10-05）/spec-kit=github/spec-kit
  140,140——全部 alive 零 archived；头部与 10 时班缓涨一致（superpowers 295,342
  09-27 后无 push 维持/ECC 273,067/mattpocock/skills 276,321 10-04）。
  （bernstein 正主由 13 时班勘定为 sipyourdrink-ltd/bernstein 1,396，本班悬置口径
  以其为准。）
- **雷达源 C**：trendshift 本轮被拦（WebFetch 域名校验 + curl 空返回，如实记录），
  以 topic 沿用（10 时班 8 页零新面孔）+ npm 两查补位（agent orchestrator/claude
  code manager——全为已知族微型新生件，零接入级）。awesome 清单 repos 实测：
  awesome-llm-apps 140,734（09-30）/awesome-mcp-servers 95,837/awesome-claude-code
  55,091（10-05）/VoltAgent 35,214（10-02）/harness-engineering 4,706（10-04）/
  Agent-Memory 658（10-04）全活；Long-Horizon 1,060（09-22 停更观察维持）/
  awesome-ai-agents-2026 1,918（06-10 停更半年）。
- **禅道周边**：easysoft/zentao-cli 61★（10-04 活跃，官方）——**零新禅道 AI 竞品
  （第 8 例后持续为零）**。
- **E 域**：esengine/DeepSeek-Reasonix **35,739★（10-05 当日 push）候选首位维持**。

## 第 3/4 步落地件核验（并行代理产出，本轮收口）

- **候选 1（presentation 演示文稿类型）**：flows.py 注册表第 18 条目（rubric 四维/
  threshold 7.0/rounds 2/manuscript=presentation.md/note 如实声明不生成 PPT 二进制）
  + dispatch writing 维度 + pipeline CONTENT_DELIVERY_CONTRACTS「演示设计顾问」四约束
  + task_compile 零额外改动（测试锚定标准单评审短链不进 light/deep）。
  **一处合理偏差**：评审稿原判「复用既有 i-preview 无需 sprite 改动」不实——精灵表
  本无 i-preview，实现代理补 symbol 并加测试锚（test_icon_symbol_defined_in_sprite），
  偏差有测试护栏，予以收口。
- **候选 2（600+ 技能去数字）**：app.js 两处 + i18n.js 新键「技能一键安装」，
  旧键双条零残留（测试哨兵防后值覆盖前值的隐形文案事故）。
- 新测试 tests/test_full_type_iteration.py **8 用例全绿**；test_full_type_round.py
  升 18 口径（矩阵/额外四类型/docstring 同步）。

## 七专项复验（12 时班本地实证）

- **A（token 节约）**：锚点抽验在位（compaction.py:182 maybe_compact /
  gitmod.py:304 collect_changes / pipeline.py:613 _budget_max_tokens /
  capability.py:100 cascade_reorder），10 时班第 2/4 步当日行号级实读沿用 | 已覆盖
- **B（插件市场）**：六源缓存实数 **810 条**（zcode 26/anthropic 315/
  anthropic-skills 5/claude-skills 99/clawhub 215/cocoloop 150，10-03 拉取；
  catalog[].plugins 口径实测）；本轮新见候选零（npm 面已知族微型件三问均不过），
  零接入维持 | 已覆盖
- **C（任务类型）**：BUILTIN_FLOWS **实数 18**（import 实证），TYPE_DIMENSIONS 18 键
  （17 类型 + zentao；defect_retro 有意落 reasoning 默认，多班同口径）；用户 14 场景
  全映射 + 额外 4（doc/resume/bid_doc/presentation）——**演示文稿空档本轮落地补齐**；
  18 类型全量「菜单/参数/辅助信息」对账以 10 时班第 2/4 步 + 新测试共同锚定 | 巡检+落地
- **D（经验库）**：data/skills.json 实读 **70 条，id/标题零重复**；流程规范
  25/70=36% 偏科口径维持（自学习正常增长非漂移，不做强制搬类）；本轮零新机制
  无新蒸馏入库 | 数据卫生
- **E（新 CLI）**：本机 which 实测 10 个在装（codex/claude/opencode/qwen/aider/
  kimi/mimo/pi/gemini/codebuddy），openclaw 未装；五候选 reasonix/fuxi/gitlawb/
  zero/empryo **全未装零接入（防死链）**，接入打法见 knowledge.md 既有条目 | 巡检
- **F（禅道）**：data/zentao.json 实读 claims=0 **零积压**、last_error 空、poll 未
  配置（用户侧预期非故障）；链路锚点沿用 10 时班第 2/4 步（_scan/_route_one/
  _reconcile/_ensure_resolved/fire_due 全在位）；零新竞品 | 巡检（只读）
- **G（产品巡检）**：过时文案活码 grep（13种/15种/已接11/17种/**600+ 技能**）全仓
  零命中——**唯一观察项（静态数字）已随候选 2 落地消除**；新毛病无 | 巡检

## 五道关实录

- ⓪ `git branch --show-current` = **main** ✓（全程未切分支）
- ① py_compile（flows/dispatch/pipeline）+ node --check（app.js/i18n.js）全过 ✓
- ② unittest discover 全量 **exit=0 三轮独立实证**（首轮/无 -v 终跑/-v 轮 1,589
  用例级 ok；汇总行在 Windows stdio 缓冲下偶发不可见——exit code 契约
  「有败必非零」为准，过程如实记录）；test_deepseek_harness 单模块 discover 复跑
  21/21 OK 排除 -v 交错假象 ✓
- ③ 逐 hunk 自审：18 类型三展示字段/i18n EN 键各恰一条/交付契约接线/精灵 symbol/
  README 双处计数+表格行同步——逐项对上评审稿口径，无越范围改动，硬编码「17 种」
  全仓零残留 ✓
- ④ git add 仅限本轮文件（新测试 test_full_type_iteration.py 走 `git add -f`）；
  `git diff --cached` 扫外来标记（pick_dialog/ask_directory/backoff）零命中 ✓
- ⑤ commit 后 `git show --stat HEAD` 核对 ✓

## 发版与移交

- 当日有代码入库 → 走发版：test_selfupdate 全绿（6/6）→ package.json patch+1 →
  CHANGELOG 顶部追加 → push → npm publish → npm view 核对。
- **待深挖队列更新**：第 3 项（演示文稿空档）**已落地划掉**；第 10 项（WebSearch
  交叉验证欠账）**本轮清账**；其余 1/2/4-9 项口径不变。
- **全名对照表移交**：ponytail=DietrichGebert/orca=stablyai/agentmemory=rohitg00/
  pi=earendil-works/OpenSpec=Fission-AI/claude-mem=thedotmack/opencode=anomalyco/
  hermes-agent=NousResearch/spec-kit=github——后续班复查直接用，免再勘定。

---

# 第 3/4 步：落地件实现收口 + 第 1/4 步调研复跑（13 时班，批6：框架/平台/SDK 生态）

> 开工 13:02、分支 main（bb88049）。工作区即 10 时班评审稿通过后的实现件
> （presentation 演示文稿类型 + 「600+ 技能」去数字化，含 test_full_type_iteration.py
> 8 用例 + test_full_type_round.py 扩至 18 类型矩阵——两文件 13 用例本班实跑全绿）。
> 13 % 7 = 6 → 批6。主扫 `scripts/borrow_scan_nightly.py` 无参全量：
> **115 查询 524 行 469 唯一仓，零失败零限流**（gh api 串行 4s）。
> **WebSearch 交叉验证欠账本班补上**（距 04 时班 5 班，队列第 10 项清账）——
> 捞出 **mksglu/context-mode 25.4k★（A3 token 节约域大仓，HN #1）**，gh api 通道
> 连续多班「零差量」后再证单通道盲区（谁/何时/为何：13 时班第 3 例实证）。

## 通道实录

- **主扫**：115 查询全 ok。批6 已录为主零增量（06 时班同批 467 仓 vs 本班 469 唯一仓，
  高度重合）；**零机制级新差量（连续第 22 班稳定期——gh api 通道口径）**。
- **WebSearch 交叉验证（跨通道，本班补账）**：单发串行可过。捞仓名未勘定三件，
  repos 端点二次实证（「新闻面 ≠ 开源仓在」第 3 例）：
  - maestro-orchestrate → **josstei/maestro-orchestrate 463★（08-07）**真仓在——多 CLI
    编排平台（Gemini CLI/Claude Code/Codex/Qwen Code，39 专家+Express 快路径+4 阶段
    工作流），A1 域微型 | 雷达
  - OpenHarness → **HKUDS/OpenHarness 15,913★**（内置个人 agent Ohmo，参考）与
    **autonomous-ai/openharness 1,105★（10-05 push）**「All your agents. All your
    machines. One」两同名仓并存，harness 域 | 参考/雷达
  - Agents Window → 正主仓名未勘定（搜出全为微型桌面 app），**不入库**（纪律）
- **repos 端点存量复查 22 仓**：头部 orca **85,140（+105 续领跑）**/gstack 135,216（+42）/
  mattpocock/skills **276,333（+101，对 superpowers 295,342 逼近至 -19k）**/ECC 273,071（+65）/
  hermes-agent 251,268/opencode 211,783/anthropics/skills 179,675/ponytail 155,099——全部
  alive 零 archived；写作域 webnovel-writer 7,318/ainovel-cli 2,096（10-05 push）/
  **yomiyasu 1,423（+23 在动）**/drama-skills 2,505；E 域 DeepSeek-Reasonix 35,740
  （10-05 push 候选首位维持）；治理/记忆域 SkillSpector 19,403（+12）/bernstein
  （正主勘定 **sipyourdrink-ltd/bernstein** 1,396，10-04 push）/spec-kit 140,142/
  OpenSpec 71,055（10-05）/agentmemory 29,136/planning-with-files 27,285。
- **npm/pypi**：pypi 历班拦截在案省配额；npm 零接入级沿用上班口径（本班未复试）。

## 新条目（本班真新面孔）

- **mksglu/context-mode**（25,423★，10-04 push，HN #1 570+ 分）新入库 | **A3 token 节约域
  最大新差量**：MCP 服务器形态，四机制——①工具输出前置沙箱（原始数据落 SQLite/FTS5
  不进上下文，315KB→5.4KB=98% 缩减）②会话连续性（文件编辑/git/任务/错误/用户决策
  事件级追踪入 SQLite，压缩时索引 FTS5、BM25 按需检索——**压缩后记忆保留**）
  ③Think in Code（LLM 写脚本处理数据只回传结果，47×Read=700KB→1×ctx_execute=3.6KB）
  ④显式不做输出风格强制（引用 Moonshot kimi-k2.5 案：激进简洁提示降基准）
  | 对比：我们三段压缩是「后处理」（剪枝→摘要→替换），它是「前置沙箱」；差量=事件级
  结构化索引+检索（我们压缩靠 LLM 摘要保要点）；④印证我们不做输出压缩的设计
  | **借鉴方向：并入待深挖第 1 项（第 8 验证+新机制角度：前置沙箱+事件索引检索）**
  | 2026-10-05
- **josstei/maestro-orchestrate**（463★，08-07）新入库 | 多 CLI 编排平台 39 专家角色
  （Gemini CLI/Claude Code/Codex/Qwen Code）+Express 快路径+4 阶段工作流 | A1 域微型
  （39 专家≈我们类型角色，量级小）| 雷达 | 2026-10-05
- **HKUDS/OpenHarness**（15,913★，06-04）新入库 | Open Agent Harness+内置个人 agent
  Ohmo | harness 域（hermes/deepseek-harness 簇旁），机制面未见新差量 | 参考 | 2026-10-05
- **autonomous-ai/openharness**（1,105★，10-05 push）新入库 | 「All your agents. All your
  machines. One」多机 harness 聚合 | 与 HKUDS/OpenHarness 同名异主，harness 域 | 雷达 | 2026-10-05

## 第 3/4 步落地件收口（本班核对）

- **落地件 1：演示文稿（presentation）类型**——flows.py BUILTIN_FLOWS 第 18 条
  （id=presentation/name=演示文稿/icon=i-preview/engine=review/manuscript=presentation.md/
  rubric=[结构逻辑,内容密度,视觉呈现,演讲适配]/threshold 7.0/rounds 2/note 如实声明
  不生成 PPT 二进制）+ dispatch.py TYPE_DIMENSIONS 归 writing +
  pipeline.py CONTENT_DELIVERY_CONTRACTS 演示设计顾问四约束 + index.html i-preview
  sprite + i18n.js EN 七键 + README 18 类型口径。测试：test_full_type_iteration.py
  6 用例（注册表字段/维度与编译规格/标准单评审短链/交付契约接线/精灵表定义/i18n
  恰好一条）全绿。
- **落地件 2：「600+ 技能」去数字化**——app.js 两处 + i18n.js 两键（「技能一键安装」
  /「Skills install in one click」），旧键零残留断言在 test_full_type_iteration.py。
- 队列第 3 项（演示文稿空档）**落地划掉**；univer 22.4k 例证完成使命。

## 七专项快照（13 时班，锚点复查口径）

- **A（token 节约）**：锚点行号级复查全在位（_budget_max_tokens:613/_cost_gate_block:648/
  maybe_compact:182/TRANSLATION_APPENDIX:2377/_read_constitution:5105/
  _shrink_context_block:2534/cascade_reorder:1525/usage.py cached:117——presentation
  契约块插入致 +6 漂移属正常）。**本班最大增量=context-mode（见新条目）**：待深挖第 1 项
  获第 8 验证+「前置沙箱 vs 后置压缩」「事件索引 FTS5/BM25 检索」新机制角度 |
  已覆盖+新差量入队
- **B（插件市场）**：六源在位（market_remote.py:50 SOURCES+缓存 6 文件）；本班新见
  四候选（context-mode 是 MCP 服务器非技能包/maestro 463★ 平台/OpenHarness×2 harness）
  三问（重合度/可直读性/用户会搜吗）均不过，零接入维持；context-mode 方法论并入
  A 专项待深挖 | 已覆盖
- **C（任务类型）**：BUILTIN_FLOWS **实数 18**（presentation 落地后）；14 场景全映射+
  doc/resume/bid_doc/presentation 四额外；test_full_type_iteration 6 用例+
  test_full_type_round 18 类型矩阵 5 用例全绿实证；i18n 三字段 EN 键恰好一条断言在位
  | 巡检+落地核验
- **D（经验库）**：data/skills.json 实读 70 条（lesson 66+procedure 4；流程规范 25=36%/
  节奏爽点 21/情节逻辑 10/人物塑造 7/一致性 4/文笔风格 3）；id/标题零重复；
  本班蒸馏：context-mode「前置沙箱+事件级检索」方法论并入待深挖队列（非教训入库——
  队列项不是教训）| 数据卫生
- **E（新 CLI）**：catalog.py:32 DEFAULT_CATALOG 14 条目；which 探测 9 直名命中
  （多词名口径沿 07 时班 13/14 在装）；五候选（reasonix/fuxi/gitlawb/zero/empryo）
  未装零接入防死链，Reasonix 35,740★ 候选首位维持 | 巡检
- **F（禅道）**：data/zentao.json 实读 claims=0 零积压、last_error 空、last_scan
  09-21、poll 未配置（用户侧预期非故障）；链路锚点（fire_due/_reconcile/_boot_reconcile）
  沿用 10 时班行号实证；零新禅道 AI 竞品（第 8 例后持续为零）| 巡检
- **G（产品巡检）**：过时活码 grep（13种/15种/已接11/17种任务）零命中；上一班唯一
  观察项「600+ 技能」本班随落地件 2 划掉；新毛病无 | 巡检

## 待深挖队列（2026-10-05 13 时快照）

1. 工具输出统一压缩管线（**第 8 验证=context-mode 25.4k★，新增「前置沙箱+事件索引
   FTS5/BM25 检索」机制角度**；管线级攒批）
2. 语义缓存（待租户/敏感边界拍板）
3. ~~演示文稿任务类型空档~~（**本班落地划掉**，presentation 已入 18 类型注册表）
4. 任务宪章编译为运行时强制策略（bernstein 1,396 在动，治理远期）
5. 聊天分叉（1code archived，管线级远期备注）
6. 小红书平台特化交付契约蒸馏（wenzi-xhs 153★，agency-agents-zh 中文角色域旁证，交人拍板）
7. webnovel-writer 三差量（向量 RAG 攒批/追读力度量/卷弧滚动规划，交人拍板）
8. 宪章自动起草骨架（claude-rules 停更注记，交人拍板）
9. 既有攒批维持：ARIS 规则裁决/multica 看板视角/凭据保险库/CONTINUUM 幂等账本/
   ASD-STE100（交人拍板）；bernstein 签名血统回执+「机械决策不进 LLM」范式簇（远期备注）
10. ~~WebSearch 交叉验证欠账~~（**本班补账划掉**——context-mode 第 3 例再证跨通道价值，
    后续每 3-4 班继续按纪律配额）

## 移交第 4 步

- 落地件两件已在工作区，随本班走五道关→提交→push→发版 v0.1.85（当天有代码入库）。
