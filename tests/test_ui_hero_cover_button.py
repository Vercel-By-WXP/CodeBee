# -*- coding: utf-8 -*-
"""封面位自带生成入口（2026-11-02 用户实案）。

用户原话：「封面图生成功能是不是和上面的合并成一个了」→ 截图红箭头指着
hero 带左侧那块深蓝「封面」占位。查代码：那块是纯展示缩略图（.eh-cover），
零点击行为，但圆角 + 阴影 + 描边的观感和按钮一模一样，点下去毫无反应
—— 又一个「假可点」。用户随后定方案：「顶部那个我们默认搞一个效果，然后
旁边或者下面加一个生成按钮吧」。

落地要点（每条都是回归点）：
1. 结构：图/占位与按钮竖排进 .eh-coverbox，宽度锁 62px 与封面图同宽；
2. 无任务 id 不出按钮：否则又是一个点了没反应的假入口；
3. 唯一确认链：顶部按钮先只读拉 /cover/prompt 弹窗展示，确认后才进
   POST /cover 付费链（不能为了「快」跳过确认直接烧钱）；
4. 重绘签名：hero 平时只按 runKey 判重，cover_gen 变了察觉不到，
   生成中/出图两态会冻在旧占位上，必须把封面状态并入签名；
5. 移动端 390px：按钮只留图标，否则 48px 封面位撑不开四个字。
"""

import os
import re
import unittest

UI_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                      "app", "ui")


def _read(name):
    with open(os.path.join(UI_DIR, name), "r", encoding="utf-8") as fh:
        return fh.read()


def _css_rule(css, selector):
    """取选择器完全相等那条规则的声明体（允许行首缩进，便于抓媒体查询内的规则）。"""
    m = re.search(r"(?m)^\s*%s\s*\{([^}]*)\}" % re.escape(selector), css)
    return m.group(1) if m else None


def _hero_paint_body(src):
    m = re.search(r"(?m)^function expertHeroPaint\s*\([^)]*\)\s*\{", src)
    if not m:
        return None
    start = m.end()
    end = src.find("\n}\n", start)
    return src[start:end if end != -1 else len(src)]


def _window_fn_body(src, name):
    """截取 `window.<name> = [async] function (...) {` 到下一个行首 `}`。"""
    m = re.search(r"(?m)^window\.%s\s*=\s*(?:async\s*)?function\s*\([^)]*\)\s*\{"
                  % re.escape(name), src)
    if not m:
        return None
    start = m.end()
    end = src.find("\n};", start)
    return src[start:end if end != -1 else len(src)]


