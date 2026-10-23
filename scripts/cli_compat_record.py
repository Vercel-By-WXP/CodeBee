#!/usr/bin/env python
"""真实 CLI 任务调用验收：一条命令产出一行可入 CLI_COMPATIBILITY.md 的记录。

设计约束（写死在代码里，不靠使用者自觉）：

- **只用隔离实例**：临时 ``TUTTI_DATA`` + 空闲端口，绝不碰 8765 上的生产服务。
- **不落仓库**：工作目录与数据都在临时目录里，跑完删；记录写到 ``--out``。
- **不猜结论**：路由是否真的用了目标 CLI 由 ``run.steps[].agent`` 判定。如果
  实际执行的是别的 CLI 或 mock，记录标为「未命中目标 CLI」，任务调用一列
  记「未测」——不许把「跑过一次任务」当成「目标 CLI 通过」。
- **脱敏**：输出前统一抹掉用户目录、令牌形态的串。

用法：

    python scripts/cli_compat_record.py --cli codex-cli
    python scripts/cli_compat_record.py --cli claude-code --timeout 1200 --out docs/cli-compat-records

退出码：0 已产出记录（结果见记录里的 result）；2 参数/环境错误；3 目标 CLI 未安装。
"""
import argparse
import json
import os
import re
import shutil
import socket
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.request

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

DEFAULT_GOAL = ("在当前目录创建 hello.py，内容只有一行 print('hello')；"
                "创建后运行它，确认输出 hello。")
DEFAULT_VERIFY = ("python -c \"import pathlib,sys; "
                  "sys.exit(0 if pathlib.Path('hello.py').is_file() else 1)\"")

_SECRET_PATTERNS = (
    (re.compile(r"(?i)\b(bearer)\s+[A-Za-z0-9._\-]+"), r"\1 <redacted>"),
    (re.compile(r"\bsk-[A-Za-z0-9_\-]{8,}"), "sk-<redacted>"),
    (re.compile(r"\bgh[pousr]_[A-Za-z0-9]{8,}"), "gh<redacted>"),
    (re.compile(r"(?i)\b(api[_-]?key|token|secret|password)\b\s*[=:]\s*\S+"),
     r"\1=<redacted>"),
)


def sanitize(text):
    """抹掉用户目录与令牌形态，保留可核对的信息（版本号、错误码、耗时）。"""
    text = str(text or "")
    home = os.path.expanduser("~")
    if home and len(home) > 3:
        text = text.replace(home, "<home>")
    text = re.sub(r"(?i)[A-Z]:\\Users\\[^\\\s\"']+", r"<home>", text)
    text = re.sub(r"/(?:home|Users)/[^/\s\"']+", r"<home>", text)
    for pattern, repl in _SECRET_PATTERNS:
        text = pattern.sub(repl, text)
    return text


def free_port():
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def http(method, path, port, body=None, timeout=60):
    url = "http://127.0.0.1:%d%s" % (port, path)
    data = json.dumps(body).encode("utf-8") if body is not None else None
    req = urllib.request.Request(url, data=data, method=method)
    if data:
        req.add_header("Content-Type", "application/json")
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return resp.status, resp.read().decode("utf-8", "replace")
    except urllib.error.HTTPError as exc:
        return exc.code, exc.read().decode("utf-8", "replace")


def json_of(method, path, port, body=None, timeout=60):
    status, raw = http(method, path, port, body, timeout)
    try:
        return status, json.loads(raw)
    except ValueError:
        return status, {"_raw": raw[:500]}


def catalog_entry(cli_id):
    from app.core import catalog
    for entry in catalog.DEFAULT_CATALOG:
        if entry.get("id") == cli_id:
            return entry
    return None


def executable_of(entry):
    orch = entry.get("orch") or {}
    detect = entry.get("detect") or {}
    return orch.get("command") or detect.get("cli") or entry.get("id")


def catalog_specs():
    """按 catalog 生成探测规格。

    必须显式传：`/api/cli-discovery` 不传 ``specs`` 时用的是 ``DEFAULT_CLIS``，
    那是 5 条旧条目（codex/claude/qwen/aider/opencode），名字与 catalog ID
    （codex-cli/...）不同名，19 条里 14 条永远探不到。这里是验收脚本，探的
    必须是 catalog 声明支持的全集，否则「未安装」和被遗漏分不开。
    """
    from app.core import catalog
    specs = []
    for entry in catalog.DEFAULT_CATALOG:
        exe = executable_of(entry)
        if not exe:
            continue
        specs.append({"name": entry.get("id"), "executable": exe})
    return specs


def local_version():
    try:
        with open(os.path.join(ROOT, "VERSION"), encoding="utf-8") as fh:
            return fh.read().strip()
    except OSError:
        return "unknown"


def local_commit():
    try:
        out = subprocess.run(["git", "rev-parse", "--short", "HEAD"], cwd=ROOT,
                             capture_output=True, text=True, timeout=15)
        return (out.stdout or "").strip() or "unknown"
    except (OSError, subprocess.SubprocessError):
        return "unknown"


