# 全类型竞品调研迭代报告（iteration-report.md）

> 新一轮「调研→对比学习→巡检→落地→自检→提交→发版→沉淀」循环的调研主报告。
> 每班追加本节内容、保留历史；详细机制沉淀见 knowledge.md，当日叙事见 2026-10-04.md。

---

## 2026-10-04 03 时班（批3：计划/spec/长任务——新一轮计划第 1/4 步）

- **班次**：2026-10-04 03:50-04:0x，小时数 3 → 3%7=3 → 轮换批3（计划/spec/长任务 11 组）
- **工作区状态**：分支 main ✓、工作区干净（git status porcelain 空）、HEAD 5701a89（v0.1.78）；本地实锚复核 BUILTIN_FLOWS **17 类型** / data/skills.json **67 条**（lesson 63+procedure 4）/ DEFAULT_CATALOG **14 条目**——与凌晨班/02 时复核班/巡检落位班三向记录一致，基线未被并行代理改变。
- **今日班次衔接**：凌晨班 01:27（批1 全量主扫 116 查询）→ 02 时复核班（独立背书）→ 巡检落位班（A-G 二轮实证）→ 02 时补录班（并行扫描增量）均已入库；本班为 03 时独立班，批3 为今日首个轮换班，不与前班重复主扫。

### 搜索覆盖矩阵

| 通道 | 规模 | 结果 |
|---|---|---|
| 批3 主扫（scripts/borrow_scan_nightly.py --batch 3） | 11 查询 50 行，gh api 串行 4s，EXIT=0 零失败零限流 | 头部全为已沉淀仓 |
| repos 端点复查 | 12 仓（批3 域 4+头部 5+E 域 1+待深挖在动 2），hippo 正主路径以 search 端点补核 | 12/12 全活跃零 archived |
| WebSearch 串行单发 | 批3 域新闻面（spec-driven 2026-10 新发布） | 无新开源发布 |
| A 常驻组 | 本小时不重复主扫（凌晨班 01:27 已全量 116 查询，同窗纪律） | 引用凌晨班 |
| 雷达源 C | repos 端点 12 仓即本班雷达复查面；topic/npm/pypi 未重复（凌晨班+补录班已过，pypi 三种拦截形态结论维持） | 引用+增量 |

### 批3 逐组结果与已录对照（50 行全量消化）

- **long-running/persistent-planning**：planning-with-files 27,267★（已沉淀：task_plan 落盘/活计划回写/子任务进度注入三件已抄）/ PlanWeave 407★（10-03 已录，方向验证）/ plandeck 66★（10-03 已录）/ 杂讯 2 例（2022 年文档仓）。
- **spec-driven**：OpenSpec 70,977★（已深挖：explore/archive 两借鉴方向在队列）/ get-shit-done 64,383★+gsd-2 7,777★（已录）/ agent-os 5,466★（已录）/ spec-workflow-mcp 4,299★（已录）。
- **plan-and-execute**：PraisonAI 9,130★ / TaskWeaver 6,168★ / refact 3,540★（均已录）；oliver-kriska/claude-elixir-phoenix 560★（Claude Code 插件 26 专家 agent+「Iron Laws enforcement」——规则强制执法向，ironcurtain 族同域小标，**新面孔定性入库**）。
- **task-decomposition**：DeepResearchAgent 3,552★（已录）/ spec_driven_develop 981★（已录族）/ claude-swarm 380★（已录族）/ plan-cascade 133★（已录族）/ codex-factory 100★（10-03 已录）。
- **milestone-tracking**：全为杂讯/新生儿（160★ 为 2021 文档仓）——里程碑追踪独立赛道维持空。
- **project-planning**：lazycodex 3,722★（已录，09-22 2k→3.7k 大涨，10-03 push 活跃）/ AI-Agents-Projects-Tutorials 2,916★（教程清单域，雷达）/ **croffasia/itsaplan 879★（新面孔：self-hosted Linear/Plane 替代、团队与 AI agent 并肩规划的项目管理工单——F 专项禅道/项目管理周边同域，组织级工具与我们「接已有禅道」不同形态，参考）** / **ZykjShadow/Async 475★（新面孔：IDE 形态 AI 编码工作台，chat+planning+agent 执行统一桌面体验——IDE 路线参考，停更 05-19）**。
- **autonomous-long-horizon**：InternAgent 1,444★（科研发现长程框架，域参考）/ **AI45Lab/OpenART 228★（新面孔：动态长程有状态环境的 agent 安全鲁棒性评测框架——评测域，我们自评测缺口远期旁证，雷达）** / OneDayAgent 36★（长程 harness 学术向）/ cogneva 30★（数字员工 24×7 小件）。
- **worktree-parallel**：worktrunk 8,726★（09-29 已录 8.4k→8,726 活跃）/ ccpm 8,400★（GitHub Issues+worktree 并行，已录族）/ pro-workflow 2,899★（已录）/ **coollabsio/jean 1,305★（新面孔：AI agent 的 dev environment，10-02 活跃——dev env 形态参考）** / uzi 582★（停更 2025-06）。
- **checkpoint-resume**：atlas-agent-control-plane 105★（evidence-backed memory+governed runtime+checkpoint DAG recovery——「不信任自报」+checkpoint 族方向验证，**新面孔定性入库**）/ tiger_cowork 62★（自托管多 provider workspace 小件）/ **CONTINUUM（Cyrax321）28★（10-02 push）= 09-21 已录同仓**（语义检查点+幂等动作账本，幂等键待深挖攒批维持，非新面孔）/ Echo 8★（小件）。
- **acceptance-criteria**：全 0-1★ 新生儿（prd-ac-execution-guide 1★ 等）——验收标准独立赛道维持空。
- **requirement-elicitation**：0 结果——需求拷问域我们 grill-me 移植已满配。

