# CodeBee 桌面预览版

复用现有 Web 界面和 Python 引擎，支持 **Windows 10/11 x64**、**Windows 7 x64（预览）**、**macOS 13+ Intel x64 / Apple Silicon arm64**。安装包携带 Python；运行 CodeBee 本身不要求用户安装 Node.js/Python。使用外部 AI CLI 时，仍需安装相应 CLI 及它的运行环境。

关闭窗口会隐藏到托盘/菜单栏，任务继续执行。托盘菜单、应用菜单可重新打开；明确退出时，有正在执行的任务会询问是否停止。系统休眠、关机或退出应用后不保证持续执行；重新启动沿用引擎已有的任务恢复规则。

数据继续使用 Windows `%APPDATA%\CodeBee`、macOS `~/.codebee`，旧 Tutti 目录仍沿用，`TUTTI_DATA` 可指定独立目录。升级、重装和卸载均不主动删除这些数据。桌面版和 CLI 共用数据目录时只能运行一个引擎；桌面端 sidecar 默认绑定 **8766**（窗口地址跨重启稳定，界面偏好/扫码记忆不丢），被占自动回落随机端口——绝不与 CLI 的 8765 冲突；当数据目录已被 CLI/另一实例占用时，sidecar 会输出 `CODEBEE_DESKTOP_ATTACH` 并退出，桌面窗口直接**接管连接到正在运行的服务**（带持久化令牌），不再报错，也不会杀掉它。

## 本地开发

构建环境要求 Node.js 22.12+，Windows 系统 tar 或 macOS tar。开发也使用随包携带的 Python，避免系统 Python/Conda 差异。

```sh
cd desktop
npm ci
npm run prepare:runtime
npm test
npm start
```

`prepare:runtime` 下载固定版本 python-build-standalone，验证官方资产 SHA256 后解包，执行 ssl/sqlite3/ctypes 与平台架构自检。Python 不放进 asar，内部调用 `sys.executable` 保持正常工作。macOS Finder 启动时读取登录 shell 的 PATH，并补齐 Homebrew、用户 npm、cargo 和 local bin 路径。

测试不会启动用户的现有服务：

```sh
npm run smoke
```

smoke 使用独立临时数据与用户目录、动态端口，验证实际窗口加载、Node 隔离、后台隐藏、重复启动、原生文件选择往返、禁用 npm 自更新、引擎退出，并输出 `dist/smoke/desktop.png` 和 `result.json`。

## 原生安装包

每个架构在对应原生系统构建，避免打包错误架构的 Python：

```sh
# Windows x64
npm run prepare:runtime -- win32 x64
npm run dist:win

# macOS Apple Silicon
npm run prepare:runtime -- darwin arm64
npm run dist:mac:arm64

# macOS Intel
npm run prepare:runtime -- darwin x64
npm run dist:mac:x64

# Windows 7 x64（预览）：Electron 22.3.27 + Python 3.8.10 embeddable
npm run dist:win7
```

生成 `dist/CodeBee-<版本>-win-x64.exe`、`CodeBee-<版本>-win7-x64.exe`、`CodeBee-<版本>-mac-arm64.dmg`、`CodeBee-<版本>-mac-x64.dmg` 与 Mac zip。构建脚本检查引擎/桌面版本一致、Python 架构与固定摘要一致；配置排除测试、数据和缓存。源码中的 `app/ui` 改动直接进入新安装包。

GitHub Actions 的 `Desktop preview` 工作流提供四个原生构建任务（win-x64 / mac-arm64 / mac-x64 / win7），先做引擎生命周期测试和桌面 smoke，再生成安装包并测试打包后的应用，只上传预览 artifacts，不创建 Release、不自动发布。共享工作区的其他在途改动不能作为正式交付基线。Win7 构建使用 Electron 22.3.27（最后支持 Win7/8/8.1 的线）+ Python 3.8.10 embeddable 运行时，`before-pack` 按 Electron 大小号强制校验运行时档位；Electron 22 主进程无全局 fetch，桌面代码统一走 `node:http`。

## 更新与正式发布

首版使用应用菜单/托盘里的“下载桌面新版”，打开官方 Releases。保存工作并退出后安装新版，数据目录保留；桌面运行模式拒绝 npm 覆盖安装和后端自行重启。当前不宣称实现了自动更新或自动回滚。

预览构建不代表正式发行验收完成。正式发布前需要固定、干净的代码基线通过全量回归，以及三个架构的干净机器安装、升级旧数据、后台运行、异常退出恢复验收。

Mac 正式发行需要 Apple Developer ID 证书与公证凭据；配置保留 hardened runtime，打包后自动将 Python 的所有 Mach-O 可执行文件和扩展库加入签名列表。使用 electron-builder 26 的 `mac.binaries` 字段。正式构建可配置 `CSC_LINK`/`CSC_KEY_PASSWORD` 和 `APPLE_ID`/`APPLE_APP_SPECIFIC_PASSWORD`/`APPLE_TEAM_ID`，或官方支持的 App Store Connect 凭据，并在发布流程启用公证。Windows 正式发行应使用代码签名证书。

无签名、公证凭据的内部预览不能声称消除了 macOS Gatekeeper 或 Windows SmartScreen 提示，也不能代替正式安装验收。
