# -*- coding: utf-8 -*-
"""智能路由：能力基线 × 历史胜率 × 角色约束 → 选智能体，并给出可解释的理由。"""
from __future__ import annotations

import json
import logging
import os
import re
import threading
import time

from . import dispatch, history, model_family, modelhub, strategy_config, upstream

# 各类智能体的能力基线（0-100）。真实 CLI 里官方双雄最高。
CAPABILITY = {
    "codex": 84, "claude": 84, "opencode": 74, "qwen": 72,
    "aider": 70, "openclaw": 60, "generic": 55, "mock": 10,
}

MAX_REPAIR_ROUNDS = 2  # 自动修复循环上限
AUTO_CRITIC_LIMIT = 2  # 自动内容评审只取综合分前两名；手工模式仍尊重完整名单
# 账号台账死号降权（2026-10-22 cline-cli Unauthorized 案：账号台账 10-17 起
# down/AUTH、探针连败 243 次，pick_critics 仍凭历史分把它选成唯一评审，两轮
# 6 次全撞 401）。罚分必须盖过一切加分组合（基线+绑定+历史+粘性），让死号
# 只在没有任何活号可选时才会被白试选中；探活成功（record_success）即回血。
LEDGER_DOWN_PENALTY = -200.0

# 下列权重经 strategy_config（data/strategy.json）外置热更：模块常量是默认值，
# 文件覆写优先（Raven Evolver 借鉴·机制闸）。常量保留在位——测试 monkeypatch
# 与代码可读性都以它为准，strategy_config 取默认值时回 getattr 本模块。
BINDING_EMPTY_PENALTY = -25.0   # 绑定链解析为空
BINDING_MULTI_BONUS = 8.0       # 绑定链 ≥2 条备胎
HISTORY_LOSS_CAP = 24.0         # 败率扣分封顶
QUOTA_PENALTY_CAP = 45.0        # 配额软罚封顶（正值）


def _ledger_down_text(agent_id, overrides=None):
    """持久账号台账判死则返回 (罚分, 理由片段)；未判死/台账不可用返回 (0, "")。

    一切口径与 bench.is_globally_benched 一致：仅 AUTH/QUOTA/TOOL_LAUNCH 等
    账号级死错且探针未恢复才 down；限流/超时等瞬态不进台账，不受影响。
    """
    try:
        from . import bench as _bench
        if _bench.is_globally_benched(agent_id):
            pen = strategy_config.get("ledger_down_penalty", overrides)
            reason = _bench.global_reason(agent_id) or "账号级死错"
            return pen, "，账号台账停用（%s，探针未恢复 %+.0f）" % (reason, pen)
    except Exception:
        pass
    return 0.0, ""


def requires_workspace_snapshot(agent):
    """Whether an agent can write files outside CodeBee's own tool layer.

    Real CLI/ACP/MCP backends all run as external processes and therefore need
    the Git checkpoint used by the recovery UI.  Mock agents are deterministic
    in-process stand-ins and do not need that boundary.
    """
    return isinstance(agent, dict) and agent.get("mode") == "real"


def writable_agents(agents, workdir):
    """Return the route pool and report checkpoint capability.

    A non-Git workspace affects recoverability, not which agent the user asked
    the system to run. Do not remove real CLIs here: that would silently make
    the router select the in-process mock agents. The runner records a warning
    for non-Git workspaces while still executing the selected real CLI.
    """
    from . import checkpoints

    capability = checkpoints.external_snapshot_capability(workdir)
    pool = list(agents or [])
    return pool, [], capability


def _task_type(ttype):
    """兼容旧调用的字符串与新任务规格对象。"""
    if isinstance(ttype, dict):
        return str(ttype.get("type") or ttype.get("dimension") or "direct")
    return str(ttype or "direct")


def _binding_bonus(agent_id, dispatch_mode=False, role="", ttype="direct",
                   overrides=None):
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
            dead = _unbound_default_upstream_down(agent_id, role, ttype)
            if dead:
                return -45.0, ("，未绑定且 CLI 默认上游 %s 健康故障（-45.0）"
                               % dead)
            empty = strategy_config.get("binding_empty_penalty", overrides)
            return (0.0, "") if dispatch_mode else (
                empty, "，绑定链为空：相关步骤将判失败（%+.1f）" % empty)
        b = modelhub.resolve_binding(agent_id)
        n = len((b or {}).get("call_chain") or [])
        if n >= 2:
            multi = strategy_config.get("binding_multi_bonus", overrides)
            return multi, "，绑定链可用（%+.1f）" % multi
        if n == 1:
            return 0.0, "，绑定单点无备胎（+0）"
        empty = strategy_config.get("binding_empty_penalty", overrides)
        return empty, "，绑定链为空：相关步骤将判失败（%+.1f）" % empty
    except Exception:
        return 0.0, ""


