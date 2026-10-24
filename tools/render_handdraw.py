# -*- coding: utf-8 -*-
"""手绘视频成片渲染器（确定性工具链，独立于服务进程运行）。

输入 storyboard.json（作者按制作包交付），输出 handdraw.mp4：
逐镜 PIL 白板绘制（逐字/逐段揭示）→ edge-tts 配音 → FFmpeg 混流与拼接。
工程检查（解码校验）在构建期完成；观感检查（真人试听）无法自动做，
报告里如实标 not_done，不冒充观感保证（skillpack 第八节：不并谎）。

本工具运行在具备 Pillow + imageio-ffmpeg + edge-tts 的解释器下
（本机为 Python 3.13 64 位），不 import 服务端 app 包。
"""
import argparse
import asyncio
import json
import os
import re
import subprocess
import sys
import tempfile
import time

PAPER = (251, 249, 244)
INK = (35, 38, 46)
ACCENT = (43, 108, 176)
GRAY = (122, 127, 136)

ELEMENT_KINDS = ("title", "point", "box", "flow", "note")
SCHEMA_EXIT, TTS_EXIT, MUX_EXIT = 2, 3, 4


def die(msg, code, report_path=None, report=None):
    print("RENDER_FAIL:%s" % code)
    if report_path and report is not None:
        report["ok"] = False
        report["error"] = msg
        with open(report_path, "w", encoding="utf-8") as f:
            json.dump(report, f, ensure_ascii=False, indent=2)
    print(msg.encode("ascii", "replace").decode("ascii")[:400])
    sys.exit(code)


def load_font(paths, size):
    from PIL import ImageFont
    for p in paths:
        if os.path.exists(p):
            try:
                return ImageFont.truetype(p, size)
            except Exception:
                continue
    return ImageFont.load_default()


def fonts(w, h):
    bold = ["C:/Windows/Fonts/msyhbd.ttc", "C:/Windows/Fonts/simhei.ttf",
            "C:/Windows/Fonts/msyh.ttc"]
    hand = ["C:/Windows/Fonts/simkai.ttf", "C:/Windows/Fonts/msyh.ttc",
            "C:/Windows/Fonts/simhei.ttf"]
    return {
        "title": load_font(bold, int(h * 0.052)),
        "point": load_font(hand, int(h * 0.034)),
        "box": load_font(hand, int(h * 0.030)),
        "note": load_font(hand, int(h * 0.026)),
        "sub": load_font(bold, int(h * 0.030)),
    }


def split_sentences(text):
    parts = [p for p in re.split(r"(?<=[。！？；!?;])", text) if p.strip()]
    return parts or [text]


_WORD_RUN = re.compile(r"[A-Za-z0-9][A-Za-z0-9.:_\-]*")


def _tokens(text):
    """英文单词串整体成 token，不拆行（「CodeBee」不得断成 C/odeBee）。"""
    out, i = [], 0
    while i < len(text):
        m = _WORD_RUN.match(text, i)
        if m:
            out.append(m.group())
            i = m.end()
        else:
            out.append(text[i])
            i += 1
    return out


_HANG_PUNCT = set("，。！？；：、）》」』…—")


def sanitize_text(text):
    """楷体缺 ✓ 字形（渲染成空心方框），映射到 √。"""
    return (text or "").replace("✓", "√").replace("✔", "√")


def wrap_text(draw, text, font, max_w):
    text = sanitize_text(text)
    lines, cur = [], ""
    for tok in _tokens(text):
        if tok == "\n":
            lines.append(cur)
            cur = ""
            continue
        if draw.textlength(cur + tok, font=font) <= max_w:
            cur += tok
        elif tok in _HANG_PUNCT and cur:
            cur += tok  # 行禁则：标点悬挂在本行尾，允许轻微越界
        else:
            lines.append(cur)
            cur = tok.lstrip()
    if cur:
        lines.append(cur)
    return lines or [""]


