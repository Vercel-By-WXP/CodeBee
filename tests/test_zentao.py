# -*- coding: utf-8 -*-
"""zentao（禅道 Bug 自动修复·产品档案+排查路由）单元测试：起本地假禅道服务器。

覆盖：SSRF 守卫、过滤、配置校验/脱敏/老配置迁移、扫描认领去重、排查三路
（模块规则优先/AI 兜底/不可用留人工）、我方端修复 resolve、非我方转派、
纯对方端转派（含测试指错人改派）、双端我端修完转派、失败升级、负责人优先级、
模块清单容错、need_manual 重排查、老 claim 归一、token 401 重试、fire_due 节流。
"""
from __future__ import annotations

import base64
import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlparse
from unittest import mock

from base import BaseTest


# ---------------------------------------------------------------- 假禅道服务器

class FakeZen:
    """内存态禅道：bugs 字典 + 调用记录。挂在 ThreadingHTTPServer 上。"""

    def __init__(self, bugs):
        self.bugs = {str(b["id"]): dict(b) for b in bugs}
        self.calls = []                 # (method, path, body)
        self.modules = {1: [{"id": 99, "name": "登录"}, {"id": 77, "name": "报表"}]}
        self.reject_next_auth = False   # 一次性：下一个带 token 的请求 401
        self.products = {1: {"id": 1, "name": "产品一", "status": "normal"},
                         2: {"id": 2, "name": "产品二", "status": "closed"}}
        self.users = [{"account": "coder", "realname": "码蜂"},
                      {"account": "tester", "realname": "测试君"}]
        self.subpath = False    # True=只认 /zentao/api.php/v1（一键安装包子路径部署）
        self.old = False        # True=只讲老版 module-method JSON 接口（REST 全 404）
        self.old_modules_js = False   # True=tree-browse 回 js::HTML（接口不在形状）
        self.old_sid = ""
        self.old_session_n = 0
        self.web_style = ""     # ""=根路径不特殊应答；"pathinfo"/"get"=按形态回登录跳转
        self.files = {}         # 路径 → 原始字节（bug 截图下载用，免 Token 直服）

    def handler(self):
        srv = self

        class H(BaseHTTPRequestHandler):
            def log_message(self, *a):
                pass

            def _reply(self, code, obj):
                raw = json.dumps(obj).encode("utf-8")
                self.send_response(code)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(raw)))
                self.end_headers()
                self.wfile.write(raw)

            def _redirect_html(self):
                raw = ("<html><meta charset='utf-8'/><script>self.location="
                       "'/zentao/user-login-td.html';</script>").encode("utf-8")
                self.send_response(200)
                self.send_header("Content-Type", "text/html")
                self.send_header("Content-Length", str(len(raw)))
                self.end_headers()
                self.wfile.write(raw)

            def _js_html(self, locate):
                raw = ("<html><meta charset='utf-8'/><script>parent.location='" +
                       locate + "';</script>").encode("utf-8")
                self.send_response(200)
                self.send_header("Content-Type", "text/html")
                self.send_header("Content-Length", str(len(raw)))
                self.end_headers()
                self.wfile.write(raw)

            def _parts(self):
                from urllib.parse import urlparse
                p = urlparse(self.path).path.lstrip("/")
                pre = "zentao/api.php/v1/" if srv.subpath else "api.php/v1/"
                if p.startswith(pre):
                    p = p[len(pre):]
                return p.split("/")

            def _auth(self):
                if not (self.headers.get("Token") or "").strip():
                    self._reply(401, {"error": "missing token"})
                    return False
                if srv.reject_next_auth:
                    srv.reject_next_auth = False
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
                if srv.old:
                    from urllib.parse import urlparse, parse_qs
                    n = int(self.headers.get("Content-Length") or 0)
                    raw = self.rfile.read(n) if n else b""
                    form = {k: v[0] for k, v in parse_qs(raw.decode("utf-8", "replace")).items()}
                    srv.calls.append(("POST", self.path, form))
                    p = urlparse(self.path).path.lstrip("/")
                    if p == "zentao/user-login.json":
                        if form.get("account") == "coder" and form.get("password") == "pw":
                            # 真机形状：user 在响应顶层、data 为 null（2026-09-20 实测）
                            self._reply(200, {"status": "success", "data": None,
                                              "user": {"account": "coder", "realname": "码蜂"}})
                        else:
                            self._reply(200, {"status": "failed",
                                              "reason": "登录失败，请检查您的用户名或密码是否填写正确。"})
                        return
                    m = None
                    for pre in ("zentao/bug-assignTo-", "zentao/bug-resolve-"):
                        if p.startswith(pre):
                            m = p[len(pre):].split(".")[0]
                            break
                    if m:
                        bug = srv.bugs.get(m)
                        if bug is None:
                            self._reply(404, {"error": "no such bug"})
                            return
                        if form.get("assignedTo"):
                            bug["assignedTo"] = [form["assignedTo"], form["assignedTo"]]
                        if p.startswith("zentao/bug-resolve-"):
                            bug["status"] = "resolved"
                            bug["resolution"] = form.get("resolution") or "fixed"
                        # 真机形状：老禅道 POST 动作成功回 js::locate HTML（2026-09-20 实测）
                        self._js_html("/zentao/bug-view-%s.json" % m)
                        return
                    self._reply(404, {"error": "unknown old POST %s" % self.path})
                    return
                srv.calls.append(("POST", self.path, self._body()))
                if self.path.endswith("/tokens"):
                    want = "/zentao/api.php/v1" if srv.subpath else "/api.php/v1"
                    if self.path[:-len("/tokens")] != want:
                        self._reply(404, {"error": "no such route"})
                        return
                    b = srv.calls[-1][2]
                    if b.get("account") == "coder" and b.get("password") == "pw":
                        self._reply(200, {"token": "T-%d" % len(srv.calls)})
                    else:
                        self._reply(401, {"error": "账号或密码不对"})
                    return
                if not self._auth():
                    return
                parts = self._parts()
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
                    b = srv.calls[-1][2]
                    if b.get("assignedTo"):
                        bug["assignedTo"] = {"account": b["assignedTo"], "realname": b["assignedTo"]}
                    self._reply(200, bug)
                    return
                self._reply(404, {"error": "unknown PUT"})

            def do_GET(self):
                srv.calls.append(("GET", self.path, None))
                if srv.web_style and urlparse(self.path).path.rstrip("/") in ("", "/zentao"):
                    # web 根按配置的路由形态回登录跳转（bug 链接形态探测的目标应答）
                    self._js_html("/zentao/user-login-td.html" if srv.web_style == "pathinfo"
                                  else "index.php?m=user&f=login")
                    return
                fp = urlparse(self.path).path
                if fp in srv.files:
                    raw = srv.files[fp]
                    self.send_response(200)
                    self.send_header("Content-Type", "application/octet-stream")
                    self.send_header("Content-Length", str(len(raw)))
                    self.end_headers()
                    self.wfile.write(raw)
                    return
                if srv.old:
                    p = urlparse(self.path).path.lstrip("/")
                    if p == "zentao/api-getsessionid.json":
                        srv.old_session_n += 1
                        srv.old_sid = "S%d" % srv.old_session_n
                        self._reply(200, {"status": "success", "data": json.dumps(
                            {"sessionName": "zentaosid", "sessionID": srv.old_sid,
                             "rand": 1234})})
                        return
                    # 未登录 → 登录页重定向 HTML（非 JSON，适配层按会话死处理）
                    if ("zentaosid=" + srv.old_sid) not in self.path:
                        self._redirect_html()
                        return
                    if p.startswith("zentao/bug-browse-"):
                        seg = p.split(".")[0][len("zentao/bug-browse-"):]
                        pid = int(seg.split("-")[0])     # 兼容路径参数形态 bug-browse-13-0-unclosed-…
                        bugs = [b for b in srv.bugs.values() if int(b.get("product") or 0) == pid]
                        for b in bugs:
                            b["assignedTo"] = ["coder", "码蜂"]      # 老版数组形态
                        self._reply(200, {"status": "success", "data": json.dumps(
                            {"bugs": bugs,
                             "pager": {"recTotal": len(bugs), "recPerPage": 100, "pageID": 1}})})
                        return
                    if p.startswith("zentao/bug-view-"):
                        bid = p[len("zentao/bug-view-"):].split(".")[0]
                        bug = srv.bugs.get(bid)
                        if bug is None:
                            self._reply(404, {"error": "no such bug"})
                            return
                        self._reply(200, {"status": "success", "data": json.dumps({"bug": bug})})
                        return
                    if p == "zentao/api-getmodel-product-getpairs.json":
                        pairs = {str(k): v["name"] for k, v in srv.products.items()}
                        self._reply(200, {"status": "success", "data": json.dumps(pairs)})
                        return
                    if p == "zentao/api-getmodel-user-getpairs.json":
                        self._reply(200, {"status": "success", "data": json.dumps(
                            {u["account"]: u["realname"] for u in srv.users})})
                        return
                    if p.startswith("zentao/tree-browse-"):
                        # 真机形状（2026-09-22 实探）：module 视图 status=success
                        # 但树为空；bug/story 视图才回完整嵌套树
                        seg = p.split(".")[0][len("zentao/tree-browse-"):]
                        pid, vt = seg.split("-")[0], seg.split("-")[1]
                        if srv.old_modules_js:
                            self._js_html("/zentao/tree-browse-%s-bug.json" % pid)
                            return
                        if vt == "module":
                            self._reply(200, {"status": "success", "data": json.dumps(
                                {"sons": [], "tree": [], "viewType": "module",
                                 "modules": ""})})
                            return
                        mods = srv.modules.get(int(pid)) or []
                        tree = [{"id": m["id"], "name": m["name"], "parent": "0",
                                 "children": []} for m in mods]
                        if tree:
                            tree[0]["children"] = [{"id": int("%d0" % tree[0]["id"]),
                                                    "name": tree[0]["name"] + "/子",
                                                    "parent": tree[0]["id"],
                                                    "children": []}]
                        self._reply(200, {"status": "success", "data": json.dumps(
                            {"sons": tree, "tree": tree, "viewType": vt})})
                        return
                    self._reply(404, {"error": "unknown old GET %s" % self.path})
                    return
                if not self._auth():
                    return
                parts = self._parts()
                if parts == ["products"]:
                    plist = list(srv.products.values())
                    self._reply(200, {"products": plist, "total": len(plist),
                                      "page": 1, "limit": 100})
                    return
                if parts == ["users"]:
                    self._reply(200, {"users": srv.users, "total": len(srv.users),
                                      "page": 1, "limit": 100})
                    return
                if len(parts) == 3 and parts[0] == "products" and parts[2] == "bugs":
                    pid = int(parts[1])
                    bugs = [b for b in srv.bugs.values() if int(b.get("product") or 0) == pid]
                    query = parse_qs(urlparse(self.path).query)
                    status = (query.get("status") or [""])[0]
                    assigned = (query.get("assignedTo") or [""])[0]
                    if status:
                        bugs = [b for b in bugs if str(b.get("status") or "") == status]
                    if assigned:
                        def acct(value):
                            if isinstance(value, dict):
                                return str(value.get("account") or "")
                            if isinstance(value, (list, tuple)) and value:
                                return str(value[0] or "")
                            return str(value or "")
                        bugs = [b for b in bugs if acct(b.get("assignedTo")) == assigned]
                    self._reply(200, {"bugs": bugs, "total": len(bugs),
                                      "page": 1, "limit": 100})
                    return
                if len(parts) == 3 and parts[0] == "products" and parts[2] == "modules":
                    mods = srv.modules.get(int(parts[1]))
                    if mods is None:
                        self._reply(404, {"error": "no such product"})
                    else:
                        self._reply(200, {"modules": mods})
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

        def fake_launch(bug, profile, side, cfg):
            repo = zentao._repo_of(profile, side)
            payload = {"type": "code", "goal": zentao._goal_text(bug, side),
                       "workdir": str(repo.get("workdir") or self.workdir),
                       "title": ("[禅道#%s][%s] %s" % (bug.get("id"), side, bug.get("title") or ""))[:60]}
            if str(repo.get("git_rev") or "").strip():
                payload["git_rev"] = repo["git_rev"]
            if str(repo.get("verify_command") or "").strip():
                payload["verify_command"] = repo["verify_command"]
            task = store.create_task(payload)
            run = store.create_run("orchestration", task["title"], task_id=task["id"])
            store.update_task_status(task["id"], "queued")
            self.launched.append({"task": task, "run": run, "bug": bug, "side": side})
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

    def profile(self, **kw):
        p = {"product": 1, "assigned_to": "coder", "severity_cap": 0,
             "our_sides": ["backend"],
             "repos": {"backend": {"workdir": str(self.workdir), "git_rev": "",
                                   "verify_command": ""},
                       "frontend": {"workdir": "", "git_rev": "", "verify_command": ""}},
             "repo_hints": {"backend": "", "frontend": ""},
             "owners": {"backend": "be-owner", "frontend": "fe-owner", "not_ours": ""},
             "module_routes": [{"module": 99, "side": "backend", "account": ""}]}
        for k, v in kw.items():
            if k == "repos":
                for side in ("backend", "frontend"):
                    p["repos"].setdefault(side, {})
                    p["repos"][side].update(v.get(side) or {})
            else:
                p[k] = v
        return p

    def configure(self, profiles=None, **kw):
        cfg = {"base_url": "http://127.0.0.1:%d" % self.port,
               "account": "coder", "password": "pw"}
        cfg.update(kw)
        cfg["product_profiles"] = profiles if profiles is not None else [self.profile()]
        return self.zen_mod.save_config(cfg)

    def bug(self, bid=101, **kw):
        b = {"id": bid, "product": 1, "title": "登录页 500", "module": 99,
             "steps": "<p>打开<b>登录页</b></p><br>报 500",
             "severity": 2, "pri": 2, "type": "codeerror",
             "status": "active", "os": "win", "browser": "edge",
             "keywords": "", "assignedTo": {"account": "coder", "realname": "码蜂"},
             "openedBy": {"account": "tester", "realname": "测试君"}}
        b.update(kw)
        self.fz.bugs[str(bid)] = b
        return b

    def claim(self):
        claims = self.zen_mod.view()["claims"]
        return claims[0] if claims else None

    def resolve_calls(self, bid):
        return [x for x in self.fz.calls
                if x[0] == "POST" and x[1].endswith("/bugs/%s/resolve" % bid)]

    def put_calls(self, bid):
        return [x for x in self.fz.calls
                if x[0] == "PUT" and x[1].endswith("/bugs/%s" % bid)]


