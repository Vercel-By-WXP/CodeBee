# -*- coding: utf-8 -*-
"""平台规则种子（seed_platform_rules）测试：播种/幂等/让位/墓碑/停用/注入。

种子语义红线：用户手改过的条目永不回写；用户删掉的不复活；用户先建的
同题/近似题条目不并压——官方内容更新只刷新「未被人工动过」的条目。
"""
from __future__ import annotations

from base import BaseTest


class TestPlatformRulesSeed(BaseTest):
    def runTest(self):
        from app.core import knowledge, platform_rules
        knowledge._FILE = self.data_dir / "knowledge.json"

        # 1) 首次播种：SEEDS 全量入库，approved + 官方来源 + 抓取日账龄
        n = knowledge.seed_platform_rules()
        self.assertEqual(n, len(platform_rules.SEEDS))
        entries = {x["title"]: x for x in knowledge.list_entries(platform_rules.SCOPE)}
        self.assertEqual(len(entries), len(platform_rules.SEEDS))
        for seed in platform_rules.SEEDS:
            it = entries[seed["title"]]
            self.assertEqual(it["status"], "approved")
            self.assertEqual(it["source"], platform_rules.SOURCE)
            self.assertEqual(it["as_of"], platform_rules.AS_OF)
            self.assertEqual(it["confidence"], "high")
            self.assertIn("番茄", it["tags"])

        # 2) 注入：scope 命中连载任务，规则正文进知识块
        block = knowledge.block_for({"type": "serial_novel",
                                     "goal": "番茄连载冲刺推荐验证期"})
        self.assertIn("知识库", block)
        self.assertIn("连续 3 日", block)
        # scope 不命中（其他任务类型）不注入
        self.assertEqual(
            knowledge.block_for({"type": "code", "goal": "重构模块"}), "")

        # 3) 幂等：重复播种零写入、不见 seen 膨胀、不产生重复条目
        before = {x["title"]: (x["seen"], x["updated_at"])
                  for x in knowledge.list_entries(platform_rules.SCOPE)}
        self.assertEqual(knowledge.seed_platform_rules(), 0)
        self.assertEqual(len(knowledge.list_entries(platform_rules.SCOPE)),
                         len(platform_rules.SEEDS))
        after = {x["title"]: (x["seen"], x["updated_at"])
                 for x in knowledge.list_entries(platform_rules.SCOPE)}
        self.assertEqual(before, after)


class TestPlatformRulesUserCreatedExact(BaseTest):
    """用户先建了精确同题条目：种子视为用户已拥有，不覆盖不重复。"""

    def runTest(self):
        from app.core import knowledge, platform_rules
        knowledge._FILE = self.data_dir / "knowledge.json"
        seed1 = platform_rules.SEEDS[1]

        mine = knowledge.upsert_entry(platform_rules.SCOPE, seed1["title"],
                                      "我先写的版本", status="approved")
        n = knowledge.seed_platform_rules()
        self.assertEqual(n, len(platform_rules.SEEDS) - 1)   # 只跳过同题那条
        cur = [x for x in knowledge.list_entries(platform_rules.SCOPE)
               if x["id"] == mine["id"]][0]
        self.assertEqual(cur["body"], "我先写的版本")
        self.assertNotEqual(cur["source"], platform_rules.SOURCE)
        self.assertEqual(len(knowledge.list_entries(platform_rules.SCOPE)),
                         len(platform_rules.SEEDS))          # 没有另立重复条目


class TestPlatformRulesUserCreatedNear(BaseTest):
    """用户先建了近似题条目（标题包含度 ≥0.8）：同样跳过，不并压。"""

    def runTest(self):
        from app.core import knowledge, platform_rules
        knowledge._FILE = self.data_dir / "knowledge.json"
        seed0 = platform_rules.SEEDS[0]

        near_title = seed0["title"].replace("与合同红线", "")
        mine = knowledge.upsert_entry(platform_rules.SCOPE, near_title,
                                      "近似题的用户笔记", status="approved")
        knowledge.seed_platform_rules()
        entries = knowledge.list_entries(platform_rules.SCOPE)
        titles = {x["title"] for x in entries}
        self.assertNotIn(seed0["title"], titles)   # 没有另立重复条目
        self.assertIn(near_title, titles)
        cur = [x for x in entries if x["id"] == mine["id"]][0]
        self.assertEqual(cur["body"], "近似题的用户笔记")
        self.assertEqual(len(entries), len(platform_rules.SEEDS))


