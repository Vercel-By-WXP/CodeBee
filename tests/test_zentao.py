# -*- coding: utf-8 -*-
"""zentao（禅道 Bug 自动修复）单元测试：起本地假禅道服务器，绝不真连外网。

覆盖：SSRF 守卫、过滤/防呆、配置校验与脱敏、扫描认领去重、终态回写
（resolve/评论）、merge 失败不谎报 resolved、auto_resolve 关闭、token 401 重试、
fire_due 节流闸。
"""
from __future__ import annotations

import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from base import BaseTest


# ---------------------------------------------------------------- 假禅道服务器

class FakeZen:
    """内存态禅道：bugs 字典 + 调用记录。挂在 ThreadingHTTPServer 上。"""

    def __init__(self, bugs):
        self.bugs = {str(b["id"]): dict(b) for b in bugs}
        self.calls = []          # (method, path, body)
        self.reject_next_auth = False   # True：下一次带 token 的请求 401（模拟 token 过期）

    # ---- handler 工厂 ----
    def handler(self):
        srv = self

        class H(BaseHTTPRequestHandler):
            def log_message(self, *a):      # 静音
                pass

            def _reply(self, code, obj):
                raw = json.dumps(obj).encode("utf-8")
                self.send_response(code)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(raw)))
                self.end_headers()
                self.wfile.write(raw)

            def _parts(self):
                """/api.php/v1/products/1/bugs?... → ["products","1","bugs"]"""
                from urllib.parse import urlparse
                p = urlparse(self.path).path.lstrip("/")
                if p.startswith("api.php/v1/"):
                    p = p[len("api.php/v1/"):]
                return p.split("/")

            def _auth(self):
                tok = (self.headers.get("Token") or "").strip()
                if not tok:
                    self._reply(401, {"error": "missing token"})
                    return False
                if srv.reject_next_auth:
                    srv.reject_next_auth = False   # 一次性：模拟「这枚 token 刚好过期」
                    self._reply(401, {"error": "token expired"})
                    return False
                return True

            def _body(self):
                n = int(self.headers.get("Content-Length") or 0)
                raw = self.rfile.read(n) if n else b"{}"
                try:
                    return json.loads(raw.decode("utf-8"))
                except Exception:
                    return {}

            def do_POST(self):
                srv.calls.append(("POST", self.path, self._body()))
                if self.path.endswith("/tokens"):
                    b = srv.calls[-1][2]
                    if b.get("account") == "coder" and b.get("password") == "pw":
                        self._reply(200, {"token": "T-%d" % len(srv.calls)})
                    else:
                        self._reply(401, {"error": "账号或密码不对"})
                    return
                if not self._auth():
                    return
                parts = self._parts()
                # /bugs/<id>/resolve
                if len(parts) == 3 and parts[0] == "bugs" and parts[2] == "resolve":
                    bug = srv.bugs.get(parts[1])
                    if bug is None:
                        self._reply(404, {"error": "no such bug"})
                        return
                    b = srv.calls[-1][2]
                    bug["resolution"] = b.get("resolution") or "fixed"
                    bug["status"] = "resolved"
                    if b.get("assignedTo"):
                        bug["assignedTo"] = {"account": b["assignedTo"], "realname": b["assignedTo"]}
                    self._reply(200, bug)
                    return
                self._reply(404, {"error": "unknown POST %s" % self.path})

            def do_PUT(self):
                srv.calls.append(("PUT", self.path, self._body()))
                if not self._auth():
                    return
                parts = self._parts()
                if len(parts) == 2 and parts[0] == "bugs":
                    bug = srv.bugs.get(parts[1])
                    if bug is None:
                        self._reply(404, {"error": "no such bug"})
                        return
                    self._reply(200, bug)
                    return
                self._reply(404, {"error": "unknown PUT"})

            def do_GET(self):
                srv.calls.append(("GET", self.path, None))
                if not self._auth():
                    return
                parts = self._parts()
                if len(parts) == 3 and parts[0] == "products" and parts[2] == "bugs":
                    pid = int(parts[1])
                    bugs = [b for b in srv.bugs.values() if int(b.get("product") or 0) == pid]
                    self._reply(200, {"bugs": bugs, "total": len(bugs),
                                      "page": 1, "limit": 100})
                    return
                if len(parts) == 2 and parts[0] == "bugs":
                    bug = srv.bugs.get(parts[1])
                    if bug is None:
                        self._reply(404, {"error": "no such bug"})
                    else:
                        self._reply(200, bug)
                    return
                self._reply(404, {"error": "unknown GET %s" % self.path})

        return H


