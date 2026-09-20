# -*- coding: utf-8 -*-
"""知识库（Knowledge）测试：CRUD/指纹去重/草稿闸门/注入命中与预算/账龄标注。

知识库记「已知是这样」（领域事实/结论/方法），与教训库（记「别这么做」）分工；
自动提炼一律草稿态，人工转正后才参与注入——垃圾知识进提示词比没有知识更糟。
"""
from __future__ import annotations

from unittest import mock

from base import BaseTest


class TestKnowledgeStore(BaseTest):
    def runTest(self):
        from app.core import knowledge
        knowledge._FILE = self.data_dir / "knowledge.json"

        # 1) 手动新建（upsert 直带 approved）= 已确认
        it = knowledge.upsert_entry("novel", "番茄签约模式分连载/完本两种",
                                    "番茄建书时选择连载模式或完本模式，影响后续更新义务。",
                                    tags=["番茄", "签约"], status="approved")
        self.assertTrue(it["id"].startswith("kb-"))
        self.assertEqual(it["status"], "approved")
        self.assertRegex(it["as_of"], r"^\d{4}-\d{2}-\d{2}$")

        # 2) 指纹去重：同 scope 同标题（标点差异被归一）→ 合并而非新增
        it2 = knowledge.upsert_entry("novel", "番茄签约模式，分连载/完本两种！", "更新版正文",
                                     tags=["番茄"], status="draft")
        self.assertEqual(it2["id"], it["id"])
        self.assertEqual(it2["seen"], 2)
        # approved 正文不被 draft 覆盖，新内容进修订候选
        self.assertIn("连载模式或完本", it2["body"])
        self.assertEqual(len(it2["revisions"]), 1)
        # draft 旧条被 approved 新条覆盖
        d = knowledge.upsert_entry("novel", "草稿条目", "v1", status="draft")
        d2 = knowledge.upsert_entry("novel", "草稿条目", "v2 确认版", status="approved")
        self.assertEqual(d2["id"], d["id"])
        self.assertEqual(d2["body"], "v2 确认版")
        self.assertEqual(d2["status"], "approved")
        # 标题/正文为空拒绝
        self.assertIsNone(knowledge.upsert_entry("novel", "", "x"))
        self.assertIsNone(knowledge.upsert_entry("novel", "x", ""))

        # 3) list/view 聚合
        v = knowledge.view()
        self.assertEqual(v["total"], 2)
        self.assertEqual(v["drafts"], 0)
        self.assertIn("番茄", v["tags"])
        knowledge.upsert_entry("code", "纯草稿条目", "未确认", status="draft")
        self.assertEqual(knowledge.view()["drafts"], 1)

        # 4) entry_op：启停 / 转正 / 编辑 / 删除 / 非法
        kid = d["id"]
        knowledge.entry_op(kid, "disable")
        self.assertEqual([x for x in knowledge.list_entries() if x["id"] == kid][0]["enabled"], False)
        knowledge.entry_op(kid, "enable")
        self.assertIsNone(knowledge.entry_op(kid, "approve"))
        self.assertIsNone(knowledge.entry_op(kid, "edit", fields={"body": "改过", "tags": ["工程"]}))
        edited = [x for x in knowledge.list_entries() if x["id"] == kid][0]
        self.assertEqual(edited["body"], "改过")
        self.assertEqual(edited["tags"], ["工程"])
        # edit 不改状态（转正必须显式 approve，保持人工闸门）
        self.assertEqual(edited["status"], "approved")
        self.assertEqual(knowledge.entry_op("nope", "delete"), "知识条目不存在")
        self.assertTrue(str(knowledge.entry_op(kid, "bad-op")).startswith("未知操作"))
        self.assertIsNone(knowledge.entry_op(kid, "delete"))
        self.assertEqual(len(knowledge.list_entries()), 2)

        # 5) 提炼守卫：run 不存在 → 0（无编排者/无产出同样静默跳过，宁缺毋滥）
        self.assertEqual(knowledge.learn_from_run("no-such-run"), 0)

        # 6) 终态闸门：非 done 的运行不提炼（失败/取消产物是半成品，会污染知识库）
        from app.core import store
        task = store.create_task({"type": "novel", "goal": "写一本小说", "workdir": str(self.workdir)})
        run = store.create_run("orchestration", "测试运行", task_id=task["id"])
        store.update_run(run["id"], status="failed", error="模拟失败")
        self.assertEqual(knowledge.learn_from_run(run["id"]), 0)
        store.update_run(run["id"], status="cancelled")
        self.assertEqual(knowledge.learn_from_run(run["id"]), 0)


class TestKnowledgeMigrate(BaseTest):
    def runTest(self):
        from app.core import knowledge
        knowledge._FILE = self.data_dir / "knowledge.json"

        # 1) 草稿闸门退役：历史 draft 一次性转正（幂等）
        knowledge.upsert_entry("novel", "老草稿一", "内容一", status="draft")
        knowledge.upsert_entry("novel", "老草稿二", "内容二", status="draft")
        knowledge.upsert_entry("novel", "已是转正", "内容三", status="approved")
        n = knowledge.migrate_drafts_approved()
        self.assertEqual(n, 2)
        self.assertEqual(knowledge.view()["drafts"], 0)
        # 幂等：没有 draft 时零写入
        self.assertEqual(knowledge.migrate_drafts_approved(), 0)

        # 2) 空库幂等
        for x in knowledge.list_entries():
            knowledge.entry_op(x["id"], "delete")
        self.assertEqual(knowledge.migrate_drafts_approved(), 0)


