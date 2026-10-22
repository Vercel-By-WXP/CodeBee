# -*- coding: utf-8 -*-
"""handdraw_video 成片渲染编排：storyboard.json → mp4。

渲染器本体在 tools/render_handdraw.py（需 Pillow + imageio-ffmpeg + edge-tts，
本机装在 Python 3.13 64 位下）。服务端是 32 位 Python 3.8，本模块只做三件事：
校验 storyboard schema、找具备依赖的解释器、子进程跑渲染并回收结果——
不 import 任何渲染依赖，测试可全静态进行。
"""
import json
import os
import subprocess
import sys
import time

ELEMENT_KINDS = ("title", "point", "box", "flow", "note")
RENDER_TIMEOUT_S = 1500


def validate_storyboard(path):
    """返回 (board, err)；err 为 None 表示合法并可直接交给渲染器。"""
    try:
        with open(path, encoding="utf-8") as f:
            board = json.load(f)
    except Exception as e:
        return None, "storyboard.json 读取失败：%s" % e
    if not isinstance(board, dict):
        return None, "storyboard 根必须是对象"
    scenes = board.get("scenes")
    if not isinstance(scenes, list) or not scenes:
        return None, "scenes 必须是非空数组"
    res = board.get("resolution") or [720, 1280]
    if (not isinstance(res, list) or len(res) != 2
            or not all(isinstance(v, int) and 256 <= v <= 2160 for v in res)):
        return None, "resolution 必须是 [宽,高]（256~2160 的整数）"
    fps = board.get("fps") or 30
    if not isinstance(fps, int) or not 10 <= fps <= 60:
        return None, "fps 必须是 10~60 的整数"
    voice = board.get("voice")
    if voice is not None and not str(voice).strip():
        return None, "voice 不能为空字符串"
    for i, sc in enumerate(scenes):
        if not isinstance(sc, dict):
            return None, "scene[%d] 必须是对象" % i
        if not str(sc.get("narration") or "").strip():
            return None, "scene[%d].narration 不能为空" % i
        els = sc.get("elements")
        if not isinstance(els, list) or not els:
            return None, "scene[%d].elements 必须是非空数组" % i
        for j, el in enumerate(els):
            if (not isinstance(el, dict) or el.get("kind") not in ELEMENT_KINDS):
                return None, ("scene[%d].elements[%d] 非法：kind 必须是 %s 之一"
                              % (i, j, "|".join(ELEMENT_KINDS)))
            if el.get("kind") == "flow":
                if (not isinstance(el.get("items"), list)
                        or len(el["items"]) < 2
                        or not all(str(t).strip() for t in el["items"])):
                    return None, "scene[%d].elements[%d] flow 需要 items ≥2 且非空" % (i, j)
            elif not str(el.get("text") or "").strip():
                return None, "scene[%d].elements[%d].text 不能为空" % (i, j)
    return board, None


def _probe(interp):
    cmd = list(interp) + ["-c", "import PIL, imageio_ffmpeg, edge_tts"]
    try:
        r = subprocess.run(cmd, capture_output=True, timeout=120)
        return r.returncode == 0
    except Exception:
        return False


def find_interpreter():
    """依次探测 py -3.13 与当前解释器；都没有返回 None。"""
    candidates = (("py", "-3.13"), (sys.executable,))
    for base in candidates:
        if _probe(base):
            return list(base)
    return None


def render_for_run(task, workdir, timeout_s=RENDER_TIMEOUT_S):
    """返回 (ok, video_path, note)。ok=False 时 note 是可直接给人看的原因。"""
    sb_path = os.path.join(workdir, "storyboard.json")
    if not os.path.exists(sb_path):
        return False, None, "workdir 缺 storyboard.json（作者未交付渲染输入）"
    _board, err = validate_storyboard(sb_path)
    if err:
        return False, None, "storyboard.json 不合渲染契约：%s" % err
    interp = find_interpreter()
    if not interp:
        return False, None, ("未找到具备 Pillow+imageio-ffmpeg+edge-tts 的解释器"
                             "（本机请在 Python 3.13 下 pip install edge-tts imageio-ffmpeg）")
    tool = os.path.join(os.path.dirname(os.path.dirname(
        os.path.abspath(__file__))), "tools", "render_handdraw.py")
    out_path = os.path.join(workdir, "handdraw.mp4")
    rpt_path = os.path.join(workdir, "render_report.json")
    cmd = interp + ["-X", "utf8", tool, "--storyboard", sb_path,
                    "--out", out_path, "--report", rpt_path]
    t0 = time.time()
    try:
        r = subprocess.run(cmd, capture_output=True, timeout=timeout_s)
    except subprocess.TimeoutExpired:
        return False, None, "渲染超时（上限 %ds），成片未产出" % timeout_s
    took = time.time() - t0
    if r.returncode != 0 or not os.path.exists(out_path):
        tail = (r.stderr or b"").decode("utf-8", "replace").strip()[-400:]
        return False, None, "渲染器退出码 %s（耗时 %.0fs）：%s" % (
            r.returncode, took, tail or "无 stderr 输出")
    return True, out_path, "成片已产出（%.1f MB，渲染耗时 %.0fs）" % (
        os.path.getsize(out_path) / 1e6, took)
