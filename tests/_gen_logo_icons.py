# -*- coding: utf-8 -*-
"""重新生成 PWA 图标（icons/icon-192.png、icon-512.png）。

与 app/ui/index.html 里的 #i-baton 精灵图同一造型：指挥棒（棒头实心圆 +
斜向棒体）挥出弧线，弧上三颗渐大的声部点渐次进场。白图形色，底为 CSS 磁贴
同款 135° 蓝紫渐变（--accent #5b8cff → --accent2 #8b5cf6）。
maskable 安全区：图形内容控制在画布中央 ~66% 内。

用法：python tests/_gen_logo_icons.py   （改动 logo 造型后重跑）
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)) + "/..")
from PIL import Image, ImageDraw

ROOT = os.path.normpath(os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
OUT = os.path.join(ROOT, "app", "ui", "icons")
SS = 4  # 超采样倍数，缩回后获得平滑边缘

C0 = (0x5B, 0x8C, 0xFF)  # 左上
C1 = (0x8B, 0x5C, 0xF6)  # 右下
GLYPH = (255, 255, 255)

# 与 #i-baton 同一组坐标（viewBox 0..24）
GRIP = (5.2, 18.8, 2.6)
STICK = ((6.8, 17.2), (13.6, 8.2))
DOTS = [(16.0, 5.9, 1.6), (19.9, 4.3, 2.0), (21.6, 8.9, 2.3)]
STROKE = 2.4

# 内容包围盒 → 居中偏移：x∈[3.2,23.45] y∈[1.85,21]，中心约 (13.3,11.4)
CX, CY = 13.25, 11.85
SAFE = 0.66  # 内容占画布比例（maskable 安全区内）


def render(size):
    s = size * SS
    img = Image.new("RGB", (s, s), C0)
    px = img.load()
    for y in range(s):
        for x in range(s):
            t = (x + y) / (2.0 * (s - 1))  # 135°：左上→右下
            px[x, y] = tuple(int(a + (b - a) * t) for a, b in zip(C0, C1))
    d = ImageDraw.Draw(img)
    scale = s * SAFE / 24.0
    ox, oy = (s - 24.0 * scale) / 2.0, (s - 24.0 * scale) / 2.0

    def P(x, y):
        return ox + x * scale, oy + y * scale

    def R(r):
        return r * scale

    d.line([P(*STICK[0]), P(*STICK[1])], fill=GLYPH, width=int(round(STROKE * scale)))
    ex = STROKE * scale / 2.0  # 像素半径：线宽一半，让棒体端点呈圆头
    for pt in STICK:
        x, y = P(*pt)
        d.ellipse((x - ex, y - ex, x + ex, y + ex), fill=GLYPH)
    for x, y, r in [GRIP] + DOTS:
        cx, cy = P(x, y)
        rr = R(r) + ex  # 实心点与描边同粗度基准，视觉与小图标一致
        d.ellipse((cx - rr, cy - rr, cx + rr, cy + rr), fill=GLYPH)
    return img.resize((size, size), Image.LANCZOS)


def main():
    if not os.path.isdir(OUT):
        os.makedirs(OUT)
    for size in (192, 512):
        render(size).save(os.path.join(OUT, "icon-%d.png" % size))
        print("wrote icon-%d.png" % size)


if __name__ == "__main__":
    main()