# ---------------------------------------------------------------- 用例脚手架

class ZenCase(BaseTest):
    """公共脚手架：假禅道起在本机随机端口；zentao._FILE/状态重绑到临时目录。"""

    def setUp(self):
        super().setUp()
        from app.core import store, zentao
        self.zen_mod = zentao
        self.store = store
        zentao._FILE = self.data_dir / "zentao.json"
        zentao._test_reset()
        # 启动器 stub：走真 store 建 task/run（不入队，不真跑编排）
        self.launched = []
        self._orig_launch = zentao._launch_fix

        def fake_launch(bug, cfg):
            payload = {"type": "code", "goal": zentao._goal_text(bug),
                       "workdir": zentao._workdir(cfg),
                       "title": ("[禅道#%s] %s" % (bug.get("id"), bug.get("title") or ""))[:60]}
            if str(cfg.get("git_rev") or "").strip():
                payload["git_rev"] = cfg["git_rev"]
            task = store.create_task(payload)
            run = store.create_run("orchestration", task["title"], task_id=task["id"])
            store.update_task_status(task["id"], "queued")
            self.launched.append({"task": task, "run": run, "bug": bug})
            return task, run
        zentao._launch_fix = fake_launch
        # 假服务器
        self.fz = FakeZen([])
        self.httpd = ThreadingHTTPServer(("127.0.0.1", 0), self.fz.handler())
        self.port = self.httpd.server_address[1]
        threading.Thread(target=self.httpd.serve_forever, daemon=True).start()
        self.addCleanup(self._restore)

    def _restore(self):
        self.httpd.shutdown()
        self.httpd.server_close()
        self.zen_mod._launch_fix = self._orig_launch
        from app.core import store
        store._TASKS.clear()
        store._RUNS.clear()

    def configure(self, **kw):
        cfg = {"base_url": "http://127.0.0.1:%d" % self.port,
               "account": "coder", "password": "pw",
               "products": [1], "assigned_to": "coder",
               "workdir": str(self.workdir)}
        cfg.update(kw)
        return self.zen_mod.save_config(cfg)

    def bug(self, bid=101, **kw):
        b = {"id": bid, "product": 1, "title": "登录页 500",
             "steps": "<p>打开<b>登录页</b></p><br>报 500",
             "severity": 2, "pri": 2, "type": "codeerror",
             "status": "active", "os": "win", "browser": "edge",
             "keywords": "", "assignedTo": {"account": "coder", "realname": "码蜂"},
             "openedBy": {"account": "tester", "realname": "测试君"}}
        b.update(kw)
        self.fz.bugs[str(bid)] = b
        return b


class TestGuardUrl(ZenCase):
    def runTest(self):
        ZenError = self.zen_mod.ZenError
        # 放行：本机/内网/可解析域名
        for url in ("http://127.0.0.1:9999/api.php/v1/tokens",
                    "http://localhost:88/zentao/api.php/v1/tokens",
                    "http://192.168.1.10/zentao/api.php/v1/tokens"):
            self.assertEqual(self.zen_mod._guard_url(url), url)
        # 拦截：非 http 协议 / 云元数据 / 链路本地
        for bad in ("ftp://x/tokens", "file:///etc/passwd",
                    "http://169.254.169.254/latest/meta-data",
                    "http://metadata.google.internal/computeMetadata/v1/"):
            with self.assertRaises(ZenError):
                self.zen_mod._guard_url(bad)


class TestFilters(ZenCase):
    def runTest(self):
        f = self.zen_mod._claimable
        cfg = {"products": [1], "assigned_to": "coder", "severity_cap": 0}
        self.assertTrue(f({"status": "active", "product": 1,
                           "assignedTo": {"account": "coder"}}, cfg))
        self.assertFalse(f({"status": "resolved", "product": 1,
                            "assignedTo": {"account": "coder"}}, cfg), "非 active 不认领")
        self.assertFalse(f({"status": "active", "product": 2,
                            "assignedTo": {"account": "coder"}}, cfg), "产品不符不认领")
        self.assertFalse(f({"status": "active", "product": 1,
                            "assignedTo": "someone-else"}, cfg), "指派不符不认领")
        self.assertFalse(f({"status": "active", "product": 1,
                            "assignedTo": {"account": "coder"}}, {}), "过滤全空不认领（防呆）")
        cfg_sev = dict(cfg, severity_cap=2)
        self.assertTrue(f({"status": "active", "product": 1,
                           "assignedTo": "coder", "severity": 1}, cfg_sev), "severity 1 ≤ 2")
        self.assertFalse(f({"status": "active", "product": 1,
                            "assignedTo": "coder", "severity": 3}, cfg_sev), "severity 3 > 2")
        # HTML 剥离：标签清掉、正文保留（标签位以空格替代，中文断言按词查）
        txt = self.zen_mod._strip_html("<p>打开<b>登录页</b></p><br>报 500")
        self.assertIn("登录页", txt)
        self.assertIn("报 500", txt)
        self.assertNotIn("<", txt)


