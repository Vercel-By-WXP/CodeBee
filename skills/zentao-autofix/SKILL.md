---
name: zentao-autofix
description: 从禅道拉取缺陷，分析根因、定位代码、修复、跑验收、归档根因，并把重复出现的错误沉淀成防复发规则。当用户提到「禅道 / bug / 缺陷 / 线上问题 / 报错修复 / 修 bug」或贴出堆栈要你修时使用；也用于批量处理待修缺陷队列、生成项目错误预防规则。
license: MIT
---

# 禅道缺陷自动修复

把「读禅道 → 定位 → 修 → 验证 → 记根因 → 防复发」做成可重复的流程。
本技能是薄壳：所有确定性逻辑在 `zfix` CLI 里，换 AI 平台只换壳，行为不漂移。

命令前缀：`{{ZFIX}}`
（安装脚本会把上面这行替换成本机真实命令；若它仍是花括号占位符，说明用的是源码副本，
直接在本技能目录下执行 `python zfix.py` 即可。）

## 什么时候用

- 用户让你修禅道上的某个缺陷，或贴了一段报错/堆栈让你排查；
- 用户问「还有哪些 bug 没修」「这个错误为什么反复出现」；
- 用户要求「以后别再犯同样的错」——走知识沉淀那条路。

不适用：纯需求开发、重构、性能调优（这些没有缺陷单，别硬套流程）。

## 硬流程（顺序不可跳）

```bash
{{ZFIX}} doctor            # 0. 先自检：配置/凭据/技术栈/验收命令
{{ZFIX}} next              # 1. 取一个待修缺陷，并生成上下文包（最省事的入口）
# 或指定缺陷：{{ZFIX}} sync && {{ZFIX}} triage && {{ZFIX}} context <bugID>
```

2. **读完上下文包再动手**。路径在 `next` 输出里，形如
   `.zfix/context/<id>.md`。里面有：复现步骤、堆栈关键帧、候选文件:行、
   历史同类修法、相关错误模式、**改动边界**、**验收命令**。
3. 改代码。只改上下文包第 3 节点名的文件；改到第 4 个文件就说明定位错了，回去重新 `locate`。
4. 验证，**红灯不许报完成**：

```bash
{{ZFIX}} verify <bugID>
```

5. 归档根因（这一步是知识库的唯一输入，不写就等于这次白修）：

```bash
{{ZFIX}} record <bugID> --root-cause null_ref --reason "order 缺 address 时未判空，直接对 None 调 .get"
```

6. 收尾两句：

```bash
{{ZFIX}} learn --apply      # 同一根因重复出现时，蒸馏成预防规则
{{ZFIX}} preflight          # 把规则写进 AGENTS.md / CLAUDE.md，下次开工会读到
```

## 命令速查

| 命令 | 作用 |
| --- | --- |
| `init` | 生成 `.zfix/config.toml`，注册项目 |
| `doctor` | 环境 + 禅道连通性 + 验收命令自检 |
| `sync` | 从禅道（或本地收件目录）拉缺陷 |
| `list` | 本地缺陷列表 |
| `triage` | 打指纹、判根因、定可修性、去重 |
| `context <id>` | 生成 AI 修复上下文包（核心交付物） |
| `locate <id>` | 只做定位，打印候选文件:行 |
| `branch <id>` | 按缺陷号建修复分支 |
| `verify <id>` | 跑 lint / typecheck / test 并留档 |
| `record <id>` | 归档修复档案（必须写 `--reason`） |
| `learn` | 重复修复 → 预防模式 |
| `preflight` | 规则注入 `AGENTS.md` / `CLAUDE.md` |
| `guard` | 扫当前 diff，命中已知反模式就报警 |
| `report` | 修复量、验收通过率、复发率、根因分布 |
| `next` | 取下一个待修缺陷 + 生成上下文包 |
| `show <id>` | 查看单条缺陷的完整字段 |

所有命令都支持 `--json`（写在后面前面都行），便于串到别的脚本里。

## 根因分类（`--root-cause` 只能从这些值里选）

`null_ref` 空值 · `boundary` 边界 · `type_mismatch` 类型 · `concurrency` 并发 ·
`resource` 资源泄漏 · `config` 配置 · `dependency` 依赖 · `network` 网络 ·
`permission` 权限 · `encoding` 编码 · `state` 状态机 · `compat` 兼容回归 ·
`data` 脏数据 · `ui` 界面 · `perf` 性能 · `unknown` 未归类

**`--reason` 必须写「为什么会错」，不是「改了哪行」。**
- 好：`分页参数为 0 时未做边界校验，空数组取 [0] 抛 IndexError`
- 坏：`加了 if 判断`、`改了 service.py 第 42 行`

## 红线

1. **不 commit、不 push、不改远端**。分支可以建（`zfix branch`），提交由人决定。
2. **不碰生产服务**：不重启、不清库、不连生产库查数据。
3. **不越界改动**：只改上下文包里点名的文件；不顺手重构、不批量格式化、不加未要求的依赖。
4. **不伪造验证**：`verify` 没跑过或红灯，就说「未验证/未通过」，不要说「已修复」。
5. **不静默跳过**：`verify` 出现 `SKIP`（工具没装）要如实告诉用户这一步没验到。
6. **信息不足就停**：`triage` 判成 `manual` 且用户没给更多信息时，问清楚再动手，别猜。

## 失败的常见原因（先查这些，别急着怀疑代码）

| 现象 | 处理 |
| --- | --- |
| `doctor` 报缺凭据 | 设 `ZENTAO_ACCOUNT` / `ZENTAO_PASSWORD`，或 `ZENTAO_TOKEN` |
| 禅道版本 < 16 无 REST | 配置里 `source.version = "legacy"`；仍不行就导出到 `.zfix/inbox/` 改用 `kind = "local"` |
| `verify` 全是 SKIP | 项目没装测试工具或没配命令，去 `[stack]` 显式写 `test` |
| `context` 候选位置为空 | 缺陷描述没有可搜索的符号，让用户补复现步骤/堆栈 |
| 定位到的是测试文件 | 在 `[fix] write_scope` 里限定源码目录 |

## 输出要求

给用户的结论至少包含四件事：**哪条缺陷、根因是什么、改了什么、验没验过**。
批量处理时先给队列概况（几条 auto / 几条 manual / 几条重复），再逐条报。

细致的架构与扩展方式见同目录 `DESIGN.md`；配置字段见 `README.md`。
