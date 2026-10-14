# 断点续接班报告（round-resume-report.md）

> **班次**：2026-10-14 14:44 起（UTC+8），hour=14、14%7=0 → **轮换批7（中文/网关/本地/办公）**。
> 本班系编排计划「核对中断现场并完成全类型调研」第 1/4 步的续接执行体：上一执行体于
> implement-1/4 中断（用户主动取消），本班核对现场后续跑调研并落本报告。
> 通道纪律：gh api 认证串行、间隔 7 秒（任务书本轮明确 6—8 秒、≤10 次/分，严于
> keywords.md 的 4 秒口径——从严执行两者皆满足）；WebSearch 串行单发；结果重合跳余页。

## 一、中断现场核对（git 实测，14:44-14:50）

- **分支**：`git branch --show-current` = **main ✓**（主 checkout，未切 feature、未建 worktree）。
- **HEAD**：`9ed0919`（v0.1.112 定版），reflog 无 reset/rebase 痕迹；stash 两笔均为 10-12 前历史遗留，不动。
- **未提交面**（git status porcelain 实测，与断点简报比对）：

| 文件 | 状态 | 归属判定 | 本班处置 |
|---|---|---|---|
| `app/core/phone_relay.py`（330 行） | 新增未跟踪 | **中断任务链产物**（断点简报在列） | 只读记录，不重做、不提交 |
| `app/core/relay_ws.py`（121 行） | 新增未跟踪 | 同上 | 同上 |
| `cloud/phone-relay/`（worker+portal+tests，.wrangler/node_modules 在 .gitignore 外由其自有 .gitignore 管） | 新增未跟踪 | 同上 | 同上 |
| `tests/test_phone_relay.py`（212 行） | 新增未跟踪 | 同上 | 同上 |
| `app/main.py`（+36/-2） | 修改未暂存 | 同上（手机连接接线） | 同上 |
| `app/ui/app.js`（+56/-3） | 修改未暂存 | 同上（配对弹框 UI） | 同上 |
| `app/core/publish/manager.py`（+51/-1） | 修改未暂存 | **并行会话在途工作**（发布链：无 pending 记录时的人工确认兜底），断点简报**不在列** | 完整保留，不 add、不 review、不提交 |
| `tests/test_publish_manager.py`（+20） | 修改未暂存 | 同上（伴随测试 `test_manual_confirm_without_pending_record`） | 同上 |
| `app/core/settings.py` | **已恢复干净**（断点简报曾在列、当前 diff 为空） | 推断被并行会话收走或回退；HEAD 与工作区一致 | 无需处置，如实记差异 |

- **现场结论**：索引零暂存（我的提交不会裹挟并行工作）；中断链与并行链文件集零重叠，可安全并行。

## 二、中断产物函数级清单（实读源码，供第 2-4 步续接）

### 1. `app/core/phone_relay.py` — 电脑端反向连接器（自包含，无 Cloudflare 凭据下发）

- `relay_url()`：入口域校验（独立域名/HTTPS/拒绝 userinfo）,默认 `https://app.codebeeagent.com`，env `CODEBEE_RELAY_URL` 可覆盖。
- `_config()/_save(config)`：`data/phone-relay.json` 身份凭据（64 hex secret，0o600 原子写）；损坏时抛错**不静默重建**（防身份被顶替，有测试锚定）。
- `safe_path(path)`：转发路径白名单——拒 `//`、拒 `/relay/`、拒 `/api/connect`（远程端不能碰本机配对管理接口）、拒 CRLF/空字节，≤8192。
- `Connector`（线程化）：`start()`（置 `remote.set_trusted_proxy(True)`——转发请求自带令牌+显式远程标记）/`view()`（enabled/connected/error/entry/device/pair_url/pair_code）/`new_pair()`（5 分钟一次性配对码）/`revoke()`（等服务端 ACK，10 秒超时）/`stop()`/`_run()`（指数退避 1→30s 重连；消息类型 pair-ready/paired/revoked/ack/cancel/request；并发上限 24）/`_heartbeat()`（20s ping）/`_request()`（方法白名单+safe_path+16MiB 体上限+头白名单透传，注入 `X-CodeBee-Token`+`X-Forwarded-For: 198.18.0.1`，响应头白名单回传，`read1(48K)` 分块流式+背压 ACK）。
- `view()/start_saved(port)/operate(port, action)/stop()`：模块级单例（RLock）；`operate` 支持 pair/revoke/disable，disable 离线时**轮换身份**（旧会话永不能重连）。

### 2. `app/core/relay_ws.py` — 自实现 RFC 6455 客户端 `RelaySocket`

- 构造：TLS 强制（ws 仅限环回）、Sec-WebSocket-Accept 逐字校验、Bearer secret 鉴权、握手 16KiB 上限。
- `send/_send`：客户端强制掩码、24MiB 消息上限；`receive`：分片重组、控制帧校验、ping 自动 pong；`close`：shutdown 唤醒阻塞读，不做无限期关闭握手。

### 3. `cloud/phone-relay/` — Cloudflare Worker 中转服务（app.codebeeagent.com）

- `src/worker.mjs`（242 行）：Durable Object（DEVICES 每设备一实例 + GATES 限流）；路由 `/relay/health`、`/relay/portal`（CSP 严格静态页）、`/relay/connector/<device>`（WebSocket，secret 哈希前 32 位=设备名）、`/relay/pair`（同源+限流 10/分+一次性码）、会话 cookie（Secure/HttpOnly/SameSite=Strict，30 天，每电脑 ≤16 会话）。
- `src/policy.mjs`（99 行）：常量/摘要/sessionCookie/sameOrigin/safePath/boundedBody/securityHeaders。
- `public/relay/portal.js|css`：手机端配对门户；`tests/policy.test.mjs`、`tests/browser.mjs`：Worker 侧测试。
- `wrangler.jsonc`/`package.json`/`README.md`：部署与验证说明（`npm run dev` + `CODEBEE_RELAY_TEST_URL` 联测）。
- **隐私边界（README 自述）**：中转终止 TLS 非端到端加密；持久存储仅配对码/会话哈希与限流计数，不存任务正文。

