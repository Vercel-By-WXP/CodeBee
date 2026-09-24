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
            "read_tags": ["重生", "重生", "  ", "打脸", "完全编造的标签"],
            "protagonist_1": "林晚照还会更长",
            "summary": "x" * 600,
        }, "fanqie", "女主复仇文")
        self.assertLessEqual(len(m["book_name"]), 15)
        self.assertEqual(m["signing_mode"], "连载模式")     # 非法值回默认（表单真实选项）
        self.assertEqual(m["target_reader"], "女频")        # goal 含「女」
        self.assertEqual(m["category"], "")                 # 假分类清空（宁缺勿错）
        self.assertEqual(m["legacy_read_tags"], ["重生", "打脸"])  # 旧字段并入+过滤
        self.assertEqual(m["tags_theme"], [])
        self.assertEqual(m["content_world"], [])            # 新内容标签组默认空
        self.assertEqual(m["protagonist_1"], "林晚照还会")
        self.assertLessEqual(len(m["summary"]), 500)

    def test_fanqie_content_tags_limits(self):
        # 内容标签四组：官方上限 情节4/情感2/人设4/世界观1，表外词过滤
        m = bookmeta._norm({
            "book_name": "《织焰》", "summary": "s", "target_reader": "女频",
            "content_plot": ["追妻火葬场", "全员重生", "编造情节", "修罗场", "多宝", "退婚"],
            "content_emotion": ["先婚后爱", "智性恋", "隐婚"],
            "content_character": ["满级大佬", "炮灰", "学霸", "神豪", "团宠"],
            "content_world": ["规则怪谈", "兽世"],   # 世界观最多 1，第二个被截
        }, "fanqie", "")
        self.assertEqual(len(m["content_plot"]), 4)
        self.assertNotIn("编造情节", m["content_plot"])
        self.assertEqual(len(m["content_emotion"]), 2)      # 截到上限 2
        self.assertEqual(len(m["content_character"]), 4)
        self.assertEqual(m["content_world"], ["规则怪谈"])   # 只留 1 个

    def test_fanqie_real_catalog(self):
        # 真实表内项可通过：女频分类「宫斗宅斗」、阅读标签三组各最多 2
        m = bookmeta._norm({
            "book_name": "《金枝》", "summary": "s", "target_reader": "女频",
            "signing_mode": "连载模式", "category": "宫斗宅斗",
            "tags_theme": ["古言权谋"], "tags_role": ["嫡女"],
            "tags_plot": ["重生", "追妻火葬场"],   # 「宅斗」不在番茄女频标签表，会被滤
        }, "fanqie", "女频宅斗")
        self.assertEqual(m["category"], "宫斗宅斗")
        self.assertEqual(m["tags_theme"], ["古言权谋"])
        self.assertEqual(m["tags_role"], ["嫡女"])
        self.assertEqual(m["tags_plot"], ["重生", "追妻火葬场"])
        # 男频/女频表隔离：男频分类在女频频段下不可用
        m2 = bookmeta._norm({"book_name": "x", "summary": "s", "target_reader": "女频",
                             "category": "战神赘婿"}, "fanqie", "")
        self.assertEqual(m2["category"], "")

    def test_qimao_cascade(self):
        from core import bookmeta_catalog as cat
        m = bookmeta._norm({"book_name": "x", "summary": "s", "target_reader": "女生",
                            "category_main": "古代言情", "category_sub": "宫闱宅斗"},
                           "qimao", "")
        self.assertEqual(m["category_main"], "古代言情")
        self.assertEqual(m["category_sub"], "宫闱宅斗")
        # 二级不属于所给一级 → 跨一级反查官方目录纠正（同为女生频道）
        m2 = bookmeta._norm({"book_name": "x", "summary": "s", "target_reader": "女生",
                             "category_main": "古代言情", "category_sub": "总裁豪门"},
                            "qimao", "")
        self.assertEqual(m2["category_main"], "现代言情")
        self.assertEqual(m2["category_sub"], "总裁豪门")
        # 一级分类必须在真实表内，且按频道隔离：男生频道没有「古代言情」
        m3 = bookmeta._norm({"book_name": "x", "summary": "s", "target_reader": "男生",
                             "category_main": "古代言情"}, "qimao", "")
        self.assertEqual(m3["category_main"], "")
        m4 = bookmeta._norm({"book_name": "x", "summary": "s", "target_reader": "男生",
                             "category_main": "玄幻奇幻", "category_sub": "东方玄幻"},
                            "qimao", "")
        self.assertEqual((m4["category_main"], m4["category_sub"]), ("玄幻奇幻", "东方玄幻"))
        # 真案（2026-09-19 山里有人喊我）：一级空 + 女生专属二级「现实故事」
        # 配了男生频道 → 反查纠正频道与一级，三者自洽
        m5 = bookmeta._norm({"book_name": "x", "summary": "s", "target_reader": "男生",
                             "category_main": "", "category_sub": "现实故事"},
                            "qimao", "")
        self.assertEqual(m5["target_reader"], "女生")
        self.assertEqual(m5["category_main"], "现实主义")
        self.assertEqual(m5["category_sub"], "现实故事")
        # 全目录都没有的二级 → 清空，不硬填表单选不出的词
        m6 = bookmeta._norm({"book_name": "x", "summary": "s", "target_reader": "男生",
                             "category_main": "都市", "category_sub": "编造二级"},
                            "qimao", "")
        self.assertEqual(m6["category_sub"], "")
        self.assertEqual(m6["category_main"], "都市")
        self.assertEqual(len(cat.QIMAO_MAIN_CATEGORIES), 17)   # 男生11 + 女生6（官方全量）

    def test_qimao_four_tag_groups(self):
        # 四组标签：官方每组必选 1-3（过滤表外、截 3）；旧 tags 字段并入所属组
        m = bookmeta._norm({
            "book_name": "x", "summary": "s", "target_reader": "女生",
            "tags_style": ["甜宠", "编造风格", "爽文", "轻松"],   # 截到 3
            "tags_role": ["嫡女", "团宠"], "tags_plot": ["重生"], "tags_bg": ["古代"],
            "tags": ["女帝"],   # 旧字段并入角色组
        }, "qimao", "")
        self.assertEqual(m["tags_style"], ["甜宠", "爽文", "轻松"])
        self.assertEqual(m["tags_role"], ["嫡女", "团宠", "女帝"])
        self.assertEqual(m["tags_plot"], ["重生"])
        self.assertEqual(m["tags_bg"], ["古代"])

    def test_qimao_norm_defaults(self):
        m = bookmeta._norm({"summary": "s"}, "qimao", "男主都市流")
        self.assertEqual(m["target_reader"], "男生")
        self.assertEqual(m["status"], "连载中")
        self.assertEqual(m["book_name"], "")
        for k in ("tags_style", "tags_role", "tags_plot", "tags_bg"):
            self.assertEqual(m[k], [])

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

    def test_options_block_injects_real_tables(self):
        # 提示词选项块：按读者频段给对应表，且包含真实分类名
        blk_f = bookmeta._fq_options_block("女频宅斗文")
        self.assertIn("宫斗宅斗", blk_f)
        self.assertIn("古言权谋", blk_f)
        self.assertIn("先婚后爱", blk_f)      # 内容标签·情感组注入
        self.assertNotIn("战神赘婿", blk_f)   # 女频表不含男频分类
        blk_m = bookmeta._fq_options_block("男频都市异能")
        self.assertIn("都市高武", blk_m)
        self.assertIn("多女主", blk_m)
        blk_q = bookmeta._qm_options_block("女频古代言情")
        self.assertIn("现代言情", blk_q)
        self.assertIn("总裁豪门", blk_q)
        self.assertIn("必选1-3个", blk_q)
        # 频道隔离：女生频道不出现男生一级
        self.assertNotIn("玄幻奇幻", blk_q)


