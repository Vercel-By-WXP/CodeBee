# -*- coding: utf-8 -*-
"""连载评审链失效回归（2026-09-18 七猫甜宠假未达标案）。

锁定四个行为：
1. 全局评审全挂（评审模型全失败/输出不可解析）→ 判 run 失败，绝不产出
   「未达标」报告——「无法评审」≠「评审不通过」，不能拿空分误杀整批稿件；
2. 评审员全挂时从其它真实智能体补位，出分即正常收尾（对齐章级 fallback）；
3. 打磨后重评全挂 → 保留上一轮全局分与被打磨章的原分，不拿空分覆盖好分；
4. generic CLI 超长提示词（全局评审 6 万字嵌 argv 的 WinError 206 案）→
   落盘临时文件、参数位换读文件指令；短提示词与 stdin 型不受影响。
"""
from __future__ import annotations

import json
import os

from base import BaseTest


def _ok(text=""):
    return {"ok": True, "text": text, "json": None, "cost_usd": 0.0,
            "tokens": 0, "error": "", "raw": {"exit_code": 0}, "sid": ""}


GOOD = json.dumps({"scores": {"情节": 9.0, "人物": 9.0, "文笔": 9.0,
                              "节奏": 9.0, "吸引力": 9.0}, "issues": []}, ensure_ascii=False)
WEAK = json.dumps({"scores": {"情节": 5.0, "人物": 5.0, "文笔": 5.0,
                              "节奏": 5.0, "吸引力": 5.0}, "issues": []}, ensure_ascii=False)
GARBAGE = "抱歉，我这次无法完成评审。"


class SerialReviewBase(BaseTest):
    """直接驱动 _run_serial_review：impl 用 mock（起草不碰 CLI），评审员全 real，
    通过替换 pipeline._run_step 按 role 精确控制每个评审步骤的成败。"""

    def _make(self):
        from app.core import store
        task = store.create_task({
            "type": "serial_novel", "title": "评审链失效回归", "goal": "写两章",
            "workdir": str(self.workdir),
            "serial": {"chapters": 2, "words_per_chapter": 300},
            "implementer": "mock-a", "critics": ["c1", "c2"],
        })
        run = store.create_run("orchestration", task["title"], task_id=task["id"])
        store.update_run(run["id"], status="running")
        return task, run

    def _agents(self, extra_ids=()):
        critics = [{"id": cid, "mode": "real", "kind": "generic", "command": "x",
                    "label": cid} for cid in ("c1", "c2")]
        spares = [{"id": sid, "mode": "real", "kind": "generic", "command": "x",
                   "label": sid} for sid in extra_ids]
        impl = {"id": "mock-a", "mode": "mock", "label": "Mock A"}
        return critics + spares, critics, impl

    def _run_review(self, task, run, agents, critics, impl):
        from app.core import pipeline
        return pipeline._run_serial_review(
            run, task, agents, None, {}, "manual", critics, impl, {}, None, "default")


class TestGlobalReviewAllDead(SerialReviewBase):
    def test_all_dead_fails_run_not_verdict(self):
        """全局评审全挂且无补位 → run 失败；绝不盖「未达标」章。"""
        from app.core import pipeline, store
        task, run = self._make()
        agents, critics, impl = self._agents()

        def fake_step(run_id, role, agent, prompt, workdir, readonly, ev, *a, **kw):
            if role.startswith("critique-c"):
                return _ok(GOOD)          # 章级评审活着：章章达标
            if role == "global-critique":
                return _ok(GARBAGE)       # 全局评审输出不可解析
            return _ok()

        orig = pipeline._run_step
        pipeline._run_step = fake_step
        try:
            self._run_review(task, run, agents, critics, impl)
        finally:
            pipeline._run_step = orig

        run = store.get_run(run["id"])
        self.assertEqual(run["status"], "failed", run.get("verdict"))
        self.assertIn("全局一致性评审全部失败", run.get("error") or "")
        # 关键：不许产出「未达标」报告——没分数就没有资格下质量结论
        report = self._paths.RUNS_DIR / run["id"] / "report.md"
        self.assertFalse(report.is_file(), "评审全挂时不应写出评审报告")
        self.assertFalse((run.get("verdict") or {}), "评审全挂时不应留下 verdict")

    def test_spare_critic_rescues_global_review(self):
        """评审员全挂 → 补位评审出分 → 正常达标收尾。"""
        from app.core import pipeline, store
        task, run = self._make()
        agents, critics, impl = self._agents(extra_ids=("spare",))
        seen_agents = []

        def fake_step(run_id, role, agent, prompt, workdir, readonly, ev, *a, **kw):
            if role.startswith("critique-c"):
                return _ok(GOOD)
            if role == "global-critique":
                seen_agents.append(agent["id"])
                if agent["id"] == "spare":
                    return _ok(GOOD)      # 只有补位评审活下来
                return _ok(GARBAGE)
            return _ok()

        orig = pipeline._run_step
        pipeline._run_step = fake_step
        try:
            self._run_review(task, run, agents, critics, impl)
        finally:
            pipeline._run_step = orig

        run = store.get_run(run["id"])
        self.assertEqual(run["status"], "done", run.get("error"))
        v = run["verdict"]
        self.assertTrue(v["publishable"])
        self.assertTrue(v["global_pass"])
        self.assertIn("spare", seen_agents)   # 补位确实被派上场


