# -*- coding: utf-8 -*-
"""连载打磨回路五连修回归（2026-10-03 不周山守村人 21–28 章脱纲空转案）。

锁定六个行为：
1. `_chapters_named_in`：全局评审 issue 文本里点名的章号（单章/范围/各种
   连字符）→ 集合；越界章号丢弃；
2. `_archived_preface`/`_full_manuscript`：前作章文件已归档时从「已成稿/」
   找回前文；全书评审文本本批保底在场、前文只用剩余预算从尾部截取；
3. `_dedup_issues`：跨轮 issues 同章同维度同要点只留首条；
4. 打磨选章：全局 major 点名的章优先（章分全达标的脱纲章只按分数永远选
   不中）；点名章重改提示词放开「不得改主线」，未点名章照旧锁死；
5. aider 超长提示词改走 `--read`（CLI 自己读文件）——不再指挥模型「用读
   文件工具」被当注入拒答（全局有效评审恒 1 名的病根）；
6. 陈账换新账：打磨/章内修订后重评达标，旧轮 majors 必须撤下，publishable
   不被陈账永远压着 False；重评全挂则保留旧账（评审链挂≠书变差）。
"""
from __future__ import annotations

import json
import os

from base import BaseTest


def _ok(text=""):
    return {"ok": True, "text": text, "json": None, "cost_usd": 0.0,
            "tokens": 0, "error": "", "raw": {"exit_code": 0}, "sid": ""}


GOOD = json.dumps({"scores": {"情节": 9.0, "人物": 9.0, "文笔": 9.0,
                              "节奏": 9.0, "吸引力": 9.0}, "issues": []},
                  ensure_ascii=False)
OK8 = json.dumps({"scores": {"情节": 8.0, "人物": 8.0, "文笔": 8.0,
                             "节奏": 8.0, "吸引力": 8.0}, "issues": []},
                 ensure_ascii=False)
WEAK = json.dumps({"scores": {"情节": 5.0, "人物": 5.0, "文笔": 5.0,
                              "节奏": 5.0, "吸引力": 5.0}, "issues": []},
                  ensure_ascii=False)
GARBAGE = "抱歉，我无法读取您本地的文件。"
NAMED_ISSUE = ("第1章开篇即脱纲：章纲要求生计压力线，成稿写成了超自然线"
               "（必须按本章大纲推翻重写）")


def _global_weak_named():
    """带点名 major 的全局低分输出（复刻实案：分数低 + issue 点名具体章）。"""
    return json.dumps({"scores": {"情节": 5.0, "人物": 5.0, "文笔": 5.0,
                                  "节奏": 5.0, "吸引力": 5.0},
                       "issues": [{"dim": "情节", "severity": "major",
                                   "note": NAMED_ISSUE, "quote": "香灰气。照川后脖颈"}]},
                      ensure_ascii=False)


