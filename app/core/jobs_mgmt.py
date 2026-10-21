# -*- coding: utf-8 -*-
"""管理操作与安装失败 AI 修复：装/升/卸/连通性测试、CodeBee 自升级、修复白名单。

从 jobs 拆出（单文件行数治理），执行入口仍在 jobs._run_job，行为不变：
  - _do_mgmt：catalog 管理命令 + smoke 连通性测试，按步骤汇总 run 状态；
  - _do_selfupgrade：npm 全局升级并实时落盘日志；
  - _ai_repair：失败日志 + 本机环境喂给真实智能体，命令过白名单才执行；
  - _repair_command_argv：不经过 shell 的窄安装语法解析（测试直接覆盖）。
"""
from __future__ import annotations

import re
import sys

from . import prompt_templates


AI_REPAIR_PROMPT = """你是环境工程师。在 __OS__ 上执行下面的安装命令失败了，请诊断原因并给出修正命令。
只输出一个 ```json 代码块，不要输出其他内容。JSON 结构：
{"diagnosis": "失败原因（一句话）", "command": "修正后的完整安装命令", "safe": true/false}
硬性约束：command 只能是本机包管理器的安装命令，前缀必须是 __ALLOW__ 之一，且不要带 sudo
（命令非交互执行，sudo 会挂死；brew/npm 本身也拒绝 sudo）。
给不出符合约束的安全命令时，safe 设为 false 且 command 留空。
优先沿用失败命令的包管理器与安装形态（如原命令是 uv tool install 就继续用 uv，
不要另起 pip 换安装面）；git 拉取类的网络抖动直接给原命令即可，传输层兜底由执行方处理。

## 失败的命令
__CMD__

## 失败输出（尾部）
__LOG__

## 本机环境
__ENV__"""

# AI 修复命令白名单：只放行包管理器的安装类命令（提示词里的前缀清单由它生成，
# 两处永远不会漂移）。按平台分组：winget/py 启动器是 Windows 独有，brew/python3
# 是 macOS/Linux 渠道——Mac 上不放行 brew 的话，AI 给出正确的修正命令也会被拒。
_AI_REPAIR_ALLOW_WIN = ("npm install ", "winget install", "py -3.13 -m pip install",
                        "uv tool install")
_AI_REPAIR_ALLOW_UNIX = ("npm install ", "brew install", "python3 -m pip install",
                         "uv tool install")
AI_REPAIR_ALLOW = _AI_REPAIR_ALLOW_WIN  # 兼容旧引用：Windows 本机即此表

_AI_REPAIR_PREFIXES_WIN = (
    ("npm", "install"), ("winget", "install"),
    ("py", "-3.13", "-m", "pip", "install"), ("uv", "tool", "install"),
)
_AI_REPAIR_PREFIXES_UNIX = (
    ("npm", "install"), ("brew", "install"),
    ("python3", "-m", "pip", "install"), ("uv", "tool", "install"),
)
_AI_REPAIR_TOKEN_RE = re.compile(r"[A-Za-z0-9@._+:/=\[\],-]+")


def _repair_os_label(platform=None):
    return {"win32": "Windows", "darwin": "macOS"}.get(platform or sys.platform, "Linux")


def _repair_allow(platform=None):
    if (platform or sys.platform) == "win32":
        return _AI_REPAIR_ALLOW_WIN
    return _AI_REPAIR_ALLOW_UNIX


def _repair_command_allowed(cmd, platform=None):
    return _repair_command_argv(cmd, platform=platform) is not None


def _repair_command_argv(cmd, platform=None):
    """Parse the narrow AI-repair install grammar without invoking a shell.

    Shell quoting and operators are intentionally unsupported. Package names,
    versions, and common install flags remain one argv token each.
    """
    if not isinstance(cmd, str):
        return None
    command = cmd.strip()
    if not command or "\n" in command or "\r" in command:
        return None
    tokens = command.split()
    if not all(_AI_REPAIR_TOKEN_RE.fullmatch(token) for token in tokens):
        return None
    prefixes = (_AI_REPAIR_PREFIXES_WIN if (platform or sys.platform) == "win32"
                else _AI_REPAIR_PREFIXES_UNIX)
    for prefix in prefixes:
        if tokens[:len(prefix)] == list(prefix) and len(tokens) > len(prefix):
            return tokens
    return None




