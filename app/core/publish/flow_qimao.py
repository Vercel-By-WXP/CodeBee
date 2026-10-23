# -*- coding: utf-8 -*-
"""七猫发布确认链的交互实现。

从 publish.flow 拆出（单文件行数治理），由 run_flow 调用；
与小说发章共用同一编辑器组件（q-contenteditable + el-dialog），
qimao.PUBLISH_SHORT_STORY 的 qimao_short_publish 步进这里推进。

实案口径（2026-10-22 短篇实抓 + 2026-10-21 小说发章配方）：
- 「确认发布」合成 el.click() 会被平台吞掉，必须真实鼠标命中
  （elementFromPoint + CDP 指针序列）；
- 首次发布可能在确认框前追加「重要提醒」合规框（我已阅读并知晓）；
- 确认框内可能带「内容声明」下拉（本人原创/含AI生成内容）。声明在
  发布后不可修改，所以选不上就中止，绝不带着未声明状态点确认；
- 下拉在两次 evaluate 之间会收起——开下拉、点选项必须在同一次
  JS 调用里完成。
"""
from __future__ import annotations

import time

from .flow_helpers import FlowError

_DIALOG_SEL = ".el-dialog,.el-message-box,[role=dialog]"


def _qimao_visible_dialogs(page):
    """返回可见、含可点按钮的弹层文本（去重，短在前）。"""
    texts = page.call(
        "(sel)=>{const vis=e=>{const r=e.getBoundingClientRect(),"
        "s=getComputedStyle(e);return r.width>0&&r.height>0"
        "&&s.display!=='none'&&s.visibility!=='hidden';};"
        "const ds=[...document.querySelectorAll(sel)].filter(vis)"
        ".filter(e=>e.querySelector('button,a,[role=button]'));"
        "const out=[];for(const d of ds){"
        "const t=(d.innerText||'').trim();if(t&&!out.includes(t))out.push(t);}"
        "out.sort((a,b)=>a.length-b.length);return out.slice(0,8);}",
        _DIALOG_SEL, timeout=15) or []
    return [str(t) for t in texts]


def _qimao_click_dialog_label(page, label, exact=True):
    """真实鼠标点弹层里的按钮；真实路径被挡时退 DOM click（尽力）。"""
    r = page.real_click_text(
        label, scope=".el-dialog a,.el-dialog button,.el-message-box button,"
                     "a,button,[role=button]", contains=not exact)
    if (r or {}).get("ok"):
        return True
    try:
        r2 = page.call(
            "(sel,t,ex)=>{const vis=e=>{const r=e.getBoundingClientRect(),"
            "s=getComputedStyle(e);return r.width>0&&r.height>0"
            "&&s.display!=='none'&&s.visibility!=='hidden';};"
            "const norm=x=>String(x||'').replace(/\\s+/g,'').trim();"
            "const ds=[...document.querySelectorAll(sel)].filter(vis);"
            "ds.sort((a,b)=>(a.innerText||'').length-(b.innerText||'').length);"
            "for(const d of ds){"
            "const b=[...d.querySelectorAll('button,a,[role=button]')]"
            ".find(e=>vis(e)&&(ex?norm(e.innerText)===norm(t)"
            ":norm(e.innerText).includes(norm(t))));"
            "if(b){b.click();return{ok:true};}}return{ok:false};}",
            _DIALOG_SEL, label, exact, timeout=10) or {}
        return bool(r2.get("ok"))
    except Exception:
        return False


