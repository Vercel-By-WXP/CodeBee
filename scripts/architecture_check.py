# -*- coding: utf-8 -*-
"""架构基线检查器（借鉴 zai-org/ZCode 开源的 architecture-policy 体系）。

机器可执行的三道守护，防多代理并行写入时代码架构漂移：
1. **层序单向**：policy.layers 定义的层号小→大单向依赖（L_i 只准 import
   L_j, j>=i）——数据层不得反手依赖编排层。
2. **禁环**：import 图 DFS 找环；基线里的存量环豁免，新边成环即拦。
3. **行数基线**：超过 maxFileLines 且超过自己历史最高才违规——存量千行
   文件不被挡路，但「越写越长」的漂移即刻点名。

基线哲学（与 ZCode 一致）：`.architecture-baseline.json` 存 violations
快照，**存量豁免、增量零容忍**——3547 行的 pipeline.py 可以原样活着，
但谁把它推过历史最高一行就报错。

用法：
  python scripts/architecture_check.py              # 全量检查（基线豁免后）
  python scripts/architecture_check.py --changed    # 只查 git 变更文件（环仍全图）
  python scripts/architecture_check.py --update-baseline   # 把当前违规写进基线
"""
from __future__ import annotations

import ast
import json
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
POLICY_FILE = ROOT / "architecture-policy.json"
BASELINE_FILE = ROOT / ".architecture-baseline.json"


def load_policy():
    return json.loads(POLICY_FILE.read_text(encoding="utf-8"))


def load_baseline():
    if BASELINE_FILE.is_file():
        return json.loads(BASELINE_FILE.read_text(encoding="utf-8"))
    return {"version": 1, "violations": []}


def save_baseline(baseline):
    # 基线只写仓库根这个固定文件；resolve+parents 守卫防符号链接漂移
    target = BASELINE_FILE.resolve()
    if ROOT.resolve() not in target.parents:
        raise SystemExit("基线文件越界：" + str(target))
    target.write_text(json.dumps(baseline, ensure_ascii=False, indent=2) + "\n",
                      encoding="utf-8")


def module_name(py_file):
    """app/core/x.py → x；app/core/publish/manager.py → publish.manager；
    app/main.py → main。与 import 的裸名同一命名空间，图才连得起来。"""
    rel = py_file.relative_to(ROOT / "app")
    parts = list(rel.with_suffix("").parts)
    if parts and parts[0] == "core":
        parts = parts[1:]
    name = ".".join(parts)
    return name[:-9] if name.endswith(".__init__") else name


def module_deps(py_file):
    """AST 解析一个 py 文件的内部依赖（from . import x / from .x import y /
    core 层文件里的 from core import x）。"""
    name = module_name(py_file)
    deps = set()
    try:
        tree = ast.parse(py_file.read_text(encoding="utf-8", errors="replace"))
    except SyntaxError as e:
        return name, deps, [("syntax", "%s 解析失败 %s" % (name, e))]
    in_core = py_file.relative_to(ROOT).parts[:1] == ("app",) and \
        "core" in py_file.relative_to(ROOT).parts[:2]
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom) and node.level == 1:
            if node.module:
                deps.add(node.module.split(".")[0])
            else:
                for a in node.names:
                    deps.add(a.name)          # from . import x, y
        elif isinstance(node, ast.ImportFrom) and node.level == 0 and in_core:
            m = node.module or ""
            if m == "core" or m.startswith("core."):
                rest = m.split(".")[1] if "." in m else ""
                if rest:
                    deps.add(rest)
                else:
                    for a in node.names:      # from core import x, y
                        deps.add(a.name)
    return name, {d for d in deps if d != name and d not in ("core",)}, []


def build_graph(policy, only_files=None):
    """扫 checkGlobs 建 {module: set(deps)}；only_files 非空时只收这些文件。"""
    graph, problems = {}, []
    for pat in policy.get("checkGlobs", ["app/**/*.py"]):
        for py in sorted(ROOT.glob(pat)):
            if only_files is not None and str(py).replace("\\", "/") not in only_files:
                continue
            name, deps, errs = module_deps(py)
            graph[name] = deps
            problems.extend(errs)
    return graph, problems


def layer_of(policy, mod):
    for lay, mods in policy["layers"].items():
        if mod in mods:
            return int(lay)
    return 99   # 未分层模块视为最高层（只被禁，不禁人）


def check_layers(policy, graph):
    """层序单向：L_i import L_j 且 j<i 即违规。"""
    out = []
    for mod, deps in sorted(graph.items()):
        mi = layer_of(policy, mod)
        for d in sorted(deps):
            if layer_of(policy, d) < mi:
                out.append("layer: %s(L%d) -> %s(L%d) 反向依赖" %
                           (mod, mi, d, layer_of(policy, d)))
    return out