class TestGuardUrl(ZenCase):
    def runTest(self):
        ZenError = self.zen_mod.ZenError
        for url in ("http://127.0.0.1:9999/api.php/v1/tokens",
                    "http://localhost:88/zentao/api.php/v1/tokens",
                    "http://192.168.1.10/zentao/api.php/v1/tokens"):
            self.assertEqual(self.zen_mod._guard_url(url), url)
        for bad in ("ftp://x/tokens", "file:///etc/passwd",
                    "http://169.254.169.254/latest/meta-data",
                    "http://metadata.google.internal/computeMetadata/v1/"):
            with self.assertRaises(ZenError):
                self.zen_mod._guard_url(bad)


class TestFiltersAndHtml(ZenCase):
    def runTest(self):
        f = self.zen_mod._claimable
        prof = self.profile()
        self.assertTrue(f({"status": "active", "assignedTo": {"account": "coder"}}, prof))
        self.assertFalse(f({"status": "resolved", "assignedTo": {"account": "coder"}}, prof))
        self.assertFalse(f({"status": "active", "assignedTo": "someone-else"}, prof))
        prof_sev = self.profile(severity_cap=2)
        self.assertTrue(f({"status": "active", "assignedTo": "coder", "severity": 1}, prof_sev))
        self.assertFalse(f({"status": "active", "assignedTo": "coder", "severity": 3}, prof_sev))
        # HTML 剥离
        txt = self.zen_mod._strip_html("<p>打开<b>登录页</b></p><br>报 500")
        self.assertIn("登录页", txt)
        self.assertIn("报 500", txt)
        self.assertNotIn("<", txt)


