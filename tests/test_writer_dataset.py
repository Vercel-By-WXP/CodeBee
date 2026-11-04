# -*- coding: utf-8 -*-
"""训练对归集器（writer dataset）守测。

守六件事：
1. 归集认得到「连载任务 + 有章分的 run + 工作目录章文件」这三者齐备的训练对，
   逐章对上章纲（锁定章纲优先）、前文结尾、评审意见、均分。
2. 默认只收过线章；include_rejected 连未过线章一起收且带 major 意见。
3. 同（工作目录，章号）去重：多 run 只留均分最高的一份。
4. extra_dirs：跑分体系外的归档书目录收成 scored=False 纯文本对。
5. export_sft：ChatML 三消息、system 来自写手槽位版本、min_mean 过滤生效。
6. 坏 run / 无章分 run / 无工作目录任务：跳过并计数，绝不中断。
"""
from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from core import writer_dataset
from core import paths as core_paths


def _body(ch):
    return ("# 第%d章 试炼\n\n%s" % (ch, "他握紧柴刀沿界碑往北走。雪没到膝盖，"
            "风从垭口灌下来，他数着自己的脚印，第%d次回头看那片林子。" % ch) * 40)


class WriterDatasetTest(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        base = Path(self._tmp.name)
        self.tasks = base / "tasks"
        self.runs = base / "runs"
        self.out = base / "writer_dataset"
        self.wd = base / "bookA"
        for d in (self.tasks, self.runs, self.wd, self.wd / "章纲"):
            d.mkdir(parents=True)
        (self.tasks / "T-1.json").write_text(json.dumps({
            "id": "T-1", "type": "serial_novel", "title": "界碑以北",
            "workdir": str(self.wd)}), encoding="utf-8")
        (self.wd / "chapter-01.md").write_text(_body(1), encoding="utf-8")
        (self.wd / "chapter-02.md").write_text(_body(2), encoding="utf-8")
        (self.wd / "章纲" / "ch-1.md").write_text(
            "本章纲：主角进山遇迹，立下规矩。", encoding="utf-8")
        self.run_scores = [
            {"chapter": 1, "title": "进山", "passed": True,
             "means": {"节奏": 8.5, "文笔": 8.0}, "words": 2400, "rounds": 1},
            {"chapter": 2, "title": "规矩", "passed": False,
             "means": {"节奏": 7.0, "文笔": 7.0}, "words": 2100, "rounds": 2},
        ]
        self._mk_run("r-1", self.run_scores)
        self._patches = [
            mock.patch.object(core_paths, "TASKS_DIR", self.tasks),
            mock.patch.object(core_paths, "RUNS_DIR", self.runs),
        ]
        for p in self._patches:
            p.start()
            self.addCleanup(p.stop)
        self.addCleanup(self._tmp.cleanup)
        writer_dataset._DISCOVER_CACHE.clear()
        self.addCleanup(writer_dataset._DISCOVER_CACHE.clear)

    def _mk_run(self, rid, scores, outline=None):
        (self.runs / rid).mkdir(parents=True, exist_ok=True)
        (self.runs / rid / "run.json").write_text(json.dumps({
            "id": rid, "task_id": "T-1", "kind": "run",
            "writer_version": "v1",
            "outline": outline if outline is not None else {
                "style_anchor": "冷硬克制，短句",
                "chapters": [{"chapter": 1, "title": "进山",
                              "beats": "进山遇迹", "hook": "雪地里第二行脚印"},
                             {"chapter": 2, "title": "规矩",
                              "beats": "立规矩", "hook": "规矩破了"}]},
            "chapter_scores": scores,
            "verdict": {"major_issues": [
                {"chapter": 2, "severity": "major", "dim": "节奏",
                 "note": "第2章全程无获得"}]},
        }, ensure_ascii=False), encoding="utf-8")

    # 1. 基本归集
    def test_collect_basic(self):
        res = writer_dataset.collect(out_dir=self.out)
        self.assertEqual(res["pairs"], 1)          # 只收过线章
        self.assertEqual(res["tasks"], 1)
        self.assertEqual(res["runs"], 1)
        pairs = [json.loads(x) for x in
                 (self.out / res["pairs_file"]).read_text(encoding="utf-8").splitlines()]
        p = pairs[0]
        self.assertEqual(p["meta"]["chapter"], 1)
        self.assertEqual(p["meta"]["book_title"], "界碑以北")
        self.assertEqual(p["meta"]["mean"], 8.25)
        self.assertTrue(p["meta"]["passed"])
        self.assertEqual(p["meta"]["version"], "v1")
        self.assertEqual(p["outline_locked"], "本章纲：主角进山遇迹，立下规矩。")
        self.assertEqual(p["outline_run"]["hook"], "雪地里第二行脚印")
        self.assertEqual(p["style_anchor"], "冷硬克制，短句")
        self.assertEqual(p["prev_tail"], "")       # 第 1 章无前文
        self.assertIn("握紧柴刀", p["text"])
        # latest 指针
        latest = json.loads((self.out / "latest.json").read_text(encoding="utf-8"))
        self.assertEqual(latest["pairs_file"], res["pairs_file"])

    # 2. 未过线章
    def test_collect_include_rejected(self):
        res = writer_dataset.collect(include_rejected=True, out_dir=self.out)
        self.assertEqual(res["pairs"], 2)
        pairs = [json.loads(x) for x in
                 (self.out / res["pairs_file"]).read_text(encoding="utf-8").splitlines()]
        ch2 = [p for p in pairs if p["meta"]["chapter"] == 2][0]
        self.assertFalse(ch2["meta"]["passed"])
        self.assertIn("全程无获得", ch2["meta"]["issues"])

    # 3. 去重留最好稿
    def test_dedup_keeps_best(self):
        self._mk_run("r-2", [
            {"chapter": 1, "title": "进山", "passed": True,
             "means": {"节奏": 9.2, "文笔": 9.0}, "words": 2500, "rounds": 1},
            {"chapter": 2, "title": "规矩", "passed": True,
             "means": {"节奏": 8.8, "文笔": 8.8}, "words": 2400, "rounds": 1},
        ])
        res = writer_dataset.collect(out_dir=self.out)
        self.assertEqual(res["runs"], 2)
        self.assertEqual(res["dedup_dropped"], 1)  # r-1 的第 1 章被更好的替掉
        pairs = [json.loads(x) for x in
                 (self.out / res["pairs_file"]).read_text(encoding="utf-8").splitlines()]
        self.assertEqual({p["meta"]["run_id"] for p in pairs}, {"r-2"})

    # 4. 跑分体系外的书
    def test_extra_dirs(self):
        wd2 = self._tmp.name + "/bookB"
        Path(wd2, "章纲").mkdir(parents=True)
        Path(wd2, "chapter-1.md").write_text(_body(1), encoding="utf-8")
        res = writer_dataset.collect(extra_dirs=[wd2], out_dir=self.out)
        self.assertEqual(res["pairs"], 2)
        pairs = [json.loads(x) for x in
                 (self.out / res["pairs_file"]).read_text(encoding="utf-8").splitlines()]
        extra = [p for p in pairs if not p["meta"]["scored"]][0]
        self.assertEqual(extra["meta"]["workdir"], wd2)
        self.assertIsNone(extra["meta"]["mean"])

    # 5. SFT 导出
    def test_export_sft(self):
        writer_dataset.collect(out_dir=self.out)
        res = writer_dataset.export_sft(out_dir=self.out)
        self.assertTrue(res["ok"])
        self.assertEqual(res["exported"], 1)
        rows = [json.loads(x) for x in
                (self.out / res["sft_file"]).read_text(encoding="utf-8").splitlines()]
        msgs = rows[0]["messages"]
        self.assertEqual([m["role"] for m in msgs], ["system", "user", "assistant"])
        self.assertIn("写手", msgs[0]["content"])   # system 来自写手槽位（种子版）
        self.assertIn("章纲", msgs[1]["content"])
        self.assertTrue(msgs[2]["content"].startswith("# 第1章"))
        # min_mean 过滤 + include_rejected 数据源下的双保险
        writer_dataset.collect(include_rejected=True, out_dir=self.out)
        res2 = writer_dataset.export_sft(min_mean=8.0, out_dir=self.out)
        self.assertEqual(res2["exported"], 1)      # 7.0 的章被拦在门外
        # 无指针时明确报错
        empty = self.out.parent / "empty-out"
        res3 = writer_dataset.export_sft(out_dir=empty)
        self.assertFalse(res3["ok"])

    # 6. 坏数据跳过不中断
    def test_bad_inputs_skipped(self):
        (self.tasks / "T-2.json").write_text("{坏json", encoding="utf-8")
        (self.tasks / "T-3.json").write_text(json.dumps({
            "id": "T-3", "type": "serial_novel", "workdir": ""}), encoding="utf-8")
        (self.tasks / "T-4.json").write_text(json.dumps({
            "id": "T-4", "type": "code", "workdir": str(self.wd)}), encoding="utf-8")
        (self.runs / "r-bad").mkdir()
        (self.runs / "r-bad" / "run.json").write_text("]bad", encoding="utf-8")
        self._mk_run("r-noscore", [])
        res = writer_dataset.collect(out_dir=self.out)
        self.assertEqual(res["pairs"], 1)
        self.assertEqual(res["runs_no_scores"], 1)

    # 清单
    def test_manifests(self):
        writer_dataset.collect(out_dir=self.out)
        items = writer_dataset.manifests(out_dir=self.out)
        self.assertEqual(len(items), 1)
        self.assertEqual(items[0]["pairs"], 1)


if __name__ == "__main__":
    unittest.main()
