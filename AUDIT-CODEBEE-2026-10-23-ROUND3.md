# 第三轮审查报告 · 2026-10-23

**维度**：二轮未覆盖的四面——(A) innerHTML 注入面全审 365+ 处、(B) bench/modelhub/publish 台账并发、(C) runner 流式重试上界、(D) 前端失败路径。
**方法**：A 路子代理因额度 429 失败，已降级为主代理亲自全审（逐文件读码 + 静态扫描 + 逐源核实）；B/C/D 三路派 Explore 子代理并行，结论经主代理抽样复验关键项。
**口径**：只收实证过的结论；不确定项标「待复核」；已核实的非问题也留档，避免下轮重复分析。

---

## 摘要

| 维度 | 结论 | 可操作发现 |
|------|------|-----------|
| A · innerHTML 注入面 | 无存活 XSS | 0（4 处裸插值已核实不可控，建议防御性 jsq 化） |
| B · 台账并发 | 正常路径安全 | 0（3 个轻级留档，均启动期/极小概率） |
| C · 流式重试上界 | 无风暴/无限循环 | 3 轻 + 1 条件性中（P4 待复核生产直调） |
| D · 前端失败路径 | 有 1 个真功能 bug | D1（中，手机端裁决/启动 POST 必败）、D2（中）、D3/D4/D5（轻） |

**唯一需落地的功能 bug：D1**——手机端 4 个按钮（approve/reject/blocked 裁决 + 启动专家）POST body 传对象未 `JSON.stringify`，必返 400，功能 100% 坏。

---

## A · innerHTML 注入面全审（亲自，372 处）

**计数**：`app/ui` 下（排除 `demo_*.html` 开发样张）共 **372** 个 `innerHTML` 赋值：app.js 351、board.js 14、activity.js 1、mobile-workbench.js 2、project-overview.js 4。

**双轨转义约定已全面贯彻**：文本节点 / `title` / `data-*` → `esc()`（HTML 实体）；内联事件处理器 / 属性 JS 串 → `jsq()`（JS 字符串转义，含换行与 U+2028/2029）。与一轮 B1（181 处 esc→jsq）同一口径。

**最高危上下文（内联处理器）逐点核实**：全场 `onclick=`/`on*` 共约 200 余处，动态参数几乎全部 `jsq(...)`（如 `onclick="pickQuickType(\'' + jsq(f.id) + '\')"`）。仅 4 处把数据**裸插值**进内联处理器，已全部逐源核实**不可由攻击者控制**：
- `app.js:1336` `dismissOperation(event,' + op.id + ')` — `op.id` 是 `++_operationSeq` 自增整数（`app.js:1296`），非字符串可控；
- `app.js:15268` `setLayout(\'' + l.id + '\')` — `l.id` 取自硬编码 `LAYOUTS`（classic/workbench，`app.js:15056`）；
- `app.js:20798` `helpGo(\'' + c.id + '\')` — `c.id` 取自硬编码 `HELP_CHAPTERS`；
- `app.js:2869` `onclick="' + actionCall + '"` — `actionCall` 调用方全是静态字面量（`"switchTab('models')"`、`"availResetFilters()"` 等，`app.js:2934/2937/2950/2953`）。

**模型名（一轮 B1 的已证漏洞载体）专项核查**：所有 `m.name`/`f.name`/`p.model`/`pname` 进 innerHTML 均 `esc()`（文本）或 `jsq()`（处理器），未发现裸插值。

**其他高危原语**：全 `app/ui` 无 `eval(`、`document.write(`、`outerHTML=`（`grep` 零命中）。`toast()` 用 `textContent`（`app.js:1601`），故附件失败提示里的用户文件名 `f.name` 走纯文本渲染，无注入。

**结论**：**无存活存储型/反射型 XSS**。

**建议（非必须，防御性）**：把上述 4 处裸插值改为 `jsq()`（如 `jsq(op.id)`），即使未来某天把可控数据塞进这些变量也不至于引入 XSS。代价极低，可作为 hygiene 一并提供。

---

## B · bench/modelhub/publish 台账并发（Explore）

