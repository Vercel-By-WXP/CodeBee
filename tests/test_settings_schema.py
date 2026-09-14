# -*- coding: utf-8 -*-
"""settings_schema（4C schema 化设置）测试。
设计稿：docs/migration/05-model-seams.md §4C。
"""
from __future__ import annotations

import threading
from base import BaseTest


class SchemaBase(BaseTest):

    def setUp(self):
        super().setUp()
        from app.core import settings_schema as ss
        ss.init(data_dir=str(self.data_dir))

    def tearDown(self):
        from app.core import settings_schema as ss
        with ss._LOCK:
            ss._NAMESPACES.clear()
            ss._VALUES.clear()
            ss._REVISIONS.clear()
        super().tearDown()


class TestRegisterAndGet(SchemaBase):

    def test_register_fills_defaults(self):
        from app.core import settings_schema as ss
        ss.register_namespace("ns1", fields=[
            ss.FieldDef("a", "int", 3, clamp=(1, 6)),
            ss.FieldDef("b", "str", "x"),
        ])
        self.assertEqual(ss.get("ns1"), {"a": 3, "b": "x"})
        self.assertEqual(ss.get("ns1", "a"), 3)

    def test_duplicate_register_rejected(self):
        from app.core import settings_schema as ss
        ss.register_namespace("ns1", fields=[ss.FieldDef("a", "int", 1)])
        try:
            ss.register_namespace("ns1", fields=[ss.FieldDef("a", "int", 2)])
            self.fail("重复注册应被拒绝")
        except ValueError:
            pass

    def test_get_unknown_ns(self):
        from app.core import settings_schema as ss
        self.assertIsNone(ss.get("nonexistent"))


class TestMutateCAS(SchemaBase):

    def _reg(self):
        from app.core import settings_schema as ss
        ss.register_namespace("ns", fields=[
            ss.FieldDef("n", "int", 3, clamp=(1, 6)),
            ss.FieldDef("mode", "str", "auto", choices=("auto", "manual")),
            ss.FieldDef("key", "str", "", redact=True),
        ])

    def test_mutate_set_and_revision_bump(self):
        from app.core import settings_schema as ss
        self._reg()
        rev = ss.mutate("ns", [{"op": "set", "path": "n", "value": 5}])
        self.assertEqual(rev, ss.revision("ns"))
        self.assertEqual(ss.get("ns", "n"), 5)
        self.assertGreater(rev, 1)

    def test_mutate_coerces_and_clamps(self):
        from app.core import settings_schema as ss
        self._reg()
        ss.mutate("ns", [{"op": "set", "path": "n", "value": "99"}])
        self.assertEqual(ss.get("ns", "n"), 6)  # 字符串转 int + 钳到上限

    def test_mutate_bad_value_rejected_no_partial_write(self):
        from app.core import settings_schema as ss
        self._reg()
        rev0 = ss.revision("ns")
        try:
            ss.mutate("ns", [
                {"op": "set", "path": "n", "value": 4},
                {"op": "set", "path": "mode", "value": "bogus"},
            ])
            self.fail("非法 choices 应被拒绝")
        except ValueError:
            pass
        # 部分失败不落盘
        self.assertEqual(ss.get("ns", "n"), 3)
        self.assertEqual(ss.revision("ns"), rev0)

    def test_mutate_unregistered_path_rejected(self):
        from app.core import settings_schema as ss
        self._reg()
        try:
            ss.mutate("ns", [{"op": "set", "path": "hack", "value": 1}])
            self.fail("未注册字段应被拒绝")
        except ValueError:
            pass

    def test_cas_success(self):
        from app.core import settings_schema as ss
        self._reg()
        rev = ss.revision("ns")
        ss.mutate("ns", [{"op": "set", "path": "n", "value": 4}],
                  expected_revision=rev)
        self.assertEqual(ss.get("ns", "n"), 4)

    def test_cas_stale_rejected(self):
        from app.core import settings_schema as ss
        self._reg()
        rev0 = ss.revision("ns")
        ss.mutate("ns", [{"op": "set", "path": "n", "value": 4}])
        try:
            ss.mutate("ns", [{"op": "set", "path": "n", "value": 5}],
                      expected_revision=rev0)  # 陈旧
            self.fail("陈旧 revision 应被拒绝")
        except ss.SettingsConflictError:
            pass
        self.assertEqual(ss.get("ns", "n"), 4)


