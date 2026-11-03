"""禅道 REST API v1 适配器（禅道 16.x+ 内置 API：/api.php/v1/...）。"""

from __future__ import annotations

import os
import urllib.parse
from typing import Any, Dict, List, Optional

from ..models import Bug
from .base import BugSource, SourceError, register


def _s(val: Any, default: str = "") -> str:
    if val is None:
        return default
    if isinstance(val, dict):
        # 禅道有时返回 {"id":1,"name":"xxx"}
        for key in ("realname", "name", "account", "title"):
            if key in val:
                return str(val[key])
        return default
    if isinstance(val, (list, tuple)):
        return ",".join(str(x) for x in val)
    return str(val)


@register
class ZentaoV1Source(BugSource):
    kind = "zentao_v1"

    def __init__(self, cfg):  # type: ignore[no-untyped-def]
        super().__init__(cfg)
        self._token: Optional[str] = None

    # ------------------------------------------------------------ 认证
    def _env(self, dotted: str, fallback: str) -> str:
        name = str(self.cfg.get(dotted) or fallback)
        return os.environ.get(name, "")

    def token(self) -> str:
        if self._token:
            return self._token
        env_name = str(self.cfg.get("source.token_env") or "ZENTAO_TOKEN")
        cached = os.environ.get(env_name, "").strip()
        if cached:
            self._token = cached
            return cached
        account = self._env("source.account_env", "ZENTAO_ACCOUNT")
        password = self._env("source.password_env", "ZENTAO_PASSWORD")
        if not account or not password:
            raise SourceError(
                f"缺少禅道凭据：请设置环境变量 {env_name}（Token），或 "
                f"{self.cfg.get('source.account_env')} / {self.cfg.get('source.password_env')}"
            )
        payload = self._request("POST", "/api.php/v1/tokens", {"account": account, "password": password})
        token = str(payload.get("token") or "").strip()
        if not token:
            raise SourceError("禅道返回中未包含 token，账号或密码可能不正确")
        self._token = token
        return token

    # ------------------------------------------------------------ 探活
    def health(self) -> Dict[str, Any]:
        info: Dict[str, Any] = {"kind": "zentao-v1", "url": self.cfg.get("source.url")}
        try:
            token = self.token()
            info["auth"] = "ok"
            info["token_prefix"] = token[:6] + "…"
            payload = self._request("GET", "/api.php/v1/products?limit=1", headers={"Token": token})
            info["api"] = "ok"
            info["products"] = len(payload.get("products") or [])
            info["ok"] = True
        except SourceError as exc:
            info["ok"] = False
            info["error"] = str(exc)
        return info

    # ------------------------------------------------------------ 拉取
    def fetch(
        self,
        status: str = "",
        product: str = "",
        limit: int = 0,
        page: int = 1,
        **kwargs: Any,
    ) -> List[Bug]:
        token = self.token()
        product = str(product or self.cfg.get("source.default_product") or "")
        status = str(status or self.cfg.get("source.status") or "active")
        limit = int(limit or self.cfg.get("source.limit") or 20)
        query: Dict[str, str] = {"limit": str(limit), "page": str(page)}
        if status and status != "all":
            query["status"] = status
        if product:
            query["product"] = product
        extra = str(self.cfg.get("source.extra_query") or "").strip()
        if extra:
            for chunk in extra.split("&"):
                if "=" in chunk:
                    k, v = chunk.split("=", 1)
                    query[k] = v
        path = "/api.php/v1/bugs?" + urllib.parse.urlencode(query)
        payload = self._request("GET", path, headers={"Token": token})
        rows = payload.get("bugs") or payload.get("data") or []
        if not isinstance(rows, list):
            raise SourceError("禅道 bugs 字段格式异常，预期数组")
        return [self.normalize(row) for row in rows if isinstance(row, dict)]

    def detail(self, bug_id: str) -> Bug:
        token = self.token()
        payload = self._request("GET", f"/api.php/v1/bugs/{bug_id}", headers={"Token": token})
        return self.normalize(payload)

    # ------------------------------------------------------------ 归一化
    def normalize(self, row: Dict[str, Any]) -> Bug:
        bug_id = _s(row.get("id"))
        base = str(self.cfg.get("source.url") or "").rstrip("/")
        steps = _s(row.get("steps")) or _s(row.get("detail")) or _s(row.get("comment"))
        return Bug(
            id=bug_id,
            title=_s(row.get("title")) or f"Bug {bug_id}",
            project=str(self.cfg.name),
            source="zentao",
            status=_s(row.get("status")),
            severity=_s(row.get("severity"), "3"),
            pri=_s(row.get("pri"), "3"),
            product=_s(row.get("product")),
            module=_s(row.get("module")),
            type=_s(row.get("type")),
            steps=steps,
            opened_by=_s(row.get("openedBy")),
            assigned_to=_s(row.get("assignedTo")),
            created=_s(row.get("openedDate")),
            updated=_s(row.get("lastEditedDate")),
            url=f"{base}/index.php?m=bug&f=view&bugID={bug_id}",
            resolution=_s(row.get("resolution")),
            stack=_s(row.get("stack")) or _extract_stack(steps),
            extra={"source_url": base},
        )


def _extract_stack(text: str) -> str:
    """从描述里捞堆栈：连续以 at/File/ 行号 开头的行。"""
    if not text:
        return ""
    lines = text.splitlines()
    picked: List[str] = []
    for line in lines:
        stripped = line.strip()
        if stripped.startswith(("at ", "File \"", "Traceback", "Caused by", "java.", "com.", "org.")) or (
            ":" in stripped and "line" in stripped.lower()
        ):
            if stripped in picked:  # 禅道正文里常把同一帧复制两遍
                continue
            picked.append(stripped)
    return "\n".join(picked[:60])
