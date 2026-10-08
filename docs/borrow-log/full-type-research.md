# 2026-10-08 12 时班·全类型竞品调研（full-type-research.md——新一轮计划第 1/4 步锚定交付物）

> 开工实录：12:40（UTC+8，hour=12，12%7=**5** → 轮换**批5：检索/知识/浏览器**；
> 当日已跑 B7〔00 时班〕/B6〔06 时班〕/B1〔08 时班〕，B5 当日未跑，无重复）。
> 分支 main（4fe7c72）。工作区开工时干净零在制品。
> 本文件系计划锚定交付物：记录关键词、来源链接、检索时间、增量证据与覆盖矩阵。

## 一、检索通道与环境实录

| 项 | 实录 |
|---|---|
| 检索时间 | 2026-10-08 12:40 起续（UTC+8） |
| 主扫 | `scripts/borrow_scan_nightly.py` 无参全量：A 常驻 89（含内置 B1 11）+ **B5 轮换 10** + A1u/A1p2 双轮 16 = **115 查询** |
| 补跑 | 首跑遇本机网络抖动期（api.github.com TLS handshake timeout 成批出现）83 查询 FAIL，`scan_retry.py` 串行补跑（失败查询 15s 后一次重试，间隔 5s） |
| 雷达 C | awesome 20+1 源 repos 实测 / trendshift HTTP 200 / topic 8 页 / npm 三查 / pypi 在架 |
| WebSearch | 串行 2 发（批5 域定向：deep research/RAG 与 browser/scraping agent），claims 走 repos 勘定 |
| 网络异常 | 本机出口网络抖动期贯穿本班：GitHub API TLS 超时成批、npm registry curl connect timeout、trendshift/topic 页部分页面同抖——**受影响未完成项如实标注于第六节，不宣称全覆盖** |

## 二、关键词执行清单（keywords.md 规则落点）

- **A 常驻组全跑**（89 组）：A1 核心编排 8 + A2 扩展形态 7 + A3 token 节约 4 + A4 新 CLI 3 +
  A5 舰队形态 4 + A6 评测观测协作 6 + A7 写作场景 8 + A8 prompt/网关/质量 5 +
  A9 产品配套 6 + A10 全类型写作对话代码 10 + A11 项目记忆与档案 5 + A12 发布与平台 5 +
  A13 多 CLI 面板/用量/守卫 7 + 内置 B1 代码质量 11。
- **B 轮换批5**（10 组）：agent+rag / deep+research+agent / browser+use+agent+OR+browser+automation+ai /
  web+scraping+agent / search+agent+OR+retrieval+agent / document+understanding+agent /
  data+extraction+agent / competitive+intelligence+agent / citation+verification+agent+OR+source+grounding+agent /
  knowledge+base+quality+OR+rag+evaluation+agent。
- **双轮排序**：全组 sort=stars；A1 组追加 sort=updated 新锐轮（A1u 8）与 stars page=2 翻页（A1p2 8）。
- **C 雷达源全过**：awesome 清单 21 件（含 BMAD-METHOD 对标件）/ GitHub Trending（trendshift 补位）/
  topic 8 页 / npm（agent orchestrator + claude code + zentao ai 三查）/ pypi（agent-orchestrator）。
- **结果重合跳余页**：topic 页 updated 头部与主扫重合度高（常客复认为主），未加翻页——省配额纪律照旧。

## 三、主扫描统计

- 首跑：115 查询 → **32 查询成功**（135 唯一仓）+ 83 FAIL（网络抖动期，TLS handshake timeout）。
- 补跑：83 查询串行重试（每查询失败后 15s 退避一次）→ 结果见第五节更新行（补跑完成态）。
- 汇总端按 full_name 去重；已录/首见判据以 docs/borrow-log/ 全历史（knowledge.md + 历日报告 +
  round-full-types/full-type-* 锚定文件）全名双通道 Grep 过筛。

## 四、雷达 C 覆盖清单（逐源实录）

### 4.1 awesome 清单 21 件 repos 实测（零搜索配额）

