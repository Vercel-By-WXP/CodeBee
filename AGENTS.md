# Repository workflow

The user requires all development in `E:/GoOut/MultiAgentOrchestration` directly on `main` (2026-10-12).

- Use the primary checkout on `main`; do not create feature branches or development worktrees unless the user explicitly changes this requirement.
- Existing detached worktrees are retained only to preserve old files and logs. Do not resume development in them.
- Multiple chats may share the primary checkout. Preserve others' unfinished edits, coordinate before Git operations that affect shared state, and stage only the files or hunks owned by the current task.
- Do not reset, overwrite, or commit others' unfinished changes. Fetch before pushing and never force-push `main`.
- Port `8765` is the user's production service. Tests must use isolated temporary data and separate test ports; never send test requests to, bind, restart, upgrade, stop, or clear the production service. References to `8765` in unit tests must remain mocked or pure parsing checks (2026-10-16).
- Never use native browser dialogs (`alert` / `confirm` / `prompt`, bare or `window.*`) in frontend code — they render outside the app (title shows `127.0.0.1:8765`) and can be suppressed by the browser. Use the in-app layers instead: `uiConfirm` / `uiPrompt` from `app/ui/app.js` for confirmations and text input, `openModal` for forms. `tests/test_ui_native_dialog_ban.py` fails on any native call (2026-10-21).
- Follow the parent `E:/AGENTS.md` project knowledge and session archive requirements.
