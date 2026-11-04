# -*- coding: utf-8 -*-
"""桌面端 `-webkit-line-clamp` 绝迹守测。

**为什么有这个文件**：Edge 147 起 `display: -webkit-box` 被弃用（计算值落
`flow-root`），`-webkit-line-clamp` 随之失效，而标准 `line-clamp` 尚未支持。
失效之后不会报错，也不会有省略号——文本只是**被硬切一半**（半个字、半行），
比不做截断更难看，而且不同浏览器版本表现不一致，肉眼验收很容易漏过去。

手机壳内 4 处已于 2026-11-04 21:15 换 `max-height` 兜底；桌面端还有二十多处
同病，本轮一次收口（`grep -rn "webkit-line-clamp" app/ui/*.css` → 只剩注释）。

守四件事：
1. 剥掉注释后，任何 css / html 里都不许再出现 `-webkit-line-clamp` / `-webkit-box`；
2. 每个被收口的选择器必须**真的**剪裁（`overflow: hidden`）——只删 clamp 不补
   max-height 会让长文案把卡片撑高，那是另一种坏；
3. 多行截断写 **`Nlh`**（N 个行高），不许写手算的 `em`。这是本轮真踩出来的三条：
   `em` 的基准是元素自己的 font-size，而行高可能继承自父级 px
   （`.auto-tpl-card .note` 算成 3.19 行）、可能是固定 px（`.a-nc-body` 56px
   切在 3.23 行）、元素还可能自带上下 padding（全局 `* { box-sizing: border-box }`
   让 max-height 含 padding，`.avail-row-err` 3em 里只剩 1.28 行内容）。
   `lh` 恒等于一个行高，`2lh` 就是两行；
4. **单行**截断不写 max-height，写 `nowrap + ellipsis`——那是 `-webkit-line-clamp`
   失效后唯一还能出省略号的情形（多行没有等价物，只能硬切）。
"""
from __future__ import annotations

import re
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
UI = ROOT / "app" / "ui"


def _text(name: str) -> str:
    return (UI / name).read_text(encoding="utf-8")


def _rules_only(name: str) -> str:
    """剥掉注释再断言：注释里写「不要用 -webkit-line-clamp」不该把守测弄红。"""
    return re.sub(r"/\*.*?\*/", "", _text(name), flags=re.S)


def _blocks(text: str, selector: str):
    """取出 selector 对应的声明块（`{ ... }` 之间的内容），可能不止一处。"""
    out = []
    i = 0
    while True:
        i = text.find(selector, i)
        if i < 0:
            return out
        j = text.find("{", i + len(selector))
        k = text.find("}", j + 1) if j >= 0 else -1
        if j >= 0 and k > j:
            out.append(text[j + 1:k])
        i += len(selector)


# (文件, 选择器, 期望, 几行 / 为什么)；期望为 "Nlh" 或 "nowrap"
CAPS = [
    ("activity.css", ".activity-summary", "2lh", "活动摘要两行"),
    ("atlas.css", ".a-nc-body", "3lh", "星图节点正文三行（原来写死 56px，实际切在 3.23 行）"),
    ("board.css", ".c-goal", "2lh", "看板卡目标两行"),
    ("board.css", ".d-many .c-goal", "nowrap", "满负荷下只留一行，带省略号"),
    ("board.css", ".d-solo .c-goal", "3lh", "单任务放大到三行"),
    ("demo-parity.css", '#rd-meta-popover .rd-overview-title', "2lh", "浮层里的运行标题两行"),
    ("desktop-workbench.css", "#rd-overview .rd-overview-goal", "2lh", "浮层里的目标两行"),
    ("expert-cards.css", "#sub-market .exp-row p", "nowrap", "市场专家行描述一行，带省略号"),
    ("market-v2.css", "#sub-market .mk-tile p", "2lh", "市场卡片描述两行（min-height 同值保等高）"),
    ("market-v4.css", "#sub-market .mk-tile p", "nowrap", "市场卡片描述一行，带省略号"),
    ("market-v4.css", "#sub-market .exp-row p", "nowrap", "市场专家行描述一行，带省略号"),
    ("market-v4.css", "#sub-market .mk-team > p", "2lh", "专家团说明两行"),
    ("project-overview.css", ".project-result > p", "3lh", "项目成果说明三行"),
    ("reference-composer.css", ".cmp-add-item small", "2lh", "添加菜单副标题两行"),
    ("settings-v3.css", "#adv-grid .sh-card .sh-body small", "2lh", "高级设置卡描述两行"),
    ("settings-v3.css", "#page-settings .sh-card .sh-body small", "2lh", "设置首页卡描述两行"),
    ("settings-v3.css", "#kb-cards .kb-body", "3lh", "知识库卡正文三行"),
    ("style.css", ".pi-name", "2lh", "供应商名两行"),
    ("style.css", ".auto-prompt", "2lh", "自动化提示词两行"),
    ("style.css", ".auto-tpl-card .note", "3lh", "自动化模板注释三行"),
    ("style.css", ".exp-desc", "2lh", "专家卡描述两行"),
    ("style.css", "#kb-cards .card .name", "2lh", "知识库卡标题两行"),
    ("style.css", ".op-copy p", "2lh", "任务日志条目两行"),
    ("style.css", ".avail-row-err", "calc(2lh + 14px)", "通道报错两行（自带 7px 上下内边距，border-box 下要加回来）"),
    ("demo_workbench.html", ".novel-synopsis", "3lh", "演示页简介三行"),
]