class TestConfigValidationAndMask(ZenCase):
    def runTest(self):
        self.configure()
        v = self.zen_mod.view()
        self.assertTrue(v["config"]["has_password"])
        self.assertEqual(v["config"]["password"], "", "password 必须脱敏")
        # products 归一 + 去非正数
        cfg = self.zen_mod.save_config({"products": ["2", 3, -1]})
        self.assertEqual(cfg["products"], [2, 3])
        # 非法 scheme 拒绝
        from app.core import zentao
        with self.assertRaises(ValueError):
            zentao.save_config({"base_url": "ftp://x"})
        # interval 越界收敛
        self.assertEqual(zentao.save_config({"interval_hours": 999})["interval_hours"], 168)
        # 空 password 不覆盖已存密码
        cfg = zentao.save_config({"account": "coder2", "password": ""})
        self.assertEqual(cfg["account"], "coder2")
        self.assertTrue(cfg["has_password"])
        # relative workdir 拒绝
        with self.assertRaises(ValueError):
            zentao.save_config({"workdir": "relative/path"})


class TestScanClaimDedup(ZenCase):
    def runTest(self):
        self.configure()
        self.bug(101)
        self.bug(102, status="resolved")                       # 非 active
        self.bug(103, assignedTo={"account": "other"})          # 指派不符
        res = self.zen_mod.scan_now()
        self.assertTrue(res["ok"], res)
        self.assertEqual(res["claimed"], 1)
        claims = self.zen_mod.view()["claims"]
        self.assertEqual(len(claims), 1)
        c = claims[0]
        self.assertEqual(str(c["bug_id"]), "101")
        self.assertEqual(c["state"], "fixing")
        # 任务链真的建了：type=code、标题带前缀
        task = self.store.get_task(c["task_id"])
        self.assertEqual(task["type"], "code")
        self.assertTrue(task["title"].startswith("[禅道#101]"))
        self.assertIn("修复禅道 Bug #101", task["goal"])
        self.assertIn("登录页", task["goal"])               # steps 剥 HTML 后注入
        # 去重：再扫不重复认领
        res2 = self.zen_mod.scan_now()
        self.assertEqual(res2["claimed"], 0)
        self.assertEqual(len(self.zen_mod.view()["claims"]), 1)


class TestReconcileResolve(ZenCase):
    def runTest(self):
        self.configure()
        self.bug(201)
        self.zen_mod.scan_now()
        claim = self.zen_mod.view()["claims"][0]
        # run 置为 done → 对账回写 resolve
        self.store.update_run(claim["run_id"], status="done",
                              verdict={"pass": True, "publishable": True})
        res = self.zen_mod.scan_now()
        self.assertTrue(res["ok"])
        c = self.zen_mod.view()["claims"][0]
        self.assertEqual(c["state"], "resolved")
        # 禅道侧：bug 被 resolve(fixed)，评论带报告，指回报告人
        bug = self.fz.bugs["201"]
        self.assertEqual(bug["status"], "resolved")
        resolve_calls = [x for x in self.fz.calls
                         if x[0] == "POST" and x[1].endswith("/bugs/201/resolve")]
        self.assertEqual(len(resolve_calls), 1)
        body = resolve_calls[0][2]
        self.assertEqual(body["resolution"], "fixed")
        self.assertEqual(body["assignedTo"], "tester")
        self.assertIn("【CodeBee 自动修复报告】", body["comment"])
        # 幂等：已 resolved 的 bug 不再重复 resolve
        n_before = len([x for x in self.fz.calls
                        if x[0] == "POST" and x[1].endswith("/resolve")])
        self.zen_mod.scan_now()
        n_after = len([x for x in self.fz.calls
                       if x[0] == "POST" and x[1].endswith("/resolve")])
        self.assertEqual(n_before, n_after)


