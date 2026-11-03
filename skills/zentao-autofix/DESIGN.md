# 设计说明 · zentao-autofix

## 1. 核心判断：为什么是 CLI，不是提示词

同类工具常见的做法是把「怎么分析 bug、怎么定位、怎么记录」全写进 prompt。
问题是：提示词是**概率性**的。同一段提示在 Codex 上跑出来的流程和 Claude Code 上不一样，
隔一周再跑又不一样。这样的工具没法承担「质量守门」的职责。

所以这里的分工是：

| 层 | 内容 | 特性 |
| --- | --- | --- |
| 确定性层（`zfix` CLI） | 拉取、归一化、指纹、定位、跑命令、存档案、扫 diff | 可单测、可重复、可进 CI |
| 概率层（AI） | 读懂上下文、判断根因、写出修补、写清解释 | 换模型/换平台只影响这一层 |
| 薄壳层（`SKILL.md`） | 流程约束、红线、命令表 | 每家平台一份，内容同源 |

判据很简单：**能写成 if/else 的，一律不进 prompt。**
AI 只做「需要理解力」的那一段，其余全部下沉。这样换平台时行为不漂移。

## 2. 流水线（九段）

```
intake → triage → locate → context → fix(AI) → verify → record → learn → prevent
```

1. **intake**（`sync`）：适配器把任意缺陷源归一化成 `Bug`。
   适配器只实现三个方法：`health()` / `fetch()` / `normalize()`。
2. **triage**（`triage`）：算指纹、判根因、定可修性（auto/manual/skip）、按指纹去重。
   `skip` 不是失败，是「不值得花 AI 一次调用」——已闭环、描述不足、重复项都在这里挡掉。
3. **locate**（`locate`）：三路信号打分（堆栈帧 100 分 > 符号 25~40 分 > 关键词 10 分）。
4. **context**（`context`）：把上面所有结论 + 历史经验 + 硬约束 + 验收命令压成一页 Markdown。
   **这是整个系统的核心交付物**——平台无关的可读中间格式，任何 AI 读同一份文件得到同一套依据。
5. **fix**：由 AI 执行。CLI 不写业务代码，也就不承担「AI 改错」的责任边界。
6. **verify**（`verify`）：按配方跑 lint → typecheck → test，短路在第一个红灯。
7. **record**（`record`）：归档根因。**这是知识库唯一的写入入口**，也是全系统最容易被人糊弄的一步，
   所以 `--reason` 是必填，且提示语明确要求写「为什么会错」而不是「改了哪行」。
8. **learn**（`learn`）：同一指纹出现 ≥N 次且都有根因说明 → 蒸馏成 `Pattern`。
9. **prevent**（`preflight` + `guard`）：规则注入 `AGENTS.md`（AI 开工前读）+ diff 拦截（提交前跑）。

## 3. 指纹：整套「学习」的地基

```
fingerprint = sha1( normalize(标题) + normalize(根因分类) + normalize(前 6 帧的文件名) )
```

`normalize` 抹掉：行号、内存地址、UUID、时间戳、目录前缀、纯数字。
保留：文件名、异常类型、消息骨架。

**为什么要抹行号**：同一个 bug 在两次上报里行号必然不同（有人插了两行代码）。
不抹，同一个问题会被当成两个新问题，永远学不到东西。

**为什么保留文件名**：抹掉就变成「所有模块的同名错误是一个 bug」，
跨模块去重会把无关联的问题合并掉。

**已知代价**：指纹算法一改，历史档案的指纹全失效。所以算法只在确实有缺陷时才动，
动了就要接受一段时间内 `report` 的复发率不准。这一点写进 README 的限制里，不藏着。

## 4. 根因词表：固定 16 类

`null_ref / boundary / type_mismatch / concurrency / resource / config / dependency /
network / permission / encoding / state / compat / data / ui / perf / unknown`

固定词表换来三件事：报表可聚合、规则模板可复用、AI 不用每次发明分类。
判根因用**有序线索表 + 首个命中**，顺序即先验（`NullPointerException` 必须先判成
`null_ref`，不能被 `dependency` 的泛化线索抢走）。这条顺序规则踩过 bug：
最初用「最早出现的关键词位置」做判据，结果 `nullpointer` 同时出现在两个分组里，
`dependency` 排在前面就赢了，Java 空指针全被判成依赖问题。

## 5. 知识库：三层 + 一个写入口

```
seed    knowledge/patterns/*.json       随技能分发的通用模式（只读，12 条起步）
user    ~/.zfix/knowledge/patterns/*    从实际修复蒸馏的（可写）
live    ~/.zfix/knowledge/fixes.jsonl   原始修复档案（学习与报表的原料）
project <repo>/.zfix/patterns/*         项目专属（优先级最高）
```

