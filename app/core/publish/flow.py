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
  click_match {any:[..], max_len}  关键词选块（点同时含所有关键词的最小元素）
  submit    {sel}                 终步：auto_submit=false 时跳过（留给人工确认），
                                  跳过时补一张 ready-*.png，用户在浏览器窗口里自查提交
  volume   {key, open_sel, modal_sel, item_sel, add_sel, input_sel,
            confirm_sel, settle}  发章带卷：选平台已有卷，缺则新建（自动分卷）
  verify   {url, any, settle}     上线验证：导航后断言标记在场
  url_any  {any: [..]}            断言当前 URL 含任一标记（登录跳转检测）
"""
from __future__ import annotations

import json
import re
import time

from .browser import BrowserError


class FlowError(Exception):
    """流程失败：message 面向用户（含步骤序号与截图路径线索）。"""


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
    # 点同时包含所有关键词的最小可见元素（长度最小者=最内层卡片）
    return ("(keys,maxLen)=>{"
            "const hit=[...document.querySelectorAll('div,li,section,label,span,a')].filter(e=>{"
            "const x=(e.innerText||'').trim();"
            "return x && x.length<=maxLen && keys.every(k=>x.includes(k));});"
            "if(!hit.length)return{ok:false,err:'找不到同时含 '+keys.join('+')+' 的元素'};"
            "hit.sort((a,b)=>(a.innerText||'').length-(b.innerText||'').length);"
            "let el=hit[0];"
            "const act=el.closest('a,button,[role=button],[class*=btn]')||el;"
            "act.scrollIntoView({block:'center'});act.click();"
            "return{ok:true,tag:act.tagName};}")


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
    # arco 系 label 挂 checked 类、原生 input 挂 checked 属性，双信号任一即算
    return ("(t,scope)=>{"
            "const els=[...document.querySelectorAll("
            "scope||'label,[class*=radio],[class*=checkbox]')]"
            ".filter(e=>e.getBoundingClientRect().width>0);"
            "const el=els.find(e=>((e.innerText||'').trim()).includes(t));"
            "if(!el)return{ok:false,err:'nf'};"
            "const box=el.closest('[class*=radio],[class*=checkbox]')||el;"
            "const inp=box.querySelector('input[type=radio],input[type=checkbox]')||"
            "el.querySelector('input[type=radio],input[type=checkbox]');"
            "const ck=(inp&&inp.checked)||/checked|active/.test(box.className||'')"
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


def run_flow(page, steps, values=None, config=None, auto_submit=False,
             shot=None, log=None, dup_check=False):
    """跑一个流程。values：fill 取值字典；config：平台 URL 等占位符来源。

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
                for _try in range(tries):
                    r = page.real_click_text(text, st.get("scope") or
                                             "a,button,[class*=btn]",
                                             y_min=st.get("y_min"),
                                             y_max=st.get("y_max"))
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
                        "const cards=[...document.querySelectorAll('div,li,section,tr')].filter(e=>{"
                        "const x=(e.innerText||'').trim();"
                        "return x.includes(sc)&&x.length<600&&e.getBoundingClientRect().width>0;});"
                        "if(!cards.length)return{ok:false,err:'找不到含'+sc+'的卡片'};"
                        "cards.sort((a,b)=>(b.innerText||'').length-(a.innerText||'').length);"
                        "const card=cards[0];"
                        "const btns=[...card.querySelectorAll('a,button,[role=button],[class*=btn],span')].filter(e=>{"
                        "const x=(e.innerText||'').trim();return x===t||x.includes(t)&&x.length<=t.length+6;});"
                        "if(!btns.length)return{ok:false,err:'卡片内没有'+t};"
                        "btns.sort((a,b)=>(a.innerText||'').length-(b.innerText||'').length);"
                        "let el=btns[0];"
                        "const act=el.closest('a,button,[role=button],[class*=btn]')||el;"
                        "act.scrollIntoView({block:'center'});"
                        "if(real){const rc=act.getBoundingClientRect();"
                        " const px=document.elementFromPoint(Math.round(rc.x+rc.width/2),Math.round(rc.y+rc.height/2));"
                        " if(!px||(!act.contains(px)&&!px.contains(act)))return{ok:false,err:'blocked'};"
                        " return{ok:true,x:Math.round(rc.x+rc.width/2),y:Math.round(rc.y+rc.height/2)};}"
                        "act.click();"
                        "return{ok:true};}", scope_t, text, bool(st.get("real")))
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
                # 隐藏 input（Element UI）点不到——点它的最近可见 label 祖先。
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
                r = page.call(
                    "(v)=>{const r=[...document.querySelectorAll('input[type=radio]')]"
                    ".find(x=>x.value===v);if(!r)return{ok:false,err:'radio v='+v};"
                    "const lab=r.closest('label')||(r.closest('.el-radio')||{}).firstElementChild||r;"
                    "const t=lab.matches('label,.el-radio')?lab:(r.parentElement||r);"
                    "t.scrollIntoView({block:'center'});t.click();return{ok:true};}", str(val))
                if not (r or {}).get("ok"):
                    raise FlowError((r or {}).get("err") or "单选点击失败")
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
                        "if(gm){var g=[...document.querySelectorAll('[class*=modal]')]"
                        ".find(e=>e.getBoundingClientRect().width>0);"
                        "if(g)return{ok:false,err:'modal-open'};}"
                        "const vis=e=>e.getBoundingClientRect().width>0;"
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
                if new_tab:
                    vpage = page.open_new_tab(url)
                    time.sleep(settle)
                else:
                    page.navigate(url, timeout=30)
                    time.sleep(settle)
                    vpage = page
                try:
                    marks = [str(m) for m in (st.get("any") or [])]
                    body_txt = str(vpage.call(
                        "()=>(document.body.innerText||'')", timeout=15) or "")
                    for m in marks:
                        mk = m
                        for k, v in (values or {}).items():
                            mk = mk.replace("{%s}" % k, str(v))
                        if mk not in body_txt:
                            raise FlowError("上线验证失败：%s 页面上没有「%s」（章节未真正发布）"
                                            % (url[:60], mk[:40]))
                    note(i, "验证通过")
                finally:
                    if new_tab:
                        vpage.close_tab()
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
            elif act == "volume":
                # 发章带卷（2026-10-08 批量自动发布·自动分卷）：确保目标卷
                # 在平台上存在（缺则走「新建分卷」）。平台新章按卷自动归类、
                # 不提供章节切卷入口（编辑器卷弹窗实测只能增删改卷，真机
                # code -4054 还不允许连续两个无章节空卷）——所以本步骤只
                # 「备好卷」，不假装能切卷。选择器全部数据驱动；无目标卷名
                # 或编辑器没有卷选择器（optional）时跳过。
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
                exists = any(norm_volume_name(t) == norm_volume_name(target)
                             for t in vol_items())
                if not exists:
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
                    exists = any(norm_volume_name(t) == norm_volume_name(target)
                                 for t in vol_items())
                    if not exists:
                        raise FlowError("新建分卷后列表里没有「%s」——常见原因："
                                        "平台不允许连续两个无章节的空卷"
                                        "（code -4054），等上一卷有章节再发"
                                        % target)
                    known = values.get("_volumes")
                    if isinstance(known, list):
                        disp = next((t for t in vol_items()
                                     if norm_volume_name(t) == norm_volume_name(target)), "")
                        if disp and disp not in known:
                            known.append(disp)
                    note(i, "已新建分卷「%s」" % target)
                else:
                    note(i, "分卷「%s」已存在" % target)
                # 收弹窗（取消=不动任何卷；确定会另起新草稿，都不影响归属——
                # 新章由平台按卷自动归类）
                page.call("(m)=>{var mm=document.querySelector(m);if(!mm)return{ok:false};"
                          "var b=mm.querySelectorAll('button');"
                          "for (var i=0;i<b.length;i++){"
                          " if((b[i].innerText||'').trim()==='取消'){b[i].click();return{ok:true};}}"
                          "return{ok:false};}", modal_sel, timeout=8)
                time.sleep(0.6)
                note(i, "分卷「%s」已就绪（平台按卷自动归类新章，当前卷 %s）"
                        % (target, hdr))
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
