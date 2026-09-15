# -*- coding: utf-8 -*-
"""自定义 favicon/PWA 图标生成（2026-09-15 定版）：品牌渐变瓷片 + 白色粗体字母 B。

设计：135° 蓝紫渐变（--accent #5b8cff → --accent2 #8b5cf6，与 UI 品牌磁贴同源）
铺满全幅（maskable 安全区天然满足），Segoe UI Bold 的「B」白字居中，字高约画布 58%。
不再使用旧蜂巢瓷片 LOGO 母版（见 _gen_logo_icons.py，其已不再产出 icon-*）。

产出（就地覆盖，文件名/引用关系不变）：
  app/ui/icons/icon-512.png  （PWA any + maskable）
  app/ui/icons/icon-192.png  （favicon / apple-touch-icon）

用法：python tests/_gen_favicon.py   （纯代码出图，无需母版图/浏览器）
改主题配色后同步改这里与 style.css 的 --accent/--accent2。
"""
import os

import numpy as np
from PIL import Image, ImageDraw, ImageFont

_TESTS_DIR = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(_TESTS_DIR)
OUT = os.path.join(ROOT, "app", "ui", "icons")

ACCENT = (0x5B, 0x8C, 0xFF)    # 渐变起点（左上）
ACCENT2 = (0x8B, 0x5C, 0xF6)   # 渐变终点（右下）
GLYPH = "B"
GLYPH_RATIO = 0.58             # B 字高 / 画布边长
FONT_CANDIDATES = ["segoeuib.ttf", "arialbd.ttf", "arial.ttf"]  # Windows 自带粗体优先

SIZES = (512, 192)


def gradient_tile(size):
    """135° 线性渐变（左上→右下），np 直接插值。"""
    t = (np.add.outer(np.arange(size), np.arange(size)) / (2.0 * (size - 1)))
    start = np.array(ACCENT, dtype=float)
    end = np.array(ACCENT2, dtype=float)
    px = start[None, None, :] * (1 - t[..., None]) + end[None, None, :] * t[..., None]
    return Image.fromarray(px.astype(np.uint8), "RGB")


def load_font(px):
    for name in FONT_CANDIDATES:
        path = os.path.join(os.environ.get("WINDIR", r"C:\Windows"), "Fonts", name)
        if os.path.isfile(path):
            return ImageFont.truetype(path, px)
    raise RuntimeError("找不到可用的粗体字体（segoeuib/arialbd/arial）")


def render(size):
    tile = gradient_tile(size)
    draw = ImageDraw.Draw(tile)
    # 按目标字高反推字号：先量一次再等比修正（B 无降部，bbox 高即字身高）
    target = size * GLYPH_RATIO
    font = load_font(int(target))
    x0, y0, x1, y1 = draw.textbbox((0, 0), GLYPH, font=font)
    font = load_font(int(round(font.size * target / (y1 - y0))))
    draw.text((size / 2, size / 2), GLYPH, font=font, fill="white", anchor="mm")
    return tile


def ascii_preview(img, w=36, h=36):
    """亮度转文本网格（离线核对 B 形状用，本模型不看图）。"""
    a = np.asarray(img.convert("L").resize((w, h), Image.LANCZOS)).astype(int)
    ramp = "@%#*+=-:. "   # 深→浅
    rows = []
    for row in a:
        rows.append("".join(ramp[min(9, v * 10 // 256)] for v in row))
    return "\n".join(rows)


def main():
    os.makedirs(OUT, exist_ok=True)
    big = render(512)
    big.save(os.path.join(OUT, "icon-512.png"), optimize=True)
    small = big.resize((192, 192), Image.LANCZOS)
    small.save(os.path.join(OUT, "icon-192.png"), optimize=True)
    for s in SIZES:
        print("wrote icon-%d.png" % s)

    # ---- 像素校验：渐变铺满无漏白、白墨占比合理、四边不着白
    for s, im in ((512, big), (192, small)):
        a = np.asarray(im.convert("RGB")).astype(int)
        mn = a.min(axis=2)
        tl, br = a[3, 3], a[s - 4, s - 4]
        c0, c1 = int(s * 0.28), int(s * 0.72)
        ink = (mn[c0:c1, c0:c1] > 225).mean() * 100
        edges = np.concatenate([mn[0, :], mn[-1, :], mn[:, 0], mn[:, -1]])
        assert tl[2] > 200 and tl[2] >= tl[0], "左上应偏蓝 #5b8cff: %s" % (tuple(tl),)
        assert br[0] > 120 and br[0] > br[1] + 20 and br[2] > 200, "右下应偏紫 #8b5cf6: %s" % (tuple(br),)
        assert 4.0 < ink < 40.0, "B 字白墨占比异常: %.1f%%" % ink
        assert (edges > 225).mean() < 0.01, "边缘漏白"
        print("icon-%d: 左上%s 右下%s 中心白墨 %.1f%% ✓" % (s, tuple(tl), tuple(br), ink))
    print("--- B 形状预览（512 亮度网格）---")
    print(ascii_preview(big))


if __name__ == "__main__":
    main()
