# -*- coding: utf-8 -*-
"""hooks 单测：协议解析、注入合并、事件过滤、超时静默、配置校验。

跑法：python -m unittest discover -s tests -p "test_hooks.py" -v
"""
from __future__ import annotations

import sys

from base import BaseTest

from app.core import hooks


def _py(code):
    import sys
    return f'"{sys.executable}" -c "{code}"'


class TestHooks(BaseTest):
    def setUp(self):
        super().setUp()
        hooks._FILE = self.data_dir / "hooks.json"

    def test_empty_by_default(self):
        self.assertEqual(hooks.load_list(), [])
        self.assertEqual(hooks.run_event("task_start"), "")

    def test_save_and_load_roundtrip(self):
        hooks.save_list([
            {"name": "知识库", "event": "message_submit",
             "cmd": _py("print(1)"), "enabled": True, "timeout_s": 5},
        ])
        items = hooks.load_list()
        self.assertEqual(len(items), 1)
        self.assertEqual(items[0]["event"], "message_submit")
        self.assertEqual(items[0]["timeout_s"], 5)
        self.assertTrue(items[0]["enabled"])

    def test_save_rejects_bad_event(self):
        with self.assertRaises(ValueError):
            hooks.save_list([{"event": "nope", "cmd": "x"}])

    def test_save_drops_empty_cmd(self):
        out = hooks.save_list([{"event": "task_start", "cmd": ""}])
        self.assertEqual(out, [])

    def test_stdout_plain_text_is_inject(self):
        hooks.save_list([{"event": "task_start",
                          "cmd": _py("print('注入内容')")}])
        self.assertEqual(hooks.run_event("task_start"), "注入内容")

    def test_stdout_json_inject_field(self):
        code = "import json,sys;print(json.dumps({'inject':'来自JSON'}))"
        hooks.save_list([{"event": "task_start", "cmd": _py(code)}])
        self.assertEqual(hooks.run_event("task_start"), "来自JSON")

    def test_event_filter_and_disabled(self):
        hooks.save_list([
            {"event": "run_end", "cmd": _py("print('wrong')")},
            {"event": "task_start", "cmd": _py("print('off')"), "enabled": False},
        ])
        self.assertEqual(hooks.run_event("task_start"), "")

    def test_timeout_is_silent(self):
        code = "import time;time.sleep(30)"
        hooks.save_list([{"event": "task_start", "cmd": _py(code), "timeout_s": 1}])
        self.assertEqual(hooks.run_event("task_start"), "")

    def test_failing_cmd_is_silent(self):
        hooks.save_list([{"event": "message_submit",
                          "cmd": _py("import sys;sys.exit(3)")}])
        self.assertEqual(hooks.run_event("message_submit", text="hi"), "")

    def test_stdin_payload_reaches_hook(self):
        # 代码只用单引号（_py 外壳是双引号；shlex posix=False 不解析 \" 转义）
        code = "import json,sys;d=json.load(sys.stdin);print('got:'+d['event'])"
        hooks.save_list([{"event": "run_end", "cmd": _py(code)}])
        outs = hooks.run_event("run_end", task_id="t1")
        self.assertEqual(outs, "got:run_end")

    def test_multiple_hooks_merged_in_order(self):
        hooks.save_list([
            {"event": "task_start", "cmd": _py("print('A')")},
            {"event": "task_start", "cmd": _py("print('B')")},
        ])
        self.assertEqual(hooks.run_event("task_start"), "A\n\nB")

    def test_test_one_reports_no_output(self):
        ok, out = hooks.test_one(_py("print('')"))
        self.assertTrue(ok)
        self.assertIn("无输出", out)

    def test_pop_inject_roundtrip(self):
        from app.core import store
        run = store.create_run("orchestration", "探针", task_id=None)
        self.assertFalse(store.append_run_inject(run["id"], ""))
        self.assertTrue(store.append_run_inject(run["id"], "注入A"))
        store.append_run_inject(run["id"], "注入B")
        self.assertIn("注入A", store.pop_run_inject(run["id"]))
        self.assertEqual(store.pop_run_inject(run["id"]), "")   # 消费即清


if __name__ == "__main__":
    import unittest
    unittest.main()
