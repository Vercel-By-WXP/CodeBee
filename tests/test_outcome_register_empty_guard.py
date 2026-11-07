"""11-07 23:1x 守测：ⓒ 补登工具在「空清单 / 真源零分母 / CSV 缺失」要真 fail-closed。

只读取证（tmp/audit_csv_empty_2315.py，零 HTTP / 零写入）坐实四种边界：
  A 只剩表头  → 「清单还没核对（0 行结论全空）」+「留空合法」+ exit 0（假绿）
  B 0 字节    → 表头漂移 blocked + exit 2（与 A 同为「空」却结论相反）
  C 真源指错  → 「真被判过 0/0」+ 提醒 fail-closed，却仍 exit 0
  D 文件不存在 → 裸 FileNotFoundError traceback，没人话恢复指引

`data/exports/` 已 gitignore、CSV 不进仓，换机器或误清空就会撞上，不是假想场景。

修法只追加 unknown 判据 / 人话异常 / 分母提醒；判据签名、四路结构、覆盖率
分母、IO 次数、九步与生产代码零改动。真样本来自 outcome_register_rows.json
（80 行真 CSV / 115 task / 81 分母）；边界态一律在真行上派生，不造业务结论。
所有 CLI 测试全 mock，绝不碰生产库 / 8765。
"""
from __future__ import annotations

import ast
import csv
import importlib.util
import io
import json
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "outcome_register.py"
FIXTURE = ROOT / "tests" / "fixtures" / "outcome_register_rows.json"
HANDOVER = ROOT / "迭代计划.md"

spec = importlib.util.spec_from_file_location(
    "outcome_register_empty_guard", SCRIPT)
reg = importlib.util.module_from_spec(spec)
spec.loader.exec_module(reg)

FX = json.loads(FIXTURE.read_text(encoding="utf-8"))
HEADER = FX["header"]
REAL_ROWS = FX["real_rows"]
MISSING = set(FX["missing_ids"])
GAPS = {
    "total": FX["scan_summary"]["outcome_gaps_total"],
    "registered": FX["scan_summary"]["outcome_gaps_registered"],
    "registration_rate": FX["scan_summary"]["outcome_gaps_rate"],
    "missing": [{"task_id": t} for t in FX["missing_ids"]],
}
ZERO_GAPS = {"total": 0, "registered": 0,
             "registration_rate": 0, "missing": []}
RESTORE_CMD = "export_outcome_gaps.py --prod"


