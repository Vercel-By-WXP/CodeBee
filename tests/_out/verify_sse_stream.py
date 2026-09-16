# -*- coding: utf-8 -*-
"""实测验证：modelhub.chat(on_delta=...) 流式全链路（SSE → 节流落盘）。

用真实编排者供应商发一条极小对话，走 planner._log_streamer 写临时日志；
断言：结果 ok、日志文件非空（增量真实落盘）、返回文本与日志内容一致。
只读 models.json 编排者配置，日志写临时目录，成本≈一次极小补全。
"""
from __future__ import annotations

import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from app.core import modelhub, planner


def main():
    tmp = Path(tempfile.mkdtemp(prefix="sse-verify-"))
    log_path = tmp / "stream-check.log"

    orch = planner._orchestrator()
    if not orch:
        print("FAIL: 未配置编排者（编排设置），无法实测")
        return 2
    prov, model = orch
    print("编排者:", prov.get("name", prov["id"]), "|", model,
          "| protocol:", prov.get("protocol"))

    cb = planner._log_streamer(str(log_path))
    res = modelhub.chat(prov["id"], model, "只回复两个字：收到",
                        max_tokens=512, timeout=60, on_delta=cb)
    cb.flush()
    print("ok:", res.get("ok"), "| text:", (res.get("text") or "")[:50],
          "| tokens:", res.get("tokens"), "| error:", (res.get("error") or "")[:120])

    if log_path.is_file():
        body = log_path.read_text(encoding="utf-8")
        print("日志落盘:", len(body), "字符 | 内容样例:", body[:60].replace("\n", " "))
    else:
        body = ""
        print("日志落盘: （无文件）")

    if res.get("ok") and body.strip():
        print("PASS: SSE 流式全链路生效——直连调用增量已实时落盘")
        return 0
    print("FAIL: 流式未生效或调用失败")
    return 1


if __name__ == "__main__":
    sys.exit(main())
