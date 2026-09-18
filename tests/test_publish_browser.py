# -*- coding: utf-8 -*-
"""CDP 驱动冒烟测试：真 Edge headless 起→开表单页→填/点/截图→收尾。

跑法：python tests/test_publish_browser.py
覆盖 ws.py 握手/帧收发 与 browser.py 启动/求值/填表/点击/截图全链路。
"""
import json
import os
import sys
import tempfile
import threading
import time
from http.server import BaseHTTPRequestHandler, HTTPServer

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "app"))

from core.publish import browser  # noqa: E402
from core.publish.browser import Browser, BrowserError, Page  # noqa: E402

FORM = u"""<!doctype html><html><body>
<input id="title" placeholder="书名">
<textarea id="summary"></textarea>
<div id="editor" contenteditable="true"></div>
<button id="go" onclick="document.title='CLICKED'">提交</button>
</body></html>"""


class _H(BaseHTTPRequestHandler):
    def do_GET(self):
        body = FORM.encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *a):
        pass


def main():
    srv = HTTPServer(("127.0.0.1", 0), _H)
    port = srv.server_address[1]
    threading.Thread(target=srv.serve_forever, daemon=True).start()

    prof = tempfile.mkdtemp(prefix="cdp-smoke-")
    shots = os.path.join(prof, "shots")
    b = None
    fails = []

    def check(name, fn):
        try:
            v = fn()
            print("  ok   %s %s" % (name, v if v is not None else ""))
        except Exception as e:
            fails.append(name)
            print("  FAIL %s: %r" % (name, e))

    try:
        try:
            b = Browser(prof, headless=True)
            print("  ok   launch(headless)")
        except BrowserError as e:
            fails.append("launch")
            print("  FAIL launch: %r" % e)
            raise
        # 同 profile 二开：新进程转交参数即退出，端口不就绪——attach-or-launch
        # 策略由 manager 负责；这里验证旧实例端口仍活（供 attach）
        check("alive", lambda: (_ for _ in ()).throw(AssertionError) if not b.alive() else None)
        pg = b.new_page("http://127.0.0.1:%d/" % port)
        check("evaluate 1+1", lambda: (pg.evaluate("1+1"), None)[1] if pg.evaluate("1+1") == 2 else (_ for _ in ()).throw(AssertionError("!=2")))
        check("url", lambda: (_ for _ in ()).throw(AssertionError(pg.url())) if "127.0.0.1" not in pg.url() else None)
        pg.wait_for("#title")
        check("fill input", lambda: (pg.fill("#title", u"测试书名"), None)[1])
        check("read value", lambda: (_ for _ in ()).throw(AssertionError(pg.value("#title"))) if pg.value("#title") != u"测试书名" else None)
        check("fill textarea", lambda: (pg.fill("#summary", u"简介" * 100), None)[1])
        check("fill contenteditable", lambda: (pg.fill("#editor", u"第一章 正文" * 50), None)[1])
        check("editable content", lambda: (_ for _ in ()).throw(AssertionError(str(len(pg.value("#editor"))))) if len(pg.value("#editor")) < 100 else None)
        check("click", lambda: (pg.click("#go"), None)[1])
        check("click effect", lambda: (_ for _ in ()).throw(AssertionError(pg.evaluate("document.title"))) if pg.evaluate("document.title") != "CLICKED" else None)
        check("screenshot", lambda: os.path.exists(pg.screenshot(os.path.join(shots, "1.png"))))
        check("probe", lambda: (_ for _ in ()).throw(AssertionError(json.dumps(pg.probe(), ensure_ascii=False))) if len(pg.probe()) < 4 else None)
        check("big frame 分片", lambda: (_ for _ in ()).throw(AssertionError("len")) if len(pg.evaluate("'x'.repeat(3000000)")) != 3000000 else None)
        pg.close()
        check("attach", lambda: Browser.attach(b.port))
    finally:
        if b:
            b.close()
        srv.shutdown()
    print("FAILS:", fails if fails else "none")
    sys.exit(1 if fails else 0)


if __name__ == "__main__":
    main()
