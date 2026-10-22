# -*- coding: utf-8 -*-
"""Tests for the Git-versioned code cognition map."""
import json
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from app.core import code_map


def _write(root, rel, text):
    path = Path(root) / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    return path


class CodeMapBuildTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = self.tmp.name
        # Walk fallback keeps the fixture independent of whether the host temp
        # directory happens to sit inside some Git repository.
        self.git_patch = mock.patch.object(code_map, "_git_files", return_value=None)
        self.git_patch.start()
        self.addCleanup(self.git_patch.stop)
        _write(self.root, "pkg/core.py", '''"""核心编排逻辑。"""

from .util import helper


def run(task):
    return helper(task)


class Engine:
    pass


# code-map: S: 不允许在这里读生产数据
''')
        _write(self.root, "pkg/util.py", '''"""通用工具。"""


def helper(value):
    return value
''')
        _write(self.root, "web/app.ts", '''// 前端入口
import { render } from "./render";

export function boot() {
  return render();
}
''')
        _write(self.root, "web/render.ts", '''export function render() {
  return null;
}
''')
        _write(self.root, "README.md", "# 项目说明\n\n正文。\n")
        _write(self.root, "node_modules/pkg/index.js", "export const ignored = 1;\n")
        (Path(self.root) / "logo.png").write_bytes(b"\x89PNG\r\n\x1a\n")

    def tearDown(self):
        self.tmp.cleanup()

    def test_build_writes_reviewable_map_and_meta(self):
        result = code_map.build(self.root)
        self.assertEqual(result["entries"], 5)
        self.assertEqual(result["source"], "walk")
        map_path = Path(result["map"])
        self.assertTrue(map_path.is_file())
        text = map_path.read_text(encoding="utf-8")
        self.assertIn("pkg/core.py[py]: F:核心编排逻辑。", text)
        self.assertIn("R:pkg/util.py", text)
        self.assertIn("A:run,Engine", text)
        self.assertIn("S:不允许在这里读生产数据", text)
        self.assertIn("web/app.ts[ts,ui]", text)
        self.assertIn("README.md[md]: F:项目说明", text)
        self.assertNotIn("node_modules", text)
        meta = json.loads((map_path.parent / code_map.META_NAME).read_text(encoding="utf-8"))
        self.assertEqual(meta["schema"], code_map.SCHEMA)
        self.assertEqual(len(meta["files"]), 5)
        self.assertTrue(meta["files"]["pkg/core.py"]["sha256"])
        self.assertEqual(meta["stats"]["by_lang"]["py"], 2)

    def test_status_reports_each_drift_kind(self):
        code_map.build(self.root)
        clean = code_map.status(self.root)
        self.assertTrue(clean["clean"])
        self.assertEqual(clean["stale"]["count"], 0)

        _write(self.root, "pkg/util.py", '"""通用工具。"""\n\n\ndef helper(value):\n    return value + 1\n')
        stale = code_map.status(self.root)
        self.assertFalse(stale["clean"])
        self.assertEqual(stale["stale"]["count"], 1)
        self.assertEqual(stale["stale"]["paths"], ["pkg/util.py"])

        _write(self.root, "pkg/new_module.py", '"""新增模块。"""\n')
        unbaselined = code_map.status(self.root)
        self.assertEqual(unbaselined["unbaselined"]["count"], 1)
        self.assertEqual(unbaselined["unbaselined"]["paths"], ["pkg/new_module.py"])

        (Path(self.root) / "README.md").unlink()
        missing = code_map.status(self.root)
        self.assertEqual(missing["missing"]["count"], 1)
        self.assertEqual(missing["missing"]["paths"], ["README.md"])

        rebuilt = code_map.build(self.root)
        self.assertEqual(rebuilt["removed"], 1)
        self.assertTrue(code_map.status(self.root)["clean"])

    def test_deep_status_ignores_timestamp_only_changes(self):
        code_map.build(self.root)
        path = Path(self.root) / "README.md"
        stat = path.stat()
        path.write_text(path.read_text(encoding="utf-8"), encoding="utf-8")
        # Force a different mtime without changing bytes, so shallow status
        # reports stale while the hash comparison proves the entry is fresh.
        import os
        os.utime(path, ns=(stat.st_atime_ns, stat.st_mtime_ns + 5_000_000_000))
        shallow = code_map.status(self.root, deep=False)
        self.assertEqual(shallow["stale"]["count"], 1)
        deep = code_map.status(self.root, deep=True)
        self.assertEqual(deep["stale"]["count"], 0)
        self.assertTrue(deep["clean"])

    def test_partial_build_keeps_other_entries(self):
        code_map.build(self.root)
        map_path, _ = code_map._paths(Path(self.root).resolve())
        before = map_path.read_text(encoding="utf-8")
        _write(self.root, "pkg/util.py", '"""通用工具。"""\n\n\ndef helper(value):\n    return value + 2\n')
        result = code_map.build(self.root, changed_paths=["pkg/util.py"])
        after = map_path.read_text(encoding="utf-8")
        self.assertEqual(result["generated"], 1)
        self.assertIn("pkg/core.py", after)
        self.assertEqual(before, after)
        self.assertTrue(code_map.status(self.root)["clean"])

    def test_read_entries_filters_by_query_and_prefix(self):
        code_map.build(self.root)
        self.assertEqual(len(code_map.read_entries(self.root, query="helper")), 1)
        self.assertEqual(len(code_map.read_entries(self.root, query="util")), 2)
        rows = code_map.read_entries(self.root, prefix="web")
        self.assertEqual(len(rows), 2)
        self.assertTrue(all(row.startswith("web/") for row in rows))
        self.assertEqual(code_map.read_entries(self.root, query="does-not-exist"), [])


class CodeMapToolTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = self.tmp.name
        self.git_patch = mock.patch.object(code_map, "_git_files", return_value=None)
        self.git_patch.start()
        self.addCleanup(self.git_patch.stop)
        _write(self.root, "auth/login.py", '"""登录鉴权。"""\n\n\ndef login(user):\n    return user\n')

    def tearDown(self):
        self.tmp.cleanup()

    def test_builtin_tool_build_status_read(self):
        from app.core import builtin_agent_tools
        out = builtin_agent_tools._exec_tool_impl(self.root, "code_map", {"action": "build"})
        self.assertIn("代码地图已更新", out)
        status = code_map.tool_result(self.root, {"action": "status"})
        self.assertIn("干净", status)
        rows = builtin_agent_tools._exec_tool_impl(
            self.root, "code_map", {"action": "read", "query": "auth"})
        self.assertIn("auth/login.py", rows)

    def test_code_map_is_a_known_auto_executed_action(self):
        from app.core import action_policy
        spec = action_policy.spec_for("code_map")
        self.assertIsNotNone(spec)
        self.assertEqual(spec.group, "write")
        self.assertEqual(
            action_policy.preflight("code_map", sandbox={})["decision"], "autoExecute")


class CodeMapPromptTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = self.tmp.name
        self.git_patch = mock.patch.object(code_map, "_git_files", return_value=None)
        self.git_patch.start()
        self.addCleanup(self.git_patch.stop)
        _write(self.root, "src/auth/login.py", '"""登录鉴权流程。"""\n\n\ndef login(user):\n    return user\n')
        _write(self.root, "src/auth/session.py", '"""会话签发。"""\n\n\ndef issue(user):\n    return user\n')
        _write(self.root, "src/billing/invoice.py", '"""发票计算。"""\n')

    def tearDown(self):
        self.tmp.cleanup()

    def test_prompt_block_ranks_task_relevant_entries(self):
        code_map.build(self.root)
        selected = code_map.select_block({"workdir": self.root, "goal": "修复登录鉴权流程"})
        self.assertGreater(selected["entries"], 0)
        self.assertIn("## 代码认知图谱", selected["block"])
        self.assertIn("src/auth/login.py", selected["block"])
        self.assertIn("missing=0", selected["block"])

    def test_prompt_block_requires_built_map_and_workdir(self):
        self.assertEqual(code_map.prompt_block({"goal": "anything"}), "")
        self.assertEqual(code_map.prompt_block({"workdir": self.root}), "")
        self.assertEqual(code_map.status(self.root)["entries"], 0)


if __name__ == "__main__":
    unittest.main()
