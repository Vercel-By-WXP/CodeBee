# -*- coding: utf-8 -*-
"""「被服务重启打断」≠「跑挂了」：中断标记与续跑指针的守测（2026-11-05）。

用户实拍（11-05 15:24）：桌面脚本「同步最新代码并重启」跑了一次，运行记录里
立刻多出六行红色「失败 / 服务重启中断，可重试」——三本书各两行（14:15 与 10:40
各一次），而每本书其实都已被自动续跑接上，正活着那三条就排在它们上面。
一次有意的重启不该看起来像全线崩了。

修法分三层，本文件逐层锁住：

· `store.load_all()` 读盘收尸时打 `interrupted` / `interrupted_at`（状态仍是
  failed，重试/继承/统计口径不动——只多一个标记）；
· 续跑接上时给旧 run 写 `superseded_by`（`jobs._maybe_auto_resume`），历史上
  没写过的那批由读盘按后继 run 的 `auto_resumed_from` 反向推导；
· 视图层据这两个字段把行降级成灰色「已中断」，被接上的默认不占行。

隔离方式沿用 `tests/test_store_load_all.py`：临时目录 + TUTI_DATA + 子进程
（store 的 _TASKS/_RUNS 是模块级缓存，进程内换数据目录会互相污染）。
"""
import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
UI = REPO / "app" / "ui"

PROBE = r"""
import json, os
from pathlib import Path
from app.core import store
store.load_all()
runs = {rid: {"status": v.get("status"),
              "interrupted": v.get("interrupted"),
              "interrupted_at": v.get("interrupted_at"),
              "superseded_by": v.get("superseded_by"),
              "error": v.get("error")}
        for rid, v in store._RUNS.items()}
# 落盘检查：两种来源要分开验——收尸那层**写盘**（标记要活过一次降级/重启），
# 老账回填只改内存（推导值，每次启动重算）。只验内存分不出两层。
disk = {}
for p in Path(os.environ["TUTTI_DATA"], "runs").glob("*/run.json"):
    try:
        d = json.loads(p.read_text(encoding="utf-8"))
        disk[d.get("id")] = d.get("interrupted")
    except Exception:
        pass
print("@@RESULT@@" + json.dumps({"runs": runs, "disk": disk}, ensure_ascii=False))
"""


def _load(run_fixtures):
    with tempfile.TemporaryDirectory() as tmp:
        tmp = Path(tmp)
        (tmp / "tasks").mkdir()
        runs_dir = tmp / "runs"
        runs_dir.mkdir()
        for run_id, payload in run_fixtures.items():
            d = runs_dir / run_id
            d.mkdir()
            (d / "run.json").write_text(
                json.dumps(payload, ensure_ascii=False), encoding="utf-8")
        env = dict(os.environ)
        env["TUTTI_DATA"] = str(tmp)
        proc = subprocess.run([sys.executable, "-c", PROBE],
                              cwd=str(REPO), env=env, timeout=120,
                              capture_output=True, text=True)
        if proc.returncode != 0:
            raise AssertionError("probe failed: %s" % proc.stderr[-2000:])
        for line in proc.stdout.splitlines():
            if line.startswith("@@RESULT@@"):
                data = json.loads(line[len("@@RESULT@@"):])
                return data["runs"], data["disk"]
        raise AssertionError("no result marker: %s" % proc.stdout[-2000:])


class InterruptedFlagTests(unittest.TestCase):
    """读盘收尸：状态口径不变，只多一个「这是被打断的」标记。"""

    def test_running_run_gets_interrupted_flag(self):
        runs, disk = _load({"r-1": {"id": "r-1", "task_id": "t1", "status": "running"}})
        r = runs["r-1"]
        self.assertEqual(r["status"], "failed")          # 既有口径不能变
        self.assertEqual(r["interrupted"], "restart")
        self.assertTrue(r["interrupted_at"])
        self.assertIn("服务重启中断", r["error"])
        # 收尸这层必须**写盘**：标记要活过下一次启动，不能只活在内存里
        self.assertEqual(disk["r-1"], "restart")

    def test_legacy_interrupted_error_recognised(self):
        # 老数据没有标记，只有那句文案（还常被后续文案拼上尾巴）——按前缀认
        runs, disk = _load({"r-old": {
            "id": "r-old", "task_id": "t1", "status": "failed",
            "error": "服务重启中断，可重试｜自动续跑止损：连续两次失败原因相同"}})
        self.assertEqual(runs["r-old"]["interrupted"], "restart")
        # 这条是推导出来的（老数据没标记），不落盘——每次启动重算，也就不怕写坏
        self.assertIsNone(disk["r-old"])

    def test_real_failure_not_flagged(self):
        # 真跑挂的不能被打扮成「中断」，否则红点就失去意义
        runs, _ = _load({"r-bad": {
            "id": "r-bad", "task_id": "t1", "status": "failed",
            "error": "上游服务端异常（503）"}})
        self.assertIsNone(runs["r-bad"]["interrupted"])
        self.assertIsNone(runs["r-bad"]["superseded_by"])

    def test_resumed_predecessor_linked(self):
        # 历史数据里只有后继 run 的 auto_resumed_from，反推旧 run 的 superseded_by
        runs, _ = _load({
            "r-prev": {"id": "r-prev", "task_id": "t1", "status": "failed",
                       "error": "服务重启中断，可重试"},
            "r-next": {"id": "r-next", "task_id": "t1", "status": "running",
                       "auto_resumed_from": "r-prev"},
        })
        self.assertEqual(runs["r-prev"]["superseded_by"], "r-next")
        self.assertEqual(runs["r-prev"]["interrupted"], "restart")
        # 后继自己不是「被打断」的那一条
        self.assertIsNone(runs["r-next"]["superseded_by"])


