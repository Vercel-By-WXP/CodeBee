# CodeBee

> 把多个 AI 编码 CLI 编排成协作团队。

[快速开始 → QUICKSTART.md](QUICKSTART.md)

## 它解决什么问题

开发者装了多个 AI 编码 CLI，但它们往往彼此孤立。CodeBee 把这些 CLI 统一接入，
按任务路由、执行、验证、评审并在需要时自动修复，最后生成可审计的报告。

## 核心特性

- 跨厂商 CLI 统一调度：Codex CLI、Claude Code、OpenCode、QwenCode、Aider、Kimi Code、
  MiMo Code、Grok Build、Pi、DeepSeek Harness、Gemini CLI、CodeBuddy CLI、Trae Agent、
  MiniMax Code、Mistral Vibe、Cline CLI、OpenClaw（Goose 与 GitHub Copilot CLI 当前仅检测）。
- 智能路由与跨厂商降级。
- 对抗式评审：评审者优先来自不同模型族。
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

## 支持的 CLI

| CLI | 版本要求 | 编排状态 |
| --- | --- | --- |
| Codex CLI | 【占位：按官方当前稳定版】 | 支持 |
| Claude Code | 【占位：按官方当前稳定版】 | 支持 |
| OpenCode / QwenCode / Aider | 【占位：按各项目官方当前稳定版】 | 支持 |
| Kimi Code / MiMo Code / Grok Build / Pi | 【占位：按各项目官方当前稳定版】 | 支持 |
| DeepSeek Harness / Gemini CLI / CodeBuddy CLI | 【占位：按各项目官方当前稳定版】 | 支持 |
| Trae Agent / MiniMax Code / Mistral Vibe / Cline CLI | 【占位：按各项目官方当前稳定版】 | 支持 |
| OpenClaw | 【占位：按官方当前稳定版】 | 支持 |
| Goose / GitHub Copilot CLI | 【占位：按各项目官方当前稳定版】 | 仅检测，暂不自动派发 |

CodeBee 不安装或替换这些 CLI。请先按各项目官方文档完成登录和凭据配置。

## 文档

- [QUICKSTART.md](QUICKSTART.md)：安装、启动和第一个任务
- [ARCHITECTURE.md](ARCHITECTURE.md)：模块、数据流和扩展点
- [docs/routing_strategy.md](docs/routing_strategy.md)：可插拔路由策略接口
- [docs/review_storage.md](docs/review_storage.md)：评审数据结构化存储方案
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

## 测试

~~~bat
python -m unittest discover -s tests -p "test_*.py" -v
python -m unittest discover -s app\tests -p "test_*.py" -v
~~~

测试应使用临时数据目录和独立端口，不要连接生产服务。

## 贡献

请阅读 [CONTRIBUTING.md](CONTRIBUTING.md)。

## License

MIT，详见 [LICENSE](LICENSE)。
