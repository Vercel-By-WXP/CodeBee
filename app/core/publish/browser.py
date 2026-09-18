# -*- coding: utf-8 -*-
"""CDP 浏览器驱动：定位浏览器 → 带 profile 启动 → WebSocket 控制页面。

设计取向：
- 「操作」全部走 Runtime.evaluate 注入 JS（querySelector 匹配交给浏览器自己），
  Python 侧不解析 DOM——平台改版时只需改流程选择器表，驱动层不动；
- 每平台一个持久化 user-data-dir（profile）：登录态落在 profile 里，
  服务重启后重新 launch 即恢复，代码不经手 cookie；
- 浏览器窗口对用户可见（扫码登录、人工确认提交都靠它），不默认 headless；
- CDP 调试端口只绑 127.0.0.1，HTTP 探测禁用系统代理（用户挂代理时不误伤）。
"""
from __future__ import annotations

import json
import os
import re
import shutil
import signal
import socket
import subprocess
import sys
import time
import urllib.request
from pathlib import Path

from .ws import MiniWS, WebSocketError

# 本机回环的 CDP HTTP 端点绝不能走系统代理（代理环境下 urlopen 会黑洞）
_OPENER = urllib.request.build_opener(urllib.request.ProxyHandler({}))

_CANDS = {
    "win32": [r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe",
              r"C:\Program Files\Microsoft\Edge\Application\msedge.exe",
              r"C:\Program Files\Google\Chrome\Application\chrome.exe",
              r"C:\Program Files (x86)\Google\Chrome\Application\chrome.exe"],
    "darwin": ["/Applications/Microsoft Edge.app/Contents/MacOS/Microsoft Edge",
               "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome"],
    "linux": [],
}
_WHICH = ("msedge", "microsoft-edge", "google-chrome", "chromium", "chrome")


class BrowserError(Exception):
    """驱动层失败（找不到浏览器/起不来/JS 执行异常），message 面向用户。"""


def find_browser():
    """按 Edge → Chrome 顺序找本机浏览器可执行文件；找不到返回 None。"""
    for p in _CANDS.get(sys.platform, []):
        if Path(p).exists():
            return p
    for name in _WHICH:
        w = shutil.which(name)
        if w:
            return w
    return None


def _http_json(url, timeout=5.0, method="GET"):
    req = urllib.request.Request(url, method=method)
    with _OPENER.open(req, timeout=timeout) as r:
        return json.loads(r.read().decode("utf-8", "replace"))


def port_alive(port, timeout=2.0):
    """探测 127.0.0.1:<port> 上有没有活的 CDP 服务（重启后重连用）。"""
    try:
        return bool(_http_json("http://127.0.0.1:%d/json/version" % port, timeout=timeout))
    except Exception:
        return False


def _free_port():
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    s.close()
    return port