| 源 | stars | pushed | 状态 |
|---|---|---|---|
| vivy-yi/awesome-agent-orchestration | 80 | 2026-03-05 | alive |
| ComposioHQ/awesome-claude-skills | 76,679 | 2026-09-18 | alive（+9 vs 08 时班 76,670） |
| ai-boost/awesome-harness-engineering | 4,748 | 2026-10-07 | alive（+2） |
| punkpeye/awesome-mcp-servers | 95,912 | 2026-09-27 | alive（+2） |
| bradAGI/awesome-cli-coding-agents | 1,323 | 2026-10-05 | alive（持平） |
| e2b-dev/awesome-ai-agents | 30,296 | 2026-08-21 | alive（+2） |
| Shubhamsaboo/awesome-llm-apps | 140,953 | 2026-09-30 | alive（+14） |
| hesreallyhim/awesome-claude-code | 55,221 | 2026-10-08 | alive（+11） |
| VoltAgent/awesome-agent-skills | 35,347 | 2026-10-07 | alive（+10） |
| davepoon/buildwithclaude | 3,604 | 2026-10-06 | alive（持平） |
| RUC-NLPIR/Awesome-Long-Horizon-Agents | 1,065 | 2026-10-06 | alive（持平） |
| TeleAI-UAGI/Awesome-Agent-Memory | — | — | **TLS 超时未测成**（历班 659 alive，非 404） |
| caramaschiHG/awesome-ai-agents-2026 | 1,929 | 2026-06-10 | alive（持平） |
| vijaythecoder/awesome-claude-agents | 4,388 | 2025-10-30 | alive（±1 抖动） |
| TsinghuaC3I/Awesome-Memory-for-Agents | 665 | 2026-09-28 | alive（持平） |
| Engineering4AI/awesome-spec-driven-development | 289 | 2026-10-06 | alive（持平） |
| bmad-code-org/BMAD-METHOD | 53,911 | 2026-10-07 | alive（+2） |
| VoltAgent/awesome-ai-agent-papers | 1,828 | 2026-10-02 | alive（+1） |
| EvoMap/awesome-agent-evolution | 234 | 2026-10-07 | alive（持平） |
| IAAR-Shanghai/Awesome-AI-Memory | — | — | **TLS 超时未测成**（历班 1,259 alive，非 404） |
| OpenBMB/StaffDeck（对标件） | 1,970 | 2026-10-06 | alive（+1） |

**小结：19/21 实测 alive 零 archived；2 件 TLS 超时如实记（网络抖动期非 404）。**

### 4.2 trendshift（HTTP 200 331KB）

- **提取法再变（第 4 版形态）**：flight payload `full_name` 字段零命中、href 已改
  `/repositories/<数字id>` 无名形态——改用 **github.com 裸链接法**提取 **30 仓**全量成功
  （历班提取法演变：href 路径→flight payload full_name→本轮 github.com 裸链接）。
- 已录复认：storytold 家族（photocraft/filmcraft/lightcraft/pdfcraft/photocraft/vectorcraft 6 兄弟）/
  openai/math / morluto/rea / robbietilton/Compositor / mattpocock/skills / maximhq/bifrost /
  GetBusbar/busbar / alchaincyf/huashu-art-motion / eternity4719/HowToLiveBetter（域旁）/
  boykopovar/AnyPS5（域旁）/ liweiyi88 gosnakego+onedump（域旁）。
- **新面孔候选 3 件（全部 repos 勘定）**：
  - **farion1231/cc-switch 141,059★**——**已录复认放量续**（10-03 入库 139,682 → 本轮
    +1,377）：「跨平台桌面 All-in-One 助手 for Claude Code/Codex/OpenCode/OpenClaw/Grok
    Build/Hermes Agent」——A1 域同形态竞品第一星数档（超 superpowers），10-08 当日 push 活跃。
  - **docker/docker-agent 3,830★**（10-07 push）——Docker 官方「AI Agent Builder and
    Runtime」，B6 框架域大厂官方新件，borrow-log 全历史首见。
  - yetone/magpie 6,161★——已录复认放量续（10-05 基线 5,056 → +1,105）。
