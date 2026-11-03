"""zfix 命令行入口。所有平台共用这一个入口。"""

from __future__ import annotations

import argparse
import os
import sys
from typing import Any, Dict, List, Optional

from . import __version__, util
from .adapters import SourceError, create
from .config import Config, register, resolve_project
from .context import build as build_context, save as save_context
from .guard import guard as run_guard, preflight as run_preflight, render_guard
from .knowledge import KnowledgeBase
from .locate import Locator
from .models import ROOT_CAUSES, Bug, FixRecord
from .triage import cause_label, triage as run_triage
from .verify import resolve_commands, run as run_verify, save as save_verify

GLOBAL_ARGS = ("root", "project", "json_out")


# ---------------------------------------------------------------- 基础


def load_cfg(args: argparse.Namespace) -> Config:
    root = getattr(args, "root", None)
    if not root:
        name = getattr(args, "project", None)
        if name:
            resolved = resolve_project(name)
            if not resolved:
                raise SystemExit(f"未注册的项目：{name}（先在该项目目录执行 zfix init，或传 --root）")
            root = resolved
    return Config.load(os.getcwd(), explicit_root=root)


def kb_for(cfg: Config) -> KnowledgeBase:
    return KnowledgeBase(cfg)


def load_bug(cfg: Config, bug_id: str) -> Bug:
    data = util.read_json(cfg.bug_path(bug_id), None)
    if not data:
        raise SystemExit(f"本地没有 bug #{bug_id}，先跑 `zfix sync`（或 `zfix list` 确认编号）")
    return Bug.from_dict(data)


def load_all(cfg: Config) -> List[Bug]:
    folder = cfg.bugs_dir()
    out: List[Bug] = []
    for name in sorted(os.listdir(folder)):
        if not name.endswith(".json"):
            continue
        data = util.read_json(os.path.join(folder, name), None)
        if isinstance(data, dict) and data.get("id"):
            out.append(Bug.from_dict(data))
    return out


def save_bugs(cfg: Config, bugs: List[Bug]) -> int:
    for bug in bugs:
        util.write_json(cfg.bug_path(bug.id), bug.to_dict())
    return len(bugs)


def emit(payload: Any, as_json: bool, text: str = "") -> None:
    if as_json:
        import json

        print(json.dumps(payload, ensure_ascii=False, indent=2))
    elif text:
        print(text)
    elif isinstance(payload, str):
        print(payload)


# ---------------------------------------------------------------- 子命令


def cmd_init(args: argparse.Namespace) -> int:
    root = os.path.abspath(args.root or os.getcwd())
    path = os.path.join(root, ".zfix", "config.toml")
    name = args.name or os.path.basename(root.rstrip("\\/")) or "default"
    if os.path.isfile(path) and not args.force:
        print(f"配置已存在：{path}（要覆盖加 --force）")
    else:
        util.write_text(path, _template(name, args.stack, args.url, args.product))
        print(f"已生成配置：{path}")
    util.ensure_dir(os.path.join(root, ".zfix", "inbox"))
    util.ensure_dir(os.path.join(root, ".zfix", "bugs"))
    register(root, name)
    print(f"已注册项目：{name} → {root}")
    print("\n下一步：")
    print("  1) 编辑 .zfix/config.toml 填禅道地址与产品；")
    print("  2) 设置环境变量 ZENTAO_ACCOUNT / ZENTAO_PASSWORD（或 ZENTAO_TOKEN）；")
    print("  3) py -3 -m zfix doctor && py -3 -m zfix sync")
    return 0


def _template(name: str, stack: str, url: str, product: str) -> str:
    return f"""# zfix 项目配置 · {name}
# 由 `zfix init` 生成。所有字段都有默认值，只改需要的。

[project]
name = "{name}"
repo = "."
branch_prefix = "fix/"

[source]
kind = "zentao"          # zentao | local
version = "v1"           # v1 = 禅道 16.x+ REST；legacy = 旧版 Cookie 模式
url = "{url}"
products = []
default_product = "{product}"
status = "active"        # active | resolved | closed | all
limit = 20
# account_env = "ZENTAO_ACCOUNT"
# password_env = "ZENTAO_PASSWORD"
# token_env = "ZENTAO_TOKEN"
# insecure = true        # 内网自签证书时打开
# local_dir = ".zfix/inbox"   # kind=local 时的收件目录

[stack]
lang = "{stack}"         # python | node | java | go | generic
# recipe = "python-pytest"
test = ""                # 留空则按配方自动识别
lint = ""
typecheck = ""
verify_timeout = 900

[fix]
write_scope = ["src/"]
forbidden = [".zfix/", "package-lock.json", "poetry.lock"]
max_files = 12
auto_branch = true
comment_on_zentao = false

[guard]
block = false
max_rules = 20
"""