def _now():
    import time
    return time.strftime("%Y-%m-%d %H:%M:%S")


def _smoke_response_ok(text):
    """Accept an explicit OK line; substrings such as "NOT OK" are failures."""
    for line in str(text or "").splitlines():
        value = line.strip().strip("`*_\"'").strip()
        while value and value[-1] in ".!。！":
            value = value[:-1].strip()
        if value.upper() == "OK":
            return True
    return False


def _do_mgmt(job, ev):
    from . import catalog, manager, store
    run_id = job["run_id"]
    entry = catalog.by_id(job.get("entry_id"))
    op = job.get("op") or ""
    if ev.is_set() or (store.get_run(run_id) or {}).get("status") != "running":
        return
    if entry is None:
        store.update_run(run_id, expected_status="running", status="failed",
                         error="catalog 中找不到 %s" % job.get("entry_id"),
                         ended_at=_now())
        return
    step, log_abs = store.add_step(run_id, op or "mgmt", entry["id"], entry.get("name", entry["id"]))
    if step is None or log_abs is None:
        return
    ok = False
    if op in ("install", "upgrade", "uninstall"):
        res = manager.run_mgmt_command(entry, op, cancel_event=ev, log_path=str(log_abs))
        if ev.is_set() or (store.get_run(run_id) or {}).get("status") != "running":
            return
        ok = res["ok"]
        # 文件占用类失败（Windows 文件锁 EBUSY/EPERM）给人话结论并跳过 AI 修复：
        # 修复智能体面对文件锁只会给出 taskkill 全杀 node 之类白名单必拒的危险
        # 命令，白白烧一轮 300s 诊断（2026-09-18 dsh 同版本重装 EBUSY 案）
        lock_hit = (not ok and op in ("install", "upgrade") and _file_lock_error(res))
        # uv 缺失同跳 AI 修复：修复链就算装上 uv，复检目标 CLI 仍未装，结论还是
        # 失败，白烧一轮；人话指路「先装 uv 再点安装」一步到位（2026-10-09 Mac
        # aider/trae 双装失败实案）
        uv_missing = bool(res.get("uv_missing"))
        if lock_hit:
            summary = ("失败：安装文件被占用（可能有同名程序在运行，或杀毒软件正在扫描），"
                       "请关闭占用该文件的程序后重试。完整输出见日志。")
        elif uv_missing:
            summary = "失败：" + str(res.get("error") or "")[:300]
        else:
            # note 优先：良性收口（如 winget「已装无可升级」复检在装）给用户
            # 看原因，别让「完成」两个字孤零零不带解释
            extra = str(res.get("note") or res.get("error") or "")[:300]
            summary = ("完成" if ok else "失败") + (": " + extra if extra else "")
        store.finish_step(run_id, step["n"],
                          "done" if ok else "failed",
                          summary=summary,
                          exit_code=res.get("exit_code"))
        # AI 修复只针对安装类失败；卸载失败多为权限/程序占用，留给用户看日志处理
        if not ok and op in ("install", "upgrade") and not lock_hit and not uv_missing:
            ok = _ai_repair(run_id, entry, ev, entry.get(op), log_abs)
    elif op == "smoke":
        from . import runner as _r
        from . import registry
        from . import usage as _usage
        agents = registry.effective_agents(catalog.load(), manager.detect_all())
        agent = next((a for a in agents if a["id"] == entry["id"]), None)
        if agent is None:
            store.finish_step(run_id, step["n"], "failed", summary="该智能体未安装或未启用编排")
            store.update_run(run_id, expected_status="running", status="failed",
                             error="未启用", ended_at=_now())
            return
        res = _r.run_agent(agent, "连通性测试：请只回复两个字：OK",
                           readonly=True,
                           # 300s：codex CLI 启动要拉 5 个 MCP 服务器 + 注入约 13 万
                           # token 技能上下文，首 token 常超 180s（2026-09-17 实测
                           # 网关裸探 8s 就回，慢在 CLI 自身启动与上下文）。
                           timeout=300, cancel_event=ev, log_path=str(log_abs))
        if ev.is_set() or (store.get_run(run_id) or {}).get("status") != "running":
            return
        ok = bool(res.get("ok") and _smoke_response_ok(res.get("text")))
        try:
            _usage.record(source="smoke", run_id=run_id, step=step["n"], role="smoke",
                          agent=agent.get("id", ""), agent_label=agent.get("label", ""),
                          tool=agent.get("kind", ""), model=res.get("model") or "",
                          ok=ok,
                          duration_s=float((res.get("raw") or {}).get("duration") or 0.0),
                          cost_usd=float(res.get("cost_usd") or 0.0),
                          usage=res.get("usage"))
        except Exception:
            pass
        store.finish_step(run_id, step["n"], "done" if ok else "failed",
                          summary=("CLI 默认配置连通：%s" % (res.get("text") or "")[:120]) if ok
                          else ("CLI 默认配置异常：%s" %
                                (res.get("error") or (res.get("text") or "")[:120])),
                          exit_code=res["raw"].get("exit_code"),
                          cost_usd=res.get("cost_usd", 0.0), tokens=res.get("tokens", 0))
    else:
        store.finish_step(run_id, step["n"], "failed", summary="未知操作 %s" % op)
    # 以步骤状态汇总 run 状态；expected_status 守卫：用户已强制终止的运行
    # 保持 cancelled，不被步骤汇总改写成 failed（被杀的步骤记的就是非 done）
    run = store.get_run(run_id)
    statuses = [s["status"] for s in (run.get("steps") if run else [])] or ["failed"]
    final = "done" if all(s == "done" for s in statuses) else "failed"
    suffix = "（AI 修复成功）" if ok and len((run.get("steps") if run else [])) > 1 else ""
    store.update_run(run_id, expected_status="running", status=final, ended_at=_now(),
                     summary=("%s %s %s%s" % (entry.get("name"), op,
                                              "完成" if final == "done" else "失败", suffix)))


