#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""11-09 重启前置：部署包 ↔ 仓库逐文件就绪对账（只读 / 零 HTTP / 零写入）。

背景
----
九步第 ④ 步 ``POST /api/selfupdate/restart`` 是全链唯一不可逆的生产中断动作；
重启后真正被加载的是部署包副本 ``D:/nvm/v24.19.0/node_modules/codebee/app``，
不是本仓库 ``app/``。历史上多轮「同步了但没重启」，20:44 曾做过一次一次性
体检（4 个近轮模块逐字节一致 + 272 个 py 的 3.8 编译），但那是临时内联脚本，
换机器 / 再同步一轮就查不了。本脚本把体检固化成可复跑的只读判据。

用法（都在仓库根执行）
----------------------
    py -3.13 scripts/deploy_readiness.py                     # 默认对账本机部署包
    py -3.13 scripts/deploy_readiness.py --check-py38        # 附加 3.8 只读语法编译
    py -3.13 scripts/deploy_readiness.py --json              # 机器可读结论
    py -3.13 scripts/deploy_readiness.py --repo-root <d> --dep-root <d>   # 守测 / 换机器

默认部署包根目录可用环境变量 ``CODEBEE_DEPLOY_DIR`` 覆盖。

判据（决策 18：报真被判过样本数，判不了一律 unknown 不上绿标）
--------------------------------------------------------------
- 报「真被判过 N/N 共有文件」，不报覆盖率、不拿空目录当达标；
- 两边都有但内容不一致 → ``blocked``（重启会加载旧代码）；
- 仓库有 / 部署包没有：该模块名在部署包 .py 里被引用 → ``blocked``
  （重启 ``ModuleNotFoundError``）；生产零引用 → 只提醒「刻意未上线」，
  例如 ``core/publish/serial_gate.py``（默认关、尚未接调度，**接调度前必须先同步**）；
- 部署包独有：``*.bak-*`` 历史备份只提醒；其余一律 ``unknown``（判不了该不该清理）；
- 仅 CRLF/LF 差异不影响 Python 运行，逐条列出但不判 blocked；已知白名单只有
  ``core/quality/constraints.py``（部署包 LF / 仓库 CRLF）；
- 目录不存在 / 文件读不了 / 空目录 → ``unknown`` + 人话恢复指引，绝不报 ok；
- ``blocked`` / ``unknown`` 一律 ``exit 2``，只有 ``ok`` 才 ``exit 0``。

dev / prod
----------
- dev：tmpdir 造两边目录跑守测，不需要部署包存在；
- prod：只读本地文件，**不连 8765、不写任何文件、不改调度、不重启**。
  ``--check-py38`` 用 ``py -3.8`` 只读 compile（不落 pyc）；拿不到解释器时
  报 ``unknown`` 而不是「编译通过」。
