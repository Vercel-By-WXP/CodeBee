# -*- coding: utf-8 -*-
"""成品扫描加速回归：剪枝遍历语义不变 + 短 TTL 缓存 + 在飞合并。

背景（2026-09-24 详情页卡顿案）：历史 rglob 全量展开 + 逐文件 stat 在几万文件
的工作目录上单次 1.6~4 秒，而详情/侧栏是轮询调用，UI 整体被拖卡。
"""
from __future__ import annotations

import os
import threading
import time
import unittest
from pathlib import Path
from unittest import mock

from base import BaseTest

from app.core import artifacts, store


def _make_tree(root):
    """搭一棵覆盖全部过滤语义的目录树。"""
    root = Path(root).resolve()

    def put(rel, content=b"x"):
        p = root / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_bytes(content)

    put("b.md")
    put("a.md")
    put("src/main/generated/Gen.java")   # 源码树内不误杀
    put("src/out.txt")                   # 文件名叫 out 不剔（BUILD 只看祖先）
    put("src/.hidden.md")                # 可见目录下的隐藏文件仍是候选
    put(".gitignore")                    # 根下隐藏文件是候选
    put("旧文件.md")                     # mtime 早于 t0 → 不算
    # 跳过/构建/隐藏目录（任意层级剪枝）
    put(".git/objects/aa")
    put("node_modules/pkg/index.js")
    put("__pycache__/m.pyc")
    put(".venv/lib.py")
    put(".zcode/cache.bin")
    put("target/surefire-reports/Foo.class")
    put("build/classes/Foo.class")
    put("dist/app.exe")
    put("docs/out/report.txt")           # out 是构建目录名 → 剔
    # CLI 过程产物
    put(".aider.chat.history.md")
    put("chapter_2_review.json")
    put("tutti_prompt_9.txt")
    old = root / "旧文件.md"
    os.utime(str(old), (1, 1))
    # 排序样本：b.md 比 a.md 新
    os.utime(str(root / "a.md"), (2000000000, 2000000000))
    os.utime(str(root / "b.md"), (2000000100, 2000000100))


_EXPECTED = {"b.md", "a.md", "src/main/generated/Gen.java", "src/out.txt",
             "src/.hidden.md", ".gitignore"}
_EXPECTED_GONE = {".git/objects/aa", "node_modules/pkg/index.js", "__pycache__/m.pyc",
                  ".venv/lib.py", ".zcode/cache.bin", "target/surefire-reports/Foo.class",
                  "build/classes/Foo.class", "dist/app.exe", "docs/out/report.txt",
                  "旧文件.md", ".aider.chat.history.md", "chapter_2_review.json",
                  "tutti_prompt_9.txt"}


class TestScanFilters(BaseTest):

    def test_pruned_walk_semantics(self):
        wd = Path(self.tmp) / "proj"
        wd.mkdir()
        _make_tree(wd)
        files = artifacts.scan(str(wd), 2)   # t0=2：1970 年的旧文件出局
        names = [f["name"] for f in files]
        for want in _EXPECTED:
            self.assertIn(want, names)
        for gone in _EXPECTED_GONE:
            self.assertNotIn(gone, names, gone)
        # 排序：mtime 新→旧
        mt = [f["mtime"] for f in files]
        self.assertEqual(mt, sorted(mt, reverse=True))
        # limit 截断取最新
        top = artifacts.scan(str(wd), 2, limit=1)
        self.assertEqual([f["name"] for f in top], ["b.md"])


