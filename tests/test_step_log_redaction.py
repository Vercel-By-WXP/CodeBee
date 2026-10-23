# -*- coding: utf-8 -*-
"""步骤日志脱敏回归（2026-10-23 二轮审查 M1）。

不变量：同一条上游 CLI 输出，进模型前会被 redact.mask_secrets 遮罩
（builtin_agent_tools），进 UI/导出（read_step_log）也必须遮罩——脱敏策略
不能只保护一个消费方。raw=True 是显式出口（下载完整日志），默认不开。

隔离方式：临时目录 + mock patch paths.RUNS_DIR（store 的 _RUNS 是模块级
缓存，live 兜底用例用 mock.patch.dict 注入，退场即还原）。
"""
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from app.core import paths, store

RUN_ID = "r-secret"
REL = "steps/01-writer-claude.log"
# 两种凭据形态：Bearer 头（CLI 打 stderr 的常见形状）+ 环境赋值行
SECRET_LINES = (
    "POST /v1/messages\n"
    "Authorization: Bearer sk-supersecretvalue1234567890\n"
    "env API_KEY=abcdef1234567890abcdef\n"
    "normal output line\n"
)
SECRET_TOKEN = "sk-supersecretvalue1234567890"
API_SECRET = "abcdef1234567890abcdef"


class ReadStepLogRedactionTests(unittest.TestCase):
    def _seed_log(self, root, body=SECRET_LINES):
        """在 root/runs/<RUN_ID>/ 下种一份步骤日志，返回 (runs_dir, run_id)。"""
        runs = root / "runs"
        step_dir = runs / RUN_ID / "steps"
        step_dir.mkdir(parents=True)
        (step_dir / Path(REL).name).write_text(body, encoding="utf-8")
        return runs

    def test_credentials_masked_by_default(self):
        with tempfile.TemporaryDirectory() as tmp:
            runs = self._seed_log(Path(tmp))
            with mock.patch.object(paths, "RUNS_DIR", runs):
                text = store.read_step_log(RUN_ID, REL)
        self.assertNotIn(SECRET_TOKEN, text)
        self.assertNotIn(API_SECRET, text)
        self.assertIn("[key]", text)
        # 非凭据内容必须原样保留（排查上游报错还要靠它）
        self.assertIn("POST /v1/messages", text)
        self.assertIn("normal output line", text)

    def test_masking_covers_pretty_consumer(self):
        # board.py 大屏实时窗口走 pretty=True；遮罩在 pretty 之后统一生效
        with tempfile.TemporaryDirectory() as tmp:
            runs = self._seed_log(Path(tmp))
            with mock.patch.object(paths, "RUNS_DIR", runs):
                text = store.read_step_log(RUN_ID, REL, pretty=True)
        self.assertNotIn(SECRET_TOKEN, text)
        self.assertIn("[key]", text)

    def test_raw_returns_original(self):
        # raw=True 是显式出口：原文可查（调用方需已过访问令牌）
        with tempfile.TemporaryDirectory() as tmp:
            runs = self._seed_log(Path(tmp))
            with mock.patch.object(paths, "RUNS_DIR", runs):
                text = store.read_step_log(RUN_ID, REL, raw=True)
        self.assertIn(SECRET_TOKEN, text)
        self.assertIn(API_SECRET, text)

    def test_builtin_live_fallback_masked(self):
        # 日志文件缺席时兜底读 run.json 步骤记录（内置智能体实时流），
        # 同样不得把凭据原文给出 UI
        run = {"id": RUN_ID, "steps": [
            {"log": REL, "status": "running",
             "output": "调用上游\r\nAuthorization: Bearer " + SECRET_TOKEN + "\r\n"},
        ]}
        with tempfile.TemporaryDirectory() as tmp:
            runs = Path(tmp) / "runs"
            runs.mkdir()
            with mock.patch.object(paths, "RUNS_DIR", runs), \
                    mock.patch.dict(store._RUNS, {RUN_ID: run}):
                text = store.read_step_log(RUN_ID, REL)
        self.assertNotIn(SECRET_TOKEN, text)
        self.assertIn("[key]", text)
        self.assertIn("调用上游", text)

    def test_traversal_guard_unchanged(self):
        # 防目录穿越在遮罩之前短路：越界路径返回空，不因脱敏改动而绕过
        with tempfile.TemporaryDirectory() as tmp:
            runs = self._seed_log(Path(tmp))
            with mock.patch.object(paths, "RUNS_DIR", runs):
                text = store.read_step_log(RUN_ID, "../outside.log")
        self.assertEqual(text, "")


if __name__ == "__main__":
    unittest.main()
