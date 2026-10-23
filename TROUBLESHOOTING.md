# 排障指南

按「现象 → 先看哪里 → 怎么修」组织。文中的每一条都对应仓库里可查的行为；
标「已复现」的是 2026-10-23 在 Windows 11 + Python 3.13 上实际跑出来的结果，
其余来自源码与既有回归测试。

## 0. 出问题时先收三样东西

排障九成的时间花在确认「到底是哪一层坏了」。先收这三样，能省掉大半沟通：

1. **启动终端最后 20 行** —— 端口、Python 探测、就绪提示都在这里。
2. **智能体管理页「能力探测」那一行的状态** —— 三个值含义不同：`可用`、`未检测到`、`探测异常`。
3. **任务详情里失败步骤的摘要 + 任务 ID** —— 报告页的结论来自最后一步，单看结论会漏掉中间降级。

## 1. 服务起不来

### 8765 被占用

默认监听 `127.0.0.1:8765`。换端口：

```bash
codebee --port 18765            # npm 安装方式
python app/main.py --port 18765 # 源码模式
```

再用新端口访问 `http://127.0.0.1:18765`。

### npm 方式提示「未找到可用的 Python」或「版本过低」

`npm` 包**不含解释器**，启动时探测本机 `py` / `python` / `python3`，要求 Python 3.8+。
探测失败会打印安装指引并以退出码 `1` 结束，不会写入任何用户配置（代码可证：`bin/tutti.js:85-104`）。

- Windows 安装 Python 时勾选 **Add python.exe to PATH**。
- 机器上有多个解释器、探测选错时，用 `TUTTI_PYTHON` 指定，取值只有三个：
  `py` / `python` / `python3`（它只做「选择题」，不接受任意路径）：

```bash
TUTTI_PYTHON=python3 codebee
```

### 浏览器没自动打开

启动参数加 `--no-browser` 时不会自动开；手动访问 `http://127.0.0.1:<端口>` 即可。
远程/后台运行时建议就用这个参数，避免在无桌面环境里反复失败。

## 2. 检测不到某个 CLI

`catalog` 里列出 19 个 CLI，指的是 **CodeBee 有调用模板**，不代表本机装了。
2026-10-23 实测本机 19 个中 3 个未安装（`openclaw`、`minimax-code`、`goose`），
显示「未检测到」是正确行为，不是故障。

按顺序排查：

1. 在**启动 CodeBee 的同一个终端**里执行 `where <cli>`（Windows）或 `command -v <cli>`。
   GUI 启动的服务与你在终端里的 PATH 可能不同。
2. 刚装完 CLI 要重启服务或在探测页点重新检测 —— 进程启动时读的是当时的 PATH。
3. 确认 CLI 自己能跑：`<cli> --version` 有输出，再谈 CodeBee 能不能编排它。

### 面板里只出现几个 CLI，其余十几个根本不显示

已修（2026-10-23）。旧版探测默认集是 5 条手写条目（`codex`、`claude`、`qwen`、`aider`、
`opencode`），与 catalog 的 19 条 ID 不同名，面板于是只显示 5 条，其余 14 条既不显示「可用」
也不显示「未检测到」。现在默认集按 catalog 生成，19 条都会出现，名字就是 catalog ID。

如果仍只看到几个，先看**版本号那一列是不是空的、徽章是不是「响应慢」**：

| 徽章 | 含义 | 怎么办 |
| --- | --- | --- |
| 可用 | 找到可执行文件，版本也读到了 | — |
| 响应慢 | 版本命令没在预算（8s）内返回，但文件在 | 冷启动的 node 版 CLI 实测要 7–22s，点重新检测即可；不影响派发 |
| 探测异常 | 文件在，但版本命令真的报错 | 见下一节 |
| 未检测到 | PATH 里没有 | 见上一节 |

### 面板刷新很慢 / 转圈很久

已修。根因不是「CLI 多」，是旧探测用的 `subprocess.run(timeout=)` 在 Windows 上**不构成墙钟
上限**：版本命令多经 `.CMD` 包装器（cmd.exe → node.exe），超时只杀掉 cmd.exe，而继承管道的
node.exe 仍握着输出句柄，函数会一直等它退出（实测 `timeout=3` 时 mimo-code 探了 22.8s）。
现在改成能连孙进程一起杀的实现 + 并行探测，19 条约 10s。

