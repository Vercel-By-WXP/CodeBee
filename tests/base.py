# -*- coding: utf-8 -*-
"""测试公共设施：把数据目录重定向到临时目录，保证测试不污染真实数据。"""
from __future__ import annotations

import shutil
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))


class BaseTest(unittest.TestCase):
    """每个测试用例独立临时 data 目录 + 独立工作目录。"""

    def setUp(self):
        from app.core import paths
        self._paths = paths
        self.tmp = Path(tempfile.mkdtemp(prefix="orch-test-"))
        self.data_dir = self.tmp / "data"
        self.workdir = self.tmp / "work"
        self.workdir.mkdir()
        paths.DATA_DIR = self.data_dir
        paths.TASKS_DIR = self.data_dir / "tasks"
        paths.RUNS_DIR = self.data_dir / "runs"
        paths.USAGE_DIR = self.data_dir / "usage"
        paths.CATALOG_FILE = self.data_dir / "catalog.json"
        paths.ENABLED_FILE = self.data_dir / "orchestration.json"
        paths.ensure_dirs()
        # 清空模块级缓存
        from app.core import store
        store._TASKS.clear()
        store._RUNS.clear()

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def mock_agents(self):
        from app.core import registry
        return registry.effective_agents([], {})
