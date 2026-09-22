# -*- coding: utf-8 -*-
"""网页成品预览模块（app/core/preview.py）单元测试。

重点三件：入口挑选（index.html 优先）、路径硬闸（穿越/隐藏/扩展名全拒）、
HTML 注入（<base> 带路径令牌 + fetch/XHR 令牌补丁）。这些是预览的安全边界，
宁可测得啰嗦也不能让「挂载一个任意目录」出事。
"""
import os
import sys
import tempfile
import time
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
os.environ.setdefault("TUTTI_DATA", tempfile.mkdtemp(prefix="tutti-pv-"))
sys.path.insert(0, str(ROOT / "app"))

from core import paths, preview, store  # noqa: E402


def _mk_task(workdir):
    return store.create_task({"type": "code", "goal": "预览测试", "workdir": str(workdir)})


def _mk_run(task_id, steps=1):
    run = store.create_run("orchestration", "预览测试", task_id=task_id)
    for i in range(steps):
        run["steps"].append({"n": i + 1, "role": "draft", "agent": "mock",
                             "status": "done", "summary": "", "log": "",
                             "cost_usd": 0, "tokens": 0})
    store.update_run(run["id"], steps=run["steps"], status="done")
    return store.get_run(run["id"])


class PreviewBase(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="tutti-pv-wd-"))
        self.task = _mk_task(self.tmp)
        self.run = _mk_run(self.task["id"])

    def touch(self, rel, content=b"x", mtime=None):
        p = self.tmp / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_bytes(content)
        if mtime:
            os.utime(p, (mtime, mtime))
        return p

    def future(self, offset):
        """成品闸：run_artifacts 只认「任务首跑之后」动过的文件（t0 = 任务
        created_at）。造数要检验新旧排序就得把 mtime 放到未来——比回拨任务
        created_at 干净（那是 store 的状态，不该为造数去改）。"""
        return time.time() + offset


class TestSafeRel(unittest.TestCase):
    def test_blocks_traversal(self):
        self.assertIsNone(preview.safe_rel("../etc/passwd"))
        self.assertIsNone(preview.safe_rel("a/../../b.js"))
        self.assertIsNone(preview.safe_rel("a/.."))

    def test_blocks_hidden(self):
        self.assertIsNone(preview.safe_rel(".git/config"))
        self.assertIsNone(preview.safe_rel("assets/.hidden.js"))

    def test_normalizes(self):
        self.assertEqual(preview.safe_rel("/a/./b.css"), "a/b.css")
        self.assertEqual(preview.safe_rel("a\\b.js"), "a/b.js")
        self.assertEqual(preview.safe_rel("a//b.js"), "a/b.js")

    def test_empty_rejected(self):
        self.assertIsNone(preview.safe_rel(""))
        self.assertIsNone(preview.safe_rel(None))


class TestResolve(PreviewBase):
    def test_happy(self):
        self.touch("index.html")
        p, err = preview.resolve(self.run["id"], "index.html")
        self.assertIsNone(err)
        self.assertEqual(p.name, "index.html")

    def test_blocks_escape_via_symlink(self):
        outside = Path(tempfile.mkdtemp(prefix="tutti-pv-out-")) / "secret.txt"
        outside.write_text("s", encoding="utf-8")
        link = self.tmp / "leak.html"
        try:
            link.symlink_to(outside)
        except OSError:
            self.skipTest("symlink not supported")
        _, err = preview.resolve(self.run["id"], "leak.html")
        self.assertTrue(err)

    def test_blocks_disallowed_ext(self):
        self.touch("evil.py")
        _, err = preview.resolve(self.run["id"], "evil.py")
        self.assertIn("不支持", err)

    def test_blocks_hidden_target(self):
        self.touch(".git/config")
        _, err = preview.resolve(self.run["id"], ".git/config")
        self.assertIn("非法", err)


class TestServe(PreviewBase):
    def test_html_gets_base_and_token_patch(self):
        self.touch("index.html", b"<!DOCTYPE html><html><head><title>t</title></head><body></body></html>")
        data, ctype, err = preview.serve(self.run["id"], "index.html", "tok123")
        self.assertIsNone(err)
        html = data.decode("utf-8")
        self.assertIn('<base href="/preview/%s/tok123/">' % self.run["id"], html)
        self.assertIn("X-CodeBee-Token", html)   # fetch/XHR 补丁在场
        self.assertLess(html.index("<base"), html.index("<title>"))  # 注入在 head 顶部

    def test_html_without_head_still_injected(self):
        self.touch("index.html", b"<html><body>hi</body></html>")
        data, _, err = preview.serve(self.run["id"], "index.html", "tok")
        self.assertIsNone(err)
        self.assertIn("<base href=", data.decode("utf-8"))

    def test_subdir_entry_base_carries_dir(self):
        self.touch("web/index.html", b"<html><head></head><body></body></html>")
        data, _, err = preview.serve(self.run["id"], "web/index.html", "tok")
        self.assertIsNone(err)
        self.assertIn('href="/preview/%s/tok/web/">' % self.run["id"], data.decode("utf-8"))

    def test_mime_map(self):
        self.touch("style.css", b"body{}")
        self.touch("app.js", b"console.log(1)")
        _, css_ctype, err = preview.serve(self.run["id"], "style.css", "tok")
        self.assertIsNone(err)
        self.assertEqual(css_ctype, "text/css; charset=utf-8")
        _, js_ctype, err = preview.serve(self.run["id"], "app.js", "tok")
        self.assertEqual(js_ctype, "text/javascript; charset=utf-8")

    def test_gbk_html_transcoded(self):
        self.touch("index.html", "<html><head></head><body>中文</body></html>".encode("gbk"))
        data, _, err = preview.serve(self.run["id"], "index.html", "tok")
        self.assertIsNone(err)
        self.assertIn("中文", data.decode("utf-8"))

    def test_oversize_rejected(self):
        big = self.tmp / "huge.js"
        big.write_bytes(b"a" * (preview.MAX_FILE + 1))
        _, _, err = preview.serve(self.run["id"], "huge.js", "tok")
        self.assertIn("过大", err)


