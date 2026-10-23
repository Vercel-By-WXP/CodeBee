## 快速开始

### 1. 前置要求

- Python 3.8 或更高版本。npm 安装方式同样只分发启动垫片，运行时仍需本机 Python。
- Node.js 14 或更高版本。仅 npm / npx 安装方式需要；源码模式不需要 Node。
- 本机至少安装一个受支持的 AI 编码 CLI，并按其官方文档完成登录。
- Windows 可运行 python --version；macOS/Linux 可运行 python3 --version。
- 运行 where codex（Windows）或 command -v codex（macOS/Linux）确认 CLI 在 PATH。

### 2. 安装 CodeBee

两种方式任选其一，功能一致。

**方式 A：npm / npx**

试用或临时运行，不落全局命令：

~~~bash
npx codebee@latest
~~~

> **版本渠道**：当前 `0.y.z` 是**预览版**——次版本之间可能调整 CLI 参数、数据目录结构与
> HTTP 接口形状，不承诺向后兼容；`1.0.0` 起转为**正式版**，按语义化版本承诺兼容性。
> `@latest` 始终是当前推荐版本；想钉住预览通道用 `npm i -g codebee@preview`。

长期使用则安装到全局，之后直接敲 codebee：

~~~bash
npm i -g codebee
codebee
~~~

npm 包不含 Python 解释器，启动时会探测本机 python（Python 3.8+）；探测失败会打印安装指引
并退出，不会写入任何用户配置。用户数据存放在用户目录（Windows `%APPDATA%\CodeBee`，
macOS/Linux `~/.codebee`），不在包目录内，所以 `npm update -g codebee` 升级不会覆盖任务记录与配置。

**方式 B：源码模式**

~~~bash
git clone https://github.com/Vercel-By-WXP/CodeBee.git
cd CodeBee
python app/main.py --port 8765
~~~

源码模式下若仓库内 `data/` 已有内容，CodeBee 沿用它而不迁到用户目录。

Windows 也可运行 start.bat；脚本会启动同一服务。

### 3. 启动参数

默认监听 127.0.0.1:8765 并自动打开浏览器。常用开关：

| 参数 | 作用 |
| --- | --- |
| `--port 18765` | 换端口（8765 被占用时） |
| `--no-browser` | 不自动开浏览器，适合后台/远程运行 |
| `--host 0.0.0.0` | 局域网访问；须同时配置访问令牌与网络访问控制 |

npm 安装方式把这些参数写在 codebee 之后，例如 `codebee --port 18765 --no-browser`。

### 4. 打开 Web UI

浏览器访问 http://127.0.0.1:8765。

默认只监听本机回环地址，避免意外暴露。确需局域网访问时，显式加入
`--host 0.0.0.0`，并先配置令牌和网络访问控制。

### 5. 配置第一个 CLI

打开“智能体管理”或“CLI 绑定”页面，等待 CodeBee 检测 PATH 中的 CLI。

选择已安装的 CLI，启用“参与编排”，按页面提示绑定模型或沿用 CLI 自身配置。

### 6. 跑第一个任务

在新建任务页面输入目标，选择任务类型和工作目录。

点击启动编排；在任务详情中查看规划、执行、验证和评审步骤。

任务结束后，在报告和成果页面查看结果、日志、评审意见与修复记录。

## 常见问题

更多现象（起不来、检测异常、不换将、报告三态怎么读、数据在哪）见
[TROUBLESHOOTING.md](TROUBLESHOOTING.md)。

### CLI 没被检测到怎么办？

先在启动 CodeBee 的同一终端运行 where <cli> 或 command -v <cli>。

若 CLI 刚安装，重启 CodeBee 让服务重新读取 PATH；也可在智能体管理页重新检测。

### Web UI 打不开怎么办？

查看启动终端是否打印“已就绪”，并确认 8765 未被其他程序占用。

可换端口启动：`python app/main.py --port 18765`，再打开对应地址。

### 任务卡住不动怎么办？

先查看任务步骤日志和 CLI 自身的登录/配额状态。

可暂停后重试；瞬态错误会按路由链自动降级，持续失败时请保存脱敏日志提交 Issue。