class TestHeroCoverButton(unittest.TestCase):

    def test_cover_slot_and_button_are_grouped(self):
        body = _hero_paint_body(_read("app.js"))
        self.assertIsNotNone(body, "expertHeroPaint 函数不见了")
        self.assertIn('<div class="eh-coverbox">', body,
                      "封面位与按钮必须包进 .eh-coverbox（同宽竖排），否则按钮会掉到 band 的行内流里")
        self.assertIn('coverBtn + "</div>"', body,
                      ".eh-coverbox 里没接 coverBtn：顶部又没有生成入口了")
        # 真图与占位仍在同一容器内，出图后宽度不跳
        self.assertIn('class="eh-cover-img"', body, "封面真图渲染被删了")
        # 2026-11-03 占位重做：竖排书名换成横排书封（coverPhHtml），铺在真图底下
        self.assertIn("coverPhHtml(", body, "占位封面要经 coverPhHtml 输出（横排书名+底部标签）")
        self.assertIn('class="eh-cover-hit"', body,
                      "封面要包进 .eh-cover-hit 可点开大图（用户「点击也不能放大展开」）")
        self.assertIn("openCoverView(", body, "封面点击必须接 openCoverView 灯箱")

    def test_no_button_without_task_id(self):
        body = _hero_paint_body(_read("app.js"))
        self.assertRegex(body, r"const coverBtn = o\.taskId",
                         "coverBtn 必须以 o.taskId 为前置条件：没有任务 id 就调不到 /cover，"
                         "画出来的按钮只能是假可点（点了没反应）")

    def test_button_calls_hero_entry_and_disables_while_running(self):
        body = _hero_paint_body(_read("app.js"))
        self.assertIn("heroCoverGen(", body,
                      "封面位按钮必须调 heroCoverGen（弹窗确认入口），不能直连付费接口")
        self.assertIn("disabled", body,
                      "cover_gen.status=running 时按钮必须 disabled 且不可点，否则会连开两次付费生成")
        self.assertIn('covBusy ? t("生成中")', body,
                      "生成中按钮文案应切成「生成中」，而不是留一个可点的「生成封面」")

    def test_confirm_chain_before_paid_call(self):
        src = _read("app.js")
        preview = _window_fn_body(src, "heroCoverGen")
        go = _window_fn_body(src, "heroCoverGo")
        self.assertIsNotNone(preview, "window.heroCoverGen 不存在：顶部按钮点了会报未定义")
        self.assertIsNotNone(go, "window.heroCoverGo 不存在：弹窗确认按钮点了会报未定义")
        self.assertIn("/cover/prompt", preview,
                      "heroCoverGen 必须先只读拉 /cover/prompt 展示提示词（确认后才付费）")
        self.assertNotIn("coverGen(", preview,
                         "heroCoverGen 里不许直接调 coverGen：那就是跳过确认直接烧钱")
        self.assertIn("coverGen(", go,
                      "heroCoverGo 才是付费入口，必须调 window.coverGen")
        self.assertIn("closeModal()", go,
                      "确认后必须先关弹窗，否则生成中对话区被遮罩挡住")
        self.assertIn("covKey", go,
                      "heroCoverGo 要作废 hero 的 covKey 签名，否则「生成中」这一态不重绘")

    def test_hero_signature_tracks_cover_state(self):
        body = _hero_paint_body(_read("app.js"))
        self.assertIn("covKey", body,
                      "hero 渲染签名必须含封面状态，否则「生成中 → 出图」永远冻在旧占位")
        self.assertRegex(body, r"host\.dataset\.covKey === covKey|dataset\.covKey \|\| \"\"\) === covKey",
                         "缓存守卫要比较 dataset.covKey，不能只比 key/kind")
        self.assertIn("host.dataset.covKey = covKey", body,
                      "重绘时必须写回 covKey，否则每帧都会重画")
        # covKey 不能拼进 dataset.key：heroTaskOf 靠 key 反解任务
        self.assertNotIn("key = key +", body,
                         "cover 状态不许拼进 dataset.key：heroTaskOf 用该值反解任务，加了后缀就解不到")

    def test_css_width_matches_cover_and_mobile_keeps_icon(self):
        css = _read("style.css")
        box = _css_rule(css, ".eh-coverbox")
        self.assertIsNotNone(box, "缺少 .eh-coverbox 规则：按钮会脱离封面列排到别处")
        self.assertRegex(box, r"width:\s*84px",
                         ".eh-coverbox 宽度必须与 .eh-cover-hit 一致（2026-11-03 起 84px）")
        hit = _css_rule(css, ".eh-cover-hit")
        self.assertIsNotNone(hit, "缺少 .eh-cover-hit 规则：可点封面没有可点范围")
        self.assertRegex(hit, r"cursor:\s*zoom-in", "封面可点开大图，指针要给 zoom-in 提示")
        self.assertIsNotNone(_css_rule(css, ".eh-cover-gen"),
                             "缺少 .eh-cover-gen 按钮样式：会退化成浏览器默认按钮")
        self.assertIsNotNone(_css_rule(css, ".eh-cover-gen.has-img"),
                             "缺少 .eh-cover-gen.has-img：有封面时按钮会和「生成封面」抢注意力")
        # 灯箱（点封面放大）必须有层与关闭钮样式
        self.assertIsNotNone(_css_rule(css, ".cv-layer"), "缺少封面灯箱遮罩样式")
        self.assertIsNotNone(_css_rule(css, ".cv-x"), "缺少灯箱关闭按钮样式")
        # -webkit-line-clamp 在新 Chrome 已弃用失效（计算值落 flow-root），
        # 书名截断必须走 max-height 整数行
        title = _css_rule(css, ".eh-cover-title")
        self.assertIsNotNone(title, "缺少 .eh-cover-title 书名样式")
        self.assertNotIn("-webkit-line-clamp", title, "书名截断不许再用已失效的 -webkit-line-clamp")
        self.assertRegex(title, r"max-height:\s*4\.35em", "书名 max-height 要卡 3 行（1.45 × 3）")
        self.assertRegex(title, r"white-space:\s*normal",
                         "外层是 button，必须显式掰回 normal，否则书名被压成一行")

        mobile = _read("mobile-workbench.css")
        self.assertRegex(mobile, r"#exp-hero \.eh-cover-gen span\s*\{\s*display:\s*none",
                         "移动端 50px 封面位放不下「生成封面」四个字，必须隐藏文案只留图标")
        self.assertRegex(mobile, r"#exp-hero \.eh-cover-gen \.ico\s*\{[^}]*display:\s*inline-block",
                         "移动端隐藏文案后必须把 .ico 显出来，否则按钮是空的（隐形按钮）")

    def test_confirm_dialog_has_styles_and_copy(self):
        css = _read("style.css")
        self.assertIsNotNone(_css_rule(css, ".cover-ask-prompt"),
                             "缺少 .cover-ask-prompt：提示词会挤成一坨，无法核对")
        i18n = _read("i18n.js")
        for key in ("未设封面", "换封面", "生成封面",
                    "确认后才会调用图像接口，产出 cover.png 落在任务运行目录。",
                    "图像提示词（自动组装）"):
            self.assertRegex(i18n, r'"%s":\s*"[^"]+"' % re.escape(key),
                             "缺少英文词条：%s（中文界面正常，切英文会漏原文）" % key)


if __name__ == "__main__":
    unittest.main()
