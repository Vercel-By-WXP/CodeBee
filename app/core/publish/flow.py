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
  verify   {url, any, any_of, settle}
                                  上线验证：导航后断言 any 标记全部在场、
                                  any_of 标记至少一个在场（状态字样会随
                                  平台措辞变的凭据用 any_of）
  url_any  {any: [..]}            断言当前 URL 含任一标记（登录跳转检测）
  set_file {sel, key}             给隐藏的 file input 挂本地文件（封面上传）
  select_item {open_sel, item_sel, any|any_key, settle}
                                  开自绘下拉，再按候选文本点选一项
  check    {sel, checked_sel, confirm, name}
                                  勾选协议复选框；授权说明弹窗自动点确认
  dismiss_stale                   关掉番茄「有刚刚更新的草稿」恢复提示
  fanqie_short_trial              短故事试读比例：去设置→30%（未设会拦下一步）
  fanqie_short_publish            短故事发布确认链：继续发布→确定→发布成功
  qimao_short_publish             七猫短故事确认链：声明→确认发布（真实鼠标）

所有步骤都支持 if_key：values[if_key] 为空时整步跳过（可缺省的封面等）。
"""
from __future__ import annotations

import json
import re
import time

from .browser import BrowserError

from .flow_fanqie import (run_fanqie_publish, run_fanqie_short_publish,
                          run_fanqie_short_trial)
from .flow_qimao import run_qimao_short_publish
from .flow_helpers import (  # noqa: F401 - 旧调用面再导出
    DUP_HINT_WORDS, FlowError, TitleDupError, _checked_js, _click_match_js,
    _click_text_js, _dup_probe_js, _fail_shot, _fanqie_active_dialog,
    _fanqie_schedule_conflict, _fanqie_submission_limit_changed, _fill_label_js,
    _has_new_feedback, _page_error_text_js, _tag_click_js, _tag_sections_js,
    _tags_select_grouped, dup_hint,
)

_STALE_DRAFT_MARK = "有刚刚更新的草稿"


def _dismiss_fanqie_stale_draft(page, timeout=6.0):
    """关掉番茄「有刚刚更新的草稿」恢复提示（点放弃），返回是否出现过。

    这条提示与本次要填的稿无关；点「继续编辑」会把旧稿恢复成当前内容，
    随后提交的就是旧稿。必须在填表前放弃。
    """
    seen = False
    last_error = ""
    deadline = time.time() + timeout
    while time.time() < deadline:
        try:
            r = page.call(
                "(mark,label)=>{const vis=e=>{const r=e.getBoundingClientRect(),s=getComputedStyle(e);"
                "return r.width>0&&r.height>0&&s.display!=='none'&&s.visibility!=='hidden'"
                "&&s.pointerEvents!=='none'&&parseFloat(s.opacity||'1')>0;};"
                "const norm=x=>String(x||'').replace(/\\s+/g,'').trim();"
                "const ds=[...document.querySelectorAll('body *')].filter(e=>vis(e)&&"
                "norm(e.innerText).includes(mark));"
                "ds.sort((a,b)=>norm(a.innerText).length-norm(b.innerText).length);"
                "const d=ds[0];if(!d)return{ok:false,absent:true};"
                "const b=[...d.querySelectorAll('button,[role=button],a')].find(e=>"
                "vis(e)&&norm(e.innerText)===norm(label));"
                "if(!b)return{ok:false,err:'button'};"
                "const r=b.getBoundingClientRect(),x=Math.round(r.x+r.width/2),"
                "y=Math.round(r.y+r.height/2),hit=document.elementFromPoint(x,y);"
                "if(!hit||(hit!==b&&!b.contains(hit)&&!hit.contains(b)))"
                "return{ok:false,err:'blocked'};"
                "return{ok:true,x:x,y:y};}", _STALE_DRAFT_MARK, "放弃",
                timeout=10) or {}
        except BrowserError as exc:
            last_error = str(exc)[:160]
            time.sleep(0.25)
            continue
        if r.get("absent"):
            return seen
        seen = True
        if r.get("ok"):
            for kind in ("mousePressed", "mouseReleased"):
                page.send("Input.dispatchMouseEvent",
                          {"type": kind, "x": r["x"], "y": r["y"],
                           "button": "left", "clickCount": 1}, timeout=8.0)
        else:
            try:
                fallback = page.real_click_text(
                    "放弃", "button,[role=button],a", contains=False)
                if not (fallback or {}).get("ok"):
                    last_error = str(r.get("err") or "放弃按钮不可点击")
            except BrowserError as exc:
                last_error = str(exc)[:160]
        time.sleep(0.8)
    if seen:
        raise FlowError("番茄旧草稿恢复提示仍未关闭，停止填稿以免旧稿覆盖本次内容：%s"
                        % (last_error or "点击「放弃」后提示仍存在"))
    if last_error:
        raise FlowError("无法确认番茄旧草稿恢复提示状态，停止填稿：%s" % last_error)
    return False



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
            # 条件步：缺省值（无封面/无分类）时整步跳过，流程表不写两套分支
            if st.get("if_key") and values.get(str(st["if_key"])) in (
                    None, "", [], {}):
                note(i, "缺 %s，跳过 %s" % (st["if_key"], act))
                continue
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
                for _try in range(int(st.get("tries") or 3)):   # SPA 渲染慢：找不到先等再试
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
                # alt：状态相关的等价按钮（已有草稿时「存为草稿」变
                # 「更新草稿」，真机 2026-10-23 实案）。
                alts = [str(a) for a in (st.get("alt") or [])]
                if text == "存草稿":
                    alts = ["存为草稿"] + alts
                click_texts = [text] + alts
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
                scope_sel = str(st.get("scope_sel") or "").strip()
                base_js = ("(v,sc,ss)=>{let root=null;"
                           "if(ss){root=document.querySelector(ss);"
                           "if(!root||!root.querySelector('input[type=radio]'))"
                           "return{ok:false,err:'scope'};}"
                           "else if(sc){const els=[...document.querySelectorAll"
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
                    r = page.call(base_js, str(val), scope_text, scope_sel)
                    if not (r or {}).get("ok"):
                        raise FlowError("单选 %s=%s 目标未找到（scope_text=%s，"
                                        "scope_sel=%s）"
                                        % (key, want, scope_text or "无",
                                           scope_sel or "无"))
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
            elif act == "set_file":
                # 隐藏 file input 挂文件：封面/图片上传控件由平台组件托管，
                # 零尺寸、点不到——DOM.setFileInputFiles 直接赋 FileList。
                key = str(st.get("key") or "")
                path = str(st.get("path") or (values.get(key) if key else "") or "")
                if not path:
                    if st.get("optional"):
                        note(i, "无文件（%s），跳过" % (key or "path"))
                        continue
                    raise FlowError("set_file 缺少文件路径（key=%s）" % (key or "无"))
                note(i, "挂载文件 %s" % path.replace("\\", "/").rsplit("/", 1)[-1])
                page.wait_for(st["sel"], timeout=float(st.get("timeout") or 20))
                page.set_file(st["sel"], path)
                time.sleep(float(st.get("settle") or 0.8))
            elif act == "select_item":
                # 自绘下拉：先开触发器，再在弹层里按候选文本点一项。
                # 候选来自 any（静态）与 any_key（values 里的列表，如
                # _categories）；都匹配不上时落首个可见项（分类"其他"兜底）。
                open_sel = str(st.get("open_sel") or "")
                item_sel = str(st.get("item_sel") or "")
                cands = [str(c) for c in (st.get("any") or [])]
                if st.get("any_key"):
                    cands += [str(c) for c in (values.get(st["any_key"]) or [])]
                for k, v in (values or {}).items():
                    cands = [c.replace("{%s}" % k, str(v)) for c in cands]
                cands = [c.strip() for c in cands if str(c).strip()]
                note(i, "选择「%s」：候选 %s"
                     % (st.get("name") or item_sel, "／".join(cands[:6]) or "首个可见项"))

                def _current():
                    return str(page.call(
                        "(s)=>{const e=document.querySelector(s);"
                        "return e?(e.innerText||'').trim():'';}", open_sel,
                        timeout=8) or "")

                cur = _current()
                hit_cur = next((c for c in cands if c and c in cur), "")
                if hit_cur and st.get("skip_if_selected", True):
                    note(i, "已选中「%s」，跳过" % hit_cur)
                    continue
                page.wait_for(open_sel, timeout=8)
                pos = page.call(
                    "(s)=>{const e=document.querySelector(s);if(!e)return null;"
                    "e.scrollIntoView({block:'center'});"
                    "const rc=e.getBoundingClientRect();"
                    "const x=Math.round(rc.x+rc.width/2),y=Math.round(rc.y+rc.height/2);"
                    "const px=document.elementFromPoint(x,y);"
                    "if(!px||(px!==e&&!e.contains(px)&&!px.contains(e)))return null;"
                    "return{x:x,y:y};}", open_sel, timeout=8) or {}
                if pos.get("x") is not None:        # Arco 触发器认真实指针
                    for kind in ("mousePressed", "mouseReleased"):
                        page.send("Input.dispatchMouseEvent",
                                  {"type": kind, "x": pos["x"], "y": pos["y"],
                                   "button": "left", "clickCount": 1}, timeout=8.0)
                else:
                    page.click(open_sel)             # 触发器被遮挡时退合成点击
                time.sleep(float(st.get("settle") or 1.0))
                picked = ""
                for _try in range(int(st.get("tries") or 6)):
                    r = page.call(
                        "(sel,cands)=>{const vis=e=>{const r=e.getBoundingClientRect(),s=getComputedStyle(e);"
                        "return r.width>0&&r.height>0&&s.display!=='none'&&s.visibility!=='hidden'"
                        "&&parseFloat(s.opacity||'1')>0;};"
                        "const norm=x=>String(x||'').replace(/\\s+/g,'').trim();"
                        "const items=[...document.querySelectorAll(sel)].filter(vis);"
                        "if(!items.length)return{ok:false,err:'nf'};"
                        "let hit=null;"
                        "for(const c of cands){const t=norm(c);"
                        "hit=items.find(e=>norm(e.innerText)===t);if(hit)break;}"
                        "if(!hit)for(const c of cands){const t=norm(c);"
                        "hit=items.find(e=>norm(e.innerText).includes(t));if(hit)break;}"
                        "if(!hit&&cands.length)return{ok:false,err:'no-match',"
                        "ts:items.slice(0,8).map(e=>norm(e.innerText))};"
                        "if(!hit)hit=items[0];"
                        "hit.scrollIntoView({block:'center'});"
                        "const rc=hit.getBoundingClientRect();"
                        "const x=Math.round(rc.x+rc.width/2),y=Math.round(rc.y+rc.height/2);"
                        "const px=document.elementFromPoint(x,y);"
                        "if(!px||(px!==hit&&!hit.contains(px)&&!px.contains(hit)))"
                        "return{ok:false,err:'blocked'};"
                        "return{ok:true,x:x,y:y,t:norm(hit.innerText)};}",
                        item_sel, cands, timeout=12) or {}
                    if not (r or {}).get("ok"):
                        time.sleep(0.8)
                        try:                       # 弹层被点关：重开再找
                            if not page.exists(item_sel, timeout=0.5):
                                page.click(open_sel)
                                time.sleep(0.8)
                        except BrowserError:
                            pass
                        continue
                    for kind in ("mousePressed", "mouseReleased"):
                        page.send("Input.dispatchMouseEvent",
                                  {"type": kind, "x": r["x"], "y": r["y"],
                                   "button": "left", "clickCount": 1}, timeout=8.0)
                    picked = str(r.get("t") or "")
                    break
                if not picked:
                    if st.get("optional"):
                        note(i, "候选都未出现，跳过（optional）")
                        continue
                    raise FlowError("下拉里找不到可选项（候选 %s）"
                                    % "／".join(cands[:6]))
                time.sleep(float(st.get("after") or 0.8))
                cur = _current()
                for _w in range(3):                    # 芯片渲染慢：再读两次
                    if picked in cur:
                        break
                    time.sleep(0.6)
                    cur = _current()
                if picked and picked not in cur:
                    if st.get("optional"):
                        note(i, "选中态未回显「%s」，继续（optional）" % picked)
                        continue
                    raise FlowError("选择未生效：期望「%s」，当前显示「%s」"
                                    % (picked, cur[:40] or "（空）"))
                note(i, "已选「%s」" % picked)
                if st.get("close", True):        # 关掉弹层，免得挡住后续点击
                    try:
                        for kind in ("keyDown", "keyUp"):
                            page.send("Input.dispatchKeyEvent", {
                                "type": kind, "key": "Escape", "code": "Escape",
                                "windowsVirtualKeyCode": 27,
                                "nativeVirtualKeyCode": 27}, timeout=8.0)
                        time.sleep(0.4)
                    except BrowserError:
                        pass
            elif act == "check":
                # 协议勾选：ARCO 复选框的真实命中面是 .arco-checkbox-mask；
                # 首次勾选会先弹授权说明，点「我已阅读并同意」后才落 checked。
                sel = str(st.get("sel") or "")
                checked_sel = str(st.get("checked_sel") or "")
                confirm = str(st.get("confirm") or "")
                note(i, "勾选协议「%s」" % (st.get("name") or sel))

                def _is_checked():
                    if not checked_sel:
                        return False
                    try:
                        return bool(page.call("(s)=>!!document.querySelector(s)",
                                              checked_sel, timeout=6))
                    except BrowserError:
                        return False

                ok = False
                for _try in range(int(st.get("tries") or 5)):
                    if _is_checked():
                        ok = True
                        break
                    pos = page.call(
                        "(s)=>{const vis=e=>{const r=e.getBoundingClientRect(),s=getComputedStyle(e);"
                        "return r.width>0&&r.height>0&&s.display!=='none'&&s.visibility!=='hidden'"
                        "&&s.pointerEvents!=='none'&&parseFloat(s.opacity||'1')>0;};"
                        "const l=document.querySelector(s);"
                        "if(!l||!vis(l))return{ok:false,err:'nf'};"
                        "const m=l.querySelector('.arco-checkbox-mask')||l;"
                        "m.scrollIntoView({block:'center'});"
                        "const rc=m.getBoundingClientRect();"
                        "const x=Math.round(rc.x+rc.width/2),y=Math.round(rc.y+rc.height/2);"
                        "const px=document.elementFromPoint(x,y);"
                        "if(!px||(px!==m&&!m.contains(px)&&!px.contains(m)))"
                        "return{ok:false,err:'blocked'};"
                        "return{ok:true,x:x,y:y};}", sel, timeout=10) or {}
                    if pos.get("ok"):
                        for kind in ("mousePressed", "mouseReleased"):
                            page.send("Input.dispatchMouseEvent",
                                      {"type": kind, "x": pos["x"],
                                       "y": pos["y"], "button": "left",
                                       "clickCount": 1}, timeout=8.0)
                        time.sleep(0.8)
                    if _is_checked():
                        ok = True
                        break
                    if confirm:
                        r = page.real_click_text(
                            confirm, st.get("scope")
                            or "button,[role=button],a,span", contains=True)
                        if (r or {}).get("ok"):
                            time.sleep(1.0)
                            if _is_checked():
                                ok = True
                                break
                    time.sleep(0.4)
                if not ok:
                    if st.get("optional"):
                        note(i, "协议勾选未生效，继续（optional）")
                        continue
                    raise FlowError("协议勾选失败（%s）：复选框或授权说明弹窗未响应"
                                    % sel)
                note(i, "协议已勾选")
            elif act == "dismiss_stale":
                seen = _dismiss_fanqie_stale_draft(
                    page, timeout=float(st.get("timeout") or 6))
                note(i, "旧草稿恢复提示已处理" if seen else "无旧草稿恢复提示")
            elif act == "fanqie_short_trial":
                run_fanqie_short_trial(page, st, i, note, values)
            elif act == "fanqie_short_publish":
                run_fanqie_short_publish(page, st, i, note, values)
            elif act == "qimao_short_publish":
                run_qimao_short_publish(page, st, i, note, values)
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
                any_of = [str(m) for m in (st.get("any_of") or [])]
                last_missing = ""

                def _resolve(mark):
                    for k, v in (values or {}).items():
                        mark = mark.replace("{%s}" % k, str(v))
                    return mark

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
                        missing = [mk for mk in map(_resolve, marks)
                                   if mk not in body_txt]
                        if any_of and not any(mk in body_txt
                                              for mk in map(_resolve, any_of)):
                            missing.append("（任一）"
                                           + "／".join(map(_resolve, any_of)))
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
            elif act == "format_block":
                # 章节标题块：把包含指定文字的段落格式化为标题（execCommand
                # formatBlock 走编辑器原生管道，与工具栏「正文→标题」切换同
                # 效——直接改 DOM 平台数据模型不认，保存即丢）。
                # 七猫短故事发布硬校验「至少两个章节标题」（2026-10-22 实案）。
                text = str(st.get("text") or "")
                block = str(st.get("block") or "h3")
                if not text:
                    continue
                note(i, "标题块「%s」→%s" % (text[:20], block))
                r = None
                for _try in range(3):
                    r = page.call(
                        "(payload)=>{const j=JSON.parse(payload);"
                        "const body=document.querySelector('.q-contenteditable');"
                        "if(!body)return{ok:false,err:'no-editor'};"
                        "const norm=x=>String(x||'').replace(/\\s+/g,'');"
                        "const p=[...body.querySelectorAll('p,div,h1,h2,h3,h4,h5,h6')]"
                        ".find(e=>norm(e.innerText)===norm(j.text));"
                        "if(!p)return{ok:false,err:'no-para'};"
                        "const sel=window.getSelection();"
                        "const range=document.createRange();"
                        "range.selectNodeContents(p);"
                        "sel.removeAllRanges();sel.addRange(range);"
                        "document.execCommand('formatBlock', false, j.block);"
                        "const made=body.querySelector(j.block);"
                        "return{ok:!!made};"
                        "}", json.dumps({"text": text, "block": block},
                                        ensure_ascii=False), 15)
                    # 兼容旧校准表里 JSON.stringify 版本（返回字符串）
                    if isinstance(r, str):
                        try:
                            r = json.loads(r)
                        except ValueError:
                            r = {}
                    if (r or {}).get("ok"):
                        break
                    time.sleep(0.9)
                if not (r or {}).get("ok"):
                    # optional：标记段落不存在（短篇分节数少于表格列出的
                    # 标记数）时跳过——流程表按上限列标记，正文按字数实切
                    if st.get("optional") and (r or {}).get("err") == "no-para":
                        note(i, "无「%s」段落，跳过标题块" % text)
                        continue
                    raise FlowError("章节标题块「%s」设置失败：%s"
                                    % (text[:30], (r or {}).get("err")
                                       if r else "无响应"))
            elif act == "expect_text":
                # 页面反馈等待（草稿保存凭据，2026-10-10）：轮询页面文本直到
                # 出现任一成功标记（toast/状态字样）——verify 的章节管理页对
                # 「已发布章重复起草」必然假通过，编辑器保存反馈才是本次动作
                # 自己的证据。超时即失败：宁可带 fail 截图拦下，不可把「没
                # 存上」记成成功。标记文案以真机校准为准。
                # 标记里的 values 占位先解析（与 verify 同口径）；不解析会把
                # {book_name} 字面量当标记永远等不到（2026-10-22 番茄实案）。
                marks = [str(m) for m in (st.get("any") or [])]
                for k, v in (values or {}).items():
                    marks = [m.replace("{%s}" % k, str(v)) for m in marks]
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
                if run_fanqie_publish(page, st, i, note, values) == "continue":
                    continue
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
