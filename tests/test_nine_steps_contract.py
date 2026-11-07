# -*- coding: utf-8 -*-
"""11-09 九步命令块契约守测：交接单是唯一执行真源，必须与代码逐字对齐。

为什么需要这一轮（19:5x 只读审计）
--------------------------------
九步命令块在 14:00 / 14:22 / 14:37 / 15:30 / 15:58 被手工改过 5 次，
旧守测只锁其中几个具体 flag（`--as-recovery` / `--expect-task`），
没有任何人校验「整块命令」是否仍被脚本认、路由是否真存在、顺序是否对。

本轮静态对账量出两件事：
1. 参数 / 顺序 / 安全标志**零漂移**（12 个 flag 全部对得上，10 条命令形状正确）；
2. 命令块里出现 `$RID` / `$TID`，**块内零赋值**，而块头写「逐条复制」——
   人照抄 ①～⑧ 后执行 ⑨，shell 会把两个变量展开成空串（实测 `RID=[] TID=[]`），
   `--verify-run ""` 直接 `unknown` / `exit 2`。虽 fail-closed 不假绿，
   但 11-09 当天会卡在终点；正文其实写了「整行复制 ⑧ 打印的 ⑨ 命令」，
   与块头自相矛盾。修法见交接单 19:5x 段：⑨ 命令行逐字不动，只补来源说明。

守测全部从**落盘原文**取证（交接单 / 脚本 AST / main.py AST），
零夹具、零合成样本、零 HTTP、零写入；也禁止自己变成「真跑九步」的入口。
"""
import ast
import pathlib
import re
import shlex
import unittest

ROOT = pathlib.Path(__file__).resolve().parents[1]
HANDOVER = ROOT / "迭代计划.md"
SCRIPT = ROOT / "scripts" / "quota_recovery.py"
MAIN = ROOT / "app" / "main.py"

ANCHOR = "11-09 额度恢复后的一键顺序"
CIRCLED = "①②③④⑤⑥⑦⑧⑨"
ACTIONS = ("--check", "--check-inherit", "--resume-tasks",
           "--retry-one", "--verify-run")
WINDOW_BYPASS = ("--recovery-at", "--base", "--task-id")


def _strip_quote(line):
    """交接单命令块每行带 `> ` 引用前缀。"""
    if line.startswith("> "):
        return line[2:]
    if line.startswith(">"):
        return line[1:].lstrip()
    return line


def _load():
    lines = HANDOVER.read_text(encoding="utf-8").splitlines()
    anchors = [i for i, l in enumerate(lines) if ANCHOR in l]
    if len(anchors) != 1:
        raise AssertionError("九步命令块锚点必须唯一，实得 %d" % len(anchors))
    head = _strip_quote(lines[anchors[0]])
    b = next(i for i in range(anchors[0], anchors[0] + 20)
             if _strip_quote(lines[i]).strip().startswith("```bash"))
    e = next(i for i in range(b + 1, anchors[0] + 40)
             if _strip_quote(lines[i]).strip().startswith("```"))
    raw = [_strip_quote(lines[i]) for i in range(b + 1, e)]
    commands = []
    for line in raw:
        s = line.strip()
        if not (s.startswith("py ") or s.startswith("curl ")):
            continue
        commands.append({
            "line": line,
            "argv": shlex.split(line, comments=True),
            "mark": (re.findall(r"[①②③④⑤⑥⑦⑧⑨⓪]", line) or [None])[0],
        })
    return {"head": head, "block": "\n".join(raw),
            "raw": raw, "commands": commands, "anchors": len(anchors),
            "open_line": b + 1, "close_line": e + 1}


def _argparse():
    """AST 读脚本参数全集，绝不 import / 执行脚本（执行会触发真实 HTTP）。"""
    tree = ast.parse(SCRIPT.read_text(encoding="utf-8"))
    out = {}
    for n in ast.walk(tree):
        if (isinstance(n, ast.Call) and isinstance(n.func, ast.Attribute)
                and n.func.attr == "add_argument" and n.args
                and isinstance(n.args[0], ast.Constant)):
            action = None
            for kw in n.keywords:
                if kw.arg == "action" and isinstance(kw.value, ast.Constant):
                    action = kw.value.value
            out[str(n.args[0].value)] = action
    return out


