# -*- coding: utf-8 -*-
"""settings_v2 落盘与 startup namespace 守护测试。

2026-10-20 实案：settings_schema.init() 全仓无人调用，_FILE 恒为 None，
_persist() 空转——设置页 schema 化调参保存后重启即丢（生产数据目录里根本
没有 settings_v2.json）。本测试钉住两件事：
  1. init() 接上落盘句柄后，mutate 的值跨「重启」（重新 init+register）仍在；
  2. startup.auto_open_browser 默认 True（保持现行为），可关、落盘可存活。
子进程隔离：settings_schema 是模块级单例，进程内换数据目录会互相污染。
"""
import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]

PROBE = r"""
import json, sys, tempfile
from pathlib import Path
from app.core import settings_schema as ss

tmp = tempfile.mkdtemp()
ss.init(tmp)
ss.register_default_namespaces()

assert "startup" in ss.names(), "startup namespace 未注册"
assert ss.get("startup", "auto_open_browser") is True, "auto_open_browser 默认应为 True"

# 落盘句柄接上后，注册即应产生 settings_v2.json
f = Path(tmp) / "settings_v2.json"
assert f.exists(), "register 后未落盘 settings_v2.json"

# 关掉自动开浏览器（CAS 正常路径）
rev = ss.mutate("startup", [{"op": "set", "path": "auto_open_browser",
                             "value": False}])
assert ss.get("startup", "auto_open_browser") is False

# 模拟重启：重新 init + register，值必须还在（此前 _persist 空转时这一步必丢）
ss.init(tmp)
ss.register_default_namespaces()
assert ss.get("startup", "auto_open_browser") is False, \
    "重启后 auto_open_browser 丢失——落盘链断了"

# 默认值不覆盖已落盘的显式值（False is not None 才不回种默认）
assert ss.get("orchestrator", "max_goal_rounds") == 5

print("@@OK@@" + json.dumps({"revision": rev}))
"""


class SettingsV2PersistenceTests(unittest.TestCase):
    def test_persist_across_restart_and_startup_namespace(self):
        env = dict(os.environ)
        env.pop("TUTTI_DATA", None)  # 探针自带临时目录，不继承外层数据目录
        proc = subprocess.run(
            [sys.executable, "-c", PROBE],
            cwd=str(REPO), env=env, timeout=120,
            capture_output=True, text=True)
        self.assertEqual(proc.returncode, 0,
                         "probe failed:\n%s\n%s" % (proc.stdout[-2000:],
                                                    proc.stderr[-2000:]))
        self.assertIn("@@OK@@", proc.stdout)


if __name__ == "__main__":
    unittest.main()
