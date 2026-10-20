# -*- coding: utf-8 -*-
"""数据驱动的发布流程解释器：按步骤表操作一个 CDP 页面。

平台流程（建书/发章）描述为步骤数组，与代码分离：
- 内置默认表在各平台模块（fanqie/qimao），是「待校准」的推测选择器；
- data/publish/flows-<platform>.json 存在时整体覆盖内置表——平台改版/
  首次校准只改数据文件，不用动代码；
- 每步失败先落一张 fail-*.png 再抛人话错误，发布中断可回看卡在哪一步。

步骤类型：
  navigate  {url}                 打开地址（支持 {home} 等占位符，来自平台配置）
  wait      {sel, timeout}        等元素出现
  fill      {sel, key}            把 values[key] 填进输入框（React 安全 + 富文本）
  click     {sel}                 点选择器命中的元素
  click_text {text, scope, contains}  按「可见文本」点按钮/标签（无稳定 id 的弹层项）
  shot      {name}                截图存证
  probe     {note}                dump 表单元素清单（校准选择器用，结果进 log）
  click_match {any:[..], max_len}  关键词选块（点包含任一关键词的最小元素）
  submit    {sel}                 终步：auto_submit=false 时跳过（留给人工确认），
                                  跳过时补一张 ready-*.png，用户在浏览器窗口里自查提交
  volume   {key, open_sel, modal_sel, item_sel, add_sel, input_sel,
            confirm_sel, settle}  发章带卷：选平台已有卷，缺则新建（自动分卷）
  verify   {url, any, settle}     上线验证：导航后断言标记在场
  url_any  {any: [..]}            断言当前 URL 含任一标记（登录跳转检测）
"""
from __future__ import annotations

import datetime
import json
import re
import time

from .browser import BrowserError


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


