# -*- coding: utf-8 -*-
"""智能路由：能力基线 × 历史胜率 × 角色约束 → 选智能体，并给出可解释的理由。"""
from __future__ import annotations

import json
import os
import re

from . import dispatch, history

# 各类智能体的能力基线（0-100）。真实 CLI 里官方双雄最高。
CAPABILITY = {
    "codex": 84, "claude": 84, "opencode": 74, "qwen": 72,
    "aider": 70, "openclaw": 60, "generic": 55, "mock": 10,
}

MAX_REPAIR_ROUNDS = 2  # 自动修复循环上限
AUTO_CRITIC_LIMIT = 2  # 自动内容评审只取综合分前两名；手工模式仍尊重完整名单


def _task_type(ttype):
    """兼容旧调用的字符串与新任务规格对象。"""
    if isinstance(ttype, dict):
        return str(ttype.get("type") or ttype.get("dimension") or "direct")
    return str(ttype or "direct")


def _binding_bonus(agent_id, dispatch_mode=False):
    """绑定链可用性加分/减分：解析出的调用链有备胎（≥2 条）+8；单条 = 单点
    无降级空间 +0；解析为空 -25。2026-09-16 实测：静态能力基线让配额烧干的
    codex 永远压过健康备用 CLI，绑定空的 CLI 更是连用户配置的模型都没用上
    ——先按「能不能按配置跑起来」校准。2026-09-22 续修：单条链（含空链+
    仅 provider_id 的旧数据）拿过 +8 是信号失真——2026-09-22 智谱余额清零
    实测「绑定链可用」的 claude/kimi 全是单点，无一家有第二口气。返回
    (分值, 理由文本)。"""
    try:
        from . import modelhub
        pref = modelhub._binding_for(agent_id)
        configured = bool(modelhub._binding_chain(pref) or pref.get("provider_id"))
        if not configured:
            return (0.0, "") if dispatch_mode else (-25.0, "，绑定链为空：相关步骤将判失败（-25.0）")
        b = modelhub.resolve_binding(agent_id)
        n = len((b or {}).get("call_chain") or [])
        if n >= 2:
            return 8.0, "，绑定链可用（+8.0）"
        if n == 1:
            return 0.0, "，绑定单点无备胎（+0）"
        return -25.0, "，绑定链为空：相关步骤将判失败（-25.0）"
    except Exception:
        return 0.0, ""


def _history_bonus(stats, agent_id, ttype):
    s = (stats.get(agent_id) or {}).get(ttype) or {}
    if not s.get("runs"):
        return 0.0
    runs = s["runs"]
    win_rate = s["wins"] / runs
    # 胜率为主，经验为辅；败率要扣分且随样本量爬满（前 3 次线性加权，防一次
    # 偶发失败把智能体埋了）。0/3 全败 = -18：常挂的 CLI 必须排到无历史的新
    # 面孔之后（2026-09-17 前败率不扣分，0/3 还拿 +6 经验分，比没跑过还高）。
    loss_penalty = 24.0 * (1.0 - win_rate) * min(1.0, runs / 3.0)
    return round(18.0 * win_rate + min(6.0, runs) - loss_penalty, 1)


def _online_bonus(agent, role, ttype):
    """近期真实运行信号：成功率、P95 延迟和均价只作软加减分。"""
    try:
        from . import usage
        metrics = usage.routing_stats(task_type=ttype, role=role,
                                      agent=agent.get("id") or "")
        samples = int(metrics.get("samples") or 0)
        if not samples:
            return 0.0, ""
        rate = float(metrics.get("success_rate") or 0.0)
        success_score = max(-8.0, min(8.0, (rate - 0.75) * 24.0))
        p95 = max(0.0, float(metrics.get("p95_duration_s") or 0.0))
        # 旧上限 -6 抵不过 CLI 静态能力 10 分差，P95 两三分钟的模型仍会
        # 压过十几秒的健康模型。加大惩罚，让真实延迟足以改变自动选择。
        latency_score = -min(18.0, max(0.0, (p95 - 30.0) / 7.0))
        cost = max(0.0, float(metrics.get("avg_cost_usd") or 0.0))
        cost_score = -min(4.0, max(0.0, (cost - 0.01) / 0.01))
        total = round(success_score + latency_score + cost_score, 1)
        success_samples = int(metrics.get("success_samples") or samples)
        reason = ("，在线 %d/%d 验收成功（%+.1f），P95 %.1fs（%+.1f），均价 $%.4f（%+.1f）"
                  % (int(metrics.get("successes") or 0), success_samples,
                     success_score, p95, latency_score, cost, cost_score))
        # 标准要求回退层出现在候选理由里（成本与时延预算第 5 条）：
        # exact/task-role/task/global，让"分数来自哪层样本"可核对。
        reason += "，样本层 %s" % (metrics.get("fallback") or "global")
        return total, reason
    except Exception:
        return 0.0, ""


