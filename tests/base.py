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
        # 测试实例防毒闸的配套：TUTTI_DATA 指向本用例临时目录，让
        # manager.tmp_data_no_home_write() 生效——进程内单测若触发
        # launch/自愈/autobind 同步，也会被拦下不写真实 ~/.claude 等
        # CLI 配置（2026-09-22 a.test 毒入真实 settings.json 实案）。
        self._old_tutti_data = __import__("os").environ.get("TUTTI_DATA")
        __import__("os").environ["TUTTI_DATA"] = str(self.data_dir)
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
        import app.core.knowledge as _kb
        _kb._FILE = self.data_dir / "knowledge.json"
        # 健康状态使用 init() 注入存储路径；在用例入口直接绑定临时文件并清空
        # 内存状态，避免未显式 init 的调用写入上一用例已删除的目录。
        from app.core import health as _health
        self._health = _health
        with _health._LOCK:
            _health._FILE = self.data_dir / "provider_health.json"
            _health._PROVIDERS.clear()
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
        # 清状态并解除临时路径绑定。若探针或测试线程仍在使用健康模块，锁会让
        # 解绑排在当前持久化操作之后；解绑后任何延迟上报都不会碰已删除目录。
        health = getattr(self, "_health", None)
        if health is not None:
            with health._LOCK:
                health._PROVIDERS.clear()
                health._FILE = None
        os_env = __import__("os").environ
        if self._old_tutti_data is None:
            os_env.pop("TUTTI_DATA", None)
        else:
            os_env["TUTTI_DATA"] = self._old_tutti_data
        shutil.rmtree(self.tmp, ignore_errors=True)

    def mock_agents(self):
        from app.core import registry
        return registry.effective_agents([], {})
