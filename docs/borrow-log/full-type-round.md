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

---

## 2026-10-04 13 时班（新一轮计划第 2/4 步·七专项 A-G 巡检+落地）

> 分支 main（7dbc3ce）。上接 12 时班批5 调研（工作区两 docs 在制品不碰）。巡检全绿，
> 落地一件：待深挖第 7 项「教训卡标记无用显式负反馈」清账（后端零基础全新建）。

### 七专项巡检结论（本班实锚）

- **A token 节约**：预算熔断（pipeline.py:627/:696 max_tokens_per_run）/diff-only 评审（_git_diff:944+深度分级）/cascade（modelhub.classify_difficulty:3536）/_shrink_context_block（:2476，仅连载路径挂接）全在位。四方向对账：prompt 缓存/语义缓存全库零命中维持待深挖（语义缓存卡租户边界）；廉价分流已有完整链；strands-decider「机械决策不进 LLM」记 A 专项远期备注不建设 | 已覆盖
- **B 插件市场**：六源（market_remote.py SOURCES:50）+assert_public_url:113+逐跳重过网关+typosquatting 对账（market.py:131）全在位；12 时班新见候选过三问均不过零接入 | 已覆盖
- **C 任务类型**：BUILTIN_FLOWS 17 类型运行期实测全注册；rank_scan note「四平台」与 paihang._SOURCES 四源一致，无双源/单源过时文案残留 | 巡检
- **D 经验库**：data/skills.json 70 条实测——流程规范 36%/节奏爽点 30%/情节逻辑 14%/人物塑造 10%/一致性 6%/文笔风格 4%，全库视角无病态偏科（61% 旧口径为 scope 视角，09 时班同结论）；标题完全重复 0 | 数据卫生
- **E 新 CLI**：catalog 已接 11+；五候选（reasonix/fuxi/gitlawb/zero/empryo）本机 command -v 全未检出——零接入防死链维持 | 巡检
- **F 禅道**：data/zentao.json claims={} 零积压、last_error 空、poll 未启（用户侧未接实例属预期）；scan_now/路由三规则/失败不回写口径在位；12 时班周边扫描零新禅道 AI 竞品 | 巡检
- **G 产品**：版本号 package.json=0.1.80 与 CHANGELOG 一致；node --check app.js/i18n.js 过；榜单源数/菜单描述抽查零过时 | 巡检

### 落地实录：教训卡「标记无用」显式负反馈（待深挖第 7 项清账）

> hippo-memory 差量（770★，10-04 当日活跃）。此前自动闭环只有 won/lost 结局归因
> （注入后过审/失败），用户「这条教训没用」的显式判断无入口。最小实现：

- `app/core/skills.py`：①lesson_op 新增 "useless" op——立即停用（不再注入）+useless 计数 +1（再启用不丢证据）；②_karma/_surplus_decay 把 useless 按失守同权计入粘滞负证据（clamp 在 hits 内不倒挂），显式负反馈后即使手动再启用，排序也持续下沉
- `app/ui/app.js`：教训卡新增「没用」按钮（确认弹窗→op useless）+「没用 n」负反馈计数徽章
- `app/ui/i18n.js`：+4 EN 键（按钮/徽章/确认弹窗/徽章 title）
- `tests/test_lesson_feedback.py`（新增，git add -f）：3 用例——停用+计数落账、bump_hits 对齐证据量后 relevance_top 排序下沉、enable/disable/delete/未知操作原语义零回归
- 验收：py_compile+node --check 过；test_lesson_feedback 3/3 绿；test_i18n_dups 3/3、test_full_type_round 5/5 守卫绿；用例名全库唯一
- 未做与风险备注：语义缓存/prompt 缓存维持待深挖第 2 项（租户边界交人拍板）； useless 证据 clamp 在 hits 内——零注入教训被标无用不产生 karma 罚（不臆测无注入证据的排序，先停用即刻生效）

---

## 2026-10-04 15 时班（新一轮计划第 1/4 步·全类型调研——检索覆盖矩阵与证据）

> 开工 15:36、分支 main（b4dd562，v0.1.81 已发版）、工作区干净。本步只做调研与沉淀，
> 零代码改动；产出=knowledge.md「15 时班」节 + 本节。

### 执行参数与批次判定

- **本机执行时刻**：2026-10-04 15:36（周日）；**hour=15，15 % 7 = 1 → 轮换批1（代码质量与评审）**。
- **主扫描**：`scripts/borrow_scan_nightly.py` 全量（无 --batch）＝A 常驻 78（含内置 B1 11）+ B1 轮换 11 + A1u（sort=updated 新锐轮）8 + A1p2（stars page=2 翻页）8 = **116 查询**。
- **限流纪律**：gh api 认证调用串行 sleep 4s；进度日志 116/116 ok、**零 403 零 FAIL 零 EXC**；单查询超时 60s + 403 退避 20s 一次（未触发）。
- **结果**：524 行、按 full_name 去重后 **423 唯一仓**；结果高度重合（与当日凌晨班/07 时班两轮 A 常驻 + 今日批1-7 全覆盖一致）——按 keywords.md「重合跳余页省配额」纪律，未追加 page=3。

### 检索覆盖矩阵（13 场景 × 本轮命中面）

用户列举 14 场景（口径「13 种」）对 `flows.py BUILTIN_FLOWS` 实数 17（当日多班三向一致，本班不重复动码，引用凌晨班矩阵）：direct/code/novel/serial_novel/article/video_script/doc/translation/rank_scan/defect_retro/research/speech/weekly_report/email 全映射，另含 doc/resume/bid_doc。本轮词组对各类型的扫描命中：

| 类型 | 词组面 | 本轮命中结论 |
|---|---|---|
| 代码 | B1 批1 全 11 组 + A10 代码两组 | 主体已录零增量；GnawTreeWriter 0★（AST 编辑族）等微型 |
| 小说/连载 | A7 前四组 + A12 | 写作域无新竞品**第七班连续确认**（新锐轮全 0-2★） |
| 自媒体文章 | A7 article 组 + wenzi-xhs（已录） | 无新标的 |
| 调研报告 | A10 deep research + B5（今日 12 时班） | 已录族稳定 |
| 短视频脚本 | A10 短视频组 + video_script 域（5 例已录） | 无新标的 |
| 技术方案/演讲/汇报/邮件 | A10 对应四组 | 学生级为主，结论维持 |
| 翻译 | A7 translation + A10 quality | yomiyasu 1,347（10-04 当日活跃）在动；已落地条无新差量 |
| 扫榜/禅道工单 | A9 + 禅道周边 2 组 | 零新禅道 AI 竞品（第 8 例后持续为零） |
| 对话/文档 | A10 chatbot memory / doc 组 | sharedcontext 50★ 等微型 |

### 雷达源 C 遍历记录（全过，逐源证据）

| 源 | 通道 | 结果 |
|---|---|---|
| repos 端点复查 | gh api repos（22 仓，2s 间隔） | 全命中零 archived：orca 84,581（10-04）/Reasonix 35,738（10-04，E 候选首位）/oh-story 7,242/yomiyasu 1,347/hippo-memory 770/ZCode 7,384（未装维持）/spec-kit 140,041/OpenSpec 71,004 等 |
| awesome 清单 | repos 端点 8 清单 | 全活跃：awesome-claude-code 55,035（10-04）/VoltAgent 35,181/awesome-claude-skills 76,443/awesome-llm-apps 140,663/awesome-mcp-servers 95,806/harness-engineering 4,692/Agent-Memory 657（10-04）/Long-Horizon 1,059（停更观察维持） |
| Trending 替身 | trendshift.io（webfetch 直连成功） | 新面孔 t3code 24.8k/image-blaster 1.1k/whirl 355 已录；moli/OpenDots/claude-mem/Agent-Reach 已录 |
| topic 页 8 | gh api search topic: sort=stars per_page=5 | 首五位全已录；唯一新面孔 archify 76,860（已录上） |
| 新锐轮 2 | created:>2026-09-28 sort=stars | agent 域全 genpark 蓄水农场族（已录不重复）；写作域全 0-2★ |
| 自家 CLI 周边 5 | codex manager/claude code manager/opencode suite/kimi cli/grok cli | 全为已录族（cockpit-tools 18,614/opencodex 16,883/kimi-code 7,769/CLIProxyAPI 54,067） |
| 禅道周边 2 | zentaophp OR zentao ai/bug triage agent | 首页大盘噪音占位、clickup-ai-bug-triage 0★——零新禅道 AI 竞品 |
| npm 两查 | npm search --json（agent orchestrator/claude code） | 已录族为主（nax/agentcraft/oceanus/bizar/coleo），无接入级标的 |
| pypi | pypi.org/search（webfetch） | **Client Challenge 拦截页**（历班 3038B 拦截页之外新形态，第六种记录）——通道不可用如实记，npm 通道正常 |

### 本轮结论与候选裁定

