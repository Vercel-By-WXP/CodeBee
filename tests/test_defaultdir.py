# -*- coding: utf-8 -*-
"""默认保存路径：settings 持久化、任务缺省落盘、迁移、run 成品文件列表。"""
from __future__ import annotations

import json
import os
import time
import unittest
from pathlib import Path

from base import BaseTest

from app.core import paths, settings, store


class TestDefaultWorkdir(BaseTest):

    def setUp(self):
        super().setUp()
        # 每个用例都从「未自定义」开始
        settings.save({"default_workdir": ""})

    def test_effective_falls_back_to_builtin(self):
        view = settings.load()
        self.assertEqual(view["default_workdir"], "")
        self.assertTrue(view["default_workdir_effective"].endswith("workspace"))
        self.assertEqual(settings.default_workdir(), view["default_workdir_effective"])

    def test_save_and_normalize(self):
        wd = os.path.join(self.tmp, "我的作品")  # noqa: PTH118 —— 不存在也可以保存（使用时惰性创建）
        view, err = settings.save({"default_workdir": wd})
        self.assertIsNone(err)
        self.assertEqual(view["default_workdir"], wd)
        # 相对路径拒绝
        view, err = settings.save({"default_workdir": "relative/path"})
        self.assertIsNotNone(err)
        # ~ 展开
        view, err = settings.save({"default_workdir": "~" + os.sep + "tutti-home-test"})
        self.assertIsNone(err)
        self.assertFalse(view["default_workdir"].startswith("~"))
        settings.save({"default_workdir": ""})

    def test_create_task_uses_default_workdir(self):
        wd = Path(self.tmp) / "workspace默认"
        settings.save({"default_workdir": str(wd)})
        task = store.create_task({"type": "doc", "goal": "缺省目录任务"})
        self.assertEqual(task["workdir"], str(wd))
        self.assertTrue(wd.is_dir())  # 默认路径自动创建

    def test_migrate_moves_only_old_default_children(self):
        old_root = Path(self.tmp) / "old默认"
        sub = old_root / "小说甲"
        sub.mkdir(parents=True)
        (sub / "稿.md").write_text("内容", encoding="utf-8")
        settings.save({"default_workdir": str(old_root)})
        t1 = store.create_task({"type": "doc", "goal": "旧默认下的任务", "workdir": str(sub)})
        # 独立目录（用户显式指定）不应被迁移
        custom = Path(self.tmp) / "custom-proj"
        custom.mkdir()
        t2 = store.create_task({"type": "doc", "goal": "自定义目录任务", "workdir": str(custom)})

        new_root = Path(self.tmp) / "new默认"
        new_root.mkdir()
        moved, skipped = store.migrate_task_workdirs(str(old_root), str(new_root))
        self.assertEqual((moved, skipped), (1, 0))
        self.assertFalse(sub.exists())                       # 旧位置已搬走
        self.assertTrue((new_root / "小说甲" / "稿.md").is_file())
        self.assertEqual(store.get_task(t1["id"])["workdir"], str(new_root / "小说甲"))
        self.assertEqual(store.get_task(t2["id"])["workdir"], str(custom))  # 自定义目录不动

    def test_migrate_skips_running(self):
        old_root = Path(self.tmp) / "old2"
        d = old_root / "进行中"
        d.mkdir(parents=True)
        settings.save({"default_workdir": str(old_root)})
        t = store.create_task({"type": "doc", "goal": "运行中任务", "workdir": str(d)})
        store.update_task_status(t["id"], "running")
        new_root = Path(self.tmp) / "new2"
        moved, skipped = store.migrate_task_workdirs(str(old_root), str(new_root))
        self.assertEqual((moved, skipped), (0, 1))
        self.assertTrue(d.exists())                          # 运行中不动


