# -*- coding: utf-8 -*-
"""skill 装前危险模式扫描（借鉴 NVIDIA SkillSpector 的静态扫描思路）。

研究数据（SkillSpector 对 31,132 个 skill 的分析）：26.1% 含漏洞、5.2% 疑似
恶意。我们在「白名单闸门 + SSRF 防护 + 剥离式检查」之外补一道**内容静态扫描**：
装前扫 skill 正文里的危险模式，给风险提示——提示不是拦阻（用户仍可装），
但危险必须被看见。

纯内存函数：输入是文本，输出是发现列表，无网络无落盘。
"""
from __future__ import annotations

import re

# 危险模式（静态正则；命中即在装前提示行展示）
# —— 每条：(模式, 类别, 中文说明)
_PATTERNS = [
    # 代码执行
    (r"\beval\s*\(", "代码执行", "动态 eval 执行任意代码"),
    (r"\bexec\s*\(", "代码执行", "exec 执行任意代码"),
    (r"subprocess|os\.system|Popen", "代码执行", "起子进程执行命令"),
    (r"child_process", "代码执行", "Node.js 子进程（可能执行任意命令）"),
    (r"(?:curl|wget)[^\n|]*\|\s*(?:ba|z|da)?sh\b", "代码执行",
     "下载内容直接管道进 shell 执行（dropper 特征）"),
    (r"Invoke-Expression|\biex\s*\(|DownloadString", "代码执行",
     "PowerShell 下载执行（dropper 特征）"),
    # 数据外发
    (r"https?://(?!api\.|docs\.|github\.com|raw\.githubusercontent)[a-z0-9.-]+/(upload|collect|track|ingest|webhook|callback)",
     "数据外发", "向非常规端点上传/回调数据"),
    (r"requests\.post|urllib\.request|fetch\s*\(", "网络请求", "发起网络请求（确认目标可信）"),
    (r"base64.{0,10}(decode|b64decode|atob)", "数据外发",
     "base64 解码（可能隐藏混淆载荷）"),
    (r"pastebin\.com|webhook\.site|requestbin|pipedream\.net|"
     r"ngrok\.(io|app|dev)|trycloudflare\.com", "数据外发",
     "常见外传/中转信道（pastebin、ngrok 隧道、webhook.site 等）"),
    # 持久化与挖矿（治理巡检 09-24 补：装后驻留与资源盗用的静态特征）
    (r"\bcrontab\b|schtasks|LaunchAgents|CurrentVersion\\Run|StartupItems",
     "持久化", "注册定时任务/自启动项（持久化驻留特征）"),
    (r"stratum\+tcp|xmrig|cryptonight", "可疑意图", "加密货币挖矿特征"),
    # 敏感信息读取
    (r"os\.environ|process\.env|getenv", "环境读取", "读取环境变量（可能带走密钥）"),
    (r"\.ssh/|\.aws/|\.npmrc|\.gitconfig|credentials|\.env\b", "敏感文件", "触碰凭据/密钥文件路径"),
    (r"keychain|credential manager|dpapi", "敏感文件", "访问系统凭据库"),
    (r"\.claude/|\.zcode/|\.kimi-code/|\.codebee/", "敏感文件",
     "触碰 AI 助手配置目录（可能篡改系统提示词或模型绑定）"),
    # 提示注入特征
    (r"(ignore|disregard|forget).{0,30}(previous|above|prior|all).{0,20}(instruction|prompt|rule)",
     "提示注入", "指令覆盖话术（试图无视既有规则）"),
    (r"system prompt|开发者指令|隐藏指令", "提示注入", "提及系统提示词/隐藏指令"),
    (r"do not (tell|reveal|mention).{0,20}(user|human|player)", "提示注入", "要求对用户隐瞒行为"),
    # 反拒答/越权
    (r"(you (are|must) (now|act as)|从此你是|你现在必须)", "越权人格", "试图重设助手人格"),
    (r"exfiltrat|渗透|后门|backdoor|keylog", "可疑意图", "包含可疑渗透/后门词汇"),
]

_COMPILED = [(re.compile(p, re.I), cat, desc) for p, cat, desc in _PATTERNS]


def scan_text(text, max_findings=12):
    """扫描一段 skill 文本。返回 [{category, detail, line}]；空列表=干净。

    line 是 1 起的行号（供装前提示定位）。同一类别只报首个命中
    （提示行是给人看的，重复刷屏没有信息量）。"""
    findings, seen_cats = [], set()
    if not text:
        return []
    lines = text.splitlines()
    for rx, cat, desc in _COMPILED:
        if cat in seen_cats:
            continue
        for i, ln in enumerate(lines, 1):
            if rx.search(ln):
                findings.append({"category": cat, "detail": desc, "line": i})
                seen_cats.add(cat)
                break
        if len(findings) >= max_findings:
            break
    return findings


def risk_label(findings):
    """发现列表 → 风险标签（装前提示行用）。

    三级：高危（代码执行/敏感文件/可疑意图/数据外发/持久化）任一
    命中 = 高风险；中危类（网络请求/环境读取/提示注入/越权人格）≥2 类
    同时命中 = 中风险；有发现 = 注意；空 = 干净。"""
    if not findings:
        return ""
    cats = {f["category"] for f in findings}
    if cats & {"代码执行", "敏感文件", "可疑意图", "数据外发", "持久化"}:
        return "⚠ 高风险"
    medium_cats = cats & {"网络请求", "环境读取", "提示注入", "越权人格"}
    if len(medium_cats) >= 2:
        return "⚠ 中风险"
    return "△ 注意"


def scan_summary(text):
    """一步到位：扫描+汇总成一行提示文案（空=干净返回空串）。"""
    fs = scan_text(text)
    if not fs:
        return ""
    label = risk_label(fs)
    top = "、".join("%s(行%d)" % (f["category"], f["line"]) for f in fs[:4])
    more = " 等 %d 项" % len(fs) if len(fs) > 4 else ""
    return "%s：%s%s" % (label, top, more)
