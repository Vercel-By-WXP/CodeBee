# -*- coding: utf-8 -*-
"""「重点 / 全部」档位开关的守测（2026-11-05）。

背景：对话分层（chat-quiet.js 的 `isPrimary`）是**硬判据**——系统猜这一行是不是
过程，猜错时用户只能一段段点开。计划第 3 节那条待办要的就是一个不占版面的开关。
落地时钉住的四件事，本文件逐条守：

1. **档位只有两档**，默认「重点」；存 `localStorage["orch.cqlevel"]`，刷新保持。
   「全部」不是改判据，而是**不跑 foldSecondary**（折叠是破坏性的，判据改不动）。
2. 切回「重点」必须能把已经收进 `cq-sec` 的成员**按原顺序搬回去**（unfold），
   否则用户一旦点过「全部」就再也回不到分层视图。
3. 胶囊挂 `#rd-chat`，**不进 `#rd-chat-flow`**：后者每次重绘整块 innerHTML，
   而且 app.js 的复制/编辑重发按子节点索引回查——塞节点会错位。
4. 按钮节点**只在文案/档位变化时重建**。11-04 的血账：按下与抬起之间换节点，
   点击就丢了（`pointerdown` 里重写 innerHTML 的经典坑）。

chat-quiet.js 依赖 MutationObserver 与整套 DOM，无法单独 require——沿用
`tests/test_ui_chat_quiet_primary.py` 的做法，对源码切片做静态断言。
"""
from __future__ import annotations

import unittest
from pathlib import Path

UI = Path(__file__).resolve().parents[1] / "app" / "ui"
JS = UI / "chat-quiet.js"
CSS = UI / "chat-quiet.css"


def _src(path: Path = JS) -> str:
    return path.read_text(encoding="utf-8")


def _fn(name: str, src: str | None = None) -> str:
    s = src if src is not None else _src()
    start = s.index("\n  function %s(" % name)
    end = s.index("\n  function ", start + 1) if "\n  function " in s[start + 1:] else len(s)
    return s[start:end]


class LevelStateTests(unittest.TestCase):
    def test_default_is_focus(self):
        block = _fn("level")
        # 只认 "all"，其余（含没存过、存了脏值、隐私模式抛错）一律「重点」
        self.assertIn('=== "all" ? "all" : "focus"', block)
        self.assertIn("localStorage.getItem(LEVEL_KEY)", block)
        self.assertIn("catch (e) { return \"focus\"; }", block)

    def test_key_is_namespaced(self):
        self.assertIn('const LEVEL_KEY = "orch.cqlevel";', _src())

    def test_all_skips_folding(self):
        block = _fn("foldSecondary")
        # 必须写在最前面：写在后面等于该折的还是折了
        head = block[:block.index("const kids")]
        self.assertIn('if (level() === "all") return;', head)

    def test_unfold_restores_members_in_order(self):
        block = _fn("unfoldSecondary")
        # 只认直属的 cq-sec（组内部的不归这一段管）
        self.assertIn(':scope > .cq-sec', block)
        # 先把成员按原顺序插回，再删容器——顺序反了就是全丢
        self.assertLess(block.index("host.insertBefore(k, d)"), block.index("d.remove()"))
        self.assertIn('if (k.tagName === "SUMMARY") return;', block)


class CapsuleWiringTests(unittest.TestCase):
    def test_mounted_on_chat_column_not_flow(self):
        src = _src()
        block = _fn("mountLevel")
        # merge() 里传进来的 host 就是 #rd-chat-flow 本身：直接 append 会被下一次
        # `flow.innerHTML = html` 抹掉（2026-11-05 探针实测父节点正是 rd-chat-flow）
        self.assertIn('closest("#rd-chat")', _fn("levelBox"))
        self.assertIn("box.appendChild(el)", block)
        self.assertNotIn("host.appendChild(el)", block)
        # 一次性挂载点：merge() 里调，不能挂到 flow 的重绘里去
        self.assertIn("syncLevel(host)", _fn("merge"))

    def test_click_switches_and_schedules(self):
        block = _fn("mountLevel")
        self.assertIn("localStorage.setItem(LEVEL_KEY, next)", block)
        self.assertIn("unfoldSecondary(flowEl)", block)
        # 指纹不清掉，下一次对账会以为「没变化」直接 return，档位永远切不过来
        self.assertIn("delete flowEl.dataset.cqSig", block)
        self.assertIn("schedule()", block)

    def test_buttons_not_rebuilt_on_every_merge(self):
        block = _fn("syncLevel")
        self.assertIn("el.dataset.cqView !== sig", block)
        # 重建必须被这层判断挡住：无条件 innerHTML = "" 就是 11-04 的点击丢失坑
        idx_guard = block.index("el.dataset.cqView !== sig")
        idx_wipe = block.index('el.innerHTML = ""')
        self.assertLess(idx_guard, idx_wipe)

    def test_position_follows_flow(self):
        block = _fn("placeLevel")
        # 诊断条会随内容变高，胶囊位置必须量 flow，不能写死 top
        self.assertIn("f.offsetTop", block)
        self.assertIn("el.style.top", block)


class StyleAndAssetTests(unittest.TestCase):
    def test_css_is_absolute_and_quiet(self):
        css = CSS.read_text(encoding="utf-8")
        self.assertIn("#run-detail #rd-chat .cq-level", css)
        self.assertIn("position: absolute", css)
        self.assertIn("#run-detail #rd-chat { position: relative; }", css)
        # 常态半透明：它是「猜错了才动」的开关，不该抢注意力
        self.assertIn("opacity: .45", css)

    def test_assets_carry_fresh_version(self):
        html = (UI / "index.html").read_text(encoding="utf-8")
        self.assertIn("chat-quiet.css?v=20261105a", html)
        self.assertIn("chat-quiet.js?v=20261105a", html)

    def test_labels_have_english(self):
        i18n = (UI / "i18n.js").read_text(encoding="utf-8")
        self.assertIn('"全部":', i18n)   # 既有词条，档位文案直接复用


if __name__ == "__main__":
    unittest.main()
