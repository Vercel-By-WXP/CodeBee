"""11-07 19:xx 守测：ⓒ 清单导出的定位线索、7 列契约、零代填与覆盖保护。

真样本来自 `tests/fixtures/outcome_gaps_hints.json`（19:xx 生产库只读取证：
80 行真 CSV / 115 个 task 文件 / books.json 原文，含 1 处 ai→AI 静默改写）。
缺 title、多平台书名、已填结论、列数漂移等边界态都在**真实 task_id /
真实原文上派生**，夹具 `derived` 段单列标注、不计真样本。所有 CLI 测试
全 mock，绝不碰生产库 / 8765 / 生产 CSV。
"""
from __future__ import annotations

import ast
import csv
import hashlib
import importlib.util
import io
import json
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts/export_outcome_gaps.py"
FIXTURE = ROOT / "tests/fixtures/outcome_gaps_hints.json"
ROWS_FIXTURE = ROOT / "tests/fixtures/outcome_register_rows.json"
HANDOVER = ROOT / "迭代计划.md"

spec = importlib.util.spec_from_file_location("export_gaps_guard", SCRIPT)
ex = importlib.util.module_from_spec(spec)
spec.loader.exec_module(ex)
reg = ex.outcome_register  # 表头唯一真源，不重复抄一份

FX = json.loads(FIXTURE.read_text(encoding="utf-8"))
RF = json.loads(ROWS_FIXTURE.read_text(encoding="utf-8"))
REAL = FX["real_rows"]
MISSING_IDS = set(RF["missing_ids"])  # 80 个真实待登记 task_id
SCAN = FX["scan_summary"]
EVID = FX["evidence_samples"]        # 4 本真实投稿证据（生产台账原文派生）
NOISE = FX["noise_events"]           # 5 条真实非投稿动作（含 ok=True 的 publish）
LIVE_ACTIONS = set(FX["live_actions"])


def _missing():
    """待登记清单：13 条真样本的 task_id + 真 CSV 的 status/created_at/reason。"""
    return [{"task_id": x["task_id"], "status": x["status"],
             "created_at": x["created_at"], "reason": x["reason"]}
            for x in REAL]


def _derived():
    return {"coverage": {"outcome_gaps": {"missing": _missing()}}}


def _setup_data(tmp):
    """把夹具里的真实原文重放成最小落盘目录（守测专用，非生产库）。"""
    tasks = Path(tmp) / "tasks"
    publish = Path(tmp) / "publish"
    tasks.mkdir(parents=True)
    publish.mkdir(parents=True)
    books = {}
    for x in REAL:
        tasks.joinpath(x["task_id"] + ".json").write_text(
            json.dumps({"id": x["task_id"], "title": x["task_title"],
                        "workdir": x["workdir_tail"]}, ensure_ascii=False),
            encoding="utf-8")
        if x["book_titles"]:
            # 真样本里有同一 task 挂两个不同平台书名的情况，全部重放
            books[x["task_id"]] = {
                ("fanqie" if i == 0 else "qimao" if i == 1 else "p%d" % i):
                    {"title": t}
                for i, t in enumerate(x["book_titles"])
            }
    publish.joinpath("books.json").write_text(
        json.dumps(books, ensure_ascii=False), encoding="utf-8")
    # 真实台账：4 本有 LIVE_ACTIONS 成功动作（按夹具 counts 全量重放），
    # 另 5 条真实非投稿动作做噪声；守测只看字段，文件名不影响口径。
    lines = []
    for sample in EVID:
        for cnt in sample["counts"]:
            for _ in range(cnt["n"]):
                lines.append(json.dumps({
                    "task_id": sample["task_id"],
                    "platform": cnt["platform"], "action": cnt["action"],
                    "ok": True, "day": "", "ts": "", "title": "",
                }, ensure_ascii=False))
    for n in NOISE:
        lines.append(json.dumps({
            "task_id": n["task_id"], "platform": n["platform"],
            "action": n["action"], "ok": n["ok"],
        }, ensure_ascii=False))
    publish.joinpath("publish-202610.jsonl").write_text(
        "\n".join(lines) + "\n", encoding="utf-8")
    return Path(tmp)


