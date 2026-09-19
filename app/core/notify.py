# -*- coding: utf-8 -*-
"""运行结果群推送（借鉴 agency-orchestrator 的 --notify）：任务跑完把结果摘要
推到钉钉/飞书/企业微信群机器人，配合定时自动化就是「AI 团队每天定点交活」。

webhook 一个地址全包——按域名自动适配三种机器人格式（同 agency-orchestrator
思路）；也接受任意 https 地址（按钉钉 text 形状发，自建 n8n 等自选）。
推送在后台线程、失败只记日志——通知永远不影响任务本身。
"""
from __future__ import annotations

import logging

log = logging.getLogger(__name__)


def _payload_for(webhook, text):
    """按 webhook 域名适配群机器人消息体。返回 None = 无法识别的地址。"""
    from urllib.parse import urlparse
    host = (urlparse(webhook).hostname or "").lower()
    if "dingtalk" in host:
        return {"msgtype": "text", "text": {"content": text}}
    if "feishu" in host or "larksuite" in host:
        return {"msg_type": "text", "content": {"text": text}}
    if "weixin" in host or "wechat" in host or "work.weixin" in host:
        return {"msgtype": "text", "text": {"content": text}}
    if host:
        return {"msgtype": "text", "text": {"content": text}}   # 未知域名按钉钉形状
    return None


def _webhook():
    try:
        from . import settings
        return str(settings.load().get("notify_webhook") or "").strip()
    except Exception:
        return ""


def _post(hook, text):
    """curl 子进程 POST（Python 不经手响应体）；网络失败只记日志。"""
    from . import runner
    import json as _json
    body = _json.dumps(_payload_for(hook, text), ensure_ascii=False)
    r = runner.run_process(
        argv=["curl", "-sS", "--max-time", "20",
              "-H", "Content-Type: application/json",
              "-d", body, hook],
        timeout=30)
    return bool(r["ok"])


def push_text(text):
    """推一条文本到群。配置了 webhook 才推；失败返回 False 不抛错。"""
    hook = _webhook()
    if not hook:
        return False
    if not hook.startswith("https://"):
        log.warning("notify webhook 必须是 https")
        return False
    try:
        return _post(hook, text)
    except Exception as e:
        log.warning("notify push failed: %s", e)
        return False


def push_run_async(run_id):
    """任务收尾后异步推送结果摘要（jobs 层调用；绝不阻塞/影响任务）。"""
    threading.Thread(target=push_run, daemon=True,
                     name="notify-%s" % run_id, args=(run_id,)).start()


def push_run(run_id):
    """组装 run 结果摘要并推送（webhook 未配置时静默跳过）。

    摘要末尾附分享页路径（notify_base_url 设置非空时拼完整链接，否则只给
    run_id 供在本机 CodeBee 界面查找）。"""
    hook = _webhook()
    if not hook:
        return False
    from . import store
    run = store.get_run(run_id) or {}
    if not run:
        return False
    status = str(run.get("status") or "")
    mark = {"done": "✅", "failed": "❌", "cancelled": "⚪"}.get(status, "🔔")
    lines = ["%s CodeBee 任务%s" % (mark, {"done": "完成", "failed": "失败",
                                           "cancelled": "已取消"}.get(status, status))]
    lines.append("任务：%s" % (run.get("title") or run_id))
    if run.get("error"):
        lines.append("错误：%s" % str(run["error"])[:200])
    v = run.get("verdict") or {}
    if v.get("overall") is not None:
        lines.append("综合评分 %.1f（%s）" % (
            float(v["overall"]), "达标" if v.get("publishable") else "未达标"))
    try:
        from .settings import load as _sload
        base = str(_sload().get("notify_base_url") or "").strip().rstrip("/")
        if base:
            lines.append("📄 详情：%s/api/runs/%s/share" % (base, run_id))
    except Exception:
        pass
    return push_text("\n".join(lines))


import threading  # noqa: E402  （push_run_async 依赖；置底避免顶部循环导入）
