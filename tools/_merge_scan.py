# -*- coding: utf-8 -*-
"""合并事故扫描 v4：
1. 相对导入/绝对 core 导入的父模块存在且被 git 跟踪；导入名 = 子模块文件 或 模块内定义。
2. pipeline 修复在位标记。
"""
import ast
import io
import os
import re
import subprocess
import sys

ROOT = r"E:\GoOut\MultiAgentOrchestration"
os.chdir(ROOT)
APP = "app"


def resolve(pkg_dotted, mod_dotted):
    """app/ 相对定位：pkg_dotted=None 表示 app 根。返回文件路径或 None。"""
    parts = (pkg_dotted.split(".") if pkg_dotted else []) + \
            (mod_dotted.split(".") if mod_dotted else [])
    p = os.path.join(APP, *parts) if parts else APP
    if os.path.isdir(p):
        f = os.path.join(p, "__init__.py")
        return f if os.path.isfile(f) else None
    f = p + ".py"
    return f if os.path.isfile(f) else None


def tracked(rel):
    return subprocess.run(["git", "ls-files", "--error-unmatch", rel.replace(os.sep, "/")],
                          capture_output=True).returncode == 0


problems, checked = [], 0
for dirpath, dirs, files in os.walk(APP):
    dirs[:] = [d for d in dirs if d != "__pycache__"]
    for f in files:
        if not f.endswith(".py"):
            continue
        full = os.path.join(dirpath, f)
        pkg = dirpath[len(APP) + 1:].replace(os.sep, ".") if dirpath != APP else ""
        try:
            tree = ast.parse(io.open(full, encoding="utf-8").read())
        except SyntaxError as e:
            problems.append("%s: SyntaxError %s" % (full, e))
            continue
        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom):
                if not node.level and not (node.module or "").startswith("core"):
                    continue  # 标准库/第三方绝对导入不归本包管
                if node.level:  # 相对导入：level=1 即当前包
                    base = pkg.split(".") if pkg else []
                    for _ in range(node.level - 1):
                        base = base[:-1]
                    pkg_dotted = ".".join(base)
                    mod = node.module
                else:
                    mod = node.module  # 形如 core.xxx
                    pkg_dotted = ""
                mf = resolve(pkg_dotted, mod)
                label = ("." * node.level) + (mod or "")
                if mf is None:
                    problems.append("%s: from %s import ... -> 父模块缺文件 (%s.%s)"
                                    % (full, label, pkg_dotted, mod))
                    continue
                if not tracked(mf):
                    problems.append("%s: from %s import ... -> 父模块未提交: %s"
                                    % (full, label, mf))
                    continue
                msrc = io.open(mf, encoding="utf-8").read() if os.path.isfile(mf) else ""
                base_pkg = (pkg_dotted + "." + mod).strip(".") if mod else pkg_dotted
                for a in node.names:
                    if a.name == "*":
                        continue
                    checked += 1
                    cand = resolve(base_pkg, a.name)
                    if cand:
                        if not tracked(cand):
                            problems.append("%s: from %s import %s -> 子模块未提交: %s"
                                            % (full, label, a.name, cand))
                        continue
                    if not re.search(r"^(def |class )%s\b|^%s\s*=" % (a.name, a.name),
                                     msrc, re.M):
                        problems.append("%s: from %s import %s -> 无此定义/子模块"
                                        % (full, label, a.name))
            elif isinstance(node, ast.Import):
                for a in node.names:
                    if not (a.name == "core" or a.name.startswith("core.")):
                        continue
                    checked += 1
                    mf = resolve("", a.name)
                    if mf is None:
                        problems.append("%s: import %s -> 缺文件" % (full, a.name))
                    elif not tracked(mf):
                        problems.append("%s: import %s -> 未提交: %s" % (full, a.name, mf))

print("import 完整性（检查 %d 个名字绑定）:" % checked,
      "OK" if not problems else "PROBLEMS(%d)" % len(problems))
for x in problems[:20]:
    print("  ", x)

src = io.open(os.path.join(APP, "core", "pipeline.py"), encoding="utf-8").read()
markers = {
    "多评审并发（b1dc44e）": ("ThreadPool" in src or "并行" in src),
    "知识库注入（0.1.18）": ("knowledge" in src),
    "死链闸门收窄（0.1.7）": ("binding_configured" in src),
    "竞品证据锚定/写前自查（0.1.17）": ("证据" in src or "evidence" in src.lower()),
}
print("pipeline 修复在位:")
bad = False
for k, v in markers.items():
    print("   ", "OK " if v else "MISS", k)
    bad = bad or not v
sys.exit(1 if (problems or bad) else 0)
