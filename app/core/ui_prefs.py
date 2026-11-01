# -*- coding: utf-8 -*-
"""界面偏好落盘（2026-10-29 记忆中心开关）：前端 localStorage 只管本浏览器，
而记忆注入/沉淀发生在服务端 pipeline——开关必须服务端也有一份，否则换浏览器
或远端调用时门控失效。单文件带锁读写，损坏即弃（同 prefs.py 的姿势）。

键位（白名单，缺省全开）：
- memory_conversation  对话记忆（项目记忆）：False 时 pipeline 不注入
  project_memory.active_text，run 结束也不再 capture_run。
- memory_local_skills  本地记忆与技能：False 时 capability_runtime 不注入
  经验库/知识库块，run 结束不做 skills/knowledge 的 learn_async 沉淀。
插件/外部技能/工程地图是「安装的本事」，不归这两个开关管。
"""
from __future__ import annotations

import json
import threading

from . import atomic, paths

_LOCK = threading.Lock()

DEFAULTS = {
    "memory_conversation": True,
    "memory_local_skills": True,
}


def _file():
    # 动态取路径：DATA_DIR 可被测试重定向，模块级缓存会绑死首次导入的目录
    return paths.DATA_DIR / "ui-prefs.json"


def load():
    """读全部开关；文件缺失/损坏返回出厂默认（全开）。"""
    out = dict(DEFAULTS)
    try:
        data = json.loads(_file().read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return out
    if not isinstance(data, dict):
        return out
    for key in DEFAULTS:
        if key in data:
            out[key] = bool(data[key])
    return out


def get(key):
    """读单个开关；未知键返回 None。"""
    if key not in DEFAULTS:
        return None
    return load().get(key)


def save(patch):
    """合并保存白名单内的键（bool 化），返回保存后的完整视图。"""
    view = load()
    if not isinstance(patch, dict):
        return view
    with _LOCK:
        for key in DEFAULTS:
            if key in patch:
                view[key] = bool(patch[key])
        try:
            atomic.write_json(_file(), view, mode=0o600, indent=2)
        except OSError:
            pass
        return view
