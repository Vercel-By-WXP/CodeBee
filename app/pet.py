# -*- coding: utf-8 -*-
"""桌面蜜蜂（桌宠）：CodeBee 服务的一只常驻小蜜蜂，浮在桌面角落实时汇报蜂群动态。

参考 Codex Pets 的产品形态（/pet 唤起、随 agent 状态变化），但形象固定为
CodeBee 蜜蜂。实现要点：

- 独立进程：Tk 必须独占自家进程主线程（pick_dialog.py 同款哲学），由 main.py
  的看护线程按设置拉起；轮询 /api/pet_state 取任务近况，服务关了蜜蜂自己离开。
- 两种渲染：优先「精灵模式」——app/pet_bee.png（tools/_gen_pet_sprite.py 从
  用户提供的手绘毛绒蜜蜂生成）+ Pillow 运行时做动画帧（工作=左右倾斜摆、
  庆祝=原地旋转、睡觉=调暗降饱和、失联=灰化）；机器没有 Pillow 或素材缺失
  时回落「手绘模式」——canvas 矢量卡通蜜蜂（零依赖兜底，npm 分发用户可能
  装不了 PIL）。
- 状态机：sleep（打盹）/ work（振翅）/ cheer（翻滚庆祝）/ alert（警示抖动），
  由「上一轮活跃任务集合 vs 本轮结果」的差分推出，不需要后端记事件流。
- 悬停任务清单走指针轮询而不是 Enter/Leave：-transparentcolor 的透明像素在
  Windows 上是点击穿透的，Tk 收不到那片区域的进入/离开事件（蜜蜂还在动，
  事件时序更乱）；每 250ms 查一次指针是否落在窗口包围盒内，稳定且把整个
  窗口都变成热区。
- 设置真源是 data/settings.json（pet_enabled / pet_mode），蜜蜂轮询到关闭指令
  自行退出；「关闭桌宠」菜单就是写这个设置，看护线程因此不会把它复活。

用法：python app/pet.py --port 8765 --data-dir <data目录>
诊断：python app/pet.py --port 8765 --once   （拉一次状态打印 JSON，不开窗口）
"""
from __future__ import annotations

import argparse
import http.client
import json
import math
import os
import queue
import random
import sys
import threading
import time
import webbrowser
from pathlib import Path

# --------------------------------------------------------------- 纯逻辑（可测试）

ACTIVE_STATUSES = ("queued", "running")
GOOD_STATUSES = ("done",)
BAD_STATUSES = ("failed", "cancelled", "timeout")
POLL_MS = 4000          # 轮询间隔
POLL_TIMEOUT = 2.5      # 单次拉取超时（秒）
MAX_MISS = 15           # 连续拉不到服务 N 次后自离（约 1 分钟，防孤儿常驻）
MOOD_HOLD_S = 12.0      # cheer/alert 表情最低持续
IDLE_HIDE_S = 90.0      # tasks_only 模式：空闲多久后隐身
HOVER_MS = 250          # 指针轮询周期
HOVER_DELAY_MS = 350    # 悬停多久后才弹任务清单（扫过不弹）


def _safe_int(value, default=0, minimum=0):
    """外部状态里的计数可能是空值或脏字符串；统一兜底并限制为非负数。"""
    try:
        number = int(value)
    except (TypeError, ValueError, OverflowError):
        number = int(default)
    return max(minimum, number)


def parse_snapshot(raw):
    """把 /api/pet_state 响应裁成蜜蜂关心的最小集。字段缺失一律兜底，不抛。"""
    raw = raw if isinstance(raw, dict) else {}
    st = raw.get("settings") if isinstance(raw.get("settings"), dict) else {}
    workers = raw.get("workers") if isinstance(raw.get("workers"), dict) else {}
    digest = raw.get("digest") if isinstance(raw.get("digest"), dict) else {}
    task_rows = raw.get("tasks") if isinstance(raw.get("tasks"), list) else []
    tasks = []
    for t in task_rows[:300]:
        if not isinstance(t, dict):
            continue
        tasks.append({
            "id": str(t.get("id") or ""),
            "title": str(t.get("title") or t.get("id") or ""),
            "run_status": str(t.get("run_status") or ""),
            "steps_done": _safe_int(t.get("steps_done")),
            "steps_total": _safe_int(t.get("steps_total")),
            "step_current": str(t.get("step_current") or ""),
            "error": str(t.get("error") or ""),
        })
    return {
        # boot：服务进程启动标识（值变化=服务换人，本宠让位给新服务的蜜蜂）
        "boot": raw.get("boot") or None,
        "settings": {
            "pet_enabled": bool(st.get("pet_enabled", True)),
            "pet_mode": str(st.get("pet_mode") or "always"),
            "pet_skin": str(st.get("pet_skin") or DEFAULT_SKIN),
        },
        "workers": {
            "running": _safe_int(workers.get("running")),
            "queued": _safe_int(workers.get("queued")),
        },
        "tasks": tasks,
        "digest": {
            "unseen": _safe_int(digest.get("unseen")),
            "latest": digest.get("latest")
                      if isinstance(digest.get("latest"), dict) else None,
        },
    }


def derive_state(prev_active_ids, tasks):
    """差分推导蜜蜂状态。

    prev_active_ids：上一轮快照里 queued/running 的任务 id 集合；tasks：本轮
    任务列表。返回 (state, info)：state ∈ sleep/work/cheer/alert；info 是
    {"active": [...], "good": [...], "bad": [...]}——本轮活跃、刚完工、刚出事
    的任务（气泡文案用）。全部完成的判定只看上一轮活跃集合，天然不受历史
    失败任务污染（三年前的失败不会让蜜蜂永远举牌子）。
    """
    info = {"active": [], "good": [], "bad": []}
    for t in tasks:
        if t["run_status"] in ACTIVE_STATUSES:
            info["active"].append(t)
    if info["active"]:
        return "work", info
    if prev_active_ids:
        by_id = {t["id"]: t for t in tasks}
        for tid in prev_active_ids:
            t = by_id.get(tid)
            if t is None:
                continue
            if t["run_status"] in BAD_STATUSES:
                info["bad"].append(t)
            elif t["run_status"] in GOOD_STATUSES:
                info["good"].append(t)
        if info["bad"]:
            return "alert", info
        if info["good"]:
            return "cheer", info
    return "sleep", info


def tooltip_lines(snap, lang="zh"):
    """悬停清单：未读摘要 + 进行中 + 待启动/自动续跑，最多 6 行。

    只列「还在跑 / 待跑」的任务——历史失败项会刷满清单（截图实测十几条
    ✘ 把面板撑成一堵墙），失败已由警示气泡点名，这里不再重复。
    """
    L = LANG.get(lang, LANG["zh"])
    rows = []
    dg = snap.get("digest") or {}
    n_dg = int(dg.get("unseen") or 0)
    if n_dg > 0:
        rows.append(L["digest_tip"] % n_dg)
    for t in snap["tasks"]:
        if t["run_status"] == "running":
            prog = ""
            if t["steps_total"]:
                prog = "  %d/%d" % (t["steps_done"], t["steps_total"])
            step = (" · " + t["step_current"]) if t["step_current"] else ""
            rows.append("● %s%s%s" % (_short(t["title"]), step, prog))
    for t in snap["tasks"]:
        if t["run_status"] == "queued":
            rows.append("○ %s（%s）" % (_short(t["title"]), L["queued"]))
    return rows[:6] or [L["all_clear"]]


