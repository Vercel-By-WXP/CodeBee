# -*- coding: utf-8 -*-
"""用户自建经验包（3A 多源分层）+ Persona（3B）测试。
设计稿：docs/migration/04-skills-seam.md §3A/§3B。
"""
from __future__ import annotations

import time
from pathlib import Path
from base import BaseTest


def _write_user_pack(self, filename, content):
    """往测试临时 data 目录的 skillpacks/ 写用户包。"""
    d = self.data_dir / "skillpacks"
    d.mkdir(parents=True, exist_ok=True)
    p = d / filename
    p.write_text(content, encoding="utf-8")
    # 目录 mtime 粒度较粗：强刷清单缓存
    from app.core import skills
    with skills._LOCK:
        skills._user_dir_mtime["ts"] = 0.0
        skills._user_dir_mtime["ids"] = None
    return p


class TestParseFrontmatter(BaseTest):

    def test_no_frontmatter(self):
        from app.core.skills import _parse_frontmatter
        meta, body = _parse_frontmatter("# 标题\n正文")
        self.assertEqual(meta, {})
        self.assertEqual(body, "# 标题\n正文")

    def test_basic_fields(self):
        from app.core.skills import _parse_frontmatter
        meta, body = _parse_frontmatter(
            "---\nname: 我的包\nnote: 说明\n---\n正文内容")
        self.assertEqual(meta["name"], "我的包")
        self.assertEqual(meta["note"], "说明")
        self.assertEqual(body, "正文内容")

    def test_list_scopes(self):
        from app.core.skills import _parse_frontmatter
        meta, _ = _parse_frontmatter(
            "---\nname: x\nscopes:\n  - novel\n  - serial_novel\n---\nbody")
        self.assertEqual(meta["scopes"], ["novel", "serial_novel"])

    def test_persona_multiline_quoted(self):
        from app.core.skills import _parse_frontmatter
        meta, _ = _parse_frontmatter(
            '---\nname: x\npersona: "你是严格的评审员"\n---\nbody')
        self.assertEqual(meta["persona"], "你是严格的评审员")


class TestUserPacks(BaseTest):

    def setUp(self):
        super().setUp()
        from app.core import skills
        skills.init_for_test() if hasattr(skills, "init_for_test") else None
        with skills._LOCK:
            skills._user_pack_cache.clear()
            skills._user_dir_mtime["ts"] = 0.0
            skills._user_dir_mtime["ids"] = None

    def test_user_pack_discovered(self):
        from app.core import skills
        _write_user_pack(self, "my-domain.md",
                         "---\nname: 领域规范\nscopes:\n  - doc\n---\n# 规范正文")
        packs = skills.user_packs()
        self.assertEqual(len(packs), 1)
        self.assertEqual(packs[0]["name"], "领域规范")
        self.assertEqual(packs[0]["scopes"], ["doc"])
        self.assertTrue(packs[0]["user"])
        self.assertTrue(packs[0]["id"].startswith("user-"))

    def test_all_packs_merges_builtin_and_user(self):
        from app.core import skills
        _write_user_pack(self, "extra.md", "无 frontmatter 的纯正文")
        ids = [p["id"] for p in skills.all_packs()]
        self.assertIn("qimao-signing", ids)  # 内置
        self.assertTrue(any(i.startswith("user-") for i in ids))  # 用户

    def test_stable_id_from_stem(self):
        from app.core import skills
        p1 = _write_user_pack(self, "same-name.md", "---\nname: A\n---\nv1")
        pack1 = skills.user_packs()[0]
        # 改文件内容（同名）→ id 不变
        time.sleep(0.02)
        p1.write_text("---\nname: A\n---\nv2", encoding="utf-8")
        with skills._LOCK:
            skills._user_pack_cache.clear()
        pack2 = skills.user_packs()[0]
        self.assertEqual(pack1["id"], pack2["id"])
        self.assertEqual(pack2["body"].strip(), "v2")

    def test_block_for_includes_user_pack(self):
        from app.core import skills
        _write_user_pack(self, "doc-std.md",
                         "---\nname: 文档规范\nscopes:\n  - doc\n---\n必须分三段")
        text, used = skills.block_for({"type": "doc"})
        self.assertIn("文档规范", text)
        self.assertIn("必须分三段", text)
        self.assertTrue(any(u.startswith("user-") for u in used))

    def test_scope_mismatch_excluded(self):
        from app.core import skills
        _write_user_pack(self, "novel-only.md",
                         "---\nname: 只给小说\nscopes:\n  - novel\n---\n正文")
        text, used = skills.block_for({"type": "doc"})
        self.assertNotIn("只给小说", text)

    def test_star_scope_matches_all(self):
        from app.core import skills
        _write_user_pack(self, "universal.md",
                         "---\nname: 全域包\nscopes:\n  - \"*\"\n---\n通用规则")
        text, used = skills.block_for({"type": "anything"})
        self.assertIn("通用规则", text)

    def test_pack_op_disable_user_pack(self):
        from app.core import skills
        _write_user_pack(self, "toggle.md",
                         "---\nname: 可停用\nscopes:\n  - doc\n---\n内容")
        pack = skills.user_packs()[0]
        self.assertIsNone(skills.pack_op(pack["id"], "disable"))
        text, used = skills.block_for({"type": "doc"})
        self.assertNotIn("可停用", text)
        self.assertIsNone(skills.pack_op(pack["id"], "enable"))
        text, _ = skills.block_for({"type": "doc"})
        self.assertIn("可停用", text)

    def test_pack_op_unknown_rejected(self):
        from app.core import skills
        err = skills.pack_op("user-nonexistent", "disable")
        self.assertEqual(err, "经验包不存在")


