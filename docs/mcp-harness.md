# MCP Harness · 编排平台能力暴露

本 Harness 通过 MCP（Model Context Protocol）把编排平台的核心能力暴露给外部 AI 客户端（Claude Desktop、Cursor、Qoder 等），让 Agent 可以直接操控平台对象，无需手点 UI。

## 架构

```
外部 AI 客户端 ──(MCP Streamable HTTP)──▶ /mcp/orchestration
                                              │
                                              ▼
                                    mcp_harness.py
                                    ├── 鉴权层（Bearer Token）
                                    ├── 写开关（allowMutatingTools）
                                    └── 工具执行层
                                              │
                                              ▼
                                    平台核心模块
                                    ├── registry（Agent 管理）
                                    ├── flows（Workflow 模板）
                                    ├── jobs（任务队列）
                                    └── store（执行历史）
```

## 快速开始

### 1. 生成访问令牌

```bash
curl -X POST http://localhost:8765/api/mcp/harness/token \
  -H "Content-Type: application/json" \
  -H "X-CodeBee-Token: <your-access-token>" \
  -d '{"action": "create", "user_id": "claude-desktop"}'
```

响应：
```json
{
  "token": "orch_xxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx",
  "warning": "Token shown once. Store it securely."
}
```

**重要**：令牌只展示一次，立即保存。

### 2. 配置 AI 客户端

#### Claude Desktop / Cursor

```json
{
  "mcpServers": {
    "orchestration": {
      "type": "http",
      "url": "http://localhost:8765/mcp/orchestration",
      "headers": {
        "Authorization": "Bearer orch_xxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx"
      }
    }
  }
}
```

#### Qoder

```json
{
  "mcpServers": {
    "orchestration": {
      "url": "http://localhost:8765/mcp/orchestration",
      "headers": {
        "Authorization": "Bearer orch_xxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx"
      }
    }
  }
}
```

### 3. 测试连接

```bash
curl http://localhost:8765/mcp/orchestration \
  -H "Authorization: Bearer orch_xxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx"
```

应返回工具列表。

## 工具清单

工具命名遵循 `object_action` 模式，便于发现和记忆。

### Agent 管理（agent_*）

| 工具 | 写 | 说明 |
| --- | --- | --- |
| `agent_list` | | 列出所有已注册 Agent 及其能力 |
| `agent_get` | | 按 ID 查看 Agent 详情 |
| `agent_create` | ✔ | 创建新 Agent 定义 |
| `agent_update` | ✔ | 部分更新 Agent（未提交字段保持原值） |
| `agent_delete` | ✔ | 删除 Agent 定义 |

### Workflow 模板（workflow_*）

| 工具 | 写 | 说明 |
| --- | --- | --- |
| `workflow_list` | | 列出所有 Workflow 模板 |
| `workflow_get` | | 按 ID 查看 Workflow 详情（含步骤定义） |
| `workflow_create` | ✔ | 创建新 Workflow 模板 |
| `workflow_update` | ✔ | 更新 Workflow 模板 |
| `workflow_delete` | ✔ | 删除 Workflow 模板 |

### 任务管理（task_*）

| 工具 | 写 | 说明 |
| --- | --- | --- |
| `task_list` | | 列出任务（支持按状态过滤） |
| `task_get` | | 按 ID 查看任务详情 |
| `task_create` | ✔ | 创建并提交新任务到队列 |
| `task_cancel` | ✔ | 取消待执行或运行中的任务 |

### 执行历史（execution_*）

| 工具 | 写 | 说明 |
| --- | --- | --- |
| `execution_list` | | 列出任务执行记录（可按 task_id 过滤） |
| `execution_get` | | 按 ID 查看执行详情（含输出、日志） |

### 元工具

| 工具 | 写 | 说明 |
| --- | --- | --- |
| `orchestration_catalog` | | 按能力分组列出所有工具，标注是否写操作 |

**建议**：首次连接时调用 `orchestration_catalog` 获取完整能力清单。

## 安全模型

### 分层鉴权

```
1. 传输层鉴权：Bearer Token 校验（缺失或非法 → 401）
2. 令牌解析：反查用户身份（只存 SHA-256 哈希）
3. 写开关检查：tool.mutating() && !allowMutatingTools → 拒绝
4. 执行与清理：结果序列化为 JSON，无论成败都清理上下文
```

### 令牌管理

