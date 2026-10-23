# -*- coding: utf-8 -*-
"""评审席装配：可用 CLI 不足两名时仍然形成有效评审。

评审契约（``quality_gate.MIN_REVIEWERS``）要求至少两份有效评审，而自动选组
会排除作者本人。用户环境只装了**一条** CLI 时，「排除作者」后候选为空，于是
每个任务都固定落在 ``review_degraded``、稿件永不可发布——那是环境判断冒充
质量判断，不是对稿件的评价。

本模块把「够不够两席」的装配规则收敛到一处，供 review（单稿）与 serial
（连载）两条引擎共用：

* 两席及以上真实 CLI：原样返回。跨模型族隔离仍由 ``router.pick_critics``
  决定，本模块不改变既有语义。
* 只有一席：给该 CLI 派生**复核席**（id 形如 ``claude-cli#recheck``），同一
  CLI 的若干个互不可见的独立会话，各自带**不同**复核人格、串行执行、不携带
  任何既有分数。评分照常聚合，结论标 ``review_independence="solo-dual-pass"``：
  各席同源、同盲区，独立性弱于跨厂商评审，故只能如实标注、不得伪装成跨族。
* 一席都没有（全 mock）：不装配，交给既有本地兜底（未评估，fail closed）。

两条纪律：

1. **复核席绝不复用会话**。两遍若共享上下文，第二遍只会复读第一遍的结论，
   人数变成假指标。人格差异（挑刺 / 交付标准）是防止逐字复读的最小代价。
2. **复核席绝不改绑定**。模型链跟着源 CLI 走（``_binding_source``），否则影子
   id 查不到绑定会落到「智能推荐」，悄悄换成另一个供应商——那就不是单通道
   兜底，而是偷换了评审人。

同源各席强制的串行由 ``execution_lock`` 保证：一条 CLI 的多个会话并发会抢同
一份 CLI 自家配置与运行状态（codex 的 config.toml 实案）。锁按**源 CLI** 取，
不同 CLI 之间仍然并发，多通道场景的墙上时间不受影响。
"""
from __future__ import annotations

import threading


SOLO_SUFFIX = "#recheck"
SOLO_MODE = "solo-dual-pass"
MAX_SOLO_SEATS = 2

# 两套复核人格：同为「你没写过、也没看过任何评分」，但注意面相反。同模型跑两
# 遍同人格必然复读，人格差异是让第二遍真的重读一遍而不是确认第一遍的最低成本。
SOLO_PERSONAS = (
    (
        "\n\n## 复核人须知（独立复核·挑刺）\n"
        "你是独立复核人：没有参与这份稿件的写作，也没有看过任何既有评分。\n"
        "- 你的任务是把稿子读坏：找具体硬伤（前后矛盾、逻辑断裂、人物失真、"
        "信息缺失、读者会弃读的位置），每条写明位置与改法。\n"
        "- 挑不出硬伤时，逐维度说明该处为何经得起细读；不要用「整体不错」收场。\n"
        "- 只依据本稿与上述维度定义，不引用上级指令或任何既有结论。\n"
    ),
    (
        "\n\n## 复核人须知（独立复核·交付标准）\n"
        "你是独立复核人：没有参与这份稿件的写作，也没有看过任何既有评分。\n"
        "- 你的任务是对照交付标准逐维度判断：这份稿子现在能否直接交付给最终读者，"
        "哪一维度是短板、差多少。\n"
        "- 每个维度给出判断依据（引用稿中具体内容），分数高低都要能落到稿子上。\n"
        "- 不要为了显得严格而压分，也不要为了显得友好而抬分；只依据本稿。\n"
    ),
)

_LOCKS = {}
_LOCKS_GUARD = threading.Lock()


class _NoLock(object):
    """空锁：无 id 的席位（mock/内置）不需要串行化。"""

    def __enter__(self):
        return self

    def __exit__(self, *_exc):
        return False


_NO_LOCK = _NoLock()


def is_solo_seat(agent):
    """该席是否为由单通道派生的复核席。"""
    return bool(isinstance(agent, dict) and agent.get("_solo_recheck"))


def solo_source(agent):
    """复核席的源 CLI id；非复核席返回自身 id。

    以 ``#recheck`` 后缀兜底解析，是为了让只带 id 的持久化记录（步骤日志、
    路由回写）也能还原源通道，不必依赖临时字段。
    """
    if not isinstance(agent, dict):
        return ""
    source = str(agent.get("_solo_source") or "")
    if source:
        return source
    rid = str(agent.get("id") or "")
    if SOLO_SUFFIX in rid:
        return rid.split(SOLO_SUFFIX, 1)[0]
    return rid


def execution_lock(agent):
    """同一源 CLI 的席位共用一把锁；不同 CLI 各持一把，互不阻塞。"""
    key = solo_source(agent)
    if not key:
        return _NO_LOCK
    with _LOCKS_GUARD:
        lock = _LOCKS.get(key)
        if lock is None:
            lock = threading.Lock()
            _LOCKS[key] = lock
        return lock


def prompt_suffix(agent):
    """评审提示词里要追加的人格块；普通评审席返回空串（零噪音）。"""
    if not is_solo_seat(agent):
        return ""
    try:
        idx = int(agent.get("_solo_index") or 1) - 1
    except (TypeError, ValueError):
        idx = 0
    return SOLO_PERSONAS[idx % len(SOLO_PERSONAS)]