def _history_bonus(stats, agent_id, ttype, overrides=None):
    s = (stats.get(agent_id) or {}).get(ttype) or {}
    if not s.get("runs"):
        return 0.0
    runs = s["runs"]
    win_rate = s["wins"] / runs
    # 胜率为主，经验为辅；败率要扣分且随样本量爬满（前 3 次线性加权，防一次
    # 偶发失败把智能体埋了）。扣分项封顶 -24，0/3 全败净 -21（含 +3 经验分）、
    # 0/6 净 -18：常挂的 CLI 必须排到无历史的新面孔之后（2026-09-17 前败率不
    # 扣分，0/3 还拿 +6 经验分，比没跑过还高）。
    loss_cap = strategy_config.get("history_loss_cap", overrides)
    loss_penalty = loss_cap * (1.0 - win_rate) * min(1.0, runs / 3.0)
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


def _tool_chain_penalty(agent_id):
    """工具动作验证降级（借鉴 RuiC-oc-ferry 探测协议）：绑定链模型动作验证
    全为「仅对话」时，agentic 步骤（CLI 写文件/跑命令、内置智能体工具环）
    会整轮空转——-10 盖过历史加分区间（±24 内的常规项），但远轻于配额满额
    -45，绝境兜底仍可选到。无判定/部分仅对话不加减：备胎还能接住。"""
    try:
        from . import modelhub
        flag, _txt = modelhub.chain_tool_verdict(agent_id)
    except Exception:
        return 0.0, ""
    if flag == "all_chat_only":
        return -10.0, "，动作验证：绑定模型均为仅对话（-10.0）"
    return 0.0, ""


# ---------------------------------------------------------------- 会话粘性
# magpie「stays=」借鉴（2026-10-22 调研）：厂商 prompt cache 按「同模型+同
# 前缀」命中（≥1024 token 才值得保留），连载多轮修订每轮全稿重喂，中途换
# CLI/换模型 = 整轮缓存作废全价重付。同一（角色,任务类型）在 TTL 内选过的
# CLI 续选给小额亲和分——只做平票裁决：+4 落在历史加分区间内，远小于工具
# 降级 -10 与配额 -45，健康/配额/动作验证信号照样一票压过粘性。仅真实 CLI
# 参与（mock/演示代理不粘）；进程内存态，服务重启清零，TTL 到期自动失效。
STICKY_TTL_S = 600
STICKY_BONUS = 4.0
_STICKY = {"by_key": {}, "lock": threading.Lock()}

# 连败冷却：败绩扣分封顶 -24，压不过高能力基线——2026-10-22 codex 实案：
# 历史 0/6（-18）仍以 45 分压过 opencode 38 分先跑，白烧 250s 确认失败才让位。
# 近窗 3 连败（起草/实现位，见 history.STREAK_WINDOW_S）-100 一记沉底：健康者
# 永远排在前面，仅当全场都在冷却里才兜底当选；冷却中同时停用粘性加成（续选
# 一个正在连败的 CLI 保 prompt cache 是负优化）。败迹出窗自愈，无需人工拉黑。
STREAK_BENCH_MIN = 3
STREAK_BENCH_PENALTY = -100.0


def sticky_reset():
    """清空粘性记忆（测试隔离用；服务运行期无需调用）。"""
    with _STICKY["lock"]:
        _STICKY["by_key"].clear()


def _sticky_remember(role, ttype, agent_id):
    aid = str(agent_id or "")
    if not aid:
        return
    now = time.time()
    key = (str(role or ""), _task_type(ttype))
    ttl = strategy_config.get("sticky_ttl_s")
    with _STICKY["lock"]:
        for k in [k for k, v in _STICKY["by_key"].items()
                  if now - v[1] > ttl]:
            _STICKY["by_key"].pop(k, None)   # 顺手清过期，防字典慢性增长
        _STICKY["by_key"][key] = (aid, now)


