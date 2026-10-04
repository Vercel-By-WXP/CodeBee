# 2026-10-04 全类型轮·七专项巡检（计划第 2/4 步）

> 开工 09:43、分支 main（98445b4，v0.1.79 已发版）、工作区仅本步两文件在制（上步调研已入库）。
> 本步为巡检+提案步：A-G 全实证落本报告；落地提案与经验库蒸馏条目只记录不执行（纳入第 3 步确认清单）。零代码改动。

## A. token 节约（真实实现逐项实锚）

**检查方法**：grep + 逐段 Read pipeline.py / compaction.py / modelhub.py / token_meter.py / skills.py，以下行号全部本班实核。

| 机制 | 真实实现（文件:行） | 结果 |
|---|---|---|
| 三段压缩 | compaction.py `estimate_tokens/prune_text/select_range(:51)/compact_region(:119)/maybe_compact(:182)`；pipeline.py `_compaction_enabled(:231)`（默认关，settings v2 `orchestrator.compaction.enabled`）、`_spawn_step(:672)` 灰度分支 :703-744「撑爆→压缩→守门重试」 | 在位 |
| token_meter | token_meter.py；pipeline.py :690-696 `used()` 预算表、:723/:742 `accumulate()` 双路径累进（压缩路径+直通路径都有，:738 注释防直通漏记） | 在位 |
| 预算熔断 | `_budget_max_tokens(:627)`、`_budget_cost_caps(:632)`（日/月成本双封顶）、超预算停后续步骤 :687-696 | 在位 |
| cascade | :1471-1478 `capability.cascade_reorder`（cascade.enabled 开关，difficulty 传参） | 在位 |
| 经验召回 | skills.py 注入通道（scope 匹配 + `relevance_top(:533)` karma 排序 + 使用反馈闭环 :546 + karma 时间衰减 :348）；知识库检索 retrieval.py `search(:116)`（story_tracking.upsert :212 回写、mcp_server :186 暴露） | 在位（双通道） |
| 会话复用 | pipeline.py `_validate_resume(:207)`、resume 传递 :1405/:1456/:1682/:3611/:3961；sessions.py `scan(:298)` 扫 codex/claude/qwen/opencode/mimo 五源本机会话（qwen :243 文件名即 session id 直 resume） | 在位 |
| diff 评审 | `_git_diff(:944)`（diff HEAD+未跟踪新文件）、`_review_depth_note(:1015)` diff 规模分级、:1033-1038 diff 注入评审 prompt、:956 `-n 40` 封顶 | 在位（diff-only 已半实现：评审输入即 diff+shortstat） |
| _shrink_context_block | :2471 定义（四层优先级：圣经>模块库>经验库>大纲/前情，按边界保序截断）；消费点 :3600（连载起草路径） | 在位（仅连载路径挂接） |

**对照调研四方向评估（不重复建设）**：
- **prompt 缓存 / 语义缓存**：grep `prompt_cache|semantic_cache|cache_control` 全库零命中——确实未建。结论维持待深挖队列第 2 项口径：语义缓存卡租户/敏感边界拍板，prompt 缓存依赖上游 API（direct 引擎直连模型 API 才有收益，CLI 引擎侧由各家 CLI 自管）——**不擅自新建**。
- **diff-only 评审**：已有 _git_diff+深度分级+40 行封顶，与调研对象（OpenAI DevDay codex review）机制重合，评审属编排层与 CLI 自带层并行，无机制差量，不动。
- **廉价模型分流**：已有完整链 `classify_difficulty(modelhub:3536)` → `bind_agent(difficulty)(:2217)` → cascade_reorder → code_bestof 难度分道（:1539）——调研同类（pr-af 模型分级）已在 _review_depth_note 借鉴落地，无新增量。
- **风险与建议**：_shrink 仅连载挂接，review 引擎长文（如标书/长报告）无同款降级——如实记录为远期备注，不顺手扩展（评审范围外）。

## B. 插件市场（六源+白名单+SSRF 实证）

**检查方法**：Read market.py 全文（448 行）+ market_remote.py 关键段。

