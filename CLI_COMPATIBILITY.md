# CLI 版本兼容验收

README 中的 CLI 接入表反映 `app/core/catalog.py` 声明的调用入口。它不表示对应版本或操作系统
已通过端到端验收。维护发布说明时，按本页记录实际跑过的组合；未记录的组合保持“未验证”。

## 每条验收记录

记录以下信息，认证账号、密钥和提示词中的私密内容必须脱敏：

| 字段 | 记录内容 |
| --- | --- |
| CodeBee | 发布版本、Git commit |
| 环境 | 操作系统与版本、CPU 架构 |
| CLI | catalog ID、CLI 精确版本、安装来源 |
| 模型与认证 | provider/model 标识、认证方式；不记录账号或凭据 |
| 调用入口 | 实际参数模板、工作目录和输入通道（argv/stdin/文件） |
| 验收用例 | 用例名称、结果、耗时和脱敏证据位置 |
| 边界 | 已知失败、未测能力，以及该结果不覆盖的环境组合 |

至少分别记录安装与版本识别、一次真实任务调用及非空结果、任务超时或失败时的退出行为。catalog 声明支持会话恢复的 CLI 还需单独验证恢复；没有恢复入口的 CLI 不得标记为已验证恢复。

“通过”只覆盖记录里的 CodeBee 版本、CLI 版本、操作系统、模型/认证方式和用例。mock、单元测试、仅检测到可执行文件或用户自行运行过 CLI，都不能替代真实调用验收。未运行的组合标为“未测”，失败或受环境阻碍的组合保留失败/阻碍结果和原因，不得从其他系统或相邻版本推断为通过。

## 发布记录模板

复制下面的表格到对应版本的发布说明，并只填写实际执行过的项目：

| CLI | CLI 版本 | 操作系统/架构 | 模型/认证方式 | 任务调用 | 超时/失败退出 | 会话恢复 | 结果与证据 |
| --- | --- | --- | --- | --- | --- | --- | --- |
| 例：Codex CLI | 精确版本 | Windows 版本/架构 | provider/model；认证方式 | 通过/失败/未测 | 通过/失败/未测 | 通过/失败/不适用/未测 | 脱敏日志或测试路径 |

新增或移除 catalog CLI 时，同步更新 README 接入表。`tests/test_cli_compatibility_docs.py`
校验 README 中的 CLI 清单和自动编排/仅检测分类与 catalog 一致，防止接入范围文档漂移；
它不验证真实 CLI 兼容性。

## 实测记录

### 2026-10-23 · Windows 11 · 仅版本识别

**环境**：CodeBee `0.1.120`（工作树，HEAD `5061f5e`，含未提交改动 —— 非发布快照）、
Windows 11 `10.0.26200` AMD64、Python `3.13.14`、Node `22.22.2`。

**探测方式**：进程内调用 `cli_discovery.discover(specs=...)`，spec 由 `catalog.DEFAULT_CATALOG`
的 19 条生成，与 `/api/cli-discovery` 使用同一代码路径与同一份探测函数；**未通过 UI 操作**。

**覆盖范围**：仅「安装与版本识别」。真实任务调用、超时/失败退出、会话恢复三项本次**全部未测**，
不得据本表推断通过。本表也不是发布验收记录 —— 发布验收须按上节模板另记。

| catalog CLI | CLI 版本 | 安装与版本识别 | 任务调用 | 超时/失败退出 | 会话恢复 | 备注 |
| --- | --- | --- | --- | --- | --- | --- |
| codex-cli | codex-cli 0.160.0 | 通过 | 未测 | 未测 | 未测 | npm 全局 .CMD |
| claude-code | 2.1.288 (Claude Code) | 通过 | 未测 | 未测 | 未测 | npm 全局 .CMD |
| opencode | 1.18.34 | 通过 | 未测 | 未测 | 未测 | npm 全局 .CMD |
| qwencode | 0.24.7 | 通过 | 未测 | 未测 | 未测 | npm 全局 .CMD |
| aider | aider.EXE 0.86.2 | 通过 | 未测 | 未测 | 未测 | 版本串含可执行名 |
| openclaw | — | 未安装 | 未测 | 未测 | 未测 | 不在 PATH |
| kimi-code | 2.1.1 | 通过 | 未测 | 未测 | 未测 | |
| mimo-code | 0.1.15 | 通过 | 未测 | 未测 | 未测 | |
| grok-build | grok 1.0.44 (5b807183dd79) | 通过 | 未测 | 未测 | 未测 | |
| pi | 0.99.1 | 通过 | 未测 | 未测 | 未测 | |
| deepseek-harness | 0.2.0-rc.2 | 通过 | 未测 | 未测 | 未测 | 预发布版本 |
| gemini-cli | 0.62.0 | 通过 | 未测 | 未测 | 未测 | 19 路并发探测曾返回空版本，单独复测正常，记为探测抖动 |
| codebuddy | 2.160.0 | 通过 | 未测 | 未测 | 未测 | |
| trae-agent | （未读到） | **失败** | 未测 | 未测 | 未测 | `trae-cli --version` 启动即 `ModuleNotFoundError: No module named 'docker'`，属本机 CLI 安装不完整 |
| minimax-code | — | 未安装 | 未测 | 未测 | 未测 | 不在 PATH |
| goose | — | 未安装 | 未测 | 未测 | 未测 | 不在 PATH |
| mistral-vibe | python.exe C:\Users\HP\.local\bin\vibe.EXE 2.26.1 | 通过（版本需人工解析） | 未测 | 未测 | 未测 | CLI 自身把命令行与版本一并输出，CodeBee 不猜 |
| cline-cli | 3.0.73 | 通过 | 未测 | 未测 | 未测 | |
| github-copilot-cli | GitHub Copilot CLI 1.0.98. | 通过 | 未测 | 未测 | 未测 | |

合计：19 条中 16 条可检出、15 条版本可读、1 条检出但启动失败、3 条未安装。

**本次同时修正的呈现缺陷**：`probe()` 原先把版本命令输出的首行直接写入 `version`，
崩溃的 CLI 会把 `Traceback (most recent call last):` 当作版本号显示。现改为
`version=""` + `error="版本识别失败：CLI 启动即报错（<末行原因>）"`，`available` 仍保留，
用于区分「没装」与「装了但跑不起来」。回归见 `tests/test_cli_probe_version_sanitize.py`。

下一步按上节模板补「任务调用 / 超时退出 / 会话恢复」三层：优先 codex-cli、claude-code、
opencode 三个入口各跑 1 个真实任务。

