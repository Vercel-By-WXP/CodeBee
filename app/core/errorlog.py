# -*- coding: utf-8 -*-
"""错误台账：把分散在各处的失败（步骤失败/超时/看门狗击杀、CLI 启动失败）
统一落成结构化记录——「客户遇到了问题我们根本不知道」的第一块拼图。

落盘：data/errors/errors-YYYYMM.jsonl（按月分文件，append-only，一行一记录）。
与 usage.py 同一套纪律：
- record() 任何异常都吞掉——埋点失败绝不能影响任务执行；
- 落盘前先脱敏（scrub_text）：密钥/令牌/绝对路径/超长文本一概不进台账。

隐私红线（上传侧在 telemetry.py 再兜一道底）：
1. API KEY / 凭据绝不入台账；
2. 任务正文 / 章节内容（用户版权内容）绝不入台账——detail 只允许失败原因摘录；
3. 绝对路径剥成相对片段。
"""
from __future__ import annotations

import json
import threading
import time
import uuid

from . import paths
from .redact import scrub_text

LOCK = threading.RLock()

FIELDS = ("id", "ts", "day", "category", "reason", "detail", "provider",
          "model", "tool", "role", "run_id", "task_id", "step",
          "exit_code", "app_version", "os")

# detail 摘录上限（字符）：失败原因足够，不要变成日志搬运
DETAIL_LIMIT = 600


# ---------------------------------------------------------------- 脱敏

def _coerce_str(v, limit):
    return str(v or "")[:limit]


def _os_tag():
    import sys
    return {"win32": "windows", "darwin": "macos"}.get(sys.platform, sys.platform)


def _version():
    try:
        from . import selfupdate
        return str(selfupdate.package_version() or "dev")
    except Exception:
        return "dev"


def record(category="", reason="", detail="", provider="", model="", tool="",
           role="", run_id="", task_id="", step=0, exit_code=None, source=""):
    """追加一条错误记录。缺省字段留空；任何异常都吞掉（埋点不拖垮业务）。

    category: step / run / launch / service（枚举放宽，未知值原样入库）
    reason:   失败原因码（error_codes.ErrorCode 的字符串值优先，如 TIMEOUT）
    detail:   失败摘录（内部先过 scrub_text，绝不存原文）
    """
    try:
        rec = {
            "id": "%s-%s" % (time.strftime("%Y-%m-%d %H:%M:%S"),
                             uuid.uuid4().hex[:8]),
            "ts": time.strftime("%Y-%m-%d %H:%M:%S"),
            "day": time.strftime("%Y-%m-%d"),
            "category": _coerce_str(category, 24) or "step",
            "reason": _coerce_str(reason, 40) or "UNKNOWN",
            "detail": scrub_text(detail),
            "provider": _coerce_str(provider, 60),
            "model": _coerce_str(model, 80),
            "tool": _coerce_str(tool, 24),
            "role": _coerce_str(role, 40),
            "run_id": _coerce_str(run_id, 64),
            "task_id": _coerce_str(task_id, 64),
            "step": int(step or 0),
            "exit_code": exit_code,
            "app_version": _version()[:24],
            "os": _os_tag(),
        }
        line = json.dumps(rec, ensure_ascii=False)
        day = rec["day"]
        with LOCK:
            paths.ERRORS_DIR.mkdir(parents=True, exist_ok=True)
            with open(paths.ERRORS_DIR / ("errors-%s.jsonl" % day[:7].replace("-", "")),
                      "a", encoding="utf-8") as f:
                f.write(line + "\n")
    except Exception:
        pass


def iter_records(days=0):
    """按时间范围读取台账（days=0 表示全部）。返回按写入顺序的记录列表。"""
    out = []
    try:
        files = sorted(paths.ERRORS_DIR.glob("errors-*.jsonl")) if paths.ERRORS_DIR.is_dir() else []
    except Exception:
        return out
    since_day = ""
    if days:
        import datetime
        since_day = (datetime.date.today()
                     - datetime.timedelta(days=int(days) - 1)).isoformat()
    for p in files:
        try:
            for line in p.read_text(encoding="utf-8", errors="replace").splitlines():
                line = line.strip()
                if not line.startswith("{"):
                    continue
                try:
                    r = json.loads(line)
                except Exception:
                    continue
                if not isinstance(r, dict):
                    continue
                if since_day and str(r.get("day", "")) < since_day:
                    continue
                out.append(r)
        except Exception:
            continue
    return out


def pending_since(cursor, limit=200):
    """取 cursor（上次上传到的记录 id）之后的记录，最多 limit 条。

    id 以时间开头、uuid 尾巴保证唯一且字典序≈时间序；返回 (records, new_cursor)。
    """
    try:
        recs = [r for r in iter_records(0) if str(r.get("id") or "") > str(cursor or "")]
        recs.sort(key=lambda r: str(r.get("id") or ""))
        batch = recs[:max(1, int(limit))]
        new_cursor = str(batch[-1].get("id")) if batch else str(cursor or "")
        return batch, new_cursor
    except Exception:
        return [], str(cursor or "")