class TestNamedHelpers(BaseTest):
    def test_chapters_named_in_variants(self):
        from app.core import pipeline as P
        issues = [{"note": "第22章视角违约"},
                  {"note": "第26—28章成稿与章纲全面脱轨"},
                  {"note": "先看第26-28章，再看第30章"},
                  {"issue": "第2章～第3章复用同一场面"}]   # note 缺失回退 issue 键
        self.assertEqual(P._chapters_named_in(issues), {2, 3, 22, 26, 27, 28, 30})

    def test_chapters_named_in_range_clamp_and_edges(self):
        from app.core import pipeline as P
        issues = [{"note": "第20章、第21章、第28章、第29章都在内"}]
        self.assertEqual(P._chapters_named_in(issues, lo=21, hi=28), {21, 28})
        # 倒序范围归一；空输入；非 dict 安全
        self.assertEqual(P._chapters_named_in([{"note": "第28-26章"}]), {26, 27, 28})
        self.assertEqual(P._chapters_named_in([]), set())
        self.assertEqual(P._chapters_named_in([None, "x", {"note": "无章号"}]), set())

    def test_archived_preface_recovery(self):
        from app.core import pipeline as P
        adir = self.workdir / "已成稿"
        adir.mkdir()
        (adir / "卷一合集.md").write_text("第一章前文A\n\n第二章前文B", encoding="utf-8")
        (adir / "阅读目录.md").write_text("目录：不是正文", encoding="utf-8")
        # 前文章节文件全部不在场 → 归档找回；目录文件不算正文（断言用包含式，
        # 不依赖 read 侧换行归一）
        pref = P._archived_preface(self.workdir, 21)
        self.assertIn("第一章前文A", pref)
        self.assertIn("第二章前文B", pref)
        self.assertNotIn("目录", pref)
        # 任一前文章节在场 → 视为未归档，走正常路径
        P._write_chapter(self.workdir, 3, "还在场的第三章")
        self.assertEqual(P._archived_preface(self.workdir, 21), "")
        # 首批（start<=1）没有前文可找
        self.assertEqual(P._archived_preface(self.workdir, 1), "")

    def test_full_manuscript_batch_first_budget(self):
        from app.core import pipeline as P
        # 本批 21–22 在场；前文已归档
        P._write_chapter(self.workdir, 21, "新21" * 10)
        P._write_chapter(self.workdir, 22, "新22" * 10)
        adir = self.workdir / "已成稿"
        adir.mkdir()
        old = "旧" * 70000   # 必须长过默认 60k 预算，才能验到「尾部截取+标记」
        (adir / "合集.md").write_text(old, encoding="utf-8")
        text = P._full_manuscript(self.workdir, 21, 22)
        self.assertIn("新21", text)
        self.assertIn("新22", text)            # 本批永远完整在场
        self.assertIn("前文较长", text)         # 截断时有明确标记
        # 预算充足时前文尾部在场（靠近本批的最要紧）
        text2 = P._full_manuscript(self.workdir, 21, 22, cap=10000)
        self.assertIn("旧" * 50, text2)
        # 无归档无前文 → 只剩本批
        P._write_chapter(self.workdir, 1, "第一章")
        self.assertNotIn("旧" * 50,
                         P._full_manuscript(self.workdir, 21, 22, cap=10000))
        # 本批自身超 cap → 保底截断不炸
        only_batch = P._full_manuscript(self.workdir, 21, 22, cap=30)
        self.assertLessEqual(len(only_batch), 30)

    def test_dedup_issues(self):
        from app.core import pipeline as P
        a = {"chapter": "全书", "dim": "情节", "severity": "major", "note": "第26章脱轨" + "x" * 80}
        a2 = {"chapter": "全书", "dim": "情节", "severity": "major",
              "note": "第26章脱轨" + "x" * 80 + "（换个说法再提一遍）"}   # 前 60 字同 → 同一条
        b = {"chapter": "全书", "dim": "节奏", "severity": "major", "note": "第26章脱轨" + "x" * 80}
        c = {"chapter": 21, "dim": "情节", "severity": "major", "note": "第26章脱轨" + "x" * 80}
        out = P._dedup_issues([a, a2, b, c, None])
        self.assertEqual(len(out), 3)
        self.assertIs(out[0], a)               # 保留首条


