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
的 19 条生成；**未通过 UI 操作**。

与 `/api/cli-discovery` 的关系需要说准：**探测函数是同一个**（`cli_discovery.probe`），但当天
早些时候**默认探测集不是同一份**。接口在请求体不带 `specs` 时用的是 `DEFAULT_CLIS`（5 条旧条目：
`codex`/`claude`/`qwen`/`aider`/`opencode`），与 catalog ID（`codex-cli` 等）不同名；UI 面板发的
正是空对象 `{}`，所以**界面最多只能显示 19 条里的 5 条**，其余 14 条既不显示「未安装」也不显示
「可用」——是没被探测。本表上 19 行结果是显式传目录得到的。

该缺口当日已修（`app/core/cli_discovery.py`）：默认集改由 `default_specs()` 按目录生成，旧 5 条
只在目录不可用时兜底。修复卡住的不是「条目多」，而是**超时根本不是墙钟上限**：

```
subprocess.run(timeout=3) 实测   mimo-code 22.8s / pi 19.6s / gemini 17.3s / cline 14.0s
```

`.CMD` 包装器是 cmd.exe → node.exe，`run()` 超时只杀 cmd.exe，而继承管道的 node.exe 仍握着
stdout/stderr 句柄，`communicate()` 会一直等它自己退出。改为 Popen + runner 已验证的 `_kill_tree`
（连孙进程一起杀）后：

| 场景 | 修复前 | 修复后 |
| --- | --- | --- |
| 19 条串行探测 | 48.4s | — |
| 19 条并行探测（6 并发，`timeout=3`） | — | 7.5s（但 4 条冷启动慢的被误标） |
| 19 条并行探测（`timeout=8`，接口新默认） | — | **9.9s，16 可用，0 误判** |

「慢 ≠ 坏」单独区分：没在预算内返回版本的标 `slow`，界面显示「响应慢」而不是「探测异常」。

**覆盖范围**：仅「安装与版本识别」。真实任务调用、超时/失败退出、会话恢复三项本次**全部未测**，
不得据本表推断通过。本表也不是发布验收记录 —— 发布验收须按上节模板另记。

### 2026-10-23 · 真实任务调用（首轮，3 个入口）

**方式**：`python scripts/cli_compat_record.py --cli <id>`。隔离实例（临时 `TUTTI_DATA` + 空闲端口，
不碰 8765）、只启用目标 CLI、最小用例「创建 hello.py 并运行确认输出 hello」、`verify_command`
检查文件落地。**命中判定看 `run.steps[].agent`** —— 若执行者不是目标 CLI，整条记「未命中 / 未测」。

原始记录落在 [`compat-records/`](compat-records/)（随仓库入库，可直接复核）。记录**不写运行
数据、不写用户目录、不写私有网关主机名**：URL 只保留协议与路径（如 `/v1/responses`），
公共站点（github/npm/官方文档）原样保留。要复跑一条就 `--cli <id>`，新记录自动进同一目录。

| CLI | 命中 | 结果 | 耗时 | 失败原因（脱敏后原文） |
| --- | --- | --- | --- | --- |
| codex-cli | 是 | **失败** | 66.4s | `Reconnecting... 2/5 (unexpected status 404 Not Found: openai_error, url: https://<endpoint>/v1/responses)` |
| claude-code | 是 | **失败** | 9.0s | `There's an issue with the selected model (glm-5.3). It may not exist or you may not have access to it.` |
| opencode | 是 | **失败** | 229.2s | `Error: This model is not available in your country` |

三条结论要分开说：

1. **CodeBee 侧没坏**：三个 CLI 都真的被启动并进入了实现步骤（`steps` 里就是它们），错误原文
   被完整带回到运行记录里，失败按「无其他真实 CLI 可换将」收口，没有静默卡住；没有出现
   「跑完了但什么都没做还标通过」。
2. **CLI 侧不可用**：codex 指向的自建网关不支持 `/v1/responses`（404）；claude 配的模型
   `glm-5.3` 不存在或无权限；opencode 选定模型在当前地区不可用。三条都是本机 CLI/账号配置，
   **不是 CodeBee 的兼容性结论**，所以「任务调用」记 **失败（环境原因）**，不记通过也不记
   「CodeBee 不支持」。
3. **这轮拿不到「通过」样本**：本机三个代表性入口没有一个是可用配置。要产出真正的通过记录，
   需要一个能跑通任务的 CLI + 模型；命令已就绪，换台机器或换掉模型配置即可复跑。
   （第二轮已用 GitHub Copilot CLI 拿到通过样本，见下节。）

**已定性的归因偏差（2026-10-23 已修）**：codex 那条终态文案写「超时」，同一条 stderr 里
却是 404。CLI 侧 `Reconnecting... 2/5` 重连耗尽后才到墙钟上限，两者都真，但**读者只会看到
「超时」**，容易把供应商 4xx 误读成 CodeBee 慢。根因在分类表：`404` 当时不被识别，
`_classify_failure` 在 `timed_out` 分支追死因追不到就回落 `TIMEOUT`。现 400/404/422 归
`UPSTREAM_CONFIG`（可换将、算 blocked、不是账号死错），真实子进程复现验证
（`tests/test_upstream_config_classify.py`：stderr 喷 404 后挂住 → `UPSTREAM_CONFIG`；
无 4xx 证据的纯卡死仍 `TIMEOUT`）。

