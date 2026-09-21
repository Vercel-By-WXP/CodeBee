# -*- coding: utf-8 -*-
"""wire 协议适配测试：探测、wire_caps 存取、跨协议解析注入、launch_pick、失效作废。

「模型接入」页用 1 token 最小对话实测网关另一条 wire；通过的记 wire_caps，
绑定解析据此放宽协议闸门。探测失败保持跳过语义——宁可 ⚠ 也不错注入。
所有密钥均为运行时构造的假值，网关是本机测试假服务，无真实凭据外发。
"""
from __future__ import annotations

import json
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer

from base import BaseTest

FAKE_KEY = "sk-test-" + "abcdefgh" * 3


class _FakeGateway(BaseHTTPRequestHandler):
    """本地假网关：messages / chat/completions / responses 一律 200，其余 404；
    GET /models 返回一个模型列表（供「获取模型列表」路径测）。"""

    def log_message(self, *a):  # 静音请求日志
        pass

    def _json(self, code, payload):
        body = json.dumps(payload).encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        if self.path.endswith("/models"):
            self._json(200, {"data": [{"id": "gpt-x"}, {"id": "claude-y"}]})
        else:
            self._json(404, {"error": "no route"})

    def do_POST(self):
        n = int(self.headers.get("Content-Length") or 0)
        self.rfile.read(n)
        if self.path.endswith(("/messages", "/chat/completions", "/responses")):
            self._json(200, {"content": [{"type": "text", "text": "ok"}], "choices": []})
        else:
            self._json(404, {"error": "no route"})


def _serve():
    srv = HTTPServer(("127.0.0.1", 0), _FakeGateway)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    return srv


