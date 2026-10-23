# 仓库巡检报告（2026-10-23）

## 变更概览
- 分支为 `main`，与 `origin/main` 对齐。报告生成前有 9 个已跟踪文件未提交，`git diff --stat` 为 79 行新增、27 行删除；另有未跟踪的 `app/ui/demo_artifact_chips.html`。这些现存改动均保留。
- 发布流程改动涉及 `app/core/publish/flow.py`、`ledger.py`、`manager.py`：兼容页面脚本返回对象/旧 JSON 字符串、记录截断后的步骤日志，并按实际流程键记账。
- UI 改动涉及 `app/ui/app.js`、`style.css`、`desktop-workbench.css`、`redesign-layout.css`、`reference-composer.css`、`workspace-mode.css`，集中在产出文件 chip 和参考输入区样式。
- 最近 15 条提交主线集中于 CLI/评审失败恢复、番茄与七猫短篇发布链，以及任务时间线、快捷导航和工作台 UI；当前 HEAD 为 `607bcd7`。
- 本报告是本次唯一新增文件；没有修改或暂存源码，也未运行测试。

## 待办与风险
- 源码扫描没有发现真实悬挂的 `TODO`、`FIXME` 或 `HACK`。命中均为自动化任务说明（`app/core/automation.py:776`、`:781`，`app/ui/i18n.js:2819`，`tools/i18n_add_batch3.py:86`）、历史案例/提示文本（`app/core/error_codes.py:172`、`app/core/pipeline.py:1736`）或名为 `todo` 的业务字段。
- **CLI 兜底未触发。** 失败运行 `r-20261023-141509-2253` 的 Codex 日志只有忽略配置项事件，没有可读的模型答复。CLI 以非零码退出后，`app/core/cli_parsers.py:102` 的 `_codex_fail_msg` 不识别 `item.completed` 内的 `item.type=error`；错误因此落为通用 `VENDOR_ERROR`（`app/core/runner.py:1624`），而默认换 CLI 白名单不包含该类型（`app/core/routing_strategy.py:47-74`）。所以起草失败后虽然继续评审修订，发布门禁仍正确保持关闭。配置告警是否是进程退出的唯一原因，现有日志不足以证明。
- 该巡检自动化的交付契约要求 `INSPECTION.md`，失败运行评审却发现稿件落在 `report.md`；应检查目标文件是否存在，而不能只看流程报告生成。此次由本报告补齐指定文件名。
- 未跟踪的 `app/ui/demo_artifact_chips.html` 没有匹配忽略规则；`package.json` 将整个 `app` 纳入包，而 `app/.npmignore` 尚未排除此文件，存在设计 demo 随 npm 包发布的风险。
- `app/ui/apple_skin_hotfix.js:107-110` 和 `app/ui/apple_inject_console.js:127-130` 含手动注入脚本的 `console.log`，在应用源码中未找到引用。根目录 `Temp/` 下的 probe、临时 diff 等文件仍被 Git 跟踪，且 `.gitignore` 未忽略 `Temp/`；它们更像维护/调试材料，归属和是否保留需确认。
- `app/main.py` 与少数核心模块中的 `print` 主要是启动状态、CLI 输出或运行诊断；本轮未发现其它明显的生产路径调试输出。`tmp_check_*.js` 命中忽略规则，不在未提交文件列表中。

## 今日建议
- 为 Codex JSONL 的嵌套错误事件补解析和回归测试，并将可恢复的 CLI/配置启动失败归入允许换 CLI 的错误类别；不要把“忽略配置项”告警直接等同于模型服务故障。
- 检查用户级 `~/.codex/config.toml` 中被当前 Codex CLI 忽略的键，按安装版本迁移或移除；同时保留完整事件日志，以确认是否还有被截掉的终态错误。
- 自动化在判定完成前应核验 `INSPECTION.md` 等合同指定产物的存在和非空；失败的起草可以保留评审稿，但不得把 `report.md` 当成目标文件已交付。
- 不要清理、回滚或提交当前共享工作区的发布与 UI 改动。先确认未跟踪 demo 的归属，并决定将其从包清单排除还是作为正式资源纳入。
- 对已跟踪的 `Temp/` 调试材料及无运行时引用的手动注入脚本，先确认维护者和用途，再单独归档/排除；本轮按只读要求未作清理。
