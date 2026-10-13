# 2026-10-13 展开对话布局修复

- 修改文件：`app/ui/style.css`、`tests/ui_chat_expanded.mjs`。
- 根因：展开态仍被普通运行详情的 `width: 100%` / `height: 100%` 规则覆盖；固定定位加上 12px inset 后超出视口，详情背景透明使聊天文字叠到侧栏。
- 修复：展开态以视口 inset 决定宽高，使用实底；对话与任务栏使用限定高度的网格，时间线内部滚动；窄屏展开时隐藏任务栏；工作区的窄列布局限制行高并保持输入框可见。
- 验证：`node tests/ui_chat_expanded.mjs` 通过，覆盖 1920x1080、1366x768、1024x600、390x844，工作区关闭/打开、深色主题、展开后收起恢复；修改前探针在 1920x1080 复现面板越界至 1932x1092 且背景透明。
- 验证：`node tests/ui_desktop_workbench.mjs` 通过；`git diff --check -- app/ui/style.css` 通过。截图位于本地忽略目录 `output/playwright/chat-expanded/`，已检查桌面与手机工作区布局。
- 经验：固定定位的展开视图必须覆盖常规详情页的尺寸和背景规则，CSS 特异性要同时考虑包含多个 ID 的通用设置页选择器；仅添加 fixed/inset 不足以形成独立视图。
- 提交纪律：直接在主 checkout 的 main 修改，只暂存展开态 CSS hunk和本次测试/记录，保留其他聊天的在途改动。