class TestWireCaps(BaseTest):
    def test_probe_and_resolve(self):
        from app.core import modelhub
        modelhub._FILE = self.data_dir / "models.json"
        srv = _serve()
        try:
            base = "http://127.0.0.1:%d" % srv.server_address[1]

            # 1) openai 原生供应商 → 探测出 anthropic 面（/v1 → 根 + /v1/messages）
            modelhub.upsert_provider({"name": "网关A", "protocol": "openai",
                                      "base_url": base + "/v1", "api_key": FAKE_KEY,
                                      "model": "m1"})
            pa = modelhub.providers()[0]["id"]
            caps, note = modelhub.probe_wire_caps(pa)
            self.assertEqual(note, "")
            self.assertEqual(caps["anthropic"]["base"], base)
            self.assertEqual(caps["anthropic"]["wire_api"], "messages")
            # 结果落盘
            self.assertEqual(modelhub.providers()[0]["wire_caps"]["anthropic"]["base"], base)

            # 2) claude-code 链引用 openai 供应商：适配后参与解析，注入 ANTHROPIC_*
            modelhub.set_binding("claude-code", chain=[{"provider_id": pa, "model": "m1"}])
            r = modelhub.resolve_binding("claude-code")
            self.assertEqual(r["env"]["ANTHROPIC_BASE_URL"], base)
            self.assertEqual(r["env"]["ANTHROPIC_AUTH_TOKEN"], FAKE_KEY)
            self.assertEqual(r["env"]["ANTHROPIC_MODEL"], "m1")
            self.assertNotIn("codex_provider", r["call_chain"][0])

            # 3) codex 链不受影响：原生 openai 仍走 codex_provider（原生端点）
            modelhub.set_binding("codex-cli", chain=[{"provider_id": pa, "model": "m1"}])
            rc = modelhub.resolve_binding("codex-cli")
            self.assertEqual(rc["codex_provider"]["base_url"], base + "/v1")

            # 4) launch_pick：anthropic 协议命中适配副本（base 覆写，协议改名）
            pick, err = modelhub.launch_pick("claude-code", ("anthropic",))
            self.assertIsNone(err)
            self.assertEqual(pick["provider"]["protocol"], "anthropic")
            self.assertEqual(pick["provider"]["base_url"], base)
            # launch_pick：openai 协议原生命中，供应商原样返回
            pick2, err2 = modelhub.launch_pick("codex-cli", ("openai",))
            self.assertIsNone(err2)
            self.assertEqual(pick2["provider"]["base_url"], base + "/v1")

            # 5) anthropic 原生供应商 → 探测出 openai 面（responses 优先）
            modelhub.upsert_provider({"name": "网关B", "protocol": "anthropic",
                                      "base_url": base, "api_key": FAKE_KEY,
                                      "model": "m2"})
            pb = [p for p in modelhub.providers() if p["name"] == "网关B"][0]["id"]
            caps_b, _ = modelhub.probe_wire_caps(pb)
            self.assertEqual(caps_b["openai"]["base"], base + "/v1")
            self.assertEqual(caps_b["openai"]["wire_api"], "responses")
            modelhub.set_binding("codex-cli", chain=[
                {"provider_id": pb, "model": "m2"}])
            rd = modelhub.resolve_binding("codex-cli")
            self.assertEqual(rd["codex_provider"]["base_url"], base + "/v1")
            self.assertEqual(rd["codex_provider"]["wire_api"], "responses")
        finally:
            srv.shutdown()
            srv.server_close()

    def test_probe_failure_keeps_gate_shut(self):
        from app.core import modelhub
        modelhub._FILE = self.data_dir / "models.json"

        # 探测失败（连接拒绝的环回端口）→ 不产生 caps，note 说明原因
        modelhub.upsert_provider({"name": "坏网关", "protocol": "openai",
                                  "base_url": "http://127.0.0.1:9/v1",
                                  "api_key": FAKE_KEY, "model": "m1"})
        pb = modelhub.providers()[0]["id"]
        caps, note = modelhub.probe_wire_caps(pb)
        self.assertEqual(caps, {})
        self.assertTrue(note)

        # 先植入历史 caps 再探失败 → 旧结果作废（以实测为准），解析恢复跳过
        data = modelhub._load()
        data["providers"][0]["wire_caps"] = {
            "anthropic": {"base": "http://127.0.0.1:9", "wire_api": "messages"}}
        modelhub._save(data)
        modelhub.set_binding("claude-code", chain=[{"provider_id": pb, "model": "m1"}])
        self.assertIsNotNone(modelhub.resolve_binding("claude-code"))
        caps2, _ = modelhub.probe_wire_caps(pb)
        self.assertEqual(caps2, {})
        self.assertIsNone(modelhub.resolve_binding("claude-code"))

    def test_upsert_invalidates_caps(self):
        from app.core import modelhub
        modelhub._FILE = self.data_dir / "models.json"
        modelhub.upsert_provider({"name": "网关", "protocol": "openai",
                                  "base_url": "https://a.test/v1", "api_key": FAKE_KEY})
        pid = modelhub.providers()[0]["id"]
        data = modelhub._load()
        data["providers"][0]["wire_caps"] = {
            "anthropic": {"base": "https://a.test", "wire_api": "messages"}}
        modelhub._save(data)
        # 只改名字：caps 保留
        modelhub.upsert_provider({"id": pid, "name": "网关改", "protocol": "openai",
                                  "base_url": "https://a.test/v1", "api_key": ""})
        self.assertIn("wire_caps", modelhub.providers()[0])
        # 换地址：caps 作废
        modelhub.upsert_provider({"id": pid, "name": "网关改", "protocol": "openai",
                                  "base_url": "https://b.test/v1", "api_key": ""})
        self.assertNotIn("wire_caps", modelhub.providers()[0])

    def test_known_base_mapping(self):
        from app.core import modelhub
        # Z.ai 双端点：anthropic 面 → openai 面的已知映射排候选首位
        cands = modelhub._wire_base_candidates("https://api.z.ai/api/anthropic", "openai")
        self.assertEqual(cands[0], "https://api.z.ai/api/paas/v4")
        # 智谱同样分开提供 anthropic/openai 两个入口，不能在 anthropic base
        # 后面硬拼 /responses（会让 Codex 无限重连）。
        glm = modelhub._wire_base_candidates(
            "https://open.bigmodel.cn/api/anthropic", "openai")
        self.assertEqual(glm[0], "https://open.bigmodel.cn/api/paas/v4")
        # 常规 openai base → anthropic 候选去掉 /v1
        self.assertEqual(modelhub._wire_base_candidates("https://x.test/v1", "anthropic"),
                         ["https://x.test"])


