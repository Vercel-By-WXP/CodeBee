"""CLI 能力探测：覆盖全集、并行、超时真的是墙钟上限、慢≠坏。

背景（2026-10-23 实测）：能力面板只能看到 5 个 CLI，因为默认探测集是手写的
5 条旧条目；而把默认集换成 catalog 全集又会拖到 39s+，根因不是「条目多」，是
``subprocess.run(timeout=)`` 在 Windows 上对 .CMD 包装器不构成墙钟上限 —— 只杀掉
cmd.exe 后，继承管道的 node.exe 仍握着句柄，communicate() 会一直等它退出
（实测 timeout=3 时 mimo-code 探了 22.8s）。
"""
import os
import sys
import tempfile
import time
import unittest
from unittest import mock

from app.core import catalog, cli_discovery

STUB = "import time\ntime.sleep(30)\nprint('late')\n"


class DefaultSpecsTest(unittest.TestCase):
    def test_default_covers_whole_catalog_with_catalog_ids(self):
        specs = cli_discovery.default_specs()
        ids = [entry.get("id") for entry in catalog.DEFAULT_CATALOG]
        self.assertEqual([s["name"] for s in specs], ids)
        self.assertTrue(all(s["executable"] for s in specs))

    def test_falls_back_to_legacy_set_when_catalog_unusable(self):
        """目录坏了不能让面板打不开：退回旧 5 条即可。"""
        broken = mock.Mock()
        broken.__iter__ = mock.Mock(side_effect=RuntimeError("catalog broken"))
        with mock.patch.object(catalog, "DEFAULT_CATALOG", broken):
            specs = cli_discovery.default_specs()
        self.assertEqual([s["name"] for s in specs],
                         [s["name"] for s in cli_discovery.DEFAULT_CLIS])


class DiscoverTest(unittest.TestCase):
    def test_order_is_preserved(self):
        """顺序必须稳定：界面列表与缓存都按它排，跳动会让人以为 CLI 变了。"""
        specs = [{"name": "c%d" % i, "executable": "x"} for i in range(8)]

        def fake(spec, timeout=3, environ=None):
            # 故意让后面的更快返回，乱序完成时才可能暴露顺序 bug
            time.sleep(0.02 * (8 - int(spec["name"][1:])))
            return {"name": spec["name"], "available": True, "version": "v", "error": ""}

        with mock.patch.object(cli_discovery, "probe", fake):
            rows = cli_discovery.discover(specs, timeout=1, workers=8)
        self.assertEqual([r["name"] for r in rows], [s["name"] for s in specs])

    def test_probes_run_concurrently(self):
        specs = [{"name": "c%d" % i, "executable": "x"} for i in range(8)]

        def slow(spec, timeout=3, environ=None):
            time.sleep(0.25)
            return {"name": spec["name"], "available": True, "version": "v", "error": ""}

        with mock.patch.object(cli_discovery, "probe", slow):
            t0 = time.time()
            cli_discovery.discover(specs, timeout=1, workers=4)
            elapsed = time.time() - t0
        # 串行需 2.0s；4 并发约 0.5s。给足余量，CI 慢机器也不误判。
        self.assertLess(elapsed, 1.5, "探测仍是串行：%.2fs" % elapsed)

    def test_empty_version_is_retried_once(self):
        """批量探测 .CMD 偶发拿不到输出，重试一次，别记成故障。"""
        calls = []

        def flaky(spec, timeout=3, environ=None):
            calls.append(spec["name"])
            if len(calls) == 1:
                return {"name": spec["name"], "available": True, "version": "", "error": ""}
            return {"name": spec["name"], "available": True, "version": "1.2.3", "error": ""}

        with mock.patch.object(cli_discovery, "probe", flaky):
            rows = cli_discovery.discover([{"name": "c", "executable": "x"}],
                                          timeout=1, workers=1)
        self.assertEqual(rows[0]["version"], "1.2.3")
        self.assertEqual(len(calls), 2, "空输出版本没有重试")

    def test_unavailable_cli_is_not_retried(self):
        """没装的不该重试：那是确定结论，重试只是在拖慢面板。"""
        calls = []

        def missing(spec, timeout=3, environ=None):
            calls.append(spec["name"])
            return {"name": spec["name"], "available": False, "version": "", "error": ""}

        with mock.patch.object(cli_discovery, "probe", missing):
            cli_discovery.discover([{"name": "c", "executable": "x"}], workers=1)
        self.assertEqual(len(calls), 1)


class WallClockTest(unittest.TestCase):
    def test_version_command_timeout_is_a_hard_bound(self):
        """`_run_capture` 必须在预算附近返回，不能被孙进程拖住。"""
        tmp = tempfile.mkdtemp(prefix="probe-timeout-")
        stub = os.path.join(tmp, "stub.py")
        with open(stub, "w", encoding="utf-8") as fh:
            fh.write(STUB)
        t0 = time.time()
        code, out, err, timed_out = cli_discovery._run_capture(sys.executable, stub, 1.5)
        elapsed = time.time() - t0
        self.assertTrue(timed_out, "超时未标记：code=%r out=%r" % (code, out))
        self.assertLess(elapsed, 15, "超时收口太慢：%.1fs" % elapsed)

    def test_slow_probe_is_marked_slow_not_broken(self):
        """慢 ≠ 坏：available 仍为真，只加 slow 标记，界面另用一套措辞。"""
        with mock.patch.object(cli_discovery.shutil, "which", return_value="C:/fake/cli.CMD"):
            with mock.patch.object(cli_discovery, "_run_capture",
                                   return_value=(None, "", "", True)):
                row = cli_discovery.probe({"name": "slow-cli", "executable": "cli"})
        self.assertTrue(row["available"], "装了就该是可用，只是没取到版本")
        self.assertTrue(row.get("slow"))
        self.assertIn("无输出", row["error"])


if __name__ == "__main__":
    unittest.main()
