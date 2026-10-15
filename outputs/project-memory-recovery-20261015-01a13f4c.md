# 项目记忆为空：修复与恢复记录

日期：2026-10-15（Asia/Shanghai）

## 结果

真实服务 `http://127.0.0.1:8765/api/memory` 已由 0 个工作区恢复到 **9 个工作区、178 条项目记忆**：迁移旧 Markdown 31 条，从历史完成运行补回 147 条。全部是待批准候选，没有自动批准或注入。逐个工作区核对任务详情记忆接口，数量与记忆中心一致；注入预览为空，符合审批要求。第二次回填新增 0 条。

## 根因

- 原有候选记忆写入仅在代码引擎收尾执行，写作与直连引擎遗漏。真实活动任务中，27 个使用 review、3 个使用 direct、2 个使用 code；历史完成编排运行没有成功完成的 code 引擎。
- 历史代码记忆保存在 `.codebee/project-memory.md`，新治理界面只读 `.codebee/project-memory.jsonl`，没有迁移。
- 写入异常原先被静默吞掉，无法区分没有记录与保存失败。

## 本次源码改动

- `E:/GoOut/MultiAgentOrchestration/app/core/project_memory.py`：旧格式迁移、各引擎完成记录生成、批量幂等回填；使用运行时固化快照，保留历史时间和过期规则；完整流式读取日志以保留较早审批结果；每个工作区批量回填只读一次日志。
- `E:/GoOut/MultiAgentOrchestration/app/core/pipeline.py`：以公共 finally 收尾沉淀各引擎候选，失败、取消、未完成 partial 不产生完成记忆；写作/直连同样读取已批准记忆；保存失败写入运行 warnings。
- `E:/GoOut/MultiAgentOrchestration/app/main.py`：加载任务与运行后自动迁移和回填历史项目记忆。
- `E:/GoOut/MultiAgentOrchestration/tests/test_project_memory_lifecycle.py`：15 项生命周期回归，包含真实 mock 流水线、审批、过期、5000 行以外历史决策、历史快照和批量读取次数。
- `E:/GoOut/MultiAgentOrchestration/tests/test_pipeline.py`：将旧的未审批 Markdown 附件上下文测试数据改为已批准 JSONL 记忆。

主检出保持 main，没有执行分支、worktree、暂存、提交、推送操作。共享检出中其他会话的未完成修改均保留。

## 验证

- `python -m py_compile app/core/project_memory.py app/core/pipeline.py app/main.py tests/test_project_memory_lifecycle.py tests/test_pipeline.py`：通过。
- `git diff --check`：通过（仅出现已有 LF/CRLF 提示）。
- 生命周期、direct 引擎、附件上下文联合回归：24 项，23 通过，1 项因 Windows 无符号链接权限跳过。
- `test_agent_governance`、`test_agent_operating_contracts`：17 项全部通过。
- `test_competitive_features` 中 memory 相关接口、审批与过期回归：7 项，6 通过，1 项符号链接权限跳过。
- 合计上述定向回归 48 项，46 通过、2 跳过。
- 隔离数据目录、随机端口启动完整 HTTP 服务：启动时自动恢复 1 条旧记录及 1 条完成记录，`/api/memory` 显示 1 个工作区、2 条待批准且指纹正确的记忆。冒烟后只终止本次测试进程。
- 扩大回归发现 `TestSerialDelayedFallbackRoute`、`TestDirectiveInjection`、`TestPauseGate` 3 项失败；在内存中加载 Git HEAD 原版 pipeline、保留其余环境后，同样复现全部 3 项，确认并非本轮记忆改动引入。没有修改这些独立问题。
- 按 Python review 技能做独立只读审查；已修复审查指出的历史审批去重窗口、历史任务输入漂移和批量回填重复扫描问题，最终无 CRITICAL/HIGH 阻塞。

## 数据与生效范围

已将恢复内容追加到实际工作区的 `.codebee/project-memory.jsonl`。旧 Markdown 原样保留。恢复后对其中 24 条仍待批准候选追加版本更新，使内容与运行固化快照一致；未覆盖任何已审批记录，所有指纹通过真实接口校验。

6 个历史任务指向两处已不存在的旧实验目录 `E:/GoOut/tutti-novel-lab`、`E:/GoOut/tutti-novel-qimao`，记录警告并跳过，未创建这些目录；其他工作区正常恢复。

真实服务仍有任务运行，本次未重启。**178 条恢复记忆已经可见；源码中的后续自动沉淀和启动回填逻辑在下次服务重启后生效。**

## 可复用经验

记忆功能应同时检查生产、持久化格式迁移、列表发现、审批和注入；不能将经验库/知识库有数据当作项目记忆链路已接通。历史回填必须使用运行时快照，不能把任务后来的编辑当作历史事实。去重索引不能丢弃显示窗口之外的审批记录，批量恢复也不应对每条候选重复扫描整个文件。

## 实际恢复的数据文件

- `C:\Users\HP\AppData\Roaming\workspace\.codebee\project-memory.jsonl`
- `E:\AI小说\不周山守村人\.codebee\project-memory.jsonl`
- `E:\AI小说\头七结账人\.codebee\project-memory.jsonl`
- `E:\AI小说\小说\.codebee\project-memory.jsonl`
- `E:\AI小说\我的马甲藏不住了\.codebee\project-memory.jsonl`
- `E:\AI小说\神瞳鉴宝：我在当铺断真假\.codebee\project-memory.jsonl`
- `E:\AI小说\选题\.codebee\project-memory.jsonl`
- `E:\GoOut\MultiAgentOrchestration\.codebee\project-memory.jsonl`
- `E:\项目知识库\.codebee\project-memory.jsonl`

## Follow-up fixes and delivery

Fixed malformed UTF-8/Unicode/source isolation, reviewed ID preservation, decision/feedback locking, workspace-move protection, memory lookup after checkout, per-workspace index caching; fixed completion/persistence/cross-task refresh, archived run refresh/preview and stale responses.

Verified a clean HEAD test snapshot with staged changes only: Python 35 run, 34 pass, 1 Windows symlink privilege skip; Node UI regression and syntax pass. Isolated HTTP startup preserves the malformed row, imports legacy and completed run entries, and projects project_memory_id. Live browser: 9 projects / 178 entries; archived run detail 34 rows. Both independent reviewers approved.

Commit 5769860777933b45658500d99602f9738a45749b pushed to main; ls-remote matches. Used the existing enabled system proxy through per-command git -c after direct GitHub connection failed. Other chats changes retained. Live service not restarted.