def _snapshot(root):
    """只快照落盘真源 tasks/ 与 publish/，不含 --out 输出的本地 CSV。"""
    out = {}
    for sub in ("tasks", "publish"):
        for path in sorted((Path(root) / sub).rglob("*")):
            if path.is_file():
                out[str(path)] = hashlib.sha256(
                    path.read_bytes()).hexdigest()
    return out


def _run(argv):
    buf = io.StringIO()
    with redirect_stdout(buf):
        code = ex.main(argv)
    return code, buf.getvalue()


class _Case(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.data = _setup_data(self.tmp.name)
        self.out = Path(self.tmp.name) / "out.csv"
        self.patcher = mock.patch.object(
            ex.journey_sources, "derive_events", return_value=_derived())
        self.patcher.start()
        self.addCleanup(self.patcher.stop)

    def _build(self):
        return ex.build_rows(self.data)[0]

    # ---- 真样本与唯一真源 ----
    def test_01_header_is_single_source_of_truth(self):
        self.assertIs(ex.HEADER, reg.EXPECTED_HEADER)
        self.assertEqual(7, len(ex.HEADER))
        self.assertEqual(FX["header"], list(ex.HEADER))

    def test_02_real_samples_are_real(self):
        self.assertEqual(80, SCAN["csv_rows"])
        self.assertEqual(68, SCAN["csv_without_book_title"])
        self.assertEqual(68, SCAN["missing_with_task_title"])
        self.assertEqual(68, SCAN["missing_with_workdir_tail"])
        for x in REAL:
            self.assertIn(x["task_id"], MISSING_IDS)
            self.assertTrue(x["task_title"], x["task_id"])
            self.assertTrue(x["workdir_tail"], x["task_id"])
        self.assertEqual(13, len(REAL))
        print("真被判过的真实定位样本数 %d/%d" % (len(REAL), len(REAL)))

    # ---- 7 列契约 / 零代填 ----
    def test_03_all_rows_seven_columns_and_blank_conclusion(self):
        rows = self._build()
        self.assertEqual(13, len(rows))
        for row in rows:
            self.assertEqual(7, len(row))
            self.assertEqual("", row[5], "第 6 列只能由人填，脚本绝不代填")
            self.assertIn(row[0], MISSING_IDS)

    def test_04_second_column_only_books_json_original(self):
        rows = {r[0]: r for r in self._build()}
        for x in REAL:
            row = rows[x["task_id"]]
            expected = " / ".join(x["book_titles"])
            self.assertEqual(expected, row[1], x["task_id"])
            if not x["book_titles"]:
                self.assertIn(x["task_title"], row[6],
                              "任务标题只能进备注，不能冒充平台书名")
        # 实证：原 CSV 把 AI 静默小写；新脚本取 books.json 原文
        case = next(x for x in REAL
                    if x["task_id"] == "t-20261017-134251-3423")
        self.assertNotEqual(case["csv_book_col"], case["book_titles"][0])
        self.assertEqual(case["book_titles"][0], rows[case["task_id"]][1])

    def test_05_remark_labels_hint_as_not_book_title(self):
        for row in self._build():
            self.assertIn(ex.HINT_LABEL, row[6])
            self.assertIn("非平台书名", row[6])
            self.assertIn(ex.REMARK_BASE, row[6])
        by_id = {x["task_id"]: x for x in REAL}
        for row in self._build():
            x = by_id[row[0]]
            self.assertIn("任务标题=" + x["task_title"], row[6])
            self.assertIn("目录=" + x["workdir_tail"], row[6])

    def test_06_missing_hint_is_fail_closed(self):
        """派生：真 task 缺 title/workdir → 报无线索，不猜、不抛错。"""
        tid = REAL[0]["task_id"]
        (self.data / "tasks" / (tid + ".json")).write_text(
            json.dumps({"id": tid}, ensure_ascii=False), encoding="utf-8")
        rows = {r[0]: r for r in self._build()}
        self.assertIn("无", rows[tid][6])
        self.assertIn("判不了", rows[tid][6])
        self.assertNotIn(ex.HINT_LABEL, rows[tid][6])

    def test_07_multiple_titles_joined_without_duplicates(self):
        """派生：真 task 多平台不同书名 → 全部原文进第 2 列；同名去重。"""
        by_id = {r[0]: r for r in self._build()}
        for x in REAL:
            self.assertEqual(" / ".join(x["book_titles"]),
                             by_id[x["task_id"]][1], x["task_id"])
        books_path = self.data / "publish" / "books.json"
        books = json.loads(books_path.read_text(encoding="utf-8"))
        tid = REAL[6]["task_id"]
        title = REAL[6]["book_titles"][0]
        books[tid] = {"fanqie": {"title": title},
                      "qimao": {"title": title}}  # 同名 → 只留一次
        books_path.write_text(json.dumps(books, ensure_ascii=False),
                             encoding="utf-8")
        rows = {r[0]: r for r in self._build()}
        self.assertEqual(title, rows[tid][1])
        books[tid]["qimao"] = {"title": "另一个平台书名"}  # 不同名 → 并列
        books_path.write_text(json.dumps(books, ensure_ascii=False),
                             encoding="utf-8")
        rows = {r[0]: r for r in self._build()}
        self.assertEqual(title + " / 另一个平台书名", rows[tid][1])

    # ---- 覆盖保护 ----
    def test_08_guard_rejects_filled_conclusion(self):
        """派生：人已填结论的 CSV 绝不覆盖，且文件逐字节不变。"""
        ex.write_csv(self.out, self._build())
        with open(self.out, "r", encoding="utf-8-sig", newline="") as fh:
            rows = list(csv.reader(fh))
        rows[1][5] = "qimao|signed|"
        with open(self.out, "w", encoding="utf-8-sig", newline="") as fh:
            csv.writer(fh).writerows(rows)
        before = self.out.read_bytes()
        reason = ex.guard_existing(self.out)
        self.assertIsNotNone(reason)
        self.assertIn("拒绝覆盖", reason)
        code, text = _run(["--data-dir", str(self.data), "--out", str(self.out)])
        self.assertEqual(2, code)
        self.assertIn("拒绝覆盖", text)
        self.assertEqual(before, self.out.read_bytes())

    def test_09_guard_rejects_column_drift(self):
        """派生：列数漂移判不了结论列在哪，fail-closed。"""
        ex.write_csv(self.out, self._build())
        with open(self.out, "r", encoding="utf-8-sig", newline="") as fh:
            rows = list(csv.reader(fh))
        rows[1] = rows[1][:6]
        with open(self.out, "w", encoding="utf-8-sig", newline="") as fh:
            csv.writer(fh).writerows(rows)
        reason = ex.guard_existing(self.out)
        self.assertIn("fail-closed", reason)

    # ---- 零 HTTP / 只读 / 消费点 ----
    def test_10_zero_http_and_data_dir_readonly(self):
        source = SCRIPT.read_text(encoding="utf-8")
        tree = ast.parse(source)
        imported = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                imported.update(a.name for a in node.names)
            elif isinstance(node, ast.ImportFrom):
                imported.add(node.module or "")
        for bad in ("urllib", "requests", "http.client", "socket"):
            # 精确名 + 子模块前缀都要拦：import urllib.request 不是 "urllib"
            self.assertFalse(
                any(name == bad or name.startswith(bad + ".")
                    for name in imported),
                "不许引入网络依赖：%s" % bad)
        for word in ("urlopen", "api_post", "api_get"):
            self.assertNotIn(word, source)
        flags = set()
        for node in ast.walk(tree):
            if (isinstance(node, ast.Call)
                    and isinstance(node.func, ast.Attribute)
                    and node.func.attr == "add_argument" and node.args
                    and isinstance(node.args[0], ast.Constant)):
                flags.add(str(node.args[0].value))
        self.assertFalse([f for f in flags if f.startswith("--force")],
                         "不许提供绕过覆盖保护的强制开关")
        before = _snapshot(self.data)
        code, text = _run(["--data-dir", str(self.data), "--out", str(self.out)])
        self.assertEqual(0, code, text)
        self.assertTrue(before == _snapshot(self.data),
                        "生产落盘目录必须零改动")

    def test_11_exported_csv_accepted_by_outcome_register(self):
        code, _ = _run(["--data-dir", str(self.data), "--out", str(self.out)])
        self.assertEqual(0, code)
        header, rows = reg.read_csv(str(self.out))
        judged = reg.judge_rows(header, rows, MISSING_IDS)
        self.assertEqual([], judged["blocked"])
        self.assertEqual([], judged["unknown"])
        self.assertEqual(0, judged["judged"], "结论恒空 → 真被判过 0 行")
        self.assertEqual(13, len(judged["skip"]))
        self.assertEqual(header, list(reg.EXPECTED_HEADER))

    def test_12_reports_true_judged_count(self):
        code, text = _run(["--data-dir", str(self.data), "--out", str(self.out)])
        self.assertEqual(0, code)
        self.assertIn("真被判过（能给出定位线索）：13/13 行", text)
        self.assertIn("第 6 列恒空", text)
        self.assertIn("零 HTTP", text)

    def test_13_rejects_missing_data_dir(self):
        code, text = _run(["--data-dir", str(self.data / "nope"),
                           "--out", str(self.out)])
        self.assertEqual(2, code)
        self.assertIn("拒绝", text)
        self.assertFalse(self.out.exists())

    def test_14_bad_task_file_counted_not_guessed(self):
        """派生：坏 task 文件计数、不影响其它行，也不猜线索。"""
        bad = self.data / "tasks" / "broken.json"
        bad.write_text("{not json", encoding="utf-8")
        rows, stats = ex.build_rows(self.data)
        self.assertEqual(1, stats["bad_task_files"])
        self.assertEqual(13, len(rows))
        self.assertEqual(13, stats["hint_rows"])

    def test_15_handover_documents_the_consumer(self):
        text = HANDOVER.read_text(encoding="utf-8")
        self.assertTrue("export_outcome_gaps.py" in text,
                        "交接单缺导出脚本记录")
        self.assertTrue("定位线索" in text, "交接单缺定位线索口径")

    # ---- 21:3x 追加：台账证据只是线索，绝不进判据 / 不代填 ----
    def test_16_evidence_reuses_project_live_actions(self):
        """口径唯一真源：投稿证据必须复用 journey_sources.LIVE_ACTIONS。"""
        self.assertEqual(sorted(ex.journey_sources.LIVE_ACTIONS),
                         sorted(LIVE_ACTIONS))
        self.assertEqual(23, SCAN["tasks_with_publish_evidence"])
        self.assertEqual(57, SCAN["tasks_without_publish_evidence"])
        self.assertEqual(235, SCAN["live_ok_events"])
        self.assertEqual(0, SCAN["bad_publish_lines"])
        source = SCRIPT.read_text(encoding="utf-8")
        self.assertIn("LIVE_ACTIONS", source,
                      "不许另造一套投稿动作词表")
        for word in ("connect", "probe", "auto_fire"):
            self.assertNotIn('"%s"' % word, source,
                             "别把非投稿动作硬编码进证据口径")

    def test_17_evidence_only_in_remark_not_in_columns_1_to_6(self):
        """有/无台账两种情况下，第 1-6 列必须逐字相同。"""
        rows_with = {r[0]: r for r in self._build()}
        ledger = self.data / "publish" / "publish-202610.jsonl"
        ledger.unlink()
        rows_without = {r[0]: r for r in self._build()}
        self.assertEqual(sorted(rows_with), sorted(rows_without))
        for tid in rows_with:
            self.assertEqual(rows_without[tid][:6], rows_with[tid][:6],
                             "%s：证据不许改动第 1-6 列" % tid)
            self.assertEqual("", rows_with[tid][5])
        for sample in EVID:
            remark = rows_with[sample["task_id"]][6]
            self.assertIn(ex.EVIDENCE_LABEL, remark)
            for cnt in sample["counts"]:
                self.assertIn(cnt["action"], remark)
                self.assertIn(cnt["platform"], remark)
        zero = [r for r in rows_with.values()
                if "无成功投稿动作" in r[6]]
        self.assertEqual(len(rows_with) - len(EVID), len(zero))

    def test_18_noise_and_failed_actions_are_not_evidence(self):
        """真噪声：ok=True 的 publish 但不在 LIVE_ACTIONS，也不算投稿证据。"""
        ev = ex.load_publish_evidence(self.data)
        for tid, counts in ev.items():
            for (_, action), n in counts.items():
                self.assertIn(action, LIVE_ACTIONS)
                self.assertGreater(n, 0)
        noise_only = {n["action"] for n in NOISE} - LIVE_ACTIONS
        self.assertTrue(noise_only, "夹具必须留真实非投稿动作")
        self.assertNotIn("publish", {a for c in ev.values()
                                     for (_, a) in c})
        # 派生：把一本真样本的 LIVE 行 ok 改 false → 证据整体消失
        target = EVID[0]["task_id"]
        ledger = self.data / "publish" / "publish-202610.jsonl"
        lines = []
        for line in ledger.read_text(encoding="utf-8").splitlines():
            obj = json.loads(line)
            if obj.get("task_id") == target:
                obj["ok"] = False
            lines.append(json.dumps(obj, ensure_ascii=False))
        ledger.write_text("\n".join(lines) + "\n", encoding="utf-8")
        self.assertNotIn(target, ex.load_publish_evidence(self.data))

    def test_19_bad_ledger_lines_counted_and_fail_closed(self):
        """派生：坏 JSON 行计数、不猜、不影响其它行。"""
        ledger = self.data / "publish" / "publish-202610.jsonl"
        ledger.write_text(ledger.read_text(encoding="utf-8")
                          + "{not json\n", encoding="utf-8")
        ev, stat = ex.load_publish_evidence(self.data, with_stat=True)
        self.assertEqual(1, stat["bad_lines"])
        self.assertGreater(stat["lines"], stat["bad_lines"])
        self.assertIn(EVID[0]["task_id"], ev)
        rows, stats = ex.build_rows(self.data)
        self.assertEqual(1, stats["bad_ledger_lines"])
        self.assertEqual(13, len(rows))

    def test_20_evidence_never_reaches_judgement_layer(self):
        """判据层零漂移：judge_rows / parse_conclusion 不接证据参数。"""
        reg_tree = ast.parse((ROOT / "scripts/outcome_register.py").read_text(
            encoding="utf-8"))
        sigs = {n.name: [a.arg for a in n.args.args]
                for n in reg_tree.body if isinstance(n, ast.FunctionDef)}
        self.assertEqual(["header", "rows", "missing_ids"],
                         sigs["judge_rows"])
        self.assertEqual(["cell"], sigs["parse_conclusion"])
        ex_tree = ast.parse(SCRIPT.read_text(encoding="utf-8"))
        for node in ast.walk(ex_tree):
            if (isinstance(node, ast.Call)
                    and isinstance(node.func, ast.Attribute)
                    and node.func.attr in ("judge_rows", "parse_conclusion")):
                self.fail("导出脚本不许调用判据：%s" % node.func.attr)
        # 导出的 CSV 判据仍只认第 6 列：13 行全空 → 真被判过 0
        rows = self._build()
        judged = reg.judge_rows(list(reg.EXPECTED_HEADER), rows, MISSING_IDS)
        self.assertEqual(0, judged["judged"])
        self.assertEqual(13, len(judged["skip"]))
        self.assertEqual([], judged["blocked"])

    def test_21_blank_conclusion_is_legal_and_documented(self):
        """零证据 ≠ 从未投稿；留空是合法核对结论，不必填满 80 行。"""
        rows = self._build()
        for row in rows:
            for phrase in ("留空合法", "零证据 ≠ 从未投过", "不必填满"):
                self.assertIn(phrase, row[6])
        buf = io.StringIO()
        with redirect_stdout(buf):
            self.assertEqual(0, reg.main(["--template"]))
        tpl = buf.getvalue()
        self.assertIn("留空合法", tpl)
        self.assertIn("不必填满", tpl)
        lines = HANDOVER.read_text(encoding="utf-8").splitlines()
        # 按行首定位第 3 节本轮段落：只查「全文含某词」会被表格行假绿
        para = next((l for l in lines
                     if l.startswith("> **11-07 21:")
                     and "台账证据层" in l), None)
        self.assertIsNotNone(para, "第 3 节缺本轮变更日志段")
        for phrase in ("23/57", "留空合法", "台账证据",
                       "零证据 ≠ 从未投过", "不进判据"):
            self.assertTrue(phrase in para,
                            "第 3 节段落缺口径：%s" % phrase)
        row = next((l for l in lines
                    if l.startswith("| 11-07 | **ⓒ 清单补台账证据层")), None)
        self.assertIsNotNone(row, "第 4 节缺变更日志行")
        self.assertTrue("23/57" in row, "第 4 节表格行缺口径")


if __name__ == "__main__":
    unittest.main()
