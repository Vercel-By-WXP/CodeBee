# 2026-10-05 18 时班·全类型调研过程审计（full-type-audit，新一轮计划第 1/4 步）

> 本文件为本轮（18 时班）过程记录：查询批次、来源通道、14 类型覆盖表、失败/限流项、
> 可追溯差异清单。未访问的来源如实标「未访问」，不冒充完成。

## 0. 执行环境实录

- 开工 2026-10-05 18:37（UTC+8），收口 19:1x。
- 分支 **main**（HEAD 86d6f21，v0.1.86 已发版），工作区开工时干净，无并行写手在场。
- B 批次选择：18 % 7 = **4** → 批4（治理/安全/人机协同）。
- 通道前提：gh api 认证可用（gho_ keyring），搜索接口按认证口径串行 sleep 4s。

## 1. 查询批次（主扫 `scripts/borrow_scan_nightly.py` 无参全量模式）

| 批次 | 组 | 查询数 | 行数 | 结果 |
|---|---|---|---|---|
| A 常驻 | A1 核心编排 8 / A2 扩展形态 7 / A3 token 节约 4 / A4 新 CLI 3 / A5 舰队形态 4 / A6 评测观测协作 6 / A7 写作场景 8 / A8 prompt 网关质量 5 / A9 产品配套 6 / A10 全类型写作对话代码 10 / A11 项目记忆 5 / A12 发布平台 5 / A13 面板用量守卫 7 | 78 | 343 | 全 ok |
| A 常驻内置 | B1 代码质量评审 11（QUERIES 内置份） | 11 | 51 | 全 ok |
| B 轮换 | B4 治理/安全/人机协同 10（human-in-the-loop / approval workflow / governance / guardrails / permission policy / audit trail / kill switch / risk control / prompt injection defense / policy evaluation） | 10 | 50 | 全 ok |
| 双轮排序 | A1u（A1 全 8 组 sort=updated 新锐轮）+ A1p2（A1 全 8 组 stars page=2 翻页） | 16 | 80 | 全 ok |
| **合计** | | **115** | **524** | **469 唯一仓，零失败零限流** |

- 双轮排序纪律：stars 轮为主（全组），updated 新锐轮与 page=2 翻页按惯例挂 A1 核心组；
  结果高度重合组（A1p2 与 A1 重合 40/40 由汇总端按 full_name 去重）未再翻 page=3。
- 原始扫描 JSONL：`%TEMP%\borrow_scan_18h.jsonl`（524 行，脚本不写仓库内文件）。

## 2. 来源通道实录（雷达源 C）

| 通道 | 本班状态 | 说明 |
|---|---|---|
| gh api search/repositories（主扫） | ✅ 完成 | 115 查询见上，零 403 零 FAIL |
| gh api repos 端点（存量复查） | ✅ 完成 | 31 仓（30 alive + 1 裸名 404 勘误），串行 sleep 2s，core 配额池 |
| gh api users/<user>/repos（组织矩阵顺藤） | ✅ 完成 | PerryLink（user 型属主，users/ 端点第 2 例）→ DSH 插件生态 48 件（非 fork 且 dsh- 前缀，per_page=100 两页 28+20 实测；初记 15 件系首页口径不可追溯，独立评审后勘误） |
| topic 页 8 个 | ✅ 完成 | multi-agent-orchestration / ai-agents / claude-code / agent-framework / claude-skills / llm-agents / ai-coding-assistant / mcp，sort=updated 各 5 件 |
| npm search 两查 | ✅ 完成 | "agent orchestrator"（10 件）+ "claude code manager"（8 件）——已录族/微型，零接入级 |
| awesome 清单新鲜度 | ✅ 完成 | 9 源 repos 实测全 alive 零 archived（VoltAgent 35,222 / awesome-mcp-servers 95,833 / awesome-claude-code 55,088 / harness-engineering 4,707 / Long-Horizon 1,062 停更观察 / Agent-Memory 658 / TsinghuaC3I 665 / awesome-claude-agents 4,389 / awesome-llm-apps 140,744） |
| WebSearch 串行交叉验证 | ✅ 完成 | 1 查（治理域——本班单通道「零新竞品」结论性判定按 keywords.md 规则配额）：捞出名全为已录框架族（LangGraph/CrewAI/AutoGen/ADK/OpenAI Agents SDK/Dify/Mastra/OpenClaw），零新仓 claim，双通道互证 |
| trendshift.io（Trending 替身） | ❌ 被拦 | WebFetch 域名校验拦截（10 时班可达、12/16/18 时班三连拦）——以 topic 页 sort=updated 新锐视角补位，如实记不冒充 |
| GitHub Trending 直抓 | ❌ 未访问 | 历班直抓被拦在案，替身 trendshift 本班亦被拦——本轮 Trending 面缺位如实记录 |
| pypi search | ❌ 未访问 | 历班六种拦截形态在案，按纪律省配额未复试 |