**持久化模型**：单写进程（pipeline 线程池，无 multiprocessing/uvicorn workers），desktop/ 不直写任何台账（只走 HTTP）。正常路径下三台账均为「持锁 → load → mutate → save」单临界区：
- bench：`_LOCK`（`bench.py:30`）；health：`_LOCK=RLock`（`health.py:58`）；modelhub：`_LOCK=RLock`（`modelhub.py:104`，无内存缓存，每次读盘）；evalbench：`_LOCK`（`evalbench.py`，`_update` 把 RMW 收单一临界区，注释明示防「同秒多写互踩」）；publish-ledger：`LOCK=RLock`（`ledger.py:24`，books.json 三个 RMW 全持锁）；publish state/accounts：`mgr.LOCK`；auto 三缓存各有 `RLock`。

**竞态点（全部轻级，留档）**：
- R1 `modelhub.py:2870-2905` 启动迁移以陈旧 state 全量写回 `ENABLED_FILE`，与用户同时 toggle 模型编排（`registry.set_preference` 持 `registry._LOCK`）理论撞窗，仅启动一次性，实际可达性待复核；
- R2 `benchstore.py:35-41` 读盘瞬时失败以 `{}` 为基重建 → 历史 results 被空文档覆盖，触发概率极低（atomic 替换 + Windows 100 次重试退避）；
- R4 `publish_auth.py:150-162` `stat()`/`read_text()` 间文件被替换 → 一次性脏 fingerprint 缓存，下次 stat 即失效自愈。

**结论**：正常并发写路径**无 lost update / 撕裂文件**。原子写在（不防 lost update，但 R1/R2 已覆盖）。

---

## C · runner 流式重试上界（Explore）

**全部重试/重连均有上界**（列关键项）：
- 模型候选链 `_max_model_attempts`=3（有 deadline）/4（无），env `TUTTI_MAX_MODEL_ATTEMPTS` 钳 [1,12]（`runner.py:1968-1980`）；单候选进程 ≤4 次；只读流重试 `STREAM_RETRY_N`=2×1s（守卫 `model_deadline-now>1`）；限流宽限 `RATE_LIMIT_GRACE_N`=1×≤45s；dsh 启动熔断 4 次/600s + 10s 间隔；
- 看门狗 `run_process` `while True` 由 deadline + 180s 静默窗 + 600s stall + abort markers 收敛（`runner.py:888-945`）；
- pipeline 评审 `REVIEW_MAX_CALLS`=4；连载 `range(3)` + 换将 `range(2)`；赛马 `range(2)`；直连 `DIRECT_MAX_TURNS`=200；字数闸 2 次；自动续跑 `AUTO_RESUME_MAX`=10 + `AUTO_WAIT_RESUME_MAX`=10（总闸 20）；
- 任务级 deadline 默认 3600s 钳 [1s, 7d]（`policy.py:38`、`store.py:1125`）；`_wait_gate` 暂停为设计语义（用户意志）；SSE 32；`_stop_pending_terminals` 2s 共享预算。

**问题详表**：
- **P4（条件性·中）** `runner.py:906-921` activity 延长逻辑：若 `deadline is None` 且子进程以 <180s 间隔持续滴输出，stall/elapsed 均不触发 → 进程可无限期存活。**已核实 `app/core` 内全部编排调用方均传 deadline**（`store.py:1125`、`pipeline` 内 743/1454/1472/790/1052），仅直接调 API/测试（`deadline=None`）可达。需确认桌面端/外部脚本是否存在生产直调——待主代理复核。
- **P1/P2/P3（轻）** 起草退避 `time.sleep(30*draft_attempt)`（pipeline.py:4472-4475）、评审 `time.sleep(2.0)`（pipeline.py:2804/2831/2880）、opencode 秒退 `time.sleep(2.0)`（runner.py:2429）均不查 deadline，超支 ≤60s/≤6s/≤2s 后由 `_ensure_budget` 收口，**不永挂**。
- **P5（留档）** `_retry_content_draft_with_cli` `while True`（pipeline.py:2630-2680）无显式数字上限，但 `tried` 单调增 + `pick_switch_candidate` 排除集收敛（上界=注册表大小），不会死循环，与连载 `range(2)` 不对称，建议补显式 cap。

