# -*- coding: utf-8 -*-
"""编排者默认全自动 + 手动覆盖回归。

核心语义（2026-10-30 定稿）：
1. 未手动启用编排者 → resolve_orchestrator 自动选型：硬过滤（启停/密钥/健康/
   探针/模型启停）后用 dispatch 评分取最优，不是随机、不是「首个可用」；
2. 手动启用且有效 → 显式配置优先；
3. 手动配置失效（供应商停用/删密钥/模型停用）→ 回落自动选型，不整链停摆；
4. 无任何可用供应商 → 返回 None（调用方自有兜底）；
5. auto_orch_pick 带 60s 缓存，配置变更（set_orchestrator）立即作废。
"""
from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from app.core import modelhub


def _provider(pid, name, protocol="openai", model="m-a", models=None, enabled=True,
              api_key="key", priority=1, tier="standard", image_in=False):
    if models is None:
        models = [{"name": model, "enabled": True, "hidden": False,
                   "priority": priority, "tier": tier, "image_in": image_in}]
    else:
        # 调用方传入显式 dict 列表时原样使用（如模型停用/启用混合）
        models = list(models)
    return {
        "id": pid, "name": name, "protocol": protocol, "base_url": "https://x/v1",
        "model": model, "enabled": enabled, "api_key": api_key,
        "models": models,
    }