def score(agent, role, ttype, stats=None):
    """返回 (总分, 理由字符串)。配额惩罚：catalog 里配了
    quota_tokens_per_hour 的智能体，本小时用量越接近配额分越低
    （封顶 -45，足以盖过历史加分），满额后仅在没有其他选择时才会被选中。"""
    stats = stats or {}
    ttype = _task_type(ttype)
    base = CAPABILITY.get(agent.get("kind"), 60)
    use_dispatch = bool(agent.get("_dispatch_task_type") or
                        agent.get("dispatch_enabled"))
    bb, btxt = _binding_bonus(agent.get("id"), dispatch_mode=use_dispatch)
    hb = _history_bonus(stats, agent.get("id"), ttype)
    online, online_txt = _online_bonus(agent, role, ttype)
    # 保持公开 score() 的历史绝对分值；运行级候选由 pipeline 标记画像后
    # 才启用能力亲和度，避免旧插件/测试调用被新权重悄然改变。
    affinity, affinity_txt = (dispatch.agent_affinity(agent.get("kind"), ttype, role)
                              if use_dispatch else (0.0, "兼容模式"))
    total = base + bb + hb + affinity + online
    hs = (stats.get(agent.get("id")) or {}).get(ttype)
    htxt = ("，历史 %d/%d 胜（%s）" % (hs["wins"], hs["runs"], "%+.1f" % hb)) if hs else "，无历史记录"
    quota_txt = ""
    quota = int(agent.get("quota_tokens_per_hour") or 0)
    if quota > 0:
        try:
            from . import usage
            used = usage.agent_tokens_recent(agent.get("id"), hours=1)
        except Exception:
            used = 0
        if used > 0:
            ratio = min(1.0, used / float(quota))
            penalty = round(-45.0 * ratio, 1)
            if penalty:
                total += penalty
                quota_txt = "，本小时 %d/%d tokens（%s）" % (used, quota, penalty)
    return total, "能力基线 %d，%s%s%s%s%s，总分 %s" % (
        base, affinity_txt, btxt, htxt, online_txt, quota_txt, round(total, 1))


def pick(agents, role, ttype, stats=None, exclude=()):
    """按分选出最优智能体。返回 (agent, 理由) 或 (None, "")。"""
    stats = stats or history.agent_stats()
    best, best_reason = None, ""
    for a in agents:
        if a["id"] in exclude:
            continue
        total, reason = score(a, role, ttype, stats)
        if best is None or total > best[0]:
            best, best_reason = (total, a), reason
    if best is None:
        return None, ""
    return best[1], best_reason


# 空链 CLI 的真实上游在各自本机配置里（注册表里的 provider_id 是 UI 残留，
# 2026-09-22 实测 qwencode 注册表指 prov-36 本机却指公司网关）。正则探针
# 只为换将时识别「同上游」，读不到就算未知——未知不参与剔除，宁白试不误杀。
_LOCAL_ENDPOINT_PROBES = (
    ("kimi-code", "~/.kimi-code/config.toml", r'baseUrl\s*=\s*"([^"]+)"'),
    ("claude-code", "~/.claude/settings.json", r'"ANTHROPIC_BASE_URL"\s*:\s*"([^"]+)"'),
    ("qwencode", "~/.qwen/settings.json", r'"OPENAI_BASE_URL"\s*:\s*"([^"]+)"'),
    ("opencode", "~/.config/opencode/opencode.json", r'"baseURL"\s*:\s*"([^"]+)"'),
    ("opencode", "~/.config/opencode/opencode.jsonc", r'"baseURL"\s*:\s*"([^"]+)"'),
    ("codex-cli", "~/.codex/config.toml", r'base_url\s*=\s*"([^"]+)"'),
)