class TestMtimeHotReload(BaseTest):

    def test_modified_file_reflected(self):
        from app.core import skills
        p = _write_user_pack(self, "hot.md", "---\nname: 热更\n---\n版本一")
        text, _ = skills.block_for({"type": "*"})
        self.assertIn("版本一", text)
        # 改内容 + 强制 mtime 变化
        time.sleep(0.05)
        p.write_text("---\nname: 热更\n---\n版本二", encoding="utf-8")
        os_utime = p.with_name("hot.md")
        import os
        st = os_utime.stat()
        os.utime(os_utime, (st.st_atime + 2, st.st_mtime + 2))
        text, _ = skills.block_for({"type": "*"})
        self.assertIn("版本二", text)
        self.assertNotIn("版本一", text)

    def test_new_file_picked_up(self):
        from app.core import skills
        text, _ = skills.block_for({"type": "*"})
        self.assertNotIn("后来加的", text)
        _write_user_pack(self, "later.md", "后来加的包")
        text, _ = skills.block_for({"type": "*"})
        self.assertIn("后来加的包", text)


class TestPersona(BaseTest):

    def test_persona_block_injected_separately(self):
        from app.core import skills
        _write_user_pack(self, "persona.md",
                         "---\nname: 严格评审\n"
                         "scopes:\n  - doc\n"
                         "persona: \"你是一位一丝不苟的技术文档评审员\"\n"
                         "---\n评审时必须检查一致性")
        text, used = skills.block_for({"type": "doc"})
        self.assertIn("【角色设定：严格评审】", text)
        self.assertIn("你是一位一丝不苟的技术文档评审员", text)
        self.assertIn("【严格评审】", text)  # 正文块
        self.assertIn("评审时必须检查一致性", text)
        # 角色设定在正文之前
        self.assertLess(text.index("角色设定"), text.index("评审时必须检查"))

    def test_no_persona_no_block(self):
        from app.core import skills
        _write_user_pack(self, "plain.md", "---\nname: 无角色\n---\n只有正文")
        text, _ = skills.block_for({"type": "*"})
        self.assertNotIn("角色设定", text)

    def test_list_packs_exposes_persona_flag(self):
        from app.core import skills
        _write_user_pack(self, "with-p.md",
                         "---\nname: 有角色\npersona: \"角色\"\n---\n正文")
        _write_user_pack(self, "no-p.md", "无 frontmatter")
        listing = {p["name"]: p for p in skills.list_packs()}
        self.assertTrue(listing["有角色"]["persona"])
        self.assertFalse(listing["no-p"]["persona"])  # 无 frontmatter → 名回落为文件名