def cmd_doctor(args: argparse.Namespace) -> int:
    cfg = load_cfg(args)
    kb = kb_for(cfg)
    checks: List[Dict[str, Any]] = []

    checks.append({"name": "python", "ok": sys.version_info >= (3, 8), "detail": sys.version.split()[0]})
    checks.append({"name": "git", "ok": util.has_bin("git"), "detail": "已安装" if util.has_bin("git") else "未找到"})
    checks.append({"name": "ripgrep", "ok": util.has_bin("rg"), "detail": "已安装（定位更快）" if util.has_bin("rg") else "未安装，回退内置搜索（较慢）"})
    checks.append({"name": "config", "ok": cfg.exists, "detail": cfg.path or "缺 .zfix/config.toml"})
    is_repo = util.git("rev-parse --is-inside-work-tree", cwd=cfg.repo)["ok"]
    checks.append({"name": "git repo", "ok": is_repo, "detail": cfg.repo})

    problems = cfg.source_ready()
    checks.append({"name": "source", "ok": not problems, "detail": "；".join(problems) or str(cfg.get("source.kind"))})

    if not problems:
        try:
            health = create(cfg).health()
        except SourceError as exc:
            health = {"ok": False, "error": str(exc)}
        checks.append({"name": "zentao", "ok": bool(health.get("ok")), "detail": health})
    commands = resolve_commands(cfg)
    checks.append({"name": "verify cmds", "ok": bool(commands), "detail": commands or "未识别技术栈"})
    checks.append({"name": "knowledge", "ok": True, "detail": f"{len(kb.patterns)} 条模式 / {len(kb.fixes())} 条修复档案"})

    lines = [f"zfix {__version__} · 自检（项目 {cfg.name}）", ""]
    for check in checks:
        mark = "OK  " if check["ok"] else "FAIL"
        detail = check["detail"] if isinstance(check["detail"], str) else _compact(check["detail"])
        lines.append(f"  [{mark}] {check['name']:<12} {detail}")
    bad = [c for c in checks if not c["ok"]]
    lines += ["", f"{len(checks) - len(bad)}/{len(checks)} 通过" + ("" if not bad else f"，失败项：{', '.join(c['name'] for c in bad)}")]
    emit({"ok": not bad, "checks": checks}, args.json_out, "\n".join(lines))
    return 0 if not bad else 1


def _compact(data: Any, limit: int = 200) -> str:
    import json

    return json.dumps(data, ensure_ascii=False)[:limit]


def cmd_sync(args: argparse.Namespace) -> int:
    cfg = load_cfg(args)
    source = create(cfg)
    bugs = source.fetch(status=args.status, product=args.product, limit=args.limit)
    saved = save_bugs(cfg, bugs)
    lines = [f"同步完成：拉取 {len(bugs)} 条，落盘 {saved} 条 → {cfg.bugs_dir()}"]
    for bug in bugs[:20]:
        lines.append(f"  #{bug.id:<8} P{bug.pri}/{bug.severity} {bug.status:<10} {bug.title[:60]}")
    if len(bugs) > 20:
        lines.append(f"  …… 另有 {len(bugs) - 20} 条")
    emit({"fetched": len(bugs), "saved": saved, "bugs": [b.to_dict() for b in bugs]}, args.json_out, "\n".join(lines))
    return 0


def cmd_list(args: argparse.Namespace) -> int:
    cfg = load_cfg(args)
    bugs = load_all(cfg)
    if args.status:
        bugs = [b for b in bugs if b.status == args.status]
    if args.decision:
        bugs = [b for b in bugs if b.decision == args.decision]
    bugs.sort(key=lambda b: (b.severity_rank, b.pri, b.id))
    if not bugs:
        emit([], args.json_out, "本地无 bug 记录，先跑 `zfix sync`")
        return 0
    lines = [f"{'ID':<9}{'P':<3}{'状态':<11}{'分诊':<8}{'根因':<14}标题"]
    for bug in bugs:
        lines.append(
            f"{bug.id:<9}{bug.pri:<3}{bug.status[:10]:<11}{bug.decision or '-':<8}"
            f"{bug.root_cause or '-':<14}{bug.title[:52]}"
        )
    emit({"count": len(bugs), "bugs": [b.to_dict() for b in bugs]}, args.json_out, "\n".join(lines))
    return 0