def element_cost(el):
    kind = el.get("kind")
    if kind == "title":
        return len(el.get("text") or "") * 0.6 + 8
    if kind == "flow":
        return len(el.get("items") or []) * 8
    if kind == "note":
        return len(el.get("text") or "") * 0.8 + 2
    return len(el.get("text") or "") + 4


def layout_scene(scene, w, h, f):
    """返回 (elements_with_geom, content_bottom)；几何按竖排流式计算。"""
    from PIL import Image, ImageDraw
    probe = ImageDraw.Draw(Image.new("RGB", (8, 8)))
    margin = int(w * 0.08)
    max_w = w - margin * 2
    top = int(h * 0.14)
    bottom_limit = int(h * 0.80)
    gap = int(h * 0.024)
    geoms, y = [], top
    for el in scene.get("elements") or []:
        kind = el.get("kind")
        g = dict(el)
        if kind == "title":
            g["font"] = f["title"]
            g["lines"] = wrap_text(probe, el.get("text") or "", f["title"], max_w)
            g["h"] = len(g["lines"]) * int(h * 0.066) + int(h * 0.02)
            g["x"] = margin
        elif kind == "point":
            g["font"] = f["point"]
            g["lines"] = wrap_text(probe, el.get("text") or "", f["point"],
                                   max_w - int(h * 0.03))
            g["h"] = len(g["lines"]) * int(h * 0.048)
            g["x"] = margin
        elif kind == "box":
            g["font"] = f["box"]
            lines = wrap_text(probe, el.get("text") or "", f["box"],
                              max_w * 0.7)
            g["lines"] = lines
            lw = max(int(probe.textlength(t, font=f["box"])) for t in lines)
            g["bw"] = min(max_w, lw + int(h * 0.06))
            g["h"] = len(lines) * int(h * 0.044) + int(h * 0.035)
            g["x"] = (w - g["bw"]) // 2
        elif kind == "flow":
            g["font"] = f["box"]
            items = [str(t) for t in (el.get("items") or [])]
            n = max(2, len(items))
            arrow_w = int(w * 0.05)
            bw = min(int(h * 0.16), (max_w - arrow_w * (n - 1)) // n)
            g["items"] = items
            g["bw"], g["arrow_w"] = bw, arrow_w
            g["lines_per"] = [wrap_text(probe, t, f["box"], bw - int(h * 0.02))
                              for t in items]
            g["h"] = max(len(ls) for ls in g["lines_per"]) * int(h * 0.040) \
                + int(h * 0.03)
            g["x"] = margin
        elif kind == "note":
            g["font"] = f["note"]
            g["lines"] = wrap_text(probe, el.get("text") or "", f["note"], max_w)
            g["h"] = len(g["lines"]) * int(h * 0.034)
            g["x"] = margin
        else:
            continue
        g["y"] = y
        y += g["h"] + gap
        geoms.append(g)
    if y > bottom_limit and geoms:
        scale = max(0.55, (bottom_limit - top) / (y - top))
        for g in geoms:
            g["y"] = top + int((g["y"] - top) * scale)
            g["font"] = None  # 重取按缩放字号
        # 重新按缩放计算字号
        rescaled = []
        for g in geoms:
            kind = g.get("kind")
            size_key = {"title": "title", "point": "point", "box": "box",
                        "flow": "box", "note": "note"}[kind]
            base = f[size_key].size
            g["font"] = load_font(
                ["C:/Windows/Fonts/simkai.ttf", "C:/Windows/Fonts/msyhbd.ttc",
                 "C:/Windows/Fonts/msyh.ttc", "C:/Windows/Fonts/simhei.ttf"],
                max(14, int(base * scale)))
            rescaled.append(g)
        geoms = rescaled
    return geoms


def draw_scene_frame(geoms, w, h, f, progress, subtitle_lines):
    """progress 0..1 = 本镜绘制进度；subtitle_lines 为当前字幕行。"""
    from PIL import Image, ImageDraw
    img = Image.new("RGB", (w, h), PAPER)
    d = ImageDraw.Draw(img)
    total = sum(g["_cost"] for g in geoms)
    acc = 0.0
    for g in geoms:
        start = acc / total
        acc += g["_cost"]
        end = acc / total
        p = min(1.0, max(0.0, (progress - start) / max(1e-6, end - start)))
        if p <= 0:
            continue
        kind = g["kind"]
        font = g["font"]
        x, y = g["x"], g["y"]
        if kind == "title":
            n_chars = int(sum(len(t) for t in g["lines"]) * p)
            shown = n_chars
            yy = y
            for line in g["lines"]:
                take = line[:shown]
                d.text((x, yy), take, font=font, fill=INK)
                shown -= len(line)
                yy += int(h * 0.066)
                if shown <= 0:
                    break
            if p >= 1.0:
                lw = int(d.textlength("".join(g["lines"])[:18], font=font))
                d.line([(x, y + int(h * 0.062)), (x + lw, y + int(h * 0.060))],
                       fill=ACCENT, width=5)
                d.line([(x + 3, y + int(h * 0.064)), (x + lw - 2, y + int(h * 0.061))],
                       fill=ACCENT, width=3)
        elif kind == "point":
            br = max(4, int(h * 0.008))
            cy = y + int(h * 0.026)
            d.ellipse([x, cy - br, x + br * 2, cy + br], fill=ACCENT)
            n_chars = int(sum(len(t) for t in g["lines"]) * p)
            shown = n_chars
            yy = y
            for line in g["lines"]:
                take = line[:max(0, shown)]
                d.text((x + int(h * 0.03), yy), take, font=font, fill=INK)
                shown -= len(line)
                yy += int(h * 0.048)
                if shown <= 0:
                    break
        elif kind == "box":
            reveal = int(g["h"] * p)
            d.rounded_rectangle([x, y, x + g["bw"], y + reveal], radius=12,
                                outline=ACCENT, width=4)
            if p >= 1.0:
                d.rounded_rectangle([x, y, x + g["bw"], y + g["h"]], radius=12,
                                    fill=(235, 242, 250), outline=ACCENT, width=4)
                yy = y + int(h * 0.016)
                for line in g["lines"]:
                    tw = d.textlength(line, font=font)
                    d.text((x + (g["bw"] - tw) // 2, yy), line, font=font, fill=INK)
                    yy += int(h * 0.044)
        elif kind == "flow":
            n = len(g["items"])
            step = g["bw"] + g["arrow_w"]
            for i in range(n):
                box_p = min(1.0, max(0.0, p * n - i))
                bx = x + i * step
                if box_p <= 0:
                    continue
                bh = int(g["h"])
                d.rounded_rectangle([bx, y, bx + g["bw"], y + int(bh * box_p)],
                                    radius=10, outline=ACCENT, width=4)
                if box_p >= 1.0:
                    yy = y + int(h * 0.012)
                    for line in g["lines_per"][i]:
                        tw = d.textlength(line, font=font)
                        d.text((bx + (g["bw"] - tw) // 2, yy), line,
                               font=font, fill=INK)
                        yy += int(h * 0.040)
                if i < n - 1 and p * n - (i + 1) > 0.35:
                    ax0, ax1 = bx + g["bw"] + 4, bx + g["bw"] + g["arrow_w"] - 6
                    ay = y + g["h"] // 2
                    d.line([(ax0, ay), (ax1, ay)], fill=ACCENT, width=5)
                    d.polygon([(ax1, ay - 7), (ax1, ay + 7), (ax1 + 7, ay)],
                              fill=ACCENT)
        elif kind == "note":
            shown = int(sum(len(t) for t in g["lines"]) * p)
            yy = y
            for line in g["lines"]:
                take = line[:max(0, shown)]
                d.text((x, yy), take, font=font, fill=GRAY)
                shown -= len(line)
                yy += int(h * 0.034)
                if shown <= 0:
                    break
    # 字幕层（唯一一层）：底部字幕带，只显示当前句
    band_y0 = int(h * 0.84)
    d.line([(int(w * 0.06), band_y0), (int(w * 0.94), band_y0)],
           fill=(210, 205, 195), width=2)
    if subtitle_lines:
        line_h = int(h * 0.040)
        total_text_h = line_h * len(subtitle_lines)
        yy = band_y0 + (int(h * 0.155) - total_text_h) // 2
        for line in subtitle_lines:
            tw = d.textlength(line, font=f["sub"])
            d.text(((w - tw) // 2, yy), line, font=f["sub"], fill=INK)
            yy += line_h
    return img


def audio_duration(path, ff):
    r = subprocess.run([ff, "-i", path], capture_output=True)
    m = re.search(rb"Duration: (\d+):(\d+):([\d.]+)", r.stderr or b"")
    if not m:
        raise RuntimeError("cannot parse duration")
    return int(m.group(1)) * 3600 + int(m.group(2)) * 60 + float(m.group(3))


def tts_scene(loop, text, voice, out_path):
    import edge_tts
    async def _go():
        com = edge_tts.Communicate(text, voice)
        await com.save(out_path)
    last = None
    for attempt in (1, 2):
        try:
            loop.run_until_complete(_go())
            if os.path.exists(out_path) and os.path.getsize(out_path) > 500:
                return
            raise RuntimeError("tts output too small")
        except Exception as e:
            last = e
            time.sleep(1.5)
    raise RuntimeError("tts failed: %s" % last)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--storyboard", required=True)
    ap.add_argument("--out", required=True)
    ap.add_argument("--report", required=True)
    args = ap.parse_args()

    report = {"ok": False, "checks": {}}
    try:
        with open(args.storyboard, encoding="utf-8") as f:
            board = json.load(f)
    except Exception as e:
        die("storyboard unreadable: %s" % e, SCHEMA_EXIT, args.report, report)
    scenes = board.get("scenes")
    if not isinstance(scenes, list) or not scenes:
        die("scenes must be non-empty list", SCHEMA_EXIT, args.report, report)
    w, h = (board.get("resolution") or [720, 1280])
    fps = int(board.get("fps") or 30)
    voice = board.get("voice") or "zh-CN-XiaoxiaoNeural"
    report.update({"resolution": [w, h], "fps": fps, "voice": voice,
                   "scenes": len(scenes)})

    try:
        import imageio.v2 as imageio
        from PIL import Image, ImageDraw
        import imageio_ffmpeg
        ff = imageio_ffmpeg.get_ffmpeg_exe()
    except Exception as e:
        die("missing render deps: %s" % e, SCHEMA_EXIT, args.report, report)

    f = fonts(w, h)
    loop = asyncio.new_event_loop()
    warnings = []
    t0 = time.time()
    try:
        with tempfile.TemporaryDirectory(prefix="handdraw_") as tmp:
            segs = []
            for idx, sc in enumerate(scenes):
                narration = sanitize_text(str(sc.get("narration") or "").strip())
                if not narration:
                    die("scene %d narration empty" % idx, SCHEMA_EXIT,
                        args.report, report)
                geoms = layout_scene(sc, w, h, f)
                if not geoms:
                    die("scene %d has no drawable elements" % idx,
                        SCHEMA_EXIT, args.report, report)
                for g in geoms:
                    g["_cost"] = element_cost(g)
                mp3 = os.path.join(tmp, "s%02d.mp3" % idx)
                try:
                    tts_scene(loop, narration, voice, mp3)
                except Exception as e:
                    die("scene %d tts failed: %s" % (idx, e), TTS_EXIT,
                        args.report, report)
                dur = audio_duration(mp3, ff)
                dur = max(2.0, dur)
                n_frames = max(2, round((dur + 0.35) * fps))
                draw_frames = max(1, int(n_frames * 0.55))
                sents = split_sentences(narration)
                char_tot = sum(len(s) for s in sents)
                silent = os.path.join(tmp, "s%02d_silent.mp4" % idx)
                # macro_block_size=1：竖屏 1080 宽不是 16 的倍数，默认 16 会走
                # 1080→1088 自动缩放链并导致编码退出码 3（2026-10-24 实案）；
                # 1080×1920 均为偶数，yuv420p 直编码无兼容问题。
                wr = imageio.get_writer(silent, fps=fps, codec="libx264",
                                        quality=6, pixelformat="yuv420p",
                                        macro_block_size=1)
                last_arr = None
                for fi in range(n_frames):
                    if fi < draw_frames:
                        progress = fi / draw_frames
                    else:
                        progress = 1.0
                    # 字幕按句切分，句内按字符占比对齐时间
                    t_sec = fi / fps
                    acc_t, sub = 0.0, sents[-1]
                    for s in sents:
                        seg = dur * len(s) / char_tot
                        if t_sec < acc_t + seg:
                            sub = s
                            break
                        acc_t += seg
                    sub_lines = wrap_text(ImageDraw.Draw(
                        Image.new("RGB", (8, 8))), sub, f["sub"],
                        int(w * 0.86))[:2]
                    img = draw_scene_frame(geoms, w, h, f, progress, sub_lines)
                    import numpy as np
                    last_arr = np.asarray(img)
                    wr.append_data(last_arr)
                wr.close()
                seg_av = os.path.join(tmp, "s%02d_av.mp4" % idx)
                r = subprocess.run(
                    [ff, "-y", "-i", silent, "-i", mp3,
                     "-map", "0:v:0", "-map", "1:a:0", "-c:v", "copy",
                     "-c:a", "aac", "-b:a", "128k", "-af", "apad=pad_dur=0.3",
                     "-movflags", "+faststart", seg_av],
                    capture_output=True)
                if r.returncode != 0 or not os.path.exists(seg_av):
                    die("scene %d mux failed: %s" % (
                        idx, (r.stderr or b"")[-300:]), MUX_EXIT,
                        args.report, report)
                segs.append(seg_av)

            list_path = os.path.join(tmp, "list.txt")
            with open(list_path, "w", encoding="ascii") as lf:
                for s in segs:
                    lf.write("file '%s'\n" % s.replace("\\", "/").replace("'", "'\\''"))
            r = subprocess.run(
                [ff, "-y", "-f", "concat", "-safe", "0", "-i", list_path,
                 "-c", "copy", "-metadata", "comment=AI-generated content",
                 "-movflags", "+faststart", args.out],
                capture_output=True)
            if r.returncode != 0 or not os.path.exists(args.out):
                die("concat failed: %s" % (r.stderr or b"")[-300:], MUX_EXIT,
                    args.report, report)
    finally:
        loop.close()

    # 工程检查：全片解码校验
    r = subprocess.run([ff, "-v", "error", "-i", args.out, "-f", "null", "-"],
                       capture_output=True)
    decode_ok = r.returncode == 0 and not (r.stderr or b"").strip()
    if not decode_ok:
        warnings.append("decode check reported issues: %s"
                        % (r.stderr or b"")[-200:])
    dur = None
    try:
        dur = audio_duration(args.out, ff)
    except Exception:
        warnings.append("final duration unparsable")
    report.update({
        "ok": decode_ok,
        "output": args.out,
        "duration_s": round(dur, 2) if dur else None,
        "size_mb": round(os.path.getsize(args.out) / 1e6, 2),
        "render_seconds": round(time.time() - t0, 1),
        "checks": {"decode": "pass" if decode_ok else "fail",
                   "audio_mux": "pass",
                   "listen_human": "not_done",
                   "cover_image": "not_produced_v1"},
        "warnings": warnings,
        "subtitle": "burn-in per sentence, single layer",
    })
    with open(args.report, "w", encoding="utf-8") as f2:
        json.dump(report, f2, ensure_ascii=False, indent=2)
    print("RENDER_OK %s" % json.dumps(
        {k: report[k] for k in ("duration_s", "size_mb", "scenes")},
        ensure_ascii=False))


if __name__ == "__main__":
    main()
