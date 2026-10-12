# 桌面工作台参考图布局优化（2026-10-12）

用户提供 Codex、ZCode、QCoder 截图，要求优化桌面版样式和布局。在主 checkout 的 main
直接实现，保留其他会话改动。用户随后要求提交 Git 并推送，本次仅收录下列六个文件，
不发布安装包。

实际修改：

- `app/ui/desktop-workbench.css`：独立桌面样式层，>900px 生效；保留可拖宽项目栏，
  对话最大宽度 820px，输入框沿相同阅读轴贴底，任务栏稳定停靠右侧；修复宽屏固定浮卡
  与旧 `.page-wide` 宽度限制产生的布局冲突。矮窗口任务操作可滚动，弱化装饰与阴影，
  经典/海洋深色使用中性石墨灰，其余皮肤沿用配色。
- `app/ui/index.html`：在共享样式之后引入桌面样式文件。
- `tests/ui_desktop_workbench.mjs`：使用既有 Edge/CDP 引导和独立临时数据，验证对话内部
  滚动、输入框可见、任务栏无重叠、宽屏阅读宽度、右缘停靠、矮窗口、侧栏拖宽和换肤。
  不执行 AI CLI；截图消息明确标记为示例数据。
- `.gitignore`：允许追踪新的桌面回归文件。
- `docs/ui-design.md`：同步桌面布局规则和验收命令。

验证结果：

- `node tests/ui_desktop_workbench.mjs`：通过；DPR=1，显式设置 reduced-motion，
  CDP 校准实际视口 1920/1600/1440/1366/1024/920×900，以及 1366×600。
- `node tests/ui_mobile_workspaces.mjs`：29 项通过，附带原有抽屉图标测试 6 项通过。
- `npm test`（desktop）：5 项通过。
- `node --check tests/ui_desktop_workbench.mjs`、`git diff --check`：通过。
- `node tests/_chk_i18n_dups.mjs`：3190 个键，无重复。本轮未添加产品文案。
- `npm run smoke`（desktop）：未完成。当地尚无 Electron 二进制，命令进入运行时下载后
  长时间没有完成，停止本轮拥有的下载/启动进程。不能据此宣称原生窗口 smoke 已通过。

截图：`output/desktop-workbench/home.png`、`chat-light.png`、`chat-dark.png`、
`chat-1366.png`、`chat-collapsed.png`。新安装包需后续构建，已安装版本不会自动包含源码改动。

可复用经验：旧桌面 CSS 同时包含宽屏浮卡、详情网格、`.page-wide` 和 `:has()` 宽度帽，
仅覆盖卡片定位不足以保证右缘停靠；回归应检查整个祖先宽度链与实际几何位置。
