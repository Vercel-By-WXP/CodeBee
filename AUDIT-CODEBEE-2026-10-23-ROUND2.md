# CodeBee 第二轮审查（2026-10-23 19:20）

维度：**并发与锁竞争 / 超时与重试链路 / 错误分类与日志脱敏 / UI 非内联路径**
（第一轮覆盖面：热更新完整性、凭据外传、内联处理器 XSS、评审隔离、解析器健壮性、
ACP 生命周期、SSE 上限、流式落盘、换将隔离、Git Bash 探测，见
`AUDIT-CODEBEE-2026-10-23.md`。）

> **执行方式说明**：本轮原计划派 4 个只读探查代理并行跑，四个都在 1 分钟内因
> 账号额度 429 失败（`429 额度已用尽`），没有拿到任何代理产出。以下结论全部由
> 主代理亲自读代码 + 跑小脚本验证得到，因此**覆盖范围小于原计划**：文中「未覆盖」
> 一节如实列出缺口，不用代理结论充数。

---

## 1. 结论速览

| # | 维度 | 结论 |
|---|---|---|
| M1 | 日志脱敏 | 步骤日志回给 UI/导出前不脱敏，而同源文本进模型前已脱敏（一致性缺口，**中**） |
| L1 | 并发 | `cli_commands._bash_cache` 无锁读写（良性，已被 60s TTL 缓解，**轻**） |
| — | 其余 | 本轮已核查的路径未发现可复现缺陷，见 §2 逐条证据 |

没有 P0/P1。已修项见 §4（本轮只改报告，不动源码，等确认）。

---

## 2. 已核实「不是问题」的点（附证据，避免下轮重复劳动）

### 2.1 并发与锁

- **`store.LOCK` 是 `threading.RLock()`**（`app/core/store.py:41`），不是 `Lock`。
  静态扫描确认仅两处「持锁再取锁」：`write_story_bible:1759→get_task:1767`、
  `retry_task:1929→create_run:1945`。若是 `Lock` 这两处就是自锁死。
- **两把锁无锁序反转**：`stream_step` 是唯一同时取 `LOCK` 与 `_STREAM_LOCK` 的函数，
  顺序固定 `LOCK → _STREAM_LOCK`；`_STREAM_LOCK` 的两个使用点（`store.py:2687`、
  `2716`）都未被 `LOCK` 外的路径先取，不存在反向顺序。
- **模块级缓存访问全部在锁内**：`get_run`（`store.py:1157`）、`list_runs`（1162）、
  `create_run`（1095）、`get_task`（632）均 `with LOCK`；`_STREAM_TS` 的 4 个访问点
  （2688/2691/2718/2720-2721）都在 `_STREAM_LOCK` 内。
- **原子写不存在共享临时文件**：`atomic.write_bytes` 用 `tempfile.mkstemp` 在目标
  目录建唯一临时文件 + `os.replace`（`atomic.py:64-80`），Windows 读竞争用有界重试
  （100 次、间隔 2ms→20ms 封顶，最坏约 1.9s）而不是无限重试。
- **`atomic.create_bytes`**（`atomic.py:109`）用 `os.link` 发布，保留 `O_EXCL`
  首写者胜语义，不会把并发首次写变成覆盖。

### 2.2 超时与重试

- **任务级 deadline 存在且生效**：`_run_deadline`（`pipeline.py:422`）把持久化的
  墙钟截止转成 monotonic deadline，`_step_timeout`（441）用 `min(请求值, 剩余)`
  收敛单步超时。不是「每步各自 20 分钟、无总闸」。
- **评审换将循环有界且会看取消**：`pipeline.py:2731` 的 `for attempt in range(1, 4)`：
  停滞看门狗 `stalled` 直接 break（避免「同一 CLI 白烧 3×600s」）、
  `attempt_cancelled(res)` break、账号级死错 break、候选耗尽 break。
  上限注释里有 2026-10-23 实案（单个评审步连撞 39 次、50 分钟未收场）的修复记录。
- **凭据走 env 不走 argv**：`modelhub.py:2173-2196` 把 key 写进 `ORCH_API_KEY` /
  `ANTHROPIC_API_KEY` / `DEEPSEEK_API_KEY` 等环境变量注入子进程；未发现
  `--api-key` 形式的 argv 传参（那会同时泄漏给 `ps` 和日志）。
- **步骤日志落盘权限收紧**：`runner._open_bounded_log`（`runner.py:583`）以
  `0o600` 创建并在非 Windows 上 `fchmod` 收紧。

### 2.3 错误分类

- **未归类时保守**：`classify_error_text`（`error_codes.py:186`）无可靠信号返回
  `None`，调用方落到 `VENDOR_ERROR`（「未识别具体原因」），按产品失败保守处理，
  不会默认可重试。
- **区分已经踩过坑并留了实案注释**：403 包装的预扣费失败判 `QUOTA` 而非
  `FORBIDDEN`（2026-10-15 qwen 欠费连撞 31 次案）；「429 + 达到使用上限 + 后可继续
  使用」判账号级死错而非 `RATE_LIMIT`（2026-10-23 单任务 54 分钟案）。

