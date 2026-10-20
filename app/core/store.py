# -*- coding: utf-8 -*-
"""存储层：任务与运行（含管理操作）全部落盘，可回放。

目录约定：
  data/tasks/<task_id>.json
  data/runs/<run_id>/run.json
  data/runs/<run_id>/steps/<NN>-<role>-<agent>.log
  data/runs/<run_id>/report.md
"""
from __future__ import annotations

import json
import os
import re
import secrets
import shutil
import threading
import time
from concurrent import futures
from pathlib import Path

from . import artifacts, atomic, audit, paths, runner, tracing, volumes

LOCK = threading.RLock()
_TASKS = {}
_RUNS = {}

# 评审文案里的章号提及（「第22章」「第26—28章」）——与 pipeline._CH_MENTION_RE
# 保持同一口径（store 不得反向 import pipeline，只能各存一份；改动须两处同步）。
_CH_MENTION_RE = re.compile(r"第\s*(\d{1,4})\s*(?:[—–\-~至到]\s*(\d{1,4})\s*)?章")

# 流式实时落盘的节流表：(run_id, 步骤号) -> 上次落盘时刻。步骤收尾即清，
# 长跑也不会无限增长（finish_step 里调 clear_stream_state）。
_STREAM_LOCK = threading.Lock()
_STREAM_TS = {}
LIVE_TEXT_MAX = 20000   # 单步骤实时思考/正文的保留上限（与 builtin_agent 同口径）

DEFAULT_TASK_TIMEOUT_S = 3600
MIN_TASK_TIMEOUT_S = 1
MAX_TASK_TIMEOUT_S = 7 * 24 * 3600
REVIEW_TASK_TIMEOUT_S = 7200
LONG_TASK_TIMEOUT_S = 14400
SERIAL_EXTRA_CHAPTER_S = 1800

# ---------------------------------------------------------------- 状态版本（SSE 事件驱动）
# 任何落盘写都算状态变化：SSE 连接等版本号变化才构建/推送全量状态，
# 空闲时连接零开销（此前每连接每 0.8s 盲构建全量 payload，多端并发会放大成假死）
_VER_CV = threading.Condition()
_STATE_VER = 1


def bump_state():
    global _STATE_VER
    with _VER_CV:
        _STATE_VER += 1
        _VER_CV.notify_all()


def state_version():
    return _STATE_VER


def wait_state_change(last_ver, timeout):
    """阻塞直到版本号超过 last_ver 或超时。返回当前版本号。"""
    with _VER_CV:
        if _STATE_VER != last_ver:
            return _STATE_VER
        _VER_CV.wait(timeout)
        return _STATE_VER


def _new_id(prefix):
    stamp = time.strftime("%Y%m%d-%H%M%S")
    # 10k random suffixes are not unique under a burst of task/run creation.
    # Reserve against both indexes and disk so a collision cannot silently
    # overwrite a queued run and make one of several submissions disappear.
    with LOCK:
        for _ in range(100):
            candidate = "%s-%s-%04d" % (prefix, stamp, secrets.randbelow(10000))
            if candidate in _TASKS or candidate in _RUNS:
                continue
            if (paths.TASKS_DIR / (candidate + ".json")).exists():
                continue
            if (paths.RUNS_DIR / candidate).exists():
                continue
            return candidate
    raise RuntimeError("无法生成唯一记录 ID")


def _safe_name(s):
    return re.sub(r"[^0-9A-Za-z_-]+", "-", str(s))[:40].strip("-") or "x"


def _task_timeout_s(value=None):
    """Resolve one bounded wall-clock budget for a task."""
    raw = value
    if raw in (None, ""):
        raw = os.environ.get("TUTTI_TASK_TIMEOUT_S", DEFAULT_TASK_TIMEOUT_S)
    try:
        seconds = int(float(raw))
    except (TypeError, ValueError):
        seconds = DEFAULT_TASK_TIMEOUT_S
    return max(MIN_TASK_TIMEOUT_S, min(MAX_TASK_TIMEOUT_S, seconds))


def _norm_drama(value):
    """Normalize optional short-drama season constraints from task creation."""
    if not isinstance(value, dict):
        return None
    out = {}
    for key, low, high in (("episodes", 1, 200),
                           ("seconds_per_episode", 15, 600)):
        raw = value.get(key)
        if raw in (None, ""):
            continue
        try:
            out[key] = max(low, min(high, int(raw)))
        except (TypeError, ValueError):
            continue
    return out or None


def _default_timeout_s(task):
    """Choose a bounded default from the amount of work, not one hour for all."""
    env_value = os.environ.get("TUTTI_TASK_TIMEOUT_S")
    if env_value not in (None, ""):
        return _task_timeout_s(env_value)
    serial = task.get("serial")
    if isinstance(serial, dict) and serial.get("chapters"):
        try:
            chapters = max(1, min(20, int(serial["chapters"])))
        except (TypeError, ValueError):
            chapters = 8
        return min(MAX_TASK_TIMEOUT_S,
                   LONG_TASK_TIMEOUT_S + max(0, chapters - 8) * SERIAL_EXTRA_CHAPTER_S)
    if task.get("engine") == "code":
        return LONG_TASK_TIMEOUT_S
    if task.get("engine") == "review":
        return REVIEW_TASK_TIMEOUT_S
    return DEFAULT_TASK_TIMEOUT_S


def _norm_early_finish(value):
    """Normalize the durable early-finish plan carried by a serial batch."""
    if not isinstance(value, dict):
        return None
    try:
        target = int(value.get("target_chapter") or 0)
    except (TypeError, ValueError):
        return None
    # Persisted payloads can come from older clients or manual edits. Do not
    # silently rewrite an invalid target into a different ending chapter;
    # dropping the malformed plan lets the caller repair it explicitly.
    if target < 1 or target > 500:
        return None
    out = {"target_chapter": target}
    source = str(value.get("source_task") or "").strip()
    if re.match(r"^[A-Za-z][0-9A-Za-z_-]*$", source):
        out["source_task"] = source
    reason = str(value.get("reason") or "").strip()
    if reason:
        out["reason"] = reason[:240]
    requested_at = str(value.get("requested_at") or "").strip()
    if requested_at:
        out["requested_at"] = requested_at[:32]
    return out


# ---------------------------------------------------------------- 任务

def create_task(payload):
    """校验并创建任务。payload 至少含 type/goal/workdir。

    type 必须是 flows.py 里的有效流程 ID；流程参数（引擎/维度/阈值/轮数/产出
    文件/提示词覆盖）在创建时固化到任务上，之后修改流程定义不影响已建任务。
    """
    if not isinstance(payload, dict):
        raise ValueError("任务参数必须是 JSON 对象")

    def _text(value, field):
        if value is None:
            return ""
        if not isinstance(value, str):
            raise ValueError("%s 必须是文本" % field)
        return value.strip()

    from . import flows as flows_mod
    flow = flows_mod.get_flow(payload.get("type"))
    if flow is None:
        raise ValueError("未知任务类型：%s（可选：%s）"
                         % (payload.get("type"), "、".join(f["id"] for f in flows_mod.list_flows())))
    expert = None
    expert_id = str(payload.get("expert_id") or "").strip()
    if expert_id:
        from . import experts as experts_mod
        expert = experts_mod.get_expert(expert_id)
        if expert is None:
            raise ValueError("未知专家模板：%s" % expert_id)
        if expert.get("flow_id") != flow.get("id"):
            raise ValueError("专家模板 %s 绑定的流程与任务类型不一致" % expert_id)
    title = _text(payload.get("title"), "title")
    goal = _text(payload.get("goal"), "goal")
    workdir = _text(payload.get("workdir"), "workdir")
    if not goal:
        # 扫榜选材的菜单承诺「方向可留空，默认分析总榜热门题材」——
        # paihang.rank_scan_prompt 与 pipeline 两侧均已支持空 goal，这里放行；
        # 其余类型目标必填不变。
        if payload.get("type") == "rank_scan":
            title = title or flow.get("name") or "扫榜选材"
        else:
            raise ValueError("目标描述不能为空")
    else:
        title = title or goal.splitlines()[0][:30]  # 标题可省略，自动取目标首行
    if not workdir:
        # 未指定目录 → 用「默认保存路径」（设置里可改；内置回落 <数据目录>/workspace）。
        # 默认路径允许自动创建；用户显式给的目录仍必须已存在。
        from . import settings as settings_mod
        workdir = settings_mod.default_workdir()
        wd = Path(workdir)
        try:
            wd.mkdir(parents=True, exist_ok=True)
        except Exception as e:
            raise ValueError("默认保存路径不可用: %s（%s）" % (workdir, e))
    wd = Path(workdir)
    if not wd.is_absolute():
        raise ValueError("工作目录必须是绝对路径")
    if not wd.is_dir():
        raise ValueError("工作目录不存在: %s" % workdir)
    mode = payload.get("mode")
    if mode not in ("auto", "fast", "expert", "manual"):
        mode = "manual" if payload.get("implementer") else "auto"
    difficulty = payload.get("difficulty")
    if difficulty not in ("auto", "easy", "hard", "default"):
        difficulty = "auto"
    task = {
        "id": _new_id("t"), "type": flow["id"], "engine": flow["engine"],
        "title": title, "goal": goal,
        "context": _text(payload.get("context"), "context"),
        "workdir": str(wd),
        "mode": mode,
        "difficulty": difficulty,
        "implementer": payload.get("implementer") or "",
        "attachments": [],
        "created_at": time.strftime("%Y-%m-%d %H:%M:%S"),
        "created_at_ns": time.time_ns(),
        "status": "created",
    }
    if flow["id"] == "short_drama":
        drama = _norm_drama(payload.get("drama"))
        if drama:
            task["drama"] = drama
    if expert:
        from . import experts as experts_mod
        task["expert_id"] = expert["id"]
        task["expert_snapshot"] = experts_mod.snapshot(expert)
    plan_mode = payload.get("plan_mode", False)
    if not isinstance(plan_mode, bool):
        raise ValueError("plan_mode 必须是布尔值")
    task["plan_mode"] = plan_mode
    # Every new task must persist one verified execution policy. If policy
    # normalization fails, do not silently replace it with a broader default.
    try:
        from . import policy
        task["sandbox"] = policy.normalize_sandbox(payload.get("sandbox"), wd)
    except Exception as exc:
        raise ValueError("沙箱策略无法验证，任务未创建") from exc
    thinking = str(payload.get("thinking") or "standard").strip().lower()
    task["thinking"] = thinking if thinking in ("auto", "low", "standard", "high") else "auto"
    # 流程修订出处（借鉴 WorkDSH ADR-0010）：创建时钉住当时流程定义的指纹。
    # 固化参数已保证「改流程不影响本任务」，digest 再补上可对账的一面——
    # 续跑/重试时若流程已改，flows.flow_drift 能给出明确诊断而非静默换新。
    flow_content_sha = flows_mod.flow_content_sha256(flow)
    task["flow_snapshot"] = {"id": flow["id"], "name": (flow.get("name") or "")[:40],
                             "engine": flow["engine"],
                             "edited": bool(flow.get("edited")),
                             "digest": flow_content_sha[:16],
                             "revision_id": "flowrev-" + flow_content_sha[:16],
                             "content_sha256": flow_content_sha}
    # 参数修订号（借鉴 WorkDSH 契约纪律 expectedRevision）：写接口据此做过期
    # 写入检测。老任务无此键按 1 处理；不带 expected_rev 的调用不受影响。
    task["rev"] = 1
    # Durable task contract: acceptance criteria and approval requirement are
    # persisted outside the task JSON so evidence/audit history survives task
    # parameter edits and can be consumed by API/MCP clients independently.
    raw_criteria = payload.get("acceptance_criteria")
    from . import acceptance
    task["acceptance_thresholds"] = acceptance.normalize_thresholds(
        payload.get("acceptance_thresholds", {}))
    if raw_criteria not in (None, "") and not isinstance(raw_criteria, list):
        raise ValueError("acceptance_criteria 必须是列表")
    task["acceptance_criteria"] = [str(x).strip()[:500] for x in
                                    (raw_criteria or []) if str(x).strip()][:40]
    # Keep the user's hard requirements separate from free-form context so
    # every execution path (including the fast path) can audit them.
    try:
        from . import intent_contract
        task["intent_contract"] = intent_contract.build(
            task["goal"], intent_contract.trusted_context(task["context"]),
            task["acceptance_criteria"])
    except Exception:
        task["intent_contract"] = {"version": 1, "primary_goal": task["goal"],
                                    "must": [task["goal"]] if task["goal"] else [],
                                    "must_not": [], "deliverables": [],
                                    "raw_excerpt": task["goal"]}
    approval_required = payload.get("approval_required", False)
    if not isinstance(approval_required, bool):
        raise ValueError("approval_required 必须是布尔值")
    task["approval_required"] = approval_required
    if flow["engine"] == "direct":
        task["direct_provider_id"] = _text(payload.get("direct_provider_id"),
                                            "direct_provider_id")[:80]
        task["direct_model"] = _text(payload.get("direct_model"), "direct_model")[:160]
        if task["direct_model"] and not task["direct_provider_id"]:
            raise ValueError("指定对话模型时必须同时指定厂商")
        # 手动指定执行 CLI（2026-09-23）：优先级高于模型绑定——运行时走该 CLI
        # 的会话链而非内置智能体。存 id（如 codex-cli），可用性在运行时校验
        da = _text(payload.get("direct_agent"), "direct_agent")[:80]
        if da:
            task["direct_agent"] = da
    # 代码版本：仅当引用合法才固化（流水线执行前据此检出任务分支）
    from . import gitmod
    git_rev = _text(payload.get("git_rev"), "git_rev")
    if git_rev:
        # 前端下拉值带 kind 前缀（branch:main / tag:v1 / commit:abc），此处归一为纯 rev；
        # git 分支/标签名本身允许含冒号（罕见），前缀剥离只认这三种已知 kind
        git_rev = re.sub(r"^(branch|tag|commit):", "", git_rev)
        if not gitmod.valid_rev(git_rev):
            raise ValueError("非法的代码版本引用：%s" % git_rev[:40])
        task["git_rev"] = git_rev
    if flow["engine"] == "code":
        task["verify_command"] = _text(payload.get("verify_command"), "verify_command")
    elif flow["engine"] == "direct":
        pass  # 直连任务：无验证命令也无评审参数，目标+附件即全部输入
    else:
        ms = _text(payload.get("manuscript") or flow.get("manuscript") or "manuscript.md",
                   "manuscript")
        ms = re.sub(r"[\\/]", "_", ms)  # 只允许工作目录内的相对文件名
        ms = re.sub(r"\.{2,}", "_", ms).lstrip(".")  # 顺带清掉残留的 ..
        task["manuscript"] = ms
        try:
            task["rounds"] = max(1, min(5, int(payload.get("rounds") or flow.get("rounds") or 2)))
        except Exception:
            task["rounds"] = 2
        try:
            # Best-of-N 赛马候选数（非连载单稿；连载走 serial.variants 的同章赛马）
            task["best_of"] = max(1, min(3, int(payload.get("best_of") or flow.get("best_of") or 1)))
        except Exception:
            task["best_of"] = 1
        try:
            task["threshold"] = max(1.0, min(10.0,
                                             float(payload.get("threshold") or flow.get("threshold") or 7.0)))
        except Exception:
            task["threshold"] = 7.0
        dims = payload.get("rubric")
        if not (isinstance(dims, list) and dims):
            dims = flow.get("rubric")
        if isinstance(dims, list) and dims:
            task["rubric"] = [str(d).strip() for d in dims if str(d).strip()][:8]
        for key in ("draft_prompt", "critique_prompt"):  # 自定义流程的提示词覆盖
            if flow.get(key):
                task[key] = flow[key]
        # 连载模式：逐章起草/评审/修订（任务级 serial 覆盖流程默认）。
        # payload 中显式传 null 表示关闭流程默认连载；字段缺失才沿用流程默认，
        # 这样前端把章节清空时不会被 serial_novel 的默认值悄悄重新打开。
        serial_unset = object()
        serial_value = payload.get("serial", serial_unset)
        if serial_value is None or (isinstance(serial_value, dict) and
                                    serial_value.get("enabled") is False):
            serial = None
        elif isinstance(serial_value, dict):
            serial = serial_value
        else:
            serial = flow.get("serial")
        if isinstance(serial, dict) and serial.get("chapters"):
            try:
                s: dict[str, object] = {
                    # 续写批次允许只续 1 章，下限放宽到 1（全新连载仍由前端约束 ≥2）
                    "chapters": max(1, min(20, int(serial["chapters"]))),
                    "words_per_chapter": max(500, min(8000,
                                                      int(serial.get("words_per_chapter") or 2500))),
                }
            except Exception:
                s = {}
            if s:
                if isinstance(serial.get("opening_gate"), bool):
                    s["opening_gate"] = serial["opening_gate"]
                early_finish = _norm_early_finish(serial.get("early_finish"))
                if early_finish:
                    s["early_finish"] = early_finish
                # 续写：从 start_chapter 章接着写（章节文件/步骤/评分都用全书章号）；
                # continues 指向上一批任务，生成大纲时回溯前情保证剧情衔接
                try:
                    sc = max(1, min(500, int(serial.get("start_chapter") or 1)))
                except Exception:
                    sc = 1
                if sc > 1:
                    s["start_chapter"] = sc
                cont = str(serial.get("continues") or "").strip()
                if re.match(r"^[A-Za-z][0-9A-Za-z_-]*$", cont) and get_task(cont):
                    s["continues"] = cont
                # 同章多稿赛马（dev-3.0）：1=关；2-3 = 每章并行起草 N 稿评审择优
                try:
                    v = max(1, min(3, int(serial.get("variants") or 1)))
                except Exception:
                    v = 1
                if v > 1:
                    s["variants"] = v
                # 多线剧情推演（inkos 借鉴 2026-09-23）：2-3 = 写章前生成
                # N 条分支节拍计划择优注入（计划级赛马，省 prose 级开销）；
                # 越界钳到 3（与 variants 同口径：上限仍可用，1=不启用不落键）
                try:
                    br = max(1, min(3, int(serial.get("branches") or 1)))
                except Exception:
                    br = 1
                if br > 1:
                    s["branches"] = br
                # 分卷：显式卷表优先（新建任务时用户明确给出的分卷信息），
                # 其次每卷章数等长切卷。卷边界一律由 volumes 模块从「章号」
                # 确定性推导——续写批次沿用同一份卷表/每卷章数即自动接上同一卷，
                # 不会因分批而错位。
                vol_spec = volumes.norm_spec(serial.get("volumes"))
                if not vol_spec:
                    # 否则从文字里识别：表单「指定卷结构」框 > 任务目标 > 上下文。
                    # 保守识别——认不出返回空，绝不把普通文字误判成分卷。
                    vol_spec = volumes.parse_spec_text(serial.get("volumes_text")) \
                        or volumes.parse_spec_text(goal) \
                        or volumes.parse_spec_text(task.get("context") or "")
                if vol_spec:
                    s["volumes"] = vol_spec
                vper = volumes.norm_per(serial.get("volume_chapters"))
                if vper:
                    s["volume_chapters"] = vper
                task["serial"] = s
    raw_timeout = payload.get("timeout_s")
    task["timeout_s"] = (_task_timeout_s(raw_timeout)
                         if raw_timeout not in (None, "") else _default_timeout_s(task))
    critics = payload.get("critics")
    if isinstance(critics, list) and critics:
        task["critics"] = [str(c) for c in critics]
    # 初始故事圣经必须在任务入队前落盘，保证首个章节步骤就能读到设定。
    # 只对带连载引擎的任务接收；已有不同内容的圣经拒绝覆盖，避免新任务误伤旧书设定。
    initial_bible = str(payload.get("story_bible") or "").strip()
    if initial_bible:
        if not task.get("serial"):
            raise ValueError("初始故事圣经仅适用于连载小说任务")
        if len(initial_bible) > BIBLE_MAX_CHARS:
            raise ValueError("故事圣经超长（最大 %d 字符，当前 %d 字符）" %
                             (BIBLE_MAX_CHARS, len(initial_bible)))
    resume = payload.get("resume")
    if isinstance(resume, dict) and resume.get("agent") and resume.get("session"):
        task["resume"] = {"agent": str(resume["agent"])[:40],
                          "session": str(resume["session"])[:80],
                          "preview": str(resume.get("preview") or "")[:140]}
        # 会话所属项目目录：续会话时 CLI 必须在该目录下启动（opencode/qwen 按 cwd 定位会话）
        proj = str(resume.get("project") or "")[:260]
        if proj:
            task["resume"]["project"] = proj
    # 初始圣经先于附件提交：如果目录已有设定，尽早拒绝，避免附件已移动却
    # 因故事圣经冲突导致任务创建失败。相同内容的重试是幂等的（例如附件
    # 提交中断后重试），不会覆盖已有设定。
    if initial_bible:
        _write_initial_story_bible(str(wd), initial_bible)
    # 附件：把待提交文件移入工作目录 _attachments/，清单注入 context（__CONTEXT__ 全链路可见）。
    # 两种形态：字符串 id = 待提交区文件（新建任务）；dict 清单 = 已落盘的附件
    # （继续连载/重试沿用同目录同文件，直接复制清单，不再移文件）。
    att_ids = payload.get("attachments")
    if isinstance(att_ids, list) and att_ids:
        from . import attachments as att_mod
        dict_items = [a for a in att_ids if isinstance(a, dict) and a.get("path")]
        if dict_items:
            root = Path(str(wd)).resolve()
            items = []
            for raw in dict_items[:att_mod.MAX_FILES]:
                rel = att_mod.norm_rel(raw)
                fp = (root / rel).resolve() if rel else root / "__invalid__"
                if rel and root in fp.parents and fp.is_file():
                    item = dict(raw)
                    item["path"] = rel
                    items.append(item)
            if not items:
                raise ValueError("附件路径必须位于工作目录的 _attachments/ 内")
        else:  # 纯 id 形态 → 从待提交区移入工作目录
            try:
                items = att_mod.commit_to_workdir(
                    str(wd), [str(x) for x in att_ids][:att_mod.MAX_FILES])
            except Exception as e:
                raise ValueError("附件落盘失败: %s" % e)
        if items:
            task["attachments"] = items
            task["asset_refs"] = att_mod.asset_refs(str(wd), items)
            try:
                from . import assets
                for ref in task["asset_refs"]:
                    saved = assets.register(str(wd), ref.get("path"),
                                            owner_type="task", owner_id=task["id"],
                                            name=ref.get("name"), mime=ref.get("mime"))
                    if saved:
                        ref.update({k: saved[k] for k in ("asset_id", "revision_id",
                                                          "content_sha256")})
            except Exception:
                pass
            task["context"] = att_mod.merge_context(
                task["context"], items, workdir=str(wd))
    # Attachments can contribute user instructions to context, so rebuild the
    # contract after attachment context has been merged.
    try:
        from . import intent_contract
        task["intent_contract"] = intent_contract.build(
            task["goal"], intent_contract.trusted_context(task["context"]),
            task["acceptance_criteria"])
    except Exception:
        pass
    from . import execution_config
    task["configuration_sources"] = execution_config.sources(task, payload, flow)
    with LOCK:
        _TASKS[task["id"]] = task
        _save_json(paths.TASKS_DIR / (task["id"] + ".json"), task)
    try:
        from . import contracts
        contracts.create(task["id"], {
            "acceptance_criteria": task.get("acceptance_criteria"),
            "approval_required": task.get("approval_required"),
            "actor": "system",
        })
    except Exception:
        # Contracts are an evidence enrichment layer; legacy task creation
        # must remain available if a damaged optional contract file is found.
        pass
    if task.get("serial"):
        try:
            from . import story_tracking
            story_tracking.init(task["workdir"], task["id"], task["title"],
                                premise=task.get("context") or task.get("goal") or "")
        except Exception:
            pass
    return task


