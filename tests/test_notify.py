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


class PersonalPushTests(BaseTest):
    """个人推送通道（Bark/ntfy/Server酱/Telegram）：路由、请求形状、隔离失败。"""

    def setUp(self):
        super().setUp()
        from app.core import settings as settings_mod
        self._orig_settings_file = settings_mod._FILE
        settings_mod._FILE = self.data_dir / "settings.json"

    def tearDown(self):
        from app.core import settings as settings_mod
        settings_mod._FILE = self._orig_settings_file
        super().tearDown()

    @staticmethod
    def _capture():
        """mock runner.run_process，按 (url, json body or form body) 记录。"""
        import json as _json
        from unittest import mock
        from app.core import runner
        calls = []

        def fake_run(argv=None, **kw):
            url = argv[-1]
            body = None
            if "-d" in argv:
                raw = argv[argv.index("-d") + 1]
                try:
                    body = _json.loads(raw)
                except Exception:
                    body = raw
            calls.append((url, body))
            return {"ok": True}

        return calls, mock.patch.object(runner, "run_process", side_effect=fake_run)

    def test_no_channels_returns_empty(self):
        from app.core import notify, settings as settings_mod
        settings_mod.save({})
        self.assertEqual(notify.push_text_ex("hi"), {})
        self.assertFalse(notify.push_text("hi"))

    def test_bark_and_telegram_shapes(self):
        from app.core import notify, settings as settings_mod
        settings_mod.save({"notify_bark_key": "bk123",
                           "notify_telegram_token": "T123", "notify_telegram_chat_id": "42"})
        calls, patcher = self._capture()
        with patcher:
            res = notify.push_text_ex("标题行\n正文第一段")
        self.assertEqual(set(res), {"bark", "telegram"})
        self.assertTrue(all(res.values()))
        urls = {c[0] for c in calls}
        self.assertTrue(any(u.startswith("https://api.day.app/push") for u in urls))
        self.assertTrue(any("api.telegram.org/botT123/sendMessage" in u for u in urls))
        bark = next(b for u, b in calls if "/push" in u)
        self.assertEqual(bark["device_key"], "bk123")
        self.assertEqual(bark["title"], "标题行")
        self.assertEqual(bark["body"], "正文第一段")
        tg = next(b for u, b in calls if "sendMessage" in u)
        self.assertEqual(tg["chat_id"], "42")
        self.assertIn("正文第一段", tg["text"])

    def test_bark_custom_server(self):
        from app.core import notify, settings as settings_mod
        settings_mod.save({"notify_bark_key": "k", "notify_bark_server": "https://bark.self.host/"})
        calls, patcher = self._capture()
        with patcher:
            notify.push_text_ex("hi")
        self.assertTrue(any(c[0] == "https://bark.self.host/push" for c in calls))

    def test_ntfy_json_publish_to_server_root(self):
        from app.core import notify, settings as settings_mod
        settings_mod.save({"notify_ntfy_topic": "https://ntfy.self.host/my-topic"})
        calls, patcher = self._capture()
        with patcher:
            res = notify.push_text_ex("标题\n内容")
        self.assertEqual(res, {"ntfy": True})
        url, body = calls[0]
        self.assertEqual(url, "https://ntfy.self.host/")
        self.assertEqual(body["topic"], "my-topic")
        self.assertEqual(body["title"], "标题")
        self.assertEqual(body["message"], "内容")

    def test_ntfy_bare_topic_defaults_official(self):
        from app.core import notify, settings as settings_mod
        settings_mod.save({"notify_ntfy_topic": "my-topic"})
        calls, patcher = self._capture()
        with patcher:
            notify.push_text_ex("hi")
        url, body = calls[0]
        self.assertEqual(url, "https://ntfy.sh/")
        self.assertEqual(body["topic"], "my-topic")

    def test_serverchan_form_urlencoded(self):
        from app.core import notify, settings as settings_mod
        settings_mod.save({"notify_serverchan_key": "SCT_x"})
        calls, patcher = self._capture()
        with patcher:
            notify.push_text_ex("标题\n正文")
        url, body = calls[0]
        self.assertEqual(url, "https://sctapi.ftqq.com/SCT_x.send")
        # 表单体按 application/x-www-form-urlencoded 编码（中文非裸吐），
        # urlencode 结果可被 parse_qs 无损还原
        self.assertNotIn("标题", body)
        from urllib.parse import parse_qs
        pairs = parse_qs(body)
        self.assertEqual(pairs["title"], ["标题"])
        self.assertEqual(pairs["desp"], ["正文"])

    def test_channel_failure_isolated(self):
        from unittest import mock
        from app.core import notify, settings as settings_mod
        settings_mod.save({"notify_bark_key": "k",
                           "notify_telegram_token": "T", "notify_telegram_chat_id": "1"})
        with mock.patch.object(notify, "_send", side_effect=[True, RuntimeError("net down")]):
            res = notify.push_text_ex("hi")
        self.assertEqual(res, {"bark": True, "telegram": False})
        self.assertTrue(notify.push_text("hi"))

    def test_push_run_works_without_webhook(self):
        """只有个人通道也应收到任务摘要（旧版无 webhook 即静默跳过）。"""
        from unittest import mock
        from app.core import notify, settings as settings_mod, store
        task = store.create_task({"type": "direct", "title": "夜更", "goal": "g",
                                  "workdir": str(self.workdir)})
        run = store.create_run("orchestration", "夜更", task_id=task["id"])
        store.update_run(run["id"], status="done")
        settings_mod.save({"notify_serverchan_key": "SCT_x"})
        with mock.patch.object(notify, "_send", return_value=True):
            self.assertTrue(notify.push_run(run["id"]))


if __name__ == "__main__":
    unittest.main()