- **真新面孔 ~18 件全为参考/雷达级，零机制级新差量（连续第十六班稳定期）**；最大两件 t3code（24.8k，同形态控制面）与 archify（76.9k，图表生成 skill）均无「他们有、我们没有、确实好用」的可抄机制——同形态形态差量（移动全控）与域外能力（图表工序）分别交人拍板/记录，不擅自扩。
- **历史项目逐项增量复查**已并入 knowledge.md「15 时班·复查记录」（22 仓 repos 端点 + 主扫双轮比对）。
- keywords.md 零调整（无新依据）；A1-A10 常驻组、批1、C 雷达全部执行完毕；不可访问源（pypi）如实记为阻塞。
- E 专项顺带实测：本机在装 13 CLI、九候选全未装——零接入防死链维持（无新接入候选达标）。

---

# 2026-10-06 16 时班（新一轮计划第 2/4 步·七专项 A-G 巡检+落地提案）

> 开工 16:23（UTC+8）、分支 main（ac15ee1，v0.1.88 已发版）。工作区在制品 = 当日
> 01 时起各调研/巡检班沉淀三 docs（2026-10-06.md / current-round.md / knowledge.md，
> 未提交）——本班增量追加、逐字不动其既有内容。本步为巡检+提案步：A-G 全实证落本节；
> 落地提案只记录不执行（纳入第 3 步确认清单，评审通过再实现）。零代码改动。
> 以下行号全部为本班 16:2x-16:4x 独立实读，非沿用历班。

## A. token 节约（单列小节·八件既有机制逐项实锚）

**检查方法**：grep 定位 + 逐段 Read pipeline.py / task_compile / modelhub / usage / step_runner。

| 机制 | 真实实现（文件:行，本班实核） | 结果 |
|---|---|---|
| 三段压缩 | compaction.py（estimate_tokens/prune_text/select_range/compact_region/maybe_compact）；pipeline.py `_compaction_enabled(:231)`（默认关灰度）+ `_spawn_step(:672)` 撑爆→压缩→守门重试分支 :703 起 | 在位 |
| token_meter | `_spawn_step` 内双路径累进：压缩路径 :723 + 直通路径 :743（:738 注释「直通也必须回填 usage 防预算表空转」）；cached 单独记账（usage.py :117-120，不计 used） | 在位 |
| 预算熔断 | 花费闸 `_cost_gate_block` 先于 token 闸（:677 注释「钱比 token 更早见顶」）；`_budget_max_tokens(:613)`+`_budget_cost_caps(:632)` 日/月双封顶；ENV_BLOCK 只拦下一步不掐当前步（:685-701） | 在位 |
| cascade | pipeline.py :1529 `capability.cascade_reorder`（easy 任务 + 设置 opt-in，difficulty 传参）；上游 `classify_difficulty(modelhub:3536)` → `bind_agent(:2217)`；code_bestof 难度分道 `_code_bestof(:1146)` | 在位 |
| 经验召回 | skills.block_for（:619，stable_order 按 id 保前缀字节稳定）；知识库 retrieval.search + knowledge.block_for（:3599 消费）；调用点 stable_order 双处 :3141-3142/:3595-3598 | 在位 |
| 会话复用 | `_valid_resume(:206)` → `_resume_workdir(:219)` → `_resume_sid(:270)`（codex/claude/opencode/qwen 原生 resume，generic 走 resume_argv_template）；resume 钉原 CLI 换将不补位（:150 注释） | 在位 |
| diff 评审 | `_git_diff(:948)`（HEAD diff+未跟踪新文件，gitmod.collect_changes 30KB 封顶）+ `_review_depth_note(:1019)`（<40 行快评/≥600 行先概览后深看）唯一消费 :1089 + `_scope_note(:1054)` 越范围提醒 | 在位 |
| _shrink 家族 | `_shrink_context_block(:2538)`（圣经>模块库>经验库四层优先级）+ `_serial_shrunk_block(:2581)`（降级说明透出进 step note）；消费点 :3676 非赛马重试 + :3801 赛马复赛（21 时班落地件在位） | 在位 |

**辅助锚**：step_runner `_PRECHECK_RATIO=0.9(:28)` 事前门 :80；modelhub `chat(cache_ttl)(:3673)` 精确匹配响应缓存；`cache_control|semantic_cache|prompt_cache` 全 app/core grep **零命中**（本班复核维持）。

**四方向判定（不重复建设）**：prompt 缓存=供应商侧能力，应用侧已用 stable_order 保前缀最大化；语义缓存=队列既有项卡租户/敏感边界交人拍板；diff-only 评审=已有满配（diff+shortstat+深度分级+越范围提醒）；廉价模型分流=cascade+难度选模+bestof 分道已满配。**零新建**。

## B. 插件市场（六源+闸门+三问裁定）

- **六源在位**：market_remote.py SOURCES :50-76（zcode / anthropic / anthropic-skills / claude-skills / clawhub / cocoloop，镜像优先+回退序）。
- **缓存实数 810 逐源实测**：zcode 26 / anthropic 315 / anthropic-skills 5 / claude-skills 99 / clawhub 215 / cocoloop 150，fetched_at 全 2026-10-03——与 01 时班口径零漂移。
- **闸门实锚**：SSRF `assert_public_url(:113)`；安装白名单 `inspect_tree(:568)`（:588 白名单外内容不参与检查与安装）；market.py typosquatting 近名对账（:131/:294）、装前 `skill_scan.scan_summary`（:281）、内容指纹突变（:291-293）、装后冒烟（:340-357）。零绕闸零自动安装。
- **本轮候选三问裁定（全部跟踪不接入）**：agnix 440★（AI 助手配置 linter——独立 CLI 不可直装六源，校验思路留 B 专项备注）/ dsh-plugins-store 68★（DSH 生态商店，不属六源范畴）/ kodus-ai 1,449★（AGPLv3 且 review 域机制已满配）/ pr-agent 13,273★（仓名迁移勘定后机制对应物已满配）/ nautilus-compass 1,134★（「无编排者协调」不同轨）。

## C. 任务类型（注册表 18 × 指令面 14 映射）

- **注册表实数 18**（flows.py:36-134 逐条实读）：direct/code/novel/serial_novel/article/video_script/doc/translation/rank_scan/defect_retro/research/speech/presentation/weekly_report/email/tech_proposal/resume/bid_doc——14 指令项（含禅道工单→defect_retro）映射零缺失，另 4 自研（doc/presentation/resume/bid_doc）。
- **流程参数实证**：review 型 14 个全带 manuscript/rubric(4-5 维)/threshold(7.0，bid_doc 7.5)/rounds(2)；direct 型 3 个（direct/rank_scan/defect_retro）目标+附件即全部输入；code 型带 verify_command；serial 型带 chapters/words_per_chapter/variants/branches。`task_compile.content_workflow(:156)` 轻量三类型 {email, weekly_report, translation}（:163）免大纲单评审、深度三类型 {novel, research, tech_proposal}（:164）大纲+双评审、threshold≥8.5 强制双评审（:194）——菜单描述/goal_hint/note 与执行参数一致。
- **i18n 实证**：18 类型中文名在 i18n.js 程序化核验零缺失；新增 branches 编辑器 EN 键在位（「同章赛马稿件数」「多线剧情推演数」「1 = 关闭」）。守卫复跑：test_full_type_round **5/5 OK** + test_i18n_dups **3/3 OK**（TUTTI_DATA 隔离净进程）。

## D. 经验库（数据卫生体检）

