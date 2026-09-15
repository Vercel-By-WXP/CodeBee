# -*- coding: utf-8 -*-
"""从品牌母版重新生成 logo 资产（2026-09 正式版 B 标）——不含 favicon！

favicon/PWA 图标（icon-192/512.png）2026-09-15 起改为自定义「渐变瓷片+字母 B」，
由 tests/_gen_favicon.py 纯代码生成；本脚本重跑**不会**再触碰它们。

母版：app/ui/icons/brand-square.png（方形竖版，上 B 标下字标）——
设计稿转自 CodeBee LOGO（蜂巢瓷片拼成 B）。本脚本自动：
1) 定位 B 标区域（首个墨迹行带，与字标间有大片留白）；
2) 白底转透明：纯白→alpha 0；抗锯齿过渡带→部分 alpha 并去白（c'=(c-(1-a)·255)/a），
   高饱和瓷片色（藏青 #013a75 / 亮青 #29abe2 一族）原样保留；
3) 产出 app/ui/icons/：
   - logo-mark.png       透明底 B 标（备用 / README 历史）
   - logo-horizontal.png 透明底横版锁版图（B 标+CodeBee 字标一体；README 锁版图）

⚠️ 横版含银灰瓷片（主体 ~#c0c0d0，高光至 ~#e4e5e8，低饱和）：透明阈值必须钉在
mn≥235，否则银瓷片会被打穿变半透明。
用法：python tests/_gen_logo_icons.py   （换母版后重跑）
"""
import os

import numpy as np
from PIL import Image

_TESTS_DIR = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(_TESTS_DIR)  # 仓库根（tests 的上级）
OUT = os.path.join(ROOT, "app", "ui", "icons")
MASTER = os.path.join(OUT, "brand-square.png")

WHITE_MIN = 245   # mn ≥ 此值且低饱和 → 纯白，全透明
EDGE_MIN = 160    # 过渡带下限（mn ≥ 此值且低饱和 → 软边）
EDGE_SAT = 45     # 过渡带允许的最大饱和度（瓷片色饱和度高，不会误伤）

# 横版锁版参数：银灰瓷片（最亮 mn≈234）与白底（252-255）同在低饱和区，
# 全局阈值必须钉在 mn≥246 才能不打穿瓷片；二值透明，不做软边（28px 显示下无感）
LOCKUP_MASTER = os.path.join(OUT, "brand-horizontal.png")
L_WHITE_MIN = 246  # mn ≥ 此值且 sat≤8 → 全透明（背景 + 字腔）
L_EDGE_SAT = 8


def locate_mark(img):
    """返回 B 标在母版中的 (x0, y0, x1, y1)：第一个墨迹行带 + 带内墨迹列。"""
    a = np.asarray(img.convert("RGB"))
    ink = a.astype(int).sum(axis=2) < 700
    rows = ink.sum(axis=1)
    bands, start = [], None
    for y, v in enumerate(rows):
        if v > 3 and start is None:
            start = y
        elif v <= 3 and start is not None:
            bands.append((start, y))
            start = None
    if start is not None:
        bands.append((start, len(rows)))
    bands = [(s, e) for s, e in bands if e - s > 8]
    y0, y1 = bands[0]
    cols = ink[y0:y1].sum(axis=0)
    nz = np.nonzero(cols > 2)[0]
    return int(nz.min()), int(y0), int(nz.max()) + 1, int(y1)