class TestScanCache(BaseTest):

    def _tree(self):
        wd = Path(self.tmp) / "proj"
        wd.mkdir()
        (wd / "a.md").write_text("x", encoding="utf-8")
        return str(wd)

    def test_ttl_hit_and_bypass(self):
        wd = self._tree()
        calls = {"n": 0}
        real = artifacts._walk_files

        def counting(root, t0):
            calls["n"] += 1
            return real(root, t0)

        with mock.patch.object(artifacts, "_walk_files", counting):
            artifacts.scan(wd, 0, cache=True)
            artifacts.scan(wd, 0, cache=True)
            self.assertEqual(calls["n"], 1)      # TTL 内复用，不再扫盘
            artifacts.scan(wd, 0)                # cache=False 永远现扫
            self.assertEqual(calls["n"], 2)

    def test_ttl_expiry_and_running_window(self):
        wd = self._tree()
        clock = {"t": 1000.0}
        calls = {"n": 0}
        real = artifacts._walk_files

        def counting(root, t0):
            calls["n"] += 1
            return real(root, t0)

        with mock.patch.object(artifacts, "_walk_files", counting), \
                mock.patch.object(artifacts, "_now", lambda: clock["t"]):
            artifacts.scan(wd, 0, cache=True)
            clock["t"] += artifacts.CACHE_TTL_IDLE - 0.1
            artifacts.scan(wd, 0, cache=True)    # 空闲 TTL 内 → 缓存
            self.assertEqual(calls["n"], 1)
            clock["t"] += artifacts.CACHE_TTL_IDLE + 0.1
            artifacts.scan(wd, 0, cache=True)    # 过期 → 重扫
            self.assertEqual(calls["n"], 2)
            clock["t"] += artifacts.CACHE_TTL_RUNNING + 0.1
            artifacts.scan(wd, 0, cache=True, running=True)   # 运行中 TTL 更短
            self.assertEqual(calls["n"], 3)

    def test_key_covers_root_and_t0(self):
        wd = self._tree()
        artifacts.scan(wd, 0, cache=True)
        files = artifacts.scan(wd, 2000000000, cache=True)   # t0 不同 → 不同键
        self.assertEqual(files, [])              # 全部早于 t0 → 空结果，不是旧缓存

    def test_running_to_idle_refreshes_final_artifacts(self):
        wd = self._tree()
        clock = {"t": 1000.0}
        calls = {"n": 0}
        real = artifacts._walk_files

        def counting(root, t0):
            calls["n"] += 1
            return real(root, t0)

        with mock.patch.object(artifacts, "_walk_files", counting), \
                mock.patch.object(artifacts, "_now", lambda: clock["t"]):
            artifacts.scan(wd, 0, cache=True, running=True)
            (Path(wd) / "final.md").write_text("done", encoding="utf-8")
            # 终态的较长 TTL 不能让运行态缓存隐藏最后落盘的文件。
            files = artifacts.scan(wd, 0, cache=True, running=False)
            self.assertIn("final.md", [f["name"] for f in files])
            self.assertEqual(calls["n"], 2)


class TestInflightMerge(BaseTest):

    def test_concurrent_same_key_scans_once(self):
        wd = Path(self.tmp) / "proj"
        wd.mkdir()
        (wd / "a.md").write_text("x", encoding="utf-8")
        started = threading.Event()
        release = threading.Event()
        calls = {"n": 0}

        def slow_walk(root, t0):
            calls["n"] += 1
            started.set()
            release.wait(5)
            return [{"name": "a.md", "size": 1,
                     "mtime": int(time.time())}]

        out = {}

        def go():
            out.setdefault("r", artifacts.scan(str(wd), 0, cache=True))

        with mock.patch.object(artifacts, "_walk_files", slow_walk):
            t_a = threading.Thread(target=go)
            t_a.start()
            self.assertTrue(started.wait(5))
            t_b = threading.Thread(target=go)
            t_b.start()
            time.sleep(0.15)                  # B 应等在在飞锁上，而不是自己再扫
            self.assertEqual(calls["n"], 1)
            release.set()
            t_a.join(5)
            t_b.join(5)
            self.assertEqual(calls["n"], 1)   # 合并成功：同键只扫一次
            self.assertEqual(out["r"][0]["name"], "a.md")


class TestStoreWrapper(BaseTest):

    def test_default_fresh_and_cache_optin(self):
        wd = Path(self.tmp) / "w"
        wd.mkdir()
        task = store.create_task({"type": "doc", "goal": "缓存语义", "workdir": str(wd)})
        run = store.create_run("orchestration", "r1", task_id=task["id"])
        store.update_run(run["id"], status="running",
                         started_at=time.strftime("%Y-%m-%d %H:%M:%S"))
        (wd / "out.md").write_text("x", encoding="utf-8")

        wdir, files = store.run_artifacts(run["id"], cache=True)
        self.assertEqual(wdir, str(wd))
        self.assertIn("out.md", [f["name"] for f in files])
        # 运行中任务的缓存语义：TTL 内轮询不重扫（新落盘文件暂不可见）
        (wd / "late.md").write_text("y", encoding="utf-8")
        _, cached = store.run_artifacts(run["id"], cache=True)
        self.assertNotIn("late.md", [f["name"] for f in cached])
        # 默认（cache=False）永远现扫——发布/预览/知识库的口径
        _, fresh = store.run_artifacts(run["id"])
        self.assertIn("late.md", [f["name"] for f in fresh])


if __name__ == "__main__":
    unittest.main()