def run_flow(page, steps, values=None, config=None, auto_submit=True,
             shot=None, log=None, dup_check=False):
    """跑一个流程。values：fill 取值字典；config：平台 URL 等占位符来源。

    auto_submit=True → 普通发布流程自动完成最终提交；只有草稿流程或
    明确的内部探测步骤才应传 False。普通发布入口会在更上层再次强制为 True，
    防止旧客户端把上传停在人工确认页。
    shot(name) → 截图落盘函数（manager 注入，路径含任务/平台维度）；
    log(line)  → 步骤日志函数（进发布记录的 log 字段）。返回执行到的步数。
    dup_check  → 提交前探测页面重名提示（建书专用）：平台实时校验喊「已
    存在」时点提交必然被拒、流程只会误报「未登录或改版」——在点击前抛
    TitleDupError 让上层走换名重试，别白点（2026-09-30 马甲案）。"""
    values = values or {}
    config = config or {}
    log = log or (lambda s: None)
    # 原生弹窗灭活（confirm/alert/prompt）：无人值守流程没人点「确定」，
    # 原生弹窗会挂起渲染主线程让后续 CDP 全线超时（2026-10-08 实案）
    getattr(page, "neutralize_dialogs", lambda: None)()

    def note(i, s):
        log("步骤%d %s" % (i, s))

    for i, st in enumerate(steps):
        act = (st.get("do") or "").strip()
        try:
            if act == "navigate":
                url = st["url"]
                for k, v in config.items():
                    url = url.replace("{%s}" % k, str(v))
                for k, v in (values or {}).items():   # editor_url/draft_url 等任务级占位
                    url = url.replace("{%s}" % k, str(v))
                # 占位符没被吃掉=values 缺键（平台模块缺 URL 生成器/登记不全）。
                # 直接放行会让浏览器停在 about:blank，到 url_any 才误报
                # 「未登录或改版」——在这里点名缺哪个键（0924 番茄三连败）。
                left = re.findall(r"\{[A-Za-z_][A-Za-z0-9_]*\}", url)
                if left:
                    raise FlowError("URL 占位符 %s 没有对应值：%s"
                                    % (",".join(left), url[:90]))
                note(i, "打开 %s" % url)
                # 重页（编辑器/管理列表）可按步放宽；缺省 45s（30s 对慢网偏紧）
                page.navigate(url, timeout=float(st.get("timeout") or 45))
            elif act == "wait":
                note(i, "等待 %s" % st["sel"])
                page.wait_for(st["sel"], timeout=float(st.get("timeout") or 12))
            elif act == "fill":
                key = st.get("key") or ""
                text = values.get(key, "")
                if text in ("", None):
                    continue                    # 空值字段跳过（如无第二主角）
                note(i, "填入 %s（%d 字）" % (key, len(str(text))))
                page.wait_for(st["sel"], timeout=8)
                page.fill(st["sel"], str(text))
            elif act == "click":
                note(i, "点击 %s" % st["sel"])
                page.wait_for(st["sel"], timeout=8)
                page.click(st["sel"])
            elif act == "click_text":
                text = str(st.get("text") or "")
                for k, v in (values or {}).items():     # {signing_mode} 等动态值
                    text = text.replace("{%s}" % k, str(v))
                if not text:
                    continue
                note(i, "点击「%s」" % text)
                r = None
                for _try in range(3):               # SPA 渲染慢：找不到先等再试
                    r = page.call(_click_text_js(), text, st.get("scope") or "",
                                  bool(st.get("contains")))
                    if (r or {}).get("ok"):
                        break
                    time.sleep(0.9)
                if not (r or {}).get("ok"):
                    raise FlowError((r or {}).get("err") or text)
            elif act == "click_real":
                # 真实鼠标事件点击（CDP Input 派发）：qm-btn 等自定义按钮
                # 只认真实事件序列，el.click() 无效。发布确认链全靠它。
                text = str(st.get("text") or "")
                for k, v in (values or {}).items():   # {target_reader} 等动态值
                    text = text.replace("{%s}" % k, str(v))
                for k, v in (values or {}).items():
                    text = text.replace("{%s}" % k, str(v))
                if not text:
                    continue
                note(i, "真实点击「%s」" % text)
                r = None
                verified = not st.get("expect_checked")
                tries = int(st.get("tries") or 25)
                # 七猫改版把“存草稿”按钮文案改成“存为草稿”；保留流程
                # 表旧文案作为主键，同时在真实页面上兼容这个等价文案。
                click_texts = ([text, "存为草稿"] if text == "存草稿"
                               else [text])
                for _try in range(tries):
                    for click_text in click_texts:
                        r = page.real_click_text(click_text, st.get("scope") or
                                                 "a,button,[class*=btn]",
                                                 y_min=st.get("y_min"),
                                                 y_max=st.get("y_max"))
                        if (r or {}).get("ok"):
                            break
                    if (r or {}).get("ok"):
                        if verified:
                            break
                        # 点击成功≠选中成功：回头验 checked 态，不中重点
                        v = page.call(_checked_js(), text,
                                      st.get("scope") or "")
                        if (v or {}).get("ok"):
                            verified = True
                            break
                        note(i, "「%s」已点但未见选中态，重试" % text)
                    time.sleep(0.4)
                if not verified or not (r or {}).get("ok"):
                    if st.get("optional"):
                        note(i, "「%s」未出现，跳过（optional）" % text)
                        continue
                    if (r or {}).get("ok") and st.get("expect_checked"):
                        raise FlowError("「%s」连点 %d 次仍未见选中态"
                                        "（radio 被遮罩/改版拦下）" % (text, tries))
                    raise FlowError((r or {}).get("err") or text)
            elif act == "click_in":
                # 先按 scope_text 定位容器（如书卡），再在容器内点 text 按钮。
                # 解决「按钮与书名同卡片但不在彼此祖先链」的组合定位。
                # real=true：容器内定位后走 CDP 真实鼠标（2026-10-08 番茄发布
                # 提示弹窗实案——el.click() 点「提交」不触发发布请求，平台只认
                # 真实输入事件）；tries 可配（默认 10，弹窗要等云端保存）。
                scope_t = str(st.get("scope_text") or "")
                text = str(st.get("text") or "")
                for k, v in (values or {}).items():
                    scope_t = scope_t.replace("{%s}" % k, str(v))
                    text = text.replace("{%s}" % k, str(v))
                note(i, "在含「%s」的卡片内点「%s」" % (scope_t, text))
                r = None
                for _try in range(int(st.get("tries") or 10)):
                    r = page.call(
                        "(sc,t,real)=>{"
                        # Ignore invisible modal/editor duplicates.  Their
                        # zero-sized center otherwise triggers `blocked` and
                        # retries until the whole batch times out.
                        "const vis=e=>{const r=e.getBoundingClientRect(),s=getComputedStyle(e);"
                        "return r.width>0&&r.height>0&&s.display!=='none'&&s.visibility!=='hidden'"
                        "&&s.pointerEvents!=='none'&&parseFloat(s.opacity||'1')>0;};"
                        "const cards=[...document.querySelectorAll('div,li,section,tr')].filter(e=>{"
                        "const x=(e.innerText||'').trim();"
                        "const hasBtn=e=>[...e.querySelectorAll('a,button,[role=button],[class*=btn],span')]"
                        ".some(b=>{const y=(b.innerText||'').trim();return vis(b)&&(y===t||y.includes(t)&&y.length<=t.length+6);});"
                        "return vis(e)&&x.includes(sc)&&x.length<600&&hasBtn(e);});"
                        "if(!cards.length)return{ok:false,err:'找不到含'+sc+'的卡片'};"
                        # Pick the tightest matching card.  A page-level/table
                        # wrapper also contains the chapter text and would
                        # otherwise win by length, sending duplicate-title
                        # drafts to the newest row instead of the requested one.
                        "cards.sort((a,b)=>(a.innerText||'').length-(b.innerText||'').length);"
                        "const card=cards[0];"
                        "let btns=[...card.querySelectorAll('a,button,[role=button],[class*=btn],span')].filter(e=>{"
                        "const x=(e.innerText||'').trim();return vis(e)&&!e.disabled&&(x===t||x.includes(t)&&x.length<=t.length+6);});"
                        "if(!btns.length)return{ok:false,err:'卡片内没有'+t};"
                        "const exact=btns.filter(e=>(e.innerText||'').trim()===t);"
                        "if(exact.length)btns=exact;"
                        "btns.sort((a,b)=>(a.innerText||'').length-(b.innerText||'').length);"
                        "let blocked='';"
                        "for(const el of btns){"
                        # A class containing 'btn' can be the whole footer,
                        # including Cancel. Only promote a label to an action
                        # with the same text; never click the footer's center.
                        # Prefer the nearest semantic control only when its
                        # label is exactly the requested action.  A footer
                        # wrapper such as ``.footer-btns`` may contain both
                        # cancel and submit labels; promoting that wrapper (or
                        # a child span whose center is covered by a sibling)
                        # was the source of the live "提交被挡住：SPAN" failure.
                        "const parent=el.closest('a,button,[role=button]');"
                        "const ptxt=parent?(parent.innerText||'').trim():'';"
                        "const etxt=(el.innerText||'').trim();"
                        "const act=parent&&ptxt===etxt?parent:el;"
                        "if(!vis(act)||act.disabled||act.getAttribute('aria-disabled')==='true')continue;"
                        "act.scrollIntoView({block:'center'});"
                        "if(real){const rc=act.getBoundingClientRect();"
                        # Probe several interior points.  Text spans can have
                        # a sibling icon or a transient overlay at their exact
                        # center even though another point on the same action
                        # is a valid, unambiguous click target.
                        " const pts=[[.5,.5],[.25,.5],[.75,.5],[.5,.25],[.5,.75]];"
                        " let hit=null;for(const [fx,fy] of pts){"
                        "  const x=Math.round(rc.x+rc.width*fx),y=Math.round(rc.y+rc.height*fy);"
                        "  const px=document.elementFromPoint(x,y);"
                        "  const top=px&&px.closest('a,button,[role=button]');"
                        "  const sameAction=top&&(top.innerText||'').trim()===t"
                        "   &&cards.some(c=>c.contains(top))"
                        "   &&top.getBoundingClientRect().width>0;"
                        "  if(px&&(act.contains(px)||px.contains(act)||sameAction)){hit={x,y};break;}"
                        "  if(!hit&&px)blocked='点击「'+t+'」被挡住：'+px.tagName+'.'+String(px.className||'').slice(0,100);"
                        " }"
                        " if(!hit)continue;"
                        " return{ok:true,x:hit.x,y:hit.y};}"
                        "act.click();"
                        "return{ok:true};}"
                        "return{ok:false,err:blocked||'卡片内没有可用的'+t};}", scope_t, text, bool(st.get("real")))
                    if (r or {}).get("ok"):
                        break
                    time.sleep(0.9)
                if not (r or {}).get("ok"):
                    if st.get("optional"):
                        note(i, "含「%s」的卡片未出现，跳过（optional）" % scope_t)
                        continue
                    raise FlowError((r or {}).get("err") or "click_in 失败")
                if st.get("real") and isinstance(r, dict) and "x" in r:
                    for _t in ("mousePressed", "mouseReleased"):
                        page.send("Input.dispatchMouseEvent",
                                  {"type": _t, "x": r["x"], "y": r["y"],
                                   "button": "left", "clickCount": 1}, timeout=8)
                    note(i, "真实鼠标点「%s」@(%d,%d)" % (text, r["x"], r["y"]))
            elif act == "click_arrow":
                # 展开下拉按钮组（番茄「下一步▾」）：点目标按钮组右缘 10px
                note(i, "展开下拉箭头")
                r = page.call(
                    "(t)=>{"
                    "const vis=e=>e.getBoundingClientRect().width>0;"
                    "const btn=[...document.querySelectorAll('button')].find(e=>"
                    "vis(e)&&(e.innerText||'').trim().includes(t));"
                    "if(!btn)return{ok:false,err:'按钮未找到'};"
                    "const host=btn.closest('[class*=group],[class*=dropdown]')||btn.parentElement;"
                    "const r=host.getBoundingClientRect();"
                    "return{ok:true,x:Math.round(r.x+r.width-10),y:Math.round(r.y+r.height/2)};}", str(st.get("target") or "下一步"))
                if not (r or {}).get("ok"):
                    if st.get("optional"):
                        note(i, "箭头未出现，跳过（optional）")
                        continue
                    raise FlowError((r or {}).get("err") or "箭头定位失败")
                page.send("Input.dispatchMouseEvent",
                          {"type": "mousePressed", "x": r["x"], "y": r["y"],
                           "button": "left", "clickCount": 1}, timeout=8.0)
                page.send("Input.dispatchMouseEvent",
                          {"type": "mouseReleased", "x": r["x"], "y": r["y"],
                           "button": "left", "clickCount": 1}, timeout=8.0)
                time.sleep(float(st.get("settle") or 1.2))
            elif act == "click_match":
                # 关键词选块（站点卡片等）：点同时包含所有关键词的最小元素
                keys = [str(x) for x in (st.get("any") or []) if str(x).strip()]
                for k2, v2 in (values or {}).items():   # {book_name} 等动态值
                    keys = [x.replace("{%s}" % k2, str(v2)) for x in keys]
                note(i, "点击含 %s 的卡片" % keys)
                r = None
                for _try in range(4):               # 弹层渲染慢：找不到先等再试
                    r = page.call(_click_match_js(), keys,
                                  int(st.get("max_len") or 400))
                    if (r or {}).get("ok"):
                        break
                    time.sleep(1.0)
                if not (r or {}).get("ok"):
                    raise FlowError((r or {}).get("err") or "卡片未找到")
                # 站点卡片（七猫 client-box 等）只认真实鼠标事件序列：合成
                # act.click() 点了不选中，确认时被「请选择建书站点」拦下，
                # 表单永不出现（2026-10-18 七猫建书 textarea 等待超时实案）。
                # 这里按 JS 返回的卡片中心坐标派发 CDP 真实鼠标（同
                # real_click_text 通道）；无坐标的历史返回形态退回合成点击。
                if (r or {}).get("x") is not None:
                    page.send("Input.dispatchMouseEvent",
                              {"type": "mousePressed", "x": r["x"], "y": r["y"],
                               "button": "left", "clickCount": 1}, timeout=8.0)
                    page.send("Input.dispatchMouseEvent",
                              {"type": "mouseReleased", "x": r["x"], "y": r["y"],
                               "button": "left", "clickCount": 1}, timeout=8.0)
                # Vue card selections update asynchronously; the following
                # confirm action must observe the selected class before it is
                # clicked (Qimao otherwise reports “请选择建书站点”).
                time.sleep(float(st.get("settle") or 0.6))
            elif act == "fill_label":
                # 按字段标签填（Element UI form-item：label 与控件同容器）
                key = st.get("key") or ""
                label = str(st.get("label") or "")
                text = str(values.get(key, "") or "")
                if not text:
                    continue
                note(i, "填字段「%s」（%d 字）" % (label, len(text)))
                page.wait_for("[class*=form]", timeout=8)
                r = None
                for _try in range(3):
                    r = page.call(_fill_label_js(), label, text)
                    if (r or {}).get("ok"):
                        break
                    time.sleep(0.9)
                if not (r or {}).get("ok"):
                    raise FlowError((r or {}).get("err") or "填字段失败")
            elif act == "radio":
                # 单选组：values[map[key]] 映射到 radio value 后点它。
                # 隐藏 input（Element UI）点不到——真实鼠标点它的可见 label
                # 中心。合成 label.click() 在 label 包住整组 radio 的形态下
                # 永远激活组内第一个选项（2026-10-18 七猫建书「女生被选成
                # 男生」实案：频道错→分类下拉加载成男生分类→连锁全错）。
                # 点后回读 checked 态，未中重点（含 y 微调避开相邻项）。
                key = st.get("key") or ""
                group = st.get("map") or {}
                want = str(values.get(key, "")).strip()
                val = group.get(want)
                if val is None:
                    if st.get("optional"):
                        note(i, "跳过单选 %s（无值）" % key)
                        continue
                    raise FlowError("单选 %s：值「%s」不在映射 %s 里" % (key, want, list(group)))
                note(i, "单选 %s=%s（value=%s）" % (key, want, val))
                # 分组定位：同页多组 radio 的 value 会撞车（七猫建书实案：
                # 目标读者 0/1 与连载状态 0/1 同值——点连载 value=0 命中
                # 目标读者组的男生，把已选女生翻回去，some() 验证还被两组
                # 同值假阳性骗过）。scope_text 先定位含该文本的表单行
                # （.el-form-item 等），组内再找 value——找不到容器才退全局。
                scope_text = str(st.get("scope_text") or "").strip()
                base_js = ("(v,sc)=>{let root=null;"
                           "if(sc){const els=[...document.querySelectorAll"
                           "('[class*=form-item],[class*=form_row],fieldset')]"
                           ".filter(e=>e.getBoundingClientRect().width>0"
                           "&&e.querySelector('input[type=radio]'));"
                           "const hit=els.filter(e=>(e.innerText||'').includes(sc))"
                           ".sort((a,b)=>(a.innerText||'').length-(b.innerText||'').length);"
                           "if(hit.length)root=hit[0];}"
                           "const all=[...document.querySelectorAll('input[type=radio]')];"
                           "const pool=root?root.querySelectorAll('input[type=radio]'):all;"
                           "const rs=[...pool].filter(x=>x.value===v&&!x.disabled);"
                           "if(!rs.length)return{ok:false,err:'nf'};"
                           "return{ok:true,idx:all.indexOf(rs[0])};}")

                def _checked():
                    return page.call(
                        "(i)=>{const all=[...document.querySelectorAll"
                        "('input[type=radio]')];return i>=0&&i<all.length"
                        "&&!!all[i].checked;}", idx)

                verified = False
                idx = -1
                for _try in range(8):
                    r = page.call(base_js, str(val), scope_text)
                    if not (r or {}).get("ok"):
                        raise FlowError("单选 %s=%s 目标未找到（scope_text=%s）"
                                        % (key, want, scope_text or "无"))
                    idx = r["idx"]
                    # 策略1：隐藏 input 原生 click（Element UI 的 input 未
                    # display:none，编程点击可直接翻转受控状态）
                    page.call(
                        "(i)=>{const all=[...document.querySelectorAll"
                        "('input[type=radio]')];if(i>=0&&i<all.length)all[i].click();}",
                        idx)
                    time.sleep(0.45)
                    if _checked():
                        verified = True
                        break
                    # 策略2：label 中心真实鼠标（elementFromPoint 守卫，
                    # 被吸顶栏/浮层挡住时不盲点）
                    pos = page.call(
                        "(i)=>{const all=[...document.querySelectorAll"
                        "('input[type=radio]');const r0=all[i];"
                        "const lab=r0.closest('label')||r0.closest('.el-radio')"
                        "||r0.parentElement;"
                        "lab.scrollIntoView({block:'center'});"
                        "const rc=lab.getBoundingClientRect();"
                        "const x=Math.round(rc.x+rc.width/2),y=Math.round(rc.y+rc.height/2);"
                        "const hit=document.elementFromPoint(x,y);"
                        "if(!hit||(hit!==lab&&!lab.contains(hit)&&!hit.contains(lab)))"
                        "return{ok:false};"
                        "return {ok:true,x:x,y:y};}", idx)
                    if (pos or {}).get("ok") and pos.get("x") is not None:
                        page.send("Input.dispatchMouseEvent",
                                  {"type": "mousePressed", "x": pos["x"], "y": pos["y"],
                                   "button": "left", "clickCount": 1}, timeout=8.0)
                        page.send("Input.dispatchMouseEvent",
                                  {"type": "mouseReleased", "x": pos["x"], "y": pos["y"],
                                   "button": "left", "clickCount": 1}, timeout=8.0)
                        time.sleep(0.5)
                        if _checked():
                            verified = True
                            break
                    note(i, "单选 %s=%s 已点未选中，重试" % (key, want))
                if not verified:
                    raise FlowError("单选 %s=%s 连点 8 次未选中" % (key, want))
            elif act == "tags":
                # 标签弹层逐个点选：manager 把标签清单放 values["_tags"]。
                # 项为 [组名, 标签] 且弹层呈现分组结构（.tags-wrap）时走
                # 「分组定位」：芯片在**它所属组的区块内**精确定位、点完即
                # 回读该组选中集校验——点不中就重新定位重试，绝不带病前进。
                # 弹层无此结构或清单含无组名项时回退旧路径（整页 contains
                # 搜索：组名切换 + 直接点标签，历史平台兜底）。
                tags = values.get("_tags") or []
                if not tags:
                    note(i, "无标签可点，跳过")
                    continue
                scope = st.get("scope") or "span,li,label,[class*=dialog] *,[class*=popper] *"
                pairs = [(str(it[0]).strip(), str(it[1]).strip()) for it in tags
                         if not isinstance(it, (str, int))
                         and str(it[0] or "").strip() and str(it[1] or "").strip()]
                sections = None
                if pairs and len(pairs) == len(tags):
                    for _w in range(8):            # 等弹层渲染出分组结构
                        sections = page.call(_tag_sections_js())
                        if sections:
                            break
                        time.sleep(1.0)
                if sections and isinstance(sections, list):
                    missing = {g for g, _ in pairs} - {s["g"] for s in sections}
                    if missing:
                        # 弹层是分组结构但组名对不上=平台目录/组名改了——
                        # 回退旧路径只会全局乱点（本次事故根源形态），当场报错
                        raise FlowError(
                            "标签弹层分组与预期不符：缺 %s（弹层现有组：%s）；"
                            "请重生成作品信息后再试"
                            % ("、".join(sorted(missing)),
                               "、".join(s["g"] for s in sections)))
                    note(i, "点选标签 %d 项（分组定位）" % len(pairs))
                    _tags_select_grouped(page, note, i, pairs)
                    continue
                note(i, "点选标签 %d 项" % len(tags))
                for item in tags:
                    grp, tg = ("", str(item)) if isinstance(item, (str, int)) \
                        else (str(item[0] or ""), str(item[1] or ""))
                    if not tg:
                        continue
                    if grp:                             # 切到目标组
                        # 前置 click_text「添加标签」可能被分类下拉的关闭
                        # 动画/遮罩吞掉（el.click() 打在遮罩上也返回 ok），
                        # 组名找不到=弹层还没开：多等几轮让它开出来
                        r0 = None
                        for _try in range(4):
                            r0 = page.call(_click_text_js(), grp, scope, True)
                            if (r0 or {}).get("ok"):
                                break
                            time.sleep(1.0)
                        if not (r0 or {}).get("ok"):
                            raise FlowError("标签组「%s」切换失败（标签弹层未打开）" % grp)
                        time.sleep(0.3)
                    r = None
                    for _try in range(3):
                        r = page.call(_click_text_js(), tg, scope, True)
                        if (r or {}).get("ok"):
                            break
                        time.sleep(0.7)
                    if not (r or {}).get("ok"):
                        raise FlowError("标签「%s」点选失败：%s" % (tg, (r or {}).get("err")))
            elif act == "shot":
                note(i, "截图 %s" % st.get("name"))
                if shot:
                    shot(st.get("name") or "step")
            elif act == "probe":
                info = page.probe()
                note(i, "表单探测 %s：%d 个可交互元素" % (st.get("note") or "", len(info)))
                if log:
                    log("PROBE " + json.dumps(info, ensure_ascii=False)[:4000])
            elif act == "submit":
                # always=true 的提交步（草稿流的「存草稿」）本身就是保存动作，
                # 不吃 auto_submit 人工闸——被闸吞掉时流程停在「填好未存」，
                # manager 却照记成功（2026-10-10 44 章假成功实案）
                if not auto_submit and not st.get("always"):
                    note(i, "已填好未提交——请人工检查后提交（auto_submit=false）")
                    if shot:
                        shot("ready-manual-submit")
                    return i + 1
                if st.get("gate"):
                    # 闸门型 submit：只做 manual 模式的停点，auto 模式不点击——
                    # 动作交给后续步骤（如 js_click）单次执行。真实鼠标+JS 双击
                    # 「下一步」会打断平台进行中的云端保存，弹窗永远不出现
                    # （2026-10-08 深夜实案）。
                    note(i, "跳过点击（gate：动作由后续步骤单次执行）")
                    continue
                # Fanqie can restore an autosaved draft from an earlier failed
                # attempt and place a blocking modal over the editor.  The
                # modal is unrelated to the chapter currently being filled;
                # choosing "继续编辑" would restore stale content and then
                # submit the wrong chapter.  Dismiss it before clicking
                # 「下一步」 so the normal publish flow can proceed.
                stale_draft = page.call(
                    "()=>{const vis=e=>{const r=e.getBoundingClientRect(),s=getComputedStyle(e);"
                    "return r.width>0&&r.height>0&&s.display!=='none'&&s.visibility!=='hidden';};"
                    "const ds=[...document.querySelectorAll('[role=dialog],.arco-modal,"
                    ".byte-modal-wrapper')].filter(vis);"
                    "const d=[...ds].reverse().find(e=>(e.innerText||'').includes('有刚刚更新的草稿'));"
                    "if(!d)return{ok:false};"
                    "const b=[...d.querySelectorAll('button,[role=button]')].find(e=>"
                    "(e.innerText||'').replace(/\\s+/g,'').trim()==='放弃');"
                    "if(!b)return{ok:false,err:'button'};b.click();return{ok:true};}",
                    timeout=10) or {}
                # The editor can mount this prompt as a byte-modal wrapper
                # before its text is available on the wrapper node.  In that
                # short React render window the scoped lookup above returns
                # false even though the visible overlay already blocks the
                # trusted click on 「下一步」.  Detect the rendered prompt
                # from the visible body and dismiss it through the same
                # pointer path used for consequential buttons.
                if not stale_draft.get("ok"):
                    stale_point = page.call(
                        "()=>{const vis=e=>{const r=e.getBoundingClientRect(),s=getComputedStyle(e);"
                        "return r.width>0&&r.height>0&&s.display!=='none'&&s.visibility!=='hidden'"
                        "&&s.pointerEvents!=='none'&&parseFloat(s.opacity||'1')>0;};"
                        "const norm=x=>String(x||'').replace(/\\s+/g,'').trim();"
                        "const ds=[...document.querySelectorAll('body *')].filter(e=>vis(e)&&"
                        "norm(e.innerText).includes('有刚刚更新的草稿'));"
                        "ds.sort((a,b)=>norm(a.innerText).length-norm(b.innerText).length);"
                        "const d=ds[0];if(!d)return null;"
                        "const b=[...d.querySelectorAll('button,[role=button],a')].find(e=>"
                        "vis(e)&&norm(e.innerText)==='放弃');if(!b)return null;"
                        "const r=b.getBoundingClientRect(),x=Math.round(r.x+r.width/2),"
                        "y=Math.round(r.y+r.height/2),hit=document.elementFromPoint(x,y);"
                        "if(!hit||(hit!==b&&!b.contains(hit)&&!hit.contains(b)))return null;"
                        "return{x:x,y:y};}", timeout=10) or {}
                    if stale_point.get("x"):
                        page.send("Input.dispatchMouseEvent", {
                            "type": "mousePressed", "x": stale_point["x"],
                            "y": stale_point["y"], "button": "left", "clickCount": 1},
                            timeout=8.0)
                        page.send("Input.dispatchMouseEvent", {
                            "type": "mouseReleased", "x": stale_point["x"],
                            "y": stale_point["y"], "button": "left", "clickCount": 1},
                            timeout=8.0)
                        stale_draft = {"ok": True, "via": "scoped-pointer"}
                if stale_draft.get("ok"):
                    note(i, "已放弃旧草稿恢复提示")
                    # A DOM click can return successfully while Arco's
                    # transition/React handler has not closed the overlay.
                    # Do not continue into the submit action with the modal
                    # still covering the editor: that can click the wrong
                    # action or leave the current chapter unsubmitted.
                    deadline = time.time() + 3.0
                    while time.time() < deadline:
                        still_visible = page.call(
                            "()=>{const vis=e=>{const r=e.getBoundingClientRect(),s=getComputedStyle(e);"
                            "return r.width>0&&r.height>0&&s.display!=='none'&&s.visibility!=='hidden';};"
                            "return [...document.querySelectorAll('[role=dialog],.arco-modal,.byte-modal-wrapper')]"
                            ".filter(vis).some(e=>(e.innerText||'').replace(/\\s+/g,'').includes('有刚刚更新的草稿'))||"
                            "[...document.querySelectorAll('body *')].some(e=>vis(e)&&"
                            "(e.innerText||'').replace(/\\s+/g,'').includes('有刚刚更新的草稿'));}",
                            timeout=10)
                        if not still_visible:
                            break
                        time.sleep(0.2)
                    else:
                        raise FlowError("番茄旧草稿恢复提示点击后仍未关闭")
                    time.sleep(0.2)
                if dup_check:
                    # 事前预检：填表阶段平台实时校验（fill 派发过 change 事件，
                    # blur/输入即触发）喊了重名就别点了，点了也必被拒
                    hint = dup_hint(page)
                    if hint:
                        _fail_shot(shot, "step%d-title-dup" % i)
                        raise TitleDupError(hint)
                if st.get("text"):                    # 按按钮文本提交
                    text = str(st["text"])
                    note(i, "提交「%s」（真实鼠标事件）" % text)
                    r = None
                    for _try in range(int(st.get("tries") or 3)):
                        r = page.real_click_text(
                            text, st.get("scope") or "button,a,[class*=btn]",
                            contains=True)
                        if (r or {}).get("ok"):
                            break
                        time.sleep(0.9)
                    if not (r or {}).get("ok"):
                        raise FlowError((r or {}).get("err") or "提交按钮未找到")
                else:
                    note(i, "提交 %s" % st.get("sel") or "")
                    page.wait_for(st["sel"], timeout=8)
                    page.click(st["sel"])
            elif act == "sleep":
                # 固定等待（SPA 水合/动画缓冲）：比 wait 更钝但最可靠
                time.sleep(float(st.get("s") or 1.0))
                note(i, "等待 %.1fs" % float(st.get("s") or 1.0))
            elif act == "wait_gone":
                # 等元素消失（弹层关闭动画对齐）：连发点击被关闭动画的
                # mask 吞掉是多层弹窗流程的经典竞态
                sel = st["sel"]
                note(i, "等待 %s 消失" % sel)
                deadline = time.time() + float(st.get("timeout") or 10)
                while time.time() < deadline:
                    try:
                        if not page.exists(sel, timeout=1.0):
                            break
                    except BrowserError:
                        break
                    time.sleep(0.4)
                else:
                    raise FlowError("等待 %s 消失超时" % sel)
                note(i, "已消失")
            elif act == "js_click":
                # JS el.click()：headless/后台页签下 CDP 真实鼠标事件不触发
                # 平台处理器（番茄编辑器实测：real mouse 点「下一步」无响应，
                # DOM click 反而可靠），两路并存互为兜底。
                text = str(st.get("text") or "")
                for k, v in (values or {}).items():
                    text = text.replace("{%s}" % k, str(v))
                if not text:
                    continue
                scope = str(st.get("scope") or "button")
                contains = bool(st.get("contains", True))
                note(i, "JS点击「%s」" % text)
                r = None
                for _try in range(int(st.get("tries") or 10)):
                    r = page.call(
                        "(t,scope,c,gm)=>{"
                        "const vis=e=>{const r=e.getBoundingClientRect(),s=getComputedStyle(e);"
                        "return r.width>0&&r.height>0&&s.display!=='none'&&s.visibility!=='hidden'"
                        "&&s.pointerEvents!=='none'&&parseFloat(s.opacity||'1')>0;};"
                        # Match dialog containers only. A broad class substring
                        # also catches ordinary descendants such as
                        # .arco-modal-title, which made this skip the actual
                        # editor click even though no blocking dialog existed.
                        "if(gm){var g=[...document.querySelectorAll('.el-dialog,.modal,[role=dialog],"
                        ".arco-modal,.byte-modal-wrapper')]"
                        ".filter(e=>vis(e)&&e.querySelector('button,[role=button]')).find(e=>{"
                        "const x=(e.innerText||'').trim();return x.length>0;});"
                        "if(g)return{ok:false,err:'modal-open'};}"
                        "const els=[...document.querySelectorAll(scope||'button')].filter(vis);"
                        "let cands=els.filter(e=>{const x=(e.innerText||'').trim();"
                        "return x&&(c?x.includes(t):x===t);});"
                        "if(!cands.length)return{ok:false,err:'nf'};"
                        "cands.sort((a,b)=>((a.innerText||'').trim().length)-((b.innerText||'').trim().length));"
                        "cands[0].click();return{ok:true};}",
                        text, scope, contains, bool(st.get("skip_if_modal")), timeout=8)
                    if (r or {}).get("ok"):
                        break
                    if (r or {}).get("err") == "modal-open":
                        note(i, "弹窗已开（真实点击已生效），跳过 JS点击「%s」" % text)
                        r = {"ok": True, "via": "modal-open"}
                        break
                    time.sleep(0.6)
                if not (r or {}).get("ok"):
                    if st.get("optional"):
                        note(i, "「%s」未出现，跳过（optional）" % text)
                        continue
                    raise FlowError("JS点击「%s」失败" % text)
            elif act == "verify":
                # 上线验证：导航到验证页断言文本在场——防「流程完成但平台
                # 静默未发布」的假成功（0 字草稿案）。url/any 支持 values 占位。
                # new_tab=true 时开新页签验证、本页签原地不动：在编辑器页签
                # 里导航会触发 beforeunload 原生确认（渲染挂起+发布请求被
                # 取消，2026-10-08 实案），编辑器流程一律用新页签。
                url = str(st.get("url") or "")
                for k, v in (values or {}).items():
                    url = url.replace("{%s}" % k, str(v))
                note(i, "验证 %s" % url[:60])
                settle = float(st.get("settle") or 4)
                new_tab = bool(st.get("new_tab"))
                marks = [str(m) for m in (st.get("any") or [])]
                last_missing = ""
                for verify_try in range(int(st.get("tries") or 3)):
                    if new_tab:
                        vpage = page.open_new_tab(url)
                        time.sleep(settle)
                    else:
                        page.navigate(url, timeout=30)
                        time.sleep(settle)
                        vpage = page
                    try:
                        body_txt = str(vpage.call(
                            "()=>(document.body.innerText||'')", timeout=15) or "")
                        missing = []
                        for m in marks:
                            mk = m
                            for k, v in (values or {}).items():
                                mk = mk.replace("{%s}" % k, str(v))
                            if mk not in body_txt:
                                missing.append(mk)
                        if not missing:
                            note(i, "验证通过")
                            break
                        last_missing = missing[0]
                    finally:
                        if new_tab:
                            vpage.close_tab()
                    if verify_try + 1 < int(st.get("tries") or 3):
                        time.sleep(float(st.get("retry_settle") or 3))
                else:
                    raise FlowError("上线验证失败：%s 页面上没有「%s」（章节未真正发布）"
                                    % (url[:60], last_missing[:40]))
            elif act == "expect_text":
                # 页面反馈等待（草稿保存凭据，2026-10-10）：轮询页面文本直到
                # 出现任一成功标记（toast/状态字样）——verify 的章节管理页对
                # 「已发布章重复起草」必然假通过，编辑器保存反馈才是本次动作
                # 自己的证据。超时即失败：宁可带 fail 截图拦下，不可把「没
                # 存上」记成成功。标记文案以真机校准为准。
                marks = [str(m) for m in (st.get("any") or [])]
                if not marks:
                    continue
                deadline = time.time() + float(st.get("timeout") or 12)
                hit = ""
                while time.time() < deadline:
                    if st.get("visible_only"):
                        body_txt = str(page.call(
                            "()=>[...document.querySelectorAll('body *')].filter(e=>{"
                            "const r=e.getBoundingClientRect(),s=getComputedStyle(e);"
                            "return r.width>0&&r.height>0&&s.display!=='none'"
                            "&&s.visibility!=='hidden'&&parseFloat(s.opacity||'1')>0;})"
                            ".map(e=>(e.innerText||'').trim()).filter(Boolean).join('\\n')",
                            timeout=15) or "")
                    else:
                        body_txt = str(page.call(
                            "()=>(document.body.innerText||'')", timeout=15) or "")
                    hit = next((m for m in marks if m in body_txt), "")
                    if hit:
                        break
                    time.sleep(0.8)
                if not hit:
                    raise FlowError("未见保存成功反馈（等了 %ds，标记：%s）——"
                                    "若截图里实际已保存成功，请把真实反馈文案"
                                    "校准进流程表"
                                    % (int(float(st.get("timeout") or 12)),
                                       "、".join(marks)[:60]))
                note(i, "反馈命中「%s」" % hit)
            elif act == "expect_text_absent":
                # A real click can be delivered without activating the page's
                # action (for example, a publish-confirm modal remains open).
                # Treat disappearance of the modal text as a required
                # postcondition before proceeding to remote status checks.
                marks = [str(m) for m in (st.get("any") or [])]
                if not marks:
                    continue
                deadline = time.time() + float(st.get("timeout") or 12)
                remaining = marks
                while time.time() < deadline:
                    if st.get("visible_only"):
                        body_txt = str(page.call(
                            "()=>[...document.querySelectorAll('body *')].filter(e=>{"
                            "const r=e.getBoundingClientRect(),s=getComputedStyle(e);"
                            "return r.width>0&&r.height>0&&s.display!=='none'"
                            "&&s.visibility!=='hidden'&&parseFloat(s.opacity||'1')>0;})"
                            ".map(e=>(e.innerText||'').trim()).filter(Boolean).join('\\n')",
                            timeout=15) or "")
                    else:
                        body_txt = str(page.call(
                            "()=>(document.body.innerText||'')", timeout=15) or "")
                    remaining = [m for m in marks if m in body_txt]
                    if not remaining:
                        break
                    time.sleep(0.5)
                if remaining:
                    raise FlowError("点击后页面仍停留在确认界面（%ds 后仍见：%s）"
                                    % (int(float(st.get("timeout") or 12)),
                                       "、".join(remaining)[:80]))
                note(i, "确认界面已关闭")
            elif act == "fanqie_publish":
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
            elif act == "verify_chapter_status":
                # Publication is proven only by this chapter's row in the
                # platform chapter-management page carrying a live/review
                # status. A draft title, cloud-save banner, or generic toast
                # is not evidence that the chapter was submitted.
                url = str(st.get("url") or "")
                for k, v in (values or {}).items():
                    url = url.replace("{%s}" % k, str(v))
                note(i, "核验章节发布状态 %s" % url[:60])
                new_tab = bool(st.get("new_tab"))
                vpage = page.open_new_tab(url) if new_tab else page
                if not new_tab:
                    vpage.navigate(url, timeout=float(st.get("nav_timeout") or 40))
                vpage.wait_for(st.get("sel") or ".el-table__row, tr",
                               timeout=float(st.get("timeout") or 25))
                time.sleep(float(st.get("settle") or 4))
                # Fanqie renders the numeric prefix separately from the
                # semantic title, while Qimao stores chapter_title as the
                # semantic title. Prefer chapter_name when it is available so
                # "第一百零九章 标题" is not compared with "第109章 标题".
                title = str(values.get("chapter_name") or
                            values.get("chapter_title") or "").strip()
                number = str(values.get("chapter_no") or "").strip()
                number_mark = "第%s章" % number if number else ""
                live = [str(x) for x in (st.get("live_status") or
                                          ["待发布", "待审核", "审核中", "已发布"])]
                draft = [str(x) for x in (st.get("draft_status") or ["草稿"])]
                row = ""
                try:
                    if config.get("volume_js"):
                        from types import SimpleNamespace
                        from .chaprows import collect_all_volumes
                        rows, _volumes = collect_all_volumes(
                            vpage, SimpleNamespace(CONFIG=config))
                        row = next((str(r) for r in rows
                                    if number_mark and number_mark in str(r)), "")
                    else:
                        for _page_no in range(int(st.get("max_pages") or 20)):
                            rows = vpage.call(
                                "(sel)=>[...document.querySelectorAll(sel)]"
                                ".filter(e=>{const r=e.getBoundingClientRect();return r.width>0&&r.height>0;})"
                                ".map(e=>(e.innerText||'').replace(/\\s+/g,' ').trim())"
                                ".filter(Boolean)", st.get("row_sel") or ".el-table__row, tr",
                                timeout=15) or []
                            row = next((str(r) for r in rows
                                        if number_mark and number_mark in str(r)), "")
                            if row:
                                break
                            pager_js = str(st.get("pager_next_js") or
                                           config.get("pager_next_js") or "")
                            if not pager_js:
                                break
                            moved = vpage.call(pager_js, timeout=10) or {}
                            if moved.get("done"):
                                break
                            time.sleep(float(st.get("page_settle") or 0.8))
                    if not row:
                        # Fanqie accepts the publish request before its
                        # chapter-management list is updated.  A single
                        # immediate read can therefore report a false
                        # negative (the page still ends at the preceding
                        # chapter).  Refresh the same management URL and
                        # re-read the visible rows for a bounded period
                        # before treating the receipt as a failure.
                        for _verify_try in range(8):
                            time.sleep(3.0)
                            try:
                                vpage.navigate(url, timeout=float(
                                    st.get("nav_timeout") or 40))
                                vpage.wait_for(st.get("sel") or
                                               ".el-table__row, tr",
                                               timeout=float(st.get("timeout") or 25))
                                time.sleep(float(st.get("page_settle") or 1))
                                rows = vpage.call(
                                    "(sel)=>[...document.querySelectorAll(sel)]"
                                    ".filter(e=>{const r=e.getBoundingClientRect();"
                                    "return r.width>0&&r.height>0;})"
                                    ".map(e=>(e.innerText||'').replace(/\\s+/g,' ').trim())"
                                    ".filter(Boolean)",
                                    st.get("row_sel") or ".el-table__row, tr",
                                    timeout=15) or []
                                row = next((str(r) for r in rows
                                            if number_mark and number_mark in str(r)), "")
                                if row:
                                    break
                            except Exception:
                                continue
                    if not row:
                        raise FlowError("发布状态核验失败：章节管理页找不到本章「%s」"
                                        % (number_mark or title))
                    if title and title not in row:
                        raise FlowError("发布状态核验失败：第%s章标题不一致，远端行：%s"
                                        % (number, row[:120]))
                    if any(mark in row for mark in draft):
                        raise FlowError("发布状态核验失败：本章仍是草稿：%s" % row[:120])
                    hit = next((mark for mark in live if mark in row), "")
                    if not hit:
                        raise FlowError("发布状态核验失败：本章行未见待发布/待审核/审核中/已发布状态：%s"
                                        % row[:120])
                    note(i, "章节管理行核验通过（%s）：%s" % (hit, row[:100]))
                finally:
                    if new_tab:
                        vpage.close_tab()
            elif act == "volume":
                # 按本地卷计划在平台选择目标卷；如果缺失则创建，再点选它。
                # 仅创建并关闭弹窗会保留平台当前卷，后续新章节就会落错卷。
                # 无目标卷名或平台无卷选择器（optional）时跳过。
                from .volumes import norm_volume_name
                target = str(values.get(st.get("key") or "volume_name") or "").strip()
                open_sel = str(st.get("open_sel") or "")
                modal_sel = str(st.get("modal_sel") or "")
                item_sel = str(st.get("item_sel") or "")
                settle = float(st.get("settle") or 1.2)
                if not target:
                    note(i, "无分卷目标，跳过")
                    continue
                if not (open_sel and modal_sel and item_sel):
                    raise FlowError("volume 步骤缺选择器配置（open_sel/modal_sel/item_sel）")
                hdr = ""
                r = {"ok": False}
                for _try in range(int(st.get("tries") or 6)):
                    hdr = str(page.call("(s)=>{var v=document.querySelector(s);"
                                        "return v?(v.innerText||'').trim():'';}",
                                        open_sel, timeout=8) or "").strip()
                    if hdr:
                        break
                    time.sleep(1.0)     # 编辑器头部卷名渲染慢，别一次就放弃
                if not hdr:
                    if st.get("optional"):
                        note(i, "编辑器无分卷选择器，跳过")
                        continue
                    raise FlowError("分卷选择器未出现：%s" % open_sel)
                if norm_volume_name(hdr) == norm_volume_name(target):
                    note(i, "当前卷已是「%s」" % hdr)
                    continue

                def vol_items():
                    out = page.call("(s)=>[...document.querySelectorAll(s)]"
                                    ".filter(e=>e.getBoundingClientRect().width>0)"
                                    ".map(e=>(e.innerText||'').trim())",
                                    item_sel, timeout=8)
                    return [str(x).strip() for x in (out or []) if str(x or "").strip()]

                r = page.call("(s)=>{var v=document.querySelector(s);"
                              "if(!v)return{ok:false};v.click();return{ok:true};}",
                              open_sel, timeout=8)
                if not (r or {}).get("ok"):
                    if st.get("optional"):
                        note(i, "编辑器无分卷选择器，跳过")
                        continue
                    raise FlowError("分卷选择器点不开：%s" % open_sel)
                time.sleep(settle)
                def pick_hit():
                    return next((t for t in vol_items()
                                 if norm_volume_name(t) == norm_volume_name(target)), "")

                hit = pick_hit()
                if not hit:
                    add_sel = str(st.get("add_sel") or "")
                    if not add_sel:
                        raise FlowError("平台没有目标分卷「%s」且流程未配新建分卷" % target)
                    r = page.call("(s)=>{var e=document.querySelector(s);"
                                  "if(!e)return{ok:false};e.click();return{ok:true};}",
                                  add_sel, timeout=8)
                    if not (r or {}).get("ok"):
                        raise FlowError("「新建分卷」点不到：%s" % add_sel)
                    time.sleep(settle)
                    rr = page.call("(m,t,inp,cs)=>{var mm=document.querySelector(m);"
                                   "if(!mm)return{ok:false,err:'nomodal'};"
                                   "var el=mm.querySelector(inp);"
                                   "if(!el)return{ok:false,err:'noinput'};"
                                   "var d=Object.getOwnPropertyDescriptor("
                                   "HTMLInputElement.prototype,'value');"
                                   "d.set.call(el,t);"
                                   "el.dispatchEvent(new Event('input',{bubbles:true}));"
                                   "var c=mm.querySelector(cs);"
                                   "if(!c)return{ok:false,err:'noconfirm'};"
                                   "c.click();return{ok:true};}",
                                   modal_sel, target,
                                   str(st.get("input_sel") or "input"),
                                   str(st.get("confirm_sel") or "i.tomato-confirm"),
                                   timeout=8)
                    if not (rr or {}).get("ok"):
                        raise FlowError("新建分卷「%s」失败：%s"
                                        % (target, (rr or {}).get("err")))
                    time.sleep(settle)
                    hit = pick_hit()
                    if not hit:
                        raise FlowError("新建分卷后列表里没有「%s」——常见原因："
                                        "平台不允许连续两个无章节的空卷"
                                        "（code -4054），等上一卷有章节再发"
                                        % target)
                    known = values.get("_volumes")
                    if isinstance(known, list) and hit not in known:
                        known.append(hit)
                    note(i, "已新建分卷「%s」" % hit)

                # 点选目标卷后再确认，确保新章节会归入对应卷。只在弹窗内
                # 查找条目，避免同名文本误点到页面其它位置。
                rr = page.call("(m,s,t)=>{var mm=document.querySelector(m);"
                               "if(!mm)return{ok:false,err:'nomodal'};"
                               "var els=[...mm.querySelectorAll(s)];"
                               "var hit=els.find(e=>((e.innerText||'').trim())===t);"
                               "if(!hit)return{ok:false,err:'noitem'};"
                               "hit.click();return{ok:true};}",
                               modal_sel, item_sel, hit, timeout=8)
                if not (rr or {}).get("ok"):
                    raise FlowError("分卷「%s」点选失败：%s"
                                    % (hit, (rr or {}).get("err")))
                time.sleep(0.5)
                rr = page.call("(m)=>{var mm=document.querySelector(m);"
                               "if(!mm)return{ok:false,err:'nomodal'};"
                               "var b=mm.querySelectorAll('button');"
                               "for (var i=0;i<b.length;i++){"
                               " if((b[i].innerText||'').trim()==='确定'){"
                               "  b[i].click();return{ok:true};}}"
                               "return{ok:false,err:'noconfirm'};}",
                               modal_sel, timeout=8)
                if not (rr or {}).get("ok"):
                    raise FlowError("分卷「%s」已点选，但弹窗「确定」失败：%s"
                                    % (hit, (rr or {}).get("err")))
                time.sleep(settle)
                selected = str(page.call("(s)=>{var v=document.querySelector(s);"
                                         "return v?(v.innerText||'').trim():'';}",
                                         open_sel, timeout=8) or "").strip()
                if norm_volume_name(selected) != norm_volume_name(target):
                    raise FlowError("目标卷选择未生效：要选「%s」，当前显示「%s」"
                                    % (target, selected or "（空）"))
                note(i, "分卷已选「%s」" % selected)
            elif act == "url_any":
                u = str(page.url() or "")
                marks = st.get("any") or []
                if not any(m in u for m in marks):
                    hint = ""
                    try:
                        hint = str(page.call(_page_error_text_js(), timeout=5) or "")
                    except Exception:
                        pass
                    msg = "当前页面 %s 不含预期标记 %s（可能未登录或改版）" % (u[:90], marks)
                    if hint:
                        msg += "；页面提示：%s" % hint[:150]
                    else:
                        msg += "（常见原因：表单校验未过，如简介字数不足）"
                    raise FlowError(msg)
                note(i, "页面标记校验通过（%s）" % u[:80])
            else:
                raise FlowError("未知步骤类型：%s" % act)
        except FlowError:
            _fail_shot(shot, "step%d-%s" % (i, act))
            raise
        except BrowserError as e:
            _fail_shot(shot, "step%d-%s" % (i, act))
            raise FlowError("步骤%d（%s）失败：%s" % (i, act, e))
        except KeyError as e:
            raise FlowError("步骤%d 缺少字段 %s" % (i, e))
    return len(steps)


def _fail_shot(shot, name):
    try:
        if shot:
            shot(name)
    except Exception:
        pass