def find_cycles(graph):
    """DFS 找全部环（三色标记），返回 sorted tuple 列表（去旋转重合）。"""
    cycles, WHITE, GRAY, BLACK = set(), 0, 1, 2
    color = {m: WHITE for m in graph}

    def dfs(node, stack):
        color[node] = GRAY
        for nxt in sorted(graph.get(node, ())):
            if color.get(nxt, BLACK) == GRAY and nxt in stack:
                cyc = tuple(stack[stack.index(nxt):])
                rot = min(range(len(cyc)), key=lambda i: cyc[i:])
                cycles.add(cyc[rot:] + cyc[:rot])
            elif color.get(nxt, BLACK) == WHITE:
                dfs(nxt, stack + [nxt])
        color[node] = BLACK

    for m in sorted(graph):
        if color[m] == WHITE:
            dfs(m, [m])
    return sorted(cycles)


def check_lines(policy, baseline):
    """行数基线：> maxFileLines 且 > 基线历史最高才违规。"""
    limit = policy.get("maxFileLines", 1200)
    hist = {}
    for v in baseline.get("violations", []):
        if isinstance(v, dict) and v.get("kind") == "lines":
            hist[v.get("file")] = int(v.get("lines") or 0)
    out = []
    for pat in policy.get("checkGlobs", ["app/**/*.py"]):
        for py in sorted(ROOT.glob(pat)):
            n = len(py.read_text(encoding="utf-8", errors="replace").splitlines())
            key = str(py.relative_to(ROOT)).replace("\\", "/")
            if n > limit and n > hist.get(key, 0):
                out.append(("lines", key, n))
    return out


def violated_now(policy, graph, baseline, only_files=None):
    """当前全部违规，返回 [(kind, text, extra)]——text 与基线比对做豁免。"""
    out = []
    for c in find_cycles(graph):
        out.append(("cycle", "cycle: " + " -> ".join(c + (c[0],)), None))
    layer_graph = graph
    if only_files is not None:
        changed_modules = set()
        for name in only_files:
            path = Path(name)
            if path.is_file():
                changed_modules.add(module_name(path))
        layer_graph = {name: deps for name, deps in graph.items()
                       if name in changed_modules}
    for v in check_layers(policy, layer_graph):
        out.append(("layer", v, None))
    limit = policy.get("maxFileLines", 1200)
    hist = {}
    for v in baseline.get("violations", []):
        if isinstance(v, dict) and v.get("kind") == "lines":
            hist[v.get("file")] = int(v.get("lines") or 0)
    for pat in policy.get("checkGlobs", ["app/**/*.py"]):
        for py in sorted(ROOT.glob(pat)):
            if only_files is not None and str(py.resolve()).replace("\\", "/") not in only_files:
                continue
            n = len(py.read_text(encoding="utf-8", errors="replace").splitlines())
            key = str(py.relative_to(ROOT)).replace("\\", "/")
            if n > limit and n > hist.get(key, 0):
                out.append(("lines",
                            "lines: %s %d 行（上限 %d，历史最高 %d）"
                            % (key, n, limit, hist.get(key, 0)),
                            {"file": key, "lines": n}))
    return out


def known_texts(baseline):
    return {v.get("text") for v in baseline.get("violations", [])
            if isinstance(v, dict)}


def changed_files():
    """git 变更文件（工作区 + 暂存区，相对仓库根、正斜杠）。"""
    r = subprocess.run(["git", "-C", str(ROOT), "diff", "--name-only", "HEAD"],
                       capture_output=True)
    files = set()
    for ln in r.stdout.decode("utf-8", "replace").splitlines():
        ln = ln.strip()
        if ln.endswith(".py") and ln.startswith("app/"):
            files.add(str((ROOT / ln).resolve()).replace("\\", "/"))
    return files


def main(argv):
    policy = load_policy()
    baseline = load_baseline()
    only = None
    update = False
    for a in argv:
        if a == "--changed":
            only = changed_files()
        elif a == "--update-baseline":
            update = True
        else:
            print("未知参数 %s" % a)
            return 2
    # 环必须使用完整依赖图；--changed 只限制层序和行数检查的源文件。
    graph, problems = build_graph(policy)
    if problems:
        for p in problems:
            print("[syntax]", p)
        return 2
    now = violated_now(policy, graph, baseline, only_files=only)
    if update:
        # 生成快照时必须忽略旧基线，否则未增长的超长文件会从新基线消失，
        # 下一次检查立刻被当成新增违规。
        now = violated_now(policy, graph, {"version": 1, "violations": []})
        uniq = {}
        for k, t, e in now:
            uniq[t] = {"kind": k, "text": t, **(e or {})}
        recs = sorted(uniq.values(), key=lambda r: r["text"])
        save_baseline({"version": 1, "violations": recs})
        print("基线已更新：%d 条存量违规豁免在案" % len(recs))
        return 0
    known = known_texts(baseline)
    # lines 类生成时已用基线历史过滤（n > 历史最高才产出），天然是新违规，
    # 不走 text 匹配（text 里的历史值会随 update 变化，匹配必失效）
    fresh = sorted([t for _, t, e in now if e] +
                   [t for k, t, e in now if not e and t not in known])
    if fresh:
        print("架构检查 FAIL：%d 条新增违规（存量 %d 条已豁免）"
              % (len(fresh), len(known)))
        for v in fresh:
            print("  [FAIL] " + v)
        return 1
    print("架构检查 OK（存量豁免 %d 条，无新增违规）" % len(known))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
