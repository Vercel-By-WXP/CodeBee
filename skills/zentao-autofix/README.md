# zentao-autofix · 禅道缺陷自动修复与防复发

读禅道 → 分析根因 → 定位代码 → 修复 → 跑验收 → 归档根因 → 蒸馏规则 → 提交前拦截。

**一份逻辑，多平台通用**：核心全在零依赖 Python CLI（`zfix`）里，Skill 只是薄壳。
Codex、Claude Code、CodeBee、OpenCode 装的都是同一份逻辑，换平台不改变行为。

```
zfix sync → zfix triage → zfix context <id> → (AI 改代码) → zfix verify <id>
   → zfix record <id> → zfix learn --apply → zfix preflight → zfix guard
```

## 快速开始

```bash
# 0. 装到当前项目（四选一，all 表示全装）
python scripts/install.py --target claude --project /path/to/your/repo

# 1. 初始化项目配置
cd /path/to/your/repo
py -3 zfix.py init --stack python

# 2. 填禅道地址与凭据
#    编辑 .zfix/config.toml 的 [source]，然后：
export ZENTAO_ACCOUNT=your-account
export ZENTAO_PASSWORD=your-password
#   或直接用 token：export ZENTAO_TOKEN=xxxx

# 3. 自检
py -3 zfix.py doctor

# 4. 跑一轮
py -3 zfix.py sync          # 拉缺陷
py -3 zfix.py triage        # 打指纹/判根因/去重
py -3 zfix.py next          # 取一个待修缺陷 + 生成上下文包
```

然后把这个路径交给 AI：`.zfix/context/<bugID>.md`。它读完再动手，改完跑 `verify`，
最后 `record` 写根因。

## 命令

| 命令 | 说明 |
| --- | --- |
| `init` | 生成 `.zfix/config.toml`、创建目录、注册项目 |
| `doctor` | 环境 + 禅道连通性 + 技术栈识别 + 验收命令自检 |
| `sync [--status active] [--product 1] [--limit 20]` | 拉取缺陷到 `.zfix/bugs/` |
| `list [--status] [--decision auto\|manual\|skip]` | 本地缺陷列表 |
| `triage` | 指纹、根因初判、可修性决策、重复聚类 |
| `context <id> [--print]` | 生成上下文包 `.zfix/context/<id>.md` |
| `locate <id> [--limit 12]` | 只做代码定位，打印候选文件:行 |
| `branch <id> [--dry-run]` | 按 `fix/<id>-<slug>` 建分支 |
| `verify <id> [--only test] [--no-scope]` | 跑 lint/typecheck/test 并留档 |
| `record <id> --root-cause X --reason "..."` | 归档修复档案 |
| `learn [--min 2] [--apply]` | 重复修复 → 预防模式 |
| `preflight [--dry-run] [--platforms claude,codex]` | 规则注入 `AGENTS.md` / `CLAUDE.md` |
| `guard [--staged] [--diff-file f]` | 扫 diff，命中已知反模式报警（可阻断） |
| `report [--all-projects]` | 修复量 / 验收通过率 / 复发率 / 根因分布 |
| `next` | 取下一个待修缺陷并生成上下文包 |
| `show <id>` | 查看单条缺陷的完整字段 |

所有命令都接受 `--json`（写前写后都行），便于串进 CI 或别的 Agent。

## 配置

`.zfix/config.toml`，字段与默认值见 `examples/config.sample.json`（同名同义）。
关键几项：

| 键 | 作用 |
| --- | --- |
| `source.kind` | `zentao`（默认）/ `local`（读 `.zfix/inbox/` 下的 json、csv） |
| `source.version` | `v1` = 禅道 16.x+ REST；`legacy` = 旧版 Cookie（尽力而为） |
| `source.insecure` | 内网自签证书时置 `true` |
| `stack.lang` / `stack.recipe` | 技术栈；不填 recipe 时按文件特征自动识别 |
| `stack.test` / `lint` / `typecheck` | 留空则用配方默认；填了就完全覆盖 |
| `fix.write_scope` | 允许改的目录；`record` 也只统计这些目录下的改动 |
| `fix.max_files` | 单次修复的改动文件上限（超了说明定位错了） |
| `guard.block` | `true` 时 `guard` 命中即返回非 0，可挂 pre-commit / CI |

凭据只从环境变量读，不落盘：`ZENTAO_ACCOUNT` / `ZENTAO_PASSWORD` / `ZENTAO_TOKEN`。

## 目录

```
<项目>/.zfix/
├── config.toml          项目配置（应提交）
├── inbox/               kind=local 时的缺陷收件目录
├── bugs/                同步下来的缺陷（可 gitignore）
├── context/             生成的上下文包（可 gitignore）
├── runs/                verify 结果
└── patterns/            项目级错误模式（可选，优先级最高）

~/.zfix/                  用户级、跨项目
├── projects.json        项目注册表
└── knowledge/
    ├── fixes.jsonl      修复档案（学习与报表的原料）
    └── patterns/        蒸馏出来的模式
```

知识按「项目 > 用户 > 技能自带种子」三层合并，同名 id 后者被前者覆盖。

## 挂到提交前（可选）

```bash
# .git/hooks/pre-commit
py -3 .zfix/../zfix.py guard --staged
```

`guard.block = true` 时命中即阻断。想先观察一段时间就别开 block，只让它输出提示。

## 技术栈

内置配方：`python-pytest`、`node-package`、`java-maven`、`go-mod`、`generic`。
新增一个栈只需在 `recipes/` 加一个 JSON，不改代码。

## 已知限制

- 指纹算法一旦改动，历史档案里的指纹会失效（`report` 的复发率会短暂失真）。
- 旧版禅道（<16.x）没有稳定的 JSON 出口，`legacy` 是尽力而为；拿不到就导出到
  `.zfix/inbox/` 走 `local`。
- `verify` 里命令不存在会标 `SKIP` 而不是 `FAIL`——环境问题不该伪装成代码问题，但
  你必须知道这一步没真正验过。

架构与扩展方式见 `DESIGN.md`。
