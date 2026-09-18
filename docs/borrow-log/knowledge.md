# 竞品模式库（knowledge.md）

> 每轮调研深挖的项目在此沉淀：项目 | 亮点机制 | 与 CodeBee 对比 | 结论 | 上次调研日期。
> **已沉淀项目每轮仍需复查**：老项目会更新——看近期 commits/releases/CHANGELOG 相对
> 上次调研日的增量，有新机制就吸收更新；确实没变化写一行「YYYY-MM-DD 复查无增量」。
> 知识库的作用是知道查过什么、从哪续查，不是免查金牌。新条目追加在文件尾部。
> 结论取值：借鉴(进路线图) / 已落地 / 已覆盖 / 不适用 / 参考。

## token 节约机制专项（每轮必查维度）

目标：帮 CodeBee 用户省 token。已有机制（对照基准）：
- 三段上下文压缩（compaction.py：工具结果剪枝→LLM 摘要→surface replace）
- token_meter 压力表 + 换将超窗预检（step_runner 0.9 事前门）
- 单次运行预算熔断（budget.max_tokens_per_run）
- cascade 级联路由（easy 任务按 tier 升序走廉价模型）
- 经验召回免重读（skills.relevance_top）
- CLI 会话复用（免重发前缀）+ 连载前情提要

待调研方向：prompt 缓存显式利用（cache 断点/热缓存不压缩）、语义结果缓存、
diff-only 评审、工具结果去重、精简输出协议、更细粒度廉价模型分流。

## 2026-09-18 第三轮（调研日 2026-09-18）

## 2026-09-18 第三轮

- **omnigent**（omnigent-ai/omnigent，10.1k★）| 编排多 CLI 的元壳：每步前按「本步将用模型」容量重估并压缩、跨壳任务交接、聚合看板、pre-run 成本预估 | 换将超窗预检与成本预估已抄；跨壳交接≈我们的换将链；看板≈蜂巢 | 已落地/已覆盖
- **agent-orchestrator**（Untrivial-ai，12.1k★）| planning→merge 全程监督、.spec/PROMPT.md 任务规格文件化、计划评审闸 | spec 文件化未抄（路线图）；计划闸≈编排者+待裁决 | 部分借鉴
- **oh-my-claudecode**（39.2k★）| 团队化编排、安全围栏、自学习沉淀、PR 工作流、doctor 健康诊断 | 大多有对应物（经验库=自学习、diagnostics=doctor、评审闸=围栏） | 已覆盖
- **munder-difflin**（7.5k★）| 同任务 N 克隆并行+评审择优+每任务 token 上限；LanceDB 向量经验检索 | 赛马已抄（连载+单稿）；预算熔断已有；向量检索未抄（重依赖，暂缓） | 已落地
- **freebuff**（CodebuffAI/freebuff，12.3k★）| 每步按本步模型容量重估、缓存感知压缩、suggest_followups、best-of-n 多策略+败者精华回收、专职子 agent 分工（thinker/researcher-web/file-explorer 家族）| 预检/追问卡/赛马精华已抄；专职子 agent 分工未抄（路线图候选）；缓存感知按设计不需要 | 已落地/部分
- **emdash**（5.8k★）| 并行编码 agent + worktree 隔离 + 外部集成面 | 隔离链已有；外部集成抄了 Webhook 思路 | 已覆盖
- **edict**（cft0808，16.9k★）| 三省六部制分角色治理 + 实时看板 + 多模型 | 治理隐喻可参考；看板=蜂巢 | 参考
- **grill-me-skill**（RobMitt，610★）| 需求拷问：一次一问、每题多选弹窗、能自答绝不问用户、沿决策树逐分支到达共识、收尾汇总决策 | 三问向导是固定题序，此为自适应树状追问升级；追问芯片机制现成可复用 | 借鉴（路线图：需求拷问模式）
- **grill-for-unknowns**（nicobailon，219★）| 先找未知项、再拷问计划、达成实现前共识 | 与 grill-me 合并借鉴 | 借鉴（同上）

## 前两轮（详见当日报告）

- **Baton / Codeband / CodeCrew** | 隔离链、待裁决徽章、跨族评审硬规则、章节实时预览、bee.py CLI、README 小队叙事 | 全部已落地（第一轮清单①-⑥）
- **第二轮 GitHub 全景** | 采访式向导（三问一确认）等 | 全部已落地

## 检索经验（方法论沉淀）

- 只搜 2-3 组关键词会漏大量同类项目（2026-09-18 用户指正）：必须 ≥12 组关键词（含 token 节约专项组）+ sort=stars/updated 双轮 + created:>近半年新锐轮 + GitHub topic 页 + awesome 清单顺藤摸瓜 + Trending
- skill 生态（topic:claude-skills、skills marketplace）是独立借鉴源：好 SKILL 的方法论蒸馏进经验库/内置技能，不限于代码功能
- 已沉淀项目不等于免查：每轮复查增量，老项目的新版本常带新机制