class TestPolishNamedPriority(BaseTest):
    """直接驱动 _run_serial_review：全局低分 + 点名 major → 打磨必须先改点名章。

    实案病灶：21–28 章整体脱纲但章分全 8+，按分数选章永远选中「分低的好章」，
    真正的病灶章（全局点名）没人碰，全局分纹丝不动烧穿预算。
    """

    def _make(self, chapters=3):
        from app.core import store
        task = store.create_task({
            "type": "serial_novel", "title": "点名打磨回归", "goal": "写三章",
            "workdir": str(self.workdir),
            "serial": {"chapters": chapters, "words_per_chapter": 300},
            "implementer": "mock-a", "critics": ["c1", "c2"],
        })
        run = store.create_run("orchestration", task["title"], task_id=task["id"])
        store.update_run(run["id"], status="running")
        return task, run

    def _agents(self):
        critics = [{"id": cid, "mode": "real", "kind": "generic", "command": "x",
                    "label": cid} for cid in ("c1", "c2")]
        # impl 必须 real：mock 打磨直接写盘不经过 _run_step，假件收不到
        # polish-c 角色（同 test_serial_review_resilience 的打磨组基建）
        impl = {"id": "impl-r", "mode": "real", "kind": "generic",
                "command": "x", "label": "Impl R"}
        return critics, impl

    def _run_with(self, task, run, critics, impl, fake_step):
        from app.core import pipeline
        orig_step = pipeline._run_step
        orig_outline = pipeline.planner.make_serial_outline

        def fake_outline(t, author_agent=None, workdir=None, ev=None, log_path=None):
            n = int((t.get("serial") or {}).get("chapters") or 3)
            return {"book_title": "回归书", "source": "llm(fake)",
                    "chapters": [{"title": "第 %d 章" % (k + 1), "beats": "节拍%d" % (k + 1),
                                  "hook": "钩子"} for k in range(n)]}

        pipeline.planner.make_serial_outline = fake_outline
        pipeline._run_step = fake_step
        try:
            return pipeline._run_serial_review(
                run, task, critics, None, {}, "manual", critics, impl, {}, None, "default")
        finally:
            pipeline._run_step = orig_step
            pipeline.planner.make_serial_outline = orig_outline

    def test_named_chapter_preempts_score_pick_and_unlocks_mainline(self):
        from app.core import pipeline, store
        task, run = self._make()
        critics, impl = self._agents()
        polish_prompts = {}
        n_global = {"n": 0}

        def fake_step(run_id, role, agent, prompt, workdir, readonly, ev, *a, **kw):
            if role.startswith("draft-c"):
                pipeline._write_chapter(workdir, int(role.split("c")[-1]), "山" * 400)
                return _ok()
            if role.startswith("critique-c"):
                i = int(role.split("c")[-1])
                # 第 3 章分更低但都达标：按分数选章会先选它（实案里=永远选不中病灶章）
                return _ok(OK8 if i == 3 else GOOD)
            if role == "global-critique":
                n_global["n"] += 1
                # 第一轮（本批 2 名评审）：低分 + 点名第 1 章 → 触发打磨；
                # 之后的轮次：重评达标（打磨已修好）
                if n_global["n"] <= len(critics):
                    return _ok(_global_weak_named())
                return _ok(GOOD)
            if role.startswith("polish-c"):
                polish_prompts[role] = prompt
                pipeline._write_chapter(workdir, int(role.split("c")[-1]), "改" * 400)
                return _ok()
            return _ok()

        self._run_with(task, run, critics, impl, fake_step)

        # 打磨面=点名章（第 1 章）优先 + 分数补位（第 3 章）；未点名的第 2 章不进
        self.assertEqual(sorted(polish_prompts), ["polish-c1", "polish-c3"],
                         "点名章必须先改，而不是只按章分挑分最低的")
        # 点名章：放开禁改主线 + 评审点名原话进提示词 + 本章节拍（按大纲重写的依据）
        self.assertIn("可推翻现稿自创的情节线", polish_prompts["polish-c1"])
        self.assertIn(NAMED_ISSUE[:40], polish_prompts["polish-c1"])
        self.assertIn("节拍1", polish_prompts["polish-c1"])
        # 未点名章：照旧锁死主线（防打磨越权改剧情）
        self.assertIn("不得改动既有剧情主线的关键事实", polish_prompts["polish-c3"])
        self.assertNotIn("可推翻现稿自创的情节线", polish_prompts["polish-c3"])

        run = store.get_run(run["id"])
        self.assertEqual(run["status"], "done", run.get("error"))
        v = run["verdict"]
        # 重评达标后陈账必须换掉：第一轮的点名 major 不得再压着 publishable
        self.assertTrue(v["global_pass"], v.get("global_scores"))
        self.assertTrue(v["publishable"],
                        "重评已达标，旧轮 majors 必须撤下：major_issues=%s"
                        % v.get("major_issues"))

    def test_polish_rereview_dead_keeps_old_issues(self):
        """打磨后重评全挂 → 分数与 issues 都保留旧账（评审链挂≠书变差）。"""
        from app.core import pipeline, store
        task, run = self._make()
        critics, impl = self._agents()
        n_global = {"n": 0}

        def fake_step(run_id, role, agent, prompt, workdir, readonly, ev, *a, **kw):
            if role.startswith("draft-c"):
                pipeline._write_chapter(workdir, int(role.split("c")[-1]), "山" * 400)
                return _ok()
            if role.startswith("critique-c"):
                return _ok(GARBAGE if "打磨后" in prompt else GOOD)
            if role == "global-critique":
                n_global["n"] += 1
                return _ok(_global_weak_named() if n_global["n"] == 1 else GARBAGE)
            if role.startswith("polish-c"):
                pipeline._write_chapter(workdir, int(role.split("c")[-1]), "改" * 400)
                return _ok()
            return _ok()

        self._run_with(task, run, critics, impl, fake_step)

        run = store.get_run(run["id"])
        self.assertEqual(run["status"], "done", run.get("error"))
        v = run["verdict"]
        self.assertTrue(all(abs(x - 5.0) < 0.01 for x in v["global_scores"].values()))
        notes = [str(x.get("note") or "") for x in v.get("major_issues") or []]
        self.assertTrue(any(NAMED_ISSUE[:20] in n for n in notes),
                        "重评全挂时旧账必须保留：%s" % notes)


