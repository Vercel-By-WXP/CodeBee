# -*- coding: utf-8 -*-
"""可分享报告页（借鉴 agency-orchestrator 的 ao report）：把 run 成果渲染成
自包含单文件 HTML——零 JS、内联样式、离线可看，直接发群/发人。

纯函数无网络；数据全部来自调用方（store/paths 已读好的对象）。
"""
from __future__ import annotations

import html as _html
import time


def _esc(s):
    return _html.escape(str(s if s is not None else ""), quote=True)


def _md_ish(text):
    """极简 Markdown 呈现：标题/列表/粗体/代码，够报告阅读即可，不做完整解析。"""
    out = []
    for ln in (text or "").splitlines():
        e = _esc(ln)
        if ln.startswith("# "):
            out.append("<h1>%s</h1>" % e[2:])
        elif ln.startswith("## "):
            out.append("<h2>%s</h2>" % e[3:])
        elif ln.startswith("### "):
            out.append("<h3>%s</h3>" % e[4:])
        elif ln.startswith("- "):
            out.append("<li>%s</li>" % e[2:])
        else:
            out.append("<p>%s</p>" % e)
    return "\n".join(out)


_SHARE_TPL = """<!DOCTYPE html>
<html lang="zh"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>__TITLE__ · CodeBee 任务报告</title>
<style>
body{margin:0;background:#f5f6f8;color:#1c2330;font:15px/1.65 -apple-system,"PingFang SC","Microsoft YaHei",sans-serif}
.wrap{max-width:760px;margin:0 auto;padding:28px 18px 60px}
.head{border-bottom:3px solid #2b6cb0;padding-bottom:14px;margin-bottom:22px}
h1{margin:0 0 6px;font-size:24px}
.meta{color:#5a6472;font-size:13px}
.badge{display:inline-block;border-radius:999px;padding:2px 12px;font-size:13px;font-weight:600}
.badge.ok{background:#e6f6ec;color:#177245}.badge.bad{background:#fdeaea;color:#b42318}
.badge.off{background:#eef0f3;color:#5a6472}
.card{background:#fff;border:1px solid #e3e6ea;border-radius:12px;padding:18px 20px;margin-bottom:18px}
.card h2{font-size:16px;margin:0 0 12px;color:#2b6cb0}
table{border-collapse:collapse;width:100%}
td{padding:6px 10px;border-bottom:1px solid #eef0f3}
tr.sum td{font-weight:700;border-top:2px solid #e3e6ea}
.step{display:flex;gap:10px;padding:7px 0;border-bottom:1px dashed #eef0f3;font-size:13.5px;align-items:baseline}
.step .n{color:#8a93a0;font-weight:700}.step .role{font-weight:600;min-width:88px}
.step .who{color:#5a6472;min-width:90px}.step .sum{flex:1}.st{font-size:12px}
.st.done{color:#177245}.st.failed{color:#b42318}
h1,h2,h3{margin:14px 0 8px}li{margin:4px 0}.trow{padding:2px 0;border-bottom:1px dotted #eee}
p{margin:8px 0}.mut{color:#8a93a0}code{background:#f0f2f5;padding:1px 6px;border-radius:4px}
pre{background:#f0f2f5;padding:12px;border-radius:8px;overflow-x:auto;white-space:pre-wrap}
</style></head><body><div class="wrap">
<div class="head"><h1>__TITLE__</h1>
<div class="meta">__META__ · __ST__ · CodeBee 生成于 __NOW__</div></div>
<div class="card"><h2>结果</h2><span class="badge __STCLS__">__ST__</span>__OVERALL__</div>
<div class="card"><h2>步骤</h2>__STEPS__</div>
<div class="card"><h2>报告</h2>__REPORT__</div>
<div class="meta">由 <b>CodeBee</b> 多智能体编排台生成</div>
</div></body></html>"""


def render_share_html(run, task, report_text):
    """渲染自包含分享页。run/task/report_text 缺失时优雅降级。"""
    run = run or {}
    task = task or {}
    status = str(run.get("status") or "")
    st_label = {"done": "已完成", "failed": "失败", "cancelled": "已取消",
                "running": "进行中"}.get(status, status)
    st_cls = {"done": "ok", "failed": "bad", "cancelled": "off"}.get(status, "off")
    verdict = run.get("verdict") or {}
    means = verdict.get("scores") or {}

    score_rows = ""
    for d, v in means.items():
        score_rows += "<tr><td>%s</td><td>%.1f</td></tr>" % (_esc(d), float(v))
    if verdict:
        score_rows += ('<tr class="sum"><td>综合</td>'
                       '<td>%.1f</td></tr>' % float(verdict.get("overall") or 0))

    steps_rows = ""
    for s in run.get("steps") or []:
        steps_rows += (
            '<div class="step"><span class="n">%02d</span>'
            '<span class="role">%s</span><span class="who">%s</span>'
            '<span class="sum">%s</span><span class="st %s">%s</span></div>'
            % (s.get("n") or 0, _esc(s.get("role") or ""),
               _esc(s.get("agent_label") or s.get("agent") or ""),
               _esc((s.get("summary") or "")[:220]), _esc(s.get("status") or ""),
               _esc(s.get("status") or "")))

    report_html = _md_ish(report_text) if report_text else "<p class='mut'>（暂无报告）</p>"
    overall_note = ("综合 %.1f 分" % float(verdict.get("overall"))
                    if verdict.get("overall") is not None else "")

    return (_SHARE_TPL
            .replace("__TITLE__", _esc(task.get("title") or run.get("title") or "任务报告"))
            .replace("__META__", _esc(task.get("goal") or ""))
            .replace("__ST__", _esc(st_label))
            .replace("__STCLS__", st_cls)
            .replace("__NOW__", _esc(time.strftime("%Y-%m-%d %H:%M")))
            .replace("__OVERALL__", _esc(overall_note))
            .replace("__STEPS__", steps_rows or "<p class='mut'>（无步骤）</p>")
            .replace("__REPORT__", report_html))
