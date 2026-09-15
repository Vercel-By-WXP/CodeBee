# -*- coding: utf-8 -*-
"""同章多稿赛马端到端（dev-3.0 借鉴项；不碰真实 CLI）。

两个假 CLI 智能体：badcli 起草必埋「注水情节」、goodcli 必埋「高光情节」；
评审夹具按稿件标记给 4.0 / 9.5 分。variants=2、badcli 任作者（变体 0），
锁定：
1. 两路变体并行起草（steps 含 draft-c1-v0 / draft-c1-v1）；
2. 择优是按评审均分而非默认序——胜者必须是 goodcli 那一路；
3. 胜稿转正为 chapter-01.md，败稿文件被删除；
4. run.variants 记录赛马结果；胜者评审分数直接作为第 1 轮章分（9.5 过门禁）。
"""
from __future__ import annotations

import json
import os
import sys

from base import BaseTest

RACE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "fixtures_race_cli.py")


class TestRaceVariants(BaseTest):

    def test_race_picks_best_variant(self):
        from app.core import catalog, manager, pipeline, registry, store

        paths_catalog = [
            {"id": "badcli", "name": "Bad CLI", "cli_group": "x",
             "detect": {"cli": sys.executable},
             "orch": {"kind": "generic", "command": sys.executable,
                      "argv_template": [RACE, "bad", "{prompt}"]},
             "default_enabled": True},
            {"id": "goodcli", "name": "Good CLI", "cli_group": "x",
             "detect": {"cli": sys.executable},
             "orch": {"kind": "generic", "command": sys.executable,
                      "argv_template": [RACE, "good", "{prompt}"]},
             "default_enabled": True},
        ]
        self.data_dir.mkdir(parents=True, exist_ok=True)
        catalog._CACHE["entries"] = None
        catalog._CACHE["ts"] = 0
        self._paths.CATALOG_FILE.write_text(json.dumps(paths_catalog, ensure_ascii=False),
                                            encoding="utf-8")
        self._paths.ENABLED_FILE.write_text("{}", encoding="utf-8")

        orig_detect = manager.detect_all
        orig_enabled = registry.effective_agents.__globals__["load_enabled"]
        try:
            manager.detect_all = lambda force=False: {
                "badcli": {"installed": True}, "goodcli": {"installed": True}}
            registry.effective_agents.__globals__["load_enabled"] = lambda: {}

            task = store.create_task({
                "type": "serial_novel", "title": "赛马书", "goal": "写一章",
                "workdir": str(self.workdir),
                "mode": "manual", "implementer": "badcli",
                "serial": {"chapters": 1, "words_per_chapter": 600, "variants": 2},
            })
            self.assertEqual(task["serial"]["variants"], 2)
            run = store.create_run("orchestration", task["title"], task_id=task["id"])
            store.update_task_status(task["id"], "queued")
            pipeline.execute_run(run["id"])
        finally:
            manager.detect_all = orig_detect
            registry.effective_agents.__globals__["load_enabled"] = orig_enabled

        r = store.get_run(run["id"])
        self.assertEqual(r["status"], "done", r.get("error"))
        roles = [s["role"] for s in (r.get("steps") or [])]
        self.assertIn("draft-c1-v0", roles)
        self.assertIn("draft-c1-v1", roles)

        # 赛马结果记录：v0（badcli/注水）败、v1（goodcli/高光）胜
        race = (r.get("variants") or {}).get("1") or []
        self.assertEqual(len(race), 2)
        chosen = [x for x in race if x.get("chosen")]
        self.assertEqual(len(chosen), 1)
        self.assertEqual(chosen[0]["agent"], "goodcli")
        self.assertEqual(chosen[0]["avg"], 9.5)

        # 文件收敛：胜稿转正、败稿删除
        win_text = (self.workdir / "chapter-01.md").read_text(encoding="utf-8")
        self.assertIn("高光情节", win_text)
        self.assertNotIn("注水情节", win_text)
        self.assertFalse((self.workdir / "chapter-01-v0.md").exists())
        self.assertFalse((self.workdir / "chapter-01-v1.md").exists())

        # 胜者评审直接作第 1 轮章分：9.5 过门禁，无修订轮
        cs = (r.get("chapter_scores") or [{}])[0]
        means = cs.get("means") or {}
        self.assertTrue(means)
        self.assertGreater(min(float(v) for v in means.values()), 9.0)
        self.assertEqual(cs.get("rounds"), 1)

    def test_variants_default_off_and_clamped(self):
        """variants 缺省/越界的钳制：>3 → 3；<1 或非法 → 不写字段（=1 关）。"""
        from app.core import store
        t1 = store.create_task({
            "type": "serial_novel", "title": "a", "goal": "g", "workdir": str(self.workdir),
            "serial": {"chapters": 2, "variants": 9}})
        self.assertEqual(t1["serial"]["variants"], 3)
        t2 = store.create_task({
            "type": "serial_novel", "title": "b", "goal": "g", "workdir": str(self.workdir),
            "serial": {"chapters": 2, "variants": 0}})
        self.assertNotIn("variants", t2["serial"])
        # 续写沿用赛马配置
        (self.workdir / "chapter-01.md").write_text("x", encoding="utf-8")
        ok, err, nxt = store.continue_task(t1["id"])
        self.assertTrue(ok, err)
        self.assertEqual(nxt["serial"]["variants"], 3)