def _short(text, limit=22):
    """任务标题截断：标题常带长路径/长句，不截会把清单撑成一行一句。"""
    s = str(text or "").replace("\n", " ").strip()
    return s if len(s) <= limit else s[:limit] + "…"


LANG = {
    "zh": {
        "open": "打开 CodeBee",
        "mode": "显示模式",
        "always": "常驻显示",
        "tasks_only": "仅任务运行时出现",
        "skin": "形象",
        "size": "大小",
        "small": "小",
        "mid": "中",
        "big": "大",
        "reset_pos": "回到初始位置",
        "quiet": "让TA安静一会（30 分钟）",
        "hide": "先藏起来（30 分钟）",
        "quiet_ack": "好吧，俺安静会儿…",
        "lang": "语言",
        "close": "关闭桌宠（设置里可重开）",
        "all_clear": "蜂群闲着，都在打盹…",
        "title": "蜂群动态",
        "n_running": "%d 个进行中",
        "queued": "正在启动",
        "cheer": "🎉 %d 个任务完工！",
        "alert": "⚠️ 「%s」出岔子了，点我看看",
        "bye": "蜜蜂回巢啦",
        "chatter": ["蜂群今天也很努力哦", "点点俺，去蜂巢瞧瞧",
                    "代码写完记得眨眨眼~", "俺在监工，谁都不许摸鱼",
                    "你今天比昨天好看（蜂言蜂语）"],
        "digest": "🍯 「%s」有新群摘要：\n%s\n（点我细看）",
        "digest_tip": "🍯 %d 条群摘要未读，点我细看",
    },
    "en": {
        "open": "Open CodeBee",
        "mode": "Display mode",
        "always": "Always visible",
        "tasks_only": "Only when tasks run",
        "skin": "Look",
        "size": "Size",
        "small": "S",
        "mid": "M",
        "big": "L",
        "reset_pos": "Reset position",
        "quiet": "Quiet for 30 min",
        "hide": "Hide for 30 min",
        "quiet_ack": "Fine, I'll zip it…",
        "lang": "Language",
        "close": "Close the bee (re-enable in Settings)",
        "all_clear": "Hive is resting…",
        "title": "Hive activity",
        "n_running": "%d running",
        "queued": "queued",
        "cheer": "🎉 %d task(s) done!",
        "alert": "⚠️ \"%s\" ran into trouble",
        "bye": "Back to the hive",
        "chatter": ["The hive is buzzing hard today", "Click me — tour the hive",
                    "Blink once when the code compiles", "No slacking on my watch",
                    "You look better than yesterday (bee talk)"],
        "digest": "🍯 New digest for \"%s\":\n%s\n(click to read)",
        "digest_tip": "🍯 %d unread digest(s), click to read",
    },
}

# 预置形象：key → (精灵文件, 显示名)。两个名字一律用原文（品牌/形象名不翻译）。
SKINS = {
    "plush": ("pet_bee.png", "毛绒蜜蜂"),
    "robot": ("pet_bee_robot.png", "机械蜜蜂"),
}
DEFAULT_SKIN = "plush"
# 大小三档（显示高度倍率），对齐竞品宠物的「小/中/大」
SIZES = {"small": 0.82, "mid": 1.0, "big": 1.24}
DEFAULT_SIZE = "mid"
QUIET_S = 30 * 60      # 「让TA安静一会」：静音气泡时长
HIDE_S = 30 * 60       # 「先藏起来」：隐身时长（任务忙完也等满，藏就藏彻底）


def digest_bubble_text(digest, lang="zh"):
    """新群摘要气泡文案（纯函数，测试直接 import）：群名 + 摘要首行预览。"""
    latest = (digest or {}).get("latest") or {}
    group = str(latest.get("group") or "?")
    first = ""
    for ln in str(latest.get("text") or "").splitlines():
        ln = ln.strip().lstrip("-*·• ").strip()
        if ln:
            first = ln
            break
    if len(first) > 60:
        first = first[:60] + "…"
    return LANG.get(lang, LANG["zh"])["digest"] % (group, first or "…")


def digest_alert(prev_seen, digest):
    """未读摘要是否该报一次（纯函数）：返回 (新的 prev_seen, 是否提醒)。

    只增不减地比较会漏报：用户在网页点开面板会把 unseen 清零，之后新摘要
    从 1 起涨，永远小于历史峰值 → 再也不提醒。故未读归零时把基准一起归零。
    轮询回调里抛异常会中断整个 tick，故脏载荷一律当 0 处理。
    """
    try:
        un = int((digest or {}).get("unseen") or 0)
    except (TypeError, ValueError):
        un = 0
    try:
        prev = int(prev_seen or 0)
    except (TypeError, ValueError):
        prev = 0
    if un <= 0:
        return 0, False
    if un > prev:
        return un, True
    return prev, False


def _http_json(port, path, body=None):
    """本机 API 调用：GET 拉 /api/pet_state，POST 改设置（127.0.0.1 免令牌）。

    主机是字面量、端口是 argparse 的 int、路径只来自本文件里两处固定字面量
    ——没有可被外部输入摆布的 URL（Mimosa SSRF 规则的同款写法）。
    """
    conn = http.client.HTTPConnection("127.0.0.1", int(port),
                                      timeout=POLL_TIMEOUT)
    try:
        payload = None
        headers = {}
        method = "GET"
        if body is not None:
            payload = json.dumps(body).encode("utf-8")
            headers["Content-Type"] = "application/json"
            method = "POST"
        conn.request(method, path, body=payload, headers=headers)
        resp = conn.getresponse()
        data = resp.read()
        if not 200 <= resp.status < 300:
            raise RuntimeError("CodeBee API 返回 HTTP %d" % resp.status)
        return json.loads(data.decode("utf-8"))
    finally:
        conn.close()


# --------------------------------------------------------------- Tk 渲染

# 透明键色：窗口里凡是这个颜色都变透明（Windows -transparentcolor）。
# 用罕见深色，蜜蜂配色不会撞上。
KEY = "#010203"
KEY_RGB = (1, 2, 3)
WIN_W, WIN_H = 170, 150          # 手绘回落模式的窗口尺寸
SPRITE_DIR = Path(__file__).resolve().parent
SPRITE_DISP_H = 112              # 精灵显示高度（宽等比）


def _pid_alive(pid):
    """跨平台 PID 存活探测：Windows 走 OpenProcess，POSIX 用 kill 0 探针。"""
    if not pid:
        return False
    if os.name == "nt":
        try:
            import ctypes
            SYNCHRONIZE = 0x00100000
            h = ctypes.windll.kernel32.OpenProcess(SYNCHRONIZE, 0, int(pid))
            if h:
                ctypes.windll.kernel32.CloseHandle(h)
                return True
            return False
        except Exception:
            return True  # 探不动就宁可保守：当作活着，避免双开
    try:
        os.kill(int(pid), 0)
        return True
    except Exception:
        return False


def global_lock_path():
    """全机唯一单实例锁（用户拍板：桌面上只允许一只蜜蜂）。

    之前锁按数据目录分，两个服务实例（不同 TUTTI_DATA，比如开发仓 + npm 装
    的正式版，或测试冒烟）会各养一只，桌面上出现多只蜜蜂。放系统临时目录
    才是真正的全机闸：同用户任意进程都看得到它。
    """
    import tempfile
    return Path(tempfile.gettempdir()) / "codebee-pet.lock"