def _do_selfupgrade(job, ev):
    """CodeBee 自升级：在 mgmt run 里跑 npm install -g @latest，日志实时落盘。"""
    from . import selfupdate, store
    run_id = job["run_id"]
    if ev.is_set() or (store.get_run(run_id) or {}).get("status") != "running":
        return
    step, log_abs = store.add_step(run_id, "selfupgrade", "__self__", "CodeBee")
    if step is None or log_abs is None:
        return
    try:
        res = selfupdate.run_upgrade(run_id, str(log_abs), cancel_event=ev)
    except Exception as e:
        res = {"ok": False, "exit_code": None, "error": repr(e)}
    if ev.is_set() or (store.get_run(run_id) or {}).get("status") != "running":
        return
    store.finish_step(run_id, step["n"], "done" if res["ok"] else "failed",
                      summary="升级完成，服务将自动重启生效" if res["ok"]
                      else ("升级失败: " + res["error"][:300]),
                      exit_code=res.get("exit_code"))
    # expected_status 守卫：用户已强制终止的运行保持 cancelled，不被改写
    store.update_run(run_id, expected_status="running",
                     status="done" if res["ok"] else "failed", ended_at=_now(),
                     summary="CodeBee selfupgrade %s" % ("完成" if res["ok"] else "失败"))


def _file_lock_error(res):
    """安装/升级失败输出是否为文件占用类错误（npm/pip 的 EBUSY/EPERM 文件锁）。"""
    err = (res or {}).get("error") or ""
    return "EBUSY" in err or "EPERM" in err


