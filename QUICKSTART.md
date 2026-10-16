## 快速开始

### 1. 前置要求

- Python 3.8 或更高版本。
- 本机至少安装一个受支持的 AI 编码 CLI，并按其官方文档完成登录。
- Windows 可运行 python --version；macOS/Linux 可运行 python3 --version。
- 运行 where codex（Windows）或 command -v codex（macOS/Linux）确认 CLI 在 PATH。

### 2. 获取 CodeBee

~~~bash
git clone https://github.com/Vercel-By-WXP/CodeBee.git
cd CodeBee
~~~

### 3. 启动

源码模式直接运行：

~~~bash
python app/main.py --port 8765
~~~

Windows 也可运行 start.bat；脚本会启动同一服务。

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

### CLI 没被检测到怎么办？

先在启动 CodeBee 的同一终端运行 where <cli> 或 command -v <cli>。

若 CLI 刚安装，重启 CodeBee 让服务重新读取 PATH；也可在智能体管理页重新检测。

### Web UI 打不开怎么办？

查看启动终端是否打印“已就绪”，并确认 8765 未被其他程序占用。

可换端口启动：`python app/main.py --port 18765`，再打开对应地址。

### 任务卡住不动怎么办？

先查看任务步骤日志和 CLI 自身的登录/配额状态。

可暂停后重试；瞬态错误会按路由链自动降级，持续失败时请保存脱敏日志提交 Issue。