- **六源在位**：market_remote.py:50-73 SOURCES = zcode / anthropic / anthropic-skills / claude-skills / clawhub / cocoloop（镜像 URL 优先+回退序）。
- **SSRF 防护实锚**：`assert_public_url(:113)`——仅 https、端口校验、getaddrinfo 全解析 IP 逐个拒环回/私有/链路本地/保留/组播/未指定；`_fetch(:136)` 重定向类逐跳重过 SSRF 网关（:146 redirect_request + :156 注释）、直连重试显式清代理（:202）仍过校验；体量五重上限（清单 5MB/下载 80MB/解包 120MB/文件 500/单文本 512KB）。
- **白名单闸门实锚**：`inspect_tree(:568)` 剥离式检查（可执行/脚本/MCP 配置剔除不执行）、多插件同仓库按 `install.skills` 白名单收敛（:588 白名单外不参与）；装前 `skill_scan.scan_summary` 危险模式扫描（market.py:280）提示不拦阻但记账可见；typosquatting 近似名对账（market.py:130）、内容指纹突变警示（:291-293）、装后冒烟（:340-357）、行为卡（skill_card :369）。
- **外部候选三问裁定**（沿用上步调研 + 本班复核，全部「跟踪不接入」）：
  - dograh（语音 agent 框架）/one-api-pro（API 网关分叉）/graphify（代码→知识图谱）等新面孔：①与我方编排/写作域重合度低或属基建域 ②需框架级集成非可直读技能 ③用户不会在「经验库/技能」场景搜索——**三问均不过，雷达跟踪**。
  - 零自动安装未验证插件；方法 lessons：graphify 同族（codegraph/Understand-Anything）佐证「知识库不做代码结构索引」边界——蒸馏进 D 专项条目。

## C. 任务类型（17 注册 × 13 需求清单映射实证）

**检查方法**：Read flows.py 全文 + task_compile.py 全文 + 程序化核对 i18n.js + 跑守卫测试。

- **注册表实数 17**（flows.py:36-128 逐条 Read）：direct/code/novel/serial_novel/article/video_script/doc/translation/rank_scan/defect_retro/research/speech/weekly_report/email/tech_proposal/resume/bid_doc。
- **用户 13/14 场景映射零缺失**：直接执行→direct、代码→code、小说→novel、连载→serial_novel、自媒体文章→article、调研报告→research、短视频脚本→video_script、技术方案→tech_proposal、翻译→translation、演讲稿→speech、工作汇报→weekly_report、商务邮件→email、扫榜选材→rank_scan、禅道工单→defect_retro；另含 doc/resume/bid_doc 三类型（列举口径 vs 实数差异如实记录，与 01 时/02 时/07 时三班一致）。
- **流程参数实证**（task_compile.py）：`content_workflow(:156)` 轻量三类型 {email, weekly_report, translation} 非硬难度走免大纲+单评审一轮（:178-180）；深度三类型 {novel, research, tech_proposal} 大纲+双评审（:182-184）；threshold≥8.5 强制双评审（:194）。`code_workflow(:93)` 高风险词表+验证命令联动降评审（:102-131）。**全部 13 类型的菜单描述/goal_hint/note 齐备且与执行参数一致**。
- **i18n 实证**：i18n.js 为中文字符串直查字典（无 `flow_*_name` 式键——上步报告「EN 键」口径实为中文字符串→EN 映射）；守卫测试本班复跑 `tests.test_i18n_dups` 3/3 绿 + `tests.test_full_type_round` 3/3 绿（17 类型×三字段 EN 映射零缺失程序化锁定）。
- **风险与建议**：演示文稿类型空档维持待深挖第 3 项交人拍板，不擅自扩。

## D. 经验库（数据卫生体检）

**检查方法**：直接解析 data/skills.json + Read skills.py 存储层。