def _save_json(path, data):
    atomic.write_json(path, data)
    bump_state()


def update_task_status(task_id, status):
    with LOCK:
        task = _TASKS.get(task_id)
        if task:
            task["status"] = status
            _save_json(paths.TASKS_DIR / (task_id + ".json"), task)


def _valid_id(task_id):
    """task_id 白名单：字母开头，仅字母/数字/下划线/连字符。

    id 会被拼进磁盘路径（tasks/<id>.json 等）与 URL，`../` 这类穿越序列必须
    在入口处拒掉；store 里所有「id 直拼路径」的读写都要先过这道闸。
    """
    return bool(re.match(r"^[A-Za-z][0-9A-Za-z_-]*$", str(task_id)))


def set_book_meta(task_id, platform, entry):
    """写任务的作品信息状态（book_meta[platform] = {status, data?, error?, at}）。

    一键生成是后台线程跑的，前端靠任务 JSON 里的这个字段看进度。这里必须
    bump_state：SSE 存活时前端不主动拉状态，不推送则卡片停在旧状态（点生成
    不翻「生成中」、跑完不翻「已生成」）。任务不存在返回 False。"""
    if not _valid_id(task_id):
        return False
    with LOCK:
        task = _TASKS.get(task_id)
        if not task:
            return False
        task.setdefault("book_meta", {})[platform] = entry
        _save_json(paths.TASKS_DIR / (task_id + ".json"), task)
    bump_state()
    return True


def set_cover_gen(task_id, entry):
    """写任务的封面生成状态（cover_gen = {status, file?, run_id?, model?, error?, at}）。

    与 set_book_meta 同理必须 bump_state（SSE 推送翻卡片）。任务不存在返回 False。"""
    if not _valid_id(task_id):
        return False
    with LOCK:
        task = _TASKS.get(task_id)
        if not task:
            return False
        task["cover_gen"] = entry
        _save_json(paths.TASKS_DIR / (task_id + ".json"), task)
    bump_state()
    return True


def set_auto_publish(task_id, ap):
    """写任务的定时发布配置（auto.py 每日到点读它触发批量发布）。

    校验在 publish/auto.norm_auto_publish（路由层先归一再落这里）；存 None
    表示清除。bump_state 同 set_book_meta：前端卡片即时反映开关态。"""
    if not _valid_id(task_id):
        return False
    with LOCK:
        task = _TASKS.get(task_id)
        if not task:
            return False
        if ap is None:
            task.pop("auto_publish", None)
        else:
            task["auto_publish"] = ap
        _save_json(paths.TASKS_DIR / (task_id + ".json"), task)
    bump_state()
    return True


def get_task(task_id, *, strict=False):
    """Read a task; strict callers distinguish unreadable files from deletion."""
    if not _valid_id(task_id):
        return None
    with LOCK:
        if task_id in _TASKS:
            return _TASKS[task_id]
    p = paths.TASKS_DIR / (task_id + ".json")
    if p.is_file():
        try:
            t = json.loads(p.read_text(encoding="utf-8"))
            _TASKS[task_id] = t
            return t
        except FileNotFoundError:
            return None
        except Exception:
            if strict:
                raise
            return None
    return None


def update_task_intent_contract(task_id, contract):
    """Persist a normalized contract for a legacy task on its next run."""
    if not _valid_id(task_id) or not isinstance(contract, dict):
        return False
    with LOCK:
        task = _TASKS.get(task_id)
        if not task:
            return False
        if task.get("intent_contract") == contract:
            return True
        task["intent_contract"] = contract
        _save_json(paths.TASKS_DIR / (task_id + ".json"), task)
    return True


def list_tasks(limit=None, archived=None):
    """按 id 倒序列任务。archived 筛选归档态；limit=None 返回全部。"""
    with LOCK:
        ids = sorted(_TASKS.keys(), reverse=True)
        out = []
        for i in ids:
            t = _TASKS[i]
            if archived is not None and bool(t.get("archived")) != archived:
                continue
            out.append(t)
            if limit is not None and len(out) >= limit:
                break
        return out


def _creation_sort_key(item):
    """Return a stable chronological key for tasks and runs.

    New records carry nanosecond creation stamps; legacy records fall back to
    their second-resolution timestamp and id for deterministic ordering.
    """
    try:
        ns = int((item or {}).get("created_at_ns") or 0)
    except (TypeError, ValueError):
        ns = 0
    return (ns, str((item or {}).get("created_at") or ""),
            str((item or {}).get("id") or ""))


def serial_chain_ids(task_id):
    """连载链条全部任务 id，根任务在前（一本书一条链：开书资料/发布绑定沿链共享）。

    含祖先（沿 serial.continues 上溯）与后代（全量扫 continues 指向链条成员）；
    非连载/未知任务回退 [task_id]。环与超深链安全（上限 50）。"""
    task_id = str(task_id or "")
    if not task_id:
        return []
    with LOCK:
        by_id = {str(t.get("id")): t for t in _TASKS.values()}
    if task_id not in by_id:
        return [task_id]
    order, seen = [], set()
    cur = task_id
    while cur and cur in by_id and cur not in seen and len(seen) < 50:
        seen.add(cur)
        order.append(cur)
        cur = str(((by_id[cur].get("serial") or {}).get("continues")) or "") or ""
    order.reverse()                      # 根在前
    in_chain, grew = set(order), True
    while grew:                          # 后代收进链条，迭代到收敛
        grew = False
        candidates = []
        for tid, t in by_id.items():
            if tid in in_chain:
                continue
            parent = str(((t.get("serial") or {}).get("continues")) or "")
            if parent and parent in in_chain:
                candidates.append((tid, t))
        for tid, _ in sorted(candidates, key=lambda pair: _creation_sort_key(pair[1])):
            order.append(tid)
            in_chain.add(tid)
            grew = True
    return order


def chain_book_meta(task, platform):
    """任务在某平台的作品信息「面板视图」：根优先返回首个有状态的条目。

    与 inherited_book_meta 同为根优先，但不筛 done——生成可从链上任意任务
    触发、落点在根任务，running/failed 也要沿链可见，否则续写批次触发生成
    后面板永远空转。running/done 优先于 failed（旧失败不遮新结果）。"""
    task = task or {}
    platform = str(platform)
    own = ((task.get("book_meta") or {}).get(platform)) or {}
    first_failed = None
    for tid in serial_chain_ids(task.get("id") or ""):
        t = task if tid == str(task.get("id")) else get_task(tid)
        if not t:
            continue
        e = ((t.get("book_meta") or {}).get(platform)) or {}
        if not (isinstance(e, dict) and e.get("status")):
            continue
        if e.get("status") in ("running", "done"):
            return e
        if first_failed is None:
            first_failed = e
    return first_failed or own


def inherited_book_meta(task, platform):
    """任务在某平台的作品信息：连载链上「生成完成」的条目整链共用（都是用第一个）。

    根在前找第一个 status=done 且有 data 的条目；整链都没有则返回任务自己的
    条目（可能为空——调用方按未生成口径处理）。"""
    task = task or {}
    platform = str(platform)
    own = ((task.get("book_meta") or {}).get(platform)) or {}
    for tid in serial_chain_ids(task.get("id") or ""):
        t = task if tid == str(task.get("id")) else get_task(tid)
        if not t:
            continue
        e = ((t.get("book_meta") or {}).get(platform)) or {}
        if e.get("status") == "done" and e.get("data"):
            return e
    return own


def migrate_task_workdirs(old_root, new_root):
    """把「旧默认保存路径」下的任务目录搬到新默认路径下，并更新任务记录。

    只动位于 old_root 内部的任务目录（当初由默认路径自动放置的）；用户显式
    指定的其他目录一律不碰。运行中/排队中的任务跳过（工作目录正被使用）。
    返回 (移动数, 跳过数)。
    """
    oldp = Path(old_root).resolve()
    newp = Path(new_root)
    if oldp == newp.resolve():
        return 0, 0
    moved = skipped = 0
    with LOCK:
        for tid in sorted(_TASKS.keys()):
            t = _TASKS[tid]
            wd = str(t.get("workdir") or "").strip()
            if not wd:
                continue
            try:
                wdp = Path(wd).resolve()
                rel = wdp.relative_to(oldp)
            except (ValueError, OSError):
                continue  # 不在旧默认路径下：不碰
            if t.get("status") in ("queued", "running"):
                skipped += 1
                continue
            if not wdp.is_dir():
                continue
            target = newp / rel
            if target.exists():
                target = newp / (rel.name + "_migrated_" + tid[-4:])
            try:
                target.parent.mkdir(parents=True, exist_ok=True)
                shutil.move(str(wdp), str(target))
            except Exception:
                skipped += 1
                continue
            t["workdir"] = str(target)
            _save_json(paths.TASKS_DIR / (tid + ".json"), t)
            moved += 1
    return moved, skipped


def _sanitize_run_text(r):
    """就地清洗 run 与各步骤里的 CLI 文本字段（错误串/摘要/输出）。

    写入侧与读盘侧共用：runner 已经把 ANSI/覆写/乱码墙洗过一遍，这里兜住
    「错误串是我们自己拼的」「历史数据是旧版本写的」两条漏网路径。只动字符串
    值，不碰结构——清洗是幂等的，重复调用无副作用。"""
    for k in ("error", "summary"):
        v = r.get(k)
        if isinstance(v, str) and v:
            r[k] = runner.clean_cli_text(v)
    for s in r.get("steps") or []:
        if not isinstance(s, dict):
            continue
        for k in ("summary", "error"):
            v = s.get(k)
            if isinstance(v, str) and v:
                s[k] = runner.clean_cli_text(v)
        # output 是智能体正文：只剥 ANSI（同 finish_step 的口径）
        if isinstance(s.get("output"), str) and s["output"]:
            s["output"] = runner.strip_ansi(s["output"])