class _Case(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.dir = Path(self.tmp.name)
        self.csv = self.dir / "gaps.csv"
        self.calls = {"load": [], "post": [], "get": []}

    def write_csv(self, rows):
        """写一份 utf-8-sig 的 7 列清单；rows 为空即「只剩表头」。"""
        with self.csv.open("w", encoding="utf-8-sig", newline="") as fh:
            csv.writer(fh).writerows([HEADER] + list(rows))
        return str(self.csv)

    def write_empty_file(self):
        self.csv.write_bytes(b"")
        return str(self.csv)

    def gates(self, missing=MISSING, gaps=GAPS):
        calls = self.calls

        def _load(data_dir):
            calls["load"].append(data_dir)
            return set(missing), dict(gaps)

        def _post(base, path, body):
            calls["post"].append(path)
            return {}

        def _get(base, path):
            calls["get"].append(path)
            return {}

        return (mock.patch.object(reg, "load_missing_ids", _load),
                mock.patch.object(reg, "api_post", _post),
                mock.patch.object(reg, "api_get", _get))

    def check(self, csv_path=None, missing=MISSING, gaps=GAPS):
        buf = io.StringIO()
        p, q, r = self.gates(missing, gaps)
        with p, q, r, redirect_stdout(buf):
            rc = reg.cmd_check(csv_path or str(self.csv), "unused")
        return rc, buf.getvalue()

    def apply(self, confirmed=True, csv_path=None,
              missing=MISSING, gaps=GAPS, dry_run=False):
        buf = io.StringIO()
        p, q, r = self.gates(missing, gaps)
        with p, q, r, redirect_stdout(buf):
            rc = reg.cmd_apply(csv_path or str(self.csv), "unused",
                               "http://127.0.0.1:1", dry_run, confirmed)
        return rc, buf.getvalue()


class TestFixtureContract(_Case):
    def test_01_real_fixture_shape(self):
        """真夹具口径：表头 7 列逐字一致、80 本待登记、分母 81。"""
        self.assertEqual(7, len(HEADER))
        self.assertEqual(reg.EXPECTED_HEADER, HEADER)
        self.assertEqual(80, len(MISSING))
        self.assertEqual(81, GAPS["total"])
        self.assertTrue(REAL_ROWS, "真 CSV 行不能是空的")


class TestEmptyCsvIsNotGreen(_Case):
    def test_02_header_only_becomes_unknown_and_exit2(self):
        """A：只剩表头 = 数据全丢，必须判不了 / 拒写，不许报「0 行全空」绿标。"""
        self.write_csv([])
        r = reg.judge_rows(HEADER, [], MISSING)
        self.assertTrue(any("0 数据行" in x["reason"] for x in r["unknown"]),
                        "0 数据行必须进 unknown：%r" % (r["unknown"],))
        rc, text = self.check()
        self.assertEqual(2, rc, text)
        self.assertNotIn("清单还没核对", text)
        self.assertNotIn("留空合法", text)
        self.assertEqual([], self.calls["post"])

    def test_03_zero_byte_still_blocked(self):
        """B：0 字节走表头漂移；A 与 B 同为「空」，都必须拒写。"""
        p = self.write_empty_file()
        header, rows = reg.read_csv(p)
        self.assertEqual([], header)
        self.assertEqual([], rows)
        r = reg.judge_rows(header, rows, MISSING)
        self.assertTrue(any("表头漂移" in x["reason"] for x in r["blocked"]))
        rc, text = self.check(csv_path=p)
        self.assertEqual(2, rc, text)
        self.assertEqual([], self.calls["post"])

    def test_04_empty_truth_source_is_unknown_and_exit2(self):
        """C：真源零分母 = 判不了归不归这本；notes 口径保留、但必须 exit 2。"""
        r = reg.judge_rows(HEADER, REAL_ROWS, set())
        self.assertTrue(any("fail-closed" in n for n in r["notes"]),
                        "旧 test_22 的 notes 口径不许破坏")
        self.assertTrue(any("真源零待登记" in x["reason"]
                            for x in r["unknown"]),
                        "零分母必须进 unknown：%r" % (r["unknown"],))
        self.write_csv(REAL_ROWS)
        rc, text = self.check(missing=set(), gaps=ZERO_GAPS)
        self.assertEqual(2, rc, text)
        self.assertIn("0/0 不是覆盖率", text)
        self.assertEqual([], self.calls["post"])

    def test_05_missing_csv_human_error_and_zero_post(self):
        """D：文件不存在给人话恢复指引、exit 2、零 POST，不许裸 traceback。"""
        gone = str(self.dir / "gone.csv")
        self.assertFalse(Path(gone).exists())
        with self.assertRaises(Exception) as ctx:
            reg.read_csv(gone)
        self.assertIn(RESTORE_CMD, str(ctx.exception))
        rc, text = self.check(csv_path=gone)
        self.assertEqual(2, rc, text)
        self.assertIn(RESTORE_CMD, text)
        self.assertNotIn("Traceback", text)
        rc2, text2 = self.apply(confirmed=True, csv_path=gone)
        self.assertEqual(2, rc2, text2)
        self.assertEqual([], self.calls["post"])
        self.assertEqual([], self.calls["get"])
        self.assertEqual([], self.calls["load"])

    def test_06_first_gate_precedes_csv_check(self):
        """--apply 缺确认标志时第一道闸仍最前：CSV 不存在也不许抢跑 / 零 IO。"""
        gone = str(self.dir / "gone.csv")
        rc, text = self.apply(confirmed=False, csv_path=gone)
        self.assertEqual(2, rc, text)
        self.assertIn("拒写：--apply 必须同时带", text)
        self.assertEqual([], self.calls["load"])
        self.assertEqual([], self.calls["post"])
        self.assertEqual([], self.calls["get"])

    def test_07_normal_baseline_unchanged(self):
        """正常清单不受影响：exit 0、真被判过 0/N、不报 unknown、不报零分母。"""
        self.write_csv(REAL_ROWS)
        rc, text = self.check()
        self.assertEqual(0, rc, text)
        self.assertIn("真被判过：0/%d 行" % len(REAL_ROWS), text)
        self.assertNotIn("unknown=1", text)
        self.assertNotIn("0/0 不是覆盖率", text)
        self.assertIn("清单还没核对", text)

    def test_08_zero_denominator_note_only_when_zero(self):
        """分母提醒只在 total=0 时出现，正常 1/81 不许冒出来。"""
        result = reg.judge_rows(HEADER, REAL_ROWS, MISSING)
        buf = io.StringIO()
        with redirect_stdout(buf):
            reg.print_verdict(result, ZERO_GAPS)
        self.assertIn("0/0 不是覆盖率", buf.getvalue())
        buf2 = io.StringIO()
        with redirect_stdout(buf2):
            reg.print_verdict(result, GAPS)
        self.assertNotIn("0/0 不是覆盖率", buf2.getvalue())
        self.assertIn("1/81", buf2.getvalue())


class TestCodeAndHandoverContract(_Case):
    def test_09_ast_signature_and_gate_order(self):
        """签名 3 参不变、第一道闸仍是函数体首句、read_csv 真判存在性。"""
        tree = ast.parse(SCRIPT.read_text(encoding="utf-8"))
        fns = {n.name: n for n in tree.body
               if isinstance(n, ast.FunctionDef)}
        self.assertEqual(["header", "rows", "missing_ids"],
                         [a.arg for a in fns["judge_rows"].args.args])
        first = fns["cmd_apply"].body[0]
        self.assertIsInstance(first, ast.If)
        self.assertIn("confirmed", ast.dump(first.test),
                      "确认标志必须仍在第一次 IO 之前")
        self.assertIn("is_file", ast.dump(fns["read_csv"]))
        main_dump = ast.dump(fns["main"])
        self.assertIn("cmd_check", main_dump)
        self.assertIn("cmd_apply", main_dump)

    def test_10_handover_documents_the_guard(self):
        """交接单第 3 节必须记这一笔（按段落行首定位，别用全文包含假绿）。"""
        lines = HANDOVER.read_text(encoding="utf-8").splitlines()
        para = next((l for l in lines
                     if l.startswith("> **11-07 23:") and "空清单" in l),
                    None)
        self.assertIsNotNone(para, "交接单缺 23:xx 空清单守卫段落")
        for phrase in ("fail-closed", "exit 2", "不进仓", "unknown"):
            self.assertTrue(phrase in para, "段落缺口径：%s" % phrase)


if __name__ == "__main__":
    unittest.main()
