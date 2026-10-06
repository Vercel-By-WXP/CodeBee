# -*- coding: utf-8 -*-
"""本轮借鉴落地回归守卫（2026-10-05 16 时班第 3/4 步，两件均纯提示词指引）：
1. 评审深读指引（OpenCodeReview 借鉴）——CODE_REVIEW_PROMPT 不再要求「只基于变更
   内容」：diff 为主要依据，允许只读打开涉及文件核对上下文，无法读文件时回退 diff；
   「不要修改任何文件」约束保留。
2. 半成品收工提醒（agent-delegate 借鉴第三件）——评审要求检查 TODO/FIXME/占位实现/
   未接线的函数或配置，发现按半成品如实降档；纯提醒不改 pass 判定语义。
3. 封面提示词预览确认（2026-10-06 10 时班候选甲，drama-skills 借鉴③「先预览
   确认再生产」）——生成封面先只读展示提示词，确认后才走 POST /cover 付费链。

跑法：python -m unittest discover -s tests -p "test_borrow_round_regressions.py" -v
（或 cd tests && python -m unittest test_borrow_round_regressions -v）
"""
from __future__ import annotations

import sys
from pathlib import Path

from base import BaseTest

# 封面预览端点测试要 import app.main（其内部 from core import ...，需 app/ 入 path）
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "app"))


class ReviewDeepReadTests(BaseTest):
    """评审深读指引：旧句「请只基于下方提供的任务与变更内容进行评审」与评审步
    只读沙箱（可读不可写）的能力正面相抵，造成半盲评。"""

    def setUp(self):
        super().setUp()
        from app.core import pipeline
        self.prompt = pipeline.CODE_REVIEW_PROMPT

    def test_deep_read_guidance_present(self):
        """允许在只读前提下打开涉及文件及周边代码核对上下文。"""
        self.assertIn("如运行环境允许读取文件", self.prompt)
        self.assertIn("只读，不得修改任何文件", self.prompt)

    def test_diff_fallback_kept(self):
        """读不了文件的环境有明确回退口径：基于 diff 评审。"""
        self.assertIn("无法读文件时基于 diff 评审", self.prompt)

    def test_blind_review_phrase_removed(self):
        """与深读相抵的旧句不再出现。"""
        self.assertNotIn("请只基于下方提供的任务与变更内容进行评审", self.prompt)

    def test_no_write_constraint_kept(self):
        """评审员禁改文件的既有约束保留：身份句原文+深读只读括注两处都在。"""
        self.assertIn("你是代码评审员（不要修改任何文件）", self.prompt)
        self.assertIn("只读，不得修改任何文件", self.prompt)


class HalfDoneReminderTests(BaseTest):
    """半成品收工提醒：占位实现静态提醒进评审要求（此前链路无 TODO/FIXME 检查）。"""

    def setUp(self):
        super().setUp()
        from app.core import pipeline
        self.prompt = pipeline.CODE_REVIEW_PROMPT

    def test_reminder_present(self):
        """TODO/FIXME/占位实现/未接线逐项点名，发现按半成品降档。"""
        self.assertIn("半成品收工提醒", self.prompt)
        self.assertIn("TODO/FIXME", self.prompt)
        self.assertIn("占位实现", self.prompt)
        self.assertIn("未接线", self.prompt)
        self.assertIn("按半成品如实降档", self.prompt)

    def test_prompt_contract_intact(self):
        """既有 JSON 输出契约与占位符不被误伤（_run_review 渲染依赖）。"""
        for token in ("```json", '"pass"', "__GOAL__", "__VERIFY__", "__DIFF__"):
            self.assertIn(token, self.prompt, token)