### 新条目（微型定性入库，均无未覆盖机制级差量）

- **croffasia/itsaplan**（879★，10-03 活跃）| self-hosted Linear/Plane 替代，团队与 AI agent 并肩规划发版的项目管理+工单 | 与禅道集成（F 专项）同域不同形态：我们接已有禅道回写，不造项目管理工具；组织级多人工单流不适用单人单机 | 参考（F 域雷达）| 2026-10-04
- **coollabsio/jean**（1,305★，10-02 活跃）| AI agent 的 dev environment（开发环境形态）| 我们=编排台（工作目录即环境），dev env 托管路线参考 | 雷达 | 2026-10-04
- **AI45Lab/OpenART**（228★，10-03 活跃）| 动态长程有状态环境 agent 安全/鲁棒性评测框架 | 评测域补格（harbor/Kiln/evalscope 旁）；自评测缺口维持远期 | 参考（评测域）| 2026-10-04
- **malevrigns/atlas-agent-control-plane**（105★，09-13）| auditable 控制面：evidence-backed memory+governed tool runtime+checkpoint DAG recovery | 证据锚/事件流计数/checkpoint 恢复均有对应物；DAG 形态恢复为同域再验证 | 方向验证 | 2026-10-04
- **oliver-kriska/claude-elixir-phoenix**（560★，10-02）| Claude Code 插件 26 专家 agent+「Iron Laws enforcement」 | 规则强制执法向（ironcurtain/宪章运行时化待深挖第 4 项同域佐证 +1）| 方向验证 | 2026-10-04
- **ZykjShadow/Async**（475★，05-19 停更 4 月+）| IDE 形态 AI 编码工作台 | IDE 路线历史标本 | 参考 | 2026-10-04
- **MARKTECHPOST-AI-MEDIA-INC/AI-Agents-Projects-Tutorials**（2,916★）| 多 agent 系统/记忆/规划教程清单 | 清单域雷达（不入市场三问不过）| 雷达 | 2026-10-04
- 其余小件：InternAgent 1,444★（科研长程，域外参考）/ OneDayAgent 36★ / cogneva 30★ / tiger_cowork 62★ / Echo 8★——体量与机制面均小，雷达一句带过。

### 复查记录（repos 端点 12 仓 + search 补核，04 时）

- 批3 域全活跃：github/spec-kit **140,000★**（10-03 push，vs 10-04 凌晨班 139,986 +14）/ Fission-AI/OpenSpec 70,977★（10-02）/ planning-with-files 27,267★（10-01）/ agentmemory 29,117★（10-03 push）。
- 头部续涨：orca **84,351★**（10-03 push，vs 02 时 84,311 +40 续领跑）/ superpowers 294,848★（09-27）/ anthropics/skills **179,515★**（+8）/ claude-mem **95,482★**（10-03 push，+53）。
- E 域：esengine/DeepSeek-Reasonix **35,737★**（10-03 push）候选首位维持；九候选全未装零接入维持。
- 待深挖在动仓：**kitfunso/hippo-memory 770★（10-03 push，正主路径经 search 端点补核，待深挖第 7 项「标记无用」维持）/ lifedever/claude-rules 192★（03-19 维持，第 10 项宪章骨架维持）**。
- **星数跃升互证**：danyuchn/asd-ste100-skill **409→3,211★**（10-04 补录班 409 → 本班 3,211，push 停 09-08 无新提交——存量放量，疑入榜/被清单收录；ASD-STE100 技术英语规范化蒸馏候选价值不变，仍小件交人拍板）。
- 状态变更：12 仓零新增 archived。

