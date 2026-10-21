# -*- coding: utf-8 -*-
"""发布流程的 JS/DOM 辅助与错误类型。

从 publish.flow 拆出（单文件行数治理），原模块再导出保持旧调用面。
"""
from __future__ import annotations

import time


class FlowError(Exception):
    """流程失败：message 面向用户（含步骤序号与截图路径线索）。"""


def _fanqie_active_dialog(dialogs):
    """Prefer blocking confirmation dialogs over an underlying publish form.

    Fanqie can render a typo-confirmation modal above the publish-settings
    modal. DOM order is not visual stacking order, so selecting the last
    visible dialog can make the flow click the covered settings form again.
    """
    texts = [str(text or "").strip() for text in (dialogs or [])]
    for mark in ("发布提示", "请选择内容检测方式", "是否进行内容风险检测？",
                 "发布设置"):
        for text in texts:
            if mark in text:
                return text
    return texts[-1] if texts else ""


def _has_new_feedback(before, after, marks):
    """Return whether a submit click produced a new matching dialog/toast."""
    before_dialog = str((before or {}).get("dialog") or "")
    after_dialog = str((after or {}).get("dialog") or "")
    before_body = str((before or {}).get("body") or "")
    after_body = str((after or {}).get("body") or "")
    before_feedback = [str(x) for x in ((before or {}).get("feedback") or [])]
    after_feedback = [str(x) for x in ((after or {}).get("feedback") or [])]
    for mark in marks:
        if mark in after_dialog and mark not in before_dialog:
            return True
        if mark in after_body and mark not in before_body:
            return True
        if (sum(mark in x for x in after_feedback) >
                sum(mark in x for x in before_feedback)):
            return True
    return False


def _fanqie_submission_limit_changed(before, after):
    """A daily submission quota cannot be fixed by changing publish date."""
    return _has_new_feedback(
        before, after, ["提交字数超出每日上限", "投稿字数超出每日上限"])


def _fanqie_schedule_conflict(before, after):
    """Detect a new platform rejection of the requested schedule slot."""
    return _has_new_feedback(
        before, after, ["半小时以后", "发布时间间隔", "同一时间"])


class TitleDupError(FlowError):
    """平台拒绝书名：重名（全平台书名唯一）。message 带平台提示原文。

    与普通流程失败分开成独立类型：manager 建书要按它触发「对账复用 →
    自动换名重试」，普通失败不能换名（换了也建不成）。"""

    def __init__(self, hint):
        super().__init__("平台拒绝书名（已存在同名作品）：%s" % hint)
        self.hint = hint


# 重名提示识别词（番茄/七猫建书页校验文案口径：「书名已存在」「该名称已被
# 使用」等）。核心词「已存在/已被使用/已被注册/重名」足够特异——建书页上
# 出现这些词几乎必然指向书名撞车；不加宽泛的「重复」，防误伤其它字段校验。
DUP_HINT_WORDS = ("已存在", "已被使用", "已被注册", "重名", "已被占用")


def _dup_probe_js():
    # 抓页面上可见提示里命中重名关键词的那条（toast/表单校验/弹窗）。
    # 选择器与 _page_error_text_js 同源；只回命中词的那条，空串=页面没喊重名。
    return ("(words)=>{const sels='.arco-message,.arco-notification,"
            "[class*=message],[class*=toast],[class*=error],[class*=alert],"
            "[class*=tip],[class*=tooltip],[class*=valid]';"
            "for(const e of document.querySelectorAll(sels)){"
            "const r=e.getBoundingClientRect();"
            "if(r.width<=0||r.height<=0)continue;"
            "const x=(e.innerText||'').trim();"
            "if(!x||x.length>160)continue;"
            "for(const w of words){if(x.includes(w))return x;}}"
            "return '';}")


def dup_hint(page, timeout=5):
    """读页面上的重名提示文本；无/读失败返回空串。预检与事后探测共用。"""
    try:
        v = page.call(_dup_probe_js(), list(DUP_HINT_WORDS), timeout=timeout)
        return v.strip() if isinstance(v, str) else ""
    except Exception:
        return ""


def _click_match_js():
    # 点包含任一关键词的最小可见元素（长度最小者=最内层卡片）。
    # 流程字段名为 ``any``：例如七猫建书入口同时兼容“新建小说”和
    # “创建作品”，平台页面只出现其中一个时也必须能继续。
    return ("(keys,maxLen)=>{"
            "const vis=e=>{const r=e.getBoundingClientRect();return r.width>0&&r.height>0};"
            "const ds=[...document.querySelectorAll('.el-dialog,.modal,[role=dialog]')].filter(vis);"
            "const root=ds.length?ds[ds.length-1]:document;"
            "const hit=[...root.querySelectorAll('div,li,section,label,span,a')].filter(e=>{"
            "const x=(e.innerText||'').trim();"
            "return x && x.length<=maxLen && keys.some(k=>x.includes(k));});"
            "if(!hit.length)return{ok:false,err:'找不到包含任一关键词：'+keys.join('、')+' 的元素'};"
            "hit.sort((a,b)=>{const ax=(a.innerText||''),bx=(b.innerText||'');"
            "const as=keys.reduce((n,k)=>n+(ax.includes(k)?1:0),0);"
            "const bs=keys.reduce((n,k)=>n+(bx.includes(k)?1:0),0);"
            "return bs-as||ax.length-bx.length;});"
            "let el=hit[0];"
            "let act=el.closest('a,button,[role=button],[class*=btn]');"
            "if(!act){const card=el.closest('.client-box');if(card)act=card;}"
            "if(!act)act=el;"
            "act.scrollIntoView({block:'center'});"
            "const rc=act.getBoundingClientRect();"
            "return{ok:true,tag:act.tagName,"
            "x:Math.round(rc.x+rc.width/2),y:Math.round(rc.y+rc.height/2)};}")