class TestServerSideBugFilters(ZenCase):
    def runTest(self):
        """REST 列表请求先按激活状态/指派人过滤，仍保留本地兜底过滤。"""
        self.configure()
        cfg = self.zen_mod._cfg()
        profile = self.zen_mod._profiles(cfg)[0]
        self.bug(121)
        self.bug(122, assignedTo={"account": "someone-else"})
        self.bug(123, status="resolved")

        bugs = self.zen_mod.list_bugs(cfg, 1, profile)

        self.assertEqual([b["id"] for b in bugs], [121])
        calls = [path for method, path, _ in self.fz.calls
                 if method == "GET" and "/products/1/bugs" in path]
        self.assertEqual(len(calls), 1)
        query = parse_qs(urlparse(calls[0]).query)
        self.assertEqual(query.get("status"), ["active"])
        self.assertEqual(query.get("assignedTo"), ["coder"])


class TestConfigAndMigration(ZenCase):
    def runTest(self):
        from app.core import zentao
        # 档案校验：product 重复拒绝
        with self.assertRaises(ValueError):
            zentao.save_config({"product_profiles": [self.profile(), self.profile()]})
        # 非法 side 拒绝
        bad2 = self.profile()
        bad2["module_routes"] = [{"module": 5, "side": "left", "account": ""}]
        with self.assertRaises(ValueError):
            zentao.save_config({"product_profiles": [bad2]})
        # 正常保存 + 脱敏（工作目录留空合法：回落默认保存路径）
        self.configure()
        v = zentao.view()
        self.assertTrue(v["config"]["has_password"])
        self.assertEqual(v["config"]["password"], "")
        self.assertEqual(v["config"]["product_profiles"][0]["owners"]["frontend"], "fe-owner")
        # 相对路径工作目录拒绝
        bad3 = self.profile()
        bad3["repos"]["backend"]["workdir"] = "rel/path"
        with self.assertRaises(ValueError):
            zentao.save_config({"product_profiles": [bad3]})

    def testLegacyMigration(self):
        """老扁平配置（products+单仓库）load 时迁移成产品档案。"""
        self.zen_mod._FILE.write_text(json.dumps({
            "version": 1,
            "config": {"base_url": "http://x", "account": "a", "password": "p",
                       "products": [3, 7], "assigned_to": "coder", "severity_cap": 2,
                       "workdir": str(self.workdir), "git_rev": "main",
                       "verify_command": "pytest"},
            "claims": {},
        }, ensure_ascii=False), encoding="utf-8")
        self.zen_mod.load(force=True)
        cfg = self.zen_mod.view()["config"]
        profs = cfg["product_profiles"]
        self.assertEqual([p["product"] for p in profs], [3, 7])
        self.assertEqual(profs[0]["repos"]["backend"]["workdir"], str(self.workdir))
        self.assertEqual(profs[0]["repos"]["backend"]["git_rev"], "main")
        self.assertEqual(profs[0]["our_sides"], ["backend"])
        self.assertEqual(profs[0]["assigned_to"], "coder")

    def testLegacyClaimMigration(self):
        """老 claim（单 task_id/run_id）归一为 tasks 数组。"""
        self.zen_mod._FILE.write_text(json.dumps({
            "version": 1,
            "config": {"product_profiles": [self.profile()]},
            "claims": {"555": {"bug_id": 555, "product": 1, "title": "老 claim",
                               "task_id": "t-old", "run_id": "r-old", "state": "fixing",
                               "note": "", "attempts": 0,
                               "claimed_at": "2026-09-19 08:00:00"}},
        }, ensure_ascii=False), encoding="utf-8")
        self.zen_mod.load(force=True)
        c = self.claim()
        self.assertEqual(len(c["tasks"]), 1)
        self.assertEqual(c["tasks"][0]["task_id"], "t-old")
        self.assertEqual(c["tasks"][0]["side"], "backend")


class TestBugWebBase(ZenCase):
    def runTest(self):
        """view() 带 bug_base：探测出的子路径地基优先，没探测过回落原始地址。"""
        self.configure()
        base = "http://127.0.0.1:%d" % self.port
        v = self.zen_mod.view()
        self.assertEqual(v["bug_base"], base)
        # 老接口探测命中 /zentao 子路径 → web 根带子路径（用户真机形态）
        self.zen_mod._MODE[base] = "old"
        self.zen_mod._OLD["api"] = base + "/zentao"
        self.assertEqual(self.zen_mod.view()["bug_base"], base + "/zentao")
        # REST 根探测命中 → 剥掉 api.php/v1 尾巴
        self.zen_mod._MODE.clear()
        self.zen_mod._OLD.update(api="", sid="", at=0.0)
        self.zen_mod._APIBASE[base] = base + "/api.php/v1"
        self.assertEqual(self.zen_mod.view()["bug_base"], base)
        # 误把 REST 根存进了配置且未探测 → 回落也剥尾巴；非法值回空
        self.zen_mod._APIBASE.clear()
        self.zen_mod.save_config({"base_url": base + "/api.php/v1", "account": "coder",
                                  "password": "pw", "product_profiles": [self.profile()]})
        self.assertEqual(self.zen_mod.view()["bug_base"], base)
        self.zen_mod.save_config({"base_url": "", "account": "coder", "password": "pw",
                                  "product_profiles": [self.profile()]})
        self.assertEqual(self.zen_mod.view()["bug_base"], "")


class TestBugLinkStyle(ZenCase):
    def runTest(self):
        """bug 详情链接形态：默认伪静态 bug-view-N.html（真机 2026-09-23 实证
        伪静态部署对 GET 式链接不路由），探测到 GET 形态部署才回退查询串。"""
        self.configure()
        base = "http://127.0.0.1:%d" % self.port
        # 没探出来（假服务器根路径 401）→ probe 空串，view 按官方默认伪静态
        self.assertEqual(self.zen_mod.probe_web_style(base), "")
        self.assertEqual(self.zen_mod.view()["bug_style"], "pathinfo")
        # 根路径回伪静态登录跳转 → 探出 pathinfo 并进 view()
        self.fz.web_style = "pathinfo"
        self.assertEqual(self.zen_mod.probe_web_style(base), "pathinfo")
        self.assertEqual(self.zen_mod.view()["bug_style"], "pathinfo")
        # 探出后缓存生效：假服务器换形态，不清缓存维持原判
        self.fz.web_style = "get"
        self.assertEqual(self.zen_mod.probe_web_style(base), "pathinfo")
        # 清缓存重探 → get；view() 跟着标 get
        self.zen_mod._WEBSTYLE.clear()
        self.assertEqual(self.zen_mod.probe_web_style(base), "get")
        self.assertEqual(self.zen_mod.view()["bug_style"], "get")
        # 非法地址探不出来，不抛
        self.assertEqual(self.zen_mod.probe_web_style("ftp://x"), "")


