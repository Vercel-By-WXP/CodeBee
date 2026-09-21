# -*- coding: utf-8 -*-
"""规划器：把用户目标自动拆解为有序子任务。

优先用「编排设置」直连 API 的编排者模型（统一规划/管理）；未配置或调用
失败时回落到最强可用 CLI 智能体；再失败退化为单步模板——计划永远可执行，
不阻塞任务。review 类引擎可让编排者产出写作大纲，拼进起草提示词。
"""
from __future__ import annotations

import json
import os
import re
import time

from . import knowledge, modelhub, runner, skills, usage

MAX_SUBTASKS = 4
DEFAULT_OUTLINE_TIMEOUT = 900   # 8 章大纲 + 经验包注入是重生成任务，300s 实测不够


def _append_log(log_path, text):
    """向步骤日志追加一段（编排者 API 尝试结果等子进程日志覆盖不到的内容）。

    编排者直连调用不走 run_process，没有自动落盘；不补写的话运行中步骤
    日志始终为空，失败原因（网关 502/解析失败）对外完全不可见。"""
    if not log_path:
        return
    try:
        with open(log_path, "a", encoding="utf-8") as f:
            f.write(text if text.endswith("\n") else text + "\n")
    except OSError:
        pass


def _log_streamer(log_path, min_chars=400, min_secs=0.8):
    """直连流式增量 → 步骤日志的节流写入器（原样拼接，不额外加换行）。

    编排者直连生成原本全程黑箱：日志只有一行标题，用户盯着它几分钟以为
    卡死。每个 SSE 分片都开文件写太碎，攒够 min_chars 或超过 min_secs 才
    刷一次；结束时必须调 cb.flush() 补上尾段。无 log_path 时为空操作
    （仍带 flush，调用方无需判空）。"""
    if not log_path:
        def nop(delta):
            pass
        nop.flush = lambda: None
        return nop
    st = {"buf": "", "at": time.time()}

    def _write():
        if st["buf"]:
            try:
                with open(log_path, "a", encoding="utf-8") as f:
                    f.write(st["buf"])
            except OSError:
                pass
            st["buf"] = ""
        st["at"] = time.time()

    def cb(delta):
        if not delta:
            return
        st["buf"] += delta
        if len(st["buf"]) >= min_chars or time.time() - st["at"] >= min_secs:
            _write()

    def flush():
        _write()
    cb.flush = flush
    return cb


def _outline_timeout():
    """大纲/规划的 CLI 兜底超时（秒）：env TUTTI_OUTLINE_TIMEOUT（秒）优先，
    其次设置 orchestrator.outline_timeout_s，缺省 900。"""
    try:
        raw = os.environ.get("TUTTI_OUTLINE_TIMEOUT")
        if raw:
            return max(60, min(3600, int(float(raw))))
    except Exception:
        pass
    try:
        from .settings_schema import get as ss_get, register_default_namespaces
        register_default_namespaces()
        v = int(ss_get("orchestrator", "outline_timeout_s") or 0)
        if v > 0:
            return v
    except Exception:
        pass
    return DEFAULT_OUTLINE_TIMEOUT


def _log_usage(source, role, task, res, agent=None, tool="", model="", provider="",
               provider_id=""):
    """规划链路的调用入台账（编排者直连 / CLI 规划）；失败不影响规划本身。"""
    try:
        usage.record(source=source, task_id=(task or {}).get("id", ""),
                     task_type=(task or {}).get("type", ""), role=role,
                     agent=(agent or {}).get("id", "") or "orchestrator",
                     agent_label=(agent or {}).get("label", "") or "编排者",
                     tool=tool or (agent or {}).get("kind", "") or "orchestrator",
                     model=model or ((res or {}).get("model") or ""),
                     provider=provider, ok=bool((res or {}).get("ok")),
                     duration_s=float(((res or {}).get("raw") or {}).get("duration") or 0.0),
                     cost_usd=float((res or {}).get("cost_usd") or 0.0),
                     usage=(res or {}).get("usage"))
        # 告警模块：编排者直连调用的成功/失败上报（provider_id 供「禁用厂商」定位）
        if provider:
            from . import health
            if (res or {}).get("ok"):
                health.report_success(provider)
            else:
                health.report_failure(provider, (res or {}).get("error") or "",
                                      model=model, provider_id=provider_id)
    except Exception:
        pass