# 手机壳内早就换过兜底的几处：守着别被回退成 clamp
MOBILE_CAPS = [
    ("mobile-shell.css", ".mob-exp-desc", "2.9em"),
    ("mobile-workbench.css", ".mobile-task-copy b", "3em"),
]


class NoLiveLineClampTest(unittest.TestCase):
    def test_no_live_line_clamp_anywhere(self):
        offenders = []
        for path in sorted(UI.glob("*.css")):
            body = re.sub(r"/\*.*?\*/", "", path.read_text(encoding="utf-8"), flags=re.S)
            for n, line in enumerate(body.splitlines(), 1):
                if "line-clamp" in line or "-webkit-box" in line:
                    offenders.append("%s:%d %s" % (path.name, n, line.strip()[:80]))
        self.assertEqual(offenders, [],
                         "Edge 147+ 已弃用 -webkit-box，clamp 会硬切丢字：\n" + "\n".join(offenders))

    def test_no_clamp_left_in_js_generated_markup(self):
        """界面里也别再用内联 `-webkit-line-clamp`（JS/HTML 里拼的样式同样失效）。"""
        offenders = []
        for path in sorted(list(UI.glob("*.js")) + list(UI.glob("*.html"))):
            text = path.read_text(encoding="utf-8", errors="ignore")
            text = re.sub(r"/\*.*?\*/", "", text, flags=re.S)   # 注释里的说明不算
            if "line-clamp" in text or "-webkit-box" in text:
                offenders.append(path.name)
        self.assertEqual(offenders, [])

    def test_reason_is_documented_in_style(self):
        """收口的原因写在 style.css 里，别只剩一堆 max-height 没人知道为什么。"""
        comments = "\n".join(re.findall(r"/\*.*?\*/", _text("style.css"), flags=re.S))
        self.assertIn("Edge 147", comments)
        self.assertIn("-webkit-box", comments)


class CapTest(unittest.TestCase):
    def test_every_capped_selector_is_really_clipped(self):
        problems = []
        for name, selector, want, why in CAPS:
            blocks = _blocks(_rules_only(name), selector)
            if not blocks:
                problems.append("%s %s 找不到声明块（%s）" % (name, selector, why))
                continue
            if want == "nowrap":
                ok = any("white-space: nowrap" in b and "text-overflow: ellipsis" in b
                         for b in blocks)
                if not ok:
                    problems.append("%s %s 单行截断要 nowrap + ellipsis（%s），实际块：%s"
                                    % (name, selector, why,
                                       " / ".join(b.strip()[:70] for b in blocks)))
                continue
            if not any(("max-height: " + want) in b for b in blocks):
                problems.append("%s %s 期望 max-height: %s（%s），实际块：%s"
                                % (name, selector, want, why,
                                   " / ".join(b.strip()[:70] for b in blocks)))
            if not any("overflow: hidden" in b or "overflow:hidden" in b for b in blocks):
                problems.append("%s %s 有 max-height 但没 overflow: hidden（不会剪裁）"
                                % (name, selector))
        self.assertEqual(problems, [], "\n".join(problems))

    def test_multi_line_caps_use_lh_not_hand_computed_em(self):
        """多行截断只认 `Nlh`：em 要手算「行数 × 行高」，行高来源一变就切半行。"""
        bad = []
        for name, selector, want, why in CAPS:
            if want == "nowrap" or want == "calc(2lh + 14px)":
                continue
            if not re.fullmatch(r"\dlh", want):
                bad.append("%s %s 的期望值 %s 不是 Nlh" % (name, selector, want))
        self.assertEqual(bad, [])

    def test_mobile_fallbacks_still_there(self):
        for name, selector, want in MOBILE_CAPS:
            blocks = _blocks(_rules_only(name), selector)
            self.assertTrue(any(("max-height: " + want) in b or ("max-height:" + want) in b
                                for b in blocks),
                            "%s %s 的 max-height: %s 兜底没了" % (name, selector, want))


if __name__ == "__main__":
    unittest.main()