class TestTriage(ZenCase):
    def runTest(self):
        cfg = self.configure()
        prof = self.zen_mod._profiles(cfg)[0]
        # ① 模块规则命中：绝不调 AI
        called = []
        orig_ai = self.zen_mod._ai_triage
        self.zen_mod._ai_triage = lambda b, p: called.append(1) or {"side": "frontend"}
        try:
            tri = self.zen_mod._triage({"module": 99}, prof, cfg)
            self.assertEqual(tri["side"], "backend")
            self.assertEqual(tri["by"], "rule")
            self.assertEqual(called, [], "规则命中不得调 AI")
        finally:
            self.zen_mod._ai_triage = orig_ai
        # ② 未命中走 AI：AI 判 frontend
        self.zen_mod._ai_triage = lambda b, p: {"side": "frontend", "reason": "页面白屏"}
        try:
            tri = self.zen_mod._triage({"module": 5}, prof, cfg)
            self.assertEqual(tri["side"], "frontend")
            self.assertEqual(tri["by"], "ai")
        finally:
            self.zen_mod._ai_triage = orig_ai
        # ③ AI 不可用 → unknown
        self.zen_mod._ai_triage = lambda b, p: None
        try:
            tri = self.zen_mod._triage({"module": 5}, prof, cfg)
            self.assertEqual(tri["side"], "unknown")
        finally:
            self.zen_mod._ai_triage = orig_ai
        # ④ triage_ai 关 → 未命中直接 unknown，不调 AI
        cfg_off = self.configure(triage_ai=False)
        tri = self.zen_mod._triage({"module": 5}, prof, cfg_off)
        self.assertEqual(tri["side"], "unknown")


class TestScanFixResolve(ZenCase):
    """主链路：我方端 bug → 建任务 → 全部 done → resolve+指回报告人。"""
    def runTest(self):
        self.configure()
        self.bug(101)
        res = self.zen_mod.scan_now()
        self.assertTrue(res["ok"], res)
        self.assertEqual(res["claimed"], 1)
        c = self.claim()
        self.assertEqual(c["state"], "fixing")
        self.assertEqual(c["triage"]["side"], "backend")
        self.assertEqual(c["triage"]["by"], "rule")
        self.assertEqual(len(c["tasks"]), 1)
        self.assertEqual(c["tasks"][0]["side"], "backend")
        task = self.store.get_task(c["tasks"][0]["task_id"])
        self.assertEqual(task["type"], "code")
        self.assertTrue(task["title"].startswith("[禅道#101]"))
        self.assertIn("登录页", task["goal"])
        self.assertIn("只负责【后端】部分", task["goal"])
        # 去重
        self.assertEqual(self.zen_mod.scan_now()["claimed"], 0)
        # run 全 done → resolve
        self.store.update_run(c["tasks"][0]["run_id"], status="done",
                              verdict={"pass": True, "publishable": True})
        self.assertTrue(self.zen_mod.scan_now()["ok"])
        c = self.claim()
        self.assertEqual(c["state"], "resolved")
        bug = self.fz.bugs["101"]
        self.assertEqual(bug["status"], "resolved")
        rc = self.resolve_calls("101")
        self.assertEqual(len(rc), 1)
        self.assertEqual(rc[0][2]["resolution"], "fixed")
        self.assertEqual(rc[0][2]["assignedTo"], "tester")
        self.assertIn("自动修复报告", rc[0][2]["comment"])


class TestReconcileFollowsRetry(ZenCase):
    """对账认任务最新 run：首跑失败后人工重试成功，回写按成功走（#27697 案）。"""
    def runTest(self):
        import time as _t
        self.configure()
        self.bug(110)
        self.zen_mod.scan_now()
        c = self.claim()
        tid = c["tasks"][0]["task_id"]
        old_rid = c["tasks"][0]["run_id"]
        self.store.update_run(old_rid, status="failed", error="网络抖动")
        _t.sleep(1.05)   # run id 是秒级时间戳+随机后缀，确保重试 run 排序更新
        r2 = self.store.create_run("orchestration", "人工重试", task_id=tid)
        self.store.update_run(r2["id"], status="done", verdict={"pass": True})
        self.zen_mod.scan_now()
        c = self.claim()
        self.assertEqual(c["state"], "resolved")
        self.assertEqual(c["tasks"][0]["run_id"], r2["id"], "claim 换绑到重试 run")
        self.assertEqual(len(self.resolve_calls("110")), 1)


class TestResolveGateUncommitted(ZenCase):
    """没基线的任务：工作区有未提交的已跟踪改动 → 拦 resolve 转人工（落库闸）。"""
    def runTest(self):
        from app.core import runner as _r
        repo = self.tmp / "repo"
        repo.mkdir()

        def git(*args):
            r = _r.run_process(argv=["git", "-C", str(repo)] + list(args), timeout=30)
            self.assertTrue(r["ok"], r)
            return r

        git("init")
        (repo / "a.txt").write_text("v1\n", encoding="utf-8")
        git("add", "a.txt")
        git("-c", "user.email=t@t", "-c", "user.name=t", "commit", "-m", "init")
        (repo / "a.txt").write_text("v2 改了没提交\n", encoding="utf-8")
        self.configure(profiles=[self.profile(repos={"backend": {"workdir": str(repo)}})])
        self.bug(111)
        self.zen_mod.scan_now()
        c = self.claim()
        self.store.update_run(c["tasks"][0]["run_id"], status="done",
                              verdict={"pass": True})
        self.zen_mod.scan_now()
        c = self.claim()
        self.assertEqual(c["state"], "done_manual")
        self.assertIn("未提交", c["note"])
        self.assertEqual(self.resolve_calls("111"), [])


class TestLaunchAutoBaseline(ZenCase):
    """档案没配基线：落单自动取工作目录 HEAD，修复走任务分支隔离。"""
    def runTest(self):
        from app.core import jobs, runner as _r
        repo = self.tmp / "repo2"
        repo.mkdir()
        for args in (["init"],
                     ["-c", "user.email=t@t", "-c", "user.name=t",
                      "commit", "--allow-empty", "-m", "init"]):
            r = _r.run_process(argv=["git", "-C", str(repo)] + args, timeout=30)
            self.assertTrue(r["ok"], r)
        head = _r.run_process(argv=["git", "-C", str(repo), "rev-parse", "--short", "HEAD"],
                              timeout=20)["stdout"].strip()
        self.zen_mod._launch_fix = self._orig_launch
        with mock.patch.object(jobs, "enqueue"):
            task, run = self.zen_mod._launch_fix(
                self.bug(112), self.profile(repos={"backend": {"workdir": str(repo)}}),
                "backend", {})
        self.assertEqual(task["git_rev"], head)
        self.store.update_run(run["id"], status="failed")   # 收口，别留 queued


class TestBootReconcile(ZenCase):
    """重启补对账：fixing 挂单在 start() 后被兜底收口，不等下个 tick。"""
    def runTest(self):
        self.configure()
        self.bug(113)
        self.zen_mod.scan_now()
        c = self.claim()
        self.store.update_run(c["tasks"][0]["run_id"], status="failed", error="断网")
        self.zen_mod.start()
        timer = self.zen_mod._BOOT_TIMER
        self.assertIsNotNone(timer, "有 fixing 挂单时安排补对账")
        self.addCleanup(timer.cancel)
        self.zen_mod._boot_reconcile()
        self.assertEqual(self.claim()["state"], "escalated")


class TestLaunchFailureClosesRun(ZenCase):
    """执行器拒绝启动时，禅道任务和运行都必须立即失败，不能留下 queued。"""
    def runTest(self):
        from app.core import jobs
        self.zen_mod._launch_fix = self._orig_launch
        bug = self.bug(102)
        profile = self.profile()
        with mock.patch.object(jobs, "enqueue", side_effect=jobs.JobsBusyError("busy")):
            with self.assertRaises(jobs.JobsBusyError):
                self.zen_mod._launch_fix(bug, profile, "backend", {})
        self.assertEqual(len(self.store._TASKS), 1)
        self.assertEqual(len(self.store._RUNS), 1)
        task = next(iter(self.store._TASKS.values()))
        run = next(iter(self.store._RUNS.values()))
        self.assertEqual(task["status"], "failed")
        self.assertEqual(run["status"], "failed")
        self.assertIn("本次未排队", run.get("error") or "")


