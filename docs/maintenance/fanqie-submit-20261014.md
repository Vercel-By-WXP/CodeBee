# 番茄第 107 章发布提示停滞排查（2026-10-14）

用户截图显示《不周山守村人》的错别字确认弹窗。用户随后确认已手动发布；本次未再次提交章节。用户按 Esc 中止 Computer Use 后，没有继续操作真实浏览器。

## 实际证据

- 活服务来自 `D:/nvm/v24.19.0/node_modules/codebee/app/main.py`，版本 0.1.111；数据目录为 `C:/Users/HP/AppData/Roaming/CodeBee`，不是仓库 data。
- 发布日志 09:27:19 和 11:41:41 均显示 book_id `7682979513168301118`、第 107 章《康养的随访表》失败，错误为 `blocked`。
- 11:41:40 的 `step12-click_in.png` 显示发布提示与提交按钮仍在场。两次失败发生于自动点击定位的遮挡守卫，并非审核等待。
- `click_in` 使用 `[class*=btn]` 向上寻找动作元素，可能把“提交”文字提升成包含取消和提交的按钮组；旧逻辑点击组中心，且只有泛化的 `blocked` 错误。由于用户已手动发布，没有再次采集此弹窗的真实 DOM；按钮组误匹配在隔离模拟页面中复现并修复，不能据此声称已经实机验证下一次发布。

## 实际修改

- `app/core/publish/flow.py`：存在精确文字候选时仅选精确候选；只把文字标签提升到同文案动作元素；在同一卡片内尝试未被遮挡的候选；保留落点遮挡检查，并报告挡路元素。禁止提交被挡时回退到“取消/提交”组容器。
- `tests/test_publish_browser.py`：补按钮组中心误匹配和真实遮罩拒点两项隔离浏览器回归，清理用例间成功标记。
- 本机 npm 安装的 `app/core/publish/flow.py`：只同步上述修改区块，先校验区块与仓库 HEAD 一致；保存原文件到 `flow.py.bak-fanqie-submit-20261014`。未覆盖其他安装代码。

## 验证与结果

- `python tests/test_publish_browser.py`：最终通过，`FAILS: none`。隔离测试服务在浏览器关闭时出现 ConnectionResetError，测试进程退出码为 0。
- `python tests/test_publish_manager.py`：35 项通过，1 项失败（既有七猫流程缺 expect_text 的契约测试，与本次 click_in 修复无关）；没有修改七猫流程。
- `python -m py_compile app/core/publish/flow.py tests/test_publish_browser.py D:/nvm/v24.19.0/node_modules/codebee/app/core/publish/flow.py`：通过。
- `git diff --check -- app/core/publish/flow.py tests/test_publish_browser.py`：通过。
- 独立 code-reviewer 审查：修正了测试成功状态继承和遮罩后按钮组回退问题，最终无阻塞发现。

## 生效边界与协作

首次排查时活服务仍加载旧模块。API 显示两个写作 run 正在运行，因此没有重启；本机同步修复将在下次编排台重启后生效。没有再次发布或补造平台成功回执，没有重新校准已发台账；首次排查阶段尚未提交或推送 Git。保留其他聊天在 checkpoints、publish/auto、publish/ledger、settings、UI 等文件的在途修改。

可复用经验：发布排查先定位活服务路径与数据目录；文字标签和整个按钮组都可能匹配 btn 类名，提升动作元素必须验证文案一致；精准候选被遮挡时不能回退到更大容器。模拟验证与真实平台验证应分别陈述。

## 11:55 生效复核

再次请求活服务的 `/api/runs` 与两个 `/api/runs/<id>`：两个写作 run 均仍为 running，分别处于 draft-c5 和 draft-c6。通过 Win32_Process 复核服务 PID 27764 与其写作后代 PID 39844、51976 均仍存活；这是真实任务等待，不仅依据旧状态文件。服务启动于 11:38:57，修复文件修改于 11:51:10；安装副本已包含精确候选过滤和同文案动作提升，但原服务未重启。安全加载修复仍待当前任务结束，goal 保持 active。本轮没有操作浏览器、再次提交章节、重启服务或 Git 操作。

## 12:01 阻塞审计

连续三轮 goal continuation 均核实同一安全重启前置条件未满足，期间也通过具体作业进程句柄进行了等待复核。最新两个 run 仍处于 draft-c5 / critique-c6，实际后代进程为 claude PID 39844、opencode PID 25644，服务仍是 11:38:57 启动的 PID 27764。当前安装副本 click_in 区块逐字比对与审查通过的仓库修复一致。

完成审计：用户已手动发布的章节不再提交；历史失败原因和截图已定位；按钮组误匹配修复、隔离浏览器测试和代码审查已完成；npm 安装副本已备份并同步。缺少的是活服务加载修复与真实平台下一次使用验证，不能把文件同步冒充运行时生效。当前进程没有已有的发布模块热重载接口；重启会中断其他正在执行的任务，需等待外部任务结束或用户明确改动其执行约束。此为共享活服务的部署前置条件阻塞，goal 标为 blocked，不标为 complete。后续恢复时必须重新读取 `/api/runs` 与实际进程，待全部执行任务结束且发布平台非 busy 后才重启，并验证新服务启动时间晚于修复文件时间。

## Git 交付

用户后续要求完成并提交、推送 Git。交付范围限定为 `app/core/publish/flow.py`、`tests/test_publish_browser.py` 和本记录；其他聊天在途文件不纳入提交。提交前再次核对上述代码差异未变，语法检查与 owned 文件差异检查通过；已有隔离浏览器回归与独立审查结果继续适用。直接在共享主 checkout 的 main 提交，使用明确 pathspec 隔离其他文件，推送前 fetch，禁止 force-push。运行时生效边界保持如上，不把 Git 推送等同于活服务已重载。
