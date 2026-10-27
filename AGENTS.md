# Repository workflow

The user requires all development in `E:/GoOut/MultiAgentOrchestration` directly on `main` (2026-10-12).

- Use the primary checkout on `main`; do not create feature branches or development worktrees unless the user explicitly changes this requirement.
- Existing detached worktrees are retained only to preserve old files and logs. Do not resume development in them.
- Multiple chats may share the primary checkout. Preserve others' unfinished edits, coordinate before Git operations that affect shared state, and stage only the files or hunks owned by the current task.
- Do not reset, overwrite, or commit others' unfinished changes. Fetch before pushing and never force-push `main`.
- Port `8765` is the user's production service. Tests must use isolated temporary data and separate test ports; never send test requests to, bind, restart, upgrade, stop, or clear the production service. References to `8765` in unit tests must remain mocked or pure parsing checks (2026-10-16).
- Never use native browser dialogs (`alert` / `confirm` / `prompt`, bare or `window.*`) in frontend code — they render outside the app (title shows `127.0.0.1:8765`) and can be suppressed by the browser. Use the in-app layers instead: `uiConfirm` / `uiPrompt` from `app/ui/app.js` for confirmations and text input, `openModal` for forms. `tests/test_ui_native_dialog_ban.py` fails on any native call (2026-10-21).
- Follow the parent `E:/AGENTS.md` project knowledge and session archive requirements.

## 迭代计划（跨会话交接，强制）

- 所有开发进展以根目录 `迭代计划.md` 为唯一交接单。**每次会话开始先读它**（尤其第 2 节进行中、第 3 节下一步），再做任何事。
- **每次会话结束前必须更新三处**：第 1 节状态表的状态与完成度、第 4 节「已完成」追加一行（日期 / 事项 / 验收方式）、第 3 节「下一步」划掉已完成并补入新发现的坑；并把顶部「最后更新」改成当前时间。
- 判断「做完」的标准是能写出验收方式（可执行命令或可观察结果），不是写了多少代码。写不出验收方式的，留在「进行中」。
- 不新建平行计划文件；方向变了就改这份，并在第 5 节「决策记录」写清为什么改。