_PI_SETTINGS_PATH = "~/.pi/agent/settings.json"
_PI_MODELS_PATH = "~/.pi/agent/models.json"


def _pi_provider_hosts(selected_name):
    """按选中名取 pi 供应商块的 host:port（先 models.json，再 settings.json 兜底）。

    老配置可能把 providers 直接写进 settings.json，两件都认；只认选中的那块。"""
    hosts = set()
    if not selected_name:
        return hosts
    for rel in (_PI_MODELS_PATH, _PI_SETTINGS_PATH):
        try:
            with open(os.path.expanduser(rel), "r", encoding="utf-8") as fh:
                cfg = json.load(fh)
        except (OSError, ValueError, TypeError):
            continue
        if not isinstance(cfg, dict):
            continue
        block = (cfg.get("providers") or {}).get(selected_name)
        if not isinstance(block, dict):
            continue
        h = _host_of(block.get("baseUrl") or block.get("baseURL") or
                     block.get("base_url"))
        if h:
            hosts.add(h)
            break   # 命中即定：models.json 优先，别让另一件的旧块参与判断
    return hosts


def _host_of(url):
    """上游归一：只留 host:port。/api/anthropic 与 /v1 的路径差异不算换上游。"""
    m = re.match(r"^[a-zA-Z][a-zA-Z0-9+.-]*://([^/?#]+)", str(url or "").strip())
    return m.group(1).lower() if m else ""


def agent_upstreams(agent_id, difficulty="default", task_type="", role=""):
    """候选实际会用到的上游集合（host:port 归一）。显式链优先；空绑定按
    运行期自动推荐链判断，再回落到 CLI 本机配置；都拿不到返回空集 = 未知。"""
    ups = set()
    try:
        from . import modelhub
        pref = modelhub._binding_for(agent_id)
        provs = {p.get("id"): p for p in modelhub.providers()}

        def _add_chain(chain):
            for item in chain or ():
                pid = (item.get("provider_id") or "").strip()
                provider = item.get("provider") or provs.get(pid) or {}
                h = _host_of(provider.get("base_url"))
                if h:
                    ups.add(h)

        configured = bool(modelhub._binding_chain(pref) or pref.get("provider_id"))
        # 保留原始显式链中的 endpoint，即使某条已禁用/失效；同一个死上游仍
        # 不值得在另一个 CLI 上立刻再撞一次。
        if configured:
            chain = modelhub._binding_chain(pref)
            _add_chain(chain)
            resolved = modelhub.resolve_binding(agent_id, difficulty) or {}
            _add_chain(resolved.get("call_chain"))
        else:
            # bind_agent 在空绑定时会自动推荐供应商链。换将选路必须观察同一
            # 生效链，否则“空绑定”的多个 CLI 可能被排成不同候选却共用一个 403 网关。
            recommended = modelhub.recommend_binding(
                agent_id, difficulty, task_type=task_type, role=role) or {}
            _add_chain(recommended.get("call_chain"))
    except Exception:
        pass
    if not ups:
        if agent_id == "pi":
            # Pi 的选择位在 settings.json（defaultProvider），而 endpoint 在**同目录
            # models.json** 的 providers.<选中名>.baseUrl（pi 的 getModelsPath()，
            # CodeBee 托管与用户手工配置都落在那儿）——不能只扫所有 baseUrl，
            # 那会把未选中的 provider 也误算成当前上游。
            try:
                with open(os.path.expanduser(_PI_SETTINGS_PATH), "r",
                          encoding="utf-8") as fh:
                    settings = json.load(fh)
                selected_name = str((settings.get("defaultProvider")
                                     if isinstance(settings, dict) else "") or "")
                ups.update(_pi_provider_hosts(selected_name))
            except (OSError, ValueError, TypeError):
                pass
    if not ups:
        for aid, path, pat in _LOCAL_ENDPOINT_PROBES:
            if aid != agent_id:
                continue
            try:
                with open(os.path.expanduser(path), "r", encoding="utf-8",
                          errors="ignore") as fh:
                    txt = fh.read()
            except OSError:
                continue
            for m in re.finditer(pat, txt):
                h = _host_of(m.group(1))
                if h:
                    ups.add(h)
    return ups


