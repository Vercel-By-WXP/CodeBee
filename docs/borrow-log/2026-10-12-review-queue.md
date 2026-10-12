# 人工评审队列收口（2026-10-12）

交接中明确的剩余项是：外部人工评审已有持久化状态机与 API，但普通用户没有操作入口。本次在「设置 → 本机智能体」补齐队列，沿用现有面板、弹窗与主题变量，不引入前端依赖。

## 实现与边界

- 加载/刷新、待处理/全部/批准/关闭/过期筛选，按最近更新时间排序。
- 新建请求：运行 ID（支持最近运行补全）、摘要、文件路径与渠道标签。
- 分配评审人、批准、要求修改、关闭与标记过期；按钮严格按后端状态机开放，批准后的请求不能重新分配。
- 分配时必须填写评审人，要求修改时必须填写意见；路径限额与 API 对齐。提交期间禁用保存并阻止重复提交，刷新序号阻止旧请求覆盖新数据。
- 摘要、渠道、评审人、路径、意见与 ID 均经 HTML 转义；API 错误留在表单内，刷新失败保留已加载队列。
- 中英文词条、表单标签、状态播报、窄屏折行齐备。渠道是记录标签；创建不会发送外部消息，批准不会自动发布。

修改文件：`app/ui/index.html`、`app/ui/app.js`、`app/ui/style.css`、`app/ui/i18n.js`；新增 `app/tests/test_review_queue_ui.py`、`tests/review_queue_logic.test.mjs`、`tests/ui_review_queue.mjs`。

## 验证

共享工作区同时有活动视图与 Hive3D 在制品，因此按差异块暂存本次改动，再用 `git checkout-index` 导出准确的暂存快照进行独立验证。其他在制品没有纳入本次提交。

| 检查 | 命令/范围 | 结果 |
|---|---|---|
| UI 行为 | `node --test tests/review_queue_logic.test.mjs` | 9/9；过滤、排序、转义、表单、限额、错误与并发 |
| 真实浏览器/API | `node tests/ui_review_queue.mjs` | 20/20；临时数据目录、Edge DPR=1、真实创建与全部状态流转、持久化、英语、390px 卡片宽度 |
| 应用测试 | 在快照 `app/` 执行 `python -X utf8 -m unittest discover -s tests -p 'test_*.py'` | 210 项，OK，6 项跳过 |
| HTTP/治理契约 | discover `test_http_contract_routes.py` 与 `test_agent_operating_contracts.py` | 10/10 + 6/6；同时复核既有检查点、能力清单、记忆预算/来源与评审幂等契约 |
| 英文覆盖/重复键 | 快照根目录 discover `test_i18n*.py` | 6/6 |
| 语法/差异 | `node --check` 两份 JS；`git diff --cached --check` | 通过 |

根目录全量也实际跑完：`python -X utf8 tests/_guard_fullrun.py 'test_*.py'`，2353 项，36 failures / 6 errors / 2 skipped，退出码 1，**不能声称全量通过**。该运行跨越了本轮文件写入窗口，其中一个英文覆盖失败读到了尚未写齐的版本；最终暂存快照的六项英文检查已经全部通过。其余失败涉及既有 catalog/启动、取消与恢复、预算调用缺 task_id、默认配置、小说/技能提示词、Qwen 注入、更新与节假日数据等；先前调研记录也已记载这些域的失败。本次没有用删测试或放宽断言掩盖这些红灯。

完整输出保留在本机临时目录 `C:/Users/HP/AppData/Local/Temp/codebee-review-audit-717fa0a695d84c5094c430ae21d74923/` 下（`root-tests.log`、`app-tests.log`、`snapshot-app-tests.log`）。最终隔离浏览器截图位于 `C:/Users/HP/AppData/Local/Temp/codebee-review-ui-Gpcy8F/review-queue.png`。未运行 npm publish，未调整版本号。

复用经验：在共享工作区提交前，先筛选本人差异块，再用暂存快照验证；实时文件变化造成的中间态测试失败要在最终快照中重新核验，不能直接归入历史基线。
