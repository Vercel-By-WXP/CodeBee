# -*- coding: utf-8 -*-
"""外部 Skill 的相关性判定：错配一次就要多塞几千字无关指令。

2026-10-23 实案：一个 ``code`` 类型的重构任务被注入了 `awesome-novel`
（8KB 小说写作流），因为任务字段 ``type`` 被当成关键词，而 ``code`` 正好命中
codex 技能 id 的 ``codex-skill:`` 命名空间前缀。
"""
import unittest

from app.core import capability_runtime


def _row(rid="codex-skill:awesome-novel", name="awesome-novel", desc="",
         **extra):
    row = {
        "id": rid,
        "name": name,
        "display_name": name,
        "description": desc or (
            "和 AI 协作写小说的工作流系统。9 个 agent 协作完成从设定到归档的"
            "完整写作流程。适用场景：从零写新小说、导入已有小说。"),
        "source_type": "codex",
        "enabled": True,
    }
    row.update(extra)
    return row


class RowHayTests(unittest.TestCase):
    def test_source_namespace_is_not_a_keyword(self):
        """``codex-skill:`` 是出处标记，不是主题词。"""
        hay = capability_runtime._row_hay(_row())
        self.assertNotIn("codex-skill:", hay)
        self.assertNotIn("codex", hay)

    def test_topic_text_excludes_the_routing_type(self):
        """``type`` 是路由判别字段，不参与主题匹配。"""
        text = capability_runtime._task_text(
            {"type": "code", "title": "重构", "goal": "拆掉这个模块"})
        self.assertNotIn("code", text)
        self.assertIn("重构", text)


class RelevanceTests(unittest.TestCase):
    def test_code_task_does_not_match_a_novel_skill(self):
        row = _row()
        task = {"type": "code", "title": "重构",
                "goal": "重构这个模块，抽出公共逻辑"}
        self.assertFalse(
            capability_runtime._relevant(row,
                                         capability_runtime._task_text(task)))

    def test_doc_task_does_not_match_a_novel_skill(self):
        row = _row()
        task = {"type": "doc", "title": "部署说明",
                "goal": "写一份说明文档，介绍部署步骤"}
        self.assertFalse(
            capability_runtime._relevant(row,
                                         capability_runtime._task_text(task)))

    def test_novel_task_still_matches(self):
        row = _row()
        task = {"type": "novel", "title": "都市长篇", "goal": "写一部都市小说"}
        self.assertTrue(
            capability_runtime._relevant(row,
                                         capability_runtime._task_text(task)))

    def test_other_source_prefixes_are_stripped_too(self):
        """codebee / agents / system 前缀同样是出处，不能当关键词。"""
        for rid in ("codebee-skill:fanqie-novel", "agents-skill:some-pack",
                    "system-skill:builtin"):
            row = _row(rid=rid, name="pack", desc="一份普通说明")
            hay = capability_runtime._row_hay(row)
            self.assertNotIn("-skill:", hay, rid)

    def test_empty_task_text_never_matches(self):
        self.assertFalse(capability_runtime._relevant(_row(), ""))


if __name__ == "__main__":
    unittest.main()