def load_all():
    recovered_failures = []
    # 冷启动大头：几百个 run 的 run.json（本机实测 171MB/891 个）逐个串行读，
    # 慢盘/杀软扫描下两位数秒（2026-10-20 实案 11.1s，占整次启动的一半）。
    # 读盘是可并发的 I/O：先在锁外用线程池把文件读成 JSON，再进锁按原顺序
    # 对账/落盘——入账顺序、收尸口径、异常吞掉策略与串行版完全一致。
    def _read_json(p):
        try:
            return json.loads(p.read_text(encoding="utf-8"))
        except Exception:
            return None

    task_files = sorted(paths.TASKS_DIR.glob("*.json"))
    run_files = sorted(paths.RUNS_DIR.glob("*/run.json"))
    with futures.ThreadPoolExecutor(
            max_workers=min(16, max(4, os.cpu_count() or 4))) as _pool:
        task_blobs = list(_pool.map(_read_json, task_files))
        run_blobs = list(_pool.map(_read_json, run_files))
    with LOCK:
        for t in task_blobs:
            try:
                _TASKS[t["id"]] = t
            except Exception:
                pass
        for p, r in zip(run_files, run_blobs):
            try:
                r.pop("cancel_event", None)
                # 存量清洗：早期版本把 CLI 的 ANSI 转义/控制字符原样写进了摘要与
                # 错误串（`[91m[1mError:`、U+FFFD 乱码墙），读盘时统一洗一遍——
                # 老运行不必等重跑才干净（2026-09-20「咋还有乱码」实测）
                _sanitize_run_text(r)
                # 直接执行线程不跨进程：running 与无截止时间的 queued 是上次
                # 进程中断残骸。带 resume_enqueue_at 的 queued 是有意的自动续跑
                # 退避，保留给 jobs.restore_deferred_resumes 重建 Timer。
                interrupted = (r.get("status") == "running" or
                               (r.get("status") == "queued" and
                                not r.get("resume_enqueue_at")))
                if interrupted:
                    r["status"] = "failed"
                    r["error"] = r.get("error") or "服务重启中断，可重试"
                    # 补 ended_at：收尸只改状态不收时间，run 永远挂着「无结束
                    # 时间」的空壳（2026-10-15 实案：中断 run 反复被收尸重写
                    # 却始终 ended_at=None，UI 上像仍在跑）。就地生成当前时间。
                    r["ended_at"] = r.get("ended_at") or time.strftime("%Y-%m-%d %H:%M:%S")
                    _save_json(p, r)
                    # 读盘收尸绕过 update_run，需在释放存储锁后补一条 run
                    # 级错误记录；避免错误台账的文件 I/O 拉长全局锁。
                    recovered_failures.append(dict(r))
                _RUNS[r["id"]] = r
            except Exception:
                pass
        # 回填历史遗留：运行已终态而任务仍停在 created/queued/running 的脏状态
        for t in _TASKS.values():
            if t.get("status") not in ("created", "queued", "running"):
                continue
            runs = [r for r in _RUNS.values() if r.get("task_id") == t["id"]]
            if not runs:
                # 卡在排队却从未有运行：创建流程被中断，标记失败允许重试
                t["status"] = "failed"
                t["error"] = "没有运行记录（创建可能被中断），可重试"
                _save_json(paths.TASKS_DIR / (t["id"] + ".json"), t)
                continue
            latest = max(runs, key=lambda r: r["id"])
            if latest.get("status") in ("done", "failed", "cancelled", "timeout"):
                t["status"] = latest["status"]
                _save_json(paths.TASKS_DIR / (t["id"] + ".json"), t)
    for run in recovered_failures:
        _record_run_failure(run)


# ---------------------------------------------------------------- 运行（含管理操作）

_RUN_ESTIMATOR = None
_RUN_AUDITOR = None


def set_run_estimator(estimator):
    """由应用入口注入 ETA 估算器，保持存储层不反向依赖用量模块。"""
    global _RUN_ESTIMATOR
    _RUN_ESTIMATOR = estimator if callable(estimator) else None


def set_run_auditor(auditor):
    """注入 run 终态对账器（usage.audit_run），同样不让存储层依赖用量模块。"""
    global _RUN_AUDITOR
    _RUN_AUDITOR = auditor if callable(auditor) else None


def _redact_command(command, limit=300):
    value = str(command or "")[:limit]
    if not value:
        return ""
    pattern = re.compile(
        r"(?i)(api[_-]?key|access[_-]?token|auth(?:orization)?|password|passwd|secret|token)"
        r"(\s*[:=]\s*|\s+)([^\s]+)")
    return pattern.sub(lambda m: m.group(1) + m.group(2) + "<redacted>", value)


def _execution_snapshot(task):
    """Capture the redacted execution inputs at run creation time.

    This is intentionally a snapshot of identities and revisions, never API
    keys or task bodies.  Later binding/skill/knowledge changes therefore do
    not rewrite the explanation of an existing run.
    """
    task = task or {}
    from . import execution_config
    flow_snapshot = dict(task.get("flow_snapshot") or {})
    flow_digest = str(flow_snapshot.get("digest") or "")
    flow_content_sha = str(flow_snapshot.get("content_sha256") or "")
    flow_revision_id = str(flow_snapshot.get("revision_id") or "")
    if not flow_content_sha and len(flow_digest) == 64:
        flow_content_sha = flow_digest
    if not flow_revision_id and flow_digest:
        flow_revision_id = "flowrev-" + flow_digest[:16]

    expert_snapshot = task.get("expert_snapshot")
    if not isinstance(expert_snapshot, dict):
        expert_snapshot = {}
    snapshot = {
        "captured_at": time.strftime("%Y-%m-%d %H:%M:%S"),
        "configuration": execution_config.capture(task),
        "task_id": task.get("id") or "",
        "flow_revision": flow_digest,
        "flow_revision_id": flow_revision_id,
        "flow_content_sha256": flow_content_sha,
        "flow_snapshot": flow_snapshot,
        "expert_snapshot": {
            "id": str(expert_snapshot.get("id") or "")[:80],
            "name": str(expert_snapshot.get("name") or "")[:120],
            "version": str(expert_snapshot.get("version") or "")[:40],
            "content_sha256": str(expert_snapshot.get("content_sha256") or ""),
        } if expert_snapshot else {},
        "workdir": str(task.get("workdir") or "")[:500],
        "git_rev": str(task.get("git_rev") or "")[:160],
        "verify_command": _redact_command(task.get("verify_command") or ""),
        "quality_gate": {
            "threshold": task.get("threshold"),
            "rubric": list(task.get("rubric") or [])[:8],
            "rounds": task.get("rounds"),
            "best_of": task.get("best_of"),
        },
    }
    try:
        from . import knowledge, modelhub, skills
        snapshot["skills"] = skills.revision_for_task(task)
        snapshot["knowledge"] = knowledge.revision_for_task(task)
        providers = {str(p.get("id") or ""): p for p in modelhub.providers()}
        bindings = {}
        for agent_id, binding in (modelhub.bindings() or {}).items():
            chain = []
            for item in (binding or {}).get("chain") or []:
                pid = str(item.get("provider_id") or "")
                prov = providers.get(pid) or {}
                try:
                    key_ids = [str(k.get("id") or "") for k in modelhub._provider_keys(prov)
                               if k.get("id")]
                except Exception:
                    key_ids = []
                entry = {
                    "provider_id": pid,
                    "model": str(item.get("model") or "")[:160],
                    "key_ids": key_ids[:8],
                }
                if pid:
                    entry.update(modelhub.connection_identity(pid, key_ids[0] if key_ids else ""))
                chain.append(entry)
            bindings[str(agent_id)[:80]] = {
                "difficulty_routing": bool((binding or {}).get("difficulty_routing")),
                "chain": chain,
            }
        snapshot["agent_bindings"] = bindings
        if task.get("direct_provider_id") or task.get("direct_model"):
            direct = {
                "provider_id": str(task.get("direct_provider_id") or "")[:80],
                "model": str(task.get("direct_model") or "")[:160],
                "agent": str(task.get("direct_agent") or "")[:80],
            }
            direct_pid = str(task.get("direct_provider_id") or "")
            direct_prov = providers.get(direct_pid) or {}
            direct_keys = modelhub._provider_keys(direct_prov)
            if direct_pid:
                direct.update(modelhub.connection_identity(
                    direct_pid, str((direct_keys[0] if direct_keys else {}).get("id") or "")))
            snapshot["direct"] = direct
    except Exception:
        # A snapshot is diagnostic metadata.  A broken optional catalog must
        # never prevent the task from being created.
        snapshot.setdefault("skills", {})
        snapshot.setdefault("knowledge", {})
        snapshot.setdefault("agent_bindings", {})
    return snapshot


# run 终态集合：与 CAS 收口、状态回填共用同一份定义
TERMINAL_STATUSES = ("done", "failed", "cancelled", "timeout")


def _record_run_failure(run):
    """把 run 级失败写入错误台账，避免启动/调度异常只留在 run.json。

    步骤失败已经由 pipeline 记录；这里只覆盖 run 的收口失败（例如
    enqueue/线程启动失败、服务重启恢复和超时），并由调用方保证同一 run
    只在首次进入终态时调用。只写稳定原因和安全摘要，不把原始错误或任务
    正文复制进遥测台账。
    """
    try:
        from . import errorlog
        from .error_codes import ErrorCode, classify_error_text, error_code_value, safe_error_summary

        status = str(run.get("status") or "failed")
        raw_code = run.get("error_code")
        if not raw_code:
            failed_steps = [s for s in (run.get("steps") or [])
                            if isinstance(s, dict) and s.get("status") in ("failed", "timeout")]
            if failed_steps:
                raw_code = failed_steps[-1].get("error_code")
        if not raw_code:
            raw_code = classify_error_text(run.get("error") or "")

        try:
            code = ErrorCode(raw_code) if raw_code else None
        except (TypeError, ValueError):
            code = None
        if code is not None:
            reason = error_code_value(code)
            detail = safe_error_summary(code)
        elif status == "timeout":
            reason, detail = "TIMEOUT", "调用超时"
        else:
            reason, detail = "RUN_FAILED", "运行失败"

        step = next((s for s in reversed(run.get("steps") or [])
                     if isinstance(s, dict) and s.get("status") in ("failed", "timeout")), {})
        provider = step.get("provider") or run.get("provider") or ""
        if isinstance(provider, dict):
            provider = provider.get("id") or provider.get("name") or ""
        errorlog.record(
            category="run", reason=reason, detail=detail,
            provider=provider, model=step.get("model") or run.get("model") or "",
            tool=step.get("agent") or run.get("tool") or "",
            role=step.get("role") or "", run_id=run.get("id") or "",
            task_id=run.get("task_id") or "", step=step.get("n") or 0,
            exit_code=step.get("exit_code"))
    except Exception:
        # 诊断台账是旁路能力，绝不能阻塞状态收口。
        pass


def create_run(kind, title, task_id=None, entry_id=None, op=None):
    run = {
        "id": _new_id("r" if kind == "orchestration" else "m"),
        "kind": kind,  # orchestration | mgmt
        "title": title,
        "task_id": task_id, "entry_id": entry_id, "op": op,
        "status": "queued", "steps": [], "messages": [],
        "created_at": time.strftime("%Y-%m-%d %H:%M:%S"),
        "created_at_ns": time.time_ns(),
        "started_at": None, "ended_at": None,
        "deadline_at": None,
        "cost_usd": 0.0, "tokens": 0, "error": "",
        "verdict": None, "summary": "",
        # ``paused`` remains the backwards-compatible user intent flag.
        # ``pause_phase`` makes the visible lifecycle explicit: the request
        # may still be waiting for the current step(s), or the run may have
        # actually reached a step boundary and be waiting at the gate.
        "pause_phase": "none", "pause_requested_at": None,
        "pause_reached_at": None,
    }
    # Persist trace identity at run creation so replay remains stable across
    # process restarts and legacy readers can still use the normal run shape.
    run["trace_schema_version"] = tracing.SCHEMA_VERSION
    run["trace_id"] = tracing.trace_id(run["id"])
    run["root_span_id"] = tracing.root_span_id(run["id"])
    if kind == "orchestration" and task_id:
        task = get_task(task_id) or {}
        run["timeout_budget_s"] = _task_timeout_s(task.get("timeout_s"))
        # Provisional for direct callers; jobs resets it at worker claim so
        # queued/backoff time does not shorten the actual execution budget.
        run["deadline_at"] = time.time() + run["timeout_budget_s"]
        run["execution_snapshot"] = _execution_snapshot(task)
        # Freeze even an empty gate: later task edits cannot regrade history.
        run["acceptance_thresholds"] = dict(task.get("acceptance_thresholds") or {})
        try:
            eta = _RUN_ESTIMATOR(
                task_type=task.get("type") or "", mode=task.get("mode") or "auto",
                thinking=task.get("thinking") or "standard",
                rounds=task.get("rounds"))
            run["estimated_duration_s"] = eta.get("estimated_duration_s")
            run["estimated_p90_s"] = eta.get("p90_duration_s")
            run["estimate_source"] = eta.get("duration_source")
        except Exception:
            pass
    rdir = paths.RUNS_DIR / run["id"]
    (rdir / "steps").mkdir(parents=True, exist_ok=True)
    with LOCK:
        # 先完成原子落盘，再发布到内存索引。旧顺序在 _save_json 失败时会
        # 留下只存在于 _RUNS 的“幽灵 run”，后续 UI 看到排队记录却永远无法
        # 读取/恢复其 run.json。
        try:
            _save_json(rdir / "run.json", run)
        except Exception:
            _RUNS.pop(run["id"], None)
            raise
        _RUNS[run["id"]] = run
    # 新 run（排队/起跑）必须唤醒 SSE：否则侧栏 task_latest 感知不到
    # 「最新一次运行」已易主，字形/时间停在上一轮（2026-09-22 侧栏假在跑案）
    bump_state()
    return run


def get_run(run_id):
    with LOCK:
        return _RUNS.get(run_id)


def list_runs(limit=60):
    with LOCK:
        ids = sorted(_RUNS.keys(), reverse=True)
        selected = ids if limit is None else ids[:limit]
        return [_RUNS[i] for i in selected]


def latest_run_by_task():
    """每个任务的最近一次运行（含 steps），全量扫描不受 list_runs 窗口限制。

    侧栏靠它给每个任务展示真实近况；从未运行过的任务不在返回值里。
    run id 含时间戳，字典序即新旧序。
    """
    with LOCK:
        best = {}
        for r in _RUNS.values():
            tid = r.get("task_id")
            if not tid:
                continue
            cur = best.get(tid)
            if cur is None or _creation_sort_key(r) > _creation_sort_key(cur):
                best[tid] = r
        return {tid: dict(r) for tid, r in best.items()}


def task_run_stats():
    """每个任务的运行次数与步骤总数（全量扫描，不受 run 窗口限制）。

    侧栏「查看全部 N 次运行 · M 步」的计数来源——从窗口里数会因刷屏/窗口滑动而算错。
    """
    with LOCK:
        stats = {}
        for r in _RUNS.values():
            tid = r.get("task_id")
            if not tid:
                continue
            s = stats.setdefault(tid, {"runs": 0, "steps": 0})
            s["runs"] += 1
            s["steps"] += len(r.get("steps") or [])
        return stats


def task_runs(task_id):
    """某任务的全部运行（新→旧，含 steps）。任务级详情视图用，按需拉全量。"""
    with LOCK:
        runs = [dict(r) for r in _RUNS.values() if r.get("task_id") == task_id]
    runs.sort(key=_creation_sort_key, reverse=True)
    return runs


def task_runs_page(task_id, offset=0, limit=3):
    """分页读取任务运行，只复制当前页；全量轻统计在详情打开时按需计算。"""
    with LOCK:
        runs = [r for r in _RUNS.values() if r.get("task_id") == task_id]
    runs.sort(key=_creation_sort_key, reverse=True)
    page = [dict(run) for run in runs[offset:offset + limit]]
    total_steps = settled_steps = total_tokens = 0
    total_cost = 0.0
    for run in runs:
        steps = run.get("steps") or []
        total_steps += len(steps)
        settled_steps += sum(1 for step in steps
                             if step.get("status") not in ("running", "queued"))
        try:
            total_cost += float(run.get("cost_usd") or 0)
        except (TypeError, ValueError):
            pass
        try:
            total_tokens += int(run.get("tokens") or 0)
        except (TypeError, ValueError):
            pass
    return {
        "runs": page,
        "total": len(runs),
        "offset": offset,
        "has_more": offset + len(page) < len(runs),
        "totals": {"steps": total_steps, "settled_steps": settled_steps,
                   "cost_usd": total_cost, "tokens": total_tokens},
    }


