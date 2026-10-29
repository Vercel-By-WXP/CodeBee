# -*- coding: utf-8 -*-
"""成稿台（dossier）单测。

锁死三条边界：

1. **没有正文 ≠ 要件缺失**。刚建任务时所有条目必须是 ``unknown``；把「还没写」
   判成「缺项」，作者一进来就看到满屏红叉，会以为稿子废了。
2. **缺项要说清是哪一项，且分硬/软**。标书里资格与商务要件缺一项即废标，
   合成一个「完整度 82%」没法指导补稿。
3. **导出只认正文**。没稿不导出空文件；正文已有 Markdown 标题时不补第二个标题。
"""
from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from core import agents, dossier


def _task(title="机房搬迁实施方案", type_="bid_doc", manuscript="bid.md",
          workdir=""):
    return {"id": "t-doc-1", "title": title, "type": type_,
            "status": "done", "manuscript": manuscript, "workdir": workdir}


class _Base(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.root = Path(self._tmp.name)

    def tearDown(self):
        self._tmp.cleanup()

    def write(self, text, name="bid.md"):
        p = self.root / name
        p.write_text(text, encoding="utf-8")
        return str(self.root)


class OutlineTest(unittest.TestCase):
    def test_markdown_headings(self):
        body = "# 一、总则\n正文一\n## 1.1 范围\n范围正文\n## 1.2 目标\n目标正文"
        nodes = dossier.outline_nodes(body)
        self.assertEqual([n["title"] for n in nodes], ["一、总则", "1.1 范围", "1.2 目标"])
        self.assertEqual([n["level"] for n in nodes], [1, 2, 2])
        self.assertTrue(all(n["words"] > 0 for n in nodes))

    def test_chinese_numbering(self):
        body = "一、投标函\n内容\n二、报价表\n内容"
        nodes = dossier.outline_nodes(body)
        self.assertEqual([n["title"] for n in nodes], ["投标函", "报价表"])
        self.assertEqual([n["level"] for n in nodes], [2, 2])

    def test_no_heading_is_single_body_node(self):
        nodes = dossier.outline_nodes("这是一段没有标题的正文。")
        self.assertEqual(len(nodes), 1)
        self.assertEqual(nodes[0]["title"], "正文")
        self.assertGreater(nodes[0]["words"], 0)

    def test_empty_body_has_no_nodes(self):
        self.assertEqual(dossier.outline_nodes(""), [])


class ManuscriptTest(_Base):
    def test_task_field_wins(self):
        self.assertEqual(dossier.manuscript_name(_task(manuscript="custom.md")),
                         "custom.md")

    def test_flow_fallback(self):
        # 任务上没写 manuscript 时取流程定义（bid_doc → bid.md）
        self.assertEqual(dossier.manuscript_name({"type": "bid_doc"}), "bid.md")

    def test_path_is_sanitized_to_basename(self):
        task = _task(manuscript="../../etc/passwd")
        self.assertEqual(dossier.manuscript_name(task), "passwd")

    def test_read_body_from_workdir(self):
        workdir = self.write("正文内容")
        task = _task(workdir=workdir)
        self.assertIn("正文内容", dossier.read_body(task))

    def test_read_body_missing_workdir(self):
        self.assertEqual(dossier.read_body(_task(workdir="")), "")


class ChecklistTest(unittest.TestCase):
    def test_no_body_all_unknown(self):
        items = dossier.check({"type": "bid_doc"}, body="")
        self.assertTrue(items)
        self.assertTrue(all(i["status"] == "unknown" for i in items))

    def test_present_and_missing(self):
        body = "投标函\n本公司具备营业执照与资质证书。"
        items = dossier.check({"type": "bid_doc"}, body=body)
        by_key = {i["key"]: i for i in items}
        self.assertEqual(by_key["bid_letter"]["status"], "present")
        self.assertEqual(by_key["license"]["status"], "present")
        self.assertEqual(by_key["bid_bond"]["status"], "missing")

    def test_hard_flag_is_carried(self):
        items = dossier.check({"type": "bid_doc"}, body="投标函")
        by_key = {i["key"]: i for i in items}
        self.assertTrue(by_key["bid_letter"]["hard"])
        self.assertFalse(by_key["business_deviation"]["hard"])

    def test_unknown_flow_uses_generic_table(self):
        items = dossier.check({"type": "totally_new_doc_flow"}, body="背景与目的")
        self.assertTrue(items)
        self.assertTrue(any(i["key"] == "context" for i in items))

    def test_keywords_are_specific(self):
        # 「函」不应该把「公函」当成投标函（keywords 用的是具体词）
        items = dossier.check({"type": "bid_doc"}, body="这是一份公函。")
        by_key = {i["key"]: i for i in items}
        self.assertEqual(by_key["bid_letter"]["status"], "missing")


class BuildTest(_Base):
    def test_empty_build_state(self):
        doc = dossier.build(_task(workdir=""))
        self.assertFalse(doc["has_body"])
        self.assertEqual(doc["summary"]["state"], "empty")
        self.assertEqual(doc["outline"], [])
        self.assertFalse(doc["export"]["available"])

    def test_blocked_when_hard_missing(self):
        workdir = self.write("投标函\n本公司提交投标文件。")
        doc = dossier.build(_task(workdir=workdir))
        self.assertTrue(doc["has_body"])
        self.assertEqual(doc["summary"]["state"], "blocked")
        self.assertGreater(doc["summary"]["hard_missing"], 0)
        self.assertIn("bid_bond", doc["summary"]["missing_hard_keys"])

    def test_ok_when_all_items_present(self):
        # 把所有要件关键词拼进正文 → 18 项全中
        words = []
        for row in dossier.requirements("bid_doc"):
            words.extend(row["keywords"])
        workdir = self.write("\n".join(words))
        doc = dossier.build(_task(workdir=workdir))
        self.assertEqual(doc["summary"]["state"], "ok")
        self.assertEqual(doc["summary"]["missing"], 0)

    def test_warn_when_only_soft_missing(self):
        # doc 没有硬性项；只写背景/目的，其余为建议补充
        workdir = self.write("背景与目的：说明机房搬迁。", "document.md")
        doc = dossier.build(_task(type_="doc", manuscript="document.md",
                                  workdir=workdir))
        self.assertEqual(doc["summary"]["state"], "warn")
        self.assertEqual(doc["summary"]["hard_missing"], 0)

    def test_flow_name_is_resolved(self):
        doc = dossier.build(_task(workdir=""))
        self.assertEqual(doc["task"]["flow_name"], "标书编制")


class ExportTest(_Base):
    def test_no_body_exports_nothing(self):
        self.assertEqual(dossier.export_text(_task(workdir="")), "")

    def test_prepends_title_when_body_has_no_heading(self):
        workdir = self.write("正文没有标题。")
        text = dossier.export_text(_task(title="搬迁方案", workdir=workdir))
        self.assertTrue(text.startswith("# 搬迁方案"))
        self.assertIn("正文没有标题", text)

    def test_does_not_duplicate_existing_heading(self):
        workdir = self.write("# 已有标题\n\n正文")
        text = dossier.export_text(_task(title="另一个标题", workdir=workdir))
        self.assertTrue(text.lstrip().startswith("# 已有标题"))
        self.assertNotIn("# 另一个标题", text)

    def test_filename_sanitizes_illegal_chars(self):
        name = dossier.filename({"title": 'a/b:c*d?"e<f>g'})
        self.assertNotIn("/", name)
        self.assertNotIn(":", name)
        self.assertTrue(name.endswith(".md"))


class SummarizeAndTableTest(_Base):
    def test_summarize_projects_fields(self):
        workdir = self.write("正文")
        row = dossier.summarize(_task(workdir=workdir))
        for key in ("id", "title", "type", "flow_name", "status", "words", "known"):
            self.assertIn(key, row)

    def test_table_covers_all_flows(self):
        rows = dossier.table()
        flows = {r["flow"] for r in rows}
        self.assertEqual(flows, set(dossier.DOSSIER_FLOWS))
        bid = next(r for r in rows if r["flow"] == "bid_doc")
        self.assertGreater(bid["hard"], 0)


class ArchetypeWiringTest(unittest.TestCase):
    def test_dossier_page_is_declared(self):
        # v2 的 dossier 独立页已删除：原型不再声明默认页，统一落到工作台
        self.assertEqual(agents.ARCHETYPES["dossier"]["default_page"], "")

    def test_dossier_agents_resolve_to_page(self):
        # 文档类 Agent 没有独立页面，surface 为空即「走统一工作台」
        for flow in ("doc", "bid_doc", "tech_proposal"):
            spec = agents.get(flow)
            self.assertEqual(spec["surface"], "", flow)


if __name__ == "__main__":
    unittest.main()