class TestAiderLongPromptRead(BaseTest):
    def test_aider_overlong_prompt_uses_read_flag(self):
        """aider 超长提示词 → --read 带文件入会话；不再让模型「用读文件工具」。

        实案：glm-5.3 收到读文件指令直接判提示注入拒答（十秒白卷），全局有效
        评审恒 1 名，MIN_REVIEWERS=2 门禁结构性不可能通过。
        """
        from app.core import runner as R
        agent = {"id": "aider-r", "kind": "aider", "mode": "real", "command": "aider"}
        long_prompt = "你是网文主编。请评审以下书稿：" + "很久很久以前。" * 3000
        argv, stdin_text, _, tmp = R._build_call(
            agent, "aider", "", True, None, long_prompt, workdir=str(self.workdir))
        try:
            self.assertIsNone(stdin_text)
            self.assertEqual(len(tmp), 1)
            self.assertIn("--read", argv)
            self.assertEqual(argv[argv.index("--read") + 1], tmp[0])
            # 指令位换成的说明不得再指挥模型「读文件」（会被判注入）
            msg = argv[argv.index("--message") + 1]
            self.assertIn("只读文件加入会话", msg)
            self.assertNotIn("读文件工具", msg)
            self.assertNotIn(long_prompt, argv)          # 原文不嵌 argv
            with open(tmp[0], encoding="utf-8") as f:
                self.assertEqual(f.read(), long_prompt)  # 全文无损落盘
        finally:
            for p in tmp:
                try:
                    os.remove(p)
                except OSError:
                    pass

    def test_aider_short_prompt_stays_inline(self):
        from app.core import runner as R
        agent = {"id": "aider-r", "kind": "aider", "mode": "real", "command": "aider"}
        argv, _, _, tmp = R._build_call(
            agent, "aider", "", True, None, "短提示词", workdir=str(self.workdir))
        self.assertEqual(argv[argv.index("--message") + 1], "短提示词")
        self.assertEqual(tmp, [])
        self.assertNotIn("--read", argv)

    def test_generic_overlong_prompt_keeps_read_tool_instruction(self):
        """generic 分支保持原语义（CLI 自带读文件工具）——只动 aider。"""
        from app.core import runner as R
        agent = {"id": "kimi-code", "kind": "generic", "mode": "real",
                 "command": "kimi", "argv_template": ["-p", "{prompt}"]}
        long_prompt = "评审以下书稿：" + "很久很久以前。" * 4000
        argv, _, _, tmp = R._build_call(
            agent, "generic", "", True, None, long_prompt, workdir=str(self.workdir))
        try:
            self.assertIn("已写入文件", argv[-1])
            self.assertNotIn("--read", argv)
        finally:
            for p in tmp:
                try:
                    os.remove(p)
                except OSError:
                    pass


class TestStoryTrackingCRLF(BaseTest):
    """章稿 CRLF 摘要错位回归（2026-10-03 续写批漂移闸误杀实案）。

    Windows 上 _write_chapter 文本模式落盘 CRLF → _read_chapter 保真读回带
    \r\n → commit_chapter 再用文本模式写 prose.md 二次翻译成 \r\r\n：磁盘稿
    与状态摘要永久错位，下一批 run 开场 check() 必拦「章节正文不一致」。
    修法：写侧归一 LF+字节落盘；查侧兼容新旧三种磁盘/摘要形态。
    """

    def test_commit_normalizes_crlf_and_pins_lf_bytes(self):
        from app.core import story_tracking as ST
        ST.init(str(self.workdir), "t-1", "书", premise="p")
        ST.commit_chapter(str(self.workdir), 1, "细纲一",
                          "# 第 1 章\r\n\r\n正文A\r\n")
        self.assertTrue(ST.check(str(self.workdir))["ok"])
        raw = (self.workdir / ".codebee" / "chapters" / "0001" / "prose.md").read_bytes()
        self.assertNotIn(b"\r", raw, "prose.md 必须钉死 LF，不得再出 CRCRLF")

    def test_legacy_crcrlf_disk_and_crlf_digest_still_match(self):
        from app.core import story_tracking as ST
        ST.init(str(self.workdir), "t-1", "书", premise="p")
        ST.commit_chapter(str(self.workdir), 1, "细纲一", "正文A")
        prose_path = self.workdir / ".codebee" / "chapters" / "0001" / "prose.md"
        state = ST.load(str(self.workdir))
        # 旧写形态：磁盘被文本模式二次翻译成 \r\r\n，状态摘要按 CRLF 原文存
        legacy_text = "# 第 1 章\r\n\r\n正文A"
        prose_path.write_bytes(legacy_text.replace("\n", "\r\n").encode("utf-8"))
        state["chapters"][0]["prose_digest"] = ST._digest(legacy_text.strip())
        ST._save(self.workdir, state)
        self.assertTrue(ST.check(str(self.workdir))["ok"],
                        "旧状态不得被判漂移（同源即认）")
        # 旧形态二：磁盘普通 CRLF（未二次翻译），摘要按 CRLF 原文存
        prose_path.write_bytes(legacy_text.replace("\n", "\r\n")
                               .replace("\r\r\n", "\r\n").encode("utf-8"))
        self.assertTrue(ST.check(str(self.workdir))["ok"])
        # 真被改过内容 → 必须照常拦截
        prose_path.write_bytes("被手工改过的正文\n".encode("utf-8"))
        self.assertFalse(ST.check(str(self.workdir))["ok"])


