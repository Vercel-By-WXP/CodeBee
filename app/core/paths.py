# -*- coding: utf-8 -*-
"""路径与目录约定。"""
from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]  # E:\GoOut\MultiAgentOrchestration
APP_DIR = ROOT / "app"
UI_DIR = APP_DIR / "ui"
DATA_DIR = ROOT / "data"
TASKS_DIR = DATA_DIR / "tasks"
RUNS_DIR = DATA_DIR / "runs"
CATALOG_FILE = DATA_DIR / "catalog.json"
ENABLED_FILE = DATA_DIR / "orchestration.json"

LOG_TAIL_CHARS = 4000  # API 返回日志时的截断长度


def ensure_dirs():
    for p in (DATA_DIR, TASKS_DIR, RUNS_DIR):
        p.mkdir(parents=True, exist_ok=True)
