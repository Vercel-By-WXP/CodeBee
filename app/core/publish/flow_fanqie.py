# -*- coding: utf-8 -*-
"""番茄发布确认链的交互实现。

从 publish.flow 拆出（单文件行数治理），由 run_flow 调用；
命中 continue 语义时返回 "continue"。
"""
from __future__ import annotations

import datetime
import re
import time

from .flow_helpers import (
    FlowError, _fanqie_active_dialog, _fanqie_schedule_conflict,
    _fanqie_submission_limit_changed, _has_new_feedback,
)


def run_fanqie_publish(page, st, i, note, values):
    # Fanqie inserts typo, content-check, and publish-settings
    # dialogs dynamically. Advance whichever dialog is visible.
    # When the daily quota rejects a chapter, try later dates one
    # by one instead of retrying the same blocked submission.
    note(i, "处理番茄发布确认链")

    def fq_state():
        state = page.call(
            "()=>{const vis=e=>{const r=e.getBoundingClientRect(),s=getComputedStyle(e);"
            "return r.width>0&&r.height>0&&s.display!=='none'&&s.visibility!=='hidden';};"
            "const ds=[...document.querySelectorAll('[role=dialog],.arco-modal')].filter(vis);"
            "const fs=[...document.querySelectorAll('[role=alert],.arco-message,"
            ".arco-notification,.arco-modal-notice')].filter(vis)"
            ".map(e=>(e.innerText||'').trim()).filter(Boolean);"
            "return{dialogs:ds.map(d=>(d.innerText||'').trim()),feedback:fs,"
            "body:(document.body.innerText||''),url:location.href};}", timeout=15) or {}
        state["dialog"] = _fanqie_active_dialog(state.get("dialogs"))
        return state

    def fq_click(dialog_mark, label):
        return page.call(
            "(mark,label)=>{const vis=e=>{const r=e.getBoundingClientRect();"
            "return r.width>0&&r.height>0};"
            "const ds=[...document.querySelectorAll('[role=dialog],.arco-modal')].filter(vis);"
            "const d=[...ds].reverse().find(e=>(e.innerText||'').includes(mark));"
            "if(!d)return{ok:false,err:'dialog'};"
            "const norm=x=>String(x||'').replace(/\\s+/g,'').trim();"
            "const es=[...d.querySelectorAll('button,[role=button],label')].filter(vis);"
            "let e=es.find(x=>norm(x.innerText)===norm(label));"
            "if(!e)e=es.find(x=>norm(x.innerText).includes(norm(label)));"
            "if(!e&&norm(label)==='确认发布')e=d.querySelector('button.arco-btn-primary');"
            "if(!e)return{ok:false,err:'action'};e.click();return{ok:true};}",
            dialog_mark, label, timeout=15) or {}

    def fq_schedule_enabled():
        return bool(page.call(
            "()=>{const vis=e=>{const r=e.getBoundingClientRect(),s=getComputedStyle(e);"
            "return r.width>0&&r.height>0&&s.display!=='none'&&s.visibility!=='hidden';};"
            # Do not include every node whose class contains "modal":
            # Fanqie's title itself is .arco-modal-title, which can
            # become the last text match and hide its switch children.
            "const ds=[...document.querySelectorAll('[role=dialog],.arco-modal')].filter(vis);"
            "let d=[...ds].reverse().find(e=>(e.innerText||'').includes('发布设置'));"
            "if(!d)d=document.body;const ss=[...d.querySelectorAll('button[role=switch],"
            "input[type=checkbox],.arco-switch')].filter(vis);"
            "const labeled=ss.filter(s=>(s.closest('label')?.innerText||s.parentElement?.innerText||'').includes('定时发布'));"
            "const s=(labeled.length?labeled:ss).at(-1);if(!s)return false;"
            "const c=String(s.className||'');return s.getAttribute('aria-checked')==='true'||"
            "s.checked===true||s.getAttribute('data-checked')==='true'||"
            "s.getAttribute('aria-pressed')==='true'||/(^|\\s)(is-checked|is-active|checked|active|arco-switch-checked|switch-checked)(\\s|$)/i.test(c)||c.includes('arco-switch-checked');}", timeout=10))

    def fq_schedule_switch_point():
        """Return the visible switch center in the current dialog.

        Arco's switch is controlled by React state.  On the live
        Fanqie editor a DOM ``click()`` reaches the node but does
        not update that state; the platform only accepts the real
        pointer sequence.  Keep the lookup scoped to the visible
        publish dialog so hidden editor copies cannot win.
        """
        return page.call(
            "()=>{const vis=e=>{const r=e.getBoundingClientRect(),s=getComputedStyle(e);"
            "return r.width>0&&r.height>0&&s.display!=='none'&&s.visibility!=='hidden'"
            "&&s.pointerEvents!=='none'&&parseFloat(s.opacity||'1')>0;};"
            # Scope to actual dialog containers; matching generic
            # modal/dialog classes also catches the title node.
            "const ds=[...document.querySelectorAll('[role=dialog],.arco-modal')].filter(vis);"
            "let d=[...ds].reverse().find(e=>(e.innerText||'').includes('发布设置'));"
            "if(!d)d=[...ds].reverse().find(e=>(e.innerText||'').includes('定时发布'));"
            # The live page has briefly rendered the modal without
            # its text node while React was committing the second
            # publish-settings render. In that window the dialog
            # is visible and the switch is usable, but text-based
            # scoping returns no container. Fall back to the
            # visible Arco switch itself instead of declaring the
            # timed control missing.
            "if(!d)d=document.body;const ss=[...d.querySelectorAll('button[role=switch],"
            "input[type=checkbox],.arco-switch')].filter(vis);"
            "const labeled=ss.filter(s=>(s.closest('label')?.innerText||s.parentElement?.innerText||'').includes('定时发布'));"
            "let s=(labeled.length?labeled:ss).at(-1);"
            "if(!s){const ts=[...d.querySelectorAll('*')].filter(e=>vis(e)&&"
            "(e.innerText||'').trim()==='定时发布');ts.sort((a,b)=>a.getBoundingClientRect().width-b.getBoundingClientRect().width);"
            "let e=ts[0];for(let n=0;e&&n<5;n++,e=e.parentElement){"
            "const r=e.getBoundingClientRect();const c=[...e.querySelectorAll('*')].filter(x=>vis(x)&&"
            "x.getBoundingClientRect().left>=r.left+r.width*.45);if(c.length){"
            "c.sort((a,b)=>(a.getBoundingClientRect().width*a.getBoundingClientRect().height)-"
            "(b.getBoundingClientRect().width*b.getBoundingClientRect().height));s=c[0];break;}}}"
            "if(!s)return null;const r=s.getBoundingClientRect();"
            "const c=String(s.className||'');return{x:Math.round(r.x+r.width/2),y:Math.round(r.y+r.height/2),"
            "checked:s.getAttribute('aria-checked')==='true'||s.checked===true||"
            "s.getAttribute('data-checked')==='true'||s.getAttribute('aria-pressed')==='true'||"
            "/(^|\\s)(is-checked|is-active|checked|active|arco-switch-checked|switch-checked)(\\s|$)/i.test(c)||c.includes('arco-switch-checked')};}", timeout=10)

    def fq_enable_schedule():
        if fq_schedule_enabled():
            return
        # 点「否」后 React 重渲染会短暂卸载开关（2026-10-17 实案：
        # 失败后现场开关就在弹窗里）——带退避反复找，别一击定生死。
        point = {}
        find_deadline = time.time() + 30.0
        while time.time() < find_deadline:
            point = fq_schedule_switch_point() or {}
            if point.get("x"):
                break
            time.sleep(0.6)
        if not point.get("x"):
            # Last-resort live control lookup. This is deliberately
            # still scoped to a visible switch and never clicks
            # the child .arco-switch-dot; the previous failure was
            # a transient modal text/render race, not absence of
            # the platform control.
            point = page.call(
                "()=>{const vis=e=>{const r=e.getBoundingClientRect(),s=getComputedStyle(e);"
                "return r.width>0&&r.height>0&&s.display!=='none'&&s.visibility!=='hidden'"
                "&&s.pointerEvents!=='none'&&parseFloat(s.opacity||'1')>0;};"
                "const ss=[...document.querySelectorAll('button[role=switch],.arco-switch')]"
                ".filter(vis);const s=ss.at(-1);if(!s)return null;const r=s.getBoundingClientRect();"
                "return{x:Math.round(r.x+r.width/2),y:Math.round(r.y+r.height/2),"
                "checked:s.getAttribute('aria-checked')==='true'||s.classList.contains('arco-switch-checked')};}",
                timeout=10) or {}
        if not point.get("x"):
            raise FlowError("番茄发布设置里找不到定时发布开关")
        # Newer Arco builds accept the semantic button click;
        # older builds need the trusted CDP pointer path below.
        dom_clicked = page.call(
            "()=>{const ds=[...document.querySelectorAll('[role=dialog],.arco-modal')];"
            "const d=[...ds].reverse().find(e=>(e.innerText||'').includes('发布设置'))"
            "||[...ds].reverse().find(e=>(e.innerText||'').includes('定时发布'));"
            "const s=d&&d.querySelector('button[role=switch]');"
            "if(!s)return{ok:false};s.click();return{ok:true};}", timeout=10) or {}
        if dom_clicked.get("ok"):
            deadline = time.time() + 1.2
            while time.time() < deadline:
                if fq_schedule_enabled():
                    return
                time.sleep(0.12)
        # React occasionally drops the first CDP pointer sequence
        # while the publish dialog is finishing its transition.
        # Retry the real button center, never the switch-dot child,
        # and read the controlled state after each attempt.
        for _attempt in range(4):
            page.send("Input.dispatchMouseEvent", {
                "type": "mousePressed", "x": point["x"], "y": point["y"],
                "button": "left", "clickCount": 1}, timeout=8.0)
            page.send("Input.dispatchMouseEvent", {
                "type": "mouseReleased", "x": point["x"], "y": point["y"],
                "button": "left", "clickCount": 1}, timeout=8.0)
            deadline = time.time() + 1.0
            while time.time() < deadline:
                if fq_schedule_enabled():
                    return
                time.sleep(0.12)
            point = fq_schedule_switch_point() or point
        raise FlowError("番茄定时发布开关点击后仍未开启（页面未接受真实点击）")

    def fq_pick_date(day):
        iso = day.strftime("%Y-%m-%d")
        opened = page.call(
            "()=>{const ds=[...document.querySelectorAll('[role=dialog],.arco-modal')];"
            "const d=[...ds].reverse().find(e=>(e.innerText||'').includes('发布设置'));"
            "const x=d&&d.querySelector('input[placeholder=\"请选择日期\"]');"
            "if(!x)return{ok:false};x.click();return{ok:true};}", timeout=10) or {}
        if not opened.get("ok"):
            raise FlowError("番茄发布设置里找不到日期输入框")
        time.sleep(0.2)
        for _month_try in range(36):
            head = str(page.call(
                "()=>{const e=document.querySelector('.arco-picker-header-value');"
                "return e?(e.innerText||'').trim():'';}", timeout=10) or "")
            m = re.search(r"(\d{4})年(\d{1,2})月", head)
            if not m:
                raise FlowError("番茄日期面板未打开")
            diff = (day.year - int(m.group(1))) * 12 + day.month - int(m.group(2))
            if diff == 0:
                break
            moved = page.call(
                "(forward)=>{const es=[...document.querySelectorAll('.arco-picker-header-icon')]"
                ".filter(e=>e.getBoundingClientRect().width>0);"
                "const e=es[forward?2:1];if(!e)return{ok:false};"
                "e.click();return{ok:true};}", diff > 0, timeout=10) or {}
            if not moved.get("ok"):
                raise FlowError("番茄日期面板无法切换月份")
            time.sleep(0.12)
        picked = page.call(
            "(day)=>{const es=[...document.querySelectorAll('.arco-picker-cell-in-view')]"
            ".filter(e=>e.getBoundingClientRect().width>0);"
            "const e=es.find(x=>(x.innerText||'').trim()===String(day));"
            "if(!e)return{ok:false};e.click();return{ok:true};}", day.day, timeout=10) or {}
        if not picked.get("ok"):
            raise FlowError("番茄日期面板找不到 %s" % iso)
        time.sleep(0.2)
        got = str(page.call(
            "()=>{const ds=[...document.querySelectorAll('[role=dialog],.arco-modal')];"
            "const d=[...ds].reverse().find(e=>(e.innerText||'').includes('发布设置'));"
            "const x=d&&d.querySelector('input[placeholder=\"请选择日期\"]');"
            "return x?x.value:'';}", timeout=10) or "")
        if got != iso:
            raise FlowError("番茄定时日期选择失败：期望 %s，实际 %s" % (iso, got))

    def fq_pick_time(slot):
        hhmm = slot.strftime("%H:%M")
        opened = page.call(
            "()=>{const ds=[...document.querySelectorAll('[role=dialog],.arco-modal')];"
            "const d=[...ds].reverse().find(e=>(e.innerText||'').includes('发布设置'));"
            "const x=d&&d.querySelector('input[placeholder=\"请选择时间\"]');"
            "if(!x)return{ok:false};x.click();return{ok:true};}", timeout=10) or {}
        if not opened.get("ok"):
            raise FlowError("番茄发布设置里找不到时间输入框")
        time.sleep(0.2)
        picked = page.call(
            "(hour,minute)=>{const ps=[...document.querySelectorAll('.arco-timepicker')]"
            ".filter(e=>e.getBoundingClientRect().width>0);const p=ps.at(-1);"
            "const ls=p&&p.querySelectorAll('.arco-timepicker-list');"
            "if(!ls||ls.length<2)return{ok:false,err:'panel'};"
            "const pick=(list,value)=>{const es=[...list.querySelectorAll('.arco-timepicker-cell')];"
            "const e=es.find(x=>(x.innerText||'').trim()===value);if(!e)return false;"
            "e.click();return true;};return{ok:pick(ls[0],hour)&&pick(ls[1],minute)};}",
            slot.strftime("%H"), slot.strftime("%M"), timeout=10) or {}
        if not picked.get("ok"):
            raise FlowError("番茄时间面板无法选择 %s" % hhmm)
        confirmed = page.call(
            "()=>{const ps=[...document.querySelectorAll('.arco-timepicker-container')]"
            ".filter(e=>e.getBoundingClientRect().width>0);const p=ps.at(-1);"
            "const b=p&&[...p.querySelectorAll('button')].find(e=>(e.innerText||'').trim()==='确定');"
            "if(!b)return{ok:false};b.click();return{ok:true};}", timeout=10) or {}
        if not confirmed.get("ok"):
            raise FlowError("番茄时间面板没有确定按钮")
        time.sleep(0.2)
        got = str(page.call(
            "()=>{const ds=[...document.querySelectorAll('[role=dialog],.arco-modal')];"
            "const d=[...ds].reverse().find(e=>(e.innerText||'').includes('发布设置'));"
            "const x=d&&d.querySelector('input[placeholder=\"请选择时间\"]');"
            "return x?x.value:'';}", timeout=10) or "")
        if got != hhmm:
            raise FlowError("番茄定时时间选择失败：期望 %s，实际 %s" % (hhmm, got))

    def fq_read_slot():
        """读发布设置里定时日期/时间输入框的当前值（含平台预填）。"""
        got = page.call(
            "()=>{const ds=[...document.querySelectorAll('[role=dialog],.arco-modal')];"
            "const d=[...ds].reverse().find(e=>(e.innerText||'').includes('发布设置'));"
            "if(!d)return{};"
            "const di=d.querySelector('input[placeholder=\"请选择日期\"]');"
            "const ti=d.querySelector('input[placeholder=\"请选择时间\"]');"
            "return{date:di?(di.value||'').trim():'',"
            "time:ti?(ti.value||'').trim():''};}", timeout=10) or {}
        out = {}
        m = re.search(r"(20\d{2})[-/年](\d{1,2})[-/月](\d{1,2})",
                      str(got.get("date") or ""))
        if m:
            out["date"] = "%s-%02d-%02d" % (
                m.group(1), int(m.group(2)), int(m.group(3)))
        mt = re.search(r"(\d{1,2}):(\d{2})", str(got.get("time") or ""))
        if mt:
            out["time"] = "%02d:%s" % (int(mt.group(1)), mt.group(2))
        return out

    raw_date = str(values.get(st.get("date_key") or "publish_date") or "").strip()
    if raw_date:
        try:
            trial_day = datetime.datetime.strptime(raw_date, "%Y-%m-%d").date()
        except ValueError:
            raise FlowError("定时发布日期格式应为 YYYY-MM-DD：%s" % raw_date)
    else:
        trial_day = datetime.date.today() + datetime.timedelta(days=1)
    raw_time = str(values.get(st.get("time_key") or "publish_time") or
                   "00:00").strip()
    try:
        trial_time = datetime.datetime.strptime(raw_time, "%H:%M").time()
    except ValueError:
        raise FlowError("定时发布时间格式应为 HH:MM：%s" % raw_time)
    force_schedule = bool(st.get("force_schedule") or raw_date)
    # force_direct：今天就要上线——平台弹「建议本章定时发布」
    # 或「有定时待发布章节」横幅时一律点否拒绝、直接确认发布
    # （2026-10-18 用户指令：新书首发今天可发，别全排到明天）
    force_direct = bool(values.get("force_direct"))
    max_days = max(1, int(st.get("max_schedule_days") or 120))
    deadline = time.time() + float(st.get("timeout") or 180)
    tried_dates = 0
    while time.time() < deadline:
        state = fq_state()
        dialog_text = str(state.get("dialog") or "")
        body_text = str(state.get("body") or "")
        # A page-level quota rejection can close the settings
        # dialog. Report the actual platform feedback instead of
        # trying to click a confirmation button that disappeared.
        # A submission quota does not prove a later date is usable.
        page_limit = any(mark in body_text for mark in (
            "提交字数超出每日上限", "提交字数超过每日上限",
            "投稿字数超过每日上限",
            "投稿字数超出每日上限"))
        if (page_limit and not dialog_text and
                "/publish/" in str(state.get("url") or "")):
            raise FlowError("番茄提示提交字数超出每日上限；本次未确认提交，请额度恢复后核对章节状态再续发")
        if "发布提示" in dialog_text and "错别字" in dialog_text:
            if not fq_click("发布提示", "提交").get("ok"):
                raise FlowError("番茄错别字提示框没有提交按钮")
            time.sleep(0.8)
            continue
        if "请选择内容检测方式" in dialog_text:
            if not fq_click("请选择内容检测方式", "仅基础检测").get("ok"):
                raise FlowError("番茄内容检测弹窗没有基础检测按钮")
            time.sleep(1.2)
            continue
        if "是否进行内容风险检测" in dialog_text:
            if not fq_click("是否进行内容风险检测", "取消").get("ok"):
                raise FlowError("番茄风险检测弹窗没有取消按钮")
            time.sleep(0.5)
            continue
        if "发布设置" in dialog_text:
            # 先点「否」再开定时（2026-10-18 实案：先开开关会触发
            # 重渲染挪走 AI 单选，导致「无法选择未使用 AI」）。
            # 点否后开关可能被重渲染洗掉——由 fq_enable_schedule
            # 内的 30 秒退避重试负责找回。
            if not fq_click("发布设置", "否").get("ok"):
                raise FlowError("番茄发布设置无法选择“未使用 AI”")
            # 「建议本章定时发布」横幅是弹窗打开后异步渲染的，
            # 首次读文本可能还没有它——点完「否」重读一次再判定，
            # 别拿旧文本定终身（2026-10-16 实案：误判不定时→
            # 直接确认发布→平台不收→同一章反复失败卡设置页）。
            time.sleep(0.5)
            dialog_text = str(fq_state().get("dialog") or "") \
                or dialog_text
            queued_hint = ("建议本章定时发布" in dialog_text or
                           "有定时待发布章节" in dialog_text)
            use_schedule = ((force_schedule or queued_hint)
                            and not force_direct)
            if use_schedule:
                # Tell the caller that the platform accepted the
                # scheduled path even when the flow was entered
                # from the normal upload action via queued_hint.
                # Without this marker the manager incorrectly
                # records the chapter as published immediately.
                values["_scheduled_by_platform"] = True
                if tried_dates >= max_days:
                    raise FlowError("番茄连续 %d 个日期均触发每日字数上限" % max_days)
                if not fq_schedule_enabled():
                    fq_enable_schedule()
                # 平台预填档期优先：定时队列里有前序章时，平台会把
                # 下一个可用档期直接填进日期/时间框——照抄它，别硬
                # 选自己的日期撞队列顺序（被拒即「指定的定时发布
                # 时间不符合要求」）。
                prefill = fq_read_slot()
                # A stale client-side prefill can point back to the
                # first day of an existing queue (for example
                # 10/20 while the local ledger is already at
                # 11/11).  Never move a new chapter backwards;
                # use the cadence computed from the last confirmed
                # slot in that case.
                if prefill.get("date") and prefill.get("time"):
                    try:
                        _prefill_dt = datetime.datetime.strptime(
                            prefill["date"] + " " + prefill["time"],
                            "%Y-%m-%d %H:%M")
                        _requested_dt = datetime.datetime.combine(
                            trial_day, trial_time)
                        if _prefill_dt < _requested_dt:
                            prefill = {}
                    except Exception:
                        pass
                if prefill.get("date") and prefill.get("time"):
                    values["scheduled_date"] = prefill["date"]
                    values["scheduled_time"] = prefill["time"]
                    note(i, "按平台预填档期定时发布 %s %s" %
                         (prefill["date"], prefill["time"]))
                else:
                    fq_pick_date(trial_day)
                    fq_pick_time(trial_time)
                    # Save the actual accepted candidate back to
                    # the caller so the publish ledger can continue
                    # this queue's cadence for the next chapter.
                    values["scheduled_date"] = trial_day.strftime("%Y-%m-%d")
                    values["scheduled_time"] = trial_time.strftime("%H:%M")
                    note(i, "尝试定时发布 %s %s" %
                         (trial_day.strftime("%Y-%m-%d"),
                          trial_time.strftime("%H:%M")))
                tried_dates += 1
            if not use_schedule and fq_schedule_enabled():
                # force_direct：平台预填了定时开关（新书建议定时的
                # 形态）——照既有结论「开关只认真实指针序列」，
                # 真实点击关掉它，确认发布才是真·今天直发。
                pt = fq_schedule_switch_point() or {}
                if pt.get("x"):
                    page.send("Input.dispatchMouseEvent", {
                        "type": "mousePressed", "x": pt["x"], "y": pt["y"],
                        "button": "left", "clickCount": 1}, timeout=8.0)
                    page.send("Input.dispatchMouseEvent", {
                        "type": "mouseReleased", "x": pt["x"], "y": pt["y"],
                        "button": "left", "clickCount": 1}, timeout=8.0)
                    time.sleep(0.5)
                    note(i, "已关闭平台预填的定时开关（今天直发）")
            before_submit = fq_state()
            if "发布设置" not in str(before_submit.get("dialog") or ""):
                continue
            confirmed = fq_click("发布设置", "确认发布")
            if not confirmed.get("ok"):
                # The button is present in the live Arco dialog,
                # but a React re-render can briefly make text
                # matching stale.  Re-query the active dialog and
                # click its primary action directly before failing.
                confirmed = page.call(
                    "()=>{const vis=e=>{const r=e.getBoundingClientRect(),s=getComputedStyle(e);"
                    "return r.width>0&&r.height>0&&s.display!=='none'&&s.visibility!=='hidden';};"
                    "const ds=[...document.querySelectorAll('[role=dialog],.arco-modal')].filter(vis);"
                    "const d=[...ds].reverse().find(e=>(e.innerText||'').includes('发布设置'));"
                    "const e=d&&d.querySelector('button.arco-btn-primary');"
                    "if(!e)return{ok:false,err:'primary'};e.click();return{ok:true};}",
                    timeout=10) or {}
            if not confirmed.get("ok"):
                raise FlowError("番茄发布设置没有确认发布按钮")
            feedback_marks = ["每日上限", "半小时以后", "发布时间间隔",
                              "同一时间", "请选择是否使用 AI"]
            after = fq_state()
            feedback_deadline = time.time() + 2.2
            while (time.time() < feedback_deadline and
                   "发布设置" in str(after.get("dialog") or "") and
                   not any(_has_new_feedback(before_submit, after, [mark])
                           for mark in feedback_marks)):
                time.sleep(0.15)
                after = fq_state()
            after_dialog = str(after.get("dialog") or "")
            if _fanqie_submission_limit_changed(before_submit, after):
                # 每日字数上限弹窗：直发超额，或定时目标日额度被
                # 账户级队列占满（2026-10-17 凌晨实案：头七定时章
                # 占着额度，神瞳直发/定时双双被同一弹窗截断）。
                # 只要还剩可试日期就推进一天改走定时重试，不再
                # 整批停摆——额度总会在某个明日放出来。
                fq_click("发布设置", "取消")
                if tried_dates + 1 >= max_days:
                    raise FlowError(
                        "番茄连续 %d 个日期均提示投稿字数超出每日上限，"
                        "请额度恢复后再发" % max_days)
                force_schedule = True
                trial_day += datetime.timedelta(days=1)
                trial_time = datetime.time(0, 0)
                tried_dates += 1
                note(i, "每日上限弹窗：顺延定时至 %s 00:00 重试"
                     % trial_day.strftime("%Y-%m-%d"))
                if "发布设置" not in after_dialog:
                    page.call(
                        "()=>{const e=[...document.querySelectorAll('button')]"
                        ".find(x=>x.getBoundingClientRect().width>0"
                        "&&(x.innerText||'').trim()==='下一步');"
                        "if(!e)return{ok:false};e.click();return{ok:true};}",
                        timeout=10)
                    time.sleep(0.8)
                continue
            if _has_new_feedback(before_submit, after, ["每日上限"]):
                force_schedule = True
                trial_day += datetime.timedelta(days=1)
                trial_time = datetime.time(0, 0)
                if "发布设置" not in after_dialog:
                    page.call(
                        "()=>{const e=[...document.querySelectorAll('button')]"
                        ".find(x=>x.getBoundingClientRect().width>0"
                        "&&(x.innerText||'').trim()==='下一步');"
                        "if(!e)return{ok:false};e.click();return{ok:true};}",
                        timeout=10)
                    time.sleep(0.8)
                continue
            if _fanqie_schedule_conflict(before_submit, after):
                if "发布设置" in after_dialog:
                    fq_click("发布设置", "取消")
                raise FlowError(
                    "番茄拒绝了指定的定时发布时间；为保持章节队列顺序，"
                    "本次没有自动改到其他日期，请核对前序章节排期后重试")
            if _has_new_feedback(before_submit, after,
                                 ["请选择是否使用 AI"]):
                continue
            if "发布设置" not in after_dialog:
                note(i, "番茄提交已被平台接受")
                break
            if (not use_schedule and
                    ("建议本章定时发布" in after_dialog or
                     "有定时待发布章节" in after_dialog)):
                # 首轮判定时横幅还没渲染、误走了立即发布；平台
                # 没收。带着横幅重进下一轮，走定时分支，别把
                # 可恢复的判定竞态当成硬失败。
                note(i, "平台提示需定时发布，改走定时分支重试")
                continue
            raise FlowError("番茄确认发布后仍停留在设置页：%s" % after_dialog[:160])
        if ("chapter-manage" in str(state.get("url") or "") or
                "已提交" in body_text):
            note(i, "番茄提交页已跳转")
            break
        time.sleep(0.4)
    else:
        raise FlowError("等待番茄发布确认链超时")