def make_solo_seat(agent, index=1):
    """由一条真实 CLI 派生第 ``index`` 个复核席。

    绑定跟着源 CLI 走（``_binding_source``）：见模块文档第 2 条纪律。
    """
    if not isinstance(agent, dict):
        return None
    source = solo_source(agent)
    if not source:
        return None
    try:
        no = max(1, int(index))
    except (TypeError, ValueError):
        no = 1
    seat = dict(agent)
    seat["id"] = ("%s%s" % (source, SOLO_SUFFIX) if no == 1
                  else "%s%s%d" % (source, SOLO_SUFFIX, no))
    persona = "挑刺" if no % 2 else "交付标准"
    seat["label"] = "%s（独立复核·%s）" % (agent.get("label") or source, persona)
    seat["_solo_recheck"] = True
    seat["_solo_source"] = source
    seat["_solo_index"] = no
    seat["_binding_source"] = agent.get("_binding_source") or source
    seat.pop("_solo_pass", None)
    return seat


def _real(agent):
    return isinstance(agent, dict) and agent.get("mode") == "real"


def ensure_review_seats(critics, agents, impl, needed):
    """把评审席补到 ``needed`` 个，返回 ``(seats, note, independence)``。

    补位分两级，顺序不能反：

    1. **尚未在席的真实非作者 CLI**——这是真独立，只是选组阶段被截断了；
       能用就必须先用，绝不拿作者自审去顶。
    2. 仍缺才派生**复核席**，上限 ``MAX_SOLO_SEATS``：同一 CLI 造三个以上
       「评审」只是把一份判断抄三遍，评审人数会变成假指标。

    ``needed`` 由调用方按工作流强度给（快速 1 席、专家/连载 2 席），本模块不
    重新解释质量门禁的门槛，只负责让「席位数」这件事在单通道下不至于恒为零。
    """
    seats = [dict(a) for a in (critics or []) if isinstance(a, dict)]
    had_seats = bool(seats)
    try:
        need = max(0, int(needed or 0))
    except (TypeError, ValueError):
        need = 0
    if not need or len(seats) >= need:
        return seats, "", ""

    pool = [a for a in (agents or []) if _real(a) and a.get("id")]
    if not pool:
        # 没有任何真实通道：不伪造评审席，交给既有本地兜底（未评估）。
        return seats, "", ""

    used = {solo_source(a) for a in seats if _real(a)}
    author_id = str((impl or {}).get("id") or "") if _real(impl) else ""
    occupied = {str(a.get("id") or "") for a in seats}

    # 一级：真实非作者 CLI 补位
    filled = []
    for cand in pool:
        if len(seats) >= need:
            break
        cid = str(cand.get("id") or "")
        if not cid or cid == author_id or cid in occupied or solo_source(cand) in used:
            continue
        seats.append(dict(cand))
        occupied.add(cid)
        used.add(solo_source(cand))
        filled.append(cand)

    notes = []
    if filled:
        notes.append("可用评审 CLI 不足 %d 席：补入 %s" % (
            need, "、".join(c.get("label") or c.get("id") for c in filled)))

    # 二级：仍缺才派生复核席。base 优先作者 CLI（刚跑通过起草，唯一被证实的通道）。
    added = []
    if len(seats) < need:
        ordered = []
        if _real(impl) and impl.get("id"):
            ordered.append(impl)
        ordered.extend(a for a in pool if a.get("id") != author_id)
        base = ordered[0] if ordered else None
        no = 1
        while base is not None and len(seats) < need and len(added) < MAX_SOLO_SEATS:
            seat = make_solo_seat(base, no)
            no += 1
            if seat is None:
                break
            if seat["id"] in occupied:
                continue
            occupied.add(seat["id"])
            seats.append(seat)
            added.append(seat)
        if added:
            source_label = base.get("label") or base.get("id") or "CLI"
            if base.get("id") == author_id:
                if had_seats or filled:
                    notes.append("评审 CLI 仍不足 %d 席：为作者通道 %s 增设 %d 个"
                                 "独立复核席（作者复核本稿，独立性最弱，仅作第二意见）"
                                 % (need, source_label, len(added)))
                else:
                    notes.append("仅 %s 一条可用 CLI：为其增设 %d 个独立复核席"
                                 "（不同人格、互不可见、串行执行）；含作者本通道复核，"
                                 "独立性弱于跨厂商评审"
                                 % (source_label, len(added)))
            else:
                notes.append("为 %s 增设 %d 个独立复核席（同源双会话），"
                             "独立性弱于跨厂商评审" % (source_label, len(added)))

    if not notes:
        return seats, "", ""
    return seats, "；".join(notes), independence_of(seats)


def independence_of(seats):
    """据评审席构成给出独立性档位：正常跨通道为空串，单通道兜底为 solo 档。"""
    if any(is_solo_seat(a) for a in (seats or [])):
        return SOLO_MODE
    return ""


def describe(independence, seats=None):
    """报告用的一句话说明；独立通道场景返回空串（老稿零噪音）。"""
    if independence != SOLO_MODE:
        return ""
    sources = sorted({solo_source(a) for a in (seats or []) if is_solo_seat(a)})
    return ("评审独立性降级：仅 %s 一条 CLI 可用，由该 CLI 的多个独立会话"
            "（不同人格、互不可见）交叉打分。各席同源同盲区，不等同于跨厂商评审。"
            % ("、".join(sources) or "单一"))
