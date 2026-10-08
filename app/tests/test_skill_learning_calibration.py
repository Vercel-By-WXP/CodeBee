import unittest
from unittest.mock import patch

from core import skills


class SkillLearningCalibrationTests(unittest.TestCase):
    def test_single_run_weak_dimension_is_labeled_as_observation_not_repeated_history(self):
        task = {"type": "novel"}
        run = {"verdict": {
            "threshold": 7.0,
            "chapter_scores": [{"chapter": 1, "title": "开篇", "means": {"节奏": 5.5}}],
        }}

        lessons = skills._fallback_lessons(task, run)

        self.assertEqual(len(lessons), 1)
        self.assertTrue(lessons[0]["title"].startswith("本次观察："))
        self.assertNotIn("反复", lessons[0]["title"] + lessons[0]["content"])
        self.assertIn("单次运行", lessons[0]["content"])

    def test_failed_or_infrastructure_run_does_not_learn_quality_lessons(self):
        run = {"id": "r1", "task_id": "t1", "status": "failed",
               "verdict": {"pass": False, "chapter_scores": [
                   {"chapter": 1, "means": {"节奏": 2.0}}]},
               "steps": [{"agent": "writer"}]}
        with patch("core.store.get_run", return_value=run), \
             patch("core.store.get_task", return_value={"type": "novel"}), \
             patch.object(skills, "upsert_lesson") as upsert, \
             patch.object(skills, "note_outcome"):
            self.assertEqual(skills.learn_from_run("r1", use_orchestrator=False), 0)
        upsert.assert_not_called()

    def test_successful_single_run_does_not_call_a_practice_verified(self):
        prompt = skills.LEARN_PROMPT

        self.assertIn("候选做法", prompt)
        self.assertIn("单次运行不能称为已验证", prompt)
        self.assertNotIn("已验证有效的做法", prompt)

    def test_done_run_without_review_signal_does_not_learn(self):
        run = {"id": "r3", "task_id": "t3", "status": "done",
               "verdict": {"note": "runner completed"}, "steps": [{"agent": "writer"}]}
        with patch("core.store.get_run", return_value=run), \
             patch("core.store.get_task", return_value={"type": "novel"}), \
             patch.object(skills, "_fallback_lessons", return_value=[{
                 "title": "误生成", "content": "没有评审依据", "dim": "文笔"}]) as fallback, \
             patch.object(skills, "upsert_lesson") as upsert, \
             patch.object(skills, "note_outcome"):
            self.assertEqual(skills.learn_from_run("r3", use_orchestrator=False), 0)
        fallback.assert_not_called()
        upsert.assert_not_called()

    def test_done_run_without_evaluation_steps_does_not_learn(self):
        run = {"id": "r4", "task_id": "t4", "status": "done",
               "verdict": {"pass": True, "chapter_scores": [
                   {"chapter": 1, "means": {"节奏": 8.0}}]}, "steps": []}
        with patch("core.store.get_run", return_value=run), \
             patch("core.store.get_task", return_value={"type": "novel"}), \
             patch.object(skills, "upsert_lesson") as upsert, \
             patch.object(skills, "note_outcome"):
            self.assertEqual(skills.learn_from_run("r4", use_orchestrator=False), 0)
        upsert.assert_not_called()

    def test_model_lessons_without_evidence_prefix_are_rejected(self):
        run = {"id": "r5", "task_id": "t5", "status": "done",
               "verdict": {"pass": True, "chapter_scores": [
                   {"chapter": 1, "means": {"节奏": 8.0}}]},
               "steps": [{"agent": "reviewer"}]}
        fake_model = {"ok": True, "text": '{"lessons":[{"title":"先制造冲突","content":"以后都这样写"}]}' }
        with patch("core.store.get_run", return_value=run), \
             patch("core.store.get_task", return_value={"type": "novel"}), \
             patch("core.modelhub.resolve_orchestrator", return_value=("p", "m")), \
             patch("core.modelhub.chat", return_value=fake_model), \
             patch("core.runner.extract_json", return_value={"lessons": [
                 {"title": "先制造冲突", "content": "以后都这样写"}]}), \
             patch.object(skills, "upsert_lesson") as upsert, \
             patch.object(skills, "note_outcome"):
            self.assertEqual(skills.learn_from_run("r5", use_orchestrator=True), 0)
        upsert.assert_not_called()

    def test_fallback_learning_remains_scoped_to_task_type(self):
        run = {"id": "r2", "task_id": "t2", "status": "done",
               "verdict": {"pass": False, "threshold": 7.0,
                   "chapter_scores": [{"chapter": 1, "means": {"文笔": 4.0}}]},
               "steps": [{"agent": "writer"}]}
        with patch("core.store.get_run", return_value=run), \
             patch("core.store.get_task", return_value={"type": "novel"}), \
             patch.object(skills, "upsert_lesson", return_value={"id": "l1"}) as upsert, \
             patch.object(skills, "note_outcome"):
            self.assertEqual(skills.learn_from_run("r2", use_orchestrator=False), 1)
        self.assertEqual(upsert.call_args.args[0], "novel")


if __name__ == "__main__":
    unittest.main()
