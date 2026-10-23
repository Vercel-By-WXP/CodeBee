# -*- coding: utf-8 -*-
"""代码任务的评审者选择必须与模式无关，一律走隔离选择器。

手动模式（mode=manual）曾走 `_pick_reviewer_legacy`：只排除作者 id，不比较
模型族/供应商/上游。作者与下一个候选绑定同族模型时，「跨厂商对抗评审」退化成
同族互评，且 `route.reviewer` 为空串，报告里看不出任何异常。

本测试锁 wiring：`_run_code` 里的评审者选择只有一条路径，且那条路径是
`router.pick_reviewer`；v1 选择器不再存在（避免有人「为兼容」把它接回来）。
"""
from __future__ import annotations

import re
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
CODE = (ROOT / "app" / "core" / "pipeline_code.py").read_text(encoding="utf-8")
PIPELINE = (ROOT / "app" / "core" / "pipeline.py").read_text(encoding="utf-8")


class ReviewerSelectionWiringTest(unittest.TestCase):
    def test_code_review_uses_isolating_picker(self):
        self.assertIn("router.pick_reviewer(", CODE)
        self.assertNotIn("_pick_reviewer_legacy", CODE)

    def test_legacy_picker_is_gone(self):
        self.assertNotIn("def _pick_reviewer_legacy", PIPELINE)

    def test_reviewer_selection_is_not_branched_by_mode(self):
        # 关键契约：`if mode in ("auto", "expert")` 决定用哪个选择器的写法不得复现。
        offenders = [line.strip() for line in CODE.splitlines()
                     if re.search(r'mode\s+in\s+\("auto",\s*"expert"\)', line)
                     and "reviewer" in line]
        self.assertEqual(offenders, [])


if __name__ == "__main__":
    unittest.main()
