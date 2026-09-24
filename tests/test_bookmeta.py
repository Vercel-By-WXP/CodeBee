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
        # 2026-09-24 起双频道全量注入 + 判定规则 + 跨频道禁令：单频道注入
        # 挡不住模型自带另一频道的分类知识（男频军事文被配成女生·权谋天下案）
        self.assertIn("男生频道", blk_q)
        self.assertIn("女生频道", blk_q)
        self.assertIn("严禁跨频道", blk_q)
        self.assertIn("架空历史", blk_q)


class TestChannelAnchor(unittest.TestCase):
    """频道题材信号锚（2026-09-24 实案：男频军事文《戍边骑奴》被生成端配成
    女生/古代言情/权谋天下——「权谋天下」只存在于女生频道，跨频道组合级联
    自洽，目录校验拦不住，只能靠题材信号判频道）。"""

    MILITARY_SUMMARY = ("现代特种兵王殉职雪崩，睁眼成了大乾北境最卑贱的骑奴，"
                        "只有编号七十三。他凭蹄印预言蛮骑夜袭救下全营，军功却"
                        "被军官冒领，反挨二十军棍。他以上现代特战本领与练兵之"
                        "道，雪谷设伏阵斩百夫长，从骑奴到将军，戍边抗蛮。")

    def _military_meta(self):
        return {"book_name": "戍边骑奴：我以军功镇山河",
                "target_reader": "女生", "category_main": "古代言情",
                "category_sub": "权谋天下",
                "tags_style": ["热血", "爽文", "正剧"],
                "tags_role": ["特种兵", "将军", "杀伐果断"],
                "tags_plot": ["穿越", "逆袭", "战争"],
                "tags_bg": ["架空历史", "古代", "历史"],
                "protagonist_1": "秦骁", "protagonist_2": "沈青梧",
                "status": "连载中", "summary": self.MILITARY_SUMMARY}

    def test_channel_anchor_unit(self):
        self.assertEqual(bookmeta._channel_anchor("军功练兵戍边"), "男生")
        self.assertEqual(bookmeta._channel_anchor("甜宠宅斗嫡女"), "女生")
        self.assertIsNone(bookmeta._channel_anchor("军功与甜宠并存"))   # 双侧不判
        self.assertIsNone(bookmeta._channel_anchor("平平无奇的日常"))   # 零信号不判
        self.assertIsNone(bookmeta._channel_anchor(""))

    def test_real_case_male_military_flipped(self):
        # 真案复刻：模型跨频道自造 女生/古代言情/权谋天下，信号单侧男生 →
        # 翻转 + 旧频道分类作废 + 素材补齐按男生频道重选（历史/架空历史）
        from core import bookmeta_catalog as cat
        task = {"goal": "帮我确定一个热门小说方向，帮我生成大纲",
                "book_meta": {"fanqie": {"status": "done",
                                         "data": {"target_reader": "男频"}}}}
        meta = self._military_meta()
        material = "架空历史大乾军旅 " + self.MILITARY_SUMMARY
        out = bookmeta._fill_required_fields(task, "qimao", meta, {}, material)
        self.assertEqual(out["target_reader"], "男生")
        self.assertEqual(out["category_main"], "历史")
        self.assertEqual(out["category_sub"], "架空历史")
        self.assertTrue(out.get("_fixes"))                    # 纠偏说明在场
        self.assertIn("题材信号", "；".join(out["_fixes"]))
        self.assertEqual(out["tags_role"], ["特种兵", "将军", "杀伐果断"])  # 标签不动

    def test_female_romance_kept(self):
        meta = {"book_name": "x", "target_reader": "女生",
                "category_main": "现代言情", "category_sub": "总裁豪门",
                "summary": "落魄千金闪婚豪门总裁，甜宠虐恋双重奏",
                "tags_style": ["甜宠"], "tags_role": ["女总裁"],
                "tags_plot": ["闪婚"], "tags_bg": ["都市"],
                "protagonist_1": "苏", "protagonist_2": "陆", "status": "连载中"}
        out = bookmeta._fill_required_fields({}, "qimao", meta, {}, meta["summary"])
        self.assertEqual(out["target_reader"], "女生")
        self.assertEqual((out["category_main"], out["category_sub"]),
                         ("现代言情", "总裁豪门"))
        self.assertNotIn("_fixes", out)                       # 信号与声称一致不纠

    def test_balanced_signals_no_flip(self):
        # 双侧命中（军旅+甜宠）→ 不判 → 保守保留模型声称，绝不反向错纠
        meta = {"book_name": "x", "target_reader": "女生",
                "category_main": "现代言情", "category_sub": "职场情缘",
                "summary": "特种兵退伍的她回乡开甜品店，军功章与甜宠日常",
                "tags_style": ["甜宠"], "tags_role": ["特种兵"],
                "tags_plot": ["逆袭"], "tags_bg": ["都市"],
                "protagonist_1": "甲", "protagonist_2": "乙", "status": "连载中"}
        out = bookmeta._fill_required_fields({}, "qimao", meta, {}, meta["summary"])
        self.assertEqual(out["target_reader"], "女生")
        self.assertNotIn("_fixes", out)

    def test_peer_platform_anchor(self):
        # 信号零命中时看同书另一平台已定的读者：番茄男频 → 七猫翻转男生
        from core import bookmeta_catalog as cat
        task = {"goal": "无信号的日常故事",
                "book_meta": {"fanqie": {"status": "done",
                                         "data": {"target_reader": "男频"}}}}
        meta = {"book_name": "x", "target_reader": "女生",
                "category_main": "古代言情", "category_sub": "权谋天下",
                "summary": "一个关于美食与日常的平淡故事",
                "tags_style": ["治愈"], "tags_role": ["医生"],
                "tags_plot": ["美食"], "tags_bg": ["都市"],
                "protagonist_1": "甲", "protagonist_2": "乙", "status": "连载中"}
        out = bookmeta._fill_required_fields(task, "qimao", meta, {}, "美食日常")
        self.assertEqual(out["target_reader"], "男生")
        self.assertIn("另一平台", "；".join(out.get("_fixes") or []))
        self.assertIn(out["category_main"], cat.QIMAO_CATS["男生"])  # 分类随频道重选
        self.assertIn(out["category_sub"],
                      cat.QIMAO_CATS["男生"][out["category_main"]])

    def test_apply_fix_note_pops_and_appends(self):
        meta = {"_fixes": ["频道纠偏：女生→男生（依据：题材信号）"], "x": 1}
        src = bookmeta._apply_fix_note(meta, "llm(a)")
        self.assertEqual(src, "llm(a)；频道纠偏：女生→男生（依据：题材信号）")
        self.assertNotIn("_fixes", meta)                       # 临时键随出随清
        self.assertEqual(bookmeta._apply_fix_note({"x": 1}, "s"), "s")

    def test_fanqie_category_fallback_channel_aware(self):
        # 《戍边骑奴》案：旧兜底不分频道，「戍边→古风世情」女频词男频素材
        # 选不中 → 全部落男频表首项「西方奇幻」。架空王朝军事文应兜到历史类
        task = {"goal": "", "id": "t-x", "title": "书"}
        meta = {"book_name": "书", "target_reader": "男频",
                "summary": "现代兵王成了大乾北境骑奴，以军功镇山河",
                "protagonist_1": "秦", "protagonist_2": "沈"}
        material = ("现代兵王殉职雪崩，成了大乾王朝北境军营的骑奴编号七十三，"
                    "军功练兵戍边，从骑奴到将军。")
        out = bookmeta._fill_required_fields(task, "fanqie", meta, {}, material)
        self.assertIn(out["category"], ("历史古代", "历史脑洞"))
        self.assertNotEqual(out["category"], "西方奇幻")
        # 女频素材兜女频词
        meta_f = {"book_name": "书", "target_reader": "女频",
                  "summary": "嫡女重生宅斗虐渣",
                  "protagonist_1": "甲", "protagonist_2": "乙"}
        out_f = bookmeta._fill_required_fields(task, "fanqie", meta_f, {},
                                               "古代深宅嫡女庶女宅斗")
        self.assertEqual(out_f["category"], "宫斗宅斗")


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