def _routes():
    """main.py 是 _route_get / _route_post 两个分派函数，不是 do_GET/do_POST。"""
    src = MAIN.read_text(encoding="utf-8")
    tree = ast.parse(src)
    spans = {}
    for n in ast.walk(tree):
        if (isinstance(n, ast.FunctionDef)
                and n.name in ("_route_get", "_route_post")):
            spans[n.name] = (n.lineno, n.end_lineno or n.lineno)
    out = {}
    for i, line in enumerate(src.splitlines(), 1):
        m = re.search(r"""path\s*==\s*["']([^"']+)["']""", line)
        if not m:
            continue
        for name, (lo, hi) in spans.items():
            if lo <= i <= hi:
                out.setdefault(m.group(1), set()).add(name)
    return out


def _shape(argv):
    if argv[0] == "py":
        return "py " + " ".join(x for x in argv[3:] if x.startswith("--"))
    method = argv[argv.index("-X") + 1] if "-X" in argv else "GET"
    url = next(x for x in argv if x.startswith("http://"))
    return "curl " + method + " " + url.rsplit("8765", 1)[1]


EXPECTED_SHAPES = [
    "py --check",
    "py --resume-tasks --dry-run --confirm-quota-restored",
    "py --resume-tasks --confirm-quota-restored",
    "curl POST /api/selfupdate/restart",
    "curl GET /api/state",
    "py --check",
    "py --check-inherit",
    "py --retry-one --dry-run --confirm-quota-restored",
    "py --retry-one --confirm-quota-restored",
    "py --verify-run --as-recovery --expect-task",
]


