# -*- coding: utf-8 -*-
"""发版闸门收集缺口桥接：让纯 pytest 函数风格守测真的被跑起来。

问题（22:34 实测）
------------------
``scripts/run_protected_suite.py`` 用 ``unittest.defaultTestLoader.discover``
跑全量守测，而 ``tests/`` 里有 6 个文件是**顶层 test_* 函数、没有 TestCase 子类**：
unittest 对它们的收集数是 **0**。这 6 个里恰好包括三道最关键的棘轮——
``test_publish_selectors_registry.py``（选择器 113→16）、
``test_token_governance.py``（CSS token 治理）、
``test_quota_exhausted_classify.py``（额度分类）。也就是说它们只在
``py -3.13 -m pytest`` 里跑，**发版闸门根本没跑**。

做法
----
本文件本身是 unittest.TestCase（discover 一定收得到），动态 import 那些模块、
逐个调用零参数的顶层 test 函数，等于把 pytest 的收集行为补进标准库闸门。

诚实边界（别把「桥接」说成「全覆盖」）
--------------------------------------
- ``test_runner_claude_thinking_cap.py`` 的用例带 ``monkeypatch`` fixture，
  没有 pytest 就无法直调，单列在 ``NEEDS_FIXTURE`` 里**不假装覆盖**；
- ``test_token_governance.py::test_cross_file_duplicate_defs_zero`` 是
  ``@pytest.mark.xfail``（收敛目标，当前预期失败），桥接按 xfail 语义跳过；
- 棘轮守测：磁盘上新出现任何 pytest-only 守测文件而没进清单，本文件立刻红，
  强制接入桥接 —— 这就是「覆盖面」的自动化，不用靠人记。

dev / prod
----------
零依赖、零写盘、零网络；dev 直接
``py -3.13 -m unittest discover -s tests -p test_pytest_style_bridge.py -v``，
prod 由 ``scripts/run_protected_suite.py`` 自动收集。
"""
import ast
import importlib.util
import inspect
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
APP = ROOT / "app"
TESTS = ROOT / "tests"
for p in (str(ROOT), str(APP)):
    if p not in sys.path:
        sys.path.insert(0, p)

# 零参数、可直接调用的 pytest 风格守测。
BRIDGED = frozenset((
    "test_error_fixtures.py",
    "test_publish_selectors_registry.py",
    "test_quota_exhausted_classify.py",
    "test_token_governance.py",
    "test_wire_bridge.py",
))
# 需要 pytest fixture，桥接不了：单列标注，绝不静默丢弃。
NEEDS_FIXTURE = frozenset(("test_runner_claude_thinking_cap.py",))
# 与 @pytest.mark.xfail 语义对齐：这些函数当前预期失败，桥接不要求它们通过。
XFAIL_EQUIVALENT = {
    "test_token_governance.py": frozenset(("test_cross_file_duplicate_defs_zero",)),
}


def pytest_only_files():
    """AST 扫出「有顶层 test_ 函数且没有 unittest.TestCase 子类」的文件。"""
    out = set()
    for path in sorted(TESTS.glob("test_*.py")):
        try:
            tree = ast.parse(path.read_text(encoding="utf-8"))
        except (SyntaxError, UnicodeDecodeError):
            continue
        has_case = any(
            isinstance(node, ast.ClassDef) and any(
                (isinstance(b, ast.Attribute) and b.attr == "TestCase") or
                (isinstance(b, ast.Name) and b.id == "TestCase")
                for b in node.bases)
            for node in tree.body)
        has_func = any(isinstance(node, ast.FunctionDef) and
                       node.name.startswith("test_") for node in tree.body)
        if has_func and not has_case:
            out.add(path.name)
    return out


def _load(filename):
    """按唯一模块名加载，避免和 unittest discover 的模块名撞车。"""
    name = "_bridge_" + filename[:-3]
    spec = importlib.util.spec_from_file_location(name, str(TESTS / filename))
    if spec is None or spec.loader is None:
        raise ImportError("无法加载 %s" % filename)
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod
    spec.loader.exec_module(mod)
    return mod