CODE_PLAN_PROMPT = """你是技术负责人。请把下面的开发目标拆解为 __N__ 个以内、按顺序执行的子任务，
并判定任务难度和后续质量步骤。只输出一个 ```json 代码块，不要输出其他内容。JSON 结构：
{"difficulty": "easy 或 hard", "workflow": {"review_required": true, "max_repair_rounds": 0, "allow_switch": false}, "subtasks": [{"title": "简短标题", "detail": "具体要做什么，给执行工程师的直接指令", "files": ["涉及的文件路径"]}]}
难度判定：常规增删改查/小函数/格式调整 = easy；跨模块改动/架构调整/复杂算法/安全相关 = hard。
质量步骤判定：涉及安全、数据迁移、并发、权限、公共 API 或跨模块改动时必须评审；
有明确自动验收的低风险小改可不做模型评审。max_repair_rounds 取 0-2，只有复杂任务才建议换将。
子任务粒度要可独立验证；最后一个子任务必须包含整体联调/收尾。

## 先探索再计划（重要，借鉴 OpenSpec explore）
拆解之前先用你的读文件/搜索工具**实际查看工作目录**，找到目标相关的真实文件与函数，再据此拆解。
detail 必须点名真实存在的文件路径；files 列出该子任务会改动的文件（新建的写目标路径）。
**没探索过就不要凭想象编路径**——计划的可执行性取决于它对真实代码库的贴合度。
若工作目录为空（全新项目），files 写计划新建的文件路径。

## 开发目标
__GOAL__

## 背景与上下文
__CONTEXT__

## 验收命令（最终必须通过）
__VERIFY__"""

REVIEW_OUTLINE_PROMPT = """你是内容主编。请为下面的创作任务拟一份写作大纲（要点列表，3-8 条），
并按内容风险建议后续质量步骤。
只输出一个 ```json 代码块，不要输出其他内容。JSON 结构：
{"outline": ["要点1", "要点2", ...], "workflow": {"reviewers": 1, "review_rounds": 1}}
reviewers 取 1-2，review_rounds 取 1-3；研究报告、技术方案、长文和高发布门槛应增加，
普通邮件、短翻译、工作汇报等低风险短内容保持 1。

## 创作任务
__GOAL__

## 背景与上下文
__CONTEXT__"""

SERIAL_OUTLINE_PROMPT = """你是网文主编，熟悉签约平台（番茄/七猫/起点）的过稿标准。

__SKILLS__
请为下面的小说目标设计一份连载大纲：共 __N__ 章，每章约 __W__ 字。
只输出一个 ```json 代码块，不要输出其他内容。JSON 结构：
{"book_title": "书名", "chapters": [{"title": "章节标题", "beats": "本章剧情要点（50-120字：事件/冲突/推进）", "hook": "章末钩子（一句话）"}]}
硬性要求：
- 第 1-3 章是黄金三章：第 1 章开篇即冲突+人设立住，第 3 章末留大钩子；
- 每章有明确冲突与剧情推进，禁止水字数的日常流水账；
- 结局必须闭环（完本感），主角有成长弧光；
- 题材健康，无违规内容，符合平台签约调性。

## 小说目标
__GOAL__

## 背景与上下文
__CONTEXT__"""