def _sticky_bonus(agent, role, ttype, overrides=None):
    if agent.get("mode") != "real":
        return 0.0, ""
    key = (str(role or ""), _task_type(ttype))
    with _STICKY["lock"]:
        hit = _STICKY["by_key"].get(key)
    if not hit:
        return 0.0, ""
    aid, ts = hit
    ttl = strategy_config.get("sticky_ttl_s", overrides)
    if not aid or time.time() - ts > ttl:
        return 0.0, ""
    if str(agent.get("id") or "") != aid:
        return 0.0, ""
    bonus = strategy_config.get("sticky_bonus", overrides)
    return (bonus,
            "，会话粘性：延续上一轮选择保 prompt cache（%+.1f）" % bonus)


def score(agent, role, ttype, stats=None, overrides=None):
    """返回 (总分, 理由字符串)。配额惩罚：catalog 里配了
    quota_tokens_per_hour 的智能体，本小时用量越接近配额分越低
    （封顶 -45，足以盖过历史加分），满额后仅在没有其他选择时才会被选中。

    overrides（挑战者权重字典，Raven Evolver 影子对账用）：只覆写
    strategy_config 外置的那批键，None = 走当前生效配置。"""
    stats = stats or {}
    ttype = _task_type(ttype)
    base = CAPABILITY.get(agent.get("kind"), 60)
    use_dispatch = bool(agent.get("_dispatch_task_type") or
                        agent.get("dispatch_enabled"))
    bb, btxt = _binding_bonus(agent.get("id"), dispatch_mode=use_dispatch,
                              role=role, ttype=ttype, overrides=overrides)
    hb = _history_bonus(stats, agent.get("id"), ttype, overrides=overrides)
    online, online_txt = _online_bonus(agent, role, ttype)
    # 保持公开 score() 的历史绝对分值；运行级候选由 pipeline 标记画像后
    # 才启用能力亲和度，避免旧插件/测试调用被新权重悄然改变。
    affinity, affinity_txt = (dispatch.agent_affinity(agent.get("kind"), ttype, role)
                              if use_dispatch else (0.0, "兼容模式"))
    tv, tv_txt = _tool_chain_penalty(agent.get("id"))
    sv, sticky_txt = _sticky_bonus(agent, role, ttype, overrides=overrides)
    bench, bench_txt = 0.0, ""
    if agent.get("mode") != "mock":
        streak = int(((stats.get(agent.get("id")) or {}).get(ttype) or {})
                     .get("streak") or 0)
        if streak >= strategy_config.get("streak_bench_min", overrides):
            bench = strategy_config.get("streak_bench_penalty", overrides)
            if sv:
                sv, sticky_txt = 0.0, "，粘性停用（连败冷却）"
            bench_txt = "，近窗 %d 连败冷却（%+.0f）" % (streak, bench)
    total = base + bb + hb + affinity + online + tv + sv + bench
    ledger, ledger_txt = _ledger_down_text(agent.get("id"), overrides=overrides)
    total += ledger
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
            penalty = round(-strategy_config.get("quota_penalty_cap", overrides) * ratio, 1)
            if penalty:
                total += penalty
                quota_txt = "，本小时 %d/%d tokens（%s）" % (used, quota, penalty)
    return total, "能力基线 %d，%s%s%s%s%s%s%s%s%s，总分 %s" % (
        base, affinity_txt, btxt, htxt, online_txt, quota_txt, tv_txt,
        sticky_txt, bench_txt, ledger_txt, round(total, 1))


def _pick_scored(agents, role, ttype, stats=None, exclude=()):
    """Legacy score picker used by the built-in strategy."""
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


def _shadow_note(agents, role, ttype, stats, exclude, champion, context):
    """冠军/挑战者影子对账（Raven Evolver 借鉴）：只记一笔，绝不影响选将。

    挑战者未启用/任何异常都静默跳过——影子链路挂了不能连累生产路由。
    冠军分数用当前生效权重现场重打（与挑战者同一 stats，口径可比）。"""
    try:
        from . import strategy_config, strategy_lab
        overrides = strategy_config.challenger_weights()
        if not overrides:
            return
        pool = [a for a in (agents or [])
                if isinstance(a, dict) and a.get("id")
                and a.get("id") not in (exclude or ())]
        if not pool:
            return
        st = stats or {}
        best = None
        for a in pool:
            total, _reason = score(a, role, ttype, st, overrides=overrides)
            if best is None or total > best[0]:
                best = (total, a)
        if best is None:
            return
        champ_score, _r = score(champion, role, ttype, st)
        strategy_lab.append_shadow({
            "ts": time.strftime("%Y-%m-%d %H:%M:%S"),
            "run_id": str((context or {}).get("run_id") or ""),
            "task_id": str((context or {}).get("task_id") or ""),
            "role": str(role or ""),
            "ttype": _task_type(ttype),
            "champion_agent": str(champion.get("id") or ""),
            "champion_score": round(champ_score, 1),
            "challenger_agent": str(best[1].get("id") or ""),
            "challenger_score": round(best[0], 1),
            "diverged": str(best[1].get("id") or "") != str(champion.get("id") or ""),
        })
    except Exception:
        pass