### 4. 接线与 UI（`app/main.py` / `app/ui/app.js`）

- main.py：`Handler._phone_admin_local()`（配对管理仅限真实本机：环回地址+拒绝 XFF/CF-Connecting-For 转发+Origin 校验）；`GET /api/connect` 追加 `relay` 视图（非本机为 None）；`POST /api/connect/relay`（action=pair/revoke/disable，403/400 语义）；启动参数 `--no-phone-relay`；`main()` 启动时 `phone_relay.start_saved(port)`+`atexit` 注册，手机入口优先于隧道地址打印。
- app.js：`openPhoneConnect()` 弹框插入「Codebee · 连接自己的电脑」区块（配对/撤销/关闭按钮+状态轮询 2s）；`renderPhoneRelay(relay)`（状态文案+连接码+chips 增删+`_phoneRelayTimer` 轮询）；`phoneRelayAction(action)`（revoke/disable 带 confirm）；`renderConnQR()` 无 URL 时占位文案。
- tests/test_phone_relay.py：`ConnectorPolicyTest`（safe_path 黑白名单/TLS 强制/凭据损坏不静默替换/身份持久化）+ `RelayIntegrationTest`（需本地 Worker，`CODEBEE_RELAY_TEST_URL` 开关：双电脑隔离配对/单次配对码/跨设备 cookie 拒绝/控制权 423/SSE 流式穿透/进程重启身份保持/并发配对恰一会话/撤销与登出）。

### 5. 未完成面（第 2-4 步待收口）

- 未提交、未过五道关（py_compile/全量测试/自审/暂存核对均未跑）；`cloud/phone-relay/.wrangler/` 本地状态目录混在未跟踪面（提交时须确认被其 .gitignore 覆盖，`find` 实测其 .gitignore 在位）。
- `style.css` 未见 `.relay-connect` 样式追加（diff 仅 main.py/app.js——UI 用既有类拼装，是否需补样式交第 2 步巡检判断）。
- v0.1.112 已发版**不含**本件；「当天有代码入库才发」的判断留第 4 步。

## 三、词库/知识库/路线图实读记录

- **keywords.md**：实际 **148 组 + 雷达源**（A 常驻 78 组 A1-A13 + B 轮换池 69 组批1-7 + C 雷达源），任务书「130 组/A1-A10/约 50 组」为旧口径——以文件现行规则为准执行。词库内嵌 10 条实测纪律（限流/翻页/WebSearch 交叉验证/repos 二次实证/org 顺藤/属主勘定/URL 编码/小时不可测降级/合并查询/3-4 班交叉验证节奏）。
- **knowledge.md**（7889 行，817KB）：时间轴式沉淀，最近条目至 2026-10-14 11:40 班（检查点流式读取已落地并随 v0.1.112 入库）。头部为 token 节约专项（每轮必查维度）+ 检索经验方法论。
- **路线图/待深挖队列**：无独立 roadmap 文件；队列快照散布于 iteration-report.md（10-04 版 11 项）与 knowledge.md 各班（10-08 增至 12 项，10-13 维持），最新基准确 10-13 03/04 时班 77 仓复查表（本班复查以此为基准）。历史报告全目录已核（borrow-log 48 文件），本班实读：keywords.md 全文、knowledge.md 头部/结构/近 3 日尾部、current-round.md、iteration-report.md、full-type-round.md 尾部、2026-10-13.md 头部、2026-10-14.md 全文。
- **当日已跑班次**：05 时班（批5，216/216 主扫零新差量）+ 11:40 续班（检查点流式读取落地）+ 14 时断点班（手机连接功能，未提交）。本班 14:44 续接为当日第 4 班，B7 与 05 时班 B5 不同域不重复。

## 四、预置任务类型核对（「13 种」口径澄清）

- **flows.py:37-133 `BUILTIN_FLOWS` 实读 = 18 条**：direct（直接执行）/code（代码）/novel（小说）/serial_novel（连载小说）/article（自媒体文章）/video_script（短视频脚本）/doc（文档）/translation（翻译）/rank_scan（扫榜选材）/defect_retro（缺陷复盘，即禅道工单复盘入口）/research（调研报告）/speech（演讲稿）/presentation（演示文稿）/weekly_report（工作汇报）/email（商务邮件）/tech_proposal（技术方案）/resume（简历）/bid_doc（标书编制）。
- **用户列举 14 项全部有对应预置**（直接执行/代码/小说/连载/自媒体文章/调研报告/短视频脚本/技术方案/翻译/演讲稿/工作汇报/商务邮件/扫榜选材/禅道工单→defect_retro），零遗漏；另有 doc/presentation/resume/bid_doc 4 条为列举未提及的预置。
- **「13 种」口径来源勘定**：full-type-round.md 等 报告中的「review 引擎 13 类型」指 engine=review 的 13 条流程（novel/article/video_script/doc/translation/research/speech/presentation/weekly_report/email/tech_proposal/resume/bid_doc）——18 预置中 direct/code/serial_novel/rank_scan/defect_retro 走 direct/code/serial 引擎不计入。任务书「13 种」系该口径与「14 项列举」的混合旧称，实数以 18 为准。
- 对话场景 = direct 引擎+手机/多端控制权链；知识库 = 批5 knowledge base quality 词组+A10 deep research（历班同结论，本班 B7 轮换不在批5 窗口，引用 05 时班该域零新结论）。
