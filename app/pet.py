# -*- coding: utf-8 -*-
"""桌面蜜蜂（桌宠）：CodeBee 服务的一只常驻小蜜蜂，浮在桌面角落实时汇报蜂群动态。

参考 Codex Pets 的产品形态（/pet 唤起、随 agent 状态变化），但形象固定为
CodeBee 蜜蜂（与 UI 蜂巢工作台同一品牌语言）。实现要点：

- 独立进程：Tk 必须独占自家进程主线程（pick_dialog.py 同款哲学），由 main.py
  的看护线程按设置拉起；轮询 /api/pet_state 取任务近况，服务关了蜜蜂自己离开。
- 零依赖：tkinter 标准库自绘蜜蜂（canvas 矢量卡通风：圆润身体、大眼双高光、
  腮红、软阴影、星光点缀），不引任何第三方包；纯逻辑（状态推导）放模块顶层
  不碰 tkinter，测试直接 import。
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
import sys
import time
import webbrowser
from pathlib import Path

# --------------------------------------------------------------- 纯逻辑（可测试）

ACTIVE_STATUSES = ("queued", "running")
BAD_STATUSES = ("failed", "cancelled")
POLL_MS = 4000          # 轮询间隔
POLL_TIMEOUT = 2.5      # 单次拉取超时（秒）
MAX_MISS = 15           # 连续拉不到服务 N 次后自离（约 1 分钟，防孤儿常驻）
MOOD_HOLD_S = 12.0      # cheer/alert 表情最低持续
IDLE_HIDE_S = 90.0      # tasks_only 模式：空闲多久后隐身
HOVER_MS = 250          # 指针轮询周期
HOVER_DELAY_MS = 350    # 悬停多久后才弹任务清单（扫过不弹）


def parse_snapshot(raw):
    """把 /api/pet_state 响应裁成蜜蜂关心的最小集。字段缺失一律兜底，不抛。"""
    raw = raw if isinstance(raw, dict) else {}
    st = raw.get("settings") or {}
    tasks = []
    for t in (raw.get("tasks") or [])[:300]:
        if not isinstance(t, dict):
            continue
        tasks.append({
            "id": str(t.get("id") or ""),
            "title": str(t.get("title") or t.get("id") or ""),
            "run_status": str(t.get("run_status") or ""),
            "steps_done": int(t.get("steps_done") or 0),
            "steps_total": int(t.get("steps_total") or 0),
            "step_current": str(t.get("step_current") or ""),
            "error": str(t.get("error") or ""),
        })
    return {
        "settings": {
            "pet_enabled": bool(st.get("pet_enabled", True)),
            "pet_mode": str(st.get("pet_mode") or "always"),
        },
        "workers": {
            "running": int((raw.get("workers") or {}).get("running") or 0),
            "queued": int((raw.get("workers") or {}).get("queued") or 0),
        },
        "tasks": tasks,
        "digest": {
            "unseen": int((raw.get("digest") or {}).get("unseen") or 0),
            "latest": (raw.get("digest") or {}).get("latest")
                      if isinstance((raw.get("digest") or {}).get("latest"), dict)
                      else None,
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
            if t is None or t["run_status"] not in BAD_STATUSES:
                info["good"].append(t or {"id": tid, "title": tid})
            else:
                info["bad"].append(t)
        if info["bad"]:
            return "alert", info
        if info["good"]:
            return "cheer", info
    return "sleep", info


def tooltip_lines(snap, lang="zh"):
    """悬停清单的任务行：未读摘要在最上，其后进行中、排队、失败，最多 8 行。"""
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
            rows.append("● %s%s%s" % (t["title"], step, prog))
    for t in snap["tasks"]:
        if t["run_status"] == "queued":
            rows.append("○ %s（%s）" % (t["title"], L["queued"]))
    for t in snap["tasks"]:
        if t["run_status"] in BAD_STATUSES:
            rows.append("✘ %s" % t["title"])
    return rows[:8] or [L["all_clear"]]


LANG = {
    "zh": {
        "open": "打开 CodeBee",
        "mode": "显示模式",
        "always": "常驻显示",
        "tasks_only": "仅任务运行时出现",
        "lang": "语言",
        "close": "关闭桌宠（设置里可重开）",
        "all_clear": "蜂群闲着，都在打盹…",
        "title": "蜂群动态",
        "n_running": "%d 个进行中",
        "queued": "排队中",
        "cheer": "🎉 %d 个任务完工！",
        "alert": "⚠️ 「%s」出岔子了，点我看看",
        "bye": "蜜蜂回巢啦",
        "digest": "🍯 「%s」有新群摘要：\n%s\n（点我细看）",
        "digest_tip": "🍯 %d 条群摘要未读，点我细看",
    },
    "en": {
        "open": "Open CodeBee",
        "mode": "Display mode",
        "always": "Always visible",
        "tasks_only": "Only when tasks run",
        "lang": "Language",
        "close": "Close the bee (re-enable in Settings)",
        "all_clear": "Hive is resting…",
        "title": "Hive activity",
        "n_running": "%d running",
        "queued": "queued",
        "cheer": "🎉 %d task(s) done!",
        "alert": "⚠️ \"%s\" ran into trouble",
        "bye": "Back to the hive",
        "digest": "🍯 New digest for \"%s\":\n%s\n(click to read)",
        "digest_tip": "🍯 %d unread digest(s), click to read",
    },
}


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
        return json.loads(resp.read().decode("utf-8"))
    finally:
        conn.close()


# --------------------------------------------------------------- Tk 渲染

# 透明键色：窗口里凡是这个颜色都变透明（Windows -transparentcolor）。
# 用罕见深色，蜜蜂配色不会撞上。
KEY = "#010203"
WIN_W, WIN_H = 170, 150


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


class PetApp:
    """蜜蜂窗口：一只卡通风矢量蜜蜂 + 状态机动画 + 悬停任务清单 + 右键菜单。"""

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

        self.cv = tk.Canvas(self.root, width=WIN_W, height=WIN_H,
                            bg=KEY, highlightthickness=0, bd=0)
        self.cv.pack()

        # 运行态
        self.snap = None
        self.miss = 0
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
        self.seen_digests = 0   # 本轮已通知过的摘要未读数（涨了才报，不重复轰炸）

        self._build_bee()
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
            x, y = sw - WIN_W - 18, sh - WIN_H - 64   # 默认右下角，托盘上方
        x = max(0, min(int(x), sw - WIN_W))
        y = max(0, min(int(y), sh - WIN_H - 40))      # 底部留 40px 给任务栏
        self.root.geometry("+%d+%d" % (x, y))

    def _clamp_pos(self):
        sw = self.root.winfo_screenwidth()
        sh = self.root.winfo_screenheight()
        x = max(0, min(self.root.winfo_x(), sw - WIN_W))
        y = max(0, min(self.root.winfo_y(), sh - WIN_H - 40))
        self.root.geometry("+%d+%d" % (x, y))

    # ---- 单实例锁：心跳文件，别的蜜蜂 60s 内摸过就退让 ----
    def _acquire_lock(self):
        lock = self.data_dir / "pet.lock"
        try:
            if lock.exists() and time.time() - lock.stat().st_mtime < 60:
                try:
                    pid = int(json.loads(lock.read_text(encoding="utf-8")
                                         or "{}").get("pid") or 0)
                except Exception:
                    pid = 0
                if _pid_alive(pid):
                    return False
        except Exception:
            pass
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
            (self.data_dir / "pet.lock").write_text(
                json.dumps({"pid": os.getpid(), "ts": time.time()}),
                encoding="utf-8")
        except Exception:
            pass

    # ---- 蜜蜂绘制（卡通风：圆润身体 + 大眼双高光 + 腮红 + 软阴影 + 星光）----
    # 画件分两类：随蜜蜂整体平移的进 self.bee；贴地不动的（影子/进度条）单独存。
    def _build_bee(self):
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
        # 睁眼（大眼 + 每只双高光，萌点全在这）
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

        # —— 状态点缀 ——
        self.badge = add(cv.create_oval(146, 22, 166, 42, fill="#4A90D9",
                                        outline="#FFFFFF", width=2))
        self.badge_txt = add(cv.create_text(156, 32, text="", fill="#FFFFFF",
                                            font=("Segoe UI", 10, "bold")))
        self.speed = [add(cv.create_line(14, 72, 32, 72, fill="#A9C3E2",
                                         width=3, capstyle="round")),
                      add(cv.create_line(8, 88, 30, 88, fill="#A9C3E2",
                                         width=3, capstyle="round"))]
        self.zzz = [add(cv.create_text(124, 48, text="z", fill="#B9C2D0",
                                       font=("Segoe UI", 11, "italic bold"))),
                    add(cv.create_text(138, 36, text="Z", fill="#CBD4E0",
                                       font=("Segoe UI", 13, "italic bold"))),
                    add(cv.create_text(151, 26, text="z", fill="#DDE3EC",
                                       font=("Segoe UI", 10, "italic bold")))]

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
                    self.eye_happy, [self.mouth_o], self.speed, self.zzz,
                    self.spark):
            for i in grp:
                cv.itemconfigure(i, state="hidden")
        cv.itemconfigure(self.badge, state="hidden")
        cv.itemconfigure(self.badge_txt, state="hidden")
        # 失联灰化要按原色还原，先把基色记账
        self._palette = {i: cv.itemcget(i, "fill")
                         for i in (self.body, self.belly)}

        # —— 贴地件（不随蜜蜂移动）——
        self.shadow = cv.create_oval(52, 126, 124, 137, fill="#232E3B",
                                     outline="", stipple="gray25")
        self.pbar_bg = cv.create_rectangle(46, 139, 128, 147, fill="#202A36",
                                           outline="#3A4654")
        self.pbar_fg = cv.create_rectangle(47, 140, 48, 146, fill="#7ED07E",
                                           outline="")
        cv.itemconfigure(self.pbar_bg, state="hidden")
        cv.itemconfigure(self.pbar_fg, state="hidden")

        # 动画游标
        self.ox = self.oy = 0.0
        self._t0 = time.time()

    def _move_bee(self, dx, dy):
        if dx or dy:
            for i in self.bee:
                self.cv.move(i, dx, dy)
            self.ox += dx
            self.oy += dy

    def _show(self, ids, on):
        for i in ids:
            self.cv.itemconfigure(i, state=("normal" if on else "hidden"))

    # ---- 状态套用：切换眼睛/翅膀/嘴/徽章/进度条 ----
    def _apply_state(self, st, force=False):
        if st == self.state and not force:
            return
        self.state = st
        cv = self.cv
        # 翅膀：睡收翅；警示下压定住；飞行两帧由 _flap 接管
        flying = st in ("work", "cheer")
        self._show(self.wing_rest, st in ("sleep", "dead"))
        if not flying:
            self._show(self.wing_up, False)
            self._show(self.wing_down, st == "alert")
        # 眼睛
        self._show(self.eye_open, st in ("work", "alert", "dead"))
        self._show(self.eye_closed, st in ("sleep", "dead"))
        self._show(self.eye_happy, st == "cheer")
        # 嘴：警示惊呼小圆嘴，其余微笑
        cv.itemconfigure(self.smile,
                         state=("hidden" if st == "alert" else "normal"))
        self._show([self.mouth_o], st == "alert")
        # 徽章
        n_active = 0
        if self.snap:
            n_active = len(self.snap.get("active_ids") or [])
        if st == "work":
            cv.itemconfigure(self.badge, fill="#4A90D9", state="normal")
            cv.itemconfigure(self.badge_txt, text=(str(n_active)
                                                   if n_active > 1 else "…"),
                             state="normal")
        elif st == "alert":
            cv.itemconfigure(self.badge, fill="#D9484A", state="normal")
            cv.itemconfigure(self.badge_txt, text="!", state="normal")
        else:
            cv.itemconfigure(self.badge, state="hidden")
            cv.itemconfigure(self.badge_txt, state="hidden")
        self._show(self.speed, False)
        self._show(self.zzz, False)
        self._show(self.spark, False)
        # 进度条只在工作且有步骤进度时显示
        prog = []
        if st == "work" and self.snap:
            prog = [t for t in self.snap["tasks"]
                    if t["run_status"] == "running" and t["steps_total"]]
        if prog:
            done = sum(t["steps_done"] for t in prog)
            total = sum(t["steps_total"] for t in prog)
            ratio = max(0.0, min(1.0, done / float(total)))
            cv.itemconfigure(self.pbar_bg, state="normal")
            cv.itemconfigure(self.pbar_fg, state="normal")
            cv.coords(self.pbar_fg, 47, 140, 47 + 80 * ratio, 146)
        else:
            self._show([self.pbar_bg, self.pbar_fg], False)
        # 服务失联灰化（按记账原色还原）
        if st == "dead" and not self.dead_visual:
            self.dead_visual = True
            cv.itemconfigure(self.body, fill="#B9B9B9")
            cv.itemconfigure(self.belly, fill="#A6A6A6")
            cv.itemconfigure(self.gloss, state="hidden")
            cv.itemconfigure(self.badge, fill="#8A93A6", state="normal")
            cv.itemconfigure(self.badge_txt, text="?", state="normal")
        elif st != "dead" and self.dead_visual:
            self.dead_visual = False
            cv.itemconfigure(self.body, fill=self._palette[self.body])
            cv.itemconfigure(self.belly, fill=self._palette[self.belly])
            cv.itemconfigure(self.gloss, state="normal")

    # ---- 数据轮询（4s 一拍）----
    def _tick(self):
        self._touch_lock()
        snap = None
        try:
            snap = parse_snapshot(_http_json(self.port, "/api/pet_state"))
            self.miss = 0
        except Exception:
            self.miss += 1
        if snap is not None:
            if not snap["settings"]["pet_enabled"]:
                return self._bye()
            self.snap = snap
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
            un = int(dg.get("unseen") or 0)
            if un > self.seen_digests:
                self.seen_digests = un
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
        st = self.state
        if st == "work":
            tx, ty = math.sin(t * 2.1) * 4.0, math.sin(t * 4.2) * 5.0
            self._flap()
        elif st == "cheer":
            tx, ty = math.cos(t * 5.0) * 12.0, math.sin(t * 5.0) * 9.0
            self._flap()
        elif st == "alert":
            tx = 3.0 if int(t * 5) % 2 else -3.0
            ty = 0.0
        elif st == "dead":
            tx, ty = 0.0, 0.0
        else:   # sleep：坐得低一点，呼吸式微沉浮
            tx, ty = 0.0, 5.0 + math.sin(t * 1.2) * 1.5
        self._move_bee(tx - self.ox, ty - self.oy)
        # 影子跟着高度呼吸：飞得越高影子越窄
        w = max(40.0, 72.0 - (5.0 - ty) * 2.0)
        self.cv.coords(self.shadow, 88 - w / 2, 126, 88 + w / 2, 137)
        if st == "sleep":
            phase = int(t * 1.6) % 4
            for i, z in enumerate(self.zzz):
                self.cv.itemconfigure(z, state=("normal" if i < min(phase, 3)
                                                else "hidden"))
            rise = (t * 8) % 12
            self.cv.coords(self.zzz[0], 124, 48 - rise * 0.4)
            self.cv.coords(self.zzz[1], 138, 36 - rise * 0.4)
            self.cv.coords(self.zzz[2], 151, 26 - rise * 0.4)
        if st == "cheer":
            on = int(t * 6) % 2
            for i, s in enumerate(self.spark):
                self.cv.itemconfigure(s, state=("normal" if (i % 2) == on
                                                else "hidden"))
        if st == "work":
            on = int(t * 8) % 2
            for i, s in enumerate(self.speed):
                self.cv.itemconfigure(s, state=("normal" if on
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
                      wx <= px < wx + WIN_W and wy <= py < wy + WIN_H)
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
            x = wx + WIN_W + 8
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

    def _show_bubble(self, text, secs=5.0):
        self._destroy_win("_bubble")
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
        x = self.root.winfo_x() + WIN_W - 40
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
        m.add_command(label="◎ " + self._L("lang"), state="disabled")
        m.add_radiobutton(label="    中文",
                          command=lambda: self._set_lang("zh"),
                          variable=self._lang_var, value="zh")
        m.add_radiobutton(label="    English",
                          command=lambda: self._set_lang("en"),
                          variable=self._lang_var, value="en")
        m.add_separator()
        m.add_command(label=self._L("close"), command=self._bye)
        return m

    def _set_mode(self, mode):
        try:
            _http_json(self.port, "/api/settings", body={"pet_mode": mode})
        except Exception:
            pass

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
        self._lang_var = self.tk.StringVar(value=self.lang)
        cv = self.cv
        self._press = None
        self._moved = False
        cv.bind("<ButtonPress-1>", self._on_press)
        cv.bind("<B1-Motion>", self._on_motion)
        cv.bind("<ButtonRelease-1>", self._on_release)
        cv.bind("<Button-3>", lambda e: self._menu().tk_popup(e.x_root,
                                                              e.y_root))
        self.root.protocol("WM_DELETE_WINDOW", self._bye)

    def _on_press(self, e):
        self._press = (e.x, e.y, self.root.winfo_x(), self.root.winfo_y())
        self._moved = False

    def _on_motion(self, e):
        if not self._press:
            return
        dx = e.x - self._press[0]
        dy = e.y - self._press[1]
        if abs(dx) + abs(dy) > 4:
            self._moved = True
            self.root.geometry("+%d+%d" % (self._press[2] + dx,
                                           self._press[3] + dy))

    def _on_release(self, e):
        moved, self._moved = self._moved, False
        self._press = None
        if moved:
            self._clamp_pos()
            self._save_cfg(x=self.root.winfo_x(), y=self.root.winfo_y())
        else:
            self._open_ui()

    def _bye(self):
        try:
            self._save_cfg(x=self.root.winfo_x(), y=self.root.winfo_y())
        except Exception:
            pass
        try:
            (self.data_dir / "pet.lock").unlink()
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