class TestRepairChannel(unittest.TestCase):
    """repair_existing 频道自愈：已落库的跨频道坏数据字段全齐（级联自洽），
    旧口径 required_ok 放行；靠题材信号识别后启动修复即翻转，且幂等。"""

    def test_wrong_channel_entry_repaired_and_idempotent(self):
        from core import bookmeta_catalog as cat
        tid = "t-bmtestchan-0001"
        bad = {"book_name": "戍边骑奴：我以军功镇山河", "target_reader": "女生",
               "category_main": "古代言情", "category_sub": "权谋天下",
               "tags_style": ["热血", "爽文"], "tags_role": ["特种兵", "将军"],
               "tags_plot": ["穿越", "战争"], "tags_bg": ["架空历史"],
               "protagonist_1": "秦骁", "protagonist_2": "沈青梧",
               "status": "连载中",
               "summary": ("现代特种兵王殉职雪崩，成了大乾北境最卑贱的骑奴。"
                           "军功被冒领反挨军棍，他凭特战本领与练兵之道，"
                           "雪谷设伏阵斩百夫长，从骑奴到将军，戍边抗蛮。")}
        with store.LOCK:
            store._TASKS[tid] = {"id": tid, "title": "书", "status": "done",
                                 "goal": "架空历史军旅题材长篇",
                                 "workdir": str(TMP / "wd-repair"),
                                 "serial": {"start_chapter": 1},
                                 "book_meta": {"qimao": {"status": "done",
                                                         "data": dict(bad),
                                                         "source": "llm(x)",
                                                         "at": "x"}}}
        n = bookmeta.repair_existing()
        self.assertGreaterEqual(n, 1)
        fixed = store.get_task(tid)["book_meta"]["qimao"]
        self.assertEqual(fixed["data"]["target_reader"], "男生")
        self.assertIn(fixed["data"]["category_main"], cat.QIMAO_CATS["男生"])
        self.assertIn(fixed["data"]["category_sub"],
                      cat.QIMAO_CATS["男生"][fixed["data"]["category_main"]])
        self.assertIn("频道纠偏", fixed["source"])
        # 幂等：修完再跑不重复改写（_fixes 临时键不落盘是前提）
        self.assertEqual(bookmeta.repair_existing(), 0)
        with store.LOCK:
            del store._TASKS[tid]


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
