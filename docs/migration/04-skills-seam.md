# 主题 4：技能 seam 化（Phase 4）

> dsh 把 skills 做成"分层注册表 + 多源 provider + catalog 注入 + 按需正文加载"。
> Tutti 现状是"函数注册式"，缺渐进加载、缺跨运行沉淀。本主题重做。

---

## 3A. SkillProvider 多源分层 + 路径热监听

**目标**：把内置 pack / `data/skillpacks/` / 自动 lessons 三类做成独立 provider；每轮仅 diff 注入 catalog；磁盘目录热监听失效。

**dsh 参考**：
- [`packages/skill/skill/src/index.ts:356`](E:/GoOut/_dsh_ref/packages/skill/skill/src/index.ts#L356) 分层注册表，最近层赢
- [`packages/skill/skill-filesystem/src/index.ts`](E:/GoOut/_dsh_ref/packages/skill/skill-filesystem/src/index.ts) chokidar 热失效
- [`packages/skill/tool-skill/src/index.ts:213-251`](E:/GoOut/_dsh_ref/packages/skill/tool-skill/src/index.ts#L213) 每轮注入 catalog + digest 替换

**Tutti 痛点**：
- [`app/core/skills.py`](E:/GoOut/MultiAgentOrchestration/app/core/skills.py) 是函数注册式，仅在内存里
- 自动 lessons 难跨运行沉淀
- 现有 `block_for()` 6000 字截断要重新放权

**改动范围**：
- 重构 `app/core/skills.py`：改为"provider 注册表 + 工具调用"
- 新增 `app/core/skill_providers/` 子目录：`builtin.py`（内置 pack）/ `filesystem.py`（磁盘）/ `lessons.py`（自动 lessons）
- `pipeline.py:_run_step` 每轮调 `skill_registry.get_catalog()` 注入 prompt

**代码骨架**：
```python
# app/core/skills.py（重写为注册表骨架）
from typing import Protocol, List, Dict, Optional
import hashlib

class SkillSummary:
    """轻量摘要：每轮都注入。"""
    def __init__(self, name: str, description: str, invocation: str):
        self.name = name
        self.description = description
        self.invocation = invocation  # "model" | "user" | "both"

class SkillContent:
    """按需加载的完整正文。"""
    def __init__(self, name: str, body: str, metadata: dict):
        self.name = name
        self.body = body
        self.metadata = metadata

class SkillProvider(Protocol):
    def list(self) -> List[SkillSummary]: ...
    def get(self, name: str) -> Optional[SkillContent]: ...
    def invalidate(self) -> None: ...  # 路径热监听触发

class SkillRegistry:
    def __init__(self):
        self._providers: List[SkillProvider] = []
        self._cache: Dict[str, SkillSummary] = {}
        self._cache_digest = ""

    def register(self, provider: SkillProvider, priority: int):
        # priority: builtin=0, filesystem=1, lessons=2（数字大的赢）
        self._providers.append((priority, provider))

    def _rebuild_cache(self):
        cache = {}
        for _, p in sorted(self._providers, key=lambda x: -x[0]):
            for s in p.list():
                if s.name not in cache:  # 高优先级先注册，先到先得
                    cache[s.name] = s
        self._cache = cache
        # digest 用于判断"目录是否变化"
        body = "|".join(f"{s.name}:{s.description}" for s in cache.values())
        self._cache_digest = hashlib.sha1(body.encode()).hexdigest()[:16]

    def get_catalog(self, *, force=False) -> tuple[str, List[SkillSummary]]:
        if force or not self._cache:
            self._rebuild_cache()
        return self._cache_digest, list(self._cache.values())

    def get(self, name: str) -> Optional[SkillContent]:
        # 倒序遍历 provider 找内容
        for _, p in sorted(self._providers, key=lambda x: -x[0]):
            c = p.get(name)
            if c:
                return c
        return None

    def invalidate(self, name: Optional[str] = None):
        """文件系统 provider 监听到变化时调用。"""
        if name is None:
            self._cache.clear()
        else:
            self._cache.pop(name, None)

skill_registry = SkillRegistry()
```

**provider 实现骨架**：
```python
# app/core/skill_providers/builtin.py
class BuiltinProvider:
    def __init__(self):
        self._summaries = [
            SkillSummary("search", "搜索代码/文档", "model"),
            SkillSummary("edit", "编辑文件", "model"),
            ...
        ]
        self._contents = {
            "search": SkillContent("search", "...", {}),
            ...
        }
    def list(self):
        return self._summaries
    def get(self, name):
        return self._contents.get(name)

# app/core/skill_providers/filesystem.py
import os, glob, threading, time
from watchdog.observers import Observer  # 已有依赖

class FilesystemProvider:
    def __init__(self, root_dir: str):
        self.root_dir = root_dir
        self._summaries = {}
        self._contents = {}
        self._lock = threading.Lock()
        self._scan()
        self._watch()
    def _scan(self):
        for path in glob.glob(os.path.join(self.root_dir, "**/SKILL.md"), recursive=True):
            text = open(path, encoding="utf-8").read()
            # 解析 frontmatter
            name, desc, body = parse_skill_md(text)
            self._summaries[name] = SkillSummary(name, desc, "both")
            self._contents[name] = SkillContent(name, body, {"path": path})
    def _watch(self):
        # watchdog Observer 监听 self.root_dir
        # on_modified → skill_registry.invalidate(name)
        pass
    def list(self):
        with self._lock:
            return list(self._summaries.values())
    def get(self, name):
        with self._lock:
            return self._contents.get(name)
    def invalidate(self):
        with self._lock:
            self._summaries.clear()
            self._contents.clear()
        self._scan()

# app/core/skill_providers/lessons.py
class LessonsProvider:
    """自动 lessons 来自 data/lessons.jsonl，每条 lesson 是一个 skill。"""
    def __init__(self, lessons_path: str):
        self.lessons_path = lessons_path
        self._cache = {}
        self._load()
    def _load(self):
        # 读 lessons.jsonl，每条 → SkillSummary/SkillContent
        pass
    def list(self): return list(self._cache.values())
    def get(self, name): return self._cache.get(name)
```

**集成点**（`pipeline.py:_run_step`）：
```python
# 替换现有 block_for 调用
digest, catalog = skill_registry.get_catalog()
system_block = (
    f"\n<available_skills digest={digest}>\n"
    + "\n".join(f"- {s.name}: {s.description} (invocation={s.invocation})" for s in catalog)
    + "\n</available_skills>\n"
)
# 注入 system prompt
# 当用户/模型调用 `skill:foo` 时再 load 正文
def load_skill(name):
    content = skill_registry.get(name)
    if content:
        return content.body
    return None
```

**测试**（`tests/test_skill_registry.py`）：
- 三个 provider 注册同名 skill → 高优先级赢
- 改磁盘 SKILL.md → watchdog 触发 invalidate → 下次 list 看到新内容
- digest 变化检测 → 第二轮不再注入（diff 缓存）
- `skill_registry.get("nonexistent")` → None
- 并发 register + get → lock 安全

**风险**：
- **改造面广**：现有 `skills.py` 全量重写。
- **block_for 兼容**：保留旧 `block_for(name)` 函数，内部走新 `registry.get(name)`，避免 pipeline.py 大改。
- **依赖 watchdog**：已是 Tutti 依赖（`app/core/manager.py` 用过），无新增。

**回退**：保留旧 `BLOCK_BY_NAME` 字典为 fallback。

**依赖**：无（独立模块）。

---

## 3B. Skill 独立 Persona

**目标**：允许每个 skill 自带"角色提示"，作为系统提示词的独立段落注入（而不是硬塞到主提示词里）。

**dsh 参考**：[`packages/subagent/subagent/src/types.ts`](E:/GoOut/_dsh_ref/packages/subagent/subagent/src/types.ts) `persona` 机制，每个 skill/agent 自带角色。

**Tutti 痛点**：当前 skill 内容混入主提示词，难以精细控制。

**改动范围**：
- `SkillContent` 加 `persona: str` 字段
- `app/core/skill_providers/*.py` 解析 SKILL.md 的 `persona` frontmatter
- `pipeline.py` 在主 system 段后追加 `skill.persona` 段

**代码骨架**：
```python
# SkillContent 扩展
@dataclass
class SkillContent:
    name: str
    body: str
    metadata: dict
    persona: str = ""  # 新增

# pipeline.py
for skill_name in task.activated_skills:
    content = skill_registry.get(skill_name)
    if content and content.persona:
        prompt_parts.append(f"\n<!-- persona: {skill_name} -->\n{content.persona}\n")
```

**SKILL.md 格式**：
```markdown
---
name: code-review
description: 评审代码改动
persona: |
  你是一位严格的代码评审员，关注：
  1. 边界条件
  2. 错误处理
  3. 测试覆盖
invocation: both
---

# code-review 技能正文
...
```

**测试**（`tests/test_skill_persona.py`）：
- 解析 SKILL.md with persona → SkillContent.persona 非空
- 无 persona 字段 → SkillContent.persona = ""
- pipeline 注入 persona 段 → 出现在 system prompt 末尾
- persona 过长 (>2KB) → 截断 + 警告

**风险**：低，纯字段扩展。

**回退**：persona 字段缺省即原行为。

**依赖**：3A（注册表 + provider 已就绪）。

---

## Phase 4 实施清单

按依赖顺序：

1. **3A SkillProvider 注册表 + filesystem 热监听**（独立，重构 skills.py）
2. **3B Skill Persona**（依赖 3A）

完成 3A 后保留 `block_for` wrapper 兼容旧调用；3B 是字段扩展，几乎零风险。

---

## Phase 4 风险与回退

- 整体改造面中等，主要是 skills.py 重构 + provider 三件。
- **灰度开关**：`data/orchestration.json: skills_v2 = false` 走旧 `BLOCK_BY_NAME`。
- watchdog 监听失败时（Windows 上偶发），退化到定时轮询（60s）。