def pick(agents, role, ttype, stats=None, exclude=(), context=None):
    """选 CLI 主入口：委托 _pick_impl，成功后追加挑战者影子记录。

    context（可选）：{"run_id":..., "task_id":...}——只进影子台账供对账报告
    关联步骤结局，不参与选择。"""
    sel, reason = _pick_impl(agents, role, ttype, stats, exclude)
    if sel is not None:
        _shadow_note(agents, role, ttype, stats, exclude, sel, context)
    return sel, reason


def _pick_impl(agents, role, ttype, stats=None, exclude=()):
    """Use the registered policy to select a CLI; retain the legacy return shape."""
    from . import routing_strategy

    pool = list(agents or [])
    stats = stats or history.agent_stats()
    context = {
        "role": role,
        "type": _task_type(ttype),
        "task_type": ttype,
        "stats": stats,
        "exclude": tuple(exclude or ()),
    }
    try:
        strategy = routing_strategy.get()
        result = strategy.select_cli(context, pool)
    except Exception as exc:
        logging.getLogger(__name__).warning(
            "routing strategy selection failed; using builtin: %s",
            type(exc).__name__)
        sel, reason = _pick_scored(pool, role, ttype, stats, exclude=exclude)
        if sel is not None:
            _sticky_remember(role, ttype, sel.get("id"))
        return sel, reason

    if result is None:
        return None, ""
    reason = ""
    if isinstance(result, tuple) and len(result) == 2:
        result, reason = result
    if not isinstance(result, dict) or not result.get("id"):
        logging.getLogger(__name__).warning(
            "routing strategy returned an invalid CLI; using builtin")
        sel, reason = _pick_scored(pool, role, ttype, stats, exclude=exclude)
        if sel is not None:
            _sticky_remember(role, ttype, sel.get("id"))
        return sel, reason
    selected_id = str(result.get("id"))
    excluded = {str(value) for value in (exclude or ())}
    selected = next((agent for agent in pool
                     if isinstance(agent, dict)
                     and str(agent.get("id") or "") == selected_id
                     and selected_id not in excluded), None)
    if selected is None:
        logging.getLogger(__name__).warning(
            "routing strategy selected a CLI outside its candidate pool; using builtin")
        sel, reason = _pick_scored(pool, role, ttype, stats, exclude=exclude)
        if sel is not None:
            _sticky_remember(role, ttype, sel.get("id"))
        return sel, reason
    _sticky_remember(role, ttype, selected_id)
    fallback_reason = "策略 %s" % type(strategy).__name__
    return selected, str(reason or fallback_reason)


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
    """上游归一：路径、认证信息和默认端口不影响同一上游身份。"""
    return upstream.normalize_upstream(url)


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


def _upstream_cooldown_remaining(agent_id, difficulty="default", ttype="",
                                 role=""):
    """候选上游里最短的剩余熔断秒数（换将「最快解禁先用」的排序键）。

    只统计健康台账里真实在计时的熔断（provider 级 circuit + 模型级
    open_until 取最小）。返回 None=查不到任何熔断时间——调用方维持原
    评分序，不假装知道。"""
    try:
        from . import health
        ups = agent_upstreams(agent_id, difficulty=difficulty,
                              task_type=ttype, role=role)
        if not ups:
            return None
        names = set()
        for prov in modelhub.providers():
            h = _host_of(prov.get("base_url"))
            if h in ups and prov.get("name"):
                names.add(prov["name"])
        best = None
        for name in names:
            rem = health.cooldown_remaining(name)
            if rem is not None:
                best = rem if best is None else min(best, rem)
        return best
    except Exception:
        return None