def pick_switch_candidate(agents, role, ttype, stats, exclude=(),
                          dead_upstreams=(), difficulty="default",
                          blocked_upstreams=()):
    """换将选将。配额类死亡背景下：与死者同上游的候选依次让位，直到找到
    异上游/上游未知的候选（2026-09-22 实案：kimi 与 claude 同骑智谱，0.1 分
    之差把异上游 qwencode 压在下面，换将=换壳不换命）。异上游耗尽后同上游
    候选捡回分数最高者——聊胜于无。403 明确拒绝的上游始终阻断，不捡回重试。"""
    exclude = set(exclude)
    blocked_sets = [set(d) for d in (blocked_upstreams or ()) if d]
    best, reason = pick(agents, role, ttype, stats, exclude=exclude)
    if best is None or (not dead_upstreams and not blocked_sets):
        return best, reason
    deferred = []
    while best is not None:
        ups = agent_upstreams(best.get("id"), difficulty=difficulty,
                              task_type=ttype, role=role)
        if ups and any(ups & blocked for blocked in blocked_sets):
            # 明确的 403 权限拒绝：同一上游没有换模型/换 CLI 再试的价值，
            # 即使异上游候选耗尽也不把它捡回来（与可换账号的配额错误不同）。
            exclude.add(best.get("id"))
            best, reason = pick(agents, role, ttype, stats, exclude=exclude)
            continue
        if not (ups and any(ups & dead for dead in dead_upstreams)):
            break  # 异上游或上游未知：就用它
        deferred.append((best, reason))
        best, reason = pick(agents, role, ttype, stats, exclude=exclude | {
            x.get("id") for x, _ in deferred})
    if best is None and deferred:
        best, reason = deferred[0]  # 异上游全灭：同上游里挑最高的顶上
    elif deferred:
        reason = ("%s（%s 与死者同上游，延后让位异上游候选）"
                  % (reason, deferred[-1][0].get("id")))
    return best, reason


def route_plan(agents, role, task_spec, stats=None, exclude=(), selected=None,
               participants=(), selection_reason=""):
    """生成可审计的候选排序，并可用实际选路覆盖评分预选结果。"""
    if stats is None:
        stats = history.agent_stats()
    # 与 pick 保持同一候选池；绑定/健康扣分仍由 score 和 modelhub 负责，
    # 诊断不能悄悄排除实际可能被选中的 mock 或备用 CLI。
    pool = list(agents or [])
    rows = []
    for index, agent in enumerate(pool):
        if agent.get("id") in exclude:
            continue
        candidate = dict(agent)
        if isinstance(task_spec, dict):
            candidate["_dispatch_task_type"] = task_spec.get("type") or "direct"
            candidate["_dispatch_role"] = role
        total, reason = score(candidate, role, task_spec, stats)
        rows.append({"agent_id": agent.get("id") or "",
                     "label": agent.get("label") or agent.get("id") or "",
                     "kind": agent.get("kind") or "generic",
                     "score": round(total, 1), "reason": reason,
                     "order": index})
    rows.sort(key=lambda x: (-x["score"], x["order"]))
    selected_id = (selected or {}).get("id") if isinstance(selected, dict) else ""
    if selected_id and not any(x["agent_id"] == selected_id for x in rows):
        rows.append({"agent_id": selected_id,
                     "label": selected.get("label") or selected_id,
                     "kind": selected.get("kind") or "builtin",
                     "score": 0.0, "reason": selection_reason or "实际选路",
                     "order": len(rows)})
    chosen = selected_id or (rows[0]["agent_id"] if rows else "")
    participant_ids = []
    for agent in participants or ():
        agent_id = agent.get("id") if isinstance(agent, dict) else str(agent or "")
        if agent_id and agent_id not in participant_ids:
            participant_ids.append(agent_id)
    active_ids = participant_ids or ([chosen] if chosen else [])
    return {"role": role, "selected": chosen, "participants": participant_ids,
            "selection_reason": selection_reason,
            "candidates": rows,
            "fallback": [x["agent_id"] for x in rows
                         if x["agent_id"] not in active_ids]}