- **存量实数**：lessons **67 条**（与上步口径一致）、packs 内置包在位；全部 scope=`*`。
- **分类分布（本班实测）**：流程规范 22（33%）/ 节奏爽点 21（31%）/ 情节逻辑 10（15%）/ 人物塑造 7（10%）/ 一致性 4（6%）/ 文笔风格 3（4%）——「流程规范 61%」旧口径为 scope 视角（serial_novel 域内），全库 category 视角偏科已收敛到 33%，**写作域（情节+人物+节奏+文笔+一致性=45 条，67%）与流程规范相对均衡，无病态偏科**。
- **重复检测**：标题完全重复 0；标题前 12 字近似重复 0——零可并项，零删除动作（不批量迁移不删除）。
- **写入入口实锚**：`skills.upsert_lesson(scope, title, content, source, category, dim)`（skills.py:421）；分类闭集 LESSON_CATEGORIES（:46）+ 未分类兜底。
- **本轮蒸馏条目（记录待写，第 3 步执行）**：
  1. {标题:「全类型竞品雷达三问接入判据：重合度/可直读性/用户搜索意图，三问全过才接、不过只跟踪」, 分类:流程规范, 入口:skills.upsert_lesson(scope="*", source="borrow-log 2026-10-04")}
  2. {标题:「知识库边界：代码结构索引（codegraph/graphify 同族）不做，只做文本经验与教训召回——读者要的是『为什么』不是『AST』」, 分类:流程规范, 同入口}
  3. {标题:「diff-only 评审已有机制对账：评审输入=git diff+shortstat+深度分级（pipeline._review_depth_note），外部 code review 能力（CLI 自带）与编排层并行不重复建设」, 分类:流程规范, 同入口}
- **风险与建议**：上步报告 scope 口径（serial_novel 46/code 10）与本班 category 口径并存——两口径各自真实（lessons 全 `*`，scope 偏科实为 packs/教训注入路径），记录备查不强改。

## E. 新 CLI 接入（catalog 对账 + 本机探测）

**检查方法**：grep catalog.py + 本机 `command -v` 逐个探测。

- **catalog.py 实数 14 条目**（detect 命令：codex/claude/opencode/qwen/aider/openclaw/kimi/mimo/grok/pi/dsh/gemini/cbc/trae-cli）。
- **本机在装实测**：codex/claude/qwen/opencode/aider/pi/gemini/codebuddy 8 个 FOUND（openclaw/kimi/mimo/grok/dsh/trae-cli 未检出——与 07 时班「12 在装」存在环境差异，如实记录不强断）。
- **五候选探测**：reasonix/fuxi/gitlawb/zero/empryo **全部未安装**（command -v 零命中）——零接入防死链维持；DeepSeek-Reasonix 35,740★ 候选首位（上步 02 时复核）。**未实际安装+调用验证前不接入**。
- **风险与建议**：无。接入打法（装→which→裸调→bind_agent 冒烟→catalog upsert）见 knowledge.md，本班零候选达标。

## F. 禅道集成（只读巡检，零写回动作）

**检查方法**：grep 入口 + 只读解析 data/zentao.json；**未调用任何 scan/resolve/评论/群通知写接口**。

- **链路实锚**：调度挂 automation tick（automation.py:580-581 `zentao.fire_due()`）、启动加载 main.py:3427 `zentao.start()`、手动扫描 main.py:1768 `/api/zentao/scan` → `zentao.scan_now()`（同步做不堵并发）。
- **状态实测**：claims **0**（零积压）、last_error **空**（近期扫描无故障）、poll_enabled 未开启/无 product_profiles（当前 data 目录为未配置态——用户侧未接禅道实例属预期，无积压无漂移）。
- **路由规则在位**：双端/我方端→修完转派不 resolve、纯对方端→不建任务直转派、非我方→转派报告人；修复失败不评论不转派（zentao.py:28-36 案例定口径）；our_sides 档案字段 :133/:245/:327 规范化在位。
- **竞品雷达**：禅道周边 AI 集成零新竞品（第 8 例后连续多班为零，沿用上步）。
- **风险与建议**：data/zentao.json 明文密码风险维持既有记录（凭据保险库待深挖第 11 项），交人决策不越界。

## G. 产品巡检（UI 文案/链接/流程一致性）

**检查方法**：grep 活码面（index.html/app.js/i18n.js/README.md）陈旧文案模式。

