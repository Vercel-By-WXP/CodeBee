# -*- coding: utf-8 -*-
"""本场停用台账：撞过账号级死错的 CLI，后续步骤不再重试。

实案（2026-10-15《头七结账人》复评）：qwen 欠费 403「预扣费额度失败」
被分类成 FORBIDDEN 后，连载评审每一章仍按 critic 名单把 qwen 原样送场，
同一条死链连撞 31 次、白烧 4 小时——失败早已证明该账号本场不可能恢复，
后续章节应该直接绕开它、让活着的评审顶上，全死才收场。

设计边界：
· 只在 run 内生效（进程内字典，key=run_id），不落盘、不跨 run——
  充值/登录是用户动作，下一 run 重新探测，绝不把「昨晚欠费」错杀成
  「今天也不许用」。
· 只登记账号级死错（error_codes.ACCOUNT_DEAD_CODES：QUOTA/AUTH/
  MISSING_CREDENTIAL/FORBIDDEN/TOOL_LAUNCH）；限流/超时/网络抖动是
  transient，退避重试的既有路径不变，不进台账。
· 一切读接口静默容错：台账挂了不能连累编排主链路。
"""
from __future__ import annotations

import threading
import time

from .error_codes import ErrorCode, error_code_value, safe_error_summary

_LOCK = threading.Lock()
# run_id -> {agent_id: {"code": str, "summary": str, "at": float}}
_BENCHED = {}
_MAX_RUNS = 256   # 长驻服务的内存护栏：超限丢最老条目（run 已终态，台账自然失效）


def bench(run_id, agent_id, code, at=None):
    """把 agent 记入 run 的本场停用名单。非账号级死错静默忽略。

    首报即停用：账号级死错没有「再试一次说不定好了」的空间——
    重试本身就是浪费（每次失败都是一次完整 CLI 冷启动）。
    """
    if not run_id or not agent_id:
        return False
    value = error_code_value(code)
    from .error_codes import is_account_dead_error
    if not is_account_dead_error(value):
        return False
    row = {
        "code": value,
        "summary": safe_error_summary(value),
        "at": float(at if at is not None else time.time()),
    }
    with _LOCK:
        entries = _BENCHED.setdefault(str(run_id), {})
        if agent_id in entries:
            return False   # 已停用，保留首报现场
        entries[agent_id] = row
        if len(_BENCHED) > _MAX_RUNS:
            oldest = min(_BENCHED, key=lambda k: max(
                (v.get("at") or 0) for v in _BENCHED[k].values()) or 0)
            _BENCHED.pop(oldest, None)
    return True


def reset(run_id):
    """run 收场时清掉台账（幂等；测试也用它隔离用例）。"""
    with _LOCK:
        _BENCHED.pop(str(run_id), None)


def is_benched(run_id, agent_id):
    """该 CLI 是否已在本场停用名单。任何异常都按「未停用」处理——宁白试不误杀。"""
    try:
        with _LOCK:
            return str(agent_id) in _BENCHED.get(str(run_id), {})
    except Exception:
        return False


def reason(run_id, agent_id):
    """停用原因的一句话说明（未停用返回空串），供步骤备注/错误文案拼接。"""
    try:
        with _LOCK:
            row = _BENCHED.get(str(run_id), {}).get(str(agent_id))
        if not row:
            return ""
        return "%s（%s）" % (row.get("summary") or row.get("code") or "账号级错误",
                            row.get("code") or "")
    except Exception:
        return ""


def benched_agents(run_id):
    """run 的停用名单快照：{agent_id: 原因说明}。读侧容错，异常返回空 dict。"""
    try:
        with _LOCK:
            entries = dict(_BENCHED.get(str(run_id), {}))
        return {aid: "%s（%s）" % (v.get("summary") or v.get("code") or "账号级错误",
                                  v.get("code") or "")
                for aid, v in entries.items()}
    except Exception:
        return {}


def benched_ids(run_id):
    """停用 agent id 集合——换将/补位的 exclude 名单直接并入即可。"""
    try:
        with _LOCK:
            return set(_BENCHED.get(str(run_id), {}))
    except Exception:
        return set()
