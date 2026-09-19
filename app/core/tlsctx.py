# -*- coding: utf-8 -*-
"""HTTPS 证书校验上下文：补齐 macOS 上 Python 缺失的 CA 源（不关校验）。

现象：用官方 pkg 装 Node 的 Mac 上往往再装 python.org 的 Python，它不读
系统钥匙串，默认验证路径下没有根证书，所有 HTTPS 请求报
CERTIFICATE_VERIFY_FAILED（unable to get local issuer certificate）。

修法是给默认上下文**追加**可用 CA 源，校验语义只增不减：
  1. certifi（装了就用，跨平台最全）；
  2. /etc/ssl/cert.pem（macOS 系统自带 CA 束，Catalina 起就有）。
两个都拿不到时返回默认上下文——报错与旧行为一致，绝不静默关校验。
"""
import ssl
import sys


def cafiles():
    """候选 CA 束路径；坏路径/不存在由 load_verify_locations 抛错后被吞。"""
    out = []
    try:
        import certifi
        out.append(certifi.where())
    except Exception:
        pass
    if sys.platform == "darwin":
        out.append("/etc/ssl/cert.pem")
    return out


def _build():
    ctx = ssl.create_default_context()
    for f in cafiles():
        try:
            ctx.load_verify_locations(cafile=f)
        except Exception:
            pass
    return ctx


_CTX = None


def context():
    """带兜底 CA 源的 ssl.SSLContext（进程内缓存；校验语义与默认一致）。"""
    global _CTX
    if _CTX is None:
        _CTX = _build()
    return _CTX


def humanize(err_text):
    """证书验证失败 → 可行动的人话提示（非证书类错误原样返回）。

    兜底 CA 仍验证失败只剩两类真实原因：本机代理对 HTTPS 做 TLS 拦截
    （其根证书只在系统钥匙串/浏览器里，Python 的静态 CA 束不认），或极端
    环境连 /etc/ssl/cert.pem 都没有。SSL_CERT_FILE 是 OpenSSL 标准逃生门，
    create_default_context() 本就吃它，无需额外代码。"""
    text = str(err_text or "")
    if "CERTIFICATE_VERIFY_FAILED" not in text:
        return text
    return (text + " —— 证书验证仍失败：① 若开着代理/安全软件且开启了 HTTPS 拦截，"
            "关掉拦截，或把其根证书导出后设环境变量 SSL_CERT_FILE 指向它再启动；"
            "② 确认系统存在 /etc/ssl/cert.pem；③ 刚升级过 CodeBee 的话，"
            "旧服务进程可能还在跑——关掉终端窗口重开")
