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


def _debug_ports_for_profile(user_data_dir):
    """扫描本机浏览器进程命令行，返回使用该 profile 的主实例的调试端口。

    跨平台：Windows 走 wmic（PS 启动太慢），POSIX 走 ps。只认主进程
    （--type= 子进程没有调试端口）。路径按小写+正反斜杠归一后比对。"""
    import subprocess as _sp
    try:
        if sys.platform == "win32":
            out = _sp.check_output(
                ["wmic", "process", "where", "Name='msedge.exe'", "get", "CommandLine"],
                stderr=_sp.DEVNULL, timeout=12).decode("utf-8", "replace")
            lines = out.splitlines()
        else:
            out = _sp.check_output(["ps", "-axo", "command"], timeout=12).decode()
            lines = [l for l in out.splitlines()
                     if "msedge" in l or "chrome" in l or "chromium" in l]
    except Exception:
        return []
    key = str(user_data_dir).replace("\\", "/").strip("/").lower()
    ports = []
    for ln in lines:
        ln = ln.strip()
        if not ln or "--type=" in ln or "--remote-debugging-port=" not in ln:
            continue
        norm = ln.replace("\\\\", "/").replace("\\", "/").lower()
        if key not in norm:
            continue
        m = re.search(r"--remote-debugging-port=(\d+)", ln)
        if m:
            ports.append(int(m.group(1)))
    return ports


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
            # Edge 对已有实例的 profile：新进程转交参数后秒退（stdout 会打
            # 「正在现有浏览器会话中打开」），调试端口是老实例自己的（甚至
            # 自选的，真机实测指定 59852 实际跑 56394）。等自己指定的端口必然
            # 超时——先扫进程命令行找该 profile 的真端口接管（含隐藏的后台
            # 常驻实例：Edge「启动加速/后台运行」的进程锁着 profile 不可见）。
            for p in _debug_ports_for_profile(self.user_data_dir):
                if p != self.port and port_alive(p, timeout=2.0):
                    self.port = p
                    self.proc = None         # 老实例不是本对象起的：close 不杀它
                    return
            if self.proc.poll() == 0:
                raise BrowserError(
                    "浏览器把启动请求转给了已在运行的会话（该平台浏览器有隐藏的"
                    "后台实例占着档案）——请在任务管理器结束所有 Edge 进程，"
                    "或重启电脑后重试；登录态不受影响")
            raise BrowserError(
                "浏览器调试端口 15 秒内没就绪：该平台可能已有浏览器实例在跑但"
                "端口探测不到——关掉它的所有窗口后重试，或重启电脑后首次连接")

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
            self._ua_override()
        except BrowserError:
            self.ws.close()
            raise

    def _ua_override(self):
        """UA 伪装成标准 Chrome：Edge 尾巴（Edg/x.y）会被部分站点（如七猫
        建书页）的浏览器检测判「版本过低」整页替换。用 CDP Browser.getVersion
        的真实内核版本拼标准 Chrome UA，各站点兼容性等同 Chrome。失败不拦
        （个别上下文可能禁用该域）。"""
        try:
            r = self.send("Browser.getVersion", timeout=5.0)
            ver = str((r.get("product") or "")).split("/")[-1] or "130.0.0.0"
            ua = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                  "(KHTML, like Gecko) Chrome/%s Safari/537.36" % ver)
            self.send("Network.setUserAgentOverride",
                      {"userAgent": ua}, timeout=5.0)
        except BrowserError:
            pass

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
        """导航并等文档就绪。SPA 路由可能不触发完整 load，readyState 轮询兜底。

        about:blank 起跳时 readyState 本就 complete——必须同时等 location
        真正到达目标域，否则后续步骤打在空白页上（url_any 误报）。"""
        from urllib.parse import urlparse as _up
        if url == "about:blank":             # 中转页：无需等加载（页面忙时会假超时）
            try:
                self.send("Page.navigate", {"url": url}, timeout=5.0)
            except BrowserError:
                pass
            time.sleep(0.4)
            return
        try:
            self.send("Page.navigate", {"url": url}, timeout=timeout)
        except BrowserError:
            pass                             # 老页面销毁时连接报错属正常，轮询兜底
        host = _up(url).netloc
        deadline = time.time() + timeout
        seen_url = False
        while time.time() < deadline:
            try:
                href = self.evaluate("location.href", timeout=3.0) or ""
                if not host or host in href:
                    seen_url = True
                if seen_url and self.evaluate("document.readyState", timeout=3.0) == "complete":
                    time.sleep(0.5)          # SPA 首帧渲染余量
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
        setter 再补 input/change 事件；富文本（章节正文）走 CDP
        Input.insertText——execCommand('insertText') 对千字长文会截断
        （真机实测 1020 字只进 615），insertText 走完整输入管道无此限。"""
        text = str(text)
        ce = None
        for _try in range(24):                     # 编辑器慢加载 + class 动态 + edit-mask
            ce = self.call(                        # 遮罩需真实点击激活后才可编辑
                "(s)=>{const els=[...document.querySelectorAll(s)];"
                "let best=null;for(const e of els){"
                "if(!e.isContentEditable)continue;"
                "const r=e.getBoundingClientRect();"
                "if(r.width<=0)continue;"
                "const a=r.width*r.height;"
                "if(!best||a>best.a)best={e,a};}"
                "if(best){"
                "const el=best.e;"
                "el.scrollIntoView({block:'center'});el.focus();"
                "const r=document.createRange();r.selectNodeContents(el);"
                "const g=getSelection();g.removeAllRanges();g.addRange(r);return {hit:true};}"
                "const vis=els.filter(e=>e.getBoundingClientRect().width>0);"
                "if(!vis.length)return null;"
                "let big=vis[0];for(const e of vis){"
                "const r=e.getBoundingClientRect();"
                "if(r.width*r.height>big.getBoundingClientRect().width*big.getBoundingClientRect().height)big=e;}"
                "const r=big.getBoundingClientRect();"
                "return {hit:false,x:Math.round(r.x+r.width/2),y:Math.round(r.y+r.height/2)};}", sel)
            if ce is None:
                break
            if isinstance(ce, dict) and ce.get("hit"):
                ce = True
                break
            if isinstance(ce, dict) and "x" in ce:      # 遮罩未揭：真实点击激活
                self.send("Input.dispatchMouseEvent",
                          {"type": "mousePressed", "x": ce["x"], "y": ce["y"],
                           "button": "left", "clickCount": 1}, timeout=8.0)
                self.send("Input.dispatchMouseEvent",
                          {"type": "mouseReleased", "x": ce["x"], "y": ce["y"],
                           "button": "left", "clickCount": 1}, timeout=8.0)
                ce = False
                time.sleep(0.8)
                continue
            ce = False
            time.sleep(0.5)
        if ce is True:                            # 富文本：焦点就位后键盘级插入
            self.send("Input.insertText", {"text": text}, timeout=30.0)
            n = self.call("(s)=>{const els=[...document.querySelectorAll(s)].filter(e=>e.isContentEditable);"
                          "let n=0;for(const e of els)n=Math.max(n,(e.innerText||'').length);return n;}", sel)
            if int(n or 0) < min(len(text), 20):
                raise BrowserError("富文本插入不完整（%s/%d 字）" % (n, len(text)))
            return True
        if ce is None:
            raise BrowserError("找不到输入框 %s" % sel)
        r = self.call(
            "(s,t)=>{const el=document.querySelector(s);if(!el)"
            "return{ok:false,err:'找不到输入框 '+s};"
            "el.scrollIntoView({block:'center'});el.focus();"
            "const proto=el.tagName==='TEXTAREA'?HTMLTextAreaElement.prototype"
            ":HTMLInputElement.prototype;"
            "const d=Object.getOwnPropertyDescriptor(proto,'value');"
            "(d&&d.set?d.set:function(v){el.value=v}).call(el,t);"
            "el.dispatchEvent(new Event('input',{bubbles:true}));"
            "el.dispatchEvent(new Event('change',{bubbles:true}));"
            "return{ok:true};}", sel, text)
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

    def real_click_text(self, text, scope="", contains=True, exact_fallback=True):
        """按文本真实点击：JS 定位元素中心坐标 → CDP Input 派发鼠标事件序列。

        qm-btn 一类自定义按钮只认真实事件序列（mousedown/mouseup/focus），
        el.click() 对它们无效——建书「确认创建」/发章「立即发布」都栽在这。
        返回 {ok, tag?, via}；找不到元素返回 {ok: False, err}。"""
        r = self.call(
            "(t,scope,c)=>{"
            "const els=[...document.querySelectorAll(scope||'button,a,[role=button],span,li,[class*=btn]')];"
            "let cands=els.filter(e=>{const x=(e.innerText||'').trim();"
            "return x&&(c?x.includes(t):x===t);});"
            "if(!cands.length&&c){cands=els.filter(e=>(e.innerText||'').trim()===t);}"
            "if(!cands.length)return{ok:false,err:'nf'};"
            "cands.sort((a,b)=>((a.innerText||'').trim().length)-((b.innerText||'').trim().length));"
            "const el=cands[0];el.scrollIntoView({block:'center'});"
            "const rc=el.getBoundingClientRect();"
            "return{ok:true,x:Math.round(rc.x+rc.width/2),y:Math.round(rc.y+rc.height/2),"
            "tag:el.tagName,cls:(el.className||'').toString().slice(0,30)};}",
            str(text), scope or "", bool(contains))
        if not (r or {}).get("ok"):
            return {"ok": False, "err": "页面上找不到文本为「%s」的可点元素" % text}
        x, y = r["x"], r["y"]
        self.send("Input.dispatchMouseEvent",
                  {"type": "mousePressed", "x": x, "y": y,
                   "button": "left", "clickCount": 1}, timeout=8.0)
        self.send("Input.dispatchMouseEvent",
                  {"type": "mouseReleased", "x": x, "y": y,
                   "button": "left", "clickCount": 1}, timeout=8.0)
        return {"ok": True, "tag": r.get("tag"), "via": "input"}

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
