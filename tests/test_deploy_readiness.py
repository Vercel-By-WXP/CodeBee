#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""部署就绪对账守测：真夹具 + 派生边界（单列标注，不计真样本）。

真样本：`tests/fixtures/deploy_readiness_20261107.json` 是 11-07 23:52 对本机
真实部署包的只读对账结论（973/973 文件、真被判过 971/971）。
派生边界：tmpdir 造目录验证判据分支，用来证明「改坏了会红」，不算真样本、
不进「真被判过」计数，也绝不连 8765 / 写生产文件。
"""
from __future__ import annotations

import ast
import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

import deploy_readiness as dr  # noqa: E402

SCRIPT = ROOT / "scripts" / "deploy_readiness.py"
FIXTURE = ROOT / "tests" / "fixtures" / "deploy_readiness_20261107.json"
HANDOVER = ROOT / "迭代计划.md"


def _tree(root, files):
    """守测专用：造一个含 app/ 的目录树（派生夹具，不是生产数据）。"""
    app = Path(root) / "app"
    app.mkdir(parents=True, exist_ok=True)
    for rel, data in files.items():
        p = app / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_bytes(data if isinstance(data, bytes) else data.encode("utf-8"))
    return app


def _dep_root():
    return Path(os.environ.get("CODEBEE_DEPLOY_DIR", str(dr.DEFAULT_DEP_ROOT)))


REAL = (_dep_root() / "app").is_dir()


class RealBaselineTest(unittest.TestCase):
    """真样本：本机真实仓库 ↔ 真实部署包，只读、零写入。"""

    @unittest.skipUnless(REAL, "本机没有部署包副本；换机器用 --dep-root 复跑")
    def test_01_real_pair_matches_frozen_baseline(self):
        base = json.loads(FIXTURE.read_text(encoding="utf-8"))
        d = dr.build_result(str(ROOT), str(_dep_root()))
        self.assertEqual(base["verdict"], d["verdict"])
        self.assertEqual(base["counts"], d["counts"])
        self.assertEqual(base["lists"], d["lists"])
        c = d["counts"]
        self.assertEqual(c["compared"], c["byte_equal"] + c["eol_only"] + c["content_diff"])
        self.assertEqual(0, c["content_diff"], "真差异必须为空，重启才不会加载旧代码")
        self.assertGreater(c["compared"], 900, "真被判过样本数不能是空集假绿")

    @unittest.skipUnless(REAL, "本机没有部署包副本；换机器用 --dep-root 复跑")
    def test_02_real_cli_exit_zero_and_reports_judged_count(self):
        res = subprocess.run([sys.executable, str(SCRIPT)],
                             capture_output=True, text=True,
                             encoding="utf-8", errors="replace")
        self.assertEqual(0, res.returncode, (res.stdout or "")[-600:])
        self.assertIn("真被判过", res.stdout)
        self.assertIn("零 HTTP / 零写入", res.stdout)
        self.assertIn("结论：ok", res.stdout)
        self.assertIn("serial_gate.py", res.stdout)

    @unittest.skipUnless(REAL, "本机没有部署包副本；换机器用 --dep-root 复跑")
    def test_03_real_py38_smoke_zero_failure(self):
        d = dr.build_result(str(ROOT), str(_dep_root()), check_py38=True)
        self.assertIn("py38", d)
        self.assertEqual("ok", d["py38"]["status"], d["py38"])
        self.assertEqual(0, d["py38"]["failed"])
        self.assertGreater(d["py38"]["compiled"], 200)
        self.assertEqual("ok", d["verdict"])

    def test_04_fixture_records_real_serial_gate_gap(self):
        """真缺口必须留在夹具里：仓库有、部署包没有，接调度前要同步。"""
        base = json.loads(FIXTURE.read_text(encoding="utf-8"))
        self.assertEqual("ok", base["verdict"])
        self.assertIn("core/publish/serial_gate.py", base["lists"]["only_repo"])
        self.assertIn("core/quality/constraints.py", base["lists"]["eol_only"])
        self.assertEqual(2, len(base["lists"]["only_dep"]))


class JudgePairTest(unittest.TestCase):
    """派生边界：证明每条判据改坏了会红，不计入真被判过样本数。"""

    def test_05_identical_trees_ok(self):
        d = dr.judge_pair({"a.py": b"x = 1\n"}, {"a.py": b"x = 1\n"})
        self.assertEqual("ok", d["verdict"])
        self.assertEqual(1, d["counts"]["compared"])
        self.assertEqual(1, d["counts"]["byte_equal"])

    def test_06_eol_only_is_note_not_blocked(self):
        d = dr.judge_pair({"core/quality/constraints.py": b"a\r\nb\r\n"},
                          {"core/quality/constraints.py": b"a\nb\n"})
        self.assertEqual("ok", d["verdict"])
        self.assertEqual(1, d["counts"]["eol_only"])
        self.assertTrue(any("已知仅行尾差异" in n for n in d["notes"]))

    def test_07_unknown_eol_diff_still_reported(self):
        d = dr.judge_pair({"other.py": b"a\r\n"}, {"other.py": b"a\n"})
        self.assertEqual("ok", d["verdict"])
        self.assertTrue(any("不在已知白名单" in n for n in d["notes"]),
                        "未知行尾差异不能静默吞掉")

    def test_08_content_diff_blocked(self):
        d = dr.judge_pair({"x.py": b"old"}, {"x.py": b"new"})
        self.assertEqual("blocked", d["verdict"])
        self.assertEqual(1, d["counts"]["content_diff"])
        self.assertTrue(any("内容不一致" in b for b in d["blockers"]))

    def test_09_only_repo_unreferenced_is_note_not_block(self):
        """serial_gate.py 现状：默认关、零消费点，不许误判成重启会挂。"""
        d = dr.judge_pair(
            {"core/publish/serial_gate.py": b"# gate\n", "a.py": b"x"},
            {"a.py": b"x"})
        self.assertEqual("ok", d["verdict"])
        self.assertTrue(any("接调度" in n for n in d["notes"]))

    def test_10_only_repo_referenced_is_blocked(self):
        """一旦接了调度（部署包出现引用），缺模块就是重启 500，必须红。"""
        d = dr.judge_pair(
            {"core/publish/serial_gate.py": b"# gate\n", "a.py": b"x"},
            {"a.py": b"from core.publish import serial_gate\n"})
        self.assertEqual("blocked", d["verdict"])
        self.assertTrue(any("ModuleNotFoundError" in b for b in d["blockers"]))

    def test_11_only_dep_backup_is_note(self):
        d = dr.judge_pair({"a.py": b"x"},
                          {"a.py": b"x", "ui/index.html.bak-cspace-20261105b": b"bak"})
        self.assertEqual("ok", d["verdict"])
        self.assertTrue(any("历史备份" in n for n in d["notes"]))

    def test_12_only_dep_plain_file_is_unknown(self):
        d = dr.judge_pair({"a.py": b"x"}, {"a.py": b"x", "core/extra.py": b"y"})
        self.assertEqual("unknown", d["verdict"])
        self.assertTrue(any("判不了" in u for u in d["unknowns"]))

    def test_13_unreadable_file_is_unknown(self):
        d = dr.judge_pair({"a.py": b"x"}, {"a.py": b"x"}, unreadable=["core/b.py"])
        self.assertEqual("unknown", d["verdict"])
        self.assertEqual(1, d["counts"]["unreadable"])

    def test_14_empty_trees_are_unknown_not_ok(self):
        d = dr.judge_pair({}, {})
        self.assertEqual("unknown", d["verdict"])
        self.assertTrue(any("0/0" in u for u in d["unknowns"]),
                        "0/0 不是就绪，别拿空目录当达标")

    def test_15_no_overlap_is_unknown(self):
        d = dr.judge_pair({"a.py": b"x"}, {"b.py": b"x"})
        self.assertEqual("unknown", d["verdict"])
        self.assertTrue(any("共有文件 0" in u for u in d["unknowns"]))

    def test_16_blockers_beat_unknowns(self):
        d = dr.judge_pair({"a.py": b"1", "gone.py": b"x"},
                          {"a.py": b"2", "extra.py": b"y"})
        self.assertEqual("blocked", d["verdict"])


class ContractTest(unittest.TestCase):
    """IO / CLI / 只读契约：脚本不许变成第二个写入口。"""

    def test_17_missing_dir_is_unknown_with_recovery_hint(self):
        with tempfile.TemporaryDirectory() as t:
            d = dr.build_result(str(Path(t) / "nope"), str(Path(t) / "nope2"))
            self.assertEqual("unknown", d["verdict"])
            self.assertTrue(any("恢复指引" in u for u in d["unknowns"]))
            self.assertTrue(any("CODEBEE_DEPLOY_DIR" in u for u in d["unknowns"]))

    def test_18_cli_exit_code_ok_and_blocked(self):
        with tempfile.TemporaryDirectory() as t:
            good = Path(t) / "good"
            _tree(good, {"a.py": b"x = 1\n"})
            ok = subprocess.run(
                [sys.executable, str(SCRIPT), "--repo-root", str(good),
                 "--dep-root", str(good)],
                capture_output=True, text=True, encoding="utf-8", errors="replace")
            self.assertEqual(0, ok.returncode, (ok.stdout or "")[-400:])

            left, right = Path(t) / "left", Path(t) / "right"
            _tree(left, {"a.py": b"old"})
            _tree(right, {"a.py": b"new"})
            bad = subprocess.run(
                [sys.executable, str(SCRIPT), "--repo-root", str(left),
                 "--dep-root", str(right)],
                capture_output=True, text=True, encoding="utf-8", errors="replace")
            self.assertEqual(2, bad.returncode)
            self.assertIn("结论：blocked", bad.stdout)

    def test_19_json_shape_and_no_py38_by_default(self):
        with tempfile.TemporaryDirectory() as t:
            good = Path(t) / "good"
            _tree(good, {"a.py": b"x"})
            res = subprocess.run(
                [sys.executable, str(SCRIPT), "--repo-root", str(good),
                 "--dep-root", str(good), "--json"],
                capture_output=True, text=True, encoding="utf-8", errors="replace")
            self.assertEqual(0, res.returncode)
            d = json.loads(res.stdout)
            self.assertEqual("ok", d["verdict"])
            self.assertEqual(1, d["counts"]["compared"])
            self.assertNotIn("py38", d, "3.8 编译必须显式开关，默认别偷偷跑")

    def test_20_script_is_read_only_and_http_free(self):
        tree = ast.parse(SCRIPT.read_text(encoding="utf-8"))
        banned_mods = ("urllib", "http", "requests", "socket", "shutil", "httpx")
        bad_imports = []
        for n in ast.walk(tree):
            if isinstance(n, ast.Import):
                for a in n.names:
                    if a.name.split(".")[0] in banned_mods:
                        bad_imports.append(a.name)
            elif isinstance(n, ast.ImportFrom) and n.module:
                if n.module.split(".")[0] in banned_mods:
                    bad_imports.append(n.module)
        self.assertEqual([], bad_imports, "对账脚本不许联网")

        banned_calls = ("write_bytes", "write_text", "unlink", "rmdir", "remove",
                        "rmtree", "copy", "copy2", "move", "chmod", "eval", "exec")
        bad_calls = []
        for n in ast.walk(tree):
            if not isinstance(n, ast.Call):
                continue
            f = n.func
            if isinstance(f, ast.Attribute) and f.attr in banned_calls:
                bad_calls.append(f.attr)
            if isinstance(f, ast.Name) and f.id in ("eval", "exec"):
                bad_calls.append(f.id)
            if isinstance(f, ast.Name) and f.id == "open":
                bad_calls.append("open")
        self.assertEqual([], bad_calls, "对账脚本不许写盘 / 不许 open")

    def test_21_judge_pair_is_pure(self):
        tree = ast.parse(SCRIPT.read_text(encoding="utf-8"))
        fn = next(n for n in tree.body
                  if isinstance(n, ast.FunctionDef) and n.name == "judge_pair")
        banned = ("open", "subprocess", "read_bytes", "read_text", "eval", "exec")
        bad = []
        for n in ast.walk(fn):
            if isinstance(n, ast.Name) and n.id in banned:
                bad.append(n.id)
            if isinstance(n, ast.Attribute) and n.attr in ("read_bytes", "read_text", "run"):
                bad.append(n.attr)
            if isinstance(n, (ast.Import, ast.ImportFrom)):
                bad.append("import")
        self.assertEqual([], bad, "judge_pair 必须保持纯函数，守测才能喂字节字典")

    def test_22_cli_flag_whitelist(self):
        tree = ast.parse(SCRIPT.read_text(encoding="utf-8"))
        flags = set()
        for n in ast.walk(tree):
            if (isinstance(n, ast.Call) and isinstance(n.func, ast.Attribute)
                    and n.func.attr == "add_argument" and n.args
                    and isinstance(n.args[0], ast.Constant)
                    and str(n.args[0].value).startswith("--")):
                flags.add(str(n.args[0].value))
        self.assertEqual({"--repo-root", "--dep-root", "--json", "--check-py38"}, flags,
                         "别给对账脚本加写开关 / 绕窗口开关")

    def test_23_handover_records_pre_restart_gate(self):
        """消费点：交接单必须写明 ④ 重启前跑这一层，否则工具又是死代码。"""
        lines = HANDOVER.read_text(encoding="utf-8").splitlines()
        seg = [l for l in lines
               if l.startswith("> **11-") and "部署就绪对账" in l]
        self.assertEqual(1, len(seg), "新段要唯一，别让历史段抢命中")
        self.assertIn("deploy_readiness.py", seg[0])
        self.assertIn("serial_gate.py", seg[0])
        self.assertIn("重启", seg[0])


if __name__ == "__main__":
    unittest.main()
