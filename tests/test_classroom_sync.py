# -*- coding: utf-8 -*-
"""作家课堂自动沉淀（classroom_sync）测试：SPA 解析夹具 / 同步流 / 守卫。

红线：检测零模型成本；提炼失败不记账下轮重试；模型判无干货记账消费；
单轮 ≤3 篇控成本；SSRF 校验拒绝非官方域。
"""
from __future__ import annotations

import json
from unittest import mock

from base import BaseTest

_LIST_HTML = (
    '{"ver_uri":"x","link":"https:\\u002F\\u002Ffanqienovel.com\\u002Fwriter'
    '\\u002Fzone\\u002Farticle\\u002F7672323371945689150","time":"2026-08-10",'
    '"type":5},{"id":2,"title":"占位甲","content":"c","cover":"",'
    '"link":"https:\\u002F\\u002Ffanqienovel.com\\u002Fwriter\\u002Fzone'
    '\\u002Farticle\\u002F7670381123347693593","time":"2026-08-01"}')

_ARTICLE_HTML = (
    '<html><script>window._ROUTER_DATA = {"loaderData":{"article.(id)'
    '\\u002Fpage":{"assess":0,"title":"全角标题","content":"<h1>一、规则</h1>'
    '<p>已签约&amp;总字数达到8万字。</p><li>连续3日更新</li>"}}};'
    '</script></html>')

_DISTILL_OK = {"ok": True, "text": '```json\n{"title": "推荐验证条件", '
               '"body": "已签约且总字数达到8万字，连续3日更新不低于4000字。"}\n```'}
_DISTILL_EMPTY = {"ok": True, "text": '```json\n{"title": "", "body": ""}\n```'}


class _FakeFetch:
    def __init__(self, arts, pages):
        self.arts = arts
        self.pages = pages
        self.fetched = []

    def list_articles(self):
        return [dict(a) for a in self.arts]

    def fetch_article_text(self, aid):
        self.fetched.append(aid)
        return self.pages.get(aid)


class TestClassroomParse(BaseTest):
    def test_parse(self):
        from app.core import classroom_sync as cs

        # 1) 列表解析：逃逸 id 提取 + 近邻标题/时间 + 去重保序
        arts = cs.parse_article_list(_LIST_HTML + _LIST_HTML)  # 重复去重
        self.assertEqual([a["id"] for a in arts],
                         ["7672323371945689150", "7670381123347693593"])
        self.assertEqual(arts[0]["time"], "2026-08-10")

        # 2) 正文解析：ROUTER_DATA 取 content + HTML 转纯文本（实体解码/结构保留）
        art = cs.parse_article_content(_ARTICLE_HTML)
        self.assertIsNotNone(art)
        self.assertEqual(art["title"], "全角标题")
        self.assertIn("# 一、规则", art["text"])
        self.assertIn("已签约&总字数达到8万字。", art["text"])
        self.assertIn("- 连续3日更新", art["text"])

        # 3) 垃圾输入返回 None
        self.assertIsNone(cs.parse_article_content("<html>shell</html>"))
        self.assertIsNone(cs.parse_article_content(
            '<script>window._ROUTER_DATA = {"loaderData":{}}</script>'))

    def test_get_host_pin(self):
        from app.core import classroom_sync as cs
        # SSRF 闸：非官方域/非 https 在出站前即拒（不触网）
        for bad in ("http://fanqienovel.com/x", "https://evil.example.com/x",
                    "https://127.0.0.1/x", "file:///etc/passwd"):
            with self.assertRaises(Exception):
                cs._get(bad)


