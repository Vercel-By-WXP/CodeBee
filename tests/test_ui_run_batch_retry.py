# -*- coding: utf-8 -*-
"""运行记录批量重试（`app/ui/run-batch.js`）守测（2026-11-05）。

用户实拍：一次同步重启把五条运行记录留成「已中断·可续跑」，想一起续上只能
一条条点开详情再点「继续任务」。运行记录本来就有勾选框（为批量删除而建），
这层覆盖件补上「重试所选 / 全部重试 / 行内重试」三处入口。

本文件锁两类前提：

1. **接线**：覆盖层必须在 app.js 之后加载、观察 `#run-list` 而不是改 app.js；
   文案走 `t()` 且中英双份；确认走 `uiConfirm`（原生弹框全仓封禁）。
2. **判据**：`retryable()` 的四个条件——该 run 是任务最新的一条、没有
   `superseded_by`、任务是终态异常（failed/cancelled/timeout）、任务此刻
   没在 running/queued。判据放宽会把「已经在跑的任务」或「更早有新 run 的
   旧账本」也摆成可重试，点下去要么被后端拒，要么拿最新状态重跑却不指向
   屏幕上那一行。真跑用例见 `scratch/run-batch-probe.mjs`（隔离实例 +
   桩掉 `/retry`，实拍浅色/深色/980 窄窗）。
"""
import json
import re
import shutil
import subprocess
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
UI = ROOT / "app" / "ui"
JS = UI / "run-batch.js"


def _src():
    return JS.read_text(encoding="utf-8", errors="replace")


class WiringTests(unittest.TestCase):
    """覆盖层挂得上、关得掉、不动 app.js。"""

    def setUp(self):
        self.src = _src()

    def test_loaded_after_app_js(self):
        html = (UI / "index.html").read_text(encoding="utf-8", errors="replace")
        self.assertIn('src="run-batch.js"', html, "index.html 没引用覆盖层，等于没做")
        self.assertLess(html.index('src="app.js"'), html.index('src="run-batch.js"'),
                        "必须排在 app.js 之后（要用它的 t/api/toast/uiConfirm/poll）")

    def test_installs_once(self):
        self.assertIn("__runBatchInstalled", self.src,
                      "重复加载会挂两套按钮与两个 observer")

    def test_watches_run_list_without_touching_app_js(self):
        self.assertIn('getElementById("run-list")', self.src)
        self.assertIn("MutationObserver", self.src)
        app = (UI / "app.js").read_text(encoding="utf-8", errors="replace")
        self.assertNotIn("runBatchRetry", app, "app.js 不该知道覆盖层存在（删引用即回滚）")

    def test_task_level_retry_endpoint(self):
        # 重试是任务级动作：store.retry_task 的断点续跑语义，不是 run 级重放
        self.assertIn('"/api/tasks/" + encodeURIComponent(r.task_id) + "/retry"', self.src)
        self.assertIn('method: "POST"', self.src)

    def test_same_task_enqueued_once(self):
        self.assertIn("seenTask[r.task_id]", self.src,
                      "同一任务的多条账本行必须只发起一次（后端会拒第二条）")

    def test_confirm_uses_in_app_layer(self):
        self.assertIn("uiConfirm(", self.src)
        for bad in ("window.confirm(", "window.alert(", "window.prompt("):
            self.assertNotIn(bad, self.src)
        # 行/工具栏按钮全走 addEventListener：不拼内联 onclick，免掉 jsq 转义面
        self.assertNotIn("onclick=", self.src)

    def test_clears_selection_and_refreshes(self):
        self.assertIn("selRuns", self.src)
        self.assertIn("poll()", self.src)

    def test_row_button_reads_before_delete(self):
        self.assertIn('querySelector("button.danger")', self.src)
        self.assertIn("insertBefore", self.src)

    def test_labels_translated(self):
        i18n = (UI / "i18n.js").read_text(encoding="utf-8", errors="replace")
        # 只认真正的 t("…")：前缀排除 . / 标识符字符，否则 querySelector("…") 会误命中
        keys = re.findall(r'(?<![.\w$])t\(\s*"((?:[^"\\]|\\.)*)"', self.src)
        missing = [k for k in keys if k and ('"%s"' % k) not in i18n]
        self.assertTrue(keys, "没抓到任何 t(\"…\") —— 正则或写法变了，先修测试")
        self.assertEqual(missing, [], "这些文案缺 i18n 词条（英文界面会露中文）：%s" % missing)


class RetryableJudgementTests(unittest.TestCase):
    """真跑 `retryable()`（node 抽出函数体 + 注入 latestOf），逐条判据都过一遍。"""

    CASES = [
        # (说明, run, {task_id: 最新 run}, 期望)
        ("最新一条真失败", {"id": "a", "task_id": "t", "status": "failed"}, {"t": {"id": "a", "status": "failed"}}, True),
        ("重启打断也是 failed", {"id": "a", "task_id": "t", "status": "failed", "interrupted": "restart"},
         {"t": {"id": "a", "status": "failed"}}, True),
        ("已取消可重试", {"id": "a", "task_id": "t", "status": "cancelled"}, {"t": {"id": "a", "status": "cancelled"}}, True),
        ("超时可重试", {"id": "a", "task_id": "t", "status": "timeout"}, {"t": {"id": "a", "status": "timeout"}}, True),
        ("已被续跑接上", {"id": "a", "task_id": "t", "status": "failed", "superseded_by": "b"},
         {"t": {"id": "a", "status": "failed"}}, False),
        ("不是任务最新的一条", {"id": "a", "task_id": "t", "status": "failed"},
         {"t": {"id": "b", "status": "failed"}}, False),
        ("最新那条还在跑", {"id": "a", "task_id": "t", "status": "failed"},
         {"t": {"id": "a", "status": "running"}}, False),
        ("最新那条在退避排队", {"id": "a", "task_id": "t", "status": "failed"},
         {"t": {"id": "a", "status": "queued"}}, False),
        ("已完成（未达标也不进「重试」）", {"id": "a", "task_id": "t", "status": "done"}, {"t": {"id": "a", "status": "done"}}, False),
        ("任务账本缺失", {"id": "a", "task_id": "t", "status": "failed"}, {}, False),
        ("没有 task_id 的直连会话", {"id": "a", "status": "failed"}, {}, False),
    ]

    @unittest.skipUnless(shutil.which("node"), "需要 node 才能真跑 retryable()")
    def test_table(self):
        src = _src()
        start = src.index("function retryable(r) {")
        fn = src[start:src.index("\n  }", start) + 4]
        script = "\n".join([
            "const assert = require('node:assert/strict');",
            "const src = " + json.dumps(fn) + ";",
            "function build(latest) {",
            "  const latestOf = (tid) => latest[tid] || null;",
            "  return new Function('latestOf', src + ';return retryable;')(latestOf);",
            "}",
            "const cases = " + json.dumps(self.CASES, ensure_ascii=False) + ";",
            "let n = 0;",
            "for (const [label, run, latest, want] of cases) {",
            "  const got = build(latest)(run);",
            "  assert.equal(got, want, label + ' -> ' + got + ' (want ' + want + ')');",
            "  n++;",
            "}",
            "console.log('CASES=' + n);",
        ])
        out = subprocess.run(["node", "-e", script], capture_output=True, text=True, timeout=60)
        self.assertEqual(out.returncode, 0, out.stderr[-1500:])
        # 「零误拦 ≠ 判得准」：必须报真判过的样本数
        self.assertIn("CASES=%d" % len(self.CASES), out.stdout)


if __name__ == "__main__":
    unittest.main()
