# -*- coding: utf-8 -*-
"""各轮借调研落地件的回归守卫累积（文件名按迭代惯例固定）。

2026-10-09 轮第 3/4 步两件（见文件尾两节）：
  A. flows overrides 的 verify_command 消毒分流（含路径命令保真 +
     分享码 roundtrip 保真）；
  B. recommendTaskType 的 video_script 规则补「b站/视频号」。

——以下为 2026-10-07 轮落地件——
JS 侧 t() 字面量键 ↔ i18n.js 词条全量对账。

本轮第 2/4 步（07 时巡检班）C 项勘误：i18n 覆盖检测口径=按中文源文键（前端
t(f.name) 包裹映射），按 id 键测会全量误报。该口径此前只落到 index.html
data-i18n 键（test_i18n_key_coverage.py）与 BUILTIN_FLOWS 三字段
（test_i18n_dups.py）——app.js 里 1700+ 处 JS 侧 t("字面量") 调用从无对账，
本轮全量实测捞出 54 键缺词条（英文界面 toast/状态/弹窗标题中文裸奔），补齐
后本文件把「JS 侧字面量键全覆盖 + 本轮修复面点名」锁成契约：日后加文案漏
词条、或本批词条被误删，在此先红。

边界：t(key) 未命中静默返回键本身（zh 模式同值、不报错），缺词条只能靠
文件级对账兜底；字面量含拼接片段（"P95 "/"（行 " 等），词条照实收（与既有
"共 "/" · 吞吐 " 同例）；正则认双引号与单引号两种实参（2026-10-07 落地班补
盲区：app.js 现存 2 处 t('…') 单引号调用，旧正则漏对账），与
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
# 单引号形态（2026-10-07 落地班补盲区：app.js 现存 t('…') 写法，旧正则漏对账）
_T_CALL_SQ = re.compile(r"(?<![\w$.])t\(\s*'((?:[^'\\\n]|\\.)+)'\s*[,)]")

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
        keys = sorted(set(_T_CALL.findall(_read(_APP_JS)))
                      | set(_T_CALL_SQ.findall(_read(_APP_JS))))
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



# ---------------------------------------------------------------------------
# 2026-10-09 轮第 3/4 步落地件 A：verify_command 消毒分流。
# 修复前：overrides 把 verify_command 与 manuscript 共用文件名消毒
# （`/`→`_` 等），`pytest tests/test_a.py` 被改坏成 `pytest tests_test_a.py`
# 必失败；与自定义 code 流程（upsert_flow 仅截断 200）口径不一。
# 修复后：verify_command 仅 strip+截断，manuscript 消毒保持不变。
from test_recommend_rules import _rules


class VerifyCommandOverrideTests(BaseTest):
    """overrides 的 verify_command 走「仅截断」口径（分享码导入同走此链）。"""

    CMD = "pytest tests/test_a.py -q"  # 修复前会被改坏成 pytest tests_test_a.py -q

    def test_verify_command_with_path_preserved_all_builtin_types(self):
        """修复前可复现 → 修复后保真：全部预置类型的 overrides 命令原样。"""
        from app.core.flows import BUILTIN_FLOWS, _apply_overrides
        for f in BUILTIN_FLOWS:
            merged = _apply_overrides(f, {"verify_command": self.CMD})
            self.assertEqual(merged["verify_command"], self.CMD,
                             "类型 %s 的验证命令被文件名消毒改写" % f["id"])

    def test_manuscript_sanitization_unchanged(self):
        """manuscript 仍走文件名消毒（路径分隔符/点段/前导点），行为不变。"""
        from app.core.flows import _apply_overrides
        base = {"id": "x", "engine": "review"}
        self.assertEqual(
            _apply_overrides(base, {"manuscript": "a/b\\c..d.md"})["manuscript"],
            "a_b_c_d.md")
        # 点段先替换成 `_`，单前导点才由 lstrip 吃掉（既有行为原样锁定）
        self.assertEqual(
            _apply_overrides(dict(base), {"manuscript": "..lead.md"})["manuscript"],
            "_lead.md")
        self.assertEqual(
            _apply_overrides(dict(base), {"manuscript": ".hidden.md"})["manuscript"],
            "hidden.md")

    def test_verify_command_truncate_and_empty_ignored(self):
        """与自定义 code 流程同口径：仅截断 200；空白不落键（保持默认）。"""
        from app.core.flows import _apply_overrides
        base = {"id": "x", "engine": "code", "verify_command": ""}
        long_cmd = "echo " + "a" * 300
        self.assertEqual(
            _apply_overrides(dict(base), {"verify_command": long_cmd})["verify_command"],
            long_cmd[:200])
        self.assertEqual(
            _apply_overrides(dict(base), {"verify_command": "   "})["verify_command"],
            "")

    def test_share_code_roundtrip_custom_code_flow(self):
        """分享码导出→导入 roundtrip（自定义 code 流程）：含路径命令保真。"""
        from app.core import flows
        _, err = flows.upsert_flow({"id": "it_code", "name": "迭代验证",
                                    "engine": "code", "verify_command": self.CMD})
        self.assertIsNone(err, err)
        code, err = flows.export_flow_code("it_code")
        self.assertIsNone(err, err)
        result, err = flows.import_flow_code(code)
        self.assertIsNone(err, err)
        self.assertEqual(result["status"], "noop")  # 内容与现状一致即保真
        self.assertEqual(flows.get_flow("it_code")["verify_command"], self.CMD)

    def test_share_code_roundtrip_builtin_override(self):
        """分享码导入预置 code 流程（写 overrides 路径）：含路径命令保真。"""
        from app.core import flows
        _, err = flows.upsert_flow({"id": "code", "name": "代码", "engine": "code",
                                    "verify_command": self.CMD})
        self.assertIsNone(err, err)
        code, err = flows.export_flow_code("code")
        self.assertIsNone(err, err)
        result, err = flows.import_flow_code(code)
        self.assertIsNone(err, err)
        self.assertEqual(result["status"], "noop")
        self.assertEqual(flows.get_flow("code")["verify_command"], self.CMD)


# ---------------------------------------------------------------------------
# 2026-10-09 轮第 3/4 步落地件 B：recommendTaskType 的 video_script 规则补词。
# 修复前：goal_hint 引导「抖音/B站/视频号」（flows.py video_script），规则只认
# 「抖音」——照提示输入不弹类型切换建议。
class VideoScriptRuleTests(unittest.TestCase):
    """video_script 推荐规则补「b站/视频号」（提取法复用 test_recommend_rules）。"""

    def _recommend(self, goal):
        for fid, pat in _rules():
            if pat.search(goal):
                return fid
        return ""

    def test_goal_hint_platforms_hit_video_script(self):
        """照 goal_hint 提示输入「B站/视频号」应命中类型建议（修复前为空）。"""
        self.assertEqual(self._recommend("做个3分钟的B站视频，介绍这个项目"),
                         "video_script")
        self.assertEqual(self._recommend("视频号脚本，新品发布60秒"), "video_script")

    def test_existing_hits_unchanged(self):
        """既有命中不回归：抖音/短视频照旧，规则顺序（novel 先于 article）照旧。"""
        self.assertEqual(self._recommend("写个短视频脚本，抖音带货"), "video_script")
        self.assertEqual(self._recommend("写一篇悬疑小说"), "novel")


if __name__ == "__main__":
    unittest.main()
