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
                  "argv_template": [cli, "{prompt}"],
                  "env": {"TUTTI_TEST_SELFCONFIG": "1"}},  # 自带配置：过死链闸门
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

    def test_retry_rewrites_unqualified_chapters(self):
        """上一遍完整跑完但未达标 → 未过线章的成稿与分数不进继承，
        重试只重写重评这几章；达标收尾与全章未过线的行为各自锁定。"""
        from unittest import mock
        from app.core import store
        # run id 尾段是随机数，同秒创建的 run 字典序不定；继承取「最新一遍」，
        # 这里把 id 钉成递增序列，保证 prev_runs 的新旧序与创建序一致。
        seq = {"n": 0}

        def _seq_id(prefix):
            seq["n"] += 1
            return "%s-20260920-000000-%04d" % (prefix, seq["n"])

        with mock.patch.object(store, "_new_id", _seq_id):
            self._rewrite_unqualified_body(store)

    def _rewrite_unqualified_body(self, store):
        task = store.create_task({
            "type": "serial_novel", "title": "闸门-重写未达标章", "mode": "auto",
            "goal": "写一部短篇连载", "workdir": str(self.workdir),
            "serial": {"chapters": 3, "words_per_chapter": 800},
        })

        def _score(ch, mean, passed):
            return {"chapter": ch, "title": "第 %d 章" % ch,
                    "means": {"情节": mean}, "passed": passed,
                    "rounds": 1, "words": 800}

        def _seed_prev():
            r = store.create_run("orchestration", task["title"], task_id=task["id"])
            store.update_run(r["id"], outline={
                "book_title": "书", "source": "template",
                "chapters": [{"title": "第 %d 章" % i, "beats": "空", "hook": ""}
                             for i in (1, 2, 3)]})
            for i in (1, 2, 3):
                s, _ = store.add_step(r["id"], "draft-c%d" % i, "mock", "mock")
                store.finish_step(r["id"], s["n"], "done", summary="x", duration_s=0.1)
            return r

        # 上一遍：第 2 章未过线，整轮未达标收尾 → 第 2 章出继承，1/3 章保留
        r1 = _seed_prev()
        store.update_run(r1["id"], status="done", verdict={
            "serial": True, "publishable": False, "overall": 6.9,
            "chapter_scores": [_score(1, 7.5, True), _score(2, 6.0, False),
                               _score(3, 7.4, True)]},
            ended_at="2026-09-20 00:00:00")
        ok, err, r2 = store.retry_task(task["id"])
        self.assertTrue(ok, err)
        inh = r2.get("inherit") or {}
        self.assertEqual(inh.get("done_chapters"), [1, 3], "未过线的第 2 章不应进继承")
        self.assertEqual([c["chapter"] for c in inh.get("chapter_scores") or []],
                         [1, 3], "未过线章的分数也不应带进继承")
        self.assertTrue(inh.get("outline"))
        store.update_run(r2["id"], status="failed", ended_at="2026-09-20 00:00:01")

        # 对照：达标收尾 → 全部章照常继承（断点续跑行为不变）
        r3 = _seed_prev()
        store.update_run(r3["id"], status="done", verdict={
            "serial": True, "publishable": True, "overall": 7.8,
            "chapter_scores": [_score(1, 7.5, True), _score(2, 7.6, True),
                               _score(3, 7.4, True)]},
            ended_at="2026-09-20 00:00:02")
        ok, err, r4 = store.retry_task(task["id"])
        self.assertTrue(ok, err)
        inh4 = r4.get("inherit") or {}
        self.assertEqual(inh4.get("done_chapters"), [1, 2, 3])
        self.assertEqual(len(inh4.get("chapter_scores") or []), 3)
        store.update_run(r4["id"], status="failed", ended_at="2026-09-20 00:00:03")

        # 全章未过线 → done 清空但仍继承大纲：全书重写、结构不丢
        r5 = _seed_prev()
        store.update_run(r5["id"], status="done", verdict={
            "serial": True, "publishable": False, "overall": 5.0,
            "chapter_scores": [_score(1, 5.0, False), _score(2, 5.1, False),
                               _score(3, 4.9, False)]},
            ended_at="2026-09-20 00:00:04")
        ok, err, r6 = store.retry_task(task["id"])
        self.assertTrue(ok, err)
        inh6 = r6.get("inherit") or {}
        self.assertEqual(inh6.get("done_chapters"), [])
        self.assertEqual(inh6.get("chapter_scores"), [])
        self.assertTrue(inh6.get("outline"))


class TestQualityGateStandardIsExecutable(unittest.TestCase):
    """质量门判定口径必须写在标准里并被钉住，不能只活在代码注释里。"""

    SECTION = "## 内容质量判定门"

    def setUp(self):
        root = Path(__file__).resolve().parents[1]
        self.text = (root / "docs" / "execution-standard.md").read_text(
            encoding="utf-8")

    def test_section_and_impl_row_exist(self):
        self.assertIn(self.SECTION, self.text)
        self.assertIn("| 内容质量判定门 |", self.text)

    def test_decisions_are_pinned(self):
        section = self.text.split(self.SECTION, 1)[1]
        for marker in (
            "双闸相与",            # 验证 ∧ 评审，单闸绿灯不算过
            "不取平均",            # 逐维度全达标
            "一律视为不达标",      # 缺分/缺阈值不猜成通过
            "不落 0 分",           # 评审器失败不折算成质量 0 分
            "免检通行证",          # 反向：也不得借评审器失败放行
            "不带病产出",          # 输入降级即中止
            "不得是报告标题",      # 体裁错位判质量失败
            "只有评审维度集合",    # 裁定权归属
        ):
            with self.subTest(marker=marker):
                self.assertIn(marker, section)

    def test_task_row_still_points_at_the_gate(self):
        self.assertIn("验证命令通过且质量门通过", self.text)


if __name__ == "__main__":
    unittest.main()