def task_side(task_id):
    """任务检查器（右缘停靠列）的轻量聚合：最新 run 摘要 + 进度步骤 +
    git 分支/裁决状态 + 变更行级统计 + 成品文件。

    刻意不带 diff 文本与消息历史——面板每 2s 轮询一次，响应必须保持 KB 级；
    diff 按需走 /api/runs/<id> 单拉。任务不存在返回 None。

    变更统计取双路径：任务运行中 → 实时 numstat（工作区正检出在任务分支，
    未提交改动即产物）；已结束 → 读最新 run 的 changes 快照（finalize 已把
    产物提交进任务分支并切回，实时统计恒为 0，只能用落盘快照）。
    """
    task = get_task(task_id)
    if not task:
        return None
    runs = task_runs(task_id)
    latest = runs[0] if runs else None
    steps = (latest or {}).get("steps") or []
    done = sum(1 for s in steps if s.get("status") == "done")
    current = next((s for s in steps if s.get("status") == "running"), None)
    active = bool(latest) and latest.get("status") in ("queued", "running")
    # 分支上下文取最近一次带 git 信息的 run（重试/续跑会带出同一任务分支）
    git = {}
    for r in runs:
        if r.get("git"):
            git = {k: r["git"].get(k) for k in
                   ("rev", "branch", "commit", "from_branch", "base_commit",
                    "restored", "restore_error")}
            break
    git["state"] = task.get("git_state") or ""
    changes = {"count": 0, "add_total": None, "del_total": None, "files": []}
    if active:
        from . import gitmod
        live = gitmod.collect_changes(task.get("workdir"))
        changes["count"] = len(live.get("files") or [])
        changes["add_total"] = live.get("add_total") or 0
        changes["del_total"] = live.get("del_total") or 0
        changes["files"] = [
            {"status": f.get("status"), "path": f.get("path"),
             "add": f.get("add") or 0, "del": f.get("del") or 0}
            for f in (live.get("files") or [])[:50]]
        git["branch"] = git.get("branch") or gitmod.branch_name(task_id)
    elif latest is not None:
        snap = latest.get("changes") or {}
        snap_files = snap.get("files") or []
        changes["count"] = len(snap_files)
        changes["add_total"] = snap.get("add_total")
        changes["del_total"] = snap.get("del_total")
        changes["files"] = [
            {"status": f.get("status"), "path": f.get("path"),
             "add": f.get("add"), "del": f.get("del")}
            for f in snap_files[:50]]
    total_steps = sum(len(r.get("steps") or []) for r in runs)
    # 成品口径闸：任务一步都没跑出来过（如历次都在检出前失败）→ 工作目录里的
    # 文件变动全是并行活动的噪音，不算这个任务的成品
    wd, arts = run_artifacts(latest["id"], limit=50, cache=True) if (latest and total_steps > 0) else ("", [])
    return {
        "task": {k: task.get(k) for k in ("id", "title", "status", "workdir", "git_state",
                                          "git_rev")},
        "run": ({k: latest.get(k) for k in ("id", "status", "cost_usd", "tokens", "error",
                                            "created_at", "started_at", "ended_at")}
                if latest else None),
        "progress": {"total": len(steps), "done": done,
                     "current": ({k: current.get(k) for k in
                                  ("n", "role", "agent_label", "summary", "status")}
                                 if current else None)},
        "steps": [{k: s.get(k) for k in ("n", "role", "agent_label", "summary",
                                         "status", "duration_s", "log")}
                  for s in steps[:20]],
        "git": git,
        "changes": changes,
        "workdir": wd,
        "files": arts,
        "stats": {"runs": len(runs),
                  "steps": total_steps,
                  "cost_usd": sum(float(r.get("cost_usd") or 0) for r in runs),
                  "tokens": sum(int(r.get("tokens") or 0) for r in runs)},
    }


def update_run(run_id, expected_status=None, **fields):
    """更新运行字段。expected_status 非 None 时做 CAS（§2C）：
    当前状态不等于 expected_status 则拒绝写入并返回 None，
    防止陈旧执行方（被取消的 worker、崩溃恢复前的旧线程）覆盖新状态
    ——防御模式「异步状态不是同步状态」。不传则保持原行为。"""
    audit = None
    failure_audit = None
    with LOCK:
        run = _RUNS.get(run_id)
        if not run:
            return None
        if expected_status is not None and run.get("status") != expected_status:
            return None
        prev_status = str(run.get("status") or "")
        for k in ("error", "summary"):
            if isinstance(fields.get(k), str):
                # 错误串多是我们自己拼的 CLI 尾巴（含 ANSI/覆写/乱码墙）：
                # 落内存前洗一遍，UI/台账读到的就是干净文本
                fields[k] = runner.clean_cli_text(fields[k])
        run.update(fields)
        # 终态收尸：run 已结束却还挂 queued/running 的步骤统一落 cancelled，
        # 语义与 recover_orphaned_runs 的启动清扫对齐（那套清跨进程遗留，
        # 这套在落终态瞬间即时收口——打磨组长被取消/异常打断时 UI 不闪
        # 假「工作中」）。run 终态后不可能还有步骤在真跑。
        st = fields.get("status")
        if st in ("done", "failed", "cancelled", "timeout"):
            # A terminal run can retain the legacy ``paused`` intent for
            # recovery/audit compatibility, but it must never look like a
            # live run waiting at a gate.
            run["pause_phase"] = "none"
            run["pause_requested_at"] = None
            run["pause_reached_at"] = None
            end_s = time.strftime("%H:%M:%S")
            for s in run["steps"]:
                if s.get("status") in ("queued", "running"):
                    s["status"] = "timeout" if st == "timeout" else "cancelled"
                    s["ended_at"] = s.get("ended_at") or end_s
                    if not s.get("summary"):
                        s["summary"] = ("步骤未正常收尾（任务总时限已到）"
                                         if st == "timeout" else
                                         "步骤未正常收尾（终态自动恢复）")
        # 状态回填：起跑与结束都同步任务状态。只回填终态的话，run 在跑、
        # 任务永远显示「排队中」（2026-09-18 实案：「# 重写·续」run 已 running
        # 写了一小时，任务卡 queued，用户以为卡死连点重试）。
        if st in ("running", "done", "failed", "cancelled", "timeout"):
            tid = run.get("task_id")
            task = _TASKS.get(tid) if tid else None
            if task:
                task["status"] = st
                _save_json(paths.TASKS_DIR / (tid + ".json"), task)
        if st in TERMINAL_STATUSES:
            try:
                from . import acceptance
                run["acceptance"] = acceptance.evaluate_run(
                    run, (_TASKS.get(run.get("task_id")) if run.get("task_id") else None) or {})
            except Exception:
                # Acceptance reporting is evidence enrichment; it must not
                # prevent the primary run from reaching a terminal state.
                pass
            try:
                from . import contracts
                for case in (run.get("acceptance") or {}).get("cases") or []:
                    contracts.add_evidence(
                        run.get("task_id") or "",
                        {"criterion": "acceptance:%s" % case.get("case"),
                         "status": "passed" if case.get("status") == "passed" else case.get("status"),
                         "summary": case.get("reason") or "acceptance matrix",
                         "source": "acceptance"}, actor="system")
                contracts.record_acceptance_cases(
                    run.get("task_id") or "",
                    (run.get("acceptance") or {}).get("cases") or [],
                    actor="system")
                contracts.complete(run.get("task_id") or "", run)
            except Exception:
                pass
        _save_json(paths.RUNS_DIR / run_id / "run.json", run)
        if st in TERMINAL_STATUSES and prev_status not in TERMINAL_STATUSES:
            # 事后对账：只有「非终态 → 终态」这一次翻转才记账，重复写终态
            # （UI 补写、迟到写手）不得二次入账。快照在锁内浅拷，锁外调用
            # 审计器——审计器会回读 store，锁内调用即自锁。
            audit = dict(run)
            if st in ("failed", "timeout"):
                failure_audit = dict(run)
        if st in ("done", "failed", "cancelled", "timeout"):
            # run_end 钩子（2026-09-22）：终态即触发，后台跑副作用型脚本
            # （回写知识库等）；失败/超时静默，绝不拖慢收尾路径
            try:
                import threading
                from . import hooks
                threading.Thread(
                    target=hooks.run_event, args=("run_end",), daemon=True,
                    kwargs={"task_id": run.get("task_id") or "", "run_id": run_id,
                            "ctx": {"status": st, "title": run.get("title") or "",
                                    "error": str(run.get("error") or "")[:300]}}).start()
            except Exception:
                pass
    # 每次成功写入都唤醒 SSE：run 的状态/步骤变化从前只能靠无关操作
    # （自动化落盘、删记录）顺带广播，空闲时侧栏 task_latest 会冻在
    # 「在跑」直到下一次无关 bump（2026-09-22 侧栏假在跑案）。
    # 调用都是步骤级边界，不会形成推送风暴；CAS 拒绝/无此 run 的早退
    # 路径不动版本号。
    if failure_audit is not None:
        _record_run_failure(failure_audit)
    if audit is not None and _RUN_AUDITOR is not None:
        try:
            _RUN_AUDITOR(audit)
        except Exception:
            pass    # 对账永远不能拖垮终态收尾
    bump_state()
    return run


def run_dir(run_id):
    return paths.RUNS_DIR / run_id


def _schedule_run_dir_cleanup(run_id):
    """把运行目录快速移出可见路径，再后台删除大日志目录。

    删除任务/运行记录不能让 HTTP 请求同步递归扫描数千个日志文件。
    同盘 rename 是 O(1)，原路径立即消失；后台失败也不会影响内存状态。
    """
    source = paths.RUNS_DIR / str(run_id)
    if not source.exists():
        return
    target = paths.RUNS_DIR / (".deleting-%s-%s" % (run_id, secrets.token_hex(4)))
    try:
        source.rename(target)
    except OSError:
        target = source

    def _remove():
        shutil.rmtree(target, ignore_errors=True)

    threading.Thread(target=_remove, name="codebee-delete-%s" % run_id[:12],
                     daemon=True).start()


def delete_run(run_id):
    """删除一条运行记录（内存 + 磁盘目录）。返回 (ok, 错误信息)。"""
    if not _valid_id(run_id):
        return False, "非法的记录 ID"
    with LOCK:
        run = _RUNS.get(run_id)
        if not run:
            return False, "记录不存在"
        if run.get("status") in ("queued", "running"):
            return False, "运行中的记录不能删除，请先取消"
        del _RUNS[run_id]
    _schedule_run_dir_cleanup(run_id)
    bump_state()
    return True, ""


def recover_orphaned_runs():
    """启动时调用：把上一轮进程崩溃遗留的 status='running' run 标记为 failed。

    设计稿：docs/migration/01-defense-patterns.md §1E。
    参考 dsh docs/subsystems/persistence.zh.md:108-114 interruptedTurnClosers。
    调用时机：load_all 之后，启动 HTTP 服务之前。
    返回恢复的 run 数。
    """
    now = time.strftime("%Y-%m-%d %H:%M:%S")
    with LOCK:
        # status=running 视为崩溃遗留（包括 ended_at 已设的异常状态，统一兜底）
        candidates = [r["id"] for r in _RUNS.values() if r.get("status") == "running"]
    recovered = 0
    for run_id in candidates:
        # update_run 内部再次加 LOCK（RLock 允许重入），并把终态回填到 task
        update_run(run_id,
                   status="failed",
                   ended_at=now,
                   error="interrupted at startup (auto-recovered)")
        recovered += 1
    # 步骤级兜底：终态 run 里卡在 queued/running 的步骤落「已取消」。
    # 既清崩溃遗留，也清取消收尾修复前的僵尸步骤（运行已取消、格子永转）；
    # 进行中的 run 不碰——其步骤是活流程，收尾由 pipeline._run_step 负责。
    healed = 0
    step_now = time.strftime("%H:%M:%S")
    with LOCK:
        for r in _RUNS.values():
            if r.get("status") not in ("done", "failed", "cancelled", "timeout"):
                continue
            changed = False
            for s in r.get("steps") or []:
                if s.get("status") in ("queued", "running"):
                    s["status"] = "timeout" if r.get("status") == "timeout" else "cancelled"
                    s["ended_at"] = s.get("ended_at") or step_now
                    if not s.get("summary"):
                        s["summary"] = ("步骤未正常收尾（任务总时限已到）"
                                         if r.get("status") == "timeout" else
                                         "步骤未正常收尾（取消/中断自动恢复）")
                    changed = True
            if changed:
                _save_json(paths.RUNS_DIR / r["id"] / "run.json", r)
                healed += 1
    if healed:
        bump_state()
    return recovered


def recover_interrupted_mgmt():
    """启动时调用：把崩溃遗留的 queued 管理操作 run 标记为 failed。

    running 的 mgmt run 已由 recover_orphaned_runs 统一收尸；queued 不在
    其候选里——通用恢复故意留着 queued 给连载任务的 resume_interrupted
    复活，但 mgmt job 只存在于内存队列，重启后永远没人认领，留着还会
    堵住同条目的去重闸门。workers 尚未启动，此时 queued 必是遗留。
    """
    now = time.strftime("%Y-%m-%d %H:%M:%S")
    with LOCK:
        candidates = [r["id"] for r in _RUNS.values()
                      if r.get("kind") == "mgmt" and r.get("status") == "queued"]
    for run_id in candidates:
        update_run(run_id, status="failed", ended_at=now,
                   error="interrupted at startup (mgmt auto-recovered)")
    return len(candidates)


def active_mgmt_run(entry_id):
    """该目录条目当前进行中（queued/running）的管理操作 run；没有则 None。

    供「同条目同时只跑一个安装/升级/卸载」去重闸使用：两个同名全局 npm
    并发装同一包会互锁（2026-09-18 codex 双开案）。内存索引即真源——
    重启后 recover_* 已把遗留 run 收成终态。
    """
    with LOCK:
        for r in _RUNS.values():
            if r.get("kind") == "mgmt" and r.get("entry_id") == entry_id \
                    and r.get("status") in ("queued", "running"):
                return dict(r)
    return None


def run_workdir(run_id):
    """该 run 的工作目录（经其 task 关联）；无任务的 run（如管理操作）返回空串。"""
    run = get_run(run_id)
    if not run:
        return ""
    task = get_task(run.get("task_id") or "") if run.get("task_id") else None
    return str(task.get("workdir") or "") if task else ""


def task_first_start(task_id, fallback=""):
    """该任务最早一次运行的开始时间（含回退：任务创建时间 → 指定回退值）。"""
    stamps = []
    with LOCK:
        for r in _RUNS.values():
            if r.get("task_id") == task_id:
                stamps.append(str(r.get("started_at") or r.get("created_at") or ""))
    task = get_task(task_id) if task_id else None
    if task:
        stamps.append(str(task.get("created_at") or ""))
    stamps.append(str(fallback or ""))
    best = ""
    for s in stamps:
        if s and (not best or s < best):
            best = s
    return best


def run_artifacts(run_id, limit=200, cache=False):
    """列一次运行的「成品文件」：工作目录里自该任务首次运行以来新产生/修改的文件。

    断点续跑会拆成多条 run，只按本 run 过滤会漏掉早期章节；这里以「任务首跑」
    为起点。返回 (workdir, files)；files 按 mtime 新→旧，name 为工作目录内
    相对路径，已跳过 .git / 隐藏目录 / node_modules / target 等构建产物目录。
    cache=True 供 UI 轮询热路径（详情/侧栏/状态快照）复用短 TTL 缓存与在飞
    合并；发布/预览/知识库等正确性敏感方保持默认，永远现扫。
    剪枝遍历与过滤实现在 artifacts.py（几万文件目录从秒级降到百毫秒级）。
    """
    wd = run_workdir(run_id)
    if not wd:
        return "", []
    run = get_run(run_id) or {}
    t0 = task_first_start(run.get("task_id") or "",
                           run.get("started_at") or run.get("created_at") or "")
    try:
        t0 = time.mktime(time.strptime(t0, "%Y-%m-%d %H:%M:%S")) - 1
    except Exception:
        t0 = 0
    root = Path(wd)
    if not root.is_dir():
        return wd, []
    running = False
    if cache:
        task = get_task(run.get("task_id") or "") if run.get("task_id") else None
        running = bool(task) and task.get("status") in ("queued", "running")
    return wd, artifacts.scan(str(root), t0, limit=limit,
                              running=running, cache=cache)


def run_asset_refs(run_id, limit=200):
    """Return content-addressed references for this run's output files."""
    wd, files = run_artifacts(run_id, limit=limit, cache=False)
    if not wd:
        return []
    from . import attachments
    refs = attachments.asset_refs(wd, [{"path": f.get("name") or f.get("path") or ""}
                                       for f in files if isinstance(f, dict)])
    try:
        from . import assets
        run = get_run(run_id) or {}
        for ref in refs:
            saved = assets.register(wd, ref.get("path"), owner_type="run",
                                    owner_id=run_id, name=ref.get("name"),
                                    mime=ref.get("mime"))
            if saved:
                ref.update({k: saved[k] for k in ("asset_id", "revision_id",
                                                  "content_sha256")})
    except Exception:
        pass
    return refs


def task_step_count(task_id):
    """该任务所有 run 的步骤总数（含进行中）。

    成品口径的闸：一步都没跑出来过的任务（历次都在检出等前置环节失败）
    没有成品可言，工作目录里的文件变动全是并行活动的噪音。
    """
    if not task_id:
        return 0
    return sum(len(r.get("steps") or []) for r in task_runs(task_id))


# ---------------------------------------------------------------- 故事圣经（story-bible.md）

BIBLE_FILE = "story-bible.md"
BIBLE_MAX_CHARS = 20000