def _fill_label_js():
    # Element UI 表单：在 .el-form-item（或 class 含 form-item 的容器）里按
    # label 文本定位控件，走与 browser.fill 相同的 native setter 管道
    return ("(labelText,text)=>{"
            "const items=[...document.querySelectorAll('.el-form-item,[class*=form-item]')];"
            "let target=null;"
            "for(const it of items){"
            "const lb=it.querySelector('[class*=label],[class*=label]');"
            "const ltxt=((lb&&lb.innerText)||'').trim();"
            "if(ltxt&&ltxt.includes(labelText)){target=it;break;}}"
            "if(!target)return{ok:false,err:'找不到字段「'+labelText+'」'};"
            "const el=target.querySelector('textarea,[contenteditable=true],input[type=text]')||"
            "target.querySelector('input,textarea');"
            "if(!el)return{ok:false,err:'字段「'+labelText+'」下没有输入控件'};"
            "el.scrollIntoView({block:'center'});el.focus();"
            "if(el.isContentEditable){"
            "const r=document.createRange();r.selectNodeContents(el);"
            "const g=getSelection();g.removeAllRanges();g.addRange(r);"
            "document.execCommand('insertText',false,text);"
            "return{ok:true};}"
            "const proto=el.tagName==='TEXTAREA'?HTMLTextAreaElement.prototype:HTMLInputElement.prototype;"
            "const d=Object.getOwnPropertyDescriptor(proto,'value');"
            "(d&&d.set?d.set:function(v){el.value=v}).call(el,text);"
            "el.dispatchEvent(new Event('input',{bubbles:true}));"
            "el.dispatchEvent(new Event('change',{bubbles:true}));"
            "return{ok:true};}")


def _click_text_js():
    # contains 模式下外层容器（整页文本）也会 includes 命中——候选按文本长度
    # 升序取最短者=最内层最精确元素（「新建小说」按钮赢过包它的大 DIV）
    return ("(t,scope,contains)=>{"
            "const els=[...document.querySelectorAll(scope||'button,a,[role=button],span,li')];"
            "const cands=els.filter(e=>{const x=(e.innerText||'').trim();"
            "return x&&(contains?x.includes(t):x===t);});"
            "if(!cands.length)return{ok:false,err:'页面上找不到文本为「'+t+'」的可点元素'};"
            "cands.sort((a,b)=>((a.innerText||'').trim().length)-((b.innerText||'').trim().length));"
            "const hit=cands[0];"
            "hit.scrollIntoView({block:'center'});hit.click();return{ok:true};}")


def _checked_js():
    # radio/checkbox 点击闭环：点击派发成功 ≠ 选中成功（遮罩/几何打偏会静默
    # 吞掉，2026-09-28 建书两 radio 全空实案）。点完回头验 checked 态：
    # arco 系 label 挂 checked 类、原生 input 挂 checked 属性，双信号任一即算。
    # 元素匹配必须「文本精确等值」优先：scope 里的 [class*=serial-radio] 会先
    # 命中整个 radio 组容器（serial-radio-group，文本“男频女频”包含“女频”），
    # 而组内第一个 input 恒为第一选项——按 includes 取容器后验证「女频」读到
    # 的一直是男频的未选中态，点击明明已生效却误报未选中（2026-10-18 番茄
    # 建书「女频连点8次仍未见选中态」实案）。组容器仅在无精确匹配时兜底，
    # 此时语义放宽为「容器内任意 input 选中即算」。
    return ("(t,scope)=>{"
            "const els=[...document.querySelectorAll("
            "scope||'label,[class*=radio],[class*=checkbox]')]"
            ".filter(e=>e.getBoundingClientRect().width>0);"
            "const eq=els.filter(e=>((e.innerText||'').trim())===t);"
            "const el=eq[0]||els.find(e=>((e.innerText||'').trim()).includes(t));"
            "if(!el)return{ok:false,err:'nf'};"
            "const box=el.closest('[class*=radio],[class*=checkbox]')||el;"
            "let any=false;"
            "for(const i of box.querySelectorAll('input[type=radio],input[type=checkbox]')){"
            "if(i.checked){any=true;break;}}"
            "const ck=any||/checked|active/.test(box.className||'')"
            "||/checked|active/.test(el.className||'');"
            "return{ok:!!ck};}")


