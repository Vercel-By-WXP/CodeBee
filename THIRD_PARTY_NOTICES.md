# 第三方来源说明

## 空间管家规则数据（2026-11-05 引入）

- **来源**：https://github.com/lz-kwok/C-Space
- **引入内容**：仅 `src-tauri/rules/` 下的三个 JSON 规则数据文件，原样复制并改名落到
  `app/core/cspace/rules/`：
  - `system.json`（系统、驱动、日志规则）
  - `dev_browser.json`（开发工具链、IDE、浏览器规则）
  - `apps.json`（社交、办公、音视频、游戏平台规则）
- **未引入**：该项目的任何 Rust、TypeScript/React 源码、图标、构建配置或可执行文件。
  CodeBee 的扫描、风险判定、清理、目录联接搬家全部是独立的 Python 标准库实现，
  只是**按同一套机制思路重写**，并消费上述规则数据。
- **改动**：规则里的 `%WINDIR%` / `%LOCALAPPDATA%` 等路径宏由 CodeBee 侧展开；
  camelCase 字段（`riskNote` / `cleanTarget` / `migrateNote` / `minAgeDays`）在读取时映射为
  下划线命名；JSON 文件本身未改动。
- **许可状态（2026-11-05 二次核实）**：上游 `README.md` 明确写「本项目基于 MIT License
  开源」并给出 `LICENSE` 链接，但：
  - 仓库根目录**没有 LICENSE 文件**（Contents API 根目录列表 + 该链接 raw 均 404）；
  - GitHub Repos API 的 `license` 字段为 **`null`**（GitHub 未识别到任何许可证）；
  - 递归遍历 `main` 分支整棵 git tree，**全仓没有任何 LICENSE / NOTICE 文件**。

  即：MIT 的授权声明存在，但许可证条款文本未随仓库提供。本轮只复制三个 JSON 数据
  文件、未复制任何代码，风险有限；但**分发前必须确认**，不能把 README 的一句话当成
  已落地许可。
  > 合规提示：若权利人后续提出异议或补上了与 README 不一致的许可条款，直接删除
  > `app/core/cspace/rules/` 下三个 JSON，`rules.load_rules()` 会返回空规则集，其余功能
  > 不受影响（扫描与只读盘点仍可用，清理/搬家候选为空）。
- **归属**：CodeBee 界面、注释与文档不出现上游产品名；此处保留来源链接仅为满足
  开源归属与合规追溯。

## 既有来源

项目其他借鉴来源与决策记录见根目录 `迭代计划.md` 第 5 节。
