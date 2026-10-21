# -*- coding: utf-8 -*-
"""novel_quality 的共享术语与章节号归一化。

从 novel_quality 拆出，供 feedback/style 两个子模块与主模块共同引用，
避免互相导入形成环。
"""
from __future__ import annotations

import re
from typing import Any, Optional


SIGNING_RUBRIC = [
    "开篇吸引力",
    "情节推进",
    "文风统一",
    "情感细腻度",
    "节奏控制",
]

OPENING_DIM = "开篇吸引力"
_FEEDBACK_ID_INLINE_LIMIT = 200


def _positive_chapter_number(value: Any) -> Optional[int]:
    """Parse a chapter number without silently truncating floats or booleans."""
    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        return value if value > 0 else None
    if isinstance(value, str) and re.fullmatch(r"[0-9]+", value.strip()):
        try:
            number = int(value.strip())
        except (ValueError, OverflowError):
            return None
        return number if number > 0 else None
    return None