def cmd_triage(args: argparse.Namespace) -> int:
    cfg = load_cfg(args)
    kb = kb_for(cfg)
    bugs = load_all(cfg)
    if not bugs:
        raise SystemExit("本地无 bug，先 `zfix sync`")
    known = {r.fingerprint for r in kb.fixes() if r.fingerprint}
    stats = run_triage(bugs, known)
    save_bugs(cfg, bugs)
    lines = [
        f"分诊完成：{stats['total']} 条 → auto {stats['auto']} / manual {stats['manual']} / skip {stats['skip']}"
        f"（其中重复 {stats['duplicates']}）",
        "",
    ]
    by_cause: Dict[str, int] = stats["by_cause"]  # type: ignore[assignment]
    if by_cause:
        lines.append("根因分布：" + "，".join(f"{cause_label(k)} {v}" for k, v in sorted(by_cause.items(), key=lambda kv: -kv[1])))
        lines.append("")
    for bug in sorted(bugs, key=lambda b: (b.decision != "auto", b.severity_rank)):
        flag = {"auto": "[可自动]", "manual": "[需人工]", "skip": "[跳过]"}.get(bug.decision, "[-]")
        lines.append(f"{flag} #{bug.id} {bug.title[:48]}")
        lines.append(f"        指纹 {bug.fingerprint} · 根因 {cause_label(bug.root_cause)} · {bug.triage_note}")
    emit({"stats": stats, "bugs": [b.to_dict() for b in bugs]}, args.json_out, "\n".join(lines))
    return 0


def cmd_context(args: argparse.Namespace) -> int:
    cfg = load_cfg(args)
    bug = load_bug(cfg, args.bug)
    kb = kb_for(cfg)
    locations = Locator(cfg.repo).locate(bug, limit=args.limit)
    content = build_context(cfg, bug, kb, locations)
    path = save_context(cfg, bug, content)
    if args.print_out:
        emit(content, args.json_out, content)
    else:
        emit({"path": path, "bug": bug.to_dict()}, args.json_out, f"上下文包已生成：{path}")
        if not args.json_out:
            print(f"（候选位置 {len(locations)} 处；把上面这个路径交给 AI，让它先读完再动手）")
    return 0


def cmd_locate(args: argparse.Namespace) -> int:
    cfg = load_cfg(args)
    bug = load_bug(cfg, args.bug)
    locations = Locator(cfg.repo).locate(bug, limit=args.limit)
    if args.json_out:
        emit({"bug": bug.id, "locations": [loc.to_dict() for loc in locations]}, True)
        return 0
    if not locations:
        print("未定位到候选文件。检查：1) 是否在正确仓库；2) 描述是否有可搜索的符号。")
        return 1
    lines = [f"#{bug.id} 候选位置（{len(locations)} 处，按可疑度）", ""]
    for idx, loc in enumerate(locations, 1):
        lines.append(f"{idx:>2}. {loc.path}" + (f":{loc.line}" if loc.line else "") + f"  score={loc.score}")
        lines.append(f"    {'；'.join(loc.reasons)}")
    emit({"locations": [loc.to_dict() for loc in locations]}, False, "\n".join(lines))
    return 0


def cmd_branch(args: argparse.Namespace) -> int:
    cfg = load_cfg(args)
    bug = load_bug(cfg, args.bug)
    prefix = str(cfg.get("project.branch_prefix") or "fix/")
    current = util.git_branch(cfg.repo)
    branch = f"{prefix}{bug.id}-{util.slug(bug.title)[:32]}"
    if args.dry_run:
        emit({"branch": branch}, args.json_out, f"将创建分支：{branch}（当前 {current}）")
        return 0
    if current == branch:
        emit({"branch": branch, "created": False}, args.json_out, f"已在分支 {branch}")
        return 0
    if not util.git("rev-parse --verify HEAD", cwd=cfg.repo)["ok"]:
        raise SystemExit("当前仓库还没有任何提交，先提交一次再建分支")
    res = util.git(f"checkout -b {branch}", cwd=cfg.repo)
    if not res["ok"]:
        # 分支已存在则直接切换
        res = util.git(f"checkout {branch}", cwd=cfg.repo)
    if not res["ok"]:
        raise SystemExit(f"创建分支失败：{res['output'][:400]}")
    emit({"branch": branch, "created": True}, args.json_out, f"已切换到分支 {branch}")
    return 0