def agents_used(run):
    """从步骤里取实际执行者。run.steps[].agent 是路由的真实结果，不是意图。"""
    used = []
    for step in (run or {}).get("steps") or []:
        if not isinstance(step, dict):
            continue
        name = str(step.get("agent") or "").strip()
        if name and name not in used:
            used.append(name)
    return used


def classify_outcome(run, target_id):
    """把一次运行归成可入表的结果。命中判定优先于成败判定。"""
    run = run or {}
    status = str(run.get("status") or "")
    verdict = run.get("verdict") if isinstance(run.get("verdict"), dict) else {}
    used = agents_used(run)
    if target_id not in used:
        return {
            "result": "未命中目标 CLI",
            "task_call": "未测",
            "reason": "路由实际执行者：%s；目标 %s 未被调用，本次运行不能作为它的验收证据"
                      % ("、".join(used) or "（无步骤记录）", target_id),
        }
    if status == "done":
        return {"result": "通过", "task_call": "通过",
                "reason": "运行到终态 done，目标 CLI 出现在执行步骤中"}
    if status == "cancelled":
        return {"result": "失败", "task_call": "失败",
                "reason": "运行被取消；不算通过"}
    return {"result": "失败", "task_call": "失败",
            "reason": "运行终态 %s，错误：%s" % (status, sanitize(run.get("error") or "")[:200])}


def build_record(meta):
    """纯函数：把采集到的事实拼成记录。便于单测，不允许在这里补猜字段。"""
    rec = {
        "captured_at": meta["captured_at"],
        "codebee_version": meta["codebee_version"],
        "codebee_commit": meta["codebee_commit"],
        "os": meta["os"],
        "cli_id": meta["cli_id"],
        "cli_version": sanitize(meta["cli_version"]),
        "cli_source": sanitize(meta["cli_source"]),
        "model_auth": "未记录",
        "invocation": sanitize(meta.get("invocation") or "由 CodeBee 路由决定"),
        "case": meta.get("case") or DEFAULT_GOAL,
        "result": meta["outcome"]["result"],
        "task_call": meta["outcome"]["task_call"],
        "timeout_exit": meta.get("timeout_exit") or "未测",
        "session_resume": meta.get("session_resume") or "未测",
        "duration_s": meta.get("duration_s"),
        "steps": meta.get("steps") or [],
        "verify_ran": meta.get("verify_ran"),
        "verify_pass": meta.get("verify_pass"),
        "review_pass": meta.get("review_pass"),
        "evidence": sanitize(meta.get("evidence") or ""),
        "boundary": meta["outcome"].get("reason") or "",
    }
    return rec


def markdown_row(rec):
    return "| %s | %s | %s | %s | %s | %s | %s | %s |" % (
        rec["cli_id"], rec["cli_version"] or "—", rec["os"],
        rec["model_auth"], rec["task_call"], rec["timeout_exit"],
        rec["session_resume"],
        "%s；%s" % (rec["result"], rec["boundary"][:120]))


def write_record(rec, out_dir):
    os.makedirs(out_dir, exist_ok=True)
    stamp = time.strftime("%Y%m%d-%H%M%S")
    path = os.path.join(out_dir, "%s-%s.json" % (rec["cli_id"], stamp))
    atomic_write(path, rec)
    return path


def atomic_write(path, payload):
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as fh:
        json.dump(payload, fh, ensure_ascii=False, indent=2)
    os.replace(tmp, path)


def engine_env(data_dir):
    env = dict(os.environ)
    env["TUTTI_DATA"] = data_dir
    env.pop("TUTTI_PORT", None)
    return env


def pin_cli(port, target_id, installed_ids):
    """只留目标 CLI 参与编排。返回实际设置失败的项目，不静默忽略。"""
    failed = []
    for cid in installed_ids:
        body = {"agent_id": cid, "enabled": cid == target_id}
        status, _ = http("POST", "/api/orchestration", port, body, timeout=15)
        if status != 200:
            failed.append(cid)
    return failed


