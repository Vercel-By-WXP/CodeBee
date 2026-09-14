# 07 · Token 成本优化：真实数据 + 开源借鉴 + 落地方案

> 2026-09-14。基于用量台账真实数据（data/usage/usage-202609.jsonl，209 条记录、3260 万 token），
> 不是拍脑袋。开源借鉴映射到 Tutti 的「CLI 子进程驱动」架构，不照搬 API-only 方案。

## 一、钱花在哪（真实台账）

| 发现 | 数据 | 含义 |
|---|---|---|
| **输入占绝对大头** | draft-c7：121 万 tok / 3 次调用，输出仅 5.7 万；implement-4/4 单次 ~103 万 tok | 优化输入（缓存/复用/裁剪）远比压输出值钱 |
| **缓存命中率极低** | glm-5.3-flash：420 万 tok 仅 33 万 cached（**8%**）；对照 [opencode]deepseek：45.8 万 tok 中 32.3 万 cached（**70%**） | 同样的端点，会话化路径命中率 70%——差距全是架构造成的 |
| **修复/修订轮最烧钱** | fix-r1/r2 各 ~295 万 tok / 3 次（**单次 ~100 万**）；revise-c8 单次 109 万 tok | 修复循环每次全新调用，仓库上下文+规范块+全文全部重发 |
| **每章都全量重发** | draft-c1 11 次调用 89 万 tok | 章与章之间技能块/大纲/目标完全相同，却拿不到缓存 |

**代码根因**（已核实）：
1. `_run_step` 的所有 draft / revise / fix / critique 调用**均未传 resume**——只有任务级「在已有会话上继续」才用会话；
2. runner **丢弃了 CLI 返回的会话 id**（codex `thread.started.thread_id`、claude JSON `session_id` 都没解析）；
3. `block_for()` 对所有角色注入同一份 ≤6000 字符规范块；
4. `modelhub.chat` 的提示词把易变数据混在前面，静态前缀拿不到缓存。

## 二、开源借鉴清单（映射到 Tutti 架构）

| 开源/机制 | 核心思想 | 对 Tutti 的适用性 |
|---|---|---|
| **DeepSeek Context Caching**（官方文档公开） | 磁盘级前缀缓存自动生效，命中价 ~1/10；官方建议**静态内容前置** | ★★★ 直接适用：chat() 调用重排 + CLI 走 DeepSeek 兼容端点时 stdin 前缀稳定即可命中 |
| **Anthropic Prompt Caching / OpenAI Prefix Caching** | 同上，claude/codex 在 **session 内**自动吃缓存 | ★★★ 这就是 resume 省钱的底层原因：--resume 后 system+历史前缀按缓存读计价 |
| **FrugalGPT**（Stanford，开源） | 质量闸门触发的模型级联：便宜模型先跑，不达标才升级 | ★★ Tutti 有现成的质量信号（评审分+verify 命令）和跨厂商链，加个「easy 先便宜链」就是原生级联 |
| **gptcache / AutoGen DiskCache** | 响应缓存（精确/语义） | ★★ 只借鉴**精确匹配**：连通测试、同参数重试、重规划。语义缓存对写作质量风险大，不默认 |
| **LiteLLM budgets** | 预算治理：spend 上限、超限熔断 | ★★ token_meter 数据已就绪，差一个 per-run 预算闸 + UI 展示 |
| **LLMLingua / LLMLingua-2**（Microsoft，开源） | 小模型压缩提示词 2-20x | ★ 实验性：压缩写作规范可能掉细节（黄金三章的细则被压没了就毁了），默认关 |
| **dsh compaction**（已学过，Phase 2 落地） | 上下文压缩 | 已落地（TUTTI_COMPACTION），本篇聚焦它之外的省钱手段 |

## 三、落地方案（按 ROI 排序）

### T1 立即收益（小改动，预计砍掉 30-60% 输入）

- **T1.1 修订/修复轮复用会话（resume-first）**
  runner 解析并返回 CLI 会话 id（codex thread_id / claude session_id / opencode -s / qwen -r）；
  flows 里 revise/fix/re-review 从上一步结果取 sid 传入 `_run_step(..., resume=sid)`。
  数据支撑：revise-c8 单次 109 万 tok → resume 后增量只有新指令+改稿，预计降一个数量级。
  ⚠️ 注意：跨厂商评审不能复用（会话在原厂商侧）；同厂商的二次评审/修订才复用。
- **T1.2 角色化技能注入（block_for 加 role 维度）**
  评审/critique 只注入 rubric + 教训（~1/3 体量）；draft 注入写作规范；规划不注入长规范。
  数据支撑：6k 字符规范块 × 每章 3-4 次调用 × N 章。
- **T1.3 静态前缀排序（零风险重排）**
  modelhub.chat 三处调用（编排者/learn/连通测试）：固定 system+格式说明 → 技能 → 易变数据置底。
  DeepSeek/Anthropic/OpenAI 的前缀缓存全部受益；顺带把 `cached` 字段盯进台账（已有）观察命中率变化。
- **T1.4 输出纪律**：critique/orchestrator 的 max_tokens 按实际需要收紧（输出在总量里占小头，顺手做）。

### T2 治理（中等改动）

- **T2.1 per-run token 预算闸**：settings_schema 加 `budget` namespace（如 serial 任务单次 run 上限）；
  token_meter 已有压力数据，jobs 出队前检查，超限暂停入队 + UI 弹确认。借鉴 LiteLLM budgets。
- **T2.2 幂等调用精确缓存**：modelhub.chat 加 (provider,model,完整prompt) 的 hash 精确缓存，
  只对 source=test（连通测试）与编排者重试场景生效，TTL 24h。借鉴 gptcache，但只做 exact-match。

### T3 策略（实验，默认关）

- **T3.1 FrugalGPT 式级联**：difficulty=easy 的任务先走便宜链，质量闸门不过再按 chain 升级——
  复用 capability.py 的维度分类 + 现有跨厂商链 + 现有 repair 轮，几乎只是路由策略。
- **T3.2 LLMLingua 技能块压缩**：可选实验开关，风险是写作细则被压掉，需 A/B 对比评审分。
- **T3.3 语义响应缓存**：明确不推荐默认启用（创作类任务语义命中＝抄袭自己）。

## 四、预期收益（保守估算，以台账真实基线为准）

以「20 章连载、每章 1 draft + 2 review + 0.5 revise + 0.3 fix」的典型任务：
- T1.1 resume 复用：revise/fix 输入从全量重发降为增量，这两类占单章成本 ~40% → 总量省 **~25-35%**
- T1.2 角色化注入：评审输入规范块缩到 1/3 → 再省 **~8-12%**
- T1.3 前缀缓存：draft/plan 的稳定前缀按缓存读计价（DeepSeek 1/10 价）→ 看端点，**0-30%**
- 合计预期 **-35% 到 -60%**，且与 TUTTI_COMPACTION 正交（压缩解决"撑爆"，本篇解决"重复花钱"）。

## 五、实施顺序建议

T1.3（半小时）→ T1.2（半天）→ T1.1（1-2 天，含 runner 会话 id 解析与 flows 传递）→
T2.1/T2.2（各半天）→ T3.1（1 天）→ T3.2/T3.3 仅实验。
每步都可独立回退；台账 `cached` 字段是天然的验收指标（看命中率从 8% 爬到多少）。
