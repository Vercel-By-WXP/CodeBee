"""分诊：归一化 → 指纹 → 根因初判 → 可修性决策 → 去重聚类。

目标不是「猜对根因」，而是把不可能自动修的和明显重复的挡掉，
让 AI 只处理值得处理的那部分，并带上足够上下文。
"""

from __future__ import annotations

import re
from typing import Dict, List, Optional

from . import util
from .models import AUTO_DECISION, ROOT_CAUSES, Bug

# 根因线索表：按特异性从高到低，命中即停（避免「null」压过「deadlock」）
_CAUSE_HINTS: List[tuple] = [
    ("concurrency", ("deadlock", "race condition", "concurrent modification", "竞态", "死锁", "并发", "lock wait", "脏读")),
    ("resource", ("too many open files", "memory leak", "泄漏", "泄露", "未释放", "connection pool", "句柄")),
    ("network", ("timeout", "timed out", "connection refused", "超时", "连接被拒绝", "502", "504", "econnreset", "socket")),
    ("dependency", ("modulenotfound", "no such module", "cannot find module", "version conflict", "依赖冲突", "找不到包", "noclassdeffound")),
    ("permission", ("permission denied", "401", "403", "unauthorized", "forbidden", "无权限", "没有权限", "鉴权")),
    ("encoding", ("unicodedecodeerror", "unicodeencodeerror", "乱码", "编码", "mojibake", "gbk")),
    ("boundary", ("indexerror", "out of range", "off-by-one", "越界", "数组下标", "空数组", "边界")),
    ("type_mismatch", ("typeerror", "classcastexception", "cannot convert", "类型错误", "类型不匹配", "undefined is not", "is not a function")),
    ("null_ref", ("nullpointerexception", "nonetype", "noneType", "cannot read propert", "is null", "undefined", "attributeerror", "空指针", "为空", "未初始化")),
    ("config", ("config", "environment variable", "配置", "环境变量", "profile not found", "missing setting")),
    ("data", ("dirty data", "data integrity", "constraint violation", "脏数据", "数据不一致", "唯一键", "重复插入")),
    ("state", ("invalid state", "illegal state", "状态", "流程", "order status", "重复提交")),
    ("perf", ("slow", "timeout after", "oom", "outofmemory", "内存溢出", "性能", "卡顿", "太慢")),
    ("compat", ("regression", "兼容", "回归", "老版本", "升级后", "previously worked", "works on my machine")),
    ("ui", ("样式", "布局", "错位", "遮挡", "点击无反应", "显示不全", "溢出", "闪烁")),
]

_INSUFFICIENT = 25  # 描述短于此长度且无堆栈 → 信息不足


def classify_root_cause(text: str) -> str:
    """按线索表的顺序取第一个命中。

    顺序即先验：越靠前的线索越特异、越不该被后面的泛化线索抢走
    （例如 NullPointerException 必须先判成 null_ref，不能被 dependency 抢走）。
    """
    low = (text or "").lower()
    if not low.strip():
        return "unknown"
    for cause, hints in _CAUSE_HINTS:
        for hint in hints:
            if hint in low:
                return cause
    return "unknown"


_FRAME_RE = re.compile(r'(?:File "([^"]+)", line (\d+)(?:, in ([\w.<>]+))?)|(?:\bat\s+([\w.$]+)\(([^()\s]+)\))')


def top_frames(text: str, limit: int = 6) -> List[str]:
    """取堆栈里最靠前的几帧，作为指纹的稳定部分（去重，同一帧只留一条）。"""
    frames: List[str] = []
    for match in _FRAME_RE.finditer(text or ""):
        if match.group(1):
            frame = f"{match.group(1)}:{match.group(2)}:{match.group(3) or ''}"
        elif match.group(5):
            frame = f"{match.group(5)}:{match.group(4)}"
        else:
            continue
        if frame in frames:
            continue
        frames.append(frame)
        if len(frames) >= limit:
            break
    return frames