- **存量实测**（python 直读 data/skills.json）：lessons **72** 零漂移——流程规范 26（36%）/节奏爽点 21（29%）/情节逻辑 10（14%）/人物塑造 7（10%）/一致性 4（6%）/文笔风格 4（6%）；全库 category 视角无病态偏科（「61%」旧口径为 scope 视角：serial_novel 46/code 11/* 10/direct 4/article 1）；packs=3。
- **重复检测**：标题完全重复 **0**——零可并项，零删除动作（不批量重写不迁移）。
- **蒸馏候选 1 条**（记录待写，第 3 步执行，见提案 2）。

## E. 新 CLI 接入（catalog 对账 + 本机探测）

- **catalog.py DEFAULT_CATALOG=14**（import 实测）：codex-cli/claude-code/opencode/qwencode/aider/openclaw/kimi-code/mimo-code/grok-build/pi/deepseek-harness/gemini-cli/codebuddy/trae-agent。
- **本机在装 13/14**（command -v 逐个实测；openclaw MISSING——与 01 时班「13/14 在装」口径一致；codebuddy 条目探测名 cbc FOUND）。
- **六候选探测**：deepseek-reasonix/reasonix/fuxi/gitlawb/zero/empryo `command -v` **全 MISSING**——未装不实测不接入，防死链维持。

## F. 禅道集成（只读巡检，零写回动作）

- **配置态复核（承 13 时班勘定口径，本班独立复证成立）**：data/zentao.json 为「已配置+显式关闭」态——base_url 实例（10.143.132.5:8899）+ 账号 + product_profiles 产品 96（our_sides=[backend]，owners backend/frontend 双侧在位）；**poll_enabled=false 系 config 内层显式关闭**（13 时班勘正多班「键缺失/配置缺位」误读，本班实读同判）。
- **本班新增实锚两点**：①产品 96 路由目标 backend workdir E:\GitLab\cbc\mo-so **本机实存**（ls 实测 EXISTS）——档案路由不是死链；②设置页禅道子页动作五件齐全（app.js:1012-1017 测试连接/拉产品/拉账号/扫描/保存）。
- **扫描状态**：claims=**0** 零积压、last_error **空**（无故障）、last_scan 停 2026-09-21 20:43（poll 关闭所致非漂移）。
- **链路实锚**：调度挂 automation tick（automation.py:580-581 `zentao.fire_due()`，自节流）、启动加载 main.py:3438 `zentao.start()`、手动扫描 main.py:1779-1781 `/api/zentao/scan`→`scan_now()`（zentao.py :2470/:2479/:2496）；设置页禅道子页动作齐全（app.js:1012-1017 测试连接/拉产品/拉账号/扫描/保存五动作）。
- **路由核对**：产品 96 → mo-so 仓路由目标实存，our_sides 规范化（zentao.py:327）与「双端/我方端→修完转派、纯对方端→直转派、失败不评论不转派」三规则口径在位（沿用历班实读）。
- **只读纪律**：未触发 scan/resolve/评论/群通知任何写接口；密码字段核对面全程 masked 不外播——**明文密码风险维持在档**（凭据保险库队列项，交人拍板）。
- **竞品雷达**：禅道周边零新竞品（沿用 01 时班第 19 例后口径）。

## G. 产品巡检（UI 文案/链接/描述一致性）

- 过时文案 grep（「13 种/14 种/16 种/17 种/单源」）i18n.js/index.html/app.js/README **零命中**；README.md:135「**18 种任务类型**」与注册表实数一致。
- rank_scan 四平台三向一致（flows.py:82 note ↔ paihang.py:97-102 `_SOURCES` 七猫/番茄/起点/纵横 ↔ README）。
- **在册 G 项清账**：22 时班所记「serial.branches 无 UI 编辑入口」已收口——任务表单 f-branches（app.js:769/:3192/:3957）+ 流程编辑器 fl-branches（:10936/:10982）+ i18n EN 键全在位。
- **结果：零新毛病，零改动**。

## 受阻项（如实记录，不擅动）

1. **全局一致性评审无容量降级**（22 时班录，本班复核仍在）：圣经原样注入无封顶+full_text 头截，容量受限通道 N 评审并发全挂整 run 判失败——评审覆盖面属质量语义，交人拍板。
2. 语义缓存/prompt 缓存显式接入、凭据保险库（data/zentao.json 明文密码）——队列既有项，交人拍板。
3. 队列第 1 项 RAG 向量检索——需 embedding 基建，非「小而实」；本班提案 1 只取第 4 项的关键词半张（不引基建）。
4. 禅道 poll 未开启——用户侧部署决策，不代开（实例可达性本班亦未探测，避免触发真实请求）。

---

## 落地提案（第 3 步确认清单，评审通过再动）

### 提案 1：连载起草「相关历史章节推荐」注入（队列第 4 项关键词版）

> 口径说明：13 时班「在册代码级小而实积压核对为零」指其时候拍板件（语义缓存/评审降级/
> gate 修法）——本提案出自队列活项第 4 项（20 时班移交注记「供择小而实、第 1/4 项同管线
> 攒批」）的新立提案，走本步评审门；若评审认定属行为语义交人拍板范畴，转候拍板不擅动。

- **需求**：连载前情只看近 2 章结尾摘录+findings 摘要（pipeline.py :3394-3413），300+ 章长篇的伏笔回收/人物回场只有 ledger watchlist（:3383 陈账督促）兜底；story_tracking 已持久化每章 facts/characters/foreshadowing（story_tracking.py:125 commit_chapter）却未参与起草注入。
- **真实函数/锚点**：章循环 pipeline.py:3339 起（`ch = outline["chapters"][k-1]` :3341 带 beats/hook/highlight）；`tracking_state = story_tracking.load(workdir)` 已在同函数作用域（:3082）；prev 组装块 :3394-3413。
- **拟改文件**：仅 app/core/pipeline.py + tests/ 新测试。新增 `_related_chapters_note(tracking_state, ch, max_n=3)`：纯本地关键词重叠计分（本章 beats/hook/highlight 分词 vs 历史章 foreshadowing/characters/facts 行），命中章输出「第 N 章：相关行摘录」capped 注入 `if i > 1` 的 prev 尾部；零匹配零输出。**零 LLM 调用、零存储写入**。
- **验收用例**：① py_compile 过；② 新测试（隔离 workdir 造 tracking state）：历史章 foreshadowing 含「玉佩」+ 本章 beats 含「玉佩」→ note 命中该章；零重叠大纲 → 空串；③ 既有 test_serial_ctx_shrink 4 项 + test_race_ctx_shrink 不回归。
- **跨模块影响**：story_tracking 只读；评审语义/flows/UI 零改动；输出经 prev 注入路径自然进入降级块（不新增 budget 口子）。
- **收益证据**：近 2 章窗口外的中期记忆空洞（10-04 20 时班队列实证：500+ 章体量下前情覆盖不足）补上「哪几章埋了这条线」的显式指针，成本为零额外模型调用。

### 提案 2：D 专项蒸馏 1 条调研方法论入经验库（非代码）

- **需求**：本轮（01 时班）WebSearch 交叉验证勘定 PR-Agent 仓名迁移（qodo-ai→The-PR-Agent，「新闻面 ≠ 开源仓在」规则第 4 例）——方法论目前只在 knowledge.md 对照表层，经验库注入面没有。
- **真实入口**：`skills.upsert_lesson`（skills.py:421 起）。
- **拟写条目**：{标题:「竞品勘定必须 repos 端点二次实证——新闻/搜索面与开源仓存续脱节（仓名迁移/属主消失/商业闭源），主扫与 WebSearch 捞到的名一律 repos 复核 stars/push/正主后再入对照表」, scope:"*", category:流程规范, source:"borrow-log 2026-10-06"}。
- **验收**：lessons 72→73、category 落闭集、标题查重零重复、seen=1 不分裂。
- **回滚**：按 title 精确移除，无联动。

## 本班纪律对账

- 零代码改动（巡检+提案步）；改动面仅本节两 docs 追加（full-type-round.md / knowledge.md），在制三 docs 未触碰。
- F 专项全程只读（含未探测实例可达性——避免触发真实请求）；E 专项六候选零安装；B 专项零自动装包零绕闸。
- 守卫测试经 TUTTI_DATA 隔离净进程复跑，未写真实经验库。

---

## 第 3/4 步落地实录（16 时段·提案 1+2 实施）

> 承上节提案清单执行：提案 1（连载起草「相关历史章节推荐」注入）+ 提案 2（蒸馏 1 条
> 方法论入经验库）。与提案逐条对齐：零 LLM 调用、零存储写入、零 UI/flows/评审语义改动。

### 实际改动路径

| 文件 | 改动 | 提案锚点核对 |
|---|---|---|
| `app/core/pipeline.py` | ①新增 `_related_chapters_note(tracking_state, ch, current=0, max_n=3)`（置于 `_serial_shrunk_block` 之后，`_critic_lens` 之前）：本章 beats/hook/highlight 与历史章 foreshadowing/characters/facts 行做字符 bigram 重叠计分（复用 `skills._text_bigrams`，knowledge._title_sim 同款先例），命中行数排序取 top max_n，输出「第 N 章：相关行摘录」；行摘录 80 字截断、每章 ≤3 行、单行命中门槛 ≥2 个 bigram；零匹配/无状态/空章纲返回空串；`current` 排除当章及之后记录（重跑已提交章不跟自己记录自证）。②前情组装块（findings 注入之后、仍 `if i > 1` 内）追加消费：`rel = _related_chapters_note(tracking_state, ch, current=i)`，命中才 `prev += "\n\n" + rel`，失败静默——经 prev 注入路径自然随降级块走，零新增预算口子 | 与提案签名一致（追加 `current` 关键字参数为真实重跑场景所需守卫，max_n=3 默认不变）；唯一消费点即 prev 尾部，零第二注入点 |
| `tests/test_full_type_round.py` | +3 用例（新 `RelatedChaptersNoteTests` 类）：`test_related_chapters_note_hits_orders_and_caps`（命中/排序/零重叠章不出现/max_n 封顶）、`test_related_chapters_note_silent_and_self_excluded`（无状态/空章纲/零重叠空串、current 自章排除、current=0 可命中、max_n=0 空串）、`test_serial_draft_wires_related_note_into_prev`（起草前情组装接线在位）；模块 docstring 补第 5 条 | 提案验收用例①②全覆盖；用例名全库唯一（grep 核过） |
| `data/skills.json` | lessons **72→73**：新增「竞品勘定必须 repos 端点二次实证……」（scope=\*、category=流程规范、seen=1、source=borrow-log 2026-10-06），经 `skills.upsert_lesson`（skills.py:421）API 落库，标题查重零重复、闭集分类 | 提案 2 验收全过；运行时数据不入库（data/ gitignore） |

- 零 UI/样式/浏览器交互改动 → `app/ui/app.js`/`i18n.js`/`index.html`/`style.css` 未触碰，`tests/ui_full_type_round.mjs` 依规格条件不建，`node --check` 不适用（零 JS 改动）。
- `current` 参数设计说明：提案签名 `_related_chapters_note(tracking_state, ch, max_n=3)`；实施为 `(tracking_state, ch, current=0, max_n=3)`——续写批重跑已提交章时（start_chapter 回退），记录在案的历史章会与自己大纲自证命中，`current=i` 一参排除，调用点单行传参，不引额外抽象。

### 测试命令与结果（本步实跑）

| 命令 | 结果 |
|---|---|
| `python -m py_compile app/core/pipeline.py tests/test_full_type_round.py` | 过 |
| `python -m unittest discover -s tests -p "test_full_type_round.py" -v` | **8/8 OK**（原 5 + 新 3） |
| `python -m unittest discover -s tests -p "test_serial_ctx_shrink.py"` | **4/4 OK**（提案回归项③） |
| `python -m unittest discover -s tests -p "test_race_ctx_shrink.py"` | **1/1 OK**（61s，赛马收缩不回归） |
| `python -m unittest discover -s tests -p "test_i18n_dups.py"` | **3/3 OK** |
| `python -m unittest discover -s tests -p "test_content_contracts.py"` | **9/9 OK**（未选类型与既有内容契约零回归） |
| `python -m unittest discover -s tests > log 2>&1`（全量，落盘取真实退出码） | **REAL_EXIT=0**、FAIL/ERROR 行零——判全绿；「Ran/OK」摘要行缺失系队列在册「32 位全量 discover 静默退出」已知形态（exit 0 + 零 FAIL 行双信号一致，如实记不扩修） |
| 闸④预扫 `git diff \| grep -E "pick_dialog\|ask_directory\|backoff"` | 零命中 |
| 改动两文件 UTF-8 无 BOM 逐字节校验 | 过 |

- 17/18 预置类型注册表零改动（`test_all_builtin_types_register_and_resolve` + `test_user_scenario_matrix_zero_missing` 随 8/8 绿锁定）。
- 真实风险如实记（不自行扩大修复）：`data/skills.json` 为运行时单文件 JSON 直写（skills 层既有形态，本步沿用 `upsert_lesson` 未改存储层）；`_related_chapters_note` 计分为字符 bigram 重叠，纯启发式指针（非语义召回），命中质量以评审链兜底——与提案「零 LLM 调用零存储写入」边界一致。

### 交第 4/4 步清单（闸②全量复核+闸⑤提交+闸⑥推送发版）

- 待提交文件：`app/core/pipeline.py`、`tests/test_full_type_round.py`、`docs/borrow-log/full-type-round.md`（本节）+ 前步调研沉淀三 docs（2026-10-06.md / current-round.md / knowledge.md，归属前步不混提）。
- 发版判断：本轮 pipeline.py 有代码入库（新函数+前情注入），按「当天有代码入库才发」候选 patch+1（v0.1.88 → v0.1.89，test_selfupdate 全绿前置）。

---

# 2026-10-07 09 时班（批2：学习记忆与自我改进——新一轮计划第 1/4 步全类型调研）

> 开工 09:38（UTC+8，hour=9，9%7=2 → 轮换批2）、分支 main（5afa213，v0.1.90 已发版）、
> 工作区干净（07 时落地班 i18n 54 词条已随 v0.1.90 入库）。通道：gh api 认证可用
> （主扫串行 sleep 4s、repos 端点复查 sleep 0.5s）、WebSearch 串行 1 发。
> **并入说明**：本文件系滚动累积报告；当日报告 docs/borrow-log/2026-10-07.md 已录
> 03/04/05/06/07 时班五节，本班（09 时）节追加于此，knowledge.md 本班节同步沉淀。

## 主扫描与批次判定

- **主扫**：`scripts/borrow_scan_nightly.py` 无参全量（自动选批 B2）——A 常驻 89
  （含内置 B1 11）+ B2 轮换 10 + A1u/A1p2 双轮 16 = **115 查询 524 行 467 唯一仓，
  exit 0、PROGRESS DONE 115 实证，零 403 零 FAIL**（唯一 grep「error」命中系描述
  文本非错误记录）。
- **主扫面过筛**（全名+简称双通道对 docs/borrow-log/ 全历史）：**330 已录 / 137
  首见**，首见以域外噪声与微型为主（课程/教程/游戏/域外工具）；**B2 域 10 词头部
  全已录**——NirDiamant/Agent_Memory_Techniques 1,087 / alibaizhanov/mengram 204 /
  MemTensor/MemRL 175 / ray-r-ren/agent-apprenticeship 1,617 / rohitg00/pro-workflow
  2,906 / K-Dense-AI/scientific-agent-skills 47,793 / colbymchenry/codegraph 73,347 /
  getzep/graphiti 31,498——**批2 域稳定期延续，零机制级新差量**（批3/6 域轮过均稳同型）。

## WebSearch 交叉验证（串行 1 发，3 新名 repos 端点二次实证全坐实）

| 仓 | stars/pushed/created | 机制亮点 | 对比 | 结论 | 日期 |
|---|---|---|---|---|---|
| EvoMap/awesome-agent-evolution | 234 / 10-05 / 2026-03 | agent 进化/记忆系统/多智能体架构/自我改进专属清单 | 批2 域专属地图第 3 张（与 TeleAI-UAGI 记忆域、TsinghuaC3I 论文集互补） | **C 源候选（批2 域专属清单），keywords.md 本班补入** | 2026-10-07 |
| IAAR-Shanghai/Awesome-AI-Memory | 1,257 / 10-06 / 2025-12 | AI 记忆知识库：长期记忆/推理/检索/memory-native 架构 | 同上，记忆域知识库面；WebSearch 报 1,218→repos 实测 1,257（+39 在动） | **C 源候选（批2 域），keywords.md 本班补入** | 2026-10-07 |
| AetherLabsAI/RSIAgent | 466 / 10-02 / 2026-09-13 | training-free 递归自我改进多智能体框架：Curriculum/Actor/Verifier 三 agent + 可复用持久记忆（broad-then-deep 探索） | 批2 域直接新面孔；「探索→验证→记忆沉淀」与经验召回/教训沉淀同向，但我们无 curriculum 型自主探索 | **批2 域判据件入库（雷达/判据）** | 2026-10-07 |

## trendshift 根页 29 仓（本班主要增量面；候选逐件 repos 实证）

- **autoharness（tigerless-labs）已录放量 +885：8,087→8,972**（trendshift 在榜驱动，
  昨日历班 7,906→8,013→8,071→8,087→今 8,972 加速）——「使用轨迹→SKILL 自动蒸馏+
  自动修剪失效」观察项信号显著增强（2026-10-06 队列项同族观察维持，本班增量入档）。
- **mattpocock/skills 278,184★ 属主勘定（悬置项销账）**：03/06 时班「mattpocock-skills
  属主悬置」实因仓名为 **skills** 非 mattpocock-skills——trendshift 在榜+repos 实证
  一次勘清（277,948→278,184 +236 放量续）。
- **新面孔 repos 实证 3 件**：**anthropics/knowledge-work-plugins 26,452★**（知识工作者
  插件官方仓——写作/文档/办公域 skill 生态对标面，B 专项域旁大件）/ **openai/math
  2,250★**（created 10-06 当日新建、无 description，OpenAI 官方 org，域旁判据）/
  **chengyi-ai/native-subtitle-quote-image 1,480★**（视频内嵌字幕取帧→3:4 社交长图
  Agent Skill，短视频/自媒体域 skill 生态件）。
- 已录复认：bifrost 8,588 / GetBusbar/busbar / rea 9,577 / Strata 15,802 /
  Compositor 9,182 / storytold 纯净室全家桶 6 件 / tester-army/e2e /
  Ebony-Vinyl/dsh-our-free-model / mattpocock/skills；huashu-art-motion 232→689
  （+457 放量，昨日 full-type-iteration.md 已录非新面孔）。
- 排除件（域外噪声如实记）：scholay/rimes（macOS 输入法）/ 34306/vphone-aio /
  mars-tw/anti-gambling-trader-tw / joshuaswarren/omarchy-apple-dev /
  Funny-Bones/ELDEN-RING / eternity4719/HowToLiveBetter / liweiyi88 双仓（03 时班已排除复见）。

## 雷达 C 全过（逐源证据）

| 源 | 通道 | 结果 |
|---|---|---|
| awesome 清单 18 源 | gh api repos 逐个（0.5s） | **18/18 alive 零 archived**（含本班新补 EvoMap/IAAR 双源候选实测在架）；ComposioHQ/awesome-claude-skills **76,604** 正主复认（wshobson 旧属主 404 系历史迁移勘清）；hesreallyhim/awesome-claude-code 55,166 / VoltAgent skills 35,281 / awesome-llm-apps 140,874 / awesome-mcp-servers 95,880 / harness-engineering 4,730 / buildwithclaude 3,601 / RUC-NLPIR 1,063 / TeleAI 658 / TsinghuaC3I 665 / VoltAgent papers 1,823 / Engineering4AI 288 / caramaschiHG 1,923 / vijaythecoder 4,388 / bradAGI 1,319 / e2b-dev 30,281 / vivy-yi 79 |
| Trending | trendshift.io 根页直抓（curl 327KB） | **29 仓全解析**（见上节；WebFetch 域名校验拦、curl 直抓成功） |
| topic 页 8 | gh api search topic: sort=stars per_page=5 | 首五位**全已录零新大件**（agent-swarm 861/synapse-ai 327/LoopTroop 159/agentic-os 189 微型复见；ECC/hermes/ponytail/nanobot/BettaFish/Reasonix/haystack/claude-mem/archify/oh-my-openagent/planning-with-files/agenticSeek/atlas/MonkeyCode 全已录） |
| 发行渠道 npm | npm search 两查 | 已录族为主（nax/agentcraft/bizar/tide-commander/coleo/opencode-oceanus/@deepseek-ai/dsh-hooks-claude-code rc.5 复见）；**新微型 3 件**：raycoder 1.0.0-rc.9（本地可恢复 coding-agent 编排）/garda-agent-orchestrator 1.4.3（带强制闸门的治理型本地运行时）/@cyberine/cli 0.2.3（多 provider 循环） |
| 发行渠道 pypi | curl 直抓 | **第 10 班复认受阻**：HTTP 200 但 3,038 字节 CSP 挑战壳页零 snippet——通道持续不可用如实记 |
| 框架周边搜 | 重合裁定 | langgraph/crewai/autogen 平台词与 B6 批全重合（06 时班刚跑 3 小时）——按「重合跳余页省配额」纪律不重跑；`q=openrig+OR+vercel+eve 生态` 已跑：eve 子串误中 every-* 族噪声大、零新生态移植件（openrig/eve 本体已录对标跟踪） |
| 自家 CLI 周边 7 查 | gh api search | 头部全已录族：cockpit-tools 18,670 / cc-switch 140,514 / kimi-cli 11,426+kimi-code 7,781 / CLIProxyAPI 54,351 / opencodex 17,023 / **awesome-dsh-plugin 17,921 + dsh-web 8,436（DSH 生态在录复认）** |
| 禅道周边 2 查 | gh api search | 头部大盘噪音+clickup-ai-bug-triage 0★ 复见——**第 24 例零新禅道 AI 竞品**（06 时班第 23 例顺延） |
| 新锐轮 | 主扫内置 A1u 8 组 | 首见以微型为主（detent 16★/reevesagents 88★/dsh-novel-forge 15★ 等，见下节） |

## 存量复查（repos 端点 48 件：39 件批量 + WebSearch/trendshift 实证 9 件；零 archived）

- **头部增量（vs 06 时班，间隔约 3 小时）**：orca **86,501→86,553（+52 续领跑）**/
  superpowers 296,003→296,028 / ECC 274,261→274,315 / claude-mem 97,130→97,200 /
  hermes-agent 251,684→251,714 / opencode 212,040→212,054 / pi 112,953→112,977 /
  ponytail 156,791→156,868。