def _bible_path(workdir):
    """工作目录内圣经文件绝对路径；目录穿越直接返回 None（不读外面任何东西）。"""
    wd = str(workdir or "").strip()
    if not wd or not os.path.isdir(wd):
        return None
    root = Path(wd).resolve()
    p = (root / BIBLE_FILE).resolve()
    if root != p and root not in p.parents:
        return None
    return p


def _write_initial_story_bible(workdir, text):
    """创建任务时安全播种故事圣经。

    初始圣经写入发生在任务入队之前，多个请求可能同时指向同一个工作目录。
    旧逻辑先在锁外检查、再在锁外写入，两个请求都能通过检查，后写请求会
    覆盖先写的设定。这里把检查和写入放进同一进程锁，并在文件不存在时用
    ``O_EXCL`` 做最后一道独占创建；已有不同内容的文件始终拒绝覆盖。
    """
    p = _bible_path(workdir)
    if p is None:
        raise ValueError("故事圣经写入失败：工作目录不可用")
    with LOCK:
        try:
            p.parent.mkdir(parents=True, exist_ok=True)
            if p.exists():
                if not p.is_file():
                    raise ValueError("故事圣经写入失败：目标路径不是文件")
                existing = runner.read_text_any_enc(p).strip()
                if existing:
                    if existing == text:
                        return
                    raise ValueError("工作目录已有 story-bible.md，请清空初始圣经输入或先编辑已有设定")
                # 空文件是合法的旧占位文件；在锁内覆盖，避免本服务内的并发写入。
                atomic.write_text(p, text)
                return
            try:
                atomic.create_text(p, text, mode=0o644)
                return
            except FileExistsError:
                # 其他进程可能刚创建了文件。相同内容可幂等返回；空文件仍可
                # 完成播种，非空不同内容绝不静默覆盖。
                if p.is_file():
                    existing = runner.read_text_any_enc(p).strip()
                    if existing == text:
                        return
                    if not existing:
                        atomic.write_text(p, text)
                        return
                raise ValueError("工作目录已有 story-bible.md，请清空初始圣经输入或先编辑已有设定")
        except ValueError:
            raise
        except OSError as e:
            raise ValueError("故事圣经写入失败：%s" % e)


def read_story_bible(task_id):
    """读取任务工作目录里的故事圣经。返回 (文件路径, 文本内容, None) 或
    (None, None, 错误信息)。"""
    from . import store as _self
    task = get_task(task_id)
    if not task:
        return None, None, "任务不存在"
    p = _bible_path(task.get("workdir"))
    if p is None:
        return None, None, "工作目录不存在或路径越界"
    try:
        if p.is_file():
            return str(p), runner.read_text_any_enc(p), None
        return str(p), "", None   # 文件未创建：空内容
    except OSError as e:
        return None, None, "读取失败: %s" % e


def write_story_bible(task_id, text):
    """写入故事圣经到任务工作目录。守卫：
    - 任务不存在/工作目录越界 → 拒绝；
    - 任务正在运行（queued/running）→ 拒绝（圣经是中流砥柱，运行中不能换骨架）；
    - 文本超长（BIBLE_MAX_CHARS）→ 拒绝；
    - 写入失败 → 报错。
    返回 (ok, 错误信息)。"""
    with LOCK:
        task = get_task(task_id)
        if not task:
            return False, "任务不存在"
        if task.get("status") in ("queued", "running"):
            return False, "任务正在运行，不能修改故事圣经（请等运行结束后再编辑）"
        p = _bible_path(task.get("workdir"))
        if p is None:
            return False, "工作目录不存在或路径越界"
        text = (text or "").strip()
        if len(text) > BIBLE_MAX_CHARS:
            return False, "故事圣经超长（最大 %d 字符，当前 %d 字符）" % (BIBLE_MAX_CHARS, len(text))
        try:
            p.parent.mkdir(parents=True, exist_ok=True)
            atomic.write_text(p, text)
        except OSError as e:
            return False, "写入失败: %s" % e
    # 故事圣经是提示词输入，写入后让 SSE/轮询端尽快看到新状态。
    bump_state()
    return True, ""


def read_run_file(run_id, rel):
    """读取 run 工作目录内的一个文件（防目录穿越）。返回 (bytes, 错误)。"""
    wd = run_workdir(run_id)
    if not wd:
        return b"", "该运行没有关联的工作目录"
    rel = (rel or "").replace("\\", "/").lstrip("/")
    if not rel:
        return b"", "缺少文件名"
    root = Path(wd).resolve()
    p = (root / rel).resolve()
    if root != p and root not in p.parents:
        return b"", "非法路径"
    if not p.is_file():
        return b"", "文件不存在"
    try:
        return p.read_bytes(), None
    except OSError as e:
        return b"", "读取失败: %s" % e


def delete_runs(run_ids):
    """批量删除运行记录。跳过排队中/运行中的记录。

    返回 (删除数, 跳过数, 错误信息)：非法 ID 与运行中的记录都计入跳过，
    合法且已结束的记录照常删除，不因个别非法 ID 整体失败。
    """
    if not isinstance(run_ids, (list, tuple)):
        return 0, 0, "ids 必须是数组"
    deleted, skipped, bad = 0, 0, 0
    for run_id in run_ids:
        rid = str(run_id)
        if not _valid_id(rid):
            bad += 1
            continue
        with LOCK:
            run = _RUNS.get(rid)
            if not run or run.get("status") in ("queued", "running"):
                skipped += 1
                continue
            del _RUNS[rid]
        _schedule_run_dir_cleanup(rid)
        deleted += 1
    if deleted:
        bump_state()
    return deleted, skipped, ("有 %d 条非法记录 ID" % bad) if bad else ""


def clear_runs():
    """一键清除全部运行记录（跳过排队中/运行中的）。返回 (删除数, 跳过数)。"""
    with LOCK:
        targets, skipped = [], 0
        for rid, r in _RUNS.items():
            if r.get("status") in ("queued", "running"):
                skipped += 1
            else:
                targets.append(rid)
        for rid in targets:
            del _RUNS[rid]
    for rid in targets:
        _schedule_run_dir_cleanup(rid)
    bump_state()
    return len(targets), skipped


def archive_task(task_id, archived=True):
    """归档/取消归档：归档后从默认列表与侧栏隐藏，数据保留，可随时恢复。
    运行中/排队也允许归档——归档只是隐藏，运行照常继续，不删任何东西。"""
    if not _valid_id(task_id):
        return False, "非法的任务 ID"
    with LOCK:
        task = _TASKS.get(task_id)
        if not task:
            return False, "任务不存在"
        task["archived"] = bool(archived)
        _save_json(paths.TASKS_DIR / (task_id + ".json"), task)
    return True, ""


def delete_task(task_id, actor=""):
    """删除任务及其全部运行记录（含日志与报告目录）。返回 (ok, 错误信息)。

    不可逆且无回收站，故落审计（谁/何时/删了什么——2026-09-28 连载根任务
    被删案：事后无人能回答这两问）。连载链的前序任务被后续任务 serial.continues
    依赖，删了会把整条链的「继续连载/断点续跑」地基抽掉——这类任务只许归档。"""
    if not _valid_id(task_id):
        return False, "非法的任务 ID"
    with LOCK:
        task = _TASKS.get(task_id)
        if not task:
            return False, "任务不存在"
        if task.get("status") in ("queued", "running"):
            return False, "运行中的任务不能删除，请先取消"
        if task.get("type") == "serial_novel":
            for other in _TASKS.values():
                if other.get("id") != task_id and \
                        (other.get("serial") or {}).get("continues") == task_id:
                    return False, "该任务是连载链的前序任务（后续任务依赖它），请用「归档」隐藏而不是删除"
        run_ids = []
        for rid, r in _RUNS.items():
            if r.get("task_id") != task_id:
                continue
            if r.get("status") in ("queued", "running"):
                return False, "有运行中的记录，请先取消"
            run_ids.append(rid)
        del _TASKS[task_id]
        for rid in run_ids:
            _RUNS.pop(rid, None)
    for rid in run_ids:
        _schedule_run_dir_cleanup(rid)
    (paths.TASKS_DIR / (task_id + ".json")).unlink(missing_ok=True)
    audit.record("task_delete", {
        "task_id": task_id,
        "title": task.get("title") or "",
        "type": task.get("type") or "",
        "status": task.get("status") or "",
        "workdir": task.get("workdir") or "",
        "run_ids": run_ids,
        "actor": actor or "local",
    })
    bump_state()
    return True, ""


def retry_task(task_id):
    """手动重试：为失败/已取消的任务再创建一次新运行。返回 (ok, 错误, run)。

    连载任务断点续跑：若上一次运行已产出大纲与部分章节成稿，新运行继承
    （inherit）大纲与已完成章号——流水线跳过这些章的起草（成稿/评审分数
    直接复用或重评），避免几十万字长篇因一次超时全部重来。
    """
    if not _valid_id(task_id):
        return False, "非法的任务 ID", None
    with LOCK:
        task = _TASKS.get(task_id)
        if not task:
            return False, "任务不存在", None
        for r in _RUNS.values():
            if r.get("task_id") == task_id and r.get("status") in ("queued", "running"):
                return False, "任务仍在运行中，不能重试", None
        run = create_run("orchestration", task.get("title") or task_id, task_id=task_id)
        # 运行中指挥继承：旧 run 里未消费的用户纠偏指令带入新 run——
        # 指令是针对目标的意图，不因一次超时/失败而丢（自动续跑同享）。
        # 旧 run 的消息保留原样（历史回放可见），新 run 里是未消费副本。
        inherited = {}
        delivered = set()
        for prev in _RUNS.values():
            if prev.get("task_id") != task_id or prev["id"] == run["id"]:
                continue
            for m in prev.get("messages") or []:
                key = (m.get("id"), m.get("created_at"), m.get("text"))
                if m.get("consumed"):
                    delivered.add(key)
                else:
                    inherited[key] = dict(m)
        if inherited:
            run["messages"] = [m for key, m in inherited.items() if key not in delivered][-50:]
        if task.get("serial"):
            # 按 id 倒序≈按时间倒序，但 id 后缀是随机数（_new_id），同秒双发
            # （watchdog 补队实测有同秒对）会乱序——账本/成稿都认「最新创建」，
            # 这里必须按创建时刻排（无 ns 的老 run 兜 0，id 决胜）。
            prev_runs = sorted((r for r in _RUNS.values()
                                if r.get("task_id") == task_id and r["id"] != run["id"]),
                               key=lambda r: (r.get("created_at_ns") or 0, r["id"]),
                               reverse=True)
            # 原因账与成稿解耦（2026-10-16 实案）：成稿（大纲/done 章/分数）取
            # 最新终态 prev；原因账（redo/redo_issues）独立向前扫最近一个
            # publishable=False 的完结 verdict——中间插着的失败 run（服务重启/
            # 超时/漂移闸拦截：落了步骤没出 verdict）不再把账本遮蔽成空表
            # （不周山续5：5151 判 3 条 major 后两日 13 跑全空账盲写实案）。
            # running/pending 的 prev 不作成稿与账本来源：retry_task 本身拒在
            # 跑任务，此闸是双实例/状态陈旧时代的兜底（r-20261012-160116 实案：
            # 活实例还在写、本实例按收尸态继承了半程快照）；达标完结
            # （publishable=True）即清账，更旧的未达标 verdict 不再追溯。
            outline = None
            done = []
            base_scores = []
            ledger = None       # (redo, redo_issues, scores)——账本已定，可为空账
            for prev in prev_runs:
                if outline is None:
                    o = prev.get("outline")
                    # 降级大纲没有真实情节，继承只会让每一遍都按空模板写废——
                    # 跳过它，让新运行重新生成（编排者恢复后即可拿到真大纲）。
                    # mock 模板的 outline 无 degraded 标记，正常继承不受影响。
                    if o and not o.get("degraded"):
                        outline = o
                if prev.get("status") not in ("done", "failed", "cancelled", "timeout"):
                    continue
                if not done:
                    got = sorted({int(s["role"].split("c")[-1])
                                  for s in (prev.get("steps") or [])
                                  if s.get("status") == "done"
                                  and (s.get("role") or "").startswith("draft-c")
                                  and str(s.get("role")).split("c")[-1].isdigit()})
                    if got:
                        done = got
                        base_scores = ((prev.get("verdict") or {}).get("chapter_scores")
                                       or prev.get("chapter_scores") or [])
                if outline is not None and done and ledger is not None:
                    break
                pv = prev.get("verdict") or {}
                if ledger is not None:
                    continue
                if pv.get("publishable") is True:
                    ledger = (set(), [], [])
                    continue
                # 重写未达标章：上一遍完整跑完但质量未过线（verdict.publishable
                # =False）时，未过线章的成稿与分数都不进继承——流水线对缺继承
                # 的章走正常起草+评审，等于只重写这几章；已过线章照常复用不烧
                # token。大纲始终继承（全章未过线时 done 清空也继承），保住全书
                # 结构——这正是「重写未达标章」按钮（前端 done+未达标态放行
                # retry）区别于断点续跑的语义。
                redo = set()
                redo_issues = []
                v_scores = pv.get("chapter_scores") or []
                if (prev.get("verdict") or {}).get("publishable") is False:
                    _pv = prev.get("verdict") or {}
                    # 病灶章以 note 文案点名为准（与 pipeline._chapters_named_in
                    # 同一正则口径）：major 的 chapter 字段常是评审/签约检查点
                    # 所在章而非病灶章——2026-10-13 神瞳实案：chapter=9、病灶在
                    # 第 6 章金额矛盾，只按字段回炉永远修不掉，重跑必然再被
                    # 同一条 major 点名，「反复写→反复不达标」死循环。
                    try:
                        _vlo = int(_pv.get("start_chapter") or 1)
                    except (TypeError, ValueError):
                        _vlo = 1
                    try:
                        _vhi = int(_pv.get("end_chapter") or 0) or (
                            _vlo + max(len((prev.get("outline") or {}).get(
                                "chapters") or []) - 1, 0))
                    except (TypeError, ValueError):
                        _vhi = _vlo + max(len((prev.get("outline") or {}).get(
                            "chapters") or []) - 1, 0)
                    redo = {int(c["chapter"]) for c in v_scores
                            if not c.get("passed") and c.get("chapter") is not None}
                    unlocated_majors = []
                    # 未达标原因账（verdict.major_issues，章节级+全局级）随继承
                    # 下发：流水线把本章点名项与全书级项渲染进重写章起草提示词，
                    # 作者第一遍就知道往哪改，不再盲写等修订拉回。被 major 点名
                    # 的过线章一并回炉——总判定卡在 major 上时只重写未过线章
                    # 消不掉账，「重写未达标章」会空转（2026-10-10 实案）。
                    for x in _pv.get("major_issues") or []:
                        if not isinstance(x, dict):
                            continue
                        redo_issues.append(x)
                        mentions = list(_CH_MENTION_RE.finditer(
                            str(x.get("note") or x.get("issue") or "")))
                        mentioned = bool(mentions)
                        for m in mentions:
                            a = int(m.group(1))
                            b = int(m.group(2)) if m.group(2) else a
                            if b < a:
                                a, b = b, a
                            for c in range(a, b + 1):
                                if _vlo <= c <= _vhi:
                                    redo.add(c)
                        try:
                            c = int(x.get("chapter"))
                            if _vlo <= c <= _vhi:
                                redo.add(c)
                            elif not mentioned and (c < _vlo or c > _vhi):
                                # 明确归属批次外的意见不能随意映射到本批。
                                mentioned = True
                        except (TypeError, ValueError):
                            c = None
                        if (x.get("severity") == "major" and not mentioned
                                and c is None):
                            unlocated_majors.append(x)
                    # 全局维度未过线时，旧逻辑只认未过章或 major 点名章。
                    # 若章节全过线、没有 major，全书评审失败便会沿用全部原稿，
                    # 再花一轮 token 得到同一结论。把全局短板映射到该维度得分
                    # 最低的批内章节，最多额外回炉两章；没有对应分数时不猜。
                    try:
                        threshold = float(_pv.get("threshold"))
                    except (TypeError, ValueError):
                        threshold = None
                    global_scores = _pv.get("global_scores") or {}
                    weak_dims = {}
                    if threshold is not None and isinstance(global_scores, dict):
                        weak_dims = {
                            str(dim): float(score)
                            for dim, score in global_scores.items()
                            if isinstance(score, (int, float)) and float(score) < threshold
                        }
                    if weak_dims:
                        candidates = []
                        for chapter_score in v_scores:
                            if not isinstance(chapter_score, dict):
                                continue
                            chapter = chapter_score.get("chapter")
                            try:
                                chapter = int(chapter)
                            except (TypeError, ValueError):
                                continue
                            if not _vlo <= chapter <= _vhi:
                                continue
                            means = chapter_score.get("means") or {}
                            comparable = [(dim, float(means[dim])) for dim in weak_dims
                                          if isinstance(means, dict)
                                          and isinstance(means.get(dim), (int, float))]
                            if comparable:
                                candidates.append((
                                    sum(score for _, score in comparable) / len(comparable),
                                    chapter, comparable))
                        remaining = [c for c in sorted(candidates) if c[1] not in redo]
                        for _, chapter, comparable in remaining[:2]:
                            redo.add(chapter)
                            for dim, score in comparable:
                                global_score = weak_dims[dim]
                                redo_issues.append({
                                    "chapter": chapter, "dim": dim, "severity": "major",
                                    "note": ("全局评审「%s」%.1f 低于阈值 %.1f；"
                                             "本章该维度 %.1f，请修复本章对全书的贡献"
                                             % (dim, global_score, threshold, score)),
                                })
                    # 全书级 major 没有点名章节时，原逻辑只把意见留在报告里，
                    # retry 会继承全部成稿并重跑同一评审，稿件不变、结论也不变。
                    # 按 major 的评审维度将原意见路由到该维度最低的批内章节；
                    # 维度无法对应时以本批章均分兜底。已有具体回炉章不重复计入，
                    # 每轮最多再选两章，避免全书重写和无限扩大成本。
                    if unlocated_majors and len(redo) < 2:
                        candidates = []
                        for chapter_score in v_scores:
                            if not isinstance(chapter_score, dict):
                                continue
                            chapter = chapter_score.get("chapter")
                            try:
                                chapter = int(chapter)
                            except (TypeError, ValueError):
                                continue
                            if not _vlo <= chapter <= _vhi or chapter in redo:
                                continue
                            means = chapter_score.get("means") or {}
                            vals = [float(v) for v in means.values()
                                    if isinstance(means, dict)
                                    and isinstance(v, (int, float))]
                            if not vals:
                                continue
                            candidates.append((sum(vals) / len(vals), chapter, means))
                        available = sorted(candidates)
                        targeted = set()
                        route_cap = max(0, 2 - len(redo))
                        for issue in unlocated_majors:
                            dim = str(issue.get("dim") or "")
                            matching = [c for c in available
                                        if isinstance(c[2], dict)
                                        and isinstance(c[2].get(dim), (int, float))]
                            for _, chapter, _means in (matching or available):
                                if chapter in targeted:
                                    continue
                                targeted.add(chapter)
                                redo.add(chapter)
                                routed = dict(issue)
                                routed["chapter"] = chapter
                                routed["note"] = (
                                    str(issue.get("note") or issue.get("issue") or "全书级问题")
                                    + "；请重点检查本章对该全书问题的贡献并逐条修复")
                                redo_issues.append(routed)
                                break
                            if len(targeted) >= route_cap:
                                break
                    # 账本落袋（循环后统一合成 inherit）：scores 先带该 verdict
                    # 里全部已过线章，回炉剔除在循环后做（已发布章豁免先行）。
                    ledger = (redo, redo_issues,
                              [c for c in v_scores if c.get("passed")])
            # 账本合成：回炉集先剔已发布章（平台与本地版本不能分叉），再从
            # 最新成稿里剔除；原因账随继承下发——达标续跑/断点续跑均为空表。
            redo, redo_issues, ledger_scores = ledger or (set(), [], [])
            if redo:
                from .publish import ledger as _pub_ledger
                published = (_pub_ledger.published_chapters(task_id, "fanqie")
                             | _pub_ledger.published_chapters(task_id, "qimao"))
                redo.difference_update(published)
                done = [n for n in done if n not in redo]
                ledger_scores = [c for c in ledger_scores
                                 if c.get("chapter") not in redo]
            if outline is not None and (done or redo):
                run["inherit"] = {
                    "outline": outline,
                    "done_chapters": done,
                    # 分数优先取 verdict（整轮成功时的最终账）；
                    # failed/cancelled 的 run 没有 verdict，退回每章实时
                    # 落账的 chapter_scores——否则多轮失败恢复会把全部
                    # 已过线章节重新评审（实测一晚白烧数百万 token）。
                    # 未达标重写（redo 非空）只带账本 verdict 里已过线
                    # 且不在回炉集的章分。
                    "chapter_scores": ledger_scores if redo else base_scores,
                    # 未达标重写的原因账（达标续跑/断点续跑时为空表）
                    "redo_issues": redo_issues,
                }
        else:
            # 断点续跑（代码等非连载任务，A+B）：上一遍失败/取消/超时 run 的
            # 实现会话（inherit_session，跨 run 续 CLI 会话）与断点简报
            # （handoff，走到哪/改了什么/还剩什么）继承给新 run——重试从断点
            # 继续而不是从零烧 token。done 的 run 不继承：主动重跑语义就是重来。
            prev_runs = sorted((r for r in _RUNS.values()
                                if r.get("task_id") == task_id and r["id"] != run["id"]),
                               key=lambda r: r["id"], reverse=True)
            for prev in prev_runs:
                if prev.get("status") not in ("failed", "cancelled", "timeout"):
                    continue
                if prev.get("impl_session") and "inherit_session" not in run:
                    run["inherit_session"] = dict(prev["impl_session"])
                if prev.get("handoff") and "handoff" not in run:
                    run["handoff"] = dict(prev["handoff"])
                if "inherit_session" in run or "handoff" in run:
                    break
        task["status"] = "queued"
        _save_json(paths.TASKS_DIR / (task_id + ".json"), task)
        _save_json(paths.RUNS_DIR / run["id"] / "run.json", run)
    return True, "", run