class TestAutoProtocol(BaseTest):
    """protocol="auto"：导入不填格式，靠实测分类；旧显式值行为完全不变。"""

    def test_auto_default_and_explicit_preserved(self):
        from app.core import modelhub
        modelhub._FILE = self.data_dir / "models.json"
        # 新增不传 protocol → auto；显式值原样保留（零影响迁移的核心保证）
        modelhub.upsert_provider({"name": "新网关", "base_url": "https://a.test/v1",
                                  "api_key": FAKE_KEY})
        self.assertEqual(modelhub.providers()[0]["protocol"], "auto")
        modelhub.upsert_provider({"name": "老网关", "protocol": "anthropic",
                                  "base_url": "https://b.test/v1", "api_key": FAKE_KEY})
        provs = {p["name"]: p for p in modelhub.providers()}
        self.assertEqual(provs["老网关"]["protocol"], "anthropic")
        # 更新时不传 protocol：沿用旧值，不被打回 auto
        modelhub.upsert_provider({"id": provs["老网关"]["id"], "name": "老网关",
                                  "base_url": "https://b.test/v1", "api_key": ""})
        self.assertEqual(modelhub.providers()[1]["protocol"], "anthropic")
        # 非法协议仍拒绝
        self.assertTrue(modelhub.upsert_provider({"name": "x", "protocol": "grpc",
                                                  "base_url": "https://c.test/v1"}))

    def test_auto_probes_all_wires_and_resolves(self):
        from app.core import modelhub
        modelhub._FILE = self.data_dir / "models.json"
        srv = _serve()
        try:
            base = "http://127.0.0.1:%d" % srv.server_address[1]
            modelhub.upsert_provider({"name": "聚合网关", "base_url": base + "/v1",
                                      "api_key": FAKE_KEY, "model": "m1"})
            pid = modelhub.providers()[0]["id"]
            self.assertEqual(modelhub.providers()[0]["protocol"], "auto")
            # auto：两条 wire 都探（不像显式那样跳过原生那条）
            caps, note = modelhub.probe_wire_caps(pid)
            self.assertEqual(note, "")
            self.assertIn("anthropic", caps)
            self.assertIn("openai", caps)

            # 解析：同一个 auto 供应商同时满足 claude（anthropic）与 codex（openai）
            modelhub.set_binding("claude-code", chain=[{"provider_id": pid, "model": "m1"}])
            modelhub.set_binding("codex-cli", chain=[{"provider_id": pid, "model": "m1"}])
            r_claude = modelhub.resolve_binding("claude-code")
            self.assertEqual(r_claude["env"]["ANTHROPIC_BASE_URL"], base)
            r_codex = modelhub.resolve_binding("codex-cli")
            self.assertEqual(r_codex["codex_provider"]["base_url"], base + "/v1")

            # 需要唯一协议的场景（单模型测试 / 直连对话）逐条试实测 wire
            self.assertEqual(modelhub._protocol_candidates(modelhub.providers()[0]),
                             [("anthropic", base), ("openai", base + "/v1")])
            t = modelhub.test_model(pid, "m1")
            self.assertTrue(t["ok"], t.get("error"))
            self.assertEqual(t["protocol"], "anthropic")   # 偏好序首条
            c = modelhub.chat(pid, "m1", "ping", max_tokens=1)
            self.assertTrue(c["ok"], c.get("error"))
        finally:
            srv.shutdown()
            srv.server_close()

    def test_auto_without_probe_is_not_guessed(self):
        from app.core import modelhub
        modelhub._FILE = self.data_dir / "models.json"
        # auto 且没探过：解析跳过（不猜协议）；单模型测试先自带一次适配探测，
        # 网关真不通（连不上）时探不出 wire，如实报错且不落 caps
        modelhub.upsert_provider({"name": "未探测网关", "base_url": "http://127.0.0.1:9/v1",
                                  "api_key": FAKE_KEY, "model": "m1"})
        pid = modelhub.providers()[0]["id"]
        modelhub.set_binding("claude-code", chain=[{"provider_id": pid, "model": "m1"}])
        self.assertIsNone(modelhub.resolve_binding("claude-code"))
        self.assertEqual(modelhub._protocol_candidates(modelhub.providers()[0]), [])
        t = modelhub.test_model(pid, "m1")
        self.assertFalse(t["ok"])
        self.assertIn("wire", t["error"])
        self.assertEqual(modelhub.providers()[0].get("wire_caps"), None)

    def test_auto_test_and_chat_self_probe(self):
        """鸡生蛋解除：auto 未探测时单模型测试/直连对话自带一次适配探测，
        探通即落盘放行；模型全停用也能借首个可见模型探（探针只是连通验证，
        不要求模型参与编排）。"""
        from app.core import modelhub
        modelhub._FILE = self.data_dir / "models.json"
        srv = _serve()
        try:
            base = "http://127.0.0.1:%d" % srv.server_address[1]
            modelhub.upsert_provider({"name": "懒探网关", "base_url": base + "/v1",
                                      "api_key": FAKE_KEY})
            pid = [p for p in modelhub.providers() if p["name"] == "懒探网关"][0]["id"]
            # 种一个全停用的模型列表：没有「启用模型」可挑
            data = modelhub._load()
            data["providers"][0]["models"] = [
                {"name": "m1", "enabled": False, "priority": 1},
                {"name": "m2", "enabled": False, "priority": 2}]
            modelhub._save(data)
            self.assertEqual(modelhub._protocol_candidates(
                [p for p in modelhub.providers() if p["id"] == pid][0]), [])

            t = modelhub.test_model(pid, "m1")
            self.assertTrue(t["ok"], t.get("error"))
            self.assertIn("openai",
                          [p for p in modelhub.providers() if p["id"] == pid][0]
                          .get("wire_caps") or {})

            # 直连对话同样自带探测（第二个全新供应商验证）
            modelhub.upsert_provider({"name": "懒探二号", "base_url": base + "/v1",
                                      "api_key": FAKE_KEY})
            pid2 = [p for p in modelhub.providers() if p["name"] == "懒探二号"][0]["id"]
            c = modelhub.chat(pid2, "m2", "ping", max_tokens=1)
            self.assertTrue(c["ok"], c.get("error"))
            self.assertTrue(modelhub._protocol_candidates(
                [p for p in modelhub.providers() if p["id"] == pid2][0]))
        finally:
            srv.shutdown()
            srv.server_close()

    def test_auto_fetch_models_tries_both_shapes(self):
        from app.core import modelhub
        modelhub._FILE = self.data_dir / "models.json"
        srv = _serve()
        try:
            base = "http://127.0.0.1:%d" % srv.server_address[1]
            modelhub.upsert_provider({"name": "聚合网关", "base_url": base + "/v1",
                                      "api_key": FAKE_KEY})
            pid = modelhub.providers()[0]["id"]
            n, err = modelhub.refresh_models(pid)
            self.assertTrue(n > 0, err)
            # 取列表不依赖用户填的格式：auto 也拿到了（两条 URL 形状都试）
            self.assertTrue(any(m["name"] == "gpt-x" for m in modelhub.providers()[0]["models"]))
        finally:
            srv.shutdown()
            srv.server_close()


if __name__ == "__main__":
    import unittest as _u
    _u.main()