class TestInChapterRevisionFreshLedger(BaseTest):
    """章内修订回路换账：第 1 轮 majors → 修订 → 第 2 轮达标 → 旧账必须撤下。"""

    def test_revision_passed_drops_round1_majors(self):
        from app.core import pipeline, store
        task = store.create_task({
            "type": "serial_novel", "title": "章内换账回归", "goal": "写两章",
            "workdir": str(self.workdir),
            "serial": {"chapters": 2, "words_per_chapter": 300},
            "implementer": "impl-r", "critics": ["c1", "c2"],
        })
        run = store.create_run("orchestration", task["title"], task_id=task["id"])
        store.update_run(run["id"], status="running")
        critics = [{"id": cid, "mode": "real", "kind": "generic", "command": "x",
                    "label": cid} for cid in ("c1", "c2")]
        impl = {"id": "impl-r", "mode": "real", "kind": "generic",
                "command": "x", "label": "Impl R"}
        weak = json.dumps({"scores": {"情节": 6.0, "人物": 6.0, "文笔": 6.0,
                                      "节奏": 6.0, "吸引力": 6.0},
                           "issues": [{"dim": "情节", "severity": "major",
                                       "note": "第1章开篇信息密度偏低（第一轮意见）",
                                       "quote": "山山山山山山"}]}, ensure_ascii=False)

        def fake_step(run_id, role, agent, prompt, workdir, readonly, ev, *a, **kw):
            if role.startswith("draft-c"):
                pipeline._write_chapter(workdir, int(role.split("c")[-1]), "山" * 400)
                return _ok()
            if role.startswith("revise-c"):
                pipeline._write_chapter(workdir, int(role.split("c")[-1]), "改" * 400)
                return _ok()
            if role.startswith("critique-c"):
                return _ok(GOOD)
            if role == "global-critique":
                return _ok(GOOD)
            return _ok()

        # 章级评审按「同一章第 1 次=首轮低分、第 2 次=复评达标」区分
        seen = {}

        def fake_step2(run_id, role, agent, prompt, workdir, readonly, ev, *a, **kw):
            if role.startswith("critique-c"):
                # 同章每轮 2 名评审：前 2 次调用=首轮低分，之后=修订复评达标
                seen[role] = seen.get(role, 0) + 1
                return _ok(weak if seen[role] <= 2 else GOOD)
            return fake_step(run_id, role, agent, prompt, workdir, readonly, ev, *a, **kw)

        orig_step = pipeline._run_step
        orig_outline = pipeline.planner.make_serial_outline

        def fake_outline(t, author_agent=None, workdir=None, ev=None, log_path=None):
            n = int((t.get("serial") or {}).get("chapters") or 2)
            return {"book_title": "回归书", "source": "llm(fake)",
                    "chapters": [{"title": "第 %d 章" % (k + 1), "beats": "节拍",
                                  "hook": "钩子"} for k in range(n)]}

        pipeline.planner.make_serial_outline = fake_outline
        pipeline._run_step = fake_step2
        try:
            pipeline._run_serial_review(
                run, task, critics, None, {}, "manual", critics, impl, {}, None, "default")
        finally:
            pipeline._run_step = orig_step
            pipeline.planner.make_serial_outline = orig_outline

        run = store.get_run(run["id"])
        self.assertEqual(run["status"], "done", run.get("error"))
        v = run["verdict"]
        self.assertTrue(all(c["passed"] for c in v["chapter_scores"]), v["chapter_scores"])
        self.assertTrue(v["publishable"],
                        "第 2 轮已达标的章不得被第 1 轮旧 majors 压死：major_issues=%s"
                        % v.get("major_issues"))
        # 确实发生过修订（否则没测到换账路径）
        self.assertTrue(any(r >= 2 for c in v["chapter_scores"]
                            for r in [c.get("rounds", 1)]), v["chapter_scores"])
