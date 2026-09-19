# -*- coding: utf-8 -*-
"""运行结果群推送（notify，借鉴 agency-orchestrator --notify）单测。

跑法：python -m unittest discover -s tests -p "test_notify.py" -v
"""
from __future__ import annotations

import json

from base import BaseTest


class PayloadAdaptTests(BaseTest):

    def test_dingtalk_shape(self):
        from app.core import notify
        p = notify._payload_for("https://oapi.dingtalk.com/robot/send?access_token=x", "hi")
        self.assertEqual(p["msgtype"], "text")
        self.assertEqual(p["text"]["content"], "hi")

    def test_feishu_shape(self):
        from app.core import notify
        p = notify._payload_for("https://open.feishu.cn/open-apis/bot/v2/hook/x", "hi")
        self.assertEqual(p["msg_type"], "text")
        self.assertEqual(p["content"]["text"], "hi")

    def test_wecom_shape(self):
        from app.core import notify
        p = notify._payload_for("https://qyapi.weixin.qq.com/cgi-bin/webhook/send?key=x", "hi")
        self.assertEqual(p["msgtype"], "text")
        self.assertEqual(p["text"]["content"], "hi")

    def test_unknown_host_falls_back_to_dingtalk_shape(self):
        from app.core import notify
        p = notify._payload_for("https://n8n.self.host/webhook/x", "hi")
        self.assertEqual(p["text"]["content"], "hi")


class PushRunTests(BaseTest):

    def setUp(self):
        super().setUp()
        from app.core import settings as settings_mod
        self._orig_settings_file = settings_mod._FILE
        settings_mod._FILE = self.data_dir / "settings.json"   # 隔离：不碰真实设置

    def tearDown(self):
        from app.core import settings as settings_mod
        settings_mod._FILE = self._orig_settings_file
        super().tearDown()

    def _seed(self):
        from app.core import store
        task = store.create_task({"type": "direct", "title": "夜更两章", "goal": "g",
                                  "workdir": str(self.workdir)})
        run = store.create_run("orchestration", "夜更两章", task_id=task["id"])
        store.update_run(run["id"], status="done",
                         verdict={"overall": 8.8, "publishable": True,
                                  "rounds_used": 1, "scores": {"情节": 9.0},
                                  "threshold": 7.0})
        return task["id"], run["id"]

    def test_push_run_unknown_run_skips(self):
        from unittest import mock
        from app.core import notify, settings as settings_mod
        settings_mod.save({"notify_webhook": "https://oapi.dingtalk.com/robot?access_token=x"})
        sent = []
        with mock.patch.object(notify, "_post", side_effect=lambda h, t: sent.append(t) or True):
            self.assertFalse(notify.push_run("no-such-run"))
        self.assertEqual(sent, [])

    def test_push_run_sends_summary(self):
        from unittest import mock
        from app.core import notify, settings as settings_mod
        task_id, run_id = self._seed()
        settings_mod.save({"notify_webhook": "https://oapi.dingtalk.com/robot?access_token=x"})
        sent = []

        def fake_post(hook, text):
            sent.append(text)
            return True

        with mock.patch.object(notify, "_post", side_effect=fake_post):
            self.assertTrue(notify.push_run(run_id))
        self.assertTrue(sent)
        self.assertIn("夜更两章", sent[0])
        self.assertIn("完成", sent[0])
        self.assertIn("8.8", sent[0])

    def test_push_text_uses_curl_post(self):
        from unittest import mock
        from app.core import notify, runner, settings as settings_mod
        settings_mod.save({"notify_webhook": "https://oapi.dingtalk.com/robot?access_token=x"})
        recorded = []

        def fake_run(argv=None, **kw):
            recorded.append(argv)
            return {"ok": True}

        with mock.patch.object(runner, "run_process", side_effect=fake_run):
            self.assertTrue(notify.push_text("hello"))
        self.assertTrue(recorded and recorded[0][0] == "curl")
        self.assertTrue(any(a.startswith("{") for a in recorded[0]))


if __name__ == "__main__":
    unittest.main()