## 3. 检测到了，但显示「探测异常」

**含义**：可执行文件在 PATH 里，但版本命令本身失败了。
这类情况 CodeBee 会保留「已找到路径」的事实，同时把版本留空并给出原因，而不是把
错误输出当成版本号（`app/core/cli_discovery.py` 的 `_version_text`）。

已复现的例子：

```
trae-agent  探测异常
版本识别失败：CLI 启动即报错（ModuleNotFoundError: No module named 'docker'）
```

**怎么修**：先在终端手工跑一次 `<cli> --version` 复现同样的报错 —— 这是 CLI 自身安装
不完整（缺依赖、wrapper 指向坏路径），修 CLI 而不是修 CodeBee。装好后重新检测即可。

另外两类**不是故障**：

| 现象 | 说明 |
| --- | --- |
| 版本串里带解释器路径（如 `mistral-vibe` 显示 `python.exe C:\...\vibe.EXE 2.26.1`） | CLI 自己把命令行当版本输出；CodeBee 不猜解析，不影响派发 |
| 一批 CLI 同时探测时个别版本为空，单独复测正常 | 并发探测 `.CMD` 包装器的抖动（已复现一次），重新检测即可 |

## 4. 任务失败、或「明明失败了却不换 CLI」

判定是按顺序走的，看懂顺序就知道为什么没换：

1. **归类**：错误被映射到 20 类错误码之一（`app/core/error_codes.py`）。
2. **是否可换将**：只有可恢复错误才换下一个 CLI（`app/core/routing_strategy.py` 的
   `should_fallback`），当前白名单：
   `NETWORK`、`UPSTREAM_SERVER`、`TIMEOUT`、`QUOTA`、`RATE_LIMIT`、`LOCAL_STATE`、
   `FORBIDDEN`、`MODEL_UNAVAILABLE`、`VENDOR_REFUSAL`、`AUTH`、`MISSING_CREDENTIAL`、
   `VENDOR_ERROR`。
3. **账号级死错熔断**：`QUOTA`、`AUTH`、`MISSING_CREDENTIAL`、`FORBIDDEN`、`TOOL_LAUNCH`
   属于账号级死错（`ACCOUNT_DEAD_CODES`），该 CLI **在本次运行内停用**，不会原地重试同一个死号。

所以「AUTH 既在白名单又被熔断」不矛盾：**换的是下一个 CLI，停的是这个 CLI 本场的使用权**。

不换将的明确例外：

- 用户取消（`CANCELLED`）—— 取消不触发换将。
- 捕获输出被截断（`output_truncated`）—— 反而允许多试一次，因为截断更像采集问题而非模型失败。

## 5. 报告怎么读

任务的验证结论是三态，**不要只看 ✅/❌**：

| 结论 | 含义 |
| --- | --- |
| 通过 | 验证命令跑过且通过 |
| 未通过 | 验证命令跑过且失败 |
| 未执行 | 没配验证命令，或运行没走到验证步骤 |

「未执行」时报告会多一行 ⚠ 警示，因为此时结论只来自模型评审、**不含任何测试证据**。
评审被降级时另外会打「⚠ 评审降级」并写原因。

CLI 的兼容性边界（哪些组合真的验收过）见 [CLI_COMPATIBILITY.md](CLI_COMPATIBILITY.md)，
不要用 README 的接入表推断某个版本可用。

## 6. 数据在哪、升级会不会丢

数据目录按优先级选（`app/core/paths.py`）：

1. 环境变量 `TUTTI_DATA`（多实例/测试用）；
2. 仓库内 `data/` 且已有内容 —— 源码模式沿用，升级不动数据；
3. 用户目录：Windows `%APPDATA%\CodeBee`，macOS/Linux `~/.codebee`。

npm 全局安装**一定走用户目录**，因为 `npm update -g` 会整体替换包目录。

- 升级：`npm update -g codebee` 不动数据目录。
- 备份：停服务后整目录复制即可；换机器同理。
- 源码模式下删了仓库 `data/` 就是真删，没有回滚。

## 7. 提 Issue 前

请附：`VERSION` 内容、操作系统与架构、相关 CLI 的 `--version`、任务 ID、失败步骤摘要。

**不要**贴访问令牌、账号、完整 API Key 或未脱敏的提示词。日志里的路径与模型名可以保留，
它们是定位问题的关键信息。
