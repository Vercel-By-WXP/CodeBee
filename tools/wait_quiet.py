#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""等待 app/ui/app.js 进入稳定（无写入者）窗口。
环境变量 TARGET_FILE 指定要监测的文件；默认 app/ui/app.js。
环境变量 WINDOW_SEC 稳定时长（默认 60s）。
每 2s 采一次 (md5前10位, 字节数)；连续达标 WINDOW_SEC 秒即返回 0 退出。
若长时间无窗口，返回 1。
"""
import hashlib
import os
import sys
import time
from pathlib import Path

TARGET = Path(os.environ.get("TARGET_FILE", "E:/GoOut/MultiAgentOrchestration/app/ui/app.js"))
WINDOW = int(os.environ.get("WINDOW_SEC", "60"))
sample = [None]
start = time.time()
while time.time() - start < 300:
    try:
        data = TARGET.read_bytes()
        sig = hashlib.sha256(data).hexdigest()[:10]
        size = len(data)
        sig_str = (sig, size)
    except Exception as e:
        sig_str = ("ERR", str(e))
    if sig_str == sample[-1]:
        time.sleep(2)
        continue
    sample.append(sig_str)
    # 已进入稳定，继续盯
    time.sleep(2)
    if time.time() - start >= 300:
        print("NO_WINDOW_TIMEOUT", file=sys.stderr)
        return 1
    # 若连续采样中已稳定 WINDOW 秒，退出
    stable_since = None
    for i in range(len(sample) - 1, 0, -1):
        if sample[i] == sample[i - 1]:
            stable_since = time.time() - sample[0] if i == 1 else time.time() - sample[i]
            break
    if stable_since and time.time() - stable_since >= WINDOW:
        print("QUIET_WINDOW_READY at", time.strftime("%H:%M:%S"))
        return 0

print("NO_WINDOW_READY", file=sys.stderr)
return 1