def cmd_verify(args: argparse.Namespace) -> int:
    cfg = load_cfg(args)
    bug = None
    if args.bug:
        try:
            bug = load_bug(cfg, args.bug)
        except SystemExit:
            bug = None
    payload = run_verify(cfg, bug, only=args.only or "", scoped=not args.no_scope)
    if args.bug:
        payload["bug"] = args.bug
        path = save_verify(cfg, args.bug, payload)
        payload["saved"] = path
    lines = [f"验收 {'通过' if payload['ok'] else '未通过'} · {payload['summary']}", ""]
    for step in payload["steps"]:
        lines.append(f"$ {step['cmd']}")
        lines.append(f"  退出码 {step['exit_code']} · {step['duration_s']}s")
        if not step["ok"]:
            lines += ["", step["tail"][-2500:], ""]
    emit(payload, args.json_out, "\n".join(lines))
    return 0 if payload["ok"] else 1


def cmd_record(args: argparse.Namespace) -> int:
    cfg = load_cfg(args)
    kb = kb_for(cfg)
    bug = load_bug(cfg, args.bug)
    cause = args.root_cause or bug.root_cause or "unknown"
    if cause not in ROOT_CAUSES:
        raise SystemExit(f"未知根因分类 {cause}；可用：{', '.join(sorted(ROOT_CAUSES))}")
    if not args.reason.strip():
        raise SystemExit("必须用 --reason 写一句人能看懂的根因（例如「分页参数为 0 时未做边界校验，导致空数组取 [0]」）")
    verify_payload: Dict[str, Any] = {}
    vpath = os.path.join(cfg.runs_dir(), f"{util.slug(bug.id)}-verify.json")
    if os.path.isfile(vpath):
        verify_payload = util.read_json(vpath, {}) or {}
        if args.require_green and not args.allow_red and not verify_payload.get("ok"):
            raise SystemExit("最近一次验收未通过，拒绝记录（确需记录加 --allow-red）")
    files = args.files or _changed_files(cfg)
    record = FixRecord(
        bug_id=bug.id,
        project=cfg.name,
        fingerprint=bug.fingerprint,
        root_cause=cause,
        reason=args.reason.strip(),
        summary=(args.summary or bug.title)[:200],
        files=files,
        branch=util.git_branch(cfg.repo),
        commit=util.git("rev-parse --short HEAD", cwd=cfg.repo)["output"].strip(),
        verify={k: verify_payload.get(k) for k in ("ok", "cmd", "summary", "ran_at")} if verify_payload else {},
        duration_s=int(args.duration or 0),
        agent=args.agent or os.environ.get("ZFIX_AGENT", ""),
        created_at=util.now_iso(),
        tags=[t for t in (args.tags or "").split(",") if t.strip()],
    )
    kb.add_fix(record)
    bug.root_cause = cause
    bug.decision = "skip"
    bug.triage_note = "已修复并归档"
    util.write_json(cfg.bug_path(bug.id), bug.to_dict())
    lines = [
        f"已归档 #{bug.id} → {util_expand()}",
        f"  根因：{cause_label(cause)}（{cause}）",
        f"  说明：{record.reason}",
        f"  改动：{len(files)} 个文件 · 分支 {record.branch or '-'} · 验收 {'通过' if record.verify.get('ok') else '未记录'}",
    ]
    emit(record.to_dict(), args.json_out, "\n".join(lines))
    return 0


def util_expand() -> str:
    from .knowledge import fixes_path

    return fixes_path()