def _unbound_default_upstream_down(agent_id, role="", ttype="direct"):
    """未配置绑定时，CLI 本机默认上游是否正处健康故障。

    bind_agent 在没有可用供应商时回落到 CLI 自家配置；若那家上游已被健康
    台账判死（2026-10-22 实案：codex 的 ~/.codex/config.toml 指向 403 配额
    网关，评审仍按静态能力分排第一），能力基线/历史胜率不该继续把它推上
    候选首位。只认精确 host 命中；读不到上游算未知、不扣分（宁白试不误杀）。
    """
    try:
        from . import health, modelhub
        pref = modelhub._binding_for(agent_id)
        if modelhub._binding_chain(pref) or pref.get("provider_id"):
            return ""
        down_names = health.down_names()
        if not down_names:
            return ""
        dead_hosts = set()
        for prov in modelhub.providers():
            if prov.get("name") not in down_names:
                continue
            host = _host_of(prov.get("base_url"))
            if host:
                dead_hosts.add(host)
        if not dead_hosts:
            return ""
        ups = agent_upstreams(agent_id, task_type=_task_type(ttype), role=role)
        hit = sorted(ups & dead_hosts)
        return hit[0] if hit else ""
    except Exception:
        return ""


def _switch_identity(agent, ttype, difficulty):
    """换将隔离用的身份：模型族集合 + 上游 host 集合，任一取不到即为空集。

    口径与评审隔离共用 `_review_identity`（含绑定链上每一行）：只看主绑定会
    漏掉「主模型挂了自动落到同网关备用模型」这种同故障域，而那正是换将要
    避开的东西。
    """
    try:
        ident = _review_identity(agent or {}, ttype, difficulty)
    except Exception:
        return set(), set()
    families = {str(x).casefold() for x in (ident.get("families") or ()) if x}
    upstreams = {str(x) for x in (ident.get("upstreams") or ()) if x}
    return families, upstreams


def _ranked_candidates(agents, role, ttype, stats, exclude):
    """按评分从高到低给候选，不写影子台账、不触发策略选路副作用。

    换将隔离只是在「本来就会被选中的那批人」里挪顺序，所以这里不调用
    `pick()`——它每次都会追加挑战者影子记录，逐候选枚举会把台账写花。
    """
    rows = []
    seen = set()
    for agent in agents or ():
        if not isinstance(agent, dict):
            continue
        ident = str(agent.get("id") or "")
        if not ident or ident in exclude or ident in seen:
            continue
        seen.add(ident)
        try:
            total, _ = score(agent, role, ttype, stats)
        except Exception:
            total = 0.0
        try:
            total = float(total or 0.0)
        except (TypeError, ValueError):
            total = 0.0
        rows.append((-total, ident, agent))
    rows.sort(key=lambda row: (row[0], row[1]))
    return [row[2] for row in rows]


def _prefer_disjoint_candidate(agents, role, ttype, stats, exclude, difficulty,
                               failed_cli, best, reason):
    """瞬态失败后尽量换到异上游/异族，别换壳不换命（2026-10-23 审查 C1）。

    配额与 403 会进 health/dead 台账，由 `pick_switch_candidate` 主循环按
    上游去重；但 5xx、超时、解析失败不记账，这些情况原来只看分数：分数最高
    的备份往往就是同一个网关（同厂商不同 CLI 壳尤其常见），换过去仍然是同一
    个故障域，白烧一轮并把真正的跨族降级往后拖。

    只做「重排」不做「扩池」：候选仍来自调用方给的 agents，排除仍是 exclude；
    死者身份未知（取不到上游也取不到族）时原样返回——宁白试不误杀。
    """
    failed_id = str((failed_cli or {}).get("id") or "") if isinstance(failed_cli, dict) else ""
    if not failed_id or not isinstance(best, dict) or best.get("id") == failed_id:
        return best, reason
    failed_fams, failed_ups = _switch_identity(failed_cli, ttype, difficulty)
    if not failed_fams and not failed_ups:
        return best, reason      # 没有可比的身份：不重排
    best_fams, best_ups = _switch_identity(best, ttype, difficulty)
    if not ((best_ups and failed_ups and (best_ups & failed_ups))
            or (best_fams and failed_fams and (best_fams & failed_fams))):
        return best, reason      # 首选本就不在同一故障域

    same_gateway = bool(best_ups & failed_ups)
    upstream_only = None
    for candidate in _ranked_candidates(agents, role, ttype, stats, exclude):
        if candidate.get("id") == best.get("id"):
            continue
        if str(candidate.get("mode") or "") == "mock":
            continue
        fams, ups = _switch_identity(candidate, ttype, difficulty)
        up_ok = bool(failed_ups and ups and not (ups & failed_ups))
        fam_ok = bool(failed_fams and fams and not (fams & failed_fams))
        if up_ok and fam_ok:
            return candidate, (
                "瞬态失败后换到异上游异族候选 %s（原首选 %s 与死者%s，跳过）"
                % (candidate.get("id"), best.get("id"),
                   "同上游" if same_gateway else "同族"))
        if up_ok and upstream_only is None:
            upstream_only = candidate
    if upstream_only is not None:
        return upstream_only, (
            "瞬态失败后换到异上游候选 %s（原首选 %s 与死者%s，跳过）"
            % (upstream_only.get("id"), best.get("id"),
               "同上游" if same_gateway else "同族"))
    # 隔离候选耗尽：保留原选择（分数最高、且策略允许），reason 不动——这时
    # 选路结果与旧行为完全一致，往每条 reason 后面挂一句「无满足隔离的候选」
    # 只会在「全公司共用一个网关」这种常见部署里把换将说明刷成噪音。事实
    # 本身进日志，审计要看时能查。
    logging.getLogger(__name__).warning(
        "换将无满足隔离的候选：%s 与失败者 %s %s（role=%s type=%s）",
        best.get("id"), failed_id, "同上游" if same_gateway else "同族",
        role, ttype)
    return best, reason