def global_lock_held():
    """别的蜜蜂还活着吗（锁 60s 内有心跳且 PID 存活）。看护线程用它决定
    要不要拉起，避免拉起即退出的空转。"""
    lock = global_lock_path()
    try:
        if lock.exists() and time.time() - lock.stat().st_mtime < 60:
            pid = int(json.loads(lock.read_text(encoding="utf-8")
                                 or "{}").get("pid") or 0)
            return _pid_alive(pid)
    except Exception:
        pass
    return False


class PetApp:
    """蜜蜂窗口：蜜蜂本体（精灵/手绘）+ 状态机动画 + 悬停任务清单 + 右键菜单。"""

    def __init__(self, port, data_dir, lang="zh"):
        self.port = port
        self.data_dir = Path(data_dir)
        self.lang = lang if lang in LANG else "zh"
        self.cfg_file = self.data_dir / "pet.json"
        cfg = self._load_cfg()

        import tkinter as tk
        self.tk = tk
        self.root = tk.Tk()
        self.root.title("CodeBee")
        self.root.overrideredirect(True)      # 无边框：桌宠不要标题栏和任务栏
        self.root.attributes("-topmost", True)
        if os.name == "nt":
            try:
                self.root.attributes("-transparentcolor", KEY)
            except Exception:
                pass
        self.root.configure(bg=KEY)

        # 形象：本地 cfg 优先（换形象要立刻见效，不等服务端轮询），缺省 plush
        self.skin = str(cfg.get("skin") or DEFAULT_SKIN)
        if self.skin not in SKINS:
            self.skin = DEFAULT_SKIN
        # 大小：小/中/大（显示高度倍率）
        self.size_key = str(cfg.get("size") or DEFAULT_SIZE)
        if self.size_key not in SIZES:
            self.size_key = DEFAULT_SIZE

        # 精灵帧要在 root 建好之后再做（ImageTk.PhotoImage 依赖 Tk 解释器）
        self.frames = self._load_frames(self.skin)
        if self.frames:
            self.win_w, self.win_h = self._size_for(self.frames)
        else:
            self.win_w, self.win_h = WIN_W, WIN_H

        self.cv = tk.Canvas(self.root, width=self.win_w, height=self.win_h,
                            bg=KEY, highlightthickness=0, bd=0)
        self.cv.pack()
        self.spr = None

        # 运行态
        self.snap = None
        self.miss = 0
        self.boot = None    # 首次见到的服务 boot 标识；变化=服务换进程，本宠让位
        self.prev_active = set()
        self.mood_kind, self.mood_until = "", 0.0
        self.last_busy = time.time()
        self.state = "sleep"
        self.dead_visual = False
        self.hidden = False
        self._flap_a = True
        self._lock_cnt = 0
        self._bubble = None
        self._bubble_until = 0.0
        self._tip = None
        self._hover = False
        self._tip_pending = None
        self.quiet_until = 0.0                      # 「安静一会」：静音气泡截止
        self.hide_until = 0.0                       # 「先藏起来」：隐身截止
        self.hop_until = 0.0                        # 冒气泡时的蹦跶动作截止
        self.next_chatter = time.time() + random.uniform(90, 240)
        self.seen_digests = 0   # 本轮已通知过的摘要未读数（涨了才报，不重复轰炸）
        self._polling = False
        self._poll_results = queue.Queue(maxsize=1)
        self._settings_lock = threading.Lock()
        self._settings_pending = {}
        self._settings_desired = {}
        self._settings_confirmed = {}
        self._settings_seq = 0
        self._settings_worker_running = False

        if self.frames:
            self._build_sprite()
        else:
            self._build_canvas_bee()
        self._restore_pos(cfg)
        self._bind_input()
        self._apply_state("sleep", force=True)
        self.root.after(200, self._tick)
        self.root.after(100, self._animate)
        self.root.after(HOVER_MS, self._hover_tick)

    # ---- 配置（位置+语言，蜜蜂私有，不进 settings.json）----
    def _load_cfg(self):
        try:
            data = json.loads(self.cfg_file.read_text(encoding="utf-8"))
            return data if isinstance(data, dict) else {}
        except Exception:
            return {}

    def _save_cfg(self, **kw):
        cfg = self._load_cfg()
        cfg.update(kw)
        try:
            self.cfg_file.parent.mkdir(parents=True, exist_ok=True)
            self.cfg_file.write_text(json.dumps(cfg, ensure_ascii=False),
                                     encoding="utf-8")
        except Exception:
            pass

    def _restore_pos(self, cfg):
        x, y = cfg.get("x"), cfg.get("y")
        sw = self.root.winfo_screenwidth()
        sh = self.root.winfo_screenheight()
        if not (isinstance(x, int) and isinstance(y, int)):
            x, y = sw - self.win_w - 18, sh - self.win_h - 64  # 右下角，托盘上方
        x = max(0, min(int(x), sw - self.win_w))
        y = max(0, min(int(y), sh - self.win_h - 40))          # 底部留给任务栏
        self.root.geometry("+%d+%d" % (x, y))

    def _clamp_pos(self):
        sw = self.root.winfo_screenwidth()
        sh = self.root.winfo_screenheight()
        x = max(0, min(self.root.winfo_x(), sw - self.win_w))
        y = max(0, min(self.root.winfo_y(), sh - self.win_h - 40))
        self.root.geometry("+%d+%d" % (x, y))

    # ---- 单实例锁：全机唯一（见 global_lock_path），心跳 60s，摸过就退让 ----
    def _acquire_lock(self):
        lock = global_lock_path()
        if global_lock_held():
            return False
        try:
            lock.write_text(json.dumps({"pid": os.getpid(),
                                        "ts": time.time()}), encoding="utf-8")
        except Exception:
            pass
        return True

    def _touch_lock(self):
        self._lock_cnt += 1
        if self._lock_cnt % 8:      # 约每 30s 心跳一次
            return
        try:
            global_lock_path().write_text(
                json.dumps({"pid": os.getpid(), "ts": time.time()}),
                encoding="utf-8")
        except Exception:
            pass

    # ---- 精灵模式：app/pet_bee.png + Pillow 运行时动画帧 ----
    def _load_frames(self, skin):
        """做各状态帧；Pillow/素材缺失回 None（手绘回落），绝不抛。

        所有帧都 rotate(expand=False) 在底图画布上转——帧尺寸全一致，窗口
        能贴着蜂体开（庆祝摆动 ±20° 也裁不到蜂身）。庆祝不转整圈：转圈要
        expand 放画布，窗口会比蜂体大一圈，点缀画件全悬空（真机截图翻车）。
        """
        try:
            from PIL import Image, ImageEnhance, ImageTk
        except Exception:
            return None
        fname = SKINS.get(skin, SKINS[DEFAULT_SKIN])[0]
        try:
            base0 = Image.open(SPRITE_DIR / fname).convert("RGBA")
            w0, h0 = base0.size
            disp_h = max(60, int(SPRITE_DISP_H * SIZES.get(self.size_key, 1.0)))
            disp_w = max(1, round(w0 * disp_h / h0))
            base = base0.resize((disp_w, disp_h), Image.LANCZOS)

            def flat(img):
                """合成到键色底：羽化边缘自然过渡，键色即透明，无需真 alpha。"""
                bg = Image.new("RGBA", img.size, KEY_RGB + (255,))
                bg.alpha_composite(img)
                return bg.convert("RGB")

            def bake(img):
                """RGB → PhotoImage；近键色噪点（旋转/缩放的 AA 碎点）归键。"""
                px = img.load()
                w, h = img.size
                for yy in range(h):
                    for xx in range(w):
                        r, g, b = px[xx, yy]
                        if abs(r - 1) + abs(g - 2) + abs(b - 3) < 12:
                            px[xx, yy] = KEY_RGB
                return ImageTk.PhotoImage(img)

            rot = lambda a: flat(base.rotate(a, resample=Image.BICUBIC))
            frames = {
                "alert": [bake(flat(base))],
                "sleep": [bake(ImageEnhance.Brightness(
                    ImageEnhance.Color(flat(base)).enhance(0.7)
                ).enhance(0.55))],
                "dead": [bake(ImageEnhance.Brightness(
                    flat(base).convert("L").convert("RGB")).enhance(0.6))],
                "work": [bake(rot(a)) for a in (-8, -4, 0, 4, 8)],
                # 欢腾摇摆：左倾-回正-右倾-回正，像跳舞不像旋转木马
                "cheer": [bake(rot(a)) for a in (0, 10, 20, 10, 0, -10,
                                                 -20, -10)],
            }
            return frames
        except Exception:
            return None

    def _size_for(self, frames):
        fw = max(f.width() for lst in frames.values() for f in lst)
        fh = max(f.height() for lst in frames.values() for f in lst)
        return int(fw) + 8, int(fh) + 4

    # ---- 蜜蜂绘制（精灵模式：贴图 + 状态点缀画件）----
    # 用户拍板去掉：右上徽章、左侧速度线、底部影子（真机截图圈删）——
    # 用户拍板去掉：右上徽章、左侧速度线、底部影子（真机截图圈删）、
    # 底部任务进度条（2026-09-21 真机截图圈删）——
    # 窗口贴着蜂体开，点缀只留睡觉 Zzz / 庆祝星光。
    def _build_sprite(self):
        cv = self.cv
        cx, cy = self.win_w // 2, self.win_h // 2 + 2
        self._spr_c = (cx, cy)
        self.spr = cv.create_image(cx, cy, image=self.frames["alert"][0],
                                   anchor="center")
        hw, hh = self.win_w / 2, self.win_h / 2
        self.zzz = [cv.create_text(cx + hw - 12, cy - hh + 14, text="z",
                                   fill="#8A93A6",
                                   font=("Segoe UI", 11, "italic bold")),
                    cv.create_text(cx + hw - 4, cy - hh + 4, text="Z",
                                   fill="#A5AEC0",
                                   font=("Segoe UI", 13, "italic bold"))]
        self.zzz_base = [cv.coords(z) for z in self.zzz]

        def star(x, y):
            return (x, y - 7, x + 2, y - 2, x + 7, y, x + 2, y + 2,
                    x, y + 7, x - 2, y + 2, x - 7, y, x - 2, y - 2)

        self.spark = [cv.create_polygon(star(cx - hw + 8, cy - hh + 12),
                                        fill="#FFD54D", outline="",
                                        smooth=True),
                      cv.create_polygon(star(cx + hw - 10, cy + hh - 10),
                                        fill="#FFE08A", outline="",
                                        smooth=True)]
        for grp in (self.zzz, self.spark):
            for i in grp:
                cv.itemconfigure(i, state="hidden")
        self.ox = self.oy = 0.0
        self._t0 = time.time()

    def _set_skin(self, skin):
        """换形象：本地 cfg 立即生效 + 落 settings（重启用），画布原位重建。"""
        if skin not in SKINS or skin == self.skin:
            return
        self.skin = skin
        self._save_cfg(skin=skin)
        self._post_settings({"pet_skin": skin})
        self._rebuild_sprite()

    def _rebuild_sprite(self):
        frames = self._load_frames(self.skin)
        if frames is None:
            return   # 换皮肤缺素材：保持现状，别把蜜蜂变没了
        self.frames = frames
        self.win_w, self.win_h = self._size_for(frames)
        self.cv.delete("all")
        self.cv.configure(width=self.win_w, height=self.win_h)
        self._build_sprite()
        self._apply_state(self.state, force=True)
        self._clamp_pos()

    def _set_size(self, key):
        """大小三档：重建帧+窗口（显示高度乘系数），大小记本地 cfg。"""
        if key not in SIZES or key == self.size_key:
            return
        self.size_key = key
        self._save_cfg(size=key)
        self._rebuild_sprite()

    def _reset_pos(self):
        """回到默认的右下角初始位置（对齐竞品「回到初始位置」）。"""
        self.root.geometry("+%d+%d"
                           % (self.root.winfo_screenwidth() - self.win_w - 18,
                              self.root.winfo_screenheight() - self.win_h - 64))
        self._save_cfg(x=self.root.winfo_x(), y=self.root.winfo_y())

    def _be_quiet(self):
        """安静 30 分钟：不冒气泡不碎碎念（时限内一概闭嘴；这条确认语本身
        走 force 放行）。悬停清单不受影响，该看任务还是能看。"""
        self._show_bubble(self._L("quiet_ack"), secs=4.0, force=True)
        self.quiet_until = time.time() + QUIET_S

    def _hide_awhile(self):
        """先藏起来 30 分钟：彻底隐身（时限内任务再忙也不现身），到点自己回来。"""
        self.hide_until = time.time() + HIDE_S
        self._tip_hide()
        self.hidden = True
        try:
            self.root.withdraw()
        except Exception:
            pass

    # ---- 蜜蜂绘制（手绘回落：canvas 矢量卡通）----
    def _build_canvas_bee(self):
        cv = self.cv
        self.bee = []

        def add(item_id):
            self.bee.append(item_id)
            return item_id

        # —— 翅膀（三套：振翅上/下帧 + 睡觉收翅；画在身体后面）——
        WING_A, WING_B, WING_EDGE = "#D9EAFF", "#E8F2FF", "#9FB9D8"
        self.wing_up = [add(cv.create_oval(54, 22, 92, 62, fill=WING_A,
                                           outline=WING_EDGE, width=2,
                                           stipple="gray50")),
                        add(cv.create_oval(84, 26, 124, 66, fill=WING_B,
                                           outline=WING_EDGE, width=2,
                                           stipple="gray50"))]
        self.wing_down = [add(cv.create_oval(48, 40, 90, 72, fill=WING_A,
                                             outline=WING_EDGE, width=2,
                                             stipple="gray50")),
                          add(cv.create_oval(86, 42, 128, 74, fill=WING_B,
                                             outline=WING_EDGE, width=2,
                                             stipple="gray50"))]
        self.wing_rest = [add(cv.create_oval(62, 46, 88, 62, fill="#C4D6EA",
                                             outline=WING_EDGE, width=1,
                                             stipple="gray50")),
                          add(cv.create_oval(90, 46, 116, 62, fill="#D2E0EE",
                                             outline=WING_EDGE, width=1,
                                             stipple="gray50"))]

        # —— 身体（基色 → 肚底暗色 → 环纹 → 顶部高光 → 外轮廓收边盖住接缝）——
        self.leg1 = add(cv.create_line(68, 112, 63, 126, fill="#6B4A16",
                                       width=3, capstyle="round"))
        self.leg2 = add(cv.create_line(100, 112, 97, 126, fill="#6B4A16",
                                       width=3, capstyle="round"))
        self.stinger = add(cv.create_polygon(40, 82, 22, 88, 40, 94,
                                             fill="#6B4A16", outline="",
                                             smooth=True))
        self.body = add(cv.create_oval(36, 58, 136, 118, fill="#FFD75E",
                                       outline=""))
        self.belly = add(cv.create_oval(40, 86, 132, 116, fill="#F0B93C",
                                        outline=""))
        self.stripe1 = add(cv.create_oval(58, 61, 76, 115, fill="#4A3220",
                                          outline=""))
        self.stripe2 = add(cv.create_oval(84, 61, 102, 115, fill="#4A3220",
                                          outline=""))
        self.gloss = add(cv.create_oval(44, 62, 92, 80, fill="#FFE79A",
                                        outline="", stipple="gray50"))
        self.body_edge = add(cv.create_oval(36, 58, 136, 118, fill="",
                                            outline="#6B4A16", width=2))

        # —— 触角（画在头前面但接头藏进头圆，曲线比直线柔）——
        add(cv.create_line(120, 60, 114, 44, 110, 36, fill="#3A2C25",
                           width=2, smooth=True, capstyle="round"))
        add(cv.create_line(138, 60, 146, 44, 150, 36, fill="#3A2C25",
                           width=2, smooth=True, capstyle="round"))
        add(cv.create_oval(104, 30, 116, 42, fill="#3A2C25", outline=""))
        add(cv.create_oval(144, 30, 156, 42, fill="#3A2C25", outline=""))

        # —— 头 + 脸 ——
        self.head = add(cv.create_oval(106, 54, 154, 102, fill="#3A2C25",
                                       outline="#241A14", width=2))
        self.head_gloss = add(cv.create_oval(112, 58, 130, 70, fill="#5A463C",
                                             outline="", stipple="gray50"))
        # 睁眼（大眼 + 每只双高光）
        self.eye_open = [
            add(cv.create_oval(112, 66, 130, 90, fill="#FFFFFF", outline="")),
            add(cv.create_oval(116, 72, 128, 88, fill="#241A14", outline="")),
            add(cv.create_oval(118, 74, 123, 79, fill="#FFFFFF", outline="")),
            add(cv.create_oval(125, 83, 127, 86, fill="#FFFFFF", outline="")),
            add(cv.create_oval(132, 66, 150, 90, fill="#FFFFFF", outline="")),
            add(cv.create_oval(136, 72, 148, 88, fill="#241A14", outline="")),
            add(cv.create_oval(138, 74, 143, 79, fill="#FFFFFF", outline="")),
            add(cv.create_oval(145, 83, 147, 86, fill="#FFFFFF", outline="")),
        ]
        # 闭眼（睡觉 ⌣）/ 弯弯眼（开心 ∩）
        self.eye_closed = [
            add(cv.create_arc(112, 72, 130, 86, start=180, extent=180,
                              style="arc", outline="#E8D8C8", width=2)),
            add(cv.create_arc(132, 72, 150, 86, start=180, extent=180,
                              style="arc", outline="#E8D8C8", width=2)),
        ]
        self.eye_happy = [
            add(cv.create_arc(112, 72, 130, 86, start=0, extent=180,
                              style="arc", outline="#E8D8C8", width=2)),
            add(cv.create_arc(132, 72, 150, 86, start=0, extent=180,
                              style="arc", outline="#E8D8C8", width=2)),
        ]
        # 腮红 + 嘴（微笑 / 惊呼小圆嘴）
        self.blush = [
            add(cv.create_oval(106, 90, 120, 98, fill="#E88A7D", outline="",
                               stipple="gray50")),
            add(cv.create_oval(142, 90, 156, 98, fill="#E88A7D", outline="",
                               stipple="gray50")),
        ]
        self.smile = add(cv.create_arc(124, 86, 138, 96, start=200, extent=140,
                                       style="arc", outline="#E8D8C8",
                                       width=2))
        self.mouth_o = add(cv.create_oval(127, 88, 135, 96, fill="#241A14",
                                          outline=""))

        # —— 状态点缀（用户拍板去掉徽章/速度线/影子，只留 Zzz + 星光）——
        self.zzz = [add(cv.create_text(124, 48, text="z", fill="#B9C2D0",
                                       font=("Segoe UI", 11, "italic bold"))),
                    add(cv.create_text(138, 36, text="Z", fill="#CBD4E0",
                                       font=("Segoe UI", 13, "italic bold"))),
                    add(cv.create_text(151, 26, text="z", fill="#DDE3EC",
                                       font=("Segoe UI", 10, "italic bold")))]
        self.zzz_base = [cv.coords(z) for z in self.zzz]

        def star(x, y):
            return (x, y - 7, x + 2, y - 2, x + 7, y, x + 2, y + 2,
                    x, y + 7, x - 2, y + 2, x - 7, y, x - 2, y - 2)

        self.spark = [add(cv.create_polygon(star(24, 40), fill="#FFE08A",
                                            outline="", smooth=True)),
                      add(cv.create_polygon(star(40, 104), fill="#FFD54D",
                                            outline="", smooth=True)),
                      add(cv.create_polygon(star(150, 108), fill="#FFE08A",
                                            outline="", smooth=True)),
                      add(cv.create_polygon(star(160, 60), fill="#FFF3C4",
                                            outline="", smooth=True))]

        for grp in (self.wing_up, self.wing_down, self.eye_closed,
                    self.eye_happy, [self.mouth_o], self.zzz,
                    self.spark):
            for i in grp:
                cv.itemconfigure(i, state="hidden")
        # 失联灰化要按原色还原，先把基色记账
        self._palette = {i: cv.itemcget(i, "fill")
                         for i in (self.body, self.belly)}

        # 动画游标
        self.ox = self.oy = 0.0
        self._t0 = time.time()

    def _move_bee(self, dx, dy):
        if not (dx or dy):
            return
        if self.frames:
            self.cv.move(self.spr, dx, dy)      # 精灵：整张贴图平移
        else:
            for i in self.bee:
                self.cv.move(i, dx, dy)
        self.ox += dx
        self.oy += dy

    def _show(self, ids, on):
        for i in ids:
            self.cv.itemconfigure(i, state=("normal" if on else "hidden"))

    # ---- 状态套用：精灵选帧交给 _animate；这里切点缀画件 ----
    def _apply_state(self, st, force=False):
        if st == self.state and not force:
            return
        self.state = st
        cv = self.cv
        if not self.frames:
            # —— 手绘模式专属：翅膀 / 眼睛 / 嘴 / 失联灰化 ——
            flying = st in ("work", "cheer")
            self._show(self.wing_rest, st in ("sleep", "dead"))
            if not flying:
                self._show(self.wing_up, False)
                self._show(self.wing_down, st == "alert")
            self._show(self.eye_open, st in ("work", "alert", "dead"))
            self._show(self.eye_closed, st in ("sleep", "dead"))
            self._show(self.eye_happy, st == "cheer")
            cv.itemconfigure(self.smile,
                             state=("hidden" if st == "alert" else "normal"))
            self._show([self.mouth_o], st == "alert")
            if st == "dead" and not self.dead_visual:
                self.dead_visual = True
                cv.itemconfigure(self.body, fill="#B9B9B9")
                cv.itemconfigure(self.belly, fill="#A6A6A6")
                cv.itemconfigure(self.gloss, state="hidden")
            elif st != "dead" and self.dead_visual:
                self.dead_visual = False
                cv.itemconfigure(self.body, fill=self._palette[self.body])
                cv.itemconfigure(self.belly, fill=self._palette[self.belly])
                cv.itemconfigure(self.gloss, state="normal")
        # —— 共同：Zzz / 星光（徽章、速度线、进度条均已按用户要求移除）——
        self._show(self.zzz, False)
        self._show(self.spark, False)

    def _post_settings(self, body):
        """合并并串行发送设置，保证快速连点时最后一次选择最终生效。"""
        patch = dict(body or {})
        if not patch:
            return
        with self._settings_lock:
            self._settings_seq += 1
            seq = self._settings_seq
            for key, value in patch.items():
                self._settings_pending[key] = (seq, value)
                self._settings_desired[key] = (seq, value)
            if self._settings_worker_running:
                return
            self._settings_worker_running = True

        def send():
            while True:
                with self._settings_lock:
                    if not self._settings_pending:
                        self._settings_worker_running = False
                        return
                    current = dict(self._settings_pending)
                    self._settings_pending.clear()
                try:
                    _http_json(self.port, "/api/settings",
                               body={key: item[1] for key, item in current.items()})
                    with self._settings_lock:
                        for key, item in current.items():
                            self._settings_confirmed[key] = max(
                                item[0], self._settings_confirmed.get(key, 0))
                except Exception:
                    # 新值优先；失败的旧值只补回尚未被新点击覆盖的键。
                    with self._settings_lock:
                        for key, item in current.items():
                            self._settings_pending.setdefault(key, item)
                    time.sleep(1.0)
        threading.Thread(target=send, name="pet-settings", daemon=True).start()

    def _reconcile_desired_settings(self, snap, confirmed_at_poll_start):
        """只覆盖写入确认前已发出的旧轮询；确认后的快照以服务端为准。"""
        with self._settings_lock:
            for key, item in list(self._settings_desired.items()):
                seq, value = item
                if confirmed_at_poll_start.get(key, 0) >= seq:
                    del self._settings_desired[key]
                else:
                    snap["settings"][key] = value

    # ---- 数据轮询（4s 一拍；网络请求不占 Tk 主线程）----
    def _tick(self):
        self._touch_lock()
        if self._polling:
            return
        self._polling = True
        with self._settings_lock:
            confirmed_at_poll_start = dict(self._settings_confirmed)

        def fetch():
            snap = None
            error = None
            try:
                snap = parse_snapshot(_http_json(self.port, "/api/pet_state"))
            except Exception as exc:
                error = exc
            try:
                self._poll_results.put_nowait(
                    (snap, error, confirmed_at_poll_start))
            except queue.Full:
                pass

        threading.Thread(target=fetch, name="pet-poll", daemon=True).start()
        self.root.after(50, self._collect_poll)

    def _collect_poll(self):
        try:
            snap, error, confirmed_at_poll_start = self._poll_results.get_nowait()
        except queue.Empty:
            self.root.after(50, self._collect_poll)
            return
        self._polling = False
        if error is not None:
            self.miss += 1
        else:
            self.miss = 0
        if snap is not None:
            # 服务换人检测（升级重启同端口场景）：boot 与首次见到的不一致，
            # 说明响应来自新进程——本进程是旧代码的遗老，立即让位，新服务的
            # 看护会 spawn 带新形象的新蜜蜂（旧形象常驻的根因修复）。
            boot = snap.get("boot")
            if boot:
                if self.boot is None:
                    self.boot = boot
                elif boot != self.boot:
                    return self._bye()
            self._reconcile_desired_settings(snap, confirmed_at_poll_start)
            if not snap["settings"]["pet_enabled"]:
                return self._bye()
            self.snap = snap
            # 设置页换形象：服务端设置为准，原位换肤（菜单换肤走 _set_skin 落同一处）
            sk = str(snap["settings"].get("pet_skin") or "")
            if sk in SKINS and sk != self.skin:
                self.skin = sk
                self._save_cfg(skin=sk)
                self._rebuild_sprite()
            raw, info = derive_state(self.prev_active, snap["tasks"])
            self.prev_active = {t["id"] for t in snap["tasks"]
                                if t["run_status"] in ACTIVE_STATUSES}
            now = time.time()
            if raw in ("cheer", "alert"):
                self.mood_kind, self.mood_until = raw, now + MOOD_HOLD_S
                self._bubble(raw, info)
                self.last_busy = now
            elif raw == "work":
                self.last_busy = now
            st = raw if raw != "sleep" else (
                (self.mood_kind or "sleep") if now < self.mood_until
                else "sleep")
            # 群摘要喂食：未读数涨了才报一次（气泡 9s），睡觉被打断就欢腾一下；
            # tasks_only 模式下把报摘要当作「忙」，隐身中的蜜蜂会因此现身。
            dg = snap.get("digest") or {}
            self.seen_digests, notify = digest_alert(self.seen_digests, dg)
            if notify:
                self._show_bubble(digest_bubble_text(dg, self.lang), secs=9.0)
                self.last_busy = now
                if st == "sleep":
                    self.mood_kind, self.mood_until = "cheer", now + MOOD_HOLD_S
                    st = "cheer"
            snap["active_ids"] = list(self.prev_active)
            self._apply_state(st)
            self._sync_visibility(st)
            if self._tip is not None and self._hover:
                self._tip_update()
            # 待机台词：睡觉且不害羞（未静音/未藏起）时偶尔冒一句，像有性格
            now = time.time()
            if (st == "sleep" and not self.hidden
                    and now > self.next_chatter
                    and now >= self.quiet_until):
                self._show_bubble(self._L(random.choice(LANG[self.lang]
                                                        ["chatter"])),
                                  secs=6.0)
            self.next_chatter = max(self.next_chatter,
                                    now + random.uniform(240, 540))
        if self.miss >= MAX_MISS:
            return self._bye()
        if self.miss >= 2:
            self._apply_state("dead")
            self._sync_visibility("dead")
        self.root.after(POLL_MS, self._tick)

    def _sync_visibility(self, st):
        """tasks_only 模式：空闲超过阈值隐身，忙起来立刻回来。"""
        mode = "always"
        if self.snap:
            mode = self.snap["settings"]["pet_mode"]
        want_visible = (mode != "tasks_only" or st not in ("sleep", "dead") or
                        time.time() - self.last_busy < IDLE_HIDE_S)
        if time.time() < self.hide_until:
            want_visible = False   # 「先藏起来」：时限内隐身，忙也叫不应
        if want_visible and self.hidden:
            self.hidden = False
            try:
                self.root.deiconify()
                self._clamp_pos()
            except Exception:
                pass
        elif not want_visible and not self.hidden:
            self.hidden = True
            try:
                self.root.withdraw()
                self._tip_hide()
            except Exception:
                pass

    # ---- 动画（100ms 一帧）----
    def _animate(self):
        t = time.time() - self._t0
        if self._press:      # 拖动中冻结姿态：窗口跟手优先，别让蜂在框里乱飘
            self.root.after(100, self._animate)
            return
        st = self.state
        if st == "work":
            tx, ty = math.sin(t * 2.1) * 4.0, math.sin(t * 4.2) * 5.0
        elif st == "cheer":
            # 摇摆舞 + 小跳：蹦跶比原地晃欢腾得多
            tx = math.sin(t * 6.0) * 8.0
            ty = -abs(math.sin(t * 5.0)) * 10.0
        elif st == "alert":
            tx = 3.0 if int(t * 5) % 2 else -3.0
            ty = 0.0
        elif st == "dead":
            tx, ty = 0.0, 0.0
        else:   # sleep：坐得低一点，呼吸式微沉浮
            tx, ty = 0.0, 3.0 + math.sin(t * 1.2) * 1.5
        # 冒气泡时蹦一下（所有状态通用的活性小动作）
        now = time.time()
        if now < self.hop_until:
            k = (self.hop_until - now) / 0.7
            ty -= abs(math.sin(k * math.pi)) * 9.0
        if self.frames:
            # —— 精灵选帧：单帧状态直接用；work 高频摆=振翅悬浮；
            #     sleep 每 18s 借工作帧慢慢伸个懒腰，坐着也有活物感 ——
            if st == "work":
                wl = self.frames["work"]
                k = int(t * 10) % (2 * (len(wl) - 1))
                img = wl[k if k < len(wl) else 2 * (len(wl) - 1) - k]
            elif st == "cheer":
                cl = self.frames["cheer"]
                img = cl[int(t * 8) % len(cl)]
            elif st == "sleep":
                img = self.frames["sleep"][0]
                cyc = t % 18.0
                if cyc < 2.4:
                    wl = self.frames["work"]
                    img = wl[int(round(abs(math.sin(cyc / 2.4 * math.pi))
                                       * (len(wl) - 1)))]
            else:
                img = self.frames[st][0]
            if self.spr is not None:
                self.cv.itemconfigure(self.spr, image=img)
        else:
            if st in ("work", "cheer"):
                self._flap()
        self._move_bee(tx - self.ox, ty - self.oy)
        if st == "sleep":
            n = len(self.zzz)
            phase = int(t * 1.6) % (n + 1)
            for i, z in enumerate(self.zzz):
                self.cv.itemconfigure(z, state=("normal" if i < phase
                                                else "hidden"))
            rise = (t * 8) % 10
            for k, z in enumerate(self.zzz):
                self.cv.coords(z, self.zzz_base[k][0],
                               self.zzz_base[k][1] - rise * 0.4)
        if st == "cheer":
            on = int(t * 6) % 2
            for i, s in enumerate(self.spark):
                self.cv.itemconfigure(s, state=("normal" if (i % 2) == on
                                                else "hidden"))
        self.root.after(100, self._animate)

    def _flap(self):
        self._flap_a = not self._flap_a
        self._show(self.wing_up, self._flap_a)
        self._show(self.wing_down, not self._flap_a)

    # ---- 悬停任务清单（指针轮询：透明像素点击穿透，Enter/Leave 收不全）----
    def _hover_tick(self):
        try:
            px, py = self.root.winfo_pointerx(), self.root.winfo_pointery()
            wx, wy = self.root.winfo_x(), self.root.winfo_y()
            inside = (not self.hidden and
                      wx <= px < wx + self.win_w and
                      wy <= py < wy + self.win_h)
        except Exception:
            inside = False
        if inside and not self._hover:
            self._hover = True
            self._tip_pending = self.root.after(HOVER_DELAY_MS, self._tip_show)
        elif not inside and self._hover:
            self._hover = False
            if self._tip_pending:
                try:
                    self.root.after_cancel(self._tip_pending)
                except Exception:
                    pass
                self._tip_pending = None
            self._tip_hide()
        self.root.after(HOVER_MS, self._hover_tick)

    def _tip_show(self):
        self._tip_pending = None
        if not self._hover:
            return
        if self._tip is None:
            tk = self.tk
            tip = tk.Toplevel(self.root)
            tip.overrideredirect(True)
            try:
                tip.attributes("-topmost", True)
                tip.configure(bg="#1B2430")
            except Exception:
                pass
            self._tip_head = tk.Label(tip, text=self._tip_header(),
                                      bg="#1B2430", fg="#9FB9D8",
                                      font=("Microsoft YaHei UI", 8))
            self._tip_head.pack(anchor="w", padx=10, pady=(6, 0))
            self._tip_body = tk.Label(tip, text="", bg="#1B2430",
                                      fg="#ECF2F8",
                                      font=("Microsoft YaHei UI", 9),
                                      justify="left", wraplength=280)
            self._tip_body.pack(anchor="w", padx=10, pady=(2, 8))
            self._tip = tip
        self._tip_update()
        try:
            self._tip.deiconify()
            self._tip.lift()
        except Exception:
            pass

    def _tip_header(self):
        n = 0
        if self.snap:
            n = sum(1 for t in self.snap["tasks"]
                    if t["run_status"] in ACTIVE_STATUSES)
        return (self._L("title") + " · " + self._L("n_running") % n) if n \
            else self._L("title")

    def _tip_update(self):
        if self._tip is None:
            return
        body = "\n".join(tooltip_lines(self.snap or {"tasks": []}, self.lang))
        try:
            self._tip_head.configure(text=self._tip_header())
            self._tip_body.configure(text=body)
            self._tip_place()
        except Exception:
            pass

    def _tip_place(self):
        try:
            self._tip.update_idletasks()
            tw, th = self._tip.winfo_width(), self._tip.winfo_height()
            wx, wy = self.root.winfo_x(), self.root.winfo_y()
            sw = self.root.winfo_screenwidth()
            x = wx + self.win_w + 8
            if x + tw > sw - 4:
                x = max(4, wx - tw - 8)
            y = max(4, min(wy + 2, self.root.winfo_screenheight() - th - 40))
            self._tip.geometry("+%d+%d" % (x, y))
        except Exception:
            pass

    def _tip_hide(self):
        self._destroy_win("_tip")

    # ---- 气泡 / 菜单 ----
    def _bubble(self, kind, info):
        if kind == "cheer":
            text = self._L("cheer") % max(1, len(info["good"]))
        else:
            bad = info["bad"][0] if info["bad"] else {"title": "?"}
            text = self._L("alert") % bad.get("title", "?")
        self._show_bubble(text)

    def _show_bubble(self, text, secs=5.0, force=False):
        if not force and time.time() < self.quiet_until:
            return   # 「安静一会」期间不碎碎念（系统确认语用 force 放行）
        self._destroy_win("_bubble")
        self.hop_until = time.time() + 0.7   # 冒泡蹦一下，像在说话
        tk = self.tk
        bub = tk.Toplevel(self.root)
        bub.overrideredirect(True)
        try:
            bub.attributes("-topmost", True)
        except Exception:
            pass
        tk.Label(bub, text=text, bg="#FFF8E1", fg="#4A3200",
                 bd=1, relief="solid",
                 font=("Microsoft YaHei UI", 9), justify="left",
                 wraplength=230, padx=8, pady=5).pack()
        x = self.root.winfo_x() + self.win_w - 40
        y = max(0, self.root.winfo_y() - 8)
        bub.update_idletasks()
        if x + bub.winfo_width() > self.root.winfo_screenwidth():
            x = self.root.winfo_screenwidth() - bub.winfo_width() - 8
        bub.geometry("+%d+%d" % (x, y))
        self._bubble = bub
        self._bubble_until = time.time() + secs
        self.root.after(int(secs * 1000), self._hide_bubble)

    def _hide_bubble(self):
        if time.time() < self._bubble_until:
            self.root.after(500, self._hide_bubble)
            return
        self._destroy_win("_bubble")

    def _destroy_win(self, attr):
        try:
            win = getattr(self, attr, None)
            if win is not None:
                win.destroy()
        except Exception:
            pass
        setattr(self, attr, None)

    def _menu(self):
        mode = "always"
        if self.snap:
            mode = self.snap["settings"]["pet_mode"]
        self._mode_var.set(mode)
        self._skin_var.set(self.skin)
        self._size_var.set(self.size_key)
        self._lang_var.set(self.lang)
        m = self.tk.Menu(self.root, tearoff=0)
        m.add_command(label=self._L("open"), command=self._open_ui)
        m.add_separator()
        m.add_command(label="◎ " + self._L("mode"), state="disabled")
        m.add_radiobutton(label="    " + self._L("always"),
                          command=lambda: self._set_mode("always"),
                          variable=self._mode_var, value="always")
        m.add_radiobutton(label="    " + self._L("tasks_only"),
                          command=lambda: self._set_mode("tasks_only"),
                          variable=self._mode_var, value="tasks_only")
        m.add_separator()
        m.add_command(label="◎ " + self._L("skin"), state="disabled")
        for key, (_, name) in SKINS.items():
            m.add_radiobutton(label="    " + name,
                              command=lambda k=key: self._set_skin(k),
                              variable=self._skin_var, value=key)
        m.add_command(label="◎ " + self._L("size"), state="disabled")
        for key in ("small", "mid", "big"):
            m.add_radiobutton(label="    " + self._L(key),
                              command=lambda k=key: self._set_size(k),
                              variable=self._size_var, value=key)
        m.add_separator()
        m.add_command(label=self._L("reset_pos"), command=self._reset_pos)
        m.add_command(label=self._L("quiet"), command=self._be_quiet)
        m.add_command(label=self._L("hide"), command=self._hide_awhile)
        m.add_separator()
        m.add_command(label="◎ " + self._L("lang"), state="disabled")
        m.add_radiobutton(label="    中文",
                          command=lambda: self._set_lang("zh"),
                          variable=self._lang_var, value="zh")
        m.add_radiobutton(label="    English",
                          command=lambda: self._set_lang("en"),
                          variable=self._lang_var, value="en")
        m.add_separator()
        m.add_command(label=self._L("close"),
                      command=lambda: self._bye(write_setting=True))
        return m

    def _set_mode(self, mode):
        self._post_settings({"pet_mode": mode})

    def _set_lang(self, lang):
        self.lang = lang
        self._save_cfg(lang=lang)

    def _open_ui(self):
        try:
            webbrowser.open("http://127.0.0.1:%d" % self.port)
        except Exception:
            pass

    def _bind_input(self):
        # 菜单变量须先于 _menu 存在
        self._mode_var = self.tk.StringVar(value="always")
        self._skin_var = self.tk.StringVar(value=self.skin)
        self._size_var = self.tk.StringVar(value=self.size_key)
        self._lang_var = self.tk.StringVar(value=self.lang)
        cv = self.cv
        self._press = None
        self._moved = False
        self._drag_to = None
        self._drag_pending = False
        cv.bind("<ButtonPress-1>", self._on_press)
        cv.bind("<B1-Motion>", self._on_motion)
        cv.bind("<ButtonRelease-1>", self._on_release)
        cv.bind("<Button-3>", lambda e: self._menu().tk_popup(e.x_root,
                                                              e.y_root))
        self.root.protocol("WM_DELETE_WINDOW", self._bye)

    def _on_press(self, e):
        # 窗口拖动起点：屏幕坐标 + 窗口原点一起记（scan_mark/scan_dragto 是
        # canvas 系 widget 的子命令，顶层窗口没有——此前绑定在这里的拖动
        # 实际全部抛 AttributeError 被 except 吞掉，表现为「拖不动」）
        self._press = (e.x_root, e.y_root, self.root.winfo_x(), self.root.winfo_y())
        self._moved = False

    def _on_motion(self, e):
        if not self._press:
            return
        px, py, wx, wy = self._press
        if (abs(e.x_root - px) + abs(e.y_root - py) > 4):
            self._moved = True
        # geometry 逐事件会卡成 PPT；这里合并到 ~60fps 节流（事件风暴只在
        # 节流窗内记终点，一次 geometry 落位），实测跟手且不吃 CPU
        self._drag_to = (wx + (e.x_root - px), wy + (e.y_root - py))
        if self._drag_pending:
            return
        self._drag_pending = True
        self.root.after(16, self._apply_drag)

    def _apply_drag(self):
        self._drag_pending = False
        if not self._drag_to:
            return
        try:
            self.root.geometry("+%d+%d" % self._drag_to)
        except Exception:
            pass

    def _on_release(self, e):
        moved, self._moved = self._moved, False
        self._press = None
        if moved:
            self._clamp_pos()
            self._save_cfg(x=self.root.winfo_x(), y=self.root.winfo_y())
        else:
            self._open_ui()

    def _bye(self, write_setting=False):
        """退出。write_setting=True（右键菜单「关闭桌宠」）先把 pet_enabled=False
        写回服务端设置——否则看护/下次启动都会按设置里的 enabled 把蜜蜂复活，
        用户点关闭等于没关（2026-09-21 用户实测「宠物关不了」的根因：菜单
        关闭只销毁窗口不落设置，与注释宣称的契约相反）。服务端 pet_enabled
        已为 False 的自离路径（轮询发现/miss 超限）无需再写。"""
        if write_setting:
            try:
                self._post_settings({"pet_enabled": False})
            except Exception:
                pass
        try:
            self._save_cfg(x=self.root.winfo_x(), y=self.root.winfo_y())
        except Exception:
            pass
        try:
            global_lock_path().unlink()
        except Exception:
            pass
        try:
            self.root.destroy()
        except Exception:
            pass

    def _L(self, key):
        return LANG[self.lang][key]