## 3. 全类型覆盖表（用户列举 14 场景 vs 注册表实数）

注册表实数（本班代码复核 `app/core/flows.py` BUILTIN_FLOWS）：**18 类型**
direct / code / novel / serial_novel / article / video_script / doc / translation /
rank_scan / defect_retro / research / speech / presentation / weekly_report / email /
tech_proposal / resume / bid_doc。

| 用户点名场景 | 注册类型 | 本班扫描覆盖来源（词组级） | 覆盖 |
|---|---|---|---|
| 直接执行 | direct | A1×8 + A2 autonomous/workflow 组 | ✅ |
| 代码 | code | A10 code gen + code refactor 组 + B1 评审 11 组（内置份+批1 同词表） | ✅ |
| 小说 | novel | A7 novel/story + creative writing + long form + 中文网文 4 组 | ✅ |
| 连载 | serial_novel | A7 consistency 组 + A12 chapter hook/serial pacing 组 | ✅ |
| 自媒体文章 | article | A7 article/blog writing 组 | ✅ |
| 调研报告 | research | A10 deep research 组（批5 轮换词组本班未轮到，域由 A10 覆盖） | ✅ |
| 短视频脚本 | video_script | A10 short video script 组 | ✅ |
| 技术方案 | tech_proposal | A5 spec-driven 组 + A7 long form 组 | ✅ |
| 翻译 | translation | A7 translation workflow 组 + A10 translation quality 组（双组） | ✅ |
| 演讲稿 | speech | A7 speech/presentation script 组 + A10 slides 组（双组） | ✅ |
| 工作汇报 | weekly_report | A10 weekly report 组 | ✅ |
| 商务邮件 | email | A10 email writing 组 | ✅ |
| 扫榜选材 | rank_scan | A9 webnovel author tools/小说作者工具 组（拆解爆款域） | ✅ |
| 禅道工单 | defect_retro | A13 defect retrospective 组 + 雷达禅道周边（easysoft/zentao-cli 61★ 无增量） | ✅ |

额外关注面（非用户列举但属全类型雷达）：对话记忆=A10 chatbot memory 组 ✅ /
知识库=A11 spec archive 组（批5 域词组未轮到）✅ / 文档生成=A10 doc gen 组 ✅ /
演示文稿=A10 slides 组（presentation 已落地，本轮复核在册）✅。

**结论：14 场景全映射 + 4 额外类型，本轮扫描面无类型缺覆盖。**

## 4. 失败 / 限流项（如实记录）

- 主扫 115 查询：**零失败零限流**（进度文件 0 条 FAIL、0 条 403 退避）。
- repos 端点：1 次 404（裸名 `open-code-review`——短名非正主，全名 alibaba/open-code-review 复查成功；已入全名对照表口径）。
- trendshift / GitHub Trending / pypi 三通道未完成（见 §2 表，缺位如实记录，以 topic/npm/WebSearch 补位）。
- WebSearch：1 发即过，零 429。

## 5. 可追溯差异清单（新面孔 → 证据 → 裁决）

