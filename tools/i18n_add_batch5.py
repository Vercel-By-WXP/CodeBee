#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""第五轮词条：并行 agent 新增 + 英文扫摘第二轮发现的缺口。
含：编排者标签、导入跳过说明、经验教训标题（内置 40 条 seed lessons）、
qwen 目录 note 短版、DeepSeek note 变体。"""
from pathlib import Path
import sys

I18N = Path(r"E:\GoOut\MultiAgentOrchestration\app\ui\i18n.js")

TR = {
    # —— 编排者 / 用量 ——
    "编排者": "Orchestrator",
    # —— modelhub 跳过说明（note 带 %s 的静态部分） ——
    "跳过 %s 条（缺地址或格式不识别）：%s": "Skipped %s entries (missing URL or unrecognized format): %s",
    "跳过 %s 个（缺合法 baseURL）：%s": "Skipped %s (invalid baseURL): %s",
    "settings.json 里的 ANTHROPIC_BASE_URL 不是 http/https": "ANTHROPIC_BASE_URL in settings.json is not http/https",
    "settings.json 的 env 未配置 ANTHROPIC_BASE_URL": "env in settings.json has no ANTHROPIC_BASE_URL",
    "config.toml 未配置第三方 model_providers（且 auth.json 无 OPENAI_API_KEY）": "config.toml has no third-party model_providers (and auth.json has no OPENAI_API_KEY)",
    "settings.json 的 modelProviders 为空": "modelProviders in settings.json is empty",
    "未在 .env 找到 GEMINI_API_KEY / GOOGLE_GEMINI_BASE_URL": "GEMINI_API_KEY / GOOGLE_GEMINI_BASE_URL not found in .env",
    "opencode.json 的 provider 为空": "provider list in opencode.json is empty",
    "Cursor 未自定义 API Key / Base URL（官方订阅无本地凭证可导入）": "Cursor has no custom API key / base URL (official subscription has no local credentials to import)",
    "Trae 未添加自定义模型（预置模型的 AK / BaseURL 为空，无凭证可导入）": "Trae has no custom models (built-in models have empty AK / BaseURL — nothing to import)",
    "settings.yaml 未配置 llm-deepseek.baseURL": "llm-deepseek.baseURL not set in settings.yaml",
    # —— qwen note 短版（catalog.json data 目录与 catalog.py 内置不同步） ——
    "通义千问编码 CLI（gemini-cli 系）；无头走 stdin": "Qwen coding CLI (gemini-cli family); headless via stdin",
    # —— DeepSeek note 长版（catalog.py 136-137 拼接） ——
    "DeepSeek 官方 agent harness（dsh，profile 插件架构）；无头是「一次性任务」——答完即退，无交互后续，不支持会话恢复；任务只走位置参数（超长提示词受 Windows 命令行上限约 32k 约束）；模型写进 ~/.dsh/settings.yaml 的 agent-default-model.model；密钥由「CLI 绑定」注入 DEEPSEEK_API_KEY（优先级最高）；端点注入 DEEPSEEK_BASE_URL，但若 settings.yaml 已配 base_url，则以它为准（settings 优先于 env）":
        "DeepSeek's official agent harness (dsh, profile-plugin architecture); headless is one-shot — it answers and exits, no interactive follow-up or session resume; tasks pass via positional args (very long prompts cap at Windows' ~32k command-line limit); the model goes to agent-default-model.model in ~/.dsh/settings.yaml; the \"CLI Bindings\" page injects DEEPSEEK_API_KEY (highest priority) and DEEPSEEK_BASE_URL, but if settings.yaml already sets base_url it wins (settings over env)",
    # —— skills.py 内置 seed 教训标题（40 条，data 副本里跑出来的） ——
    "三轮失败升级": "Escalate after three failed rounds",
    "人物 维度反复不达标": "Characters dimension repeatedly below threshold",
    "情节 维度反复不达标": "Plot dimension repeatedly below threshold",
    "文笔 维度反复不达标": "Prose dimension repeatedly below threshold",
    "信息与证据必须有来源": "Info & evidence must have sources",
    "修复不重复空转": "No blind repair loops",
    "修复前本地复跑": "Reproduce locally before fixing",
    "修复绑定失败证据": "Capture evidence on bind failure",
    "关键爽点须预埋伏笔": "Key payoffs need planted setup",
    "函数任务缺验收项": "Function tasks lack acceptance items",
    "切换前保留失败诊断": "Keep failure diagnostics before switching",
    "加函数校验完整性": "Verify completeness when adding functions",
    "加函数类任务开工前补齐函数名、参数、返回值、错误处理和验收测试；信息不足时先向用户确认，避免按猜测实现导致verify不通过。":
        "Before starting add-a-function tasks, nail down the function name, params, return value, error handling and acceptance tests; confirm with the user when info is missing — guessing leads to verify failures.",
    "多轮修复未变要停": "Stop when fixes change nothing",
    "开篇信息密度不足": "Opening lacks information density",
    "开篇说明拖节奏": "Expository opening drags pacing",
    "打脸必须反派在场": "Payback needs the villain on stage",
    "打脸须反派在场": "Payback requires the villain present",
    "无问题不盲信": "Don't trust an empty review",
    "明确接口验收": "Explicit interface acceptance",
    "爽点须配新阻力": "Every payoff needs fresh resistance",
    "环境缺陷误判代码": "Environment issues misread as code bugs",
    "目标不可执行先澄清": "Clarify unactionable goals first",
    "禁静态倒叙承接章": "No static flashback bridge chapters",
    "空评审不可信": "Empty reviews are untrustworthy",
    "章末钩子防自答": "Chapter-end hooks must not self-answer",
    "章节开篇钩子滞后": "Chapter opening hook arrives late",
    "章首钩子不足": "Weak chapter-opening hook",
    "胜利只给七分": "Wins cap at seventy percent",
    "评审不能替代验证": "Review cannot replace verification",
    "评审与验证脱节": "Review and verification disconnected",
    "重复修复未定位": "Repeated fixes without root cause",
    "隐瞒须硬动机": "Concealment needs hard motives",
    "验收以verify为准": "verify is the acceptance bar",
    "验收口径显式化": "Make acceptance criteria explicit",
    "验证不通过不得通过": "Failed verification blocks acceptance",
    "验证失败先修复": "Fix before re-review on verify failure",
    "验证失败先归因": "Attribute verify failures first",
    "验证失败无问题清单": "Verify failure with no issue list",
    "验证未过先定位": "Locate before retry on verify failure",
    "验评不一致": "Verification-review mismatch",
    "决定性反转所依赖的条款、道具、关系，必须在更早章节落笔可见（至少一句具体描写），不能事后亮牌+主角自答带过；写大纲时为每个大爽点标注伏笔埋设，交稿前自查越度。":
        "Every clause, prop or relationship a decisive reversal relies on must appear concretely in earlier chapters (at least one specific description) — no last-minute reveals plus protagonist self-answering; tag the setup for each big payoff in the outline and self-check before delivery.",
    "下次每章正文前100字内必须出现新信息、冲突、悬念或目标；写作前先定章首钩子句，验收时检查读者是否能立即知道为何继续读。":
        "From now on, the first 100 characters of each chapter must surface new information, conflict, suspense or a goal; fix the chapter-opening hook line before drafting, and at acceptance check whether a reader can immediately tell why to keep reading.",
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


BLOCK_HEAD = "    // —— 2026-09-16 第五轮：编排者标签/导入说明/内置教训标题/目录 note 变体 ——"


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