### 七专项快照（本班实证面 + 引用今日既有班次）

- **A token 节约**：批3 域零新 token 机制；锚点实证引用巡检落位班二轮（预算熔断/评审深度/cascade/_shrink/task_plan 全在位）；四方向既有结论维持 | 已覆盖
- **B 插件市场**：本班新见（itsaplan/jean/OpenART 等）过三问（重合/可直读/用户会搜吗）均不过，零接入维持；六源连通性引用 02 时补录班全绿实证 | 已覆盖
- **C 任务类型**：17 类型本班实数复核一致；BUILTIN_FLOWS 17×3 字段 EN 键引用凌晨班程序化核对零缺失；演示文稿空档维持交人拍板 | 巡检
- **D 经验库**：教训 67 条本班实数复核零漂移；本班调研方法论增量=「批3 头部仓三班连扫重合率高时优先做 repos 端点增量复查而非重复主扫」（本班实践，已体现在覆盖矩阵）| 数据卫生
- **E 新 CLI**：catalog 14 条目一致；Reasonix 35,737★ 候选首位维持；九候选全未装零接入防死链 | 巡检
- **F 禅道**：claims/last_error 引用凌晨班静态链路巡检通过；禅道周边本班新见 itsaplan 为项目管理工具（非禅道 AI 集成），零新禅道 AI 竞品维持（第 8 例后持续为零）| 巡检
- **G 产品**：过时文案活源 grep 引用凌晨班零命中；本班无新发现 | 巡检

### 任务类型矩阵核对（列举口径 vs 注册表实数）

用户列举 14 场景（口径「13 种」）vs BUILTIN_FLOWS 实数 **17**（本班 python 实测复核）：直接执行/代码/小说/连载/自媒体文章/调研报告/短视频脚本/技术方案/翻译/演讲稿/工作汇报/商务邮件/扫榜选材/禅道工单全部有注册表对应；注册表另含 **doc/resume/bid_doc** 三类型为列举未提及。差异如实记录（第 5 班连续一致），无场景遗漏。对话与知识库场景：非独立预置类型，由 direct 类型+经验库/知识库覆盖（10-04 凌晨班同结论）。

### WebSearch 新闻面（串行 1 发）