- **放量族**：**autoharness 8,087→8,972（+885 最大放量，trendshift 驱动）**/ rea
  9,046→9,577（+531 放量续）/ diagram-design 43,908→44,062（+154）/ Strata
  15,650→15,802（+152）/ OpenMontage 64,627→64,702（+75）/ yomiyasu 1,594→1,621
  （+27 续）/ openrig 5,494→5,524（+30 续）/ huashu-art-motion 232→689。
- **持平族**：DeepSeek-Reasonix（esengine）35,741→35,742（E 候选首位维持）/
  webnovel-writer 7,335 / ainovel-cli 2,110 / huobao-drama 15,790 / hippo-memory 773 /
  beads 27,687 / agentmemory 29,185 / bernstein 1,416 / gascity 1,331 /
  nautilus-compass 1,255 / bifrost 8,588 / eve 5,475 / heym 1,404 / SkillSpector
  19,570 / open-code-review 44,055 / context-mode 25,550。
- **属主勘定批量与教训（如实记）**：本班首轮 repos 复查 9 件 404 **系试拼属主名失配
  （非属主迁移）**——按库内已录属主复测 11 件全 alive：hermes-agent=**NousResearch**
  （blackboxo 系误拼）/ hippo-memory=kitfunso / yomiyasu=nanaism / webnovel-writer=
  lingfengQAQ / ainovel-cli=voocel / huobao-drama=chatfire-AI / gascity=gastownhall /
  nautilus-compass=chunxiaoxx / bernstein=sipyourdrink-ltd / awesome-claude-skills=
  ComposioHQ。**教训：repos 复查应先从 knowledge.md 提取已录全名再探测，勿凭记忆拼
  属主**（in:name 勘定规则的前置步骤）。
