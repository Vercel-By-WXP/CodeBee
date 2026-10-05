# -*- coding: utf-8 -*-
"""全类型迭代落地核验（2026-10-05 全类型轮，第 3/4 步）：

  1. 正常：新增「演示文稿」（presentation）类型注册可用——注册表字段齐、
     dispatch 归 writing 维度、编译规格正确；
  2. 流程参数：不进 light/deep 集合，走标准单评审短链（评审稿「零额外
     改动」口径的回归锚）；交付契约接线（test_content_contracts 要求
     每个非连载 review 类型必须有契约，缺了全量红）；
  3. 回归：i-preview 图标在精灵表有定义（菜单不空白）；i18n EN 三字段
     键恰好一条（英文界面不回落中文）；
  4. 边界：帮助页「600+ 技能」静态数字去数字化——四条字符串同步后
     旧文案残留为零、新键跨端一致（app.js 中文原文 = i18n.js EN 键）。

跑法：python -m unittest discover -s tests -p "test_full_type_iteration.py" -v
"""
from __future__ import annotations

import io
import os
import re

from base import BaseTest
from test_i18n_dups import _en_block

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_APP_JS = os.path.join(_ROOT, "app", "ui", "app.js")
_I18N_JS = os.path.join(_ROOT, "app", "ui", "i18n.js")
_INDEX_HTML = os.path.join(_ROOT, "app", "ui", "index.html")


def _pairs_of(block, key):
    """EN 块里 key 的全部（值）命中——条数即该键出现次数。"""
    pat = r'(?:^|[,{]\s*)"' + re.escape(key) + r'"\s*:\s*"((?:[^"\\]|\\.)*)"'
    return re.findall(pat, block)


class PresentationFlowTests(BaseTest):

    def test_flow_registered_with_full_meta(self):
        """正常路径：presentation 注册、字段齐、如实声明不生成 PPT 二进制。"""
        from app.core import flows
        f = flows.get_flow("presentation")
        self.assertIsNotNone(f)
        self.assertTrue(f["builtin"])
        self.assertEqual(f["engine"], "review")
        self.assertEqual(f["manuscript"], "presentation.md")
        self.assertEqual(f["icon"], "i-preview")
        self.assertEqual(f["rubric"], ["结构逻辑", "内容密度", "视觉呈现", "演讲适配"])
        self.assertEqual(f["threshold"], 7.0)
        self.assertEqual(f["rounds"], 2)
        self.assertTrue(f["goal_hint"] and f["note"])
        self.assertIn("不生成 PPT", f["note"], "note 必须如实声明产出形态（防预期错位）")

    def test_dimension_writing_and_compile_spec(self):
        """dispatch 归 writing 维度；编译规格引擎/难度/产出文件正确。"""
        from app.core import dispatch, task_compile
        self.assertEqual(dispatch.TYPE_DIMENSIONS.get("presentation"), "writing")
        spec = task_compile.compile_task({"type": "presentation", "goal": "产品评审演示"})
        self.assertEqual(spec["engine"], "review")
        self.assertEqual(spec["dimension"], "writing")
        self.assertEqual(spec["difficulty"], "default")   # 非研究/方案类，不默认 hard
        self.assertEqual(spec["deliverable"], "presentation.md")
        self.assertEqual(spec["quality_dimensions"][0], "结构逻辑")

    def test_standard_review_lane_not_light_not_deep(self):
        """流程参数：标准难度走单评审短链（不进 light/deep 集合的口径锚）。"""
        from app.core import task_compile
        wf = task_compile.content_workflow({"type": "presentation", "rounds": 2}, "default")
        self.assertFalse(wf["outline"])
        self.assertEqual(wf["reviewers"], 1)
        self.assertEqual(wf["reason"], "标准内容使用单评审短链")

    def test_delivery_contract_wired(self):
        """交付契约接线：角色非兜底、约束块含逐页产出与不生成 PPT 声明。"""
        from app.core import pipeline
        role = pipeline._content_role({"type": "presentation"})
        self.assertNotEqual("内容交付专家", role)
        c = pipeline._content_contract({"type": "presentation"})
        self.assertIn("本类型交付约束", c)
        self.assertIn("逐页", c)
        self.assertIn("不生成 PPT", c)

    def test_icon_symbol_defined_in_sprite(self):
        """回归：i-preview 在 index.html 精灵表有 symbol 定义（菜单图标不空白）。"""
        with io.open(_INDEX_HTML, encoding="utf-8") as fh:
            html = fh.read()
        self.assertGreaterEqual(len(re.findall(r'symbol id="i-preview"', html)), 1)

    def test_i18n_en_keys_exactly_once(self):
        """回归：name/goal_hint/note 三字段 EN 键各恰好一条（缺=英文界面回落中文）。"""
        from app.core.flows import BUILTIN_FLOWS
        block = _en_block()
        by_id = {f["id"]: f for f in BUILTIN_FLOWS}
        for field in ("name", "goal_hint", "note"):
            text = by_id["presentation"][field]
            hits = _pairs_of(block, text)
            self.assertEqual(len(hits), 1,
                             "presentation.%s 应恰有一条 EN 译: %r" % (field, text))
            self.assertTrue(hits[0].strip())


class MarketplaceCopyDequantifiedTests(BaseTest):
    """候选 2：帮助页「600+ 技能」静态数字去数字化（六源缓存实数 810 且随上游
    浮动，静态下限必然持续过期——G 项巡检唯一观察项）。"""

    def test_no_stale_number_in_app_js(self):
        with io.open(_APP_JS, encoding="utf-8") as fh:
            src = fh.read()
        self.assertNotIn("600+ 技能", src, "app.js 残留「600+ 技能」静态数字")

    def test_new_cn_key_matches_en_exactly_once(self):
        with io.open(_APP_JS, encoding="utf-8") as fh:
            app_src = fh.read()
        with io.open(_I18N_JS, encoding="utf-8") as fh:
            i18n_src = fh.read()
        self.assertIn("t(\"技能一键安装\")", app_src, "app.js 应使用新文案键")
        hits = _pairs_of(_en_block(), "技能一键安装")
        self.assertEqual(len(hits), 1, "新键应恰有一条 EN 译")
        self.assertEqual(hits[0], "Skills install in one click")
        # 旧键两条不得残留（后值覆盖前值的隐形文案事故形态）
        self.assertNotIn("600+ 技能，一键安装", i18n_src)
        self.assertNotIn("600+ 技能一键安装", i18n_src)


if __name__ == "__main__":
    import unittest
    unittest.main()