### 2.4 UI 非内联路径

- **无动态代码执行**：`app/ui/*.js` 中 `eval(` / `new Function` / `setTimeout("字符串")`
  均为 0 处（grep 全量确认）。
- **抽样 8 处 `innerHTML` 拼接均正确转义**：`app.js:302`（章节预览，`esc(t(...))`）、
  `2256`（通知卡，`esc(head/title)`）、`6208`/`6232`（错误提示，`esc(...)`）、
  `8173`（报告读取失败，`esc(...)`）、`12031`（蜂巢计数，数字 + `esc(t(...))`）。
- **`data-*` 委托链路未见注入面**：`dataset.*` 的消费点（`app.js:847-874` 模型菜单、
  `17158/17727` 来源筛选）都作为普通字符串用，未进入 URL/`href`/`src`/`fetch`。
- **localStorage 不含密钥**：写入项为视图态、侧栏目录、通知开关、`orch.token`
  （访问令牌，本来就要持久化）、`orch.client`（客户端 id）。

---

## 3. 发现的问题

### M1 · 步骤日志回给前端/导出前不脱敏（中，一致性 + 纵深防御缺口）

**位置**：`app/core/store.py:2879` `read_step_log()`（清洗 ANSI/折叠遥测/翻译事件流，
但不调用 `redact`）→ `app/main.py:1180`（`?tail=` 读取）与 `1206` 一带的导出/预览。

**为什么是问题**：同一条上游 CLI 输出，在**进模型之前**会被
`app/core/builtin_agent_tools.py:1363` 的 `redact.mask_secrets(raw_text)` 洗一遍
（`data_taint.py:24` 同样按此前提设计），但在**进 UI / 进导出包**时是原文。
也就是说脱敏策略只保护了一个消费方。

**限定（不夸大的部分）**：
1. 步骤文件以 `0o600` 落盘（`runner.py:583`），同机其它用户读不到；
2. `/api/...` 读取接口本身需要访问令牌；
3. **我没有构造出「密钥真的进了日志」的样本**——未发现 CLI 回显 `Authorization`
   头或完整 URL（含 `key=`）的证据，凭据也不走 argv。因此这不是已证实的凭据外泄，
   而是「一旦某家 CLI 把请求细节打进 stderr，就会原样出现在 UI 和导出包里」的单点。

**建议**（最小改动）：`read_step_log` 返回前过一次
`redact.scrub_text(text)`；导出/预览走同一入口即可全覆盖。
代价：日志里出现 `[key]` 会略微影响「照着日志排查上游报错」的场景——所以更稳的
做法是加一个 `raw=1` 参数给「下载完整日志」用，默认脱敏。

**回归**：`tests/test_request_log_redaction.py` 已有同类断言可参照；新增一条
「含 `sk-` 的步骤日志经 `read_step_log` 后不得原文返回」。

### L1 · `_bash_cache` 无锁读写（轻，良性）

**位置**：`app/core/cli_commands.py` 的 `_bash_cache`（上一轮 C2 刚给它加了
60s 负缓存 TTL）。`find_git_bash()` 可能被多个任务线程并发调用：字典的
`done/path` 读写没有锁。

**后果**：最坏是两条线程同时探一次盘（`shutil.which` 幂等），或一个线程读到
另一个线程刚写入的半初始化状态——Python 字典的单键读写本身原子，实际观察不到
错值。TTL 已经保证不会永久缓存错结果。

**建议**：不必改；若想彻底干净，把 `_bash_cache` 的读写包进模块级 `Lock`
（5 行）或改用 `functools.lru_cache` 包一层。列在这里只是留档，避免下轮重复分析。

---

## 4. 本轮改动

只新增本报告。**未改任何源码**——M1 涉及「日志可读性 vs 脱敏」的产品取舍（见
M1 的 `raw=1` 讨论），L1 无实际影响，都等确认后再动。

---

## 5. 未覆盖（下一轮待办，按优先级）

1. **UI 全量 `innerHTML` 语句级审计**：`app.js` 有 365 处 `innerHTML`，本轮只抽样
   8 处。需要按语句（而非按行）提取右值并判定每个拼接片段是否过 `esc/jsq/t`。
2. **bench / modelhub / publish 的台账与锁**：这些模块有进程级台账
   （`benched_ids`、`blocked_upstreams`、影子挑战者），本轮未查其并发写与
   TTL 清理；`publish/manager.py` 有外部平台交互，超时/重试未查。
3. **前端健壮性**：轮询失败后的 loading 态、长列表分页、EventSource 重连退避，
   本轮只看了有没有 `eval`，未看失败路径。
4. **`runner.py` 的流式重试**（`runner.py:2298-2302`、`2542` 的 `STREAM_RETRY_N`）：
   空响应与只读流中断各有界重试，本轮未验证其上界与 cancel 交互。

> 说明：原计划覆盖以上全部的 4 个并行探查代理全部因额度 429 失败，所以本轮只交付
> 已亲自验证的部分。恢复代理额度后可按 §5 顺序继续；或由你指定先做哪一项，我单独深挖。
