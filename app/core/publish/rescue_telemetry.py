# -*- coding: utf-8 -*-
"""人工救火动作台账：append-only JSONL，按月分文件。

为什么单独一份台账
------------------
``journey`` 指标要判「这本书有没有被人工救过」，但现有落盘里查不到：
``store.retry_task`` 建新 run 时不写「谁触发的」；解除暂停时
``store_messages.py`` 把 ``pause_requested_at`` / ``pause_reached_at`` 清成
None；改稿单是只读现算。没记录 ≠ 没发生，所以只读报告只能一律 unknown。

这里在**用户动作发生的那一层**（API 路由）记一笔，不改状态机语义：

- ``POST /api/tasks/<id>/retry`` 成功入队 → ``manual_retry``
- ``POST /api/runs/<id>/pause`` 带 ``paused=false`` 成功 → ``manual_pause_resume``

落盘 ``<data_dir>/publish/rescue-YYYYMM.jsonl``，与发布台账同目录、同范式。

三条纪律
--------
1. **记账失败绝不影响主流程**：整个 record 包在 try 里，任何异常吞掉返回
   False。人工点了「继续任务」，哪怕磁盘满了也必须能继续。
2. **fail-closed 校验**：kind 不在 ``RESCUE_LOG_KINDS``、task_id / source 缺失，
   一律拒写返回 False。宁可少记，不写脏数据污染指标。
3. **幂等**：同 (kind, task_id, source, ts 秒) 只记一次；重启后靠读当月文件
   复核。重复事件本来也不会影响 journey（它按 kind 集合判），但台账要干净。

dev / prod
----------
``record(..., data_dir=None)`` 默认写 ``paths.PUBLISH_DIR``（生产库）；守测
传临时目录做结构夹具。**默认开启**——这是唯一一处新增的写行为，只多一个
append-only 文件，不碰 run/task/state 任何字段，也不需要重启就能被读侧看到；
但服务进程要加载新模块，仍需 11-09 重启后才会开始记账（见交接单第 3 节）。
"""
from __future__ import annotations

import json
import threading
import time
from pathlib import Path

from .. import paths
from . import telemetry_contract as contract

LOCK = threading.RLock()
FIELDS = ("ts", "day", "kind", "task_id", "source", "run_id", "note")
MAX_NOTE_CHARS = 200
MAX_SOURCE_CHARS = 40
MAX_TASK_CHARS = 64


def _month_file(day, data_dir=None):
    # data_dir 的语义与 journey_sources 一致：**数据根**（含 publish/ 子目录）。
    # 不传时 paths.PUBLISH_DIR 本身就是 publish 目录，不要再拼一层。
    folder = (Path(data_dir) / "publish") if data_dir else paths.PUBLISH_DIR
    return folder / ("rescue-%s.jsonl" % str(day)[:7].replace("-", ""))


def _already(path, key):
    """读当月文件复核幂等键；读失败返回 False（不阻塞写）。"""
    try:
        if not path.exists():
            return False
        with open(str(path), "r", encoding="utf-8") as fh:
            for line in fh:
                line = line.strip()
                if not line:
                    continue
                try:
                    rec = json.loads(line)
                except (ValueError, TypeError):
                    continue
                got = (rec.get("kind"), rec.get("task_id"),
                       rec.get("source"), rec.get("ts"))
                if got == key:
                    return True
    except OSError:
        return False
    return False


def iter_records(data_dir):
    """只读遍历 rescue-*.jsonl；坏行返回 (记录, 坏行数)，不静默吞。"""
    base = Path(data_dir) / "publish"
    records, bad = [], 0
    if not base.is_dir():
        return records, bad, False
    for path in sorted(base.glob(contract.RESCUE_FILE_GLOB)):
        try:
            with open(str(path), "r", encoding="utf-8") as fh:
                for line in fh:
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        rec = json.loads(line)
                    except (ValueError, TypeError):
                        bad += 1
                        continue
                    if not isinstance(rec, dict):
                        bad += 1
                        continue
                    records.append(rec)
        except OSError:
            bad += 1
    return records, bad, True


def record(kind, task_id, source, run_id="", note="", data_dir=None):
    """记一条人工动作。返回 True 表示已落盘（或已存在幂等记录）。

    **绝不外抛异常**：调用方在 API 成功分支里直接调，不该被记账拖挂。
    """
    try:
        kind = str(kind or "").strip()
        tid = str(task_id or "").strip()[:MAX_TASK_CHARS]
        src = str(source or "").strip()[:MAX_SOURCE_CHARS]
        if kind not in contract.RESCUE_LOG_KINDS or not tid or not src:
            return False
        ts = time.strftime("%Y-%m-%d %H:%M:%S")
        rec = {
            "ts": ts,
            "day": ts[:10],
            "kind": kind,
            "task_id": tid,
            "source": src,
            "run_id": str(run_id or "")[:64],
            "note": str(note or "")[:MAX_NOTE_CHARS],
        }
        path = _month_file(rec["day"], data_dir)
        key = (rec["kind"], rec["task_id"], rec["source"], rec["ts"])
        with LOCK:
            if _already(path, key):
                return True
            folder = path.parent
            if not folder.is_dir():
                folder.mkdir(parents=True, exist_ok=True)
            with open(str(path), "a", encoding="utf-8") as fh:
                fh.write(json.dumps(rec, ensure_ascii=False) + "\n")
        return True
    except Exception:
        # 磁盘满 / 权限 / 编码 / 目录被占用：主流程照常往下走。
        return False
