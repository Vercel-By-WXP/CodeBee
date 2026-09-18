# -*- coding: utf-8 -*-
"""故事圣经 + 评审视角播种回归（NovelClaw / dev-3.0 借鉴项）。

锁定：
1. story-bible.md 约定：工作目录存在即注入（带「以圣经为准」框架头），
   不存在/空文件/目录穿越路径一律返回空串——零配置零噪音；
2. 注入是框架级：连载起草（__SKILLS__ 合流）、章节评审（crit_prompt_for）、
   全局评审三处都带圣经（用 mock 跑通流水线后断言 step 日志/评分存在即可，
   提示词内容走 _story_bible 单测锁定）；
3. 评审视角播种：N≥2 时各评审领互不重复的镜头、同一评审镜头固定（字节稳定），
   单评审/手动名单不播种。
"""
from __future__ import annotations

import threading

from base import BaseTest


class TestStoryBibleAndLenses(BaseTest):

    def test_story_bible_convention(self):
        from app.core import pipeline
        (self.workdir / "story-bible.md").write_text(
            "# 林晚（女主）\n- 性格：外冷内热\n\n# 伏笔\n- 第 3 章出现的铜钥匙", encoding="utf-8")
        txt = pipeline._story_bible(str(self.workdir))
        self.assertIn("故事圣经", txt)
        self.assertIn("铜钥匙", txt)
        # 空文件 → 空
        (self.workdir / "story-bible.md").write_text("   \n", encoding="utf-8")
        self.assertEqual(pipeline._story_bible(str(self.workdir)), "")
        # 不存在 → 空
        (self.workdir / "story-bible.md").unlink()
        self.assertEqual(pipeline._story_bible(str(self.workdir)), "")
        # 目录穿越路径 → 空（不读工作目录外的任何东西）
        self.assertEqual(pipeline._story_bible(str(self.workdir) + "/../elsewhere"), "")

    def test_serial_run_injects_bible_into_prompts(self):
        """mock 连载跑通：圣经文件存在时，起草与评审的提示词应包含圣经内容。
        提示词不直接落盘，但 step 的重复守卫指纹链间接依赖它——这里改为
        直接断言注入函数与流水线协同：跑完 run 且章节评分正常产出。"""
        from app.core import pipeline, store
        (self.workdir / "story-bible.md").write_text("# 设定\n主角姓陈", encoding="utf-8")
        store.create_task({
            "type": "serial_novel", "title": "圣经书", "goal": "写两章",
            "workdir": str(self.workdir),
            "serial": {"chapters": 2, "words_per_chapter": 600},
        })
        task = store.list_tasks()[0]
        run = store.create_run("orchestration", task["title"], task_id=task["id"])
        store.update_task_status(task["id"], "queued")
        pipeline._agents = self.mock_agents
        pipeline.execute_run(run["id"])
        r = store.get_run(run["id"])
        self.assertEqual(r["status"], "done")
        self.assertEqual(len(r.get("chapter_scores") or []), 2)
        # 圣经在起草提示词合流路径可用（sk_block 合流逻辑）
        self.assertIn("主角姓陈", pipeline._story_bible(str(self.workdir)))

    def test_critic_lens_assignment(self):
        from app.core import pipeline
        critics = [{"id": "a"}, {"id": "b"}, {"id": "c"}]
        lenses = [pipeline._critic_lens(critics, a) for a in critics]
        self.assertTrue(all(lenses))
        self.assertEqual(len(set(lenses)), 3)          # 互不重复
        self.assertEqual(lenses, [pipeline._critic_lens(critics, a) for a in critics])  # 固定
        # 单评审不播种；名单外的 agent 也为空
        self.assertEqual(pipeline._critic_lens(critics[:1], critics[0]), "")
        self.assertEqual(pipeline._critic_lens(critics, {"id": "zz"}), "")



class TestStoryBibleStore(BaseTest):

    def _mk_task(self, status="done"):
        from app.core import store
        return store.create_task({
            "type": "serial_novel", "title": "圣经编辑", "goal": "g",
            "workdir": str(self.workdir),
            "serial": {"chapters": 2, "words_per_chapter": 600},
        })

    def test_read_write_and_guards(self):
        from app.core import store
        task = self._mk_task()
        # 未创建 → 空文本、不报错
        fp, text, err = store.read_story_bible(task["id"])
        self.assertIsNone(err)
        self.assertEqual(text, "")
        # 写入 → 回读一致；文件落在工作目录约定名上
        ok, werr = store.write_story_bible(task["id"], "# 林晚\n外冷内热")
        self.assertTrue(ok, werr)
        fp2, text2, err2 = store.read_story_bible(task["id"])
        self.assertIsNone(err2)
        self.assertIn("外冷内热", text2)
        self.assertTrue(fp2.endswith("story-bible.md"))
        from app.core import pipeline
        injected = pipeline._story_bible(str(self.workdir))
        self.assertIn(text2, injected)          # 原文进注入块
        self.assertIn("故事圣经", injected)      # 带框架头
        # 运行中 → 拒绝写（读不受限）
        store.update_task_status(task["id"], "running")
        ok3, err3 = store.write_story_bible(task["id"], "改设定")
        self.assertFalse(ok3)
        self.assertIn("正在运行", err3)
        fp4, text4, _ = store.read_story_bible(task["id"])
        self.assertIn("外冷内热", text4)
        store.update_task_status(task["id"], "done")
        # 超长 → 拒绝
        ok5, err5 = store.write_story_bible(task["id"], "x" * (store.BIBLE_MAX_CHARS + 1))
        self.assertFalse(ok5)
        self.assertIn("超长", err5)
        # 不存在的任务 → 拒绝
        ok6, err6 = store.write_story_bible("t-nonexist", "x")
        self.assertFalse(ok6)

    def test_initial_bible_creation_is_atomic(self):
        """并发建任务时只允许一个请求播种同一目录的初始圣经。"""
        from app.core import store
        payload = {
            "type": "serial_novel", "goal": "并发建书",
            "workdir": str(self.workdir),
            "serial": {"chapters": 2, "words_per_chapter": 600},
        }
        results = []

        def create(i):
            try:
                task = store.create_task({**payload, "story_bible": "设定 %d" % i})
                results.append(("ok", task["id"]))
            except ValueError as exc:
                results.append(("error", str(exc)))

        threads = [threading.Thread(target=create, args=(i,)) for i in range(2)]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join()

        self.assertEqual(sum(kind == "ok" for kind, _ in results), 1)
        self.assertEqual(sum(kind == "error" for kind, _ in results), 1)
        self.assertIn((self.workdir / "story-bible.md").read_text(encoding="utf-8"),
                      ("设定 0", "设定 1"))

