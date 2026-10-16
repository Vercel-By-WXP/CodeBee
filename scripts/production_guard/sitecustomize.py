"""Audit-hook safety belt for test runs; never allow access to production 8765."""
from __future__ import annotations

import os
import re
import sys


_PROTECTED_PIDS = {
    value for value in os.environ.get("CODEBEE_TEST_PROTECTED_PIDS", "").split(",")
    if value
}


def _guard(event: str, args: tuple[object, ...]) -> None:
    if event in ("socket.connect", "socket.bind"):
        address = args[1]
        if isinstance(address, tuple) and len(address) > 1 and address[1] == 8765:
            raise RuntimeError("Production port 8765 is forbidden in this test run")

    if event != "subprocess.Popen":
        return

    command = args[1]
    text = " ".join(str(item) for item in command) if isinstance(command, (list, tuple)) else str(command)
    if re.search(r"main\.py[\"\s]+.*--port(?:=|\s+)8765(?:\s|$)", text, re.I):
        raise RuntimeError("Starting a server on production port is forbidden")
    if re.search(r"taskkill|Stop-Process", text, re.I):
        if re.search(r"/IM\s+(?:python|py|node)", text, re.I):
            raise RuntimeError("Broad runtime process termination is forbidden")
        if any(re.search(r"(?:/PID|-Id)\s+" + re.escape(pid) + r"(?:\s|$)", text, re.I)
               for pid in _PROTECTED_PIDS):
            raise RuntimeError("Terminating the production process is forbidden")


sys.addaudithook(_guard)
