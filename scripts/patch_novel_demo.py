"""把演示数据的「作者可见字段」直接写进磁盘（不经过 store 内存缓存）。

store.create_task 之后，任何一次 store 写入都会用内存副本覆盖任务文件，
所以 story/serial/status 这类字段必须在进程退出后单独补写。
"""
import json
import sys
import time
from pathlib import Path

DATA = Path(sys.argv[1]).resolve()

PATCH = {
    "停灵三夜": {"status": "done", "serial": {"chapters": 68, "words_per_chapter": 2800}},
    "死当铺的第九十九件心愿": {"status": "done", "story": {"words": 4693}},
    # 用 paused 而不是 running：running/queued 会被 jobs 后台队列接起并真跑模型，
    # 演示数据绝不能触发真实执行（会烧额度）。
    "锈河": {"status": "paused", "serial": {"chapters": 12, "words_per_chapter": 3000}},
    "扫榜选材 · 悬疑惊悚": {"status": "done"},
}

RUN_PATCH = {
    "停灵三夜": {"progress": 100},
    "死当铺的第九十九件心愿": {"progress": 100},
    "锈河": {"status": "paused", "progress": 34, "ended_at": None,
             "summary": "已写完 12 章，暂停在第 13 章评审前。"},
    "扫榜选材 · 悬疑惊悚": {"progress": 100},
}

now = time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(time.time() - 900))


def main():
    hits = 0
    for tpath in sorted((DATA / "tasks").glob("*.json")):
        task = json.loads(tpath.read_text(encoding="utf-8"))
        patch = PATCH.get(task.get("title") or "")
        if not patch:
            continue
        task.update(patch)
        tpath.write_text(json.dumps(task, ensure_ascii=False, indent=2), encoding="utf-8")
        hits += 1

    for rdir in sorted((DATA / "runs").iterdir()):
        rpath = rdir / "run.json"
        if not rpath.exists():
            continue
        run = json.loads(rpath.read_text(encoding="utf-8"))
        patch = RUN_PATCH.get(run.get("title") or "")
        if not patch:
            continue
        if patch.get("status") == "paused":
            run["started_at"] = now
        run.update(patch)
        rpath.write_text(json.dumps(run, ensure_ascii=False, indent=2), encoding="utf-8")
    print("任务补写 %d 个，运行记录补写完成" % hits)


if __name__ == "__main__":
    main()