def pick_switch_candidate(agents, role, ttype, stats, exclude=(),
                          dead_upstreams=(), difficulty="default",
                          blocked_upstreams=(), failed_cli=None, error=None):
    """换将选将。配额类死亡背景下：与死者同上游的候选依次让位，直到找到
    异上游/上游未知的候选（2026-09-22 实案：kimi 与 claude 同骑智谱，0.1 分
    之差把异上游 qwencode 压在下面，换将=换壳不换命）。异上游耗尽后同上游
    候选捡回分数最高者——聊胜于无。403 明确拒绝的上游始终阻断，不捡回重试。"""
    from . import routing_strategy

    exclude = set(exclude)
    blocked_sets = [set(d) for d in (blocked_upstreams or ()) if d]
    try:
        strategy = routing_strategy.get()
    except Exception:
        strategy = routing_strategy.BuiltinStrategy()
    if error is not None:
        try:
            allow_fallback = bool(strategy.should_fallback(error))
        except Exception as exc:
            logging.getLogger(__name__).warning(
                "routing strategy fallback check failed; using builtin: %s",
                type(exc).__name__)
            allow_fallback = routing_strategy.BuiltinStrategy().should_fallback(error)
        if not allow_fallback:
            code = (error.get("error_code") or ""
                    if isinstance(error, dict) else "")
            return None, "策略判定错误无需跨 CLI 降级%s" % (
                ("（%s）" % code) if code else "")

    preferred_ids = []
    if isinstance(failed_cli, dict):
        try:
            chain = strategy.get_fallback_chain(failed_cli)
        except Exception as exc:
            logging.getLogger(__name__).warning(
                "routing strategy fallback chain failed: %s", type(exc).__name__)
            chain = ()
        for item in chain or ():
            ident = item.get("id") if isinstance(item, dict) else item
            ident = str(ident or "").strip()
            if ident and ident not in preferred_ids:
                preferred_ids.append(ident)
    agents_by_id = {str(agent.get("id") or ""): agent for agent in agents
                    if isinstance(agent, dict) and agent.get("id")}

    def _choose(omitted):
        for index, ident in enumerate(preferred_ids, 1):
            candidate = agents_by_id.get(ident)
            if candidate is not None and ident not in omitted:
                return candidate, "策略后备链第 %d 位" % index
        return pick(agents, role, ttype, stats, exclude=omitted)

    best, reason = _choose(exclude)
    if best is not None and not dead_upstreams and not blocked_sets:
        # 有 dead/blocked 台账时下面的主循环已按上游去重，这层不叠加——两条
        # 路径各自负责「已知死上游」和「未知的瞬态失败」，避免 reason 互撞。
        best, reason = _prefer_disjoint_candidate(
            agents, role, ttype, stats, exclude, difficulty,
            failed_cli, best, reason)
    if best is None or (not dead_upstreams and not blocked_sets):
        if best is not None:
            _sticky_remember(role, ttype, best.get("id"))
        return best, reason
    deferred = []
    while best is not None:
        ups = agent_upstreams(best.get("id"), difficulty=difficulty,
                              task_type=ttype, role=role)
        if ups and any(ups & blocked for blocked in blocked_sets):
            # 明确的 403 权限拒绝：同一上游没有换模型/换 CLI 再试的价值，
            # 即使异上游候选耗尽也不把它捡回来（与可换账号的配额错误不同）。
            exclude.add(best.get("id"))
            best, reason = _choose(exclude)
            continue
        if not (ups and any(ups & dead for dead in dead_upstreams)):
            break  # 异上游或上游未知：就用它
        deferred.append((best, reason))
        best, reason = _choose(exclude | {
            x.get("id") for x, _ in deferred})
    if best is None and deferred:
        # 异上游全灭：同上游里挑**最快解禁**的顶上（magpie smart「最快重置的
        # 先用」借鉴，2026-10-22 调研）——健康台账有熔断时刻的按剩余秒数升序，
        # 查不到时间的维持原评分序，不假装知道。
        def _rem(item):
            r = _upstream_cooldown_remaining(item[0].get("id"), difficulty,
                                             ttype, role)
            return r if r is not None else float("inf")

        ranked = sorted(deferred, key=_rem)
        best, reason = ranked[0]
        rem = _rem(ranked[0])
        if rem != float("inf"):
            reason = ("同上游候选均受限，按最快解禁顶上 %s（熔断约 %.0f 分钟后解除）"
                      % (best.get("id"), rem / 60.0))
    elif deferred:
        reason = ("%s（%s 与死者同上游，延后让位异上游候选）"
                  % (reason, deferred[-1][0].get("id")))
    if best is not None:
        _sticky_remember(role, ttype, best.get("id"))
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