def collect_functions(filename):
    """返回可直接调用的 (name, fn) 与跳过的名字。"""
    mod = _load(filename)
    skipped = XFAIL_EQUIVALENT.get(filename, frozenset())
    usable, skipped_names = [], []
    for name, fn in sorted(vars(mod).items()):
        if not name.startswith("test_") or not inspect.isfunction(fn):
            continue
        if getattr(fn, "__module__", None) != mod.__name__:
            continue  # 排除 import 进来的同名函数
        if name in skipped:
            skipped_names.append(name)
            continue
        if inspect.signature(fn).parameters:
            skipped_names.append(name)  # 需要 fixture，不假装能跑
            continue
        usable.append((name, fn))
    return usable, skipped_names


class PytestStyleBridgeTest(unittest.TestCase):

    def test_bridge_list_matches_disk(self):
        """棘轮：新增/删除 pytest-only 守测必须同步更新桥接清单。"""
        actual = pytest_only_files()
        expected = BRIDGED | NEEDS_FIXTURE
        self.assertEqual(expected, actual,
                         "发版闸门收集缺口变了：磁盘=%s 清单=%s" %
                         (sorted(actual), sorted(expected)))

    def test_every_bridged_module_really_runs(self):
        """逐条真调用；报告「真被跑过的函数数」，零函数不许绿灯。"""
        total = 0
        for filename in sorted(BRIDGED):
            usable, skipped = collect_functions(filename)
            with self.subTest(module=filename):
                self.assertTrue(usable, "%s 一个可调用守测都没有" % filename)
                for name, fn in usable:
                    with self.subTest(case="%s::%s" % (filename, name)):
                        fn()   # 断言在函数体内；抛出即红
                    total += 1
        self.assertGreaterEqual(total, 25, "桥接覆盖函数数异常：%d" % total)

    def test_xfail_equivalent_is_skipped_for_a_reason(self):
        """别把 xfail 当成通过，也别悄悄漏掉它。"""
        src = (TESTS / "test_token_governance.py").read_text(encoding="utf-8")
        self.assertIn("@_xfail", src)
        usable, skipped = collect_functions("test_token_governance.py")
        self.assertIn("test_cross_file_duplicate_defs_zero", skipped)
        self.assertTrue(all(n != "test_cross_file_duplicate_defs_zero"
                            for n, _ in usable))

    def test_fixture_module_is_honestly_unbridged(self):
        """带 monkeypatch 的守测：确认它真的需要 fixture，而不是被漏掉。"""
        tree = ast.parse((TESTS / "test_runner_claude_thinking_cap.py").read_text(
            encoding="utf-8"))
        with_args = [n.name for n in tree.body
                     if isinstance(n, ast.FunctionDef) and n.name.startswith("test_")
                     and n.args.args]
        self.assertTrue(with_args, "该文件不再需要 fixture，应移入 BRIDGED")
        self.assertNotIn("test_runner_claude_thinking_cap.py", BRIDGED)

    def test_bridge_file_itself_is_unittest_style(self):
        """自锁：本文件必须是 TestCase 风格，否则闸门又收不到。"""
        tree = ast.parse(Path(__file__).read_text(encoding="utf-8"))
        self.assertTrue(any(isinstance(n, ast.ClassDef) and any(
            isinstance(b, ast.Attribute) and b.attr == "TestCase"
            for b in n.bases) for n in tree.body))
        self.assertNotIn(Path(__file__).name, pytest_only_files())


    def test_no_pytest_environment_does_not_break_imports(self):
        """模拟纯标准库闸门：藏起 pytest 后逐个 import 6 个 pytest-only 模块。

        锁的是 ``test_runner_claude_thinking_cap.py`` 这类「直接 import pytest」的
        写法 —— 它会让 ``unittest.defaultTestLoader.discover`` 多出一个
        ``_FailedTest``，发版闸门直接红。22:53 已给该文件补缺席兜底。
        """
        import importlib.util as _ilu
        saved = sys.modules.get("pytest", "__absent__")
        sys.modules["pytest"] = None  # import pytest → ImportError，触发兜底
        try:
            for filename in sorted(BRIDGED | NEEDS_FIXTURE):
                with self.subTest(module=filename):
                    name = "_nopytest_" + filename[:-3]
                    spec = _ilu.spec_from_file_location(name, str(TESTS / filename))
                    mod = _ilu.module_from_spec(spec)
                    sys.modules[name] = mod
                    try:
                        spec.loader.exec_module(mod)  # 抛异常即红
                    finally:
                        sys.modules.pop(name, None)
        finally:
            if saved == "__absent__":
                sys.modules.pop("pytest", None)
            else:
                sys.modules["pytest"] = saved


if __name__ == "__main__":
    unittest.main()