| 仓 | 证据（本班实测） | 裁决 |
|---|---|---|
| yetone/magpie | 5,007★ / 10-05 push / topic:claude-code updated 第 4 位 | 网关族雷达（不接） |
| desplega-ai/agent-swarm | 857★ / 10-05 push / topic:multi-agent-orchestration 第 3 位 | A1 雷达 |
| kdlbs/kandev | 899★ / 10-05 push / 主扫 A13 组 | 面板族雷达 |
| ZaxbyHub/opencode-swarm | 486★ / 10-05 push / topic:mcp | A1 生态雷达 |
| PerryLink/dsh-research-report | 215★ / 10-05 push / users/PerryLink 顺藤 | 雷达（调研报告域机制备注） |
| PerryLink/dsh-permission-rules | 118★ / 10-05 push / 同上 | 雷达（批4 域） |
| PerryLink/dsh-* 群 48 件（非 fork dsh- 前缀两页 28+20 实测；初记 15 件系首页口径不可追溯，已勘误） | users/PerryLink/repos 45/48 件 10-05 当日 push（dsh-kit/dsh-laya/dsh-plugin-upgrade-016 为 09 月） | E 域自家 CLI 周边雷达，keywords.md 补词 |
| edwinkys/phantasm | 196★ / 2024-11 停更 | 拒收（停更） |
| CosmosYi/AutoControl-Arena | 108★ / ICML 学术 | 拒收（学术微型） |
| ESAA-Security | 202★ / 微型审计架构 | 拒收（量级不足） |
| gemini-ai-code-reviewer / MoaKK / MatterAI | 252/103/54★，两件停更 | 拒收（B1 族微型零差量） |
| matank001/cursor-security-rules | 380★ / 2025-08 停更 | 拒收（停更；knowledge.md 拒收明细同录） |
| alphaparkinc/genpark semantic-cache-manager | 9★ 微型 | 拒收（队列第 2 项攒批微证） |
| dsh-autotier | 1★ 微型 | 拒收（cascade 同向已满配） |
| npm 面 5 件（bdb/tide-commander/crow/garda/coleo） | npm search 实查 | 拒收（已录族/微型零接入级） |

## 6. 结论

批4 域（治理/安全/人机协同）**零机制级新差量**——15 时班 gastown 破稳后第 2 班回稳；
新面孔 7 件入库全雷达/参考级，零接入级标的。存量 30 仓全 alive 零 archived。
keywords.md 本班仅补 1 处确凿缺口（自家 CLI 周边搜缺 deepseek-harness 系词），
零其他调整。本步为纯调研步，零 app/ 实现改动、零发版对象。

---

# 七专项巡检（新一轮计划第 2/4 步，2026-10-05 19:0x-19:1x）

> 承接上节调研证据，逐专项实读代码核对现状。所有路径/函数均本轮实读；
> 运行态取自 data/ 实测；找不到/无权限的如实标「未验证」，不假定存在。

## A. token 节约（单列小节）

### 已有机制逐项对照（路径 + 函数，全部实读）

| 机制 | 位置 | 现状 |
|---|---|---|
| 三段压缩 | `app/core/compaction.py`：`prune_text`:42 / `select_range`:51 / `compact_region`:119 / `maybe_compact`:182 | 工具结果剪枝 → LLM 摘要 → surface replace 三段式在役 |
| token 计量 | `app/core/token_meter.py`：`accumulate`:81 / `used`:101 / `cached`:109 / `pressure_ratio`:116 / `capacity`:124 | 按 run 计量，含缓存读与压力比 |
| 预算熔断 | `app/core/pipeline.py`：`_ensure_budget`:61 / `_budget_max_tokens`:613 / `_budget_cost_caps`:632；spawn 前检查 :687-696 | 单 run token 上限 + 日/月花费硬顶（¥），超额停后续步骤；`settings_schema.py`:243-261 默认 0=不限 |
| cascade 廉价模型分流 | `app/core/capability.py`：`cascade_reorder`:100 / `pick_by_strength`:60；调用点 `pipeline.py`:1526-1529 | FrugalGPT 式 tier 升序重排；`settings_schema.py`:267 `cascade.enabled` **默认 False（opt-in）** |
| 会话复用 | `pipeline.py`：`impl_sid` 记录 :1560（fix 轮复用实现会话）、`critic_sids` :3559（评审第 2 轮复用会话）、`_valid_resume` :206（resume_ctx 跨 run 续用参数） | 会话内前缀走缓存读计价（§07 T1.1 注记在源） |
| 前缀稳定（间接 prompt 缓存） | `pipeline.py`:2436（提示词前缀字节稳定）、:3113（story-bible 全 run 字节稳定）、:3595/:3141（stable_order 技能块字节级一致，§07 T1.2'） | 靠字节稳定让供应商前缀缓存命中，无显式 API 标记 |
| 分层降级 | `pipeline.py`：`_shrink_context_block`:2538 / `_serial_shrunk_block`:2581 | 四层优先级：圣经 > 模块库 > 经验库（→4K 硬截）> 大纲/前情，按模块/二级标题边界裁 |
| 经验召回 | `app/core/skills.py`：`block_for` / `relevance_top`:540（stable_order+run_id hits 计数）；`app/core/knowledge.py`：`block_for`:277 | 召回块按任务域注入，字节稳定不碎前缀 |
| 评审文本预算 | `pipeline.py`：`_full_manuscript`:2992 | 本批全文保底在场，前文按剩余预算保尾部截取（cap=60000） |
| diff 截断/兜底评审 | `pipeline.py`:3002-3010（diff 预算截断）、:921（读不到文件时基于 diff 评审） | diff-only 是兜底路径而非主路径 |