def _qimao_set_declaration(page, want):
    """确认框内的「内容声明」下拉设成 want；选不到该选项返回 False。

    已发布作品的声明改不了——认错声明发布比发布失败更糟，这里宁可不发。
    """
    # 选项只做精确匹配：「不含AI生成内容」也含 "AI"，模糊匹配会选错；
    # 声明发布后不可改，宁可不发。
    r = page.call(
        "(root,want)=>{const vis=e=>{const r=e.getBoundingClientRect(),"
        "s=getComputedStyle(e);return r.width>0&&r.height>0"
        "&&s.display!=='none'&&s.visibility!=='hidden';};"
        "const norm=x=>String(x||'').replace(/\\s+/g,'').trim();"
        "const ds=[...document.querySelectorAll(root)].filter(vis);"
        "const d=ds[ds.length-1];if(!d)return{ok:false,err:'no-dialog'};"
        "const decl=e=>{const v=e.querySelector('.el-input__inner')||e;"
        "const t=norm(v.innerText||v.value||'');"
        "return t==='本人原创'||t==='含AI生成内容'||t.includes('声明');};"
        "const selEl=[...d.querySelectorAll('.el-select')].find(decl);"
        "if(!selEl)return{ok:false,err:'no-select'};"
        "const box=selEl.querySelector('.el-input__inner')||selEl;"
        "const cur=norm(box.innerText||box.value||'');"
        "if(cur===want)return{ok:true,already:true};"
        "box.click();"
        "let it=null;const t0=Date.now();"
        "while(Date.now()-t0<1500){"
        "const items=[...document.querySelectorAll('.el-select-dropdown__item')]"
        ".filter(vis);"
        "it=items.find(e=>norm(e.innerText)===norm(want));"
        "if(it)break;}"
        "if(!it)return{ok:false,err:'no-option',cur:cur};"
        "it.click();return{ok:true,picked:norm(it.innerText)};}",
        _DIALOG_SEL, want, timeout=12) or {}
    if r.get("ok") or r.get("already"):
        return True
    if r.get("err") == "no-select":
        # 弹层没有声明下拉（短故事的声明在建书时已定，真机实案）→ 放行
        return True
    return False


def run_qimao_short_publish(page, st, i, note, values):
    """七猫短故事发布确认链：立即发布 → 合规框/声明/确认发布 → 提交完成。

    「立即发布」由前置 click_real 步点过；这里循环推进可见弹层直到
    「发布成功/提交成功」或弹层全部收起（硬凭据由后续 verify 步在短故事
    管理页出具——弹层收起只说明确认链走完，不算发布成功）。"""
    note(i, "处理七猫短故事发布确认链")
    deadline = time.time() + float(st.get("timeout") or 120)
    declaration = str(values.get("content_declaration") or "含AI生成内容")
    clicks = {"compliance": 0, "renum": 0, "confirm": 0}
    while time.time() < deadline:
        try:
            body = str(page.call("()=>(document.body.innerText||'')",
                                 timeout=15) or "")
            dialogs = _qimao_visible_dialogs(page)
        except Exception:
            time.sleep(0.6)
            continue
        joined = "\n".join(dialogs)
        if "发布成功" in body or "提交成功" in body:
            note(i, "平台提示发布成功")
            return
        if "重要提醒" in joined:
            if clicks["compliance"] >= 3:
                raise FlowError("七猫重要提醒框点「我已阅读并知晓」后仍未推进：%s"
                                % joined[:160])
            if not _qimao_click_dialog_label(page, "我已阅读并知晓"):
                raise FlowError("七猫重要提醒框没有「我已阅读并知晓」按钮：%s"
                                % joined[:160])
            clicks["compliance"] += 1
            note(i, "已确认重要提醒")
            time.sleep(1.2)
            continue
        if "更正序号去发布" in joined:
            if clicks["renum"] >= 3:
                raise FlowError("七猫序号提示点「更正序号去发布」后仍未推进：%s"
                                % joined[:160])
            _qimao_click_dialog_label(page, "更正序号去发布")
            clicks["renum"] += 1
            note(i, "已点「更正序号去发布」")
            time.sleep(1.0)
            continue
        if "确认发布" in joined or "立即发布确认" in joined:
            if not _qimao_set_declaration(page, declaration):
                raise FlowError("七猫内容声明选不成「%s」：声明发布后不可修改，"
                                "已中止确认，请到平台手工核对后提交" % declaration)
            if clicks["confirm"] >= 5:
                raise FlowError("七猫确认发布点「确认发布」后弹层仍未收起：%s"
                                % joined[:160])
            if not _qimao_click_dialog_label(page, "确认发布"):
                raise FlowError("七猫确认弹层没有「确认发布」按钮（真实鼠标与 "
                                "DOM 点击都未命中）：%s" % joined[:160])
            clicks["confirm"] += 1
            note(i, "点「确认发布」")
            time.sleep(2.0)
            continue
        if "未设置试读" in body or "正文至少" in body:
            raise FlowError("七猫拦截发布：%s" % body[:160])
        if clicks["confirm"] and not joined:
            # 确认点过、弹层已收起：交给后续 verify 出硬凭据
            note(i, "确认弹层已收起，交管理页核验")
            return
        time.sleep(0.6)
    raise FlowError("等待七猫短故事发布确认链超时（%ds）"
                    % int(float(st.get("timeout") or 120)))
