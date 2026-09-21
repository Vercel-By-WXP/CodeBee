# -*- coding: utf-8 -*-
"""偏好记忆（借鉴 chinese-novelist-skill 的偏好记忆，2026-09-21）：记住用户
上次创建任务时选择的类型与参数，新建任务时预填「最近一次的选择」而不是
写死的出厂默认——高频用户不用每次重调轮数/阈值/章节。

只记参数不记内容（目标/标题绝不落库）；单文件带锁读写，损坏即弃。
"""
from __future__ import annotations

import json
import threading

from . import paths

_LOCK = threading.Lock()

SAVE_KEYS = ("type", "rounds", "threshold", "best_of", "chapters",
             "words_per_chapter", "variants", "mode", "thinking")
_LIMITS = {"rounds": (1, 5), "threshold": (1.0, 10.0), "best_of": (1, 3),
           "chapters": (1, 2000), "words_per_chapter": (200, 20000),
           "variants": (1, 3)}
_CHOICES = {
    "mode": ("auto", "fast", "expert", "manual"),
    "thinking": ("auto", "low", "standard", "high"),
}


def _file():
    # 动态取路径：DATA_DIR 可被测试重定向，模块级缓存会绑死首次导入的目录
    return paths.DATA_DIR / "last-prefs.json"


def _clamp(key, value):
    """数值钳制：历史文件被手改出天际值时按参数合法域兜底。"""
    lo, hi = _LIMITS.get(key), (0, 10 ** 9)
    lo, hi = lo if lo else hi
    try:
        return max(lo, min(hi, type(lo)(value) if isinstance(lo, int)
                           else float(value)))
    except (TypeError, ValueError):
        return None


def record(payload):
    """从创建任务的 payload 提取可复用参数（存在才覆盖，单键独立保留）。"""
    if not isinstance(payload, dict):
        return
    with _LOCK:
        cur = load()
        for key in SAVE_KEYS:
            v = payload.get(key)
            if v is None:
                continue
            if key == "type":
                v = str(v)[:32]
                if v:
                    cur["type"] = v
                continue
            if key in _CHOICES:
                v = str(v).strip().lower()
                if v in _CHOICES[key]:
                    cur[key] = v
                continue
            c = _clamp(key, v)
            if c is not None:
                cur[key] = c
        tmp = _file().with_suffix(".tmp")
        try:
            paths.DATA_DIR.mkdir(parents=True, exist_ok=True)
            tmp.write_text(json.dumps(cur, ensure_ascii=False, indent=2),
                           encoding="utf-8")
            tmp.replace(_file())
        except OSError:
            pass


def load():
    """读最近偏好；文件缺失/损坏返回 {}。"""
    try:
        data = json.loads(_file().read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}
    except (OSError, ValueError):
        return {}
