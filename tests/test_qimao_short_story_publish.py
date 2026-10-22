# -*- coding: utf-8 -*-
"""七猫短故事建书发布链回归（2026-10-22 实机校准的纯逻辑面）。

实机校准记录：真建《短故事校准草稿》(book_id=13862961) 走通建书表单后删除，
请求体/编辑器路由/字数规则均已实抓。本文件锁定流程表形状、选择器契约、
book_id URL 提取与取值映射——浏览器链路本身由真机首跑校准，不在单测内。
"""
import re
import unittest

from app.core.publish import fanqie, qimao
from app.core.publish.publish_browser import _flow_contract_ok


class QimaoShortStoryFlowTests(unittest.TestCase):
    def test_flow_table_registered_and_shaped(self):
        steps = qimao.FLOWS.get("create_short_story")
        self.assertIsInstance(steps, list) and self.assertTrue(steps)
        does = [s.get("do") for s in steps]
        self.assertIn("tags", does)                     # 标签弹层复用小说点选
        self.assertIn("submit", does)
        submit = next(s for s in steps if s.get("do") == "submit")
        self.assertEqual(submit.get("text"), "确认创建")
        self.assertIn("short_create_url", str(steps))
        # 实测锚点：qm-select 用 :has 定位（实机验证可用）、简介富文本唯一
        joined = " ".join(str(s.get("sel") or "") for s in steps)
        self.assertIn(".el-select:has(input[placeholder*='一级分类'])", joined)
        self.assertIn("contenteditable=true", joined)

    def test_flow_table_satisfies_contract(self):
        steps = qimao.FLOWS["create_short_story"]
        self.assertTrue(_flow_contract_ok("qimao", "create_short_story", steps))

    def test_config_carries_measured_facts(self):
        self.assertIn("book-manage/short", qimao.CONFIG["short_book_manage"])
        self.assertIn("short/information", qimao.CONFIG["short_create_url"])
        # 编辑器顶栏实测：正文最少 4000、最多 70000
        self.assertEqual(qimao.CONFIG["short_story_body_min_chars"], 4000)
        self.assertEqual(qimao.CONFIG["short_story_body_max_chars"], 70000)

    def test_book_id_url_re_extracts_all_entries(self):
        for url, want in [
            ("https://zuozhe.qimao.com/front/book-upload?id=13862961", "13862961"),
            ("https://zuozhe.qimao.com/front/book-manage/short/information?id=42", "42"),
            ("https://zuozhe.qimao.com/front/book-manage?next_id=&t=1", ""),
            ("https://writer.fanqie.com/book-info/777", "777"),
            ("https://zuozhe.qimao.com/front/book-manage/draft?id=111", "111"),
        ]:
            m = re.search(qimao.BOOK_ID_URL_RE, url)
            got = next((g for g in m.groups() if g), "") if m else ""
            self.assertEqual(got, want, url)

    def test_values_mapping(self):
        vals = qimao.values_create_short_story({
            "book_name": "镜中七秒", "summary": "简介", "target_reader": "女生",
            "category_main": "现代言情", "category_sub": "总裁豪门",
            "status": "连载中", "protagonist_1": "林晚"})
        self.assertEqual(vals, {"title": "镜中七秒", "summary": "简介",
                                "target_reader": "女生",
                                "category_main": "现代言情",
                                "category_sub": "总裁豪门"})

    def test_upload_short_story_draft_flow(self):
        steps = qimao.FLOWS.get("upload_short_story_draft")
        self.assertIsInstance(steps, list) and self.assertTrue(steps)
        does = [s.get("do") for s in steps]
        self.assertNotIn("tags", does)                  # 上传链无标签弹层
        fill = next(s for s in steps if s.get("key") == "chapter_body")
        self.assertIn("q-contenteditable", str(fill.get("sel")))
        # 无章节标题/序号字段：流程表不得出现 chapter_title/chapter_no 填充
        keys = {s.get("key") for s in steps}
        self.assertNotIn("chapter_title", keys)
        self.assertNotIn("chapter_no", keys)
        self.assertTrue(any(s.get("do") == "click_real"
                            and s.get("text") == "存为草稿" for s in steps))
        verify = next(s for s in steps if s.get("do") == "verify")
        self.assertIn("short_book_manage", str(verify.get("url")))
        self.assertTrue(_flow_contract_ok(
            "qimao", "upload_short_story_draft", steps))

    def test_reconcile_pair_exists(self):
        # 短故事对账走独立列表（book-manage/short），与小说版同契约
        self.assertTrue(callable(qimao.reconcile_short_story_book_id))
        self.assertTrue(callable(qimao.reconcile_book_id))
        src = qimao.resolve_book_id.__doc__ or ""
        self.assertIn("短故事", src)   # 撞名兜底两个列表都找