class TestPolishKeepsScoresWhenReviewDead(SerialReviewBase):
    def test_polish_rereview_dead_keeps_previous_scores(self):
        """全局低分触发打磨；打磨后重评全挂 → 保留上一轮全局分与章分，不覆盖成空分。"""
        from app.core import pipeline, store
        task, run = self._make()
        agents, critics, impl = self._agents()
        n_global = {"n": 0}   # 全局评审次序：第 1 次=低分触发打磨，之后=打磨后重评（挂）

        def fake_step(run_id, role, agent, prompt, workdir, readonly, ev, *a, **kw):
            if role.startswith("critique-c"):
                # 打磨重评的 prompt 带「打磨后」注记 → 挂；章级主循环 → 活
                return _ok(GARBAGE if "打磨后" in prompt else GOOD)
            if role == "global-critique":
                n_global["n"] += 1
                return _ok(WEAK if n_global["n"] == 1 else GARBAGE)
            return _ok()

        orig = pipeline._run_step
        pipeline._run_step = fake_step
        try:
            self._run_review(task, run, agents, critics, impl)
        finally:
            pipeline._run_step = orig

        run = store.get_run(run["id"])
        self.assertEqual(run["status"], "done", run.get("error"))
        v = run["verdict"]
        self.assertTrue(n_global["n"] > 1, "应至少发生过一次打磨后重评")
        # 全局分保留第一轮的真实低分（5 分），而不是被「无法解析」清空
        self.assertTrue(v["global_scores"])
        self.assertTrue(all(abs(x - 5.0) < 0.01 for x in v["global_scores"].values()))
        # 被打磨的章保留打磨前的真实 9 分，不得被空重评打成 0
        for c in v["chapter_scores"]:
            self.assertTrue(c["passed"], c)
            self.assertTrue(all(x >= 8.9 for x in c["means"].values()), c)
            self.assertFalse(c.get("polished"), c)


class TestLongPromptGoesToFile(BaseTest):
    def test_generic_overlong_prompt_moves_to_file(self):
        """generic CLI 超 2 万字符提示词 → 落盘临时文件，argv 换成读文件指令。"""
        from app.core import runner as R
        agent = {"id": "kimi-code", "kind": "generic", "mode": "real",
                 "command": "kimi", "argv_template": ["-p", "{prompt}"]}
        long_prompt = "评审以下书稿：" + "很久很久以前。" * 4000   # 约 3 万字符
        argv, stdin_text, _, tmp = R._build_call(
            agent, "generic", "", True, None, long_prompt, workdir=str(self.workdir))
        try:
            self.assertIsNone(stdin_text)
            self.assertEqual(len(tmp), 1)
            self.assertNotIn(long_prompt, argv)          # 原文不再嵌 argv
            self.assertIn("已写入文件", argv[-1])        # 参数位换成读文件指令
            self.assertIn(tmp[0], argv[-1])              # 指令里带得上文件路径
            with open(tmp[0], encoding="utf-8") as f:
                self.assertEqual(f.read(), long_prompt)  # 全文无损落盘
        finally:
            for p in tmp:
                try:
                    os.remove(p)
                except OSError:
                    pass

    def test_short_prompt_and_stdin_kinds_unaffected(self):
        """短提示词仍走 argv；stdin 型（codex）超长也不受影响。"""
        from app.core import runner as R
        kimi = {"id": "kimi-code", "kind": "generic", "mode": "real",
                "command": "kimi", "argv_template": ["-p", "{prompt}"]}
        argv, stdin_text, _, tmp = R._build_call(
            kimi, "generic", "", True, None, "短提示词", workdir=None)
        self.assertEqual(argv[-1], "短提示词")
        self.assertEqual(tmp, [])

        codex = {"id": "codex-cli", "kind": "codex", "mode": "real", "command": "codex"}
        long_prompt = "x" * 30000
        argv, stdin_text, _, tmp = R._build_call(
            codex, "codex", "", True, None, long_prompt, workdir=None)
        self.assertEqual(stdin_text, long_prompt)        # stdin 型照旧走管道
        self.assertEqual(tmp, [])

