# 可插拔路由策略

本文定义一个不依赖第三方包的路由策略契约。它是设计接口，不改变当前实现；现有默认行为仍由 `app/core/router.py` 提供。

## 接口

```python
from abc import ABC, abstractmethod


class RoutingStrategy(ABC):
    @abstractmethod
    def select_cli(self, task, available_clis):
        """返回用于执行 task 的 CLI 描述，或 None。"""

    @abstractmethod
    def should_fallback(self, error):
        """返回错误是否属于可切换候选 CLI 的瞬态/配额类失败。"""

    @abstractmethod
    def get_fallback_chain(self, cli):
        """按优先级返回 cli 之后的候选 CLI 列表。"""
```

`task`、`error` 和 CLI 描述均使用 JSON 兼容的字典；策略不得修改输入。返回值至少包含稳定的 `id`，展示字段可包含 `label`、`kind`、`model` 和 `provider`。

## 默认策略

默认策略包装当前 `router.route_plan`/`pick` 行为：先按角色和任务类型过滤可用绑定，再综合绑定优先级、在线/健康状态、历史成功率与评审跨模型族约束；执行失败仅在 `runner` 分类为瞬态、限流、配额耗尽或上游不可用时触发降级。鉴权、参数错误和用户取消默认不降级。实际权重和错误码以 `router.py`、`runner.py` 为准，未来若稳定化需要【需确认】。

伪代码示例：

```python
class BuiltinStrategy(RoutingStrategy):
    def select_cli(self, task, available_clis):
        rows = router.route_plan(
            available_clis, task.get("role", "implement"), task
        )
        selected = rows.get("selected")
        return next((c for c in available_clis if c.get("id") == selected), None)

    def should_fallback(self, error):
        return error.get("category") in {"transient", "rate_limit", "quota"}

    def get_fallback_chain(self, cli):
        return list(cli.get("fallbacks") or [])
```

## 配置加载

建议在用户配置中使用 JSON（保持标准库可解析），通过明确的策略名称选择内置或自定义策略：

```json
{
  "routing": {
    "strategy": "builtin",
    "fallback_on": ["transient", "rate_limit", "quota"],
    "chains": {
      "codex": ["claude-code", "qwen-code"],
      "claude-code": ["codex"]
    }
  }
}
```

自定义策略建议使用受控 Python 模块路径，例如 `routing.strategy_module` + `routing.strategy_class`，由启动时 `importlib` 加载；模块必须位于用户明确允许的目录，不能从任务输入动态导入。若项目选择只支持注册表而不支持动态导入，配置键名和加载入口应在实现前确认【需确认】。无论哪种方案，策略加载失败都应回退内置策略并写入启动日志。

## 兼容与测试

- 策略方法必须是纯同步、无网络副作用；耗时探测由健康检查层完成。
- 对同一输入应得到可解释的选择理由，并随 run 路由快照保存。
- 测试至少覆盖空候选、健康候选过滤、瞬态错误降级、鉴权错误不降级、循环 fallback 链和未知策略回退。