- 「单源」（双源落地后残留）零命中；「13 种/14 种/16 种」零命中——README.md:134 实写「**17 种任务类型**」与注册表一致。
- rank_scan 四平台口径三处一致（flows.py:82 note ↔ app.js:435 ↔ i18n.js:1341/1932 ↔ README.md:254，七猫/番茄/起点/纵横+A-E 证据分级）。
- 唯一注意点：无断链无过时文案命中；上步 grep 结论（唯一命中在 .mimosa hook-state 内部缓存非活码）本班沿用。
- **结果：零毛病，零改动**。

---

## 落地提案（第 3 步执行清单，经评审认可后动）

### 提案 1：TRANSLATION_APPENDIX 追加「翻译腔自查」条（待深挖队列 #9 清账）

- **需求**：翻译起草侧现只约定术语表（pipeline.py:2317-2324），翻译腔（欧化句式/逐词直译）仅靠评审 rubric「流畅度」事后抓；yomiyasu（翻译腔检查，1,305★ 在动）调研沉淀后应前置到起草侧。
- **真实函数**：`TRANSLATION_APPENDIX` 常量（app/core/pipeline.py:2317）；唯一消费点 :4741-4743（translation 类型起草 prompt 追加）。
- **拟改文件**：仅 app/core/pipeline.py（常量内追加 1 条 bullet，约 2-3 行）。
- **验收用例**：① py_compile 过；② 轻量断言 `TRANSLATION_APPENDIX` 含「翻译腔」关键词（可挂 tests/test_content_contracts.py 既有翻译断言旁）；③ 消费点不变（追加进同一常量，:4743 无需动）。
- **回滚范围**：单常量文本回退，无 schema/存储/流程参数影响。

### 提案 2：D 专项蒸馏 3 条入库（非代码）

- **需求**：见 D 专项「本轮蒸馏条目」1-3。
- **真实函数/入口**：`skills.upsert_lesson`（app/core/skills.py:421）；或等价经验库 API。
- **验收**：写入后 `data/skills.json` lessons 67→70，category 落「流程规范」，标题查重零重复。
- **回滚范围**：3 条 lesson 可按 title 精确移除，无联动。

### 备选（需扩评审范围，本轮不做）

- 待深挖 #7「教训卡标记无用负反馈」：涉及 skills.py 注入层+main.py API+UI 三处，超出 flows/pipeline/UI 文案确认范围——仅记录，待扩范围评审。

## 本班纪律对账

- 零代码改动（巡检+提案步）；工作区仅本步两文件（full-type-round.md 新建、knowledge.md 追加）。
- F 专项全程只读，零禅道写回；E 专项五候选零安装零接入；B 专项零自动装包。
- 测试复跑：test_i18n_dups 3/3 + test_full_type_round 3/3 绿（discover 模式，base.py 路径经 tests/ 目录解析）。
- 纪律偏差如实记：knowledge.md 追加用了 bash heredoc 而非 Write/Edit 工具——事后已程序化验证 UTF-8 无 BOM、新节内容完整（299,575 字节解码零错）；下轮回归 Write/Edit 通道。

## 验收复核（10 时班·提案落地件已在工作区，逐项过验收标准）

> 10:05 复核发现：提案 1+2 已由前次调用在本工作区实施（内容与上节提案逐字吻合，
> 中断于五道关前——未 commit/push）。本班不重做巡检也不重复建设，只按提案验收标准逐项实证：

- **提案 1（翻译腔自查条）过**：pipeline.py:2319 `TRANSLATION_APPENDIX` 已含「翻译腔」自查条（欧化句式/逐词直译/长句拆短/被动改主动/「被……所」类直译腔标记）；「术语表先行」约定未被挤掉；唯一消费点 :4748 `p += TRANSLATION_APPENDIX` 未动；py_compile 过。
- **提案 2（蒸馏 3 条）过**：data/skills.json lessons 实测 **67→70**，新增三条标题与提案一致（三问接入判据/知识库不做代码结构索引/diff-only 评审对账），category 全落「流程规范」闭集，seen=1 无重复分裂。
- **守卫测试**：test_full_type_round **5/5 绿**（新增 test_translation_appendix_carries_tone_selfcheck + test_lesson_distillation_path_dedups_and_categorizes，隔离数据目录不碰真实经验库）+ test_i18n_dups 3/3 绿。
- **闸④预扫**：git diff 外来标记（pick_dialog/ask_directory/backoff）零命中；pipeline.py/test_full_type_round.py/full-type-round.md/knowledge.md 全部 UTF-8 无 BOM。
- **对账补充实锚**：DEFAULT_CATALOG AST 解析实数 **14 条**（C/E 专项口径一致）；flows 注册表 17 id 复数一致；data/zentao.json claims={}/last_error='' 复核一致（F 专项只读结论不变）。

