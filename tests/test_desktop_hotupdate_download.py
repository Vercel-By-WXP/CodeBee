"""引擎热更新下载契约（desktop/lib/backend.cjs downloadTo）。

热更新曾因 downloadTo 只挂 node:http 而对 https://registry.npmjs.org 一律抛
`Protocol "https:" not supported. Expected "http:"`（桌面端「热更新没有完成」
弹窗真凶）。本测试用 node 子进程跑协议/白名单/重定向契约，防止回归。
"""
import os
import shutil
import subprocess
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


class DesktopHotUpdateDownloadTest(unittest.TestCase):
    def test_download_to_https_contract(self):
        node = shutil.which('node')
        if not node:
            self.skipTest('node 不在 PATH，跳过桌面热更新下载契约测试')
        harness = ROOT / 'tests' / 'desktop_hotupdate_harness.cjs'
        backend = ROOT / 'desktop' / 'lib' / 'backend.cjs'
        fixtures = ROOT / 'tests' / 'fixtures'
        # 仅子进程内放宽 TLS 校验，本地自签证书夹具才能完成握手。
        env = dict(os.environ, NODE_TLS_REJECT_UNAUTHORIZED='0')
        proc = subprocess.run(
            [node, str(harness), str(backend), str(fixtures)],
            capture_output=True, text=True, env=env, timeout=120, cwd=str(ROOT))
        self.assertEqual(
            proc.returncode, 0,
            f'harness failed:\nstdout={proc.stdout}\nstderr={proc.stderr}')
        self.assertIn('OK desktop_hotupdate_harness', proc.stdout)


if __name__ == '__main__':
    unittest.main()
