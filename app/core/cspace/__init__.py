# -*- coding: utf-8 -*-
"""空间管家：CodeBee 的磁盘透视、安全清理与无损搬家底盘（2026-11-05）。

从上游 MIT 开源桌面工具移植的是**规则数据与机制**，不是代码：那边是 Rust +
Tauri 的桌面程序，只有 GUI 内的 IPC 命令；这边是纯 Python 标准库的本地服务。
规则数据来自上游 MIT 项目，见仓库根 ``THIRD_PARTY_NOTICES.md``。

分层（依赖自上而下，只读层不反向依赖系统层）：

    api.py          HTTP 契约：GET 只读 / POST 两阶段确认 / 后台操作进度
    rules.py        规则库加载、路径宏与通配展开、最长前缀匹配
    risk.py         三级风险、硬白名单、reparse point 识别、启发式兜底
    scanner.py      流式目录遍历、单层透视、容量换算
    analyzer.py     大文件、重复文件三阶段哈希、硬链接替换
    winapi.py       盘符 / 提权 / 占用探测 / 系统回收站（Windows，失败优雅降级）
    links.py        目录联接、符号链接、硬链接的创建与拆除
    ledger.py       链接账本、迁移账本、审计日志、两阶段确认计划
    cleaner.py      清理计划与执行（先计划、后确认、只回收或归档）
    migrator.py     事务式搬家七步 + 回滚 + 一键迁回

三条不可协商的红线：

1. **默认只读**。所有写动作 ``confirm=False`` 时只返回计划，绝不动文件系统。
2. **两阶段确认**。破坏性动作必须先拿 ``plan_id`` / ``precheck``，再显式确认；
   执行前重新校验白名单与硬红线，不信任前端传来的任何路径。
3. **不硬删用户文件**。清理走系统回收站，失败则同盘归档到数据目录；
   搬家留备份，任何一步失败都回滚到动之前的样子。

包名沿用上游目录名只是为了规则文件的归属可追溯；界面与文案一律只叫
CodeBee「空间管家」。删掉 ``app/ui/cspace.js`` 与 ``cspace.css`` 两行引用即
回滚整个前端，后端包留着不影响任何现有功能。
"""

from __future__ import annotations

# 不在 import 时导入子模块：规则加载、账本读取、盘符枚举都必须在显式调用时
# 才发生，测试用临时数据目录注入时才不会碰到真实用户文件。
__all__ = [
    "api",
    "rules",
    "risk",
    "scanner",
    "analyzer",
    "winapi",
    "links",
    "ledger",
    "cleaner",
    "migrator",
]