def _review_identity(agent, ttype, difficulty="default"):
    """Collect all known model/provider/upstream identities for one agent.

    Fallback call-chain rows matter: if a primary model is unavailable, a
    reviewer can silently move to a model or gateway shared with the author.
    Keep identities ephemeral and conservatively compare every configured row.
    """
    primary_family = str(agent.get("model_family") or "").strip()
    if primary_family.lower() == "unknown":
        primary_family = ""
    families = {primary_family} if primary_family else set()
    providers = set()
    configured_provider = str(agent.get("provider_id") or "").strip()
    if configured_provider:
        providers.add(configured_provider)
    upstreams = set()
    try:
        binding = modelhub.resolve_binding(agent.get("id"), difficulty) or {}
        for row in binding.get("call_chain") or []:
            if not isinstance(row, dict):
                continue
            model = str(row.get("model") or "").strip()
            if model:
                family = model_family.family(model)
                if family.lower() != "unknown":
                    families.add(family)
                    if not primary_family:
                        primary_family = family
            provider_id = str(row.get("provider_id") or "").strip()
            if provider_id:
                providers.add(provider_id)
            try:
                host = _host_of((row.get("provider") or {}).get("base_url"))
                if host:
                    upstreams.add(host)
            except (AttributeError, TypeError):
                pass
    except Exception:
        # Preserve routing availability if an optional binding is damaged.
        pass
    if not upstreams:
        try:
            upstreams = agent_upstreams(
                agent.get("id"), difficulty=difficulty,
                task_type=ttype, role="review")
        except Exception:
            upstreams = set()
    if not families:
        legacy_kind = str(agent.get("kind") or "").strip()
        if legacy_kind.lower() != "unknown":
            primary_family = legacy_kind
            families.add(legacy_kind)
    return {"family": primary_family, "families": families,
            "providers": providers, "upstreams": upstreams}


