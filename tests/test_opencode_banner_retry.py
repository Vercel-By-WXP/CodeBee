# -*- coding: utf-8 -*-
"""opencode 横幅秒退原地重试 + CLI 配置原子写（2026-09-30 实案）：
并行评审爆发期 opencode 连续「退出码 1；stderr/stdout: > build · glm-5.3」，
输出清洗后只剩横幅、无任何错误文本，数分钟后同进程自愈——配置写读竞态类
瞬时抖动。修 = manager 所有 CLI 配置写盘走 temp+os.replace 原子替换 +
run_agent 对该签名原地快速重试一次（真实错误都有文本，不会误触发）。"""
from __future__ import annotations

import os
import tempfile
from pathlib import Path

from base import BaseTest
from app.core import manager, runner


def _res(**kw):
    base = {"ok": False, "exit_code": 1, "stdout": "", "stderr":
            "\x1b[0m\n> build · glm-5.3\n\x1b[0m\n", "duration": 0.8,
            "cancelled": False, "timed_out": False, "stalled": False,
            "deadline_exceeded": False, "abort_marker": None}
    base.update(kw)
    return base


class TestBannerDeathSignature(BaseTest):

    def test_banner_only_fast_death_matches(self):
        self.assertTrue(runner._opencode_fast_banner_death(_res()))

    def test_real_error_text_does_not_match(self):
        r = _res(stderr="\x1b[0m\n> build · glm-5.3\n\x1b[0m\n"
                        "Error: model not found")
        self.assertFalse(runner._opencode_fast_banner_death(r))

    def test_ok_slow_or_zero_exit_do_not_match(self):
        self.assertFalse(runner._opencode_fast_banner_death(_res(ok=True)))
        self.assertFalse(runner._opencode_fast_banner_death(_res(exit_code=None)))
        self.assertFalse(runner._opencode_fast_banner_death(_res(exit_code=0)))
        self.assertFalse(runner._opencode_fast_banner_death(_res(duration=60.0)))
        self.assertFalse(runner._opencode_fast_banner_death(_res(stderr="", stdout="")))

    def test_plain_noise_without_dot_separator_mismatch(self):
        # 无圆点分隔符的普通输出（如纯噪声）不算横幅，不触发重试
        r = _res(stderr="\x1b[0m\nloading assets\n\x1b[0m\n")
        self.assertFalse(runner._opencode_fast_banner_death(r))


class TestOpencodeRetry(BaseTest):

    def _agent(self):
        return {"id": "opencode", "kind": "opencode", "mode": "real",
                "command": "opencode", "model": "m1", "env": {}}

    def test_banner_death_retries_once_then_succeeds(self):
        calls = []
        seq = [_res(),
               {"ok": True, "exit_code": 0, "stdout": "评审 JSON 输出",
                "stderr": "", "duration": 30.0, "cancelled": False,
                "timed_out": False, "stalled": False,
                "deadline_exceeded": False, "abort_marker": None}]

        def fake_run_process(argv=None, **kw):
            calls.append(argv)
            return seq[min(len(calls) - 1, len(seq) - 1)]

        orig = runner.run_process
        runner.run_process = fake_run_process
        try:
            out = runner.run_agent(self._agent(), "评审一下",
                                   workdir=str(self.workdir))
        finally:
            runner.run_process = orig
        self.assertTrue(out["ok"])
        self.assertEqual(out["text"], "评审 JSON 输出")
        self.assertEqual(len(calls), 2)   # 原地重试恰好一次

    def test_real_failure_not_retried(self):
        calls = []

        def fake_run_process(argv=None, **kw):
            calls.append(argv)
            return _res(stderr="> build · glm-5.3\nError: model not found")

        orig = runner.run_process
        runner.run_process = fake_run_process
        try:
            out = runner.run_agent(self._agent(), "评审一下",
                                   workdir=str(self.workdir))
        finally:
            runner.run_process = orig
        self.assertFalse(out["ok"])
        self.assertEqual(len(calls), 1)   # 带错误文本的真实失败不重试


class TestAtomicConfigWrite(BaseTest):

    def test_write_model_replaces_atomically_without_debris(self):
        # 临时 HOME 下造一份 opencode.jsonc，走 write_model 后内容正确、
        # 目录里不留 .tmp 残片（os.replace 成功即删临时名）。
        # 路径一律 pathlib resolve+parents 构造与校验（Mimosa 口径）。
        home = Path(tempfile.mkdtemp(prefix="tutti_atomhome_")).resolve()
        cfg_path = (home / ".config" / "opencode" / "opencode.jsonc").resolve()
        self.assertIn(home, cfg_path.parents)
        cfg_path.parent.mkdir(parents=True, exist_ok=True)
        cfg_path.write_text('{\n  "model": "old-model"\n}\n', encoding="utf-8")
        entry = {"id": "opencode", "config": {"path": str(cfg_path),
                                              "format": "jsonc",
                                              "model_key": "model"}}
        saved = {k: os.environ.get(k) for k in ("HOME", "USERPROFILE")}
        os.environ["HOME"] = str(home)
        os.environ["USERPROFILE"] = str(home)
        try:
            w = manager.write_model(entry, "orch/glm-5.3")
        finally:
            for k, v in saved.items():
                if v is None:
                    os.environ.pop(k, None)
                else:
                    os.environ[k] = v
        self.assertTrue(w.get("ok"), w.get("error"))
        self.assertIn("orch/glm-5.3", cfg_path.read_text(encoding="utf-8"))
        leftovers = [p.name for p in cfg_path.parent.iterdir()
                     if p.suffix == ".tmp"]
        self.assertEqual(leftovers, [])