- 观察项：letta（letta-ai）25,057 pushed 09-10 **停更近月**（记忆域老牌，观察）；
  mem0 66,701 在动。

## 本班新面孔定性（三门槛：重合度/可直读性/用户会搜吗）

| 仓 | stars | 来源 | 机制亮点 | 对比 | 结论 | 日期 |
|---|---|---|---|---|---|---|
| anthropics/knowledge-work-plugins | 26,452 | trendshift+repos | 知识工作者插件官方仓（Claude Cowork 用）：写作/文档/办公域 plugin 集 | B 专项域旁大件：非六源清单不可直装；其 plugin 划分方式（知识工作者任务域）与我们 18 类型流程同向 | **对标跟踪（B 专项域旁），交后续班深挖 plugin 清单** | 2026-10-07 |
| AetherLabsAI/RSIAgent | 466 | WebSearch→repos 实证 | training-free 递归自我改进：三 agent 协作+可复用持久记忆 | 批2 域直接新面孔；机制面经验召回/教训沉淀已有对应，curriculum 探索无对应但属研究向 | 判据（批2 域） | 2026-10-07 |
| EvoMap/awesome-agent-evolution + IAAR-Shanghai/Awesome-AI-Memory | 234+1,257 | WebSearch→repos 实证 | 批2 域专属清单/知识库（第 3、4 张域专属地图） | 与 TeleAI/TsinghuaC3I/VoltAgent papers 互补 | **keywords.md C 源本班补入（雷达源）** | 2026-10-07 |
| openai/math | 2,250 | trendshift+repos | OpenAI 官方新仓（created 10-06，无 description） | 域旁（数学/推理域，与编排台非同域） | 判据（域旁，观察后续定位明朗） | 2026-10-07 |
| chengyi-ai/native-subtitle-quote-image | 1,480 | trendshift+repos | 视频 字幕取帧→3:4 社交长图 Agent Skill | 短视频/自媒体域 skill 生态件；单用途技能非六源直装 | 判据（skill 生态） | 2026-10-07 |
| huangziyuan-general/dsh-novel-forge | 15 | A1u 新锐轮 | **DSH 小说锻炉**：AI 长篇写作通病→代码强制硬约束（事实账本/上下文包/阶段门禁/零费用去 AI 味扫描/确定性审计/提案制修订） | **DSH 生态扩散第 7 信号**（19 时班 dsh-mobile-connect 第 5、03 时班 dsh-our-free-model 第 6）+A7 小说域：事实账本≈story_tracking、上下文包≈_shrink、阶段门禁≈评审轮次——机制我们全有对应，其「去 AI 味扫描」与我方同类能力对账过 | 判据（DSH 生态+A7 域双信号，跟踪） | 2026-10-07 |
| digitaldrywood/detent | 16 | A1u 新锐轮 | board-driven agentic 工作编排，单 Go 二进制 | A1 域新锐微型 | 判据（微型） | 2026-10-07 |
| mertkayacs/reevesagents | 88 | 主扫 A13 | tmux 本地工作台多 AI 编码工具并排（CLI/TUI/Web 三形态） | openrig 同域微型（多 CLI 面板族第 N 例）；tmux 系 Windows 同款短板 | 判据（微型） | 2026-10-07 |
| npm 微型 3 件 | — | npm search | raycoder（可恢复编排）/garda-agent-orchestrator（强制闸门运行时）/@cyberine/cli（多 provider 循环） | npm 通道新生件惯例判据级 | 判据（npm 微型） | 2026-10-07 |

## 全类型覆盖矩阵（14 目标类型 × 本班证据通道）

