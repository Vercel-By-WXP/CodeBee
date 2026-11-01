# -*- coding: utf-8 -*-
"""记忆中心两个真实开关的后端门控回归（2026-10-29）。

- ui_prefs 落盘读写：缺省全开、bool 化、坏文件即弃、未知键忽略
- pipeline._read_project_memory：对话记忆关闭 → 不注入（返回空串）
- pipeline._capture_project_memory：对话记忆关闭 → 不沉淀（返回 None 且不调 capture_run）
- pipeline._learn_global_memory：本地记忆关闭 → 不做 skills/knowledge 收割
- capability_runtime.prepare：本地记忆关闭 → 快照里没有经验库/知识库块，
  但快照结构与 meta 键不变（消费方无感知）
读取失败一律 fail-open（按开），开关永远不能把任务本身打断。
"""
import json
import tempfile
import unittest
from pathlib import Path

from app.core import capability_runtime, paths, pipeline, project_memory, skills, knowledge, ui_prefs


class UiPrefsTestBase(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.root = Path(self._tmp.name)
        self._old_data = paths.DATA_DIR
        paths.DATA_DIR = self.root
        self.addCleanup(self._restore)

    def _restore(self):
        paths.DATA_DIR = self._old_data


class UiPrefsStoreTest(UiPrefsTestBase):
    def test_defaults_all_open_without_file(self):
        view = ui_prefs.load()
        self.assertTrue(view["memory_conversation"])
        self.assertTrue(view["memory_local_skills"])
        self.assertIsNone(ui_prefs.get("nope"))

    def test_save_roundtrip_and_bool_coercion(self):
        view = ui_prefs.save({"memory_conversation": False, "memory_local_skills": 0})
        self.assertFalse(view["memory_conversation"])
        self.assertFalse(view["memory_local_skills"])
        # 重新 load 走磁盘
        self.assertFalse(ui_prefs.load()["memory_conversation"])
        # 再存回开
        view = ui_prefs.save({"memory_conversation": "yes"})
        self.assertTrue(view["memory_conversation"])

    def test_unknown_and_missing_keys_ignored(self):
        view = ui_prefs.save({"memory_conversation": False, "evil": "<script>"})
        self.assertNotIn("evil", view)
        self.assertTrue(view["memory_local_skills"])

    def test_corrupt_file_falls_back_to_defaults(self):
        ui_prefs.save({"memory_conversation": False})
        ui_prefs._file().write_text("{broken", encoding="utf-8")
        view = ui_prefs.load()
        self.assertTrue(view["memory_conversation"])


class PipelineConversationGateTest(UiPrefsTestBase):
    def test_read_gated_when_conversation_off(self):
        ui_prefs.save({"memory_conversation": False})
        called = []

        orig = project_memory.active_text

        def _spy(*a, **k):
            called.append(1)
            return "SHOULD-NOT-INJECT"

        project_memory.active_text = _spy
        self.addCleanup(setattr, project_memory, "active_text", orig)
        self.assertEqual(pipeline._read_project_memory(str(self.root)), "")
        self.assertEqual(called, [])

    def test_read_passes_when_on(self):
        orig = project_memory.active_text
        project_memory.active_text = lambda *a, **k: "MEMO"
        self.addCleanup(setattr, project_memory, "active_text", orig)
        self.assertEqual(pipeline._read_project_memory(str(self.root)), "MEMO")

    def test_capture_gated_when_conversation_off(self):
        ui_prefs.save({"memory_conversation": False})
        called = []

        orig = project_memory.capture_run

        def _spy(*a, **k):
            called.append(1)
            return {"id": "x"}

        project_memory.capture_run = _spy
        self.addCleanup(setattr, project_memory, "capture_run", orig)
        self.assertIsNone(pipeline._capture_project_memory({"id": "t1"}, {"id": "r1"}))
        self.assertEqual(called, [])

    def test_capture_passes_when_on(self):
        orig = project_memory.capture_run
        project_memory.capture_run = lambda task, run: {"id": "m1"}
        self.addCleanup(setattr, project_memory, "capture_run", orig)
        entry = pipeline._capture_project_memory({"id": "t1"}, {"id": "r1"})
        self.assertEqual(entry, {"id": "m1"})


class PipelineLearnGateTest(UiPrefsTestBase):
    def setUp(self):
        super().setUp()
        self._old_get_run = pipeline.store.get_run
        pipeline.store.get_run = lambda run_id: {"id": run_id}
        self.addCleanup(setattr, pipeline.store, "get_run", self._old_get_run)

    def test_learn_gated_when_local_off(self):
        ui_prefs.save({"memory_local_skills": False})
        calls = []

        s_orig, k_orig = skills.learn_async, knowledge.learn_async
        skills.learn_async = lambda run_id: calls.append("skills")
        knowledge.learn_async = lambda run_id: calls.append("knowledge")
        self.addCleanup(setattr, skills, "learn_async", s_orig)
        self.addCleanup(setattr, knowledge, "learn_async", k_orig)
        pipeline._learn_global_memory("r1")
        self.assertEqual(calls, [])

    def test_learn_passes_when_on(self):
        calls = []

        s_orig, k_orig = skills.learn_async, knowledge.learn_async
        skills.learn_async = lambda run_id: calls.append("skills")
        knowledge.learn_async = lambda run_id: calls.append("knowledge")
        self.addCleanup(setattr, skills, "learn_async", s_orig)
        self.addCleanup(setattr, knowledge, "learn_async", k_orig)
        pipeline._learn_global_memory("r1")
        self.assertEqual(calls, ["skills", "knowledge"])

    def test_learn_fail_open_when_prefs_broken(self):
        # 开关读取抛异常 → 按开处理，收割照常（开关不能打断任务闭环）
        calls = []
        s_orig, k_orig = skills.learn_async, knowledge.learn_async
        skills.learn_async = lambda run_id: calls.append("skills")
        knowledge.learn_async = lambda run_id: calls.append("knowledge")

        orig_get = ui_prefs.get
        ui_prefs.get = lambda key: (_ for _ in ()).throw(RuntimeError("boom"))
        self.addCleanup(setattr, ui_prefs, "get", orig_get)
        self.addCleanup(setattr, skills, "learn_async", s_orig)
        self.addCleanup(setattr, knowledge, "learn_async", k_orig)
        pipeline._learn_global_memory("r1")
        self.assertEqual(calls, ["skills", "knowledge"])


class CapabilitySnapshotGateTest(UiPrefsTestBase):
    def setUp(self):
        super().setUp()
        capability_runtime._RUNS.clear()
        self.addCleanup(capability_runtime._RUNS.clear)

    def _patch_blocks(self):
        s_orig = capability_runtime.skills.block_for
        k_orig = capability_runtime.knowledge.block_for
        capability_runtime.skills.block_for = lambda task, **k: ("LESSONS", ["s1"])
        capability_runtime.knowledge.block_for = lambda task: "KBLOCK"
        self.addCleanup(setattr, capability_runtime.skills, "block_for", s_orig)
        self.addCleanup(setattr, capability_runtime.knowledge, "block_for", k_orig)

    def test_snapshot_drops_lessons_kb_when_local_off(self):
        self._patch_blocks()
        ui_prefs.save({"memory_local_skills": False})
        snap = capability_runtime.prepare({"type": "*"}, "r-off")
        self.assertNotIn("LESSONS", snap["block"])
        self.assertNotIn("KBLOCK", snap["block"])
        self.assertEqual(snap["skills"], [])
        self.assertFalse(snap["knowledge"])
        # 结构键不变，消费方无感知
        for key in ("scope", "plugins", "external_skills", "mcp", "runtime", "block"):
            self.assertIn(key, snap)

    def test_snapshot_keeps_lessons_kb_when_on(self):
        self._patch_blocks()
        snap = capability_runtime.prepare({"type": "*"}, "r-on")
        self.assertIn("LESSONS", snap["block"])
        self.assertIn("KBLOCK", snap["block"])
        self.assertEqual(snap["skills"], ["s1"])
        self.assertTrue(snap["knowledge"])


if __name__ == "__main__":
    unittest.main()
