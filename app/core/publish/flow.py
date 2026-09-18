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


def _click_text_js():
    return ("(t,scope,contains)=>{"
            "const els=[...document.querySelectorAll(scope||'button,a,[role=button],span,li')];"
            "const hit=els.find(e=>{const x=(e.innerText||'').trim();"
            "return x&&(contains?x.includes(t):x===t);});"
            "if(!hit)return{ok:false,err:'页面上找不到文本为「'+t+'」的可点元素'};"
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
                note(i, "提交 %s" % st.get("sel") or "")
                page.wait_for(st["sel"], timeout=8)
                page.click(st["sel"])
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
