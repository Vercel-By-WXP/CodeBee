"""缺陷源适配器：抽象基类 + 注册表。

新增来源（Jira / 云效 / 飞书表格 / 禅道旧版）只需实现 BugSource 三件事：
fetch() 拉取、normalize() 归一化成 Bug、health() 探活。
上层流水线对来源一无所知。
"""

from __future__ import annotations

import json
import ssl
import urllib.error
import urllib.parse
import urllib.request
from typing import Any, Dict, List, Optional

from ..config import Config
from ..models import Bug


class SourceError(RuntimeError):
    pass


class BugSource:
    """缺陷源协议。"""

    kind = "base"

    def __init__(self, cfg: Config):
        self.cfg = cfg

    def health(self) -> Dict[str, Any]:
        raise NotImplementedError

    def fetch(self, **kwargs: Any) -> List[Bug]:
        raise NotImplementedError

    def comment(self, bug_id: str, text: str) -> bool:
        return False

    # -------------------------------------------------------- HTTP 帮手
    def _request(
        self,
        method: str,
        path: str,
        body: Optional[Dict[str, Any]] = None,
        headers: Optional[Dict[str, str]] = None,
        timeout: int = 30,
    ) -> Dict[str, Any]:
        base = str(self.cfg.get("source.url") or "").rstrip("/")
        url = base + path
        data = None
        hdrs = {
            "Accept": "application/json",
            "User-Agent": "zfix/0.1 (+zentao-autofix skill)",
        }
        if body is not None:
            data = json.dumps(body, ensure_ascii=False).encode("utf-8")
            hdrs["Content-Type"] = "application/json"
        hdrs.update(headers or {})
        req = urllib.request.Request(url, data=data, headers=hdrs, method=method.upper())
        ctx = None
        if self.cfg.get("source.insecure"):
            ctx = ssl.create_default_context()
            ctx.check_hostname = False
            ctx.verify_mode = ssl.CERT_NONE
        try:
            with urllib.request.urlopen(req, timeout=timeout, context=ctx) as resp:
                raw = resp.read().decode("utf-8", errors="replace")
                status = resp.status
        except urllib.error.HTTPError as exc:
            raw = exc.read().decode("utf-8", errors="replace")
            raise SourceError(f"HTTP {exc.code} · {url} · {raw[:300]}") from exc
        except urllib.error.URLError as exc:
            raise SourceError(f"无法连接 · {url} · {exc.reason}") from exc
        try:
            payload = json.loads(raw)
        except json.JSONDecodeError:
            raise SourceError(f"返回非 JSON（{len(raw)} 字节）：{raw[:200]}")
        if not isinstance(payload, dict):
            payload = {"data": payload}
        payload.setdefault("_status", status)
        return payload


_REGISTRY: Dict[str, type] = {}


def register(cls: type) -> type:
    _REGISTRY[getattr(cls, "kind", cls.__name__).lower()] = cls
    return cls


def create(cfg: Config) -> BugSource:
    from . import local_dir, zentao_legacy, zentao_v1  # noqa: F401  注册副作用

    kind = str(cfg.get("source.kind") or "zentao").lower()
    if kind in ("zentao", "zentao_v1", "v1"):
        version = str(cfg.get("source.version") or "v1").lower()
        if version in ("legacy", "v0", "old"):
            return zentao_legacy.ZentaoLegacySource(cfg)
        return zentao_v1.ZentaoV1Source(cfg)
    cls = _REGISTRY.get(kind)
    if not cls:
        raise SourceError(f"未注册的缺陷源：{kind}（可用：{', '.join(sorted(_REGISTRY))}）")
    return cls(cfg)
