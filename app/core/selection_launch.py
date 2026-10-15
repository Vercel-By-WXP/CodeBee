"""Launch serial books from reviewed immutable rank-scan packages."""
from __future__ import annotations

import hashlib
import json
import re
import threading
from pathlib import Path

from . import selection, store

_LAUNCH_LOCK = threading.RLock()
PACKAGE_FILE = selection.PACKAGE_FILE

def reviewed_package(task_id, run_id=None):
    task = store.get_task(task_id)
    if not task or task.get("type") != "rank_scan":
        raise ValueError("仅扫榜选材任务支持开书")
    runs = store.task_runs(task_id)
    # Run IDs contain a random suffix, so lexical order is not chronological within a second.
    runs.sort(key=lambda r: (str(r.get("created_at") or ""), r.get("created_at_ns") or 0), reverse=True)
    run = next((r for r in runs if r["id"] == run_id), None) if run_id else next(iter(runs), None)
    if not run or run.get("status") != "done" or not (run.get("verdict") or {}).get("selection_ready"):
        raise ValueError("本次选材尚未通过审核；请先运行新版选材评审与打磨流程")
    try:
        data = json.loads((store.run_dir(run["id"]) / PACKAGE_FILE).read_text("utf-8"))
        report = (store.run_dir(run["id"]) / "selection-report.md").read_text("utf-8")
    except (OSError, ValueError) as exc:
        raise ValueError("已审核立项包缺失，请重新运行选材") from exc
    source = data.get("source") if isinstance(data, dict) else None
    if (selection.validate_package(data) or not isinstance(source, dict) or source.get("task_id") != task_id
            or source.get("run_id") != run["id"]
            or source.get("report_sha256") != hashlib.sha256(report.encode()).hexdigest()
            or selection.parse_report(report)[0] != {k: v for k, v in data.items() if k != "source"}):
        raise ValueError("立项包与审核快照不一致，请重新审核")
    return task, run, data


def selection_info(task_id, run_id=None):
    task, run, data = reviewed_package(task_id, run_id)
    return {"run_id": run["id"], "parent_dir": task["workdir"],
            "candidates": [{"id": c["id"], "title": c["title"], "premise": c["premise"],
                            "platform": c["platform"]} for c in data["candidates"]]}


def launch_serial(task_id, body, create_and_start):
    if not isinstance(body, dict):
        raise ValueError("请求必须为对象")
    # A response pins the reviewed run so an intervening scan cannot change the chosen book.
    if not isinstance(body.get("run_id"), str) or not body["run_id"]:
        raise ValueError("请选择已审核的选材运行")
    with _LAUNCH_LOCK:
        task, run, data = reviewed_package(task_id, body["run_id"])
        if task.get("status") in ("running", "queued"):
            raise ValueError("选材仍在运行，请结束后开书")
        candidate = next((c for c in data["candidates"] if c["id"] == body.get("candidate_id")), None)
        if candidate is None:
            raise ValueError("候选不存在")
        try:
            raw_chapters = body.get("chapters", 8)
            raw_words = body.get("words_per_chapter", 2500)
            if any(isinstance(v, bool) or not re.fullmatch(r"\d+", str(v))
                   for v in (raw_chapters, raw_words)):
                raise ValueError("not an integer")
            chapters = int(raw_chapters)
            words = int(raw_words)
        except (TypeError, ValueError) as exc:
            raise ValueError("章节数和每章字数必须为整数") from exc
        if not 2 <= chapters <= 20 or not 500 <= words <= 8000:
            raise ValueError("章节数范围2-20，每章字数范围500-8000")
        title = selection.folder_title(candidate["title"])
        parent = Path(task["workdir"]).resolve(strict=True)
        target = parent / title
        origin = {"task_id": task_id, "run_id": run["id"], "candidate_id": candidate["id"]}
        marker = target / ".selection-origin.json"
        if target.exists() or target.is_symlink():
            if not target.is_symlink() and target.resolve().parent == parent:
                try:
                    previous = json.loads(marker.read_text("utf-8"))
                except (OSError, ValueError):
                    previous = {}
                if previous.get("source") == origin and previous.get("result", {}).get("task_id"):
                    return previous["status"], dict(previous["result"], reused=True)
            raise ValueError("同名书目录已存在，请修改候选书名并重新审核；不会覆盖已有作品")
        target.mkdir()  # exclusive reservation also protects against concurrent processes
        marker.write_text(json.dumps({"source": origin}, ensure_ascii=False), encoding="utf-8")
        (target / PACKAGE_FILE).write_text(json.dumps({"schema_version": 1, "source": data["source"],
                                                     "candidates": [candidate]}, ensure_ascii=False, indent=2), encoding="utf-8")
        brief = json.dumps(candidate, ensure_ascii=False, indent=2)
        (target / "selection-brief.md").write_text("# 审核后的连载输入\n\n" + brief, encoding="utf-8")
        payload = {
            "type": "serial_novel", "title": title, "workdir": str(target),
            "goal": "创作《%s》，按已审核立项包试写前%d章，每章约%d字。目标平台：%s。\n%s"
                    % (title, chapters, words, candidate["platform"], candidate["premise"]),
            "context": "## 已审核立项方案（内部评审不代表平台签约）\n" + brief
                       + "\n必须遵循 selection-brief.md；按2万/3万字节点复盘，未达到节点不得宣称完成签约准备。",
            "story_bible": candidate["story_bible"],
            "serial": {"chapters": chapters, "words_per_chapter": words},
            "mode": "expert", "rounds": 3, "threshold": 8.0,
            "thinking": task.get("thinking") or "standard",
            "sandbox": dict(task.get("sandbox") or {}, allowed_roots=[str(target)]),
        }
        status, result = create_and_start(payload)
        result = dict(result, workdir=str(target), source=origin)
        if status != 200 and result.get("task_id"):
            result["error"] = str(result.get("error") or "启动失败") + "；已保留新书任务，请从侧栏打开后重试任务"
        marker.write_text(json.dumps({"source": origin, "status": status, "result": result},
                                     ensure_ascii=False, indent=2), encoding="utf-8")
        return status, result
