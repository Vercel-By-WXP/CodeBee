# CodeBee 竞品能力巡检（2026-10-13）

## 本轮查阅的公开来源

- [OpenHands 1.27.0 release](https://github.com/All-Hands-AI/OpenHands/releases/tag/1.27.0)：2026-10-12，新增自动化设置分步面板、AI 辅助自动化表单更新，以及持久化 automation draft 管理。
- [Cline CLI 3.0.71 release](https://github.com/cline/cline/releases/tag/cli-v3.0.71)：2026-10-13，修复排队任务不会启动、模型调用失败却静默成功等调度可靠性问题，并保留最近一次错过的定时触发。
- [Zed 1.24.1-pre release](https://github.com/zed-industries/zed/releases/tag/v1.24.1-pre)：2026-10-07，改进 Agent Panel 长线程资源占用、计划更新可见性与终端拖入 Agent Panel。
- [CodeBee 上一轮竞品对照](competitive-codebee-2026-10-12.md)：已核对 Codex、Cursor、Windsurf/Cascade、Cline/Roo Code、Claude Code、Copilot、Goose、OpenHands、Devin、Replit Agent 等公开资料；本轮不重复实现已有的暂停闸、检查点、跨厂商降级和自动化调度能力。

## 选中的改进

OpenHands 的自动化草稿能力最适合 CodeBee 当前形态：CodeBee 的定时任务表单包含长提示词、流程、运行偏好和时间字段，关闭弹窗或刷新页面时原内容会丢失。把草稿放在浏览器本地可避免新增服务端协议、不会把未提交内容写入任务列表，也不影响已有任务编辑。

本轮在新建定时任务表单中加入：

1. 输入和选择项变更后，延迟 180ms 自动保存到 `localStorage`。
2. 下次打开新建表单时恢复草稿并显示提示；可手动清除。
3. 创建请求成功后清除草稿；请求失败或点击取消时保留，方便继续编辑。

## 修改与验证

- `app/ui/app.js`：草稿读取、节流保存、恢复提示、清除按钮及成功创建后的清理。
- `app/ui/i18n.js`：新增中英文文案。
- `tests/test_automation_draft_ui.py`：验证字段绑定、恢复入口和成功保存后的清理契约。

验证命令：

- `node tests/check_ui_syntax.mjs`：全部 UI JavaScript 通过。
- `node tests/automation_draft_runtime.mjs`：草稿保存、恢复、关闭及表单切换竞态通过。
- `python -m unittest tests.test_automation_draft_ui -v`：2/2 通过。
- `python -m unittest discover -s tests -p 'test_i18n_key_coverage.py' -v`：3/3 通过。
- `python -m unittest discover -s tests -p 'test_automation.py' -v`：21/21 通过。
- `python -m unittest discover -s tests -p 'test_auto.py' -v`：5/5 通过。
- `git diff --check`：通过（工作区另有其他聊天的换行提示）。

## 风险与下一轮

草稿依赖浏览器本地存储；清理浏览器站点数据或隐私模式会丢失草稿，存储配额不足时会静默退回普通表单。草稿不跨设备同步，也不覆盖已存在的服务端任务。下一轮优先调查执行链中既有的两项失败测试（`test_direct_steering` 的真实步骤与暂停闸），并继续避开其他聊天正在修改的文件。