def to_transparent(crop):
    """白底转透明（RGBA Image）：软边去白，饱和瓷片色不动。"""
    px = np.asarray(crop.convert("RGB")).astype(int)
    mn = px.min(axis=2)
    sat = px.max(axis=2) - mn
    alpha = np.full(mn.shape, 255.0)
    pure = (mn >= WHITE_MIN) & (sat <= 8)
    band = (~pure) & (mn >= EDGE_MIN) & (sat <= EDGE_SAT)
    t = np.clip((WHITE_MIN - mn) / float(WHITE_MIN - EDGE_MIN), 0.0, 1.0)
    alpha[pure] = 0.0
    alpha[band] = t[band] * 255.0
    out = px.astype(float)
    sel = band & (alpha > 20)
    a = alpha[sel] / 255.0
    for c in range(3):
        ch = out[..., c][sel]
        out[..., c][sel] = np.clip((ch - (1.0 - a) * 255.0) / np.maximum(a, 0.08), 0, 255)
    rgba = np.dstack([out.astype(np.uint8), alpha.astype(np.uint8)])
    return Image.fromarray(rgba, "RGBA")


def to_transparent_lockup(crop):
    """横版锁版白底转透明：二值规则（mn≥246 且近无饱和 → 透明），
    银灰瓷片最亮 mn≈234，与阈值间隔充分，绝无打穿。"""
    px = np.asarray(crop.convert("RGB")).astype(int)
    mn = px.min(axis=2)
    sat = px.max(axis=2) - mn
    alpha = np.where((mn >= L_WHITE_MIN) & (sat <= L_EDGE_SAT), 0, 255).astype(np.uint8)
    return Image.fromarray(np.dstack([px.astype(np.uint8), alpha]), "RGBA")


def ascii_map(img, w=44, h=40):
    """透明标记转文本网格核对（X=不透明瓷片 .=透明 x=半透明边）。"""
    a = np.asarray(img.resize((w, h), Image.LANCZOS))
    al = a[..., 3]
    rows = []
    for yy in range(h):
        row = ""
        for xx in range(w):
            v = al[yy, xx]
            row += "." if v < 40 else ("x" if v < 200 else "X")
        rows.append(row)
    return "\n".join(rows)


def main():
    if not os.path.isdir(OUT):
        os.makedirs(OUT)
    master = Image.open(MASTER)
    x0, y0, x1, y1 = locate_mark(master)
    print("mark bbox: (%d,%d)-(%d,%d) %dx%d" % (x0, y0, x1, y1, x1 - x0, y1 - y0))
    crop = master.convert("RGB").crop((x0, y0, x1, y1))
    mark = to_transparent(crop)
    mark.save(os.path.join(OUT, "logo-mark.png"), optimize=True)
    print("wrote logo-mark.png  %dx%d" % mark.size)
    # favicon/PWA（icon-192/512.png）归 tests/_gen_favicon.py 管，这里不再生成
    if os.path.isfile(LOCKUP_MASTER):
        hz = Image.open(LOCKUP_MASTER)
        ha = np.asarray(hz.convert("RGB"))
        hink = ha.astype(int).sum(axis=2) < 700
        hrows = np.nonzero(hink.sum(axis=1) > 2)[0]
        hcols = np.nonzero(hink.sum(axis=0) > 2)[0]
        m = 4
        box = (max(0, int(hcols.min()) - m), max(0, int(hrows.min()) - m),
               min(hz.width, int(hcols.max()) + 1 + m), min(hz.height, int(hrows.max()) + 1 + m))
        lockup = to_transparent_lockup(hz.convert("RGB").crop(box))
        lockup.save(os.path.join(OUT, "logo-horizontal.png"), optimize=True)
        print("wrote logo-horizontal.png  %dx%d" % lockup.size)
        la = np.asarray(lockup)
        lmn = la[..., :3].astype(int).min(axis=2)
        lsat = la[..., :3].astype(int).max(axis=2) - lmn
        silver = (lsat >= 5) & (lsat <= 40) & (lmn >= 150) & (lmn <= 225)
        ratio = float((la[..., 3][silver] >= 250).mean()) if silver.any() else 1.0
        print("silver-protection: px=%d opaque_ratio=%.4f" % (int(silver.sum()), ratio))
    else:
        print("skip logo-horizontal.png（无 brand-horizontal.png）")
    print("--- logo-mark 透明度网格 ---")
    print(ascii_map(mark))


if __name__ == "__main__":
    main()
