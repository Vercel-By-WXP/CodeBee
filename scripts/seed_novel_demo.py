"""演示数据播种（仅用于界面预览，绝不连生产数据目录）。

用法：
    TUTTI_DATA=<demo 目录> PYTHONPATH=app python scripts/seed_novel_demo.py

它会：建 4 个真实结构的任务 → 补上作者视角关心的字段（字数/章数）→
造出 4 条已结束的运行记录，让书架不是空的。不调模型、不发外部请求。
"""
import os
import sys
import time
from pathlib import Path

DATA = os.environ.get("TUTTI_DATA")
if not DATA:
    sys.exit("必须先设置 TUTTI_DATA 指向一个独立的演示目录")
if "cb-novel-demo" not in Path(DATA).name and "cb-novel-verify" not in Path(DATA).name:
    sys.exit("拒绝播种：TUTTI_DATA=%s 不像演示目录" % DATA)

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "app"))

from core import store, paths  # noqa: E402
from core.publish import ledger  # noqa: E402

BOOKS = [
    {
        "type": "serial_novel",
        "title": "停灵三夜",
        "goal": "民俗悬疑长篇：守灵人替死人守三夜，第三夜死者开口说出活人的秘密。要求每章结尾留钩子，人物动机可推敲。",
        "status": "done",
        "serial": {"chapters": 68, "words_per_chapter": 2800},
        "run": {"status": "done", "progress": 100, "ago": 3600 * 26},
    },
    {
        "type": "short_story",
        "title": "死当铺的第九十九件心愿",
        "goal": "短篇：一间只收心愿的当铺，第九十九件当品是店主自己的记忆。要求 5000 字内落地反转，结尾留余味。",
        "status": "done",
        "story": {"words": 4693},
        "run": {"status": "done", "progress": 100, "ago": 3600 * 74},
    },
    {
        "type": "novel",
        "title": "锈河",
        "goal": "工业废城群像长篇：一条被污染的河穿过三代人的命运。要求设定先行、卷纲明确、每卷有转折。",
        "status": "running",
        "serial": {"chapters": 12, "words_per_chapter": 3000},
        "run": {"status": "running", "progress": 34, "ago": 900},
    },
    {
        "type": "rank_scan",
        "title": "扫榜选材 · 悬疑惊悚",
        "goal": "",
        "status": "done",
        "run": {"status": "done", "progress": 100, "ago": 3600 * 8},
    },
]


# 番茄建书资料（男频 · 悬疑灵异）：取值必须全部落在官方目录内，且与简介的
# 频道信号一致——否则 repair_existing() 会认为「字段不全/频道失配」并整条重写。
_FANQIE_META = {
    "target_reader": "男频",
    "summary": ("守灵人替死人守三夜，第三夜死者开口，说出的却是活人的秘密。"
                "他必须在鸡叫前弄清这句话是谁想让他听见的——"
                "因为天亮之后，说这话的人就要来收尸了。"),
    "category": "悬疑灵异",
    "tags_theme": ["悬疑", "规则怪谈"],
    "tags_role": ["神探", "反派"],
    "tags_plot": ["捉鬼", "破案"],
    "content_plot": ["攻略反派"],
    "content_emotion": ["暗恋"],
    "content_character": ["马甲"],
    "content_world": ["规则怪谈"],
    "protagonist_1": "陆沉",
    "protagonist_2": "苏晚",
}


def _prose(n, tag="灯"):
    """造一段像人写的中文正文（n 字左右）。

    为什么不用 lorem：演示界面要给人看「三万字的稿子长什么样」，占位符
    一眼就露馅；而发布包的切章效果也只有在真实长度的段落上才看得出来。
    """
    unit = ("%s芯爆了一下，火苗歪向门口。他没抬头，只把手里的纸钱一张张码齐，"
            "像在码一叠随时会被风吹走的账。屋外的雨下得很稳，稳到能听出瓦沟里"
            "每一道水线的去向。三个人守着一口棺材，谁都不先说那句该说的话。\n" % tag)
    out = []
    while sum(len(x) for x in out) < n:
        out.append(unit)
    text = "".join(out)
    return text[:n]


def _workdir(item, index):
    """每本书一个独立工作目录。

    早先所有演示书共用数据目录根，导致 story.md 互相覆盖、发布包读到别人的
    稿子——演示一旦「看起来对但其实读串了」，验证就是假的。
    """
    safe = "demo-%02d-%s" % (index + 1, item["type"])
    path = Path(DATA) / safe
    path.mkdir(parents=True, exist_ok=True)
    return path