class TestValuesCreateBook(unittest.TestCase):
    """执行端 values 兜底：已落库的坏数据（一级空/频道错配）按官方目录对齐。

    真案 2026-09-19：任务 t-20260918-143825-5243 七猫 bookmeta 里
    category_main="" + category_sub="现实故事" + target_reader=男生，
    一级 click_text 静默跳过 → 死在「页面上找不到文本为『现实故事』的
    可点元素」。"""

    def test_fixes_real_broken_meta(self):
        from core.publish import qimao
        v = qimao.values_create_book({
            "book_name": "山里有人喊我", "target_reader": "男生",
            "category_main": "", "category_sub": "现实故事",
            "status": "连载中"})
        self.assertEqual(v["target_reader"], "女生")
        self.assertEqual(v["category_main"], "现实主义")
        self.assertEqual(v["category_sub"], "现实故事")

    def test_self_consistent_meta_untouched(self):
        from core.publish import qimao
        v = qimao.values_create_book({
            "book_name": "x", "target_reader": "男生",
            "category_main": "奇闻异事", "category_sub": "恐怖灵异"})
        self.assertEqual(v["target_reader"], "男生")
        self.assertEqual((v["category_main"], v["category_sub"]),
                         ("奇闻异事", "恐怖灵异"))

    def test_unknown_sub_cleared(self):
        from core.publish import qimao
        v = qimao.values_create_book({
            "book_name": "x", "target_reader": "男生",
            "category_main": "都市", "category_sub": "不存在的分类"})
        self.assertEqual(v["category_sub"], "")