| 类型 | 注册 id | 本班证据通道 |
|---|---|---|
| 直接执行 | direct | A1/A2 编排域（B2 主扫底座）+detent/reevesagents 微型判据 |
| 代码任务 | code | B1 内置 11 组（open-code-review 44,055/SkillSpector 19,570 复测） |
| 小说 | novel | A7 novel 组（webnovel-writer 7,335/ainovel-cli 2,110 复测持平）+dsh-novel-forge 判据 |
| 连载 | serial_novel | A7 consistency 组+A12 chapter hook（域沉寂零新面孔维持） |
| 自媒体文章 | article | A7 article 组（域平稳）+native-subtitle-quote-image 配图 skill 生态件 |
| 调研报告 | research | A10 deep research 组+IAAR/EvoMap 记忆知识库（调研记忆面） |
| 短视频脚本 | video_script | A10 video script 组+huashu-art-motion 689 放量复认 |
| 技术方案 | tech_proposal | A5 spec-driven 组+knowledge-work-plugins 文档域旁 |
| 翻译 | translation | A7/A10 translation 组（yomiyasu 1,621 放量续，已落地条无新差量） |
| 演讲稿 | speech | A7 speech 组（域平稳） |
| 工作汇报 | weekly_report | A10 weekly report 组（微型为主） |
| 商务邮件 | email | A10 email 组（AI-Based-Email-Generator 53★ 等微型，域长期微型） |
| 扫榜选材 | rank_scan | trendshift 29 仓+topic 8 页（本班扫榜通道即产出源） |
| 禅道工单 | defect_retro | A13 defect retro 组+禅道周边 2 查（第 24 例零新竞品） |
| 对话（补充） | — | A10 chatbot memory 组（B2 域头部 Agent_Memory_Techniques/mengram/MemRL 全已录复测） |
| 知识库（补充） | — | A2 agent+memory 组+IAAR/EvoMap 域专属地图+letta 停更观察+mem0 在动 |

「13 种」口径 vs 注册表实数：**BUILTIN_FLOWS=18 / DEFAULT_CATALOG=14 import 实测**
（direct/code/novel/serial_novel/article/video_script/doc/translation/rank_scan/
defect_retro/research/speech/presentation/weekly_report/email/tech_proposal/
resume/bid_doc）——需求列举 14 项全有对应注册 id、无遗漏无擅自省略，另含 doc/
resume/bid_doc 三自研类型，差异如实记录（与 03/04/06/07/16/19 时班同口径第 6 次
独立复证）。

## 轻量实测（本班只读，承调研班惯例）

- **E 新 CLI**：六候选 deepseek-reasonix/reasonix/fuxi/gitlawb/zero/empryo + openclaw
  `command -v` **全 MISSING**、dsh 在位（/d/nvm4w/nodejs/dsh）——零接入防死链维持；
  DeepSeek-Reasonix 35,742★ 候选首位持平。
- **D 经验库**：data/skills.json lessons=**68**（04 时班并类后新基线零回漂：流程规范
  28/节奏爽点 16/情节逻辑 10/人物塑造 7/一致性 4/文笔风格 3）、packs=3。本班蒸馏
  候选 1 条（**记录待第 3 步执行**，见下）。

## 落地提案（第 3/4 步确认清单，评审通过再动）

- **提案 1·D 专项蒸馏 1 条**（非代码）：{标题:「经验库同域竞品对照：autoharness 系
  『从真实会话自动蒸馏技能+自动修剪失效』，我方=手动沉淀+won/lost/useless 显式反馈
  +karma 时间衰减——『自动蒸馏』缺口维持同族观察，接入判断走三问（重合度/可直读
  性/用户会搜吗）」, scope:"*", category:流程规范, source:"borrow-log 2026-10-07"}。
  入口 skills.upsert_lesson（skills.py:421）；验收 lessons 68→69、闭集落类、标题
  查重零重复；回滚按 title 精确移除。
- **零代码件判定**：队列活项均攒批/拍板/远期（承 07 时巡检班口径）；本班新面孔均
  判据级/雷达级/C 源级，零接入级标的——docs-only 不发版。

## 待深挖队列（09 时快照）与未验证项

- 21 时快照 11 项全部维持（第 3/10 项保持划掉）；本班零新队列项——autoharness
  放量信号并入既有同族观察项（2026-10-06 在档），knowledge-work-plugins 交后续班
  深挖 plugin 清单（对标面非接入面）。风险在档维持（交人拍板）：data/zentao.json
  明文密码；批跑同进程测试间争用面；32 位全量 discover 静默退出；runner_drain
  计时超界。
- **未验证项（如实记录）**：openai/math 无 description 未深读（created 当日，定位
  未明）；knowledge-work-plugins 仅 repos 元数据未逐 plugin 清点；RSIAgent/IAAR/
  EvoMap 仅 README 级定性；npm 新生件无下载量核查（历班无此惯例）；pypi 通道持续
  受阻非本班可解；WebSearch 报 IAAR 1,218★ 与 repos 实测 1,257★ 差 39（时点差，
  以 repos 为准）；禅道定时扫描未真实触发验证（不为验证制造真实 Bug）。

## 词库同步与发版判定

- keywords.md C 源 awesome 清单补充行追加 EvoMap/awesome-agent-evolution +
  IAAR-Shanghai/Awesome-AI-Memory 双源（行尾标注谁/何时/为何）——批2 域专属地图
  补位（TsinghuaC3I 先例同款）。
- 本班 docs-only 零产品代码件——**不发版**（03/06 时等班先例）。

---

# 2026-10-07 10 时巡检班（新一轮第 2/4 步·七专项 A-G 实证+落地提案）

> 开工 10:01（UTC+8）、分支 main（5afa213，v0.1.90 已发版）。工作区在制品=第 1/4 步
> 09 时班调研三 docs（本班增量追加、逐字不动其既有内容）。只读巡检：零代码改动、
> 零数据写、禅道零触发、市场零装包。以下行号全部为本班 10:0x-10:1x 独立实读。

## A. token 节约（单列小节·八件机制+缓存调用方实证）

**检查方法**：grep 定位 + 逐段 Read pipeline.py:672-760/:2538-2612/:948-1058/:206-283/:1524-1534 + modelhub/usage/token_meter 锚点。

| 机制 | 真实实现（文件:行，本班实核） | 结果 |
|---|---|---|
| 三段压缩 | pipeline.py `_compaction_enabled(:231)` 默认关灰度；`_spawn_step(:672)` :703 压缩分支（撑爆→压缩→守门重试，resume 直通不压缩） | 在位 |
| token_meter | 压缩路径 :722-725 `_call` 内 accumulate + 直通路径 :740-746 补记（:738 注释「直通也必须回填 usage，否则预算表空转」）；cached 单列不计压力（token_meter.py:53-60 deque 五元组含 cached） | 在位 |
| 预算熔断 | 花费闸 `_cost_gate_block(:648)` 先于 token 闸（:676 注释「钱比 token 更早见顶」）；`_budget_max_tokens(:613)`；超限 ENV_BLOCK 只拦下一步 :694-701（当前步允许过线完成） | 在位 |
| cascade | :1526-1529 `cascade.enabled` opt-in → `capability.cascade_reorder`（difficulty/task_type/role 传参）；上游 `classify_difficulty(modelhub:3536)` | 在位 |
| 经验召回 | skills.block_for stable_order 双消费 :3191/:3657（注释「保前缀缓存字节级一致」）+ knowledge.block_for :3196/:3658 | 在位 |
| 会话复用 | `_valid_resume(:206)`（agent+session 双必需、mode=real 校验）→ `_resume_sid(:270)`（codex/claude/opencode/qwen 原生+generic resume_argv_template，mock 一律 None 防硬失败） | 在位 |
| diff 评审 | `_git_diff(:948)`（HEAD diff+未跟踪拼合）+ `_review_depth_note(:1019)`（<40 行快评省 token / ≥600 行先概览后深看）+ `_scope_note(:1054)` 越范围点名不改 pass 语义 | 在位 |
| _shrink 家族 | `_shrink_context_block(:2538)` 四层优先级（圣经>模块库>经验库>正文永不动，逐层返回止损）+ `_serial_shrunk_block(:2581)`（降级说明进 step note）；`_related_chapters_note(:2595)`（v0.1.89 落地件在位，bigram 计分常量 :2590-2592） | 在位 |

- **缓存调用方实数**：modelhub `chat(cache_ttl)(:3673)` 精确匹配响应缓存真实调用方 5 处——planner 4 处（:680/:829 attempt==1 才 3600s 重试幂等、:972/:1003 §07 T2.2 同任务幂等）+ main.py:1553 连通测试 24h。**机制无闲置**。
- `prompt_cache|semantic_cache|cache_control` 全 app/core grep 零命中维持（本班复跑）。
- **四方向判定（不重复建设）**：prompt 缓存=供应商侧能力（应用侧 stable_order 保前缀已是最大化）；语义缓存=队列候拍板；diff-only 评审满配；廉价分流 cascade+难度选模满配——**零新建**。远期备注维持：_shrink 仅连载路径挂接，review 引擎长文（标书/长报告）无同款降级（评审覆盖面属质量语义，交人拍板不扩展）。

## B. 插件市场（六源+闸门+knowledge-work-plugins 深挖）