### 对照调研方向逐项结论

- **供应商侧 prompt 显式缓存（cache_control 类）**：全 app/core grep `cache_control|prompt_cach|semantic_cach` **零命中**——唯一真实差量。现状靠前缀字节稳定间接受益缓存读计价。**处置：本步禁令「不额外扩建缓存」→ 记入落地清单候选交人工决策，本轮零改动**。
- **语义缓存**：无，不建（同上约束）；调研面 alphaparkinc/genpark semantic-cache-manager 9★ 已在拒收明细，无接入级标的。
- **diff-only 评审**：部分在役（兜底路径 + 截断预算）。全量改 diff-only 会伤评审读全文质量，属产品取舍，**维持现状**。
- **廉价模型分流**：已有（cascade），默认关属刻意 opt-in（需供应商声明 tier），**维持**。

**结论：机制面 10 项在档无缺位，无重复建设空间；差量仅供应商侧显式缓存标记 1 项，转人工决策。**

## B. 插件市场（六源 + 外部 skill 生态）

现状实读：`app/core/market.py`（内置库 `install`:248 / `install_files`:267 / `_similar_names`:130 重名 0.82 相似度闸 / `skill_card`:369 / `remove`:409）+ `app/core/market_remote.py` **六源** `SOURCES`:50-76（zcode / anthropic / anthropic-skills / claude-skills / clawhub / cocoloop）+ 闸门三件套：`assert_public_url`:113（SSRF：仅 https、主机必填、端口校验、重定向逐跳复检 `_MAX_REDIRECTS`=3）+ 防炸弹 caps :91-98（manifest 5MB / 包 80MB / 解包 120MB / 500 文件 / 单文本 512KB / 文本总量 4MB）+ `inspect_tree`:568 纯技能白名单安装闸。**既有通道完好，本班零改动闸门。**

候选三问逐个回答（均来自第 1/4 步差异清单 7 件新面孔）：

| 候选 | 重合度 | 可直读性 | 用户会搜吗 | 处置 |
|---|---|---|---|---|
| PerryLink/dsh-research-report（证据账本） | 中——research 流程有缺口驱动补查，无引用核验留痕 | 否（DSH 插件形态非 skillpack） | 弱 | **只跟踪**，机制随批5 citation-verification 词组域攒批 |
| yetone/magpie（网关族） | 高（网关路线已裁定不接） | — | — | 雷达 |
| desplega-ai/agent-swarm、kdlbs/kandev、ZaxbyHub/opencode-swarm | 高（编排/面板形态已有） | — | — | 雷达 |
| PerryLink/dsh-\* 群 48 件 | 高（形态各异但均 DSH 插件） | 否 | — | E 域周边雷达 |

方法论沉淀走 D 项经验库通道（「插件市场源接入判断三问」已在库，`data/skills.json` scope=`*`，本轮不重复入库——**D 项去重实测生效**）。
**结论：零安装动作；蒸馏内容以雷达/备注沉淀，白名单与 SSRF 闸未动。**

## C. 任务类型（全部预置类型核对）

注册表实数 **18**（`app/core/flows.py` `BUILTIN_FLOWS`:36-134 逐条实读）：direct / code / novel / serial_novel / article / video_script / doc / translation / rank_scan / defect_retro / research / speech / presentation / weekly_report / email / tech_proposal / resume / bid_doc。engine 分布：direct×3（direct/rank_scan/defect_retro）+ code×1 + review×14；review 类 threshold 7.0（bid_doc 7.5）、rounds 均 2；每类均有 name/goal_hint/note/rubric（4-5 维闭集）与产出文件名。

