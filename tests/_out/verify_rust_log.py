# -*- coding: utf-8 -*-
"""实测验证：codex 是否认 RUST_LOG=codex_otel=off（call_cli 注入的源头静音）。

走真实链路：registry 真实 codex 智能体 → modelhub.bind_agent（跨厂商链/凭据）
→ runner.run_agent 极小 prompt → 检查步骤日志无 codex_otel WARN 且事件流正常。
只读真实 models.json/orchestration.json；日志写到临时目录；花一次极小补全的量。
"""
from __future__ import annotations

import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from app.core import catalog, manager, modelhub, registry, runner


def main():
    tmp = Path(tempfile.mkdtemp(prefix="rustlog-verify-"))
    log_path = tmp / "codex-rustlog-check.log"

    agents = registry.effective_agents(catalog.load(), manager.detect_all())
    agent = next((a for a in agents if a.get("kind") == "codex"), None)
    if not agent:
        print("FAIL: 未找到真实 codex 智能体（未安装或未启用）")
        return 2
    agent = modelhub.bind_agent(agent)
    print("agent:", agent.get("id"), "| model:", agent.get("model"),
          "| chain:", len(agent.get("call_chain") or []))

    res = runner.run_agent(agent, "只回复两个字：收到", workdir=str(tmp),
                           readonly=True, timeout=180, log_path=str(log_path))
    print("ok:", res.get("ok"), "| text:", (res.get("text") or "")[:60],
          "| error:", (res.get("error") or "")[:120])

    log = log_path.read_bytes().decode("utf-8", "replace")
    warns = log.count("WARN codex_otel")
    events = log.count('"type":"')
    print("日志：codex_otel WARN 条数 =", warns, "| JSONL 事件标记 =", events)
    if res.get("ok") and warns == 0 and events > 0:
        print("PASS: RUST_LOG=codex_otel=off 生效——遥测静音，事件流正常")
        return 0
    if res.get("ok") and warns > 0:
        print("PARTIAL: 调用成功但仍有 WARN——codex 不认 RUST_LOG，显示层折叠兜底")
        return 1
    print("FAIL: 调用未成功，需先排查 CLI/网络（本测试结论无效）")
    return 2


if __name__ == "__main__":
    sys.exit(main())
