# -*- coding: utf-8 -*-
"""JS 侧 t() 字面量键 ↔ i18n.js 词条全量对账（2026-10-07 轮第 3/4 步落地件）。

本轮第 2/4 步（07 时巡检班）C 项勘误：i18n 覆盖检测口径=按中文源文键（前端
t(f.name) 包裹映射），按 id 键测会全量误报。该口径此前只落到 index.html
data-i18n 键（test_i18n_key_coverage.py）与 BUILTIN_FLOWS 三字段
（test_i18n_dups.py）——app.js 里 1700+ 处 JS 侧 t("字面量") 调用从无对账，
本轮全量实测捞出 54 键缺词条（英文界面 toast/状态/弹窗标题中文裸奔），补齐
后本文件把「JS 侧字面量键全覆盖 + 本轮修复面点名」锁成契约：日后加文案漏
词条、或本批词条被误删，在此先红。

边界：t(key) 未命中静默返回键本身（zh 模式同值、不报错），缺词条只能靠
文件级对账兜底；字面量含拼接片段（"P95 "/"（行 " 等），词条照实收（与既有
"共 "/" · 吞吐 " 同例）；正则只认双引号实参（与文件实际写法一致），与
test_i18n_key_coverage 的转义形态兼容口径相同。

跑法：python -m unittest discover -s tests -p "test_borrow_iteration.py" -v
"""
from __future__ import annotations

import io
import os
import re
import unittest

from base import BaseTest

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_APP_JS = os.path.join(_ROOT, "app", "ui", "app.js")
_I18N_JS = os.path.join(_ROOT, "app", "ui", "i18n.js")

# 与探针同式：t("…") 双引号字面量实参（排除 obj.t( 成员调用误配）
_T_CALL = re.compile(r'(?<![\w$.])t\(\s*"((?:[^"\\]|\\.)+)"\s*[,)]')

# 本轮补齐的 54 键修复清单（点名锁定，防部分回退；与 i18n.js 尾追批一一对应）
_REPAIRED_KEYS = (
    "请先选择任务",
    "正在读取任务详情…",
    "读取任务详情失败：",
    "未命名",
    "↻ 重试任务",
    "↻ 从超时进度继续",
    "从最近一次已保存的大纲和已完成章节断点续跑",
    "暂无运行步骤",
    "加载失败",
    "正在加载…",
    "正在读取报告…",
    "报告读取失败（{0}），稍后可重试",
    "点击“成果”页签后加载报告和成品文件",
    "本次运行排队中：实时进度看「步骤」页签",
    "我已在平台提交",
    "建书未确认：",
    "填稿已完成，请先在浏览器里提交，再点击确认",
    "已确认提交，台账已更新",
    "确认提交失败：",
    "将从最靠前的待发章节开始填稿（共 {0} 章待发）。本轮只填一章并停在表单页，"
    "由你在浏览器里确认提交；提交后点击“我已在平台提交”，再选择下一章。"
    "护栏（每日上限/连续失败暂停）生效。继续？",
    "正在填第一章稿——填好后请在浏览器窗口里确认提交，再重新校准",
    "Blocked：",
    "契约已更新",
    "🕘 流程版本历史：",
    "内容指纹（重装突变对账用）",
    "指纹 ",
    "自述：",
    "技能 ",
    "插件加载失败",
    "还没有发现本地插件",
    "插件安装成功",
    "安装失败",
    "插件已启用",
    "插件已停用",
    "卸载该插件？技能包和本地插件副本会被移除。",
    "插件已卸载",
    "卸载失败",
    "（行 ",
    " 个文件 · ",
    "评审维度（2-6 个，逗号分隔）",
    "例：正确性，可读性",
    "题目要求（一句话，给裁判看）",
    "题目内容 ",
    "（发给候选模型的完整题面，≤2000 字）",
    "样题已添加",
    "删除这道自定义样题？历史评测结果保留。",
    "把这段样题分享码发给任何人，对方在「添加样题 → 导入」粘贴即可：",
    "📤 导出样题",
    "粘贴样题分享码（CBSAMP1. 开头），ID 冲突自动换号。",
    "📥 导入样题分享码",
    "已导入样题「",
    "P95 ",
    "复制失败，请手动长按/右键复制",
    "✓ 远程端点连通，可上传",
)


def _read(path):
    with io.open(path, encoding="utf-8") as fh:
        return fh.read()


def _entry_count(i18n, key):
    """词条出现次数：裸形态 + JS 转义形态（承 test_i18n_key_coverage 口径）。"""
    bare = i18n.count('"%s":' % key)
    if bare:
        return bare
    return i18n.count('"%s":' % key.replace('"', '\\"'))


class AppJsTLiteralI18nTests(BaseTest):
    """app.js 全部 t("字面量") 键在 i18n.js 有词条：英文界面不回退中文裸奔。"""

    def test_all_app_js_t_literals_have_entry(self):
        """全量对账：每个 JS 侧字面量键至少一条词条（转义形态兼容）。"""
        i18n = _read(_I18N_JS)
        keys = sorted(set(_T_CALL.findall(_read(_APP_JS))))
        # 守卫自身：解析面骤降说明正则/文件形态漂移，先红报形态而非漏报
        self.assertGreater(len(keys), 1000,
                           "t() 字面量解析数异常（%d），正则或文件形态漂移" % len(keys))
        missing = [k for k in keys if _entry_count(i18n, k) < 1]
        self.assertEqual(
            missing, [],
            "app.js t() 字面量键缺 i18n 词条（英文界面回退中文）：%s"
            % " | ".join(m[:40] for m in missing[:5]))

    def test_repaired_keys_locked(self):
        """本轮 54 键修复面逐一在位（subTest 隔离定位；防部分回退）。"""
        i18n = _read(_I18N_JS)
        for key in _REPAIRED_KEYS:
            with self.subTest(key=key):
                self.assertGreaterEqual(_entry_count(i18n, key), 1, key)


if __name__ == "__main__":
    unittest.main()