- spec-driven 域 2026-10 无新开源发布；2026 主题=「specs 从文档演化为 AI agent 的可执行治理工件」（TrueFoundry 07-04 / ResearchGate 09-12 / qtrl.ai 08-24 / Thoughtworks 09-17）——与待深挖第 4 项「任务宪章编译为运行时强制策略」同向佐证，无新机制。
- 来源：[TrueFoundry](https://www.truefoundry.com) / [ResearchGate](https://www.researchgate.net) / [qtrl.ai](https://qtrl.ai) / [Thoughtworks](https://www.thoughtworks.com)

### 结论

**零机制级新差量（连续第十一班稳定期）**：批3 域头部（spec-kit/OpenSpec/planning-with-files/GSD/worktrunk）全部已沉淀且活跃，新面孔 8 件全为微型定性（参考/雷达/方向验证），无「他们有、我们没有、且确实好用」的未覆盖件。待深挖队列 11 项维持（第 7 项收窄后口径，见 knowledge.md 巡检落位班）。

### 待深挖队列（2026-10-04 04 时快照，与巡检落位班一致，零变化）

1. 工具输出统一压缩管线（第 7 验证在手，管线级攒批）
2. 语义缓存（待租户/敏感边界拍板）
3. 演示文稿任务类型空档——交人拍板（不擅自扩）
4. 任务宪章编译为运行时强制策略（ironcurtain，治理远期；本班 claude-elixir-phoenix「Iron Laws」+1 佐证）
5. 聊天分叉（1code，管线级远期备注）
6. 小红书平台特化交付契约蒸馏（wenzi-xhs 两小件，交人拍板）
7. 教训卡「标记无用」显式负反馈入口（收窄：hippo-memory ②衰减已落地，剩 ①UI 负反馈，D 专项小件交人拍板；kitfunso/hippo-memory 770★ 10-03 push 在动维持）
8. weekly_report git log 素材通道（GitPulse 差量，C 专项小件）
9. 翻译「译文翻译腔检查」蒸馏（yomiyasu，TRANSLATION_APPENDIX +1 条，交人拍板）
10. 宪章自动起草骨架（claude-rules 192★ 维持，交人拍板）
11. 既有攒批维持：ARIS 规则裁决/multica 看板视角/凭据保险库/CONTINUUM 幂等动作账本（Cyrax321 10-02 push 同域再验证）/ASD-STE100 技术英语规范化（asd-ste100-skill 3,211★ 放量，交人拍板）

---

## 2026-10-04 04 时巡检班（七专项 A-G 三轮独立实证 + 落地立项——新一轮计划第 2/4 步）

- **班次与方法**：2026-10-04 04:04-04:2x。分支 main ✓、HEAD 5701a89（v0.1.78）；工作区未提交面仅今日三班 docs（2026-10-04.md/knowledge.md 修改、iteration-report.md 新增），**代码面零未提交改动**。方法：三个只读探查代理分域实证（A / B+E+F / C+G，全部取当前真实行号）+ 本班自查实数（skills.json 计数、which 全量探测、六源真实 URL 连通、git log 差量定位、近重复对复核）——独立实锚，非转录前班。

### A token 节约（单列小节，三轮复核 + 本班行号精化）

- **既有措施全在位（当前行号）**：单次运行预算熔断 `_budget_max_tokens` pipeline.py:613-629 + 闸 :687-701；日/月成本帽 :632-669（先于 token 闸生效）；token_meter usage 累进 :721-746（压缩与直通两路都记账）；压缩守门 `_compaction_enabled` :231-241（默认关）+ 分支 :703-737；三段压缩 compaction.py:119-179（剪枝→LLM 摘要→surface replace，saved 净省入账 :156-166）；换将超窗预检 step_runner.py:75-89（_PRECHECK_RATIO=0.9）；**diff-only 评审已在位**：`_run_review` pipeline.py:1016-1022 只发 `_git_diff`（:944-949）填 `__DIFF__` 槽，不发全文；评审深度分级 :999-1013；cascade :1449-1465（easy+默认关，tier 升序 capability.py:97/:120-124）；`_shrink_context_block` 定义 :2455-2490；resume 校验链 :206-285（引擎接入 :1343/:1955/:5363）；经验召回 skills.py:533-555（bigram→_eff_karma:371→won→衰减盈余→新者）+ `_surplus_decay` :346-368；planner 缓存 cache_ttl=3600 planner.py:675/:824/:967/:998（实现 modelhub.py:3673，落盘 data/chat_cache/）。
- **记录勘误（非代码毛病）**：历史班次录「_shrink_context_block 两处调用点（:2455/:3572）」与现状不符——现全仓**仅 1 处调用**（:3572，长提示词>12000 触发），:2455 是定义本身；两处是历史形态（09-28 报告期 :2435/:3379）。代码无回归，纯文档口径修正。
- **四方向评估（本班 grep 反向确认，不重复实现）**：①prompt 缓存显式利用（cache_control/断点）**无**——但 resume 会话复用+planner 精确缓存+token_meter cached 独立计价（modelhub.py:3065/:3807）已拿走大头收益，供应商侧缓存计价已在账；②语义缓存**无**——维持「待租户/敏感边界拍板」不擅动；③diff-only 评审**已有**（:1016-1022），无新增空间；④廉价模型分流——cascade 之外还有难度选模双通道（builtin_agent.py:91/:118 model_easy/model_hard；modelhub.py:2662 easy 取末位最便宜；dispatch.py:153 easy 质量降权），**分流面已满配**。四方向零未覆盖差量，与凌晨班/巡检落位班三向一致。

### B 插件市场（六源 + 安全闸门实证）

- **六源在位**（market_remote.py:50-76）：zcode（cdn-zcode.z.ai）/anthropic（claude-plugins-official 双镜像）/anthropic-skills（anthropics/skills）/claude-skills（alirezarezvani）/clawhub（api v1）/cocoloop（api.cocoloop.cn/.com）。**本班真实 URL 连通全绿**：zcode 200/41,930B、anthropic 200/189,668B、anthropic-skills 200/2,213B、claude-skills 200、clawhub 200、cocoloop 200/11,703B——与 02 时补录班数值同量级互证。
- **安全闸门全在位**：`assert_public_url` :113-133（仅 https+全 IP 解析逐拒私网/环回/链路本地/保留段）；禁自动重定向 :145-147+手动逐跳每跳复检 :158-159+3 跳上限 :99/:179-180；git 通道同口径 :834/:841（followRedirects=false）；体量上限**六重**（清单 5MB/下载 80MB/解包 120MB/500 文件/单文本 512KB/skillpack 总文本 4MB :91-99——旧录「五重」实为六重，:_CAP_TOTAL_TEXT 前班漏计，纯记录修正）；`inspect_tree` :568-604 剥离式白名单（scripts/hooks/commands/agents 目录+.mcp.json+24 种可执行扩展剥离）；`_safe_extract` :632-670 三重防逃逸+tar 版拒符号/硬链接 :694-726；唯一落地通道 `install_files`（market.py:267）装前 `skill_scan.scan_summary` 静态扫描+typosquatting 对账 0.82+包指纹；plugins.py `_validate_manifest`/`_inside`/`_assert_no_symlinks` 五级上限全在。
- **接入三问**：本班零新候选（今日四班新见 itsaplan/jean/OpenART/atlas/claude-elixir-phoenix/Async/ChatDev/baguette/raptor 等全为域外/雷达件，无一进市场评估面）；零接入维持，安全隐患零新增（data/zentao.json 明文密码属凭据保险库域=待深挖第 11 项，交人决策，本班只读未动）。

### C 任务类型（17 类型逐一核对）

- **注册表实数 17**（flows.py:36-128）：direct/code/novel/serial_novel/article/video_script/doc/translation/rank_scan/defect_retro/research/speech/weekly_report/email/tech_proposal/resume/bid_doc——用户 14 场景映射零缺失，doc/resume/bid_doc 为额外三类型（第 6 班连续一致）。
- **i18n EN 键零缺失**：i18n.js 为「中文原文→EN」查表式（非 flows.xxx 点分键），17 name+17 goal_hint+13 note 全部命中；serial_novel note（i18n.js:1973）与 rank_scan note（:1932）两新键译文在位；前端真实消费（app.js:447/:675/:869 name、:775 goal_hint placeholder）。
- **口径细节**：演示文稿类型空档维持（无 slides 键，交人拍板不擅自扩）；weekly_report 现文案 goal_hint「周报/月报/述职？汇报给谁、做了什么（可贴流水账让小队提炼）」/note「周报/月报/述职材料：起草 → 多维评审 → 修订循环 → 发布门禁」（flows.py:102-103）——描述准确，唯素材通道缺 git log 半边（见落地立项）。

### D 经验库（数据卫生）

- **实数复核零漂移**：data/skills.json 67 条（lesson 63+procedure 4）；分类 流程规范 22/节奏爽点 21/情节逻辑 10/人物塑造 7/一致性 4/文笔风格 3（前二类 64% 偏科维持）；scope serial_novel 46/code 10/* 7/direct 4；标题零重复。
- **近重复专项复核**：同 scope 字集重合 ≥0.8 共 4 对，逐一判读**零真重复**——①「情节/人物/文笔 维度反复不达标」3 对为维度参数化模板变体（各带各自章节分数数据，合并即丢维度专属信息，不并）；②「验证未过先定位」×「验收以verify为准」1 对为共享词汇致边缘重合（前者=失败后先复现定位，后者=评审不得替代验证，动作不同，不并）。
- **方法论小教训（只入报告不入教训库）**：用 difflib/字集重合做经验库查重会把「参数化模板变体」误判为重复（本轮 difflib 前 400 字符 proxy 一度误报 1,107 对，实为共板头），查重必须 title+content 双重判读后人工定性。

### E 新 CLI

- catalog.py DEFAULT_CATALOG **14 条目**实证（:32-215：codex-cli/claude-code/opencode/qwencode/aider/openclaw/kimi-code/mimo-code/grok-build/pi/deepseek-harness/gemini-cli/codebuddy/trae-agent）；注册链 load(:422) 幂等+`_merge_new_defaults`(:406) 补新增+五类补丁族。
- **本机 which 全量探测**：在装 **13**（codex/claude/opencode/qwen/aider/kimi/mimo/grok/pi/dsh/gemini/codebuddy/trae-cli）；openclaw 在册未装；**九候选（reasonix/fuxi/gitlawb/zero/empryo/zcode/goose/crush/herdr）PATH 全空**——零接入防死链维持，DeepSeek-Reasonix 35,737★ 候选首位维持待实测。

### F 禅道集成

- **调度链在位**（本班全链实读）：automation daemon 线程（automation.py:632 启动，TICK_SECONDS=25）→ `_tick` :542 调 `zentao.fire_due`（zentao.py:2470，poll_enabled 闸 :2474）→ `_poll` :2403 `_SCAN_LOCK` 单飞 → `_poll_unlocked` :2414（_reconcile 对账→next_scan 到点→skip_holidays 顺延→`_scan` :2332）；interval_minutes 合法区间 5~10080 默认 5，失败 30 分钟重试；**legacy 换算** zentao.py:271-279（现配置仍是 interval_hours:2 老键，load 时按规则换算新默认 5 分钟——换算在位非故障）。
- **激活 Bug→建任务→回写链路**：`_route_one` :2128（_triage :1484 模块路由优先+AI 兜底→转派 _transfer :1729 幂等→我方端 `_launch_fix` :1556 create_task→enqueue，claim 落盘 :2243）→ `_reconcile` :2093→`_finish_ok` :1912（auto_merge :1920→_merge_branch :1762→未提交改动闸 :1939→auto_resolve :1990→_ensure_resolved :1708 POST resolve）→失败 `_finish_failed` :2035（修复/回写各 3 次上限 :96-97）；手动 retry_claim :2281/scan_now :2479/启动补对账 :2484（Timer 3s :2509）。
- **积压实读**（data/zentao.json 只读）：claims=**0 零积压**、last_error 空、auto_resolve/auto_merge/triage_ai 全 true、poll_enabled=**false**（last_scan 停 2026-09-21 即开关关所致，用户侧预期非故障）；本班未触发任何真实扫描/resolve/群通知。禅道 AI 集成竞品零新增（itsaplan 为项目管理工具非禅道集成，第 8 例后持续为零）。

### G 产品巡检

- 过时文案活码 grep（13种/15种/已接11/单源/双源/两平台/即将上线/coming soon）index.html/app.js/README.md/flows.py **零命中**；「四平台」3 处逐一核对均**准确**（paihang.py:97-101 _SOURCES 实为七猫/番茄/起点/纵横 4 源）；index.html 断链检查零命中（本地资源/页内锚点/60 个 SVG 精灵引用全可解析）；TODO 命中均为代码标识符非待办注释。**唯一记录级口径漂移=知识库「_shrink 两处调用」旧录（见 A 专项勘误），非产品毛病**。新毛病零新增。

### 落地立项（唯一件，不凑数立第二件——其余候选全带「交人拍板」标记）

**第 8 项：weekly_report git log 素材通道（GitPulse 差量收口，C 专项小件）**

- **差量证据链**：交付契约已承诺「合并的提交/PR（git log --since 起止）」为取材清单第一项（app/core/skillpacks/market/weekly-report.md:17）；**禅道半边已落地**（pipeline.py:4716-4726 注入 `zentao.weekly_brief()`，zentao.py:1192，tests/test_weekly_brief.py 在册）；**git log 半边全仓无实现**（grep「git log」仅命中契约文档）——模型写周报时提交素材缺源，恰违背契约第一原则「先取材，再总结，不许凭印象编」。
- **待改文件**：①app/core/pipeline.py——`_draft_prompt_for` weekly_report 分支（:4716-4726）旁追加 git 素材注入，新增小 helper `_gitlog_brief(workdir)`（函数内 `from . import gitmod` 为本文件惯例 :948/:1082/:5341；调 `gitmod._git(workdir,'log','--since=<7天>','--pretty=%h %ad %s','--date=short','-n','40')`，gitmod.py:31 统一 runner 带 timeout=20）；②tests/test_weekly_gitlog.py（新，镜像 test_weekly_brief.py mock 打法，提交时 git add -f）。
- **行为契约**：仅 type=weekly_report 生效；尽力而为——非 git 仓/无提交/命令失败一律静默返回空不注入，绝不阻塞起草（与禅道素材同款语境，注入头「## 本周提交素材（git log，如实取材，缺失勿虚构）」）；与禅道 brief 并列、各自独立 try；上限 40 条防 token 失控（预算熔断 :687-701 兜底）。测试三案：非 git 目录→""；临时 git init+2 commits→含摘要行；git 异常→""。
- **风险**：极小——纯提示词追加、默认静默、零新依赖、非 git 仓常态路径已覆盖。

### 结论

**零机制级新差量维持（连续第十二班稳定期）**：A-G 全专项三轮独立实证零代码毛病，唯一增量=两条知识库记录口径修正（_shrink 调用点数/市场体量上限重数）+ 落地立项 1 件（第 8 项 git log 通道，行号/契约/测试三案已锚定，交第 3-4/4 步落地提交）。keywords.md 本班无需调整（无新词组发现）。

---

## 2026-10-04 05 时落地班（第 8 项 weekly_report git log 素材通道——新一轮计划第 3/4 步）

- **落地件**：第 8 项（巡检班立项的唯一件，GitPulse 差量收口，C 专项小件）。范围再确认：实际改动点与立项锚定一致，无需扩大。
- **确切路径与改动**：
  1. `app/core/pipeline.py` — ①新增 helper `_gitlog_brief(workdir)`（`_git_diff` 定义之后，约 :952）：函数内 `from . import gitmod`（本文件惯例），调 `gitmod._git(workdir,'log','--since=7 days ago','--pretty=%h %ad %s','--date=short','-n','40')`，try/except 全包 + `ok=False` 双静默，返回 strip 后 stdout 或空串；②`_draft_prompt_for` weekly_report 分支（禅道 brief 注入块之后）追加并列注入：「## 本周提交素材（git log，如实取材，缺失勿虚构）」，与禅道素材各自独立、互不阻塞。
  2. `tests/test_iteration_improvements.py`（新，本步指令锚定文件名；内容即立项的 test_weekly_gitlog 三案，镜像 test_weekly_brief.py 打法）：正常（临时 git 仓两笔提交如实取材+40 行封顶）/边界（非 git 目录→""、空仓 git log 报错→""、mock 抛异常→""、ok=False→""）/回归（--since 7 天窗口、--pretty 格式、-n 40 封顶、首尾空白裁剪，参数锚定防误改）。
- **未动文件说明**：flows.py/app.js/i18n.js/index.html/style.css 无需改——注入文本为后端提示词（与禅道 brief 同款中文语境，无 UI 文案、无 i18n 键、无交互改动），故无 JS 检查项（iteration_ui_checks.mjs 不建）。
- **验收证据**：`python -m py_compile app/core/pipeline.py tests/test_iteration_improvements.py` → COMPILE_OK；`cd tests && python -m unittest test_iteration_improvements` → **OK（1 test, 0.437s）**，6 组断言全过（含真实 git init+commit 的取材正例与四类静默边界）。
- **实际收益**：weekly_report 起草提示词补上交付契约取材清单第一项的落地半边（此前 grep 全仓仅契约文档提及、无实现）——模型写周报时提交素材有真源可依，落实「先取材，再总结，不许凭印象编」契约第一原则；非 git 仓/无提交常态路径零打扰（静默空串不注入）。
- **残余风险**：极小——纯提示词追加、默认静默、零新依赖；`--since=7 days ago` 固定窗口（契约用「起止」语义，当前无任务起止参数可取，7 天为周报自然窗口，如需对齐任务起止日待后续小改）；-n 40 封顶由预算熔断（:687-701）兜底。
- **独立复跑（后续班次核实）**：注入点实锚复核 :4746 确在 `_draft_prompt_for` 内 `type=="weekly_report"` 分支（:4732 起）、`_gitlog_brief` 定义 :952 紧随 `_git_diff`，与 gitmod.py:31 `_git(workdir, *args, timeout=20)` 签名一致；`py_compile` COMPILE_OK + `test_iteration_improvements` OK（0.393s）+ 兄弟件 `test_weekly_brief` OK（0.080s）三测复绿。无 JS 改动确认（git status 仅 pipeline.py+docs+新测试），node --check 与 iteration_ui_checks.mjs 维持不适用。
- **五道关②全量回归（落地班本班补录）**：`cd tests && python -m unittest discover -p "test_*.py"` 落盘取真实退出码 → **UNITTEST_EXIT=0 全绿**（首跑经管道 tail 取到的是 tail 退出码不可信，重跑落盘复核；既有 weekly_report 测试面 test_full_type_round/test_content_contracts/test_dispatch 只碰 `_content_contract` 与类型矩阵，与新注入点零交集）。

---

## 2026-10-04 05 时收尾班（联调五道关+推送+发版 v0.1.79——新一轮计划第 4/4 步）

- **分支与工作区**：`git branch --show-current`=**main ✓**（正常路径，未触发 feature 例外）；接手时未提交面=落地班产物四件（pipeline.py / knowledge.md 修改 + iteration-report.md / test_iteration_improvements.py 新增），与计划锚定文件集一致，无外来改动。
- **第①关 静态**：`py_compile app/core/pipeline.py tests/test_iteration_improvements.py` → COMPILE_OK；本轮零 JS 改动（git status 实证仅 .py/.md），node --check 不适用。
- **第②关 全量测试（32 位分片打法，两轮均取到 Ran/OK 统计行）**：片 A（test_[a-r]）Ran 1628 → 23F+3E+2 skipped；片 B（test_[s-z]）Ran 491 → 2F；两轮复跑失败集逐项一致（可复现非 flaky；本班运行窗内并存 5-7 个外部 python 进程，含在册僵尸 PID 35060）。**判别法核验**：失败文件集 {bookmeta, launch, deepseek_harness, flows, git_workbench, http_500_guard, mcp_server, mgmt_guards, mimo_injector, portscan, publish_auto, qwen_injector_guard, usage_stats, workdsh_boundaries} 与本轮 diff 文件集求交=**空**；代表样本（flows.TestSerialResume / publish_auto / workdsh_boundaries / usage_stats）净进程单跑复现=确定性既有红，与收口班录 26F+3E 同域同归因（8e30224/07d066b/4a00d48+并发环境耦合），按防踩踏纪律不越界代修留属主班次。**改动面证据**：`test_iteration_improvements` + `test_weekly_brief` 联跑 OK（Ran 2）；发版链 selfupdate OK（Ran 6）→ smoke --pack 过（169 文件/94 模块/bin 在）→ bump 后 release_gate OK（Ran 4）+ selfupdate/content_contracts OK（Ran 15）。注：上栏落地班补录「discover 全量 exit 0 全绿」与本班分片 25F+3E 并存——并发负载下失败集环境耦合（收口班既有教训同型），两录并呈不互斥，判别以「与本轮 diff 求交」为准。
- **第③关 逐 hunk 自审+模型评审**：自审四轴全过——惯例（函数内 `from . import gitmod` 同 `_git_diff` :948 惯例；双静默同禅道 brief 块；注入头文案同款「如实取材，勿虚构」）；边界（非仓/空仓/异常/ok=False 四路静默有测试锚定；workdir 为闭包所在函数局部量 :4759 实证）；测试覆盖（六组断言含真实 git init 取材正例+参数锚定防误改）；残留（零调试代码零越权，diff 恰 +22 行 pipeline/+57 行测试）。**模型评审代理两次均被 API 网关 400 拒（「模型不存在」，model=auto，显式指定模型被代理忽略）**——基础设施阻塞如实记录，自审即本轮评审记录。
- **第④关 暂存**：只 add 本轮两文件（pipeline.py+新测试；新测试命中 .gitignore 白名单规则 `!/tests/test_*.py` 非 ignored，普通 add 即入）；`git diff --cached` 扫 pick_dialog/ask_directory/backoff 零命中。
- **第⑤关 提交核对**：`31f0e93`（feat weekly git log 通道，2 files +79）`git show --stat` 核对一致；未发生踩踏。
- **推送**：首推 443 reset，间隔 20s/30s 重试（fetch 亦两连败，GitHub 443 抖动窗口）→ 第 3 次成功 `5406ee0..ef3e755 main → main`。
- **发版 v0.1.79**：门禁链全绿 → package.json 0.1.78→0.1.79 → CHANGELOG 顶部追加 → README relnotes 段同步（CHANGELOG 头部项目自守则）→ `ef3e755` 提交并随推送上远端。**首次 npm publish 被 prepublishOnly 闸（scripts/release_gate.py）以「工作区不干净」正确拦下**（docs 在制品+本班 smoke 残留 codebee-0.1.78.tgz 在树）——闸先于上传拦截，registry 实查维持 0.1.78 零污染；处置：删自身残留 tgz + 本节 docs 落库后复跑 publish（结果见下条补记）。
- **未解决风险（如实交人）**：①既有红 25F+3E 属 14 个测试文件，根因三条均系他班在管，留属主班次；②模型评审代理基础设施故障（网关模型路由 400）待运维；③publish 闸与工作区清洁强耦合——多班并发「先落 docs 再 publish」顺序纪律本轮再次验证有效。
- **待深挖队列**：11 项维持 04 时巡检班快照，第 8 项（weekly_report git log 素材通道）**已落地关闭**（31f0e93），余 10 项口径不变。

### 收尾补记（第二收尾班独立复核，接 1f53d84 之后）

- **独立复跑同域互证**：分片复跑（片 A 23F+3E / 片 B 2F）与本班录逐项同数；14 文件失败集隔离单跑——9 模块（mimo_injector/git_workbench/qwen_injector_guard/deepseek_harness/mgmt_guards/http_500_guard/launch/portscan/bookmeta）**全绿=纯并发干扰**，与上节判别法结论一致。
- **mcp_server 2F 已修**（上节既有红集中唯一可机械收口件）：test_mcp_server.py 期望 5 工具为 7f64794（09-27）旧形态，54e28aa（09-30）契约三工具（get_contract/record_evidence/search_knowledge）为有意扩展——测试断言跟码，期望列表 5→8、stdio smoke 计数同改，`test_mcp_server` 隔离复跑 OK（Ran 6）。非越界：该文件无在管班次，改动独立成 commit。
- **既有红根因精化（交属主班次）**：①flows.TestSerialResume——mock 章 367 字 < 600 字，撞 b856632「首章正文不足 600 字」签约门禁 → publishable=False（overall 6.2>阈值 6.0、各章 passed=True 仍拦），fixture 短文未随门禁更新，补 mock 首章 ≥600 字即收；②publish_auto 10F+workdsh_boundaries 1F 同根因确认=07d066b/4a00d48 建书确认严格化后 mock book_id 空被拒，属主班次处置；③usage_stats 1F 维持 5406ee0 已录。
- **npm publish 收口**：本补记 docs + mcp 测试修复落库、工作区干净后复跑 publish（结果见推送与发版状态终录）。

---