class OrchAutoTests(unittest.TestCase):
    def _write(self, providers, orchestrator=None, pricing=None):
        tmp = tempfile.TemporaryDirectory()
        path = Path(tmp.name) / "models.json"
        data = {"providers": providers}
        if orchestrator is not None:
            data["orchestrator"] = orchestrator
        if pricing is not None:
            data["pricing"] = pricing
        path.write_text(json.dumps(data), encoding="utf-8")
        self.addCleanup(tmp.cleanup)
        return path

    def _patch(self, path):
        patchers = [
            mock.patch.object(modelhub, "_FILE", path),
            mock.patch.object(modelhub, "_AUTO_ORCH_CACHE", {"at": 0.0, "pick": None}),
            mock.patch.object(modelhub, "_evaluation_is_usable", return_value=True),
            mock.patch("app.core.health.down_names", return_value=set()),
        ]
        for p in patchers:
            p.start()
            self.addCleanup(p.stop)
        return patchers

    # ---- 自动选型默认语义 ----

    def test_default_auto_picks_best_scored(self):
        # 两个可用供应商：prov-1 模型优先级高（质量分高），应被选中而非「首个」
        path = self._write([
            _provider("prov-1", "Alpha", model="alpha-strong", priority=1,
                      tier="premium"),
            _provider("prov-2", "Beta", model="beta-budget", priority=50,
                      tier="budget"),
        ])
        self._patch(path)
        pick = modelhub.auto_orch_pick()
        self.assertIsNotNone(pick)
        self.assertEqual(pick["provider_id"], "prov-1")
        self.assertEqual(pick["model"], "alpha-strong")

    def test_auto_skips_disabled_and_keyless(self):
        path = self._write([
            _provider("prov-1", "Off", enabled=False),
            _provider("prov-2", "NoKey", api_key=""),
            _provider("prov-3", "Good"),
        ])
        self._patch(path)
        pick = modelhub.auto_orch_pick()
        self.assertEqual(pick["provider_id"], "prov-3")

    def test_auto_none_when_no_candidates(self):
        path = self._write([_provider("prov-1", "Off", enabled=False)])
        self._patch(path)
        self.assertIsNone(modelhub.auto_orch_pick())

    # ---- resolve_orchestrator 手动优先 / 失效回落 ----

    def test_manual_config_wins(self):
        path = self._write(
            [_provider("prov-1", "A"), _provider("prov-2", "B")],
            orchestrator={"provider_id": "prov-2", "model": "m-a",
                          "enabled": True})
        self._patch(path)
        prov, model = modelhub.resolve_orchestrator()
        self.assertEqual(prov["id"], "prov-2")
        self.assertEqual(model, "m-a")

    def test_manual_disabled_falls_back_to_auto(self):
        # 用户曾配过但关掉（enabled=False）→ 应回自动，不是 None
        path = self._write(
            [_provider("prov-1", "A"), _provider("prov-2", "B")],
            orchestrator={"provider_id": "prov-1", "model": "m-a",
                          "enabled": False})
        self._patch(path)
        prov, model = modelhub.resolve_orchestrator()
        self.assertIsNotNone(prov)
        self.assertTrue(model)

    def test_manual_stale_provider_falls_back(self):
        # 手动指向已停用供应商 → 自动选型接管
        path = self._write(
            [_provider("prov-1", "Dead", enabled=False),
             _provider("prov-2", "B")],
            orchestrator={"provider_id": "prov-1", "model": "m-a",
                          "enabled": True})
        self._patch(path)
        prov, model = modelhub.resolve_orchestrator()
        self.assertEqual(prov["id"], "prov-2")

    def test_manual_stale_model_falls_back(self):
        # 手动模型被停用 → _usable_default_model 回落启用名单，仍手动供应商
        path = self._write(
            [_provider("prov-1", "A", model="m-a",
                       models=[{"name": "m-a", "enabled": False},
                               {"name": "m-b", "enabled": True}])],
            orchestrator={"provider_id": "prov-1", "model": "m-a",
                          "enabled": True})
        self._patch(path)
        prov, model = modelhub.resolve_orchestrator()
        self.assertEqual(prov["id"], "prov-1")
        self.assertEqual(model, "m-b")

    def test_no_provider_returns_none(self):
        path = self._write([], orchestrator=None)
        self._patch(path)
        self.assertIsNone(modelhub.resolve_orchestrator())

    # ---- orchestrator_view 自动形态 ----

    def test_view_auto_mode(self):
        path = self._write([_provider("prov-1", "Alpha", model="alpha-strong")])
        self._patch(path)
        view = modelhub.orchestrator_view()
        self.assertEqual(view.get("mode"), "auto")
        self.assertTrue(view.get("ready"))
        self.assertEqual(view.get("provider_id"), "prov-1")

    def test_view_manual_mode(self):
        path = self._write(
            [_provider("prov-1", "Alpha")],
            orchestrator={"provider_id": "prov-1", "model": "m-a",
                          "enabled": True})
        self._patch(path)
        view = modelhub.orchestrator_view()
        self.assertEqual(view.get("mode"), "manual")
        self.assertTrue(view.get("ready"))

    # ---- 缓存失效 ----

    def test_set_orchestrator_invalidates_cache(self):
        path = self._write([_provider("prov-1", "A")])
        self._patch(path)
        modelhub.set_orchestrator("prov-1", model="m-a", enabled=True)
        self.assertEqual(modelhub._AUTO_ORCH_CACHE["at"], 0.0)

    def test_cache_reuses_within_ttl(self):
        path = self._write([_provider("prov-1", "A")])
        self._patch(path)
        first = modelhub.auto_orch_pick()
        # 缓存窗口内再次调用应直接命中（_auto_orch_pick_uncached 不重跑）
        with mock.patch.object(modelhub, "_auto_orch_pick_uncached",
                               side_effect=AssertionError("should hit cache")) as m:
            second = modelhub.auto_orch_pick()
            m.assert_not_called()
        self.assertEqual(first["provider_id"], second["provider_id"])

    def test_cache_fresh_zero_recomputes(self):
        path = self._write([_provider("prov-1", "A"), _provider("prov-2", "B")])
        self._patch(path)
        first = modelhub.auto_orch_pick()
        self.assertIsNotNone(first)
        with mock.patch.object(modelhub, "_auto_orch_pick_uncached") as m:
            m.return_value = {"provider_id": "prov-2", "provider_name": "B",
                              "model": "m-a", "score": 1, "reason": "x"}
            recomputed = modelhub.auto_orch_pick(fresh_s=0)
            m.assert_called_once()
        self.assertEqual(recomputed["provider_id"], "prov-2")


if __name__ == "__main__":
    unittest.main()