def run_once(args):
    entry = catalog_entry(args.cli)
    if entry is None:
        print(json.dumps({"error": "catalog 里没有 %s" % args.cli, "exit": 2},
                         ensure_ascii=False))
        return 2
    exe = executable_of(entry)
    path = shutil.which(exe)
    if not path:
        rec = {"cli_id": args.cli, "result": "未安装", "task_call": "未测",
               "reason": "%s 不在 PATH" % exe}
        print(json.dumps(rec, ensure_ascii=False, indent=2))
        return 3

    data_dir = tempfile.mkdtemp(prefix="compat-")
    wd = os.path.join(data_dir, "wd")
    os.makedirs(wd, exist_ok=True)
    port = args.port or free_port()
    proc = subprocess.Popen([sys.executable, os.path.join(ROOT, "app", "main.py"),
                             "--port", str(port), "--host", "127.0.0.1"],
                            env=engine_env(data_dir), cwd=ROOT,
                            stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
    try:
        ready = False
        for _ in range(args.boot_timeout):
            try:
                status, _ = http("GET", "/api/state", port, timeout=4)
                if status == 200:
                    ready = True
                    break
            except Exception:
                pass
            time.sleep(1)
        if not ready:
            print(json.dumps({"error": "隔离实例未就绪", "port": port}, ensure_ascii=False))
            return 2

        status, disc = json_of("POST", "/api/cli-discovery", port,
                               {"specs": catalog_specs(), "timeout": 5}, timeout=180)
        rows = (disc or {}).get("clis") or []
        target_row = next((r for r in rows if r.get("name") == args.cli), {})
        if not target_row.get("available"):
            print(json.dumps({"error": "目标 CLI 探测不可用",
                              "cli": args.cli,
                              "error_detail": sanitize(target_row.get("error") or "")},
                             ensure_ascii=False))
            return 3
        installed = [r["name"] for r in rows if r.get("available")]
        pin_failed = pin_cli(port, args.cli, installed)

        if args.self_check:
            # 只验链路（实例/探测/编排开关），不建任务 —— 不消耗任何模型额度。
            print(json.dumps({
                "self_check": "ok", "port": port, "cli": args.cli,
                "cli_version": sanitize(target_row.get("version") or ""),
                "installed_clis": installed, "pin_failed": pin_failed,
                "note": "未建任务，任务调用/超时退出/会话恢复均未测",
            }, ensure_ascii=False, indent=2))
            return 0

        status, created = json_of("POST", "/api/tasks", port, {
            "type": "code", "title": "兼容性验收：%s" % args.cli,
            "goal": args.case, "workdir": wd,
            "verify_command": args.verify,
        })
        if status != 200:
            print(json.dumps({"error": "建任务失败", "status": status,
                              "body": sanitize(str(created)[:300])}, ensure_ascii=False))
            return 2
        run_id = created.get("run_id") or ""

        started = time.time()
        run, terminal = {}, False
        while time.time() - started < args.timeout:
            status, payload = json_of("GET", "/api/runs/%s" % run_id, port, timeout=30)
            run = (payload or {}).get("run") or {}
            if run.get("status") in ("done", "failed", "cancelled"):
                terminal = True
                break
            time.sleep(3)
        duration = round(time.time() - started, 1)
        if not terminal:
            http("POST", "/api/runs/%s/cancel" % run_id, port, {}, timeout=20)
            outcome = {"result": "未测", "task_call": "未测",
                       "reason": "轮询 %ss 未到终态，已请求取消；超时退出未取证" % args.timeout}
        else:
            outcome = classify_outcome(run, args.cli)

        verdict = run.get("verdict") if isinstance(run.get("verdict"), dict) else {}
        rec = build_record({
            "captured_at": time.strftime("%Y-%m-%d %H:%M:%S"),
            "codebee_version": local_version(),
            "codebee_commit": local_commit(),
            "os": "%s %s" % (sys.platform, os.environ.get("PROCESSOR_ARCHITECTURE") or ""),
            "cli_id": args.cli,
            "cli_version": target_row.get("version") or "",
            "cli_source": target_row.get("path") or path,
            "case": args.case,
            "outcome": outcome,
            "duration_s": duration,
            "steps": agents_used(run),
            "verify_ran": verdict.get("verify_ran"),
            "verify_pass": verdict.get("verify_pass"),
            "review_pass": verdict.get("review_pass"),
            "evidence": "run %s（隔离实例 port=%s，数据目录已删）" % (run_id, port),
            "timeout_exit": "未测",
            "session_resume": "未测",
        })
        if pin_failed:
            rec["boundary"] += "；编排开关设置失败：%s" % "、".join(pin_failed)
        out_path = write_record(rec, args.out)
        print(json.dumps({"record": rec, "file": out_path, "markdown_row": markdown_row(rec)},
                         ensure_ascii=False, indent=2))
        return 0
    finally:
        try:
            proc.terminate()
            proc.wait(timeout=15)
        except Exception:
            try:
                proc.kill()
            except Exception:
                pass
        shutil.rmtree(data_dir, ignore_errors=True)


def main(argv=None):
    parser = argparse.ArgumentParser(description="真实 CLI 任务调用验收（隔离实例）")
    parser.add_argument("--cli", required=True, help="catalog ID，如 codex-cli")
    parser.add_argument("--port", type=int, default=0, help="隔离实例端口，0=自动")
    parser.add_argument("--timeout", type=int, default=900, help="单次任务轮询上限（秒）")
    parser.add_argument("--boot-timeout", type=int, default=90, help="实例启动等待（秒）")
    parser.add_argument("--case", default=DEFAULT_GOAL, help="验收用例的自然语言目标")
    parser.add_argument("--verify", default=DEFAULT_VERIFY, help="验证命令")
    parser.add_argument("--out", default=os.path.join("docs", "cli-compat-records"),
                        help="记录输出目录")
    parser.add_argument("--self-check", action="store_true",
                        help="只验链路（实例/探测/编排开关），不建任务、不消耗额度")
    args = parser.parse_args(argv)
    return run_once(args)


if __name__ == "__main__":
    sys.exit(main())