class NineStepsContract(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.d = _load()
        cls.flags = _argparse()
        cls.routes = _routes()
        cls.value_flags = {k for k, v in cls.flags.items()
                           if v not in ("store_true", "store_false", "count")}

    def test_01_block_shape(self):
        """锚点唯一、代码块闭合、恰好 10 条可执行命令。"""
        self.assertEqual(1, self.d["anchors"])
        self.assertEqual(10, len(self.d["commands"]))
        self.assertLess(self.d["open_line"], self.d["close_line"])
        self.assertFalse(
            any(l.strip().startswith("```") for l in self.d["raw"]),
            "代码块内不应残留未闭合的围栏")

    def test_02_step_order(self):
        """编号序列 ①..⑨；第 5 条是重启后的 /api/state 探活、无编号。"""
        marks = [c["mark"] for c in self.d["commands"]]
        self.assertEqual(list(CIRCLED[:4]) + [None] + list(CIRCLED[4:]),
                         marks)

    def test_03_scripts_exist(self):
        for c in self.d["commands"]:
            if c["argv"][0] == "py":
                self.assertTrue((ROOT / c["argv"][2]).is_file(),
                                c["argv"][2])

    def test_04_every_flag_known_to_argparse(self):
        """命令块里每个 --flag 必须真是脚本认的，手敲拼错当天才不会卡住。"""
        self.assertEqual(12, len(self.flags))
        for c in self.d["commands"]:
            if c["argv"][0] != "py":
                continue
            unknown = [x for x in c["argv"][3:]
                       if x.startswith("--") and x not in self.flags]
            self.assertEqual([], unknown, c["line"])

    def test_05_value_flags_have_values(self):
        for c in self.d["commands"]:
            a = c["argv"]
            if a[0] != "py":
                continue
            for i, x in enumerate(a[3:], 3):
                if x in self.value_flags:
                    self.assertLess(i + 1, len(a), c["line"])
                    self.assertFalse(a[i + 1].startswith("--"), c["line"])

    def test_06_exactly_one_action_each(self):
        for c in self.d["commands"]:
            if c["argv"][0] != "py":
                continue
            acts = [x for x in c["argv"] if x in ACTIONS]
            self.assertEqual(1, len(acts), c["line"])

    def test_07_command_shapes(self):
        """十步形状逐条冻结：顺序、dry-run、确认标志都不能漂。"""
        shapes = [_shape(c["argv"]) for c in self.d["commands"]]
        self.assertEqual(EXPECTED_SHAPES, shapes)

    def test_08_curl_routes_and_methods(self):
        """④ 重启 POST 真在 _route_post；探活 GET 真在 _route_get。"""
        self.assertIn("_route_post", self.routes["/api/selfupdate/restart"])
        self.assertIn("_route_get", self.routes["/api/state"])
        curl = [c for c in self.d["commands"] if c["argv"][0] == "curl"]
        self.assertEqual(2, len(curl))
        for c in curl:
            a = c["argv"]
            method = a[a.index("-X") + 1] if "-X" in a else "GET"
            url = next(x for x in a if x.startswith("http://"))
            path = "/" + url.split("8765/", 1)[1]
            expected = "_route_post" if method == "POST" else "_route_get"
            self.assertIn(expected, self.routes.get(path, set()), path)

    def test_09_confirm_flags_only_on_real_writes(self):
        lines = [c["line"] for c in self.d["commands"]]
        # ③ / ⑧ 是真写，必须带人工确认标志、绝不能带 dry-run
        for i in (2, 8):
            self.assertIn("--confirm-quota-restored", lines[i])
            self.assertNotIn("--dry-run", lines[i])
        # ①⑤⑥⑨ 是只读，绝不能带确认标志（防把只读步骤误当放行）
        for i in (0, 5, 6, 9):
            self.assertNotIn("--confirm-quota-restored", lines[i])

    def test_10_dry_run_precedes_real_writes(self):
        shapes = [_shape(c["argv"]) for c in self.d["commands"]]
        # ② 与 ③、⑦ 与 ⑧ 必须只差一个 --dry-run，且预演紧邻在真写之前
        self.assertEqual(shapes[1].replace(" --dry-run", ""), shapes[2])
        self.assertEqual(shapes[7].replace(" --dry-run", ""), shapes[8])
        self.assertIn("--dry-run", shapes[1])
        self.assertIn("--dry-run", shapes[7])

    def test_11_verify_identity_and_placeholders(self):
        a = self.d["commands"][9]["argv"]
        self.assertIn("--as-recovery", a)
        self.assertIn("--expect-task", a)
        self.assertEqual("$RID", a[a.index("--verify-run") + 1])
        self.assertEqual("$TID", a[a.index("--expect-task") + 1])
        # 不许把历史 run / task id 硬编码进命令块（贴错就是验收假绿）
        self.assertIsNone(re.search(r"\b[rt]-20\d{6}-", self.d["block"]))

    def test_12_no_window_bypass(self):
        for flag in WINDOW_BYPASS:
            self.assertNotIn(flag, self.d["block"], flag)

    def test_13_shell_vars_have_source_contract(self):
        """核心：块里出现 $RID/$TID 却没有赋值时，必须写明来源。

        合法形态 A：块内显式 `RID=...` / `TID=...`（真值或明确占位）；
        合法形态 B：块内注释明写「别直接复制 + 整行复制 ⑧ 打印的命令」。
        两者都没有 = 人照抄 ⑨ 必然拿到空串，红。
        """
        block = self.d["block"]
        self.assertIn("$RID", block)
        self.assertIn("$TID", block)
        assigned = (re.search(r"\bRID\s*=", block)
                    and re.search(r"\bTID\s*=", block))
        note = ("别直接复制" in block
                and "整行复制 ⑧ 打印" in block
                and "没有赋值" in block)
        self.assertTrue(bool(assigned) or note,
                        "$RID/$TID 既无赋值也无来源说明，⑨ 会拿空串")

    def test_14_header_exempts_step_nine(self):
        """块头不能再写无条件「逐条复制」——⑨ 与 ①～⑧ 不是同一种复制。"""
        head = self.d["head"]
        if not re.search(r"\bRID\s*=", self.d["block"]):
            self.assertIn("①～⑧ 逐条复制", head)
            self.assertIn("⑨ 整行复制 ⑧ 打印的命令", head)
        self.assertNotIn("逐条复制，别手敲", head)

    def test_15_this_test_never_runs_the_nine_steps(self):
        """守测只读：不许 subprocess / 网络 / 动态执行（防未来误烧额度）。"""
        src = pathlib.Path(__file__).read_text(encoding="utf-8")
        tree = ast.parse(src)
        imported = set()
        for n in ast.walk(tree):
            if isinstance(n, ast.Import):
                imported.update(a.name for a in n.names)
            elif isinstance(n, ast.ImportFrom) and n.module:
                imported.add(n.module)
        for mod in imported:
            self.assertFalse(mod == "subprocess"
                             or mod.startswith("urllib")
                             or mod.startswith("socket")
                             or mod == "requests", mod)
        forbidden_calls = []
        for n in ast.walk(tree):
            if isinstance(n, ast.Call) and isinstance(n.func, ast.Name):
                if n.func.id in ("eval", "exec", "system"):
                    forbidden_calls.append(n.func.id)
        self.assertEqual([], forbidden_calls)

    def test_16_handover_keeps_crlf(self):
        """交接单是 CRLF 共享文件，别被 autocrlf 整文件翻成 LF。"""
        b = HANDOVER.read_bytes()
        crlf = b.count(b"\r\n")
        bare = b.count(b"\n") - crlf
        self.assertGreater(crlf, 3000)
        self.assertEqual(0, bare)


if __name__ == "__main__":
    unittest.main()
