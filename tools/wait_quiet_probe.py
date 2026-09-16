#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""探测 app.js 写入活跃模式：找最长连续稳定窗口并报告长度。"""
import hashlib
import os
import time
from pathlib import Path

TARGET = Path(os.environ.get("TARGET_FILE", "E:/GoOut/MultiAgentOrchestration/app/ui/app.js"))


def sig():
    try:
        data = TARGET.read_bytes()
        return (hashlib.sha256(data).hexdigest()[:10], len(data))
    except Exception as e:
        return ("ERR", str(e))


start = time.time()
samples = [(sig(), time.time())]
while time.time() - start < 240:
    s, t = sig()
    if s == samples[-1][0]:
        samples.append((s, t))
        time.sleep(2)
        continue
    samples.append((s, t))
    time.sleep(2)

stable_starts = []
for i in range(1, len(samples)):
    if samples[i][0] == samples[i - 1][0]:
        start_ts = samples[i - 1][1]
        stable_starts.append((time.time() - start_ts, start_ts, samples[i - 1][0]))

if stable_starts:
    longest = max(stable_starts, key=lambda x: x[0])
    print("样本数:", len(samples))
    print("最长连续稳定窗口: %.1fs 开始于 %.1f (start=%s)" %
          (longest[0], longest[1], time.strftime("%H:%M:%S", time.localtime(longest[1]))))
    print("各稳定窗口(秒):", [round(x[0], 1) for x in stable_starts])
    last = stable_starts[-1] if stable_starts else (None, None)
    print("最后一段稳定窗口: %.1fs" % (last[0] if last else 0))
else:
    print("未观测到稳定窗口")
