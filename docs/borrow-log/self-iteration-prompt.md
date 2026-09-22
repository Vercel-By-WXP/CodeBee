# CodeBee 自我迭代·常备提示词

> 由 ZCode 每 2 小时执行一轮（2026-09-23 起执行通道从 CodeBee 内部编排切到 ZCode）。
> 原文来自定时任务 auto-20260921-135622-8720 的 prompt 字段，一字未改，供执行方直接读取。

---

工作目录 E:\GoOut\MultiAgentOrchestration（CodeBee 多智能体编排台，Python 3.8 后端 + 原生 JS 前端）。执行一轮「全类型竞品调研 → 对比学习 → 七专项巡检优化 → 落地 → 全面自检 → 提交推送 → 自动发版 → 沉淀」，全程中文。总方针：多学习、多沉淀、多优化——好功能抄过来改造优化，评审合格才提交，提交后推送。**覆盖全部预置任务类型**（直接执行/代码/小说/连载/自媒体文章/调研报告/短视频脚本/技术方案/翻译/演讲稿/工作汇报/商务邮件/扫榜选材/禅道工单）——不只针对小说，代码、对话、文档、翻译、知识库等全部类型都要搜到、优化到。

1) 调研：**先读 docs/borrow-log/keywords.md 关键词总库（130 组+雷达源）**，严格按其规则执行：
- **词组选择**：A1-A10 常驻组全跑（约 50 组）；轮换池 B 按当前小时数对 7 取模选对应批（08 点→1，10 点→3……轮着跑不同批）
- 常驻组：核心编排 8/扩展形态 7/token 节约 4/新 CLI 3/舰队形态 4/评测观测协作 6/**写作场景 8（novel+article+speech+translation+consistency）**/prompt 网关质量 5/**全类型写作对话代码 10（email/weekly report/video script/translation quality/chatbot memory/code gen/code refactor/doc gen/presentation/deep research）**/产品配套 6
- 雷达源 C 全过（awesome 清单 10 个+Trending+topic 页 8 个+npm/pypi+框架周边+自家 CLI 周边+禅道/项目管理周边）
- 纪律：双轮排序（stars+updated/新锐）；GitHub API 限流 10 次/分 sleep 6-8 秒；结果重合跳余页

2) 对比学习 + 沉淀：维护 knowledge.md（亮点|对比|结论|上次调研日期），已沉淀项目每轮复查增量。读全部历史报告/knowledge.md/keywords.md。只认「他们有、我们没有、且确实好用」。**关注每个预置任务类型的竞品**：代码任务看 code review/test agent，小说任务看 writing/story agent，文章任务看 article/blog agent，翻译任务看 translation agent，对话任务看 chatbot memory agent——全类型雷达。

3) 七专项巡检（每轮必做）：
A. token 节约：对比已有（三段压缩/token_meter/预算熔断/cascade/经验召回/会话复用/diff 评审/_shrink_context_block 分层降级）；方向：prompt 缓存、语义缓存、diff-only 评审、廉价模型分流。报告单列小节
B. 插件市场：扫 CodeBee 六源市场（app/core/market*.py）与外部 skill 生态，挑高质量不重复的走既有通道接入（白名单闸门+SSRF 防护，装前看内容质量）；不适合装的蒸馏方法论进经验库。**接入判断三问**（重合度/可直读性/用户会搜吗）回答不好就不接，雷达跟踪即可
C. 任务类型：巡检**全部 13 种预置类型**的菜单描述/流程参数/辅助信息——不只扫榜/连载，代码/翻译/演讲/邮件/汇报/短视频/技术方案全要看
D. 经验库：去重/合并/分类准确性（已知偏科：流程规范 61%），蒸馏本轮调研方法论入库（好 SKILL 精华也走这条）
E. 新 CLI 接入：对照 catalog.py（已接 11 个）+ 本机 which 探测；候选 DeepSeek-Reasonix/FuXi/Gitlawb/zero/Empryo（本机未装待实测，不盲目录接入防死链）；接入打法见 knowledge.md
F. 禅道集成巡检：**禅道已接入**（app/core/zentao.py：定时扫描激活 Bug→自动建 code 修复任务→合并+resolve+评论回写+群通知；设置页禅道子页）——每轮检查禅道定时扫描是否正常、有没有新 Bug 积压、产品档案路由是否准确、有新禅道 AI 集成竞品就入库
G. 产品巡检：发现的 UI/流程小毛病随手修（文案过时/断链/描述与实际不符——如双源落地后菜单仍写单源这类）

4) 落地：从路线图积压挑 1~2 个「小而实」。纪律：改前 Read（并行代理写入防冲突）；style.css 追加尾；Write/Edit（Mimosa 禁 Bash 直写）；新增测试放 tests/（gitignore，提交 git add -f）；grep 重名；临时服务随机高位端口+TUTTI_DATA

5) 提交前五道关全过：
⓪ git branch --show-current 必须 main（并行代理会切 feature 分支；在 feature 分支不 checkout，git push origin HEAD:main）
① py_compile + node --check（各自改动文件）
② unittest discover 全量全绿（约 3-4 分钟）
③ 逐 hunk 自审（惯例/边界/i18n/测试覆盖/无残留）
④ 只 add 自己文件（新测试 -f）；git diff --cached 扫外来标记（pick_dialog/ask_directory/backoff）
⑤ commit 后 git show --stat HEAD 核对；踩踏=工作区完整态补恢复提交

6) 推送与发版：git push（443 抖动 sleep 15-30 重试 ≤2）；远端领先先 fetch+log 核对后 pull --no-rebase。发版（仅当天有代码入库才发）：test_selfupdate 全绿 → package.json patch+1 → CHANGELOG 顶部追加 → git push → npm publish → npm view codebee version 核对。失败 revert 并报告。

7) 沉淀：更新当日 docs/borrow-log/ 报告与 knowledge.md（新条目+复查+待深挖队列）；keywords.md 有调整同步。改前 Read 冲突重读。

8) 最后给用户 ≤10 行中文总结。
