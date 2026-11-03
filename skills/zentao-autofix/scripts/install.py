#!/usr/bin/env python3
"""把 zentao-autofix 装到各 AI 平台，并把已有的错误规则注入项目规则文件。

用法：
    python scripts/install.py --target claude --project .
    python scripts/install.py --target all
    python scripts/install.py --target codex --global

平台目录约定（各家自己的规范，技能本体只有一份）：
    claude    <proj>/.claude/skills/zentao-autofix/
    codex     <proj>/.codex/skills/zentao-autofix/   + AGENTS.md
    codebee   <proj>/.codebee/skills/zentao-autofix/ + AGENTS.md
    opencode  <proj>/.opencode/skill/zentao-autofix/ + AGENTS.md
"""

from __future__ import annotations

import argparse
import os
import shutil
import sys

SRC = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
COPY_ITEMS = ["SKILL.md", "README.md", "DESIGN.md", "zfix.py", "zfix", "recipes", "knowledge", "examples"]
SKIP_DIRS = {"__pycache__", ".pytest_cache", ".zfix"}

PROJECT_TARGETS = {
    "claude": os.path.join(".claude", "skills", "zentao-autofix"),
    "codex": os.path.join(".codex", "skills", "zentao-autofix"),
    "codebee": os.path.join(".codebee", "skills", "zentao-autofix"),
    "opencode": os.path.join(".opencode", "skill", "zentao-autofix"),
}
GLOBAL_TARGETS = {
    "claude": os.path.join("~", ".claude", "skills", "zentao-autofix"),
    "codex": os.path.join("~", ".codex", "skills", "zentao-autofix"),
    "codebee": os.path.join("~", ".codebee", "skills", "zentao-autofix"),
    "opencode": os.path.join("~", ".config", "opencode", "skill", "zentao-autofix"),
}
# 注入规则时用的平台名（交给 zfix.guard.preflight 决定写哪些文件）
RULE_PLATFORM = {"claude": "claude", "codex": "codex", "codebee": "codebee", "opencode": "opencode"}


def launcher_command(dest: str) -> str:
    """生成一条在本机一定跑得通的调用命令。"""
    target = os.path.join(dest, "zfix.py").replace("\\", "/")
    if os.name == "nt":
        return f'py -3 "{target}"'
    return f'python3 "{target}"'


def copytree(src: str, dst: str) -> int:
    count = 0
    for root, dirs, files in os.walk(src):
        dirs[:] = [d for d in dirs if d not in SKIP_DIRS]
        rel = os.path.relpath(root, src)
        out_dir = dst if rel == "." else os.path.join(dst, rel)
        os.makedirs(out_dir, exist_ok=True)
        for name in files:
            if name.endswith((".pyc", ".pyo")):
                continue
            shutil.copy2(os.path.join(root, name), os.path.join(out_dir, name))
            count += 1
    return count


def install_one(platform: str, project: str, use_global: bool, dry_run: bool) -> str:
    dest_rel = (GLOBAL_TARGETS if use_global else PROJECT_TARGETS)[platform]
    dest = os.path.expanduser(dest_rel) if use_global else os.path.join(project, dest_rel)
    dest = os.path.abspath(dest)
    if dry_run:
        print(f"  [dry-run] {platform:<9} → {dest}")
        return dest
    os.makedirs(dest, exist_ok=True)
    copied = 0
    for item in COPY_ITEMS:
        src = os.path.join(SRC, item)
        if not os.path.exists(src):
            continue
        target = os.path.join(dest, item)
        if os.path.isdir(src):
            copied += copytree(src, target)
        else:
            os.makedirs(os.path.dirname(target), exist_ok=True)
            shutil.copy2(src, target)
            copied += 1
    # SKILL.md 里的命令占位符换成本机真实路径，AI 才能直接执行
    skill_path = os.path.join(dest, "SKILL.md")
    if os.path.isfile(skill_path):
        with open(skill_path, "r", encoding="utf-8") as fh:
            text = fh.read()
        with open(skill_path, "w", encoding="utf-8", newline="\n") as fh:
            fh.write(text.replace("{{ZFIX}}", launcher_command(dest)))
    write_shims(dest)
    print(f"  [安装] {platform:<9} → {dest}  （{copied} 个文件）")
    return dest


def write_shims(dest: str) -> None:
    """写两个启动脚本，装上 PATH 之后可以直接敲 zfix。"""
    py = os.path.join(dest, "zfix.py").replace("\\", "/")
    with open(os.path.join(dest, "zfix.cmd"), "w", encoding="utf-8", newline="\r\n") as fh:
        fh.write(f'@echo off\r\npy -3 "{py}" %*\r\n')
    sh = os.path.join(dest, "zfix.sh")
    with open(sh, "w", encoding="utf-8", newline="\n") as fh:
        fh.write(f'#!/bin/sh\nexec python3 "{py}" "$@"\n')
    try:
        os.chmod(sh, 0o755)
    except OSError:
        pass


def inject_rules(project: str, platforms: list, dry_run: bool) -> None:
    sys.path.insert(0, SRC)
    from zfix.config import Config
    from zfix.guard import preflight
    from zfix.knowledge import KnowledgeBase

    cfg = Config.load(project)
    if dry_run:
        print("  [dry-run] 规则注入：AGENTS.md / CLAUDE.md")
        return
    result = preflight(cfg, KnowledgeBase(cfg), dry_run=False, platforms=platforms)
    for item in result["files"]:
        state = "已更新" if item["changed"] else "无变化"
        print(f"  [规则] {state} {item['path']}（{result['rules']} 条）")


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="安装 zentao-autofix 到 AI 编程平台")
    parser.add_argument("--target", default="all", choices=["all", "claude", "codex", "codebee", "opencode"])
    parser.add_argument("--project", default=".", help="项目根目录（默认当前目录）")
    parser.add_argument("--global", dest="use_global", action="store_true", help="装到用户级目录而非项目内")
    parser.add_argument("--no-rules", action="store_true", help="只装技能，不注入规则")
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args(argv)

    project = os.path.abspath(args.project)
    platforms = list(PROJECT_TARGETS) if args.target == "all" else [args.target]
    print(f"zentao-autofix → {project}{'（用户级）' if args.use_global else ''}")
    for platform in platforms:
        install_one(platform, project, args.use_global, args.dry_run)
    if not args.no_rules:
        inject_rules(project, [RULE_PLATFORM[p] for p in platforms], args.dry_run)
    if not args.dry_run:
        print("\n下一步：")
        print(f"  1) cd \"{project}\" && py -3 zfix.py init        # 生成 .zfix/config.toml")
        print("  2) 设置 ZENTAO_ACCOUNT / ZENTAO_PASSWORD")
        print("  3) py -3 zfix.py doctor")
    return 0


if __name__ == "__main__":
    sys.exit(main())
