# -*- coding: utf-8 -*-
"""连载追话答疑 + 评审解析三道网 + 恢复单飞守卫回归（2026-09-18 七猫女频案）。

锁定六个行为：
1. 评审 JSON 内嵌未转义引号（"第5章"冷战三天"…"）→ 宽松修复后照常出分，
   不再把正常评审判成「输出不可解析」（当日连环烧掉 3 轮自动续跑的根因）；
2. 花括号兜底扫描掉进内层时，返回值本身就是 维度→分 本体 → as_scores 包回；
3. agentic CLI 把 JSON 写进文件、stdout 只留中文总结 → 正文按维度提分兜底；
4. 连载任务追话（/chat 或终态 run 递话）→ 起单步答疑轮（op=qa），不再整本重跑；
5. 自动续跑/续跑副本出队遇同任务已有活跃运行 → 让位，不再连环排队撞车；
6. 重启后遗留的 queued 编排运行重新入队（队列在内存里，进程死=排队项僵尸）。
"""
from __future__ import annotations

import json

from base import BaseTest


def _ok(text=""):
    return {"ok": True, "text": text, "json": None, "cost_usd": 0.0,
            "tokens": 0, "error": "", "raw": {"exit_code": 0}, "sid": ""}


def _err(msg="boom"):
    return {"ok": False, "text": "", "json": None, "cost_usd": 0.0,
            "tokens": 0, "error": msg, "raw": {"exit_code": 1}, "sid": ""}


# 2026-09-18 r-20260918-162131 真实病案缩样：围栏内 issues.note 带未转义引号
BROKEN_QUOTES = (
    "• ```json\n  {\n    \"scores\": {\n      \"情节\": 9,\n      \"人物\": 9,\n"
    "      \"文笔\": 9,\n      \"节奏\": 8,\n      \"吸引力\": 9\n    },\n"
    "    \"issues\": [\n      {\n        \"dim\": \"节奏\",\n"
    "        \"severity\": \"minor\",\n"
    "        \"note\": \"第5章\"冷战三天\"使用段落跳跃式概述，节奏稍快\"\n"
    "      }\n    ],\n    \"summary\": \"整体达到签约水平\"\n  }\n  ```")
DIMS = ["情节", "人物", "文笔", "节奏", "吸引力"]
PROSE = ("• 评审完成，JSON 已写入 review_result.json。简要总结：\n\n"
         "  **评分（宁严勿宽）**\n  - 情节 8 / 人物 8 / 文笔 9 / 节奏 8 / 吸引力 8\n")


class TestParseNets(BaseTest):
    """评审输出解析三道网。"""

    def test_broken_quotes_fence_still_scores(self):
        from app.core import runner
        gj = runner.extract_json(BROKEN_QUOTES)
        self.assertTrue(isinstance(gj, dict) and gj.get("scores"),
                        "内嵌引号病案必须宽松修复出分")
        self.assertEqual(len(gj["scores"]), 5)
        self.assertEqual(len(gj.get("issues") or []), 1)   # issues 也完整救回

    def test_good_json_passthrough(self):
        from app.core import runner
        good = json.dumps({"scores": {"情节": 8}, "issues": []}, ensure_ascii=False)
        self.assertEqual(runner.extract_json(good)["scores"], {"情节": 8})
        self.assertIsNone(runner.extract_json("我觉得写得还行"))

    def test_as_scores_wraps_inner_object(self):
        from app.core import runner
        inner = {"情节": 9, "人物": 9, "文笔": 9, "节奏": 8, "吸引力": 9}
        wrapped = runner.as_scores(dict(inner))
        self.assertEqual(set(wrapped["scores"]), set(inner))
        # 有 scores 键的原样通过；非纯数字 dict 不动
        keep = {"scores": {"情节": 8}, "issues": []}
        self.assertEqual(runner.as_scores(keep), keep)
        mixed = {"情节": 8, "note": "还行"}
        self.assertEqual(runner.as_scores(mixed), mixed)

    def test_scores_from_prose(self):
        from app.core import runner
        got = runner.scores_from_prose(PROSE, DIMS)
        self.assertEqual(got, {"情节": 8.0, "人物": 8.0, "文笔": 9.0,
                               "节奏": 8.0, "吸引力": 8.0})
        self.assertEqual(runner.scores_from_prose("情节不错，张力十足", DIMS), {})

    def test_critique_json_failure_shape(self):
        from app.core import pipeline
        gj = pipeline._critique_json({"text": "我觉得写得还行", "error": ""}, DIMS)
        self.assertEqual(gj["scores"], {})
        self.assertTrue(gj["summary"].startswith("评审输出无法解析"))


