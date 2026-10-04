# -*- coding: utf-8 -*-
"""赛马复赛分层降级（端到端，不碰真实 CLI）。

背景（2026-10-04 巡检实锤）：连载起草重试的分层降级已随 3d0e013 落地
（_serial_shrunk_block 按身份分块），但同章赛马路径的复赛（race_round≥1）
仍照发同一份全量提示词——容量受限通道上首轮 N 路全挂后，复赛只会把
N×2400s 再烧一遍。修复后复赛与非赛马重试同口径：超 12000 触发按身份收缩
（经验库→4K、圣经二级标题边界），大纲/前情/知识库块永不动。

夹具：前两次调用（首轮两路变体）必败，其后写合格稿；每次调用记录
（提示词体量、是否含降级标记）到独立状态文件。锁定：
1. 复赛确实发生（共 4 次起草调用）；
2. 复赛两路提示词带降级标记且体量严格小于首轮；
3. 赛马仍正常收卷（run done、胜稿转正）。
"""
from __future__ import annotations

import json
import os
import sys

from base import BaseTest

SHRINK_CLI = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                          "fixtures_race_shrink_cli.py")


class TestRaceCtxShrink(BaseTest):

    def test_race_round2_shrinks_oversized_prompt(self):
        from app.core import catalog, manager, pipeline, registry, store

        state_dir = self.workdir / "shrink-state"
        state_dir.mkdir(parents=True, exist_ok=True)
        paths_catalog = [
            {"id": "shrinkcli", "name": "Shrink CLI", "cli_group": "x",
             "detect": {"cli": sys.executable},
             "orch": {"kind": "generic", "command": sys.executable,
                      "argv_template": [SHRINK_CLI, "{prompt}"],
                      "env": {"TUTTI_TEST_SELFCONFIG": "1",   # 过死链闸门
                              "RACE_SHRINK_STATE_DIR": str(state_dir)}},
             "default_enabled": True},
        ]
        self.data_dir.mkdir(parents=True, exist_ok=True)
        catalog._CACHE["entries"] = None
        catalog._CACHE["ts"] = 0
        self._paths.CATALOG_FILE.write_text(json.dumps(paths_catalog, ensure_ascii=False),
                                            encoding="utf-8")
        self._paths.ENABLED_FILE.write_text("{}", encoding="utf-8")
        # 超预算圣经（含多个二级标题边界）：把首轮提示词顶过 12000 触发线
        sections = "\n\n".join("## 设定第 %d 节\n\n%s" % (n, "世界观细节文字，用于撑起体量。" * 30)
                               for n in range(12))
        (self.workdir / "story-bible.md").write_text(
            "## 故事圣经\n\n主角：林晚。\n\n" + sections, encoding="utf-8")

        orig_detect = manager.detect_all
        orig_enabled = registry.effective_agents.__globals__["load_enabled"]
        try:
            manager.detect_all = lambda force=False: {"shrinkcli": {"installed": True}}
            registry.effective_agents.__globals__["load_enabled"] = lambda: {}

            task = store.create_task({
                "type": "serial_novel", "title": "降级赛马书", "goal": "写一章",
                "workdir": str(self.workdir),
                "mode": "manual", "implementer": "shrinkcli",
                "serial": {"chapters": 1, "words_per_chapter": 600, "variants": 2},
            })
            run = store.create_run("orchestration", task["title"], task_id=task["id"])
            store.update_task_status(task["id"], "queued")
            pipeline.execute_run(run["id"])
        finally:
            manager.detect_all = orig_detect
            registry.effective_agents.__globals__["load_enabled"] = orig_enabled

        r = store.get_run(run["id"])
        self.assertEqual(r["status"], "done", r.get("error"))

        calls = [json.loads(p.read_text(encoding="utf-8"))
                 for p in state_dir.glob("call-*.json")]
        self.assertEqual(len(calls), 4, "首轮 2 路失败 + 复赛 2 路 = 4 次起草调用")
        full = [c for c in calls if not c["shrunk"]]
        shrunk = [c for c in calls if c["shrunk"]]
        self.assertEqual(len(full), 2)     # 首轮全量照发（先给全量一次机会）
        self.assertEqual(len(shrunk), 2)   # 复赛两路都降级
        self.assertLess(max(c["len"] for c in shrunk),
                        min(c["len"] for c in full),
                        "复赛提示词体量必须严格小于首轮（降级真的变小了）")

        # 赛马收卷不受降级影响：胜稿转正为 chapter-01.md
        win_text = (self.workdir / "chapter-01.md").read_text(encoding="utf-8")
        self.assertIn("高光情节", win_text)


if __name__ == "__main__":
    import unittest
    unittest.main()