class TestPolishLeaderFinishes(SerialReviewBase):
    """打磨组长（polish-rN）收尾回归（2026-09-19 假 running 实案）。

    组长的 finish_step 原来在整个「子章重改循环+全书重评」之后：子章双败
    （stream disconnected）后代码仍进全书重评，单个评审挂 35 分钟，UI 只见
    打磨 2/3 久卡「工作中」。锁定两个行为：
    1. 子章全部重改失败 → 组长立即 failed 收尾，不再发起全书重评；
    2. 打磨后的全书重评抛异常 → 组长也要落终态，绝不留假 running。

    impl 必须是 real 模式（mock 打磨恒成功走不到病根），因此大纲与 _run_step
    都要接假件：大纲不走 _run_step，real 模式会真外呼（测试挂死教训）。
    """

    def _run_with(self, task, run, agents, critics, impl, fake_step):
        from app.core import pipeline
        orig_step = pipeline._run_step
        orig_outline = pipeline.planner.make_serial_outline

        def fake_outline(t, author_agent=None, workdir=None, ev=None, log_path=None):
            n = int((t.get("serial") or {}).get("chapters") or 2)
            return {"book_title": "回归书", "source": "llm(fake)",
                    "chapters": [{"title": "第 %d 章" % (k + 1), "beats": "剧情",
                                  "hook": "钩子"} for k in range(n)]}

        pipeline.planner.make_serial_outline = fake_outline
        pipeline._run_step = fake_step
        try:
            return pipeline._run_serial_review(
                run, task, agents, None, {}, "manual", critics, impl, {}, None, "default")
        finally:
            pipeline._run_step = orig_step
            pipeline.planner.make_serial_outline = orig_outline

    @staticmethod
    def _dead_res():
        return {"ok": False, "text": "", "json": None, "cost_usd": 0.0,
                "tokens": 0, "error": "stream disconnected", "raw": {}, "sid": ""}

    @staticmethod
    def _seed_chapter(workdir, i):
        from app.core import pipeline
        pipeline._write_chapter(workdir, i, "山" * 500)

    def test_all_rewrites_dead_leader_fails_without_global_rereview(self):
        """子章重改全败 → 组长 failed + 跳过全书重评（评审链只跑第一轮）。"""
        from app.core import pipeline, store
        task, run = self._make()
        agents, critics, _ = self._agents()
        impl = {"id": "impl-r", "mode": "real", "kind": "generic",
                "command": "x", "label": "Impl R"}
        n_global = {"n": 0}

        def fake_step(run_id, role, agent, prompt, workdir, readonly, ev, *a, **kw):
            if role.startswith("draft-c"):
                self._seed_chapter(workdir, int(role.split("c")[-1]))
                return _ok()
            if role.startswith("critique-c"):
                return _ok(GOOD)          # 章级达标：不进章级修订分支
            if role == "global-critique":
                n_global["n"] += 1
                return _ok(WEAK)          # 全局低分 → 触发打磨
            if role.startswith("polish-c"):
                return self._dead_res()   # ★ 重改全败（本次实案病根）
            return _ok()

        self._run_with(task, run, agents, critics, impl, fake_step)

        run = store.get_run(run["id"])
        leader = next(s for s in run["steps"] if s["role"] == "polish-r1")
        self.assertEqual(leader["status"], "failed",
                         "重改全败后组长必须收尾，不得挂 running")
        self.assertIn("重改未成功", leader.get("summary") or "")
        self.assertEqual(n_global["n"], len(critics),
                         "章稿没变就不得再烧一轮全书重评（bug 存在时会翻倍）")
        self.assertEqual(run["status"], "done", run.get("error"))

    def test_global_rereview_crash_still_finishes_leader(self):
        """打磨后全书重评抛异常 → 组长落 failed 终态，异常照常上抛。"""
        from app.core import store
        task, run = self._make()
        agents, critics, _ = self._agents()
        impl = {"id": "impl-r", "mode": "real", "kind": "generic",
                "command": "x", "label": "Impl R"}
        n_global = {"n": 0}

        def fake_step(run_id, role, agent, prompt, workdir, readonly, ev, *a, **kw):
            if role.startswith("draft-c"):
                self._seed_chapter(workdir, int(role.split("c")[-1]))
                return _ok()
            if role.startswith("critique-c"):
                return _ok(GARBAGE if "打磨后" in prompt else GOOD)
            if role == "global-critique":
                n_global["n"] += 1
                if n_global["n"] > len(critics):   # 第一轮=评审员数；超出即重评轮
                    raise RuntimeError("评审管道爆炸")
                return _ok(WEAK)
            if role.startswith("polish-c"):
                self._seed_chapter(workdir, int(role.split("c")[-1]))
                return _ok()              # 重改成功 → fixed 非空 → 进重评
            return _ok()

        self.assertRaises(RuntimeError, self._run_with,
                          task, run, agents, critics, impl, fake_step)

        run = store.get_run(run["id"])
        leader = next(s for s in run["steps"] if s["role"] == "polish-r1")
        self.assertEqual(leader["status"], "failed",
                         "重评异常时组长也必须收尾，不得留假 running")
        self.assertIn("异常中止", leader.get("summary") or "")
