# -*- coding: utf-8 -*-
"""TLS 上下文与出站请求防线测试：

- tlsctx 校验语义不降级（CERT_REQUIRED + check_hostname，只增不减）；
- SSRF 拒绝分支真实生效——修复前 _validate_host 返回 (None, msg) 元组、
  调用方判 `is None` 永假，私网/环回校验是死代码；
- anthropic 形网关（zcode-plan 等）GET /models 404 的人话提示；
- ZCode 导入未带出密钥的条目时在 note 中明示，不再静默。
全部本地/离线，不访问任何真实外网地址。
"""
from __future__ import annotations

import http.server
import json
import ssl
import threading

from base import BaseTest


class TestTlsCtx(BaseTest):
    def test_context_keeps_verification(self):
        """上下文必须保持完整校验语义，兜底 CA 只能追加不能放宽。"""
        from app.core import tlsctx
        ctx = tlsctx.context()
        self.assertIsInstance(ctx, ssl.SSLContext)
        self.assertEqual(ctx.verify_mode, ssl.CERT_REQUIRED)
        self.assertTrue(ctx.check_hostname)

    def test_context_cached(self):
        from app.core import tlsctx
        self.assertIs(tlsctx.context(), tlsctx.context())

    def test_fetch_models_rejects_loopback(self):
        from app.core import modelhub
        names, err = modelhub._fetch_models_http(
            "http://127.0.0.1:9/v1/models", "k", "openai", allow_private=False)
        self.assertIsNone(names)
        self.assertIn("拒绝访问", err)

    def test_post_json_rejects_loopback(self):
        from app.core import modelhub
        status, obj, err = modelhub._post_json_http(
            "http://127.0.0.1:9/v1/messages", {}, {}, allow_private=False)
        self.assertEqual(status, 0)
        self.assertIsNone(obj)
        self.assertIn("拒绝访问", err)

    def test_post_sse_rejects_loopback(self):
        from app.core import modelhub
        status, text, usage, err = modelhub._post_sse_http(
            "http://127.0.0.1:9/v1/messages", {}, {}, False, 5, "anthropic",
            lambda *_: None)
        self.assertEqual(status, 0)
        self.assertIn("拒绝访问", err)

    def test_models_404_friendly_message(self):
        """本地 404 服务器验证：无 /models 接口的网关给出人话而非裸 URLError。"""
        from app.core import modelhub

        class _H404(http.server.BaseHTTPRequestHandler):
            def do_GET(self):
                self.send_response(404)
                self.end_headers()

            def log_message(self, *a):
                pass

        srv = http.server.ThreadingHTTPServer(("127.0.0.1", 0), _H404)
        threading.Thread(target=srv.serve_forever, daemon=True).start()
        try:
            names, err = modelhub._fetch_models_http(
                "http://127.0.0.1:%d/v1" % srv.server_address[1], "k",
                "anthropic", allow_private=True)
            self.assertIsNone(names)
            self.assertIn("无模型列表接口", err)
        finally:
            srv.shutdown()
            srv.server_close()

    def test_zcode_import_notes_missing_keys(self):
        """builtin Plan 条目（密钥不入 config.json）导入时 note 明示。"""
        from app.core import modelhub
        cfg = self.data_dir / "zcode-config.json"
        cfg.write_text(json.dumps({
            "model": "GLM-5.3-Flash",
            "provider": {
                "p1": {"kind": "anthropic",
                       "options": {"baseURL": "https://zcode.z.ai/api/v1/zcode-plan/anthropic"},
                       "models": {"GLM-5.3-Flash": {}}},
                "p2": {"kind": "anthropic",
                       "options": {"baseURL": "https://a.test/api/anthropic",
                                   "apiKey": "sk-x"},
                       "models": {"m1": {}}},
                "p3": {"kind": "anthropic", "options": {}},
            }}), encoding="utf-8")
        old = modelhub.ZCODE_CONFIG
        modelhub.ZCODE_CONFIG = str(cfg)
        try:
            out = modelhub._src_zcode()
        finally:
            modelhub.ZCODE_CONFIG = old
        self.assertTrue(out["found"])
        self.assertEqual(len(out["providers"]), 2)   # p3 缺 baseURL 被跳过
        self.assertIn("未带出密钥", out["note"])
        self.assertIn("p1", out["note"])             # 无名条目回落 pid 展示
        nokey = [p for p in out["providers"] if not p.get("api_key")]
        self.assertEqual([p["source_id"] for p in nokey], ["zcode:p1"])