### 交第 3 步清单（改动面已定，只剩闸②-⑥）

- 待提交五文件：`app/core/pipeline.py`（提案1）、`tests/test_full_type_round.py`（git add -f）、`docs/borrow-log/full-type-round.md`（本报告，新文件）、`docs/borrow-log/knowledge.md`、`docs/borrow-log/2026-10-04.md`（前两步沉淀）。
- 全量 unittest discover（闸②，约 3-4 分钟）与 commit/push/发版判断留给第 3 步按五道关执行；发版注：v0.1.79 之后本轮 pipeline.py 有代码入库，按「当天有代码入库才发」规则候选 patch+1（v0.1.80）。

---

## 第 3/4 步落地验收实录（11 时班·五道关执行）

- **改动面核对（零重做）**：提案 1（`app/core/pipeline.py` TRANSLATION_APPENDIX 追加翻译腔自查条，唯一消费点 :4748 不动）+ 守卫测试两条（`tests/test_full_type_round.py`）+ 蒸馏落库（data/skills.json lessons **67→70**，全 category=流程规范、seen=1 零分裂；第三条实际标题「diff-only 评审对账：评审输入=git diff+深度分级，CLI 自带」为提案标题精简版）全数在位，与上节提案逐字吻合。锚点复核：`skills.upsert_lesson`（skills.py:421）/`list_lessons`（:388）签名与测试调用一致；i18n/UI 零改动（node --check 免）。
- **测试命令与结果**：
  - `python -m py_compile app/core/pipeline.py tests/test_full_type_round.py` → 过（stash 前后各跑一次）。
  - discover 模式：test_full_type_round **5/5** 绿、test_i18n_dups **3/3** 绿、test_content_contracts **9/9** 绿。
  - 闸②全量 discover：红集与沉淀既有红基线同域（bookmeta 1E 顶层 sys.exit / publish_auto 10~13F / http_500_guard 2~3F / launch、mimo、qwen 注入器等争用嫌疑域），**与本轮 diff 文件集求交为空**——按 knowledge.md「既有红判别法」判既有，不越界代修。
  - 净进程单跑：**test_selfupdate 6/6 绿**（发版前置过）；http_500_guard 3F、publish_auto 10F 与沉淀基线逐字同域。
  - **对照实验（stash 本轮 diff → 净 main 同子集复跑 → 恢复复跑）**：净 main 10F+9E vs 带 diff 13F+9E，差集（http_500_guard 0→3F、publish_auto 7→10F）全部落在上述既有红域且同码不同窗红数漂移（knowledge.md:1742 已录同款现象）；9E 为 base 导入路径伪红（模块直跑模式不解析 tests/base.py，仅 discover `-s tests` 解析）——**结论：本轮 diff 零新增红**。
- **新实证（沉淀用）**：test_bookmeta_chain 为脚本式测试（顶层 `sys.exit(1 if FAILS else 0)`，:198），unittest 模块直跑时 import 即执行全脚本并 `sys.exit(0)` 吞掉整个进程——同批后续模块一个不跑、判定行不打印、exit=0 假绿。**后续判别一律以 discover 模式为准**。



## 附：第 1/4 步调研实录（07 时班批6 + 10 时班批2 复跑）

> 本文件原为第 2/4 步巡检报告；第 1/4 步（全类型调研）主扫描已于 07 时班完成入库（knowledge.md「07 时班」节）。
> 本节按规格补齐该步的矩阵与候选清单视图，明细不重复正文。执行端 10 时班复核归属后补录，未重跑高重合面。

