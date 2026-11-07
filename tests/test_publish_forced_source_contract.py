# -*- coding: utf-8 -*-
"""强制放行的「谁推开的」写入契约棘轮（unittest 风格，闸门必收）。

背景（11-07 复核）：ⓑ「新数据强制写 quality_forced_source」在代码层**已经
落地** —— ``manager._forced_source`` 对任何 forced 都返回非空三值，三处
``ledger.record`` 调用都传了 ``quality_forced_source``，``ledger.py`` 写键，
部署包两个模块与仓库逐字一致。生产 424 条缺 source 全是 2026-10 及更早的
老行（字段 11-05 才加），11 月只有 4 条 connect 且 forced=False，**没有反例**。

所以这里不再重复实现，只把「不会被将来改坏」钉死：
1. 写入侧对 forced 永远返回 manual / auto / unknown 之一，从不返回空串；
2. 读写两侧的三值枚举对齐（写侧新增值而读侧不认 = 静默假绿）；
3. **AST 棘轮**：全仓凡给 ``ledger.record`` 传 ``quality_forced`` 的调用点，
   必须同时传 ``quality_forced_source``；新增调用点漏一个立刻红；
4. 落盘语义：非 forced 不写键，forced + 合法来源才写键（兼容老调用方）。
"""
from __future__ import annotations

import ast
import json
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
APP = ROOT / "app"
if str(APP) not in sys.path:
    sys.path.insert(0, str(APP))

from core import paths  # noqa: E402
from core.publish import (journey_sources, ledger, manager)  # noqa: E402


FORCED_SOURCES = frozenset(("manual", "auto", "unknown"))


class ForcedSourceContractTest(unittest.TestCase):
    def test_writer_never_returns_empty_for_forced(self):
        cases = [
            (True, "", ""),
            (True, "赶周末流量，先发再说", ""),
            (True, "用户已启用全自动发布；系统按平台校准流程自动提交", ""),
            (True, "赶周末流量", "manual"),     # 显式声明优先于理由文本
            (True, "赶周末流量", "auto"),
            (True, "", "auto"),
            (True, "", "nonsense"),             # 非法 origin 回落文本判定
        ]
        for force, reason, origin in cases:
            got = manager._forced_source(force, reason, origin)
            with self.subTest(force=force, reason=reason[:12], origin=origin):
                self.assertIn(got, FORCED_SOURCES)
        self.assertEqual("", manager._forced_source(False, "任何理由", "manual"))

    def test_writer_and_reader_enums_agree(self):
        self.assertEqual("manual", journey_sources.FORCED_MANUAL)
        self.assertEqual("auto", journey_sources.FORCED_AUTO)
        self.assertEqual(
            FORCED_SOURCES,
            frozenset((journey_sources.FORCED_MANUAL,
                       journey_sources.FORCED_AUTO, "unknown")))
        self.assertTrue(manager.AUTO_FORCE_REASON_MARKS)
        self.assertIn("用户已启用全自动发布", manager.AUTO_FORCE_REASON_MARKS)

    def test_every_forced_record_call_passes_source(self):
        """新增写入路径若只传 quality_forced 不传来源，这里立刻红。"""
        found, violations = [], []
        for path in sorted(APP.rglob("*.py")):
            try:
                tree = ast.parse(path.read_text(encoding="utf-8"))
            except (OSError, SyntaxError, UnicodeDecodeError):
                continue
            for node in ast.walk(tree):
                if not isinstance(node, ast.Call):
                    continue
                func = node.func
                name = (func.attr if isinstance(func, ast.Attribute)
                        else func.id if isinstance(func, ast.Name) else "")
                if name != "record":
                    continue
                keys = set(k.arg for k in node.keywords)
                if "quality_forced" not in keys:
                    continue
                found.append("%s:%d" % (path.relative_to(ROOT).as_posix(),
                                        node.lineno))
                if "quality_forced_source" not in keys:
                    violations.append(found[-1])
        # 空集假绿防护：扫描逻辑坏了（或三个调用点被整体删掉）也必须红。
        self.assertGreaterEqual(len(found), 3, "写入侧调用点数量异常，检查扫描逻辑")
        self.assertEqual([], violations,
                         "这些调用点传了 quality_forced 却没传 quality_forced_source")

    def test_ledger_row_semantics(self):
        with tempfile.TemporaryDirectory() as td:
            saved = paths.PUBLISH_DIR
            paths.PUBLISH_DIR = Path(td)
            try:
                ledger.record("fanqie", "upload_chapter", task_id="t-1",
                              ok=True, quality_forced=True,
                              quality_forced_source="manual")
                ledger.record("fanqie", "upload_chapter", task_id="t-2",
                              ok=True, quality_forced=True,
                              quality_forced_source="auto")
                ledger.record("fanqie", "upload_chapter", task_id="t-3",
                              ok=True)                     # 非 forced
                ledger.record("fanqie", "upload_chapter", task_id="t-4",
                              ok=True, quality_forced=True)  # 老调用方
                rows = []
                for fp in sorted(Path(td).glob("publish-*.jsonl")):
                    for line in fp.read_text(encoding="utf-8").splitlines():
                        if line.strip():
                            rows.append(json.loads(line))
            finally:
                paths.PUBLISH_DIR = saved
        self.assertEqual(4, len(rows))
        self.assertEqual("manual", rows[0]["quality_forced_source"])
        self.assertEqual("auto", rows[1]["quality_forced_source"])
        self.assertNotIn("quality_forced_source", rows[2])
        self.assertNotIn("quality_forced_source", rows[3])  # 老行保持干净
        self.assertFalse(rows[2]["quality_forced"])

    def test_old_row_without_source_stays_fail_closed_in_reader(self):
        """老行不可判定：既不算人工救火，也不算「没救火」。"""
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            (root / "tasks").mkdir()
            (root / "publish").mkdir()
            (root / "tasks" / "t-old.json").write_text(json.dumps({
                "id": "t-old", "type": "serial_novel", "status": "done",
                "created_at": "2026-10-18 10:00:00"}, ensure_ascii=False),
                encoding="utf-8")
            (root / "publish" / "outcomes.json").write_text(json.dumps({
                "t-old": {"qimao": {"stage": "signed"}}}, ensure_ascii=False),
                encoding="utf-8")
            (root / "publish" / "publish-202610.jsonl").write_text(
                json.dumps({"task_id": "t-old", "action": "upload_chapter",
                            "ok": True, "quality_forced": True},
                           ensure_ascii=False) + "\n", encoding="utf-8")
            d = journey_sources.derive_events(str(root))
            kinds = [e.get("kind") for e in d["events"]
                     if e.get("task_id") == "t-old"]
            self.assertNotIn("force_publish", kinds)
            self.assertIn(journey_sources.UNOBSERVABLE_KIND, kinds)
            self.assertEqual(1, d["coverage"]["sources"]
                             ["publish_ledger"]["forced_unknown_source"])


if __name__ == "__main__":
    unittest.main()
