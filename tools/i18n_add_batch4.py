#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""第四轮词条：并行 agent 新增界面（换肤卡片/代码主题/作品信息/经验包市场变体/
用量占位符/运行列表错误语）的 EN 翻译。key 与前端运行时字符串逐字一致。"""
from pathlib import Path
import sys

I18N = Path(r"E:\GoOut\MultiAgentOrchestration\app\ui\i18n.js")

TR = {
    # —— 皮肤（SKINS 渲染点包 t()，定义存中文 key） ——
    "深海": "Deep Sea",
    "经典": "Classic",
    "森野": "Forest",
    "暖阳": "Amber",
    "霓虹": "Neon",
    "高对比": "High Contrast",
    "藏青底色 + 天蓝强调，夜间长时间盯任务更沉静（默认）": "Navy base + sky-blue accent; calmer for long night sessions (default)",
    "黑白灰 + 蓝色强调，ChatGPT 式清爽配色": "Black/white/gray + blue accent — the clean ChatGPT-style palette",
    "墨绿底色 + 青翠强调，偏自然的护眼配色": "Dark-green base + emerald accent; a natural, easy-on-the-eyes palette",
    "暖棕底色 + 琥珀强调，纸感暖调": "Warm brown base + amber accent; a paper-warm tone",
    "暗紫底色 + 品红强调，霓虹感强": "Deep purple base + magenta accent; strong neon vibe",
    "纯黑 / 纯白 + 硬边框、去阴影，弱视与强光环境更清晰": "Pure black / pure white + hard borders, no shadows; clearer for low vision or bright light",
    # —— 代码主题描述（下拉改用 desc + 预览徽标） ——
    "经典浅色，清爽不抢眼，日间界面首选": "Classic light — clean and unobtrusive; the daytime pick",
    "VS 家族浅色，蓝紫关键词配色": "Visual Studio family light, blue-purple keywords",
    "苹果开发工具同款浅色，冷色克制": "Xcode-style light; cool and restrained",
    "米黄纸感底色，长时间阅读更柔和": "Cream paper-tone base; softer for long reading",
    "经典深色，夜间界面首选": "Classic dark; the nighttime pick",
    "VS 家族深色，灰蓝底更沉稳": "Visual Studio family dark; steady gray-blue base",
    "高饱和黄紫粉，老牌编辑器名主题": "Saturated yellow/purple/pink — the classic editor theme",
    "Atom 出品的均衡深色，蓝灰底不刺眼": "Atom's balanced dark; gentle blue-gray base",
    # —— 作品信息一键生成 ——
    "番茄": "Fanqie",
    "七猫": "Qimao",
    "作品名": "Book title",
    "签约模式": "Signing model",
    "目标读者": "Target readers",
    "阅读标签": "Reader tags",
    "内容标签": "Content tags",
    "主角名1": "Lead 1",
    "主角名2": "Lead 2",
    "作品简介": "Synopsis",
    "作品名称": "Book title",
    "一级分类": "Category L1",
    "二级分类": "Category L2",
    "作品标签": "Book tags",
    "作品状态": "Book status",
    "已生成": "Generated",
    "生成中": "Generating",
    "生成失败": "Generation failed",
    # —— 经验包市场变体（skillpacks/*.md frontmatter 与 market.py desc 不同文案） ——
    "周报晨报生成器守则": "Weekly/Morning Report Generator Rules",
    "提交信息规范 / 分支模型 / 危险操作红线 / 冲突与回滚 / 提交前自检清单": "Commit message conventions / branch model / risky-op red lines / conflict & rollback / pre-commit checklist",
    "提测/评审前的风险过单：边界与异常 / 资源与并发 / 安全 / 兼容与回退": "Pre-review risk triage: boundary & exceptions / resources & concurrency / security / compatibility & rollback",
    "面向用户的更新公告：固定六段结构 / 写作纪律 / 破坏性变更三要素 / 可复制模板与反例": "User-facing release notes: fixed six-section structure / writing discipline / three elements of breaking changes / copyable template with counter-examples",
    "从任务与运行记录生成汇报：先取材再总结 / 晨报三段 / 周报四段 / 量化纪律与模板": "Build reports from tasks & runs: gather-then-summarize / three morning sections / four weekly sections / quantified discipline & templates",
    "角色档案模板（欲望/恐惧/语言指纹）+ 四拍弧光规划 + 连载防崩人设纪律与自检": "Character sheet template (desire/fear/language fingerprint) + four-beat arc planning + anti-OOC discipline & self-checks for serials",
    "设定台账五件套 / 设定变更三步流程 / 高频吃书场景 / 章前查章后记闭环": "Five-part setting ledger / three-step change process / high-frequency canon-break scenarios / pre-chapter & post-chapter check loop",
    # —— 用量页占位模型名（usage.py 落盘的占位串） ——
    "(历史未记录)": "(not recorded — legacy runs)",
    "(默认)": "(default)",
    # —— 运行列表错误语（store.py 重启中断） ——
    "服务重启中断，可重试": "Interrupted by service restart — retry to resume",
    # —— 提供商 wire 适配提示残余 ——
    "已适配：": "Adapted: ",
    "未发现可适配的 wire": "No adaptable wire found",
}


def js_str(s):
    out = []
    for ch in s:
        if ch == '"':
            out.append('\\"')
        elif ch == "\\":
            out.append("\\\\")
        elif ch == "\n":
            out.append("\\n")
        else:
            out.append(ch)
    return '"' + "".join(out) + '"'


BLOCK_HEAD = "    // —— 2026-09-16 第四轮：皮肤/代码主题/作品信息/经验包变体/用量占位 ——"


def main():
    sys.path.insert(0, str(I18N.parent.parent / "tools"))
    from i18n_coverage import dict_keys
    src = I18N.read_text(encoding="utf-8")
    if BLOCK_HEAD.strip() in src:
        print("块已存在，跳过")
        return
    existing = dict_keys()
    lines, dupes = [], 0
    for k, v in TR.items():
        if k in existing:
            dupes += 1
            continue
        lines.append("    " + js_str(k) + ": " + js_str(v) + ",")
    if not lines:
        print(f"全部 {dupes} 条已存在，跳过")
        return
    block = "\n" + BLOCK_HEAD + "\n" + "\n".join(lines)
    anchor = src.index("  // ---------- 工具 ----------")
    close = src.rindex("\n  };", 0, anchor)
    src = src[:close] + block + src[close:]
    I18N.write_text(src, encoding="utf-8")
    print(f"插入 {len(lines)} 条（{dupes} 条重复跳过）")


if __name__ == "__main__":
    main()
