"""zfix · 禅道 Bug 自动修复与防复发引擎。

设计原则：所有确定性逻辑住在 Python 标准库里，与 AI 编程平台完全解耦。
各平台（Codex / Claude Code / CodeBee / OpenCode）只提供薄壳提示词，
调用同一个 `zfix` CLI。换平台不换逻辑。
"""

__version__ = "0.1.0"
__all__ = ["__version__"]