**13/14 计数差异解释**：任务总则点名 14 场景（含「禅道工单」）；本步指令写「13 种」为旧计数；代码实数 18。对齐口径——14 场景与注册类型**一一映射**（直接执行→direct、代码→code、小说→novel、连载→serial_novel、自媒体文章→article、短视频脚本→video_script、调研报告→research、技术方案→tech_proposal、翻译→translation、演讲稿→speech、工作汇报→weekly_report、商务邮件→email、扫榜选材→rank_scan、**禅道工单→defect_retro**）；再加注册表独有的 文档 / 演示文稿 / 简历 / 标书编制 4 种 = **18**。另注：禅道**集成**（`app/core/zentao.py`，README:242）把激活 Bug 自动转成 code 修复任务，是集成入口不另占菜单类型——与 defect_retro（吃禅道/Jira CSV 的复盘类型）是两条链路，勿混。

描述与实现一致性抽查：
- rank_scan note「四平台」= `app/core/paihang.py` 四源实现（七猫/番茄/起点/纵横，`fetch_rank_items`:105 跨源去重、全败才空）——**一致**（双源→四源升级文案已同步）；
- i18n.js 六源名（:1963-1968）与 18 类型名/goal_hint/note 英译在位（:1925-1992 行段）；
- defect_retro goal_hint 引导附禅道/Jira CSV，与 `defectretro.py` 消费口径一致。

**缺口：本轮核对零失配、零随手修对象。**

## D. 经验库（去重/合并/分类）

现状实读：`app/core/skills.py` `upsert_lesson`:424（去重=`_title_containment`:412 字符 bigram 包含度，与 `knowledge._title_sim` 同款口径；id=scope+标题哈希，老教训再沉淀**合并不分裂**）；闭集分类 `_normalize_category`:75（精确→category 关键词→dim 关键词→未分类）；`_karma`:326/`evolve_lessons`:565 正负反馈演化；召回 `relevance_top`:540/`block_for`；合并字段封顶 8 条 :467。

实测分布（`data/skills.json`，本班 70 条）：分类 流程规范 25（36%）/ 节奏爽点 21（30%）/ 情节逻辑 10（14%）/ 人物塑造 7（10%）/ 一致性 4（6%）/ 文笔风格 3（4%）；scope serial_novel 46（**66%**）/ code 10（14%）/ `\*` 10 / direct 4。任务文本所记「流程规范 61%」为双重旧值——knowledge.md:1831（2026-10-04）已勘误过一次（当时 67 条 33%），本班 70 条 36% 复核维持「无病态偏科」结论；真实偏科在 **scope（serial_novel 66%、code 仅 14%）**。

处置：按现有通道（`upsert_lesson`）入库 code 域方法论 1 条（见落地清单①），稀释 scope 偏科；「市场接入三问」已在库（scope=`*`）故**不重复入库**——去重纪律实测生效。分类器不改（避免过度工程，偏科靠持续入库运营性纠偏）。

## E. 新 CLI 接入

`app/core/catalog.py` `DEFAULT_CATALOG`:32 实数 **14 个**（codex-cli、claude-code、opencode、qwencode、aider、openclaw、kimi-code、mimo-code、grok-build、pi、deepseek-harness、gemini-cli、codebuddy、trae-agent；installed×2 + installable×12）——任务文本「已接 11 个」为旧计数。

本机 which 实测（2026-10-05，**按 catalog 探测名**：codex/claude/opencode/qwen/aider/openclaw/kimi/mimo/grok/pi/dsh/gemini/cbc/trae-cli，见 `catalog.py` 各条 `detect.cli`）：在机 **13** 个（codex/claude/opencode/qwen/aider/kimi/mimo/grok/pi/dsh/gemini/cbc/trae-cli），仅 **openclaw** 未命中。勘误：本节初稿按**包名**（kimi-code/deepseek-harness/trae）探测误记「在机 10 个、kimi-code/deepseek-harness/trae 待装」——探测名与包名不同（kimi=@moonshot-ai/kimi-code、dsh=@deepseek-ai/dsh、trae-cli），knowledge.md:1832 旧诫在案，独立评审后按探测名重测勘误；trae-cli 在机但运行态本班未测。

