# 工作 / 开发场景选择（2026-10-12）

用户参考 Codex、ZCode、QCoder 的顶栏产品选择器，希望桌面版支持多种模式选择。本轮
采用两个本地 UI 场景：

- **工作**：默认「直接执行」，快捷入口为直接执行、文档、调研、翻译；首页提示写作、调研、文档与翻译。
- **开发**：默认「代码」，快捷入口为代码、缺陷复盘、技术方案、直接执行；首页提示编码、调试、测试与评审。

场景是现有任务/引擎的 UI 偏好，不新增后端模式。选择保存在 `orch.workspace-mode.v1`，
按场景记忆最近任务类型。切换时保留目标、目录、上下文、附件和编排档位；已有任务仍全部显示。
菜单使用可访问的 radio menu，并在 `body` 中以固定浮层定位，避免桌面侧栏和移动抽屉的 overflow 裁剪。

修改文件：`app/ui/app.js`、`app/ui/index.html`、`app/ui/workspace-mode.css`、`app/ui/i18n.js`、
`.gitignore`、`tests/ui_workspace_modes.mjs`、`tests/ui_desktop_workbench.mjs`、`docs/ui-design.md`。

验证：`node tests/ui_workspace_modes.mjs`（24 项）、`node tests/ui_desktop_workbench.mjs`、
`node tests/ui_mobile_workspaces.mjs`（29 项及附带 6 项抽屉回归）、`node --check app/ui/app.js`、
`node --check tests/ui_workspace_modes.mjs`、`node tests/_chk_i18n_dups.mjs`（3200 键，无重复）、
`git diff --check` 均通过。截图在 `output/workspace-modes/`，使用独立验收数据。