class TestClassroomSyncFlow(BaseTest):
    def _arts(self):
        return [{"id": "a1", "title": "文一", "time": "2026-10-01"},
                {"id": "a2", "title": "文二", "time": "2026-10-02"},
                {"id": "a3", "title": "文三", "time": "2026-10-03"},
                {"id": "a4", "title": "文四", "time": "2026-10-04"}]

    def test_flow(self):
        from app.core import classroom_sync as cs, knowledge
        knowledge._FILE = self.data_dir / "knowledge.json"

        # 0) 未注入 kb：跳过
        self.assertEqual(cs.sync_once(force=True),
                         {"skipped": "no-kb"})

        fake = _FakeFetch(self._arts(), {"a1": {"title": "文一", "text": "正文一"},
                                         "a2": {"title": "文二", "text": "正文二"},
                                         "a3": {"title": "文三", "text": "正文三"},
                                         "a4": {"title": "文四", "text": "正文四"}})

        # 1) 首轮：4 篇新文章但单轮只提炼 3 篇（成本闸），全部入库
        ok1 = dict(_DISTILL_OK)
        ok2 = {"ok": True, "text": '```json\n{"title": "签约机会", '
                                '"body": "2万/5万/8万字各1次申请机会。"}\n```'}
        ok3 = {"ok": True, "text": '```json\n{"title": "全勤门槛", '
                                '"body": "过审满10万字的次月起才有全勤资格。"}\n```'}
        with mock.patch.object(cs.modelhub, "resolve_orchestrator",
                               return_value=({"id": "p"}, "m")), \
             mock.patch.object(cs.modelhub, "chat",
                               side_effect=[ok1, ok2, ok3]) as chat:
            r = cs.sync_once(force=True, kb=knowledge, fetch=fake)
        self.assertEqual(r["new"], 4)
        self.assertEqual(r["made"], 3)
        self.assertEqual(r["errors"], [])
        self.assertEqual(fake.fetched, ["a1", "a2", "a3"])
        self.assertEqual(chat.call_count, 3)
        entries = {x["title"]: x for x in knowledge.list_entries("serial_novel")}
        self.assertEqual(len(entries), 3)
        it = entries["番茄课堂｜推荐验证条件"]
        self.assertEqual(it["status"], "approved")
        self.assertEqual(it["source"], cs.FANQIE_ARTICLE_URL + "a1")
        self.assertEqual(it["as_of"], "2026-10-01")
        self.assertEqual(it["confidence"], "medium")
        self.assertIn("作家课堂", it["tags"])
        # 成功后 ok_at 记账，冷却生效
        st = json.loads((self.data_dir / "classroom_sync.json").read_text(encoding="utf-8"))
        self.assertIn("a1", st["synced"])
        self.assertGreater(st["ok_at"], 0)
        self.assertEqual(cs.sync_once(kb=knowledge, fetch=fake),
                         {"skipped": "cooldown"})

        # 2) 提炼失败：不记账，下轮重试同一批
        fake2 = _FakeFetch([{"id": "b1", "title": "文B", "time": ""}],
                           {"b1": {"title": "文B", "text": "正文B"}})
        with mock.patch.object(cs.modelhub, "resolve_orchestrator",
                               return_value=({"id": "p"}, "m")), \
             mock.patch.object(cs.modelhub, "chat", return_value={"ok": False}):
            r2 = cs.sync_once(force=True, kb=knowledge, fetch=fake2)
        self.assertEqual(r2["errors"], [{"id": "b1", "error": "distill"}])
        self.assertNotIn("b1", cs._load_state()["synced"])
        with mock.patch.object(cs.modelhub, "resolve_orchestrator",
                               return_value=({"id": "p"}, "m")), \
             mock.patch.object(cs.modelhub, "chat",
                               return_value={"ok": True, "text":
                                             '```json\n{"title": "定时发布规则", '
                                             '"body": "章节按顺序排队进审。"}\n```'}):
            r3 = cs.sync_once(force=True, kb=knowledge, fetch=fake2)
        self.assertEqual(r3["made"], 1)
        self.assertEqual(r3["errors"], [])

        # 3) 模型判「无硬规则」：记账消费，不入库
        fake3 = _FakeFetch([{"id": "c1", "title": "功能公告", "time": ""}],
                           {"c1": {"title": "功能公告", "text": "纯公告"}})
        with mock.patch.object(cs.modelhub, "resolve_orchestrator",
                               return_value=({"id": "p"}, "m")), \
             mock.patch.object(cs.modelhub, "chat",
                               return_value=dict(_DISTILL_EMPTY)):
            r4 = cs.sync_once(force=True, kb=knowledge, fetch=fake3)
        self.assertEqual(r4["made"], 0)
        self.assertEqual(r4["errors"], [])
        self.assertIn("c1", cs._load_state()["synced"])
        self.assertEqual(len(knowledge.list_entries("serial_novel")), 4)

        # 4) 列表拉取失败：报错且不刷 ok_at
        class _Boom:
            def list_articles(self):
                raise OSError("network down")

            def fetch_article_text(self, aid):
                return None
        r5 = cs.sync_once(force=True, kb=knowledge, fetch=_Boom())
        self.assertIn("list:", r5["error"])

    def test_daemon_kill_switch(self):
        from app.core import classroom_sync as cs
        # BaseTest 已设 TUTTI_CLASSROOM_SYNC=0：start_daemon 直接拒绝
        self.assertIsNone(cs.start_daemon(kb=object()))


if __name__ == "__main__":
    import unittest as _u
    _u.main()
