# -*- coding: utf-8 -*-
"""一键发布：CDP 驱动平台后台（番茄/七猫）建书与章节发布。

分层：ws.py（最小 WebSocket）→ browser.py（CDP 驱动）→ ledger.py（台账）
→ 平台流程（数据驱动的步骤表，selector 可外置覆盖）→ manager.py（状态机）。
"""
from . import browser, ledger  # noqa: F401