class TestNotOursTransfer(ZenCase):
    """非我方：只转派不解决（指回报告人）。"""
    def runTest(self):
        self.configure(profiles=[self.profile(
            module_routes=[{"module": 77, "side": "not_ours", "account": ""}])])
        self.bug(201, module=77)
        self.assertTrue(self.zen_mod.scan_now()["ok"])
        c = self.claim()
        self.assertEqual(c["state"], "transferred")
        self.assertEqual(c["triage"]["side"], "not_ours")
        puts = self.put_calls("201")
        self.assertEqual(len(puts), 1)
        self.assertEqual(puts[0][2]["assignedTo"], "tester", "not_ours 无配置 → 指回报告人")
        self.assertIn("非我方", puts[0][2]["comment"])
        self.assertEqual(self.resolve_calls("201"), [], "只转派绝不 resolve")
        # bug 保持激活
        self.assertEqual(self.fz.bugs["201"]["status"], "active")


class TestWrongAssigneeReroute(ZenCase):
    """纯对方端问题（测试指错到我方账号）：不改状态直接转给正确负责人。"""
    def runTest(self):
        cfg = self.configure()
        self.bug(301, module=5, assignedTo={"account": "coder"})   # 指错到我方
        # AI 兜底判 frontend：打桩 _ai_triage
        orig = self.zen_mod._ai_triage
        self.zen_mod._ai_triage = lambda b, p: {"side": "frontend", "reason": "页面白屏"}
        try:
            res = self.zen_mod.scan_now()
        finally:
            self.zen_mod._ai_triage = orig
        self.assertTrue(res["ok"], res)
        c = self.claim()
        self.assertEqual(c["state"], "transferred")
        self.assertEqual(c["triage"]["side"], "frontend")
        self.assertEqual(c["triage"]["by"], "ai")
        self.assertEqual(self.launched, [], "纯对方端不建修复任务")
        puts = self.put_calls("301")
        self.assertEqual(len(puts), 1)
        self.assertEqual(puts[0][2]["assignedTo"], "fe-owner", "转给前端负责人")
        self.assertIn("前端", puts[0][2]["comment"])
        self.assertIn("页面白屏", puts[0][2]["comment"])
        self.assertEqual(self.resolve_calls("301"), [])


class TestRouteAccountPriority(ZenCase):
    """转派目标优先级：模块路由 account > 端负责人。"""
    def runTest(self):
        self.configure(profiles=[self.profile(
            module_routes=[{"module": 77, "side": "frontend", "account": "route-guy"}])])
        self.bug(311, module=77)
        self.assertTrue(self.zen_mod.scan_now()["ok"])
        puts = self.put_calls("311")
        self.assertEqual(puts[0][2]["assignedTo"], "route-guy")


class TestBothSidesOurBackend(ZenCase):
    """双端问题、我方只管后端：修完合并后转派前端负责人，不 resolve。"""
    def runTest(self):
        cfg = self.configure(profiles=[self.profile(
            repos={"backend": {"workdir": str(self.workdir), "git_rev": "main"}},
            module_routes=[])])
        self.bug(401, module=5)
        # 合并成功桩要罩住「建任务」与「对账合并」两次扫描
        orig = self.zen_mod._ai_triage
        orig_merge = self.zen_mod._merge_branch
        self.zen_mod._ai_triage = lambda b, p: {"side": "both", "reason": "接口+页面都要改"}
        self.zen_mod._merge_branch = lambda wd, t: (True, "", {"commit": "abc1234"})
        try:
            res = self.zen_mod.scan_now()
            self.assertTrue(res["ok"], res)
            c = self.claim()
            self.assertEqual(c["state"], "fixing")
            self.assertEqual(len(c["tasks"]), 1, "我方只有后端：只建一个任务")
            task = self.store.get_task(c["tasks"][0]["task_id"])
            self.assertEqual(task.get("git_rev"), "main")
            self.store.update_run(c["tasks"][0]["run_id"], status="done",
                                  verdict={"pass": True},
                                  git={"commit": "abc1234", "from_branch": "main"})
            self.assertTrue(self.zen_mod.scan_now()["ok"])
        finally:
            self.zen_mod._ai_triage = orig
            self.zen_mod._merge_branch = orig_merge
        c = self.claim()
        self.assertEqual(c["state"], "transferred")
        self.assertEqual(self.resolve_calls("401"), [], "双端未齐不 resolve")
        puts = self.put_calls("401")
        self.assertEqual(len(puts), 1)
        self.assertEqual(puts[0][2]["assignedTo"], "fe-owner")
        self.assertIn("排查转派", puts[0][2]["comment"])
        self.assertIn("abc1234", puts[0][2]["comment"], "转派评论带我方修复提交")
        self.assertEqual(self.fz.bugs["401"]["status"], "active")


class TestBothSidesMergeFail(ZenCase):
    """双端问题、合并失败：不转派不 resolve（不带着没落库的修复转派）。"""
    def runTest(self):
        # git_rev 配了但工作目录不是 git 仓库 → 真实 gitmod 必拒
        cfg = self.configure(profiles=[self.profile(
            repos={"backend": {"workdir": str(self.workdir), "git_rev": "main"}},
            module_routes=[])])
        self.bug(411, module=5)
        orig = self.zen_mod._ai_triage
        self.zen_mod._ai_triage = lambda b, p: {"side": "both"}
        try:
            self.zen_mod.scan_now()
        finally:
            self.zen_mod._ai_triage = orig
        c = self.claim()
        self.store.update_run(c["tasks"][0]["run_id"], status="done")
        self.zen_mod.scan_now()
        c = self.claim()
        self.assertEqual(c["state"], "merge_failed")
        self.assertEqual(self.resolve_calls("411"), [])
        self.assertEqual(self.put_calls("411"), [], "合并失败不转派")


class TestFailureEscalate(ZenCase):
    """修复任务失败：评论尝试记录 + 转派该端负责人，不 resolve。"""
    def runTest(self):
        self.configure()
        self.bug(501)
        self.zen_mod.scan_now()
        c = self.claim()
        self.store.update_run(c["tasks"][0]["run_id"], status="failed",
                              error="验证命令退出码 1")
        self.zen_mod.scan_now()
        c = self.claim()
        self.assertEqual(c["state"], "escalated")
        puts = self.put_calls("501")
        self.assertEqual(len(puts), 1)
        self.assertEqual(puts[0][2]["assignedTo"], "be-owner", "失败转后端负责人")
        self.assertIn("未成功", puts[0][2]["comment"])
        self.assertIn("验证命令退出码 1", puts[0][2]["comment"])
        self.assertEqual(self.resolve_calls("501"), [])


class TestFailureNoOwner(ZenCase):
    """失败但没配负责人：只评论（commented），不误转，且不清空指派人。"""
    def runTest(self):
        self.configure(profiles=[self.profile(owners={})])
        self.bug(511)
        self.zen_mod.scan_now()
        c = self.claim()
        self.store.update_run(c["tasks"][0]["run_id"], status="failed", error="boom")
        self.zen_mod.scan_now()
        c = self.claim()
        self.assertEqual(c["state"], "commented")
        self.assertIn("负责人未配置", c["note"])
        puts = self.put_calls("511")
        self.assertEqual(len(puts), 1)
        self.assertEqual(puts[0][2].get("assignedTo"), "coder",
                         "空 target 只评论必须带回当前指派人（禅道 PUT 缺 assignedTo 会清空指派）")
        self.assertEqual(self.fz.bugs["511"]["assignedTo"]["account"], "coder")
        self.assertEqual(self.resolve_calls("511"), [])


