# -*- coding: utf-8 -*-
"""成功粘性（ok-pin）回归。

2026-11-05 台账实测（172 个带配置快照的连载 run）：51% 的书跨章换过模型、
46% 换过供应商，其中约七成漂移发生在上一章**成功之后**——不是失败降级，
是解析层每章自己抖（健康判定 down / 模型级熔断 / 难度重排）。

对连载这种长跑，换模型的代价不是失败（换后九成照样能跑），而是文风断裂，
台账测不出来。所以记「最近一次真正跑通的 (供应商, 模型)」，下次解析提到
链首并放过**瞬态**闸门；确定性死因（停用/删除/无密钥/协议不符）照旧拦，
整链失败即清。
"""
import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from app.core import modelhub

PROV_A = {"id": "prov-a", "name": "甲供应商", "enabled": True,
          "api_key": "sk-a", "protocol": "openai",
          "base_url": "https://a.example/v1", "model": "m1",
          "models": [{"name": "m1", "enabled": True}]}
PROV_B = {"id": "prov-b", "name": "乙供应商", "enabled": True,
          "api_key": "sk-b", "protocol": "openai",
          "base_url": "https://b.example/v1", "model": "mb",
          "models": [{"name": "mb", "enabled": True}]}
TARGET = "test-cli"

CHAIN = {"chain": [{"provider_id": "prov-a", "model": "m1"},
                   {"provider_id": "prov-b", "model": "mb"}]}


