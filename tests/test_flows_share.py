# -*- coding: utf-8 -*-
"""流程分享码（export_flow_code / import_flow_code）单测。

覆盖：导出码形状与白名单字段、往返保真、noop/updated/created 三态、
预置流程写 overrides、坏码（前缀/截断/种类）拒绝。
"""
from __future__ import annotations

import json
import unittest

from base import BaseTest

from app.core import flows


class TestFlowShare(BaseTest):

    def _upsert_podcast(self, **kw):
        payload = {"id": "podcast", "name": "播客脚本", "icon": "🎙", "engine": "review",
                   "manuscript": "script.md", "rubric": ["选题", "结构", "口语化"],
                   "threshold": 7.5, "rounds": 3, "note": "单集脚本产出"}
        payload.update(kw)
        flow, err = flows.upsert_flow(payload)
        self.assertIsNone(err, err)
        return flow

    def test_export_shape_and_roundtrip(self):
        self._upsert_podcast()
        code, err = flows.export_flow_code("podcast")
        self.assertIsNone(err, err)
        self.assertTrue(code.startswith("CBFLOW1."))
        # 码体是明文信封：解开后能看到 kind 与流程定义（无凭据类内容）
        raw = code[len("CBFLOW1."):]
        import base64
        payload = json.loads(base64.urlsafe_b64decode(raw + "=" * (-len(raw) % 4)))
        self.assertEqual(payload["kind"], "codebee-flow")
        self.assertNotIn("builtin", payload["flow"])

        flows.delete_flow("podcast")
        self.assertIsNone(flows.get_flow("podcast"))
        res, err = flows.import_flow_code(code)
        self.assertIsNone(err, err)
        self.assertEqual(res["status"], "created")
        f = flows.get_flow("podcast")
        self.assertEqual(f["threshold"], 7.5)
        self.assertEqual(f["rubric"], ["选题", "结构", "口语化"])
        self.assertEqual(f["rounds"], 3)
        self.assertFalse(f["builtin"])

    def test_import_noop_and_updated(self):
        self._upsert_podcast()
        code, _ = flows.export_flow_code("podcast")
        res, err = flows.import_flow_code(code)
        self.assertIsNone(err, err)
        self.assertEqual(res["status"], "noop")

        # 现有定义被改掉后再导入旧码 → updated，内容回到分享码版本
        self._upsert_podcast(threshold=9.0, name="播客脚本V2")
        res, err = flows.import_flow_code(code)
        self.assertIsNone(err, err)
        self.assertEqual(res["status"], "updated")
        self.assertEqual(flows.get_flow("podcast")["threshold"], 7.5)

    def test_import_builtin_writes_override(self):
        code, err = flows.export_flow_code("novel")
        self.assertIsNone(err, err)
        res, err = flows.import_flow_code(code)
        self.assertIsNone(err, err)
        self.assertEqual(res["status"], "noop")
        self.assertTrue(res["builtin"])

        # 对预置流程动过刀后，导入分享码 = 覆盖为分享码内容（可恢复默认）
        flows.upsert_flow({"id": "novel", "threshold": 9.9, "rubric": ["情节"]})
        self.assertEqual(flows.get_flow("novel")["threshold"], 9.9)
        res, err = flows.import_flow_code(code)
        self.assertIsNone(err, err)
        self.assertEqual(res["status"], "updated")
        self.assertNotEqual(flows.get_flow("novel")["threshold"], 9.9)
        self.assertIsNone(flows.reset_flow("novel"))

    def test_bad_codes_rejected(self):
        self._upsert_podcast()
        good, _ = flows.export_flow_code("podcast")
        cases = [
            "",                      # 空
            "random text",           # 无前缀
            good[:-8],               # 截断（base64 解不开或 JSON 残缺）
            "CBFLOW1." + good[len("CBFLOW1."):-2] + "xx",
        ]
        for bad in cases:
            res, err = flows.import_flow_code(bad)
            self.assertIsNone(res)
            self.assertTrue(err, "坏码必须报错：%r" % bad[:24])
        # 种类不对（前缀对、内容不是流程）
        import base64
        wrong_kind = "CBFLOW1." + base64.urlsafe_b64encode(
            json.dumps({"kind": "something-else"}).encode()).decode()
        res, err = flows.import_flow_code(wrong_kind)
        self.assertIsNone(res)
        self.assertIn("CodeBee 流程", err)
        # 种类对但缺流程 ID
        no_id = "CBFLOW1." + base64.urlsafe_b64encode(
            json.dumps({"kind": "codebee-flow", "flow": {"name": "x"}}).encode()).decode()
        res, err = flows.import_flow_code(no_id)
        self.assertIsNone(res)
        self.assertIn("ID", err)

    def test_export_unknown_flow(self):
        res, err = flows.export_flow_code("no-such")
        self.assertIsNone(res)
        self.assertEqual(err, "流程不存在")


if __name__ == "__main__":
    unittest.main()
