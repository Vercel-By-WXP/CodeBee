"""禅道旧版（<16.x，无 REST v1）适配器：尽力而为。

策略：cookie 登录 → 依次尝试几种已知的 JSON 出口。任何一步拿不到结构化数据就
明确报错并给出替代方案（导出到本地目录走 local 源），绝不静默返回空列表——
空列表会被上层误判成「禅道没有 bug」。
"""

from __future__ import annotations

import http.cookiejar
import json
import os
import re
import urllib.error
import urllib.parse
import urllib.request
from typing import Any, Dict, List, Optional

from ..models import Bug
from .base import BugSource, SourceError, register
from .zentao_v1 import _extract_stack, _s


@register
class ZentaoLegacySource(BugSource):
    kind = "zentao_legacy"

    def __init__(self, cfg):  # type: ignore[no-untyped-def]
        super().__init__(cfg)
        self._opener: Optional[urllib.request.OpenerDirector] = None

    def _env(self, dotted: str, fallback: str) -> str:
        return os.environ.get(str(self.cfg.get(dotted) or fallback), "")

    def opener(self) -> urllib.request.OpenerDirector:
        if self._opener is not None:
            return self._opener
        jar = http.cookiejar.CookieJar()
        self._opener = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(jar))
        account = self._env("source.account_env", "ZENTAO_ACCOUNT")
        password = self._env("source.password_env", "ZENTAO_PASSWORD")
        if not account or not password:
            raise SourceError(
                "禅道旧版必须提供账号密码：设置 "
                f"{self.cfg.get('source.account_env')} / {self.cfg.get('source.password_env')}"
            )
        base = str(self.cfg.get("source.url") or "").rstrip("/")
        body = urllib.parse.urlencode({"account": account, "password": password}).encode("utf-8")
        req = urllib.request.Request(
            f"{base}/index.php?m=user&f=login",
            data=body,
            headers={"Content-Type": "application/x-www-form-urlencoded", "User-Agent": "zfix/0.1"},
        )
        try:
            with self._opener.open(req, timeout=30) as resp:
                html = resp.read().decode("utf-8", errors="replace")
        except urllib.error.URLError as exc:
            raise SourceError(f"登录失败：{exc}") from exc
        if "登录" in html and "password" in html.lower() and len(html) < 4000:
            raise SourceError("登录被拒：账号或密码不正确（禅道返回了登录页）")
        return self._opener

    def health(self) -> Dict[str, Any]:
        info: Dict[str, Any] = {"kind": "zentao-legacy", "url": self.cfg.get("source.url")}
        try:
            self.opener()
            info.update({"ok": True, "auth": "ok", "note": "旧版接口为尽力而为模式"})
        except SourceError as exc:
            info.update({"ok": False, "error": str(exc)})
        return info

    def fetch(self, status: str = "", product: str = "", limit: int = 0, page: int = 1, **kwargs: Any) -> List[Bug]:
        opener = self.opener()
        base = str(self.cfg.get("source.url") or "").rstrip("/")
        product = str(product or self.cfg.get("source.default_product") or "1")
        status = str(status or self.cfg.get("source.status") or "active")
        limit = int(limit or self.cfg.get("source.limit") or 20)
        candidates = [
            f"{base}/index.php?m=bug&f=browse&product={product}&status={status}&t=json&limit={limit}",
            f"{base}/bug-browse-{product}-0-{status}-0-0-0-0-0-{limit}-1-0.json",
            f"{base}/api.php?m=bug&f=browse&product={product}&status={status}&limit={limit}&t=json",
        ]
        errors: List[str] = []
        for url in candidates:
            try:
                with opener.open(urllib.request.Request(url, headers={"Accept": "application/json"}), timeout=30) as resp:
                    raw = resp.read().decode("utf-8", errors="replace")
            except urllib.error.URLError as exc:
                errors.append(f"{url} · {exc}")
                continue
            rows = _parse_rows(raw)
            if rows:
                return [self.normalize(r) for r in rows]
            errors.append(f"{url} · 未解析出 bug 列表（{len(raw)} 字节）")
        raise SourceError(
            "禅道旧版未提供可解析的 JSON 出口。建议二选一：\n"
            "  1) 升级到 16.x+ 并把 source.version 设为 v1；\n"
            "  2) 在禅道里导出 bug 列表到 .zfix/inbox/，把 source.kind 设为 local。\n"
            "尝试过：" + " | ".join(errors[:3])
        )

    def normalize(self, row: Dict[str, Any]) -> Bug:
        bug_id = _s(row.get("id") or row.get("bugID"))
        base = str(self.cfg.get("source.url") or "").rstrip("/")
        steps = _s(row.get("steps")) or _s(row.get("detail"))
        return Bug(
            id=bug_id,
            title=_s(row.get("title")),
            project=str(self.cfg.name),
            source="zentao-legacy",
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
            stack=_extract_stack(steps),
        )


def _parse_rows(raw: str) -> List[Dict[str, Any]]:
    """容忍 HTML 包裹 / JSONP / 纯 JSON 三种形态。"""
    text = raw.strip()
    if not text:
        return []
    if text[0] in "[{":
        try:
            data = json.loads(text)
        except json.JSONDecodeError:
            data = None
        rows = _rows_of(data)
        if rows:
            return rows
    match = re.search(r"(\[\s*\{.*\}\s*\])", text, re.S)
    if match:
        try:
            return _rows_of(json.loads(match.group(1)))
        except json.JSONDecodeError:
            pass
    # 最后兜底：从 HTML 表格里抠 bugID + 标题
    pairs = re.findall(r"bugID=(\d+)[^>]*>\s*(?:<[^>]+>\s*)*([^<]{4,80})<", text)
    return [{"id": pid, "title": title.strip()} for pid, title in pairs[:100]]


def _rows_of(data: Any) -> List[Dict[str, Any]]:
    if isinstance(data, list):
        return [x for x in data if isinstance(x, dict)]
    if not isinstance(data, dict):
        return []
    for key in ("bugs", "data", "list", "bugList", "records"):
        val = data.get(key)
        if isinstance(val, list):
            return [x for x in val if isinstance(x, dict)]
        if isinstance(val, dict):
            nested = _rows_of(val)
            if nested:
                return nested
    return []
