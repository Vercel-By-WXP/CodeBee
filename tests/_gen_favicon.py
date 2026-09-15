# -*- coding: utf-8 -*-
"""自定义 favicon/PWA 图标生成（2026-09-15 定版）：黑色圆角方块 + 白 B + 天蓝点。

设计（参照用户给的「K+蓝点」样式）：近黑圆角方块（#1a1a1a，边长≈画布 98%，
圆角率 22%，圆角外透明），白色粗体「B」居中（Segoe UI Bold，字高≈画布 58%），
右上角缀一枚天蓝圆点（#29abe2，直径≈画布 8.5%，位置贴 B 右肩）。不使用旧蜂巢
瓷片 LOGO 母版（见 _gen_logo_icons.py，其已不再产出 icon-*）。

产出（就地覆盖，文件名/引用关系不变；PNG 带透明通道）：
  app/ui/icons/icon-512.png  （PWA any + maskable）
  app/ui/icons/icon-192.png  （favicon / apple-touch-icon）

用法：python tests/_gen_favicon.py   （纯代码出图，无需母版图/浏览器）
改配色/形状动 TILE / GLYPH_C / DOT_C / DOT_RATIO / RADIUS_RATIO。
PIL 画圆角矩形无抗锯齿：4× 超采样渲染再 LANCZOS 缩回。
"""
import os

import numpy as np
from PIL import Image, ImageDraw, ImageFont

_TESTS_DIR = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(_TESTS_DIR)
OUT = os.path.join(ROOT, "app", "ui", "icons")

TILE = (0x1A, 0x1A, 0x1A)      # 方块底色：近黑
GLYPH_C = (0xFF, 0xFF, 0xFF)   # 字色：白
DOT_C = (0x29, 0xAB, 0xE2)     # 右上角圆点：天蓝（品牌色呼应）
GLYPH = "B"
GLYPH_RATIO = 0.58             # B 字高 / 画布边长
TILE_RATIO = 0.98              # 方块边长 / 画布边长（四周留 1% 透明边）
RADIUS_RATIO = 0.22            # 圆角半径 / 方块边长
DOT_RATIO = 0.085              # 圆点直径 / 画布边长
FONT_CANDIDATES = ["segoeuib.ttf", "arialbd.ttf", "arial.ttf"]  # Windows 自带粗体优先
DIST = 70                      # 像素到字色的距离阈值（判「是白」），抗锯齿半覆盖也算
SS = 4                         # 超采样倍数（PIL 画圆角矩形无抗锯齿，靠缩小回采样平滑）

SIZES = (512, 192)


def load_font(px):
    for name in FONT_CANDIDATES:
        path = os.path.join(os.environ.get("WINDIR", r"C:\Windows"), "Fonts", name)
        if os.path.isfile(path):
            return ImageFont.truetype(path, px)
    raise RuntimeError("找不到可用的粗体字体（segoeuib/arialbd/arial）")


def render(size):
    s = size * SS
    img = Image.new("RGBA", (s, s), (0, 0, 0, 0))
    draw = ImageDraw.Draw(img)
    inset = int(s * (1 - TILE_RATIO) / 2)
    draw.rounded_rectangle([inset, inset, s - inset, s - inset],
                           radius=int((s - 2 * inset) * RADIUS_RATIO), fill=TILE + (255,))
    target = s * GLYPH_RATIO
    font = load_font(int(target))
    x0, y0, x1, y1 = draw.textbbox((0, 0), GLYPH, font=font)
    font = load_font(int(round(font.size * target / (y1 - y0))))
    draw.text((s / 2, s / 2), GLYPH, font=font, fill=GLYPH_C + (255,), anchor="mm")
    bb = draw.textbbox((s / 2, s / 2), GLYPH, font=font, anchor="mm")
    # 蓝点贴 B 右肩：圆心在字右缘外一点、字顶线附近
    dr = s * DOT_RATIO / 2
    cx, cy = bb[2] + dr * 0.9, bb[1] + dr * 1.2
    draw.ellipse([cx - dr, cy - dr, cx + dr, cy + dr], fill=DOT_C + (255,))
    return img.resize((size, size), Image.LANCZOS)