```bash
# 列出所有令牌（脱敏）
curl -X POST http://localhost:8765/api/mcp/harness/token \
  -H "Content-Type: application/json" \
  -H "X-CodeBee-Token: <your-access-token>" \
  -d '{"action": "list"}'

# 撤销令牌
curl -X POST http://localhost:8765/api/mcp/harness/token \
  -H "Content-Type: application/json" \
  -H "X-CodeBee-Token: <your-access-token>" \
  -d '{"action": "revoke", "token": "orch_xxxxxxxx"}'
```

### 写操作开关

默认关闭写操作（安全模式）。启用方法：

```bash
# 临时启用（运行时）
curl -X POST http://localhost:8765/api/settings \
  -H "Content-Type: application/json" \
  -H "X-CodeBee-Token: <your-access-token>" \
  -d '{"mcp_harness_allow_mutating": true}'

# 永久启用（配置文件）
# 在 settings.json 中添加：
{
  "mcp_harness_allow_mutating": true
}
```

**建议**：生产环境保持关闭，仅在开发/测试时启用。

### 响应裁剪

敏感字段永不返回：
- Agent 定义中的 API Key / Secret
- 任务上下文中的用户凭证
- 执行日志中的环境变量

## 典型工作流

### 场景 1：查询 Agent 能力并创建任务

```text
1. orchestration_catalog       → 获取工具清单
2. agent_list                  → 列出可用 Agent
3. agent_get                   → 查看某个 Agent 详情（能力、参数）
4. workflow_list               → 列出 Workflow 模板
5. task_create                 → 提交任务（指定 workflow_id + 参数）
6. execution_list              → 查询执行历史
7. execution_get               → 查看某次执行的详细输出
```

### 场景 2：动态编排（需启用写操作）

```text
1. agent_create                → 注册新 Agent（自定义能力）
2. workflow_create             → 创建新 Workflow（组合多个 Agent）
3. task_create                 → 提交任务
4. agent_update                → 根据执行结果调整 Agent 参数
5. workflow_update             → 优化 Workflow 步骤
```

## 与 DataY 的对比

| 特性 | DataY | 本实现 |
| --- | --- | --- |
| 协议 | MCP Streamable HTTP | MCP Streamable HTTP |
| 端点 | `/mcp/datay` | `/mcp/orchestration` |
| 鉴权 | Bearer Token（BCrypt） | Bearer Token（SHA-256） |
| 写开关 | ✅ | ✅ |
| 多租户 | ✅（X-Tenant-Id） | ❌（单用户） |
| 工具命名 | `object_action` | `object_action` |
| 元工具 | `datay_harness_catalog` | `orchestration_catalog` |
| 响应裁剪 | ✅ | ✅ |

## 扩展指南

### 添加新工具

1. 在 `app/core/mcp_harness.py` 中定义工具函数：

```python
@_tool("custom_action")
def custom_action(params: dict) -> dict:
    """工具描述（会出现在工具列表中）。"""
    # 实现逻辑
    return {"result": "..."}
```

2. 如果是写操作，添加 `@_mark_mutating` 装饰器：

```python
@_mark_mutating
@_tool("custom_create")
def custom_create(params: dict) -> dict:
    """创建资源（写操作）。"""
    if not _allow_mutating():
        return {"error": "Mutating operations are disabled"}
    # 实现逻辑
    return {"created": True}
```

3. 工具会自动注册到 `_TOOLS` 字典，无需手动维护列表。

### 添加新对象类型

参考现有 `agent_*`、`workflow_*`、`task_*`、`execution_*` 的分组模式：

1. 创建新的核心模块（如 `app/core/custom_objects.py`）
2. 在 `mcp_harness.py` 中导入并添加工具
3. 更新本文档的工具清单

## 故障排查

### 401 Unauthorized

- 检查 Authorization 头格式：`Bearer <token>`
- 确认令牌未撤销：`/api/mcp/harness/token?action=list`
- 验证服务端口和地址

### 写操作被拒绝

- 检查写开关：`settings.mcp_harness_allow_mutating`
- 确认请求包含 `@_mark_mutating` 装饰器的工具会触发此检查

### 工具执行失败

- 查看服务日志：`log.exception("Tool execution failed: %s", name)`
- 确认平台核心模块（registry、flows、jobs、store）正常工作
- 检查参数格式是否符合工具定义

## 参考资料

- [MCP 协议规范](https://modelcontextprotocol.io/specification)
- [DataY MCP Harness 文档](https://github.com/yuyuejia/datay/blob/main/datay-web/docs/datay-mcp-harness.md)
- [Streamable HTTP 传输](https://modelcontextprotocol.io/specification/2025-06-18/transports/streamable-http)