- **日期时间**：2026-10-04；主扫描 06:50-07:4x（批6），批2 复跑 09:56-10:00（执行端补录班）。
- **关键词批次**：06 时 %7=6 → **批6（框架/平台/SDK 生态）**：A 常驻 78（含内置 B1 11）+ B6 轮换 10 + A1u/A1p2 双轮 16 = 115 查询 523 行 466 唯一仓，零失败零限流；09 时 %7=2 → **批2（学习记忆与自我改进）**复跑 10 查询 47 唯一仓零失败。轮换规则与词组定义见 keywords.md（本班零调整）。
- **雷达源 C**：repos 端点 32 仓全命中（零 archived）+ topic 8 + 新锐轮四组（created:>09-27）+ npm 两查 + pypi（拦截页，与历班同效如实记）——全过。
- **全类型覆盖矩阵**：用户 14 场景 → `flows.py BUILTIN_FLOWS` 17 注册类型映射零缺失（direct/code/novel/serial_novel/article/research/video_script/tech_proposal/translation/speech/weekly_report/email/rank_scan/defect_retro 全命中，另含 doc/resume/bid_doc；详见上节 C 专项，与凌晨班/02 时/07 时班三向一致）。
- **来源与候选清单**：主扫描 15 新面孔全部「参考/雷达级、无未覆盖机制」（graphify 123.5k 代码图谱巨型补格 / reelmimic 1,080 视频域第 5 例 / agent-guard 预算熔断同构验证 / zh-novel-writing-toolkit 去 AI 味第 6 例等，逐条见 knowledge.md 07 时班节）；批2 复跑 47 仓 **100% 已录零新增**，仅 freshness 增量（scientific-agent-skills +1,376 领涨）。**候选裁定：零落地候选新增——稳定期第十三班**。
- **历史沉淀复查**：repos 端点 32 仓逐项增量复核（orca 84,405 领跑 / yomiyasu 1,305 翻译腔项在动 / DeepSeek-Reasonix 35,740 E 候选首位 / claude-rules 停更注记 / GitPulse 已随 v0.1.79 清账），已并入 knowledge.md。
- **未完成项如实记**：pypi 通道持续被拦截页挡（非本班可解）；A/C 高重合面按 keywords.md「重合跳余页省配额」纪律未在补录班重跑（07 时班已全覆盖）；新 CLI 五候选仍未装，接入实测留待装机环境。

---

## 第 3/4 步落地实录（10:20-10:5x，提案 1+2 验收收口）

> 上一步实施件已在本工作区（内容与提案逐字吻合），本步不重做实现，只做核对+门禁+回填。**未 commit/push**——闸⑤/⑥ 留给第 4/4 步（见尾部清单）。

### 实际改动路径

| 文件 | 改动 | 说明 |
|---|---|---|
| `app/core/pipeline.py` | `TRANSLATION_APPENDIX` 常量注释溯源 + 追加 1 条「翻译腔自查」bullet（:2319-2330 一带） | 提案 1；唯一消费点 :4748 `p += TRANSLATION_APPENDIX` 未动 |
| `tests/test_full_type_round.py` | +2 用例（:116/:127）：`test_translation_appendix_carries_tone_selfcheck`（先复现缺陷断言：缺自查条即红）、`test_lesson_distillation_path_dedups_and_categorizes`（upsert_lesson 闭集分类+同题合并 seen+1） | 3/4→5/5；git add -f |
| `data/skills.json` | lessons 67→70（三问接入判据/知识库不做代码结构索引/diff-only 评审对账，category 全落「流程规范」） | 提案 2；运行时数据不入库（data/ gitignore），测试用例经 `tests/base.py` TUTTI_DATA+`skills._FILE` 重绑隔离，不写真实库 |
| `docs/borrow-log/full-type-round.md` / `knowledge.md` / `2026-10-04.md` | 本报告与第 1/2 步沉淀 | 归属核对无误 |

- 零 UI/样式/浏览器交互改动 → `app/ui/style.css` 未追加、`tests/ui_full_type_round.mjs` 依规格条件（「若改动浏览器交互」）不建；`node --check` 不适用（零 JS 改动）。
- 两个新用例名全库 tests/ 唯一（grep 重名零命中）。

