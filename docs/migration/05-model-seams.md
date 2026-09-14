# 主题 5：模型/凭据/设置 seam 化（Phase 5）

> dsh 把模型/凭据/设置做成 capability seam，三角色（Service Definition / Provider / Consumer）。
> Tutti 现状 modelhub 双写、凭据分散、settings 仅 key-value。本主题 seam 化。

---

## 4A. LLMProvider 抽象 + 原子切换

> **⚠️ 2026-09-14 实施时复核：dsh 式 adapter 重构对 Tutti 无净收益，取消。**
> 复核 `app/core/modelhub.py`：`chat()`（L1841）本身已是统一 adapter seam——
> anthropic/openai/google 三协议在一个入口分发、`providers()` 每次现读、
> `_save()` tmp+replace 原子写。再抽 `LlmProvider` 协议 + ProviderRegistry
> 只是把现有函数包一层间接层，没有可替换的第二个实现要挂进来
> （dsh 需要该抽象是因为它有 llm-deepseek / llm-pi-ai / llm-replay 多提供方并存）。
> 未来若出现第二实现（如同一供应商多网关 A/B），再按需引入。

**目标**：把 modelhub 双写（registry + bindings）改为单写 `registerProvider`，返回带 `replace()` 的 handle；约 5 处 chat() 调用点同步改造。