class TestReconcileFailureComments(ZenCase):
    def runTest(self):
        self.configure()
        self.bug(301)
        self.zen_mod.scan_now()
        claim = self.zen_mod.view()["claims"][0]
        self.store.update_run(claim["run_id"], status="failed", error="验证命令退出码 1")
        self.zen_mod.scan_now()
        c = self.zen_mod.view()["claims"][0]
        self.assertEqual(c["state"], "commented")
        puts = [x for x in self.fz.calls if x[0] == "PUT" and x[1].endswith("/bugs/301")]
        self.assertEqual(len(puts), 1)
        self.assertIn("自动修复未成功", puts[0][2]["comment"])
        resolves = [x for x in self.fz.calls
                    if x[0] == "POST" and "resolve" in x[1]]
        self.assertEqual(resolves, [], "失败绝不 resolve")


class TestMergeFailureNoResolve(ZenCase):
    def runTest(self):
        # git_rev 配了 + auto_merge 开，但工作目录不是 git 仓库 → 合并必败
        self.configure(git_rev="main")
        self.bug(401)
        self.zen_mod.scan_now()
        claim = self.zen_mod.view()["claims"][0]
        task = self.store.get_task(claim["task_id"])
        self.assertEqual(task.get("git_rev"), "main")
        self.store.update_run(claim["run_id"], status="done", verdict={"pass": True})
        self.zen_mod.scan_now()
        c = self.zen_mod.view()["claims"][0]
        self.assertEqual(c["state"], "merge_failed")
        resolves = [x for x in self.fz.calls
                    if x[0] == "POST" and "resolve" in x[1]]
        self.assertEqual(resolves, [], "合并失败不得 resolve（不谎报）")
        self.assertIn("合并失败", c["note"])


class TestAutoResolveOff(ZenCase):
    def runTest(self):
        self.configure(auto_resolve=False)
        self.bug(501)
        self.zen_mod.scan_now()
        claim = self.zen_mod.view()["claims"][0]
        self.store.update_run(claim["run_id"], status="done", verdict={"pass": True})
        self.zen_mod.scan_now()
        c = self.zen_mod.view()["claims"][0]
        self.assertEqual(c["state"], "done_manual")
        self.assertEqual([x for x in self.fz.calls
                          if x[0] == "POST" and "resolve" in x[1]], [])


class TestToken401Retry(ZenCase):
    def runTest(self):
        self.fz.reject_next_auth = True    # 下一枚 token 首用即 401 → 客户端必须重取重试
        self.configure()
        self.bug(601)
        res = self.zen_mod.scan_now()
        self.assertTrue(res["ok"], res)
        self.assertEqual(res["claimed"], 1, "401 重取 token 后应能正常拉列表")


class TestFireDueGate(ZenCase):
    def runTest(self):
        self.configure()                    # poll_enabled 默认 False
        self.assertEqual(self.zen_mod.fire_due(), None, "未启用轮询 fire_due 零动作")
        self.bug(701)
        self.zen_mod.save_config({"poll_enabled": True})
        # 保存配置后 next_scan 立即到期 → 首个 tick 应扫描
        res = self.zen_mod.fire_due()
        self.assertIsNotNone(res)
        self.assertEqual(res["claimed"], 1)
        # 刚扫过 → 节流：未到点不重扫
        res2 = self.zen_mod.fire_due()
        self.assertEqual(res2.get("skipped"), "not_due")
        self.assertEqual(len(self.zen_mod.view()["claims"]), 1)
        # 群 webhook 未配置时 push_text 静默 False，不影响流程（隐式已验证）


class TestConnection(ZenCase):
    def runTest(self):
        # 错误口令
        ok, msg = self.zen_mod.test_connection(
            base_url="http://127.0.0.1:%d" % self.port, account="coder", password="wrong")
        self.assertFalse(ok)
        self.assertIn("密码", msg)
        # 正确口令 + 产品可达
        self.bug(801)
        ok, msg = self.zen_mod.test_connection(
            base_url="http://127.0.0.1:%d" % self.port, account="coder", password="pw")
        self.assertTrue(ok, msg)
        # 地址缺失
        ok, msg = self.zen_mod.test_connection(base_url="", account="a", password="b")
        self.assertFalse(ok)
