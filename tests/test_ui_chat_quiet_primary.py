"""对话「重点 / 次要」分层的判据守测（2026-11-05 首次补）。

背景：`app/ui/chat-quiet.js` 是 11-04 用户「对话看起来眼睛痛，分一下重点和次要」
的落地层，500 行、整整一层交互，之前**零守测**。计划里那条「判据的样本回归」
写的是「补一个有成段智能体回复的 run 做正样本」——2026-11-05 用 CDP 在 4 个真实
任务上数了一遍（`scratch/cq-primary-audit.mjs` / `cq-substantive-measure.mjs`）：
1695 条 agent 行，**0 条**是「没有 .chat-step-meta 的成段正文」。原因是
app.js 的 stepMeta 表达式在 done 状态下补一句 t("完成")，恒非空。

所以「智能体在跟你说话」这个形态在产品里并不存在——对话时间线就是步骤流水，
正样本根本不存在，那条待办的前提是错的。这不影响分层结论（步骤全折是对的），
但说明判据成立与否取决于 app.js 的渲染规则：**只要哪天那里不再写 step-meta，
chat-quiet 就会把所有智能体输出当重点，分层整体失效且不会报错**。本文件把
这个跨文件前提也锁住。

chat-quiet.js 依赖 MutationObserver 与整套 DOM，无法单独 require——沿用
`tests/test_ui_chat_process_gate.py` 的做法，对源码切片做静态断言。
"""
from __future__ import annotations

import re
import unittest
from pathlib import Path

UI = Path(__file__).resolve().parents[1] / "app" / "ui"
QUIET_JS = UI / "chat-quiet.js"
APP_JS = UI / "app.js"


def _fn(name: str, path: Path = QUIET_JS) -> str:
    """取 chat-quiet.js 里 `  function name(` 到下一个同缩进 function 之间的源码。"""
    src = path.read_text(encoding="utf-8")
    start = src.index("\n  function %s(" % name)
    end = src.index("\n  function ", start + 1) if "\n  function " in src[start + 1:] else len(src)
    return src[start:end]


def _app_fn(name: str) -> str:
    src = APP_JS.read_text(encoding="utf-8")
    start = src.index("\nfunction %s(" % name)
    end = src.index("\nfunction ", start + 1)
    return src[start:end]


class PrimaryJudgementTests(unittest.TestCase):
    """`isPrimary()`：哪些行必须留在主线外面。都是「认错就丢东西」的方向。"""

    def setUp(self):
        self.src = _fn("isPrimary")

    def test_result_cards_are_primary(self):
        for cls in ("chat-fail-card", "chat-review-card", "chat-result", "chat-change-row"):
            self.assertIn(cls, self.src, "%s 必须判重点（折不折它都是在吞结果）" % cls)
        # 四条必须写在「返回 false」之前——顺序反了等于白写
        first_false = self.src.index("return false")
        for cls in ("chat-fail-card", "chat-review-card", "chat-result", "chat-change-row"):
            self.assertLess(self.src.index(cls), first_false,
                            "%s 的判据落在 return false 之后，永远走不到" % cls)

    def test_running_row_is_primary(self):
        # 正在跑的那条是用户唯一的实时窗口，折起来等于把进度藏了
        self.assertIn('if (b.classList.contains("is-running")) return true', self.src)

    def test_my_own_message_is_primary(self):
        self.assertIn('if (c.contains("me")) return true', self.src)

    def test_unknown_block_kept(self):
        # 不认识的块一律留：「宁可多留一行，不误吞结果」
        self.assertIn("if (!c.contains(\"chat-row\")) return true", self.src)

    def test_already_folded_is_idempotent(self):
        # 二次运行时已收好的段不能再套一层（每 1.2s 重绘都会重跑一次判据）
        self.assertIn('if (c.contains("cq-sec")) return true', self.src)

    def test_step_output_is_secondary(self):
        # 步骤行（气泡直属 step-meta）= 次要。这是本层的主判据，也是
        # `app.js` 那个恒非空前提所覆盖的那一批。
        self.assertIn('if (b.querySelector(":scope > .chat-step-meta")) return false', self.src)