def _page_error_text_js():
    # 抓页面上可见的报错/提示（toast、表单校验消息）：提交被平台静默拒绝
    # 时 url_any 只看得到「没跳转」，把页面自己的话带回来才不用瞎猜
    return ("()=>{const sels='.arco-message,.arco-notification,"
            "[class*=message],[class*=toast],[class*=error],[class*=alert]';"
            "const out=[],seen={};"
            "for(const e of document.querySelectorAll(sels)){"
            "const r=e.getBoundingClientRect();"
            "if(r.width<=0||r.height<=0)continue;"
            "const x=(e.innerText||'').trim();"
            "if(!x||x.length>120||seen[x])continue;seen[x]=1;out.push(x);}"
            "return out.slice(0,5).join(' ｜ ');}")


def _tag_sections_js():
    # 标签弹层分组读取（七猫形态）：div.tags-wrap[data-type-id] 每组一块，
    # 组名在 .tags-tit（带前导空格须 trim），芯片名 .tags-con-name，
    # 选中态 .tags-con.tag-selected。返回 null = 弹层无此结构。
    return ("()=>{const wraps=[...document.querySelectorAll('.tags-wrap')];"
            "if(!wraps.length)return null;"
            "return wraps.map(w=>({"
            "g:(((w.querySelector('.tags-tit')||{}).innerText)||'').trim(),"
            "sel:[...w.querySelectorAll('.tags-box .tags-con.tag-selected .tags-con-name')]"
            ".map(e=>(e.innerText||'').trim())}));}")


def _tag_click_js():
    # 组内精确定位芯片再点。四组选项同屏渲染、芯片点击永远落在其所在
    # 分组（左侧组名导航只管高亮不管归属），且组名导航/全弹层 contains
    # 搜索在滚动竞态下会假成功——el.click() 打在滚动中的芯片上被平台吞
    # 掉也报 ok（0928 实案：前 9 个全中、第 10 个「都市」假成功，背景组
    # 空选，「确定」被平台以「每个类型下至少选择1个标签」打回）。
    # err=nogroup（弹层没这组）单独返回，供上层区分「没等到弹层」与
    # 「组里没这个标签」（后者=平台目录漂移，必须立刻报错而不是去别组碰）。
    return ("(g,t)=>{for(const w of document.querySelectorAll('.tags-wrap')){"
            "const gname=(((w.querySelector('.tags-tit')||{}).innerText)||'').trim();"
            "if(gname!==g)continue;"
            "const hit=[...w.querySelectorAll('.tags-box .tags-con-name')]"
            ".find(e=>((e.innerText||'').trim())===t);"
            "if(!hit)return{ok:false,err:'组「'+g+'」下没有标签「'+t+'」'};"
            "hit.scrollIntoView({block:'center'});hit.click();return{ok:true};}"
            "return{ok:false,err:'nogroup'};}")


def _tags_select_grouped(page, note, i, pairs):
    """分组定位点选：逐项点+回读校验（点不中就重新定位重试），收尾全组
    对账——多选的再点一次剔除（芯片是开关）、缺的补点，两轮仍不齐才报
    错，错误带组名+差集，别让人对着平台的「每个类型下至少选择1个标签」
    toast 瞎猜。"""
    def read():
        return page.call(_tag_sections_js()) or []

    def one(g, t):
        for _try in range(4):
            r = page.call(_tag_click_js(), g, t)
            err = (r or {}).get("err")
            if err == "nogroup":
                time.sleep(0.8)                # 弹层中途重渲染：等它回来
                continue
            if err:
                raise FlowError(err)           # 组里没有这个标签=目录漂移，去别组碰只会点错
            time.sleep(0.5)
            for s in read():
                if s["g"] == g:
                    if t in s["sel"]:
                        return True
                    break
        return False

    for g, t in pairs:
        if not one(g, t):
            raise FlowError("标签「%s」未能选入「%s」组（该组或已选满3个）" % (t, g))
    want = {}
    for g, t in pairs:
        want.setdefault(g, set()).add(t)
    bad = []
    for _round in range(2):
        bad = []
        for s in read():
            w = want.get(s["g"])
            if w is None:
                continue                       # 没让点的组不动它
            extra = sorted(set(s["sel"]) - w)
            miss = sorted(w - set(s["sel"]))
            if extra or miss:
                parts = []
                if extra:
                    parts.append("多选 " + "、".join(extra))
                if miss:
                    parts.append("缺 " + "、".join(miss))
                bad.append("「%s」组%s" % (s["g"], "；".join(parts)))
        if not bad:
            note(i, "标签对账通过（%d 项）" % len(pairs))
            return
        for s in read():                       # 多选：再点一次取消
            for x in set(s["sel"]) - want.get(s["g"], set()):
                page.call(_tag_click_js(), s["g"], x)
                time.sleep(0.4)
        for g, t in pairs:                     # 缺选：补点
            sel = next((x["sel"] for x in read() if x["g"] == g), [])
            if t not in sel:
                page.call(_tag_click_js(), g, t)
                time.sleep(0.4)
    raise FlowError("标签对账未过：%s" % "；".join(bad))


def _fail_shot(shot, name):
    try:
        if shot:
            shot(name)
    except Exception:
        pass
