# -*- coding: utf-8 -*-
"""路由健康扣分：未绑定 CLI 的默认上游已判死时不许继续霸榜。

2026-10-22 实案：codex-cli 在 modelhub 里没有绑定（binding_source=cli.default），
其 ~/.codex/config.toml 指向的 vsllm.cc（维云）已被健康台账判 down；评审选路
仍按能力基线 84 把它排第一，三轮都撞同一个 403，评审整轮失败。
"""
import unittest
from unittest import mock

from app.core import router


class UnboundDefaultUpstreamHealthTests(unittest.TestCase):
    def test_unbound_cli_with_dead_default_upstream_is_demoted(self):
        codex = {"id": "codex-cli", "kind": "codex", "mode": "real",
                 "_dispatch_task_type": "novel"}
        opencode = {"id": "opencode", "kind": "opencode", "mode": "real",
                    "_dispatch_task_type": "novel"}

        def upstreams(agent_id, **_kwargs):
            return ({"vsllm.cc"} if agent_id == "codex-cli"
                    else {"open.bigmodel.cn"})

        with mock.patch.object(router.modelhub, "_binding_for",
                               return_value={}), \
             mock.patch.object(router.modelhub, "providers",
                               return_value=[{"name": "维云模型开放平台",
                                              "base_url": "https://vsllm.cc/v1"}]), \
             mock.patch("app.core.health.down_names",
                        return_value={"维云模型开放平台"}), \
             mock.patch.object(router, "agent_upstreams",
                               side_effect=upstreams):
            codex_score, codex_reason = router.score(codex, "review", "novel", {})
            other_score, _ = router.score(opencode, "review", "novel", {})

        self.assertIn("默认上游", codex_reason)
        self.assertIn("健康故障", codex_reason)
        self.assertLess(codex_score, other_score)

    def test_healthy_default_upstream_keeps_previous_scoring(self):
        codex = {"id": "codex-cli", "kind": "codex", "mode": "real",
                 "_dispatch_task_type": "novel"}
        with mock.patch.object(router.modelhub, "_binding_for",
                               return_value={}), \
             mock.patch.object(router.modelhub, "providers",
                               return_value=[{"name": "维云模型开放平台",
                                              "base_url": "https://vsllm.cc/v1"}]), \
             mock.patch("app.core.health.down_names", return_value=set()), \
             mock.patch.object(router, "agent_upstreams",
                               return_value={"vsllm.cc"}):
            _, reason = router.score(codex, "review", "novel", {})
        self.assertNotIn("默认上游", reason)
        self.assertNotIn("绑定链为空", reason)

    def test_bound_cli_does_not_consult_default_upstream_health(self):
        codex = {"id": "codex-cli", "kind": "codex", "mode": "real",
                 "_dispatch_task_type": "novel"}
        pref = {"provider_id": "prov-36",
                "chain": [{"provider_id": "prov-36", "model": "glm-5.3"}]}
        binding = {"call_chain": [{"provider_id": "prov-36", "model": "glm-5.3"},
                                  {"provider_id": "prov-48", "model": "glm-4.6"}]}
        with mock.patch.object(router.modelhub, "_binding_for",
                               return_value=pref), \
             mock.patch.object(router.modelhub, "resolve_binding",
                               return_value=binding), \
             mock.patch("app.core.health.down_names",
                        return_value={"维云模型开放平台"}) as down, \
             mock.patch.object(router, "agent_upstreams",
                               return_value={"vsllm.cc"}):
            _, reason = router.score(codex, "review", "novel", {})
        self.assertIn("绑定链可用", reason)
        down.assert_not_called()


if __name__ == "__main__":
    unittest.main()