def _review_separation(left, right):
    """Return (isolated, reason), treating any shared fallback as same-source."""
    left_families = {str(item).casefold() for item in left["families"]}
    right_families = {str(item).casefold() for item in right["families"]}
    shared = left_families & right_families
    if shared:
        label = sorted(shared)[0]
        return False, "模型族相同（%s）" % label
    left_providers = {str(item).casefold() for item in left["providers"]}
    right_providers = {str(item).casefold() for item in right["providers"]}
    shared = left_providers & right_providers
    if shared:
        return False, "供应商相同（%s）" % sorted(shared)[0]
    shared = left["upstreams"] & right["upstreams"]
    if shared:
        return False, "上游相同（%s）" % sorted(shared)[0]
    return True, "模型族/供应商/上游均隔离"


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

    impl_identity = _review_identity(impl, ttype, difficulty)

    def _identity(agent):
        return _review_identity(agent, ttype, difficulty)

    def _separation(agent):
        return _review_separation(impl_identity, _identity(agent))
    real = [a for a in agents
            if a.get("mode") == "real" and a["id"] != impl["id"] and _alive(a)]
    isolated = []
    rejected = []
    for candidate in real:
        safe, why = _separation(candidate)
        if safe:
            isolated.append(candidate)
        else:
            rejected.append((candidate, why))
    # Prefer a genuinely isolated model family.  ``kind`` remains a tie-break
    # for legacy agents whose model metadata is unavailable.
    cross = [a for a in isolated if a.get("kind") != impl_kind]
    if cross:
        best = max(cross, key=lambda a: score(a, "review", ttype, stats)[0])
        _, reason = score(best, "review", ttype, stats)
        return best, "跨模型族/供应商评审（作者 %s，评审 %s）：%s" % (
            impl_identity["family"] or impl_kind,
            _identity(best)["family"] or best.get("kind"), reason)
    if isolated:
        best = max(isolated, key=lambda a: score(a, "review", ttype, stats)[0])
        _, reason = score(best, "review", ttype, stats)
        return best, "隔离约束满足但 CLI kind 相同（%s）：%s" % (
            _identity(best)["family"] or best.get("kind"), reason)
    if real:
        best = max(real, key=lambda a: score(a, "review", ttype, stats)[0])
        _, reason = score(best, "review", ttype, stats)
        reasons = "; ".join("%s: %s" % (a.get("id"), why)
                            for a, why in rejected[:3])
        return best, ("（无满足模型族/供应商/上游隔离的评审者，"
                      "兼容回退同源候选；原因：%s）" % reasons) + reason
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
    real = [a for a in agents if a.get("mode") == "real" and
            (not impl or a["id"] != impl["id"])]
    if real:
        ranked = sorted(real, key=lambda a: score(a, "review", ttype, stats)[0],
                        reverse=True)
        if impl and impl.get("mode") == "real":
            author_identity = _review_identity(impl, ttype)
            isolated, fallback = [], []
            for agent in ranked:
                safe, why = _review_separation(
                    author_identity, _review_identity(agent, ttype))
                (isolated if safe else fallback).append((agent, why))
            chosen = [agent for agent, _ in isolated[:AUTO_CRITIC_LIMIT]]
            fallback_used = []
            if len(chosen) < AUTO_CRITIC_LIMIT:
                count = AUTO_CRITIC_LIMIT - len(chosen)
                fallback_used = fallback[:count]
                chosen.extend(agent for agent, _ in fallback_used)
            note = "按成功率与延迟选择前 %d 名评审（共 %d 名候选）" % (
                len(chosen), len(ranked))
            if isolated:
                note += "；跨模型族隔离优先（并排除同供应商/同上游）"
            if fallback_used:
                detail = "; ".join("%s: %s" % (agent.get("id"), why)
                                    for agent, why in fallback_used[:3])
                note += "；同源候选兼容回退（原因：%s）" % detail
            return chosen, note
        chosen = ranked[:AUTO_CRITIC_LIMIT]
        note = "按成功率与延迟选择前 %d 名评审（共 %d 名候选）" % (
            len(chosen), len(ranked))
        if impl and all(a.get("kind") == impl.get("kind") for a in chosen):
            note += "；评审组与作者同为 %s，建议启用其他厂商 CLI 交叉评审" % impl.get("kind")
        return chosen, note
    mocks = [a for a in agents if a.get("mode") == "mock"]
    if mocks:
        return mocks[:2], "（无真实智能体，用内置 mock 演示）"
    if impl and impl.get("mode") == "real":
        # 只有一条 CLI 时，旧逻辑返回 agents[:2]，也就是把作者本人当评审——
        # 自评既拿不到独立意见，还会以「作者 id」计入评审人数，把一份自我确认
        # 记成两个人的共识。宁可返回空组，由 review_roster 派生独立复核席
        # （同源双会话），也不伪装成有评审。
        return [], ("（仅 %s 一条可用 CLI：不启用作者自评，改由单通道独立复核席评审）"
                    % (impl.get("label") or impl.get("id") or "CLI"))
    return agents[:2], "（无真实智能体）"