class _Base(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        root = Path(self._tmp.name).resolve()
        self.path = root / "models.json"
        # 先钉住 _FILE，再清粘性：落盘位置跟着 _FILE 走，顺序反了就会去动
        # 开发库/生产库里真实的 model_pin.json。
        self._file = mock.patch.object(modelhub, "_FILE", self.path)
        self._file.start()
        self.addCleanup(self._file.stop)
        modelhub.ok_pin_clear()
        modelhub.bind_scope("")      # contextvar 会跟着线程走，别让上一个用例串味
        self._env = mock.patch.dict(os.environ, {"TUTTI_MODEL_PIN": "1"})
        self._env.start()

    def tearDown(self):
        self._env.stop()
        modelhub.bind_scope("")
        modelhub.ok_pin_clear()
        self._tmp.cleanup()

    def _write_data(self, providers=None, chain=None):
        data = {"providers": providers if providers is not None else [PROV_A, PROV_B],
                "bindings": {TARGET: dict(chain if chain is not None else CHAIN)}}
        self.path.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")

    def _restart(self):
        """模拟进程重启：内存清空、磁盘留着。"""
        with modelhub._PIN["lock"]:
            modelhub._PIN["by_target"].clear()
            modelhub._PIN["loaded"] = False

    def _resolve(self, providers=None, down=(), fused=(), eval_ok=True):
        self._write_data(providers)
        fused_pairs = set(fused)

        def _is_model_down(provider, model):
            return (provider, model) in fused_pairs

        patchers = [
            mock.patch("app.core.health.down_names", return_value=set(down)),
            mock.patch("app.core.health.is_model_down", side_effect=_is_model_down),
            mock.patch.object(modelhub, "_evaluation_is_usable", return_value=eval_ok),
        ]
        for p in patchers:
            p.start()
            self.addCleanup(p.stop)
        return modelhub.resolve_binding(TARGET)


class TestOkPinHead(_Base):
    def test_pin_promotes_successful_entry_to_head(self):
        """乙供应商的 mb 上一章跑通过 → 下一章它排第一，尽管手工链里它是第二条。"""
        modelhub.note_success(TARGET, "prov-b", "mb")
        r = self._resolve()
        self.assertEqual(r["model"], "mb")
        self.assertEqual(r["provider"]["name"], "乙供应商")
        chain_ids = [e.get("provider_id") for e in r["call_chain"]]
        self.assertEqual(chain_ids[0], "prov-b")

    def test_no_pin_keeps_manual_order(self):
        r = self._resolve()
        self.assertEqual(r["model"], "m1")
        self.assertEqual([e.get("provider_id") for e in r["call_chain"]],
                         ["prov-a", "prov-b"])

    def test_pin_overrides_health_down(self):
        """上一章刚成功的那条不该被一段瞬态健康窗挤掉（这是七成漂移的来源）。"""
        modelhub.note_success(TARGET, "prov-b", "mb")
        r = self._resolve(down=("乙供应商",))
        self.assertEqual(r["model"], "mb")
        self.assertEqual(r["call_chain"][0].get("provider_id"), "prov-b")

    def test_pin_overrides_model_level_fuse(self):
        modelhub.note_success(TARGET, "prov-b", "mb")
        r = self._resolve(fused=(("乙供应商", "mb"),))
        self.assertEqual(r["model"], "mb")

    def test_non_pinned_entry_still_blocked_by_down(self):
        """粘性只救它自己那一条，别把另一家的健康问题一起放过。"""
        modelhub.note_success(TARGET, "prov-a", "m1")
        r = self._resolve(down=("乙供应商",))
        self.assertEqual([e.get("provider_id") for e in r["call_chain"]],
                         ["prov-a"])


class TestOkPinLifecycle(_Base):
    def test_failure_clears_pin(self):
        modelhub.note_success(TARGET, "prov-b", "mb")
        modelhub.note_failure(TARGET)
        r = self._resolve()
        self.assertEqual(r["model"], "m1")

    def test_pin_expires(self):
        modelhub.note_success(TARGET, "prov-b", "mb")
        later = modelhub.time.time() + modelhub._pin_ttl() + 60
        with mock.patch.object(modelhub.time, "time", return_value=later):
            r = self._resolve()
        self.assertEqual(r["model"], "m1")

    def test_switch_can_disable(self):
        modelhub.note_success(TARGET, "prov-b", "mb")
        with mock.patch.dict(os.environ, {"TUTTI_MODEL_PIN": "0"}):
            r = self._resolve()
        self.assertEqual(r["model"], "m1")

    def test_empty_target_is_ignored(self):
        modelhub.note_success("", "prov-b", "mb")
        self.assertEqual(modelhub._ok_pin(""), None)


class TestOkPinSafety(_Base):
    def test_pin_never_revives_disabled_provider(self):
        """确定性死因不放过：停用的供应商不能因为「上次成功过」被救活。"""
        modelhub.note_success(TARGET, "prov-b", "mb")
        disabled = dict(PROV_B, enabled=False)
        r = self._resolve(providers=[PROV_A, disabled])
        self.assertEqual(r["model"], "m1")
        self.assertEqual([e.get("provider_id") for e in r["call_chain"]],
                         ["prov-a"])

    def test_pin_never_revives_provider_without_key(self):
        modelhub.note_success(TARGET, "prov-b", "mb")
        nokey = dict(PROV_B, api_key="")
        r = self._resolve(providers=[PROV_A, nokey])
        self.assertEqual(r["model"], "m1")


class TestOkPinRecommend(_Base):
    """自动推荐路径（生产上多数 CLI 的 chain 是空的，全靠现算推荐）。

    这条路径不拦的话，pin 只在「有显式链」时才生效，等于对生产大半配置无效。
    """

    def _recommend(self, down=(), fused=()):
        self._write_data(chain={"chain": []})
        fused_pairs = set(fused)

        def _is_model_down(provider, model):
            return (provider, model) in fused_pairs

        patchers = [
            mock.patch("app.core.health.down_names", return_value=set(down)),
            mock.patch("app.core.health.is_model_down", side_effect=_is_model_down),
            mock.patch.object(modelhub, "_evaluation_is_usable", return_value=True),
            # 排序本身另有守测；这里固定按候选顺序，才能断言是 pin 而不是价格
            # 决定了链首。
            mock.patch("app.core.dispatch.rank_model_entries",
                       side_effect=lambda cands, *a, **kw: (list(cands), [])),
        ]
        for p in patchers:
            p.start()
            self.addCleanup(p.stop)
        return modelhub.recommend_binding(TARGET)

    def test_pin_promotes_in_recommend_path(self):
        modelhub.note_success(TARGET, "prov-b", "mb")
        r = self._recommend()
        self.assertEqual(r["model"], "mb")
        self.assertEqual(r["provider"]["name"], "乙供应商")

    def test_no_pin_keeps_rank_order(self):
        r = self._recommend()
        self.assertEqual(r["model"], "m1")

    def test_pin_survives_health_down_in_recommend(self):
        modelhub.note_success(TARGET, "prov-b", "mb")
        r = self._recommend(down=("乙供应商",))
        self.assertEqual(r["model"], "mb")

    def test_pin_does_not_revive_disabled_in_recommend(self):
        modelhub.note_success(TARGET, "prov-b", "mb")
        disabled = dict(PROV_B, enabled=False)
        data = {"providers": [PROV_A, disabled], "bindings": {TARGET: {"chain": []}}}
        self.path.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
        patchers = [
            mock.patch.object(modelhub, "_FILE", self.path),
            mock.patch("app.core.health.down_names", return_value=set()),
            mock.patch("app.core.health.is_model_down", return_value=False),
            mock.patch.object(modelhub, "_evaluation_is_usable", return_value=True),
            mock.patch("app.core.dispatch.rank_model_entries",
                       side_effect=lambda cands, *a, **kw: (list(cands), [])),
        ]
        for p in patchers:
            p.start()
            self.addCleanup(p.stop)
        r = modelhub.recommend_binding(TARGET)
        self.assertEqual(r["model"], "m1")


class TestOkPinPersist(_Base):
    """粘性必须活过重启。

    连载是跨天写的，而服务重启（部署、崩溃、用户关电脑）是必然事件；只放内存
    的话这条机制在真实使用中等于不存在，反事实估算里的收益一步都拿不到。
    """

    def test_success_survives_restart(self):
        modelhub.note_success(TARGET, "prov-b", "mb")
        self._restart()
        r = self._resolve()
        self.assertEqual(r["model"], "mb")
        self.assertEqual(r["call_chain"][0].get("provider_id"), "prov-b")

    def test_failure_clears_on_disk_too(self):
        modelhub.note_success(TARGET, "prov-b", "mb")
        modelhub.note_failure(TARGET)
        self._restart()
        self.assertEqual(self._resolve()["model"], "m1")

    def test_stale_entry_not_resurrected_from_disk(self):
        """落盘期间就过期的不许读回来：旧钉子比没有更糟。"""
        pin_file = self.path.parent / "model_pin.json"
        pin_file.write_text(json.dumps({"version": 1, "by_target": {
            TARGET: {"provider_id": "prov-b", "model": "mb",
                     "at": modelhub.time.time() - modelhub._pin_ttl() - 60}}}),
            encoding="utf-8")
        self._restart()
        self.assertEqual(self._resolve()["model"], "m1")

    def test_bad_file_degrades_to_no_pin(self):
        """坏文件只丢粘性，不许让解析层抛错（退回改动前的行为）。"""
        (self.path.parent / "model_pin.json").write_text("{not json", encoding="utf-8")
        self._restart()
        self.assertEqual(self._resolve()["model"], "m1")

    def test_corrupt_entry_shapes_ignored(self):
        (self.path.parent / "model_pin.json").write_text(json.dumps(
            {"version": 1, "by_target": {TARGET: "oops", "": {"provider_id": "x"}}}),
            encoding="utf-8")
        self._restart()
        self.assertEqual(self._resolve()["model"], "m1")


class TestOkPinTtlEvidence(_Base):
    def test_default_window_covers_real_chapter_gap(self):
        """605 次「上章成功后」的章间隔 p90=30.9 分、max=164 分（台账实测）。

        窗口若只有 30 分，约 10% 的换章会丢锁——守这条阈值，别把有效期调回去。
        """
        self.assertGreaterEqual(modelhub.OK_PIN_TTL_S, 3 * 3600)

    def test_env_can_extend(self):
        with mock.patch.dict(os.environ, {"TUTTI_MODEL_PIN_TTL_MIN": "1440"}):
            self.assertEqual(modelhub._pin_ttl(), 86400)

    def test_env_garbage_falls_back(self):
        for bad in ("", "abc", "0", "-5"):
            with mock.patch.dict(os.environ, {"TUTTI_MODEL_PIN_TTL_MIN": bad}):
                self.assertEqual(modelhub._pin_ttl(), modelhub.OK_PIN_TTL_S)


class TestManualBindingWins(_Base):
    """人手改绑优先于学到的粘性。

    没有这条，用户在界面把写手换掉后，下一章会被上一章的成功记录顶回去，
    表现为「改了没用」——比不换更让人失去信任。
    """

    def test_explicit_rebind_clears_pin(self):
        self._write_data()
        modelhub.note_success(TARGET, "prov-b", "mb")
        modelhub.set_binding(TARGET, chain=[{"provider_id": "prov-a", "model": "m1"}])
        self.assertIsNone(modelhub._ok_pin(TARGET))
        self.assertEqual(self._resolve()["model"], "m1")

    def test_idempotent_rewrite_keeps_pin(self):
        self._write_data()
        modelhub.note_success(TARGET, "prov-b", "mb")
        modelhub.set_binding(TARGET, chain=[{"provider_id": "prov-b", "model": "mb"}])
        self.assertIsNotNone(modelhub._ok_pin(TARGET))

    def test_clearing_chain_keeps_pin(self):
        """链清空 = 交给自动推荐，正是粘性该生效的场合。"""
        self._write_data()
        modelhub.note_success(TARGET, "prov-b", "mb")
        modelhub.set_binding(TARGET, chain=[])
        self.assertIsNotNone(modelhub._ok_pin(TARGET))


class TestOkPinScope(_Base):
    """钉位按「本」隔离。

    11-06 生产实测：四个活动 run 全部用 claude-code。若钉位只按 CLI 记，
    A 书刚记下甲、B 书记成乙，A 下一章读到乙——粘性直接变成漂移源。
    """

    def test_two_books_do_not_overwrite_each_other(self):
        self._write_data()
        modelhub.bind_scope("t-bookA")
        modelhub.note_success(TARGET, "prov-b", "mb")
        modelhub.bind_scope("t-bookB")
        modelhub.note_success(TARGET, "prov-a", "m1")
        # 各自回到自己的作用域，仍然各钉各的
        modelhub.bind_scope("t-bookA")
        self.assertEqual(modelhub._ok_pin(TARGET)["model"], "mb")
        modelhub.bind_scope("t-bookB")
        self.assertEqual(modelhub._ok_pin(TARGET)["model"], "m1")

    def test_scope_isolation_in_resolution(self):
        self._write_data()
        modelhub.bind_scope("t-bookA")
        modelhub.note_success(TARGET, "prov-b", "mb")
        modelhub.bind_scope("t-bookB")
        self.assertEqual(self._resolve()["model"], "m1")

    def test_scoped_pin_not_visible_outside_run(self):
        modelhub.bind_scope("t-bookA")
        modelhub.note_success(TARGET, "prov-b", "mb")
        modelhub.bind_scope("")
        self.assertIsNone(modelhub._ok_pin(TARGET))
        self.assertIsNotNone(modelhub._pin_any(TARGET))

    def test_manual_rebind_clears_every_scope(self):
        self._write_data()
        modelhub.bind_scope("t-bookA")
        modelhub.note_success(TARGET, "prov-b", "mb")
        modelhub.bind_scope("t-bookB")
        modelhub.note_success(TARGET, "prov-b", "mb")
        modelhub.bind_scope("")
        modelhub.set_binding(TARGET, chain=[{"provider_id": "prov-a", "model": "m1"}])
        self.assertIsNone(modelhub._pin_any(TARGET))

    def test_scope_ctx_restores(self):
        self._write_data()
        with modelhub.scope_ctx("t-bookA"):
            modelhub.note_success(TARGET, "prov-b", "mb")
            self.assertIsNotNone(modelhub._ok_pin(TARGET))
        self.assertIsNone(modelhub._ok_pin(TARGET))

    def test_scope_survives_restart(self):
        self._write_data()
        with modelhub.scope_ctx("t-bookA"):
            modelhub.note_success(TARGET, "prov-b", "mb")
        self._restart()
        with modelhub.scope_ctx("t-bookA"):
            self.assertEqual(self._resolve()["model"], "mb")


class TestOkPinWiring(_Base):
    """跨文件前提：pipeline 必须在步骤入口绑定作用域。

    `bind_scope` 是空实现也不会让本条失败以外的任何测试变红——所以只能锁源码。
    子线程不继承 contextvar，漏了这行就退回「按 CLI 共享钉位」。
    """

    @staticmethod
    def _body(name):
        src = Path("app/core/pipeline.py").read_text(encoding="utf-8")
        start = src.index("def %s(" % name)
        nxt = src.find("\ndef ", start + 10)
        return src[start:] if nxt < 0 else src[start:nxt]

    def test_run_step_binds_scope(self):
        self.assertIn("modelhub.bind_scope(", self._body("_run_step"))

    def test_execute_run_uses_scope_ctx(self):
        body = self._body("execute_run")
        self.assertIn("modelhub.scope_ctx(", body)
        self.assertIn("task_id", body)


class _StubRunner(object):
    """_note_binding_outcome 的记账口径（不跑真 CLI）。"""

    def test_success_records_pinned_entry(self):
        from app.core import runner
        out = {"ok": True, "model": "mb",
               "attempts": [{"ok": False, "model": "m1", "provider_id": "prov-a"},
                            {"ok": True, "model": "mb", "provider_id": "prov-b"}]}
        runner._note_binding_outcome({"id": TARGET}, out)
        rec = modelhub._ok_pin(TARGET)
        self.assertEqual(rec["provider_id"], "prov-b")
        self.assertEqual(rec["model"], "mb")

    def test_binding_source_wins_over_id(self):
        from app.core import runner
        out = {"ok": True, "model": "m1",
               "attempts": [{"ok": True, "model": "m1", "provider_id": "prov-a"}]}
        runner._note_binding_outcome({"id": "reviewer-shadow",
                                      "_binding_source": TARGET}, out)
        self.assertIsNotNone(modelhub._ok_pin(TARGET))
        self.assertIsNone(modelhub._ok_pin("reviewer-shadow"))

    def test_failure_clears(self):
        from app.core import runner
        modelhub.note_success(TARGET, "prov-b", "mb")
        runner._note_binding_outcome({"id": TARGET},
                                     {"ok": False, "error_code": "RATE_LIMIT"})
        self.assertIsNone(modelhub._ok_pin(TARGET))

    def test_cancel_does_not_clear(self):
        """取消不是模型的问题：别把一条刚证明能用的路径清掉。"""
        from app.core import runner
        modelhub.note_success(TARGET, "prov-b", "mb")
        runner._note_binding_outcome({"id": TARGET},
                                     {"ok": False, "cancelled": True,
                                      "raw": {"cancelled": True}})
        self.assertIsNotNone(modelhub._ok_pin(TARGET))


if __name__ == "__main__":
    unittest.main()