_CH_FILE_RE = re.compile(r"^chapter-(\d{1,4})\.md$")


def serial_book_chapter_numbers(task):
    """Return chapter numbers currently present in the serial work directory."""
    wd = str((task or {}).get("workdir") or "")
    if not wd:
        return []
    try:
        return sorted({int(m.group(1)) for p in Path(wd).glob("chapter-*.md")
                       if (m := _CH_FILE_RE.match(p.name))})
    except OSError:
        return []


def serial_book_progress(task):
    """这本书已经写到第几章：工作目录里的 chapter-*.md 是事实标准（续写批次
    共用同一目录，天然包含全部历史章）；文件缺失时沿 continues 链推算兜底。"""
    chapters = serial_book_chapter_numbers(task)
    last = max(chapters, default=0)
    if last:
        return last
    seen = set()
    cur = task
    while cur and cur.get("serial") and cur["id"] not in seen and len(seen) < 20:
        seen.add(cur["id"])
        s = cur["serial"]
        try:
            start = int(s.get("start_chapter") or 1)
        except Exception:
            start = 1
        runs = task_runs(cur["id"])
        batch = 0
        for r in runs:
            o = r.get("outline")
            if o and o.get("chapters") and not o.get("degraded"):
                batch = len(o["chapters"])
                break
        # 从没跑过的任务不计入进度：计划章数 ≠ 已写成章数
        if runs:
            batch = batch or int(s.get("chapters") or 0)
        if batch:
            last = max(last, start + batch - 1)
        cur = get_task(str(s.get("continues") or ""))
    return last


def serial_early_finish_plan(task):
    """Return the active shortened-ending plan for a serial chain, if any."""
    cur, seen = task, set()
    while cur and cur.get("id") not in seen and len(seen) < 50:
        seen.add(cur.get("id"))
        plan = _norm_early_finish((cur.get("serial") or {}).get("early_finish"))
        if plan:
            return plan
        parent = str((cur.get("serial") or {}).get("continues") or "")
        cur = get_task(parent) if parent else None
    return None


def continue_info(task_id):
    """「继续连载」弹框数据：能否续、已写到第几章、默认续几章。任务不存在返回 None。"""
    task = get_task(task_id)
    if not task:
        return None
    serial = task.get("serial") or {}
    try:
        default_ch = int(serial.get("chapters") or 8)
    except Exception:
        default_ch = 8
    plan = serial_early_finish_plan(task)
    info = {"task_id": task_id, "serial": bool(serial),
            "last_chapter": 0, "default_chapters": default_ch,
            "words_per_chapter": int(serial.get("words_per_chapter") or 2500),
            "can": False, "reason": "",
            "early_finish": plan or None,
            "planned_end_chapter": (plan or {}).get("target_chapter") if plan else None,
            "remaining_chapters": None,
            "early_finish_state": "none",
            "early_finish_latest_task_id": None,
            "early_finish_latest_task_status": None,
            "early_finish_latest_run_id": None,
            "early_finish_latest_run_status": None,
            "early_finish_chapters_reached": False,
            "early_finish_review_passed": False,
            "early_finish_content_complete": False,
            "early_finish_publish_progress": [],
            "early_finish_publication_complete": False,
            "orphaned_chapters": [],
            "missing_chapters": [],
            "can_update_early_finish": False,
            "can_cancel_early_finish": False}
    if not serial:
        info["reason"] = "只有连载任务支持继续连载"
        return info

    chain = serial_chain_ids(task_id) or [task_id]
    latest_id = chain[-1]
    latest_task = get_task(latest_id) or task
    latest_runs = task_runs(latest_id)
    latest_run = latest_runs[0] if latest_runs else None
    info["early_finish_latest_task_id"] = latest_id
    info["early_finish_latest_task_status"] = latest_task.get("status") or ""
    info["early_finish_latest_run_id"] = (latest_run or {}).get("id")
    info["early_finish_latest_run_status"] = (latest_run or {}).get("status") or ""

    # A normal continuation may be started from any batch in the chain: use
    # the newest batch as the operation source so the next child cannot fork
    # from an older batch. Early-ending plans are stricter and keep the old
    # batch read-only below, because their target must not be bypassed.
    if latest_id != task_id and not plan:
        task = latest_task
        serial = task.get("serial") or {}
        plan = serial_early_finish_plan(task)

    # A continuation chain shares one work directory; always summarize progress
    # from the newest batch so opening an older batch cannot show stale state.
    raw_last = serial_book_progress(latest_task)
    last = min(raw_last, int(plan["target_chapter"])) if plan else raw_last
    info["last_chapter"] = last
    if plan:
        target = int(plan["target_chapter"])
        chapter_numbers = serial_book_chapter_numbers(latest_task)
        chapter_set = set(chapter_numbers)
        info["orphaned_chapters"] = [n for n in chapter_numbers if n > target]
        info["missing_chapters"] = [n for n in range(1, target + 1)
                                     if n not in chapter_set]
        info["remaining_chapters"] = max(0, target - last)
        info["early_finish_chapters_reached"] = last >= target
        verdict = (latest_run or {}).get("verdict") or {}
        try:
            verdict_end = int(verdict.get("end_chapter") or 0)
        except (TypeError, ValueError):
            verdict_end = 0
        content_complete = bool(
            latest_run and latest_run.get("status") == "done"
            and verdict_end >= target and not info["missing_chapters"])
        review_passed = bool(content_complete and verdict.get("publishable") is True)
        info["early_finish_review_passed"] = review_passed
        info["early_finish_content_complete"] = content_complete

        # Editorial completion and platform publication are separate states.
        # Never imply that a written/approved ending is fully published while
        # chapters are still merely drafted or queued on a platform.
        try:
            from .publish import ledger, manager
            platforms = {str(platform) for platform in ledger.books_for(task_id)
                         if str(platform) in manager.PLATFORMS}
            for tid in chain:
                chained = get_task(tid) or {}
                auto = chained.get("auto_publish")
                platform = str((auto or {}).get("platform") or "")
                if (isinstance(auto, dict) and auto.get("enabled")
                        and platform in manager.PLATFORMS):
                    platforms.add(platform)
            publish_progress = []
            chapter_range = set(range(1, target + 1))
            for platform in sorted(platforms):
                published = set(ledger.published_chapters(task_id, platform)) & chapter_range
                scheduled = set(ledger.scheduled_chapters(task_id, platform)) & chapter_range
                drafted = set(ledger.drafted_chapters(task_id, platform)) & chapter_range
                outstanding = chapter_range - published - scheduled - drafted
                publish_progress.append({
                    "platform": platform,
                    "published": len(published),
                    "scheduled": len(scheduled - published),
                    "drafted": len(drafted - published - scheduled),
                    "outstanding": len(outstanding),
                })
            info["early_finish_publish_progress"] = publish_progress
            info["early_finish_publication_complete"] = bool(
                publish_progress and all(row["outstanding"] == 0
                                         and row["scheduled"] == 0
                                         and row["drafted"] == 0
                                         for row in publish_progress))
        except Exception:
            # Publishing is optional; a ledger hiccup must not block writing.
            info["early_finish_publish_progress"] = []
            info["early_finish_publication_complete"] = False
        latest_run_status = info["early_finish_latest_run_status"]
        latest_status = (latest_run_status
                         or info.get("early_finish_latest_task_status"))
        if latest_status in ("queued", "running"):
            info["early_finish_state"] = latest_status
        elif review_passed:
            info["early_finish_state"] = "completed"
        elif content_complete:
            info["early_finish_state"] = "review_failed"
        elif latest_run_status in ("failed", "cancelled", "timeout"):
            info["early_finish_state"] = latest_run_status
        elif last >= target:
            info["early_finish_state"] = "chapters_reached"
        else:
            info["early_finish_state"] = "planned"

    # Check the whole chain before returning the snapshot. This keeps the
    # summary above available while still preventing edits during active work.
    chain_tasks = [get_task(tid) for tid in chain]
    if latest_id != task_id and plan:
        latest_status = (info.get("early_finish_latest_run_status")
                         or info.get("early_finish_latest_task_status") or "未知")
        info["reason"] = ("请从最新连载批次继续，避免旧批次重复规划；"
                          "最新批次状态：%s" % latest_status)
        return info
    if any(t and t.get("status") in ("queued", "running") for t in chain_tasks):
        info["reason"] = "连载任务还在运行中，等结束或取消后再操作"
        return info
    if any(r.get("task_id") in chain and r.get("status") in ("queued", "running")
           for r in _RUNS.values()):
        info["reason"] = "连载有运行中的记录，请先取消或等待结束"
        return info
    if plan and info["early_finish_review_passed"]:
        info["reason"] = "提前完结内容与评审已完成"
        if info["early_finish_publish_progress"] and not info["early_finish_publication_complete"]:
            info["reason"] += "；仍有章节待在平台正式发布"
        return info
    if plan and last >= int(plan["target_chapter"]):
        info["reason"] = "最终章范围已达到，但最新批次尚未完成终稿评审"
        if info["missing_chapters"]:
            info["reason"] += "；缺少章节：%s" % ", ".join(
                str(n) for n in info["missing_chapters"][:20])
        if info["orphaned_chapters"]:
            info["reason"] += "；最终章之后的遗留稿不会自动发布"
        return info
    if last < 1:
        info["reason"] = "还没写成任何一章（工作目录里没有 chapter-*.md），先跑完一次连载"
        return info
    info["can"] = True
    if plan:
        info["can_update_early_finish"] = True
        info["can_cancel_early_finish"] = True
    return info


def continue_task(task_id, chapters=None):
    """在此基础上新建任务继续连载：沿用目标/上下文/目录/评审设置，从已写到
    的下一章接着写（章节文件与成书合并按全书章号衔接，旧章不动）。
    返回 (ok, 错误, 新任务)。"""
    task = get_task(task_id)
    if not task:
        return False, "任务不存在", None
    info = continue_info(task_id)
    if not info.get("can"):
        return False, info.get("reason") or "当前不能继续连载", None
    latest_id = info.get("early_finish_latest_task_id") or task_id
    if latest_id != task_id:
        latest = get_task(latest_id)
        if not latest:
            return False, "最新连载批次不存在，请刷新后重试", None
        task_id = latest_id
        task = latest
    serial = task["serial"]
    plan = serial_early_finish_plan(task)
    remaining = None
    if plan:
        remaining = max(0, int(plan["target_chapter"]) - info["last_chapter"])
        if remaining <= 0:
            return False, "已达到提前完结规划的最终章", None
    try:
        batch = max(1, min(20, int(chapters or serial.get("chapters") or 8)))
    except Exception:
        batch = max(1, min(20, int(serial.get("chapters") or 8)))
    if remaining is not None:
        batch = min(batch, remaining)
    # 标题：去掉历史「·续N」后缀取根名，按链条代数标 ·续 / ·续2 / ·续3…
    base_title = re.sub(r"·续\d*$", "", task.get("title") or "").strip() or task_id
    gen, cur, seen = 0, task, set()
    while cur and cur["id"] not in seen and len(seen) < 20:
        seen.add(cur["id"])
        gen += 1
        cur = get_task(str((cur.get("serial") or {}).get("continues") or ""))
    payload = {
        "type": task["type"],
        "title": base_title + ("·续" if gen <= 1 else "·续%d" % gen),
        "goal": task.get("goal") or "",
        "context": task.get("context") or "",
        "workdir": task.get("workdir") or "",
        "mode": task.get("mode") or "auto",
        "difficulty": task.get("difficulty") or "auto",
        "implementer": task.get("implementer") or "",
        "critics": task.get("critics") or [],
        "acceptance_criteria": task.get("acceptance_criteria") or [],
        "acceptance_thresholds": dict(task.get("acceptance_thresholds") or {}),
        "approval_required": bool(task.get("approval_required")),
        "manuscript": task.get("manuscript") or "manuscript.md",
        "threshold": task.get("threshold") or 7.0,
        "serial": {"chapters": batch,
                   "words_per_chapter": int(serial.get("words_per_chapter") or 2500),
                   "start_chapter": info["last_chapter"] + 1,
                   "continues": task_id},
    }
    if plan:
        payload["serial"]["early_finish"] = plan
    if serial.get("variants"):   # 赛马配置随链条沿用
        payload["serial"]["variants"] = serial["variants"]
    # 多线推演（branches）同口径随链条沿用：漏带则续写批次恒回 1，
    # 多线剧情推演在连载链上只剩首批生效
    if serial.get("branches"):
        payload["serial"]["branches"] = serial["branches"]
    # 分卷配置随链条沿用：卷边界必须跨批次稳定，否则同一卷会被续写批次重切
    if serial.get("volumes"):
        payload["serial"]["volumes"] = serial["volumes"]
    if serial.get("volume_chapters"):
        payload["serial"]["volume_chapters"] = serial["volume_chapters"]
    for key in ("draft_prompt", "critique_prompt"):  # 自定义提示词覆盖一并沿用
        if task.get(key):
            payload[key] = task[key]
    try:
        new = create_task(payload)
    except ValueError as e:
        return False, str(e), None
    # A scheduled publishing policy belongs to the book chain, not one batch.
    # Carry it to the new batch so chapters produced by the shortened ending
    # continue through the same unattended publishing workflow.
    if isinstance(task.get("auto_publish"), dict):
        set_auto_publish(new["id"], dict(task["auto_publish"]))
        new = get_task(new["id"]) or new
    return True, "", new


