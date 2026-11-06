# -*- coding: utf-8 -*-
"""救火遥测契约：四类人工接管里，哪几类「有埋点 / 不适用 / 仍不可观测」。

这是 ``journey`` 指标能不能上线的**唯一开关**，写侧（``rescue_telemetry``）
与读侧（``journey_sources``）都只认这里，不许各自维护一份。

2026-11-06 23:2x 只读取证后的现状
----------------------------------
============================  ==========================================
类别                          证据
============================  ==========================================
``force_publish``             ``publish-*.jsonl`` 的 ``quality_forced``
                              + ``quality_forced_source``（老数据缺 source）
``manual_retry``              ``rescue-*.jsonl``（``POST /api/tasks/<id>/retry``）
``manual_pause_resume``       ``rescue-*.jsonl``（``POST /api/runs/<id>/pause``
                              且 ``paused=false``）
``manual_revise``             **产品里没有独立写入口**：改稿单是
                              ``main.py`` 的只读现算，作者改稿后重提走的是
                              ``retry``，已被 ``manual_retry`` 覆盖。
============================  ==========================================

``manual_revise`` 归 ``NOT_APPLICABLE`` 不是「懒得做」，是「不可能发生」：
一个不存在的动作不该阻塞指标。但这条论证会随产品演进而失效——所以守测
用 AST 棘轮扫描 ``app/``：一旦有人真的加了 ``manual_revise`` 写入口，
守测立刻红，要求把它挪进 ``OBSERVED_RESCUE_KINDS`` 并补台账。

**时间边界（别拿历史书假绿）**：埋点只能记录启用之后的动作，历史 run 里
``retry_task`` 不写来源、解除暂停还把 ``pause_requested_at`` 清成 None，
只读回溯不出人工痕迹。所以只有 ``created_at >= telemetry_since`` 的书才
可能进分母；``telemetry_since=None``（未声明启用时刻）时**全部判不了**。
启用时刻由 11-09 重启埋点生效后显式传入，不在这里硬编码未来时间。
"""
from __future__ import annotations

import re

from . import journey

# 单一真源：口径扩大必须改 journey.RESCUE_KINDS + 这里 + 守测。
RESCUE_KINDS = frozenset(journey.RESCUE_KINDS)

OBSERVED_RESCUE_KINDS = frozenset((
    "force_publish", "manual_retry", "manual_pause_resume",
))
NOT_APPLICABLE_RESCUE_KINDS = frozenset(("manual_revise",))
UNOBSERVED_RESCUE_KINDS = (RESCUE_KINDS - OBSERVED_RESCUE_KINDS
                           - NOT_APPLICABLE_RESCUE_KINDS)

# 由 rescue 台账记录的类别（force_publish 走发布台账，不重复记）。
RESCUE_LOG_KINDS = OBSERVED_RESCUE_KINDS - frozenset(("force_publish",))
RESCUE_FILE_GLOB = "rescue-*.jsonl"

_TS_RE = re.compile(r"^\d{4}-\d{2}-\d{2} \d{2}:\d{2}(?::\d{2})?$")


def normalize_since(value):
    """把启用时刻规范成 ``YYYY-MM-DD HH:MM:SS``；坏输入 fail-closed 返回 None。"""
    text = str(value or "").strip()
    if not text or not _TS_RE.match(text):
        return None
    return text if len(text) == 19 else text + ":00"


def is_post_telemetry(created_at, since):
    """这本书是否建单于埋点启用之后。since 未声明 / 格式坏 → 一律 False。"""
    norm = normalize_since(since)
    created = str(created_at or "").strip()
    if not norm or not _TS_RE.match(created):
        return False
    if len(created) == 16:
        created += ":00"
    return created >= norm
