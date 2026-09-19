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
  submit    {sel}                 终步：auto_submit=false 时跳过（留给人工确认），
                                  跳过时补一张 ready-*.png，用户在浏览器窗口里自查提交
  url_any   {any: [..]}           断言当前 URL 含任一标记，不含则报错（登录跳转检测）
"""
from __future__ import annotations

import json
import time

from .browser import BrowserError


class FlowError(Exception):
    """流程失败：message 面向用户（含步骤序号与截图路径线索）。"""


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


def run_flow(page, steps, values=None, config=None, auto_submit=False,
             shot=None, log=None):
    """跑一个流程。values：fill 取值字典；config：平台 URL 等占位符来源。

    shot(name) → 截图落盘函数（manager 注入，路径含任务/平台维度）；
    log(line)  → 步骤日志函数（进发布记录的 log 字段）。返回执行到的步数。"""
    values = values or {}
    config = config or {}
    log = log or (lambda s: None)

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
                note(i, "打开 %s" % url)
                page.navigate(url)
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
                for k, v in (values or {}).items():
                    text = text.replace("{%s}" % k, str(v))
                if not text:
                    continue
                note(i, "真实点击「%s」" % text)
                r = None
                for _try in range(int(st.get("tries") or 25)):
                    r = page.real_click_text(text, st.get("scope") or
                                             "a,button,[class*=btn]")
                    if (r or {}).get("ok"):
                        break
                    time.sleep(0.4)
                if not (r or {}).get("ok"):
                    if st.get("optional"):
                        note(i, "「%s」未出现，跳过（optional）" % text)
                        continue
                    raise FlowError((r or {}).get("err") or text)
            elif act == "click_in":
                # 先按 scope_text 定位容器（如书卡），再在容器内点 text 按钮。
                # 解决「按钮与书名同卡片但不在彼此祖先链」的组合定位。
                scope_t = str(st.get("scope_text") or "")
                text = str(st.get("text") or "")
                for k, v in (values or {}).items():
                    scope_t = scope_t.replace("{%s}" % k, str(v))
                    text = text.replace("{%s}" % k, str(v))
                note(i, "在含「%s」的卡片内点「%s」" % (scope_t, text))
                r = None
                for _try in range(10):            # 表格异步渲染：重试窗口加长
                    r = page.call(
                        "(sc,t)=>{"
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
                        "act.scrollIntoView({block:'center'});act.click();"
                        "return{ok:true};}", scope_t, text)
                    if (r or {}).get("ok"):
                        break
                    time.sleep(0.9)
                if not (r or {}).get("ok"):
                    raise FlowError((r or {}).get("err") or "click_in 失败")
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
                # 项为 [组名, 标签] 时先点左侧组名切换（组标签懒渲染）再点标签；
                # 纯字符串直接点。弹层打开由前置 click_text「添加标签」负责。
                tags = values.get("_tags") or []
                if not tags:
                    note(i, "无标签可点，跳过")
                    continue
                scope = st.get("scope") or "span,li,label,[class*=dialog] *,[class*=popper] *"
                note(i, "点选标签 %d 项" % len(tags))
                for item in tags:
                    grp, tg = ("", str(item)) if isinstance(item, (str, int)) \
                        else (str(item[0] or ""), str(item[1] or ""))
                    if not tg:
                        continue
                    if grp:                             # 切到目标组
                        r0 = None
                        for _try in range(2):
                            r0 = page.call(_click_text_js(), grp, scope, True)
                            if (r0 or {}).get("ok"):
                                break
                            time.sleep(0.6)
                        if not (r0 or {}).get("ok"):
                            raise FlowError("标签组「%s」切换失败" % grp)
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
                if not auto_submit:
                    note(i, "已填好未提交——请人工检查后提交（auto_submit=false）")
                    if shot:
                        shot("ready-manual-submit")
                    return i + 1
                if st.get("text"):                    # 按按钮文本提交
                    text = str(st["text"])
                    note(i, "提交「%s」（真实鼠标事件）" % text)
                    r = None
                    for _try in range(3):
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
            elif act == "verify":
                # 上线验证：导航到验证页断言文本在场——防「流程完成但平台
                # 静默未发布」的假成功（0 字草稿案）。url/any 支持 values 占位。
                url = str(st.get("url") or "")
                for k, v in (values or {}).items():
                    url = url.replace("{%s}" % k, str(v))
                note(i, "验证 %s" % url[:60])
                page.navigate(url, timeout=30)
                time.sleep(float(st.get("settle") or 4))
                marks = [str(m) for m in (st.get("any") or [])]
                body_txt = str(page.call(
                    "()=>(document.body.innerText||'')", timeout=10) or "")
                for m in marks:
                    mk = m
                    for k, v in (values or {}).items():
                        mk = mk.replace("{%s}" % k, str(v))
                    if mk not in body_txt:
                        raise FlowError("上线验证失败：%s 页面上没有「%s」（章节未真正发布）"
                                        % (url[:60], mk[:40]))
                note(i, "验证通过")
            elif act == "url_any":
                u = str(page.url() or "")
                marks = st.get("any") or []
                if not any(m in u for m in marks):
                    raise FlowError("当前页面 %s 不含预期标记 %s（可能未登录或改版）"
                                    % (u[:90], marks))
                note(i, "页面标记校验通过")
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
