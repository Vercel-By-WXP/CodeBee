# -*- coding: utf-8 -*-
"""写手槽位（writer slot）守测。

守七件事：
1. 种子默认存在但**未启用**——active_version 为空时 block_for 必须返回空串
   （生产链行为不变，启用是显式动作）。
2. 注入门禁：只对 novel/serial_novel 注入；其他任务类型一律空串。
3. 版本管理：add_version 递增 vN；set_active 校验存在性；历史版本不删。
4. 台账：record_run 记一行、版本归因认 run.writer_version、上限 200 条滚动。
5. 影子配置：sample_rate 钳位 [0,1]；未启用/零采样 maybe_shadow 必须是 False。
6. 影子工作线程：generate_once 打桩——成功产出记字数/落文件；失败记 error；
   全程不抛错、不碰生产。
7. 对账报告：裁判分 vs 生产章分 → wins/losses/ties；读不到生产分标 unresolved。
"""
from __future__ import annotations

import json
import tempfile
import time
import unittest
from pathlib import Path
from unittest import mock

from core import writer_slot, modelhub


class WriterSlotTest(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        base = Path(self._tmp.name)
        self._patches = [
            mock.patch.object(writer_slot, "_FILE", base / "writer_slot.json"),
            mock.patch.object(writer_slot, "_SHADOW_JSONL",
                              base / "writer_shadow.jsonl"),
            mock.patch.object(writer_slot, "_SHADOW_DIR", base / "writer_shadow"),
        ]
        for p in self._patches:
            p.start()
            self.addCleanup(p.stop)
        self.addCleanup(self._tmp.cleanup)

    # 1. 种子默认存在但未启用
    def test_seed_exists_but_dormant(self):
        st = writer_slot.state()
        self.assertIn("v1", st["versions"])
        self.assertTrue(st["versions"]["v1"]["prompt"].strip())
        self.assertEqual(st["active_version"], "")
        self.assertEqual(writer_slot.block_for({"type": "serial_novel"}), "")

    # 2. 注入门禁
    def test_block_gating(self):
        ok, _ = writer_slot.add_version("纪律甲：不写模板腔。", "测试版")
        self.assertTrue(ok)
        ok, _ = writer_slot.set_active("v2")
        self.assertTrue(ok)
        block = writer_slot.block_for({"type": "serial_novel"})
        self.assertIn("写手纪律 v2", block)
        self.assertIn("纪律甲", block)
        self.assertEqual(writer_slot.block_for({"type": "code"}), "")
        self.assertEqual(writer_slot.block_for({}), "")
        # 关回空 = 全链不注入
        writer_slot.set_active("")
        self.assertEqual(writer_slot.block_for({"type": "serial_novel"}), "")

    # 3. 版本管理
    def test_version_management(self):
        ok, msg = writer_slot.add_version("", "空 prompt 必须拒绝")
        self.assertFalse(ok)
        ok, v2 = writer_slot.add_version("纪律乙。")
        self.assertEqual(v2, "v2")
        ok, _ = writer_slot.add_version("纪律丙。")
        self.assertEqual(writer_slot.state()["versions"]["v3"]["prompt"], "纪律丙。")
        ok, msg = writer_slot.set_active("v99")
        self.assertFalse(ok)
        self.assertIn("不存在", msg)
        # 历史版本永不删
        self.assertIn("v1", writer_slot.state()["versions"])

    # 4. 迭代台账
    def test_ledger_records_and_cap(self):
        verdict = {"chapter_scores": [{"passed": True}, {"passed": False}],
                   "overall": 8.3, "publishable": True,
                   "quality_status": "publish_ready", "total_words": 5000}
        task = {"id": "t-1", "title": "书甲"}
        writer_slot.record_run({"id": "r-1", "writer_version": "v2"}, task, verdict)
        led = writer_slot.state()["ledger"]
        self.assertEqual(len(led), 1)
        self.assertEqual(led[0]["version"], "v2")     # run 钉的版本优先
        self.assertEqual(led[0]["chapters"], 2)
        self.assertEqual(led[0]["passed_chapters"], 1)
        self.assertEqual(led[0]["overall"], 8.3)
        # 无 writer_version → 记 active（未启用记空串）
        writer_slot.record_run({"id": "r-2"}, task, verdict)
        self.assertEqual(writer_slot.state()["ledger"][0]["version"], "")
        # 上限滚动：新的在前
        with mock.patch.object(writer_slot, "_LEDGER_CAP", 10):
            for i in range(15):
                writer_slot.record_run({"id": "r-%d" % i}, task, verdict)
        led = writer_slot.state()["ledger"]
        self.assertEqual(len(led), 10)
        self.assertEqual(led[0]["run_id"], "r-14")

    # 5. 影子配置
    def test_shadow_config_clamps(self):
        sh = writer_slot.set_shadow(enabled=True, sample_rate=7, provider_id="p1",
                                    model="m1")
        self.assertEqual(sh["sample_rate"], 1.0)
        self.assertTrue(sh["enabled"])
        sh = writer_slot.set_shadow(sample_rate=-2)
        self.assertEqual(sh["sample_rate"], 0.0)
        sh = writer_slot.set_shadow(enabled="yes")
        self.assertTrue(sh["enabled"])
        writer_slot.set_shadow(enabled=False, sample_rate=0, provider_id="", model="")
        self.assertFalse(writer_slot.maybe_shadow("r", {"id": "t"}, 1, "p", "w"))
        writer_slot.set_shadow(enabled=True, sample_rate=0, provider_id="p", model="m")
        self.assertFalse(writer_slot.maybe_shadow("r", {"id": "t"}, 1, "p", "w"))

    # 6. 影子工作线程（generate_once 打桩，零网络）
    def test_shadow_worker_success_and_failure(self):
        writer_slot.set_shadow(enabled=True, provider_id="p", model="m")
        gen = mock.Mock(return_value={"ok": True, "text": "第一章 进山。" + "雪" * 50,
                                      "latency_ms": 12})
        with mock.patch.object(modelhub, "generate_once", gen):
            writer_slot._shadow_worker("r-1", "t-1", 3, "提示词", "/tmp/wd", "冠军稿", 2400)
        rows = writer_slot.read_shadow()
        self.assertEqual(len(rows), 1)
        row = rows[0]
        self.assertTrue(row["ok"])
        self.assertEqual(row["chapter"], 3)
        self.assertEqual(row["version"], "")
        self.assertGreater(row["words"], 0)
        self.assertIn("file", row)
        fpath = writer_slot._SHADOW_DIR / row["file"]
        self.assertTrue(fpath.exists())
        self.assertIn("进山", fpath.read_text(encoding="utf-8"))
        # 失败路径：error 进台账，不抛错
        gen2 = mock.Mock(return_value={"ok": False, "error": "boom",
                                       "latency_ms": 5})
        with mock.patch.object(modelhub, "generate_once", gen2):
            writer_slot._shadow_worker("r-1", "t-1", 4, "p", "w", "c", 2400)
        row = writer_slot.read_shadow()[-1]
        self.assertFalse(row["ok"])
        self.assertEqual(row["error"], "boom")
        # 缺配置直接短路
        writer_slot.set_shadow(provider_id="", model="")
        writer_slot._shadow_worker("r-1", "t-1", 5, "p", "w", "c", 2400)
        row = writer_slot.read_shadow()[-1]
        self.assertFalse(row["ok"])
        self.assertIn("未配置", row["error"])

    def test_maybe_shadow_dispatch(self):
        writer_slot.set_shadow(enabled=True, sample_rate=1.0,
                               provider_id="p", model="m")
        gen = mock.Mock(return_value={"ok": False, "error": "x"})
        with mock.patch.object(modelhub, "generate_once", gen):
            self.assertTrue(writer_slot.maybe_shadow(
                "r-1", {"id": "t-1", "serial": {"words_per_chapter": 2000}},
                1, "提示词", "wd", "冠军"))
            deadline = time.time() + 5
            while time.time() < deadline and not writer_slot.read_shadow():
                time.sleep(0.05)
        self.assertEqual(len(writer_slot.read_shadow()), 1)

    # 7. 对账报告
    def test_shadow_report(self):
        base = time.strftime("%Y-%m-%dT%H:%M:%S")
        rows = [
            {"ts": base, "run_id": "r-1", "chapter": 1, "ok": True,
             "judge_score": 8.8, "dup": False},
            {"ts": base, "run_id": "r-1", "chapter": 2, "ok": True,
             "judge_score": 7.0, "dup": True},
            {"ts": base, "run_id": "r-1", "chapter": 3, "ok": True,
             "judge_score": 8.1, "dup": False},
            {"ts": base, "run_id": "r-2", "chapter": 1, "ok": False,
             "error": "boom"},
        ]
        with writer_slot._SHADOW_JSONL.open("w", encoding="utf-8") as f:
            for r in rows:
                f.write(json.dumps(r, ensure_ascii=False) + "\n")
        champ = {"r-1": {1: 8.0, 2: 8.0, 3: 8.0}, "r-2": {1: 8.0}}
        with mock.patch.object(writer_slot, "_champion_mean",
                               side_effect=lambda rid, ch:
                                   champ.get(rid, {}).get(ch)):
            rep = writer_slot.shadow_report(days=30)
        self.assertEqual(rep["total"], 4)
        self.assertEqual(rep["ok"], 3)
        self.assertEqual(rep["failed"], 1)
        self.assertEqual(rep["judged"], 3)
        self.assertEqual(rep["wins"], 1)      # 8.8 > 8.2
        self.assertEqual(rep["losses"], 1)    # 7.0 < 7.8
        self.assertEqual(rep["ties"], 1)      # 8.1 ∈ [7.8, 8.2]
        self.assertEqual(rep["dup_count"], 1)
        self.assertEqual(rep["unresolved"], 1)  # 失败条目无生产分
        self.assertAlmostEqual(rep["mean_judge"], 7.97, delta=0.01)

    # 裁判 JSON 抠取
    def test_judge_parse(self):
        score, note = writer_slot._judge_parse(
            '前置噪声 {"score": 8.2, "note": "节奏好"} 尾部')
        self.assertEqual(score, 8.2)
        self.assertEqual(note, "节奏好")
        self.assertEqual(writer_slot._judge_parse("没有 JSON"), (None, ""))

    # apply_binding：有 agent_id 才动 modelhub
    def test_apply_binding(self):
        res = writer_slot.apply_binding("v1")
        self.assertFalse(res["ok"])
        writer_slot.set_binding_hint("v1", {"agent_id": "claude-code",
                                            "provider_id": "prov-x",
                                            "model": "m-1"})
        with mock.patch.object(modelhub, "set_binding",
                               return_value={"provider_id": "prov-x",
                                             "model": "m-1", "chain": []}) as sb:
            res = writer_slot.apply_binding("v1")
        self.assertTrue(res["ok"])
        sb.assert_called_once_with("claude-code", provider_id="prov-x", model="m-1")
        self.assertEqual(res["binding"]["model"], "m-1")


if __name__ == "__main__":
    unittest.main()
