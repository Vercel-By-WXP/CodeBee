# -*- coding: utf-8 -*-
"""停用模型不可用闸门测试（0.1.98 排查定版）。

默认模型键位（model / model_easy / model_hard / 编排者配置的 model）与
models[] 的启停开关互不联动——历史上所有「走默认模型」的路径都不对账
启用名单，已停用的模型会被静默真用。本套断言四条铁律：

1. 默认/难度模型键位指向停用模型时，运行时一律回落该厂商启用名单；
2. 全停用时按「无默认模型」处理（编排者判未就绪，直连/绑定不点名死模型）；
3. 显式点名停用模型仍然拒绝（大声失败，不静默换模）；
4. 自动调度候选兜底不再把已停用的默认模型充作唯一候选。
"""
from __future__ import annotations

from base import BaseTest

FAKE_KEY = "sk-test-" + "dddddddd" * 3


def _prov_with_models(modelhub, enabled=(), disabled=(), name="网关D",
                      default="", easy="", hard=""):
    """建一个 openai 供应商：models[] 按 enabled/disabled 两组写入，另设
    默认/难度模型键位。返回 provider id。"""
    modelhub.upsert_provider({"name": name, "protocol": "openai",
                              "base_url": "https://d.test/v1", "api_key": FAKE_KEY,
                              "model": default, "model_easy": easy, "model_hard": hard})
    pid = modelhub.providers()[0]["id"]
    data = modelhub._load()
    prov = next(p for p in data["providers"] if p["id"] == pid)
    prov["models"] = [{"name": n, "enabled": True} for n in enabled] + \
                     [{"name": n, "enabled": False} for n in disabled]
    modelhub._save(data)
    return pid


class TestDisabledModelGate(BaseTest):
    def test_orchestrator_disabled_default_falls_back(self):
        from app.core import modelhub
        modelhub._FILE = self.data_dir / "models.json"
        pid = _prov_with_models(modelhub, enabled=["m-ok"], disabled=["m-dead"],
                                default="m-dead")

        # 编排者配置点名了后来被停用的模型：回落厂商启用名单，不冒充可用
        modelhub.set_orchestrator(pid, model="m-dead", enabled=True)
        orch = modelhub.resolve_orchestrator()
        self.assertIsNotNone(orch)
        self.assertEqual(orch[1], "m-ok")

        # 配置留空走厂商默认模型键位（同样指向停用模型）：同样回落
        modelhub.set_orchestrator(pid, model="", enabled=True)
        self.assertEqual(modelhub.resolve_orchestrator()[1], "m-ok")

        # 全停用：无可用模型 → 编排者判未就绪（UI 走「当前配置不生效」提示）
        data = modelhub._load()
        prov = next(p for p in data["providers"] if p["id"] == pid)
        prov["models"] = [{"name": "m-dead", "enabled": False}]
        modelhub._save(data)
        modelhub.set_orchestrator(pid, model="m-dead", enabled=True)
        self.assertIsNone(modelhub.resolve_orchestrator())

    def test_builtin_agent_disabled_default(self):
        from app.core import builtin_agent, modelhub
        modelhub._FILE = self.data_dir / "models.json"
        pid = _prov_with_models(modelhub, enabled=["m-ok"], disabled=["m-dead"],
                                default="m-dead", hard="m-dead", easy="m-dead")

        # 点名厂商 + 随厂商推荐：默认模型已停用 → 回落启用名单
        r = builtin_agent.resolve(pid, "")
        self.assertIsNotNone(r)
        self.assertEqual(r["model"], "m-ok")

        # 点名厂商 + 点名停用模型：大声失败（返回 None），不静默换模
        self.assertIsNone(builtin_agent.resolve(pid, "m-dead"))

        # 自动推荐（无厂商）：难度路由的 hard/easy 键位同指停用模型 → 回落
        r = builtin_agent.resolve("", "", "hard")
        self.assertIsNotNone(r)
        self.assertEqual(r["model"], "m-ok")
        r = builtin_agent.resolve("", "", "easy")
        self.assertIsNotNone(r)
        self.assertEqual(r["model"], "m-ok")

    def test_resolve_binding_disabled_default(self):
        from app.core import modelhub
        modelhub._FILE = self.data_dir / "models.json"
        pid = _prov_with_models(modelhub, enabled=["m-ok", "m-ok2"],
                                disabled=["m-dead"], default="m-dead")

        # 无显式链（provider_id 绑定）：默认模型停用 → 主模型回落启用名单
        modelhub.set_binding("opencode", provider_id=pid)
        r = modelhub.resolve_binding("opencode")
        self.assertIsNotNone(r)
        self.assertEqual(r["model"], "m-ok")

        # 显式链点名停用模型：该条目跳过；只剩死条目 → 整链判死
        modelhub.set_binding("opencode", chain=[{"provider_id": pid, "model": "m-dead"}])
        self.assertIsNone(modelhub.resolve_binding("opencode"))
        modelhub.set_binding("opencode", chain=[{"provider_id": pid, "model": "m-dead"},
                                                {"provider_id": pid, "model": "m-ok"}])
        r = modelhub.resolve_binding("opencode")
        self.assertEqual(r["model"], "m-ok")
        self.assertEqual(r["model_fallbacks"], [])

        # 兼容空模型链条目（仅注入凭据语义）：默认模型停用时不冒充，
        # 回落启用名单；全停用时保持空模型（不点名死模型）
        modelhub.set_binding("opencode", chain=[{"provider_id": pid, "model": "m-ok"}])
        data = modelhub._load()
        data["bindings"]["opencode"]["chain"] = [{"provider_id": pid, "model": ""}]
        modelhub._save(data)
        r = modelhub.resolve_binding("opencode")
        self.assertEqual(r["model"], "m-ok")
        data = modelhub._load()
        prov = next(p for p in data["providers"] if p["id"] == pid)
        prov["models"] = [{"name": "m-dead", "enabled": False}]
        modelhub._save(data)
        r = modelhub.resolve_binding("opencode")
        self.assertIsNotNone(r)
        self.assertEqual(r["model"], "")

    def test_recommend_binding_skips_disabled_default(self):
        from app.core import modelhub
        modelhub._FILE = self.data_dir / "models.json"

        # 名单全停用 + 默认键位也指向停用模型：不产候选（不再兜底死模型）
        pid = _prov_with_models(modelhub, enabled=[], disabled=["m-dead"],
                                default="m-dead", name="全停网关")
        self.assertIsNone(modelhub.recommend_binding("opencode"))

        # 有启用模型时推荐链只含启用模型
        pid2 = _prov_with_models(modelhub, enabled=["m-ok"], disabled=["m-dead"],
                                 default="m-dead", name="健康网关")
        r = modelhub.recommend_binding("opencode")
        self.assertIsNotNone(r)
        models = [e["model"] for e in r["call_chain"] if e.get("provider") and
                  (e.get("provider") or {}).get("id") == pid2]
        self.assertIn("m-ok", models)
        self.assertNotIn("m-dead", models)
