#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""把本轮 i18n 补漏的 EN 词条批量插入 i18n.js 字典尾部（const EN = { ... }; 的 }; 前）。

数据在脚本内 TR 里（人工翻译）。转义规则与字典既有词条一致：
JS 源码里 \n 两字符 = 运行时字面反斜杠+n（app.js 的 uiConfirm key 就是这么写的）；
需要真换行的 key（少数），值用真换行。
"""
import re
from pathlib import Path

I18N = Path(r"E:\GoOut\MultiAgentOrchestration\app\ui\i18n.js")

# key → en 译文。key 与 app.js/index.html 运行时字面量逐字节一致（含前导空格）。
# 注意：源文件中 \n 是两字符（反斜杠+n）时，这里写双反斜杠 \\n。
TR = {
    # —— 拼接片段 ——
    " 个供应商（google 等协议）仅登记，不支持注入 CLI，未出现在上面的下拉中。": " provider(s) (google etc.) are registry-only, can't inject into CLIs, and are hidden from the dropdown above.",
    " 个降级备选）。": " fallback(s)).",
    " 分": " min",
    " 参与编排": " Takes part in orchestration",
    " 启用编排者（规划 / 难度判定 / 写作大纲）": " Enable orchestrator (planning / difficulty judging / outline)",
    " 回应正常": " responded OK",
    " 字（自动注入该类任务的规划与评审提示词）": " chars (planning & review prompts for this task type are injected automatically)",
    " 按难度自动选模型（简单/困难）": " Auto-pick model by difficulty (easy/hard)",
    " 条）": " entries)",
    " 次运行": " run(s)",
    " 次（": " time(s) (",
    " 步": " step(s)",
    "% 成": "% ok",
    "(未知)": "(unknown)",
    "、": ", ",
    "」的模型。": "\" model.",
    "出现 ": " seen ",
    "创建 ": "created ",
    "另有 ": "Another ",
    "名称 ": "Name ",
    "引擎 ": "Engine ",
    "成本 ": "Cost ",
    "打开": "Open",
    "注入该厂商凭据": "Injects this provider's credentials",
    "来源 ": "Source ",
    "模式 ": "Mode ",
    "次 · 首次失败 ": " calls · first failure ",
    "正文 ": "Body ",
    "步骤 ": "Steps ",
    "耗时": "Duration",
    "第 ": "Run ",
    "运行 ": "Run ",
    "输入 ": "Input ",
    "输出 ": "Output ",
    "调用": "Calls",
    "费用": "Cost",
    "轮": " rounds",
    "章": " chapters",
    "万": "K",
    "亿": "B",
    "年": "",
    "月": "m",
    "日": "d",
    "刚刚": "just now",
    "昨天": "yesterday",
    # —— 标点/空白（en 回落原样即可，但补上保证覆盖表干净） ——
    "；": "; ",
    "　": " ",
    "　·　": " · ",
    "　模型 ": " · model ",
    # —— 短词按钮/标签 ——
    "恢复": "Restore",
    "恢复所选": "Restore selected",
    "测试": "Test",
    "测试连接": "Test connection",
    "日志": "Log",
    "清除": "Clear",
    "禁用": "Disable",
    "禁用厂商": "Disable provider",
    "禁用模型": "Disable model",
    "禁用该厂商": "Disable this provider",
    "禁用该模型": "Disable this model",
    "重启": "Restart",
    "状态": "Status",
    "工具": "Tool",
    "角色": "Role",
    "智能体": "Agent",
    "模型": "Model",
    "模型：": "Model: ",
    "电脑": "Computer",
    "接管": "Take over",
    "打开网页": "Open web",
    "合成": "Merge",
    "连接异常": "connection issue",
    "连续失败": "consecutive failures",
    "一致性": "Consistency",
    "人物塑造": "Characters",
    "情节逻辑": "Plot logic",
    "文笔风格": "Prose style",
    "节奏爽点": "Pacing & hooks",
    "流程规范": "Workflow rules",
    "未分类": "Uncategorized",
    "开发仓库（git clone）": "Dev repo (git clone)",
    "未知安装方式": "Unknown install type",
    "源码拷贝": "Source copy",
    "npm 全局安装": "npm global install",
    "安装方式": "Install type",
    "当前版本": "Current version",
    "当前版本：": "Current version: ",
    "（当前值）": "(current value)",
    "（无主运行）": "(orphan run)",
    "（未同步版本号）": "(version not synced)",
    "（未生效：检查密钥 / 启停 / 模型）": "(inactive: check key / enable state / model)",
    "（未能推导，请先在 catalog 配置 uninstall）": "(couldn't derive — set uninstall in the catalog first)",
    "（未设置）": "(not set)",
    "（生效中）": "(active)",
    "（等待输出…）": "(waiting for output…)",
    "（自定义）": "(custom)",
    "（该维度暂无数据）": "(no data for this dimension)",
    "（跳过 ": "(skipped ",
    "），无需升级。": ") — no update needed.",
    "＋ 新建自定义流程": "＋ New custom flow",
    "，可在「关于与更新」一键升级": " — one-click upgrade in About & Updates",
    "，当前没有可注入的 CLI，编排时会回落为 CLI 默认配置。": ", but no injectable CLI matches it — orchestration falls back to CLI defaults.",
    "：连续失败 ": ": consecutive failures ",
    "该供应商协议为 ": "This provider's protocol is ",
    "该供应商已停用：编排时不会注入它，将回落为 CLI 默认配置（模型链也不会生效）。": "This provider is disabled: orchestration won't inject it and will fall back to CLI defaults (the model chain won't apply either).",
    "绑定后编排调用会注入该供应商的 API key 与地址；不绑定则只按下方模型链传 -m 参数。": "When bound, orchestrated calls inject this provider's API key and URL; otherwise only the model chain below is passed via -m.",
    "还没有可选模型：先到「模型接入」页导入供应商并获取模型列表。": "No models available yet — import a provider and fetch its model list on the \"Models\" page first.",
    "还没有已安装且可编排的 CLI——先到「智能体管理」页安装并启用。": "No installed, orchestration-capable CLI yet — install and enable one on the \"Agents\" page.",
    "还没有自动教训——完成一次真实任务后，系统会自己复盘并沉淀。": "No auto lessons yet — the system reviews and distills them after a real run completes.",
    "不绑定（用 CLI 自身的凭据与配置）": "Not bound (use the CLI's own credentials & config)",
    "anthropic（Claude 系）": "anthropic (Claude family)",
    "openai（Codex / 通用）": "openai (Codex / generic)",
    "google（Gemini，仅登记不支持注入）": "google (Gemini, registry only — no injection)",
    "API 地址 *": "API URL *",
    "API 密钥": "API key",
    "协议 *": "Protocol *",
    "名称 *": "Name *",
    "例：公司网关": "e.g. Company gateway",
    "https://host/v1（若填 /chat/completions 会自动收敛为基址）": "https://host/v1 (a /chat/completions suffix is trimmed to the base URL)",
    "sk-...（可留空，稍后补填）": "sk-... (can be left empty, fill in later)",
    "可留空": "optional",
    "默认模型（写入配置文件）": "Default model (written to the CLI's config)",
    "✓ 连通 ": "✓ Reachable ",
    "左侧选择供应商；还没有供应商时点左上角「导入」，": "Pick a provider on the left; none yet? Click \"Import\" at the top-left —",
    "可从 CCSwitch / Codex / Claude Code / ZCode / Qwen / Gemini / OpenCode / Continue / Cursor / Trae 扫描带入。": " configs can be scanned from CCSwitch / Codex / Claude Code / ZCode / Qwen / Gemini / OpenCode / Continue / Cursor / Trae.",
    "正在扫描本机 AI 工具配置…": "Scanning local AI tool configs…",
    "勾选要导入的来源。导入只读取这些工具的配置，不会改动它们本身；": "Tick the sources to import. Import only reads these tools' configs without modifying them;",
    "已导入过的供应商会原地更新（保留你设置的模型与启停状态）。": "already-imported providers update in place (keeping your model and enable settings).",
    "从列表删除：刷新/重新导入不会再带回，可在分组底部恢复": "Remove from list: refresh/re-import won't bring it back — restorable at the group footer",
    "任务类型": "Task type",
    "供应商健康告警": "Provider health alerts",
    "命令面板": "Command palette",
    "例：2500": "e.g. 2500",
    "例：8": "e.g. 8",
    "例：8（逐章起草+评审+修订）": "e.g. 8 (draft+review+revise per chapter)",
    "例：python -m pytest -q（可空=只评审）": "e.g. python -m pytest -q (empty = review only)",
    "例：情节, 人物, 文笔": "e.g. plot, characters, prose",
    "例：技术播客单集脚本产出": "e.g. produce a tech-podcast episode script",
    "例：播客脚本": "e.g. podcast script",
    "可空，自动取目标首行": "optional — auto-taken from the goal's first line",
    "工作目录内的文件名": "File name inside the workdir",
    "1 = 关闭": "1 = off",
    "1 = 关闭（2-3 每章多稿择优，更贵）": "1 = off (2-3 drafts per chapter, best wins — pricier)",
    "小写字母开头，可留空": "start with a lowercase letter; optional",
    "留空 = 2": "empty = 2",
    "留空 = 7.0": "empty = 7.0",
    "留空 = 内容, 结构, 表达": "empty = content, structure, style",
    "留空 = 内置通用模板": "empty = built-in template",
    "留空 = 按流程 ID 生成": "empty = derived from flow ID",
    "留空则保存到默认路径；或填绝对路径，例 E:\\GoOut\\my-project": "Leave empty to save to the default path, or enter an absolute path, e.g. E:\\GoOut\\my-project",
    "自定义流程只需填名称与引擎，其余留空走默认。": "Custom flows only need a name and engine — leave the rest empty for defaults.",
    "预置流程可直接编辑（阈值/轮数/维度/章节数/提示词），改动随时可「恢复默认」；": "Built-in flows are editable (threshold / rounds / dimensions / chapters / prompts) — \"Reset\" restores defaults anytime;",
    "语言 / Language": "Language",
    "检查器分区": "Inspector sections",
    "输入/输出": "Input/Output",
    "当前范围内都是历史运行回填的记录：只保留总量与费用，": "Every record in this range is backfilled from historical runs — totals and cost only;",
    "输入/输出/缓存细分从新调用开始记录。": "input/output/cache breakdown starts with new calls.",
    "当天暂无用量": "No usage today",
    "已发出打开指令": "Open command sent",
    "已恢复": "Restored",
    "已手动标记 {0} 为恢复，探针将重新核实": "Manually marked {0} as recovered — probes will re-verify",
    "已按「%1」预填新任务表单，确认或修改后提交": "New task form prefilled from \"%1\" — review, adjust, then submit",
    "已写到第 %1 章，将从第 %2 章接着写（同一工作目录，成书合并全本）。续写章数：": "Written through chapter %1; continuation starts at chapter %2 (same workdir; merged book includes all). Chapters to continue:",
    "已是最新版。": "Already up to date.",
    "已注入 ": "injected ",
    "已清除 ": "Cleared ",
    "已禁用厂商 {0}：链降级自动跳过，绑定页可重新启用": "Provider {0} disabled: chain fallback skips it — re-enable on the Bindings page",
    "已禁用模型 {0} · {1}，链降级自动跳过；绑定页可重新启用": "Model {0} · {1} disabled: chain fallback skips it — re-enable on the Bindings page",
    "已静默 {0} 的告警（恢复后自动重新武装）": "Muted {0}'s alert (auto-rearms on recovery)",
    "开始升级": "Start upgrade",
    "当前状态不支持": "Not supported in the current state",
    "恢复内置默认 catalog？你对该文件的修改将丢失。": "Restore the built-in default catalog? Your edits to that file will be lost.",
    "手动标记恢复": "Mark recovered manually",
    "手机相机扫码即自动登录（地址已含访问令牌，扫一次永久记住）。": "Scan with your phone camera to log in (the URL embeds the access token — one scan remembers it).",
    "局域网地址要求手机与电脑连同一 WiFi；Tailscale 地址出门也能用，": "LAN addresses require the phone and computer on the same WiFi; Tailscale works anywhere —",
    "两端需登录同一 Tailscale 账号。手机控制时另一端自动变为只读，可在顶栏接管。": "both devices must sign into the same Tailscale account. When the phone is in control, other devices go read-only — take over from the top bar.",
    "连不上时（如路由器重启后地址变了）回电脑重新打开此弹框扫新码即可。": "If it can't connect (e.g. the address changed after a router reboot), re-open this dialog on the computer and scan the new QR.",
    "无法续写：": "Cannot continue writing: ",
    "无法获取版本信息（服务未连接）": "Can't fetch version info (service not connected)",
    "服务重启中，几秒后自动恢复": "Service restarting — recovers in a few seconds",
    "正在升级…": "Upgrading…",
    "正在重启…": "Restarting…",
    "目录选择仅限本机使用，请手动输入路径": "Folder picking is local-only — enter the path manually",
    "确认禁用模型 {0}？链降级将自动跳过它，其余模型不受影响；可在 CLI 绑定页重新启用。": "Disable model {0}? Chain fallback will skip it; other models are unaffected — re-enable it on the CLI Bindings page.",
    "确认禁用该厂商？禁用后链降级自动跳过它，恢复后可在 CLI 绑定页重新启用。": "Disable this provider? Chain fallback will skip it — re-enable on the CLI Bindings page once recovered.",
    "注意：沿用原目录且稿件名相同时，提交会覆盖原稿件": "Note: keeping the same workdir and manuscript name means the commit overwrites the original manuscript",
    "注意：沿用原目录时，提交会覆盖原书的章节与成书文件；续写请用「继续连载」": "Note: with the same workdir, the commit overwrites the original chapters and merged book — use \"Continue serial\" to extend",
    "继续连载（新任务）": "Continue serial (new task)",
    "升级会下载并安装最新版（约 1-2 分钟），期间服务继续可用。现在开始？": "The upgrade downloads and installs the latest version (~1–2 min); the service stays usable meanwhile. Start now?",
    "升级失败，详情见运行记录": "Upgrade failed — see runs for details",
    "升级完成！点「重启服务生效」换新版本": "Upgrade done! Click \"Restart to apply\" for the new version",
    "升级已开始，日志见运行记录": "Upgrade started — log lives in Runs",
    "发现新版本": "New version found",
    "发现新版本 v": "New version v",
    "运行中的记录不可删除，请先取消": "Can't delete a running record — cancel it first",
    "删除该记录": "Delete this record",
    "加载失败：": "Load failed: ",
    "创建续写任务失败：": "Failed to create continuation task: ",
    "运行时模型链（跨厂商，最多 ": "Runtime model chain (cross-provider, max ",
    "立即升级": "Upgrade now",
    "重启服务换上新版本？页面会短暂断开并自动恢复。": "Restart the service to load the new version? The page will blink and auto-recover.",
    "重启超时，请手动刷新页面": "Restart timed out — refresh the page manually",
    "静默本次告警": "Mute this alert",
    "暂无经验包": "No skill packs yet",
    "最多选 ": "At most ",
    "CLI 默认凭据": "CLI default credentials",
    "Chrome·电脑": "Chrome · Computer",
    "1 = 关闭（2-3 每章多稿择优，更贵）": "1 = off (2-3 drafts per chapter, best wins — pricier)",
    "启用中": "Enabled",
    # —— 卸载确认 / 升级提示（i18n_fix_nl 已把源码 \\n 归一为 \n：运行时是真换行） ——
    "？\n\n将执行：\n": "?\n\nWill run:\n",
    "\n\n该 CLI 会从本机移除（配置文件保留）。此操作不可撤销。": "\n\nThe CLI will be removed from this machine (config files kept). This cannot be undone.",
    "\n最新版本：": "\nLatest: ",
    "\n可更新：": "\nUpdatable: ",
    "\n说明：": "\nNote: ",
    "\n点击到「编排设置」更换": "\nClick to change it in \"Orchestration\".",
    "尚未选择编排者供应商\n点击到「编排设置」，从已接入的厂商里选一个": "No orchestrator provider selected\nOpen \"Orchestration\" and pick one of the connected providers",
    "」？\n刷新 / 重新导入模型列表都不会再带回，可在分组底部「恢复全部」找回。": "\"?\nRefreshing / re-importing the model list won't bring it back — use \"Restore all\" at the group footer.",
    "」？\n停用后它的绑定会回落为 CLI 默认；配置与模型列表保留，可随时再启用。": "\"?\nOnce disabled, its bindings revert to CLI defaults; config and model list are kept and can be re-enabled anytime.",
    " 个供应商？\n停用后其绑定会回落为 CLI 默认；配置与模型列表都保留，可随时再启用。": " providers?\nOnce disabled, their bindings revert to CLI defaults; config and model lists are kept and can be re-enabled anytime.",
    " 个供应商？\n相关 CLI 绑定会自动解绑，此操作不可撤销。": " providers?\nRelated CLI bindings will be detached. This cannot be undone.",
    " 个模型？\n停用只影响编排选模，不删除配置。": " models?\nDisabling only affects orchestration model picking — no config is deleted.",
    " 个模型？\n刷新 / 重新导入都不会再带回，可在「已删除」里恢复。": " models?\nRefresh / re-import won't bring them back — restore them from \"Deleted\".",
    " 个模型？\n它们会重新启用并自动置顶。": " models?\nThey'll be re-enabled and moved to the top.",
    "恢复该供应商下全部已删除的模型？\n它们会回到优先级末尾。": "Restore all deleted models under this provider?\nThey'll return at the end of the priority order.",
}

BLOCK_HEAD = "    // —— 2026-09-15 第二轮补词条（HTML 拼接片段 + 覆盖率核对） ——"


def js_str(s: str) -> str:
    """Python str → JS 双引号字面量源码（保持字面 \\n 两字符原样）。"""
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


def main():
    import sys
    src = I18N.read_text(encoding="utf-8")
    if BLOCK_HEAD.strip() in src:
        print("块已存在，跳过")
        return
    sys.path.insert(0, str(I18N.parent.parent / "tools"))
    from i18n_coverage import dict_keys
    # 去重：字典里已有同 key（运行时真实值）的不重复插入
    existing = dict_keys()
    lines = []
    dupes = 0
    for k, v in TR.items():
        if k in existing:
            dupes += 1
            continue
        lines.append("    " + js_str(k) + ": " + js_str(v) + ",")
    if not lines:
        print(f"全部 {dupes} 条已存在，跳过")
        return
    block = "\n" + BLOCK_HEAD + "\n" + "\n".join(lines)
    # 插到 const EN = { ... } 收尾的 }; 之前。锚点：工具注释前最近的 };
    anchor = src.index("  // ---------- 工具 ----------")
    close = src.rindex("\n  };", 0, anchor)
    src = src[:close] + block + src[close:]
    I18N.write_text(src, encoding="utf-8")
    print(f"插入 {len(lines)} 条词条（{dupes} 条重复跳过）")


if __name__ == "__main__":
    main()