SERIAL_CONTINUE_OUTLINE_PROMPT = """你是网文主编，熟悉签约平台（番茄/七猫/起点）的过稿标准。

__SKILLS__
这是一部长篇连载的续写：全书已完成前 __DONE__ 章，现在请规划第 __START__–__END__ 章
（本批共 __N__ 章，每章约 __W__ 字）。只输出一个 ```json 代码块，不要输出其他内容。JSON 结构：
{"book_title": "书名（与前文保持一致）", "chapters": [{"title": "章节标题", "beats": "本章剧情要点（50-120字：事件/冲突/推进）", "hook": "章末钩子（一句话）"}]}
硬性要求：
- 第 1 章直接衔接前文（见下方前情），不得跳线、不得重启设定、不得复述前文；
- 主线沿既有脉络推进，新冲突尽量从已埋伏笔中生长，人物性格与前文一致；
- 每章有明确冲突与剧情推进，禁止水字数的日常流水账；
- 题材健康，无违规内容，符合平台签约调性。

## 前情大纲（已完成章节，章号为全书章号）
__PREV_OUTLINE__

## 最新一章结尾（衔接锚点）
__PREV_TAIL__

## 小说目标
__GOAL__

## 背景与上下文
__CONTEXT__"""


def _norm_chapters(data, n):
    """规范化连载大纲输出；不合规返回 None。"""
    if not isinstance(data, dict):
        return None
    chs = data.get("chapters")
    if not isinstance(chs, list) or not chs:
        return None
    out = []
    for c in chs[:n]:
        if not isinstance(c, dict):
            continue
        title = str(c.get("title") or "").strip()
        beats = str(c.get("beats") or "").strip()
        if not title:
            continue
        out.append({"title": title[:60], "beats": beats[:500],
                    "hook": str(c.get("hook") or "").strip()[:200]})
    if len(out) < min(n, max(2, n // 2)):   # 至少给出半数章的大纲，否则视为失败（n=1 时至少 1 章）
        return None
    while len(out) < n:             # 缺的章补模板位
        out.append({"title": "第 %d 章" % (len(out) + 1), "beats": "按全书目标推进剧情",
                    "hook": ""})
    return {"book_title": str(data.get("book_title") or "").strip()[:40], "chapters": out[:n]}


def _prev_serial_story(task):
    """续写大纲的前情素材：沿 serial.continues 链收集已完成各章大纲（标全书章号）
    + 最新一章结尾（衔接锚点）+ 既有书名。返回 (前情文本, 已完成章数, 书名, 最新章结尾)。"""
    from . import store  # 惰性导入：store 不依赖 planner，避免测试环境导入顺序问题
    chain, seen, cur = [], set(), task
    while len(chain) < 10:
        cont = str((cur.get("serial") or {}).get("continues") or "")
        prev = store.get_task(cont) if cont else None
        if not prev or prev["id"] in seen:
            break
        seen.add(prev["id"])
        chain.append(prev)
        cur = prev
    lines, book_title = [], ""
    for prev in reversed(chain):            # 旧 → 新，前情按章号顺序铺开
        try:
            ps = int((prev.get("serial") or {}).get("start_chapter") or 1)
        except Exception:
            ps = 1
        outline = None
        for r in store.task_runs(prev["id"]):
            o = r.get("outline")
            if o and o.get("chapters") and not o.get("degraded"):
                outline = o
                break
        if not outline:
            continue
        book_title = book_title or str(outline.get("book_title") or "")
        for k, c in enumerate(outline["chapters"]):
            lines.append("第 %d 章《%s》：%s" % (ps + k, c.get("title", ""),
                                               str(c.get("beats") or "")[:120]))
    # 衔接锚点：工作目录里章号最大的章节文件结尾（续写批次共用同一目录）
    tail, best_i = "", 0
    try:
        from pathlib import Path
        for p in Path(task.get("workdir") or "").glob("chapter-*.md"):
            m = re.match(r"^chapter-(\d{1,4})\.md$", p.name)
            if m and int(m.group(1)) > best_i:
                best, best_i = p, int(m.group(1))
        if best_i:
            # GBK 兼容：CLI 子代理在中文 Windows 上可能把章稿落成 GBK
            b = best.read_bytes()
            try:
                tail = b.decode("utf-8")[-500:].strip()
            except UnicodeDecodeError:
                try:
                    tail = b.decode("gbk")[-500:].strip()
                except UnicodeDecodeError:
                    tail = b.decode("utf-8", "replace")[-500:].strip()
    except OSError:
        pass
    return "\n".join(lines), max(best_i, 0), book_title, tail


def make_serial_outline(task, author_agent=None, workdir=None, ev=None, log_path=None):
    """连载大纲：编排者 API 优先 → 作者 CLI → 模板。返回 {book_title, chapters:[{title,beats,hook}]}。

    续写批次（serial.start_chapter > 1）：改用续写大纲提示词，注入前情大纲与
    最新一章结尾；书名沿用前文，保证跨批次剧情/设定衔接。

    log_path：步骤日志绝对路径。编排者直连调用不经 run_process，需显式补写
    尝试结果，否则运行中点开步骤永远显示（无输出）、失败原因不可见。"""
    serial = task.get("serial") or {}
    n = int(serial.get("chapters") or 8)
    wpc = int(serial.get("words_per_chapter") or 2500)
    start = int(serial.get("start_chapter") or 1)
    sk_block, _ = skills.block_for(task)
    kb_block = knowledge.block_for(task)
    if kb_block:
        sk_block = (sk_block + "\n\n" + kb_block) if sk_block else kb_block
    prev_title = ""
    if start > 1:
        prev_lines, done, prev_title, prev_tail = _prev_serial_story(task)
        done = max(done, start - 1)
        prompt = (SERIAL_CONTINUE_OUTLINE_PROMPT
                  .replace("__SKILLS__", sk_block)
                  .replace("__DONE__", str(done))
                  .replace("__START__", str(start))
                  .replace("__END__", str(start + n - 1))
                  .replace("__N__", str(n)).replace("__W__", str(wpc))
                  .replace("__PREV_OUTLINE__", prev_lines or "（无大纲记录，请依据下方最新一章结尾与小说目标衔接）")
                  .replace("__PREV_TAIL__", prev_tail or "（无）")
                  .replace("__GOAL__", task["goal"])
                  .replace("__CONTEXT__", task.get("context") or "（无）"))
    else:
        prompt = (SERIAL_OUTLINE_PROMPT.replace("__SKILLS__", sk_block)
                  .replace("__N__", str(n)).replace("__W__", str(wpc))
                  .replace("__GOAL__", task["goal"])
                  .replace("__CONTEXT__", task.get("context") or "（无）"))
    # 续写批次打上全书章号标记；书名缺省时沿用前文
    def _mark(o):
        if o and start > 1:
            o["start_chapter"] = start
            if not o.get("book_title"):
                o["book_title"] = prev_title
        return o

    orch = _orchestrator()
    orch_errors = []   # 每次编排者尝试的真实失败原因，落步骤日志 + degraded_reason
    if orch:
        prov, model = orch
        label = "%s · %s" % (prov.get("name", prov["id"]), model)
        _append_log(log_path, "===== 编排者大纲（%s）=====" % label)
        # 网关 502/503 是常见瞬时故障，编排者重试一次再放弃（实测公司网关连续 8h 502）
        for _attempt in (1, 2):
            # glm-5.3 等推理模型的"思考"就吃掉数千 token：max_tokens 给足，
            # 否则 stop_reason=max_tokens、正文为空（实测 2048 全被思考吞掉）
            # 流式回调把增量实时写进步骤日志，直连生成不再是一行标题的黑箱
            cb = _log_streamer(log_path)
            # §07 T2.2 幂等精确缓存（TTL 1h）：断点续跑/重跑同任务时大纲
            # prompt 逐字节相同，命中省一次全量规划调用。第 2 次尝试故意
            # 旁路缓存——首试 ok 但 JSON 不合法时，重试要的是「再抽一次样
            # 本」而不是同一份坏文本。
            res = modelhub.chat(prov["id"], model, prompt,
                                max_tokens=16000, timeout=300, on_delta=cb,
                                reasoning_effort=_reasoning_effort(task),
                                cache_ttl=3600 if _attempt == 1 else 0)
            cb.flush()
            _log_usage("outline", "outline", task, res, model=model,
                       provider=prov.get("name", prov.get("id", "")),
                       provider_id=prov.get("id", ""))
            if res["ok"]:
                _append_log(log_path, "尝试 %d：返回 %d tokens，解析 JSON 中…"
                            % (_attempt, res.get("tokens") or 0))
            else:
                _append_log(log_path, "尝试 %d 失败：%s"
                            % (_attempt, (res.get("error") or "未知错误")[:300]))
            orch_errors.append(str(res.get("error") or "返回内容无法解析为大纲"))
            data = runner.extract_json(res.get("text") or "") if res["ok"] else None
            outline = _norm_chapters(data, n)
            if outline:
                outline["source"] = "编排者(%s)" % label
                return _mark(outline)
        _append_log(log_path, "编排者两次尝试均未产出可用大纲，回退作者 CLI…")
    reason_tail = ("；".join(dict.fromkeys(orch_errors))[:200]) if orch_errors \
        else "编排者未配置/不可用"

    if author_agent and author_agent.get("mode") == "real":
        # 8 章大纲 + 经验包注入是重生成任务，300s 实测不够（claude CLI 必超时）；
        # 超时可经 env TUTTI_OUTLINE_TIMEOUT 或设置 orchestrator.outline_timeout_s 调整
        _append_log(log_path, "===== 作者 CLI（%s）=====" % author_agent.get("id", "?"))
        res = runner.run_agent(modelhub.bind_agent(author_agent), prompt,
                               workdir=workdir or task.get("workdir"), readonly=True,
                               timeout=_outline_timeout(), cancel_event=ev,
                               log_path=log_path)
        _log_usage("outline", "outline", task, res, agent=author_agent)
        if not res["ok"]:
            _append_log(log_path, "作者 CLI 失败：%s" % (res.get("error") or "")[:300])
            reason_tail = (res.get("error") or reason_tail)[:200]
        outline = _norm_chapters(runner.extract_json(res.get("text") or ""), n)
        if outline:
            outline["source"] = "llm(%s)" % author_agent["id"]
            return _mark(outline)
    elif author_agent and author_agent.get("mode") == "mock":
        # mock：确定性模板大纲
        pass

    # 兜底模板只有章号、没有任何情节设计，据此写出的全书等于空转。
    # 真实任务标记 degraded 让上层中止并等续跑重试；mock 测试按确定性模板继续。
    chapters = [{"title": "第 %d 章" % (start + i), "beats": "按全书目标推进剧情，保持冲突与钩子",
                 "hook": ""} for i in range(n)]
    out = {"book_title": prev_title, "chapters": chapters, "source": "template"}
    if author_agent and author_agent.get("mode") != "mock":
        out["degraded"] = True
        out["degraded_reason"] = "编排者/作者模型均未返回可用大纲（%s）" % reason_tail
    return _mark(out)


def _norm_subtasks(data):
    """规范化 LLM 计划输出；不合规返回 None。

    files 字段（OpenSpec explore 借鉴）：计划锚定的真实文件清单，透传给执行步
    让实现者知道改动面（缺失/非法时留空，向后兼容旧计划）。"""
    if not isinstance(data, dict):
        return None
    subs = data.get("subtasks")
    if not isinstance(subs, list) or not subs:
        return None
    steps = []
    for s in subs[:MAX_SUBTASKS]:
        if not isinstance(s, dict):
            continue
        title = str(s.get("title") or "").strip()
        detail = str(s.get("detail") or "").strip()
        if not title:
            continue
        step = {"title": title[:60], "detail": detail[:1500]}
        files = s.get("files")
        if isinstance(files, list):
            clean = [str(f).strip()[:200] for f in files if str(f).strip()][:10]
            if clean:
                step["files"] = clean
        steps.append(step)
    return steps or None


def _plan_difficulty(data):
    d = str((data or {}).get("difficulty") or "").lower()
    return d if d in ("easy", "hard") else None


def _plan_workflow(data):
    """规范化规划器给出的后续步骤建议；最终安全下限由任务编译器裁决。"""
    raw = (data or {}).get("workflow")
    if not isinstance(raw, dict):
        return None
    out = {}
    if isinstance(raw.get("review_required"), bool):
        out["review_required"] = raw["review_required"]
    try:
        if "max_repair_rounds" in raw:
            out["max_repair_rounds"] = max(0, min(2, int(raw["max_repair_rounds"])))
    except (TypeError, ValueError):
        pass
    if isinstance(raw.get("allow_switch"), bool):
        out["allow_switch"] = raw["allow_switch"]
    return out or None


def _orchestrator():
    """编排者可用时返回 (provider, model)，否则 None。"""
    try:
        return modelhub.resolve_orchestrator()
    except Exception:
        return None


def _reasoning_effort(task):
    """把任务级思考程度映射为直连编排者支持的 reasoning 参数。"""
    task = task if isinstance(task, dict) else {}
    selected = str(task.get("thinking") or "standard").strip().lower()
    if selected in ("low", "standard", "high"):
        return {"low": "low", "standard": "medium", "high": "high"}[selected]
    mode = str(task.get("mode") or "auto").lower()
    difficulty = str(task.get("difficulty") or "auto").lower()
    ttype = str(task.get("type") or "").lower()
    try:
        threshold = float(task.get("threshold") or 7.0)
    except (TypeError, ValueError):
        threshold = 7.0
    if mode == "fast" or difficulty == "easy" or threshold <= 6.0:
        return "low"
    if (mode == "expert" or difficulty == "hard" or threshold >= 8.5
            or ttype in ("research", "tech_proposal", "serial_novel")):
        return "high"
    return "medium"


def make_code_plan(task, planner_agent, workdir, ev=None, resume=None, log_path=None):
    """代码任务计划：编排者 API 优先 → CLI 智能体 → 单步模板。"""
    orch = _orchestrator()
    if orch:
        plan = _orch_code_plan(task, orch[0], orch[1], log_path=log_path)
        if plan:
            return plan
    if planner_agent is None:
        return _fallback_code_plan(task, "（无可用智能体）")
    if planner_agent.get("mode") == "mock":
        return _fallback_code_plan(task, "mock 模式：单步模板")
    prompt = (CODE_PLAN_PROMPT.replace("__N__", str(MAX_SUBTASKS))
              .replace("__GOAL__", task["goal"])
              .replace("__CONTEXT__", task.get("context") or "（无）")
              .replace("__VERIFY__", task.get("verify_command") or "（未配置）"))
    res = runner.run_agent(planner_agent, prompt, workdir=workdir, readonly=True,
                           timeout=300, cancel_event=ev, resume=resume,
                           log_path=log_path)
    _log_usage("plan", "plan", task, res, agent=planner_agent)
    data = runner.extract_json(res.get("text") or "")
    steps = _norm_subtasks(data)
    if steps:
        return {"source": "llm(%s)" % planner_agent["id"], "steps": steps,
                "difficulty": _plan_difficulty(data),
                "workflow": _plan_workflow(data)}
    return _fallback_code_plan(task, "LLM 计划解析失败，退化为单步模板")


def _fallback_code_plan(task, note):
    return {"source": "template", "note": note,
            "steps": [{"title": "实现任务", "detail": task["goal"]}]}


def _orch_code_plan(task, prov, model, log_path=None):
    cb = _log_streamer(log_path)
    # §07 T2.2：代码计划同任务同 prompt（断点续跑/重试），TTL 1h 精确缓存
    res = modelhub.chat(prov["id"], model,
                        (CODE_PLAN_PROMPT.replace("__N__", str(MAX_SUBTASKS))
                         .replace("__GOAL__", task["goal"])
                         .replace("__CONTEXT__", task.get("context") or "（无）")
                         .replace("__VERIFY__", task.get("verify_command") or "（未配置）")),
                        max_tokens=8000, timeout=300, on_delta=cb,
                        reasoning_effort=_reasoning_effort(task),
                        cache_ttl=3600)
    cb.flush()
    _log_usage("plan", "plan", task, res, model=model,
               provider=prov.get("name", prov.get("id", "")))
    if not res["ok"]:
        _append_log(log_path, "编排者计划（%s · %s）失败：%s" % (
            prov.get("name", prov["id"]), model, (res.get("error") or "未知错误")[:300]))
        return None
    data = runner.extract_json(res.get("text") or "")
    steps = _norm_subtasks(data)
    if not steps:
        _append_log(log_path, "编排者计划返回内容无法解析为子任务，回退 CLI")
        return None
    return {"source": "编排者(%s · %s)" % (prov.get("name", prov["id"]), model),
            "steps": steps, "difficulty": _plan_difficulty(data),
            "workflow": _plan_workflow(data)}


def make_review_outline(task):
    """review 类任务：编排者产出写作大纲（失败返回 None，起草退回无大纲）。"""
    orch = _orchestrator()
    if not orch:
        return None
    prov, model = orch
    res = modelhub.chat(prov["id"], model,
                        (REVIEW_OUTLINE_PROMPT
                         .replace("__GOAL__", task["goal"])
                         .replace("__CONTEXT__", task.get("context") or "（无）")),
                        max_tokens=8000, timeout=300,
                        reasoning_effort=_reasoning_effort(task),
                        cache_ttl=3600)   # §07 T2.2：同任务重试幂等，TTL 1h
    _log_usage("outline", "outline", task, res, model=model,
               provider=prov.get("name", prov.get("id", "")))
    if not res["ok"]:
        return None
    data = runner.extract_json(res.get("text") or "")
    outline = data.get("outline") if isinstance(data, dict) else None
    if not isinstance(outline, list):
        return None
    items = [str(x).strip()[:120] for x in outline if str(x).strip()][:8]
    if not items:
        return None
    workflow = data.get("workflow") if isinstance(data.get("workflow"), dict) else {}
    try:
        reviewers = max(1, min(2, int(workflow.get("reviewers") or 1)))
    except (TypeError, ValueError):
        reviewers = 1
    try:
        review_rounds = max(1, min(3, int(workflow.get("review_rounds") or 1)))
    except (TypeError, ValueError):
        review_rounds = 1
    return {"source": "编排者(%s · %s)" % (prov.get("name", prov["id"]), model),
            "items": items,
            "workflow": {"reviewers": reviewers, "review_rounds": review_rounds}}


def make_novel_plan(task, author, critics):
    dims = "、".join(task.get("rubric") or ["情节", "人物", "文笔", "节奏", "吸引力"])
    return {
        "source": "template",
        "steps": [
            {"title": "起草", "detail": "作者 %s 按目标撰写稿件" % author.get("label", author["id"])},
            {"title": "多维度评审", "detail": "评审组 %s 按 %s 打分，每维度阈值 %.1f"
                % ("、".join(a.get("label", a["id"]) for a in critics), dims, task.get("threshold", 7.0))},
            {"title": "修订循环", "detail": "任一维度低于阈值 → 汇总 major 意见回炉，至多 %d 轮"
                % task.get("rounds", 2)},
            {"title": "发布门禁", "detail": "所有维度达标才标记可发布，输出评审报告"},
        ],
    }