**dsh 参考**：
- [`packages/llm/llm/src/index.ts:200-416`](E:/GoOut/_dsh_ref/packages/llm/llm/src/index.ts#L200) `LlmAdapter` 抽象 + `registerAdapter` + `AdapterRegistrationHandle.replace()`
- [`packages/llm/llm/src/types.ts:390`](E:/GoOut/_dsh_ref/packages/llm/llm/src/types.ts#L390) `StreamChunk` 词汇表

**Tutti 痛点**：
- [`app/core/modelhub.py`](E:/GoOut/MultiAgentOrchestration/app/core/modelhub.py) 双写 registry + bindings
- chat() 调用点散落 5+ 处
- 新增模型靠手改 modelhub.py

**改动范围**：
- 重构 `app/core/modelhub.py`：新增 `LlmProvider` 抽象类 + `register_provider()` + `ProviderHandle.replace()`
- `chat()` 改为调当前 provider；`bind_agent()` 改为查 handle

**代码骨架**：
```python
# app/core/modelhub.py（重写上层骨架，保留 modelhub_v1.py 兼容旧逻辑）
from typing import Protocol, Iterator
from dataclasses import dataclass

@dataclass
class StreamChunk:
    type: str  # "text" | "reasoning" | "tool_call" | "usage" | "finish"
    delta: str = ""
    usage: dict = None
    finish_reason: str = ""

class LlmProvider(Protocol):
    """所有 LLM 适配器实现这个协议。chat/stream 由 modelhub 包装。"""
    name: str

    def stream(self, messages: list[dict], *, model: str, signal=None,
               timeout=None) -> Iterator[StreamChunk]: ...
    def capabilities(self, model: str) -> dict:
        """返回 {'context_window': N, 'supports_tools': bool, ...}"""
        ...

class ProviderHandle:
    def __init__(self, registry, name: str, provider: LlmProvider):
        self.registry = registry
        self.name = name
        self.provider = provider

    def replace(self, new_provider: LlmProvider):
        """原子换路由；调用方下次 stream() 自动用 new_provider。"""
        with self.registry._lock:
            self.registry._providers[self.name] = new_provider

class ProviderRegistry:
    def __init__(self):
        self._providers: dict[str, LlmProvider] = {}
        self._lock = threading.Lock()

    def register(self, provider: LlmProvider) -> ProviderHandle:
        with self._lock:
            self._providers[provider.name] = provider
            return ProviderHandle(self, provider.name, provider)

    def get(self, name: str) -> LlmProvider:
        return self._providers[name]  # KeyError 即可

provider_registry = ProviderRegistry()

# 现有 chat() 改造
def chat(messages: list[dict], *, model: str, **kwargs) -> dict:
    provider_name = resolve_provider_name(model)  # 从 model 名映射到 provider
    provider = provider_registry.get(provider_name)
    chunks = list(provider.stream(messages, model=model, **kwargs))
    return _chunks_to_result(chunks)
```

**集成点**：
- `bind_agent(impl, difficulty)` 改为查 provider_registry 中"difficulty=..."配置
- `classify_difficulty()` 保留
- 约 5 处 chat() 调用点改走 chat() wrapper（统一接口）

**测试**（`tests/test_provider_registry.py`）：
- register → get 返回
- handle.replace() → 下次 get 是新的
- 并发 register/replace → lock 安全
- 现有所有 chat() 调用点迁移后跑回归测试

**风险**：
- **改造面广**：modelhub.py 1897 行全量重写风险高。
- **策略**：先抽 `ProviderRegistry` + `LlmProvider` 协议，保留 `chat()` 旧实现兼容 1 周；之后渐进替换 chat() 调用点。

**回退**：保留 `modelhub_v1.py`，`ProviderRegistry` 内部调用旧实现。

**依赖**：4B（CredentialRef 配套）。

---

## 4C. Settings schema-driven + path-mutate + revision

> **✅ 2026-09-14 落地为 `app/core/settings_schema.py`（零触碰 settings.py）。**
> 按 Tutti 实际架构调整：settings.py 正被并行迭代且已有原子写+钳制，故不替换；
> 本模块提供「namespace 注册 + FieldDef 类型/choices/clamp + revision CAS +
> describe(redact_secrets) 脱敏」，管理独立的 data/settings_v2.json，供新增配置
> 使用（已注册默认 namespace `orchestrator`：compaction.* 与 max_goal_rounds）。
> 18 测试绿（tests/test_settings_schema.py）。

**目标**：把 `max_concurrent_jobs` 和未来的阈值类配置迁到 schema 层；引入 `mutate(ns, ops)` 支持"只写 secret 路径"。

**dsh 参考**：
- [`packages/settings/settings/src/index.ts:419-637`](E:/GoOut/_dsh_ref/packages/settings/settings/src/index.ts#L419) schemastery schema + `base` + `validate` + revision
- [`packages/settings/settings/src/index.ts:154`](E:/GoOut/_dsh_ref/packages/settings/settings/src/index.ts#L154) `SettingsConflictError` revision CAS

**Tutti 痛点**：
- [`app/core/settings.py`](E:/GoOut/MultiAgentOrchestration/app/core/settings.py) 仅 max_concurrent_jobs key-value
- 新增配置项需要手改 settings.py + UI 字段 + 读取处
- 没有 revision，UI 和 server 并发改配置会丢更新

**改动范围**：
- 新增 `app/core/settings_schema.py`（约 150 行）— schema 定义 + SettingsManager
- `app/core/settings.py` 改为 SettingsManager 包装
- `app/ui/app.js`：配置面板读 schema 自动生成表单

**代码骨架**：
```python
# app/core/settings_schema.py
import threading, json, time
from dataclasses import dataclass, field
from typing import Callable, Optional, List, Dict

class SettingsConflictError(Exception):
    pass

@dataclass
class FieldDef:
    path: str                # "max_concurrent_jobs" 或 "models.providers[0].api_key"
    type: str                # "int" | "str" | "bool" | "enum"
    default: object
    description: str = ""
    redact: bool = False     # 对应 dsh 的 role('secret')
    enum_values: List[object] = field(default_factory=list)

@dataclass
class NamespaceDef:
    ns: str
    fields: List[FieldDef]
    validate: Optional[Callable[[dict], Optional[str]]] = None

class SettingsManager:
    def __init__(self, home: str):
        self.home = home
        self._ns: Dict[str, NamespaceDef] = {}
        self._values: Dict[str, dict] = {}
        self._revision: Dict[str, int] = {}
        self._lock = threading.Lock()
        self._load()

    def register(self, ns_def: NamespaceDef):
        with self._lock:
            self._ns[ns_def.ns] = ns_def
            # 缺省值填充
            if ns_def.ns not in self._values:
                self._values[ns_def.ns] = {}
                for f in ns_def.fields:
                    _set_path(self._values[ns_def.ns], f.path, f.default)
            self._persist()

    def get(self, ns: str, path: Optional[str] = None):
        v = self._values.get(ns, {})
        if path is None:
            return v
        return _get_path(v, path)

    def mutate(self, ns: str, ops: List[dict], *, expected_revision: Optional[int] = None):
        """ops = [{"op":"set","path":"max_concurrent_jobs","value":3}, ...]"""
        with self._lock:
            if expected_revision is not None and expected_revision != self._revision.get(ns, 0):
                raise SettingsConflictError(
                    f"ns {ns} expected rev {expected_revision}, got {self._revision.get(ns, 0)}")
            for op in ops:
                if op["op"] == "set":
                    _set_path(self._values[ns], op["path"], op["value"])
                elif op["op"] == "del":
                    _del_path(self._values[ns], op["path"])
            # 校验
            ns_def = self._ns.get(ns)
            if ns_def and ns_def.validate:
                err = ns_def.validate(self._values[ns])
                if err:
                    raise ValueError(f"validation failed: {err}")
            self._revision[ns] = self._revision.get(ns, 0) + 1
            self._persist()
            return self._revision[ns]

    def describe(self, ns: str, *, redact_secrets: bool = True) -> dict:
        """返回不含 secret 值的描述，UI 用。"""
        v = json.loads(json.dumps(self._values.get(ns, {})))  # deep copy
        ns_def = self._ns.get(ns)
        if ns_def:
            for f in ns_def.fields:
                if f.redact and redact_secrets:
                    _set_path(v, f.path, "__REDACTED__")
        return v

    def _persist(self):
        # 写 data/settings.json（原子 rename）
        path = os.path.join(self.home, "settings.json")
        tmp = path + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump({"_revision": self._revision, "_values": self._values},
                      f, indent=2, ensure_ascii=False)
        os.replace(tmp, path)

    def _load(self):
        path = os.path.join(self.home, "settings.json")
        try:
            with open(path, encoding="utf-8") as f:
                data = json.load(f)
            self._revision = data.get("_revision", {})
            self._values = data.get("_values", {})
        except (OSError, json.JSONDecodeError):
            self._revision = {}
            self._values = {}

# _set_path / _get_path：支持 "a.b.c" 和 "a[0].b" 路径
def _set_path(d, path, value): ...
def _get_path(d, path): ...
def _del_path(d, path): ...

settings_mgr = SettingsManager(os.path.join(os.path.expanduser("~"), ".tutti"))
```

**注册现有 settings**：
```python
# app/core/settings.py 改造
def register_default_namespaces():
    settings_mgr.register(NamespaceDef(
        ns="orchestrator",
        fields=[
            FieldDef("max_concurrent_jobs", "int", 3, "最大并发任务数 1-6"),
            FieldDef("max_goal_rounds", "int", 5, "Goal 续行上限 1-20"),
            FieldDef("compaction_enabled", "bool", True, "是否启用会话压缩"),
            FieldDef("compaction_pressure_threshold", "float", 0.8, "触发压缩的压力阈值"),
        ],
        validate=lambda v: None if 1 <= v.get("max_concurrent_jobs", 3) <= 6
                          else "max_concurrent_jobs 必须在 1-6",
    ))
    settings_mgr.register(NamespaceDef(
        ns="credentials",
        fields=[
            FieldDef("OPENAI_API_KEY", "str", "", "OpenAI API Key", redact=True),
            FieldDef("ANTHROPIC_API_KEY", "str", "", "Anthropic API Key", redact=True),
            ...
        ],
    ))
```

**测试**（`tests/test_settings_manager.py`）：
- register → get 返回 default
- mutate set → 值更新 + revision+1
- mutate expected_revision 错 → SettingsConflictError
- describe redact=True → secret 字段返回 __REDACTED__
- validate 失败 → mutate 抛 ValueError
- 并发 mutate → lock 安全

**风险**：
- 中。需要一次配置迁移（data/orchestration.json → settings.json）。
- **策略**：迁移器在启动时自动跑，老字段映射到新 namespace。

**回退**：保留旧 `app/core/settings.py` 接口做 wrapper。

**依赖**：4B（凭据分层）。

---

## 4D. 能力维度（按能力选模型）

> **✅ 2026-09-14 落地为 `app/core/capability.py`（零触碰 modelhub.py）。**
> 供应商在 data/models.json 里声明可选 `strengths: [writing|coding|reasoning|vision]`
> 字段即可被识别（未声明视为无偏好兜底）；`classify_task_type`（显式 task_type >
> 关键词分类 > coding 兜底）、`pick_by_strength`（强项优先/原序兜底/停用过滤）、
> `resolve_binding_by_task`（维度绑定缺失回落 default）。13 测试绿（tests/test_capability.py）。
> bindings 按维度分键的 UI/存储改造留待接入时做（本模块已兼容现有 default 键）。

**目标**：给每个 provider profile 加 `capability_dim`（writing/code/reasoning/vision），bindings 可按 dim 选模型而非简单"主备"。

**dsh 参考**：[`packages/llm/llm/src/types.ts:566`](E:/GoOut/_dsh_ref/packages/llm/llm/src/types.ts#L566) `LlmResolvedModelInfo.reasoning` 类似设计。

**Tutti 痛点**：跨厂商降级只看瞬态可用，不看能力；编码强但写作弱时不会只换写作。

**改动范围**：
- `app/core/modelhub.py`：provider profile 加 `capability_dim: List[str]`
- `bindings[difficulty].chain` 升级为 `bindings[task_type].chain`，task_type ∈ {writing, coding, reasoning, vision}

**代码骨架**：
```python
# modelhub.py bindings 升级
{
  "bindings": {
    "writing": {
      "preferred": ["claude-sonnet-4-5", "gpt-5"],
      "chain": ["claude-sonnet-4-5", "deepseek-chat"],
    },
    "coding": {
      "preferred": ["deepseek-coder", "claude-sonnet-4-5"],
      "chain": ["deepseek-coder", "gpt-5", "claude-sonnet-4-5"],
    },
    "reasoning": {
      "preferred": ["deepseek-reasoner", "o1"],
      "chain": ["deepseek-reasoner", "o1"],
    },
    "vision": {
      "preferred": ["gpt-5-vision", "claude-sonnet-4-5"],
      "chain": ["gpt-5-vision"],
    },
  }
}

# classify_difficulty 升级
def classify_task_type(task) -> str:
    """根据 task 描述/角色分类到 writing/coding/reasoning/vision。"""
    text = (task.description or "") + " " + (task.role or "")
    if any(k in text for k in ["写", "文档", "营销", "总结"]):
        return "writing"
    if any(k in text for k in ["code", "实现", "修复", "重构", "test"]):
        return "coding"
    if any(k in text for k in ["推理", "分析", "调研", "对比"]):
        return "reasoning"
    if any(k in text for k in ["图", "ocr", "视觉", "image"]):
        return "vision"
    return "coding"  # default
```

**集成点**：
```python
# pipeline.py 替代 resolve_binding
def resolve_binding(task) -> dict:
    task_type = classify_task_type(task)
    return modelhub.bindings.get(task_type, modelhub.bindings["coding"])
```

**测试**（`tests/test_capability_dim.py`）：
- "写一篇营销文案" → writing
- "修复 bug #123" → coding
- "推理为什么性能慢" → reasoning
- bindings 缺 task_type → fallback 到 coding
- chain 中模型都不可用 → 全失败而非随机

**风险**：
- 中。能力维度分类质量依赖关键词。
- **对策**：先用关键词分类，留扩展点允许 task 显式指定 `task_type` 字段覆盖。

**回退**：保留原 `classify_difficulty` 函数（=coding/fast/slow 三档）。

**依赖**：4A。

---

## 4E. dormant provider（UI 显示但未启用）

> **⚠️ 2026-09-14 实施时复核：机制已存在，取消。**
> `modelhub.providers_op(ids, "disable")`（L555）即 dormant 语义——停用只影响
> 编排时运行时解析、不清配置、不影响绑定引用、随时可再启用；启用供应商的
> 模型目录拉取（L259）只对 `enabled=True` 且有 api_key 的条目执行。
> UI 的供应商启停开关走的正是这条通路。

**目标**：modelhub 已有 `providers` 数组，但启用需要重启；引入 dormant provider，UI 可勾选启用，启用时原子 swap。

**dsh 参考**：[`packages/llm/llm/src/index.ts:481`](E:/GoOut/_dsh_ref/packages/llm/llm/src/index.ts#L481) `LlmConfigurableProvider` 描述"配置可激活但未必已注册"的路由。

**Tutti 痛点**：新增 provider 靠手改 main.py；启用 OpenAI/Anthropic/DeepSeek 任一都需重启。

**改动范围**：
- `app/core/modelhub.py`：providers 数组拆为 `enabled_providers` + `dormant_providers`
- `app/main.py`：启动时仅 register `enabled_providers`；runtime 可调 `enable(name)` 把 dormant → enabled
- `app/ui/app.js`：provider 列表显示 dormant（灰色），点启用按钮调 API

**代码骨架**：
```python
# modelhub.py
class ModelHubConfig:
    enabled_providers: List[dict]   # 启动时 register 到 provider_registry
    dormant_providers: List[dict]   # UI 可勾选启用

def enable_provider_runtime(name: str, credentials: dict):
    """把 dormant provider 转为 enabled，注册到 provider_registry。"""
    cfg = next((p for p in dormant_providers if p["name"] == name), None)
    if not cfg:
        raise KeyError(name)
    # 构造 LlmProvider 实例
    provider = build_provider(cfg["type"], credentials)
    provider_registry.register(provider)
    dormant_providers.remove(cfg)
    enabled_providers.append(cfg)
    persist_config()
```

**API 端点**（`app/main.py`）：
```python
POST /api/providers/{name}/enable
{
  "credentials": {"OPENAI_API_KEY": "..."}
}
→ {"ok": true, "provider": "openai"}
```

**测试**（`tests/test_dormant_provider.py`）：
- 启动时只 register enabled_providers
- enable_provider_runtime → 出现在 provider_registry + 从 dormant 移除
- 持久化：重启后仍是 enabled
- 并发 enable 同一 provider → 锁安全

**风险**：
- 中。dormant → enabled 时加载 SDK 可能慢。
- **对策**：UI 显示"正在加载"提示，API 走 5s 超时。

**回退**：删除 dormant 配置项，回退到"全 enabled"启动行为。

**依赖**：4A。

---

## 5E（重复）：per-vendor timeout

参见 [01-defense-patterns.md §5E](01-defense-patterns.md#5e-每-cli-独立超时per-vendor-timeout)。本主题 5 把它纳入 Phase 5 实施。

---

## Phase 5 实施清单

按依赖顺序：

1. **4A LLMProvider 抽象**（大改，先抽 Protocol + Registry，渐进替换 chat 调用）
2. **4C Settings schema**（依赖 4B 凭据分层）
3. **4D 能力维度**（依赖 4A bindings 升级）
4. **4E dormant provider**（依赖 4A + UI）
5. **5E per-vendor timeout**（依赖 4A catalog 升级）

每完成一项：`provider_registry` 默认接管该功能，老路径保留 1 周灰度。

---

## Phase 5 风险与回退

- 整体改造面广（modelhub.py + settings.py + UI）。
- **灰度开关**：`data/orchestration.json: provider_v2 = false`。
- 大改前先建 `modelhub_v2.py` 平行运行两周。