class TestPlatformRulesUserEditWins(BaseTest):
    """用户手改/删除/停用/先建，种子都必须让位。"""

    def runTest(self):
        from app.core import knowledge, platform_rules
        knowledge._FILE = self.data_dir / "knowledge.json"
        knowledge.seed_platform_rules()
        target = platform_rules.SEEDS[0]["title"]

        # 1) 用户手改正文 → 永久让位（官方内容变了也不回写）
        kid = [x for x in knowledge.list_entries(platform_rules.SCOPE)
               if x["title"] == target][0]["id"]
        knowledge.entry_op(kid, "edit", fields={"body": "我自己的注解版本"})
        seed = dict(platform_rules.SEEDS[0])
        seed["body"] = seed["body"] + "（官方内容更新补充）"
        old = platform_rules.SEEDS[0]
        platform_rules.SEEDS[0] = seed
        try:
            self.assertEqual(knowledge.seed_platform_rules(), 0)
        finally:
            platform_rules.SEEDS[0] = old
        cur = [x for x in knowledge.list_entries(platform_rules.SCOPE)
               if x["id"] == kid][0]
        self.assertEqual(cur["body"], "我自己的注解版本")

        # 2) 用户停用 → 保留停用；启用与否不被动
        knowledge.entry_op(kid, "disable")
        self.assertEqual(knowledge.seed_platform_rules(), 0)
        cur = [x for x in knowledge.list_entries(platform_rules.SCOPE)
               if x["id"] == kid][0]
        self.assertFalse(cur["enabled"])

        # 3) 用户删除 → 墓碑生效，重启不复活
        knowledge.entry_op(kid, "delete")
        self.assertEqual(knowledge.seed_platform_rules(), 0)
        self.assertNotIn(target,
                         {x["title"] for x in
                          knowledge.list_entries(platform_rules.SCOPE)})

        # 4) 删除后已由墓碑兜住（见上一段）；停用/删除路径不再重复断言


class TestPlatformRulesOfficialRefresh(BaseTest):
    """官方内容更新：只刷新未被人工动过的条目，旧文进修订历史。"""

    def runTest(self):
        from app.core import knowledge, platform_rules
        knowledge._FILE = self.data_dir / "knowledge.json"
        knowledge.seed_platform_rules()
        target = platform_rules.SEEDS[0]["title"]
        kid = [x for x in knowledge.list_entries(platform_rules.SCOPE)
               if x["title"] == target][0]["id"]

        seed = dict(platform_rules.SEEDS[0])
        seed["body"] = seed["body"] + "（2027 年新规补充）"
        old = platform_rules.SEEDS[0]
        platform_rules.SEEDS[0] = seed
        try:
            n = knowledge.seed_platform_rules()
        finally:
            platform_rules.SEEDS[0] = old
        self.assertEqual(n, 1)
        cur = [x for x in knowledge.list_entries(platform_rules.SCOPE)
               if x["id"] == kid][0]
        self.assertIn("2027 年新规补充", cur["body"])
        self.assertEqual(len(cur["revisions"]), 1)   # 旧文留痕可回溯
        # 再跑一次：新正文已对上账，回到零写入
        platform_rules.SEEDS[0] = seed
        try:
            self.assertEqual(knowledge.seed_platform_rules(), 0)
        finally:
            platform_rules.SEEDS[0] = old


class TestPlatformRulesMigrateHook(BaseTest):
    """启动收口：migrate_drafts_approved 顺带播种（幂等），返回语义不变。"""

    def runTest(self):
        from app.core import knowledge
        knowledge._FILE = self.data_dir / "knowledge.json"

        # 有历史草稿：返回转正条数（不含种子）
        knowledge.upsert_entry("novel", "老草稿", "内容", status="draft")
        self.assertEqual(knowledge.migrate_drafts_approved(), 1)
        # 草稿转正不受种子影响；种子已随迁移入库
        self.assertEqual(knowledge.view()["drafts"], 0)
        self.assertEqual(knowledge.migrate_drafts_approved(), 0)
        # 空库/满库重复迁移都零写入
        self.assertEqual(knowledge.migrate_drafts_approved(), 0)


if __name__ == "__main__":
    import unittest as _u
    _u.main()