def early_finish_info(task_id):
    """Return the current ending-plan state for the early-finish UI."""
    info = continue_info(task_id)
    if info is None:
        return None
    info["can"] = bool(info.get("can") and not info.get("early_finish"))
    if info.get("early_finish"):
        info["reason"] = "已有提前完结规划；后续批次会按最终章继续完成"
    return info


def early_finish_existing_task(task_id, target_chapter):
    """Return an existing ending batch for an idempotent repeated request."""
    task = get_task(task_id)
    if not task:
        return None
    plan = serial_early_finish_plan(task)
    if not plan:
        return None
    try:
        target = int(target_chapter)
    except (TypeError, ValueError):
        return None
    if target != int(plan.get("target_chapter") or 0):
        return None
    chain = serial_chain_ids(task_id)
    return get_task(chain[-1]) if chain else task


def early_finish_task(task_id, target_chapter, reason=""):
    """Create the first shortened-ending continuation batch.

    Existing chapter files remain the source of truth. The new batch carries a
    durable target so later continuation cannot accidentally grow the book past
    the replanned ending.
    """
    task = get_task(task_id)
    if not task:
        return False, "任务不存在", None
    if not task.get("serial"):
        return False, "只有连载任务支持提前完结", None
    if serial_early_finish_plan(task):
        return False, "该连载已经有提前完结规划", None
    info = continue_info(task_id)
    if not info or not info.get("can"):
        return False, (info or {}).get("reason") or "当前不能提前完结", None
    try:
        target = int(target_chapter)
    except (TypeError, ValueError):
        return False, "最终章号必须是整数", None
    if target > 500:
        return False, "最终章号不能超过 500", None
    if target < 1:
        return False, "最终章号必须是正整数", None
    if target <= int(info.get("last_chapter") or 0):
        return False, "最终章号必须大于当前已完成章节", None
    plan = {"target_chapter": target, "source_task": task_id,
            "requested_at": time.strftime("%Y-%m-%d %H:%M:%S")}
    if str(reason or "").strip():
        plan["reason"] = str(reason).strip()[:240]
    batch = min(20, target - int(info["last_chapter"]))
    # Pin the plan on the current task and chain root before creating the child
    # batch. This closes the bypass where an older batch's entry could start an
    # uncapped branch after early-finish was requested. Re-check under LOCK so
    # two rapid clicks cannot create competing ending plans.
    pinned_ids = []
    previous = {}
    with LOCK:
        current = _TASKS.get(task_id) or get_task(task_id)
        if not current:
            return False, "任务不存在", None
        if serial_early_finish_plan(current):
            return False, "该连载已经有提前完结规划", None
        root = current
        seen = set()
        while root and root.get("id") not in seen:
            seen.add(root.get("id"))
            parent_id = str((root.get("serial") or {}).get("continues") or "")
            parent = get_task(parent_id) if parent_id else None
            if not parent:
                break
            root = parent
        for item in (root, current):
            if not item or item.get("id") in pinned_ids:
                continue
            tid = item["id"]
            pinned_ids.append(tid)
            previous[tid] = dict((item.get("serial") or {}).get("early_finish") or {})
            item["serial"] = dict(item.get("serial") or {})
            item["serial"]["early_finish"] = plan
            _save_json(paths.TASKS_DIR / (tid + ".json"), item)
    ok, err, new = continue_task(task_id, chapters=batch)
    if not ok:
        with LOCK:
            for tid in pinned_ids:
                item = _TASKS.get(tid) or get_task(tid)
                if not item:
                    continue
                if previous.get(tid):
                    item["serial"]["early_finish"] = previous[tid]
                else:
                    item["serial"].pop("early_finish", None)
                _save_json(paths.TASKS_DIR / (tid + ".json"), item)
        return False, err, None
    return True, "", new


def update_early_finish_task(task_id, target_chapter=None, reason=None, cancel=False):
    """Adjust or cancel an existing early-ending plan before it is complete."""
    task = get_task(task_id)
    if not task:
        return False, "任务不存在", None
    plan = serial_early_finish_plan(task)
    if not plan:
        return False, "当前没有提前完结规划", None
    info = continue_info(task_id)
    if not info:
        return False, "任务不存在", None
    latest_id = info.get("early_finish_latest_task_id") or task_id
    if latest_id != task_id:
        # Details for a serial chain are often opened on the first batch. Keep
        # those controls useful by resolving the operation to the current
        # latest batch instead of forcing the user to hunt through the chain.
        latest = get_task(latest_id)
        if not latest:
            return False, "最新连载批次不存在，请刷新后重试", None
        task_id = latest_id
        task = latest
        info = continue_info(task_id)
        plan = serial_early_finish_plan(task)
        if not info or not plan:
            return False, "当前没有提前完结规划", None
    if info.get("early_finish_state") == "completed":
        return False, "提前完结规划已经完成，不能再修改", None
    if task.get("status") in ("queued", "running"):
        return False, "任务正在运行中，完成或取消后再调整", None
    for run in _RUNS.values():
        if run.get("task_id") == task_id and run.get("status") in ("queued", "running"):
            return False, "有运行中的记录，请先取消", None
    chain = serial_chain_ids(task_id) or [task_id]
    if cancel:
        with LOCK:
            for tid in chain:
                item = _TASKS.get(tid) or get_task(tid)
                if not item:
                    continue
                serial_data = dict(item.get("serial") or {})
                serial_data.pop("early_finish", None)
                item["serial"] = serial_data
                _save_json(paths.TASKS_DIR / (tid + ".json"), item)
            bump_state()
        return True, "", get_task(task_id)
    try:
        target = int(target_chapter)
    except (TypeError, ValueError):
        return False, "最终章号必须是整数", None
    if target > 500:
        return False, "最终章号不能超过 500", None
    if target < 1:
        return False, "最终章号必须是正整数", None
    last = int(info.get("last_chapter") or 0)
    if target <= last:
        return False, "最终章号必须大于当前已完成章节", None
    current_target = int(plan.get("target_chapter") or 0)
    if target < current_target:
        try:
            from .publish import ledger, manager
            published = set()
            scheduled = set()
            for platform in manager.PLATFORMS:
                published.update(ledger.published_chapters(task_id, platform))
                scheduled.update(ledger.scheduled_chapters(task_id, platform))
            published_ahead = sorted(n for n in published if n > target)
            scheduled_ahead = sorted(n for n in scheduled if n > target)
        except Exception:
            # A target reduction must not silently cross publication state that
            # could not be verified. The user can retry after the ledger recovers.
            return False, "无法核对发布台账，暂不能缩短最终章", None
        if published_ahead:
            return False, ("最终章不能早于已发布的第 %d 章" % published_ahead[-1]), None
        if scheduled_ahead:
            return False, ("最终章不能早于已排期的第 %d 章" % scheduled_ahead[-1]), None
    revised = dict(plan)
    revised["target_chapter"] = target
    if reason is not None:
        text = str(reason).strip()
        if text:
            revised["reason"] = text[:240]
        else:
            revised.pop("reason", None)
    revised["updated_at"] = time.strftime("%Y-%m-%d %H:%M:%S")
    with LOCK:
        for tid in chain:
            item = _TASKS.get(tid) or get_task(tid)
            if not item:
                continue
            serial_data = dict(item.get("serial") or {})
            serial_data["early_finish"] = dict(revised)
            item["serial"] = serial_data
            _save_json(paths.TASKS_DIR / (tid + ".json"), item)
        bump_state()
    return True, "", get_task(task_id)


def set_task_git_state(task_id, state):
    """任务分支裁决状态：isolated（有分支待裁决）/ merged / discarded / None（清除）。

    run 检出任务分支时置 isolated，人审合并/丢弃后置终态，新一轮 run 又会
    重置回 isolated。返回 (ok, 错误信息)。
    """
    if not _valid_id(task_id):
        return False, "非法的任务 ID"
    with LOCK:
        task = _TASKS.get(task_id)
        if not task:
            return False, "任务不存在"
        if state:
            task["git_state"] = state
        else:
            task.pop("git_state", None)
        _save_json(paths.TASKS_DIR / (task_id + ".json"), task)
        bump_state()
    return True, ""


def rename_task(task_id, title):
    """重命名任务：同步更新任务与全部运行记录的标题。

    侧栏任务树按 run.title 显示组名，只改任务会让历史运行仍顶着旧名，
    所以两者一起改（运行 json 逐个回写）。
    """
    if not _valid_id(task_id):
        return False, "非法的任务 ID"
    title = str(title or "").strip()
    if not title:
        return False, "标题不能为空"
    if len(title) > 120:
        return False, "标题过长（最多 120 字）"
    with LOCK:
        task = _TASKS.get(task_id)
        if not task:
            return False, "任务不存在"
        if task.get("title") == title:
            return True, ""
        task["title"] = title
        _save_json(paths.TASKS_DIR / (task_id + ".json"), task)
        for r in _RUNS.values():
            if r.get("task_id") == task_id:
                r["title"] = title
                _save_json(paths.RUNS_DIR / r["id"] / "run.json", r)
        bump_state()
    return True, ""


def update_task_params(task_id, patch):
    """更新任务编排参数（对话条三件套：mode/thinking/direct_provider_id/direct_model，
    另有 timeout_s 任务总时限）。
    只在任务未在跑时生效——运行中改参数不会影响当前执行，静默跳过避免误导。

    修订 CAS（借鉴 WorkDSH 契约纪律 expectedRevision）：patch 携带整数
    expected_rev 时与任务当前 rev 比对，不符=别处已先改——拒绝写入并说明，
    不做过期覆盖；不带 expected_rev 的旧调用保持原行为。写成功 rev+1，
    调用方（路由）从 get_task 回读新 rev 交前端续用。"""
    if not _valid_id(task_id):
        return False, "非法的任务 ID"
    patch = dict(patch) if isinstance(patch, dict) else {}
    expected_rev = patch.pop("expected_rev", None)
    with LOCK:
        task = _TASKS.get(task_id)
        if not task:
            return False, "任务不存在"
        if task.get("status") in ("queued", "running"):
            return False, "任务运行中，参数不可改"
        if expected_rev is not None:
            try:
                want_rev = int(expected_rev)
                cur_rev = int(task.get("rev") or 1)
            except (TypeError, ValueError):
                return False, "expected_rev 必须是整数"
            if want_rev != cur_rev:
                return False, ("任务参数已被其他窗口修改（当前修订 %d），"
                               "已拒绝过期写入，界面稍后自动同步最新值" % cur_rev)
        thresholds = None
        if "acceptance_thresholds" in patch:
            from . import acceptance
            try:
                thresholds = acceptance.normalize_thresholds(patch["acceptance_thresholds"])
            except ValueError as exc:
                return False, str(exc)
        if task.get("engine") == "direct":
            pv = str(patch.get("direct_provider_id", task.get("direct_provider_id")) or "").strip()[:80]
            mv = str(patch.get("direct_model", task.get("direct_model")) or "").strip()[:160]
            if mv and not pv:
                return False, "指定对话模型时必须同时指定厂商"
        before_params = {key: task.get(key) for key in patch}
        changed = False
        mode = str(patch.get("mode") or "").strip()
        if mode in ("auto", "fast", "expert", "manual"):
            if task.get("mode") != mode:
                task["mode"] = mode
                changed = True
        thinking = str(patch.get("thinking") or "").strip()
        if thinking in ("auto", "low", "standard", "high"):
            if task.get("thinking") != thinking:
                task["thinking"] = thinking
                changed = True
        if task.get("engine") == "direct":
            if task.get("direct_provider_id") != pv or task.get("direct_model") != mv:
                task["direct_provider_id"] = pv
                task["direct_model"] = mv
                changed = True
            # 执行 CLI 可改/可清（清空 = 回内置智能体或模型绑定）；与模型绑定
            # 允许共存，运行时 CLI 优先
            av = str(patch.get("direct_agent", task.get("direct_agent")) or "").strip()[:80]
            if task.get("direct_agent") != av and (av or task.get("direct_agent")):
                task["direct_agent"] = av
                changed = True
        # 任务总时限（秒）：建任务时按工作量给默认值，此处支持空闲时显式改。
        # 下次重试/续跑按新值重新给预算；已在跑的 run 保持原截止时间。
        # 统一钳制 [1s, 7天]，脏值回落基础默认。
        raw_ts = patch.get("timeout_s")
        if raw_ts not in (None, ""):
            new_ts = _task_timeout_s(raw_ts)
            if task.get("timeout_s") != new_ts:
                task["timeout_s"] = new_ts
                changed = True
        if thresholds is not None and task.get("acceptance_thresholds") != thresholds:
            task["acceptance_thresholds"] = thresholds
            changed = True
        if changed:
            from .execution_config import FIELDS
            sources = task.setdefault("configuration_sources", {})
            for key in FIELDS:
                if key in patch and before_params.get(key) != task.get(key):
                    sources[key] = "task.params"
            task["rev"] = int(task.get("rev") or 1) + 1
            _save_json(paths.TASKS_DIR / (task_id + ".json"), task)
            bump_state()
    return True, ""


def add_step(run_id, role, agent_id, agent_label, note="", model="", provider="", configuration=None):
    with LOCK:
        run = _RUNS.get(run_id)
        if not run:
            return None, None
        n = len(run["steps"]) + 1
        step = {
            "n": n, "role": role, "agent": agent_id, "agent_label": agent_label,
            "note": note,
            # 起跑即记解析到的模型：运行中蜂巢卡/步骤列表就能看到「在用哪个模型」，
            # 不必等 finish_step 用实际结果覆盖（换将成功后以实际为准）
            "model": str(model or "")[:80],
            # 绑定速记（借鉴 WorkDSH ResolvedExecutionBinding）：本步实际用的
            # provider 链与上游 host——烧错配额/模型漂移直接对账步骤行。
            # 只记身份不记密钥；老数据无此键，读取一律 .get
            "provider": str(provider or "")[:120],
            "status": "running", "started_at": time.strftime("%H:%M:%S"),
            "started_at_epoch": time.time(),
            "trace_id": str(run.get("trace_id") or tracing.trace_id(run_id)),
            "span_id": tracing.span_id(run_id, n),
            "parent_span_id": str(run.get("root_span_id") or
                                   tracing.root_span_id(run_id)),
            "ended_at": None, "duration_s": None, "exit_code": None,
            "summary": "", "log": None, "cost_usd": 0.0, "tokens": 0,
        }
        if configuration is not None:
            import copy
            step["configuration"] = copy.deepcopy(configuration)
        run["steps"].append(step)
        log_rel = "steps/%02d-%s-%s.log" % (n, _safe_name(role), _safe_name(agent_id))
        step["log"] = log_rel
        _save_json(paths.RUNS_DIR / run_id / "run.json", run)
        log_abs = paths.RUNS_DIR / run_id / log_rel
        log_abs.parent.mkdir(parents=True, exist_ok=True)
        return step, log_abs


def stream_step(run_id, n, thinking=None, text=None, activity=None, min_secs=0.6):
    """步骤运行中实时落一段「思考过程/正文/活动」——对话时间线边跑边打印。

    直连对话此前整轮只有三点打字动画（32 秒黑箱，用户 2026-09-22 反馈）；
    模型的思维链与正文在流式回调里到达，这里节流落盘（默认 0.6s 一次），
    前端 2s 轮询一次读到的就是「它正在想什么」。

    节流按步骤记忆上次落盘时刻；收尾时 finish_step 会写入最终值并清掉
    live 标记。返回是否真的落盘（调用方可据此决定要不要做下一步）。
    """
    key = (run_id, int(n))
    now = time.time()
    with _STREAM_LOCK:
        last = _STREAM_TS.get(key, 0)
        if min_secs and now - last < min_secs:
            return False
        _STREAM_TS[key] = now
    with LOCK:
        run = _RUNS.get(run_id)
        if not run:
            return False
        for s in run["steps"]:
            if s["n"] != n:
                continue
            if thinking is not None:
                s["thinking"] = str(thinking)[-LIVE_TEXT_MAX:]
            if text is not None:
                s["stream"] = str(text)[-LIVE_TEXT_MAX:]
            if activity:
                acts = list(s.get("activity") or [])
                if not acts or acts[-1] != activity:
                    acts.append(str(activity)[:200])
                s["activity"] = acts[-8:]
            s["live"] = int(s.get("live") or 0) + 1   # 前端重绘签名（内容变即刷新）
            _save_json(paths.RUNS_DIR / run_id / "run.json", run)
            return True
    return False