class TestMarkdown(unittest.TestCase):
    def test_render_contains_fields(self):
        task = {"id": "t-1", "title": "测试书"}
        meta = bookmeta._norm({"book_name": "《测试》", "summary": "简介内容",
                               "category": "宫斗宅斗", "tags_plot": ["重生", "打脸"],
                               "content_world": ["规则怪谈"],
                               "target_reader": "女频"}, "fanqie", "")
        meta["source"] = "编排者(x)"
        md = bookmeta.render_markdown(task, "fanqie", meta)
        self.assertIn("作品信息（番茄）", md)
        self.assertIn("**作品名**：《测试》", md)
        self.assertIn("**主分类**：宫斗宅斗", md)
        self.assertIn("**情节标签**：重生、打脸", md)
        self.assertIn("**内容·世界观**：规则怪谈", md)
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

    def test_set_bumps_state_version(self):
        # 不 bump 则 SSE 存活的前端不拉新状态：卡片停在旧状态（点生成不翻、跑完也不翻）
        tid = "t-bmtest-000000-0004"
        with store.LOCK:
            store._TASKS[tid] = {"id": tid, "title": "x", "goal": "g", "status": "done"}
        ver0 = store.state_version()
        self.assertTrue(store.set_book_meta(tid, "qimao", {"status": "running"}))
        self.assertGreater(store.state_version(), ver0)


class TestRecoverOrphans(unittest.TestCase):
    """服务重启杀掉后台生成线程后，落盘 running 无人写终态：启动收尸改判 failed
    （不改判则前端永远「生成中」且按钮禁用，接口幂等拒绝，卡死不可自愈）。"""

    def test_running_to_failed_done_kept(self):
        tid = "t-bmtest-000000-0003"
        with store.LOCK:
            store._TASKS[tid] = {"id": tid, "title": "x", "goal": "g", "status": "done",
                                 "book_meta": {"fanqie": {"status": "running", "at": "x"},
                                               "qimao": {"status": "done",
                                                         "data": {"book_name": "n"}}}}
        n = bookmeta.recover_orphans()
        self.assertGreaterEqual(n, 1)
        bm = store.get_task(tid)["book_meta"]
        self.assertEqual(bm["fanqie"]["status"], "failed")
        self.assertIn("重启", bm["fanqie"]["error"])
        self.assertEqual(bm["qimao"]["status"], "done")   # 已完成的不误伤


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


class TestSummaryQuality(unittest.TestCase):
    """简介质量闸（2026-09-24）：番茄建书表单硬要求 50-500 字，模板兜底把
    目标首行（markdown 标题）当简介填进表单，提交被平台静默拒绝，流程只能
    误报「未登录或改版」——t-20260921 案。"""

    MATERIAL = ("她本是天之骄女，一朝跌落泥潭，成了人人可欺的弃子。"
                "三年蛰伏，她携满身医术归来，前夫跪求复合，仇人夜不能寐。"
                "这一次，她要亲手拿回属于自己的一切。")

    def test_md_plain_strips_markdown(self):
        self.assertEqual(bookmeta._md_plain("# 标题\n- 要点1\n> 引用"), "标题要点1引用")
        self.assertEqual(bookmeta._md_plain("正文**加粗**`码`"), "正文加粗码")
        self.assertEqual(bookmeta._md_plain("1. 首项\n2. 次项"), "首项次项")

    def test_summary_ok_threshold(self):
        self.assertFalse(bookmeta._summary_ok(
            "# 网文选题分析报告（基于七猫 + 番茄双平台榜单）"))
        self.assertFalse(bookmeta._summary_ok("（待补充）"))
        self.assertTrue(bookmeta._summary_ok("字" * 50))
        # 判定口径是「清洗后」：超长先被 _summary 截到 500，即合规
        self.assertTrue(bookmeta._summary_ok("字" * 501))
        self.assertEqual(len(bookmeta._prose_summary("字" * 600, "")), 500)

    def test_prose_summary_replaces_junk_from_material(self):
        junk = "# 网文选题分析报告（基于七猫 + 番茄双平台榜单）"
        out = bookmeta._prose_summary(junk, self.MATERIAL)
        self.assertGreaterEqual(len(out), 50)
        self.assertFalse(out.startswith("#"))
        self.assertIn("天之骄女", out)

    def test_prose_summary_keeps_good_text(self):
        good = "她本是天之骄女，一朝跌落泥潭。" * 5        # ≥50 字且无 markdown
        self.assertEqual(bookmeta._prose_summary(good, "别的素材"), good)

    def test_prose_summary_no_material_keeps_cur(self):
        junk = "# 短标题"
        self.assertEqual(bookmeta._prose_summary(junk, ""), junk)

    def test_fill_required_fixes_junk_summary(self):
        task = {"goal": "", "id": "t-x", "title": "书"}
        meta = {"summary": "# 标题行", "book_name": "书"}
        out = bookmeta._fill_required_fields(task, "fanqie", meta, {}, self.MATERIAL)
        self.assertGreaterEqual(len(out["summary"]), 50)
        self.assertFalse(out["summary"].startswith("#"))


if __name__ == "__main__":
    unittest.main(verbosity=2)
