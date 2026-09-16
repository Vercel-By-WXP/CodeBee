#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""第六轮词条：真实数据副本里 41 条自动教训的 title + content 全量 EN 翻译。
这些是 skills.py 内置 seed 教训（与 batch5 的标题表互补，含正文）。"""
from pathlib import Path
import sys

I18N = Path(r"E:\GoOut\MultiAgentOrchestration\app\ui\i18n.js")

TR = {
    "埋钩前先做谜面查重：读者能否用前文已有信息直接推出答案？能推出就换谜。优先立反常巧合、隐藏动机类真谜，并对钩子与已写情节做一次因果矛盾排查。":
        "Before planting a hook, check the riddle against what readers already know: can they deduce the answer from earlier text? If yes, change the riddle. Favor true mysteries built on strange coincidences and hidden motives, and run a cause-effect consistency check between the hook and what's already written.",
    "反击、收官章禁一路顺风：每章至少一个本章内生的新麻烦，胜利只给七分（留质疑、流程延迟或对手反扑），章末必再起一波；禁靠前文积累的情绪撑本章爆点。":
        "No smooth sailing in payback or finale chapters: each chapter needs at least one trouble born inside it, wins cap at seventy percent (leave doubt, procedural delay, or a counterattack), and a fresh wave must rise before the chapter ends. Never ride emotions accumulated in earlier chapters to carry this chapter's climax.",
    "爽点依赖反派崩溃时，反派须正面出场给反应：台词、失态、挣扎等细节，不能只靠时间戳或旁人转述暗示；递证据、报进度类功能角色也要注入个人情绪与立场，防工具人化。":
        "When a payoff depends on the villain breaking, the villain must be on stage reacting — dialogue, composure cracking, struggle — never implied via timestamps or secondhand reports. Functional characters who deliver evidence or progress reports also need personal emotions and stakes to avoid feeling like tools.",
    "角色手握可自证的证据却长期隐瞒时，必须先设定硬理由（如亮出会打草惊蛇、毁掉更大布局、付具体代价），并在隐瞒期间写其挣扎与伏笔；否则读者判降智，感情线信任基础崩塌。":
        "When a character holds self-exonerating evidence but conceals it long-term, establish a hard reason first (revealing it alerts the enemy, wrecks a bigger plan, costs something concrete), and write their struggle plus foreshadowing during the concealment — otherwise readers judge it as idiocy and the emotional trust collapses.",
    "review 通过但 verify_pass=false 时，先复现验收命令并读取失败输出，按失败点修复；不得以代码评审通过替代验证通过。":
        "When review passes but verify_pass=false, reproduce the acceptance command and read the failure output first; fix along the failure points. A passing code review never substitutes for passing verification.",
    "为新增函数补最小可运行测试，至少覆盖正常调用和边界；若无测试入口，先增加验证脚本，保证验证器能执行目标函数。":
        "Add a minimal runnable test for every new function, covering at least normal calls and boundaries; if there is no test entry, add a verification script first so the verifier can actually execute the target function.",
    "同一 verify 结果连续失败两轮后，停止盲改，检查函数签名、调用路径、依赖、退出码和测试覆盖，确认验证器真的运行了目标代码。":
        "After the same verify result fails twice in a row, stop blind patching — check the function signature, call path, dependencies, exit code and test coverage, and confirm the verifier really ran the target code.",
    "实现前明确函数名、参数、返回值和调用方式，并让评审与验证脚本使用同一口径；避免“只加函数”目标下验收标准不可判定。":
        "Before implementing, pin down the function name, params, return value and call convention, and make review and the verification script use the same yardstick — otherwise acceptance for an \"add a function\" goal is undecidable.",
    "新增函数后必须先运行最小单测或调用验证并确认通过；若 verify_pass=false，停止继续评审或返工，优先定位失败命令、输入输出和断言，修复实现后再提交验收。":
        "After adding a function, run the minimal unit test or call check first and confirm it passes; if verify_pass=false, stop further review or rework — locate the failing command, inputs/outputs and assertions, fix the implementation, then submit for acceptance.",
    "验证失败且问题列表为空时，评审必须补出复现命令、失败用例、函数签名和期望返回，否则不得继续修复。":
        "When verification fails with an empty issue list, review must produce the reproduce command, failing case, function signature and expected return — otherwise no further fixing.",
    "每轮修复须记录修改点、预期断言变化、错误输出差异；同一失败连续两轮未定位，就停止并要人工日志。":
        "Each fix round must record what changed, the expected assertion delta, and the error-output diff; if the same failure goes undiagnosed for two consecutive rounds, stop and request human logs.",
    "加函数任务先确认是否要求单测、导出路径、类型注解、异常和边界输入，并转成自动验收项。":
        "For add-a-function tasks, first confirm whether unit tests, export paths, type annotations, exceptions and boundary inputs are required, and turn them into automatic acceptance items.",
    "评审通过不能覆盖验证失败；验收以验证为准，高分评审必须解释不影响验证，否则按缺漏处理。":
        "A passing review never overrides a failed verification; acceptance follows verification. A high-scoring review must explain why it doesn't affect verification, or it counts as a gap.",
    "先区分代码缺陷与测试环境：检查依赖、入口、配置、断言版本；环境问题应标记阻塞并补环境，不反复改代码。":
        "First separate code defects from the test environment: check dependencies, entry points, configuration and assertion versions. Mark environment issues as blocked and fix the environment — don't keep changing code.",
    "code任务以verify为验收硬门槛；review_pass、高分数不能替代通过。连续两轮verify失败时停止打补丁，转查测试命令、环境、依赖和断言。":
        "For code tasks, verify is the hard acceptance gate; review_pass and high scores never substitute for passing. After two consecutive verify failures, stop patching and investigate the test command, environment, dependencies and assertions.",
    "verify失败但issues为空时，先复现并记录退出码、报错栈、失败断言与期望值；确认根因是代码、测试配置还是环境，再决定是否修改产物。":
        "When verify fails with an empty issue list, reproduce and record the exit code, error stack, failing assertion and expected value first; decide whether to touch the deliverable only after confirming the root cause is code, test config, or environment.",
    "当评审无问题但验证失败，评审结果需标记为不可信；要求评审必须引用失败用例或明确无法判定，否则重新评审或人工定位验证输出。":
        "When review finds no issues but verification fails, mark the review as untrustworthy; require the review to cite the failing case or explicitly state it can't judge — otherwise re-review or have a human read the verification output.",
    "加函数类任务开工前补齐函数名、参数、返回值、错误处理和验收测试；信息不足时先向用户确认，避免按猜测实现导致verify不通过。":
        "Before starting add-a-function work, complete the function name, params, return value, error handling and acceptance tests; confirm with the user when information is missing — implementing on guesses leads to verify failures.",
    "每轮修复前先本地跑verify或等价最小测试，确保失败原因已变化；若同一断言连续两轮未改善，不再增量修复，改为重新设计实现或接口。":
        "Before each fix round, run verify or an equivalent minimal test locally to confirm the failure cause has changed; if the same assertion hasn't improved for two rounds, stop incremental fixes and redesign the implementation or interface.",
    "代码任务目标过短或含混时，先补全输入、约束和验收标准；未澄清不得进入编码，避免产出无法验证。":
        "When a code task's goal is too short or vague, complete the inputs, constraints and acceptance criteria first; no coding before clarification, or the output can't be verified.",
    "即使评审通过或分数高，只要 verify_pass=false 就判未通过；修复后必须提交新的验证日志与失败归因。":
        "Even with a passing or high-scoring review, verify_pass=false means not accepted; after fixing, submit fresh verification logs and failure attribution.",
    "同一验证失败连续2轮未消除时，停止盲改，切换排查环境、测试夹具、依赖版本或验收命令；记录最小复现。":
        "When the same verification failure survives two consecutive rounds, stop blind changes and switch to investigating the environment, test fixtures, dependency versions or the acceptance command; record a minimal reproduction.",
    "触发 switch 前保存最后一次验证输出、改动差异和疑似原因；下次同类任务优先核对失败分类。":
        "Before triggering a switch, save the last verification output, the change diff and suspected causes; on the next similar task, check failure classifications first.",
    "修复循环若出现 review_pass 高且 verify_pass 连续失败，停止同策略自动修复，转人工分析验收命令、断言和失败日志后再提交。":
        "In a fix loop where review_pass stays high while verify_pass keeps failing, stop same-strategy auto-fixes — have a human analyze the acceptance command, assertions and failure logs before resubmitting.",
    "评审 issues 为空但整体 pass=false 时，以验收硬失败为唯一可信问题；提交前必须重跑 verify，并记录失败证据与根因。":
        "When review issues are empty but overall pass=false, treat the hard verification failure as the only trustworthy problem; rerun verify before submitting and record the failure evidence and root cause.",
    "设定、人物、环境信息不要铺在章首；先写事件异常或行动压力，必要背景压到钩子之后分散交代，避免开篇高密度不足导致过稿不稳。":
        "Don't dump setting, character and world info at the chapter top; open with an anomalous event or action pressure, and fold necessary background in after the hook — insufficient opening density destabilizes acceptance.",
    "每章第一屏必须抛出未解决冲突或新悬念；禁止以背景、情绪、日常铺垫开场。写完后检查前150字内是否有目标、威胁或选择，若没有就前置事件。":
        "Each chapter's first screen must raise an unresolved conflict or fresh suspense; no opening with background, mood or daily-life padding. After drafting, check the first 150 characters for a goal, threat or choice — if absent, move the event up.",
    "连载章节开头至少同时提供三类新信息：本章目标、主要障碍、失败代价；若只有状态说明，删减铺垫，把具体冲突事件提前到第一段。":
        "A serial chapter's opening must deliver at least three kinds of new information at once: this chapter's goal, the main obstacle, and the cost of failure. If it's only status description, cut the padding and move the concrete conflict into the first paragraph.",
    "下次每章正文前100字内必须出现新信息、冲突、悬念或目标；写作前先定章首钩子句，验收时检查读者是否能立即知道为何继续读。":
        "From now on, the first 100 characters of each chapter must contain new information, conflict, suspense or a goal; fix the chapter-opening hook line before drafting, and at acceptance check whether a reader can immediately tell why to keep reading.",
    "连载章节开头至少同时提供三类新信息：本章目标、主要障碍、失败代价；若只有状态说明，删减铺垫，把具体冲突事件提前到第一段。":
        "A serial chapter's opening must deliver at least three kinds of new information at once: this chapter's goal, the main obstacle, and the cost of failure. If it's only status description, cut the padding and move the concrete conflict into the first paragraph.",
    "决定性反转所依赖的条款、道具、关系，必须在更早章节落笔可见（至少一句具体描写），不能事后亮牌+主角自答带过；写大纲时为每个大爽点标注伏笔埋设，交稿前自查越度。":
        "Every clause, prop or relationship a decisive reversal relies on must appear concretely in earlier chapters (at least one specific description) — no last-minute reveals plus protagonist self-answering; tag the setup for each big payoff in the outline and self-check the planting before delivery.",
    "凡主角获取越权信息或关键证据（系统记录、录音、账目），必须当章或前文落实来源链（人脉、留底、委托调查），并让角色当场追问来源可信度；禁止匿名包裹、无人认领的U盘式天降证据。":
        "Whenever the protagonist obtains privileged information or key evidence (system records, recordings, ledgers), the source chain (contacts, paper trails, commissioned investigation) must be established in the same or an earlier chapter, and characters must question the source's reliability on the spot — no anonymous packages or nobody's-USB-stick sky-dropped evidence.",
    "关键爽点须预埋伏笔：在更早章节用具体物件、台词或场景完成铺垫（至少一次具体露出），不能临章现编；大纲阶段标注每个爽点的伏笔位置与回收章。":
        "Key payoffs need pre-planted setup: plant them via concrete objects, lines or scenes in earlier chapters (at least one specific appearance), never improvised in the payoff chapter; mark each payoff's setup location and payoff chapter in the outline stage.",
    "反击/打脸章节反派必须正面出场，写出其台词、失态、心理挣扎与代价；禁止只靠助理掉烟、旁人反应等旁证收尾。若该章反派客观无法出场，须在前一章给出其实时反应。":
        "In payback/face-slap chapters the villain must appear in person — write their dialogue, composure cracking, inner struggle and the price they pay; never close on sidelong evidence like an assistant dropping a cigarette or bystanders' reactions. If the villain objectively cannot appear that chapter, give their real-time reaction in the previous one.",
    "反击、收官章禁一路顺风：每章至少一个本章内生的新麻烦，胜利只给七分（留质疑、流程延迟或对手反扑），章末必再起一波；禁靠前文积累的情绪撑本章爆点。":
        "No smooth sailing in payback or finale chapters: each chapter needs at least one trouble born inside it, wins cap at seventy percent (leave doubt, procedural delay, or a counterattack), and a fresh wave must rise before the chapter ends. Never ride emotions accumulated in earlier chapters to carry this chapter's climax.",
    "同一 verify 结果连续失败两轮后，停止盲改，检查函数签名、调用路径、依赖、退出码和测试覆盖，确认验证器真的运行了目标代码。":
        "After the same verify result fails twice in a row, stop blind patching — check the function signature, call path, dependencies, exit code and test coverage, and confirm the verifier really ran the target code.",
    "同类加函数任务，只要verify_pass=false即不得因review_pass=true或高分判定通过；必须将verify结果作为最终验收依据。":
        "For add-a-function tasks of the same kind, verify_pass=false blocks acceptance regardless of review_pass or high scores; the verify result is the final acceptance basis.",
    "若verify连续2-3轮失败且无新证据，停止盲修，切换策略：要求补充测试/验收标准，或人工确认运行环境与函数入口。":
        "If verify fails 2–3 rounds in a row with no new evidence, stop blind fixes and change strategy: request additional tests/acceptance criteria, or have a human confirm the runtime environment and function entry.",
    "当评审无问题但验证失败，评审结果需标记为不可信；要求评审必须引用失败用例或明确无法判定，否则重新评审或人工定位验证输出。":
        "When review finds no issues but verification fails, mark the review as untrustworthy; require the review to cite the failing case or explicitly state it can't judge — otherwise re-review or have a human read the verification output.",
    "验证失败且问题列表为空时，评审必须补出复现命令、失败用例、函数签名和期望返回，否则不得继续修复。":
        "When verification fails with an empty issue list, review must produce the reproduce command, failing case, function signature and expected return — otherwise no further fixing.",
    "新增函数后本地跑编译/静态检查/单测，确认函数被导出、类型签名匹配、错误路径和返回值可被调用，再进入verify。":
        "After adding a function, run compilation/static checks/unit tests locally to confirm the function is exported, type signatures match, and error paths and return values are callable — only then enter verify.",
    "实现前明确函数名、参数、返回值和调用方式，并让评审与验证脚本使用同一口径；避免“只加函数”目标下验收标准不可判定。":
        "Before implementing, pin down the function name, params, return value and call convention, and make review and the verification script use the same yardstick — otherwise acceptance for an \"add a function\" goal is undecidable.",
    "每轮修复前先抓取verify日志/断言栈，明确期望签名、输入输出和依赖；无证据不改代码，禁止仅凭评审分数重复提交。":
        "Before each fix round, capture the verify log/assertion stack and clarify the expected signature, inputs/outputs and dependencies; no code changes without evidence, and never resubmit on review scores alone.",
    "每轮修复须记录修改点、预期断言变化、错误输出差异；同一失败连续两轮未定位，就停止并要人工日志。":
        "Each fix round must record what changed, the expected assertion delta, and the error-output diff; if the same failure goes undiagnosed for two consecutive rounds, stop and request human logs.",
    "多轮修复未变要停：同一 verify 结果连续失败两轮后，停止盲改，检查函数签名、调用路径、依赖、退出码和测试覆盖，确认验证器真的运行了目标代码。":
        "Stop when fixes change nothing: after the same verify result fails twice in a row, stop blind patching — check the function signature, call path, dependencies, exit code and test coverage, and confirm the verifier really ran the target code.",
    "新增函数补用例：为新增函数补最小可运行测试，至少覆盖正常调用和边界；若无测试入口，先增加验证脚本，保证验证器能执行目标函数。":
        "Add tests for new functions: add a minimal runnable test covering at least normal calls and boundaries; if there is no test entry, add a verification script first so the verifier can actually execute the target function.",
    "修复不重复空转：同一 verify 结果连续失败两轮后，停止盲改，检查函数签名、调用路径、依赖、退出码和测试覆盖，确认验证器真的运行了目标代码。":
        "No idle repair loops: after the same verify result fails twice in a row, stop blind patching — check the function signature, call path, dependencies, exit code and test coverage, and confirm the verifier really ran the target code.",
    "切换前保留失败诊断：触发 switch 前保存最后一次验证输出、改动差异和疑似原因；下次同类任务优先核对失败分类。":
        "Keep failure diagnostics before switching: before triggering a switch, save the last verification output, the change diff and suspected causes; on the next similar task, check failure classifications first.",
    "修复绑定失败证据：触发 switch 前保存最后一次验证输出、改动差异和疑似原因；下次同类任务优先核对失败分类。":
        "Capture bind-failure evidence: before triggering a switch, save the last verification output, the change diff and suspected causes; on the next similar task, check failure classifications first.",
    "修复前本地复跑：每轮修复前先本地跑verify或等价最小测试，确保失败原因已变化；若同一断言连续两轮未改善，不再增量修复，改为重新设计实现或接口。":
        "Reproduce locally before fixing: before each fix round, run verify or an equivalent minimal test locally to confirm the failure cause has changed; if the same assertion hasn't improved for two rounds, stop incremental fixes and redesign the implementation or interface.",
    "明确接口验收：实现前明确函数名、参数、返回值和调用方式，并让评审与验证脚本使用同一口径；避免“只加函数”目标下验收标准不可判定。":
        "Explicit interface acceptance: before implementing, pin down the function name, params, return value and call convention, and make review and the verification script use the same yardstick — otherwise acceptance for an \"add a function\" goal is undecidable.",
    "信息与证据必须有来源：凡主角获取越权信息或关键证据（系统记录、录音、账目），必须当章或前文落实来源链（人脉、留底、委托调查），并让角色当场追问来源可信度；禁止匿名包裹、无人认领的U盘式天降证据。":
        "Info and evidence must have sources: whenever the protagonist obtains privileged information or key evidence (system records, recordings, ledgers), the source chain must be established in the same or an earlier chapter, and characters must question its reliability on the spot — no sky-dropped evidence.",
    "验证失败先归因：verify失败但issues为空时，先复现并记录退出码、报错栈、失败断言与期望值；确认根因是代码、测试配置还是环境，再决定是否修改产物。":
        "Attribute verify failures first: when verify fails with an empty issue list, reproduce and record the exit code, error stack, failing assertion and expected value; decide whether to touch the deliverable only after confirming the root cause.",
    "评审不能替代验证：code任务以verify为验收硬门槛；review_pass、高分数不能替代通过。连续两轮verify失败时停止打补丁，转查测试命令、环境、依赖和断言。":
        "Review cannot replace verification: for code tasks verify is the hard acceptance gate; review_pass and high scores never substitute for passing. After two consecutive verify failures, stop patching and investigate the test command, environment, dependencies and assertions.",
    "环境缺陷误判代码：先区分代码缺陷与测试环境：检查依赖、入口、配置、断言版本；环境问题应标记阻塞并补环境，不反复改代码。":
        "Environment issues misread as code bugs: separate code defects from the test environment — check dependencies, entry points, configuration and assertion versions; mark environment issues as blocked and fix the environment.",
    "目标不可执行先澄清：代码任务目标过短或含混时，先补全输入、约束和验收标准；未澄清不得进入编码，避免产出无法验证。":
        "Clarify unactionable goals first: when a code task's goal is too short or vague, complete the inputs, constraints and acceptance criteria first; no coding before clarification.",
    "验证不通过不得通过：即使评审通过或分数高，只要 verify_pass=false 就判未通过；修复后必须提交新的验证日志与失败归因。":
        "Failed verification blocks acceptance: even with a passing or high-scoring review, verify_pass=false means not accepted; after fixing, submit fresh verification logs and failure attribution.",
    "多轮修复未变要停：同一验证失败连续2轮未消除时，停止盲改，切换排查环境、测试夹具、依赖版本或验收命令；记录最小复现。":
        "Stop when multiple fixes change nothing: when the same verification failure survives two consecutive rounds, stop blind changes and switch to investigating the environment, fixtures, dependency versions or the acceptance command; record a minimal reproduction.",
    "切换前保留失败诊断 / 修复绑定失败证据 / 修复不重复空转": "Keep failure diagnostics before switching / capture bind-failure evidence / no idle repair loops",
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


BLOCK_HEAD = "    // —— 2026-09-16 第六轮：内置教训 title+content 全量（真实数据副本核对） ——"


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