class TestFailureInternalCrash(ZenCase):
    """CodeBee 自身崩溃（run.error 是内部异常）：只评论不转派（#27783 案），
    评论注明工具异常且带回当前指派人。"""
    def runTest(self):
        self.configure()
        self.bug(521)
        self.zen_mod.scan_now()
        c = self.claim()
        self.store.update_run(
            c["tasks"][0]["run_id"], status="failed",
            error="NameError(\"name '_TRANSIENT' is not defined\")")
        self.zen_mod.scan_now()
        c = self.claim()
        self.assertEqual(c["state"], "commented", "内部异常不转派")
        self.assertIn("内部异常", c["note"])
        puts = self.put_calls("521")
        self.assertEqual(len(puts), 1)
        self.assertEqual(puts[0][2].get("assignedTo"), "coder", "崩溃评论不清指派人")
        self.assertIn("工具自身异常", puts[0][2]["comment"])
        self.assertIn("未成功", puts[0][2]["comment"])
        self.assertEqual(self.resolve_calls("521"), [])


class TestInternalCrashDetector(ZenCase):
    """内部崩溃判别：repr 异常/Python traceback 算；CLI 冒号形态与普通
    错误文案不算（那是真修过）。"""
    def runTest(self):
        f = self.zen_mod._looks_internal_crash
        self.assertTrue(f("NameError(\"name '_TRANSIENT' is not defined\")"))
        self.assertTrue(f("KeyError('run_id')"))
        self.assertTrue(f("Traceback (most recent call last):\n  File \"x.py\"\nNameError: x"))
        self.assertFalse(f("验证命令退出码 1"))
        self.assertFalse(f("TimeoutError: 连接超时"))
        self.assertFalse(f("运行状态 failed"))
        self.assertFalse(f(""))
        self.assertFalse(f(None))


class TestNeedManualAndRetry(ZenCase):
    """AI 关+模块未命中 → need_manual 不碰 bug；配好路由后重排查建任务。"""
    def runTest(self):
        self.configure(triage_ai=False,
                       profiles=[self.profile(module_routes=[])])
        self.bug(601, module=5)
        res = self.zen_mod.scan_now()
        self.assertTrue(res["ok"], res)
        c = self.claim()
        self.assertEqual(c["state"], "need_manual")
        self.assertEqual(self.put_calls("601"), [], "留人工不碰 bug")
        self.assertEqual(self.resolve_calls("601"), [])
        # 配好路由（模块 5 → 后端）再扫：重排查 → 建任务
        self.configure(triage_ai=False, profiles=[self.profile(
            module_routes=[{"module": 5, "side": "backend", "account": ""}])])
        self.zen_mod.scan_now()
        c = self.claim()
        self.assertEqual(c["state"], "fixing")
        self.assertEqual(len(c["tasks"]), 1)


class TestTriageAiUnavailable(ZenCase):
    """triage_ai 开但无可用供应商（测试环境无 orchestrator）→ unknown 留人工。"""
    def runTest(self):
        self.configure(profiles=[self.profile(module_routes=[])])
        self.bug(621, module=5)
        self.assertTrue(self.zen_mod.scan_now()["ok"])
        self.assertEqual(self.claim()["state"], "need_manual")


class TestDualSideBothOurs(ZenCase):
    """双端且我方两端都负责：建两个任务，全部 done 后 resolve。"""
    def runTest(self):
        self.configure(profiles=[self.profile(
            our_sides=["backend", "frontend"],
            repos={"backend": {"workdir": str(self.workdir)},
                   "frontend": {"workdir": str(self.workdir)}},
            module_routes=[])])
        self.bug(701, module=5)
        orig = self.zen_mod._ai_triage
        self.zen_mod._ai_triage = lambda b, p: {"side": "both"}
        try:
            res = self.zen_mod.scan_now()
        finally:
            self.zen_mod._ai_triage = orig
        self.assertTrue(res["ok"], res)
        c = self.claim()
        self.assertEqual(len(c["tasks"]), 2)
        self.assertEqual(sorted(t["side"] for t in c["tasks"]), ["backend", "frontend"])
        for t in c["tasks"]:
            self.store.update_run(t["run_id"], status="done")
        self.zen_mod.scan_now()
        self.assertEqual(self.claim()["state"], "resolved")
        self.assertEqual(len(self.resolve_calls("701")), 1, "两端齐了只 resolve 一次")


class TestModuleFetch(ZenCase):
    def runTest(self):
        self.configure()
        r = self.zen_mod.fetch_modules(1)
        self.assertTrue(r["ok"])
        self.assertEqual([m["id"] for m in r["modules"]], [99, 77])
        # 不存在的产品 → 404 人话报错
        r2 = self.zen_mod.fetch_modules(42)
        self.assertFalse(r2["ok"])
        self.assertIn("手工填写", r2["error"])


class TestToken401Retry(ZenCase):
    def runTest(self):
        self.fz.reject_next_auth = True
        self.configure()
        self.bug(801)
        res = self.zen_mod.scan_now()
        self.assertTrue(res["ok"], res)
        self.assertEqual(res["claimed"], 1)


class TestFireDueGate(ZenCase):
    def runTest(self):
        self.configure()                    # poll_enabled 默认 False
        self.assertEqual(self.zen_mod.fire_due(), None)
        self.bug(901)
        self.zen_mod.save_config({"poll_enabled": True})
        res = self.zen_mod.fire_due()
        self.assertIsNotNone(res)
        self.assertEqual(res["claimed"], 1)
        res2 = self.zen_mod.fire_due()
        self.assertEqual(res2.get("skipped"), "not_due")
        self.assertEqual(len(self.zen_mod.view()["claims"]), 1)


class TestScanSingleFlight(ZenCase):
    def runTest(self):
        """手动扫描与定时扫描不能并行重复拉取/认领同一批 Bug。"""
        self.configure()
        entered = threading.Event()
        release = threading.Event()
        second_done = threading.Event()
        second_result = []

        def slow_scan(_cfg):
            entered.set()
            self.assertTrue(release.wait(2), "slow scan did not get released")
            return 0

        try:
            with mock.patch.object(self.zen_mod, "_scan", side_effect=slow_scan):
                first = threading.Thread(target=self.zen_mod.scan_now, daemon=True)
                first.start()
                self.assertTrue(entered.wait(1), "first scan did not start")

                def run_second():
                    second_result.append(self.zen_mod.scan_now())
                    second_done.set()

                second = threading.Thread(target=run_second, daemon=True)
                second.start()
                self.assertTrue(second_done.wait(1), "second scan waited for the first scan")
                self.assertEqual(second_result[0].get("skipped"), "in_progress")
        finally:
            release.set()
            if "first" in locals():
                first.join(2)
            if "second" in locals():
                second.join(2)


class TestConnection(ZenCase):
    def runTest(self):
        ok, msg = self.zen_mod.test_connection(
            base_url="http://127.0.0.1:%d" % self.port, account="coder", password="wrong")
        self.assertFalse(ok)
        self.assertIn("密码", msg)
        self.bug(951)
        ok, msg = self.zen_mod.test_connection(
            base_url="http://127.0.0.1:%d" % self.port, account="coder", password="pw")
        self.assertTrue(ok, msg)
        ok, msg = self.zen_mod.test_connection(base_url="", account="a", password="b")
        self.assertFalse(ok)


class TestCatalogFetch(ZenCase):
    """产品/账号清单：下拉选择辅助。"""
    def runTest(self):
        self.configure()
        r = self.zen_mod.fetch_products()
        self.assertTrue(r["ok"], r)
        self.assertEqual([(p["id"], p["name"]) for p in r["products"]],
                         [(1, "产品一"), (2, "产品二")])
        r2 = self.zen_mod.fetch_users()
        self.assertTrue(r2["ok"], r2)
        self.assertEqual([u["account"] for u in r2["users"]], ["coder", "tester"])
        self.fz.products = {}
        r3 = self.zen_mod.fetch_products()
        self.assertFalse(r3["ok"])
        self.assertIn("产品", r3["error"])