def _write_manuscript(item, workdir):
    """按书的形态落稿：短篇一篇 story.md，连载 chapter-NN.md，长篇单稿。"""
    if item["type"] == "short_story":
        (workdir / "story.md").write_text(_prose(4600), encoding="utf-8")
    elif item["type"] == "serial_novel":
        for i in range(1, 4):
            (workdir / ("chapter-%02d.md" % i)).write_text(
                "# 第%d章 %s\n\n%s" % (i, ["来电", "雨夜", "开口"][i - 1], _prose(1800)),
                encoding="utf-8")


def main():
    made = []
    for index, item in enumerate(BOOKS):
        workdir = _workdir(item, index)
        _write_manuscript(item, workdir)
        task = store.create_task({
            "type": item["type"],
            "title": item["title"],
            "goal": item["goal"],
            "workdir": str(workdir),
        })
        tid = task["id"]
        made.append(tid)

        # 直接补写任务文件：story/serial 是流水线跑出来的字段，建任务时不存在。
        tpath = paths.TASKS_DIR / ("%s.json" % tid)
        data = store._read_json(tpath) if hasattr(store, "_read_json") else None
        if data is None:
            import json
            data = json.loads(tpath.read_text(encoding="utf-8"))
        data["status"] = item["status"]
        if item.get("story"):
            data["story"] = dict(item["story"])
        if item.get("serial"):
            data["serial"] = dict(item["serial"])
        import json
        tpath.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")

        run = store.create_run("orchestration", item["title"], task_id=tid)
        fields = {"status": item["run"]["status"], "progress": item["run"]["progress"]}
        if item["run"]["status"] == "done":
            fields["summary"] = "已产出，等待你的裁决。"
        else:
            fields["summary"] = "正在写第 5 章，已过 2 轮评审。"
        started = time.time() - item["run"]["ago"]
        fields["started_at"] = time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(started))
        if item["run"]["status"] == "done":
            fields["ended_at"] = time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(started + 1800))
        store.update_run(run["id"], **fields)

        # 作品信息（建书资料）：手动发布包要能取到书名/简介/分类/标签，
        # 否则演示出来只有一篇光秃秃的正文，看不出「拿着一份资料去平台」。
        # 字段必须按官方目录填**齐全**：启动时的 repair_existing() 会把缺项的
        # 记录按目录随机补齐，演示数据会被改成一个跟书毫无关系的分类
        # （实测：悬疑书被补成「西方奇幻 / 衍生」）。
        if item["type"] in ("short_story", "serial_novel"):
            store.set_book_meta(tid, "fanqie", {
                "status": "done", "source": "演示数据",
                "at": time.strftime("%Y-%m-%d %H:%M:%S"),
                "data": dict(_FANQIE_META, book_name=item["title"]),
            })
            if item["type"] == "short_story":
                short_tid = tid

    # 自检：启动时的补字段逻辑若还要动这批数据，说明字段没填全，演示会被改味。
    from core import bookmeta
    repaired = bookmeta.repair_existing()
    if repaired:
        print("[警告] 演示作品信息被 repair_existing 改写了 %d 条——字段没填全" % repaired)
    else:
        print("作品信息自检通过：repair_existing 无需改动")

    # 结果回流演示：让「同题材第二次开书」真的有东西可学。
    # 连载那本先「审核中」再「审核未通过」，并贴上一句像平台说的话；短篇那本
    # 故意不登记——它的面板应该显示「未登记」，但预览里能看到连载那条。
    # 这正是回流的意义：这一本还没结果，下一本已经带着上一本的教训。
    from core.publish import outcome as pub_outcome
    serial_task = store.get_task(made[0]) if made else None
    if serial_task:
        pub_outcome.record(serial_task, "fanqie", "in_review")
        pub_outcome.record(
            serial_task, "fanqie", "rejected",
            quote="您的作品经审核，开篇三章仍在铺陈设定，核心冲突出现较晚，"
                  "读者留存偏低，暂不通过。修改后可重新提交。",
            reads=860,
            note="前三章确实在铺守灵的规矩，第五版开始砍。")
        pub_outcome.note_progress(made[0], "fanqie", live=0, pending=0,
                                  total=3, rejected=1)

    # 一条失败的上线记录：界面的「手动发」入口就挂在这个信号上——用户点
    # 「重试」也救不回来的时候，才有东西可点。演示不点真链路。
    ledger.record("fanqie", "publish_short_story", task_id=short_tid,
                  title="死当铺的第九十九件心愿", ok=False,
                  error="平台页面改版：找不到「继续发布」按钮")

    print("已播种 %d 本书：" % len(made))
    for t in made:
        print("  ", t)
    print("手动发布包演示书：", short_tid)
    print("数据目录：", DATA)


if __name__ == "__main__":
    main()