合并顺序决定覆盖关系：项目 > 用户 > 种子。学习不直接写「模式」，而是从 `fixes.jsonl`
聚合——原始档案是唯一事实，模式是可以随时重算的派生数据。这样算法改了可以重跑，
不存在「模式库被写脏了没法回滚」。

## 6. 防复发：两道防线，一软一硬

- **软（`preflight`）**：把高频规则写进 `AGENTS.md` / `CLAUDE.md` 的受管区块。
  用 `<!-- zfix:rules:begin/end -->` 包裹，幂等替换，不碰人写的部分。
  按「出现次数 × 是否有可执行检测」排序，默认最多 20 条——规则多了等于没有规则。
- **硬（`guard`）**：解析 `git diff`，只扫**新增行**（改别人写的老代码不算你这次引入的），
  对 `Pattern.detect_regex` 匹配。`guard.block = true` 时返回非 0，可挂 pre-commit / CI。

只有「有检测规则的模式」才能进 `guard`，而所有模式都能进 `preflight`。
这条区分很重要：不能检测的规则（如「注意边界」）只配出现在提示里，不配阻断提交。

## 7. 多平台适配矩阵

| 平台 | 技能目录 | 规则文件 | 安装方式 |
| --- | --- | --- | --- |
| Claude Code | `.claude/skills/<name>/` | `CLAUDE.md` | `install.py --target claude` |
| Codex | `.codex/skills/<name>/` | `AGENTS.md` | `--target codex` |
| CodeBee | `.codebee/skills/<name>/` | `AGENTS.md` | `--target codebee` |
| OpenCode | `.opencode/skill/<name>/` | `AGENTS.md` | `--target opencode` |

关键设计：**技能目录里放的是完整可运行副本**（含 `zfix` 包、`recipes`、`knowledge`），
安装时把 `SKILL.md` 里的 `{{ZFIX}}` 占位符替换成本机真实命令。
不依赖 `pip install`、不依赖 `PYTHONPATH`、不依赖某一个平台的插件机制——
只要有 Python 3.8+ 就能跑，这样「不绑定平台」才是真的，而不是嘴上说说。

`AGENTS.md` 被当作事实标准：四家都读它，所以它始终是注入的首选目标。

## 8. 扩展点

- **新缺陷源**（Jira / 云效 / 飞书表格）：实现 `BugSource` 三方法 + `@register`，
  或用 `kind = "local"` 直接读导出文件（最省事，先跑通再谈对接）。
- **新技术栈**：往 `recipes/` 加一个 JSON。字段：`detect.files` / `commands.{lint,typecheck,test,test_scoped}`。
- **新根因**：`models.ROOT_CAUSES` 加一项 + `knowledge._PREVENTION_TEMPLATE` 加对应规则模板，
  否则学习出来的模式没有可用规则。
- **新反模式**：往 `knowledge/patterns/` 加 JSON，`detect_regex` 务必在真实代码上试过误报率。

## 9. 刻意不做的事

- **不自动 commit / push**：分支可以建，提交必须由人决定。自动化到提交这一步，
  一旦 AI 判断错了，污染的是历史而不是工作区。
- **不自动改禅道状态**：`comment_on_zentao` 默认关。缺陷状态是团队协作信号，
  不该被一个本地工具单方面改写。
- **不在 CLI 里写业务代码**：`fix` 阶段永远由 AI 做。工具越界去改代码，
  就等于把「不可预测」放进最需要可预测的位置。
- **不做「全自动无人值守」**：`triage` 判成 `manual` 的必须有人看一眼。
  追求 100% 自动化率的代价是错误修复直接进主干。

## 10. 验收方式（怎么算这系统真的能用）

不是看代码行数，看这几条能不能跑通：

```bash
py -3 -m pytest skills/zentao-autofix/tests -q     # 40 项单测全绿
py -3 zfix.py doctor                               # 禅道连通 + 识别出验收命令
py -3 zfix.py sync && py -3 zfix.py triage         # 缺陷能落盘并分诊
py -3 zfix.py next                                 # 产出上下文包，候选位置非空
py -3 zfix.py verify <id>                          # 至少一步真跑过（不是全 SKIP）
py -3 zfix.py record <id> --reason "..."           # 档案落盘
py -3 zfix.py learn --apply && py -3 zfix.py preflight   # 规则进了 AGENTS.md
py -3 zfix.py guard --diff-file <某次真实 diff>     # 能命中已知反模式
```

最后一条最硬：拿一个**历史上真实复发过**的问题构造 diff，`guard` 必须报警。
报不出来，说明种子模式库还太薄，继续 `learn --apply` 攒。