超时退出与会话恢复两列仍为「未测」：三个入口都在实现步骤就失败，没走到这两层。

### 2026-10-23 · 真实任务调用（第二轮：拿到通过样本 + 补齐两列）

**为什么换入口**：第一轮三个 CLAL 全部卡在「本机配置不可用」，再跑多少次也变不出通过样本。
这轮先把本机能跑通的入口找出来——逐条用最小提示词试（`reply with exactly OK`），
结果只有 **GitHub Copilot CLI** 有可用凭据；其余 8 个要么额度为 0（qwen 403 ¥0、
deepseek-harness transport 失败）、要么锁区（opencode）、要么缺 key（mistral-vibe）、
要么安装残缺（trae-cli 缺 `docker` 模块）。

**Copilot 原先被产品标成「只检测、不派发」**（`orch: null`），探测还拿条目 id
`github-copilot-cli` 当可执行名，实际命令是 `copilot` —— 等于永远「未检测到」。
这轮把它接进编排，参数按帮助输出实测：`-p` 无头、`-s` 只要答复不要统计行、
`--allow-all-tools`（非交互态不加则所有工具调用等确认）、`-r <session>` 续会话。

三条记录（`compat-records/github-copilot-cli-20261023-*.json`）分别补齐三列：

| 取证项 | 实测 | 记录值 |
| --- | --- | --- |
| 任务调用 | 创建 `hello.py` 并运行，147.6s 到终态 `done`，`verify_ran=true`/`verify_pass=true` | **通过** |
| 超时退出 | 把隔离实例里该 CLI 的 `timeout_ms` 改成 15000，真实进程 **18.1s 内终止**，终态 `[超时 15.0s，已终止进程树]` | **通过** |
| 会话恢复 | 先用同一 session-id 播下暗号「42」，再由 CodeBee 带 `resume` 续跑；回答里出现 42（提示词本身不含） | **通过（答出暗号）** |

超时的判定写在脚本里，不靠肉眼：必须「到终态 + 目标 CLI 真被调用 + 耗时 ≤ 预算+30s +
终态有超时语义」，四条缺一条就记未通过（`tests/test_cli_compat_record.py::TimeoutEvidenceTest`）。
会话恢复同理：没有 `--expect` 暗号只能证明「跑过」，证不了「上下文接上了」，一律记未测。

```bash
# 任务调用
python scripts/cli_compat_record.py --cli github-copilot-cli
# 超时退出（把该 CLI 的墙钟预算压到 15s）
python scripts/cli_compat_record.py --cli github-copilot-cli --timeout-ms 15000
# 会话恢复（先在同目录播暗号，再让 CodeBee 续）
python scripts/cli_compat_record.py --cli github-copilot-cli --workdir <同目录> \
       --session <session-id> --expect 42
```

**codex 第三次复跑（2026-10-23 傍晚，网关又变了）**：直接探网关三种请求形态，
`/v1/responses` 对**任何** payload 都返回 404（带不带 `web_search` 工具都一样），
`/v1/chat/completions` 直接读超时。同一天里这个网关给出了三种口径：404 → 拒绝
`web_search` 工具 → 又全线 404。**自建网关的能力面会漂，任何单次结论都有保质期**——
这也是记录里必须写时间与 CLI 精确版本的原因。codex 仍记失败（环境原因），
不记「CodeBee 不支持」。

**claude 全模型复探（2026-10-23 18:12）**：换模型绕不开去——`glm-5.3` / `glm-5.3-flash` /
`glm-4.6` / `glm-4.5-air` 全部 HTTP 429 `code 1310`（7 天额度上限，2026-10-26 14:09
后恢复），`glm-4-plus` 是 1113 余额不足，`glm-5.3-air` 是 1211 模型不存在。
账号级额度问题，等恢复后同一条命令即可复跑，不需要改代码。

**剩下 2 个通过样本仍然缺**，且不是代码能补的：claude-code 是账号级 7 天额度上限
（全模型 429，见上）；opencode 免费模型锁区、备用供应商同样撞额度。等凭据恢复，
用同一条命令即可复跑，不需要改代码。

