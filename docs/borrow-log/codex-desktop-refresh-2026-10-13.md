# Codex 风格桌面版刷新（2026-10-13）

用户提供 Codex 与 CodeBee 桌面截图，反馈当前桌面版层级、颜色和任务详情卡观感不佳，要求
参考 Codex 重做。保留现有任务、运行详情、模式选择和移动端 DOM/交互，只在桌面媒体查询中
刷新视觉层：

- 46px 窄导航轨，使用低对比灰底和明确的蓝色选中态；
- 264px 可调任务侧栏，品牌栏、项目标题、任务行和底部用量按 Codex 密度重新分层；
- 白色主画布、54px 顶部工具栏、居中 820px 阅读列；
- 右侧运行详情改为固定宽度的白色圆角卡，输入框与内容列共用阅读轴；
- 深色主题补充石墨色覆盖，避免浅色硬编码破坏夜间模式。

验证命令：

- `node tests/ui_desktop_workbench.mjs`：桌面宽度、阅读列、右栏停靠、矮窗口、深色换肤和
  Codex 壳层尺寸检查通过；
- `node tests/ui_mobile_workspaces.mjs`：29 项及附带 6 项移动端抽屉回归通过；
- `node --check app/ui/app.js`、`git diff --check`：通过。

截图：`output/desktop-workbench/chat-light.png`、`chat-dark.png`，使用独立验收数据。
