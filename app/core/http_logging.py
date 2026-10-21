"""Safe request-target formatting for server diagnostics."""
from __future__ import annotations

from urllib.parse import urlsplit


def safe_request_target(request_target, preview_prefix="/preview"):
    """Return a bounded route identifier without query or path credentials.

    Remote API tokens are accepted in query parameters for compatibility, and
    preview links carry a short-lived token in a path segment. Neither form
    belongs in server logs. Preview resource names are also omitted because
    they can contain private workspace filenames.
    """
    try:
        path = urlsplit(str(request_target or "")).path or "/"
    except ValueError:
        return "<invalid request target>"
    prefix = str(preview_prefix or "/preview").rstrip("/") or "/preview"
    if path == prefix or path.startswith(prefix + "/"):
        return prefix + "/<redacted>"
    return path[:512]


def log_request_exception(logger, method, request_target, exc=None):
    """Log a request failure without exposing URL credentials or exception text."""
    error_type = type(exc).__name__ if exc is not None else "Exception"
    # Exception messages and tracebacks can contain request bodies, credentials,
    # or user file paths. Keep only the stable type alongside the safe route.
    logger.error("%s %s 处理异常（%s）", str(method or "HTTP").upper(),
                 safe_request_target(request_target), error_type)
