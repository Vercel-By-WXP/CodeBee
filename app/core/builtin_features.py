# -*- coding: utf-8 -*-
"""内置功能（builtin features）：禅道 / 微信群摘要 / 端口扫描的「市场卡片」视图。

定位（2026-10-30 架构决策）：这三个功能是深度集成模块（自有路由 + 设置页 +
automation._tick 调度钩子 + 原子落盘），**不做**可安装的第三方插件——现有插件契约
（plugins.py）只装「技能包 + MCP 声明」、从不执行第三方代码，装不下它们；portscan
更在 L0 层被 portguard 依赖，不可拆卸。本模块只做一件事：把它们聚合成本市场页
「连接器 → 内置功能」区需要的**只读状态清单**——

  - enabled：功能当前是否在跑（读各自配置开关，zentao.poll_enabled /
    wxdigest.enabled）；端口扫描无常驻开关（按需点扫），恒 not_configured。
  - configured：是否已填过关键配置（zentao=base_url+account；wxdigest=watch_dir）。
    未配置的「开启」按钮直接跳设置页，不在卡片上原地开闸。

开关动作不走本模块：前端委托给既有的 /api/zentao/config、/api/wxdigest/config
部分更新链路（校验/节流/落盘/next_scan 语义全部复用），本模块零写权限。
"""
from __future__ import annotations

# 卡片元数据（前端只读展示；title/note 走 i18n，这里存中文键）
_FEATURES = (
    {
        "id": "zentao",
        "title": "禅道 Bug 自动修复",
        "note": "定时拉取禅道 Bug，排查定责后自动建修复任务并回写流转",
        "settings_sub": "zentao",          # switchTab 目标（设置页 data-sub）
        "toggle_key": "poll_enabled",      # /api/zentao/config 的开关字段
    },
    {
        "id": "wxdigest",
        "title": "微信群聊摘要",
        "note": "监控文件夹里的群聊导出，按群增量生成 AI 摘要并推送到群",
        "settings_sub": "__wxdigest",      # 设置页 data-sub（历史双下划线页 id）
        "toggle_key": "enabled",           # /api/wxdigest/config 的开关字段
    },
    {
        "id": "portscan",
        "title": "端口扫描",
        "note": "查看本机端口占用与归属项目；系统进程与自身服务拒绝关闭",
        "settings_sub": "improve",         # 「帮助改进」页的端口占用段
        "toggle_key": "",                  # 无常驻开关：不渲染 toggle，只给「去使用」
    },
)


def _zentao_state():
    from . import zentao
    cfg = (zentao.view() or {}).get("config") or {}
    configured = bool(str(cfg.get("base_url") or "").strip()
                      and str(cfg.get("account") or "").strip())
    return {"configured": configured, "enabled": bool(cfg.get("poll_enabled")),
            "detail": str(cfg.get("base_url") or "")}


def _wxdigest_state():
    from . import wxdigest
    cfg = (wxdigest.view() or {}).get("config") or {}
    configured = bool(str(cfg.get("watch_dir") or "").strip())
    return {"configured": configured, "enabled": bool(cfg.get("enabled")),
            "detail": str(cfg.get("watch_dir") or "")}


def _portscan_state():
    # 按需点扫、无常驻调度：不算 configured，也不给开关
    return {"configured": True, "enabled": False, "detail": ""}


_STATE_READERS = {"zentao": _zentao_state, "wxdigest": _wxdigest_state,
                  "portscan": _portscan_state}


def features():
    """市场「内置功能」区的只读清单：每项 = 卡片元数据 + 实时状态。

    任一功能读取失败不炸整页（卡片降级为「状态未知」，可跳设置页处理）。
    """
    out = []
    for meta in _FEATURES:
        item = dict(meta)
        try:
            item.update(_STATE_READERS[meta["id"]]())
            item["error"] = ""
        except Exception as exc:          # pragma: no cover - 防御：模块内部异常
            item.update({"configured": False, "enabled": False, "detail": ""})
            item["error"] = "状态读取失败：%s" % exc
        out.append(item)
    return out