class TestSubpathAutodetect(ZenCase):
    """根路径 REST 404 → 自动补 /zentao 子路径（一键安装包形态）。"""
    def runTest(self):
        self.fz.subpath = True
        base = "http://127.0.0.1:%d" % self.port
        self.configure(base_url=base)           # 用户填的根路径，没带子目录
        self.bug(851)
        res = self.zen_mod.scan_now()
        self.assertTrue(res["ok"], res)
        self.assertEqual(res["claimed"], 1)
        self.assertEqual(self.zen_mod.resolved_base_url(base), base + "/zentao")


class TestOldJsonApi(ZenCase):
    """REST 只认应用 code（老版禅道）→ 自动切老版 JSON 接口：会话+账密登录。
    账号字段用老版数组形态，顺带验证 _acct 兼容。"""
    def runTest(self):
        self.fz.old = True
        base = "http://127.0.0.1:%d" % self.port
        self.configure(base_url=base)
        ok, msg = self.zen_mod.test_connection()
        self.assertTrue(ok, msg)
        self.assertIn("老版", msg)
        r = self.zen_mod.fetch_products()
        self.assertTrue(r["ok"], r)
        self.assertEqual([(p["id"], p["name"]) for p in r["products"]],
                         [(1, "产品一"), (2, "产品二")])
        r2 = self.zen_mod.fetch_users()
        self.assertTrue(r2["ok"], r2)
        self.assertEqual([u["account"] for u in r2["users"]], ["coder", "tester"])
        self.bug(861)
        bugs = self.zen_mod.list_bugs(self.zen_mod._cfg(), 1)
        self.assertTrue(bugs)
        self.assertEqual(self.zen_mod._acct(bugs[0].get("assignedTo")), "coder")
        # 写回三通道：转派（PUT→bug-assignTo js::HTML）+ resolve（→bug-resolve js::HTML）
        c = self.zen_mod._cfg()
        self.zen_mod._transfer(c, 861, "tester", "转派通道测试")
        v = self.zen_mod._call("GET", "/bugs/861", cfg=c)
        self.assertEqual(self.zen_mod._acct(v.get("assignedTo")), "tester")
        self.assertEqual(str(v.get("status")), "active")
        self.zen_mod._ensure_resolved(c, 861, "resolve 通道测试", None)
        v2 = self.zen_mod._call("GET", "/bugs/861", cfg=c)
        self.assertEqual(str(v2.get("status")), "resolved")
        self.assertEqual(str(v2.get("resolution")), "fixed")


class TestModuleFetchOld(ZenCase):
    """老通道拉模块清单（2026-09-22 真机报错「响应顶层键: _list」）：
    该版禅道 module 视图回 success+空树（模块行 type=story），必须回落
    bug 视图（bug 表单模块下拉用的就是这棵树）。"""
    def runTest(self):
        self.fz.old = True
        base = "http://127.0.0.1:%d" % self.port
        self.configure(base_url=base)
        r = self.zen_mod.fetch_modules(1)
        self.assertTrue(r["ok"], r)
        self.assertEqual([m["id"] for m in r["modules"]], [99, 990, 77],
                         "module 视图空树要回落 bug 视图，且递归展开 children")
        # 三个视图都空 → 人话「模块树为空」，不是「形状不认识」也不是「没有该接口」
        self.fz.modules = {}
        r2 = self.zen_mod.fetch_modules(2)
        self.assertFalse(r2["ok"])
        self.assertIn("空", r2["error"])
        self.assertIn("手工填", r2["error"])
        self.assertNotIn("没有该接口", r2["error"])
        self.assertNotIn("_list", r2["error"])
        # 接口回 js::HTML（接口不在/被重定向）→ 点名脚本回包
        self.fz.modules = {1: [{"id": 99, "name": "登录"}]}
        self.fz.old_modules_js = True
        r3 = self.zen_mod.fetch_modules(1)
        self.assertFalse(r3["ok"])
        self.assertIn("脚本", r3["error"])


# ---------------------------------------------------------------- 图片证据（#27754 案）

_PNG1 = (b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR\x00\x00\x00\x01\x00\x00\x00\x01"
         b"\x08\x06\x00\x00\x00\x1f\x15\xc4\x89\x00\x00\x00\nIDATx\x9cc\x00\x01"
         b"\x00\x00\x05\x00\x01\r\n-\xb4\x00\x00\x00\x00IEND\xaeB`\x82")


class TestImageEvidenceHelpers(BaseTest):
    """纯函数：图片指涉识别 + src 抽取 + 魔数判型 + data URI。"""
    def runTest(self):
        from app.core import zentao
        bug_img_tag = {"title": "功能未对齐", "steps": "<p>看图</p><img src='/a.png'/>"}
        bug_img_word = {"title": "功能未对齐，具体如图。", "steps": "<p>打开页面</p>"}
        bug_plain = {"title": "接口 500", "steps": "<p>调用报错</p>"}
        bug_log_file = {"title": "报错", "steps": "<p>x</p>",
                        "files": {"1": {"title": "run.log", "extension": "log"}}}
        bug_png_file = {"title": "报错", "steps": "<p>x</p>",
                        "files": {"2": {"title": "截图.png", "extension": "png"}}}
        self.assertTrue(zentao._bug_mentions_images(bug_img_tag))
        self.assertTrue(zentao._bug_mentions_images(bug_img_word))
        self.assertFalse(zentao._bug_mentions_images(bug_plain))
        self.assertFalse(zentao._bug_mentions_images(bug_log_file), "日志附件不算图片证据")
        self.assertTrue(zentao._bug_mentions_images(bug_png_file))
        srcs = zentao._img_srcs('<img src="/a.png" ><img data-x="1" SRC=\'/b.jpg\'>'
                                '<img src="data:image/png;base64,QUJD">')
        self.assertEqual(srcs, ["/a.png", "/b.jpg", "data:image/png;base64,QUJD"])
        self.assertEqual(zentao._img_mime(_PNG1), "image/png")
        self.assertEqual(zentao._img_mime(b"<html>404</html>"), "")
        mime, raw = zentao._decode_data_uri("data:image/png;base64," +
                                            base64.b64encode(_PNG1).decode())
        self.assertEqual((mime, raw[:4]), ("image/png", _PNG1[:4]))
        self.assertEqual(zentao._file_entry_urls("9", {"extension": "png"}),
                         ["/file-read-9.png", "/file-download-9.png"])


class TestTriageImageGuard(ZenCase):
    """图片守卫：描述指向截图但读不到 → unknown 留人工，绝不调 LLM 盲判。"""
    def runTest(self):
        cfg = self.configure(profiles=[self.profile(module_routes=[])])
        prof = self.zen_mod._profiles(cfg)[0]
        self.bug(801, title="【客户管理】【客户跟进记录】功能未对齐，具体如图。",
                 steps="<p>操作后对比截图</p><img src='/file-read-555.png'/>")
        bug = self.fz.bugs["801"]
        # 图片 404、详情也无附件 → 守卫触发
        self.zen_mod._IMG_CACHE.clear()
        tri = self.zen_mod._ai_triage(bug, prof)
        self.assertEqual(tri["side"], "unknown")
        self.assertIn("图片", tri["reason"])
        # 端到端：need_manual，不建任务不转派
        self.assertTrue(self.zen_mod.scan_now()["ok"])
        c = self.claim()
        self.assertEqual(c["state"], "need_manual")
        self.assertIn("图片", c["note"])
        self.assertEqual(c.get("tasks"), [])
        self.assertEqual(self.put_calls("801"), [], "守卫路径不得转派")