class PayloadAndWiringTests(unittest.TestCase):
    """跨文件前提：标记要能到界面，续跑要能被记下来。"""

    def test_state_payload_carries_markers(self):
        # /api/state 的 run 投影是白名单：漏一个字段，界面上的判断就永远不成立
        src = (REPO / "app" / "main.py").read_text(encoding="utf-8")
        start = src.index("def _state_run_summary(")
        block = src[start:src.index("\n    summary = {key: run[key]", start)]
        for key in ("interrupted", "interrupted_at", "superseded_by"):
            self.assertIn('"%s"' % key, block,
                          "%s 没进投影白名单，界面拿不到" % key)

    def test_auto_resume_writes_back_pointer(self):
        src = (REPO / "app" / "core" / "jobs.py").read_text(encoding="utf-8")
        start = src.index("def _maybe_auto_resume(")
        block = src[start:start + 9000]
        self.assertIn("superseded_by=new_run", block,
                      "续跑接上了却不记旧 run，列表里就会一直挂着红")
        # 顺序前提：必须先拿到新 run，才谈得上回写
        self.assertLess(block.index("store.retry_task("),
                        block.index("superseded_by=new_run"))


class ViewLayerTests(unittest.TestCase):
    """视图层：红的只留给真失败；被接上的不占行。"""

    def setUp(self):
        self.app = (UI / "app.js").read_text(encoding="utf-8")

    def test_status_text_says_interrupted(self):
        start = self.app.index("function runStatusText(")
        block = self.app[start:self.app.index("\nfunction ", start + 1)]
        self.assertIn("已中断·已续跑", block)
        self.assertIn("已中断·可续跑", block)
        # 必须排在「失败」兜底之前，否则永远显示「失败」
        self.assertLess(block.index("已中断"), block.index("return txt;"))

    def test_chip_class_separates_interrupted(self):
        start = self.app.index("function runChipClass(")
        block = self.app[start:self.app.index("\nfunction ", start + 1)]
        self.assertIn('return "interrupted"', block)

    def test_glyph_grey_for_interrupted(self):
        start = self.app.index("function staskGlyph(")
        block = self.app[start:self.app.index("\nfunction ", start + 1)]
        self.assertIn("run.interrupted", block)
        self.assertIn("sglyph intr", block)

    def test_superseded_hidden_by_default(self):
        start = self.app.index("function renderRunList(")
        block = self.app[start:self.app.index("\nfunction ", start + 1)]
        self.assertIn("superseded_by", block)
        self.assertIn("showInterrupted", block)
        self.assertIn("toggleShowInterrupted", block)
        # 默认折起：不打开开关时就必须过滤掉，否则等于没做
        self.assertIn("S.showInterrupted ? allRuns : allRuns.filter", block)

    def test_toggle_is_global_for_inline_onclick(self):
        # 行是 innerHTML 拼的，onclick 只能见到全局函数
        self.assertIn("\nfunction toggleShowInterrupted(", self.app)

    def test_css_has_quiet_variants(self):
        css = (UI / "style.css").read_text(encoding="utf-8")
        self.assertIn(".chip.interrupted", css)
        self.assertIn(".sglyph.intr", css)
        self.assertIn(".run-ghost-line", css)

    def test_labels_translated(self):
        i18n = (UI / "i18n.js").read_text(encoding="utf-8")
        for key in ("已中断·可续跑", "已中断·已续跑"):
            self.assertIn('"%s":' % key, i18n, "%s 缺英文词条" % key)


if __name__ == "__main__":
    unittest.main()
