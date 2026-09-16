# -*- coding: utf-8 -*-
"""作品信息一键生成（番茄/七猫）单测：字段归一化 / 模板兜底 / Markdown 归档 /
store.book_meta 读写。不碰真实 data/（全量套跑时 TUTTI_DATA 会被别的测试模块
抢设，落盘断言一律用 store 实际解析出的 paths.TASKS_DIR / 独立临时目录）。"""
import os
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
os.environ.setdefault("TUTTI_DATA", tempfile.mkdtemp(prefix="tutti-bm-"))
sys.path.insert(0, str(ROOT / "app"))

from core import bookmeta, paths, store  # noqa: E402

TMP = Path(tempfile.mkdtemp(prefix="tutti-bm-fixture-"))


class TestNeedsBookMeta(unittest.TestCase):
    """开书资料只属于「这本书」：续写批次不再出面板/不再生成。"""

    def test_first_batch_needs(self):
        self.assertTrue(bookmeta.needs_book_meta({"serial": {"chapters": 10}}))
        self.assertTrue(bookmeta.needs_book_meta({"serial": {"chapters": 10, "start_chapter": 1}}))

    def test_continue_batch_does_not_need(self):
        self.assertFalse(bookmeta.needs_book_meta(
            {"serial": {"chapters": 8, "start_chapter": 11}}))
        self.assertFalse(bookmeta.needs_book_meta(
            {"serial": {"chapters": 1, "start_chapter": 2}}))

    def test_non_serial_does_not_need(self):
        self.assertFalse(bookmeta.needs_book_meta({"type": "novel", "serial": None}))
        self.assertFalse(bookmeta.needs_book_meta({}))
        self.assertFalse(bookmeta.needs_book_meta(None))

    def test_bad_start_chapter_treated_as_first(self):
        # 脏数据不该把面板藏掉：解析失败按首批处理（保守放行）
        self.assertTrue(bookmeta.needs_book_meta({"serial": {"start_chapter": "abc"}}))


class TestNorm(unittest.TestCase):
    def test_fanqie_norm_clamps(self):
        m = bookmeta._norm({
            "book_name": "这是一个超过了十五个字限制的书名需要被截断它",
            "signing_mode": "不知道", "target_reader": "未知",
            "read_tags": ["都市", "都市", "  ", "异能", 123],
            "protagonist_1": "林晚照还会更长",
            "summary": "x" * 600,
        }, "fanqie", "女主复仇文")
        self.assertLessEqual(len(m["book_name"]), 15)
        self.assertEqual(m["signing_mode"], "连载模式")     # 非法值回默认
        self.assertEqual(m["target_reader"], "女频")        # goal 含「女」
        self.assertEqual(m["read_tags"], ["都市", "异能", "123"])  # 去重/去空/转字符串
        self.assertEqual(m["protagonist_1"], "林晚照还会")
        self.assertLessEqual(len(m["summary"]), 500)

    def test_qimao_norm_defaults(self):
        m = bookmeta._norm({"summary": "s"}, "qimao", "男主都市流")
        self.assertEqual(m["target_reader"], "男生")
        self.assertEqual(m["status"], "连载中")
        self.assertEqual(m["book_name"], "")
        self.assertEqual(m["tags"], [])

    def test_norm_rejects_garbage(self):
        self.assertIsNone(bookmeta._norm("not a dict", "fanqie"))
        self.assertIsNone(bookmeta._norm({"irrelevant": 1}, "fanqie"))
        m = bookmeta._norm({"book_name": "短名"}, "fanqie")  # 有名无简介可用
        self.assertEqual(m["book_name"], "短名")

    def test_template_meta_uses_outline_title(self):
        task = {"goal": "女频古代宅斗长篇", "title": "任务标题"}
        outline = {"book_title": "《金枝》"}
        m = bookmeta._template_meta(task, "fanqie", outline)
        self.assertEqual(m["book_name"], "《金枝》")
        self.assertEqual(m["target_reader"], "女频")
        m2 = bookmeta._template_meta({"goal": "g", "title": "t"}, "qimao", None)
        self.assertEqual(m2["status"], "连载中")


class TestMarkdown(unittest.TestCase):
    def test_render_contains_fields(self):
        task = {"id": "t-1", "title": "测试书"}
        meta = bookmeta._norm({"book_name": "《测试》", "summary": "简介内容",
                               "read_tags": ["都市", "异能"]}, "fanqie", "")
        meta["source"] = "编排者(x)"
        md = bookmeta.render_markdown(task, "fanqie", meta)
        self.assertIn("作品信息（番茄）", md)
        self.assertIn("**作品名**：《测试》", md)
        self.assertIn("**阅读标签**：都市、异能", md)
        self.assertIn("**主角名2**：（待补充）", md)
        self.assertIn("来源：编排者(x)", md)


class TestStoreBookMeta(unittest.TestCase):
    def test_set_and_roundtrip(self):
        tid = "t-bmtest-000000-0001"
        with store.LOCK:
            store._TASKS[tid] = {"id": tid, "title": "x", "goal": "g",
                                 "workdir": str(TMP / "wd"),
                                 "status": "done"}
        ok = store.set_book_meta(tid, "fanqie", {"status": "running"})
        self.assertTrue(ok)
        self.assertEqual(store.get_task(tid)["book_meta"]["fanqie"]["status"], "running")
        ok = store.set_book_meta(tid, "fanqie", {"status": "done", "data": {"book_name": "n"}})
        self.assertTrue(ok)
        t = store.get_task(tid)
        self.assertEqual(t["book_meta"]["fanqie"]["data"]["book_name"], "n")
        # 落盘可回放
        import json
        raw = json.loads((paths.TASKS_DIR / (tid + ".json"))
                         .read_text(encoding="utf-8"))
        self.assertEqual(raw["book_meta"]["fanqie"]["status"], "done")

    def test_set_missing_task(self):
        self.assertFalse(store.set_book_meta("t-nope-000000-9999", "fanqie", {}))


class TestCollectMaterial(unittest.TestCase):
    def test_collect_includes_bible_outline_chapter(self):
        data = TMP
        wd = data / "wd-bm"
        wd.mkdir(parents=True, exist_ok=True)
        (wd / "story-bible.md").write_text("# 圣经\n女主：阿禾。", encoding="utf-8")
        (wd / "chapter-001.md").write_bytes("第一章正文开头。".encode("gbk"))  # GBK 兼容防线
        tid = "t-bmtest-000000-0002"
        with store.LOCK:
            store._TASKS[tid] = {"id": tid, "title": "x", "goal": "写个长篇",
                                 "workdir": str(wd), "status": "done"}
        rid = "r-bmtest-000000-0001"
        # task_runs 读内存缓存（不扫盘），直接种 _RUNS
        with store.LOCK:
            store._RUNS[rid] = {"id": rid, "task_id": tid, "status": "done",
                                "outline": {"book_title": "《回响》",
                                            "chapters": [{"title": "开端", "beats": "冲突", "hook": "钩"}]}}
        material, outline = bookmeta._collect_material(store.get_task(tid))
        self.assertIn("写个长篇", material)
        self.assertIn("女主：阿禾", material)          # 圣经注入
        self.assertIn("《回响》", material)            # 大纲注入
        self.assertIn("第一章正文开头。", material)     # GBK 章稿读取防线
        self.assertEqual(outline.get("book_title"), "《回响》")


if __name__ == "__main__":
    unittest.main(verbosity=2)