def clear_stream_state(run_id=None, n=None):
    """清掉流式节流状态（步骤收尾/测试收场用），避免字典随长跑无限增长。"""
    with _STREAM_LOCK:
        if run_id is None:
            _STREAM_TS.clear()
            return
        for key in [k for k in _STREAM_TS if k[0] == run_id and (n is None or k[1] == n)]:
            _STREAM_TS.pop(key, None)


def finish_step(run_id, n, status, summary="", exit_code=None,
                cost_usd=0.0, tokens=0.0, duration_s=None, model=None, output=None,
                followups=None, thinking=None, partial=False, acp_events=None,
                command=None, verify_command=None, binding_result=None):
    with LOCK:
        run = _RUNS.get(run_id)
        if not run:
            return
        for s in run["steps"]:
            if s["n"] == n:
                if binding_result is not None:
                    s.setdefault("configuration", {})["result"] = binding_result
                s["status"] = status
                s["ended_at"] = time.strftime("%H:%M:%S")
                s["ended_at_epoch"] = time.time()
                # 步骤摘要是 CLI 文本的汇聚点（失败时=错误尾巴，成功时=智能体结论）：
                # 落盘前统一清洗，避免 ANSI/覆写/乱码墙进 UI 与报告
                s["summary"] = runner.clean_cli_text(summary)
                s["exit_code"] = exit_code
                s["cost_usd"] = round(cost_usd, 4)
                s["tokens"] = tokens
                if model:
                    s["model"] = str(model)[:80]
                if command:
                    s["command"] = ([str(x)[:500] for x in command]
                                     if isinstance(command, list) else str(command)[:1000])
                if verify_command:
                    s["verify_command"] = _redact_command(verify_command, limit=1000)
                if duration_s is not None:
                    s["duration_s"] = round(duration_s, 1)
                elif s.get("started_at_epoch") is not None:
                    s["duration_s"] = round(max(
                        0.0, s["ended_at_epoch"] - s["started_at_epoch"]), 1)
                if output is not None:
                    # output 是智能体正文（对话气泡直读）：只剥 ANSI，不做噪声折叠
                    # ——正文里的装饰性长串是作者写的，不能替它省略
                    s["output"] = runner.strip_ansi(str(output))[:6000]
                if thinking is not None:
                    s["thinking"] = runner.strip_ansi(str(thinking))[:LIVE_TEXT_MAX]
                if followups:
                    s["followups"] = list(followups)[:3]
                if partial:
                    s["partial"] = True
                else:
                    s.pop("partial", None)
                if acp_events:
                    s["acp_events"] = [dict(item) for item in acp_events[:200]
                                       if isinstance(item, dict)]
                # 收尾即清运行中态：stream/activity/live 只是过程快照，留着会让
                # 前端把「已结束」的步骤仍当实时流渲染（也白占 run.json 体积）
                s.pop("stream", None)
                s.pop("activity", None)
                s.pop("live", None)
                break
        run["cost_usd"] = round(run.get("cost_usd", 0.0) + cost_usd, 4)
        run["tokens"] = run.get("tokens", 0) + tokens
        _save_json(paths.RUNS_DIR / run_id / "run.json", run)
    clear_stream_state(run_id, n)


# ---------------------------------------------------------------- 运行中指挥（消息信箱）
# 用户可在任务运行中往编排者「递话」：文字 + 附件（截图/文件）。消息先进信箱，
# 下一个智能体步骤开始前由 pipeline drain 出来注入 prompt——不插进正在跑的进程
# （无头 CLI 没有交互 stdin），而是在最近的轮间安全点生效。consumed 标记防重复注入。

def _ensure_messages(run):
    if "messages" not in run or not isinstance(run.get("messages"), list):
        run["messages"] = []
    return run["messages"]


def append_run_inject(run_id, text):
    """追加钩子注入文本到 run.hook_inject（message_submit 钩子通道）。
    直连轮间组装提示词时消费一次并清零。返回 bool。"""
    text = str(text or "").strip()
    if not text:
        return False
    with LOCK:
        run = _RUNS.get(run_id)
        if not run:
            return False
        old = str(run.get("hook_inject") or "")
        run["hook_inject"] = (old + ("\n\n" if old and text else "") + text)[:4000]
        _save_json(paths.RUNS_DIR / run_id / "run.json", run)
        return True


def pop_run_inject(run_id):
    """取出并清空 hook_inject（直连轮间提示词头一次性消费）。"""
    with LOCK:
        run = _RUNS.get(run_id)
        if not run:
            return ""
        txt = str(run.get("hook_inject") or "")
        if txt and "hook_inject" in run:
            run["hook_inject"] = ""
            try:
                _save_json(paths.RUNS_DIR / run_id / "run.json", run)
            except Exception:
                pass
        return txt


def add_message(run_id, text, sender="本机", attachments=None):
    """往运行信箱追加一条用户指令。attachments 为已落盘到 workdir 的
    附件相对路径清单（由 main.py 提交后传入）。返回消息 dict 或 None。"""
    text = str(text or "").strip()
    atts = [str(a) for a in (attachments or []) if a][:12]
    if not text and not atts:
        return None
    if len(text) > 4000:
        text = text[:4000]
    with LOCK:
        run = _RUNS.get(run_id)
        if not run:
            return None
        msgs = _ensure_messages(run)
        if len(msgs) >= 200:  # 信箱封顶：只保留最近 200 条，防 run.json 无限膨胀
            del msgs[:len(msgs) - 199]
        # id 取「现存最大 +1」而非 len+1：撤回/封顶删除后 len 会回落，
        # 用 len+1 会和存活消息撞号，前端按 id 撤回就错杀。
        nxt = 1
        for m in msgs:
            try:
                nxt = max(nxt, int(m.get("id") or 0) + 1)
            except (TypeError, ValueError):
                pass
        msg = {
            "id": "%06d" % nxt,
            "text": text, "sender": str(sender or "")[:24],
            "attachments": atts,
            "created_at": time.strftime("%H:%M:%S"),
            "consumed": False,
        }
        msgs.append(msg)
        _save_json(paths.RUNS_DIR / run_id / "run.json", run)
        return msg


def drain_messages(run_id, consumed_by=None):
    """取出并标记全部未消费消息（运行中指挥注入点调用）。

    consumed_by：{"step": 步号, "role": 角色}——送达回执，记录指令最终进了
    哪个步骤，详情页「✓已下达」据此显示去向。返回 [{text,attachments,...}]。"""
    with LOCK:
        run = _RUNS.get(run_id)
        if not run:
            return []
        pend = [m for m in _ensure_messages(run) if not m.get("consumed")]
        if not pend:
            return []
        receipt = None
        if consumed_by:
            try:
                receipt = {"step": int(consumed_by.get("step") or 0),
                           "role": str(consumed_by.get("role") or "")[:40]}
            except Exception:
                receipt = None
        for m in pend:
            m["consumed"] = True
            if receipt:
                m["consumed_by"] = dict(receipt)
        _save_json(paths.RUNS_DIR / run_id / "run.json", run)
        return [{"text": m.get("text", ""), "attachments": m.get("attachments", []),
                 "sender": m.get("sender", ""), "created_at": m.get("created_at", "")}
                for m in pend]


def peek_messages(run_id):
    """只读未消费消息（不标记）：规划步骤合入上下文用，执行步骤仍会 drain 注入。"""
    with LOCK:
        run = _RUNS.get(run_id)
        if not run:
            return []
        return [{"id": m.get("id"), "text": m.get("text", ""), "attachments": m.get("attachments", []),
                 "sender": m.get("sender", ""), "created_at": m.get("created_at", "")}
                for m in _ensure_messages(run) if not m.get("consumed")]


def pending_message(run_id, msg_id):
    """Resolve an existing queued message, rejecting delivered inherited copies."""
    with LOCK:
        run = _RUNS.get(run_id)
        if not run:
            return None
        message = next((m for m in run.get("messages") or []
                        if str(m.get("id")) == str(msg_id)), None)
        if not message:
            return None
        key = (message.get("id"), message.get("created_at"), message.get("text"))
        for owner in _RUNS.values():
            if owner["id"] != run_id and (not run.get("task_id") or
                    owner.get("task_id") != run.get("task_id")):
                continue
            if any(m.get("consumed") and
                   (m.get("id"), m.get("created_at"), m.get("text")) == key
                   for m in owner.get("messages") or []):
                return None
        return dict(message)


def retract_message(run_id, msg_id):
    """撤回一条尚未下达（consumed=False）的指令：从信箱删除。

    只删未消费的——已随步骤注入执行的删不掉（那是审计事实，且 prompt 已喂出）。
    返回 (ok, err)：ok=False 时 err 说明原因，供前端 toast。"""
    msg_id = str(msg_id or "").strip()
    if not msg_id:
        return False, "缺少消息 id"
    with LOCK:
        run = _RUNS.get(run_id)
        if not run:
            return False, "运行不存在"
        msgs = _ensure_messages(run)
        for m in msgs:
            if str(m.get("id")) == msg_id:
                key = (m.get("id"), m.get("created_at"), m.get("text"))
                copies = [(r, other) for r in _RUNS.values()
                          if r["id"] == run_id or (run.get("task_id") and
                                                  r.get("task_id") == run.get("task_id"))
                          for other in r.get("messages") or []
                          if (other.get("id"), other.get("created_at"), other.get("text")) == key]
                delivered = next((other for _, other in copies if other.get("consumed")), None)
                if delivered:
                    cb = delivered.get("consumed_by") or {}
                    where = ("已随步骤 #" + str(cb.get("step")) + " 送达") if cb.get("step") \
                        else "已送达执行"
                    return False, "指令" + where + "，无法撤回"
                for owner, other in copies:
                    owner["messages"].remove(other)
                    _save_json(paths.RUNS_DIR / owner["id"] / "run.json", owner)
                bump_state()
                return True, ""
        return False, "消息不在信箱中（可能已被撤回）"


def set_paused(run_id, paused):
    """暂停/放行，并记录请求是否已经到达下一步闸门。

    ``paused`` is kept for old callers.  New readers should use
    ``pause_phase``: ``requested`` means the current step is still running;
    ``paused`` means the gate has actually been reached.
    """
    with LOCK:
        run = _RUNS.get(run_id)
        if not run:
            return False
        target = bool(paused)
        if target:
            phase = "paused" if run.get("pause_phase") == "paused" else "requested"
            update = {"paused": True, "pause_phase": phase,
                      "pause_requested_at": run.get("pause_requested_at") or
                      time.strftime("%Y-%m-%d %H:%M:%S"),
                      "pause_reached_at": run.get("pause_reached_at") if phase == "paused" else None}
        else:
            update = {"paused": False, "pause_phase": "none",
                      "pause_requested_at": None, "pause_reached_at": None}
    update_run(run_id, **update)
    return True


def pause_control():
    """全量步骤暂停计数，不受运行列表窗口限制；管理操作没有步骤闸门。"""
    with LOCK:
        active = [run for run in _RUNS.values()
                  if run.get("kind") == "orchestration"
                  and run.get("status") in ("queued", "running")]
        return {"active": len(active),
                "paused": sum(1 for run in active if run.get("paused"))}


def pause_observability():
    """Return the split pause lifecycle without changing the legacy summary."""
    with LOCK:
        active = [run for run in _RUNS.values()
                  if run.get("kind") == "orchestration"
                  and run.get("status") in ("queued", "running")]
        requested = sum(1 for run in active
                        if run.get("paused") and
                        run.get("pause_phase", "requested") == "requested")
        reached = sum(1 for run in active
                      if run.get("paused") and
                      run.get("pause_phase") == "paused")
        return {"active": len(active), "requested": requested, "reached": reached}


def mark_pause_reached(run_id):
    """Mark the gate reached once no step from the current parallel wave runs."""
    changed = False
    with LOCK:
        run = _RUNS.get(run_id)
        if (not run or not run.get("paused") or
                run.get("status") not in ("queued", "running")):
            return False
        if any(s.get("status") == "running" for s in run.get("steps") or []):
            return False
        if run.get("pause_phase") == "paused":
            return True
        run["pause_phase"] = "paused"
        run["pause_reached_at"] = time.strftime("%Y-%m-%d %H:%M:%S")
        _save_json(paths.RUNS_DIR / run_id / "run.json", run)
        changed = True
    if changed:
        bump_state()
    return True


def set_paused_all(paused):
    """批量暂停/放行所有仍在排队或运行的编排任务。

    只触碰活动 run；终态记录保留原值，避免运行记录页的全局控制改变历史。
    返回实际发生变化的 run 数，供 UI 给出准确反馈。
    """
    target = bool(paused)
    changed = 0
    with LOCK:
        for run in _RUNS.values():
            if run.get("kind") != "orchestration" or run.get("status") not in ("queued", "running"):
                continue
            if bool(run.get("paused")) == target:
                continue
            run["paused"] = target
            if target:
                run["pause_phase"] = ("paused" if run.get("pause_phase") == "paused"
                                       else "requested")
                run["pause_requested_at"] = run.get("pause_requested_at") or time.strftime(
                    "%Y-%m-%d %H:%M:%S")
                if run["pause_phase"] != "paused":
                    run["pause_reached_at"] = None
            else:
                run["pause_phase"] = "none"
                run["pause_requested_at"] = None
                run["pause_reached_at"] = None
            _save_json(paths.RUNS_DIR / run["id"] / "run.json", run)
            changed += 1
    if changed:
        bump_state()
    return changed


def write_report(run_id, markdown):
    p = paths.RUNS_DIR / run_id / "report.md"
    atomic.write_text(p, markdown)
    with LOCK:
        run = _RUNS.get(run_id)
        if run:
            run["report"] = "report.md"
    bump_state()
    return p


def _builtin_live_log_text(run, rel_path):
    """内置智能体步骤从不落 steps/*.log（实时流只写 run.json），日志文件缺席
    时用步骤记录拼一份可读日志兜底——否则所有读日志文件的面板（大屏下钻抽屉/
    蜂巢弹窗/日志抽屉）对 CodeBee 步骤永远「日志为空」，分不清真死还是在干活
    （2026-10-12 用户反馈）。运行中：活动+思考+正文流；已结束：思考+正文+摘要
    （stream/activity 收尾即清，finish_step 另落 output/summary）。"""
    step = next((s for s in (run.get("steps") or [])
                 if s.get("log") == rel_path), None)
    if not step:
        return ""
    parts = []
    for act in (step.get("activity") or []):
        if act:
            parts.append("【活动】" + str(act))
    for key, tag in (("thinking", "【思考】"), ("stream", "【正文】"),
                     ("output", "【正文】"), ("summary", "【摘要】")):
        v = str(step.get(key) or "").strip()
        if v:
            parts.append(tag + " " + v)
    return "\n".join(parts)


def read_step_log(run_id, rel_path, tail=paths.LOG_TAIL_CHARS, pretty=False):
    p = (paths.RUNS_DIR / run_id / rel_path).resolve()
    text = ""
    try:
        # 防目录穿越：必须落在本 run 目录内
        if paths.RUNS_DIR.resolve() not in p.parents:
            return ""
        with p.open("rb") as stream:
            stream.seek(0, 2)
            size = stream.tell()
            if tail > 0 and size > tail:
                stream.seek(-tail, 2)
            else:
                stream.seek(0)
            data = stream.read()
        if tail > 0 and size > tail:
            text = "...(已截断)...\n" + runner.tail_decoded(data, tail)
        else:
            text = runner.decode_output(data)
        # 先洗终端噪声（ANSI/覆写/乱码墙），再折叠遥测刷屏（时间戳不同的重复
        # WARN）；pretty 最后把 codex JSONL 事件流翻译成【消息】【命令】等可读行
        # ——顺序不能反：乱码墙会挤掉折叠分组，翻译也会把 ANSI 当正文
        text = runner.collapse_dup_lines(runner.clean_cli_text(text))
        if pretty:
            text = runner.pretty_cli_log(text)
    except Exception:
        text = ""
    if not (text or "").strip():
        # 文件缺席/为空的兜底：内置智能体只有 run.json 实时流；顺带惠及日志
        # 已被清理的老 run（终点摘要/思考还在步骤记录里）
        live = _builtin_live_log_text(get_run(run_id) or {}, str(rel_path))
        if live:
            text = live if len(live) <= tail else \
                "...(已截断)...\n" + live[-int(tail):]
    return text
