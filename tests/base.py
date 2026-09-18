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
        # 先重定向，再做任何 app 模块导入（见下方 _FILE 重绑注释）
        paths.DATA_DIR = self.data_dir
        paths.TASKS_DIR = self.data_dir / "tasks"
        paths.RUNS_DIR = self.data_dir / "runs"
        paths.USAGE_DIR = self.data_dir / "usage"
        paths.ERRORS_DIR = self.data_dir / "errors"
        paths.CATALOG_FILE = self.data_dir / "catalog.json"
        paths.ENABLED_FILE = self.data_dir / "orchestration.json"
        paths.ensure_dirs()
        # 多个模块在 import 期把 _FILE 绑到"当时的 paths.DATA_DIR"（首次导入若
        # 发生在任何 setUp 重定向前，就会绑到真实用户数据）。实测后果：测试
        # 进程读到真实编排者配置，发起真网 API 调用，产出真大纲绕过降级闸门。
        # 因此这里统一重绑到本用例的临时目录。
        import app.core.modelhub as _mh
        _mh._FILE = self.data_dir / "models.json"
        import app.core.settings as _st
        _st._FILE = self.data_dir / "settings.json"
        import app.core.flows as _fl
        _fl._FILE = self.data_dir / "flows.json"
        import app.core.skills as _sk
        _sk._FILE = self.data_dir / "skills.json"
        from app.core import skills as _sk2
        with _sk2._LOCK:
            _sk2._user_pack_cache.clear()
            _sk2._user_dir_mtime["ts"] = 0.0
            _sk2._user_dir_mtime["ids"] = None
        # 隔离：保存当前 pipeline._agents（可能被 monkey-patch），tearDown 恢复。
        # 注意 pipeline 必须在上面的重定向/重绑之后导入（其顶层 import 会拉起
        # modelhub/planner 等，晚导入才能拿到已重绑的 _FILE）。
        from app.core import pipeline
        self._pipeline_agents_backup = pipeline._agents
        # 清空模块级缓存
        from app.core import store
        store._TASKS.clear()
        store._RUNS.clear()

    def tearDown(self):
        # 恢复 monkey-patched 的 pipeline._agents（test_pipeline 等会改成 mock 列表）
        try:
            from app.core import pipeline
            pipeline._agents = self._pipeline_agents_backup
        except Exception:
            pass
        shutil.rmtree(self.tmp, ignore_errors=True)

    def mock_agents(self):
        from app.core import registry
        return registry.effective_agents([], {})