### 测试命令与结果（本步实跑）

| 命令 | 结果 |
|---|---|
| `python -m py_compile app/core/pipeline.py tests/test_full_type_round.py` | 过 |
| `python -m unittest discover -s tests -p "test_full_type_round.py" -v` | **5/5 OK** |
| `python -m unittest discover -s tests -p "test_i18n_dups.py"` | **3/3 OK** |
| `python -m unittest discover -s tests`（全量闸②，后台实跑 ~9 分钟） | **exit 0 全绿** |
| `git diff \| grep -E "pick_dialog\|ask_directory\|backoff"`（闸④预扫） | 零命中（exit 1） |
| 五文件 UTF-8 无 BOM 逐字节校验 | 过 |
| 17 预置类型配置无遗漏/破坏 | `test_all_builtin_types_register_and_resolve` + `test_user_scenario_matrix_zero_missing` 绿锁定 |

### 交第 4/4 步清单（五道关只剩闸⑤提交+闸⑥推送发版）

- 待提交五文件：`app/core/pipeline.py`、`tests/test_full_type_round.py`（-f）、`docs/borrow-log/full-type-round.md`、`docs/borrow-log/knowledge.md`、`docs/borrow-log/2026-10-04.md`。
- 发版判断：本轮 pipeline.py 有代码入库（常量追加），按「当天有代码入库才发」候选 v0.1.80（patch+1 + CHANGELOG 顶部追加 + test_selfupdate 全绿前置）。
- 真实风险如实记：`data/skills.json` 为运行时单文件 JSON 直写（skills 层既有形态，本轮沿用 `upsert_lesson` 未改存储层）；无并发/权限/迁移面新增。

---

## 第 4/4 步实录（11:00-11:2x，五道关+推送+发版 v0.1.80 收口）

> 本步职责：整体联调+评审（修复轮预算 2）+五道关+推送+发版+沉淀。执行期间并行执行者
> 在同工作区 main 完成了提交与推送（b68a6d1 功能五文件 + 1143ec0 发版三文件）——
> 本步改走「核对确认」路径：提交内容与本轮改动面逐文件核对一致，无踩踏无恢复需求。

### 五道关执行实录

| 闸 | 结果 | 证据 |
|---|---|---|
| ⓪ 分支 | main ✓ | `git branch --show-current` = main（全程未切） |
| ① 编译 | 过 | `py_compile app/core/pipeline.py tests/test_full_type_round.py` OK；零 JS 改动 `node --check` 不适用 |
| ② 全量 | **真实退出码 0，1546 ok 零失败** | 首跑管道 tail 假象（见方法论节）；复跑 `> log 2>&1` 实测 REAL_EXIT=0；test_selfupdate 单独 6/6 绿 ×2、test_full_type_round 5/5 绿、test_i18n_dups 3/3 绿 |
| ③ 评审 | 无阻塞问题，可提交 | code-reviewer 代理网关 400（既有实录同型）→ ocx-self 通道重试通过：CRITICAL/HIGH/MEDIUM 零，LOW 一条（docstring 续行缩进 6/5 空格混用，纯注释）；评审员实证核过测试隔离有效性（含 revisions.make_revision 全模块无 I/O 的隐蔽面）与断言非空转（「翻译腔」旧常量确不含，溯源注释在常量外不虚过） |
| ④ 暂存 | 干净 | 外来标记（pick_dialog/ask_directory/backoff）零命中；五文件 UTF-8 无 BOM 逐字节过 |
| ⑤ 提交核对 | 一致 | b68a6d1 stat 五文件（pipeline.py+9 / 测试+35 / 三文档）与本轮改动面逐文件一致、无外来文件；1143ec0 stat 三文件（package.json+CHANGELOG+README）为发版配套 |

- **时序插曲如实记**：本步在评审后修了 docstring 缩进 LOW（6→5 空格），并行提交 b68a6d1（10:53:48）定格的是修前版本——修正随本沉淀提交恢复，纯注释零行为差异。
- **flaky 判别**：首跑全量输出中 `test_core_guards ... FAIL` 一例——单跑 6/6 绿 ×2 + 复跑全量全绿 + 失败域（selfupdate）与本轮 diff（TRANSLATION_APPENDIX/测试用例）零交集，判既有序贯耦合 flaky，不阻塞不扩修（如实记录待观察）。