- **六源在位**：market_remote.py SOURCES :50-76（zcode/anthropic/anthropic-skills/claude-skills/clawhub/cocoloop，镜像 URL 优先+回退序）原文实读。
- **缓存逐源实测 810**：zcode 26 / anthropic 315 / anthropic-skills 5 / claude-skills 99 / clawhub 215 / cocoloop 150（data/market_remote/*.json 逐文件解析，fetched_at 全 2026-10-03——与 01/04/16 时班零漂移；「拉取更新」系用户动作不代按）。
- **闸门实锚**：SSRF `assert_public_url(:113)` + 重定向逐跳重过（:146 redirect_request→:159 再校验）+ 体量上限 `_CAP_UNPACKED=120MB(:95)`；安装闸 `inspect_tree(:568)` 白名单外不参与（market.py:943 调用带 whitelist）+ 装前 `skill_scan.scan_summary`（market.py:281）+ 内容指纹突变对账（:289-293 注释「卸载重装也骗不过对账」）+ typosquatting 近名（:294）+ 装后冒烟（:340-357）。零绕闸零自动安装。
- **新面孔三问裁定**：09 时班移交的 **anthropics/knowledge-work-plugins（26,452★，本班 repos 实测 26,468 pushed 10-06）已完成 plugin 清单深挖**——根目录 20 域（marketing/productivity/finance/legal/sales 等）；marketing/skills 8 技能（brand-review/campaign-plan/competitive-brief/content-creation/draft-content/email-sequence/performance-report/seo-audit）；productivity/skills 任务管理族（memory-management/start/task-management/update）。**三问全不过**：①重合度中（写作/汇报/竞品简报工序同域）②可直读性否（非六源 marketplace 清单——anthropic 源 315 项内 grep 零命中，依赖 Cowork 宿主+MCP connectors）③用户不会在我方技能市场场景搜到——**跟踪不接入**；其「域→技能族」划分法与我方 18 类型流程同向，蒸馏 1 条进 D（见提案 1）。
- 禅道周边竞品：第 24 例后零新（承 09 时班，本班未重扫）。

## C. 任务类型（18 注册 × 14 指令场景映射实证）

- **注册表实数 18**（flows.py:36-129 逐条行号实读：direct:37/code:40/novel:43/serial_novel:49/article:56/video_script:62/doc:68/translation:74/rank_scan:80/defect_retro:83/research:86/speech:92/presentation:98/weekly_report:104/email:110/tech_proposal:116/resume:122/bid_doc:128）。
- **流程参数实证**（task_compile.py:156-199 整段实读）：轻量三类型 `light_types={email, weekly_report, translation}(:163)` 非 hard 免大纲单评审（:178-180）；深度三类型 `deep_types={novel, research, tech_proposal}(:164)` 大纲+双评审（:182-184）；threshold≥8.5 强制双评审（:194-195）；planner rec 可调但 clamp（reviewers≤2、rounds≤3）。
- **i18n 与守卫**：守卫测试本班复跑 **test_full_type_round 8/8 OK + test_i18n_dups 3/3 OK**（TUTTI_DATA 隔离净进程）——18 类型×三字段 EN 映射程序化锁定零缺失。
- 用户 14 场景映射零缺失（第 7 次独立复证，与 03/04/06/07/09 时班同口径）。

## D. 经验库（数据卫生体检+04 时班处置件回访）

- **存量实测**（python 直读 data/skills.json）：lessons **68**、packs=3——与 09 时班「并类后新基线」零漂移；category 分布 流程规范 28（41%）/节奏爽点 16（24%）/情节逻辑 10（15%）/人物塑造 7（10%）/一致性 4（6%）/文笔风格 3（4%）；标题完全重复 **0**；scope 视角 serial_novel 41/code 11/* 11/direct 4/article 1（scope 偏科维持既有记录，迁移留人工）。
- **04 时班处置件回访全过**：分类错位件 sk-8114e5d9a90a（「做法：小红书…」）已落 流程规范（scope=article、seen=2）；`merged_titles` 留痕 8 条——近重复聚簇 3 组 8 条并类已执行且历史可溯，零回漂。
- **写入入口实锚**：`skills.upsert_lesson`（skills.py:421 起，包含度 ≥0.8 自动合并+闭集分类）；本班零写库（蒸馏候选记录待第 3 步）。

## E. 新 CLI 接入（catalog 逐条+本机逐个探测）

- **DEFAULT_CATALOG=14**（catalog.py AST 实数+逐条 name/detect 实读）：codex/claude/opencode/qwen/aider/openclaw/kimi/mimo/grok/pi/dsh/gemini/cbc/trae-cli。
- **本机在装 13/14**（command -v 逐个实测；openclaw MISSING，与 01/04 时班口径一致）；codebuddy 条目探测名 cbc FOUND、deepseek-harness 探测名 dsh FOUND。
- **六候选探测**：reasonix/deepseek-reasonix/fuxi/gitlawb/zero/empryo `command -v` **全 MISSING**——未装不实测不接入防死链维持；DeepSeek-Reasonix 35,742★ 候选首位持平（09 时班 repos 实测）。接入打法（装→which→裸调→bind_agent 冒烟→catalog upsert）不变，本班零候选达标。

## F. 禅道集成（只读巡检，零写回零触发）

- **配置态**（data/zentao.json 直读，凭据字段全程不外播）：poll_enabled=**false**（config 内层显式关闭，13 时班勘定口径本班复证）；product 96 档案完整——our_sides=[backend]、owners backend/frontend 双侧、severity_cap=0；路由目标 backend workdir `E:\GitLab\cbc\mo-so` **ls 实存**（档案路由非死链）；`triage_ai=true`（模块路由未命中时 AI 兜底排查，zentao.py:24/:121/:1498）。
- **扫描状态**：claims=**0** 零积压、last_error **空**、last_scan 停 2026-09-21 20:43（poll 从未启动所致非漂移）；next_scan 同步停走。定时扫描未真实触发验证（不为验证制造真实 Bug，维持历班纪律）。
- **链路实锚**：`fire_due(:2470)`（:2474 poll_enabled 闸）← automation tick；`scan_now(:2479)` 手动；`start(:2496)` ← main.py:3438；手动端点 main.py:1779 `/api/zentao/scan`；设置页禅道子页动作 **6 件**（app.js:1012-1017 测试连接/拉产品/拉账号/拉模块/扫描/保存——较 16 时班「五件」多出「拉取模块清单」，app.js:11871 modules 端点供路由用，演进非缺陷）。
- 风险在档维持：明文密码（凭据保险库队列项，交人拍板）；poll 未开启系部署决策不代开。

## G. 产品巡检（UI 文案/链接/描述-行为一致性）

- 过时文案 grep（「13 种/14 种/16 种/17 种/单源」）i18n.js/index.html/app.js/README **零命中**；README.md:135「**18 种任务类型**」与注册表实数 18 一致。
- rank_scan 四平台口径本班复认一致（flows.py:82 note ↔ paihang.py:5-11 四源实测注释：七猫 qimao/番茄 fanqie/起点 m.qidian/纵横 zongheng）。
- 版本一致：package.json=0.1.90 ↔ CHANGELOG v0.1.90 顶部节（「## 未发布」空节系发版流程占位惯例非缺陷）。
- **结果：零新毛病，零改动。**

## 落地提案（第 3/4 步确认清单，评审通过再动）

### 提案 1：D 专项蒸馏 1 条（非代码）

- **需求**：09 时班移交的 knowledge-work-plugins plugin 清单深挖本班完成（B 专项）——官方生态按「域→技能族」组织（marketing 8 技能含 email-sequence/seo-audit/competitive-brief），与我方 18 类型流程参数同向；对标方法目前只在报告层，经验库注入面没有。
- **真实入口**：`skills.upsert_lesson`（skills.py:421 起）。
- **拟写条目**：{标题:「技能生态对标口径：官方知识工作者插件仓按『域→技能族』组织（marketing 8 技能/per productivity 任务管理族），对标取其域内工序划分对照我方 18 类型流程参数——整仓不接入（非六源清单、依赖 Cowork 宿主，三问不过）」, scope:"*", category:流程规范, source:"borrow-log 2026-10-07"}。
- **验收**：lessons 68→69、category 落闭集、标题查重零重复、seen=1 不分裂；回滚按 title 精确移除无联动。

### 提案 2：零代码件判定（代码级小而实积压核对为零）

- **核对过程如实记**：①A 专项 `_shrink` 扩 review 长文=评审覆盖面质量语义交拍板（16 时班口径维持）；②附录面实核 pipeline.py 仅 RESEARCH_APPENDIX(:2356)/TRANSLATION_APPENDIX(:2381) 两件，消费点 :4909/:4912 类型分发清晰，article/weekly_report 无附录系无证据支撑的伪缺口（不自造需求）；③modelhub cache_ttl 5 调用方全幂等场景，无闲置面；④B 专项零六源可直装新面孔；⑤D 专项 04 时班并类件已清账。队列活项均攒批/拍板/远期（语义缓存/RAG/凭据保险库/自动蒸馏观察）——**本班 docs-only 不发版**（03/06/09 时班先例）。

## 本班纪律对账

- 零代码改动；改动面仅本节两 docs 追加（full-type-round.md/knowledge.md），第 1/4 步在制三 docs 未触碰。
- F 专项全程只读零触发（含未探测实例可达性）；E 专项六候选零安装；B 专项零装包零绕闸；D 专项零写库。
- 守卫测试经 TUTTI_DATA 隔离净进程复跑（8/8+3/3），未写真实经验库；文档读写走 Write/Edit 通道（10-04 heredoc 偏差未复发）。

---

# 2026-10-07 第 3/4 步落地实录（提案 1 实施，提案 2 零代码判定维持）

> 承上节确认清单：仅实施提案 1（D 专项蒸馏 1 条入经验库）；提案 2 为「零代码件
> 判定」（代码级小而实积压为零），flows.py/pipeline.py/app.js 无候选面、零改动——
> 三个候选源码路径逐一核对后未触碰，无并行写入冲突（改前重读）。

## 实际改动路径

| 文件 | 改动 | 提案锚点核对 |
|---|---|---|
| `data/skills.json` | lessons **68→69**：新增「技能生态对标：官方插件仓按域→技能族组织，对标取域内工序划分对照我方 18 类型流程参数——整仓不接入（三问不过）」（scope=\*、category=流程规范、seen=1、source=borrow-log 2026-10-07），经 `skills.upsert_lesson`（skills.py:421）API 落库 | 提案 1 验收全过：闭集落类、标题查重零重复（含近似题零命中）、seen=1 不分裂、标题 57 字未被 60 字截断；提案标题「官方知识工作者插件仓」精简为「官方插件仓」以避开截断——细节（marketing 8 技能清单/Cowork 宿主）移入正文（1200 字上限内）。运行时数据不入库（data/ gitignore） |
| `tests/test_borrow_round_regressions.py` | +1 类 `DistillWriteRegressionTests` 4 用例：验收口径（闭集落位/首写 seen=1/库内恰一条）、复查再沉淀合并不分裂（seen+1 取新内容）、空题/空正文拒写边界、未涉及面不退化（既有教训分类/seen/题原样+18 类型注册表实数与 id 集不变） | 文件系 10-05/10-06 轮既有守卫（22→26 用例），只追加不动既有；用例名全库唯一（grep 核过） |
| `docs/borrow-log/full-type-round.md` | 本节 | 归属本步 |

- **零产品代码改动**：flows.py/pipeline.py/app.js/i18n.js/index.html/style.css 未触碰 → `node --check` 不适用、`tests/ui_full_type_round.mjs` 不建（规格条件「若改动浏览器交互」未触发）。
- 一处测试自身修正如实记：`_distill` 助手初版用 `title or TITLE` 回退，空串入参被默认值吞掉致边界用例假红——改 `None` 哨兵区分「未传」与「显式空」，实现（upsert_lesson 空输入拒写）行为正确未动。

## 测试命令与结果（本步实跑）

| 命令 | 结果 |
|---|---|
| `python -m py_compile tests/test_borrow_round_regressions.py` | 过 |
| `python -m unittest discover -s tests -p "test_borrow_round_regressions.py"` | **22/22 OK**（原 18 + 新 4；第 4/4 步实跑勘正，初记 26/26 系笔误） |
| `python -m unittest discover -s tests -p "test_full_type_round.py"` | **8/8 OK** |
| `python -m unittest discover -s tests -p "test_i18n_dups.py"` | **3/3 OK** |
| `python -m unittest discover -s tests -p "test_content_contracts.py"` | **9/9 OK**（未涉及任务类型零回归） |
| `git diff \| grep -E "pick_dialog\|ask_directory\|backoff"`（闸④预扫） | 零命中 |
| 改动文件 UTF-8 无 BOM 逐字节校验 | 过 |

## 实际收益与交第 4/4 步清单

- **收益**：knowledge-work-plugins 对标方法从报告层进经验库注入面（scope=\* 全类型可见），后续调研班按「域→技能族」口径对标 18 类型流程参数时可直接召回；整仓不接入的裁定理由（非六源清单/Cowork 宿主依赖/三问不过）随条目沉淀，防后续班重复深挖。
- **待提交文件**：`tests/test_borrow_round_regressions.py`（既有文件追加，非新文件，无需 -f）+ 三 docs（full-type-round.md / knowledge.md / keywords.md，第 1/2 步在制沉淀与本节）。
- **发版判断**：本轮**零产品代码入库**（仅测试+文档+运行时数据）——按「当天有代码入库才发」规则**不发版**（03/06/09/10 时班 docs-only 先例），v0.1.90 维持最新。
- 闸②全量 discover 与闸⑤/⑥提交推送留第 4/4 步执行。

---

# 2026-10-07 第 4/4 步联调收尾实录（五道关+提交推送+不发版裁定）

> 分支 main 复核在位（⓪ 过，未受并行代理切枝影响）。工作区 4 文件与第 3/4 步
> 交接清单逐一对上：三 docs + tests/test_borrow_round_regressions.py，零外来。

## 闸① 静态检查

- `python -m py_compile tests/test_borrow_round_regressions.py` 过；本轮零 JS
  改动，`node --check` 不适用；三 docs UTF-8 可读（Write/Edit 通道写入）。

## 闸② 全量测试——全量 discover 静默退出在案复现，按历班先例分片对账

- 全量 `python -m unittest discover -s tests` 两跑（重定向落盘+分流 stderr）
  均**中途硬崩无 Ran 汇总行、退出码掩成 0**，崩点 traceback 同为
  `RuntimeError: private worker path`——18 时班「静默退出元凶
  test_serial_draft_forbidden 中途硬崩、退出码掩 0」在案风险**本班再度实证**，
  该路径本机不可作闸②证据。
- **分片对账**（历班 18 时班/03-03 班先例）：266 模块分 8 批逐模块独立进程
  discover——**2208 项、263 模块 OK、唯一 FAILED 件 test_http_500_guard
  3 例**。净进程复跑该模块 **4/4 OK**、test_serial_draft_forbidden 单跑
  2/2 OK（6.5s）——失败定性为分片与全量第二跑并行时的 data 目录/端口争用
  （03-03 班「批内偶发三重证据定性非本班」同款），非本轮回归（本轮零
  产品代码改动）。**闸②判过：分片对账 2208 项有效全绿。**
- 顺带勘正：第 3/4 步节初记「26/26（原 22+新 4）」系笔误，实跑
  **22/22（原 18+新 4）**，本步已改。

## 闸③⑤ 逐 hunk 审与范围核对

- 三 docs hunk 逐一读毕：full-type-round.md 三节（09 时班调研/第 3 步落地/
  本节）、knowledge.md 两节（09 时班+10 时巡检班）格式随库内惯例
  （| 分类 | 日期 | 尾注）、keywords.md 一行补 2 个 C 源（批2 域专属地图
  第 3/4 张）；测试 +65 行为第 3 步已评审的 4 用例，无残留调试句。
- 外来标记扫描：`git diff` 中 pick_dialog/ask_directory/backoff 仅命中
  本报告自述闸④命令的文字一处，无真实代码标记。

## 提交与推送

- 只暂存本轮 4 文件（tests 文件系既有跟踪文件，普通 add）。**发版裁定维持：
  本轮零产品代码入库（app/ 零 diff），docs-only 不发版（03/06/09/10 时班
  先例），v0.1.90 维持最新**——test_selfupdate 未跑（无发版动作）。
- **补记（本节随主体提交后补录）**：主体提交 **78b56a7**（4 文件 +532/-1，
  git show --stat 核对一致、工作区随即干净、零踩踏零恢复提交）；
  `git push` 一次成功（5afa213..78b56a7，无 443 抖动、远端无领先无需 pull）；
  本补录节为独立小提交随推。

## 第 4/4 步独立复核（第二会话交叉验证，10:5x-11:0x）

> 并行班提交期间另一会话独立进场复核：起手快照仍为 5afa213+4 文件未提交，
> 巡检途中并行班完成 78b56a7+24a735c 两提交——零踩踏（本会话只读+改
> docstring 一处，改前 Read 无并发写同 hunk）。以下全部本会话独立实证。

- **推送声明核实（离线证据，不依赖当刻网络）**：`git reflog show origin/main`
  ——78b56a7 于 10:51:13、24a735c 于 10:51:45 两次 `update by push`，
  本地 origin/main=24a735c 与 HEAD 持平零分叉；本会话 fetch 虽两遇 443
  拒连（21s 超时×2），但 reflog 系 push 成功时本地落账，**「推送一次成功」
  声明属实**。
- **闸②交叉验证**：本会话独立跑全量 `discover -s tests` 亦复现
  `RuntimeError: private worker path` 硬崩、无 Ran 汇总、退出码掩 0
  （32 位 3.8.6 在案风险再实证）——并行班「分片对账 2208 项」的替代口径
  必要性成立。净进程抽查 5 模块全绿：test_http_500_guard（分片争用唯一
  FAILED 件）复跑 OK、test_full_type_round / test_i18n_dups /
  test_content_contracts OK、test_borrow_round_regressions **22/22 OK**。
  争用定性独立成立，零回归。
- **增量改动（本会话）**：tests/test_borrow_round_regressions.py 模块
  docstring 索引补第 4 条（蒸馏写入路径，文件头惯例 1-4 条齐全）+本节；
  py_compile+22/22 复跑过。**发版裁定复核维持**：npm `files` 不含 tests/
  与 docs/，本轮入库对发布产物字节零影响，v0.1.90 维持最新，不发版。
- **纪律偏差如实记**：knowledge.md 复核沉淀一度走 Bash heredoc 直写
  （10-04 偏差复发一次）——事后 python 逐字节校验 UTF-8 无 BOM 完好、
  内容正确，未造成数据损伤；后续文档仍归 Write/Edit 通道。