| catalog CLI | CLI 版本 | 安装与版本识别 | 任务调用 | 超时/失败退出 | 会话恢复 | 备注 |
| --- | --- | --- | --- | --- | --- | --- |
| github-copilot-cli | GitHub Copilot CLI 1.0.98. | 通过 | **通过**（verify 实跑且过） | **通过**（15s 预算 / 18.1s 停止） | **通过**（续会话答出暗号） | 本轮新接入编排，见上表 |
| codex-cli | codex-cli 0.160.0 | 通过 | 失败（环境：网关 `/v1/responses` 全线 404、`/v1/chat/completions` 读超时） | 未测 | 未测 | npm 全局 .CMD；网关当天三变，见上 |
| claude-code | 2.1.288 (Claude Code) | 通过 | 失败（环境：bigmodel 全模型 429 `code 1310`，10-26 14:09 恢复） | 未测 | 未测 | npm 全局 .CMD |
| opencode | 1.18.34 | 通过 | 失败（环境：地区限制） | 未测 | 未测 | npm 全局 .CMD |
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
| github-copilot-cli | GitHub Copilot CLI 1.0.98. | 通过 | **通过** | **通过** | **通过** | 本轮接入编排；无头模板带 `--allow-all-tools` |

合计：19 条中 16 条可检出、15 条版本可读、1 条检出但启动失败、3 条未安装。

**本次同时修正的呈现缺陷**：`probe()` 原先把版本命令输出的首行直接写入 `version`，
崩溃的 CLI 会把 `Traceback (most recent call last):` 当作版本号显示。现改为
`version=""` + `error="版本识别失败：CLI 启动即报错（<末行原因>）"`，`available` 仍保留，
用于区分「没装」与「装了但跑不起来」。回归见 `tests/test_cli_probe_version_sanitize.py`。

### 2026-10-23 · CodeBee 进程层：超时与失败退出（**不是** CLI 兼容性证据）

「超时/失败退出」一列此前在整个仓库里没有一条真实证据：所有 `timed_out=True` 的断言都来自
假结果，没有任何测试证明超时之后进程真的没了。2026-10-13 的 18 个 `opencode --version`
僵尸堆积正是这种盲区的产物（单测全绿，机器上进程在涨）。

`tests/test_runner_timeout_kill.py` 用**真实子进程**（不打桩 CodeBee）验证了 5 项：

| 验证点 | 结果 | 依据 |
| --- | --- | --- |
| 超时标记 `timed_out`、不误记 `cancelled`、被杀进程不报退出码 0 | 通过 | 桩进程睡 120s，`timeout=2.0` |
| 超时后**根进程与孙进程都真死** | 通过 | 桩进程再生一个睡 120s 的孙进程，按 pid 轮询 5s 确认消失，且无 `quiescence_unknown` |
| 超时前已产生的输出被保留 | 通过 | `stdout` 含桩进程启动时打印的 `SELF=<pid>` |
| 停滞看门狗先于基础超时触发 | 通过 | `timeout=300` + `stall_timeout=1.5` → `stalled=True`，实际秒级返回 |
| 正常退出不被误判超时 / 缺可执行文件归启动失败 | 通过 | 反向用例，防止重试策略被误触 |

**边界**：以上只证明 CodeBee 的进程层能杀树、能区分超时与启动失败。它**不能**替代任何真实
CLI 的超时退出验收——真实 CLI 在超时时是否留下子进程、是否吐出可诊断的最后一行，仍属未测。

### 验收工具：`scripts/cli_compat_record.py`

补「任务调用」这一层需要一个可复现、不碰生产服务的入口，因此新增该脚本：

```bash
python scripts/cli_compat_record.py --cli codex-cli --self-check   # 只验链路，不建任务、不耗额度
python scripts/cli_compat_record.py --cli codex-cli                # 跑 1 个真实任务并产出记录
python scripts/cli_compat_record.py --cli <id> --timeout-ms 15000  # 超时退出取证（压墙钟预算）
python scripts/cli_compat_record.py --cli <id> --workdir <目录> \
       --session <session-id> --expect <暗号>                       # 会话恢复取证
```

脚本内置的约束（`tests/test_cli_compat_record.py` 35 项锁住）：

- 临时 `TUTTI_DATA` + 空闲端口，**不碰 8765**；工作目录与数据跑完即删。
- 显式按 `catalog.DEFAULT_CATALOG` 传 `specs`，不依赖服务端默认集（否则 14 条 CLI 探不到）。
- 命中判定看 `run.steps[].agent`：目标 CLI 没真的被执行，就记「未命中目标 CLI / 任务调用未测」，
  不允许把「跑过一次任务」写成「该 CLI 通过」。
- 输出前统一脱敏（用户目录、`sk-*`/`gh*_`/Bearer/`api_key=`），`model_auth` 无证据就写「未记录」。
- 超时未到终态时记「未测」并请求取消，不推断为失败或通过。

**自检实测**（`--self-check`，本机，2026-10-23）：隔离实例正常启动、按目录探到 15 个可用 CLI、
编排开关全部设置成功（`pin_failed` 为空），未建任务。**任务调用、超时退出、会话恢复仍未测**。

### 尚未覆盖

按上节模板补「任务调用 / 超时退出 / 会话恢复」三层：优先 codex-cli、claude-code、opencode
三个入口各跑 1 个真实任务，且**必须**产出 `run.steps[].agent` 含目标 CLI 的证据。这一步会消耗
订阅额度并依赖本机登录态，需操作者确认后执行。