class CoverPromptPreviewTests(BaseTest):
    """封面生成前提示词预览（2026-10-06 10 时班候选甲，drama-skills 借鉴③）：
    新增只读 GET /api/tasks/{id}/cover/prompt 返回与 covergen._cover_prompt
    一致的提示词（同 task 输入同内容），不触发任何模型/图像付费调用；
    既有 POST /api/tasks/{id}/cover 链路行为回归不变。"""

    def setUp(self):
        super().setUp()
        # 与端点同模块对象：main.py 的 handler 是 `from core import covergen`，
        # 断言侧也用 core.covergen——app.core.covergen 是同源码的另一模块副本，
        # 跨副本断言当前成立（纯函数）但会给未来 patch 埋反向假红
        from core import covergen
        self.covergen = covergen

    def _task(self, workdir=""):
        return {"id": "t-cover", "title": "星辰大海",
                "goal": "少年出海追梦的太空歌剧", "workdir": workdir}

    def _server(self):
        import threading
        from http.server import ThreadingHTTPServer

        from app import main
        server = ThreadingHTTPServer(("127.0.0.1", 0), main.Handler)
        server.daemon_threads = True
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        self.addCleanup(server.server_close)
        self.addCleanup(server.shutdown)
        return main, server.server_port

    def test_prompt_endpoint_matches_covergen_prompt(self):
        """优化前该路径 404 无路由；落地后只读端点与 _cover_prompt 同源同文。"""
        import json
        import urllib.request
        from unittest import mock

        main, port = self._server()
        task = self._task()
        with mock.patch.object(main.remote, "request_authed", return_value=True), \
             mock.patch.object(main.store, "get_task", return_value=task):
            with urllib.request.urlopen(
                    "http://127.0.0.1:%d/api/tasks/t-cover/cover/prompt" % port) as resp:
                payload = json.loads(resp.read().decode("utf-8"))

        self.assertTrue(payload["ok"])
        self.assertEqual(payload["prompt"], self.covergen._cover_prompt(task))

    def test_prompt_endpoint_404_for_missing_task(self):
        """任务不存在回 404，不落任何生成状态（cover_gen 不被预览触碰）。"""
        import urllib.error
        import urllib.request
        from unittest import mock

        main, port = self._server()
        with mock.patch.object(main.remote, "request_authed", return_value=True), \
             mock.patch.object(main.store, "get_task", return_value=None):
            with self.assertRaises(urllib.error.HTTPError) as ctx:
                urllib.request.urlopen(
                    "http://127.0.0.1:%d/api/tasks/t-none/cover/prompt" % port)

        self.assertEqual(ctx.exception.code, 404)

    def test_cover_prompt_fallback_without_chapters(self):
        """边界：单稿/未开写（无章节文件）回落题材+简介，不出现剧情片段。"""
        prompt = self.covergen._cover_prompt(self._task(workdir=str(self.workdir)))
        self.assertIn("竖版小说封面插画", prompt)
        self.assertIn("少年出海追梦的太空歌剧", prompt)
        self.assertNotIn("剧情片段", prompt)

    def test_cover_prompt_includes_chapter_facts(self):
        """正常：有章节时抽取剧情事实进提示词（封面元素来自真实剧情）。"""
        (self.workdir / "chapter-01.md").write_text(
            "# 第一章\n林远登上曙光号，舷窗外是木星环。\n甲板上的合金龙门架闪着冷光。\n",
            encoding="utf-8")
        prompt = self.covergen._cover_prompt(self._task(workdir=str(self.workdir)))
        self.assertIn("剧情片段", prompt)
        self.assertIn("曙光号", prompt)

    def test_make_cover_without_providers_fails_clean(self):
        """既有 POST 链回归锚点：无可用图像供应商时 make_cover 干净失败
        （failed entry 不抛不挂不落文件），预览端点不改变该行为。"""
        from unittest import mock

        with mock.patch.object(self.covergen, "_candidates", return_value=[]):
            entry = self.covergen.make_cover("r-x", self._task())

        self.assertEqual(entry["status"], "failed")
        self.assertIn("没有可用的 openai 协议图像接口", entry["error"])

    def test_flow_registry_untouched(self):
        """未选类型不受影响：本轮零类型注册表改动，18 型实数维持，
        封面预览不是新任务类型。"""
        from app.core import flows
        ids = {f["id"] for f in flows.BUILTIN_FLOWS}
        self.assertEqual(len(flows.BUILTIN_FLOWS), 18)
        self.assertNotIn("cover_prompt", ids)