def _ai_repair(run_id, entry, ev, failed_cmd, orig_log):
    """安装失败后的 AI 诊断修复：诊断 → 白名单校验 → 执行 → 复检。"""
    from . import catalog, manager, registry, router, runner, store
    from . import usage as _usage
    agents = registry.effective_agents(catalog.load(), manager.detect_all())
    agent, _reason = router.pick(agents, "repair", "mgmt")
    if agent is None or agent.get("mode") != "real":
        return False  # 无真实智能体可用，维持原失败
    try:
        # 日志尾巴喂进 AI 修复提示词：先洗终端噪声，否则 ANSI 转义/乱码墙会被
        # 模型当成"日志原文"照抄进修复推理里
        log_tail = runner.clean_cli_text(
            runner.tail_decoded(orig_log.read_bytes(), 2000)) if orig_log.exists() else "（无输出）"
    except Exception:
        log_tail = "（日志不可读）"
    import shutil
    is_win = sys.platform == "win32"
    env_lines = [
        "OS: %s" % _repair_os_label(),
        "node: %s" % (shutil.which("node") or "缺失"),
        "npm: %s" % (shutil.which("npm") or "缺失"),
        "pnpm: %s" % (shutil.which("pnpm") or "缺失"),
        "python: %s" % (shutil.which("python3" if not is_win else "python") or "缺失"),
        "uv: %s" % (shutil.which("uv") or "缺失"),
    ]
    if not is_win:
        env_lines.append("brew: %s" % (shutil.which("brew") or "缺失"))
    prompt = prompt_templates.render_values(AI_REPAIR_PROMPT, {
        "__CMD__": failed_cmd or "（未知）",
        "__LOG__": log_tail,
        "__ENV__": "\n".join(env_lines),
        "__OS__": _repair_os_label(),
        "__ALLOW__": " / ".join(p.strip() for p in _repair_allow()),
    })
    step, log_abs = store.add_step(run_id, "ai-repair", agent["id"], agent.get("label"),
                                   note="自动诊断修复", model=agent.get("model") or "")
    if step is None or log_abs is None:
        return False
    res = runner.run_agent(agent, prompt, readonly=True, timeout=300,
                           cancel_event=ev, log_path=str(log_abs),
                           command_context={"run_id": run_id})
    try:
        _usage.record(source="repair", run_id=run_id, step=step["n"], role="ai-repair",
                      agent=agent.get("id", ""), agent_label=agent.get("label", ""),
                      tool=agent.get("kind", ""), model=res.get("model") or "",
                      ok=bool(res.get("ok")),
                      duration_s=float((res.get("raw") or {}).get("duration") or 0.0),
                      cost_usd=float(res.get("cost_usd") or 0.0),
                      usage=res.get("usage"))
    except Exception:
        pass
    if not res["ok"]:
        store.finish_step(run_id, step["n"], "failed",
                          summary="诊断调用失败：%s" % (res.get("error") or "")[:200])
        return False
    data = runner.extract_json(res.get("text") or "")
    diagnosis = str((data or {}).get("diagnosis") or "（无诊断）")[:200]
    cmd = str((data or {}).get("command") or "").strip()
    repair_argv = _repair_command_argv(cmd)
    safe = bool((data or {}).get("safe")) and repair_argv is not None
    try:
        # write_bytes 而非 write_text(...encode())：bytes 传入文本模式 write 会
        # TypeError 且被下面的裸 except 吞掉，诊断三行从未落过日志（2026-09-18 修）
        log_abs.write_bytes(
            ("\n[AI 诊断] %s\n[AI 建议] %s\n[白名单] %s\n" %
             (diagnosis, cmd or "（无）", "通过" if safe else "不通过，拒绝自动执行")).encode("utf-8"))
    except Exception:
        pass
    if not safe:
        store.finish_step(run_id, step["n"], "failed",
                          summary="AI 建议命令未过白名单，需人工执行：%s（诊断：%s）" % (cmd, diagnosis))
        return False
    fix = manager.run_install_argv(repair_argv, cancel_event=ev,
                                   log_path=str(log_abs))
    manager.detect_all(force=True)
    with manager._LOCK:
        manager._STATE["versions"].pop(entry["id"], None)
    if fix["ok"]:
        # 修复安装同样会改变版本结论，徽章缓存一并作废复检
        manager.refresh_update_async(entry)
    installed = manager.detect_entry(entry)["installed"]
    store.finish_step(run_id, step["n"],
                      "done" if (fix["ok"] and installed) else "failed",
                      summary="诊断：%s → 执行 %r：%s，检测安装状态：%s" % (
                          diagnosis, cmd,
                          "命令成功" if fix["ok"] else "命令失败",
                          "已安装" if installed else "未检出"),
                      exit_code=fix.get("exit_code"))
    return bool(fix["ok"] and installed)