def main():
    ap = argparse.ArgumentParser(description="CodeBee 桌面蜜蜂")
    ap.add_argument("--port", type=int, default=8765)
    ap.add_argument("--data-dir", default=os.environ.get("TUTTI_DATA")
                    or str(Path.home() / ".codebee"))
    ap.add_argument("--lang", default="zh", choices=("zh", "en"))
    ap.add_argument("--once", action="store_true",
                    help="拉一次状态推导蜜蜂状态并打印 JSON，不开窗口（诊断/测试）")
    args = ap.parse_args()

    try:
        sys.stdout.reconfigure(encoding="utf-8")  # py3.7+；重复设无害
    except Exception:
        pass

    if args.once:
        out = {"ok": False, "state": "sleep", "port": args.port}
        try:
            snap = parse_snapshot(_http_json(args.port, "/api/pet_state"))
            st, info = derive_state(
                {t["id"] for t in snap["tasks"]
                 if t["run_status"] in ACTIVE_STATUSES}, snap["tasks"])
            out.update({"ok": True, "state": st,
                        "workers": snap["workers"],
                        "settings": snap["settings"],
                        "digest": snap.get("digest") or {},
                        "active": len(info["active"]),
                        "good": len(info["good"]),
                        "bad": len(info["bad"])})
        except Exception as e:
            out["error"] = str(e)
        print(json.dumps(out, ensure_ascii=False))
        return

    try:
        import tkinter as tk  # noqa: F401  （提前探，缺了给人话而不是堆栈）
    except Exception:
        print("[CodeBee] 这台机器的 Python 没有 tkinter，桌面蜜蜂养不了"
              "（其余功能不受影响）")
        return 3

    try:
        from ctypes import windll
        windll.shcore.SetProcessDpiAwareness(1)   # 高分屏不发糊（失败无所谓）
    except Exception:
        pass

    app = PetApp(args.port, args.data_dir, lang=args.lang)
    if not app._acquire_lock():
        print("[CodeBee] 已有一只蜜蜂在岗，不多养一只")
        return 0
    app.root.mainloop()


if __name__ == "__main__":
    sys.exit(main())