class CoverPreviewFrontendContractTests(BaseTest):
    """前端二段确认接线契约（源码级确定性代理）。浏览器级 UI 测试入口登记：
    tests/ui_bookmeta.mjs、tests/ui_cover_inline.mjs（Edge headless CDP 家族，
    本环境不启动浏览器）；此处以源码契约锁三点——生成封面先走只读预览、
    确认才 POST /cover、取消零请求。"""

    @classmethod
    def setUpClass(cls):
        from pathlib import Path
        root = Path(__file__).resolve().parents[1]
        cls.app_js = (root / "app" / "ui" / "app.js").read_text(encoding="utf-8")
        cls.i18n_js = (root / "app" / "ui" / "i18n.js").read_text(encoding="utf-8")

    def test_generate_button_uses_readonly_preview_first(self):
        """「生成封面」按钮先调 coverPreview 只读拉提示词，不再直接 POST。"""
        self.assertIn('onclick="coverPreview(', self.app_js)
        self.assertIn("/cover/prompt", self.app_js)

    def test_post_cover_only_inside_covergen(self):
        """POST /cover 全文件仅 coverGen 一处；预览走 GET /cover/prompt。"""
        self.assertEqual(self.app_js.count('+ "/cover",'), 1)
        self.assertEqual(self.app_js.count("/cover/prompt"), 1)
        self.assertLess(self.app_js.index("window.coverGen"),
                        self.app_js.index('+ "/cover",'))
        self.assertLess(self.app_js.index("window.coverPreview"),
                        self.app_js.index("/cover/prompt"))

    def test_cancel_path_makes_no_request(self):
        """coverCancel 只清预览态重绘：函数体内无 api( 调用、不碰 coverGen。"""
        import re
        m = re.search(r"window\.coverCancel = function[\s\S]*?\n};", self.app_js)
        self.assertIsNotNone(m, "coverCancel 处理器缺失")
        self.assertNotIn("api(", m.group(0))
        self.assertNotIn("coverGen", m.group(0))

    def test_confirm_goes_through_existing_covergen(self):
        """coverConfirm 清预览态后走既有 coverGen（POST 链零改动复用）。"""
        import re
        m = re.search(r"window\.coverConfirm = function[\s\S]*?\n};", self.app_js)
        self.assertIsNotNone(m, "coverConfirm 处理器缺失")
        self.assertIn("window.coverGen(", m.group(0))

    def test_handlers_invalidate_detail_sig_before_render(self):
        """签名守卫回归锁（2026-10-06 评审 HIGH-1）：S.coverPrompt 不在
        drawTaskDetail/renderRunDetail 的数据签名里，三个处理器必须作废
        taskSig/runDetailSig 再 render，否则预览框不上屏、取消后残留
        （bmRename 注释记录过同陷阱）。"""
        import re
        h = re.search(r"function coverInvalidateDetail\(\) \{[\s\S]*?\n\}",
                      self.app_js)
        self.assertIsNotNone(h, "coverInvalidateDetail 助手缺失")
        self.assertIn('S.taskSig = ""', h.group(0))
        self.assertIn('S.runDetailSig = ""', h.group(0))
        for name in ("coverPreview", "coverConfirm", "coverCancel"):
            m = re.search(r"window\.%s = (?:async )?function[\s\S]*?\n};" % name,
                          self.app_js)
            self.assertIsNotNone(m, name + " 处理器缺失")
            self.assertIn("coverInvalidateDetail(", m.group(0), name)

    def test_new_ui_strings_have_en_entries(self):
        """新增 t() 词条在 i18n.js 有英文对照（英文界面不回退中文）。"""
        for key in ("确认生成",
                    "生成前请确认图像提示词（确认后才调用图像接口）：",
                    "封面提示词获取失败："):
            self.assertIn('"%s":' % key, self.i18n_js, key)


if __name__ == "__main__":
    import unittest
    unittest.main()