候选处置：DeepSeek-Reasonix（35,730★，复查候选首位）/ FuXi / Gitlawb / zero / Empryo 本机 which（deepseek-reasonix/fuxi/gitlawb/zero/empryo）**无一命中** → 维持「本机未装不盲目录接入」纪律（防死链）；DeepSeek-Reasonix 待装机实测后按 knowledge.md 接入打法走。deepseek-harness（dsh）本机已在（09-30 装），其插件生态词已补进 keywords.md 自家 CLI 周边搜。

## F. 禅道集成巡检

链路实读（`app/core/zentao.py`，2,692 行）：定时 `_poll`:2403/`_poll_unlocked`:2414（interval_minutes 节流，老 interval_hours 自动迁移 :269-279）、单飞 `_SCAN_LOCK`:80、`fire_due`:2470/`scan_now`:2479/`start`:2496（boot 3s `_boot_reconcile`:2484）；扫描 `_scan`:2332 → `_route_one`:2128（模块路由 `_triage`:1484/`_ai_triage`:1386 → `_launch_fix`:1556 自动建 code 修复任务）→ `_finish_ok`:1912（合并 `_merge_branch`:1762 + resolve `_ensure_resolved`:1708 + 评论回写 + 群通知 `_notify`:1754）；对账 `_reconcile`:2093/`_archive_sweep`:1842；产品档案 `_profiles`:307（`module_routes` 路由字段 :137）。设置页禅道子页在位（`app/ui/index.html`:250/:773-811），文案与实现一致（多产品/module_routes/自动 resolve/测试指错人改派）。

**运行态实测（data/zentao.json）**：`poll_enabled=False`、profiles=0、claims=0，`last_scan=2026-09-21 20:43:44`（14 天前），`last_error` 空。

如实标记：**激活 Bug 积压、产品档案路由准确性、resolve 回写链路 = 本机未验证**——本机无禅道实例档案（无访问权限），按纪律不为验证触发真实工单、不代填配置。结论口径：**代码链路与 UI 完好，本机定时扫描处于未启用状态，属部署配置缺位而非代码缺陷**。
风险在档（交人工决策，不新增）：data/zentao.json 明文密码（knowledge.md 待深挖队列已记）；是否在本机配产品档案启用定时扫描 → 人拍板。

## G. 产品巡检（UI 文案/链接/流程一致性）

| 抽查点 | 结果 |
|---|---|
| 扫榜文案「四平台」（flows.py:82） | 与 paihang.py 四源实现一致，历史升级已同步 |
| 市场六源名 i18n（i18n.js:1963-1968） | 六源全有英译；帮助中心「ZCode、Anthropic 等多个外部目录」泛化表述不过期 |
| 类型计数（README:135「18 种」） | 与 BUILTIN_FLOWS 实数一致；README:242 禅道工单口径准确 |
| 残留扫描（index.html/app.js/i18n.js grep「单源/双源/13 种/14 种」） | 零命中；类型菜单由 flows 数据动态渲染，无硬编码计数 |
| 禅道子页（index.html:773-811） | 文案与 zentao.py 实现一致 |

**结论：本轮抽查零过时文案、零断链，无随手修对象（如实记录，不硬造改动）。**

## 落地清单（小而实，可直接执行，无重复建设）

1. **【本轮已执行】D 项方法论入库 ×1**：`skills.upsert_lesson(scope='code', category='流程规范', source='borrow-log/full-type-audit 2026-10-05')`——「禅道巡检运行态三看：data/zentao.json 的 poll_enabled/profiles/last_scan」。行为变化：code 域任务经 `skills.block_for` 自动召回该教训；稀释 scope 偏科（serial_novel 66%→65%）。验收：`skills.list_lessons(scope='code')` 可查到（lesson id `sk-f9aa2d8d1043`，hits 计数在位），标题与既有 70 条无重复（插入前已查重）。「市场接入三问」已在库故未重复入库。改动仅落 `data/skills.json`（gitignored 运行态），零 app/ 源码改动。
2. **【候选·转人工决策】A 项唯一真实差量**：供应商侧显式 prompt cache_control 标记（现状仅前缀字节稳定间接受益）。受本步「不额外扩建缓存/抽象层」禁令约束不动 app/；量级涉及 modelhub/upstream 各供应商协议差异，是否立项由人拍板，在此之前雷达跟踪（调研面同域标的均为微型/停更，无现成可抄实现）。

## 本步小结