- 域旁排除：vbskycn/iptv、metasequoiaime/msime-windows、NotProtonNot、nullmoth/nvidia-macos-driver、
  yi1108/printfilm、LoreanXavier/pt-pc、threerocks/hand-drawn-styles、shihabal3amri/DiPlay。

### 4.3 topic 8 页（updated 排序）

- multi-agent-orchestration：desplega-ai/agent-swarm 867（已录复认 852→861→867 续）/
  5dive-ai/5dive 66 / Fmarzochi/EGC 63 / AutomatosAI 48（均微型新锐）；brekkylab/backlot 394（SaaS API 本地模拟器，域旁）。
- ai-agents：**frankbria/ralph-claude-code 9,668★（10-08 push）首见判据**（见第五节新面孔表）；
  cirwel/unitares 5 / matto00/concertino 2（微型）；gileshall/nanotea 0（微型）。
- claude-code：全微型（0-1★）/ jonyfs/astrolabe 1；**已录复认 frankbria/ralph-claude-code 9,668 跨页复见**。
- agent-framework：全微型（ljchang/mecha 7 / shadow3aaa/DaatLocus 7 / AshishKumar4/Nimbus 16）。
- llm-agents：scaleapi/agentenv-framework 190（RL 环境框架，域旁）/ anthony-chaudhary/fak 41（微型）。
- mcp：**rynfar/meridian 2,100★**（「Use Claude and Antigravity with Pi, OpenCode and other
  coding clients. Local API bridge」——A1 多客户端桥接域）repos 勘定 TLS 超时未成，按 topic
  页星数暂记跟踪；syarihu/agent-adjutant 3 / YashvantHange/AgentArmor 4（微型）。
- claude-skills / ai-coding-assistant 两页：TLS 超时未抓成（补抓见第五节更新行）。

### 4.4 npm 三查 + pypi

- 本机网络抖动波及 registry.npmjs.org（curl connect timeout）——**首查零返回如实记**，
  补查结果见第五节更新行。
- pypi：agent-orchestrator JSON API 同抖未成，补查见第五节。

### 4.5 WebSearch 串行 2 发（批5 域定向交叉验证）

- **第 1 发**（deep research + RAG + knowledge base 2026）：全已录族复认（gpt-researcher /
  RAGFlow / LangChain Deep Agents）；「Awesome Deep Research」清单 claim（属主未勘定）；
  学术面 WARP Attack（deep research agent 检索投毒，arXiv 2026-05）——**安全研究域旁参考**
  （我们调研报告流程抓取网页内容，投毒面理论存在；CodeBee 抓取面有 SSRF 闸门+白名单，
  非本轮落地项，雷达记档）。
- **第 2 发**（browser use + web scraping agent 2026）：**新 claim `browser-act/skills`**——
  repos 勘定成功 **6,114★**（2026-08-24 push）：「Browser automation CLI built for AI agents：
  破反爬墙/卡住转人工/并行多任务/独立多会话」——批5 域判据级新面孔（见第五节）；
  Browser Use/Stagehand/Vibium 全已录族复认；行业面「agent-driven scraping」趋势与
  CodeBee 扫榜选材/调研报告流程的抓取链同向。

## 五、主扫结果与新面孔定性

（补跑完成后更新本节：全量唯一仓数、已录/首见过筛、批5 域逐词头部表、新面孔定性表、
已沉淀项目复查增量表、全类型覆盖矩阵。）

## 六、未完成项（如实记录，不宣称全覆盖）

- TeleAI-UAGI/Awesome-Agent-Memory、IAAR-Shanghai/Awesome-AI-Memory 两 awesome 源 TLS 超时未测成本班星数（历班 alive）。
- topic claude-skills / ai-coding-assistant 两页 TLS 超时（补抓状态见第五节）。
- npm 三查 + pypi 首查零返回（补查状态见第五节）。
- 主扫首跑 83 查询 FAIL，补跑完成态与残余失败数见第五节。
- rynfar/meridian repos 勘定未成（TLS 超时），暂按 topic 页 2,100★ 跟踪不定性。