## 2026-09-18 傍晚试跑补充（调研日 2026-09-18）

- **orca**（stablyai，71.5k★）| 并行 agent 舰队的 ADE | 待深挖（本轮最大遗漏，规模超过此前所有已调研项目）| 待深挖
- **Whale**（usewhale，928★）| DeepSeek 终端 agent，~98% prompt 缓存命中 | 缓存利用路线的标杆案例 | 借鉴方向（token 专项）
- **LLMLingua**（microsoft，6.7k★）| prompt 压缩（剪枝/替换降 token）| 可用于长上下文注入前的压缩；重依赖慎接 | 参考
- **prompt-cache**（messkan，409★）| 语义缓存，降本 80% | 语义缓存=同义请求直接回缓存 | 借鉴方向（token 专项）
- **claude-code-cache-fix**（433★）| 缓存回归致 20x 成本的真实案例 | 印证「缓存破坏=真金白银」；我们压缩时机设计已避开 | 已覆盖（设计层面）
- **token-goat**（121★）| token 燃烧抑制器 | 思路参考 | 参考
- **sandcastle**（mattpocock，8k★）| TS 沙箱编排 coding agents | 沙箱隔离与我们 CLI 沙箱思路互补 | 参考
- **lazycodex**（3.5k★）| 复杂代码库的项目记忆+规划 | 项目记忆≈经验库+圣经，规划≈编排者 | 待深挖

### 复查记录
- 2026-09-18 傍晚：freebuff/omnigent/agent-orchestrator/oh-my-claudecode/munder-difflin 全部活跃（push 09-17/18，star 微增），未做逐 commit 增量——下轮补
- 2026-09-18 傍晚：grill-me-skill 无变化（push 2026-04-11）

## 2026-09-18 夜·扩组第二轮（调研日 2026-09-18）

- **orca**（stablyai，71.5k★）已深挖 | 桌面 ADE：手机伴侣 app（跑完通知+随时追话）、并行 worktree（一个 prompt 扇出 5 agent 各自 worktree，比完合并胜者）、Ghostty 级终端 | 手机伴侣≈我们 Tailscale 远程访问+通知；worktree 择优=Best-of-N 工程版（我们的赛马在内容引擎，代码引擎可借鉴 worktree 版）| 借鉴方向：代码任务 worktree 版 Best-of-N + 移动端体验
- **gsd-pi**（open-gsd，1.2k★，gsd-2 7.8k★ 的继任）已深挖 | spec-driven：milestones→slices→tasks 三层结构、.gsd/ 本地项目记忆（需求/决策/计划/验证证据）、AUTO 状态条显示当前派发模型、hermes 停滞会话通知、消息保留模型路由溯源 | 三层结构≈编排者拆解；项目记忆≈经验库+圣经；停滞通知已有看门狗；**已抄：步骤卡模型路由溯源徽章**；差量：验证证据持久化（.gsd 的 validation evidence）| 已落地（溯源徽章）+ 借鉴方向（验证证据）
- **trycua/cua**（23.1k★）| computer-use 2.0：开源驱动+跨 OS 舰队+基准 | 我们无 GUI 操控能力；建书资料抓取是 CDP 定制实现 | 不适用（暂）
- **agent-os**（buildermethods，5.4k★）| 注入代码库标准+写更好 spec | spec 路线参考 | 待深挖
- **conductor**（gemini-cli-extensions，3.7k★）| spec-driven 开发插件 | 轻量 spec 工作流参考 | 待深挖
- **moai-adk**（modu-ai，1.2k★）| SPEC plan/run/sync + TRUST 分层 | spec+信任分层参考 | 待深挖
- **OpenSandbox**（opensandbox-group，15.4k★）| 安全快速可扩展的 agent 沙箱运行时 | 我们的沙箱=CLI 自带；浏览器沙箱场景不同 | 参考
- **helix**（helixml，809★）| 私有 agent 舰队+spec coding，每 agent 独享 GPU | 私有化部署路线 | 参考
- **SWE-AF**（Agent-Field，1k★）| 自主软件工程舰队产生产级 PR | 类似 agent-orchestrator | 待深挖

### 复查记录（逐 commit）
- 2026-09-18 夜：orca 近期=工程化收尾（macOS 差分客户端加固/移动端 UI 重做/多 PR 行折行/UNSTABLE PR 可合并策略）——移动端与 PR 流成熟中；freebuff=私仓高频快照（每天 10+ 次 sync，无公开语义增量）；agent-orchestrator 当日 7 commit 均工程修复
