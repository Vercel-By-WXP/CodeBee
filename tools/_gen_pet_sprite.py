# -*- coding: utf-8 -*-
"""生成桌面蜜蜂的精灵图（app/pet_bee*.png），pet.py 精灵模式的主素材。

两张预置形象（用户可切换）：
  plush → pet_bee.png       手绘风毛绒蜜蜂
  robot → pet_bee_robot.png 机械蜜蜂
处理：裁到 alpha 有效区 → 等比缩到高 300px（LANCZOS）→ alpha 二值化 → 再裁。
运行时的各状态动画帧（倾斜/旋转/变暗/灰化）由 pet.py 启动时用 PIL 现做，
这里只出干净底图；换形象时替换源图重跑本脚本即可。

为什么二值化：桌宠窗口靠 -transparentcolor 抠底，任何半透明像素在 pet.py
里都会被合成到近黑键色上（暗化发黑）。这两张图整张都是羽化 alpha，直接合成
会让蜜蜂发闷。边缘像素实测 RGB 是亮的（非 premultiplied），所以按阈值硬切、
保留真实亮色，只裁掉几乎看不见的极淡绒毛。

用法：python tools/_gen_pet_sprite.py [plush|robot] [自定义源图]
"""
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
OUT_DIR = ROOT / "app"
MASTER_H = 300    # 底图高度；pet.py 运行时再缩到显示尺寸
ALPHA_CUT = 100   # alpha 二值化阈值：>= 保留原色，< 透明

# 预置源图（用户提供，位于 TIM 临时目录；文件不在时不重生成）
SOURCES = {
    "plush": (r"C:\Users\HP\AppData\Roaming\Tencent\TIM\Temp"
              r"\14bd8ee5dfd3089dc4c9c59a5a4e3409.png",
              OUT_DIR / "pet_bee.png"),
    "robot": (r"C:\Users\HP\AppData\Roaming\Tencent\TIM\Temp"
              r"\831a32adfb58f45c7baa3882e6df6e08.png",
              OUT_DIR / "pet_bee_robot.png"),
}


def build(src, out):
    from PIL import Image

    im = Image.open(src).convert("RGBA")
    im = im.crop(im.getchannel("A").getbbox())   # 透明边全裁掉，只留蜜蜂
    w, h = im.size
    nw = max(1, round(w * MASTER_H / h))
    im = im.resize((nw, MASTER_H), Image.LANCZOS)
    # 缩放后再切 alpha（LANCZOS 先把边界平滑了，切出来不锯齿）
    im.putalpha(im.getchannel("A").point(lambda v: 255 if v >= ALPHA_CUT else 0))
    im = im.crop(im.getchannel("A").getbbox())
    out.parent.mkdir(parents=True, exist_ok=True)
    im.save(out, optimize=True)
    print("sprite: %-12s %s  %dx%d  %.1f KB"
          % (out.stem, out, im.size[0], im.size[1], out.stat().st_size / 1024))


def main():
    argv = sys.argv[1:]
    if argv and argv[0] in SOURCES:
        keys, custom = [argv[0]], (argv[1] if len(argv) > 1 else None)
    elif argv:
        keys, custom = list(SOURCES), argv[0]
    else:
        keys, custom = list(SOURCES), None
    for k in keys:
        src, out = SOURCES[k]
        if custom:
            src = custom
        if not Path(src).exists():
            print("skip %s：源图不存在 %s" % (k, src))
            continue
        build(src, out)


if __name__ == "__main__":
    main()