**健康台账负反馈环**：持续 5xx 不在 `_PERMANENT_FAILURE_CODES`，bench 不记账，但 health 层 `FAILING_AFTER=2`→failing、`DOWN_AFTER_FAILURES=4`/`DOWN_AFTER_SECONDS=600`→down+冷却（`health.py:554-561`），**3 连败即熔断**，负反馈环在 health 层被打破，不在 bench 层。

**结论**：无重试风暴 / 无限循环。唯一需复核的是 P4（生产是否存在 `deadline=None` 的 run_agent 直调）。

---

## D · 前端失败路径（Explore）

**中枢 `api()` 兜底最全**（`app.js:1462-1539`）：401→token gate；`!res.ok`→throw 带 `data.error`（1519-1524）；423 控制权接管 + 自动重试 + 30s 节流（1491-1518）；`opts.timeout` AbortController；`requestBusyStart/End` finally 配对 → **无永久转圈路径**；空数据全链路 `|| []`/`|| {}`（30+ 处）；`render()` 抛错不白屏（poll 整体 try/catch）；SSE 断线立即降级轮询 + 60s 看门狗拉全量。

**缺陷详表**：
- **D1（中·功能 bug，亲验属实）** `mobile-workbench.js:144`（decision）与 `:162`（launch）POST `body` 传**对象**未 `JSON.stringify`；`api()` 原样透传 `fetchOpts`（`app.js:1482`）→ `body.toString()` = `"[object Object]"` → 后端 `json.loads` 失败 → 400 `{"error":"请求体不是有效的 JSON"}`（`main.py:250-252`）。全站其余 POST 均 `JSON.stringify`，此两处漏网。**后果：手机端 approve/reject/blocked 三个裁决按钮 + 「让专家做某事」启动按钮全部必败**，状态小字显示「请求体不是有效的 JSON」。代码为活路径（`index.html:594` 含 `.mobile-workspaces-head`）。
- **D2（中）** `app.js:1490` `try { data = await res.json() } catch { /* ignore */ }` 静默吞解析失败：①非 2xx 且体非 JSON（隧道/反代 502/504 HTML 页）→ 1519-1520 只能抛 `"HTTP 502"`，后端 `{"error":...}` 详情丢失；②2xx 但体非 JSON → `data=null`，调用方 `data.task`/`r.flows` 抛 TypeError 被各自 catch 吞成「Cannot read properties of null」或纯静默。
- **D3（轻）** `app.js:3289-3304` SSE 被限（503+Retry-After）无差异化提示：EventSource 拿不到失败响应体，UI 仅通用「重连中…」；SSE 规范重连间隔不采用 `Retry-After`，~3s/次撞 503。数据面有轮询兜底不丢，纯提示缺陷。
- **D4（轻）** `app.js:12215` 取消运行 catch 一律 `toast(t("取取消败：网络异常"))`，423/500/超时全显示「网络异常」，与同文件 `resumeNow`（:12227）带 `e.message` 不一致，误导用户查网络。
- **D5（轻）** `app.js:11105-11106` 成果分区 `api("/api/runs/{id}/files")` 失败 `catch { return }` 纯静默，主栏「成果」+ 检查器成品 TAB 无错误态/无 toast。

---

## 待用户拍板的修复清单（按性价比排序）

1. **D1（推荐优先，P1 功能 bug）**：`mobile-workbench.js` 两处 POST `body` 包 `JSON.stringify(...)`。一行级改动，回归点明确（手机端裁决/启动两路）。
2. **D2（P2 健壮性）**：`api()` 内 `res.ok && data===null` 时显式抛错（带原始状态码），避免调用方 null 解构被吞；需评估既有依赖「200 空 body 返回 null」的调用点。
3. **C-P4（P2 条件性）**：确认桌面端/外部脚本是否存在 `deadline=None` 的 run_agent 生产直调；若有，给 `run_process` 加进程寿命绝对帽。
4. **A 建议（P3 hygiene）**：4 处裸插值改 `jsq()` 防御性兜底。
5. **D3/D4/D5（P3 UX 健壮性）**：SSE 被限文案、取消错误原文、成果拉取失败提示——三处轻量改进。
6. **C-P1/P2/P3（P3）**：重试 sleep 前查 deadline，消除 ≤60s 超支（非必须，当前不永挂）。

**B 路无操作项**（正常路径安全，R1/R2/R4 为启动期/极小概率，无需改）。