def pick_reviewer(agents, impl, ttype, stats=None, exclude=(),
                  dead_upstreams=(), difficulty="default"):
    """评审者：跨厂商是硬规则（Codeband 的对抗式配对）——同族评审有同款盲区，
    评审者必须来自与实现者不同的 kind；跨族池为空才回退同厂商并如实备注，
    绝不把回退伪装成跨厂商。exclude 用于剔除运行时已知死候选（实现步走查证伪
    的 quota/403 死链），与 pick / pick_switch_candidate 的 exclude 同语义。
    dead_upstreams 进一步剔除「与死者同上游」的候选——配额通常按网关/账号烧刻，
    同上游 = 同配额桶，403 权限拒绝也不能换壳重试。评审者又是单点，撞死链会
    白烧一轮并误报「评审器故障」，故宁可少一个候选也不选已知同上游的。"""
    stats = stats or history.agent_stats()
    excluded = set(exclude)
    dead_sets = [set(d) for d in (dead_upstreams or ()) if d]

    def _alive(a):
        if a["id"] in excluded:
            return False
        if not dead_sets:
            return True
        ups = agent_upstreams(a.get("id"), difficulty=difficulty,
                              task_type=ttype, role="review")
        return not (ups and any(ups & d for d in dead_sets))

    impl_kind = impl.get("kind")
    real = [a for a in agents
            if a.get("mode") == "real" and a["id"] != impl["id"] and _alive(a)]
    cross = [a for a in real if a.get("kind") != impl_kind]
    if cross:
        best = max(cross, key=lambda a: score(a, "review", ttype, stats)[0])
        _, reason = score(best, "review", ttype, stats)
        return best, "跨厂商评审（%s ≠ %s）：%s" % (best.get("kind"), impl_kind, reason)
    if real:
        best = max(real, key=lambda a: score(a, "review", ttype, stats)[0])
        _, reason = score(best, "review", ttype, stats)
        return best, ("（无跨厂商智能体可用，回退同厂商 %s 评审——建议启用其他厂商的 CLI）"
                      % impl_kind) + reason
    mb = next((a for a in agents if a["id"] == "mock-b"), None)
    if mb and impl.get("mode") != "mock":
        return mb, "（无第二个真实智能体，用 mock 评审）"
    return impl, "（自评：仅有实现者一个智能体可用）"


def pick_critics(agents, ttype, stats=None, impl=None):
    """自动内容评审组选综合分前两名（排除作者本人）。

    旧逻辑让全部已启用 CLI 串行参与每章和全局评审；一条慢/失效链就会把
    任务拖长数分钟。两名保留交叉判断并封顶默认调用数；手工模式不走这里。
    """
    stats = stats or history.agent_stats()
    real = [a for a in agents if a.get("mode") == "real" and (not impl or a["id"] != impl["id"])]
    if real:
        ranked = sorted(real, key=lambda a: score(a, "review", ttype, stats)[0], reverse=True)
        real = ranked[:AUTO_CRITIC_LIMIT]
        note = "按成功率与延迟选择前 %d 名评审（共 %d 名候选）" % (len(real), len(ranked))
        if impl and impl.get("mode") == "real" \
                and all(a.get("kind") == impl.get("kind") for a in real):
            note += "；评审组与作者同为 %s，建议启用其他厂商 CLI 交叉评审" % impl.get("kind")
        return real, note
    mocks = [a for a in agents if a.get("mode") == "mock"] or agents[:2]
    return mocks, "（无真实智能体，用内置 mock 演示）"
