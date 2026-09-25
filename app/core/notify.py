# -*- coding: utf-8 -*-
"""运行结果推送（借鉴 agency-orchestrator 的 --notify）：任务跑完把结果摘要
推到钉钉/飞书/企业微信群机器人，配合定时自动化就是「AI 团队每天定点交活」。

两类通道：
  - 群机器人 webhook 一个地址全包——按域名自动适配三种机器人格式；也接受
    任意 https 地址（按钉钉 text 形状发，自建 n8n 等自选）。
  - 个人推送通道（Bark / ntfy / Server酱 / Telegram Bot）：推到私人手机，
    无人值守场景「半夜失败/待裁决」的刚需；群机器人是给群里看的，这是给你看的。
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


def _send(url, json_body=None, form=None):
    """curl 子进程发送（Python 不经手响应体）；网络失败只记日志。返回 bool。

    json_body=JSON 体；form=表单体（值需 UTF-8 百分号编码，urlencode 负责）。
    中文一律走请求体（Server酱表单 / ntfy JSON publish），不走 HTTP 头——
    头里的非 ASCII 会被网关拒收（历史坑）。
    """
    from . import runner
    import json as _json
    argv = ["curl", "-sS", "--max-time", "20"]
    if json_body is not None:
        argv += ["-H", "Content-Type: application/json",
                 "-d", _json.dumps(json_body, ensure_ascii=False)]
    elif form is not None:
        from urllib.parse import urlencode
        argv += ["-H", "Content-Type: application/x-www-form-urlencoded",
                 "-d", urlencode(form)]
    argv.append(url)
    r = runner.run_process(argv=argv, timeout=30)
    return bool(r["ok"])


def _post(hook, text):
    return _send(hook, json_body=_payload_for(hook, text))


def _personal_channels():
    """读设置里的个人推送配置，返回 [(通道名, 发送函数(title, body)), ...]。

    只收录配置完整的通道；键名与 settings.DEFAULTS 对齐。
    """
    from . import settings
    s = settings.load()
    chs = []

    bark_key = str(s.get("notify_bark_key") or "").strip()
    if bark_key:
        server = (str(s.get("notify_bark_server") or "").strip().rstrip("/")
                  or "https://api.day.app")
        chs.append(("bark", lambda t, b: _send(
            server + "/push",
            json_body={"device_key": bark_key, "title": t, "body": b})))

    ntfy = str(s.get("notify_ntfy_topic") or "").strip()
    if ntfy:
        from urllib.parse import urlparse
        u = urlparse(ntfy if "://" in ntfy else "https://ntfy.sh/" + ntfy.lstrip("/"))
        topic = u.path.strip("/")
        if topic:
            server = "%s://%s" % (u.scheme, u.netloc)
            chs.append(("ntfy", lambda t, b: _send(
                server + "/",
                json_body={"topic": topic, "title": t, "message": b})))

    sc_key = str(s.get("notify_serverchan_key") or "").strip()
    if sc_key:
        chs.append(("serverchan", lambda t, b: _send(
            "https://sctapi.ftqq.com/%s.send" % sc_key,
            form={"title": t, "desp": b})))

    tg_token = str(s.get("notify_telegram_token") or "").strip()
    tg_chat = str(s.get("notify_telegram_chat_id") or "").strip()
    if tg_token and tg_chat:
        chs.append(("telegram", lambda t, b: _send(
            "https://api.telegram.org/bot%s/sendMessage" % tg_token,
            json_body={"chat_id": tg_chat, "text": (t + "\n" + b).strip()})))
    return chs


def _split_title(text):
    """首行作标题（≤40 字），其余作正文；单行文本标题正文同文。"""
    lines = [ln for ln in str(text).splitlines() if ln.strip()]
    title = (lines[0].strip() if lines else "CodeBee")[:40]
    body = "\n".join(ln.strip() for ln in lines[1:]).strip() or title
    return title, body


def push_text_ex(text):
    """推一条文本到全部已配置通道，返回 {通道名: 是否成功}。

    未配置任何通道返回 {}；单通道失败不影响其他通道。
    """
    results = {}
    hook = _webhook()
    if hook:
        if not hook.startswith("https://"):
            log.warning("notify webhook 必须是 https")
            results["webhook"] = False
        else:
            try:
                results["webhook"] = _post(hook, text)
            except Exception as e:
                log.warning("notify push failed: %s", e)
                results["webhook"] = False
    try:
        title, body = _split_title(text)
    except Exception:
        title, body = "CodeBee", str(text)
    for name, fn in _personal_channels():
        try:
            results[name] = bool(fn(title, body))
        except Exception as e:
            log.warning("notify %s push failed: %s", name, e)
            results[name] = False
    return results


def push_text(text):
    """推一条文本；任一通道成功即 True；全未配置/全失败返回 False，不抛错。"""
    try:
        return any(push_text_ex(text).values())
    except Exception as e:
        log.warning("notify push failed: %s", e)
        return False


def push_run_async(run_id):
    """任务收尾后异步推送结果摘要（jobs 层调用；绝不阻塞/影响任务）。"""
    threading.Thread(target=push_run, daemon=True,
                     name="notify-%s" % run_id, args=(run_id,)).start()


def push_run(run_id):
    """组装 run 结果摘要并推送（群 webhook 或任一个人通道配置了才推）。

    摘要末尾附分享页路径（notify_base_url 设置非空时拼完整链接，否则只给
    run_id 供在本机 CodeBee 界面查找）。"""
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
