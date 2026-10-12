# 手机远程工作台适配（2026-10-12）

根据用户提供的 CodeBee 手机截图及 ZCode 参考图调整手机入口。≤900px 主区为单列；
导航轨与侧栏一起抽屉化，不再占用 42px。工作区卡片按现有任务目录分组，显示路径、
任务数量、状态与时间，任务复用原详情入口，目录「+」带入完整工作路径。
有任务时优先展示工作区；新任务和返回工作区切换紧凑输入页；空任务直接显示输入页。
手机取消桌面居中高度与大留白，输入字号 16px，主操作热区至少 44px，使用安全区边距。

本次修改：`app/ui/index.html`、`app/ui/app.js`、`app/ui/style.css`、`app/ui/i18n.js`、
`docs/ui-design.md`、`tests/ui_mobile.mjs`、`tests/ui_mobile_flow.mjs`、
`tests/ui_mobile_workspaces.mjs`、`.gitignore`（放行新测试）及本记录。
保留工作区已有修改，不提交或发布其他会话的改动。

验证：

- `node tests/ui_mobile_workspaces.mjs`：29 项通过，含真实 CDP 触摸、320/390/430/768/900px、
  路径带入与 HTML 转义、展开态保持、任务详情、空态、桌面恢复、窗口缩窄自动折叠。
  同时运行 `tests/ui_mobile.mjs`：6 项通过（抽屉/图标/设置/手机连接）。
- `node --check app/ui/app.js`、`node --check app/ui/i18n.js`、
  `node --check tests/ui_mobile_workspaces.mjs`：通过。
- `python -m unittest tests.test_mobile_chat_ui`：2 项通过。
- `git diff --check`：通过。

环境：Windows Edge headless + CDP，手机逻辑视口 DPR=1，桌面恢复至 1416×908（窗口
请求 1440×1000；视口以实际截图为准）。测试使用临时数据目录与本地服务，未执行任务。
截图：`output/playwright/mobile-workspaces.png`、`mobile-composer.png`、
`mobile-desktop-regression.png`。未在真机 Safari/微信内置浏览器实测。

可复用经验：移动端规则必须位于桌面皮肤/密度层之后，重要控件要核对选择器优先级。
目录「+」应置于原生 `<details>` 外侧；放在非 summary 内容内会随折叠被浏览器隐藏，
即使几何尺寸存在也无法命中。验收需用 `elementFromPoint` 与真实触摸检验。
