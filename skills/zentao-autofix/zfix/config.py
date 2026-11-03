"""项目配置：`.zfix/config.toml` 读取、默认值合并、项目注册表。"""

from __future__ import annotations

import copy
import os
from typing import Any, Dict, List, Optional

from . import util

CONFIG_DIR = ".zfix"
CONFIG_FILE = "config.toml"

DEFAULTS: Dict[str, Any] = {
    "project": {
        "name": "",
        "repo": ".",
        "branch_prefix": "fix/",
    },
    "source": {
        "kind": "zentao",
        "url": "",
        "version": "v1",
        "account_env": "ZENTAO_ACCOUNT",
        "password_env": "ZENTAO_PASSWORD",
        "token_env": "ZENTAO_TOKEN",
        "products": [],
        "default_product": "",
        "status": "active",
        "limit": 20,
        "extra_query": "",
        "local_dir": ".zfix/inbox",
    },
    "stack": {
        "lang": "generic",
        "recipe": "",
        "test": "",
        "lint": "",
        "typecheck": "",
        "verify_timeout": 900,
    },
    "fix": {
        "write_scope": [],
        "forbidden": [".zfix/", "package-lock.json", "poetry.lock"],
        "max_files": 12,
        "auto_branch": True,
        "comment_on_zentao": False,
    },
    "guard": {
        "block": False,
        "max_rules": 20,
    },
}


def deep_merge(base: Dict[str, Any], override: Dict[str, Any]) -> Dict[str, Any]:
    out = copy.deepcopy(base)
    for key, val in (override or {}).items():
        if isinstance(val, dict) and isinstance(out.get(key), dict):
            out[key] = deep_merge(out[key], val)
        else:
            out[key] = val
    return out


def find_root(start: str = ".") -> str:
    """从 cwd 向上找带 `.zfix/config.toml` 的目录；找不到就用 cwd。"""
    cur = os.path.abspath(start)
    while True:
        if os.path.isfile(os.path.join(cur, CONFIG_DIR, CONFIG_FILE)):
            return cur
        parent = os.path.dirname(cur)
        if parent == cur:
            return os.path.abspath(start)
        cur = parent


class Config:
    def __init__(self, root: str, data: Dict[str, Any], path: str = ""):
        self.root = os.path.abspath(root)
        self.data = data
        self.path = path

    # ------------------------------------------------------------ 读取
    @classmethod
    def load(cls, start: str = ".", explicit_root: Optional[str] = None) -> "Config":
        root = os.path.abspath(explicit_root) if explicit_root else find_root(start)
        path = os.path.join(root, CONFIG_DIR, CONFIG_FILE)
        raw = util.load_toml(path)
        data = deep_merge(DEFAULTS, raw)
        if not data["project"].get("name"):
            data["project"]["name"] = os.path.basename(root.rstrip("\\/")) or "default"
        return cls(root, data, path)

    @property
    def exists(self) -> bool:
        return bool(self.path) and os.path.isfile(self.path)

    def get(self, dotted: str, default: Any = None) -> Any:
        cur: Any = self.data
        for part in dotted.split("."):
            if not isinstance(cur, dict) or part not in cur:
                return default
            cur = cur[part]
        return cur

    # ------------------------------------------------------------ 路径
    @property
    def name(self) -> str:
        return str(self.get("project.name") or "default")

    @property
    def repo(self) -> str:
        return os.path.abspath(os.path.join(self.root, str(self.get("project.repo") or ".")))

    def state_dir(self, *parts: str) -> str:
        return os.path.join(self.root, CONFIG_DIR, *parts)

    def bugs_dir(self) -> str:
        return util.ensure_dir(self.state_dir("bugs"))

    def context_dir(self) -> str:
        return util.ensure_dir(self.state_dir("context"))

    def runs_dir(self) -> str:
        return util.ensure_dir(self.state_dir("runs"))

    def bug_path(self, bug_id: str) -> str:
        return os.path.join(self.bugs_dir(), f"{util.slug(str(bug_id))}.json")

    # ------------------------------------------------------------ 校验
    def source_ready(self) -> List[str]:
        kind = str(self.get("source.kind") or "zentao")
        problems: List[str] = []
        if kind == "zentao":
            if not self.get("source.url"):
                problems.append("未配置 source.url（禅道地址）")
            if self.get("source.version") == "v1":
                token_env = str(self.get("source.token_env") or "ZENTAO_TOKEN")
                if not os.environ.get(token_env):
                    if not os.environ.get(str(self.get("source.account_env") or "")):
                        problems.append(
                            f"缺少认证：环境变量 {token_env} 或 "
                            f"{self.get('source.account_env')} + {self.get('source.password_env')}"
                        )
        elif kind == "local":
            if not os.path.isdir(os.path.join(self.root, str(self.get("source.local_dir")))):
                problems.append(f"本地收件目录不存在：{self.get('source.local_dir')}")
        else:
            problems.append(f"未知的缺陷源类型：{kind}")
        return problems


# ---------------------------------------------------------------- 注册表


def registry_path() -> str:
    return os.path.join(os.path.expanduser("~"), ".zfix", "projects.json")


def load_registry() -> Dict[str, str]:
    return util.read_json(registry_path(), {}) or {}


def register(root: str, name: str) -> None:
    data = load_registry()
    data[name] = os.path.abspath(root)
    util.write_json(registry_path(), data)


def resolve_project(name: str) -> Optional[str]:
    return load_registry().get(name)