七项均有实证与结论：A 机制 10 项在档/差量 1 项转人工；B 六源+三闸完好/零安装；C 18 类型零失配/计数差异已解释（14 点名一一映射 14 注册类型〔含禅道工单→defect_retro〕+4 注册表独有=18；禅道集成转 code 不占菜单）；D 去重合并实读+分布实测+入库 1 条；E catalog 14 vs 本机在机 13（按探测名实测，初稿按包名误记 10 已勘误）/候选全未装不盲接；F 链路完好但本机未启用、积压回写如实标未验证；G 零过时文案。落地清单 1 条已执行验收、1 条转人工决策，无重复建设、零 app/ 源码改动。

---

# 第 3/4 步落地实录（2026-10-05 19:2x-19:4x）

> 承接上节落地清单逐项实施。先读后改纪律全程执行（flows.py 全文、pipeline.py 锚点段、
> 既有契约测试 7 个契约面 8 件全读）；无并行写手在场，工作区仅含第 1/2 步的 docs 改动。

## 落地范围裁定（如实记录：本轮零 app/ 源码改动）

1. **D 项方法论入库（清单①）**：上一步已执行，本步只读复核——`data/skills.json`
   实查 lesson id `sk-f9aa2d8d1043`（「禅道巡检运行态三看：data/zentao.json 的
   poll_enabled/profiles/last_scan」，scope=code）在位，零补做。
2. **供应商侧 prompt cache_control（清单②）**：维持「转人工决策」原裁定，本步不实施：
   - 上一步明文「是否立项由人拍板，在此之前雷达跟踪」，本步指令未推翻该裁定；
   - 实施面在 modelhub/upstream（各供应商协议差异），不在本步文件上限
     （pipeline/flows/app.js/i18n/index.html/style.css）之内；
   - 量级不属「小而实」，调研面同域标的均微型/停更，无可抄实现。
3. 其余五专项（B 市场/E 新 CLI/F 禅道/G 产品）上一步均结论「零改动对象」，本步
   不另造改动（反顺手加固：风险已在档，由人拍板）。

## 契约回归取证（tests/test_full_type_audit.py 未新建的裁定 + 实跑结果）

本步指令要求新建 `tests/test_full_type_audit.py` 覆盖「实际后端改进」——本步实际
后端改进为零，且「全部预置类型基础契约回归」在既有套件已逐面在守，新建只会复制
既有断言（指令同段自带禁令：「不编写无意义的 Python 测试」）。逐面对照 + 实跑
（2026-10-05，8 件 56 用例全绿零失败）：

| 契约面 | 既有用例 | 结果 |
|---|---|---|
| 18 类型注册/解析/三展示字段/review 默认参数 + 14 场景映射零缺失 + 翻译腔自查 + 蒸馏入库去重合并 | test_full_type_round.py（5） | ✅ |
| presentation 全字段精确值 + dispatch 维度 + 编译规格 + 标准评审短链 + 交付契约 + 图标 + i18n + 市场文案去数字化 | test_full_type_iteration.py（8） | ✅ |
| 图标精灵表定义 + 唯一性 | test_flow_icons.py（2） | ✅ |
| digest 语义变化/noop 不产噪/恢复回路/按修订战绩对账 | test_flow_versions.py（8） | ✅ |
| flow_snapshot 钉版 + flow_drift 漂移 + 展示字段免疫（note/name 改措辞零漂移） | test_revision_binding_ledger.py（18） | ✅ |
| 全类型交付契约（非连载 review 类型缺契约即红） | test_content_contracts.py（9） | ✅ |
| 流程字段 EN 键守卫 + bid_doc 7.5 门槛 | test_i18n_dups.py（3）+ test_bid_flow.py（3） | ✅ |

改动文件为零 → py_compile / node --check 门无对象空转（未改动任何 py/js）。

## 未处理风险（交人拍板，口径与上一步一致）

- 清单② cache_control：待拍板（见上）；拍板后实施面含 modelhub/upstream，须另立
  文件上限再动。
- data/zentao.json 明文密码 / portscan 杀进程用例挂死 / 32 位全量 discover 静默
  退出：待深挖队列与风险在档维持，本步不动。

## 本步小结

清单①复核在位、②维持人工决策闸；零 app/ diff、零新建测试文件（既有 56 用例
全类型契约实跑全绿取证）；本节即第 3/4 步全部产出，交第 4/4 步提交推送。
