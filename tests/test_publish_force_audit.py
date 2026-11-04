# -*- coding: utf-8 -*-
"""强制放行 → 平台驳回 的因果账（P0-A）单测。

要守的不是「存下来了」，而是四条边界：

1. **因果不能编**：没有强制放行就不许出现 `force_audit`，更不许写空壳
   （`{}` / `blockers: []`）——台账里真假混在一起，比没有台账更贵。
2. **失败不算**：强推但发失败了，平台不可能因为它驳回；算进来就是栽赃。
3. **不跨平台串味**：七猫的强推不能拿来解释番茄的驳回（同 `outcome` 既有纪律）。
4. **只认被判**：过审/签约成功没有要复盘的东西，不该冒出因果账。
"""
from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from core import paths
from core.publish import ledger, outcome


def _task(tid="t-force", platform="fanqie", type_="serial_novel"):
    return {
        "id": tid,
        "type": type_,
        "title": "一头猎物，三笔账",
        "platform": platform,
        "goal": "年代 打猎",
        "book_meta": {platform: {"status": "done",
                                 "data": {"category": "悬疑惊悚"}}},
    }


class _Base(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.root = Path(self._tmp.name)
        self._saved = paths.PUBLISH_DIR
        paths.PUBLISH_DIR = self.root

    def tearDown(self):
        paths.PUBLISH_DIR = self._saved
        self._tmp.cleanup()

    # -------------------------------------------------------------- 造数据
    def forced_publish(self, **kw):
        params = {"platform": "fanqie", "action": "upload_chapter",
                  "task_id": "t-force", "chapter_no": 1, "ok": True,
                  "quality_forced": True,
                  "quality_reason": "赶周末流量，先发再说",
                  "quality_blockers": ["第1章无获得：前三千字没有可量化的收益",
                                       "第3章爽点未兑现：承诺的猎物迟迟不落地"]}
        params.update(kw)
        ledger.record(**params)

    def rows(self):
        out = []
        for fp in sorted(self.root.glob("publish-*.jsonl")):
            for line in fp.read_text(encoding="utf-8").splitlines():
                if line.strip():
                    out.append(json.loads(line))
        return out

    def reject(self, **kw):
        params = {"platform": "fanqie", "stage": "signing_rejected",
                  "quote": "综合质量一般，缺乏吸引力。"}
        params.update(kw)
        return outcome.record(_task(), **params)


class LedgerBlockersTest(_Base):
    def test_forced_row_keeps_blockers(self):
        self.forced_publish()
        row = self.rows()[-1]
        self.assertTrue(row["quality_forced"])
        self.assertEqual(len(row["quality_blockers"]), 2)
        self.assertIn("第1章无获得", row["quality_blockers"][0])

    def test_plain_row_has_no_blocker_field(self):
        ledger.record("fanqie", "upload_chapter", task_id="t-force",
                      chapter_no=1, ok=True)
        row = self.rows()[-1]
        self.assertFalse(row["quality_forced"])
        self.assertNotIn("quality_blockers", row)

    def test_forced_without_detail_writes_no_empty_list(self):
        # 老台账那种「只记了 forced、没记明细」的形态：不许补空列表
        self.forced_publish(quality_blockers=None)
        row = self.rows()[-1]
        self.assertNotIn("quality_blockers", row)

    def test_blockers_are_capped_and_trimmed(self):
        self.forced_publish(quality_blockers=["  %d 号判据  " % i for i in range(9)])
        row = self.rows()[-1]
        self.assertEqual(len(row["quality_blockers"]), ledger.MAX_BLOCKERS)
        self.assertEqual(row["quality_blockers"][0], "0 号判据")
        self.assertLessEqual(len(row["quality_blockers"][0]),
                             ledger.MAX_BLOCKER_CHARS)


class LastForcedTest(_Base):
    def test_no_record_returns_none(self):
        self.assertIsNone(ledger.last_forced("t-force", platform="fanqie"))

    def test_failed_forced_row_is_ignored(self):
        self.forced_publish(ok=False, error="平台要求安全验证：验证码")
        self.assertIsNone(ledger.last_forced("t-force", platform="fanqie"))

    def test_other_platform_is_not_matched(self):
        self.forced_publish(platform="qimao")
        self.assertIsNone(ledger.last_forced("t-force", platform="fanqie"))
        self.assertIsNotNone(ledger.last_forced("t-force", platform="qimao"))

    def test_latest_wins(self):
        self.forced_publish(chapter_no=1, quality_reason="第一次")
        self.forced_publish(chapter_no=2, quality_reason="第二次")
        got = ledger.last_forced("t-force", platform="fanqie")
        self.assertEqual(got["quality_reason"], "第二次")
        self.assertEqual(got["chapter_no"], 2)


class OutcomeBackfillTest(_Base):
    def test_rejection_carries_force_audit(self):
        self.forced_publish(chapter_no=3)
        entry = self.reject()
        audit = entry["force_audit"]
        self.assertEqual(audit["stage"], "signing_rejected")
        self.assertEqual(audit["chapter_no"], 3)
        self.assertEqual(audit["reason"], "赶周末流量，先发再说")
        self.assertTrue(audit["forced_at"])
        self.assertTrue(audit["rejected_at"])
        self.assertEqual(len(audit["blockers"]), 2)

    def test_audit_survives_reload_from_disk(self):
        self.forced_publish()
        self.reject()
        stored = json.loads((self.root / "outcomes.json").read_text(encoding="utf-8"))
        self.assertIn("force_audit", stored["t-force"]["fanqie"])

    def test_no_forced_record_writes_no_field(self):
        entry = self.reject()
        self.assertNotIn("force_audit", entry)
        stored = json.loads((self.root / "outcomes.json").read_text(encoding="utf-8"))
        self.assertNotIn("force_audit", stored["t-force"]["fanqie"])

    def test_good_stage_never_audits(self):
        self.forced_publish()
        entry = self.reject(stage="signed")
        self.assertNotIn("force_audit", entry)

    def test_stale_audit_is_dropped_when_new_verdict_is_not_forced(self):
        self.forced_publish()
        self.assertIn("force_audit", self.reject())
        # 第二次被拒之前并没有强推：上一次的因果账必须摘掉，否则看起来像
        # 「这本又被强投了一次」。清掉台账模拟「那次强发已经不在窗口里」。
        rows = list(self.root.glob("publish-*.jsonl"))
        self.assertTrue(rows)
        for fp in rows:
            fp.unlink()
        entry = self.reject(quote="改过之后仍然不行。")
        self.assertNotIn("force_audit", entry)

    def test_summary_exposes_audit(self):
        self.forced_publish()
        self.reject()
        got = outcome.summary("t-force")["fanqie"]
        self.assertEqual(got["force_audit"]["chapter_no"], 1)

    def test_summary_is_empty_dict_without_force(self):
        self.reject()
        self.assertEqual(outcome.summary("t-force")["fanqie"]["force_audit"], {})


class ForcedSourceTest(_Base):
    """谁推开的闸门：人（manual）/ 程序代填（auto）/ 没写理由（unknown）。

    2026-10 台账里那 25 条 `quality_forced` 全是前端
    `pbApiWithQualityOverride` 自动重试代填的——用户根本没看见拦截。
    把它们记成「人明知有风险还是投了」，就是把这个 bug 洗成了人的失误。
    """

    def test_source_written_on_ledger_row(self):
        self.forced_publish(quality_forced_source="manual")
        self.assertEqual(self.rows()[-1]["quality_forced_source"], "manual")

    def test_source_absent_when_not_forced(self):
        ledger.record("fanqie", "upload_chapter", task_id="t-force", ok=True)
        self.assertNotIn("quality_forced_source", self.rows()[-1])

    def test_audit_carries_source(self):
        self.forced_publish(quality_forced_source="auto")
        self.assertEqual(self.reject()["force_audit"]["source"], "auto")

    def test_old_rows_without_source_omit_the_key(self):
        self.forced_publish()          # 没传 source，模拟本次改动之前的老行
        self.assertNotIn("source", self.reject()["force_audit"])

    def test_manager_classifies_reason_text(self):
        from core.publish import manager
        auto_reason = ("用户已启用全自动发布；系统按平台校准流程自动提交")
        self.assertEqual(manager._forced_source(True, auto_reason), "auto")
        self.assertEqual(manager._forced_source(True, "赶周末流量"), "manual")
        self.assertEqual(manager._forced_source(True, ""), "unknown")
        self.assertEqual(manager._forced_source(False, auto_reason), "")


if __name__ == "__main__":
    unittest.main()
