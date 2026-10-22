# CodeBee

> 把多个 AI 编码 CLI 编排成协作团队。

[快速开始 → QUICKSTART.md](QUICKSTART.md)

<!-- relnotes:start -->
### 最新版更新内容

- 全量审查发版：安全修复（设置接口密钥泄露堵死）+ 桌面发版链修复（Release 页不再必败）
- 桌面热更新加固：备份自动清理、半更新崩溃自愈、跨盘兼容
- MCP 路由修复 + task_create 走正规入队链；前端 XSS 三处修补；大屏防假死
- npm 包纳入 VERSION、剔除冗余、版本对账闭环；新增审查回归 + 业务 E2E 探针
<!-- relnotes:end -->

## 它解决什么问题

开发者装了多个 AI 编码 CLI，但它们往往彼此孤立。CodeBee 把这些 CLI 统一接入，
按任务路由、执行、验证、评审并在需要时自动修复，最后生成可审计的报告。

## 核心特性

- 跨厂商 CLI 统一调度：Codex CLI、Claude Code、OpenCode、QwenCode、Aider、Kimi Code、
  MiMo Code、Grok Build、Pi、DeepSeek Harness、Gemini CLI、CodeBuddy CLI、Trae Agent、
  MiniMax Code、Mistral Vibe、Cline CLI、OpenClaw（Goose 与 GitHub Copilot CLI 当前仅检测）。
- 智能路由与跨厂商降级。
- 对抗式评审：评审者优先来自不同模型族。
- 短剧剧本工作流：项目圣经、分集大纲、逐场剧本与双评审；当前交付文本，不生成视频。
- 本地运行，隐私优先，运行时零第三方 Python 依赖。
- 全程落盘，可回放任务、步骤、日志和报告。

## 和同类工具的差异

| 维度 | CodeBee | Aider | 其他 agent 框架 |
| --- | --- | --- | --- |
| 多 CLI 支持 | 原生多 CLI 目录与适配器 | 以单一 CLI 为主 | 取决于框架 |
| 跨厂商评审 | 内置模型族隔离与降级 | 需自行组合 | 通常需自行实现 |
| 零运行时依赖 | Python 标准库 | 需要 Python 依赖 | 取决于实现 |
| 本地优先 | 默认本机 Web UI 与本地数据 | 本地运行 | 取决于部署方式 |
| Web UI | 内置本地 Web UI | 以终端工作流为主 | 取决于框架 |

## 快速开始

请按 [QUICKSTART.md](QUICKSTART.md) 操作。

## CLI 接入范围

| CLI | CodeBee 接入情况 | 版本/系统验收记录 |
| --- | --- | --- |
| Codex CLI | 自动编排 | 目标版本和系统须随发布验收记录 |
| Claude Code | 自动编排 | 目标版本和系统须随发布验收记录 |
| OpenCode CLI | 自动编排 | 目标版本和系统须随发布验收记录 |
| QwenCode | 自动编排 | 目标版本和系统须随发布验收记录 |
| Aider | 自动编排 | 目标版本和系统须随发布验收记录 |
| OpenClaw | 自动编排 | 目标版本和系统须随发布验收记录 |
| Kimi Code | 自动编排 | 目标版本和系统须随发布验收记录 |
| MiMo Code | 自动编排 | 目标版本和系统须随发布验收记录 |
| Grok Build | 自动编排 | 目标版本和系统须随发布验收记录 |
| Pi | 自动编排 | 目标版本和系统须随发布验收记录 |
| DeepSeek Harness | 自动编排 | 目标版本和系统须随发布验收记录 |
| Gemini CLI | 自动编排 | 目标版本和系统须随发布验收记录 |
| CodeBuddy CLI | 自动编排 | 目标版本和系统须随发布验收记录 |
| Trae Agent | 自动编排 | 目标版本和系统须随发布验收记录 |
| MiniMax Code | 自动编排 | 目标版本和系统须随发布验收记录 |
| Goose | 仅检测与交互启动 | 暂无自动任务派发入口 |
| Mistral Vibe | 自动编排 | 目标版本和系统须随发布验收记录 |
| Cline CLI | 自动编排 | 目标版本和系统须随发布验收记录 |
| GitHub Copilot CLI | 仅检测与交互启动 | 暂无自动任务派发入口 |

“自动编排”表示 CodeBee 有对应的 CLI 调用模板，不代表所有版本、操作系统、模型与认证方式都通过了端到端验收。CodeBee 可在本机检测 CLI 版本；发布验收记录应写明 CLI 版本、操作系统、模型/认证方式、验证流程和结果。没有这类记录时，该组合视为未验证。本表只声明接入范围，不代替逐版本验收。记录格式见 [CLI_COMPATIBILITY.md](CLI_COMPATIBILITY.md)。我们借鉴 [ElyOffice 的兼容清单](https://github.com/ELY-Open-Source/ElyOffice/blob/main/docs/compatibility.md)，把已验证行为与未验证范围分开说明。

CodeBee 不安装或替换这些 CLI。请先按各项目官方文档完成登录和凭据配置。

## 文档

- [QUICKSTART.md](QUICKSTART.md)：安装、启动和第一个任务
- [ARCHITECTURE.md](ARCHITECTURE.md)：模块、数据流和扩展点
- [docs/routing_strategy.md](docs/routing_strategy.md)：可插拔路由策略接口
- [docs/review_storage.md](docs/review_storage.md)：评审数据结构化存储方案
- [docs/short-drama.md](docs/short-drama.md)：短剧剧本工作流与能力边界
- [CONTRIBUTING.md](CONTRIBUTING.md)：开发和贡献流程
- [SECURITY.md](SECURITY.md)：安全设计和漏洞报告

## 反馈

预览版问题、使用卡点和功能建议请统一提交到 [GitHub Issues](https://github.com/Vercel-By-WXP/CodeBee/issues/new/choose)。请附上 CodeBee 版本、系统、CLI 版本和已脱敏的复现信息；桌面版与官网使用同一入口。

## 本地 Web UI 安全说明

本地 Web UI 默认仅监听 127.0.0.1，不会暴露到局域网。如需从其他设备访问，请自行评估安全风险，并显式传入 `--host 0.0.0.0`，同时配置令牌、反向代理或其他访问控制。

源码启动：

~~~bat
python app\main.py --port 8765
~~~

默认打开 http://127.0.0.1:8765。需要局域网访问时：

~~~bat
python app\main.py --host 0.0.0.0 --port 8765
~~~

## 贡献

请阅读 [CONTRIBUTING.md](CONTRIBUTING.md)。

## License

MIT，详见 [LICENSE](LICENSE)。
