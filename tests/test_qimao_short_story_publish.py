# -*- coding: utf-8 -*-
"""七猫短故事建书发布链回归（2026-10-22 实机校准的纯逻辑面）。

实机校准记录：真建《短故事校准草稿》(book_id=13862961) 走通建书表单后删除，
请求体/编辑器路由/字数规则均已实抓。本文件锁定流程表形状、选择器契约、
book_id URL 提取与取值映射——浏览器链路本身由真机首跑校准，不在单测内。
"""
import re
import unittest

from app.core.publish import qimao
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


if __name__ == "__main__":
    unittest.main()