"""
from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_DEP_ROOT = Path(
    os.environ.get("CODEBEE_DEPLOY_DIR", "D:/nvm/v24.19.0/node_modules/codebee")
)

IGNORE_PARTS = frozenset(("__pycache__",))
IGNORE_SUFFIX = (".pyc", ".pyo")
BACKUP_MARK = ".bak-"
EOL_ONLY_KNOWN = frozenset(("core/quality/constraints.py",))

# 3.8 侧只读编译：compile() 不落 pyc，不 import 模块、不执行任何业务代码。
PY38_PROBE = (
    "import sys, pathlib\n"
    "files = list(pathlib.Path(sys.argv[1]).rglob('*.py'))\n"
    "bad = []\n"
    "for p in files:\n"
    "    try:\n"
    "        compile(p.read_bytes(), str(p), 'exec')\n"
    "    except (SyntaxError, ValueError) as e:\n"
    "        bad.append(str(p) + ': ' + str(e))\n"
    "print(len(files), len(bad))\n"
    "for b in bad[:10]:\n"
    "    print(b)\n"
)


def scan_tree(app_root):
    """只读扫描 app/ 下所有文件，返回 (files, unreadable)。目录不存在返回 None。"""
    root = Path(app_root)
    if not root.is_dir():
        return None
    files, unreadable = {}, []
    for p in root.rglob("*"):
        if not p.is_file() or IGNORE_PARTS.intersection(p.parts):
            continue
        if p.name.endswith(IGNORE_SUFFIX):
            continue
        rel = p.relative_to(root).as_posix()
        try:
            files[rel] = p.read_bytes()
        except OSError:
            unreadable.append(rel)
    return files, unreadable


def _eol(b):
    """行尾归一化，只用来区分「仅行尾差异」与「真内容差异」。"""
    return b.replace(b"\r\n", b"\n")


def referenced_by(stem, dep_files, exclude=None):
    """模块名是否出现在部署包 .py 里（含动态 import 字符串），宽匹配 fail-closed。"""
    needle = stem.encode("ascii", "ignore")
    if not needle:
        return []
    hits = []
    for rel, b in dep_files.items():
        if rel == exclude or not rel.endswith(".py"):
            continue
        if needle in b:
            hits.append(rel)
    return hits


def judge_pair(repo, dep, unreadable=()):
    """纯函数：两边 {相对路径: 原始字节} → 就绪结论。不碰文件系统、不联网。"""
    both = sorted(set(repo) & set(dep))
    only_repo = sorted(set(repo) - set(dep))
    only_dep = sorted(set(dep) - set(repo))

    same, eol, diff = [], [], []
    for key in both:
        a, b = repo[key], dep[key]
        if a == b:
            same.append(key)
        elif _eol(a) == _eol(b):
            eol.append(key)
        else:
            diff.append(key)

    blockers, notes, unknowns = [], [], []
    for key in diff:
        blockers.append("内容不一致：%s（仓库 %d 字节 / 部署包 %d 字节）"
                        % (key, len(repo[key]), len(dep[key])))

    for key in only_repo:
        if not key.endswith(".py"):
            notes.append("仓库有、部署包没有：%s（非 Python，按未上线文档处理）" % key)
            continue
        hits = referenced_by(Path(key).stem, dep)
        if hits:
            blockers.append("部署包缺模块且被引用：%s（%s）→ 重启会 ModuleNotFoundError"
                            % (key, hits[0]))
        else:
            notes.append("仓库有、部署包没有：%s（生产零引用，判「刻意未上线」；"
                         "接调度 / 接入口前必须先同步）" % key)

    for key in only_dep:
        if BACKUP_MARK in Path(key).name:
            notes.append("部署包独有历史备份：%s（不会被加载）" % key)
        else:
            unknowns.append("部署包独有文件：%s（判不了该不该清理，别当已对齐）" % key)

    for key in eol:
        if key in EOL_ONLY_KNOWN:
            notes.append("已知仅行尾差异：%s（部署包 LF / 仓库 CRLF，不影响运行）" % key)
        else:
            notes.append("仅行尾差异（不在已知白名单）：%s —— 不影响运行，但确认是有意的" % key)

    for key in unreadable:
        unknowns.append("文件读不了：%s" % key)
    if not both:
        unknowns.append("共有文件 0 个（两边空目录或目录指错）：0/0 不是就绪，"
                        "先确认 --repo-root / --dep-root")

    if blockers:
        verdict = "blocked"
    elif unknowns:
        verdict = "unknown"
    else:
        verdict = "ok"

    return {
        "verdict": verdict,
        "blockers": blockers,
        "notes": notes,
        "unknowns": unknowns,
        "counts": {
            "repo_files": len(repo),
            "dep_files": len(dep),
            "compared": len(both),
            "byte_equal": len(same),
            "eol_only": len(eol),
            "content_diff": len(diff),
            "only_repo": len(only_repo),
            "only_dep": len(only_dep),
            "unreadable": len(unreadable),
        },
        "lists": {
            "content_diff": diff,
            "eol_only": eol,
            "only_repo": only_repo,
            "only_dep": only_dep,
        },
    }


def py38_smoke(dep_app):
    """生产同款 3.8 只读语法编译。拿不到解释器 → unknown，绝不报通过。"""
    try:
        res = subprocess.run(
            ["py", "-3.8", "-c", PY38_PROBE, str(dep_app)],
            capture_output=True, text=True, encoding="utf-8",
            errors="replace", timeout=180,
        )
    except (OSError, subprocess.SubprocessError) as exc:
        return {"status": "unknown", "reason": "跑不了 py -3.8：%s" % exc}
    if res.returncode != 0:
        return {"status": "unknown",
                "reason": "3.8 探针自身失败（exit %d）：%s"
                          % (res.returncode, (res.stderr or "").strip()[:200])}
    lines = (res.stdout or "").strip().splitlines()
    if not lines:
        return {"status": "unknown", "reason": "3.8 探针无输出"}
    parts = lines[0].split()
    if len(parts) != 2 or not all(x.isdigit() for x in parts):
        return {"status": "unknown", "reason": "3.8 探针输出格式异常：%s" % lines[0][:120]}
    total, bad = int(parts[0]), int(parts[1])
    return {
        "status": "ok" if total > 0 and bad == 0 else ("unknown" if total == 0 else "blocked"),
        "compiled": total,
        "failed": bad,
        "detail": lines[1:11],
    }


def build_result(repo_root, dep_root, check_py38=False):
    """IO 层：定位两边 app/、扫描、判定。缺目录时给 unknown + 人话恢复指引。"""
    repo_app, dep_app = Path(repo_root) / "app", Path(dep_root) / "app"
    result = {
        "repo_root": str(repo_root),
        "dep_root": str(dep_root),
        "repo_app": str(repo_app),
        "dep_app": str(dep_app),
    }
    left, right = scan_tree(repo_app), scan_tree(dep_app)
    if left is None or right is None:
        missing = []
        if left is None:
            missing.append(str(repo_app))
        if right is None:
            missing.append(str(dep_app))
        result.update({
            "verdict": "unknown",
            "blockers": [],
            "notes": [],
            "unknowns": ["目录不存在：%s" % m for m in missing] + [
                "恢复指引：确认部署包路径（默认 D:/nvm/v24.19.0/node_modules/codebee），"
                "或用 --dep-root / CODEBEE_DEPLOY_DIR 指到含 app/ 的那一层；"
                "判不了不报就绪。"],
            "counts": {"repo_files": 0, "dep_files": 0, "compared": 0,
                       "byte_equal": 0, "eol_only": 0, "content_diff": 0,
                       "only_repo": 0, "only_dep": 0, "unreadable": 0},
            "lists": {"content_diff": [], "eol_only": [], "only_repo": [], "only_dep": []},
        })
        return result

    repo, unreadable = left
    dep, unreadable_dep = right
    result.update(judge_pair(repo, dep, unreadable + unreadable_dep))
    if check_py38:
        smoke = py38_smoke(dep_app)
        result["py38"] = smoke
        if smoke["status"] == "blocked":
            result["verdict"] = "blocked"
            result["blockers"].append("3.8 语法编译失败 %d/%d 个 py"
                                      % (smoke["failed"], smoke["compiled"]))
        elif smoke["status"] == "unknown":
            if result["verdict"] == "ok":
                result["verdict"] = "unknown"
            result["unknowns"].append("3.8 编译不可用：%s" % smoke.get("reason", "判不了"))
    return result


def print_human(d):
    c = d["counts"]
    print("部署就绪对账（只读 / 零 HTTP / 零写入）")
    print("  仓库 app/   : %s（%d 文件）" % (d["repo_app"], c["repo_files"]))
    print("  部署包 app/ : %s（%d 文件）" % (d["dep_app"], c["dep_files"]))
    print("  真被判过     : %d/%d 共有文件（逐字节 + 行尾归一化两轮）"
          % (c["compared"], c["compared"]))
    print("  逐字节一致 %d / 仅行尾差异 %d / 内容不一致 %d"
          % (c["byte_equal"], c["eol_only"], c["content_diff"]))
    print("  仓库独有 %d / 部署包独有 %d / 读不了 %d"
          % (c["only_repo"], c["only_dep"], c["unreadable"]))
    if d.get("py38"):
        s = d["py38"]
        print("  3.8 只读编译：%s（%s 个 py，失败 %s）"
              % (s["status"], s.get("compiled", "?"), s.get("failed", "?")))
    for line in d["blockers"]:
        print("  [blocked] %s" % line)
    for line in d["unknowns"]:
        print("  [unknown] %s" % line)
    for line in d["notes"]:
        print("  [提醒] %s" % line)
    verdict = d["verdict"]
    if verdict == "ok":
        print("结论：ok —— 部署包与仓库零真差异，11-09 重启前这一层就绪"
              "（不等于额度已恢复 / 不等于可以跳过九步其余判据）")
    elif verdict == "blocked":
        print("结论：blocked —— 先修上面列出的差异，别重启（重启后加载的是部署包代码）")
    else:
        print("结论：unknown —— 判不了就不上绿标；按上面指引核实后再决定要不要重启")
    return verdict


def main(argv=None):
    ap = argparse.ArgumentParser(
        description="11-09 重启前置：部署包与仓库只读就绪对账（零 HTTP / 零写入）")
    ap.add_argument("--repo-root", default=str(ROOT),
                    help="仓库根（其下需有 app/），默认脚本所在仓库")
    ap.add_argument("--dep-root", default=str(DEFAULT_DEP_ROOT),
                    help="部署包根（其下需有 app/），默认 CODEBEE_DEPLOY_DIR 或本机路径")
    ap.add_argument("--json", action="store_true", help="输出机器可读 JSON")
    ap.add_argument("--check-py38", action="store_true",
                    help="附加生产同款 3.8 只读语法编译（默认关，换机器不会假绿）")
    args = ap.parse_args(argv)

    d = build_result(args.repo_root, args.dep_root, check_py38=args.check_py38)
    if args.json:
        print(json.dumps(d, ensure_ascii=False, indent=2))
    else:
        print_human(d)
    return 0 if d["verdict"] == "ok" else 2


if __name__ == "__main__":
    sys.exit(main())
