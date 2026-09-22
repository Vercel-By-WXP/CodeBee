# -*- coding: utf-8 -*-
"""多 KEY 测试：同厂商多密钥、启停、排序、欠费冷却切备用、复制供应商、零影响迁移。

一个厂商可配多把 KEY（不同账号 / 欠费后的备用号）。keys 数组顺序即调用顺序；
api_key 是「首个可用 KEY」的镜像——老数据（只有 api_key）行为完全不变。
所有密钥均为运行时构造的假值，无真实凭据。
"""
from __future__ import annotations

from base import BaseTest

FAKE_K1 = "sk-test-" + "11111111" * 3
FAKE_K2 = "sk-test-" + "22222222" * 3
FAKE_K3 = "sk-test-" + "33333333" * 3


class TestProviderKeys(BaseTest):
    def _one(self, modelhub):
        """建一个供应商，返回 (pid)。"""
        modelhub.upsert_provider({"name": "网关", "protocol": "anthropic",
                                  "base_url": "https://a.test/v1", "api_key": FAKE_K1,
                                  "model": "m1"})
        return modelhub.providers()[0]["id"]

    def test_legacy_single_key_untouched(self):
        """老数据（只有 api_key）：行为与结构都不变，不凭空长出 keys 数组。"""
        from app.core import modelhub
        modelhub._FILE = self.data_dir / "models.json"
        pid = self._one(modelhub)
        p = modelhub.providers()[0]
        self.assertNotIn("keys", p)                       # 不写盘、不迁移
        self.assertEqual(p["api_key"], FAKE_K1)
        keys = modelhub._provider_keys(p)                 # 合成一条隐式 KEY
        self.assertEqual(len(keys), 1)
        self.assertEqual(keys[0]["key"], FAKE_K1)
        modelhub.set_binding("claude-code", chain=[{"provider_id": pid, "model": "m1"}])
        r = modelhub.resolve_binding("claude-code")
        self.assertEqual(r["env"]["ANTHROPIC_AUTH_TOKEN"], FAKE_K1)

    def test_add_keys_and_chain_expansion(self):
        from app.core import modelhub
        modelhub._FILE = self.data_dir / "models.json"
        pid = self._one(modelhub)
        # 加两把 KEY：首次改 KEY 时把隐式单 KEY 落成显式数组
        self.assertIsNone(modelhub.key_op(pid, "add", key=FAKE_K2, label="备用号"))
        self.assertIsNone(modelhub.key_op(pid, "add", key=FAKE_K3))
        p = modelhub.providers()[0]
        self.assertEqual([k["key"] for k in p["keys"]], [FAKE_K1, FAKE_K2, FAKE_K3])
        self.assertEqual(p["api_key"], FAKE_K1)           # 镜像=首个可用
        self.assertEqual(p["keys"][1]["label"], "备用号")

        # 链展开：同一模型按 KEY 出三条，顺序即调用顺序
        modelhub.set_binding("claude-code", chain=[{"provider_id": pid, "model": "m1"}])
        r = modelhub.resolve_binding("claude-code")
        cc = r["call_chain"]
        self.assertEqual(len(cc), 3)
        self.assertEqual([e["env"]["ANTHROPIC_AUTH_TOKEN"] for e in cc],
                         [FAKE_K1, FAKE_K2, FAKE_K3])
        self.assertEqual([e["key_id"] for e in cc], ["k1", "k2", "k3"])
        self.assertEqual([e["provider_id"] for e in cc], [pid] * 3)
        self.assertEqual(cc[0]["env"]["ANTHROPIC_BASE_URL"], "https://a.test/v1")
        # 同模型的重复条目不进 model_fallbacks（那是「换模型」的列表）
        self.assertEqual(r["model_fallbacks"], [])

    def test_quota_error_cools_key_and_switches(self):
        """欠费 KEY 进冷却 → 解析自动切到备用；首选恢复后回到队首。"""
        from app.core import modelhub
        modelhub._FILE = self.data_dir / "models.json"
        pid = self._one(modelhub)
        modelhub.key_op(pid, "add", key=FAKE_K2)
        modelhub.set_binding("claude-code", chain=[{"provider_id": pid, "model": "m1"}])
        self.assertEqual(len(modelhub.resolve_binding("claude-code")["call_chain"]), 2)

        # 首选 KEY 欠费 → 冷却，镜像切到备用
        modelhub.note_key_error(pid, "k1", "HTTP 402 Insufficient Balance")
        p = modelhub.providers()[0]
        self.assertEqual(p["api_key"], FAKE_K2)
        cc = modelhub.resolve_binding("claude-code")["call_chain"]
        self.assertEqual(len(cc), 1)
        self.assertEqual(cc[0]["env"]["ANTHROPIC_AUTH_TOKEN"], FAKE_K2)
        self.assertTrue(modelhub.provider_view()[0]["keys"][0]["cooling"])
        self.assertIn("Insufficient", modelhub.provider_view()[0]["keys"][0]["last_error"])

        # 非欠费类错误不冷却（只是记一笔）：k1 仍在冷却，k2 可用 → 链上只有 k2
        modelhub.note_key_error(pid, "k2", "HTTP 503 unavailable")
        view = modelhub.provider_view()[0]["keys"]
        self.assertFalse(view[1]["cooling"])
        self.assertTrue(view[1]["last_error"])            # 错误记下了
        cc2 = modelhub.resolve_binding("claude-code")["call_chain"]
        self.assertEqual([e["key_id"] for e in cc2], ["k2"])

        # 重置（充值后手动恢复）→ 首选回到队首，两把都可用
        self.assertIsNone(modelhub.key_op(pid, "reset", key_id="k1"))
        self.assertEqual(modelhub.providers()[0]["api_key"], FAKE_K1)
        cc3 = modelhub.resolve_binding("claude-code")["call_chain"]
        self.assertEqual([e["key_id"] for e in cc3], ["k1", "k2"])

    def test_all_keys_cooling_still_tries_one(self):
        """全部 KEY 都在冷却：仍留一条顶上（整家不可用比多试一次代价大）。"""
        from app.core import modelhub
        modelhub._FILE = self.data_dir / "models.json"
        pid = self._one(modelhub)
        modelhub.key_op(pid, "add", key=FAKE_K2)
        modelhub.note_key_error(pid, "k1", "insufficient balance")
        modelhub.note_key_error(pid, "k2", "余额不足")
        modelhub.set_binding("claude-code", chain=[{"provider_id": pid, "model": "m1"}])
        cc = modelhub.resolve_binding("claude-code")["call_chain"]
        self.assertEqual(len(cc), 1)
        self.assertEqual(cc[0]["key_id"], "k1")           # 冷却没到期也得有人顶

    def test_ratelimit_429_cools_key_and_switches(self):
        """429/中文超限文案同样进冷却切备用（2026-09-22 首选超限不切备用实案）。

        codex「exceeded retry limit...429 Too Many Requests」原靠 exceeded 命中；
        智谱原生「并发数超过限制」「每分钟Token数已超过上限」与 claude 的
        rate_limit_error 此前只记错不冷却——每个新步骤都从超限的首选 KEY 重新烧起。
        """
        from app.core import modelhub
        modelhub._FILE = self.data_dir / "models.json"
        pid = self._one(modelhub)
        modelhub.key_op(pid, "add", key=FAKE_K2)
        modelhub.set_binding("claude-code", chain=[{"provider_id": pid, "model": "m1"}])
        cases = ("HTTP 429 并发数超过限制",
                 "codex: exceeded retry limit, last status: 429 Too Many Requests",
                 "claude 返回 is_error: API Error: 429 rate_limit_error")
        for err in cases:
            modelhub.key_op(pid, "reset", key_id="k1")
            modelhub.key_op(pid, "reset", key_id="k2")
            modelhub.note_key_error(pid, "k1", err)
            view = modelhub.provider_view()[0]["keys"]
            self.assertTrue(view[0]["cooling"], err)                    # 进冷却
            self.assertEqual(modelhub.providers()[0]["api_key"], FAKE_K2, err)  # 镜像切备用
            cc = modelhub.resolve_binding("claude-code")["call_chain"]
            self.assertEqual([e["key_id"] for e in cc], ["k2"], err)    # 链上跳过
        # 超时类不属于 KEY 的锅：只记错，不冷却（原有语义不变）
        modelhub.key_op(pid, "reset", key_id="k1")
        modelhub.note_key_error(pid, "k1", "超时；stderr/stdout: ...")
        self.assertFalse(modelhub.provider_view()[0]["keys"][0]["cooling"])

    def test_runner_quota_table_in_sync(self):
        """runner 的独立 _QUOTA 副本与 modelhub 同源：新增文案两边都要认。"""
        from app.core import modelhub, runner
        for err in ("HTTP 429 并发数超过限制",
                    "API Error: 429 rate_limit_error",
                    "codex: exceeded retry limit, last status: 429 Too Many Requests"):
            self.assertTrue(modelhub._quota_error(err), err)
            self.assertTrue(runner._quota_error(err), err)

    def test_fallback_model_entries_carry_key_id(self):
        """无显式链时换模型的回退条目也带 key_id：失败时 runner 才能记账冷却。

        此前回退条目不带 key_id，_report_key 直接早退——同厂商换模型的重试
        完全不记 KEY 账。
        """
        from app.core import modelhub
        modelhub._FILE = self.data_dir / "models.json"
        pid = self._one(modelhub)
        data = modelhub._load()
        prov = data["providers"][0]
        prov["models"] = [{"name": "m1", "priority": 1}, {"name": "m2", "priority": 2}]
        modelhub._save(data)
        modelhub.key_op(pid, "add", key=FAKE_K2)
        # 只钉供应商、不写 models：models=[] 会同步清掉 provider_id（同链重建），
        # 这样 chain 保持空走无链分支，回退模型来自供应商级启用模型列表
        modelhub.set_binding("claude-code", provider_id=pid)
        cc = modelhub.resolve_binding("claude-code")["call_chain"]
        # 主模型按 KEY 展开（m1×k1、m1×k2），回退模型条目带首选可用 KEY 的 id
        self.assertEqual([(e["model"], e.get("key_id")) for e in cc],
                         [("m1", "k1"), ("m1", "k2"), ("m2", "k1")])
        self.assertEqual(cc[2]["env"]["ANTHROPIC_AUTH_TOKEN"], FAKE_K1)
        # 首选超限进冷却后：主模型与回退模型都跟镜像切到 k2
        modelhub.note_key_error(pid, "k1", "HTTP 429 并发数超过限制")
        cc2 = modelhub.resolve_binding("claude-code")["call_chain"]
        self.assertEqual([(e["model"], e.get("key_id")) for e in cc2],
                         [("m1", "k2"), ("m2", "k2")])

    def test_disable_key_excluded(self):
        from app.core import modelhub
        modelhub._FILE = self.data_dir / "models.json"
        pid = self._one(modelhub)
        modelhub.key_op(pid, "add", key=FAKE_K2)
        modelhub.set_binding("claude-code", chain=[{"provider_id": pid, "model": "m1"}])
        self.assertIsNone(modelhub.key_op(pid, "disable", key_id="k1"))
        cc = modelhub.resolve_binding("claude-code")["call_chain"]
        self.assertEqual([e["key_id"] for e in cc], ["k2"])
        self.assertEqual(modelhub.providers()[0]["api_key"], FAKE_K2)   # 镜像跟随
        # 重新启用即手动恢复：回到队首
        self.assertIsNone(modelhub.key_op(pid, "enable", key_id="k1"))
        self.assertEqual(modelhub.providers()[0]["api_key"], FAKE_K1)

    def test_reorder_keys(self):
        from app.core import modelhub
        modelhub._FILE = self.data_dir / "models.json"
        pid = self._one(modelhub)
        modelhub.key_op(pid, "add", key=FAKE_K2)
        modelhub.key_op(pid, "add", key=FAKE_K3)
        self.assertIsNone(modelhub.key_op(pid, "reorder", ids=["k3", "k1", "k2"]))
        p = modelhub.providers()[0]
        self.assertEqual([k["id"] for k in p["keys"]], ["k3", "k1", "k2"])
        self.assertEqual(p["api_key"], FAKE_K3)           # 首个可用 = 新队首
        # 排序列表与现有 KEY 不一致 → 拒绝
        self.assertTrue(modelhub.key_op(pid, "reorder", ids=["k3"]))

    def test_delete_and_update_key(self):
        from app.core import modelhub
        modelhub._FILE = self.data_dir / "models.json"
        pid = self._one(modelhub)
        modelhub.key_op(pid, "add", key=FAKE_K2)
        self.assertIsNone(modelhub.key_op(pid, "update", key_id="k2",
                                          key=FAKE_K3, label="改过"))
        p = modelhub.providers()[0]
        self.assertEqual(p["keys"][1]["key"], FAKE_K3)
        self.assertEqual(p["keys"][1]["label"], "改过")
        # 删掉首选 → 镜像切到剩下那把
        self.assertIsNone(modelhub.key_op(pid, "delete", key_id="k1"))
        self.assertEqual([k["id"] for k in modelhub.providers()[0]["keys"]], ["k2"])
        self.assertEqual(modelhub.providers()[0]["api_key"], FAKE_K3)
        # 空密钥 / 不存在的 KEY 拒绝
        self.assertTrue(modelhub.key_op(pid, "add", key="  "))
        self.assertTrue(modelhub.key_op(pid, "update", key_id="nope", label="x"))

    def test_upsert_replaces_first_key(self):
        """表单里新填的密钥替换首选 KEY，不另开一把（并清掉它的冷却）。"""
        from app.core import modelhub
        modelhub._FILE = self.data_dir / "models.json"
        pid = self._one(modelhub)
        modelhub.key_op(pid, "add", key=FAKE_K2)
        modelhub.note_key_error(pid, "k1", "insufficient balance")
        modelhub.upsert_provider({"id": pid, "name": "网关", "protocol": "anthropic",
                                  "base_url": "https://a.test/v1", "api_key": FAKE_K3})
        p = modelhub.providers()[0]
        self.assertEqual(len(p["keys"]), 2)               # 没多出来一把
        self.assertEqual(p["keys"][0]["key"], FAKE_K3)
        self.assertFalse(modelhub.provider_view()[0]["keys"][0]["cooling"])

    def test_duplicate_provider(self):
        from app.core import modelhub
        modelhub._FILE = self.data_dir / "models.json"
        pid = self._one(modelhub)
        modelhub.key_op(pid, "add", key=FAKE_K2)
        modelhub.note_key_error(pid, "k1", "insufficient balance")
        n, err = modelhub.providers_op([pid], "duplicate")
        self.assertEqual((n, err), (1, ""))
        provs = modelhub.providers()
        self.assertEqual(len(provs), 2)
        dup = provs[1]
        self.assertNotEqual(dup["id"], pid)
        self.assertTrue(dup["name"].endswith("副本"))
        self.assertTrue(dup["enabled"])
        # 副本的 KEY 重新编号且不带冷却（是新的一份配置，不是原件的状态）
        self.assertEqual([k["id"] for k in dup["keys"]], ["k1", "k2"])
        self.assertFalse(any(k.get("cool_until") for k in dup["keys"]))
        # 副本不继承绑定引用
        self.assertNotIn(dup["id"], str(modelhub.bindings()))


if __name__ == "__main__":
    import unittest as _u
    _u.main()