class TestDescribeRedact(SchemaBase):

    def test_redact_hides_secret(self):
        from app.core import settings_schema as ss
        ss.register_namespace("cred", fields=[
            ss.FieldDef("api_key", "str", "", redact=True),
            ss.FieldDef("plain", "str", "visible"),
        ])
        ss.mutate("cred", [{"op": "set", "path": "api_key", "value": "sk-real-secret"}])
        d = ss.describe("cred")
        self.assertEqual(d["values"]["api_key"], "__REDACTED__")
        self.assertEqual(d["values"]["plain"], "visible")
        # 关闭脱敏可看到（仅内部用）
        d2 = ss.describe("cred", redact_secrets=False)
        self.assertEqual(d2["values"]["api_key"], "sk-real-secret")

    def test_redact_keeps_empty_visible(self):
        from app.core import settings_schema as ss
        ss.register_namespace("cred", fields=[
            ss.FieldDef("api_key", "str", "", redact=True),
        ])
        d = ss.describe("cred")
        self.assertEqual(d["values"]["api_key"], "")  # 未配置保持空（UI 可判断"未设置"）


class TestPersistence(SchemaBase):

    def test_reload_keeps_values_and_revisions(self):
        from app.core import settings_schema as ss
        ss.register_namespace("ns", fields=[ss.FieldDef("n", "int", 1)])
        ss.mutate("ns", [{"op": "set", "path": "n", "value": 7}])
        rev = ss.revision("ns")
        ss.init(data_dir=str(self.data_dir))  # 模拟重启
        ss.register_namespace("ns", fields=[ss.FieldDef("n", "int", 1)])
        self.assertEqual(ss.get("ns", "n"), 7)
        self.assertEqual(ss.revision("ns"), rev)


class TestDefaultNamespaces(BaseTest):

    def test_register_default_idempotent(self):
        from app.core import settings_schema as ss
        ss.init(data_dir=str(self.data_dir))
        ss.register_default_namespaces()
        ss.register_default_namespaces()  # 第二次不应抛
        vals = ss.describe("orchestrator")["values"]
        self.assertIn("compaction", vals)
        self.assertIn("enabled", vals["compaction"])
        self.assertEqual(ss.get("orchestrator", "compaction.pressure_threshold"), 0.8)

    def test_threshold_clamped(self):
        from app.core import settings_schema as ss
        ss.init(data_dir=str(self.data_dir))
        ss.register_default_namespaces()
        ss.mutate("orchestrator", [
            {"op": "set", "path": "compaction.pressure_threshold", "value": 5}])
        self.assertEqual(ss.get("orchestrator", "compaction.pressure_threshold"), 0.99)


class TestConcurrency(SchemaBase):

    def test_concurrent_cas_one_winner(self):
        from app.core import settings_schema as ss
        ss.init(data_dir=str(self.data_dir))
        ss.register_namespace("ns", fields=[ss.FieldDef("n", "int", 0)])
        rev = ss.revision("ns")
        results = []

        def worker():
            try:
                ss.mutate("ns", [{"op": "set", "path": "n", "value": 1}],
                          expected_revision=rev)
                results.append("ok")
            except ss.SettingsConflictError:
                results.append("stale")

        threads = [threading.Thread(target=worker) for _ in range(4)]
        for t in threads:
            t.start()
        for t in threads:
            t.join(timeout=5)
        self.assertEqual(results.count("ok"), 1)
        self.assertEqual(results.count("stale"), 3)