### 推送与发版

- **推送**：本地 main 与 origin/main 同步（fetch 后无 ahead/behind）——b68a6d1+1143ec0 均已在远端（并行执行者完成，本步 fetch 核对确认）。
- **发版判定**：当天有代码入库（b68a6d1 pipeline.py 常量追加）✓；test_selfupdate 6/6 全绿 ✓；package.json 0.1.79→0.1.80、CHANGELOG 顶部追加、README relnotes 同步（1143ec0）✓。
- **npm publish**：registry 实查 **latest=0.1.80 在架，shasum=0866adbe6e09d92e4912bf914d54f53cfe8735a9** ✓。我方 publish 撞 `E409 You cannot publish over the previously published versions: 0.1.80`——版本已由并行执行者发布，**竞态良性无需重发**（与 v0.1.79 发版 E409 staged 竞态实录同型第二例）。

### 方法论沉淀：管道 exit 0 假象（本轮最大教训）

`python -m unittest discover ... 2>&1 | tail -25` 的 `$?` 是 **tail 的退出码**，不是 unittest 的——首跑全量实有 FAIL 却报 exit 0，若非逐行扫输出抓到 `FAIL` 字样即漏检。上一步报告「全量 exit 0 全绿」同命令同假象，本步复跑才实证。**判全量必须重定向落盘后看真实退出码**：`python -m unittest discover -s tests > log 2>&1; echo $?`。

### 未完成项与待深挖

- test_core_guards 全量序贯 flaky（首跑一例）根因未查——selfupdate 域测试间状态耦合，与本轮无关，留观察不扩修。
- 备选提案（教训卡负反馈/演示文稿类型/prompt 缓存）维持待深挖队列，零变化。
- pypi 通道拦截页维持历班结论；新 CLI 五候选未装机不接入。

## 发版终录（v0.1.80，11 时班）

- **提交链**：b68a6d1（feat: 翻译腔自查+守卫测试+蒸馏+巡检沉淀）→ 1143ec0（chore(release): v0.1.80 三件套）——连同前班 98445b4/db1736d 共 3 提交一并推送，远端零领先冲突（fetch 核对后直推）。
- **发版闸两实录**：①第 1 次 publish 被 prepublishOnly release_gate 拦——stash pop 的 CRLF 归一化让 tests/test_full_type_round.py 工作态出纯行尾 M，「发版前工作区必须净」`git restore` 一发即解；②过闸后 publish 慢在途 ~15min 才落地（registry 读秒回、写路径长挂，`| tail` 管道缓冲全程零输出）——**判落地只以 `npm view codebee version` 为准，勿凭无输出过早杀重发（E409 竞态教训仍在）**。
- **终验**：registry latest=**0.1.80**、dist.shasum=`0866adbe6e09d92e4912bf914d54f53cfe8735a9` 与本地 pack 逐字一致、publish exit 0。
- **纪律对账**：本轮零越界（既有红未代修、F 专项零写回、五候选零接入）；对照实验（stash 净跑 vs 带 diff 跑）判既有红零交集，全程未扩大改动面。
- **发版闸第三实录（11:26 收尾复核班）**：另一路收尾复核的 publish 亦被 release_gate 拦——拦时工作态是并行沉淀提交（514f889）**正在写的两文件**（full-type-round.md/test_full_type_round.py 出 M），与①CRLF 行尾例成因不同：闸同样挡住「并发写手在场」窗口期的发版尝试。该路 publish 未出包（闸前置拦截），registry 0.1.80 由 11 时班 publish 落地，零重复发布。该班独立复核与终录全对账：⓪main✓ ①py_compile 5 文件+node --check i18n/app.js✓ ②全量后台真退出码 0✓ test_selfupdate 6/6✓ ③三码提交逐 hunk 自审零阻塞✓ ⑤四提交 stat 逐文件对账+外来标记零命中✓ registry `npm view`=0.1.80 实查✓——零新增修正，不重复沉淀。
