# -*- coding: utf-8 -*-
"""质量闸门回归：评审失败不得记 0 分放行；降级大纲不得据以写全书。

背景（2026-09-13 真实七猫连载任务暴露）：
1. 两个评审模型全部失败 → 章节被记成 0.0 分继续往后写（质量闸门悄悄失效）；
2. 编排者不可用时大纲静默降级为「第 1 章/第 2 章」空模板，
   两万字按空模板写完等于废稿，且续跑还会继承这份废大纲。
本文件锁定修复后的行为：宁可中止等自动续跑，绝不带病产出。
"""
from __future__ import annotations

import contextlib
import json
import os
import sys
import unittest
from pathlib import Path

from base import BaseTest

ROLE_CLI = os.path.join(os.path.dirname(os.path.abspath(__file__)), "fixtures_role_cli.py")
BAD_CLI = os.path.join(os.path.dirname(os.path.abspath(__file__)), "fixtures_bad_cli.py")
CRASH_CLI = os.path.join(os.path.dirname(os.path.abspath(__file__)), "fixtures_crash_cli.py")


@contextlib.contextmanager
def _fake_env(cli, entry_id="fakecli"):
    """把指定假 CLI 注册为唯一已安装智能体（照搬 test_resume_e2e 的绕行手法）。"""
    from app.core import catalog, manager, registry
    from app.core import paths as paths_mod

    old_catalog = paths_mod.CATALOG_FILE.read_text(encoding="utf-8") \
        if paths_mod.CATALOG_FILE.exists() else None
    paths_mod.CATALOG_FILE.write_text(json.dumps([
        {"id": entry_id, "name": "Fake CLI", "cli_group": "installable",
         "detect": {"cli": sys.executable},
         "orch": {"kind": "generic", "command": sys.executable,
                  "argv_template": [cli, "{prompt}"]},
         "default_enabled": True},
    ], ensure_ascii=False), encoding="utf-8")
    catalog._CACHE["entries"] = None
    paths_mod.ENABLED_FILE.write_text("{}", encoding="utf-8")
    orig_load_enabled = registry.effective_agents.__globals__["load_enabled"]
    orig_detect = manager.detect_all
    try:
        registry.effective_agents.__globals__["load_enabled"] = lambda: {}
        manager.detect_all = lambda force=False: {entry_id: {"installed": True}}
        yield
    finally:
        registry.effective_agents.__globals__["load_enabled"] = orig_load_enabled
        manager.detect_all = orig_detect
        if old_catalog is not None:
            paths_mod.CATALOG_FILE.write_text(old_catalog, encoding="utf-8")
        catalog._CACHE["entries"] = None


