# -*- coding: utf-8 -*-
"""重复 CLI 调用检测：防止编排者用同一份 prompt 反复调同一 CLI 死循环。

设计稿：docs/migration/01-defense-patterns.md §5C。
参考 dsh packages/guard/repeat-tool-reminder/src/index.ts：
  监听 tools/post-execute，按 (tool, canonical_args) 跟踪，3/5/8 阈值注入 user-message 提醒。

Tutti 的 "tool" 对应到 "step role"，"canonical_args" 对应到 prompt 模板指纹。
步骤级调用代价远高于普通工具调用，因此第 3 次提醒、第 5 次强制停止。
"""
from __future__ import annotations

import hashlib
import logging
import threading
import time

log = logging.getLogger(__name__)

# 渐进阈值：第 N 次命中时给提醒；最后一次及以上时强制停止。
DEFAULT_THRESHOLDS = (3, 5)

REMINDER_TEMPLATE = (
    "⚠️ 提醒：上一步使用了几乎相同的输入（已连续 {count} 次调用 {role}）。"
    "请换一种方法：读不同的文件、用更具体的指令、或明确询问用户澄清。"
)

STOP_TEMPLATE = (
    "已连续 {count} 次使用相同输入调用 {role}，超过 {last_thr} 次阈值，"
    "强制停止本 step 以防止无限循环。请用户检查输入或调整 prompt 模板。"
)


def _prompt_fingerprint(prompt: str) -> str:
    """prompt 指纹：用前 200 字符 + 总长度（CLI prompt 头几行通常是模板部分）。

    不做严格 canonical args 比对（CLI prompt 通常很长，且 dsh 也用 JSON.stringify）；
    指纹命中说明是同一个模板路径走过多次，已经够分辨。

    用 SHA-256 取前 12 字符（96 bit）：48 亿分之一碰撞概率对去重足够；不追求密码学强度。
    """
    head = (prompt or "")[:200]
    return hashlib.sha256(f"{head}|{len(prompt or '')}".encode("utf-8")).hexdigest()[:12]


class RepeatGuard:
    """每 (run_id, step_role) 独立计数；key = prompt 指纹。

    计数仅统计相同 key 连续出现；新 prompt 会重置。
    """

    def __init__(self, thresholds=DEFAULT_THRESHOLDS, *, max_history=50):
        self.thresholds = tuple(sorted(set(thresholds)))
        self.max_history = max_history
        self._lock = threading.Lock()
        # chain[(run_id, role)] = [(ts, fingerprint)]
        self._chains: dict = {}

    def check(self, run_id: str, role: str, prompt: str) -> dict:
        """检查并更新 (run_id, role) 的连续计数。

        Returns:
            {"count": int, "reminder": str | None, "should_stop": bool}
        """
        fp = _prompt_fingerprint(prompt)
        chain_key = (run_id, role)
        with self._lock:
            chain = self._chains.setdefault(chain_key, [])
            chain.append((time.time(), fp))
            # 仅统计相同 fp 连续出现
            count = 1
            for _, prev_fp in reversed(chain[:-1]):
                if prev_fp == fp:
                    count += 1
                else:
                    break
            # 截尾避免内存膨胀
            if len(chain) > self.max_history:
                self._chains[chain_key] = chain[-self.max_history:]

        reminder = None
        stop = False
        last_thr = self.thresholds[-1]
        if count >= last_thr:
            stop = True
            reminder = STOP_TEMPLATE.format(count=count, role=role, last_thr=last_thr)
        elif count in self.thresholds:
            reminder = REMINDER_TEMPLATE.format(count=count, role=role)

        if reminder:
            log.warning("repeat-guard %s/%s count=%d stop=%s reminder=%s",
                        run_id, role, count, stop, reminder[:80])
        return {"count": count, "reminder": reminder, "should_stop": stop}

    def reset(self, run_id: str) -> int:
        """重置某 run_id 的所有 (run_id, *) 链。返回清除的链条数。"""
        with self._lock:
            cleared = 0
            for k in list(self._chains.keys()):
                if k[0] == run_id:
                    del self._chains[k]
                    cleared += 1
            return cleared

    def stats(self, run_id: str, role: str) -> dict:
        """读取 (run_id, role) 当前状态（不更新）。用于 UI 调试。"""
        chain_key = (run_id, role)
        with self._lock:
            chain = self._chains.get(chain_key, [])
            if not chain:
                return {"chain_len": 0, "last_fp": ""}
            # 当前连续计数
            last_fp = chain[-1][1]
            count = 1
            for _, fp in reversed(chain[:-1]):
                if fp == last_fp:
                    count += 1
                else:
                    break
            return {"chain_len": len(chain), "last_fp": last_fp, "count": count}


# 模块级默认实例（pipeline 接线用；run_id 入键天然隔离多任务，测试无需替换）
guard = RepeatGuard()
