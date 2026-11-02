# -*- coding: utf-8 -*-
"""记忆星图清晰度三查守测（2026-11-02 用户「图太模糊了」实案）。

当时是三重原因叠加，任何一条回退都会让画面重新发虚：

1. **DPR 下限**：`DPR = Math.min(2, devicePixelRatio)` 在浏览器缩到 80%/67%
   （大屏上很常见）时拿到 0.8，画布后备像素少于 CSS 像素、被放大显示 ——
   文字是矢量仍清晰、只有画布糊，正是用户看到的体征。必须
   `Math.max(1, Math.min(3, dpr))`：下限 1 兜住 dpr<1，上限 3 兼顾 4K/移动端。
2. **不许烘焙占位尺寸**：旧 `resize()` 用 `Math.max(200, 0)` 起步，面板还没
   量出真实宽高（刚挂载 / 父级 hidden）时就按 200×200 建画布，随后被 CSS
   拉到整栏 → 星体放大 2-3 倍成毛毛球。量不到必须直接 return 等下一帧；
   又因 ResizeObserver 会漏报，frame() 每帧比对 clientWidth 与 DPR 自愈。
3. **座位数封顶**：经验包曾不设上限，99 个包挤在内环（周长最短），星体间距
   只有 7px < 星体直径，连成一条实心黑带。每环必须给固定座位数。
"""

import os
import re
import unittest

UI_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                      "app", "ui")


def _read(name):
    with open(os.path.join(UI_DIR, name), "r", encoding="utf-8") as fh:
        return fh.read()


def _js_function_body(src, name):
    """取顶层 function name() {...} 的近似正文：从函数头到下一个行首 function/return 边界。"""
    m = re.search(r"(?m)^\s*function %s\s*\(" % re.escape(name), src)
    if not m:
        return None
    nxt = re.search(r"(?m)^\s*function ", src[m.end():])
    end = m.end() + nxt.start() if nxt else len(src)
    return src[m.start():end]


class TestAtlasSharpness(unittest.TestCase):

    def setUp(self):
        self.src = _read("app.js")
        self.resize = _js_function_body(self.src, "resize")
        self.frame = _js_function_body(self.src, "frame")
        self.build = _js_function_body(self.src, "buildSky")

    def test_resize_exists(self):
        for name, body in (("resize", self.resize), ("frame", self.frame),
                           ("buildSky", self.build)):
            self.assertIsNotNone(body, "星图的 %s() 不见了" % name)

    def test_dpr_floor_is_one(self):
        self.assertIn("Math.max(1, Math.min(3, window.devicePixelRatio",
                      self.resize,
                      "DPR 必须写成 Math.max(1, Math.min(3, dpr))：下限 1 兜住浏览器"
                      "缩到 dpr<1 的情况，少了它画布就被放大显示、整图发虚")
        self.assertNotIn("Math.min(2, window.devicePixelRatio", self.src,
                         "旧的 Math.min(2, dpr) 写法回来了：dpr<1 时画布后备像素不足")

    def test_no_placeholder_size_baking(self):
        self.assertIn("if (cssW < 40 || cssH < 40) return;", self.resize,
                      "resize() 必须先判尺寸够不够再建画布；量不到就 return 等下一帧")
        # 只查代码行：函数里的注释会解释这段历史（Math.max(200, 0)），不能算命中
        code = "\n".join(ln for ln in self.resize.splitlines()
                         if not ln.strip().startswith("//"))
        self.assertNotRegex(code, r"Math\.max\(200\s*,",
                            "resize() 里又出现了 Math.max(200, ...) 的占位尺寸烘焙：面板没展开"
                            "时会按 200px 建画布，整图被拉伸发虚")
        # 早退必须发生在写 canvas.width 之前
        early = self.resize.find("if (cssW < 40 || cssH < 40) return;")
        write = self.resize.find("canvas.width = bw")
        self.assertGreater(write, early, "早退判断被挪到了建画布之后，等于没挡住占位尺寸")

    def test_frame_self_heals_size_and_dpr(self):
        self.assertIn("canvas.clientWidth - W", self.frame,
                      "frame() 丢了每帧自愈：ResizeObserver 漏报时画布会一直停在旧尺寸")
        self.assertIn("devicePixelRatio", self.frame,
                      "frame() 自愈未比对 devicePixelRatio：浏览器缩放变化后不会重建画布")

    def test_seats_are_capped_per_orbit(self):
        self.assertIsNotNone(self.build, "buildSky() 不见了")
        caps = re.findall(r"top\((\w+),\s*(\d+)\)", self.build)
        self.assertTrue(caps, "buildSky() 里没找到 top(arr, N) 形式的座位上限")
        for arr, n in caps:
            self.assertGreater(int(n), 0, "%s 的座位上限是 %s，等于整环清空" % (arr, n))
            self.assertLessEqual(int(n), 60,
                                 "%s 的座位上限 %s 太大：内环周长装不下，会重新连成黑带" % (arr, n))
        # 不允许再把原始数组整段并进去（无上限）
        self.assertNotIn("].concat(packs,", self.build.replace(" ", ""),
                         "有未封顶的原始数组被整段并入星图，99 个经验包会把内环挤成实心带")

    def test_status_line_reports_loaded_vs_total(self):
        self.assertIn("TOTALS.all", self.src,
                      "状态行需要显示「载入 X / 共 Y」，否则用户不知道星图只画了头部")


if __name__ == "__main__":
    unittest.main()
