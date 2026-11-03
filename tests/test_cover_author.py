# -*- coding: utf-8 -*-
"""封面署名作者 + 底部封面卡收敛（2026-11-03 用户实案）。

用户原话①：「这一块就不要了吧，上面已经有了」——建书面板底部的「封面图」卡
（状态章 + 生成中转圈那条）与顶部 hero 带的封面位/生成入口重复，违反注意力
守恒，整卡删除；covergen 后端链路保留，唯一入口是 heroCoverGen 弹窗。
用户原话②：「生成封面需要加上作者，作者需要用户输入，默认展示上一次输入的
作者」——确认弹窗带署名输入框，默认回显上次输入（后端落盘 cover_author.json），
随 POST /cover 提交、拼进提示词（除署名外画面仍不得出现任何文字）。
"""

import inspect
import os
import re
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app.core import covergen  # noqa: E402

UI = ROOT / "app" / "ui"


def _read(p):
    with open(p, "r", encoding="utf-8") as fh:
        return fh.read()


def _window_fn_body(src, name):
    m = re.search(r"(?m)^window\.%s\s*=\s*(?:async\s*)?function\s*\([^)]*\)\s*\{"
                  % re.escape(name), src)
    if not m:
        return None
    start = m.end()
    end = src.find("\n};", start)
    return src[start:end if end != -1 else len(src)]


class PromptAuthorTests(unittest.TestCase):

    def test_prompt_without_author_keeps_no_text_rule(self):
        p = covergen._cover_prompt({"title": "测试书"})
        self.assertIn("画面中不要出现任何文字", p)
        self.assertNotIn("署名", p)

    def test_prompt_with_author_signs_bottom_center(self):
        p = covergen._cover_prompt({"title": "测试书"}, " 鲁迅 ")
        self.assertIn("除作者署名外不要出现任何文字", p)
        self.assertIn("「鲁迅」", p)

    def test_custom_image_prompt_untouched_by_author(self):
        self.assertEqual(covergen._cover_prompt({"image_prompt": "我的画面"}, "鲁迅"),
                         "我的画面")

    def test_start_and_make_cover_accept_author(self):
        self.assertIn("author", inspect.signature(covergen.start).parameters)
        self.assertIn("author", inspect.signature(covergen.make_cover).parameters)
        self.assertIn("author", inspect.signature(covergen.generate_async).parameters)


class LastAuthorPersistenceTests(unittest.TestCase):

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self._orig = covergen._author_path
        covergen._author_path = lambda: Path(self.tmp.name) / "cover_author.json"

    def tearDown(self):
        covergen._author_path = self._orig
        self.tmp.cleanup()

    def test_roundtrip_strips(self):
        covergen._remember_author("  某某作者  ")
        self.assertEqual(covergen.last_author(), "某某作者")

    def test_empty_input_never_clears_previous(self):
        covergen._remember_author("某某作者")
        covergen._remember_author("")
        covergen._remember_author(None)
        self.assertEqual(covergen.last_author(), "某某作者")

    def test_missing_file_returns_empty(self):
        self.assertEqual(covergen.last_author(), "")


class BottomCoverCardRemovedTests(unittest.TestCase):

    def test_no_duplicate_cover_card_in_bookmeta(self):
        src = _read(UI / "app.js")
        # 底部封面卡的头（#i-book-open 图标 + 「封面图」标题）必须整体消失
        self.assertNotIn('<use href="#i-book-open"/></svg>\' + esc(t("封面图"))', src,
                         "建书面板里还有底部封面卡：与顶部 hero 带重复（用户「这一块就不要了吧」）")
        for dead in ("S.coverPrompt", "window.coverPreview",
                     "window.coverConfirm", "window.coverCancel"):
            self.assertNotIn(dead, src, "底部封面卡删了，内联预览处理器也该一并删掉：%s" % dead)

    def test_dead_cover_card_css_removed(self):
        css = _read(UI / "style.css")
        self.assertNotIn(".bm-cover-wrap", css)
        self.assertNotIn(".bm-cover-preview", css)


class CoverAuthorUITests(unittest.TestCase):

    def setUp(self):
        self.src = _read(UI / "app.js")

    def test_modal_has_author_input_defaulting_to_last_author(self):
        body = _window_fn_body(self.src, "heroCoverGen")
        self.assertIsNotNone(body, "heroCoverGen 不见了")
        self.assertIn('id="cover-author"', body, "确认弹窗缺作者署名输入框")
        self.assertIn("cover-ask-author", body)
        # 默认值 = 后端回显的上一次输入（r.author），会话内回退 S.lastCoverAuthor
        self.assertIn("(r && r.author) || author", body)
        self.assertIn("S.lastCoverAuthor", body)

    def test_confirm_reads_input_before_closing_modal(self):
        go = _window_fn_body(self.src, "heroCoverGo")
        self.assertIsNotNone(go, "heroCoverGo 不见了")
        self.assertLess(go.index('getElementById("cover-author")'),
                        go.index("closeModal()"),
                        "必须先读输入框再 closeModal：弹层拆掉后读到的永远是空串")
        self.assertIn("window.coverGen(taskId, author)", go, "署名要随生成请求一起提交")

    def test_cover_gen_posts_author(self):
        body = _window_fn_body(self.src, "coverGen")
        self.assertIsNotNone(body)
        self.assertIn('JSON.stringify({ author: author || "" })', body,
                      "POST /cover 必须带 author 字段")

    def test_backend_routes_wire_author(self):
        main = _read(ROOT / "app" / "main.py")
        self.assertIn("covergen.start(m.group(1), author=author)", main,
                      "POST /cover 路由要把 body 里的 author 传给 covergen.start")
        self.assertIn('"author": covergen.last_author()', main,
                      "GET /cover/prompt 要回显上一次输入的作者做默认值")

    def test_i18n_has_author_strings(self):
        i18n = _read(UI / "i18n.js")
        for key in ("作者署名", "显示在封面下方的作者名，可留空"):
            self.assertRegex(i18n, r'"%s":\s*"[^"]+"' % re.escape(key),
                             "缺少英文词条：%s" % key)

    def test_author_input_has_style(self):
        css = _read(UI / "style.css")
        self.assertIsNotNone(re.search(r"(?m)^\.cover-ask-author\s*\{", css),
                             "缺 .cover-ask-author 输入框样式")
        focus = re.search(r"(?m)^\.cover-ask-author:focus\s*\{([^}]*)\}", css)
        self.assertIsNotNone(focus, "缺 .cover-ask-author:focus 聚焦样式")
        self.assertIn("var(--accent)", focus.group(1),
                      "focus 聚焦环必须消费 --accent token，不许写死十六进制色")


if __name__ == "__main__":
    unittest.main()
