# -*- coding: utf-8 -*-
"""独苗兜底回归（2026-10-22 任务全灭实案）。

「只有一个 CLI、只有一个模型」的用户不该被瞬态健康态判成绑定链全部失效：
健康 down / 模型级熔断 / 评测结论作废三道闸只在链上还有健康候选时生效，
全场皆死时按原链兜底先试（条目带 last_resort 标记），让 runner 拿真实
上游错误说话、误分类健康事件当场自愈。确定性死因（删除/停用/无密钥/
协议不符）仍照旧判死，不拿配置错误白烧上游。
"""
import json
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from app.core import modelhub

PROV_A = {"id": "prov-a", "name": "甲供应商", "enabled": True,
          "api_key": "sk-a", "protocol": "openai",
          "base_url": "https://a.example/v1", "model": "m1",
          "models": [{"name": "m1", "enabled": True},
                     {"name": "m2", "enabled": True}]}
PROV_B = {"id": "prov-b", "name": "乙供应商", "enabled": True,
          "api_key": "sk-b", "protocol": "openai",
          "base_url": "https://b.example/v1", "model": "mb",
          "models": [{"name": "mb", "enabled": True}]}
TARGET = "test-cli"   # 泛型目标：anthropic/openai 双协议都收


class _Base(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        root = Path(self._tmp.name).resolve()
        self.path = root / "models.json"
        self._write({"providers": [PROV_A], "bindings": {}})

    def tearDown(self):
        self._tmp.cleanup()

    def _write(self, data):
        self.path.write_text(json.dumps(data, ensure_ascii=False),
                             encoding="utf-8")

    def _resolve(self, providers=None, bindings=None, fused=(), down=(),
                 eval_ok=True):
        self._write({"providers": providers if providers is not None else [PROV_A],
                     "bindings": bindings or {}})
        fused_pairs = set(fused)

        def _is_model_down(provider, model):
            return (provider, model) in fused_pairs

        patchers = [
            mock.patch.object(modelhub, "_FILE", self.path),
            mock.patch("app.core.health.down_names", return_value=set(down)),
            mock.patch("app.core.health.is_model_down", side_effect=_is_model_down),
            mock.patch.object(modelhub, "_evaluation_is_usable",
                              return_value=eval_ok),
        ]
        for p in patchers:
            p.start()
            self.addCleanup(p.stop)
        return modelhub.resolve_binding(TARGET)


class ChainLastResortTests(_Base):
    def test_single_model_fused_still_resolves(self):
        out = self._resolve(bindings={TARGET: {"chain": [
            {"provider_id": "prov-a", "model": "m1"}]}},
            fused={("甲供应商", "m1")})
        self.assertIsNotNone(out)
        self.assertTrue(out.get("last_resort"))
        self.assertEqual(out["model"], "m1")
        self.assertTrue(all(e.get("last_resort") for e in out["call_chain"]))

    def test_healthy_sibling_keeps_fused_skipped(self):
        out = self._resolve(bindings={TARGET: {"chain": [
            {"provider_id": "prov-a", "model": "m1"},
            {"provider_id": "prov-a", "model": "m2"}]}},
            fused={("甲供应商", "m1")})
        self.assertIsNotNone(out)
        self.assertFalse(out.get("last_resort"))
        self.assertEqual([e["model"] for e in out["call_chain"]], ["m2"])

    def test_provider_down_last_resort(self):
        out = self._resolve(bindings={TARGET: {"chain": [
            {"provider_id": "prov-a", "model": "m1"}]}},
            down={"甲供应商"})
        self.assertIsNotNone(out)
        self.assertTrue(out.get("last_resort"))

    def test_evaluation_invalidated_last_resort(self):
        out = self._resolve(bindings={TARGET: {"chain": [
            {"provider_id": "prov-a", "model": "m1"}]}},
            eval_ok=False)
        self.assertIsNotNone(out)
        self.assertTrue(out.get("last_resort"))

    def test_deterministic_dead_still_fails(self):
        out = self._resolve(
            providers=[dict(PROV_A, enabled=False)],
            bindings={TARGET: {"chain": [{"provider_id": "prov-a",
                                          "model": "m1"}]}})
        self.assertIsNone(out)
        self.assertIn("供应商已停用", "；".join(modelhub.last_dead_reasons(TARGET)))

    def test_deleted_provider_still_fails(self):
        out = self._resolve(
            providers=[PROV_B],
            bindings={TARGET: {"chain": [{"provider_id": "prov-a",
                                          "model": "m1"}]}})
        self.assertIsNone(out)

    def test_healthy_chain_has_no_last_resort_flag(self):
        out = self._resolve(bindings={TARGET: {"chain": [
            {"provider_id": "prov-a", "model": "m1"}]}})
        self.assertIsNotNone(out)
        self.assertFalse(out.get("last_resort"))
        self.assertFalse(any(e.get("last_resort") for e in out["call_chain"]))


class SingleProviderLastResortTests(_Base):
    def test_head_fused_swaps_to_healthy_sibling(self):
        out = self._resolve(bindings={TARGET: {"provider_id": "prov-a"}},
                            fused={("甲供应商", "m1")})
        self.assertIsNotNone(out)
        self.assertEqual(out["model"], "m2")
        self.assertFalse(out.get("last_resort"))

    def test_all_models_fused_last_resort(self):
        out = self._resolve(bindings={TARGET: {"provider_id": "prov-a"}},
                            fused={("甲供应商", "m1"), ("甲供应商", "m2")})
        self.assertIsNotNone(out)
        self.assertTrue(out.get("last_resort"))
        self.assertEqual(out["model"], "m1")
        self.assertTrue(all(e.get("last_resort") for e in out["call_chain"]))

    def test_provider_eval_invalidated_last_resort(self):
        out = self._resolve(bindings={TARGET: {"provider_id": "prov-a"}},
                            eval_ok=False)
        self.assertIsNotNone(out)
        self.assertTrue(out.get("last_resort"))

    def test_disabled_provider_still_fails(self):
        out = self._resolve(
            providers=[dict(PROV_A, enabled=False)],
            bindings={TARGET: {"provider_id": "prov-a"}})
        self.assertIsNone(out)


if __name__ == "__main__":
    unittest.main()