class TestSerialQaRound(BaseTest):
    """追话答疑轮：op=qa 单步只读，不再整本重跑。"""

    def _make(self):
        from app.core import store
        task = store.create_task({
            "type": "serial_novel", "title": "答疑回归", "goal": "写一章",
            "workdir": str(self.workdir),
            "serial": {"chapters": 1, "words_per_chapter": 300},
            "implementer": "impl-a", "critics": ["c1"],
        })
        run = store.create_run("orchestration", task["title"], task_id=task["id"])
        return task, run

    def test_qa_done_by_critic(self):
        from app.core import pipeline, store
        task, run = self._make()
        store.update_run(run["id"], op="qa", qa_text="为啥没有第九章呢？")
        agents = [{"id": "c1", "mode": "real", "kind": "generic", "command": "x",
                   "label": "c1"},
                  {"id": "impl-a", "mode": "real", "kind": "generic", "command": "x",
                   "label": "impl-a"}]
        seen = {}

        def fake_step(run_id, role, agent, prompt, workdir, readonly, ev, *a, **kw):
            seen.update(role=role, agent=agent["id"], readonly=readonly,
                        q="为啥没有第九章" in prompt)
            return _ok("第九章本来就不存在：全书规划 8 章。")

        orig = pipeline._run_step
        pipeline._run_step = fake_step
        try:
            pipeline._run_serial_qa(store.get_run(run["id"]), task, agents, None)
        finally:
            pipeline._run_step = orig
        got = store.get_run(run["id"])
        self.assertEqual(got["status"], "done")
        self.assertEqual(seen["role"], "qa")
        self.assertTrue(seen["readonly"], "答疑轮必须只读")
        self.assertTrue(seen["q"], "问题文本必须进提示词")
        self.assertEqual(seen["agent"], "c1", "评审组优先作答")
        self.assertTrue(got["verdict"].get("qa"))

    def test_qa_falls_over_dead_agent(self):
        from app.core import pipeline, store
        task, run = self._make()
        store.update_run(run["id"], op="qa", qa_text="为啥？")
        agents = [{"id": "c1", "mode": "real", "kind": "generic", "command": "x",
                   "label": "c1"},
                  {"id": "impl-a", "mode": "real", "kind": "generic", "command": "x",
                   "label": "impl-a"}]
        calls = []

        def fake_step(run_id, role, agent, prompt, workdir, readonly, ev, *a, **kw):
            calls.append(agent["id"])
            if agent["id"] == "c1":
                return _err("绑定链全部失效")
            return _ok("已回复。")

        orig = pipeline._run_step
        pipeline._run_step = fake_step
        try:
            pipeline._run_serial_qa(store.get_run(run["id"]), task, agents, None)
        finally:
            pipeline._run_step = orig
        self.assertEqual(calls, ["c1", "impl-a"], "死链自动落到下一个回答者")
        self.assertEqual(store.get_run(run["id"])["status"], "done")

    def test_qa_all_dead_fails(self):
        from app.core import pipeline, store
        task, run = self._make()
        store.update_run(run["id"], op="qa")
        agents = [{"id": "c1", "mode": "real", "kind": "generic", "command": "x",
                   "label": "c1"}]

        def fake_step(*a, **kw):
            return _err("绑定链全部失效")

        orig = pipeline._run_step
        pipeline._run_step = fake_step
        try:
            pipeline._run_serial_qa(store.get_run(run["id"]), task, agents, None)
        finally:
            pipeline._run_step = orig
        got = store.get_run(run["id"])
        self.assertEqual(got["status"], "failed")
        self.assertIn("c1", got["error"])


class TestRecoveryGuards(BaseTest):
    """恢复单飞：副本让位、续跑不再排重、重启补队。"""

    def _serial_task(self, title="单飞回归"):
        from app.core import store
        return store.create_task({
            "type": "serial_novel", "title": title, "goal": "写一章",
            "workdir": str(self.workdir),
            "serial": {"chapters": 1, "words_per_chapter": 300},
            "implementer": "impl-a", "critics": ["c1"],
        })

    def test_maybe_auto_resume_skips_when_task_active(self):
        from app.core import store, jobs
        task = self._serial_task()
        dead = store.create_run("orchestration", task["title"], task_id=task["id"])
        store.update_run(dead["id"], status="failed", ended_at="2026-09-18 16:21:56")
        active = store.create_run("orchestration", task["title"], task_id=task["id"])
        store.update_run(active["id"], status="running", started_at="2026-09-18 16:21:34")
        self.assertFalse(jobs._maybe_auto_resume(dead["id"]),
                        "同任务已有活跃运行时绝不排续跑副本")

    def test_yield_duplicate_cancels_copy_not_user_round(self):
        from app.core import store, jobs
        task = self._serial_task()
        user_round = store.create_run("orchestration", task["title"], task_id=task["id"])
        store.update_run(user_round["id"], status="running")
        copy = store.create_run("orchestration", task["title"], task_id=task["id"])
        store.update_run(copy["id"], auto_resumed_from=user_round["id"])
        self.assertTrue(jobs._yield_duplicate(copy["id"]), "续跑副本出队必须让位")
        self.assertEqual(store.get_run(copy["id"])["status"], "cancelled")
        self.assertFalse(jobs._yield_duplicate(user_round["id"]), "用户轮不受拦")

    def test_requeue_pending_after_restart(self):
        from app.core import store, jobs
        task = self._serial_task()
        stuck = store.create_run("orchestration", task["title"], task_id=task["id"])
        store.update_run(stuck["id"], status="queued")
        done = store.create_run("orchestration", task["title"], task_id=task["id"])
        store.update_run(done["id"], status="done")
        got = []
        while not jobs._QUEUE.empty():
            got.append(jobs._QUEUE.get_nowait())
        n = jobs.requeue_pending()
        self.assertEqual(n, 1, "只补 queued 的编排运行，done/failed 不补")
        item = jobs._QUEUE.get_nowait()
        self.assertEqual(item["run_id"], stuck["id"])
        self.assertEqual(item["kind"], "orchestration")

    def test_requeue_pending_dedupes_active_task(self):
        from app.core import store, jobs
        task = self._serial_task("单飞回归2")
        running = store.create_run("orchestration", task["title"], task_id=task["id"])
        store.update_run(running["id"], status="running")
        queued = store.create_run("orchestration", task["title"], task_id=task["id"])
        store.update_run(queued["id"], status="queued")
        while not jobs._QUEUE.empty():
            jobs._QUEUE.get_nowait()
        self.assertEqual(jobs.requeue_pending(), 0, "同任务已有活跃运行不补队")


if __name__ == "__main__":
    unittest.main()
