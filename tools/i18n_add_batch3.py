#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""第三轮词条：后端内置数据（流程/模板/经验包/目录 note/导入来源）的 EN 翻译。
渲染点已在前端包 t()，这里把 key=中文原文 的词条插进 i18n.js。
插入位置与去重逻辑同 i18n_add_batch2。"""
from pathlib import Path
import re
import sys

I18N = Path(r"E:\GoOut\MultiAgentOrchestration\app\ui\i18n.js")

TR = {
    # —— 内置流程名 ——
    "代码": "Code",
    "小说": "Novel",
    "连载小说": "Serial Novel",
    "自媒体文章": "Blog Article",
    "短视频脚本": "Short Video Script",
    "文档": "Document",
    "翻译": "Translation",
    "调研报告": "Research Report",
    "演讲稿": "Speech",
    "工作汇报": "Work Report",
    "商务邮件": "Business Email",
    "技术方案": "Tech Proposal",
    "简历": "Resume",
    # —— 内置流程 goal_hint ——
    "要实现/修复什么（一句话）": "What to build/fix (one sentence)",
    "写什么（题材 / 篇幅 / 风格）": "What to write (genre / length / style)",
    "题材/受众/卖点 + 总字数（例：2 万字都市女频，可签约平台）": "Genre/audience/selling points + total length (e.g. 20k-word urban romance for women, submission-ready)",
    "什么主题、投哪个平台（公众号/头条/知乎）、给谁看": "Topic, target platform (WeChat / Toutiao / Zhihu), and the audience",
    "什么主题、多长时长、投哪个平台（抖音/B站/视频号）": "Topic, video length, target platform (Douyin / Bilibili / Channels)",
    "要写什么文档、给谁看": "What document, and who reads it",
    "翻译什么（源文本位置 / 目标语言 / 要求）": "What to translate (source location / target language / requirements)",
    "调研什么问题、产出给谁用": "What question to research, and who consumes the report",
    "演讲场合 / 听众 / 时长 / 核心信息": "Occasion / audience / duration / key message",
    "周报/月报/述职？汇报给谁、做了什么（可贴流水账让小队提炼）": "Weekly/monthly report or promotion review? Who reads it, what was done (paste a rough log and let the crew distill it)",
    "给谁写、要达成什么、对方背景与你们的关系": "Who it's for, what to achieve, the recipient's context and your relationship",
    "要解决什么问题、约束条件（工期/技术栈/预算）、给谁评审": "Problem to solve, constraints (timeline / stack / budget), who reviews it",
    "目标岗位 + 个人经历（可贴旧简历附件），几年经验投什么职级": "Target role + experience (attach the old resume), years of experience and the level you're applying for",
    # —— 内置流程 note ——
    "实现 → 验证命令 → 跨厂商评审 → 自动修复/换将": "Implement → verify command → cross-provider review → auto-fix / swap agent",
    "起草 → 多维评审 → 修订循环 → 发布门禁": "Draft → multi-dim review → revision loop → publish gate",
    "大纲 → 逐章起草 → 每章多维评审修订 → 全局一致性评审 → 合并（可断点续跑）": "Outline → chapter-by-chapter drafting → per-chapter multi-dim review & revision → global consistency review → merge (resumable)",
    "公众号/头条风格文章：起草 → 多维评审 → 修订循环 → 发布门禁": "WeChat/Toutiao-style article: draft → multi-dim review → revision loop → publish gate",
    "分镜 + 口播脚本：起草 → 多维评审 → 修订循环 → 发布门禁": "Storyboard + voiceover script: draft → multi-dim review → revision loop → publish gate",
    "周报/月报/述职材料：起草 → 多维评审 → 修订循环 → 发布门禁": "Weekly/monthly/promo material: draft → multi-dim review → revision loop → publish gate",
    "商务/客户/跨部门邮件：起草 → 多维评审 → 修订循环 → 发布门禁": "Business/client/cross-team email: draft → multi-dim review → revision loop → publish gate",
    "技术选型/架构/实施方案：起草 → 多维评审 → 修订循环 → 发布门禁": "Tech selection/architecture/implementation plan: draft → multi-dim review → revision loop → publish gate",
    "简历优化/定制：起草 → 多维评审 → 修订循环 → 发布门禁": "Resume tailoring: draft → multi-dim review → revision loop → publish gate",
    # —— 评审维度 rubric ——
    "内容": "Content", "结构": "Structure", "表达": "Style",
    "情节": "Plot", "人物": "Characters", "文笔": "Prose", "节奏": "Pacing", "吸引力": "Appeal",
    "选题与标题": "Topic & title", "开头吸引力": "Opening hook", "结构节奏": "Structure & rhythm", "平台适配": "Platform fit", "传播性": "Shareability",
    "黄金3秒钩子": "3-second hook", "节奏密度": "Pacing density", "口播流畅": "Spoken flow", "画面可执行": "Shootable", "互动引导": "Call to engage",
    "准确性": "Accuracy", "结构清晰": "Clear structure", "表达流畅": "Fluent writing", "实用价值": "Practical value",
    "忠实度": "Fidelity", "流畅度": "Fluency", "术语一致性": "Term consistency", "风格贴合": "Style fit",
    "全面性": "Coverage", "深度": "Depth", "论据可靠": "Sound evidence", "可读性": "Readability", "结论质量": "Conclusion quality",
    "主题聚焦": "Topic focus", "感染力": "Emotive force", "语言风格": "Language style",
    "重点突出": "Key points up front", "数据支撑": "Data-backed", "下一步可执行": "Actionable next steps",
    "目的明确": "Clear purpose", "语气得体": "Right tone", "信息完整": "Complete info", "简洁度": "Conciseness",
    "可行性": "Feasibility", "方案完整性": "Plan completeness", "风险识别": "Risk identification", "成本与收益": "Cost & benefit",
    "真实可信": "Truthful & credible", "岗位匹配": "Role match", "成果量化": "Quantified results", "关键词覆盖": "Keyword coverage",
    # —— 内置经验包（skills.py） ——
    "七猫签约标准与写作规范": "Qimao Signing Standard & Writing Rules",
    "黄金一章、爽点纪律、期待感三源、人物红线、自检清单": "Golden first chapter, payoff discipline, three sources of anticipation, character red lines, self-check list",
    "番茄小说写作与流量守则": "Fanqie Novel Writing & Traffic Rules",
    "算法流量池/完读追读、黄金三章整体验、题材标签匹配、更新纪律、合同要点": "Traffic pools & read-through, golden-three-chapters holistic check, genre tag matching, update discipline, contract essentials",
    # —— market.py 内置市场 desc ——
    "算法流量池与完读追读、黄金三章整体验、题材标签匹配、更新纪律与合同要点。": "Traffic pools & read-through, golden-three-chapters holistic check, genre tag matching, update discipline, contract essentials.",
    "七猫签约导向的写作规范：黄金一章、爽点纪律、期待感三源、人物红线与自检清单。": "Qimao-signing-oriented writing rules: golden first chapter, payoff discipline, three sources of anticipation, character red lines, self-check list.",
    "Git 提交与分支守则": "Git Commit & Branch Rules",
    "提交信息格式与粒度、分支模型、force push 与回滚等危险操作红线，附提交前自检清单。": "Commit message format & granularity, branch model, red lines for force-push/rollback and other risky ops, with a pre-commit checklist.",
    "代码风险自查清单": "Code Risk Checklist",
    "提测/评审前按事故率过单：边界与异常、资源与并发、注入与泄密、跨平台与回退。": "Pre-review triage by incident rate: boundary & exception, resources & concurrency, injection & leakage, cross-platform & fallback.",
    "版本发布说明撰写模板": "Release Notes Template",
    "面向用户的更新公告写法：固定六段结构、破坏性变更三要素、可复制模板与反例对照。": "How to write user-facing release notes: fixed six-section structure, three elements of breaking changes, copyable template with counter-examples.",
    "周报/晨报生成器守则": "Weekly/Morning Report Generator Rules",
    "先取材再总结：从任务与运行记录提炼晨报三段、周报四段，量化纪律与可复制模板。": "Gather first, then summarize: three morning-report and four weekly-report sections distilled from tasks and runs, quantified discipline and reusable templates.",
    "角色小传与人物弧光模板": "Character Bio & Arc Templates",
    "角色档案模板（欲望/恐惧/语言指纹）、四拍弧光规划与连载防 OOC 纪律。": "Character sheet template (desire/fear/language fingerprint), four-beat arc planning, and anti-OOC discipline for serials.",
    "世界观设定一致性台账守则": "Worldbuilding Consistency Ledger Rules",
    "设定台账五件套、设定变更三步流程、高频吃书场景排查与章前查章后记闭环。": "Five-part setting ledger, three-step change process, high-frequency canon-break detection, and a pre-chapter/post-chapter check loop.",
    # —— 自动化模板（automation.py） ——
    "每日仓库巡检": "Daily Repo Inspection",
    "每天早上自动巡检一次仓库：汇总未提交变更、梳理 TODO/FIXME 待办与风险，生成当天的巡检报告 INSPECTION.md。": "Automated read-only repo inspection every morning: summarize uncommitted changes, list TODO/FIXME and risks, and generate the day's INSPECTION.md.",
    "连载定时推进": "Scheduled Serial Advancement",
    "配合连载小说任务：到点自动读取故事圣经与已有章节，续写下一段剧情，并自检人设、时间线与伏笔的一致性。": "For serial-novel tasks: at the scheduled time, reads the story bible and existing chapters, continues the plot, and self-checks character, timeline and foreshadowing consistency.",
    "经验库周整理": "Weekly Lesson Library Cleanup",
    "每周一自动整理经验库：合并重复教训、按主题归纳、标记疑似过时的条目，产出精简整理报告（不直接改写原文件）。": "Every Monday, tidies the lesson library: merges duplicates, groups by theme, flags likely-stale entries, and produces a compact report (never rewrites the original files).",
    "依赖与安全扫描": "Dependency & Security Scan",
    "每周五自动对项目做一次只读的依赖与安全巡检：清点直接依赖、排查敏感文件与明文密钥风险，给出升级整改建议。": "Every Friday, a read-only dependency & security inspection: inventory direct dependencies, scan for sensitive files and plaintext secrets, and suggest upgrades/remediation.",
    # —— data/catalog.json note ——
    "ChatGPT 官方编程智能体；本机走自定义 provider（gpt-5.5）": "OpenAI's official coding agent; on this machine it uses a custom provider (gpt-5.5)",
    "Anthropic 官方 CLI；无头调用自动注入 Git Bash 路径与输出上限": "Anthropic's official CLI; headless calls auto-inject the Git Bash path and output cap",
    "无头模式：opencode run（stdin 传入提示词）": "Headless mode: opencode run (prompt via stdin)",
    "通义千问编码 CLI（gemini-cli 系）；无头走 stdin": "Qwen coding CLI (gemini-cli family); headless via stdin",
    "Python 系结对编程 CLI；用 py -3.13 安装，模型经其配置/环境变量设置": "Python pairing CLI; install with py -3.13, model set via its config/env",
    "xAI 终端编码智能体；可执行名是 grok（不是 grok-build）": "xAI terminal coding agent; the executable is grok (not grok-build)",
    "Earendil Works 的 Pi 编码 CLI（需 Node ≥22.19）；包名必须带 @earendil-works/ 前缀": "Earendil Works' Pi coding CLI (needs Node ≥22.19); package name must carry the @earendil-works/ prefix",
    "DeepSeek 官方 agent harness（dsh，profile 插件架构）；无头是「一次性任务」——答完即退": "DeepSeek's official agent harness (dsh, profile-plugin architecture); headless is one-shot — it exits after answering",
    "小米 MiMo Code（opencode 衍生）；无头调用是子命令 mimo run \"提示词\"，-p 在该 CLI 是 --password": "Xiaomi MiMo Code (OpenCode derivative); headless is the subcommand mimo run \"prompt\" — -p means --password on this CLI",
    "月之暗面 Kimi 编码 CLI（TypeScript 版，需 Node ≥22.19）；旧 Python 版 kimi-cli 正在下线": "Moonshot's Kimi coding CLI (TypeScript, needs Node ≥22.19); the old Python kimi-cli is being retired",
    "网关型个人 AI 智能体（原 Clawdbot）；编排模板装好后需验证": "Gateway-style personal AI agent (formerly Clawdbot); verify after installing orchestration templates",
    # —— modelhub.py 导入来源说明 ——
    "本地库（Claude / Claude Desktop / Codex / Gemini / OpenClaw）": "Local hub (Claude / Claude Desktop / Codex / Gemini / OpenClaw)",
    "~/.claude/settings.json 的 env 供应商": "env providers in ~/.claude/settings.json",
    "~/.codex/config.toml + auth.json（含多 model_providers）": "~/.codex/config.toml + auth.json (multi model_providers)",
    "~/.zcode/v2/config.json 的 provider 表": "provider table in ~/.zcode/v2/config.json",
    "~/.qwen/settings.json 的 modelProviders": "modelProviders in ~/.qwen/settings.json",
    "~/.gemini 的 .env / settings.json": "~/.gemini .env / settings.json",
    "~/.config/opencode/opencode.json 的 provider 表": "provider table in ~/.config/opencode/opencode.json",
    "~/.continue/config.yaml 的 models": "models in ~/.continue/config.yaml",
    "Cursor 的 cursorAuth/openAIKey + 自定义 Base URL": "Cursor's cursorAuth/openAIKey + custom base URL",
    "Trae / Trae SOLO 中自定义模型的 Base URL + AK": "custom model Base URL + AK in Trae / Trae SOLO",
    "~/.dsh/settings.yaml 的 llm-deepseek（密钥走 ~/.dsh/.env）": "llm-deepseek in ~/.dsh/settings.yaml (key in ~/.dsh/.env)",
    # —— modelhub.py 动态提示（静态部分） ——
    "未找到 DEEPSEEK_API_KEY（dsh 把密钥放在 ~/.dsh/.env 或环境变量），导入后请在编辑里补填": "DEEPSEEK_API_KEY not found (dsh keeps it in ~/.dsh/.env or env vars) — fill it in via Edit after import",
    "配置里没有带 apiBase 的模型条目": "No model entries with apiBase in the config",
    "要打开即用请在「CLI 绑定」页启用": "To make it launch-ready, enable it on the \"CLI Bindings\" page",
    "要打开即用请在「CLI 绑定」页换绑可注入协议的供应商": "To make it launch-ready, rebind to an injectable-protocol provider on the \"CLI Bindings\" page",
    "未绑定供应商：打开后需在其自带界面登录；要打开即用请到「CLI 绑定」页绑定": "No provider bound: sign in via its own UI after launch; to make it launch-ready, bind one on the \"CLI Bindings\" page",
    # —— selfupdate.py note ——
    "开发仓库模式：请用 git pull 更新（自动升级会覆盖未提交的代码）": "Dev repo mode: update with git pull (auto-upgrade would overwrite uncommitted code)",
    "非 npm 安装，无法自动更新": "Not an npm install — can't auto-update",
    "无法比较版本（本地或 registry 版本号缺失）": "Can't compare versions (local or registry version missing)",
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


BLOCK_HEAD = "    // —— 2026-09-15 第三轮：后端内置数据（流程/模板/经验包/目录/导入来源） ——"


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