def bug_fingerprint(bug: Bug) -> str:
    """三类信息合成指纹：异常类型 + 标题骨架 + 前几帧（均归一化）。

    去掉行号与绝对路径后同类错误必然收敛，这是「同一错误第二次出现能被识别」的基础。
    设计权衡：steps 不直接进指纹——复现步骤的人写措辞差异太自由（「步骤：」、
    「复现如下」、空行），两条本质相同的 bug 会因此分叉。指纹只认三类强信号：
    异常类型（root_cause 归一后）、标题骨架、堆栈帧文件名。

    帧信号在禅道富文本里常被 <br> 打断（File "…" 和行号被标签隔开）——
    top_frames 拿不到帧时退到「异常类型词表」，措辞噪声不进指纹。
    """
    text = bug.text
    cause = classify_root_cause(text)
    # 帧信号只用文件名（行号在 normalize 里抹掉，函数名可能因版本漂移缺省），
    # 且排序去重——帧顺序在不同上报里不保证稳定
    frame_names = sorted({f.split(":")[0] for f in top_frames(text)})
    if not frame_names:
        # 无堆栈：异常主类型词（AttributeError/TimeoutError…）代替帧信号。
        # 措辞自由度会击穿指纹，所以宁可用更粗但更稳的信号。
        frame_names = sorted(set(re.findall(
            r"[A-Za-z]*(?:Error|Exception|Timeout|Refused|Expired|Deadlock)[A-Za-z]*",
            text)))
    return util.fingerprint(bug.title, cause, " ".join(frame_names))


def decide(bug: Bug, known_fingerprints: Optional[set] = None) -> tuple:
    """返回 (decision, note)。decision ∈ auto / manual / skip。"""
    known_fingerprints = known_fingerprints or set()
    status = (bug.status or "").lower()
    if status in ("closed", "resolved", "done", "fixed"):
        return "skip", f"状态为 {bug.status}，已闭环"
    if status in ("", "active", "open"):
        pass
    body = (bug.steps or "").strip()
    if bug.severity_rank <= 1 and not bug.stack:
        return "manual", "P1 且无堆栈，先让人确认影响面"
    if len(body) < _INSUFFICIENT and not bug.stack:
        return "manual", f"描述仅 {len(body)} 字且无堆栈，信息不足以定位"
    if bug.fingerprint and bug.fingerprint in known_fingerprints:
        return "auto", "命中历史修复指纹，有已知修法可参照"
    if bug.stack:
        return "auto", "含可解析堆栈，可直接定位到文件行"
    if len(body) >= 60:
        return "auto", "复现描述完整，可先做关键词定位"
    return "manual", "无堆栈且描述偏短，建议人工补充复现步骤"


def triage(bugs: List[Bug], known_fingerprints: Optional[set] = None) -> Dict[str, object]:
    """就地打标并聚类重复项。返回统计摘要。"""
    known = known_fingerprints or set()
    by_fp: Dict[str, Bug] = {}
    stats = {"total": len(bugs), "auto": 0, "manual": 0, "skip": 0, "duplicates": 0, "by_cause": {}}

    # 先算指纹，再按指纹聚类
    for bug in bugs:
        bug.fingerprint = bug.fingerprint or bug_fingerprint(bug)
        bug.root_cause = bug.root_cause or classify_root_cause(bug.text)

    for bug in sorted(bugs, key=lambda b: (b.severity_rank, b.created or "", b.id)):
        original = by_fp.get(bug.fingerprint)
        if original and original.id != bug.id:
            bug.duplicate_of = original.id
            bug.decision = "skip"
            bug.triage_note = f"与 #{original.id} 同指纹（{bug.fingerprint}），合并处理"
            stats["duplicates"] += 1
            stats["skip"] += 1
            continue
        by_fp.setdefault(bug.fingerprint, bug)
        decision, note = decide(bug, known)
        bug.decision = decision if decision in AUTO_DECISION else "manual"
        bug.triage_note = note
        stats[bug.decision] += 1
        causes = stats["by_cause"]
        causes[bug.root_cause] = causes.get(bug.root_cause, 0) + 1  # type: ignore[index]
    return stats


def cause_label(cause: str) -> str:
    return ROOT_CAUSES.get(cause, cause)