class TestKnowledgeInject(BaseTest):
    def runTest(self):
        from app.core import knowledge
        knowledge._FILE = self.data_dir / "knowledge.json"

        # 1) 草稿不注入（人工闸门）
        a = knowledge.upsert_entry("novel", "番茄签约模式", "连载与完本两种，写前先确认。",
                                   tags=["番茄"], status="draft")
        self.assertEqual(knowledge.block_for({"type": "novel", "goal": "写一本番茄小说"}), "")

        # 2) 转正后 scope 命中才注入；命中即计数
        knowledge.entry_op(a["id"], "approve")
        block = knowledge.block_for({"type": "novel", "goal": "写一本番茄小说"})
        self.assertIn("知识库", block)
        self.assertIn("番茄签约模式", block)
        self.assertEqual([x for x in knowledge.list_entries() if x["id"] == a["id"]][0]["hits"], 1)

        # 3) scope 不命中不注入
        self.assertEqual(knowledge.block_for({"type": "code", "goal": "重构模块"}), "")

        # 4) 账龄标注：as_of 超过 STALE_DAYS → 「可能过期」
        old = knowledge.upsert_entry("novel", "旧平台规则", "该规则早已变化。",
                                     as_of="2000-01-01", status="approved")
        block2 = knowledge.block_for({"type": "novel", "goal": "写小说"})
        self.assertIn("可能过期", block2)
        self.assertIn("2000-01-01", block2)
        stale_map = {x["id"]: x["stale"] for x in knowledge.view()["entries"]}
        self.assertTrue(stale_map[old["id"]])
        self.assertFalse(stale_map[a["id"]])

        # 5) scope=* 通用条目 + 标签相关性命中
        knowledge.upsert_entry("*", "docker 构建缓存", "构建缓存挂载可加速 CI。",
                               tags=["docker"], status="approved")
        block3 = knowledge.block_for({"type": "code", "goal": "优化 docker 构建缓存"})
        self.assertIn("docker 构建缓存", block3)

        # 6) 独立预算截断（不挤占经验包/圣经的预算）
        old_budget = knowledge.KNOWLEDGE_BUDGET
        try:
            knowledge.KNOWLEDGE_BUDGET = 80
            b = knowledge.block_for({"type": "novel", "goal": "写小说"})
            self.assertTrue(b.endswith("…（已截断）"))
            self.assertLessEqual(len(b), 80 + len("\n…（已截断）"))
        finally:
            knowledge.KNOWLEDGE_BUDGET = old_budget

        # 7) 清空后恒为空串（默认不注入，字节稳定不碎前缀缓存）
        for x in knowledge.list_entries():
            knowledge.entry_op(x["id"], "delete")
        self.assertEqual(knowledge.block_for({"type": "novel", "goal": "写小说"}), "")


class TestKnowledgeLearningSplit(BaseTest):
    """产出里的事实进知识库，实践进经验库，旧模型缺 kind 仍按事实兼容。"""

    def runTest(self):
        from app.core import knowledge, modelhub, skills, store
        task = store.create_task({"type": "novel", "title": "沉淀分流",
                                  "goal": "总结写作调研", "workdir": str(self.workdir)})
        run = store.create_run("orchestration", task["title"], task_id=task["id"])
        step, _ = store.add_step(run["id"], "research", "codex", "Codex")
        store.finish_step(run["id"], step["n"], "done", summary="完成调研")
        store.update_run(run["id"], status="done")

        response = {"ok": True, "text": """```json
{"entries": [
  {"kind": "fact", "title": "缓存事实", "body": "供应商支持前缀缓存。", "as_of": "2026-09-21"},
  {"kind": "practice", "title": "章末钩子", "body": "每章结尾设置悬念以提升追读。", "category": "节奏爽点"},
  {"title": "兼容旧条目", "body": "旧模型未返回 kind 时仍保存为事实。"}
]}
```"""}
        with mock.patch.object(knowledge, "_pick_material", return_value="调研正文"), \
             mock.patch.object(modelhub, "resolve_orchestrator",
                               return_value=({"id": "p1"}, "m1")), \
             mock.patch.object(modelhub, "chat", return_value=response) as chat:
            self.assertEqual(knowledge.learn_from_run(run["id"]), 3)

        prompt = chat.call_args.args[2]
        self.assertIn("fact", prompt)
        self.assertIn("practice", prompt)
        self.assertIn("节奏爽点", prompt)
        self.assertEqual({x["title"] for x in knowledge.list_entries("novel")},
                         {"缓存事实", "兼容旧条目"})
        lessons = skills.list_lessons("novel")
        self.assertEqual([x["title"] for x in lessons], ["章末钩子"])
        self.assertEqual(lessons[0]["category"], "节奏爽点")


if __name__ == "__main__":
    import unittest as _u
    _u.main()