def _changed_files(cfg: Config) -> List[str]:
    """从 git 取改动文件，但只保留 write_scope 内的，并受 max_files 约束。

    不限范围的话，多人共用工作区时会把别人未提交的改动算进本次修复档案，
    污染「这次到底改了什么」这条最关键的证据。
    """
    res = util.git("diff --name-only HEAD", cwd=cfg.repo)
    files = [line.strip().replace("\\", "/") for line in res["output"].splitlines() if line.strip()] if res["ok"] else []
    scope = [str(s) for s in (cfg.get("fix.write_scope") or []) if str(s).strip()]
    if scope:
        files = [f for f in files if any(f == s.rstrip("/") or f.startswith(s) for s in scope)]
    limit = int(cfg.get("fix.max_files") or 12)
    if len(files) > limit:
        print(f"[zfix] 改动文件 {len(files)} 个，超过 max_files={limit}，只记前 {limit} 个。"
              f"建议显式传 --files 指明本次真正改的文件。", file=sys.stderr)
        files = files[:limit]
    return files


def cmd_learn(args: argparse.Namespace) -> int:
    cfg = load_cfg(args)
    kb = kb_for(cfg)
    candidates = kb.learn(min_occurrences=args.min, project="" if args.all_projects else cfg.name)
    if not candidates:
        emit([], args.json_out, "暂无可蒸馏的模式（同一指纹需至少出现 2 次，且档案里有根因说明）")
        return 0
    lines = [f"发现 {len(candidates)} 条候选模式：", ""]
    for pattern in candidates:
        lines.append(f"  {pattern.id} · {pattern.title}（{pattern.occurrences} 次）")
        lines.append(f"    规则：{pattern.rule}")
    if args.apply:
        written = [kb.save_pattern(p) for p in candidates]
        lines += ["", f"已写入 {len(written)} 个模式文件 → {os.path.dirname(written[0])}"]
        lines.append("建议接着跑 `zfix preflight` 把新规则注入 AGENTS.md。")
    else:
        lines += ["", "加 --apply 落盘。"]
    emit({"candidates": [p.to_dict() for p in candidates], "applied": bool(args.apply)}, args.json_out, "\n".join(lines))
    return 0


def cmd_guard(args: argparse.Namespace) -> int:
    cfg = load_cfg(args)
    kb = kb_for(cfg)
    diff_text = ""
    if args.diff_file:
        diff_text = util.read_text(args.diff_file, "")
    result = run_guard(cfg, kb, diff_text=diff_text, staged=args.staged)
    emit(result, args.json_out, render_guard(result))
    return 1 if result.get("blocked") else 0


def cmd_preflight(args: argparse.Namespace) -> int:
    cfg = load_cfg(args)
    kb = kb_for(cfg)
    platforms = [p.strip() for p in (args.platforms or "").split(",") if p.strip()]
    result = run_preflight(cfg, kb, dry_run=args.dry_run, platforms=platforms)
    if args.json_out:
        emit(result, True)
    elif args.dry_run:
        print(result["block"])
    else:
        for item in result["files"]:
            state = "已更新" if item["changed"] else "无变化"
            print(f"  [{state}] {item['path']}（{'已存在' if item['existed'] else '新建'}）")
        print(f"\n共注入 {result['rules']} 条规则。AI 下次开工会自动读到。")
    return 0


