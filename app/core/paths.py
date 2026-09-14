# -*- coding: utf-8 -*-
"""路径与目录约定。

数据目录选择顺序：
1. TUTTI_DATA 环境变量（测试/多实例时整体指到别处，必须在任何模块使用前设置）；
2. 仓库内 data/ 且已有内容——开发仓库与老安装平滑沿用，升级不动数据；
3. 用户目录：Windows %APPDATA%\\Tutti，macOS/Linux ~/.tutti。
   npm/pip 全局安装的包目录会在 `npm update -g` 时被整体替换，
   数据绝不能落在包内，故全新安装一律走用户目录。
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]  # 仓库根 / npm 包根
APP_DIR = ROOT / "app"
UI_DIR = APP_DIR / "ui"

# 仓库 data/ 出现其中任意一项即视为「在用」；ensure_dirs 建出的空目录不算
_DATA_SENTINELS = ("catalog.json", "models.json", "orchestration.json", "tasks", "runs")


def _user_data_dir(platform=None, environ=None, home=None) -> Path:
    """全局安装场景的用户数据目录（不创建，仅计算）。home 供测试注入。"""
    platform = platform or sys.platform
    env = os.environ if environ is None else environ
    home = Path(home) if home else Path.home()
    if platform == "win32":
        appdata = env.get("APPDATA")
        if appdata:
            return Path(appdata) / "Tutti"
        return home / "AppData" / "Roaming" / "Tutti"
    return home / ".tutti"


def default_data_dir(repo=None, environ=None) -> Path:
    env = (os.environ if environ is None else environ).get("TUTTI_DATA")
    if env:
        return Path(env)
    repo_data = Path(repo or ROOT) / "data"
    if any((repo_data / n).exists() for n in _DATA_SENTINELS):
        return repo_data
    return _user_data_dir(environ=environ)


DATA_DIR = default_data_dir()
TASKS_DIR = DATA_DIR / "tasks"
RUNS_DIR = DATA_DIR / "runs"
USAGE_DIR = DATA_DIR / "usage"
CATALOG_FILE = DATA_DIR / "catalog.json"
ENABLED_FILE = DATA_DIR / "orchestration.json"

LOG_TAIL_CHARS = 4000  # API 返回日志时的截断长度


def ensure_dirs():
    for p in (DATA_DIR, TASKS_DIR, RUNS_DIR):
        p.mkdir(parents=True, exist_ok=True)
