"""Bridge the CodeBee browser workspace to the publisher's existing CDP session.

The workspace can embed ordinary sites in an iframe, but publisher sites cannot
be controlled from that cross-origin frame. This bridge renders the existing
publisher tab as screenshots and forwards user input through CDP, so manual
work in the workspace and automated publishing share one profile and tab.
"""
from __future__ import annotations

from urllib.parse import urlsplit

from . import manager
from .browser import BrowserError


class BridgeError(BrowserError):
    """A safe, user-facing bridge error with its HTTP response status."""

    def __init__(self, message, status=400):
        super().__init__(message)
        self.status = status


_KEYS = {
    "Enter": ("Enter", "Enter", 13),
    "Tab": ("Tab", "Tab", 9),
    "Escape": ("Escape", "Escape", 27),
    "Backspace": ("Backspace", "Backspace", 8),
    "Delete": ("Delete", "Delete", 46),
    "ArrowLeft": ("ArrowLeft", "ArrowLeft", 37),
    "ArrowUp": ("ArrowUp", "ArrowUp", 38),
    "ArrowRight": ("ArrowRight", "ArrowRight", 39),
    "ArrowDown": ("ArrowDown", "ArrowDown", 40),
    "Home": ("Home", "Home", 36),
    "End": ("End", "End", 35),
    "PageUp": ("PageUp", "PageUp", 33),
    "PageDown": ("PageDown", "PageDown", 34),
    " ": (" ", "Space", 32),
}


def platform_for_url(url):
    """Return a publisher id for an HTTPS publisher URL, otherwise None."""
    try:
        parsed = urlsplit(str(url or ""))
        if parsed.scheme.lower() != "https" or parsed.username or parsed.password:
            return None
        if parsed.port not in (None, 443):
            return None
        host = (parsed.hostname or "").lower().rstrip(".")
    except (TypeError, ValueError):
        return None
    for platform, module in manager.PLATFORMS.items():
        home_host = (urlsplit(module.CONFIG["home"]).hostname or "").lower()
        root = home_host.split(".", 1)[-1] if home_host.startswith("www.") else home_host
        if host == root or host.endswith("." + root):
            return platform
    return None


def normalize_action(data):
    """Validate and bound an input event before it reaches the browser."""
    data = data if isinstance(data, dict) else {}
    action = str(data.get("action") or "")
    if action == "click":
        try:
            x, y = int(data.get("x")), int(data.get("y"))
        except (TypeError, ValueError):
            raise BridgeError("点击坐标无效")
        if not (0 <= x <= 4096 and 0 <= y <= 4096):
            raise BridgeError("点击位置超出浏览器窗口")
        return {"action": action, "x": x, "y": y}
    if action == "scroll":
        try:
            dx, dy = int(data.get("deltaX") or 0), int(data.get("deltaY") or 0)
        except (TypeError, ValueError):
            raise BridgeError("滚动距离无效")
        if abs(dx) > 2000 or abs(dy) > 2000:
            raise BridgeError("滚动距离超出范围")
        return {"action": action, "deltaX": dx, "deltaY": dy}
    if action == "text":
        text = str(data.get("text") or "")
        if len(text) > 4000:
            raise BridgeError("单次输入最多 4000 字")
        return {"action": action, "text": text}
    if action == "key":
        key = str(data.get("key") or "")
        if len(key) == 1 or key in _KEYS:
            modifiers = 0
            for field, flag in (("alt", 1), ("ctrl", 2), ("meta", 4), ("shift", 8)):
                if bool(data.get(field)):
                    modifiers |= flag
            return {"action": action, "key": key, "modifiers": modifiers}
        raise BridgeError("不支持此键盘按键")
    raise BridgeError("不支持的浏览器操作")


def _ensure_session(platform):
    if platform not in manager.PLATFORMS:
        raise BridgeError("未知发布平台")
    if manager._st(platform).get("status") == "busy":
        raise BridgeError("发布操作进行中，完成后才能操作浏览器", 409)
    status = manager._st(platform).get("status")
    if status not in ("connected", "waiting_login"):
        ok, error = manager.connect(platform)
        if not ok:
            raise BridgeError(error or "发布浏览器连接失败", 503)
    try:
        return manager._ensure_browser(platform)
    except BrowserError as exc:
        raise BridgeError(str(exc), 503)


