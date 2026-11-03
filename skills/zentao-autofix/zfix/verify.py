"""验证：按技术栈配方跑 lint / typecheck / 测试，并把结果压成可留档的结构。

配方（recipes/*.json）只描述「怎么认技术栈、跑什么命令」，
新增栈只需加一个 JSON，不改代码。
"""

from __future__ import annotations

import os
from typing import Any, Dict, List, Optional, Tuple

from . import util
from .config import Config
from .models import Bug

PKG_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
RECIPE_DIR = os.path.join(PKG_ROOT, "recipes")

# 保底配方：配置文件缺失、recipes 目录不可读时仍能工作
_FALLBACK: Dict[str, Dict[str, Any]] = {
    "python": {
        "id": "python-generic",
        "lang": "python",
        "detect": {"files": ["pyproject.toml", "setup.py", "requirements.txt", "manage.py"]},
        "commands": {"lint": "", "typecheck": "", "test": "python -m pytest -q"},
    },
    "node": {
        "id": "node-generic",
        "lang": "node",
        "detect": {"files": ["package.json"]},
        "commands": {"lint": "", "typecheck": "", "test": "npm test --silent"},
    },
    "java": {
        "id": "java-generic",
        "lang": "java",
        "detect": {"files": ["pom.xml", "build.gradle", "build.gradle.kts"]},
        "commands": {"lint": "", "typecheck": "", "test": "mvn -q -DskipITs test"},
    },
    "go": {
        "id": "go-generic",
        "lang": "go",
        "detect": {"files": ["go.mod"]},
        "commands": {"lint": "go vet ./...", "typecheck": "", "test": "go test ./..."},
    },
}


def load_recipes() -> List[Dict[str, Any]]:
    out: List[Dict[str, Any]] = []
    if os.path.isdir(RECIPE_DIR):
        for name in sorted(os.listdir(RECIPE_DIR)):
            if not name.lower().endswith(".json"):
                continue
            data = util.read_json(os.path.join(RECIPE_DIR, name), None)
            if isinstance(data, dict) and data.get("id"):
                out.append(data)
            elif isinstance(data, list):
                out.extend([r for r in data if isinstance(r, dict) and r.get("id")])
    if not out:
        out = list(_FALLBACK.values())
    return out


def detect_recipe(cfg: Config, recipes: Optional[List[Dict[str, Any]]] = None) -> Optional[Dict[str, Any]]:
    recipes = recipes if recipes is not None else load_recipes()
    wanted = str(cfg.get("stack.recipe") or "").strip()
    if wanted:
        for recipe in recipes:
            if recipe.get("id") == wanted:
                return recipe
    lang = str(cfg.get("stack.lang") or "").strip().lower()
    repo = cfg.repo
    best: Tuple[int, Optional[Dict[str, Any]]] = (0, None)
    for recipe in recipes:
        if lang and lang not in ("generic", "") and recipe.get("lang") != lang:
            continue
        detect = recipe.get("detect") or {}
        score = 0
        for marker in detect.get("files") or []:
            if os.path.isfile(os.path.join(repo, marker)):
                score += 10
        for marker in detect.get("globs") or []:
            if _glob_any(repo, marker):
                score += 3
        if score > best[0]:
            best = (score, recipe)
    if best[1]:
        return best[1]
    if lang in _FALLBACK:
        return _FALLBACK[lang]
    for recipe in recipes:
        if recipe.get("id") == "generic":
            return recipe
    return None


def _glob_any(repo: str, pattern: str) -> bool:
    import fnmatch

    depth_marker = pattern
    for _ in range(4):
        for current in (repo,):
            try:
                entries = os.listdir(current)
            except OSError:
                return False
            if any(fnmatch.fnmatch(e, depth_marker.lstrip("*/")) for e in entries):
                return True
        break
    return False


def resolve_commands(cfg: Config, scoped_keywords: Optional[List[str]] = None) -> Dict[str, str]:
    """优先级：config 显式命令 > 配方命令 > 空。"""
    recipe = detect_recipe(cfg) or {}
    base: Dict[str, str] = dict(recipe.get("commands") or {})
    for key in ("lint", "typecheck", "test"):
        override = str(cfg.get("stack." + key) or "").strip()
        if override:
            base[key] = override
    if scoped_keywords:
        scoped = str((recipe.get("commands") or {}).get("test_scoped") or "").strip()
        if scoped and not str(cfg.get("stack.test") or "").strip():
            joined = " or ".join(k for k in scoped_keywords[:4] if k)
            if joined:
                base["test"] = scoped.replace("{keywords}", joined)
    return {k: v for k, v in base.items() if v}


MISSING_TOOL_HINTS = (
    "不是内部或外部命令",
    "not recognized as an internal or external command",
    "command not found",
    "no such file or directory",
    ": not found",
)


def looks_like_missing_tool(result: Dict[str, Any]) -> bool:
    """工具没装 vs 测试真失败，必须分开判。

    Windows cmd 对不存在的命令返回 1（不是 127），只能靠输出特征补判，
    否则「环境没装 pytest」会被当成「代码有 bug」。
    """
    if result.get("exit_code") not in (1, 127, 9009):
        return False
    low = str(result.get("output") or "").lower()
    return any(hint.lower() in low for hint in MISSING_TOOL_HINTS)


def run(cfg: Config, bug: Optional[Bug] = None, only: str = "", scoped: bool = True) -> Dict[str, Any]:
    keywords: List[str] = []
    if scoped and bug is not None:
        from .knowledge import _top_keywords

        keywords = [k for k in _top_keywords(bug.text, 4) if len(k) >= 3]
    commands = resolve_commands(cfg, keywords)
    if only:
        commands = {k: v for k, v in commands.items() if k == only}
    timeout = int(cfg.get("stack.verify_timeout") or 900)
    steps = []
    for name, cmd in commands.items():
        result = util.run_cmd(cmd, cwd=cfg.repo, timeout=timeout)
        # 127 = 命令不存在。工具没装不该判失败，否则每次验收都被环境问题卡住。
        # 但不能静默：标成 skipped 并在摘要里显现，让人知道这一步没真正验过。
        if looks_like_missing_tool(result):
            result["ok"] = True
            result["skipped"] = True
            result["output"] = f"[SKIP] 命令不存在：{cmd}\n" + result["output"]
        steps.append({"name": name, **result})
        if not result["ok"]:
            # 失败就短路：先让 AI 修最便宜的问题，别在红灯上继续跑测试浪费时间
            break
    ok = all(step["ok"] for step in steps) and bool(steps)
    payload = {
        "ok": ok,
        "repo": cfg.repo,
        "ran_at": util.now_iso(),
        "steps": steps,
        "cmd": " && ".join(s["cmd"] for s in steps),
        "summary": _summary(steps),
    }
    if not steps:
        payload["summary"] = "没有可执行的验收命令（请在 [stack] 配置 test/lint）"
    return payload


def _summary(steps: List[Dict[str, Any]]) -> str:
    if not steps:
        return ""
    parts = []
    for step in steps:
        if step.get("skipped"):
            mark = "SKIP"
        else:
            mark = "PASS" if step["ok"] else ("TIMEOUT" if step.get("timed_out") else "FAIL")
        parts.append(f"{step['name']}={mark}({step.get('duration_s', 0)}s)")
    return " · ".join(parts)


def save(cfg: Config, bug_id: str, payload: Dict[str, Any]) -> str:
    path = os.path.join(cfg.runs_dir(), f"{util.slug(bug_id)}-verify.json")
    util.write_json(path, payload)
    return path