class Browser:
    """一个浏览器子进程（一个 profile 一个实例）+ 它的调试端口。"""

    def __init__(self, user_data_dir, headless=False, exe=None, extra_args=None):
        self.exe = exe or find_browser()
        if not self.exe:
            raise BrowserError("没找到 Edge/Chrome 浏览器，请先安装 Microsoft Edge")
        self.user_data_dir = str(Path(user_data_dir))
        self.port = _free_port()
        args = [self.exe,
                "--remote-debugging-port=%d" % self.port,
                "--user-data-dir=%s" % self.user_data_dir,
                "--no-first-run", "--no-default-browser-check",
                "--hide-crash-restore-bubble"]   # 崩溃恢复气泡压掉即够；
                                                 # --restore-last-session 是无值开关，
                                                 # 带 =false 反而激活会话恢复，勿加
        if extra_args:
            args += list(extra_args)
        if headless:
            args.append("--headless=new")
        try:
            self.proc = subprocess.Popen(
                args, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                start_new_session=(os.name != "nt"))  # POSIX 独立进程组供 killpg
                                                      # 杀树（runner 同款）；Windows 忽略
        except OSError as e:
            raise BrowserError("浏览器启动失败：%s" % e)
        if not self._wait_ready(15.0):
            raise BrowserError("浏览器调试端口 15 秒内没就绪（可能被安全软件拦截）")

    # ------------------------------------------------------------ 生命周期
    def _wait_ready(self, timeout):
        deadline = time.time() + timeout
        while time.time() < deadline:
            if self.proc.poll() is not None:
                return False            # 二开 profile 时新进程会把参数转交老实例后退出
            if port_alive(self.port, timeout=1.0):
                return True
            time.sleep(0.4)
        return False

    def alive(self):
        return port_alive(self.port, timeout=1.5)

    def close(self):
        """杀掉浏览器进程树。登录态在 profile 里，杀掉不丢。"""
        pid = getattr(self, "proc", None) and self.proc.pid
        try:
            if sys.platform == "win32" and pid:
                subprocess.call(["taskkill", "/F", "/T", "/PID", str(pid)],
                                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            elif pid:
                # spawn 时 start_new_session，pgid==pid，连渲染进程带杀（runner 同款）；
                # SIGTERM 单杀主进程在浏览器挂死时留残余子进程锁 profile
                os.killpg(pid, signal.SIGKILL)
            elif self.proc:
                self.proc.terminate()
        except Exception:
            pass

    # ------------------------------------------------------------ 页面
    def pages(self):
        return [t for t in _http_json("http://127.0.0.1:%d/json/list" % self.port)
                if t.get("type") == "page"]

    def new_page(self, url="about:blank"):
        t = _http_json("http://127.0.0.1:%d/json/new?%s" % (self.port, url),
                       method="PUT")    # Chromium 111+ 要求 PUT
        return Page(t["webSocketDebuggerUrl"])

    def first_page(self, create=True):
        ps = self.pages()
        if ps:
            return Page(ps[0]["webSocketDebuggerUrl"])
        return self.new_page() if create else None

    @staticmethod
    def attach(port):
        """不启动进程、直接接管已存活浏览器（服务重启后重连同 profile 的实例）。"""
        if not port_alive(port):
            raise BrowserError("端口 %s 上没有活着的浏览器" % port)
        b = Browser.__new__(Browser)
        b.port = int(port)
        b.proc = None
        b.user_data_dir = ""
        b.exe = find_browser()
        return b


class Page:
    """一个标签页的 CDP 会话：命令收发 + 注入式高层操作。"""

    def __init__(self, ws_url, connect_timeout=15.0):
        m = re.match(r"ws://([^/:]+):(\d+)(/.+)", ws_url)
        if not m:
            raise BrowserError("无法解析调试地址：%s" % ws_url[:80])
        try:
            self.ws = MiniWS(m.group(1), int(m.group(2)), m.group(3),
                             timeout=connect_timeout)
        except (WebSocketError, OSError) as e:
            raise BrowserError("连接页面失败：%s" % e)
        self._id = 0
        self._events = []
        try:
            self.send("Runtime.enable")
            self.send("Page.enable")
        except BrowserError:
            self.ws.close()
            raise

    # ------------------------------------------------------------ CDP 协议
    def send(self, method, params=None, timeout=30.0):
        """发一条命令并等它的响应；期间到达的事件进缓存（wait_event 消费）。"""
        self._id += 1
        mid = self._id
        self.ws.send_text(json.dumps({"id": mid, "method": method,
                                      "params": params or {}}))
        deadline = time.time() + timeout
        while True:
            remain = deadline - time.time()
            if remain <= 0:
                raise BrowserError("命令 %s 等响应超时（%.0fs）" % (method, timeout))
            try:
                msg = json.loads(self.ws.recv_message(timeout=remain))
            except socket.timeout:
                continue                        # 到点抛 BrowserError（上面的分支）
            except (WebSocketError, OSError) as e:
                raise BrowserError("连接断开：%s" % e)
            if msg.get("id") == mid:
                if "error" in msg:
                    raise BrowserError("CDP 拒绝 %s：%s" %
                                       (method, str(msg["error"].get("message"))[:160]))
                return msg.get("result") or {}
            if "id" not in msg:
                self._events.append(msg)

    def wait_event(self, method, timeout=30.0, drain=True):
        """等一个指定事件（如 Page.loadEventFired）；drain 时清空旧事件。"""
        if drain:
            self._events = [e for e in self._events if e.get("method") != method]
        deadline = time.time() + timeout
        while time.time() < deadline:
            for i, e in enumerate(self._events):
                if e.get("method") == method:
                    del self._events[i]
                    return e.get("params") or {}
            try:
                msg = json.loads(self.ws.recv_message(
                    timeout=max(0.2, deadline - time.time())))
            except socket.timeout:
                continue
            except (WebSocketError, OSError) as e:
                raise BrowserError("连接断开：%s" % e)
            if "id" not in msg:
                self._events.append(msg)
        raise BrowserError("等事件 %s 超时（%.0fs）" % (method, timeout))

    # ------------------------------------------------------------ 求值
    def evaluate(self, expr, await_promise=False, timeout=60.0):
        """求值 JS 表达式返回其值；JS 抛错时带出人话信息。"""
        r = self.send("Runtime.evaluate", {
            "expression": expr, "awaitPromise": await_promise,
            "returnByValue": True, "userGesture": True,
        }, timeout=timeout)
        det = r.get("exceptionDetails")
        if det:
            raise BrowserError("JS 异常：%s" % json.dumps(det, ensure_ascii=False)[:220])
        res = r.get("result") or {}      # send 已剥掉外层 {id,result} 的 result
        if res.get("subtype") == "error":
            raise BrowserError("JS 抛错：%s" % str(res.get("description"))[:200])
        return res.get("value")

    def call(self, fn, *args, **kw):
        """注入一个 JS 函数并调用：call('(sel,t)=>{...}', sel, text)。"""
        payload = ", ".join(json.dumps(a, ensure_ascii=False) for a in args)
        return self.evaluate("(%s)(%s)" % (fn, payload), **kw)

    # ------------------------------------------------------------ 高层操作
    def url(self):
        return self.evaluate("location.href")

    def navigate(self, url, timeout=30.0):
        """导航并等文档就绪。SPA 路由可能不触发完整 load，readyState 轮询兜底。"""
        try:
            self.send("Page.navigate", {"url": url}, timeout=timeout)
        except BrowserError:
            pass                             # 老页面销毁时连接报错属正常，轮询兜底
        deadline = time.time() + timeout
        while time.time() < deadline:
            try:
                if self.evaluate("document.readyState", timeout=3.0) == "complete":
                    return
            except BrowserError:
                pass                         # 导航间隙 evaluate 会短暂失败
            time.sleep(0.3)
        raise BrowserError("页面 %.0f 秒未完成加载：%s" % (timeout, url))

    def exists(self, sel, timeout=1.0):
        return bool(self.call("(s)=>!!document.querySelector(s)", sel, timeout=timeout))

    def wait_for(self, sel, timeout=15.0):
        deadline = time.time() + timeout
        while time.time() < deadline:
            if self.exists(sel, timeout=1.0):
                return
            time.sleep(0.4)
        raise BrowserError("等待元素超时（%.0fs）：%s" % (timeout, sel))

    def value(self, sel):
        return self.call("(s)=>{const e=document.querySelector(s);"
                         "return e?(e.value!==undefined?e.value:e.textContent):null}", sel)

    def fill(self, sel, text):
        """填输入框/文本域/富文本。

        React/Vue 受控组件直接赋 value 不触发框架状态，必须走原型链 native
        setter 再补 input/change 事件；网文编辑器正文多为 contenteditable，
        走 execCommand('insertText') 以触发其内部输入管道（先清空再插入）。"""
        r = self.call(
            "(s,t)=>{const el=document.querySelector(s);if(!el)"
            "return{ok:false,err:'找不到输入框 '+s};"
            "el.scrollIntoView({block:'center'});el.focus();"
            "if(el.isContentEditable){"
            "const r=document.createRange();r.selectNodeContents(el);"
            "const g=getSelection();g.removeAllRanges();g.addRange(r);"
            "document.execCommand('insertText',false,t);"
            "return{ok:el.textContent.length>=Math.min(t.length,10)};}"
            "const proto=el.tagName==='TEXTAREA'?HTMLTextAreaElement.prototype"
            ":HTMLInputElement.prototype;"
            "const d=Object.getOwnPropertyDescriptor(proto,'value');"
            "(d&&d.set?d.set:function(v){el.value=v}).call(el,t);"
            "el.dispatchEvent(new Event('input',{bubbles:true}));"
            "el.dispatchEvent(new Event('change',{bubbles:true}));"
            "return{ok:true};}", sel, str(text))
        if not (r or {}).get("ok"):
            raise BrowserError("填入失败：%s" % ((r or {}).get("err") or "目标不是可输入元素"))
        return True

    def click(self, sel):
        r = self.call(
            "(s)=>{const el=document.querySelector(s);if(!el)"
            "return{ok:false,err:'找不到 '+s};"
            "if(el.disabled)return{ok:false,err:'按钮处于禁用态（表单可能有未通过校验的字段）'};"
            "el.scrollIntoView({block:'center'});el.click();return{ok:true};}", sel)
        if not (r or {}).get("ok"):
            raise BrowserError("点击失败：%s" % ((r or {}).get("err") or sel))
        return True

    def screenshot(self, fp):
        """整页截图存证：发布每步之后落一张，出错可回看卡在哪一步。"""
        r = self.send("Page.captureScreenshot", {"format": "png", "fromSurface": True})
        import base64
        fp = Path(fp)
        fp.parent.mkdir(parents=True, exist_ok=True)
        fp.write_bytes(base64.b64decode(r.get("data") or ""))
        return str(fp)

    def close(self):
        try:
            self.send("Page.close", timeout=3.0)
        except Exception:
            pass
        self.ws.close()

    # ------------------------------------------------------------ 表单探测
    def probe(self):
        """dump 页面上可交互元素的概要——校准流程选择器表的辅助工具。

        返回 [{tag, type, sel, text, value, placeholder}]，sel 是尽量稳定的
        定位串（id/#id 优先，退而求其次 name/placeholder/文本/序号）。"""
        return self.call(
            "()=>{const out=[];"
            "document.querySelectorAll('input,textarea,button,select,[contenteditable=true]').forEach(el=>{"
            "let s='';"
            "if(el.id)s='#'+el.id;"
            "else if(el.name)s=el.tagName.toLowerCase()+'[name=\"'+el.name+'\"]';"
            "else if(el.placeholder)s=el.tagName.toLowerCase()+'[placeholder=\"'+el.placeholder.slice(0,20)+'\"]';"
            "else{const t=(el.innerText||el.value||'').trim().slice(0,12);"
            "s=t?el.tagName.toLowerCase()+':contains('+t+')':el.tagName.toLowerCase();}"
            "out.push({tag:el.tagName.toLowerCase(),type:el.type||'',sel:s,"
            "text:(el.innerText||'').trim().slice(0,16),"
            "value:(el.value||el.textContent||'').trim().slice(0,24),"
            "placeholder:(el.placeholder||'').slice(0,20)});});"
            "return out.slice(0,80);}", timeout=15.0) or []