def ascii_preview(img, w=36, h=36):
    """透明→空格、亮度反相→字符 的文本网格（白字在网格里显形，本模型不看图）。"""
    a = np.asarray(img.resize((w, h), Image.LANCZOS)).astype(int)
    alpha = a[..., 3]
    lum = a[..., :3] @ [299, 587, 114] // 1000
    ramp = " .:-=+*#%@"   # 反相：亮（白字）出深字符
    rows = []
    for yy in range(h):
        rows.append("".join(" " if alpha[yy, xx] < 40 else ramp[min(9, lum[yy, xx] * 10 // 256)]
                            for xx in range(w)))
    return "\n".join(rows)


def reference_mask(size, font):
    """同字体同位置的 B 参考渲染（黑底白字），用于形状一致性比对。"""
    ref = Image.new("L", (size, size), 0)
    ImageDraw.Draw(ref).text((size / 2, size / 2), GLYPH, font=font, fill=255, anchor="mm")
    return np.asarray(ref) > 128


def main():
    os.makedirs(OUT, exist_ok=True)
    big = render(512)
    big.save(os.path.join(OUT, "icon-512.png"), optimize=True)
    small = big.resize((192, 192), Image.LANCZOS)
    small.save(os.path.join(OUT, "icon-192.png"), optimize=True)
    for s in SIZES:
        print("wrote icon-%d.png" % s)

    # ---- 像素校验：方块外透明、方块着色、白字墨量、蓝点在右上区、字形一致
    white = np.array(GLYPH_C, dtype=float)
    tile = np.array(TILE, dtype=float)
    dot = np.array(DOT_C, dtype=float)
    for s, im in ((512, big), (192, small)):
        a = np.asarray(im.convert("RGBA")).astype(float)
        alpha = a[..., 3]
        assert alpha[3, 3] < 10 and alpha[s - 4, s - 4] < 10, "角上应透明（圆角）"
        border = np.concatenate([alpha[0, :], alpha[-1, :], alpha[:, 0], alpha[:, -1]])
        assert border.max() < 10, "贴边应全透明（方块没留边距？）"
        for fy in (0.08, 0.5, 0.92):
            for fx in (0.08, 0.5, 0.92):
                if fy == 0.5 and fx == 0.5:
                    continue   # 中心是 B
                px = a[int(s * fy), int(s * fx)]
                assert np.linalg.norm(px[:3] - tile) < 40 and px[3] > 240, \
                    "方块底色异常 (%.2f,%.2f): %s" % (fx, fy, tuple(px.astype(int)),)
        is_glyph = np.linalg.norm(a[..., :3] - white[None, None, :], axis=2) < DIST
        ratio = (is_glyph & (alpha > 200)).mean() * 100
        assert 8.0 < ratio < 35.0, "B 字墨量占比异常: %.1f%%" % ratio
        is_dot = np.linalg.norm(a[..., :3] - dot[None, None, :], axis=2) < DIST
        ys, xs = np.nonzero(is_dot & (alpha > 200))
        assert len(xs) > 0, "找不到天蓝圆点"
        dot_cx, dot_cy = xs.mean() / s, ys.mean() / s
        assert 0.6 < dot_cx < 0.85 and 0.1 < dot_cy < 0.4, \
            "圆点应在右上区: (%.2f, %.2f)" % (dot_cx, dot_cy)
        print("icon-%d: 圆角透明 ✓ 底色 ✓ 白墨 %.1f%% ✓ 蓝点(%.2f,%.2f) ✓"
              % (s, ratio, dot_cx, dot_cy))

    # ---- 字形一致性：白字掩膜 vs 同字体参考渲染，IoU 必须 > 0.85
    font = load_font(400)
    x0, y0, x1, y1 = ImageDraw.Draw(Image.new("L", (8, 8))).textbbox((0, 0), GLYPH, font=font)
    font = load_font(int(round(font.size * (512 * GLYPH_RATIO) / (y1 - y0))))
    got = np.linalg.norm(np.asarray(big.convert("RGB")).astype(float) - white[None, None, :], axis=2) < DIST
    ref = reference_mask(512, font)
    iou = (got & ref).sum() / float((got | ref).sum())
    assert iou > 0.85, "字形与参考 B 不一致：IoU=%.3f" % iou
    print("字形 IoU=%.3f ✓（与 %s 渲染的 B 一致）" % (iou, font.getname()[0]))
    print("--- B 形状预览（512 亮度网格，空格=透明）---")
    print(ascii_preview(big))


if __name__ == "__main__":
    main()
