# -*- coding: utf-8 -*-
"""Shared upstream identity normalization for routing and retry guards."""
from __future__ import annotations

import ipaddress
from urllib.parse import urlsplit


def normalize_upstream(value, default_scheme="https"):
    """Return a stable host[:port] identity, without URL path or credentials.

    Host names are case-insensitive and a trailing dot is equivalent to the
    non-dotted form. Default HTTP(S) ports are omitted so equivalent URLs share
    one failure/permission bucket. Non-default ports remain distinct.
    """
    raw = str(value or "").strip()
    if not raw:
        return ""
    candidate = raw if "://" in raw else "%s://%s" % (default_scheme, raw)
    try:
        parsed = urlsplit(candidate)
        host = parsed.hostname
        if not host:
            return ""
        host = host.rstrip(".").lower()
        ip_host = None
        try:
            ip_host = ipaddress.ip_address(host)
            host = ip_host.compressed.lower()
        except ValueError:
            pass
        port = parsed.port
        scheme = (parsed.scheme or default_scheme).lower()
        if port is not None and not ((scheme == "https" and port == 443)
                                     or (scheme == "http" and port == 80)):
            if getattr(ip_host, "version", None) == 6:
                return "[%s]:%d" % (host, port)
            return "%s:%d" % (host, port)
        return host
    except (TypeError, ValueError):
        return ""