class TestListApp(PreviewBase):
    def test_no_steps(self):
        wd = self.tmp / "fresh-wd"
        wd.mkdir(parents=True, exist_ok=True)
        (wd / "index.html").write_bytes(b"<html></html>")
        t = _mk_task(wd)
        run = store.create_run("orchestration", "无步骤", task_id=t["id"])
        app = preview.list_app(run["id"])
        self.assertFalse(app["ok"])
        self.assertEqual(app["reason"], "no_steps")

    def test_no_html(self):
        self.touch("data.json", b"{}")
        app = preview.list_app(self.run["id"])
        self.assertFalse(app["ok"])
        self.assertEqual(app["reason"], "no_html")

    def test_entry_and_tab_order(self):
        # css < js < html：证明页签条不是按 mtime 而是按 html→css→js 排的
        self.touch("script.js", b"let a=1;", mtime=self.future(30))
        self.touch("style.css", b"body{}", mtime=self.future(20))
        self.touch("index.html", b"<html></html>", mtime=self.future(40))
        app = preview.list_app(self.run["id"])
        self.assertTrue(app["ok"])
        self.assertEqual(app["entry"], "index.html")
        names = [f["name"] for f in app["files"]]
        self.assertEqual(names[0], "index.html")
        self.assertIn("style.css", names)
        self.assertIn("script.js", names)
        self.assertLess(names.index("style.css"), names.index("script.js"))
        self.assertTrue(app["base"].startswith("/preview/"))
        self.assertTrue(app["base"].endswith("/-/"))

    def test_base_without_token_falls_back_to_dash(self):
        self.touch("index.html")
        app = preview.list_app(self.run["id"])
        self.assertEqual(app["base"], "/preview/%s/-/" % self.run["id"])

    def test_prefers_index_over_other_root_html(self):
        self.touch("home.html", b"<html></html>", mtime=self.future(30))
        self.touch("index.html", b"<html></html>", mtime=self.future(10))
        app = preview.list_app(self.run["id"])
        self.assertEqual(app["entry"], "index.html")

    def test_falls_back_to_subdir_entry(self):
        self.touch("web/index.html", b"<html></html>")
        app = preview.list_app(self.run["id"])
        self.assertTrue(app["ok"])
        self.assertEqual(app["entry"], "web/index.html")

    def test_tabs_confined_to_entry_dir(self):
        self.touch("app.js", b"let a=1;", mtime=self.future(30))
        self.touch("web/index.html", b"<html></html>", mtime=self.future(25))
        self.touch("web/style.css", b"body{}", mtime=self.future(23))
        self.touch("lib/other.js", b"let b=2;", mtime=self.future(21))
        app = preview.list_app(self.run["id"])
        names = [f["name"] for f in app["files"]]
        self.assertEqual(names[0], "web/index.html")
        self.assertIn("web/style.css", names)
        self.assertNotIn("app.js", names)        # 根目录的不属于 web/ 应用
        self.assertNotIn("lib/other.js", names)

    def test_missing_run(self):
        self.assertFalse(preview.list_app("r-nope")["ok"])

    def test_no_workdir(self):
        # create_task 会校验工作目录存在，所以先建后删——目录中途被清理
        # （cleanup/用户手删）时预览要安静地判不可用，而不是抛异常
        gone = self.tmp / "nope-missing"
        gone.mkdir(parents=True, exist_ok=True)
        t = store.create_task({"type": "code", "goal": "无目录", "workdir": str(gone)})
        gone.rmdir()
        r = _mk_run(t["id"])
        app = preview.list_app(r["id"])
        self.assertEqual(app["reason"], "no_workdir")


class TestPickEntry(unittest.TestCase):
    def test_empty(self):
        self.assertEqual(preview._pick_entry([]), "")

    def test_prefers_index_then_shallow_then_new(self):
        self.assertEqual(preview._pick_entry(
            [{"name": "a.html", "mtime": 9}, {"name": "index.html", "mtime": 1}]), "index.html")
        self.assertEqual(preview._pick_entry(
            [{"name": "web/index.html", "mtime": 9}, {"name": "deep/x/index.html", "mtime": 1}]),
            "web/index.html")

    def test_ignores_non_html(self):
        self.assertEqual(preview._pick_entry([{"name": "app.js", "mtime": 1}]), "")


class TestPathToken(unittest.TestCase):
    def test_preview_path_shape(self):
        self.assertEqual(preview.preview_path("r1", "abc"), "/preview/r1/abc/")
        self.assertEqual(preview.preview_path("r1", ""), "/preview/r1/-/")

    def test_mount_reuses_api_auth_gate(self):
        # 预览挂载复用 remote.request_authed（与 /api/* 同一把尺子）：
        # loopback 豁免、远程必须带对令牌。这里按"远程形态"（非 loopback IP）
        # 断言错令牌必拒——HTTP 层在无头测试里到不了远程形态，只能在单测锁。
        from core import remote
        self.assertFalse(remote.request_authed("203.0.113.7", "", "wrong", ""))
        self.assertTrue(remote.request_authed("203.0.113.7", "", remote.token(), ""))
        self.assertTrue(remote.request_authed("127.0.0.1", "", "whatever", ""))


if __name__ == "__main__":
    unittest.main()