class TestRunArtifacts(BaseTest):

    def test_artifacts_lists_new_files_only(self):
        wd = Path(self.tmp) / "art-wd"
        wd.mkdir()
        old = wd / "旧文件.md"
        old.write_text("历史遗留", encoding="utf-8")
        os.utime(old, (time.time() - 86400, time.time() - 86400))  # 一天前
        task = store.create_task({"type": "doc", "goal": "成品文件任务", "workdir": str(wd)})
        run = store.create_run("orchestration", "成品任务", task_id=task["id"])
        store.update_run(run["id"], status="running", started_at=time.strftime("%Y-%m-%d %H:%M:%S"))
        new = wd / "script.md"
        new.write_text("成品内容", encoding="utf-8")
        # 噪音目录不进列表
        noise = wd / ".git" / "x"
        noise.parent.mkdir(exist_ok=True)
        noise.write_text("noise", encoding="utf-8")
        # agent CLI 的隐藏过程目录（.mimocode/.zcode…）同样不是成品
        for d in (".mimocode", ".zcode"):
            f = wd / d / "cron-lock"
            f.parent.mkdir(exist_ok=True)
            f.write_text("proc", encoding="utf-8")
        # 根目录里的 CLI 历史、评审 JSON 和临时长提示词也不是交付成果
        for name in (".aider.chat.history.md", ".aider.input.history",
                     "chapter_2_review.json", "draft.review.json",
                     "tutti_prompt_123.txt"):
            (wd / name).write_text("process", encoding="utf-8")
        # 构建产物目录（target/build/dist…）里的文件是工具链再生成的，不是成品
        for d in ("target/surefire-reports", "build/classes", "dist", "src/main/generated"):
            f = wd / d / "Foo.class" if d != "src/main/generated" else wd / d / "Gen.java"
            f.parent.mkdir(parents=True, exist_ok=True)
            f.write_text("compiled", encoding="utf-8")

        wdir, files = store.run_artifacts(run["id"])
        self.assertEqual(wdir, str(wd))
        names = [f["name"] for f in files]
        self.assertIn("script.md", names)
        self.assertIn("src/main/generated/Gen.java", names)   # 源码树内不误杀
        self.assertNotIn("旧文件.md", names)
        self.assertNotIn(".git/x", names)
        self.assertNotIn(".mimocode/cron-lock", names)
        self.assertNotIn(".zcode/cron-lock", names)
        self.assertNotIn(".aider.chat.history.md", names)
        self.assertNotIn(".aider.input.history", names)
        self.assertNotIn("chapter_2_review.json", names)
        self.assertNotIn("draft.review.json", names)
        self.assertNotIn("tutti_prompt_123.txt", names)
        self.assertTrue(not any(n.startswith(("target/", "build/", "dist/")) for n in names),
                        "构建产物泄漏: %s" % [n for n in names if n.startswith(("target/", "build/", "dist/"))])

    def test_artifacts_accumulate_across_runs(self):
        # 断点续跑场景：第 1 跑写 chapter-01，第 2 跑写 chapter-02；
        # 打开第 2 跑的详情也要能看到 chapter-01（以任务首跑为起点）
        wd = Path(self.tmp) / "serial-wd"
        wd.mkdir()
        task = store.create_task({"type": "doc", "goal": "连载任务", "workdir": str(wd)})
        run1 = store.create_run("orchestration", "连载1", task_id=task["id"])
        store.update_run(run1["id"], status="running", started_at=time.strftime("%Y-%m-%d %H:%M:%S"))
        c1 = wd / "chapter-01.md"
        c1.write_text("第一章", encoding="utf-8")
        store.update_run(run1["id"], status="done", ended_at=time.strftime("%Y-%m-%d %H:%M:%S"))
        run2 = store.create_run("orchestration", "连载2", task_id=task["id"])
        store.update_run(run2["id"], status="running", started_at=time.strftime("%Y-%m-%d %H:%M:%S"))
        time.sleep(0.02)
        c2 = wd / "chapter-02.md"
        c2.write_text("第二章", encoding="utf-8")

        _, files = store.run_artifacts(run2["id"])
        names = [f["name"] for f in files]
        self.assertIn("chapter-01.md", names)   # 第 1 跑的成果不丢
        self.assertIn("chapter-02.md", names)

    def test_read_run_file_blocks_traversal(self):
        wd = Path(self.tmp) / "trav-wd"
        wd.mkdir()
        (wd / "ok.md").write_text("ok", encoding="utf-8")
        task = store.create_task({"type": "doc", "goal": "穿越防护", "workdir": str(wd)})
        run = store.create_run("orchestration", "穿越任务", task_id=task["id"])
        data, err = store.read_run_file(run["id"], "ok.md")
        self.assertIsNone(err)
        self.assertEqual(data, b"ok")
        data, err = store.read_run_file(run["id"], "../outside.md")
        self.assertIsNotNone(err)
        data, err = store.read_run_file(run["id"], "")
        self.assertIsNotNone(err)