def _check_page_platform(platform, page):
    """Keep a redirected or user-navigated tab inside its publisher domain."""
    try:
        current_url = str(page.url() or "")
    except Exception:
        current_url = ""
    if platform_for_url(current_url) != platform:
        raise BridgeError("浏览器页面已离开当前发布平台，请重新打开作者后台", 403)
    return current_url


def navigate(platform, url):
    platform = str(platform or "")
    if platform_for_url(url) != platform:
        raise BridgeError("只能在此工作区打开番茄或七猫作者后台")
    browser = _ensure_session(platform)
    with manager.LOCK:
        if manager._st(platform).get("status") == "busy":
            raise BridgeError("发布操作进行中，完成后才能操作浏览器", 409)
        page = browser.first_page(create=True)
        page.navigate(str(url), timeout=30.0)
        return snapshot(platform, page=page)


def snapshot(platform, page=None):
    platform = str(platform or "")
    if platform not in manager.PLATFORMS:
        raise BridgeError("未知发布平台")
    with manager.LOCK:
        if manager._st(platform).get("status") == "busy":
            raise BridgeError("发布操作进行中，工作区暂不可操作", 409)
        browser = _ensure_session(platform) if page is None else None
        page = page or browser.first_page(create=False)
        if page is None:
            raise BridgeError("发布浏览器还没有页面，请重新打开平台", 503)
        current_url = _check_page_platform(platform, page)
        result = page.send("Page.captureScreenshot", {
            "format": "png", "fromSurface": True,
        }, timeout=12.0)
        return {"ok": True, "platform": platform,
                "url": current_url, "image": result.get("data") or ""}


def input_event(platform, data):
    platform = str(platform or "")
    if platform not in manager.PLATFORMS:
        raise BridgeError("未知发布平台")
    event = normalize_action(data)
    browser = _ensure_session(platform)
    with manager.LOCK:
        if manager._st(platform).get("status") == "busy":
            raise BridgeError("发布操作进行中，工作区暂不可操作", 409)
        page = browser.first_page(create=False)
        if page is None:
            raise BridgeError("发布浏览器还没有页面，请重新打开平台", 503)
        _check_page_platform(platform, page)
        action = event["action"]
        if action == "click":
            metrics = page.send("Page.getLayoutMetrics", timeout=5.0)
            viewport = metrics.get("cssLayoutViewport") or metrics.get("layoutViewport") or {}
            width = int(viewport.get("clientWidth") or 1366)
            height = int(viewport.get("clientHeight") or 900)
            if event["x"] >= width or event["y"] >= height:
                raise BridgeError("点击位置超出浏览器窗口")
            for kind in ("mousePressed", "mouseReleased"):
                page.send("Input.dispatchMouseEvent", {
                    "type": kind, "x": event["x"], "y": event["y"],
                    "button": "left", "clickCount": 1,
                }, timeout=5.0)
        elif action == "scroll":
            page.send("Input.dispatchMouseEvent", {
                "type": "mouseWheel", "x": 680, "y": 450,
                "deltaX": event["deltaX"], "deltaY": event["deltaY"],
            }, timeout=5.0)
        elif action == "text":
            page.send("Input.insertText", {"text": event["text"]}, timeout=10.0)
        elif action == "key":
            key = event["key"]
            if len(key) == 1:
                page.send("Input.dispatchKeyEvent", {
                    "type": "keyDown", "key": key, "text": key,
                    "modifiers": event["modifiers"],
                }, timeout=5.0)
                page.send("Input.dispatchKeyEvent", {
                    "type": "keyUp", "key": key,
                    "modifiers": event["modifiers"],
                }, timeout=5.0)
            else:
                key_name, code, virtual_key = _KEYS[key]
                for kind in ("keyDown", "keyUp"):
                    page.send("Input.dispatchKeyEvent", {
                        "type": kind, "key": key_name, "code": code,
                        "windowsVirtualKeyCode": virtual_key,
                        "modifiers": event["modifiers"],
                    }, timeout=5.0)
        return snapshot(platform, page=page)