class TestTriageWithImages(ZenCase):
    """图片抓到 → 随 chat 透传给排查模型；判定结果带 imgs；转派文案标注。"""
    def runTest(self):
        from app.core import modelhub
        self.fz.files["/file-read-555.png"] = _PNG1
        cfg = self.configure(profiles=[self.profile(module_routes=[])])
        prof = self.zen_mod._profiles(cfg)[0]
        self.bug(802, title="客户跟进记录字段缺失，具体如图。",
                 steps="<p>新建跟进记录缺字段</p><img src='/file-read-555.png'/>")
        bug = self.fz.bugs["802"]
        calls = []
        orig = (modelhub.resolve_orchestrator, modelhub.chat, modelhub._model_image_in)
        modelhub.resolve_orchestrator = lambda: (
            {"id": "p", "api_key": "k", "allow_private": True}, "m")
        modelhub._model_image_in = lambda prov, model: True

        def fake_chat(pid, model, prompt, max_tokens=2048, timeout=120, cache_ttl=0,
                      on_delta=None, reasoning_effort="", images=None):
            calls.append({"prompt": prompt, "images": images})
            return {"ok": True, "tokens": 10,
                    "text": json.dumps({"side": "frontend", "reason": "截图显示前端缺字段"})}

        modelhub.chat = fake_chat
        try:
            self.zen_mod._IMG_CACHE.clear()
            tri = self.zen_mod._ai_triage(bug, prof)
            self.assertEqual(len(calls), 1)
            self.assertEqual(tri["side"], "frontend")
            self.assertEqual(tri["imgs"], 1)
            self.assertEqual(len(calls[0]["images"]), 1, "截图必须随 chat 透传")
            self.assertEqual(calls[0]["images"][0][0], "image/png")
            self.assertIn("先看图", calls[0]["prompt"])
            # 端到端：转派前端，文案带截图标注（stub 必须盖住扫描全程）
            self.assertTrue(self.zen_mod.scan_now()["ok"])
            c = self.claim()
            self.assertEqual(c["state"], "transferred")
            puts = self.put_calls("802")
            self.assertEqual(len(puts), 1)
            self.assertIn("fe-owner", json.dumps(puts[0][2], ensure_ascii=False))
            self.assertIn("1 张 bug 截图", str(puts[0][2].get("comment") or ""))
        finally:
            (modelhub.resolve_orchestrator, modelhub.chat,
             modelhub._model_image_in) = orig


class TestTriageFilesFallback(ZenCase):
    """steps 无内嵌图 → 回落详情 files 附件取图（REST 形状 files 直接在 bug 里）。"""
    def runTest(self):
        from app.core import modelhub
        self.fz.files["/file-read-9.png"] = _PNG1
        cfg = self.configure(profiles=[self.profile(module_routes=[])])
        prof = self.zen_mod._profiles(cfg)[0]
        self.bug(803, title="页面渲染不对，见附件截图。",
                 steps="<p>样式错乱，见附件</p>",
                 files={"9": {"title": "截图.png", "extension": "png"}})
        bug = self.fz.bugs["803"]
        calls = []
        orig = (modelhub.resolve_orchestrator, modelhub.chat, modelhub._model_image_in)
        modelhub.resolve_orchestrator = lambda: (
            {"id": "p", "api_key": "k", "allow_private": True}, "m")
        modelhub._model_image_in = lambda prov, model: True

        def fake_chat(pid, model, prompt, max_tokens=2048, timeout=120, cache_ttl=0,
                      on_delta=None, reasoning_effort="", images=None):
            calls.append({"prompt": prompt, "images": images})
            return {"ok": True, "tokens": 5,
                    "text": json.dumps({"side": "backend", "reason": "数据缺失"})}

        modelhub.chat = fake_chat
        try:
            self.zen_mod._IMG_CACHE.clear()
            tri = self.zen_mod._ai_triage(bug, prof)
        finally:
            (modelhub.resolve_orchestrator, modelhub.chat,
             modelhub._model_image_in) = orig
        self.assertEqual(tri["side"], "backend")
        self.assertEqual(tri["imgs"], 1, "附件图片要经 files 回落取到")
        self.assertEqual(len(calls[0]["images"]), 1)


class TestNoVisionModelGuard(ZenCase):
    """图片抓到了但排查模型不支持 image_in → 留人工并提示开能力，不烧文本盲判。"""
    def runTest(self):
        from app.core import modelhub
        self.fz.files["/file-read-555.png"] = _PNG1
        cfg = self.configure(profiles=[self.profile(module_routes=[])])
        prof = self.zen_mod._profiles(cfg)[0]
        self.bug(804, title="功能未对齐，如图。",
                 steps="<p><img src='/file-read-555.png'/></p>")
        bug = self.fz.bugs["804"]
        called = []
        orig = (modelhub.resolve_orchestrator, modelhub.chat, modelhub._model_image_in)
        modelhub.resolve_orchestrator = lambda: (
            {"id": "p", "api_key": "k", "allow_private": True}, "m")
        modelhub._model_image_in = lambda prov, model: False

        def fake_chat(*a, **kw):
            called.append(1)
            return {"ok": False, "error": "不应被调用"}

        modelhub.chat = fake_chat
        try:
            self.zen_mod._IMG_CACHE.clear()
            tri = self.zen_mod._ai_triage(bug, prof)
        finally:
            (modelhub.resolve_orchestrator, modelhub.chat,
             modelhub._model_image_in) = orig
        self.assertEqual(called, [], "无视觉能力不得发起排查调用")
        self.assertEqual(tri["side"], "unknown")
        self.assertIn("image_in", tri["reason"])


class TestFixTaskCarriesImages(ZenCase):
    """修复任务带截图：_launch_fix 把 bug 图落 _attachments/ 并注入 goal 提示。"""
    def runTest(self):
        self.fz.files["/file-read-555.png"] = _PNG1
        cfg = self.configure(profiles=[self.profile(
            module_routes=[], repos={"backend": {"git_rev": "r1"}})])
        prof = self.zen_mod._profiles(cfg)[0]
        self.bug(805, title="列表渲染缺列，如图。",
                 steps="<p><img src='/file-read-555.png'/></p>")
        bug = self.fz.bugs["805"]
        self.zen_mod._launch_fix = self._orig_launch      # 换回真实现
        self.zen_mod._IMG_CACHE.clear()
        task, run = self.zen_mod._launch_fix(bug, prof, "backend", cfg)
        atts = task.get("attachments") or []
        self.assertEqual(len(atts), 1)
        self.assertEqual(atts[0]["path"], "_attachments/zenbug805-1.png")
        self.assertIn("先读图再动手", task["goal"])
        self.assertTrue((self.workdir / "_attachments" / "zenbug805-1.png").is_file())


class TestChatUserContentShapes(BaseTest):
    """modelhub._chat_user_content：三协议有图/无图形状。"""
    def runTest(self):
        from app.core.modelhub import _chat_user_content as c
        self.assertEqual(c("openai", "hi", None), "hi")
        self.assertEqual(c("anthropic", "hi", None), "hi")
        self.assertEqual(c("google", "hi", None), [{"text": "hi"}])
        imgs = [("image/png", "QUJD")]
        o = c("openai", "hi", imgs)
        self.assertEqual(o[0], {"type": "text", "text": "hi"})
        self.assertEqual(o[1]["type"], "image_url")
        self.assertEqual(o[1]["image_url"]["url"], "data:image/png;base64,QUJD")
        a = c("anthropic", "hi", imgs)
        self.assertEqual(a[1]["type"], "image")
        self.assertEqual(a[1]["source"]["media_type"], "image/png")
        self.assertEqual(a[1]["source"]["data"], "QUJD")
        g = c("google", "hi", imgs)
        self.assertEqual(g[0], {"text": "hi"})
        self.assertEqual(g[1]["inline_data"], {"mime_type": "image/png", "data": "QUJD"})