def cmd_report(args: argparse.Namespace) -> int:
    cfg = load_cfg(args)
    kb = kb_for(cfg)
    records = kb.fixes() if args.all_projects else kb.fixes(cfg.name)
    if not records:
        emit({"total": 0}, args.json_out, "暂无修复档案。修完第一个 bug 后跑 `zfix record`。")
        return 0
    by_cause: Dict[str, int] = {}
    by_project: Dict[str, int] = {}
    fp_count: Dict[str, int] = {}
    durations: List[int] = []
    green = 0
    for record in records:
        by_cause[record.root_cause] = by_cause.get(record.root_cause, 0) + 1
        by_project[record.project] = by_project.get(record.project, 0) + 1
        if record.fingerprint:
            fp_count[record.fingerprint] = fp_count.get(record.fingerprint, 0) + 1
        if record.duration_s:
            durations.append(record.duration_s)
        if record.verify.get("ok"):
            green += 1
    repeated = {k: v for k, v in fp_count.items() if v > 1}
    total = len(records)
    lines = [
        f"zfix 报告 · {total} 条修复档案" + ("" if args.all_projects else f"（项目 {cfg.name}）"),
        "",
        f"  验收通过率   {green}/{total} = {green * 100 // total}%",
        f"  复发缺陷     {len(repeated)} 个指纹重复出现（占 {len(repeated) * 100 // max(len(fp_count), 1)}%）",
    ]
    if repeated:
        worst = sorted(repeated.items(), key=lambda kv: -kv[1])[:5]
        lines.append("    最需处理：" + "，".join(f"{k[:8]}… ×{v}" for k, v in worst))
    if durations:
        lines.append(f"  平均修复耗时 {sum(durations) // len(durations)}s")
    lines += ["", "  根因分布："]
    for cause, count in sorted(by_cause.items(), key=lambda kv: -kv[1]):
        lines.append(f"    {cause_label(cause):<16}{count:>4}  {'█' * min(count * 2, 40)}")
    if len(by_project) > 1:
        lines += ["", "  项目分布：" + "，".join(f"{k} {v}" for k, v in sorted(by_project.items(), key=lambda kv: -kv[1]))]
    top_causes = [c for c, n in sorted(by_cause.items(), key=lambda kv: -kv[1])[:3] if n >= 2]
    if top_causes:
        lines += ["", "  建议优先加固：" + "、".join(cause_label(c) for c in top_causes)]
    emit(
        {
            "total": total,
            "green": green,
            "by_cause": by_cause,
            "by_project": by_project,
            "repeat_fingerprints": repeated,
            "avg_duration_s": (sum(durations) // len(durations)) if durations else 0,
        },
        args.json_out,
        "\n".join(lines),
    )
    return 0


def cmd_next(args: argparse.Namespace) -> int:
    """给 AI 的「下一件事」：挑一个可自动修的 bug 并生成上下文包。"""
    cfg = load_cfg(args)
    kb = kb_for(cfg)
    bugs = load_all(cfg)
    if not bugs:
        print("本地没有 bug 记录，先跑 `zfix sync`。", file=sys.stderr)
        return 1
    pending = [b for b in bugs if b.decision in ("auto", "manual") and not kb.fixes_for_bug(b.id)]
    pending.sort(key=lambda b: (b.decision != "auto", b.severity_rank, b.pri, b.id))
    if not pending:
        print("没有待修 bug（全部已归档或已跳过）。")
        return 0
    bug = pending[0]
    locations = Locator(cfg.repo).locate(bug, limit=args.limit)
    content = build_context(cfg, bug, kb, locations)
    path = save_context(cfg, bug, content)
    if args.json_out:
        emit({"bug": bug.to_dict(), "context": path, "pending": len(pending)}, True)
        return 0
    print(f"下一件事：#{bug.id} {bug.title}")
    print(f"  分诊 {bug.decision} · 根因 {cause_label(bug.root_cause)} · 指纹 {bug.fingerprint}")
    print(f"  上下文包：{path}")
    print(f"  候选位置：{len(locations)} 处（详情见上下文包第 3 节）")
    print(f"  待修队列剩余 {len(pending)} 条")
    return 0


def cmd_show(args: argparse.Namespace) -> int:
    cfg = load_cfg(args)
    bug = load_bug(cfg, args.bug)
    emit(bug.to_dict(), args.json_out, "\n".join(f"{k:<14}{v}" for k, v in bug.to_dict().items() if v not in ("", None, {}, [])))
    return 0


# ---------------------------------------------------------------- 入口


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="zfix",
        description="禅道缺陷 → 定位 → 修复 → 验证 → 沉淀 → 防复发（平台无关）",
    )
    parser.add_argument("--version", action="version", version=f"zfix {__version__}")
    parser.add_argument("--root", help="项目根目录（默认从 cwd 向上找 .zfix/config.toml）")
    parser.add_argument("--project", help="已注册的项目名（替代 --root）")
    parser.add_argument("--json", dest="json_out", action="store_true", help="输出 JSON，便于其他程序消费")
    sub = parser.add_subparsers(dest="command")

    # 全局开关也挂到每个子命令上：允许 `zfix sync --json` 这种把开关写在后面的写法。
    # 用 SUPPRESS 兜底，否则子命令的默认值会把写在子命令之前的开关覆盖掉。
    common = argparse.ArgumentParser(add_help=False)
    common.add_argument("--root", default=argparse.SUPPRESS)
    common.add_argument("--project", default=argparse.SUPPRESS)
    common.add_argument("--json", dest="json_out", action="store_true", default=argparse.SUPPRESS)

    def sp(name: str, **kwargs: Any) -> argparse.ArgumentParser:
        return sub.add_parser(name, parents=[common], **kwargs)

    p = sp("init", help="生成 .zfix/config.toml 并注册项目")
    p.add_argument("--name")
    p.add_argument("--stack", default="generic", choices=["python", "node", "java", "go", "generic"])
    p.add_argument("--url", default="")
    p.add_argument("--product", default="")
    p.add_argument("--force", action="store_true")
    p.set_defaults(func=cmd_init)

    sp("doctor", help="环境与连通性自检").set_defaults(func=cmd_doctor)

    p = sp("sync", help="从禅道拉取缺陷到本地")
    p.add_argument("--status")
    p.add_argument("--product")
    p.add_argument("--limit", type=int, default=0)
    p.set_defaults(func=cmd_sync)

    p = sp("list", help="列出本地缺陷")
    p.add_argument("--status")
    p.add_argument("--decision", choices=["auto", "manual", "skip"])
    p.set_defaults(func=cmd_list)

    sp("triage", help="打指纹 / 判根因 / 定可修性 / 去重").set_defaults(func=cmd_triage)

    p = sp("context", help="生成 AI 修复上下文包")
    p.add_argument("bug")
    p.add_argument("--limit", type=int, default=10)
    p.add_argument("--print", dest="print_out", action="store_true", help="直接打印内容")
    p.set_defaults(func=cmd_context)

    p = sp("locate", help="定位候选代码位置")
    p.add_argument("bug")
    p.add_argument("--limit", type=int, default=12)
    p.set_defaults(func=cmd_locate)

    p = sp("branch", help="按缺陷号创建修复分支")
    p.add_argument("bug")
    p.add_argument("--dry-run", action="store_true")
    p.set_defaults(func=cmd_branch)

    p = sp("verify", help="跑验收命令")
    p.add_argument("bug", nargs="?", default="")
    p.add_argument("--only", choices=["lint", "typecheck", "test"])
    p.add_argument("--no-scope", action="store_true", help="不用缺陷关键词缩小测试范围")
    p.set_defaults(func=cmd_verify)

    p = sp("record", help="归档修复档案（知识库的唯一输入）")
    p.add_argument("bug")
    p.add_argument("--root-cause", choices=sorted(ROOT_CAUSES))
    p.add_argument("--reason", required=True, help="一句话根因，写清「为什么会错」，不是「改了哪行」")
    p.add_argument("--summary")
    p.add_argument("--files", nargs="*")
    p.add_argument("--duration", type=int, default=0)
    p.add_argument("--agent", default="")
    p.add_argument("--tags", default="")
    p.add_argument("--require-green", action="store_true", help="验收未通过则拒绝归档")
    p.add_argument("--allow-red", action="store_true")
    p.set_defaults(func=cmd_record)

    p = sp("learn", help="把重复修复蒸馏成预防规则")
    p.add_argument("--min", type=int, default=2)
    p.add_argument("--apply", action="store_true")
    p.add_argument("--all-projects", action="store_true")
    p.set_defaults(func=cmd_learn)

    p = sp("guard", help="扫 diff 命中已知反模式")
    p.add_argument("--staged", action="store_true")
    p.add_argument("--diff-file")
    p.set_defaults(func=cmd_guard)

    p = sp("preflight", help="把规则注入 AGENTS.md / CLAUDE.md")
    p.add_argument("--dry-run", action="store_true")
    p.add_argument("--platforms", default="", help="逗号分隔：claude,codex,codebee,opencode")
    p.set_defaults(func=cmd_preflight)

    p = sp("report", help="修复趋势 / 复发率 / 根因分布")
    p.add_argument("--all-projects", action="store_true")
    p.set_defaults(func=cmd_report)

    p = sp("next", help="取下一个待修缺陷并生成上下文包")
    p.add_argument("--limit", type=int, default=10)
    p.set_defaults(func=cmd_next)

    p = sp("show", help="查看单条缺陷")
    p.add_argument("bug")
    p.set_defaults(func=cmd_show)

    return parser


def main(argv: Optional[List[str]] = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    if not getattr(args, "func", None):
        parser.print_help()
        return 2
    try:
        return int(args.func(args) or 0)
    except SourceError as exc:
        print(f"[zfix] 缺陷源错误：{exc}", file=sys.stderr)
        return 3
    except KeyboardInterrupt:
        return 130