class FanqieShortStoryTests(unittest.TestCase):
    """番茄短故事链（2026-10-22 实机探测：一键建 publish-short/new，
    编辑器 ProseMirror + 名称 textarea，无独立建书表单）。"""

    def test_create_flow_one_shot_navigation(self):
        steps = fanqie.FLOWS["create_short_story"]
        joined = " ".join(str(s.get("url") or "") for s in steps)
        # 模板占位 + config 实值两头锁（改版删键/删步都能红）
        self.assertIn("{short_create_url}", joined)
        self.assertIn("publish-short/new", fanqie.CONFIG["short_create_url"])
        self.assertTrue(any(s.get("do") == "wait"
                            and "ProseMirror" in str(s.get("sel")) for s in steps))

    def test_upload_flow_fills_name_and_body(self):
        steps = fanqie.FLOWS["upload_short_story_draft"]
        keys = {s.get("key") for s in steps}
        self.assertIn("chapter_title", keys)
        self.assertIn("chapter_body", keys)
        body = next(s for s in steps if s.get("key") == "chapter_body")
        self.assertIn("ProseMirror", str(body.get("sel")))
        self.assertTrue(any(s.get("do") == "click_real"
                            and s.get("text") == "存草稿" for s in steps))
        self.assertTrue(_flow_contract_ok(
            "fanqie", "upload_short_story_draft", steps))

    def test_config_and_book_id_regex(self):
        self.assertIn("short-manage", fanqie.CONFIG["short_manage"])
        self.assertIn("publish-short/new", fanqie.CONFIG["short_create_url"])
        m = re.search(fanqie.BOOK_ID_URL_RE,
                      "https://fanqienovel.com/main/writer/publish-short/7699400853773353534")
        self.assertEqual(next(g for g in m.groups() if g),
                         "7699400853773353534")
        self.assertEqual(
            fanqie.short_story_editor_url({"book_id": "123"}),
            "https://fanqienovel.com/main/writer/publish-short/123")
        # 番茄无专用取值器 → publish_create 回落 values_create_book
        self.assertFalse(hasattr(fanqie, "values_create_short_story"))
        self.assertTrue(callable(fanqie.reconcile_short_story_book_id))

    def test_word_bounds_platform_split(self):
        # 七猫编辑器实抓 4000-70000；番茄未实测，先 1000-50000 首跑校准
        self.assertEqual(qimao.CONFIG["short_story_body_min_chars"], 4000)
        self.assertEqual(fanqie.CONFIG["short_story_body_min_chars"], 1000)
        self.assertEqual(fanqie.CONFIG["short_story_body_max_chars"], 50000)


class ShortStoryPublishGuardsTests(unittest.TestCase):
    """端到端实案回归：fresh 短篇任务曾被质量闸/番茄预检反向卡死建书。"""

    def test_quality_guard_skips_short_story(self):
        from app.core.publish import manager
        r = manager._quality_release_guard(
            {"id": "t-nonexistent", "type": "short_story", "engine": "review"},
            "qimao", action="publish")
        self.assertTrue(r["allowed"])

    def test_fanqie_preflight_skips_novel_fields_for_short(self):
        from app.core.publish.publish_create import _create_preflight
        self.assertEqual(_create_preflight("fanqie", {"book_name": "镜中七秒"},
                                           short_story=True), "")
        self.assertIn("作品简介", _create_preflight(
            "fanqie", {"book_name": "镜中七秒", "summary": "短"}, short_story=False))
        # 七猫短篇表单仍吃简介/分类/标签
        self.assertIn("作品简介", _create_preflight(
            "qimao", {"book_name": "镜中七秒"}, short_story=True))


if __name__ == "__main__":
    unittest.main()
