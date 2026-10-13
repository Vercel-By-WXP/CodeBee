# -*- coding: utf-8 -*-
"""版本探测杀树 + 常驻孤儿清扫回归（2026-10-13 实案）：

管理页版本探测走 npm cmd 垫片时，裸 subprocess.run(timeout) 只杀垫片 cmd，
CLI 孙进程变孤儿存活——opencode 是客户端-服务端架构，僵实例还会劫持后续
会话的项目根；一个上午积了 18 个 `opencode --version` 僵尸，连载任务被
堵成假 running。修后：

① 探测改走 runner.run_process：超时杀整棵进程树（垫片 + CLI 孙进程）。
② version_of 每条目在飞锁：并发请求（轮询/多标签）串行到一次探测。
③ _orphan_signature 纳入 `opencode --version` 探测形态，清扫网兜得住。
④ 启动清扫升级常驻循环（run_orphan_sweep_forever，幂等单线程）。
"""
from __future__ import annotations

import os
import subprocess
import threading
import time
import unittest
import uuid

from base import BaseTest


def _probe_sample():
    sep = chr(92)
    return ('"d:%s%s%s%sopencode.exe"    --version'
            % (sep, "nvm4w", sep + "node_modules" + sep
               + "opencode-ai" + sep + "bin", ""))


class TestOrphanSignature(BaseTest):

    def test_version_probe_signature_matched(self):
        """`opencode --version` 探测残留纳入孤儿签名（本 1013 实案形态）。"""
        from app.core import manager
        self.assertTrue(manager._orphan_signature(_probe_sample()))

    def test_legacy_signatures_still_matched(self):
        """既有形态不回归：run 形态 --model / codex 专属 flag / kimi mjs。"""
        from app.core import manager
        self.assertTrue(manager._orphan_signature(
            'opencode run --model glm-4.7'))
        self.assertTrue(manager._orphan_signature(
            'codex exec --skip-git-repo-check -p x'))
        self.assertTrue(manager._orphan_signature(
            'node kimi-code/dist/main.mjs -p x'))

    def test_plain_cli_name_not_matched(self):
        """用户手动裸跑 CLI（无 Tutti 专属 flag）不进签名，不误杀。"""
        from app.core import manager
        self.assertFalse(manager._orphan_signature('opencode'))
        self.assertFalse(manager._orphan_signature('codex.exe'))


class TestSweepLoop(BaseTest):

    def test_start_is_idempotent_single_thread(self):
        """重复调用只起一条常驻清扫线程（daemon）。"""
        from app.core import manager
        manager.run_orphan_sweep_forever()
        manager.run_orphan_sweep_forever()
        time.sleep(0.2)
        named = [t for t in threading.enumerate()
                 if t.name == "orphan-sweep-loop"]
        self.assertEqual(len(named), 1)
        self.assertTrue(named[0].daemon)


class TestVersionProbe(BaseTest):

    def test_version_of_serializes_concurrent_probes(self):
        """并发 version_of 只探一次：其余吃缓存，不再成排堆探测进程。"""
        from app.core import manager
        entry = {"id": "probe-dedup-x", "detect": {"cli": "fake-cli"}}

        calls = []

        def _slow_probe(cli, timeout=20):
            calls.append(cli)
            time.sleep(0.4)
            return "1.2.3"

        orig_probe = manager._cli_version_output
        orig_detect = manager.detect_all
        manager._cli_version_output = _slow_probe
        manager.detect_all = lambda force=False: {
            "probe-dedup-x": {"installed": True, "detail": "x"}}
        try:
            results = [None] * 3
            barrier = threading.Barrier(3, timeout=10)

            def _call(i):
                barrier.wait()
                results[i] = manager.version_of(entry)

            ts = [threading.Thread(target=_call, args=(i,)) for i in range(3)]
            for t in ts:
                t.start()
            for t in ts:
                t.join(15)
            self.assertEqual(results, ["1.2.3"] * 3)
            self.assertEqual(len(calls), 1)
        finally:
            manager._cli_version_output = orig_probe
            manager.detect_all = orig_detect
            with manager._LOCK:
                manager._STATE["versions"].pop("probe-dedup-x", None)
                manager._VER_PROBE_LOCKS.pop("probe-dedup-x", None)


@unittest.skipUnless(os.name == "nt", "npm cmd 垫片孤儿是 Windows 形态")
class TestProbeKillsTree(BaseTest):

    def test_timeout_kills_child_under_cmd(self):
        """cmd /c 直启的子进程在探测超时后同死。

        对照实案形态：cmd /c 是直接子进程，真身 CLI 是它的孩子——裸
        subprocess.run 超时只杀 cmd，真身永生；run_process 必须连孩子
        一起带走。这里用 cmd → python 拓扑复刻，子进程 cmdline 带唯一
        标记，超时后按标记验尸（无文件、无路径）。"""
        from app.core import runner
        marker = "probe-tree-" + uuid.uuid4().hex[:12]
        # 睡 600s：窗口内自然退出不可能充当「被杀死」，验尸只认真死
        child_py = "import time;time.sleep(600)"

        t0 = time.time()
        out = runner.run_process(
            argv=["cmd", "/c", "python", "-c", child_py, marker],
            timeout=3)
        dur = time.time() - t0
        self.assertTrue(out["timed_out"], out)
        self.assertFalse(out["ok"])
        # 杀树含 drain/join 预算（实测最坏 ~13s）；远小于孩子 60s 的睡眠即算通过
        self.assertLess(dur, 30)

        # 验尸口径是「真死」：taskkill 超 1.2s 预算会转后台自行结束（设计内，
        # quiescence_unknown 只是诊断不改变终态），给足宽限期轮询到孩子消失。
        # 排除 $PID：PowerShell 自身 cmdline 含标记串，会自匹配成假活。
        ps_exe = os.path.join(os.environ.get("SystemRoot", "C:\Windows"),
                              "System32", "WindowsPowerShell", "v1.0",
                              "powershell.exe")
        ps = ("Get-CimInstance Win32_Process | Where-Object { "
              "$_.ProcessId -ne $PID -and $_.CommandLine -match '%s' } | "
              "Select-Object -ExpandProperty ProcessId" % marker)
        deadline = time.time() + 20
        alive = ["?"]
        while time.time() < deadline:
            r = subprocess.run([ps_exe, "-NoProfile", "-Command", ps],
                               capture_output=True, text=True, timeout=30)
            alive = [ln.strip() for ln in (r.stdout or "").splitlines()
                     if ln.strip().isdigit()]
            if not alive:
                break
            time.sleep(1.5)
        self.assertEqual(alive, [],
                         "cmd 的孩子进程在超时杀树后仍然存活（孤儿复现）")


if __name__ == "__main__":
    unittest.main()
