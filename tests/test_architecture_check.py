# -*- coding: utf-8 -*-
"""架构基线检查器（architecture_check，借鉴 zai-org/ZCode）单测。

跑法：python -m unittest discover -s tests -p "test_architecture_check.py" -v

核心契约：存量豁免（基线在案不报错）、增量零容忍（新环/新反向依赖/
行数超历史最高必抓）。
"""
from __future__ import annotations

import json
from pathlib import Path
from unittest import mock

from base import BaseTest


def _mk_repo(tmp, files):
    """files: {相对路径: 内容}——app/core/ 下的假仓库。"""
    for rel, content in files.items():
        p = tmp / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(content, encoding="utf-8")


class ArchCheckTests(BaseTest):
    def _checker(self):
        import sys
        sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
        import architecture_check as ac
        return ac

    def _wire(self, ac, policy, baseline):
        ac.ROOT = self.tmp
        ac.POLICY_FILE = self.tmp / "architecture-policy.json"
        ac.BASELINE_FILE = self.tmp / ".architecture-baseline.json"
        (self.tmp / "architecture-policy.json").write_text(
            json.dumps(policy, ensure_ascii=False), encoding="utf-8")
        if baseline is not None:
            (self.tmp / ".architecture-baseline.json").write_text(
                json.dumps(baseline, ensure_ascii=False), encoding="utf-8")

    POLICY = {
        "layers": {"0": ["paths"], "1": ["store"], "2": ["runner"]},
        "forbidCycles": True,
        "maxFileLines": 100,
        "checkGlobs": ["app/**/*.py"],
    }

    def test_layer_violation_caught(self):
        ac = self._checker()
        _mk_repo(self.tmp, {
            "app/core/store.py": "from . import runner\n",   # L1 -> L2 ✓合法
            "app/core/runner.py": "from . import paths\n",
            "app/core/paths.py": "x = 1\n",
        })
        self._wire(ac, self.POLICY, {"version": 1, "violations": []})
        # store(L1) 不违规；再造一个 L0->L1 的反向
        (self.tmp / "app/core/paths.py").write_text(
            "from . import store\nx = 1\n", encoding="utf-8")
        rc = ac.main([])
        self.assertEqual(rc, 1)

    def test_baseline_exempts_existing(self):
        ac = self._checker()
        _mk_repo(self.tmp, {
            "app/core/paths.py": "from . import store\nx = 1\n",  # L0->L1 违规
            "app/core/store.py": "x = 1\n",
            "app/core/runner.py": "x = 1\n",
        })
        base = {"version": 1, "violations": [
            {"kind": "layer", "text": "layer: paths(L0) -> store(L1) 反向依赖"}]}
        self._wire(ac, self.POLICY, base)
        rc = ac.main([])
        self.assertEqual(rc, 0)

    def test_new_cycle_caught_but_known_one_exempt(self):
        ac = self._checker()
        _mk_repo(self.tmp, {
            "app/core/a.py": "from . import b\n",
            "app/core/b.py": "from . import a\n",     # 已知环 a<->b
            "app/core/c.py": "x = 1\n",
            "app/core/d.py": "x = 1\n",
        })
        base = {"version": 1, "violations": [
            {"kind": "cycle", "text": "cycle: a -> b -> a"}]}
        self._wire(ac, self.POLICY, base)
        self.assertEqual(ac.main([]), 0)              # 已知环豁免
        # 新增 c->d->c 环 → 必须抓
        (self.tmp / "app/core/c.py").write_text("from . import d\n", encoding="utf-8")
        (self.tmp / "app/core/d.py").write_text("from . import c\n", encoding="utf-8")
        self.assertEqual(ac.main([]), 1)

    def test_lines_history_growing_caught(self):
        ac = self._checker()
        body = "\n".join("line%d = %d" % (i, i) for i in range(120))  # 120 行 > 100
        _mk_repo(self.tmp, {
            "app/core/store.py": body + "\n",
            "app/core/paths.py": "x = 1\n",
            "app/core/runner.py": "x = 1\n",
        })
        # 基线记录历史最高 120 → 当前 120 行不违规
        self._wire(ac, self.POLICY, {"version": 1, "violations": [
            {"kind": "lines", "text": "old", "file": "app/core/store.py", "lines": 120}]})
        self.assertEqual(ac.main([]), 0)
        # 涨到 121 → 抓
        (self.tmp / "app/core/store.py").write_text(body + "\nextra = 1\n",
                                                    encoding="utf-8")
        self.assertEqual(ac.main([]), 1)

    def test_update_baseline_roundtrip(self):
        ac = self._checker()
        _mk_repo(self.tmp, {
            "app/core/paths.py": "from . import store\n" + "x = 1\n" * 150,
            "app/core/store.py": "from . import paths\n",
            "app/core/runner.py": "x = 1\n",
        })
        self._wire(ac, self.POLICY, {"version": 1, "violations": []})
        self.assertEqual(ac.main(["--update-baseline"]), 0)
        b = json.loads((self.tmp / ".architecture-baseline.json").read_text(encoding="utf-8"))
        kinds = [v["kind"] for v in b["violations"]]
        self.assertIn("layer", kinds)
        self.assertIn("lines", kinds)
        # 更新后再查 → 全绿
        self.assertEqual(ac.main([]), 0)

        # 带旧行数基线再次更新也必须保留该文件，不能更新后立即报新增。
        self.assertEqual(ac.main(["--update-baseline"]), 0)
        b2 = json.loads((self.tmp / ".architecture-baseline.json").read_text(
            encoding="utf-8"))
        line_rows = [v for v in b2["violations"] if v["kind"] == "lines"]
        self.assertEqual(line_rows[0]["file"], "app/core/paths.py")
        self.assertEqual(ac.main([]), 0)

    def test_changed_mode_keeps_full_graph_for_cycles(self):
        ac = self._checker()
        _mk_repo(self.tmp, {
            "app/core/a.py": "from . import b\n",
            "app/core/b.py": "from . import a\n",
            "app/core/paths.py": "x = 1\n",
            "app/core/store.py": "x = 1\n",
            "app/core/runner.py": "x = 1\n",
        })
        self._wire(ac, self.POLICY, {"version": 1, "violations": []})
        changed = {str((self.tmp / "app/core/a.py").resolve()).replace("\\", "/")}
        with mock.patch.object(ac, "changed_files", return_value=changed):
            self.assertEqual(ac.main(["--changed"]), 1)

    def test_real_repo_smoke(self):
        """真仓冒烟：守护器与真仓策略自洽、不崩（rc 0/1 均合法——多代理并行
        写入会实时产生新违规，FAIL 是正常输出；rc==2 才是策略/解析崩坏）。"""
        import subprocess
        import sys
        r = subprocess.run([sys.executable, "scripts/architecture_check.py"],
                           capture_output=True, cwd=str(
                               Path(__file__).resolve().parents[1]))
        self.assertIn(r.returncode, (0, 1),
                      r.stdout.decode("utf-8", "replace") + r.stderr.decode("utf-8", "replace"))


if __name__ == "__main__":
    import unittest
    unittest.main()