class TestQualityGates(BaseTest):

    def test_degraded_outline_fails_real_run(self):
        """大纲模型全部失效 → 运行中止，绝不按空模板开写。"""
        from app.core import pipeline, store
        with _fake_env(cli=BAD_CLI):
            task = store.create_task({
                "type": "serial_novel", "title": "闸门-大纲降级", "mode": "manual",
                "implementer": "fakecli",
                "goal": "写一部短篇连载", "workdir": str(self.workdir),
                "serial": {"chapters": 2, "words_per_chapter": 800},
            })
            run = store.create_run("orchestration", task["title"], task_id=task["id"])
            pipeline.execute_run(run["id"])
        run = store.get_run(run["id"])
        self.assertEqual(run["status"], "failed")
        self.assertIn("大纲", run.get("error") or "")
        # 未写出任何章节文件（没有按空模板空转）
        self.assertFalse((self.workdir / "chapter-01.md").exists())

    def test_all_critics_failed_aborts_not_zero_scores(self):
        """评审全部失败 → 中止；不得把「评不上」当成 0 分写进台账。"""
        from app.core import pipeline, store
        with _fake_env(cli=ROLE_CLI):
            task = store.create_task({
                "type": "serial_novel", "title": "闸门-评审失效", "mode": "manual",
                "implementer": "fakecli", "critics": ["fakecli"],
                "goal": "写一部短篇连载", "workdir": str(self.workdir),
                "serial": {"chapters": 2, "words_per_chapter": 800},
            })
            run = store.create_run("orchestration", task["title"], task_id=task["id"])
            pipeline.execute_run(run["id"])
        run = store.get_run(run["id"])
        self.assertEqual(run["status"], "failed")
        self.assertIn("评审", run.get("error") or "")
        # 关键断言：任何章节都不得以全 0 分记入 chapter_scores
        for cs in (run.get("chapter_scores") or []):
            means = cs.get("means") or {}
            self.assertNotEqual(set(means.values()) or {0}, {0.0},
                                "评审失败被当成 0 分记录：%s" % cs)

    def test_draft_crash_with_written_file_recovers(self):
        """起草调用失败但章稿已完整落盘 → 送评审门，不整章作废。"""
        from app.core import pipeline, store
        with _fake_env(cli=CRASH_CLI):
            task = store.create_task({
                "type": "serial_novel", "title": "闸门-崩溃恢复", "mode": "manual",
                "implementer": "fakecli", "critics": ["mock-a"],
                "goal": "写一部短篇连载", "workdir": str(self.workdir),
                "serial": {"chapters": 2, "words_per_chapter": 800},
            })
            run = store.create_run("orchestration", task["title"], task_id=task["id"])
            pipeline.execute_run(run["id"])
        run = store.get_run(run["id"])
        self.assertEqual(run["status"], "done", run.get("error"))
        roles = {(s["role"], s["status"]): s for s in run["steps"]}
        d1 = roles[("draft-c1", "done")]
        self.assertIn("落盘", d1.get("summary") or "")
        for i in (1, 2):
            self.assertTrue((self.workdir / ("chapter-%02d.md" % i)).is_file())
        cs = run.get("chapter_scores") or []
        self.assertEqual([c["chapter"] for c in cs], [1, 2])
        v = run.get("verdict") or {}
        self.assertTrue(v.get("serial"))

    def test_retry_skips_degraded_outline_but_keeps_mock_template(self):
        """降级大纲不继承（强制重新生成）；mock 模板大纲正常继承。"""
        from app.core import store
        task = store.create_task({
            "type": "serial_novel", "title": "闸门-继承", "mode": "auto",
            "goal": "写一部短篇连载", "workdir": str(self.workdir),
            "serial": {"chapters": 2, "words_per_chapter": 800},
        })
        # 上一遍：降级大纲 + 已完成第 1 章
        r1 = store.create_run("orchestration", task["title"], task_id=task["id"])
        store.update_run(r1["id"], outline={
            "book_title": "", "source": "template", "degraded": True,
            "chapters": [{"title": "第 1 章", "beats": "空", "hook": ""}]})
        s, _ = store.add_step(r1["id"], "draft-c1", "mock", "mock")
        store.finish_step(r1["id"], s["n"], "done", summary="x", duration_s=0.1)
        store.update_run(r1["id"], status="failed", ended_at="2026-09-13 00:00:00")
        ok, err, r2 = store.retry_task(task["id"])
        self.assertTrue(ok, err)
        self.assertFalse(r2.get("inherit"), "降级大纲不应被继承")
        store.update_run(r2["id"], status="failed", ended_at="2026-09-13 00:00:01")
        # 对照：正常（mock）模板大纲 → 照常继承
        r3 = store.create_run("orchestration", task["title"], task_id=task["id"])
        store.update_run(r3["id"], outline={
            "book_title": "书", "source": "template",
            "chapters": [{"title": "第 1 章", "beats": "空", "hook": ""}]})
        s, _ = store.add_step(r3["id"], "draft-c1", "mock", "mock")
        store.finish_step(r3["id"], s["n"], "done", summary="x", duration_s=0.1)
        store.update_run(r3["id"], status="failed", ended_at="2026-09-13 00:00:00")
        ok, err, r4 = store.retry_task(task["id"])
        self.assertTrue(ok, err)
        self.assertTrue((r4.get("inherit") or {}).get("outline"), "正常大纲应被继承")


if __name__ == "__main__":
    unittest.main()