class FoldSecondaryTests(unittest.TestCase):
    def setUp(self):
        self.src = _fn("foldSecondary")

    def test_empty_guard_keeps_timeline(self):
        # 全是次要时不做：否则整条时间线缩成一行，等于什么都没了
        self.assertIn("if (!primary) return", self.src)

    def test_key_has_no_count(self):
        # key 里带条数 = 运行中每 1.2s 追加几条就换一次 key，已展开的段被重建 → 又闪一次
        key = [ln for ln in self.src.splitlines() if "const key = " in ln]
        self.assertTrue(key, "foldSecondary 里找不到 key 的构造")
        self.assertNotIn("length", key[0], "分段 key 不许含条数（会造成展开态被重建）")

    def test_single_ledger_not_wrapped_again(self):
        # 段里只有一条「已经收好的账本」→ 它本身就是一行摘要，别再套一层
        self.assertIn("if (rows.length === 1 && members.length === 1", self.src)


class LightKindTests(unittest.TestCase):
    """`lightKind()`：哪些行连「过程组」都不该进（真回复不能被压成一行）。"""

    def setUp(self):
        self.src = _fn("lightKind")

    def test_long_body_not_collapsed(self):
        # 主干成段（>60 字）或整段偏长（>140 字）就不合并——这是防止把
        # 章节正文/评审结论压成「第 1 章完成」那类一行的唯一一道闸。
        self.assertIn("if (core.length > 60 || txt.length > 140) return null", self.src)

    def test_structured_body_not_collapsed(self):
        self.assertIn("pre,table,ul,ol,blockquote", self.src)
        self.assertIn("querySelectorAll(\"p\").length > 2", self.src)

    def test_hard_cards_never_collapsed(self):
        for cls in ("chat-fail-card", "chat-review-card", "chat-result", "chat-change-card"):
            self.assertIn(cls, self.src)


class DupesOnlyErrShapeTests(unittest.TestCase):
    def test_dupes_require_err_shape(self):
        # 同因重试合并只认报错特征词：正常回复内容相似也绝不合并（宁可漏）
        src = _fn("dupKey")
        self.assertIn("ERR_SHAPE.test(txt)", src)
        self.assertIn("if (txt.length < 24 || !ERR_SHAPE.test(txt)) return \"\"", src)


class CrossFilePremiseTests(unittest.TestCase):
    """跨文件前提：app.js 给每个 agent 气泡都写 step-meta。

    2026-11-05 在 4 个真实任务 / 1695 条 agent 行上实测，带 step-meta 的
    100%（`scratch/cq-substantive-measure.mjs`）。chat-quiet 的整个「重点/次要」
    判据就立在这条事实上。**app.js 改了这里，分层语义会整体反转且不报错**——
    所以把它锁进守测，改的人必须先看到这条测试。
    """

    def test_step_meta_always_rendered(self):
        src = _app_fn("drawChatFlow")
        m = re.search(r"const stepMeta = \[(.*?)\]\.filter\(Boolean\)", src, re.S)
        self.assertIsNotNone(m, "找不到 stepMeta 的构造")
        body = m.group(1)
        # done（以及其它非失败终态）必须补「完成」二字，否则 stepMeta 可能是空串
        self.assertIn('t("完成")', body)
        self.assertIn('it.status === "failed" || it.status === "cancelled" ? ""', body)
        # 渲染处仍然条件插入 —— 条件在，但值恒非空
        self.assertIn("(stepMeta ? '<div class=\"chat-step-meta\">'", src)

    def test_clamp_bodies_uses_same_shape(self):
        # 长正文折叠（一行摘要 + 「查看输出」）与 isPrimary 同源判据，改一处必须对另一处
        src = _fn("clampBodies")
        self.assertIn('bubble.querySelector(":scope > .chat-step-meta")', src)


if __name__ == "__main__":
    unittest.main()
