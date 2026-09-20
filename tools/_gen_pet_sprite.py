# -*- coding: utf-8 -*-
"""生成桌面蜜蜂的精灵图（app/pet_bee.png），pet.py 精灵模式的主素材。

来源：用户提供的手绘风毛绒蜜蜂 PNG（TIM 临时目录，1254x1254 真透明通道）。
处理：裁到 alpha 有效区 → 等比缩到高 300px（LANCZOS）→ 存 RGBA PNG。
运行时的各状态动画帧（倾斜/旋转/变暗/灰化）由 pet.py 启动时用 PIL 现做，
这里只出一张干净底图；换形象时替换源图重跑本脚本即可。

用法：python tools/_gen_pet_sprite.py [源图路径]
"""
import sys
from pathlib import Path

SRC_DEFAULT = (r"C:\Users\HP\AppData\Roaming\Tencent\TIM\Temp"
               r"\14bd8ee5dfd3089dc4c9c59a5a4e3409.png")
OUT = Path(__file__).resolve().parents[1] / "app" / "pet_bee.png"
MASTER_H = 300   # 底图高度；pet.py 运行时再缩到显示尺寸
ALPHA_CUT = 100  # alpha 二值化阈值：>= 保留原色，< 透明
# 为什么二值化：桌宠窗口靠 -transparentcolor 抠底，任何半透明像素在 pet.py
# 里都会被合成到近黑键色上（暗化）。这张毛绒图整张都是羽化 alpha，直接合成
# 会让全蜂发黑发闷。边缘像素实测 RGB 是亮的（非 premultiplied），所以按
# 阈值硬切、保留真实亮色，只裁掉几乎看不见的极淡绒毛。


def main():
    from PIL import Image

    src = sys.argv[1] if len(sys.argv) > 1 else SRC_DEFAULT
    im = Image.open(src).convert("RGBA")
    box = im.getchannel("A").getbbox()   # 透明边全裁掉，只留蜜蜂
    im = im.crop(box)
    w, h = im.size
    nw = max(1, round(w * MASTER_H / h))
    im = im.resize((nw, MASTER_H), Image.LANCZOS)
    # 缩放后再切 alpha（LANCZOS 先把边界平滑了，切出来不锯齿）
    im.putalpha(im.getchannel("A").point(lambda v: 255 if v >= ALPHA_CUT else 0))
    im = im.crop(im.getchannel("A").getbbox())
    OUT.parent.mkdir(parents=True, exist_ok=True)
    im.save(OUT, optimize=True)
    print("sprite: %s  %dx%d  %.1f KB" % (OUT, im.size[0], im.size[1],
                                          OUT.stat().st_size / 1024))


if __name__ == "__main__":
    main()
